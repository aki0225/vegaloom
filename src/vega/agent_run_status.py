from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_contract import AgentObservation, AgentState
from .agent_persistence import AgentArtifactError, load_agent_state, read_agent_trace
from .progress import PROGRESS_VERSION, RunProgressLog, safe_run_id
from .run_utils import resolve_run_dir


_PHASE_STATUS = {
    "planning": "created",
    "awaiting_approval": "paused",
    "ready": "paused",
    "acting": "running",
    "observing": "running",
    "needs_human": "needs_human",
    "finalizing": "running",
    "completed": "success",
    "stopped": "stopped",
}
_TRACE_PROGRESS = {
    "agent_started": ("agent", "started"),
    "plan_approved": ("agent", "plan_updated"),
    "plan_revised": ("agent", "plan_updated"),
    "plan_invalidated_by_steer": ("agent", "plan_updated"),
    "worker_dispatch_committed": ("worker", "started"),
    "supervisor_next": ("agent", "supervisor_next"),
    "supervisor_repair": ("agent", "supervisor_repair"),
    "supervisor_replan": ("agent", "supervisor_replan"),
    "supervisor_human": ("agent", "supervisor_human"),
    "supervisor_finalize": ("agent", "supervisor_finalize"),
    "agent_paused": ("agent", "agent_paused"),
    "agent_resumed": ("agent", "agent_resumed"),
    "agent_stopped": ("agent", "agent_stopped"),
    "agent_handoff_created": ("agent", "agent_handoff_created"),
    "agent_recovery_blocked": ("agent", "agent_recovery_blocked"),
    "agent_recovery_execution_blocked": ("agent", "agent_recovery_blocked"),
    "agent_completed": ("agent", "run_finished"),
    "task_card_resumed": ("agent", "agent_resumed"),
}
_TRACE_STATUS = {
    "agent_completed": "success",
    "agent_stopped": "stopped",
    "agent_paused": "paused",
    "supervisor_human": "needs_human",
    "agent_recovery_blocked": "needs_human",
    "agent_recovery_execution_blocked": "needs_human",
}


def load_agent_status_state(
    run_dir: Path,
    *,
    ordinary_state_exists: bool,
) -> dict[str, Any]:
    if ordinary_state_exists:
        raise ValueError(
            f"run `{run_dir.name}` 同时包含 state.json 与 agent-state.json；"
            "已拒绝展示竞争状态。"
        )
    try:
        state = load_agent_state(run_dir / "agent-state.json")
    except AgentArtifactError as exc:
        raise ValueError(
            f"run `{run_dir.name}` 的 agent-state.json 无法验证；"
            "已拒绝展示不完整状态。"
        ) from exc
    if state.run_id != run_dir.name:
        raise ValueError(
            "agent-state.json run_id 与 run 目录身份不一致；"
            "为避免展示错误证据链，已拒绝读取。"
        )
    latest_child_run = latest_trusted_child_run(run_dir, state)
    return {
        "_run_kind": "agent",
        "automation_mode": None,
        "run_id": state.run_id,
        "status": _PHASE_STATUS[state.phase],
        "current_step": state.phase,
        "agent_phase": state.phase,
        "task_id": state.task_id,
        "current_work_item": state.current_work_item,
        "active_child_run": state.active_child_run,
        "last_child_run": latest_child_run,
        "brief_run": latest_child_run,
        "latest_checkpoint_id": state.latest_checkpoint_id,
        "allowed_actions": list(state.allowed_actions),
        "terminal_status": state.terminal_status,
    }


def latest_trusted_child_run(
    run_dir: Path,
    state: AgentState,
    *,
    observation: AgentObservation | None = None,
) -> str | None:
    """从当前绑定或可信 dispatch Trace 恢复最近一次真实 child。"""

    try:
        trace_items = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Agent run `{run_dir.name}` 的 trace.jsonl 无法安全读取。"
        ) from exc
    traced_execution = _latest_dispatched_execution(
        trace_items,
        expected_run_id=state.run_id,
    )
    traced_child_run = traced_execution[0] if traced_execution is not None else None
    traced_operation_id = traced_execution[1] if traced_execution is not None else None
    if state.active_child_run is not None:
        if traced_child_run != state.active_child_run:
            raise ValueError("active child 与最近可信 dispatch Trace 不一致")
        if traced_operation_id != state.active_operation_id:
            raise ValueError("active operation 与最近可信 dispatch Trace 不一致")
        return state.active_child_run
    if observation is not None and observation.authority != "external_claim":
        if observation.child_run != traced_child_run:
            raise ValueError("可信 Observation 与最近 dispatch Trace 的 child 不一致")
        if observation.operation_id != traced_operation_id:
            raise ValueError(
                "可信 Observation 与最近 dispatch Trace 的 operation 不一致"
            )
        return observation.child_run
    return traced_child_run


def agent_status_lines(payload: dict[str, Any]) -> list[str]:
    if payload.get("kind") != "agent":
        return []
    lines = [
        f"- Agent 阶段：`{payload['agent_phase']}`",
        f"- Work Item：`{payload.get('current_work_item') or '未记录'}`",
        f"- Checkpoint：`{payload.get('latest_checkpoint_id') or '尚无'}`",
        f"- 允许动作：`{', '.join(payload.get('allowed_actions') or []) or '无'}`",
    ]
    if payload.get("active_child_run"):
        lines.append(f"- active child：`{payload['active_child_run']}`")
    if payload.get("terminal_status"):
        lines.append(f"- Finish：`{payload['terminal_status']}`")
    return lines


def agent_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    phase = state.get("agent_phase")
    if phase in {"planning", "awaiting_approval"}:
        return [
            f"读取 `{run_dir / 'agent-plan.json'}`，由主会话完成只读调查并提交单 Work Item Plan。",
            f"写入新 Plan：`vega agent plan --run {run_dir.name} --input <plan.json>`。",
            f"仅在人工明确批准后运行：`vega agent approve --run {run_dir.name}`。",
        ]
    if phase == "ready":
        return [
            f"执行当前批准 Work Item：`vega agent run --run {run_dir.name}`。",
            f"另一个终端可运行：`vega watch --run {run_dir.name} --follow`。",
        ]
    if phase in {"acting", "observing"}:
        return [
            f"当前 Agent 仍在执行或对账；运行：`vega watch --run {run_dir.name} --follow`。",
            f"如需人工停止：`vega agent stop --run {run_dir.name} --reason \"...\"`。",
        ]
    if phase == "finalizing":
        return [
            f"Core Finish 已完成但 Supervisor 终态尚未发布；运行：`vega agent finalize --run {run_dir.name}`。",
        ]
    if phase == "completed":
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和 child Core Finish 证据。",
            "人工检查全部 Diff 与验证结果后自行 commit；Vega 不自动 commit、push 或 release。",
        ]
    if phase == "needs_human":
        return [
            f"读取 `{run_dir / 'status-card.md'}`、最新 Checkpoint 与 Trace，确认阻断原因。",
            "根据现场选择 steer、resume-local、recover、handoff 或停止；不要在证据不明时启动第二 Writer。",
        ]
    if phase == "stopped":
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和最新 Checkpoint，确认保留的 Workspace。",
            f"如现场仍安全且要继续：`vega agent resume-local --run {run_dir.name}`。",
        ]
    return [f"读取 `{run_dir / 'agent-state.json'}`，人工确认 Agent 状态。"]


def agent_artifact_names(state: dict[str, Any]) -> list[str]:
    names = [
        "agent-state.json",
        "agent-plan.json",
        "status-card.md",
        "task-brief.md",
        "task-brief-manifest.json",
        "trace.jsonl",
    ]
    checkpoint_id = state.get("latest_checkpoint_id")
    if isinstance(checkpoint_id, str):
        names.append(f"checkpoints/{checkpoint_id}.json")
    return names


def agent_progress_items(
    workspace: Path,
    run_dir: Path,
    status_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        trace_items = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Agent run `{run_dir.name}` 的 trace.jsonl 无法安全读取。"
        ) from exc
    items = [
        progress
        for item in trace_items
        if (progress := _trace_progress_item(item)) is not None
    ]
    child_run = status_payload.get("last_child_run")
    items.extend(_child_progress_items(workspace, child_run))
    return sorted(items, key=lambda item: str(item.get("ts") or ""))


def _child_progress_items(
    workspace: Path,
    child_run: object,
) -> list[dict[str, Any]]:
    if not isinstance(child_run, str):
        return []
    try:
        child_dir = resolve_run_dir(workspace, child_run)
    except FileNotFoundError:
        return []
    result = []
    for item in RunProgressLog(child_dir).read():
        child_item = dict(item)
        child_item["child_run"] = safe_run_id(child_run)
        result.append(child_item)
    return result


def _trace_progress_item(item: dict[str, object]) -> dict[str, Any] | None:
    event = item.get("event")
    if not isinstance(event, str) or event not in _TRACE_PROGRESS:
        return None
    step, progress_event = _TRACE_PROGRESS[event]
    payload: dict[str, Any] = {
        "version": PROGRESS_VERSION,
        "step": step,
        "event": progress_event,
    }
    _copy_valid_timestamp(item, payload)
    child_run = item.get("child_run")
    if isinstance(child_run, str) and safe_run_id(child_run) != "unknown-run":
        payload["child_run"] = safe_run_id(child_run)
    if event in _TRACE_STATUS:
        payload["status"] = _TRACE_STATUS[event]
    return payload


def _copy_valid_timestamp(
    source: dict[str, object],
    target: dict[str, Any],
) -> None:
    timestamp = source.get("ts")
    if not isinstance(timestamp, str):
        return
    try:
        datetime.fromisoformat(timestamp)
    except ValueError:
        return
    target["ts"] = timestamp


def _latest_dispatched_execution(
    trace_items: list[dict[str, object]],
    *,
    expected_run_id: str,
) -> tuple[str, str] | None:
    latest_execution: tuple[str, str] | None = None
    operation_bindings: dict[str, str] = {}
    for item in trace_items:
        if item.get("event") != "worker_dispatch_committed":
            continue
        if item.get("run_id") != expected_run_id or item.get("phase") != "acting":
            raise ValueError("worker dispatch Trace 与 Agent run 身份不一致")
        child_run = item.get("child_run")
        operation_id = item.get("operation_id")
        if (
            not isinstance(child_run, str)
            or not child_run.strip()
            or not isinstance(operation_id, str)
            or not operation_id.strip()
        ):
            raise ValueError("worker dispatch Trace 缺少完整执行绑定")
        existing_child_run = operation_bindings.setdefault(operation_id, child_run)
        if existing_child_run != child_run:
            raise ValueError("worker dispatch Trace 存在 operation 身份冲突")
        latest_execution = (child_run, operation_id)
    return latest_execution
