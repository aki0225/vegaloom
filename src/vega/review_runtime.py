from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from .execution_control import RunnerExecutionContext
from .models import (
    BriefState,
    GateResult,
    ReviewFinding,
    ReviewState,
    ReviewVerdict,
)
from .project_config import (
    ProjectConfig,
    load_project_config,
    project_policy_snapshot,
    render_project_config_summary,
)
from .project_context import render_project_context
from .project_knowledge import load_project_knowledge
from .project_profile import build_project_profile
from .prompt_metrics import (
    PromptMetrics,
    measure_prompt,
    write_context_budget_report,
    write_prompt_metrics,
)
from .redaction import redact_text, redact_value
from .review_evidence import review_evidence_issues as _review_evidence_issues
from .review_runner_contract import (
    review_result_diagnostic,
    review_result_is_trusted,
    untrusted_review_current_step,
)
from .review_risk_gate import (
    PrecomputedReviewRiskGate,
    evaluate_review_risk_gate as _default_evaluate_review_risk_gate,
    review_risk_gate_result as _review_risk_gate_result,
)
from .risk_review_reporting import (
    render_review_checklist,
    render_review_findings,
    render_runner_output,
    verdict_schema_example as _verdict_schema_example,
)
from .risk_review_runtime import (
    build_required_review_failure_reasons,
    enforce_required_risk_review,
    redact_review_verdict as _redact_review_verdict,
    render_required_review_pack_lines,
    render_required_review_prompt_rules,
    required_review_outcome_line,
    required_reviews_from_inputs,
    run_required_review_pack_eval,
    run_required_review_prompt_eval,
)
from .run_utils import create_run_dir, resolve_run_dir
from .runner import Runner, RunnerResult, make_runner
from .runtime_workspace import capture_runtime_workspace
from .trace import TraceWriter
from .workspace_check import ignored_coverage_level

REVIEW_PACK_ARTIFACTS = [
    "state.json",
    "trace.jsonl",
    "review-pack.md",
    "review-prompt.md",
    "review-checklist.md",
    "review-context.json",
    "project-context.md",
    "review-prompt-metrics.json",
    "review-prompt-metrics.md",
    "eval.md",
]
REVIEW_ARTIFACTS = [
    *REVIEW_PACK_ARTIFACTS,
    "review-runner-output.txt",
    "review-findings.md",
    "review-verdict.json",
]
MAX_TEXT_CHARS = 20000


def _evaluate_review_risk_gate(
    workspace: Path,
    repo_path: Path,
    source_run: str,
) -> dict[str, Any]:
    return _default_evaluate_review_risk_gate(workspace, repo_path, source_run)


def _review_risk_gate_payload(
    workspace: Path,
    repo_path: Path,
    source_run: str,
    precomputed: PrecomputedReviewRiskGate | None,
) -> dict[str, Any]:
    """复用 Loop 刚生成的确定性门禁；独立 Review 仍自行评估。

    Loop 路径中的结果不是 worker 输入，而是 Runtime 在同一同步调用链里、针对当前
    Reflect 生成的确定性结果。Review 仍会重新捕获工作区授权快照，终态 Eval 也会
    独立重算风险语义，因此这里仅消除相邻阶段的重复 Git 扫描，不削弱防篡改检查。
    """
    if precomputed is None:
        return _evaluate_review_risk_gate(workspace, repo_path, source_run)
    if precomputed.source_run != source_run:
        return {
            "status": "failed",
            "source_run": source_run,
            "diagnostic": "预计算风险门禁与当前 Reflect 来源不一致。",
        }
    return {
        "status": "success",
        "source_run": source_run,
        "result": precomputed.result.model_dump(mode="json"),
    }


class ReviewPackRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, repo_path: Path, source_run: str, run_id_suffix: str = "review-pack") -> Path:
        run_id, run_dir = create_run_dir(self.workspace, _new_run_id(run_id_suffix))
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = ReviewState(
            run_id=run_id,
            repo_path=str(repo_path.resolve()),
            source_run=source_run,
            status="running",
            runner="none",
        )
        state.current_step = "collect"
        state.save(run_dir / "state.json")
        trace.write("review_pack_started", repo_path=str(repo_path.resolve()), source_run=source_run)

        inputs = collect_review_inputs(self.workspace, repo_path, source_run)
        inputs["risk_gate"] = _review_risk_gate_payload(
            self.workspace,
            repo_path,
            source_run,
            None,
        )
        state.changed_files = inputs["changed_files"]
        metrics = _write_review_pack_artifacts(run_dir, inputs)
        trace.write("review_pack_written", changed_files=state.changed_files)
        trace.write(
            "review_evidence_checked",
            consistent=inputs["evidence_consistent"],
            issues=inputs["evidence_issues"],
            diagnostics=inputs["evidence_diagnostics"],
        )
        trace.write("review_prompt_measured", metrics=metrics.model_dump())
        risk_gate_result = _review_risk_gate_result(inputs)
        trace.write(
            "review_risk_gate_evaluated",
            status=str(inputs["risk_gate"].get("status") or "failed"),
            source_run=source_run,
            risk=risk_gate_result.risk if risk_gate_result else None,
            recommendation=(
                risk_gate_result.recommendation if risk_gate_result else None
            ),
        )

        state.current_step = "eval"
        run_dir.joinpath("eval.md").write_text("# Eval\n\n(pending)\n", encoding="utf-8")
        eval_results = run_review_pack_eval(run_dir, REVIEW_PACK_ARTIFACTS)
        run_dir.joinpath("eval.md").write_text(render_eval(eval_results), encoding="utf-8")
        state.eval_results = eval_results
        state.artifacts = REVIEW_PACK_ARTIFACTS
        if inputs["evidence_issues"]:
            state.status = "needs_human"
            state.current_step = "evidence_stale"
        elif metrics.exceeded:
            report = write_context_budget_report(run_dir, "review", metrics)
            state.artifacts = [*REVIEW_PACK_ARTIFACTS, report]
            state.status = "needs_human"
            state.current_step = "context_budget"
        elif risk_gate_result is None:
            state.status = "needs_human"
            state.current_step = "risk_gate_failed"
        else:
            state.status = "failed" if _has_failures(eval_results) else "success"
            state.current_step = "done"
        state.save(run_dir / "state.json")
        trace.write("eval_written", results=eval_results)
        trace.write("run_finished", status=state.status)
        return run_dir


class ReviewRuntime:
    def __init__(
        self,
        workspace: Path,
        runner: Runner | None = None,
        timeout_seconds: int = 900, progress_reporter: Callable[[str, int], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.progress_reporter = progress_reporter

    def run(
        self,
        repo_path: Path,
        source_run: str,
        runner_name: str = "codex-exec",
        execution_context: RunnerExecutionContext | None = None,
        project_config: ProjectConfig | None = None,
        precomputed_risk_gate: PrecomputedReviewRiskGate | None = None,
    ) -> Path:
        execution_config = project_config or load_project_config(repo_path)
        review_policy_snapshot = project_policy_snapshot(repo_path)
        initial_authorization_issues: list[str] = []
        if (
            precomputed_risk_gate is not None
            and precomputed_risk_gate.source_run != source_run
        ):
            initial_authorization_issues.append(
                "precomputed_risk_gate_source_mismatch"
            )
        if (
            precomputed_risk_gate is not None
            and review_policy_snapshot
            != precomputed_risk_gate.project_policy_snapshot
        ):
            initial_authorization_issues.append(
                "project_policy_changed_before_review_start"
            )
        if self.runner is None and runner_name == "codex-exec" and execution_config.runner.reviewer:
            runner_name = execution_config.runner.reviewer
        run_id, run_dir = create_run_dir(self.workspace, _new_run_id("review"))
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = ReviewState(
            run_id=run_id,
            repo_path=str(repo_path.resolve()),
            source_run=source_run,
            runner=runner_name,
            status="running",
        )
        state.current_step = "collect"
        state.save(run_dir / "state.json")
        trace.write("review_started", repo_path=str(repo_path.resolve()), source_run=source_run, runner=runner_name)
        inputs = collect_review_inputs(
            self.workspace,
            repo_path,
            source_run,
        )
        inputs["risk_gate"] = _review_risk_gate_payload(
            self.workspace,
            repo_path,
            source_run,
            precomputed_risk_gate,
        )
        authorization_issues = list(initial_authorization_issues)
        try:
            current_policy_snapshot = project_policy_snapshot(repo_path)
            authorization_snapshot = capture_runtime_workspace(self.workspace, repo_path)
        except Exception:  # noqa: BLE001 - reviewer 授权快照失败时不得启动外部 runner
            authorization_issues.append("review_authorization_snapshot_failed")
        else:
            if current_policy_snapshot != review_policy_snapshot:
                authorization_issues.append("project_policy_changed_before_reviewer")
            if (
                authorization_snapshot.fingerprint
                != inputs["current_workspace_fingerprint"]
            ):
                authorization_issues.append("review_authorization_workspace_changed")
            if authorization_snapshot.unsafe_index_paths:
                authorization_issues.append("current_unsafe_index_flags_present")
        if authorization_issues:
            inputs["evidence_issues"] = list(
                dict.fromkeys([*inputs["evidence_issues"], *authorization_issues])
            )
            inputs["evidence_consistent"] = False
        pre_review_evidence_issues = list(inputs["evidence_issues"])
        state.changed_files = inputs["changed_files"]
        metrics = _write_review_pack_artifacts(run_dir, inputs)
        trace.write("review_pack_written", changed_files=state.changed_files)
        trace.write(
            "review_evidence_checked",
            consistent=inputs["evidence_consistent"],
            issues=inputs["evidence_issues"],
            diagnostics=inputs["evidence_diagnostics"],
        )
        trace.write("review_prompt_measured", metrics=metrics.model_dump())
        risk_gate_result = _review_risk_gate_result(inputs)
        risk_gate_status = str(inputs["risk_gate"].get("status") or "failed")
        trace.write(
            "review_risk_gate_evaluated",
            status=risk_gate_status,
            source_run=source_run,
            risk=risk_gate_result.risk if risk_gate_result else None,
            recommendation=(
                risk_gate_result.recommendation if risk_gate_result else None
            ),
        )
        state.current_step = "run_reviewer"
        state.save(run_dir / "state.json")
        prompt = run_dir.joinpath("review-prompt.md").read_text(encoding="utf-8")
        budget_artifact: str | None = None
        reviewer_started = False
        reviewer_start_fingerprint = inputs["current_workspace_fingerprint"]
        if pre_review_evidence_issues:
            result = RunnerResult(
                status="skipped",
                output="",
                error="review 证据与当前工作区不属于同一快照，未启动外部 reviewer。",
                command=[],
            )
        elif metrics.exceeded:
            budget_artifact = write_context_budget_report(run_dir, "review", metrics)
            result = RunnerResult(
                status="skipped",
                output="",
                error="reviewer prompt 超过项目上下文预算，未启动外部 runner。",
                command=[],
            )
        elif risk_gate_result is None:
            result = RunnerResult(
                status="skipped",
                output="",
                error="review 风险门禁评估失败，未启动外部 reviewer。",
                command=[],
            )
        else:
            runner = self.runner or make_runner(
                runner_name,
                options=execution_config.runner.codex_exec.reviewer,
            )
            execution_context = execution_context or RunnerExecutionContext(
                execution_root=run_dir,
                execution_dir=run_dir / "executions" / "reviewer",
                run_id=run_id,
                step="reviewer",
                progress_reporter=self.progress_reporter,
            )
            reviewer_started = True
            result = runner.run(
                prompt,
                repo_path.resolve(),
                sandbox="read-only",
                timeout_seconds=self.timeout_seconds,
                execution_context=execution_context,
            )
        result = _redact_runner_result(result)
        review_execution_issues = _capture_post_review_workspace(
            run_dir, self.workspace, repo_path, inputs,
            reviewer_started=reviewer_started,
            reviewer_start_fingerprint=reviewer_start_fingerprint,
            termination_unconfirmed=result.termination_unconfirmed,
        )
        state.runner_status = result.status
        run_dir.joinpath("review-runner-output.txt").write_text(
            render_runner_output(result),
            encoding="utf-8",
        )
        interruption_artifact = _write_runner_status_report(
            run_dir,
            result,
            step="reviewer",
        )
        verdict = _review_verdict_from_result(result)
        verdict = _enforce_complete_review_evidence(
            verdict,
            [*inputs["truncated_sections"], *pre_review_evidence_issues],
        )
        verdict = _enforce_unchanged_review_workspace(
            verdict,
            inputs,
            review_execution_issues,
        )
        required_review_failures = build_required_review_failure_reasons(
            evidence_issues=pre_review_evidence_issues,
            truncated_sections=inputs["truncated_sections"],
            workspace_issues=review_execution_issues,
            prompt_budget_exceeded=metrics.exceeded,
            runner_status=result.status,
            runner_error=result.error,
            termination_unconfirmed=result.termination_unconfirmed,
        )
        verdict, risk_disclosure_issues = enforce_required_risk_review(
            verdict,
            risk_gate_result,
            evidence_failures=required_review_failures,
        )
        run_dir.joinpath("review-verdict.json").write_text(
            _redacted_model_json(verdict),
            encoding="utf-8",
        )
        run_dir.joinpath("review-findings.md").write_text(
            render_review_findings(
                verdict,
                risk_gate_result.required_reviews if risk_gate_result else [],
            ),
            encoding="utf-8",
        )
        trace.write(
            "review_runner_finished",
            runner=runner_name,
            status=result.status,
            termination_unconfirmed=result.termination_unconfirmed,
            verdict=verdict.verdict,
            findings=len(verdict.findings),
            risk_disclosures=len(verdict.risk_disclosures),
            risk_disclosure_issues=risk_disclosure_issues,
        )
        trace.write(
            "review_workspace_checked",
            changed=inputs["workspace_changed_during_review"],
            issues=review_execution_issues,
            start_fingerprint=inputs["reviewer_start_workspace_fingerprint"],
            end_fingerprint=inputs["reviewer_end_workspace_fingerprint"],
        )

        state.current_step = "eval"
        run_dir.joinpath("eval.md").write_text("# Eval\n\n(pending)\n", encoding="utf-8")
        eval_results = run_review_pack_eval(run_dir, REVIEW_ARTIFACTS)
        _append_review_eval_outcome(
            eval_results,
            result=result,
            review_execution_issues=review_execution_issues,
            pre_review_evidence_issues=pre_review_evidence_issues,
            metrics=metrics,
            risk_gate_result=risk_gate_result,
            verdict=verdict,
            risk_disclosure_issues=risk_disclosure_issues,
        )
        run_dir.joinpath("eval.md").write_text(render_eval(eval_results), encoding="utf-8")
        state.eval_results = eval_results
        state.artifacts = [
            *REVIEW_ARTIFACTS,
            *([interruption_artifact] if interruption_artifact else []),
            *([budget_artifact] if budget_artifact else []),
        ]
        state.verdict = verdict.verdict
        if result.termination_unconfirmed:
            state.status = "needs_human"
            state.current_step = "termination_unconfirmed"
        elif review_execution_issues:
            state.status = "needs_human"
            state.current_step = "workspace_changed_during_review"
        elif pre_review_evidence_issues:
            state.status = "needs_human"
            state.current_step = "evidence_stale"
        elif metrics.exceeded:
            state.status = "needs_human"
            state.current_step = "context_budget"
        elif risk_gate_result is None:
            state.status = "needs_human"
            state.current_step = "risk_gate_failed"
        elif not review_result_is_trusted(result):
            state.status = "needs_human"
            state.current_step = untrusted_review_current_step(result)
        elif inputs["truncated_sections"]:
            state.status = "needs_human"
            state.current_step = "evidence_truncated"
        elif risk_gate_result.recommendation == "human-review":
            state.status = "needs_human"
            state.current_step = "risk_gate_needs_human"
        elif verdict.verdict == "approve" and not _has_failures(eval_results):
            state.status = "success"
            state.current_step = "done"
        elif verdict.verdict == "request_changes":
            state.status = "needs_human"
            state.current_step = "done"
        else:
            state.status = "failed" if _has_failures(eval_results) else "needs_human"
            state.current_step = "done"
        state.save(run_dir / "state.json")
        trace.write("eval_written", results=eval_results)
        trace.write("run_finished", status=state.status, verdict=verdict.verdict)
        return run_dir


def collect_review_inputs(
    workspace: Path,
    repo_path: Path,
    source_run: str,
    config: ProjectConfig | None = None,
) -> dict[str, Any]:
    repo = repo_path.resolve()
    source_dir = resolve_run_dir(workspace, source_run)
    reflect_state, state_issues, state_diagnostics = _read_json_artifact(
        source_dir / "state.json",
        "source_state",
    )
    current_snapshot = capture_runtime_workspace(workspace, repo)
    source_evidence, evidence_read_issues, evidence_read_diagnostics = _read_json_artifact(
        source_dir / "review-evidence.json",
        "source_evidence",
    )
    full_diff, full_diff_issues, full_diff_diagnostics = _read_text_artifact(
        source_dir / "full-diff.patch",
        "full_diff",
    )
    test_summary, test_issues, test_diagnostics = _read_text_artifact(
        source_dir / "test-summary.md",
        "test_summary",
    )
    reflection, reflection_issues, reflection_diagnostics = _read_text_artifact(
        source_dir / "reflection.md",
        "reflection",
    )
    diff_summary, diff_summary_issues, diff_summary_diagnostics = _read_text_artifact(
        source_dir / "diff-summary.md",
        "diff_summary",
    )
    upstream_source_run = reflect_state.get("source_run")
    if not isinstance(upstream_source_run, str):
        upstream_source_run = source_evidence.get("upstream_source_run")
    source_brief, source_brief_issues, source_brief_diagnostics = (
        _read_source_brief_artifact(workspace, upstream_source_run, repo)
    )
    evidence_issues = [
        *state_issues,
        *evidence_read_issues,
        *full_diff_issues,
        *test_issues,
        *reflection_issues,
        *diff_summary_issues,
        *source_brief_issues,
        *_review_evidence_issues(
            repo,
            source_dir.name,
            reflect_state,
            source_evidence,
            source_brief,
            reflection,
            diff_summary,
            full_diff,
            test_summary,
            current_snapshot,
        ),
    ]
    evidence_issues = list(dict.fromkeys(evidence_issues))
    evidence_diagnostics = list(
        dict.fromkeys(
            [
                *state_diagnostics,
                *evidence_read_diagnostics,
                *full_diff_diagnostics,
                *test_diagnostics,
                *reflection_diagnostics,
                *diff_summary_diagnostics,
                *source_brief_diagnostics,
                *_string_list(
                    source_evidence.get("source_brief_evidence_diagnostics")
                ),
            ]
        )
    )
    state_changed_files = _string_list(reflect_state.get("changed_files"))
    evidence_changed_files = _string_list(source_evidence.get("changed_files"))
    changed_files = [
        redact_text(path)
        for path in (state_changed_files or evidence_changed_files)
    ]
    source_brief = redact_text(source_brief)
    reflection = redact_text(reflection)
    diff_summary = redact_text(diff_summary)
    test_summary = redact_text(test_summary)
    full_diff = redact_text(full_diff)
    knowledge = load_project_knowledge(
        workspace,
        repo,
        "\n".join([source_brief, reflection, diff_summary, " ".join(changed_files)]),
        changed_files,
        tracked_only=True,
        tracked_revision=current_snapshot.head_sha,
    )
    profile = build_project_profile(
        workspace,
        repo,
        tracked_only=True,
        tracked_revision=current_snapshot.head_sha,
    )
    config = config or load_project_config(
        repo,
        tracked_only=True,
        tracked_revision=current_snapshot.head_sha,
    )
    project_context = render_project_context(
        profile,
        knowledge,
        render_project_config_summary(config),
    )
    source_brief, source_brief_truncated = _truncate_with_status(source_brief)
    reflection, reflection_truncated = _truncate_with_status(reflection)
    diff_summary, diff_summary_truncated = _truncate_with_status(diff_summary)
    test_summary, test_summary_truncated = _truncate_with_status(test_summary)
    full_diff, full_diff_truncated = _truncate_with_status(
        full_diff,
        config.prompt_budget.reviewer_diff_max_chars,
    )
    project_context, project_context_truncated = _truncate_with_status(project_context)
    truncated_sections = [
        name
        for name, truncated in [
            ("source_brief", source_brief_truncated),
            ("reflection", reflection_truncated),
            ("diff_summary", diff_summary_truncated),
            ("test_summary", test_summary_truncated),
            ("project_context", project_context_truncated),
            ("full_diff", full_diff_truncated),
        ]
        if truncated
    ]
    source_ignored_coverage_level = ignored_coverage_level(
        source_evidence.get("ignored_manifest_complete"), source_evidence.get("ignored_content_complete")
    )
    return {
        "repo_path": str(repo),
        "repo_name": repo.name,
        "source_run": source_run,
        "source_run_dir": str(source_dir),
        "source_brief": source_brief,
        "reflection": reflection,
        "diff_summary": diff_summary,
        "test_summary": test_summary,
        "changed_files": changed_files,
        "full_diff": full_diff,
        "project_context": project_context,
        "truncated_sections": truncated_sections,
        "evidence_issues": evidence_issues,
        "evidence_diagnostics": evidence_diagnostics,
        "evidence_consistent": not evidence_issues,
        "source_snapshot_id": str(source_evidence.get("snapshot_id") or ""),
        "source_workspace_fingerprint": str(
            source_evidence.get("workspace_fingerprint") or ""
        ),
        "current_workspace_fingerprint": current_snapshot.fingerprint,
        "current_index_flags_sha256": current_snapshot.index_flags_sha256,
        "current_unsafe_index_paths": [
            redact_text(path) for path in current_snapshot.unsafe_index_paths
        ],
        "source_untracked_content_complete": bool(
            source_evidence.get("untracked_content_complete", False)
        ),
        "current_untracked_content_complete": current_snapshot.untracked_content_complete,
        "source_ignored_coverage_level": source_ignored_coverage_level,
        "current_ignored_coverage_level": current_snapshot.ignored_coverage_level,
        "reviewer_start_workspace_fingerprint": "",
        "reviewer_end_workspace_fingerprint": "",
        "workspace_changed_during_review": False,
        "review_execution_issues": [],
        "reviewer_prompt_max_chars": config.prompt_budget.reviewer_max_chars,
        "memory_hit_count": len(knowledge.memory_hits),
        "agents_files": [
            redact_text(item.path)
            for item in knowledge.agents_instructions
        ],
    }


def render_review_pack(inputs: dict[str, Any]) -> str:
    risk_gate = inputs.get("risk_gate")
    required_review_lines: list[str] = []
    risk_gate_note = [
        f"- ignored 证据覆盖：Reflect `{inputs['source_ignored_coverage_level']}`，当前 "
        f"`{inputs['current_ignored_coverage_level']}`；`metadata_bounded` 仅表示稳定元数据检测。"
    ]
    if isinstance(risk_gate, dict) and risk_gate.get("status") == "success":
        try:
            result = GateResult.model_validate(risk_gate.get("result"))
        except ValidationError:
            risk_gate_note.append("- 风险门禁结果格式不合法；本次审查不能作为自动通过结论。")
        else:
            risk_gate_note.append(
                f"- 风险门禁：`{result.risk}` / `{result.recommendation}`。"
            )
            if result.recommendation == "human-review":
                risk_gate_note.append(
                    "- 风险门禁要求人工审查；本次 reviewer 只提供辅助发现，不能替代人工确认。"
                )
            required_review_lines.extend(
                render_required_review_pack_lines(result.required_reviews)
            )
    elif risk_gate is not None:
        risk_gate_note.append("- 风险门禁评估失败；本次审查不能作为自动通过结论。")
    text = "\n".join(
        [
            "# Review Pack",
            "",
            "## 审查边界",
            "",
            "- 这是隔离 reviewer 的输入包，只基于事实材料审查当前 diff。",
            "- 不包含 worker 的完整聊天记录，避免被实现过程污染。",
            "- reviewer 只使用已存在的文件、diff、测试摘要、日志和项目上下文；不运行会写文件或缓存的命令，也不修改、提交、推送或发布。",
            *(
                [
                    "- 以下证据已截断："
                    + "、".join(f"`{name}`" for name in inputs["truncated_sections"])
                    + "。reviewer 可以指出已发现的问题，但不得返回 approve。"
                ]
                if inputs["truncated_sections"]
                else []
            ),
            *(
                [
                    "- Review 证据已过期或完整性校验失败："
                    + "、".join(f"`{name}`" for name in inputs["evidence_issues"])
                    + "。外部 reviewer 不会启动，请重新执行 reflect。"
                    + (
                        "\n- 持久化诊断："
                        + "；".join(inputs["evidence_diagnostics"])
                        if inputs["evidence_diagnostics"]
                        else ""
                    )
                ]
                if inputs["evidence_issues"]
                else []
            ),
            *risk_gate_note,
            "",
            *required_review_lines,
            "## 原始需求 / Agent Brief",
            "",
            inputs["source_brief"] or "- 未找到上游 agent-brief.md。",
            "",
            "## 执行后复盘",
            "",
            inputs["reflection"] or "- 未找到 reflection.md。",
            "",
            "## Diff Summary",
            "",
            inputs["diff_summary"] or "- 未找到 diff-summary.md。",
            "",
            "## Test Summary",
            "",
            inputs["test_summary"] or "- 未提供测试摘要。",
            "",
            "## 项目上下文",
            "",
            inputs["project_context"],
            "",
            "## Full Diff",
            "",
            "```diff",
            inputs["full_diff"].strip() or "<empty>",
            "```",
        ]
    ).rstrip() + "\n"
    return redact_text(text)


def render_review_prompt(inputs: dict[str, Any]) -> str:
    required_reviews = required_reviews_from_inputs(inputs)
    text = "\n".join(
        [
            "# 任务：隔离代码审查",
            "",
            "你是独立 reviewer。你没有 worker 的聊天上下文，只能基于下面的 review pack 审查当前变更。",
            "",
            "硬性要求：",
            "- 只读审查，只使用已存在的文件、diff、测试摘要、日志和项目上下文；不要自行运行验证命令或补造证据。",
            "- 不要运行测试、构建、安装依赖、格式化、代码生成或其他可能写入文件/缓存的命令，也不要修改、提交、推送、发布、删除或执行破坏性操作。",
            "- 重点找真实 bug、遗漏测试、需求不满足、项目规则违反和安全风险。",
            "- 如果证据不足，不要强行 approve，返回 needs_human。",
            *render_required_review_prompt_rules(required_reviews),
            "- 最终只能输出一个 JSON 对象，不要包 Markdown 代码块。",
            "",
            "JSON schema：",
            "```json",
            json.dumps(
                _verdict_schema_example(required_reviews),
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            render_review_pack(inputs),
        ]
    ).rstrip() + "\n"
    return redact_text(text)


def render_review_context(inputs: dict[str, Any]) -> dict[str, Any]:
    context = {
        "repo_path": inputs["repo_path"],
        "repo_name": inputs["repo_name"],
        "source_run": inputs["source_run"],
        "source_run_dir": inputs["source_run_dir"],
        "changed_files": inputs["changed_files"],
        "agents_files": inputs["agents_files"],
        "memory_hit_count": inputs["memory_hit_count"],
        "contains_worker_chat": False,
        "truncated_sections": inputs["truncated_sections"],
        "evidence_consistent": inputs["evidence_consistent"],
        "evidence_issues": inputs["evidence_issues"],
        "evidence_diagnostics": inputs["evidence_diagnostics"],
        "source_snapshot_id": inputs["source_snapshot_id"],
        "source_workspace_fingerprint": inputs["source_workspace_fingerprint"],
        "current_workspace_fingerprint": inputs["current_workspace_fingerprint"],
        "current_index_flags_sha256": inputs["current_index_flags_sha256"],
        "current_unsafe_index_paths": inputs["current_unsafe_index_paths"],
        "source_untracked_content_complete": inputs[
            "source_untracked_content_complete"
        ],
        "current_untracked_content_complete": inputs[
            "current_untracked_content_complete"
        ],
        "reviewer_start_workspace_fingerprint": inputs["reviewer_start_workspace_fingerprint"],
        "reviewer_end_workspace_fingerprint": inputs["reviewer_end_workspace_fingerprint"],
        "workspace_changed_during_review": inputs["workspace_changed_during_review"],
        "review_execution_issues": inputs["review_execution_issues"],
        "risk_gate": inputs.get("risk_gate"),
    }
    return redact_value(context)


def parse_review_verdict(output: str, error: str | None = None) -> ReviewVerdict:
    if error is not None:
        return _needs_human_verdict(f"reviewer runner 执行失败：{error}")
    try:
        return _extract_review_verdict(output)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        message = f"reviewer 输出无法解析为 verdict JSON：{type(exc).__name__}"
        if str(exc) == "multiple review verdict json candidates found":
            message = f"{message}；检测到多个合法 verdict 候选"
        return _needs_human_verdict(message)


def _review_verdict_from_result(result: RunnerResult) -> ReviewVerdict:
    if result.termination_unconfirmed:
        return _needs_human_verdict(
            "reviewer owned process tree 终止未确认，未读取或采用 runner 输出。"
        )
    if not review_result_is_trusted(result):
        return _needs_human_verdict(
            "reviewer Runner 未形成可采信结论"
            f"（{review_result_diagnostic(result)}），未读取或采用 runner 输出。"
        )
    return parse_review_verdict(result.output)


def _append_review_eval_outcome(
    eval_results: list[str],
    *,
    result: RunnerResult,
    review_execution_issues: list[str],
    pre_review_evidence_issues: list[str],
    metrics: PromptMetrics,
    risk_gate_result: GateResult | None,
    verdict: ReviewVerdict,
    risk_disclosure_issues: list[str],
) -> None:
    eval_results.extend(
        f"FAIL: required risk disclosure：{issue}"
        for issue in risk_disclosure_issues
    )
    if result.termination_unconfirmed:
        eval_results.append(
            "FAIL: reviewer owned process tree 终止未确认，未读取或采用 runner 输出"
        )
    elif review_execution_issues:
        eval_results.append("FAIL: reviewer 执行期间工作区发生变化或无法完成快照校验")
    elif pre_review_evidence_issues:
        return
    elif metrics.exceeded:
        eval_results.append("FAIL: reviewer prompt 超过上下文预算，未启动外部 runner")
    elif risk_gate_result is None:
        eval_results.append("FAIL: review 风险门禁评估失败，未启动外部 runner")
    elif not review_result_is_trusted(result):
        eval_results.append(
            "FAIL: reviewer Runner 未形成可采信结论"
            f"（{review_result_diagnostic(result)}），"
            "未读取或采用 runner 输出"
        )
    elif risk_gate_result.recommendation == "human-review":
        eval_results.append(
            required_review_outcome_line(
                risk_gate_result,
                verdict,
                risk_disclosure_issues,
            )
        )
    elif verdict.verdict == "needs_human":
        eval_results.append("FAIL: reviewer 输出需要人工处理")
    else:
        eval_results.append("PASS: reviewer 输出 verdict 可解析")


def _write_runner_status_report(
    run_dir: Path,
    result: RunnerResult,
    *,
    step: str,
) -> str | None:
    if (
        not result.termination_unconfirmed
        and result.status not in {"error", "timed_out", "stopped"}
    ):
        return None
    if result.termination_unconfirmed:
        filename = "runner-error-report.md"
        title = "Runner Termination Report"
        conclusion = (
            "reviewer owned process tree 终止未确认，未读取或采用 runner 输出。"
        )
    elif result.status == "timed_out":
        filename = "timeout-report.md"
        title = "Timeout Report"
        conclusion = "reviewer 单次执行已超时，未把超时视为审查通过。"
    elif result.status == "stopped":
        filename = "stop-report.md"
        title = "Stop Report"
        conclusion = "reviewer 已按 stop request 停止，未继续给出自动通过结论。"
    else:
        filename = "runner-error-report.md"
        title = "Runner Error Report"
        conclusion = "reviewer runner 异常退出，未产生可信审查结论。"
    report = "\n".join(
        [
            f"# {title}",
            "",
            f"- 步骤：`{step}`",
            f"- 状态：`{result.status}`",
            f"- 原因：{result.error or '未提供'}",
            "",
            "## 结论",
            "",
            f"- {conclusion}",
            "- run 已进入 `needs_human`，不会自动提交、推送或发布。",
        ]
    ).rstrip() + "\n"
    run_dir.joinpath(filename).write_text(redact_text(report), encoding="utf-8")
    return filename


def run_review_pack_eval(run_dir: Path, artifacts: list[str]) -> list[str]:
    results = [f"{'PASS' if (run_dir / item).exists() else 'FAIL'}: artifact 存在：{item}" for item in artifacts]
    prompt = run_dir / "review-prompt.md"
    prompt_text = ""
    if prompt.exists():
        prompt_text = prompt.read_text(encoding="utf-8", errors="replace")
        results.append("PASS: review prompt 标记不包含 worker 聊天" if "worker 的完整聊天记录" in prompt_text else "FAIL: review prompt 缺少隔离说明")
    metrics_path = run_dir / "review-prompt-metrics.json"
    if metrics_path.exists():
        metrics = PromptMetrics.model_validate_json(metrics_path.read_text(encoding="utf-8"))
        results.append(
            "FAIL: review prompt 超过上下文预算"
            if metrics.exceeded
            else "PASS: review prompt 未超过上下文预算"
        )
    context = _read_json(run_dir / "review-context.json")
    truncated_sections = context.get("truncated_sections") or []
    if truncated_sections:
        results.append("WARN: review evidence 已截断：" + ", ".join(truncated_sections))
    else:
        results.append("PASS: review evidence 未截断")
    evidence_issues = context.get("evidence_issues") or []
    if evidence_issues:
        results.append("FAIL: review 证据与当前工作区不属于同一快照")
        results.extend(
            f"FAIL: review evidence issue：{issue}"
            for issue in evidence_issues
        )
        results.extend(
            f"FAIL: review evidence diagnostic：{diagnostic}"
            for diagnostic in (context.get("evidence_diagnostics") or [])
        )
    else:
        results.append("PASS: review 证据与当前工作区属于同一快照")
    risk_gate = context.get("risk_gate")
    if risk_gate is not None:
        if not isinstance(risk_gate, dict):
            results.append("FAIL: review 风险门禁记录格式不合法")
        elif risk_gate.get("status") != "success":
            results.append("FAIL: review 风险门禁评估失败")
        else:
            try:
                result = GateResult.model_validate(risk_gate.get("result"))
            except ValidationError:
                results.append("FAIL: review 风险门禁结果格式不合法")
            else:
                if "review-verdict.json" in artifacts:
                    results.extend(
                        run_required_review_pack_eval(
                            result,
                            run_dir / "review-verdict.json",
                        )
                    )
                else:
                    results.extend(
                        run_required_review_prompt_eval(
                            result,
                            prompt_text,
                        )
                    )
    return results


def render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")


def _write_review_pack_artifacts(run_dir: Path, inputs: dict[str, Any]) -> PromptMetrics:
    run_dir.joinpath("review-checklist.md").write_text(
        redact_text(render_review_checklist()),
        encoding="utf-8",
    )
    review_pack = render_review_pack(inputs)
    review_prompt = render_review_prompt(inputs)
    run_dir.joinpath("review-pack.md").write_text(review_pack, encoding="utf-8")
    run_dir.joinpath("review-prompt.md").write_text(review_prompt, encoding="utf-8")
    run_dir.joinpath("project-context.md").write_text(
        redact_text(inputs["project_context"]),
        encoding="utf-8",
    )
    run_dir.joinpath("review-context.json").write_text(
        json.dumps(render_review_context(inputs), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    metrics = measure_prompt(
        review_prompt,
        role="reviewer",
        max_chars=inputs["reviewer_prompt_max_chars"],
        sections={
            "source_brief": inputs["source_brief"],
            "reflection": inputs["reflection"],
            "diff_summary": inputs["diff_summary"],
            "test_summary": inputs["test_summary"],
            "project_context": inputs["project_context"],
            "full_diff": inputs["full_diff"],
        },
    )
    write_prompt_metrics(run_dir, "review-prompt", metrics)
    return metrics


def _capture_post_review_workspace(
    run_dir: Path,
    workspace: Path,
    repo_path: Path,
    inputs: dict[str, Any],
    *,
    reviewer_started: bool,
    reviewer_start_fingerprint: str,
    termination_unconfirmed: bool = False,
) -> list[str]:
    issues: list[str] = []
    end_fingerprint = reviewer_start_fingerprint
    if termination_unconfirmed:
        end_fingerprint = ""
        issues.append("reviewer_termination_unconfirmed")
    elif reviewer_started:
        try:
            end_fingerprint = capture_runtime_workspace(workspace, repo_path).fingerprint
        except (OSError, RuntimeError, subprocess.SubprocessError):
            issues.append("workspace_snapshot_failed_after_reviewer")
        else:
            if end_fingerprint != reviewer_start_fingerprint:
                issues.append("workspace_changed_during_review")
    inputs["reviewer_start_workspace_fingerprint"] = reviewer_start_fingerprint
    inputs["reviewer_end_workspace_fingerprint"] = end_fingerprint
    inputs["workspace_changed_during_review"] = "workspace_changed_during_review" in issues
    inputs["review_execution_issues"] = issues
    inputs["evidence_issues"] = list(dict.fromkeys([*inputs["evidence_issues"], *issues]))
    inputs["evidence_consistent"] = not inputs["evidence_issues"]
    run_dir.joinpath("review-context.json").write_text(
        json.dumps(render_review_context(inputs), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return issues


def _enforce_unchanged_review_workspace(
    verdict: ReviewVerdict,
    inputs: dict[str, Any],
    issues: list[str],
) -> ReviewVerdict:
    if not issues:
        return _redact_review_verdict(verdict)
    if "workspace_changed_during_review" in issues:
        summary = "reviewer 执行期间工作区发生变化，审查结论已失效。"
        title = "Reviewer 执行期间工作区发生变化"
        recommendation = "保留现场并重新执行 reflect/review，由人工确认变化来源。"
    elif "reviewer_termination_unconfirmed" in issues:
        summary = "reviewer owned process tree 终止未确认，审查结论不可采用。"
        title = "Reviewer 进程树终止未确认"
        recommendation = "人工核对 execution 证据和系统进程，确认现场稳定后重新审查。"
    else:
        summary = "reviewer 返回后无法完成工作区快照校验，不能信任自动审查结论。"
        title = "Reviewer 返回后的工作区快照校验失败"
        recommendation = "检查 Git 工作区状态和快照错误后，由人工重新执行审查。"
    evidence = (
        f"issues={','.join(issues)}；"
        f"start={inputs['reviewer_start_workspace_fingerprint']}；"
        f"end={inputs['reviewer_end_workspace_fingerprint']}"
    )
    return _redact_review_verdict(
        ReviewVerdict(
            verdict="needs_human",
            summary=summary,
            findings=[
                ReviewFinding(
                    severity="blocker",
                    title=title,
                    evidence=evidence,
                    recommendation=recommendation,
                )
            ],
            checked_items=[*verdict.checked_items, "reviewer 执行前后工作区快照"],
        )
    )


def _redact_runner_result(result: RunnerResult) -> RunnerResult:
    return RunnerResult(
        status=result.status,
        output=(
            ""
            if result.termination_unconfirmed
            else redact_text(result.output)
        ),
        error=(
            redact_text(result.error)
            if result.error is not None
            else None
        ),
        command=[redact_text(item) for item in (result.command or [])],
        termination_unconfirmed=result.termination_unconfirmed,
    )


def _redacted_model_json(verdict: ReviewVerdict) -> str:
    return json.dumps(
        redact_value(verdict.model_dump(mode="json")),
        ensure_ascii=False,
        indent=2,
    )


def _read_source_brief_artifact(
    workspace: Path,
    source_run: object,
    repo_path: Path,
) -> tuple[str, list[str], list[str]]:
    if not source_run:
        return "", [], []
    if not isinstance(source_run, str):
        issue = "source_brief_run_invalid"
        return "", [issue], [f"{issue}: 上游 source_run 不是字符串"]
    try:
        run_dir = resolve_run_dir(workspace, source_run)
    except (FileNotFoundError, ValueError) as exc:
        issue = "source_brief_run_invalid"
        return "", [issue], [f"{issue}: 无法解析上游 source_run：{type(exc).__name__}"]
    state_path = run_dir / "state.json"
    if not state_path.exists():
        issue = "source_brief_state_missing"
        return "", [issue], [f"{issue}: 上游 source_run 缺少 state.json"]
    try:
        source_state = BriefState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as exc:
        issue = "source_brief_state_invalid"
        return "", [issue], [f"{issue}: 上游 state.json 无法验证：{type(exc).__name__}"]

    issues: list[str] = []
    diagnostics: list[str] = []
    if source_state.run_id != run_dir.name:
        issues.append("source_brief_run_id_mismatch")
        diagnostics.append(
            "source_brief_run_id_mismatch: state.run_id 与 source run 目录不一致"
        )
    if Path(source_state.repo_path).resolve() != repo_path.resolve():
        issues.append("source_brief_repo_mismatch")
        diagnostics.append(
            "source_brief_repo_mismatch: source brief 与当前仓库不一致"
        )
    if source_state.status != "success":
        issues.append("source_brief_state_not_success")
        diagnostics.append(
            f"source_brief_state_not_success: source brief 状态为 {source_state.status}"
        )
    if issues:
        return "", list(dict.fromkeys(issues)), list(dict.fromkeys(diagnostics))
    brief_text, brief_issues, brief_diagnostics = _read_text_artifact(
        run_dir / "agent-brief.md",
        "source_brief",
    )
    return (
        brief_text,
        list(dict.fromkeys([*issues, *brief_issues])),
        list(dict.fromkeys([*diagnostics, *brief_diagnostics])),
    )


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text_artifact(
    path: Path,
    issue_prefix: str,
) -> tuple[str, list[str], list[str]]:
    if not path.exists():
        issue = f"{issue_prefix}_missing"
        return "", [issue], [f"{issue}: 缺少 {path.name}"]
    try:
        return path.read_text(encoding="utf-8", errors="replace"), [], []
    except OSError as exc:
        issue = f"{issue_prefix}_unreadable"
        return "", [issue], [f"{issue}: {path.name} 无法读取：{type(exc).__name__}"]


def _read_json_artifact(
    path: Path,
    issue_prefix: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    text, issues, diagnostics = _read_text_artifact(path, issue_prefix)
    if issues:
        return {}, issues, diagnostics
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        issue = f"{issue_prefix}_invalid"
        return {}, [issue], [f"{issue}: {path.name} 无法解析：{type(exc).__name__}"]
    if not isinstance(payload, dict):
        issue = f"{issue_prefix}_invalid"
        return {}, [issue], [f"{issue}: {path.name} 顶层必须是 JSON object"]
    return payload, [], []


def _extract_review_verdict(text: str) -> ReviewVerdict:
    # codex exec 这类 runner 可能在 JSON 后追加 transcript/token 统计；
    # 允许 transcript 重复最终结果，但不同合法 verdict 仍视为歧义输出。
    verdicts: list[ReviewVerdict] = []
    for candidate in _iter_json_object_candidates(text):
        try:
            data = json.loads(candidate)
            verdicts.append(ReviewVerdict.model_validate(data))
        except (json.JSONDecodeError, ValidationError, TypeError):
            continue
    if not verdicts:
        raise ValueError("review verdict json not found")
    if any(verdict != verdicts[0] for verdict in verdicts[1:]):
        raise ValueError("multiple review verdict json candidates found")
    return verdicts[0]


def _iter_json_object_candidates(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty output")

    candidates: list[str] = []
    for start, char in enumerate(stripped):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(stripped)):
            current = stripped[end]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(stripped[start : end + 1])
                    break
    if not candidates:
        raise ValueError("json object not found")
    return candidates


def _needs_human_verdict(summary: str) -> ReviewVerdict:
    return ReviewVerdict(
        verdict="needs_human",
        summary=summary,
        findings=[
            ReviewFinding(
                severity="major",
                title="需要人工处理 reviewer 输出",
                evidence=summary,
                recommendation="检查 review-runner-output.txt，必要时手动审查或重新运行 reviewer。",
            )
        ],
        checked_items=["runner-output-parse"],
    )


def _enforce_complete_review_evidence(
    verdict: ReviewVerdict,
    truncated_sections: list[str],
) -> ReviewVerdict:
    if verdict.verdict != "approve" or not truncated_sections:
        return verdict
    evidence = "、".join(truncated_sections)
    return ReviewVerdict(
        verdict="needs_human",
        summary="reviewer 输入存在截断证据，不能把部分审查视为完整通过。",
        findings=[
            ReviewFinding(
                severity="major",
                title="Review 证据不完整",
                evidence=f"以下 sections 已截断：{evidence}",
                recommendation="缩小任务或 diff，或由人工检查完整证据后再决定是否通过。",
            )
        ],
        checked_items=[*verdict.checked_items, "上下文完整性"],
    )


def _new_run_id(suffix: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{suffix}"


def _truncate_with_status(
    text: str,
    max_chars: int = MAX_TEXT_CHARS,
) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n\n...（已截断）\n", True


def _has_failures(results: list[str]) -> bool:
    return any(item.startswith("FAIL:") for item in results)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
