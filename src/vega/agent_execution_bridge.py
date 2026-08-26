from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from .agent_contract import AgentPlan, AgentState
from .agent_operation import (
    bound_operation_kind,
    operation_ref,
)
from .agent_persistence import append_agent_trace
from .agent_run import AgentRun
from .agent_runtime_support import write_status_card
from .execution_control import (
    ExecutionRecord,
    find_execution_records,
    request_stop_for_run,
)
from .models import LoopAutomationState
from .redaction import write_redacted_json_once
from .run_utils import resolve_run_dir


def resolve_bound_execution_run_dir(
    workspace: Path,
    agent_run_dir: Path,
    state: AgentState,
    metadata: dict[str, object],
) -> Path:
    """定位当前 operation 的执行目录，并验证它仍属于已绑定 child。"""

    if not state.active_child_run:
        return agent_run_dir
    try:
        child_dir = resolve_run_dir(workspace, state.active_child_run)
    except FileNotFoundError:
        return agent_run_dir
    child_state_path = child_dir / "state.json"
    if not child_state_path.exists():
        _validate_reserved_worker_child(agent_run_dir, child_dir, state)
        return child_dir
    try:
        child_state = LoopAutomationState.model_validate_json(
            child_state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("active child 存在，但无法验证其 assist loop 身份") from exc
    repo_path = metadata.get("repo_path")
    if not isinstance(repo_path, str):
        raise ValueError("Agent run 缺少可验证的 repo binding")
    if (
        child_state.run_id != state.active_child_run
        or child_state.automation_mode != "assist"
        or Path(child_state.repo_path).resolve()
        != Path(repo_path).resolve()
    ):
        raise ValueError("active child 与 Agent run 的仓库或运行身份不一致")
    return child_dir


def _validate_reserved_worker_child(
    agent_run_dir: Path,
    child_dir: Path,
    state: AgentState,
) -> None:
    """Core state 落盘前，只凭不可变 operation 和精确 execution 身份开放 stop/recover。"""

    if bound_operation_kind(agent_run_dir, state) != "worker":
        raise ValueError("active child 尚未初始化 Core state，且当前 operation 不是 Worker")
    assert state.active_operation_id is not None
    record = resolve_bound_worker_execution(child_dir, state.active_operation_id)
    if record.lease.run_id != state.active_child_run:
        raise ValueError("预留 Worker execution 与 active child 身份不一致")


def write_execution_evidence_ref(
    agent_run_dir: Path,
    state: AgentState,
    execution_run_dir: Path,
    record: ExecutionRecord,
) -> str:
    if execution_run_dir == agent_run_dir:
        return record.path.relative_to(agent_run_dir).as_posix()
    relative = f"recovery-executions/execution-{uuid4().hex[:12]}.json"
    write_redacted_json_once(
        agent_run_dir / relative,
        {
            "schema_version": 1,
            "authority": "execution_binding_summary",
            "agent_run_id": state.run_id,
            "work_item_id": state.current_work_item,
            "child_run": state.active_child_run,
            "operation_id": state.active_operation_id,
            "execution_id": record.lease.execution_id,
            "execution_status": record.lease.status,
            "execution_artifact": record.path.relative_to(
                execution_run_dir
            ).as_posix(),
            "execution_sha256": hashlib.sha256(record.path.read_bytes()).hexdigest(),
        },
    )
    return relative


def resolve_bound_worker_execution(
    run_dir: Path,
    operation_id: str,
) -> ExecutionRecord:
    records = find_execution_records(run_dir)
    bound = [
        record
        for record in records
        if record.lease.execution_id == operation_id
        and record.lease.step == "worker"
    ]
    if not bound:
        if records:
            raise ValueError("execution 记录与当前 active operation 身份不一致")
        raise ValueError("operation 可能已开始，但缺少可验证的 execution 记录")
    if len(bound) > 1:
        raise ValueError("当前 active operation 对应多个 Worker execution 记录")
    return bound[0]


def stop_active_child(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    *,
    reason: str,
) -> AgentRun:
    if not reason.strip():
        raise ValueError("pause/stop 必须提供原因")
    child_dir = resolve_bound_execution_run_dir(
        workspace,
        run_dir,
        state,
        metadata,
    )
    if child_dir == run_dir:
        raise ValueError(
            "Writer 仍绑定，但当前 child 不是可验证的 assist run；"
            "必须先确认停止并运行 recover 完成现场对账"
        )
    assert state.active_operation_id is not None
    operation_kind = bound_operation_kind(run_dir, state)
    record = request_stop_for_run(
        child_dir,
        reason,
        expected_execution_id=(
            state.active_operation_id if operation_kind == "worker" else None
        ),
    )
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="agent_stop_requested",
        state=state,
        observation_summary="已向当前 child 的匹配 owned execution 写入 stop request",
        route_reason=reason.strip(),
        artifact_refs=[
            operation_ref(state.active_operation_id),
            write_execution_evidence_ref(run_dir, state, child_dir, record),
        ],
    )
    write_status_card(
        run_dir,
        state,
        plan,
        next_step=(
            "等待当前 child execution 返回 stopped 并完成机器对账；"
            "若原 agent run 命令已中断，请执行 recover"
        ),
    )
    return AgentRun(run_dir=run_dir, state=state, plan=plan)
