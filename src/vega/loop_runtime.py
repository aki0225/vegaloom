from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import ValidationError

from .brief_runtime import BriefRuntime
from .execution_control import RunnerExecutionContext, inspect_execution_for_recovery
from .gate_runtime import evaluate_risk, render_gate_report
from .loop_evidence import validate_loop_evidence_snapshot
from .loop_integrity import trusted_verification_passed
from .loop_prompts import (
    build_worker_prompt,
    render_fix_prompt,
    render_loop_plan,
    render_no_diff_fix_prompt,
    render_risk_gate_fix_prompt,
    render_scope_gate_fix_prompt,
    render_tracked_baseline_fix_prompt,
    render_untracked_files_fix_prompt,
    render_verification_fix_prompt,
    render_workspace_fix_prompt,
)
from .models import (
    BriefInput,
    BriefState,
    GateResult,
    LoopAutomationState,
    LoopIterationState,
    ReviewVerdict,
    SupersededTerminalRecord,
)
from .project_config import (
    ProjectConfig,
    load_project_config,
    project_policy_snapshot,
    scope_policy_sha256,
)
from .prompt_metrics import (
    PromptMetrics,
    measure_prompt,
    write_context_budget_report,
    write_prompt_metrics,
)
from .redaction import redact_text, redact_value
from .reflect_runtime import ReflectRuntime
from .review_runtime import PrecomputedReviewRiskGate, ReviewRuntime
from .risk_gate_evidence import (
    render_risk_gate_report_binding,
    sha256_text,
    validate_iteration_risk_gate_artifacts,
)
from .run_lock import RunMutationLock
from .run_utils import create_run_dir, resolve_run_dir, run_name
from .runner import Runner, RunnerResult, RunnerStatus, make_runner
from .scope_gate import (
    LoopScopeGateEvidence,
    scope_gate_state_fields,
    validate_iteration_scope_gate_artifacts,
    write_loop_scope_gate_evidence,
)
from .trace import TraceWriter, active_run_finished_indices, read_trace_items
from .verification import VerificationRunResult, run_project_verification
from .workspace_check import read_head_sha, run_workspace_check, snapshot_workspace

LOOP_INITIALIZATION_ARTIFACTS = [
    "agent-brief.md",
    "project-context.md",
    "project-policy-snapshot.json",
    "loop-plan.md",
    "worker-prompt.md",
    "worker-prompt-metrics.json",
    "worker-prompt-metrics.md",
]
LOOP_ARTIFACTS = [
    "state.json",
    "trace.jsonl",
    *LOOP_INITIALIZATION_ARTIFACTS,
    "eval.md",
]
FINAL_LOOP_ARTIFACTS = [*LOOP_ARTIFACTS, "final-report.md"]
RISK_GATE_RESULT_ARTIFACT = "risk-gate-result.json"
RISK_GATE_REPORT_ARTIFACT = "risk-gate-report.md"


@dataclass(frozen=True)
class LoopRiskGateEvidence:
    result: GateResult | None
    error: str | None
    source_run: str
    result_sha256: str
    report_sha256: str

    @property
    def status(self) -> Literal["success", "failed"]:
        return "failed" if self.error else "success"


class LoopAutomationRuntime:
    def __init__(
        self,
        workspace: Path,
        worker_runner: Runner | None = None,
        reviewer_runner: Runner | None = None,
        timeout_seconds: int = 900,
        progress_reporter: Callable[[str, int], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.worker_runner = worker_runner
        self.reviewer_runner = reviewer_runner
        self.timeout_seconds = timeout_seconds
        self.progress_reporter = progress_reporter

    def start(
        self,
        brief_input: BriefInput,
        automation_mode: Literal["assist", "auto"],
        worker_name: str = "codex-exec",
        reviewer_name: str = "codex-exec",
        max_iterations: int = 2,
        verify: bool = True,
    ) -> Path:
        repo_path = Path(brief_input.repo_path).resolve()
        config, policy_snapshot, initial_head_sha = _load_stable_start_policy(
            repo_path
        )
        worker_name, reviewer_name = _apply_runner_defaults(
            config,
            worker_name,
            reviewer_name,
        )
        run_id, run_dir = create_run_dir(
            self.workspace,
            _new_loop_run_id(brief_input.mode),
        )
        with RunMutationLock.acquire(run_dir, "loop.start"):
            return self._start_locked(
                brief_input,
                automation_mode,
                worker_name,
                reviewer_name,
                max_iterations,
                verify,
                run_id,
                run_dir,
                config,
                policy_snapshot,
                initial_head_sha,
            )

    def _start_locked(
        self,
        brief_input: BriefInput,
        automation_mode: Literal["assist", "auto"],
        worker_name: str,
        reviewer_name: str,
        max_iterations: int,
        verify: bool,
        run_id: str,
        run_dir: Path,
        config: ProjectConfig,
        policy_snapshot: dict[str, str | None],
        initial_head_sha: str,
    ) -> Path:
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = LoopAutomationState(
            run_id=run_id,
            task_mode=brief_input.mode,
            automation_mode=automation_mode,
            repo_path=brief_input.repo_path,
            input_source=brief_input.source,
            status="running",
            max_iterations=max_iterations,
            initial_head_sha=initial_head_sha,
            project_policy_snapshot=policy_snapshot,
            project_policy_snapshot_sha256=sha256_text(
                _project_policy_snapshot_text(policy_snapshot)
            ),
            scope_gate_required=True,
            scope_policy_sha256=scope_policy_sha256(config.scope),
            verification_artifact_version=2,
        )
        state.current_step = "brief"
        state.save(run_dir / "state.json")
        trace.write(
            "loop_started",
            task_mode=brief_input.mode,
            automation_mode=automation_mode,
            repo_path=brief_input.repo_path,
        )
        _write_project_policy_snapshot(run_dir, state.project_policy_snapshot)
        brief_run = BriefRuntime(self.workspace).run(brief_input)
        state.brief_run = run_name(brief_run)
        # brief 子 run 已形成后立即绑定根状态。若随后崩溃，recovery 至少能判断
        # 原始任务身份；若在此保存前崩溃，continue 会因缺少绑定而 fail closed。
        state.save(run_dir / "state.json")
        _copy_if_exists(brief_run / "agent-brief.md", run_dir / "agent-brief.md")
        _copy_if_exists(brief_run / "project-context.md", run_dir / "project-context.md")
        trace.write("brief_finished", brief_run=state.brief_run)
        _write_text_artifact(
            run_dir / "loop-plan.md",
            render_loop_plan(brief_input, automation_mode, max_iterations),
        )
        worker_prompt, worker_sections = build_worker_prompt(
            brief_input,
            brief_run,
            None,
            1,
        )
        _write_text_artifact(run_dir / "worker-prompt.md", worker_prompt)
        worker_metrics = measure_prompt(
            worker_prompt,
            role="worker",
            max_chars=config.prompt_budget.worker_max_chars,
            sections=worker_sections,
        )
        write_prompt_metrics(run_dir, "worker-prompt", worker_metrics)
        trace.write("worker_prompt_measured", metrics=worker_metrics.model_dump())
        trace.write(
            "loop_initialized",
            brief_run=state.brief_run,
            artifacts=LOOP_INITIALIZATION_ARTIFACTS,
        )
        if automation_mode == "assist":
            root_budget_artifact: str | None = None
            if worker_metrics.exceeded:
                root_budget_artifact = write_context_budget_report(
                    run_dir,
                    "worker",
                    worker_metrics,
                )
            state.current_step = "waiting_for_worker"
            state.status = "needs_human"
            state.artifacts = [
                *LOOP_ARTIFACTS,
                *([root_budget_artifact] if root_budget_artifact else []),
            ]
            trace.write("loop_waiting_for_worker", worker_prompt="worker-prompt.md")
            _finalize_loop_eval(
                run_dir,
                state,
                "needs_human",
                state.artifacts,
                trace,
            )
            return run_dir
        run_dir = self._run_auto_iterations(
            run_dir,
            state,
            brief_input,
            worker_name,
            reviewer_name,
            verify,
            config,
        )
        return run_dir

    def continue_assist(
        self,
        run: str,
        repo_path: Path,
        reviewer_name: str = "codex-exec",
        test_log: Path | None = None,
        note: str | None = None,
        verify: bool = True,
    ) -> Path:
        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "loop.continue"):
            return self._continue_assist_locked(
                run_dir,
                repo_path,
                reviewer_name=reviewer_name,
                test_log=test_log,
                note=note,
                verify=verify,
            )

    def _continue_assist_locked(
        self,
        run_dir: Path,
        repo_path: Path,
        reviewer_name: str = "codex-exec",
        test_log: Path | None = None,
        note: str | None = None,
        verify: bool = True,
    ) -> Path:
        state = LoopAutomationState.model_validate_json(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
        if state.run_id != run_dir.name:
            raise ValueError(
                "loop state.run_id 与 run 目录身份不一致；"
                "为避免在错误证据链上继续，已拒绝 continue。"
            )
        if state.automation_mode not in {"assist", "auto"}:
            raise ValueError("只有 assist/auto loop 可以使用 continue")
        repo = repo_path.resolve()
        expected_repo = Path(state.repo_path).resolve()
        if repo != expected_repo:
            raise ValueError(
                f"loop continue 目标仓库不匹配：run={expected_repo}，传入={repo}"
            )
        if state.status != "needs_human":
            raise ValueError(
                f"只有 needs_human 状态的 loop 可以 continue，当前状态：{state.status}"
            )
        _require_execution_recoverable(run_dir)
        _require_recovery_trace_binding(run_dir, state)
        _require_loop_initialization(self.workspace, run_dir, state, repo)
        if _project_policy_changed(repo, state.project_policy_snapshot):
            trace = TraceWriter(run_dir / "trace.jsonl")
            iteration_number = _next_iteration_number(run_dir, state)
            iteration_dir = _iteration_dir(run_dir, iteration_number)
            _write_project_policy_change_report(
                iteration_dir,
                state.project_policy_snapshot,
                project_policy_snapshot(repo),
            )
            state.current_iteration = iteration_number
            state.iterations.append(
                LoopIterationState(
                    iteration=iteration_number,
                    worker_status="skipped",
                )
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "项目策略文件在 loop 启动后发生变化，未继续自动验证和审查。",
            )
            trace.write("project_policy_changed", iteration=iteration_number)
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="project_policy_changed",
            )
            return run_dir
        config = load_project_config(repo)
        _, reviewer_name = _apply_runner_defaults(config, "codex-exec", reviewer_name)
        trace = TraceWriter(run_dir / "trace.jsonl")
        iteration_number = _next_iteration_number(run_dir, state)
        iteration_state = LoopIterationState(
            iteration=iteration_number,
            worker_status="skipped",
        )
        state.status = "running"
        state.current_iteration = iteration_number
        state.current_step = "workspace_check"
        state.save(run_dir / "state.json")
        iteration_dir = _iteration_dir(run_dir, iteration_number)
        trace.write("assist_continue_started", iteration=iteration_number)
        _write_text_artifact(
            iteration_dir / "worker-output.txt",
            f"{state.automation_mode} continue 模式下，worker 是主会话或人工；Vega 只记录当前工作区 diff。\n",
        )
        workspace_check = run_workspace_check(
            repo,
            iteration_dir,
            require_clean_untracked=True,
        )
        trace.write(
            "workspace_check_finished",
            iteration=iteration_number,
            status=workspace_check.status,
            new_untracked_count=workspace_check.new_untracked_count,
            baseline_untracked_changed=workspace_check.baseline_untracked_changed,
        )
        iteration_state = _update_iteration_state(
            iteration_state,
            workspace_status=workspace_check.status,
            workspace_new_files_count=workspace_check.new_untracked_count,
        )
        if workspace_check.has_failures:
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            if workspace_check.new_untracked_count:
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_untracked_files_fix_prompt(
                        iteration_number + 1,
                        workspace_check.new_untracked_files,
                    ),
                )
                trace.write(
                    "untracked_files_require_human",
                    iteration=iteration_number,
                    count=workspace_check.new_untracked_count,
                    paths=workspace_check.new_untracked_files,
                )
                conclusion = (
                    "当前工作区存在未跟踪文件；reviewer 不读取其内容，"
                    "已在 verification 前转人工确认。"
                )
                current_step = "untracked_files"
            else:
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_workspace_fix_prompt(iteration_number + 1),
                )
                conclusion = "工作区完整性检查失败，未继续 verification、reflect 或 review。"
                current_step = "workspace_check_failed"
            _write_final_report(run_dir, state, None, conclusion)
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step=current_step,
            )
            return run_dir
        state.current_step = "scope_gate"
        state.save(run_dir / "state.json")
        scope_evidence = write_loop_scope_gate_evidence(
            repo,
            config.scope,
            iteration_dir,
            trace,
            iteration=iteration_number,
            phase="pre_verification",
            expected_head_sha=state.initial_head_sha,
            expected_policy_sha256=state.scope_policy_sha256,
        )
        iteration_state = _update_iteration_state(
            iteration_state,
            **scope_gate_state_fields(scope_evidence, phase="pre_verification"),
        )
        if not scope_evidence.passed:
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_scope_gate_fix_prompt(iteration_number + 1, scope_evidence),
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "精确路径范围门禁拒绝当前 tracked diff，未继续 verification、Reflect 或 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="scope_gate_failed",
            )
            return run_dir
        auto_test_log = test_log
        verification: VerificationRunResult | None = None
        verification_failure_kind = None
        if verify and auto_test_log is None:
            state.current_step = "verify"
            state.save(run_dir / "state.json")
            verification = run_project_verification(
                self.workspace,
                repo,
                iteration_dir,
                iteration=iteration_number,
                progress_reporter=self.progress_reporter,
            )
            verification_status = _verification_status(
                verification.command_count,
                verification.failed_count,
                verification.failure_kind,
            )
            verification_failed_count = verification.failed_count
            verification_failure_kind = verification.failure_kind
            trace.write(
                "verification_finished",
                iteration=iteration_number,
                commands=verification.command_count,
                failed=verification.failed_count,
                failure_kind=verification.failure_kind,
                interruption_status=verification.interruption_status,
            )
            auto_test_log = verification.summary_path
        else:
            verification_status = "skipped"
            verification_failed_count = 0
        iteration_state = _update_iteration_state(
            iteration_state,
            verification_status=verification_status,
            verification_failed_count=verification_failed_count,
            verification_failure_kind=verification_failure_kind,
        )
        if self._pause_for_terminal_verification_result(
            run_dir,
            state,
            iteration_dir,
            iteration_number,
            iteration_state,
            verification,
            trace,
            worker_status="skipped",
            scope_evidence=scope_evidence,
        ):
            return run_dir

        current_policy = project_policy_snapshot(repo)
        if _project_policy_changed(repo, state.project_policy_snapshot):
            if verification_status != "skipped" and auto_test_log is not None:
                _copy_if_exists(auto_test_log, iteration_dir / "test-summary.md")
            _write_project_policy_change_report(
                iteration_dir,
                state.project_policy_snapshot,
                current_policy,
            )
            trace.write("project_policy_changed", iteration=iteration_number)
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_final_report(
                run_dir,
                state,
                None,
                "verification 修改了项目策略文件，已停止 Reflect 和隔离审查。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="project_policy_changed",
            )
            return run_dir

        state.current_step = "scope_gate_post_verification"
        state.save(run_dir / "state.json")
        post_scope_evidence = write_loop_scope_gate_evidence(
            repo,
            config.scope,
            iteration_dir,
            trace,
            iteration=iteration_number,
            phase="post_verification",
            expected_head_sha=state.initial_head_sha,
            expected_policy_sha256=state.scope_policy_sha256,
        )
        iteration_state = _update_iteration_state(
            iteration_state,
            **scope_gate_state_fields(
                post_scope_evidence,
                phase="post_verification",
            ),
        )
        if not post_scope_evidence.passed:
            if verification_status != "skipped" and auto_test_log is not None:
                _copy_if_exists(auto_test_log, iteration_dir / "test-summary.md")
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_scope_gate_fix_prompt(iteration_number + 1, post_scope_evidence),
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "verification 后的精确路径范围门禁拒绝当前 tracked diff，未继续 Reflect 或 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="scope_gate_post_verification_failed",
            )
            return run_dir

        state.current_step = "reflect"
        state.save(run_dir / "state.json")
        reflect_run = ReflectRuntime(self.workspace).run(
            repo,
            source_run=state.brief_run,
            test_log=auto_test_log.resolve() if auto_test_log else None,
            note=note,
        )
        _record_reflect(iteration_dir, reflect_run)
        iteration_state = _update_iteration_state(
            iteration_state,
            reflect_run=run_name(reflect_run),
        )
        if not _reflect_run_succeeded(reflect_run):
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_reflect_failure_report(iteration_dir, reflect_run)
            trace.write(
                "reflect_failed",
                iteration=iteration_number,
                reflect_run=run_name(reflect_run),
                status=_read_reflect_run_status(reflect_run),
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "Reflect 的确定性证据检查失败，未启动隔离 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="reflect_failed",
            )
            return run_dir
        trace.write(
            "reflect_finished",
            iteration=iteration_number,
            reflect_run=run_name(reflect_run),
            status="success",
        )
        current_policy = project_policy_snapshot(repo)
        if current_policy != state.project_policy_snapshot:
            self._pause_for_project_policy_change_after_reflect(
                run_dir,
                state,
                iteration_dir,
                iteration_number,
                reflect_run,
                trace,
                worker_status="skipped",
                workspace_status=workspace_check.status,
                workspace_new_files_count=workspace_check.new_untracked_count,
                verification_status=verification_status,
                verification_failed_count=verification_failed_count,
                scope_evidence=scope_evidence,
                post_scope_evidence=post_scope_evidence,
                current_policy=current_policy,
            )
            return run_dir
        state.current_step = "scope_gate_pre_review"
        state.save(run_dir / "state.json")
        review_scope_evidence = write_loop_scope_gate_evidence(
            repo,
            config.scope,
            iteration_dir,
            trace,
            iteration=iteration_number,
            phase="pre_review",
            expected_head_sha=state.initial_head_sha,
            expected_policy_sha256=state.scope_policy_sha256,
        )
        iteration_state = _update_iteration_state(
            iteration_state,
            **scope_gate_state_fields(
                review_scope_evidence,
                phase="pre_review",
            ),
        )
        if not review_scope_evidence.passed:
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_scope_gate_fix_prompt(iteration_number + 1, review_scope_evidence),
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "Reflect 后的精确路径范围门禁拒绝当前 tracked diff，未继续风险门禁或 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="scope_gate_pre_review_failed",
            )
            return run_dir
        if not _reflect_has_tracked_diff(reflect_run):
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_no_diff_fix_prompt(iteration_number + 1),
            )
            trace.write("zero_diff_requires_human", iteration=iteration_number)
            _write_final_report(
                run_dir,
                state,
                None,
                "当前工作区没有可审查的 tracked diff，不能启动隔离 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="no_diff",
            )
            return run_dir
        gate_evidence = _evaluate_loop_risk_gate(
            self.workspace,
            repo,
            reflect_run,
            iteration_dir,
            trace,
            iteration_number,
        )
        gate_result = gate_evidence.result
        iteration_state = _update_iteration_state(
            iteration_state,
            **_risk_gate_state_fields(gate_evidence),
        )
        if (
            gate_evidence.error
            or gate_result is None
            or gate_result.recommendation == "human-review"
        ):
            state.iterations.append(iteration_state)
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_risk_gate_fix_prompt(
                    iteration_number + 1,
                    None if gate_evidence.error else gate_result,
                ),
            )
            conclusion = (
                "风险门禁评估失败，未启动隔离 reviewer。"
                if gate_evidence.error or gate_result is None
                else "风险门禁要求人工确认，未继续自动隔离审查。"
            )
            current_step = (
                "risk_gate_failed"
                if gate_evidence.error or gate_result is None
                else "risk_gate_needs_human"
            )
            _write_final_report(run_dir, state, None, conclusion)
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step=current_step,
            )
            return run_dir
        state.current_step = "review"
        state.save(run_dir / "state.json")
        trace.write("review_started", iteration=iteration_number)
        review_run = self._run_review(
            repo,
            reflect_run,
            reviewer_name,
            run_dir,
            iteration_number,
            config,
            gate_result,
            state.project_policy_snapshot,
        )
        verdict = _read_verdict(review_run)
        reviewer_status = _read_review_runner_status(review_run)
        review_run_status = _read_review_run_status(review_run)
        _record_review(iteration_dir, review_run)
        trace.write(
            "review_finished",
            iteration=iteration_number,
            status=review_run_status,
            verdict=verdict.verdict,
        )
        iteration_state = _update_iteration_state(
            iteration_state,
            reviewer_status=reviewer_status,
            workspace_status="skipped",
            workspace_new_files_count=0,
            review_run=run_name(review_run),
            verdict=verdict.verdict,
            findings_count=len(verdict.findings),
        )
        state.iterations.append(iteration_state)
        state.current_iteration = iteration_number
        if not _review_run_allows_verdict(review_run_status, verdict):
            trace.write(
                "review_run_not_successful",
                iteration=iteration_number,
                status=review_run_status,
            )
            _write_final_report(
                run_dir,
                state,
                verdict,
                f"Review run 自身状态为 {review_run_status}，不能采用其 verdict。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="review_run_failed",
            )
            return run_dir
        self._finish_or_prepare_next(run_dir, state, verdict, iteration_dir, trace)
        return run_dir

    def _run_auto_iterations(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        brief_input: BriefInput,
        worker_name: str,
        reviewer_name: str,
        verify: bool,
        config: ProjectConfig,
    ) -> Path:
        trace = TraceWriter(run_dir / "trace.jsonl")
        repo_path = Path(brief_input.repo_path).resolve()
        previous_verdict: ReviewVerdict | None = None
        worker = self.worker_runner or make_runner(
            worker_name,
            options=config.runner.codex_exec.worker,
        )
        for iteration_number in range(1, state.max_iterations + 1):
            iteration_state = LoopIterationState(
                iteration=iteration_number,
                worker_status="skipped",
            )
            state.current_iteration = iteration_number
            state.current_step = "worker"
            state.save(run_dir / "state.json")
            iteration_dir = _iteration_dir(run_dir, iteration_number)
            prompt, prompt_sections = build_worker_prompt(
                brief_input,
                resolve_run_dir(self.workspace, state.brief_run or ""),
                previous_verdict,
                iteration_number,
            )
            _write_text_artifact(iteration_dir / "worker-prompt.md", prompt)
            prompt_metrics = measure_prompt(
                prompt,
                role="worker",
                max_chars=config.prompt_budget.worker_max_chars,
                sections=prompt_sections,
            )
            write_prompt_metrics(iteration_dir, "worker-prompt", prompt_metrics)
            trace.write(
                "worker_prompt_measured",
                iteration=iteration_number,
                metrics=prompt_metrics.model_dump(),
            )
            if prompt_metrics.exceeded:
                write_context_budget_report(iteration_dir, "worker", prompt_metrics)
                state.iterations.append(iteration_state)
                _write_final_report(
                    run_dir,
                    state,
                    previous_verdict,
                    "worker prompt 超过上下文预算，未启动外部 runner。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="worker_context_budget",
                )
                return run_dir
            workspace_baseline = snapshot_workspace(repo_path)
            head_changed_before_worker = bool(
                state.initial_head_sha
                and workspace_baseline.head_sha
                and workspace_baseline.head_sha != state.initial_head_sha
            )
            if not workspace_baseline.capture_complete or (
                iteration_number == 1 and workspace_baseline.has_tracked_changes
            ) or head_changed_before_worker:
                workspace_check = run_workspace_check(
                    repo_path,
                    iteration_dir,
                    baseline=workspace_baseline,
                    expected_head_sha=state.initial_head_sha,
                    allow_existing_tracked_diff=iteration_number > 1,
                )
                iteration_state = _update_iteration_state(
                    iteration_state,
                    workspace_status="failed",
                    workspace_new_files_count=workspace_check.new_untracked_count,
                )
                state.iterations.append(iteration_state)
                if head_changed_before_worker:
                    _write_text_artifact(
                        iteration_dir / "fix-prompt.md",
                        render_workspace_fix_prompt(iteration_number + 1),
                    )
                    conclusion = (
                        "loop 启动后、worker 启动前 Git HEAD 已发生变化；"
                        "为避免在错误提交上执行，未启动自动 worker。"
                    )
                    current_step = "workspace_head_changed"
                elif workspace_baseline.has_tracked_changes:
                    _write_text_artifact(
                        iteration_dir / "fix-prompt.md",
                        render_tracked_baseline_fix_prompt(iteration_number + 1),
                    )
                    conclusion = (
                        "worker 启动前已存在 tracked diff；为避免把历史修改归因于本轮 worker，"
                        "未启动自动执行。"
                    )
                    current_step = "workspace_baseline_dirty"
                else:
                    _write_text_artifact(
                        iteration_dir / "fix-prompt.md",
                        render_workspace_fix_prompt(iteration_number + 1),
                    )
                    conclusion = "worker 启动前无法完整捕获工作区基线，未启动自动执行。"
                    current_step = "workspace_baseline_unavailable"
                trace.write(
                    "workspace_baseline_blocked",
                    iteration=iteration_number,
                    capture_complete=workspace_baseline.capture_complete,
                    tracked_files=len(workspace_baseline.tracked_files),
                )
                _write_final_report(run_dir, state, previous_verdict, conclusion)
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step=current_step,
                )
                return run_dir
            trace.write("worker_started", iteration=iteration_number, runner=worker_name)
            worker_result = worker.run(
                prompt,
                repo_path,
                sandbox="workspace-write",
                timeout_seconds=self.timeout_seconds,
                execution_context=RunnerExecutionContext(
                    execution_dir=iteration_dir / "executions" / "worker",
                    run_id=state.run_id,
                    step="worker",
                    iteration=iteration_number,
                    progress_reporter=self.progress_reporter,
                ),
            )
            worker_output = (
                worker_result.error or ""
                if worker_result.termination_unconfirmed
                else worker_result.output or worker_result.error or ""
            )
            _write_text_artifact(
                iteration_dir / "worker-output.txt",
                worker_output,
            )
            trace.write(
                "worker_finished",
                iteration=iteration_number,
                status=worker_result.status,
                termination_unconfirmed=worker_result.termination_unconfirmed,
            )
            if worker_result.termination_unconfirmed or worker_result.status in {
                "timed_out",
                "stopped",
            }:
                self._finish_interrupted_worker(
                    run_dir,
                    state,
                    iteration_dir,
                    iteration_state,
                    worker_result,
                    trace,
                )
                return run_dir
            if worker_result.status != "success":
                workspace_check = run_workspace_check(
                    repo_path,
                    iteration_dir,
                    baseline=workspace_baseline,
                    allow_existing_tracked_diff=iteration_number > 1,
                )
                trace.write(
                    "workspace_check_finished",
                    iteration=iteration_number,
                    status=workspace_check.status,
                    new_untracked_count=workspace_check.new_untracked_count,
                    baseline_untracked_changed=workspace_check.baseline_untracked_changed,
                )
                iteration_state = _update_iteration_state(
                    iteration_state,
                    worker_status="failed",
                    workspace_status=workspace_check.status,
                    workspace_new_files_count=workspace_check.new_untracked_count,
                )
                state.iterations.append(iteration_state)
                _write_runner_error_report(
                    iteration_dir,
                    step="worker",
                    reason=worker_result.error,
                    workspace_status=workspace_check.raw_status,
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker runner 异常退出，可能已留下部分改动；未继续验证和审查。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="worker_error",
                )
                return run_dir

            iteration_state = _update_iteration_state(
                iteration_state,
                worker_status="success",
            )
            current_policy = project_policy_snapshot(repo_path)
            if _project_policy_changed(repo_path, state.project_policy_snapshot):
                _write_project_policy_change_report(
                    iteration_dir,
                    state.project_policy_snapshot,
                    current_policy,
                )
                trace.write("project_policy_changed", iteration=iteration_number)
                state.iterations.append(iteration_state)
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker 修改了项目策略文件，已停止自动验证和审查。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="project_policy_changed",
                )
                return run_dir

            workspace_check = run_workspace_check(
                repo_path,
                iteration_dir,
                baseline=workspace_baseline,
                allow_existing_tracked_diff=iteration_number > 1,
            )
            trace.write(
                "workspace_check_finished",
                iteration=iteration_number,
                status=workspace_check.status,
                new_untracked_count=workspace_check.new_untracked_count,
                baseline_untracked_changed=workspace_check.baseline_untracked_changed,
            )
            iteration_state = _update_iteration_state(
                iteration_state,
                workspace_status=workspace_check.status,
                workspace_new_files_count=workspace_check.new_untracked_count,
            )
            if workspace_check.has_failures:
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_workspace_fix_prompt(iteration_number + 1),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker 结束后工作区污染检查失败，已停止自动验证和审查。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="workspace_check_failed",
                )
                return run_dir

            if workspace_check.new_untracked_count:
                iteration_state = _update_iteration_state(
                    iteration_state,
                    workspace_status="failed",
                )
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_untracked_files_fix_prompt(
                        iteration_number + 1,
                        workspace_check.new_untracked_files,
                    ),
                )
                trace.write(
                    "untracked_files_require_human",
                    iteration=iteration_number,
                    count=workspace_check.new_untracked_count,
                    paths=workspace_check.new_untracked_files,
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker 新增了未跟踪文件；reviewer 不读取其内容，已转人工确认。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="untracked_files",
                )
                return run_dir

            state.current_step = "scope_gate"
            state.save(run_dir / "state.json")
            scope_evidence = write_loop_scope_gate_evidence(
                repo_path,
                config.scope,
                iteration_dir,
                trace,
                iteration=iteration_number,
                phase="pre_verification",
                expected_head_sha=state.initial_head_sha,
                expected_policy_sha256=state.scope_policy_sha256,
            )
            iteration_state = _update_iteration_state(
                iteration_state,
                **scope_gate_state_fields(scope_evidence, phase="pre_verification"),
            )
            if not scope_evidence.passed:
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_scope_gate_fix_prompt(iteration_number + 1, scope_evidence),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "精确路径范围门禁拒绝当前 tracked diff，未继续 verification、Reflect 或 reviewer。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="scope_gate_failed",
                )
                return run_dir
            verification_log: Path | None = None
            verification_status = "skipped"
            verification_failed_count = 0
            verification_failure_kind = None
            verification: VerificationRunResult | None = None
            if verify:
                state.current_step = "verify"
                state.save(run_dir / "state.json")
                verification = run_project_verification(
                    self.workspace,
                    repo_path,
                    iteration_dir,
                    iteration=iteration_number,
                    progress_reporter=self.progress_reporter,
                )
                verification_log = verification.summary_path
                verification_status = _verification_status(
                    verification.command_count,
                    verification.failed_count,
                    verification.failure_kind,
                )
                verification_failed_count = verification.failed_count
                verification_failure_kind = verification.failure_kind
                trace.write(
                    "verification_finished",
                    iteration=iteration_number,
                    commands=verification.command_count,
                    failed=verification.failed_count,
                    failure_kind=verification.failure_kind,
                    interruption_status=verification.interruption_status,
                )
            iteration_state = _update_iteration_state(
                iteration_state,
                verification_status=verification_status,
                verification_failed_count=verification_failed_count,
                verification_failure_kind=verification_failure_kind,
            )
            if self._pause_for_terminal_verification_result(
                run_dir,
                state,
                iteration_dir,
                iteration_number,
                iteration_state,
                verification,
                trace,
                worker_status="success",
                workspace_status=workspace_check.status,
                workspace_new_files_count=workspace_check.new_untracked_count,
                scope_evidence=scope_evidence,
            ):
                return run_dir

            current_policy = project_policy_snapshot(repo_path)
            if _project_policy_changed(repo_path, state.project_policy_snapshot):
                if verification_status != "skipped" and verification_log is not None:
                    _copy_if_exists(verification_log, iteration_dir / "test-summary.md")
                _write_project_policy_change_report(
                    iteration_dir,
                    state.project_policy_snapshot,
                    current_policy,
                )
                trace.write("project_policy_changed", iteration=iteration_number)
                state.iterations.append(iteration_state)
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "verification 修改了项目策略文件，已停止 Reflect 和隔离审查。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="project_policy_changed",
                )
                return run_dir

            state.current_step = "scope_gate_post_verification"
            state.save(run_dir / "state.json")
            post_scope_evidence = write_loop_scope_gate_evidence(
                repo_path,
                config.scope,
                iteration_dir,
                trace,
                iteration=iteration_number,
                phase="post_verification",
                expected_head_sha=state.initial_head_sha,
                expected_policy_sha256=state.scope_policy_sha256,
            )
            iteration_state = _update_iteration_state(
                iteration_state,
                **scope_gate_state_fields(
                    post_scope_evidence,
                    phase="post_verification",
                ),
            )
            if not post_scope_evidence.passed:
                if verification_status != "skipped" and verification_log is not None:
                    _copy_if_exists(verification_log, iteration_dir / "test-summary.md")
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_scope_gate_fix_prompt(iteration_number + 1, post_scope_evidence),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "verification 后的精确路径范围门禁拒绝当前 tracked diff，未继续 Reflect 或 reviewer。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="scope_gate_post_verification_failed",
                )
                return run_dir

            state.current_step = "reflect"
            state.save(run_dir / "state.json")
            reflect_run = ReflectRuntime(self.workspace).run(
                repo_path,
                source_run=state.brief_run,
                test_log=verification_log,
                note=f"auto loop 第 {iteration_number} 轮执行后复盘",
            )
            _record_reflect(iteration_dir, reflect_run)
            iteration_state = _update_iteration_state(
                iteration_state,
                reflect_run=run_name(reflect_run),
            )
            if not _reflect_run_succeeded(reflect_run):
                state.iterations.append(iteration_state)
                _write_reflect_failure_report(iteration_dir, reflect_run)
                trace.write(
                    "reflect_failed",
                    iteration=iteration_number,
                    reflect_run=run_name(reflect_run),
                    status=_read_reflect_run_status(reflect_run),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "Reflect 的确定性证据检查失败，未启动隔离 reviewer。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="reflect_failed",
                )
                return run_dir
            trace.write(
                "reflect_finished",
                iteration=iteration_number,
                reflect_run=run_name(reflect_run),
                status="success",
            )
            current_policy = project_policy_snapshot(repo_path)
            if current_policy != state.project_policy_snapshot:
                self._pause_for_project_policy_change_after_reflect(
                    run_dir,
                    state,
                    iteration_dir,
                    iteration_number,
                    reflect_run,
                    trace,
                    worker_status="success",
                    workspace_status=workspace_check.status,
                    workspace_new_files_count=workspace_check.new_untracked_count,
                    verification_status=verification_status,
                    verification_failed_count=verification_failed_count,
                    scope_evidence=scope_evidence,
                    post_scope_evidence=post_scope_evidence,
                    current_policy=current_policy,
                )
                return run_dir
            state.current_step = "scope_gate_pre_review"
            state.save(run_dir / "state.json")
            review_scope_evidence = write_loop_scope_gate_evidence(
                repo_path,
                config.scope,
                iteration_dir,
                trace,
                iteration=iteration_number,
                phase="pre_review",
                expected_head_sha=state.initial_head_sha,
                expected_policy_sha256=state.scope_policy_sha256,
            )
            iteration_state = _update_iteration_state(
                iteration_state,
                **scope_gate_state_fields(
                    review_scope_evidence,
                    phase="pre_review",
                ),
            )
            if not review_scope_evidence.passed:
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_scope_gate_fix_prompt(iteration_number + 1, review_scope_evidence),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "Reflect 后的精确路径范围门禁拒绝当前 tracked diff，未继续风险门禁或 reviewer。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="scope_gate_pre_review_failed",
                )
                return run_dir
            if not _reflect_has_tracked_diff(reflect_run):
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_no_diff_fix_prompt(iteration_number + 1),
                )
                trace.write("zero_diff_requires_human", iteration=iteration_number)
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "本轮没有可审查的 tracked diff，不能自动判定成功。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="no_diff",
                )
                return run_dir
            gate_evidence = _evaluate_loop_risk_gate(
                self.workspace,
                repo_path,
                reflect_run,
                iteration_dir,
                trace,
                iteration_number,
            )
            gate_result = gate_evidence.result
            iteration_state = _update_iteration_state(
                iteration_state,
                **_risk_gate_state_fields(gate_evidence),
            )
            if gate_evidence.error or gate_result is None:
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_risk_gate_fix_prompt(iteration_number + 1, None),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "风险门禁评估失败，未启动隔离 reviewer。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="risk_gate_failed",
                )
                return run_dir
            if gate_result.recommendation == "human-review":
                state.iterations.append(iteration_state)
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_risk_gate_fix_prompt(iteration_number + 1, gate_result),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "风险门禁要求人工确认，未继续自动隔离审查。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="risk_gate_needs_human",
                )
                return run_dir
            state.current_step = "review"
            state.save(run_dir / "state.json")
            trace.write("review_started", iteration=iteration_number)
            review_run = self._run_review(
                repo_path,
                reflect_run,
                reviewer_name,
                run_dir,
                iteration_number,
                config,
                gate_result,
                state.project_policy_snapshot,
            )
            verdict = _read_verdict(review_run)
            reviewer_status = _read_review_runner_status(review_run)
            review_run_status = _read_review_run_status(review_run)
            _record_review(iteration_dir, review_run)
            trace.write(
                "review_finished",
                iteration=iteration_number,
                status=review_run_status,
                verdict=verdict.verdict,
            )
            iteration_state = _update_iteration_state(
                iteration_state,
                reviewer_status=reviewer_status,
                review_run=run_name(review_run),
                verdict=verdict.verdict,
                findings_count=len(verdict.findings),
            )
            state.iterations.append(iteration_state)
            previous_verdict = verdict
            if not _review_run_allows_verdict(review_run_status, verdict):
                trace.write(
                    "review_run_not_successful",
                    iteration=iteration_number,
                    status=review_run_status,
                )
                _write_final_report(
                    run_dir,
                    state,
                    verdict,
                    f"Review run 自身状态为 {review_run_status}，不能采用其 verdict。",
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="review_run_failed",
                )
                return run_dir
            if verdict.verdict == "approve":
                self._finish_or_prepare_next(run_dir, state, verdict, iteration_dir, trace)
                return run_dir
            if verdict.verdict == "needs_human":
                self._finish_or_prepare_next(run_dir, state, verdict, iteration_dir, trace)
                return run_dir
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_fix_prompt(verdict, iteration_number + 1),
            )
            trace.write("fix_prompt_written", iteration=iteration_number)

        _write_final_report(run_dir, state, previous_verdict, "达到最大自动迭代轮数，需要人工接管。")
        self._save_loop_done(run_dir, state, "needs_human", trace)
        return run_dir

    def _finish_interrupted_worker(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_state: LoopIterationState,
        worker_result: RunnerResult,
        trace: TraceWriter,
    ) -> None:
        if worker_result.termination_unconfirmed:
            worker_status = "failed"
            _write_runner_error_report(
                iteration_dir,
                step="worker",
                reason=worker_result.error,
                workspace_status="未检查：owned process tree 终止未确认。",
                termination_unconfirmed=True,
            )
            conclusion = (
                "worker owned process tree 终止未确认；未读取 runner 输出，"
                "也未继续工作区检查、验证或审查。"
            )
            current_step = "worker_termination_unconfirmed"
        else:
            worker_status = worker_result.status
            _write_execution_interruption_report(
                iteration_dir,
                step="worker",
                status=worker_result.status,
                reason=worker_result.error,
            )
            conclusion = (
                "worker 单次执行超时，已停止后续验证和审查。"
                if worker_result.status == "timed_out"
                else "worker 已按 stop request 停止，已停止后续验证和审查。"
            )
            current_step = worker_result.status
        state.iterations.append(
            _update_iteration_state(
                iteration_state,
                worker_status=worker_status,
            )
        )
        _write_final_report(run_dir, state, None, conclusion)
        self._save_loop_done(
            run_dir,
            state,
            "needs_human",
            trace,
            current_step=current_step,
        )

    def _pause_for_project_policy_change_after_reflect(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_number: int,
        reflect_run: Path,
        trace: TraceWriter,
        *,
        worker_status: Literal["skipped", "success"],
        workspace_status: Literal["skipped", "passed", "failed"],
        workspace_new_files_count: int,
        verification_status: Literal["skipped", "passed", "failed"],
        verification_failed_count: int,
        scope_evidence: LoopScopeGateEvidence,
        post_scope_evidence: LoopScopeGateEvidence,
        current_policy: dict[str, str | None],
    ) -> None:
        """Reflect 返回后再次锁定完整项目策略，关闭 post-check 到审查之间的窗口。"""
        _write_project_policy_change_report(
            iteration_dir,
            state.project_policy_snapshot,
            current_policy,
        )
        state.current_iteration = iteration_number
        state.iterations.append(
            LoopIterationState(
                iteration=iteration_number,
                worker_status=worker_status,
                workspace_status=workspace_status,
                workspace_new_files_count=workspace_new_files_count,
                verification_status=verification_status,
                verification_failed_count=verification_failed_count,
                reflect_run=run_name(reflect_run),
                **scope_gate_state_fields(scope_evidence, phase="pre_verification"),
                **scope_gate_state_fields(
                    post_scope_evidence,
                    phase="post_verification",
                ),
            )
        )
        trace.write(
            "project_policy_changed",
            iteration=iteration_number,
            detected_after="reflect",
        )
        _write_final_report(
            run_dir,
            state,
            None,
            "Reflect 期间项目策略文件发生变化，已停止 pre-review gate 和隔离 reviewer。",
        )
        self._save_loop_done(
            run_dir,
            state,
            "needs_human",
            trace,
            current_step="project_policy_changed",
        )

    def _pause_for_terminal_verification_result(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_number: int,
        iteration_state: LoopIterationState,
        verification: VerificationRunResult | None,
        trace: TraceWriter,
        *,
        worker_status: Literal["skipped", "success"],
        workspace_status: Literal["skipped", "passed", "failed"] = "skipped",
        workspace_new_files_count: int = 0,
        scope_evidence: LoopScopeGateEvidence | None = None,
    ) -> bool:
        if verification is None:
            return False
        if verification.was_interrupted:
            self._pause_for_verification_interruption(
                run_dir,
                state,
                iteration_dir,
                iteration_number,
                verification,
                trace,
                worker_status=worker_status,
                workspace_status=workspace_status,
                workspace_new_files_count=workspace_new_files_count,
                scope_evidence=scope_evidence,
            )
            return True
        if verification.failure_kind is not None:
            self._pause_for_verification_failure(
                run_dir,
                state,
                iteration_dir,
                iteration_number,
                iteration_state,
                verification,
                trace,
            )
            return True
        return False

    def _pause_for_verification_interruption(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_number: int,
        verification: VerificationRunResult,
        trace: TraceWriter,
        *,
        worker_status: Literal["skipped", "success"],
        workspace_status: Literal["skipped", "passed", "failed"] = "skipped",
        workspace_new_files_count: int = 0,
        scope_evidence: LoopScopeGateEvidence | None = None,
    ) -> None:
        interruption_status = verification.interruption_status
        if interruption_status is None:
            raise ValueError("verification interruption 缺少中断状态")

        _copy_if_exists(verification.summary_path, iteration_dir / "test-summary.md")
        state.current_iteration = iteration_number
        state.iterations.append(
            LoopIterationState(
                iteration=iteration_number,
                worker_status=worker_status,
                workspace_status=workspace_status,
                workspace_new_files_count=workspace_new_files_count,
                verification_status="failed",
                verification_failed_count=verification.failed_count,
                **(
                    scope_gate_state_fields(scope_evidence, phase="pre_verification")
                    if scope_evidence is not None
                    else {}
                ),
            )
        )
        trace.write(
            "verification_interrupted",
            iteration=iteration_number,
            status=interruption_status,
            command=verification.interruption_command,
            reason=verification.interruption_reason,
        )
        _write_verification_interruption_report(iteration_dir, verification)
        conclusion = {
            "timed_out": "verification 命令超时，已停止剩余验证、reflect 和 review。",
            "stopped": "verification 已按 stop request 停止，未继续 reflect 或 review。",
            "termination-unconfirmed": (
                "verification owned process tree 终止未确认，已保留现场并停止 reflect 和 review。"
            ),
        }[interruption_status]
        _write_final_report(run_dir, state, None, conclusion)
        current_step = {
            "timed_out": "timed_out",
            "stopped": "stopped",
            "termination-unconfirmed": "verification_termination_unconfirmed",
        }[interruption_status]
        self._save_loop_done(
            run_dir,
            state,
            "needs_human",
            trace,
            current_step=current_step,
        )

    def _pause_for_verification_failure(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_number: int,
        iteration_state: LoopIterationState,
        verification: VerificationRunResult,
        trace: TraceWriter,
    ) -> None:
        failure_kind = verification.failure_kind
        if failure_kind == "project_config_invalid":
            event = failure_kind
            conclusion = "项目配置预检失败，未执行任何验证命令，也未继续 Reflect 或 reviewer。"
        elif failure_kind == "workspace_capture_failed":
            event = "verification_workspace_capture_failed"
            conclusion = (
                "verification 工作区指纹采集失败，证据无法绑定当前现场，"
                "已停止 Reflect 和 reviewer。"
            )
        else:
            raise ValueError("verification failure 缺少受支持的 failure_kind")
        _copy_if_exists(verification.summary_path, iteration_dir / "test-summary.md")
        state.current_iteration = iteration_number
        state.iterations.append(iteration_state)
        trace.write(
            event,
            iteration=iteration_number,
            failure_kind=failure_kind,
            commands=verification.command_count,
            failed=verification.failed_count,
        )
        _write_final_report(run_dir, state, None, conclusion)
        self._save_loop_done(
            run_dir,
            state,
            "needs_human",
            trace,
            current_step=event,
        )

    def _run_review(
        self,
        repo_path: Path,
        reflect_run: Path,
        reviewer_name: str,
        loop_run_dir: Path,
        iteration: int,
        config: ProjectConfig,
        risk_gate_result: GateResult,
        expected_project_policy_snapshot: dict[str, str | None],
    ) -> Path:
        return ReviewRuntime(
            self.workspace,
            runner=self.reviewer_runner,
            timeout_seconds=self.timeout_seconds,
        ).run(
            repo_path,
            run_name(reflect_run),
            runner_name=reviewer_name,
            execution_context=RunnerExecutionContext(
                execution_dir=_iteration_dir(loop_run_dir, iteration)
                / "executions"
                / "reviewer",
                run_id=loop_run_dir.name,
                step="reviewer",
                iteration=iteration,
                progress_reporter=self.progress_reporter,
            ),
            project_config=config,
            precomputed_risk_gate=PrecomputedReviewRiskGate(
                source_run=run_name(reflect_run),
                result=risk_gate_result,
                project_policy_snapshot=dict(expected_project_policy_snapshot),
            ),
        )

    def _finish_or_prepare_next(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        verdict: ReviewVerdict,
        iteration_dir: Path,
        trace: TraceWriter,
    ) -> None:
        if verdict.verdict == "approve":
            if not _latest_verification_passed(state):
                latest = state.iterations[-1] if state.iterations else None
                project_config_invalid = bool(
                    latest
                    and latest.verification_failure_kind == "project_config_invalid"
                )
                verification_failed = bool(
                    latest
                    and latest.verification_status == "failed"
                    and latest.verification_failed_count
                )
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_verification_fix_prompt(state.current_iteration),
                )
                _write_final_report(
                    run_dir,
                    state,
                    verdict,
                    (
                        "项目配置预检失败，未执行任何验证命令。"
                        if project_config_invalid
                        else (
                            "验证命令失败，reviewer approve 不能覆盖确定性失败。"
                            if verification_failed
                            else "缺少受信的结构化验证通过证据，不能自动通过。"
                        )
                    ),
                )
                self._save_loop_done(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step=(
                        "project_config_invalid"
                        if project_config_invalid
                        else (
                            "verification_failed"
                            if verification_failed
                            else "verification_unverified"
                        )
                    ),
                )
                return
            _write_final_report(run_dir, state, verdict, "隔离 reviewer 已通过。")
            self._save_loop_done(run_dir, state, "success", trace)
            return
        if verdict.verdict == "request_changes":
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_fix_prompt(verdict, state.current_iteration + 1),
            )
            _write_final_report(run_dir, state, verdict, "reviewer 要求修改，已生成 fix-prompt.md。")
            self._save_loop_done(run_dir, state, "needs_human", trace)
            return
        latest = state.iterations[-1] if state.iterations else None
        if latest and latest.reviewer_status == "timed_out":
            conclusion = "reviewer 单次执行超时，需要人工判断或重新审查。"
        elif latest and latest.reviewer_status == "stopped":
            conclusion = "reviewer 已按 stop request 停止，需要人工判断后续动作。"
        elif latest and latest.reviewer_status == "error":
            conclusion = "reviewer runner 异常退出，未产生可信审查结论。"
        else:
            conclusion = "reviewer 需要人工判断。"
        _write_final_report(run_dir, state, verdict, conclusion)
        if latest and latest.reviewer_status == "error":
            interruption_step = "reviewer_error"
        elif latest and latest.reviewer_status in {"timed_out", "stopped"}:
            interruption_step = latest.reviewer_status
        else:
            interruption_step = "done"
        self._save_loop_done(
            run_dir,
            state,
            "needs_human",
            trace,
            current_step=interruption_step,
        )

    def _save_loop_done(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        status: Literal["success", "failed", "needs_human"],
        trace: TraceWriter,
        current_step: str = "done",
    ) -> None:
        required_artifacts = (
            FINAL_LOOP_ARTIFACTS if (run_dir / "final-report.md").exists() else LOOP_ARTIFACTS
        )
        artifacts = list(dict.fromkeys([*state.artifacts, *required_artifacts]))
        state.current_step = current_step
        state.status = status
        state.artifacts = artifacts
        _finalize_loop_eval(run_dir, state, status, artifacts, trace)


def run_loop_eval(
    run_dir: Path,
    artifacts: list[str],
    *,
    require_terminal: bool = True,
    status_for_eval: str | None = None,
) -> list[str]:
    results = [
        f"{'PASS' if (run_dir / item).exists() else 'FAIL'}: artifact 存在：{item}"
        for item in artifacts
    ]
    state_path = run_dir / "state.json"
    if not state_path.exists():
        results.append("FAIL: loop state 不存在")
        return results
    try:
        state = LoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - eval 必须把损坏证据转成 FAIL
        results.append(f"FAIL: loop state schema 不合法：{type(exc).__name__}")
        return results
    results.append("PASS: loop state schema 合法")

    missing_declared = sorted(set(artifacts) - set(state.artifacts))
    if missing_declared:
        results.append(
            "FAIL: state.artifacts 未声明必需 artifact：" + ", ".join(missing_declared)
        )
    else:
        results.append("PASS: state.artifacts 与必需 artifact 一致")
    results.extend(_project_policy_snapshot_eval_results(run_dir, state))

    effective_status = status_for_eval or state.status
    if require_terminal:
        results.extend(
            _loop_terminal_eval_results(
                run_dir,
                state.status,
                state.superseded_terminal_events,
            )
        )

    if effective_status == "success" and not state.iterations:
        results.append("FAIL: success loop 至少需要一轮 iteration")
        return results
    if effective_status == "success":
        results.append("PASS: success loop 包含 iteration")

    if not state.iterations:
        return results

    workspace = run_dir.parent.parent
    repo_path = Path(state.repo_path)
    latest_iteration = state.iterations[-1].iteration
    for expected_iteration, iteration in enumerate(state.iterations, start=1):
        if iteration.iteration != expected_iteration:
            results.append(
                "FAIL: iteration 编号不连续："
                f"期望 {expected_iteration:02d}，实际 {iteration.iteration:02d}"
            )
        iteration_dir = run_dir / "iterations" / f"{iteration.iteration:02d}"
        if iteration.lifecycle == "interrupted":
            results.extend(
                _interrupted_iteration_evidence_checks(
                    iteration_dir,
                    iteration,
                )
            )
            continue
        if iteration.interrupted_step is not None or iteration.interrupted_at is not None:
            results.append(
                "FAIL: completed iteration 不得携带 interruption metadata："
                f"{iteration.iteration:02d}"
            )
        recompute_current = (
            iteration.iteration == latest_iteration and effective_status != "success"
        )
        results.extend(
            _loop_iteration_evidence_checks(
                iteration_dir,
                iteration,
                # 前序 Reflect 已被后续 worker 合法改变时，使用终态工作区重算会把
                # 历史证据误判为失效。success 的最终 iteration 由后面的联合快照统一
                # 重算，避免对同一风险结论重复执行；非 success 仍在这里重算当前工作区。
                workspace=workspace if recompute_current else None,
                repo_path=repo_path if recompute_current else None,
                trace_path=run_dir / "trace.jsonl",
                scope_gate_required=state.scope_gate_required,
                expected_head_sha=state.initial_head_sha,
                expected_policy_sha256=state.scope_policy_sha256,
            )
        )
    if state.current_iteration != state.iterations[-1].iteration:
        results.append("FAIL: state.current_iteration 与最后一轮 iteration 不一致")

    if effective_status == "success":
        latest = state.iterations[-1]
        iteration_dir = run_dir / "iterations" / f"{latest.iteration:02d}"
        validation_snapshot = None
        validation_error = ""
        try:
            validation_snapshot = validate_loop_evidence_snapshot(
                workspace,
                repo_path,
                run_dir,
                state=state,
            )
        except Exception as exc:  # noqa: BLE001 - eval 必须把重算异常转成 FAIL
            validation_error = type(exc).__name__
        if latest.lifecycle != "completed":
            results.append("FAIL: success loop 的最新 iteration 必须为 completed")
        else:
            results.append("PASS: success loop 的最新 iteration 已 completed")
        if latest.verdict != "approve":
            results.append("FAIL: success loop 的最新 verdict 必须为 approve")
        else:
            results.append("PASS: success loop 的最新 verdict 为 approve")
        if latest.reviewer_status != "success":
            results.append("FAIL: success loop 缺少成功的 reviewer 执行")
        else:
            results.append("PASS: success loop reviewer 执行成功")
        if latest.workspace_new_files_count:
            results.append("FAIL: success loop 仍包含新增未跟踪文件")
        if latest.verification_status != "passed" or latest.verification_failed_count:
            results.append("FAIL: success loop 的最新 verification 必须为 passed")
        else:
            if validation_snapshot is None:
                results.append(
                    "FAIL: success loop 的最新 verification 完整性重算异常："
                    f"{validation_error}"
                )
            else:
                artifact_integrity = validation_snapshot.artifact_integrity
                if trusted_verification_passed(state, artifact_integrity):
                    results.append(
                        "PASS: success loop 的最新 verification 存在受信结构化通过证据"
                    )
                else:
                    verification_issues = [
                        issue
                        for issue in artifact_integrity.issues
                        if "verification" in issue
                    ]
                    issue_summary = (
                        "：" + ", ".join(verification_issues)
                        if verification_issues
                        else ""
                    )
                    results.append(
                        "FAIL: success loop 的最新 verification "
                        f"缺少受信结构化通过证据{issue_summary}"
                    )
        if "final-report.md" not in state.artifacts:
            results.append("FAIL: success loop 未声明 final-report.md")
        if not (iteration_dir / "diff-summary.md").exists():
            results.append("FAIL: success loop 缺少 diff-summary.md")
        elif "当前没有工作区 diff 文件" in _read_optional_text(
            iteration_dir / "diff-summary.md"
        ):
            results.append("FAIL: success loop 没有可审查的 tracked diff")
        if validation_snapshot is None:
            results.append(
                "FAIL: success loop reviewer 证据新鲜度重算异常："
                f"{validation_error}"
            )
        else:
            freshness = validation_snapshot.evidence_freshness
            if freshness.fresh:
                results.append("PASS: success loop reviewer 证据仍与当前工作区一致")
            else:
                results.append(
                    "FAIL: success loop reviewer 证据已过期："
                    + ", ".join(freshness.issues)
                )
    return results


def _loop_trace_checks(
    run_dir: Path,
    expected_superseded: list[SupersededTerminalRecord],
) -> tuple[list[str], str | None]:
    trace_path = run_dir / "trace.jsonl"
    if not trace_path.exists():
        return ["FAIL: trace.jsonl 不存在"], None
    try:
        items = read_trace_items(trace_path)
    except (OSError, ValueError) as exc:
        return [f"FAIL: trace.jsonl 不是合法 JSONL：{exc}"], None
    if not items:
        return ["FAIL: trace.jsonl 为空"], None
    terminal_indices, supersede_issues = active_run_finished_indices(
        items,
        expected_superseded=[
            record.model_dump() for record in expected_superseded
        ],
    )
    if supersede_issues:
        return [
            "FAIL: run_terminal_superseded 证据无效：" + ", ".join(supersede_issues)
        ], None
    if not terminal_indices:
        return ["FAIL: trace.jsonl 缺少未被 supersede 的 run_finished 终态事件"], None
    if len(terminal_indices) != 1:
        return [
            "FAIL: trace.jsonl 必须且只能包含一个未被 supersede 的 run_finished 终态事件"
        ], None
    terminal_index = terminal_indices[0]
    if terminal_index != len(items) - 1:
        return ["FAIL: run_finished 必须是 trace.jsonl 最后一条事件"], None
    status = items[terminal_index].get("status")
    if not isinstance(status, str):
        return ["FAIL: run_finished 终态缺少 status"], None
    return ["PASS: trace.jsonl 非空且包含 run_finished 终态事件"], status


def _project_policy_snapshot_eval_results(
    run_dir: Path,
    state: LoopAutomationState,
) -> list[str]:
    expected_hash = state.project_policy_snapshot_sha256
    if expected_hash is None:
        return []
    path = run_dir / "project-policy-snapshot.json"
    if not path.is_file():
        return ["FAIL: project-policy-snapshot.json 不存在"]
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return ["FAIL: project-policy-snapshot.json 不合法"]
    results: list[str] = []
    if sha256_text(text) != expected_hash:
        results.append("FAIL: project policy snapshot hash mismatch")
    if payload != state.project_policy_snapshot:
        results.append("FAIL: project policy snapshot 与 state 不一致")
    if not results:
        results.append("PASS: project policy snapshot 与根状态绑定")
    return results


def _loop_terminal_eval_results(
    run_dir: Path,
    state_status: str,
    expected_superseded: list[SupersededTerminalRecord],
) -> list[str]:
    results, terminal_status = _loop_trace_checks(run_dir, expected_superseded)
    if terminal_status and terminal_status != state_status:
        results.append(
            f"FAIL: trace 终态与 state.status 不一致：{terminal_status} != {state_status}"
        )
    elif terminal_status:
        results.append("PASS: trace 终态与 state.status 一致")
    return results


def _loop_iteration_evidence_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    workspace: Path | None = None,
    repo_path: Path | None = None,
    trace_path: Path | None = None,
    scope_gate_required: bool = False,
    expected_head_sha: str | None = None,
    expected_policy_sha256: str | None = None,
) -> list[str]:
    results: list[str] = []
    if not iteration_dir.exists():
        return [f"FAIL: iteration artifact 目录不存在：{iteration.iteration:02d}"]

    if iteration.reflect_run:
        reflect_ref = _read_optional_text(iteration_dir / "reflect-run.txt").strip()
        if not reflect_ref:
            results.append("FAIL: iteration 声明 reflect_run 但缺少 reflect-run.txt")
        elif Path(reflect_ref).name != iteration.reflect_run:
            results.append("FAIL: reflect-run.txt 与 iteration.reflect_run 不一致")

    results.extend(_verification_iteration_state_checks(iteration))
    if iteration.verification_status in {"passed", "failed"}:
        if not (iteration_dir / "verification-summary.md").exists():
            results.append("FAIL: verification 状态已记录但缺少 verification-summary.md")
        if not (iteration_dir / "test-summary.md").exists():
            results.append("FAIL: verification 状态已记录但缺少 test-summary.md")

    scope_gate_integrity = validate_iteration_scope_gate_artifacts(
        iteration_dir,
        iteration,
        phase="pre_verification",
        # verification 本身可以合法修改工作区；pre-verification 只校验落盘绑定，
        # 不能拿最终 diff 重算，否则会把后续证据误判成 pre gate 被篡改。
        repo_path=None,
        trace_path=trace_path,
        required=scope_gate_required,
        expected_head_sha=expected_head_sha,
        expected_policy_sha256=expected_policy_sha256,
    )
    if scope_gate_integrity.valid and scope_gate_integrity.evaluated:
        results.append("PASS: pre-verification scope gate artifact 与 iteration 一致")
    else:
        results.extend(f"FAIL: {issue}" for issue in scope_gate_integrity.issues)

    post_scope_gate_integrity = validate_iteration_scope_gate_artifacts(
        iteration_dir,
        iteration,
        phase="post_verification",
        # Reflect 之后还会有 pre-review gate；post-verification 只校验其历史绑定，
        # 不能用后续工作区重算。
        repo_path=None,
        trace_path=trace_path,
        required=scope_gate_required,
        expected_head_sha=expected_head_sha,
        expected_policy_sha256=expected_policy_sha256,
    )
    if post_scope_gate_integrity.valid and post_scope_gate_integrity.evaluated:
        results.append("PASS: post-verification scope gate artifact 与 iteration 一致")
    else:
        results.extend(
            f"FAIL: post_verification_{issue}" for issue in post_scope_gate_integrity.issues
        )

    review_scope_gate_integrity = validate_iteration_scope_gate_artifacts(
        iteration_dir,
        iteration,
        phase="pre_review",
        repo_path=repo_path,
        trace_path=trace_path,
        required=scope_gate_required,
        expected_head_sha=expected_head_sha,
        expected_policy_sha256=expected_policy_sha256,
    )
    if review_scope_gate_integrity.valid and review_scope_gate_integrity.evaluated:
        results.append("PASS: pre-review scope gate artifact 与 iteration 一致")
    else:
        results.extend(f"FAIL: pre_review_{issue}" for issue in review_scope_gate_integrity.issues)

    gate_integrity = validate_iteration_risk_gate_artifacts(
        iteration_dir,
        iteration,
        workspace=workspace,
        repo_path=repo_path,
        trace_path=trace_path,
    )
    if gate_integrity.valid and iteration.risk_gate_status != "skipped":
        results.append("PASS: risk gate artifact 与 iteration 一致")
    else:
        results.extend(f"FAIL: {issue}" for issue in gate_integrity.issues)

    if iteration.verdict is None:
        return results
    verdict_path = iteration_dir / "review-verdict.json"
    if not verdict_path.exists():
        results.append("FAIL: iteration 声明 verdict 但缺少 review-verdict.json")
        return results
    try:
        verdict = ReviewVerdict.model_validate_json(
            verdict_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - eval 必须报告损坏 verdict
        results.append(f"FAIL: review-verdict.json 不合法：{type(exc).__name__}")
        return results
    if verdict.verdict != iteration.verdict:
        results.append("FAIL: review-verdict.json 与 iteration.verdict 不一致")
    else:
        results.append("PASS: reviewer verdict artifact 与 iteration 一致")

    context_path = iteration_dir / "review-context.json"
    if not context_path.exists():
        results.append("FAIL: iteration 声明 verdict 但缺少 review-context.json")
        return results
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        results.append(f"FAIL: review-context.json 不合法：{exc.msg}")
        return results
    if iteration.reflect_run and context.get("source_run") != iteration.reflect_run:
        results.append("FAIL: review-context.json 与 iteration.reflect_run 不一致")
    return results


def _verification_iteration_state_checks(
    iteration: LoopIterationState,
) -> list[str]:
    results: list[str] = []
    if iteration.verification_failed_count and iteration.verification_status != "failed":
        results.append("FAIL: verification 失败计数与状态不一致")
    if (
        iteration.verification_failure_kind is not None
        and iteration.verification_status != "failed"
    ):
        results.append("FAIL: verification failure_kind 与状态不一致")
    return results


def render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")


def _next_iteration_number(
    run_dir: Path,
    state: LoopAutomationState,
) -> int:
    expected = list(range(1, len(state.iterations) + 1))
    actual = [item.iteration for item in state.iterations]
    if actual != expected:
        raise ValueError(
            f"loop iteration 序列不连续：期望 {expected or '[]'}，实际 {actual or '[]'}"
        )
    last_iteration = state.iterations[-1].iteration if state.iterations else 0
    if state.current_iteration != last_iteration:
        raise ValueError(
            "loop current_iteration 与最后已登记 iteration 不一致，"
            "已拒绝继续以避免覆盖证据。"
        )
    next_iteration = last_iteration + 1
    next_dir = run_dir / "iterations" / f"{next_iteration:02d}"
    if next_dir.exists():
        raise ValueError(
            f"下一 iteration 目录已存在：{next_dir.relative_to(run_dir)}；"
            "已拒绝复用或覆盖旧证据。"
        )
    return next_iteration


def loop_initialization_issues(
    workspace: Path,
    run_dir: Path,
    state: LoopAutomationState,
    repo_path: Path,
) -> list[str]:
    """返回 loop 初始化证据问题，供 recovery 与 continue 使用同一判断。"""

    issues: list[str] = []
    if not state.brief_run:
        return ["brief_run_missing"]
    try:
        brief_dir = resolve_run_dir(workspace, state.brief_run)
    except (FileNotFoundError, ValueError):
        return ["brief_run_unresolvable"]
    state_path = brief_dir / "state.json"
    try:
        brief_state = BriefState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError):
        return ["brief_state_invalid"]

    if brief_state.run_id != brief_dir.name:
        issues.append("brief_run_id_mismatch")
    try:
        if Path(brief_state.repo_path).resolve() != repo_path.resolve():
            issues.append("brief_repo_mismatch")
    except OSError:
        issues.append("brief_repo_unresolvable")
    if brief_state.mode != state.task_mode:
        issues.append("brief_task_mode_mismatch")
    if brief_state.status != "success":
        issues.append("brief_status_not_success")

    for name in ("agent-brief.md", "project-context.md"):
        try:
            source_bytes = brief_dir.joinpath(name).read_bytes()
            loop_bytes = run_dir.joinpath(name).read_bytes()
        except OSError:
            issues.append(f"{name}_missing_or_unreadable")
            continue
        if source_bytes != loop_bytes:
            issues.append(f"{name}_source_mismatch")

    for name in LOOP_INITIALIZATION_ARTIFACTS:
        path = run_dir / name
        try:
            if not path.is_file() or not path.read_bytes():
                issues.append(f"{name}_missing_or_empty")
        except OSError:
            issues.append(f"{name}_missing_or_unreadable")

    prompt_path = run_dir / "worker-prompt.md"
    metrics_path = run_dir / "worker-prompt-metrics.json"
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
        metrics = PromptMetrics.model_validate_json(
            metrics_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError):
        issues.append("worker_prompt_metrics_invalid")
    else:
        if (
            metrics.role != "worker"
            or metrics.chars != len(prompt)
            or metrics.utf8_bytes != len(prompt.encode("utf-8"))
            or metrics.lines != len(prompt.splitlines())
        ):
            issues.append("worker_prompt_metrics_mismatch")

    if any(
        result.startswith("FAIL:")
        for result in _project_policy_snapshot_eval_results(run_dir, state)
    ):
        issues.append("project_policy_snapshot_invalid")

    try:
        trace_items = read_trace_items(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        issues.append("initialization_trace_invalid")
    else:
        initialized_events = [
            item for item in trace_items if item.get("event") == "loop_initialized"
        ]
        if initialized_events:
            if len(initialized_events) != 1:
                issues.append("loop_initialized_event_count_invalid")
            else:
                initialized = initialized_events[0]
                if initialized.get("brief_run") != state.brief_run:
                    issues.append("loop_initialized_brief_mismatch")
                if initialized.get("artifacts") != LOOP_INITIALIZATION_ARTIFACTS:
                    issues.append("loop_initialized_artifacts_mismatch")
        else:
            # 兼容旧 run：根级 worker_prompt_measured 发生在全部初始化文件写完之后。
            legacy_markers = [
                item
                for item in trace_items
                if item.get("event") == "worker_prompt_measured"
                and "iteration" not in item
            ]
            if len(legacy_markers) != 1:
                issues.append("loop_initialization_marker_missing")
    return list(dict.fromkeys(issues))


def _require_loop_initialization(
    workspace: Path,
    run_dir: Path,
    state: LoopAutomationState,
    repo_path: Path,
) -> None:
    issues = loop_initialization_issues(
        workspace,
        run_dir,
        state,
        repo_path,
    )
    if issues:
        raise ValueError(
            "loop 初始化未完成或证据不完整，已拒绝 continue："
            + ", ".join(issues)
        )


def _require_execution_recoverable(run_dir: Path) -> None:
    inspection = inspect_execution_for_recovery(run_dir)
    if not inspection.can_recover:
        raise ValueError(
            "当前 loop 仍有未安全消失的 execution，已拒绝 continue："
            f"{inspection.summary}"
        )


def _require_recovery_trace_binding(
    run_dir: Path,
    state: LoopAutomationState,
) -> None:
    pending_path = run_dir / ".control" / "recovery-transaction.json"
    if pending_path.exists():
        raise ValueError(
            "loop recovery transaction 尚未提交完成；"
            "请先重新执行 recover，再运行 continue。"
        )
    try:
        items = read_trace_items(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise ValueError("loop trace 无法验证，已拒绝 continue。") from exc
    _, issues = active_run_finished_indices(
        items,
        expected_superseded=[
            record.model_dump()
            for record in state.superseded_terminal_events
        ],
    )
    if issues:
        raise ValueError(
            "loop recovery 终态绑定不完整，已拒绝 continue："
            + ", ".join(issues)
        )
    if state.current_step not in {
        "recovered",
        "recovered_initialization_incomplete",
    }:
        return

    recovery_id = state.last_recovery_id
    if recovery_id is None:
        # 兼容旧 run：旧版没有 last_recovery_id，但必须保留真实 recovery 事件。
        legacy_recovered = [
            item for item in items if item.get("event") == "loop_recovered"
        ]
        if not legacy_recovered:
            raise ValueError("loop 缺少 recovery trace，已拒绝 continue。")
        return

    recovered_matches = [
        item
        for item in items
        if item.get("event") == "loop_recovered"
        and item.get("recovery_id") == recovery_id
    ]
    if len(recovered_matches) != 1:
        raise ValueError("loop_recovered 与 state.last_recovery_id 不一致。")
    recovered = recovered_matches[0]
    continuation_allowed = (
        state.current_step != "recovered_initialization_incomplete"
    )
    if recovered.get("continuation_allowed") != continuation_allowed:
        raise ValueError("loop_recovered 的 continuation_allowed 与 state 不一致。")
    expected_superseded = next(
        (
            record.terminal_event_index
            for record in state.superseded_terminal_events
            if record.recovery_id == recovery_id
        ),
        None,
    )
    if recovered.get("superseded_terminal_event") != expected_superseded:
        raise ValueError("loop_recovered 的 superseded terminal 与 state 不一致。")

    if state.iterations and state.iterations[-1].lifecycle == "interrupted":
        latest = state.iterations[-1]
        interruption_matches = [
            item
            for item in items
            if item.get("event") == "loop_iteration_interrupted"
            and item.get("recovery_id") == recovery_id
        ]
        if len(interruption_matches) != 1:
            raise ValueError(
                "loop_iteration_interrupted 与 state.last_recovery_id 不一致。"
            )
        interruption = interruption_matches[0]
        if (
            interruption.get("iteration") != latest.iteration
            or interruption.get("previous_step") != latest.interrupted_step
        ):
            raise ValueError("loop interruption trace 与最新 interrupted state 不一致。")


def _interrupted_iteration_evidence_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
) -> list[str]:
    results: list[str] = []
    if not iteration_dir.is_dir():
        return [f"FAIL: interrupted iteration 目录不存在：{iteration.iteration:02d}"]
    if not iteration.interrupted_step:
        results.append("FAIL: interrupted iteration 缺少 interrupted_step")
    if not iteration.interrupted_at:
        results.append("FAIL: interrupted iteration 缺少 interrupted_at")
    report_path = iteration_dir / "interruption-report.md"
    if not report_path.is_file():
        results.append("FAIL: interrupted iteration 缺少 interruption-report.md")
        return results
    report = _read_optional_text(report_path)
    if f"- 迭代：`{iteration.iteration}`" not in report:
        results.append("FAIL: interruption-report.md 与 iteration 编号不一致")
    if (
        iteration.interrupted_step
        and f"- 原步骤：`{iteration.interrupted_step}`" not in report
    ):
        results.append("FAIL: interruption-report.md 与 interrupted_step 不一致")
    if iteration.interrupted_at and iteration.interrupted_at not in report:
        results.append("FAIL: interruption-report.md 与 interrupted_at 不一致")
    if not results:
        results.append("PASS: interrupted iteration 证据已保留且不参与成功判定")
    return results


def _iteration_dir(run_dir: Path, iteration: int) -> Path:
    path = run_dir / "iterations" / f"{iteration:02d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_reflect(iteration_dir: Path, reflect_run: Path) -> None:
    _write_text_artifact(iteration_dir / "reflect-run.txt", str(reflect_run.resolve()) + "\n")
    for name in ["diff-summary.md", "test-summary.md", "project-context.md", "reflection.md"]:
        _copy_if_exists(reflect_run / name, iteration_dir / name)


def _read_reflect_run_status(reflect_run: Path) -> str:
    try:
        state = json.loads(reflect_run.joinpath("state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "failed"
    status = state.get("status")
    return status if isinstance(status, str) else "failed"


def _reflect_run_succeeded(reflect_run: Path) -> bool:
    return _read_reflect_run_status(reflect_run) == "success"


def _write_reflect_failure_report(iteration_dir: Path, reflect_run: Path) -> Path:
    path = iteration_dir / "reflect-failure.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Reflect Failure Report",
                "",
                f"- reflect run：`{run_name(reflect_run)}`",
                f"- 状态：`{_read_reflect_run_status(reflect_run)}`",
                "",
                "## 结论",
                "",
                "- Reflect 的确定性证据检查未通过，当前 patch 不能作为可信 reviewer 输入。",
                "- Vega 未启动隔离 reviewer，也不会把任何 approve 视为成功。",
                "- 请先查看对应 reflect run 的 `eval.md`、`diff-summary.md` 和 `test-summary.md`。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _evaluate_loop_risk_gate(
    workspace: Path,
    repo_path: Path,
    reflect_run: Path,
    iteration_dir: Path,
    trace: TraceWriter,
    iteration: int,
) -> LoopRiskGateEvidence:
    """在 loop 内复用 Gate 的确定性策略，但不隐式创建独立 gate run。

    日常 auto/continue 需要在 success 前强制执行同一套预算和风险规则；独立
    `vega gate` 仍保留给人工排障和单独观察。这里的本地 artifact 绑定当前
    iteration，避免把高风险结果只留在内存中。
    """
    result_path = iteration_dir / RISK_GATE_RESULT_ARTIFACT
    report_path = iteration_dir / RISK_GATE_REPORT_ARTIFACT
    source_run = run_name(reflect_run)
    try:
        result = evaluate_risk(workspace, repo_path, source_run)
    except Exception as exc:  # noqa: BLE001 - 风险判断错误必须 fail-closed
        diagnostic = redact_text(f"{type(exc).__name__}: {exc}")[:1000]
        payload = {
            "schema_version": 1,
            "status": "failed",
            "iteration": iteration,
            "source_run": source_run,
            "code": "risk_evaluation_failed",
            "message": "风险门禁评估失败，未生成可信自动放行结论。",
            "diagnostic": diagnostic,
        }
        result_text = json.dumps(
            redact_value(payload),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        result_path.write_text(
            result_text,
            encoding="utf-8",
        )
        result_sha256 = sha256_text(result_text)
        report_body = redact_text(
            "\n".join(
                [
                    "# 本轮风险门禁报告",
                    "",
                    f"- source reflect：`{source_run}`",
                    f"- iteration：`{iteration:02d}`",
                    "- 状态：`failed`",
                    "",
                    "## 结论",
                    "",
                    "- 风险门禁未能完成评估，Vega 不会继续自动隔离审查。",
                    f"- 诊断：{diagnostic or '未提供'}",
                ]
            ).rstrip()
            + "\n"
        )
        report_text = (
            report_body.rstrip()
            + "\n\n"
            + render_risk_gate_report_binding(
                status="failed",
                iteration=iteration,
                source_run=source_run,
                result_sha256=result_sha256,
                risk=None,
                recommendation=None,
            )
        )
        report_path.write_text(report_text, encoding="utf-8")
        trace.write(
            "risk_gate_failed",
            iteration=iteration,
            source_run=source_run,
            status="failed",
            result_sha256=result_sha256,
            report_sha256=sha256_text(report_text),
            diagnostic=diagnostic,
        )
        return LoopRiskGateEvidence(
            result=None,
            error=diagnostic or "risk_evaluation_failed",
            source_run=source_run,
            result_sha256=result_sha256,
            report_sha256=sha256_text(report_text),
        )

    payload = {
        "schema_version": 1,
        "status": "success",
        "iteration": iteration,
        "source_run": source_run,
        **result.model_dump(mode="json"),
    }
    result_text = json.dumps(
        redact_value(payload),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    result_path.write_text(
        result_text,
        encoding="utf-8",
    )
    result_sha256 = sha256_text(result_text)
    report_body = redact_text(
        render_gate_report(result)
        + "\n"
        + "\n".join(
            [
                "## 本轮关联",
                "",
                f"- source reflect：`{source_run}`",
                f"- iteration：`{iteration:02d}`",
                "- 此结果由 loop 在启动隔离 reviewer 前生成。",
            ]
        )
        + "\n"
    )
    report_text = (
        report_body.rstrip()
        + "\n\n"
        + render_risk_gate_report_binding(
            status="success",
            iteration=iteration,
            source_run=source_run,
            result_sha256=result_sha256,
            risk=result.risk,
            recommendation=result.recommendation,
        )
    )
    report_path.write_text(report_text, encoding="utf-8")
    trace.write(
        "risk_gate_finished",
        iteration=iteration,
        source_run=source_run,
        status="success",
        risk=result.risk,
        recommendation=result.recommendation,
        reasons=[reason.code for reason in result.reasons],
        result_sha256=result_sha256,
        report_sha256=sha256_text(report_text),
    )
    return LoopRiskGateEvidence(
        result=result,
        error=None,
        source_run=source_run,
        result_sha256=result_sha256,
        report_sha256=sha256_text(report_text),
    )


def _risk_gate_state_fields(evidence: LoopRiskGateEvidence) -> dict[str, object]:
    result = evidence.result
    return {
        "risk_gate_status": evidence.status,
        "risk_gate_source_run": evidence.source_run,
        "risk_gate_risk": result.risk if result else None,
        "risk_gate_recommendation": result.recommendation if result else None,
        "risk_gate_result_sha256": evidence.result_sha256,
        "risk_gate_report_sha256": evidence.report_sha256,
    }


def _update_iteration_state(
    iteration: LoopIterationState,
    **updates: Any,
) -> LoopIterationState:
    """每个 iteration 只维护一份逐阶段累积状态，避免分支重复拼装事实。"""

    payload = iteration.model_dump(mode="json")
    payload.update(updates)
    return LoopIterationState.model_validate(payload)


def _record_review(iteration_dir: Path, review_run: Path) -> None:
    for name in [
        "review-pack.md",
        "review-prompt.md",
        "review-checklist.md",
        "review-context.json",
        "review-prompt-metrics.json",
        "review-prompt-metrics.md",
        "project-context.md",
        "review-verdict.json",
        "review-findings.md",
        "review-runner-output.txt",
        "timeout-report.md",
        "stop-report.md",
        "runner-error-report.md",
        "review-context-budget-report.md",
    ]:
        _copy_if_exists(review_run / name, iteration_dir / name)
    _copy_if_exists(review_run / "state.json", iteration_dir / "review-state.json")
    _copy_if_exists(review_run / "eval.md", iteration_dir / "review-eval.md")


def _read_verdict(review_run: Path) -> ReviewVerdict:
    return ReviewVerdict.model_validate_json(
        review_run.joinpath("review-verdict.json").read_text(encoding="utf-8")
    )


def _read_review_runner_status(review_run: Path) -> RunnerStatus:
    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    status = state.get("runner_status", "skipped")
    if status not in {"skipped", "success", "error", "timed_out", "stopped"}:
        return "error"
    return status


def _read_review_run_status(review_run: Path) -> str:
    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    status = state.get("status")
    return status if isinstance(status, str) else "failed"


def _review_run_allows_verdict(status: str, verdict: ReviewVerdict) -> bool:
    if status == "success":
        return True
    return status == "needs_human" and verdict.verdict in {
        "request_changes",
        "needs_human",
    }


def _write_execution_interruption_report(
    iteration_dir: Path,
    *,
    step: str,
    status: Literal["timed_out", "stopped"],
    reason: str | None,
    command: str | None = None,
) -> Path:
    filename = "timeout-report.md" if status == "timed_out" else "stop-report.md"
    title = "Timeout Report" if status == "timed_out" else "Stop Report"
    if step == "verification":
        conclusion = (
            "当前 verification 命令已超时；本轮不会继续剩余验证、reflect 或 review。"
            if status == "timed_out"
            else "当前 verification 已按用户请求停止；本轮不会继续剩余验证、reflect 或 review。"
        )
    else:
        conclusion = (
            "当前 attempt 已超时；timeout 不等于任务失败，但本轮不会继续 verification/review。"
            if status == "timed_out"
            else "当前 attempt 已按用户请求停止，本轮不会继续 verification/review。"
        )
    path = iteration_dir / filename
    details = [
        f"# {title}",
        "",
        f"- 步骤：`{step}`",
        f"- 状态：`{status}`",
    ]
    if command:
        details.append(f"- 命令：`{command}`")
    details.extend(
        [
            f"- 原因：{reason or '未提供'}",
            "",
            "## 结论",
            "",
            f"- {conclusion}",
            "- 目标仓库现场和 execution 证据均保留，交由人工检查。",
            "- Vega 未自动回滚、清理、提交、推送或发布。",
        ]
    )
    _write_text_artifact(path, "\n".join(details).rstrip() + "\n")
    return path


def _write_verification_interruption_report(
    iteration_dir: Path,
    verification: VerificationRunResult,
) -> Path:
    status = verification.interruption_status
    if status in {"timed_out", "stopped"}:
        return _write_execution_interruption_report(
            iteration_dir,
            step="verification",
            status=status,
            reason=verification.interruption_reason,
            command=verification.interruption_command,
        )
    if status != "termination-unconfirmed":
        raise ValueError("verification interruption 缺少可识别状态")

    path = iteration_dir / "runner-error-report.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Verification Termination Report",
                "",
                "- 步骤：`verification`",
                "- 状态：`termination-unconfirmed`",
                f"- 命令：`{verification.interruption_command or '未知'}`",
                f"- 原因：{verification.interruption_reason or '未提供'}",
                "",
                "## 结论",
                "",
                "- owned process tree 的终止未被确认，不能把本轮视为普通命令失败。",
                "- 剩余验证命令、reflect 和 reviewer 均未继续执行。",
                "- execution、验证输出和目标仓库现场均已保留，必须人工确认进程与工作区状态。",
                "- Vega 未自动回滚、清理、提交、推送或发布。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _write_runner_error_report(
    iteration_dir: Path,
    *,
    step: str,
    reason: str | None,
    workspace_status: str,
    termination_unconfirmed: bool = False,
) -> Path:
    path = iteration_dir / "runner-error-report.md"
    if termination_unconfirmed:
        _write_text_artifact(
            path,
            "\n".join(
                [
                    "# Runner Termination Report",
                    "",
                    f"- 步骤：`{step}`",
                    "- 状态：`termination-unconfirmed`",
                    f"- 原因：{reason or '未提供'}",
                    "",
                    "## 结论",
                    "",
                    "- owned process tree 的终止未被确认，不能按普通 runner error 继续处理。",
                    "- runner 输出、工作区检查、verification 和 review 均未继续消费。",
                    "- execution 证据和目标仓库现场已保留，必须人工确认进程与工作区状态。",
                    "- Vega 未自动回滚、清理、提交、推送或发布。",
                ]
            ).rstrip()
            + "\n",
        )
        return path
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Runner Error Report",
                "",
                f"- 步骤：`{step}`",
                f"- 原因：{reason or '未提供'}",
                "",
                "## 当前工作区",
                "",
                "```text",
                workspace_status.strip() or "<clean>",
                "```",
                "",
                "## 结论",
                "",
                "- 外部 runner 异常不等于工作区没有改动，当前现场必须按部分完成处理。",
                "- 本轮未继续 verification/review，不能把现有 diff 视为已完成或已通过。",
                "- Vega 未自动回滚、清理、提交、推送或发布。",
                "",
                "## 建议下一步",
                "",
                "- 先人工检查 `git status`、当前 diff 和 `worker-output.txt`。",
                "- 如部分改动可保留，补齐后运行 `vega loop continue` 进入验证和隔离审查。",
                "- 如改动不可用，人工清理后重开新的 loop。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _write_final_report(
    run_dir: Path,
    state: LoopAutomationState,
    verdict: ReviewVerdict | None,
    conclusion: str,
) -> None:
    latest_iteration = state.iterations[-1] if state.iterations else None
    lines = [
        "# Final Report",
        "",
        f"- 结论：{conclusion}",
        f"- 模式：`{state.automation_mode}`",
        f"- 任务类型：`{state.task_mode}`",
        f"- brief run：`{state.brief_run or '无'}`",
        f"- 迭代轮数：`{len(state.iterations)}`",
        "",
        "## Review Verdict",
        "",
    ]
    if verdict:
        lines.extend([f"- verdict：`{verdict.verdict}`", f"- summary：{verdict.summary}", ""])
        if verdict.findings:
            lines.append("## 剩余 Findings")
            lines.append("")
            for finding in verdict.findings:
                lines.append(f"- [{finding.severity}] {finding.title}：{finding.recommendation}")
        else:
            lines.append("- 未发现阻塞问题。")
    else:
        lines.append("- 未产生有效 reviewer verdict。")
    if (
        latest_iteration
        and latest_iteration.verification_failure_kind == "project_config_invalid"
    ):
        lines.extend(
            [
                "",
                "## 验证门禁",
                "",
                "- verification：`failed`",
                "- failure kind：`project_config_invalid`",
                "- 项目配置预检失败，未执行任何验证命令。",
            ]
        )
    elif (
        latest_iteration
        and latest_iteration.verification_failure_kind == "workspace_capture_failed"
    ):
        lines.extend(
            [
                "",
                "## 验证门禁",
                "",
                "- verification：`failed`",
                "- failure kind：`workspace_capture_failed`",
                "- 工作区指纹采集失败，本轮验证证据不能绑定或复用。",
            ]
        )
    elif latest_iteration and latest_iteration.verification_status == "failed":
        lines.extend(
            [
                "",
                "## 验证门禁",
                "",
                f"- verification：`failed`，失败命令数：{latest_iteration.verification_failed_count}",
                "- 机器验证失败或中断时，不能自动进入 ready_to_commit。",
            ]
        )
    lines.extend(
        [
            "",
            "## 后续",
            "",
            "- 如需提交，请人工检查 diff 后自行 commit。",
            "- 如本次产生跨任务可复用经验，可执行 `vega reflect --lesson \"...\"` 生成候选。",
        ]
    )
    _write_text_artifact(
        run_dir / "final-report.md",
        "\n".join(lines).rstrip() + "\n",
    )


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        _write_text_artifact(
            target,
            source.read_text(encoding="utf-8", errors="replace"),
        )


def _read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _reflect_has_tracked_diff(reflect_run: Path) -> bool:
    return bool(_read_optional_text(reflect_run / "full-diff.patch").strip())


def _write_text_artifact(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact_text(text), encoding="utf-8")


def _finalize_loop_eval(
    run_dir: Path,
    state: LoopAutomationState,
    requested_status: Literal["success", "failed", "needs_human"],
    artifacts: list[str],
    trace: TraceWriter,
) -> None:
    _write_text_artifact(run_dir / "eval.md", "# Eval\n\n(pending)\n")
    state.status = "running"
    state.artifacts = artifacts
    state.save(run_dir / "state.json")

    try:
        base_results = run_loop_eval(
            run_dir,
            artifacts,
            require_terminal=False,
            status_for_eval=requested_status,
        )
    except Exception as exc:  # noqa: BLE001 - 终态收口必须 fail-closed
        base_results = [f"FAIL: loop eval 执行异常：{type(exc).__name__}"]
    final_status = _status_after_eval(base_results, requested_status)
    if requested_status == "success" and final_status == "failed":
        state.current_step = "completion_eval_failed"
    state.eval_results = base_results
    _write_text_artifact(run_dir / "eval.md", render_eval(base_results))
    state.save(run_dir / "state.json")
    trace.write("eval_written", results=base_results)
    if final_status == "needs_human":
        state.status = final_status
        state.save(run_dir / "state.json")
        trace.write("run_paused", status=state.status, current_step=state.current_step)
        return

    trace.write("run_finished", status=final_status)
    terminal_results = _loop_terminal_eval_results(
        run_dir,
        final_status,
        state.superseded_terminal_events,
    )
    if any(result.startswith("FAIL:") for result in terminal_results):
        raise RuntimeError("loop 终态 trace 审计失败，state 保持 running 以阻止误判成功")
    state.status = final_status
    state.eval_results = [*base_results, *terminal_results]
    _write_text_artifact(run_dir / "eval.md", render_eval(state.eval_results))
    state.save(run_dir / "state.json")


def _status_after_eval(
    eval_results: list[str],
    requested_status: Literal["success", "failed", "needs_human"],
) -> Literal["success", "failed", "needs_human"]:
    if any(result.startswith("FAIL:") for result in eval_results):
        return "failed"
    return requested_status


def _new_loop_run_id(task_mode: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{task_mode}-loop"


def _verification_status(
    command_count: int,
    failed_count: int,
    failure_kind: str | None = None,
) -> Literal["skipped", "passed", "failed"]:
    if failure_kind is not None:
        return "failed"
    if command_count == 0:
        return "skipped"
    if failed_count:
        return "failed"
    return "passed"


def _latest_verification_passed(state: LoopAutomationState) -> bool:
    if not state.iterations:
        return False
    latest = state.iterations[-1]
    return (
        latest.verification_status == "passed"
        and latest.verification_failed_count == 0
        and latest.verification_failure_kind is None
    )


def _load_stable_start_policy(
    repo_path: Path,
) -> tuple[ProjectConfig, dict[str, str | None], str]:
    """在创建 run 前稳定绑定 HEAD、策略文件 bytes 与解析后的配置。"""
    policy_before = project_policy_snapshot(repo_path)
    head_before = read_head_sha(repo_path)
    config = load_project_config(repo_path)
    policy_after = project_policy_snapshot(repo_path)
    head_after = read_head_sha(repo_path)
    if policy_before != policy_after or head_before != head_after:
        raise RuntimeError("loop 启动时 HEAD 或项目策略发生变化，请在工作区稳定后重试")
    return config, policy_after, head_after


def _project_policy_changed(
    repo_path: Path,
    initial_snapshot: dict[str, str | None],
) -> bool:
    return project_policy_snapshot(repo_path) != initial_snapshot


def _write_project_policy_snapshot(
    run_dir: Path,
    snapshot: dict[str, str | None],
) -> None:
    run_dir.joinpath("project-policy-snapshot.json").write_text(
        _project_policy_snapshot_text(snapshot),
        encoding="utf-8",
    )


def _project_policy_snapshot_text(snapshot: dict[str, str | None]) -> str:
    return json.dumps(redact_value(snapshot), ensure_ascii=False, indent=2) + "\n"


def _write_project_policy_change_report(
    iteration_dir: Path,
    initial_snapshot: dict[str, str | None],
    current_snapshot: dict[str, str | None],
) -> Path:
    path = iteration_dir / "project-policy-change-report.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Project Policy Change Report",
                "",
                "- worker 或人工执行后检测到 `.vega.yaml` / `.vega.yml` 发生变化。",
                "- 为避免执行策略被运行过程改写，本轮未继续自动 verification、reflect 或 reviewer。",
                "- Vega 未自动回滚、删除、提交、推送或发布任何内容。",
                "",
                "## 启动时策略快照",
                "",
                "```json",
                json.dumps(initial_snapshot, ensure_ascii=False, indent=2),
                "```",
                "",
                "## 当前策略快照",
                "",
                "```json",
                json.dumps(current_snapshot, ensure_ascii=False, indent=2),
                "```",
                "",
                "## 建议下一步",
                "",
                "- 人工审查策略文件改动是否属于本次任务。",
                "- 确认后重新创建 loop，或人工完成验证和隔离审查。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _apply_runner_defaults(
    config: ProjectConfig,
    worker_name: str,
    reviewer_name: str,
) -> tuple[str, str]:
    if worker_name == "codex-exec" and config.runner.worker:
        worker_name = config.runner.worker
    if reviewer_name == "codex-exec" and config.runner.reviewer:
        reviewer_name = config.runner.reviewer
    return worker_name, reviewer_name
