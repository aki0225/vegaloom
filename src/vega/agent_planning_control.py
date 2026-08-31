from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from .agent_contract import AgentPlan, AgentState
from .agent_persistence import append_agent_trace
from .agent_run import AgentRun
from .agent_runtime_support import write_status_card
from .execution_control import (
    ACTIVE_EXECUTION_STATUSES,
    StopRequest,
    find_execution_records,
    request_stop_for_run,
)
from .execution_paths import ExecutionPathGuard
from .redaction import write_redacted_json_once


def request_planning_stop(
    run_dir: Path,
    execution_id: str,
    reason: str,
) -> Path:
    """停止当前 Planning execution，并覆盖进程刚要启动的竞态窗口。"""

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("stop 必须提供原因，方便后续追溯")
    if (
        len(execution_id) != 32
        or any(character not in "0123456789abcdef" for character in execution_id)
    ):
        raise ValueError("Planning execution_id 必须是 32 位小写十六进制")

    matching = [
        record
        for record in find_execution_records(run_dir)
        if record.lease.execution_id == execution_id
    ]
    if len(matching) > 1:
        raise ValueError("Planning execution 证据不唯一，拒绝写入 stop request")
    if matching:
        record = matching[0]
        if record.lease.status not in ACTIVE_EXECUTION_STATUSES:
            raise ValueError(
                "Planning execution 已结束；重新运行同一 ChangeRun 完成终态对账"
            )
        stopped = request_stop_for_run(
            run_dir,
            normalized_reason,
            expected_execution_id=execution_id,
        )
        return stopped.path.parent / "stop-request.json"

    execution_dir = run_dir / "executions" / "planning" / execution_id
    guard = ExecutionPathGuard(run_dir, execution_dir)
    guard.prepare()
    path = execution_dir / "stop-request.json"
    guard.validate_artifact(path)
    request = StopRequest(
        reason=normalized_reason,
        requested_at=datetime.now(UTC).isoformat(),
        requester_pid=os.getpid(),
        execution_id=execution_id,
    )
    try:
        write_redacted_json_once(path, request.model_dump(mode="json", exclude_none=True))
    except FileExistsError:
        try:
            existing = StopRequest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("既有 Planning stop request 无法验证") from exc
        if existing.execution_id != execution_id:
            raise ValueError("既有 Planning stop request 身份不一致")
    return path


def stop_planning_run(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    reason: str,
) -> AgentRun:
    execution_id = state.active_planning_execution_id
    assert execution_id is not None
    stop_path = request_planning_stop(run_dir, execution_id, reason)
    execution_ref = stop_path.relative_to(run_dir).as_posix()
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="planning_stop_requested",
        state=state,
        observation_summary="已向当前只读 Planning execution 写入绑定身份的 stop request",
        route_reason=reason.strip(),
        artifact_refs=[execution_ref],
    )
    write_status_card(
        run_dir,
        state,
        plan,
        next_step=(
            "等待当前 Planning execution 返回 stopped；"
            "原 CLI 异常退出时，重新运行同一 ChangeRun 完成对账"
        ),
    )
    return AgentRun(run_dir=run_dir, state=state, plan=plan)
