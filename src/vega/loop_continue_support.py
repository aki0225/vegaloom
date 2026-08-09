from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .execution_control import inspect_execution_for_recovery
from .loop_evidence import validate_review_evidence_freshness
from .loop_initialization import loop_initialization_issues
from .models import (
    LoopAutomationState,
    ReviewVerdict,
    WorkerRerunAuthorization,
)
from .run_utils import resolve_run_dir
from .trace import TraceWriter, active_run_finished_indices, read_trace_items
from .worker_rerun import load_bound_auto_worker_baseline
from .workspace_baseline import LEGACY_WORKSPACE_BASELINE_UNAVAILABLE
from .workspace_check import snapshot_workspace
from .workspace_inventory import WorkspaceSnapshot, workspace_ignored_path_exclusions


@dataclass(frozen=True)
class AutoWorkerRecoveryPlan:
    rerun: bool
    iteration_number: int | None = None
    previous_verdict: ReviewVerdict | None = None
    expected_workspace_snapshot: WorkspaceSnapshot | None = None
    source_interrupted_iteration: int | None = None
    source_worker_baseline_sha256: str | None = None


@dataclass(frozen=True)
class _AutoWorkerRecoveryAssessment:
    snapshot: WorkspaceSnapshot
    rerun_safe: bool
    previous_verdict: ReviewVerdict | None = None
    source_interrupted_iteration: int | None = None
    source_worker_baseline_sha256: str | None = None


def plan_recovered_auto_worker(
    workspace: Path,
    repo_path: Path,
    run_dir: Path,
    state: LoopAutomationState,
    *,
    rerun_requested: bool,
    has_test_log: bool,
    has_note: bool,
) -> AutoWorkerRecoveryPlan:
    assessment = _assess_recovered_auto_worker(
        workspace,
        repo_path,
        run_dir,
        state,
    )
    if assessment is None:
        if rerun_requested:
            raise ValueError(
                "--rerun-worker 仅适用于 auto loop 从 Worker 中断恢复且尚无新 tracked diff 的场景。"
            )
        return AutoWorkerRecoveryPlan(rerun=False)
    snapshot = assessment.snapshot
    if not snapshot.capture_complete:
        raise ValueError("恢复后的工作区快照不完整，已拒绝重新运行 Worker。")
    if (
        state.initial_head_sha
        and snapshot.head_sha
        and snapshot.head_sha != state.initial_head_sha
    ):
        raise ValueError("Worker 中断后 Git HEAD 已变化，已拒绝重新运行 Worker。")
    if assessment.rerun_safe:
        if not rerun_requested:
            raise ValueError(
                "auto Worker 在中断前未形成相对于上一可信基线的新 tracked diff；"
                "默认 continue 不会跳过 Worker 执行验证。"
                "检查 recovery-report.md 后，明确使用 --rerun-worker "
                "重新运行同一 child 的 Worker。"
            )
        if has_test_log or has_note:
            raise ValueError("--rerun-worker 不能与 --test-log 或 --note 同时使用。")
        iteration_number = next_iteration_number(run_dir, state)
        if iteration_number > state.max_iterations:
            raise ValueError("当前 loop 已达到最大迭代轮数，不能重新运行 Worker。")
        return AutoWorkerRecoveryPlan(
            rerun=True,
            iteration_number=iteration_number,
            previous_verdict=assessment.previous_verdict,
            expected_workspace_snapshot=snapshot,
            source_interrupted_iteration=assessment.source_interrupted_iteration,
            source_worker_baseline_sha256=assessment.source_worker_baseline_sha256,
        )
    if rerun_requested:
        raise ValueError(
            "Worker 中断后工作区已偏离上一可信基线；"
            "为避免覆盖 partial work，已拒绝 --rerun-worker。"
        )
    return AutoWorkerRecoveryPlan(rerun=False)


def initialize_auto_worker_rerun(
    workspace: Path,
    repo_path: Path,
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
    *,
    start_iteration: int,
    rerun_requested: bool,
    expected_workspace_snapshot: WorkspaceSnapshot | None,
    source_interrupted_iteration: int | None = None,
    source_worker_baseline_sha256: str | None = None,
) -> WorkspaceSnapshot | None:
    recovered_snapshot: WorkspaceSnapshot | None = None
    if expected_workspace_snapshot is not None:
        recovered_snapshot = snapshot_workspace(
            repo_path,
            ignored_path_exclusions=workspace_ignored_path_exclusions(
                workspace,
                repo_path,
            ),
        )
        if recovered_snapshot != expected_workspace_snapshot:
            raise ValueError(
                "工作区在确认重跑后、Worker 启动前发生变化；"
                "已拒绝 --rerun-worker，避免覆盖新的人工或外部修改。"
            )
    if rerun_requested:
        if (
            state.last_recovery_id is None
            or source_interrupted_iteration is None
            or source_worker_baseline_sha256 is None
        ):
            raise ValueError("Worker 重跑缺少可验证的恢复授权绑定。")
        authorization = WorkerRerunAuthorization(
            rerun_iteration=start_iteration,
            source_interrupted_iteration=source_interrupted_iteration,
            recovery_id=state.last_recovery_id,
            source_worker_baseline_sha256=source_worker_baseline_sha256,
        )
        if any(
            item.rerun_iteration == authorization.rerun_iteration
            or item.recovery_id == authorization.recovery_id
            for item in state.worker_rerun_authorizations
        ):
            raise ValueError("Worker 重跑授权已存在，已拒绝重复写入。")
        state.worker_rerun_authorizations.append(authorization)
        state.status = "running"
        state.save(run_dir / "state.json")
        trace.write(
            "auto_worker_rerun_requested",
            **authorization.model_dump(mode="json"),
        )
    return recovered_snapshot


def select_worker_workspace_snapshot(
    workspace: Path,
    repo_path: Path,
    recovered_snapshot: WorkspaceSnapshot | None,
    *,
    iteration_number: int,
    start_iteration: int,
) -> tuple[WorkspaceSnapshot, WorkspaceSnapshot | None]:
    if recovered_snapshot is not None and iteration_number == start_iteration:
        return recovered_snapshot, None
    return (
        snapshot_workspace(
            repo_path,
            ignored_path_exclusions=workspace_ignored_path_exclusions(
                workspace,
                repo_path,
            ),
        ),
        recovered_snapshot,
    )


def next_iteration_number(
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


def require_loop_initialization(
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


def require_execution_recoverable(run_dir: Path) -> None:
    inspection = inspect_execution_for_recovery(run_dir)
    if not inspection.can_recover:
        raise ValueError(
            "当前 loop 仍有未安全消失的 execution，已拒绝 continue："
            f"{inspection.summary}"
        )


def require_recovery_trace_binding(
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
    _require_terminal_binding(items, state)
    if state.current_step not in {
        "recovered",
        "recovered_initialization_incomplete",
        LEGACY_WORKSPACE_BASELINE_UNAVAILABLE,
    }:
        return
    recovery_id = state.last_recovery_id
    if recovery_id is None:
        _require_legacy_recovery_event(items)
        return
    recovered = _require_recovery_event(items, recovery_id)
    _validate_recovery_event(recovered, state, recovery_id)
    _validate_interruption_event(items, state, recovery_id)


def _assess_recovered_auto_worker(
    workspace: Path,
    repo_path: Path,
    run_dir: Path,
    state: LoopAutomationState,
) -> _AutoWorkerRecoveryAssessment | None:
    if (
        state.automation_mode != "auto"
        or state.current_step != "recovered"
        or not state.iterations
    ):
        return None
    latest = state.iterations[-1]
    if latest.lifecycle != "interrupted" or latest.interrupted_step != "worker":
        return None
    snapshot = snapshot_workspace(
        repo_path,
        ignored_path_exclusions=workspace_ignored_path_exclusions(
            workspace,
            repo_path,
        ),
    )
    try:
        source_baseline = load_bound_auto_worker_baseline(
            run_dir,
            state,
            iteration=latest.iteration,
        )
    except ValueError:
        return _AutoWorkerRecoveryAssessment(
            snapshot=snapshot,
            rerun_safe=False,
            source_interrupted_iteration=latest.iteration,
        )
    baseline_unchanged = (
        source_baseline.capture_complete
        and source_baseline.tracked_diff_complete
        and snapshot == source_baseline
    )
    previous_reviewed = next(
        (
            iteration
            for iteration in reversed(state.iterations[:-1])
            if iteration.lifecycle == "completed"
            and iteration.review_run
            and iteration.verdict
        ),
        None,
    )
    if previous_reviewed is None:
        return _AutoWorkerRecoveryAssessment(
            snapshot=snapshot,
            rerun_safe=(
                baseline_unchanged
            ),
            source_interrupted_iteration=latest.iteration,
            source_worker_baseline_sha256=state.worker_baseline_sha256,
        )
    try:
        freshness = validate_review_evidence_freshness(
            workspace,
            repo_path,
            previous_reviewed.review_run or "",
        )
        previous_verdict = ReviewVerdict.model_validate_json(
            resolve_run_dir(
                workspace,
                previous_reviewed.review_run or "",
            ).joinpath("review-verdict.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, ValueError):
        return _AutoWorkerRecoveryAssessment(
            snapshot=snapshot,
            rerun_safe=False,
            source_interrupted_iteration=latest.iteration,
            source_worker_baseline_sha256=state.worker_baseline_sha256,
        )
    if (
        not freshness.fresh
        or previous_verdict.verdict != previous_reviewed.verdict
        or previous_verdict.verdict != "request_changes"
    ):
        return _AutoWorkerRecoveryAssessment(
            snapshot=snapshot,
            rerun_safe=False,
            source_interrupted_iteration=latest.iteration,
            source_worker_baseline_sha256=state.worker_baseline_sha256,
        )
    return _AutoWorkerRecoveryAssessment(
        snapshot=snapshot,
        rerun_safe=baseline_unchanged,
        previous_verdict=previous_verdict,
        source_interrupted_iteration=latest.iteration,
        source_worker_baseline_sha256=state.worker_baseline_sha256,
    )


def _require_terminal_binding(
    items: list[dict[str, object]],
    state: LoopAutomationState,
) -> None:
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


def _require_legacy_recovery_event(items: list[dict[str, object]]) -> None:
    if not any(item.get("event") == "loop_recovered" for item in items):
        raise ValueError("loop 缺少 recovery trace，已拒绝 continue。")


def _require_recovery_event(
    items: list[dict[str, object]],
    recovery_id: str,
) -> dict[str, object]:
    matches = [
        item
        for item in items
        if item.get("event") == "loop_recovered"
        and item.get("recovery_id") == recovery_id
    ]
    if len(matches) != 1:
        raise ValueError("loop_recovered 与 state.last_recovery_id 不一致。")
    return matches[0]


def _validate_recovery_event(
    recovered: dict[str, object],
    state: LoopAutomationState,
    recovery_id: str,
) -> None:
    continuation_allowed = state.current_step not in {
        "recovered_initialization_incomplete",
        LEGACY_WORKSPACE_BASELINE_UNAVAILABLE,
    }
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


def _validate_interruption_event(
    items: list[dict[str, object]],
    state: LoopAutomationState,
    recovery_id: str,
) -> None:
    if not state.iterations or state.iterations[-1].lifecycle != "interrupted":
        return
    latest = state.iterations[-1]
    matches = [
        item
        for item in items
        if item.get("event") == "loop_iteration_interrupted"
        and item.get("recovery_id") == recovery_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "loop_iteration_interrupted 与 state.last_recovery_id 不一致。"
        )
    interruption = matches[0]
    if (
        interruption.get("iteration") != latest.iteration
        or interruption.get("previous_step") != latest.interrupted_step
    ):
        raise ValueError("loop interruption trace 与最新 interrupted state 不一致。")
