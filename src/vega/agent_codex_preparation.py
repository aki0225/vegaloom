from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .agent_change_run import load_change_run_context
from .agent_change_control import (
    aggregate_final_review_verdict,
    requires_final_integration_review,
)
from .agent_contract import AgentObservation, AgentState, AgentWorkItem
from .agent_worker_evidence import hash_evidence_refs
from .codex_app_server_runner import CodexAppServerRunner
from .execution_control import RunnerExecutionContext
from .agent_persistence import read_agent_trace
from .comparison_binding import require_comparison_binding_from_mapping
from .loop_runtime import LoopAutomationRuntime
from .project_config import ProjectConfig
from .progress import make_execution_progress_reporter
from .redaction import write_redacted_json_once
from .review_contract import ReviewVerdict
from .review_runtime import parse_review_verdict
from .runner import CodexExecRunner, Runner
from .agent_runtime_support import load_agent_bundle
from .verification_command_preflight import require_verification_commands_preflight
from .workspace_check import ReviewWorkspaceSnapshot

def comparison_binding_from_metadata(
    metadata: dict[str, str],
) -> tuple[str | None, tuple[str, ...]]:
    comparison_base_sha, comparison_paths = require_comparison_binding_from_mapping(
        metadata,
        base_key="comparison_base_revision",
    )
    return comparison_base_sha, comparison_paths
def prepare_dispatch_binding(
    metadata: dict[str, str],
    repo: Path,
    work_item: AgentWorkItem,
) -> tuple[str | None, tuple[str, ...]]:
    require_verification_commands_preflight(repo, work_item.verification)
    return comparison_binding_from_metadata(metadata)
def validate_prepared_workspace(
    snapshot: ReviewWorkspaceSnapshot,
    *,
    expected_fingerprint: str,
    requires_clean_workspace: bool,
) -> None:
    if snapshot.fingerprint != expected_fingerprint:
        raise ValueError("创建 child 前 Workspace 已漂移，必须先重新对账")
    if requires_clean_workspace and (
        snapshot.staged_diff.strip()
        or snapshot.unstaged_diff.strip()
        or snapshot.untracked_files
    ):
        raise ValueError(
            "Gate 2B 首次真实 Worker 要求干净 Workspace；"
            "已有 Diff 的跨机器接力和累计归因属于后续 Gate"
        )


def read_task_brief(run_dir: Path) -> str:
    try:
        content = (run_dir / "task-brief.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("无法读取当前 Task Brief") from exc
    if not content.strip():
        raise ValueError("当前 Task Brief 为空")
    return content


def next_attempt_context(
    run_dir: Path,
    state: AgentState,
    *,
    max_repair_rounds: int = 1,
) -> tuple[int, bool]:
    trace = read_agent_trace(run_dir / "trace.jsonl")
    epoch_indexes = [
        index
        for index, item in enumerate(trace)
        if item.get("event") in {
            "plan_approved",
            "change_contract_approved",
            "change_execution_plan_auto_applied",
            "task_card_resumed",
        }
    ]
    if not epoch_indexes:
        raise ValueError("当前 Plan 缺少可验证的 attempt epoch，拒绝启动 Worker")
    epoch_index = epoch_indexes[-1]
    attempts = sum(
        1
        for item in trace[epoch_index + 1 :]
        if item.get("event") == "worker_dispatch_committed"
        and item.get("work_item") == state.current_work_item
    )
    max_attempts = max_repair_rounds + 1
    if attempts >= max_attempts:
        raise ValueError(
            "当前 Work Item 的 Worker attempt 预算已用完："
            f"{attempts}/{max_attempts}；必须由人工修改 Contract、Plan 或停止任务"
        )
    has_historical_dispatch = any(
        item.get("event") == "worker_dispatch_committed" for item in trace
    )
    requires_clean_workspace = (
        not has_historical_dispatch
        and trace[epoch_index].get("event")
        in {"plan_approved", "change_contract_approved"}
    )
    return attempts + 1, requires_clean_workspace
def ensure_isolated_reviewer(
    loop_runtime: object,
    config: ProjectConfig,
    *,
    agent_run_dir: Path | None = None,
    state: AgentState | None = None,
    persistent_session: bool = False,
) -> None:
    """只为默认 Core Reviewer 注入 MCP 隔离，不覆盖显式测试或替代 runner。"""

    if not isinstance(loop_runtime, LoopAutomationRuntime):
        return
    if (
        loop_runtime.reviewer_runner is not None
        and not isinstance(loop_runtime.reviewer_runner, CodexAppServerRunner)
    ):
        return
    if persistent_session:
        if agent_run_dir is None or state is None or state.current_work_item is None:
            raise ValueError("持久 Reviewer 缺少 Agent run 或当前 Work Item")
        loop_runtime.reviewer_runner = CodexAppServerRunner(
            agent_run_dir,
            f"reviewer:{state.current_work_item}",
            work_item_id=state.current_work_item,
            contract_revision=state.contract_revision,
            plan_revision=state.execution_plan_revision,
            output_schema=ReviewVerdict.model_json_schema(),
            isolate_session=True,
            options=config.runner.codex_exec.reviewer,
        )
        return
    if loop_runtime.reviewer_runner is None:
        loop_runtime.reviewer_runner = CodexExecRunner(
            options=config.runner.codex_exec.reviewer,
            isolate_mcp=True,
        )


def review_final_candidate(
    workspace: Path,
    run_dir: Path,
    observation: AgentObservation,
    config: ProjectConfig,
    *,
    persistent_session: bool,
    attempt_number: int,
    timeout_seconds: int,
    progress_reporter: Callable[[str, int], None] | None,
    event_reporter: Callable[[str], None],
    reviewer_runner: Runner | None = None,
) -> AgentObservation:
    """在需要时审查累计 Candidate；结果仍交给原 Supervisor 路由。"""

    _, current_state, plan, metadata = load_agent_bundle(workspace, run_dir.name)
    context = load_change_run_context(run_dir, current_state, plan, metadata)
    if context is None or not requires_final_integration_review(
        context,
        attempt_number=attempt_number,
    ):
        return observation
    candidate_sha = current_state.active_candidate_sha
    if candidate_sha is None:
        return _blocked_final_observation(
            run_dir,
            observation,
            "最终集成审查无法绑定唯一 Candidate SHA",
            payload={"status": "needs_human"},
        )
    try:
        batches = _final_review_batches(
            context.worktree.worktree_path,
            context.worktree.base_sha,
            candidate_sha,
            config.prompt_budget.reviewer_diff_max_chars,
        )
    except ValueError as exc:
        return _blocked_final_observation(
            run_dir,
            observation,
            str(exc),
            payload={"status": "needs_human"},
        )
    if not batches or len(batches) > 8:
        return _blocked_final_observation(
            run_dir,
            observation,
            "累计 Diff 无法在 8 个有界 Review 批次内完整覆盖",
            payload={"status": "needs_human", "batch_count": len(batches)},
        )
    event_reporter(f"最终集成审查已启动：{len(batches)} 个批次")
    verdicts: list[ReviewVerdict] = []
    runner_statuses: list[str] = []
    machine_evidence = {
        "candidate_sha": candidate_sha,
        "verification": observation.verification,
        "risk": observation.risk,
        "work_item_review": observation.review,
    }
    for index, (files, diff_text) in enumerate(batches, start=1):
        prompt = _final_review_prompt(
            context.contract.model_dump(mode="json"),
            context.execution_plan.model_dump(mode="json"),
            machine_evidence,
            files,
            diff_text,
            batch=index,
            total=len(batches),
        )
        if len(prompt) > config.prompt_budget.reviewer_max_chars:
            return _blocked_final_observation(
                run_dir,
                observation,
                "最终集成审查 Prompt 超过项目 Reviewer 预算",
                payload={"status": "needs_human", "batch": index},
            )
        runner = _final_review_runner(
            run_dir,
            current_state,
            candidate_sha,
            config,
            persistent_session=persistent_session,
            reviewer_runner=reviewer_runner,
        )
        execution_id = uuid4().hex
        result = runner.run(
            prompt,
            context.worktree.worktree_path,
            sandbox="read-only",
            timeout_seconds=timeout_seconds,
            execution_context=RunnerExecutionContext(
                execution_root=run_dir,
                execution_dir=(
                    run_dir
                    / "executions"
                    / "integration-review"
                    / f"{execution_id}-{index:02d}"
                ),
                run_id=run_dir.name,
                step="reviewer",
                execution_id=execution_id,
                iteration=index,
                progress_reporter=make_execution_progress_reporter(
                    run_dir,
                    progress_reporter,
                    iteration=index,
                ),
            ),
        )
        runner_statuses.append(result.status)
        verdict = parse_review_verdict(
            result.output,
            (
                result.error or f"Runner 终态为 {result.status}"
                if result.status != "success" or result.termination_unconfirmed
                else None
            ),
        )
        verdict = _require_batch_coverage(verdict, files)
        verdicts.append(verdict)
        if verdict.verdict != "approve":
            break
    final_status = aggregate_final_review_verdict(
        verdicts,
        required_risks=context.contract.authorized_risk_reviews,
    )
    payload = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "candidate_sha": candidate_sha,
        "base_sha": context.worktree.base_sha,
        "status": final_status,
        "runner_statuses": runner_statuses,
        "batches": [
            {
                "batch": index,
                "files": files,
                "verdict": verdict.model_dump(mode="json"),
            }
            for index, ((files, _), verdict) in enumerate(
                zip(batches, verdicts, strict=False),
                start=1,
            )
        ],
    }
    ref = f"integration-reviews/{observation.operation_id}.json"
    write_redacted_json_once(run_dir / ref, payload)
    evidence_refs = [*observation.evidence_refs, ref]
    evidence_sha256 = hash_evidence_refs(run_dir, evidence_refs)
    if final_status == "approve":
        event_reporter("最终集成审查通过")
        return observation.model_copy(
            update={
                "evidence_refs": evidence_refs,
                "evidence_sha256": evidence_sha256,
                "machine_summary": (
                    f"{observation.machine_summary}；最终集成 Reviewer=approve"
                ),
            }
        )
    if final_status == "request_changes":
        event_reporter("最终集成审查要求修复")
        return observation.model_copy(
            update={
                "evidence_refs": evidence_refs,
                "evidence_sha256": evidence_sha256,
                "machine_summary": (
                    f"{observation.machine_summary}；"
                    "最终集成 Reviewer=request_changes"
                ),
                "review": "failed",
                "work_item_completed": False,
                "all_work_items_completed": False,
                "repairable_in_scope": True,
            }
        )
    event_reporter("最终集成审查未形成可自动采用结论")
    return observation.model_copy(
        update={
            "evidence_refs": evidence_refs,
            "evidence_sha256": evidence_sha256,
            "machine_summary": (
                f"{observation.machine_summary}；最终集成 Reviewer=needs_human"
            ),
            "review": "blocked",
            "work_item_completed": False,
            "all_work_items_completed": False,
            "repairable_in_scope": False,
        }
    )


def _require_batch_coverage(
    verdict: ReviewVerdict,
    files: list[str],
) -> ReviewVerdict:
    if set(files).issubset(set(verdict.reviewed_files)):
        return verdict
    return ReviewVerdict(
        verdict="needs_human",
        summary="Reviewer 没有声明完整覆盖当前批次的变更文件",
        reviewed_files=verdict.reviewed_files,
        checked_items=verdict.checked_items,
        findings=verdict.findings,
        risk_disclosures=verdict.risk_disclosures,
    )


def _final_review_batches(
    repo: Path, base_sha: str, candidate_sha: str, max_chars: int
) -> list[tuple[list[str], str]]:
    names = _git_output(
        repo,
        ["diff", "--name-only", f"{base_sha}..{candidate_sha}", "--"],
    ).splitlines()
    batches: list[tuple[list[str], str]] = []
    batch_files: list[str] = []
    batch_parts: list[str] = []
    batch_size = 0
    for raw in names:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        diff = _git_output(
            repo,
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=3",
                f"{base_sha}..{candidate_sha}",
                "--",
                path,
            ],
        )
        if len(diff) > max_chars:
            return []
        if batch_parts and batch_size + len(diff) > max_chars:
            batches.append((batch_files, "\n".join(batch_parts)))
            batch_files, batch_parts, batch_size = [], [], 0
        batch_files.append(path)
        batch_parts.append(diff)
        batch_size += len(diff)
    if batch_parts:
        batches.append((batch_files, "\n".join(batch_parts)))
    return batches


def _git_output(repo: Path, args: list[str]) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0:
        raise ValueError("最终集成审查无法读取累计 Git Diff")
    return process.stdout


def _final_review_prompt(
    contract: dict[str, object],
    plan: dict[str, object],
    machine_evidence: dict[str, object],
    files: list[str],
    diff_text: str,
    *,
    batch: int,
    total: int,
) -> str:
    return (
        "# Vega 最终集成审查\n\n"
        "你是独立只读 Reviewer。只判断累计 Candidate 是否满足已批准合同。"
        "不要相信 Worker 自述，不要修改文件。输出严格匹配 ReviewVerdict JSON。\n\n"
        f"## Change Contract\n```json\n{json.dumps(contract, ensure_ascii=False)}\n```\n\n"
        f"## Execution Plan\n```json\n{json.dumps(plan, ensure_ascii=False)}\n```\n\n"
        "## 已绑定机器证据\n"
        f"```json\n{json.dumps(machine_evidence, ensure_ascii=False)}\n```\n\n"
        "上述状态由 Vega 按 Candidate SHA 对账，Verification 已在可写环境执行。"
        "不要仅因 Reviewer sandbox 无法创建临时文件或重跑测试而降级结论。"
        "Diff 有合同内问题时返回 request_changes；合同边界、外部状态或必要事实"
        "确实无法判断时才返回 needs_human。\n\n"
        f"## 批次\n{batch}/{total}；必须在 reviewed_files 中完整列出："
        f"{json.dumps(files, ensure_ascii=False)}\n\n"
        f"## 累计 Diff\n```diff\n{diff_text}\n```\n"
    )


def _final_review_runner(
    run_dir: Path, state: AgentState, candidate_sha: str, config: ProjectConfig,
    *, persistent_session: bool, reviewer_runner: Runner | None,
):
    if reviewer_runner is not None and not isinstance(
        reviewer_runner,
        CodexAppServerRunner,
    ):
        return reviewer_runner
    if persistent_session:
        return CodexAppServerRunner(
            run_dir,
            f"reviewer:integration:{candidate_sha[:12]}",
            work_item_id=state.current_work_item,
            contract_revision=state.contract_revision,
            plan_revision=state.execution_plan_revision,
            output_schema=ReviewVerdict.model_json_schema(),
            isolate_session=True,
            options=config.runner.codex_exec.reviewer,
        )
    return CodexExecRunner(
        options=config.runner.codex_exec.reviewer,
        output_schema=ReviewVerdict.model_json_schema(),
        isolate_mcp=True,
    )


def _blocked_final_observation(
    run_dir: Path, observation: AgentObservation, reason: str,
    *, payload: dict[str, object],
) -> AgentObservation:
    ref = f"integration-reviews/{observation.operation_id}.json"
    write_redacted_json_once(
        run_dir / ref,
        {
            "schema_version": 1,
            "run_id": run_dir.name,
            "status": "needs_human",
            "reason": reason,
            **payload,
        },
    )
    refs = [*observation.evidence_refs, ref]
    return observation.model_copy(
        update={
            "evidence_refs": refs,
            "evidence_sha256": hash_evidence_refs(run_dir, refs),
            "machine_summary": f"{observation.machine_summary}；{reason}",
            "review": "blocked",
            "work_item_completed": False,
            "all_work_items_completed": False,
            "repairable_in_scope": False,
        }
    )
