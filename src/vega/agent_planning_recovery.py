from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .agent_contract import AgentPlan, AgentState
from .agent_planning_execution import PreparedPlanningTurn
from .agent_planning_publication import (
    publish_active_planning_blocked,
    publish_planning_failure,
    publish_planning_stopped,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
)
from .execution_control import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionRecord,
    StopRequest,
    find_execution_records,
    inspect_execution_for_recovery,
)
from .workspace_snapshot import ReviewWorkspaceSnapshot


def prepare_planning_state(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    event_reporter: Callable[[str], None] | None,
) -> ReviewWorkspaceSnapshot | AgentRun:
    if state.run_kind != "change":
        raise ValueError("当前 ChangeRun 不在 Planning 阶段")
    if state.active_child_run or state.active_candidate_sha:
        raise ValueError("Planning 阶段不能绑定 Worker 或 Candidate")
    if state.handoff_status != "none":
        raise ValueError("当前 Planning ChangeRun 已生成 Handoff，不能继续本机执行")
    snapshot = capture_bound_workspace(run_dir)
    if state.active_planning_execution_id is not None:
        return settle_interrupted_planning_execution(
            run_dir,
            state,
            plan,
            snapshot,
            event_reporter=event_reporter,
        )
    if state.phase != "planning":
        raise ValueError("当前 ChangeRun 不在 Planning 阶段")
    return snapshot


def reconcile_planning_exception(
    workspace: Path,
    prepared: PreparedPlanningTurn,
    exc: Exception,
    *,
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    run_dir, state, plan, _ = load_agent_bundle(
        workspace,
        prepared.run_dir.name,
    )
    if (
        state.phase != "planning"
        or state.state_version != prepared.state_version
        or state.active_planning_execution_id != prepared.execution_id
    ):
        return AgentRun(run_dir=run_dir, state=state, plan=plan)
    actual = capture_bound_workspace(run_dir)
    reason = f"Planning Runner 异常：{type(exc).__name__}: {exc}"
    evidence_refs = [
        record.path.relative_to(run_dir).as_posix()
        for record in find_execution_records(run_dir)
        if record.lease.execution_id == prepared.execution_id
    ]
    try:
        record = require_terminal_planning_execution(
            run_dir,
            prepared.execution_id,
        )
    except ValueError as execution_exc:
        return publish_active_planning_blocked(
            run_dir,
            state,
            plan,
            actual,
            f"{reason}；Planning execution 终态无法确认：{execution_exc}",
            evidence_refs=evidence_refs,
            event_reporter=event_reporter,
        )
    state = update_state(state, active_planning_execution_id=None)
    if record.lease.status == "stopped" and planning_stop_was_requested(
        run_dir,
        prepared.execution_id,
    ):
        return publish_planning_stopped(
            run_dir,
            state,
            plan,
            actual,
            record.lease.reason or "Planning 已按 stop request 停止",
            evidence_refs=evidence_refs,
            event_reporter=event_reporter,
        )
    return publish_planning_failure(
        run_dir,
        state,
        plan,
        actual,
        (
            f"{reason}；同时检测到 Workspace 漂移"
            if actual.fingerprint != prepared.before.fingerprint
            else reason
        ),
        needs_human=actual.fingerprint != prepared.before.fingerprint,
        evidence_refs=evidence_refs,
        event_reporter=event_reporter,
    )


def settle_interrupted_planning_execution(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    *,
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    execution_id = state.active_planning_execution_id
    assert execution_id is not None
    settled = update_state(state, active_planning_execution_id=None)
    if (
        state.workspace_fingerprint != snapshot.fingerprint
        or snapshot.head_sha != state.accepted_checkpoint_sha
    ):
        return _planning_failure(
            run_dir,
            settled,
            plan,
            snapshot,
            "Planning execution 中断后 Workspace 已漂移，必须人工核对",
            needs_human=True,
            event_reporter=event_reporter,
        )
    records = [
        record
        for record in find_execution_records(run_dir)
        if record.lease.execution_id == execution_id
    ]
    if len(records) != 1:
        return _planning_failure(
            run_dir,
            settled,
            plan,
            snapshot,
            (
                "Planning State 仍绑定 execution，但无法找到唯一 execution 证据；"
                "为避免并发调查，必须人工核对"
            ),
            needs_human=True,
            event_reporter=event_reporter,
        )
    inspection = inspect_execution_for_recovery(run_dir)
    if not inspection.can_recover:
        raise ValueError("当前 Planning Turn 仍在运行；不能启动第二个 Planner")
    record = records[0]
    evidence_refs = [record.path.relative_to(run_dir).as_posix()]
    if record.lease.termination_unconfirmed:
        return _planning_failure(
            run_dir,
            settled,
            plan,
            snapshot,
            "Planning 进程终止未确认，不能安全重试",
            needs_human=True,
            evidence_refs=evidence_refs,
            event_reporter=event_reporter,
        )
    if record.lease.status == "stopped" and planning_stop_was_requested(
        run_dir,
        execution_id,
    ):
        return publish_planning_stopped(
            run_dir,
            settled,
            plan,
            snapshot,
            record.lease.reason or "Planning 已按 stop request 停止",
            evidence_refs=evidence_refs,
            event_reporter=event_reporter,
        )
    return _planning_failure(
        run_dir,
        settled,
        plan,
        snapshot,
        (
            f"上次 Planning execution 已失去活动主体，"
            f"终态为 {record.lease.status}；请显式重试"
        ),
        evidence_refs=evidence_refs,
        event_reporter=event_reporter,
    )


def planning_stop_was_requested(
    run_dir: Path,
    execution_id: str,
) -> bool:
    path = (
        run_dir
        / "executions"
        / "planning"
        / execution_id
        / "stop-request.json"
    )
    try:
        request = StopRequest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False
    return request.execution_id == execution_id


def require_terminal_planning_execution(
    run_dir: Path,
    execution_id: str,
) -> ExecutionRecord:
    records = [
        record
        for record in find_execution_records(run_dir)
        if record.lease.execution_id == execution_id
    ]
    if len(records) != 1:
        raise ValueError("无法唯一定位当前 Planning execution")
    record = records[0]
    if record.lease.step != "runner":
        raise ValueError("当前 execution 不属于 Planning Runner")
    if record.lease.status not in TERMINAL_EXECUTION_STATUSES:
        raise ValueError("Planning execution 尚未形成可信终态")
    inspection = inspect_execution_for_recovery(run_dir)
    if not inspection.can_recover:
        raise ValueError(f"Planning 仍有活动或未确认进程：{inspection.summary}")
    return record


def _planning_failure(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    reason: str,
    *,
    needs_human: bool = False,
    evidence_refs: list[str] | None = None,
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    return publish_planning_failure(
        run_dir,
        state,
        plan,
        snapshot,
        reason,
        needs_human=needs_human,
        evidence_refs=evidence_refs or [],
        event_reporter=event_reporter,
    )
