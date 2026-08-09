from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..execution_control import inspect_execution_for_recovery
from ..goal_models import GoalCheckpointRecord, GoalContract, GoalState
from ..progress import RunProgressLog
from ..redaction import write_redacted_json, write_redacted_text
from ..run_lock import RunMutationBusyError, RunMutationLock
from ..run_status import run_status_payload
from ..run_utils import resolve_run_dir
from ..trace import TraceWriter
from .goal_evidence import validate_goal_evidence
from .goal_reporting import render_checkpoint_reconcile_report

if TYPE_CHECKING:
    from .goal_runtime import GoalRuntime


def reconcile_checkpoint(runtime: GoalRuntime, run: str) -> Path:
    """重新核对已记录 child，并只在证据合格时完成 checkpoint。"""

    from .goal_controller import _mark_checkpoint_blocked
    from .goal_runtime import _active_checkpoint, _ensure_action_allowed

    run_dir, state, contract = runtime._load(run)
    _ensure_action_allowed(state, "reconcile")
    active = _active_checkpoint(state)
    child_run = bound_child_run(state, active)

    if active is None:
        latest = state.checkpoint_records[-1] if state.checkpoint_records else None
        if (
            state.status == "checkpoint_done"
            and latest is not None
            and latest.status == "done"
        ):
            return run_dir
        raise ValueError("goal 没有可重新核对的 active checkpoint。")
    if not child_run:
        raise ValueError(
            f"checkpoint {active.checkpoint} 没有记录 child run，不能 reconcile。"
        )

    try:
        child_dir = resolve_run_dir(runtime.workspace, child_run)
        with RunMutationLock.acquire(child_dir, "goal.reconcile"):
            return _reconcile_locked_child(
                runtime,
                run,
                run_dir,
                state,
                contract,
                active.checkpoint,
                child_dir,
            )
    except RunMutationBusyError:
        raise
    except (FileNotFoundError, ValueError) as exc:
        _mark_checkpoint_blocked(
            run_dir,
            state,
            contract,
            active.checkpoint,
            f"已记录 child 无法安全读取或核对：{type(exc).__name__}",
            RunProgressLog(run_dir),
        )
        return run_dir


def _reconcile_locked_child(
    runtime: GoalRuntime,
    run: str,
    run_dir: Path,
    state: GoalState,
    contract: GoalContract,
    checkpoint: str,
    child_dir: Path,
) -> Path:
    from .goal_controller import _mark_checkpoint_blocked

    payload = run_status_payload(runtime.workspace, child_dir.name)
    _validate_child_identity(state, payload)
    child_status = str(payload.get("status") or "unknown")
    inspection = inspect_execution_for_recovery(child_dir)
    active = next(
        (
            item
            for item in state.checkpoint_records
            if item.checkpoint == checkpoint and item.status == "planned"
        ),
        None,
    )
    if active is None:
        raise ValueError(f"checkpoint {checkpoint} 已不再处于可核对状态。")
    if active.bound_child_run is None:
        active.bound_child_run = child_dir.name
    state.last_child_run = child_dir.name
    state.last_child_status = child_status
    if not inspection.can_recover:
        state.status = "running" if child_status == "running" else "needs_human"
        state.current_step = (
            "waiting_for_worker"
            if child_status == "running"
            else "child_recovery_required"
        )
        state.active_child_run = child_dir.name
        progress_event = (
            "child_reconciled"
            if child_status == "running"
            else "child_recovery_required"
        )
        _persist_reconcile_result(
            run_dir,
            state,
            contract,
            checkpoint,
            child_dir.name,
            child_status,
            state.current_step,
            inspection.summary,
            progress_event=progress_event,
        )
        return run_dir

    if child_status == "running":
        state.status = "needs_human"
        state.current_step = "child_recovery_required"
        state.active_child_run = child_dir.name
        _persist_reconcile_result(
            run_dir,
            state,
            contract,
            checkpoint,
            child_dir.name,
            child_status,
            "child_recovery_required",
            inspection.summary,
            progress_event="child_recovery_required",
        )
        return run_dir

    state.active_child_run = None
    if child_status != "success":
        _persist_reconcile_result(
            run_dir,
            state,
            contract,
            checkpoint,
            child_dir.name,
            child_status,
            "checkpoint_blocked",
            f"child 已进入 `{child_status}`，不满足自动完成 checkpoint 的条件。",
        )
        _mark_checkpoint_blocked(
            run_dir,
            state,
            contract,
            checkpoint,
            f"重新核对后 child 状态仍为 {child_status}。",
            RunProgressLog(run_dir),
        )
        return run_dir

    state.status = "running"
    state.current_step = "checkpoint_reconciling"
    _persist_reconcile_result(
        run_dir,
        state,
        contract,
        active.checkpoint,
        child_dir.name,
        child_status,
        "validate_evidence",
        "child 已成功；正在重新校验仓库身份、artifact integrity、verification 和证据新鲜度。",
    )
    try:
        reconcile_loop_evidence_without_lock(
            runtime,
            run,
            checkpoint,
            child_dir.name,
        )
        runtime._checkpoint_done_without_lock(
            run,
            checkpoint,
            note="重新核对已恢复 child，证据资格通过。",
        )
    except (FileNotFoundError, ValueError) as exc:
        run_dir, state, contract = runtime._load(run)
        _mark_checkpoint_blocked(
            run_dir,
            state,
            contract,
            checkpoint,
            f"child 成功但 checkpoint 证据重新校验失败：{type(exc).__name__}",
            RunProgressLog(run_dir),
        )
    return run_dir


def reconcile_loop_evidence_without_lock(
    runtime: GoalRuntime,
    run: str,
    checkpoint: str,
    child_run: str,
) -> Path:
    from .goal_runtime import (
        _active_checkpoint,
        _dedupe,
        _find_checkpoint_record,
        _normalize_checkpoint,
        _save_goal_state,
        _write_progress,
    )

    run_dir, state, contract = runtime._load(run)
    checkpoint_id = _normalize_checkpoint(checkpoint)
    record = _find_checkpoint_record(state, checkpoint_id)
    if record is None or record.status != "planned":
        raise ValueError(f"checkpoint {checkpoint_id} 不是可重新核对的 active checkpoint。")
    active = _active_checkpoint(state)
    if active is None or active.checkpoint != checkpoint_id:
        raise ValueError(f"只能重新核对当前 active checkpoint：{active.checkpoint if active else '无'}")
    bound_child = bound_child_run(state, record)
    if bound_child is None or bound_child != child_run:
        raise ValueError(
            f"checkpoint {checkpoint_id} 的绑定 child 与 reconcile 目标不一致。"
        )
    evidence = validate_goal_evidence(
        runtime.workspace,
        Path(state.repo_path),
        child_run,
        "loop",
        "Goal reconcile 重新校验已记录 child loop 证据。",
        state.scope_profile,
    )
    existing_index = next(
        (
            index
            for index, item in enumerate(record.refs)
            if item.type == "loop" and item.run == evidence.run
        ),
        None,
    )
    if existing_index is None:
        record.refs.append(evidence)
    else:
        evidence.attached_at = record.refs[existing_index].attached_at
        record.refs[existing_index] = evidence
    evidence_path = run_dir / "checkpoints" / checkpoint_id / "checkpoint-evidence.json"
    write_redacted_json(evidence_path, record.model_dump(mode="json"))
    rel_evidence = f"checkpoints/{checkpoint_id}/checkpoint-evidence.json"
    state.status = "running"
    state.current_step = "checkpoint_evidence_reconciled"
    state.artifacts = _dedupe([*state.artifacts, rel_evidence])
    _write_progress(run_dir, state, contract)
    _save_goal_state(run_dir, state)
    TraceWriter(run_dir / "goal-trace.jsonl").write(
        "goal_checkpoint_evidence_reconciled",
        checkpoint=checkpoint_id,
        child_run=evidence.run,
        completion_eligible=evidence.completion_eligible,
    )
    return run_dir


def ensure_bound_child_recoverable(
    workspace: Path,
    state: GoalState,
    record: GoalCheckpointRecord | None,
) -> str | None:
    child_run = bound_child_run(state, record)
    if child_run is None:
        return None
    child_dir = resolve_run_dir(workspace, child_run)
    with RunMutationLock.acquire(child_dir, "goal.reconcile"):
        inspection = inspect_execution_for_recovery(child_dir)
        if not inspection.can_recover:
            raise ValueError(
                "绑定 child 仍存在可确认执行主体，Goal 不能先行 recover："
                f"{inspection.summary}"
            )
    if record is not None and record.bound_child_run is None:
        record.bound_child_run = child_run
    return child_run


def bound_child_run(
    state: GoalState,
    record: GoalCheckpointRecord | None,
) -> str | None:
    if record is None:
        return None
    automatic_steps = {
        "waiting_for_worker",
        "checkpoint_blocked",
        "child_recovery_required",
        "checkpoint_reconciling",
        "checkpoint_evidence_reconciled",
        "recovered",
    }
    candidates = {
        item
        for item in [record.bound_child_run, state.active_child_run]
        if item
    }
    if state.current_step in automatic_steps and state.last_child_run:
        candidates.add(state.last_child_run)
    loop_refs = {
        reference.run
        for reference in record.refs
        if reference.type == "loop"
    }
    if record.bound_child_run is not None:
        candidates.update(loop_refs)
    elif state.current_step in automatic_steps:
        if candidates:
            candidates.update(loop_refs - candidates)
        elif len(loop_refs) == 1:
            candidates.update(loop_refs)
        elif len(loop_refs) > 1:
            raise ValueError(
                f"checkpoint {record.checkpoint} 的旧状态存在多个 loop ref，"
                "不能推断自动 child 绑定。"
            )
    if len(candidates) > 1:
        raise ValueError(
            f"checkpoint {record.checkpoint} 存在多个 child 候选，"
            "不能自动判断绑定关系。"
        )
    return next(iter(candidates), None)


def _validate_child_identity(
    state: GoalState,
    payload: dict[str, object],
) -> None:
    if payload.get("kind") != "loop":
        raise ValueError("Goal 记录的 child 不是 loop run。")
    child_repo = payload.get("repo_path")
    if not isinstance(child_repo, str) or not child_repo:
        raise ValueError("child run 缺少 repo_path。")
    if Path(child_repo).resolve() != Path(state.repo_path).resolve():
        raise ValueError("child run 与 Goal 不属于同一目标仓库。")


def _persist_reconcile_result(
    run_dir: Path,
    state: GoalState,
    contract: GoalContract,
    checkpoint: str,
    child_run: str,
    child_status: str,
    decision: str,
    detail: str,
    *,
    progress_event: str = "child_reconciled",
) -> None:
    from .goal_runtime import _dedupe, _save_goal_state, _write_progress

    state.last_reconciled_at = datetime.now(UTC).isoformat()
    report_rel = f"checkpoints/{checkpoint}/checkpoint-reconcile.md"
    write_redacted_text(
        run_dir / report_rel,
        render_checkpoint_reconcile_report(
            state,
            contract,
            checkpoint,
            child_run,
            child_status,
            decision,
            detail,
        ),
    )
    state.artifacts = _dedupe([*state.artifacts, report_rel])
    _write_progress(run_dir, state, contract)
    _save_goal_state(run_dir, state)
    TraceWriter(run_dir / "goal-trace.jsonl").write(
        "goal_child_reconciled",
        checkpoint=checkpoint,
        child_run=child_run,
        child_status=child_status,
        decision=decision,
    )
    RunProgressLog(run_dir).append(
        f"goal.{progress_event}",
        checkpoint=checkpoint,
        child_run=child_run,
        status=child_status,
    )
