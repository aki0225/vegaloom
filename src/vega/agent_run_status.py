from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_contract import AgentObservation, AgentState
from .agent_persistence import AgentArtifactError, load_agent_state, read_agent_trace
from .models import LoopAutomationState
from .progress import PROGRESS_VERSION, RunProgressLog, safe_run_id, safe_run_step
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
    "worker_dispatch_reconciled": ("worker", "binding_reconciled"),
    "verification_retry_committed": ("verification", "retry_started"),
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


class AgentTraceReadError(ValueError):
    """Trace 无法读取时的专用错误，便于诊断卡安全降级。"""


_LIVE_CHILD_WAITING = "等待子流程状态"


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
    payload = {
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
    live_child_stage = read_live_child_stage(run_dir, state)
    if live_child_stage is not None:
        # 这是 status 的只读投影，不回写 Agent State，也不改变父流程阶段。
        payload["live_child_stage"] = live_child_stage
    return payload


def read_live_child_stage(run_dir: Path, state: AgentState) -> str | None:
    """读取 active assist child 的当前步骤，供 status 实时展示。

    child 的 `state.json` 是 assist loop 自己的权威状态。这里仅作展示投影，
    不用它推导父 Agent 的成功、失败或下一步决策。
    """

    if (
        state.phase not in {"acting", "observing", "needs_human"}
        or not state.active_child_run
    ):
        return None

    workspace = run_dir.parent.parent
    child_run = state.active_child_run
    try:
        child_dir = resolve_run_dir(workspace, child_run)
    except FileNotFoundError as exc:
        # dispatch 已落盘但 child 可能还没创建自己的 state.json；此时只能等待，
        # 绝不能把缺失状态显示成 passed 或其他成功阶段。
        if "run 不存在于当前 workspace" in str(exc):
            return _LIVE_CHILD_WAITING
        raise ValueError(
            f"active child `{child_run}` 的路径无法安全解析；已拒绝展示。"
        ) from exc

    child_state = _load_live_child_state(child_dir, child_run)
    if child_state is None:
        return _LIVE_CHILD_WAITING
    _require_live_child_repo_binding(run_dir, child_run, child_state)
    if not child_state.current_step.strip():
        raise ValueError(
            "active child state.json 缺少有效 current_step；已拒绝展示。"
        )
    return safe_run_step(child_state.current_step)


def _load_live_child_state(
    child_dir: Path,
    child_run: str,
) -> LoopAutomationState | None:
    state_path = child_dir / "state.json"
    if not state_path.exists():
        return None

    try:
        child_state = LoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(
            f"active child `{child_run}` 的 state.json 无法验证；已拒绝展示。"
        ) from exc

    if child_state.run_id != child_run:
        raise ValueError(
            "active child state.json 的 run_id 与绑定 child 不一致；"
            "已拒绝展示错误证据。"
        )
    return child_state


def _require_live_child_repo_binding(
    run_dir: Path,
    child_run: str,
    child_state: LoopAutomationState,
) -> None:
    try:
        metadata = json.loads(
            (run_dir / "agent-run.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Agent run `{run_dir.name}` 的 agent-run.json 无法验证；"
            "已拒绝核对 child 仓库身份。"
        ) from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("repo_path"), str):
        raise ValueError(
            f"Agent run `{run_dir.name}` 缺少可验证的 repo binding；已拒绝展示 child。"
        )
    try:
        parent_repo = Path(metadata["repo_path"]).resolve()
        child_repo = Path(child_state.repo_path).resolve()
    except (OSError, ValueError) as exc:
        raise ValueError(
            "active child 与 Agent run 的 repo binding 无法解析；已拒绝展示。"
        ) from exc
    if child_repo != parent_repo:
        raise ValueError(
            "active child 与 Agent run 的仓库身份不一致；已拒绝展示。"
        )


def latest_trusted_child_run(
    run_dir: Path,
    state: AgentState,
    *,
    observation: AgentObservation | None = None,
) -> str | None:
    """从当前绑定或可信 dispatch Trace 恢复最近一次真实 child。"""

    traced_execution = latest_dispatch_binding(run_dir, state)
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


def latest_dispatch_binding(
    run_dir: Path,
    state: AgentState,
) -> tuple[str, str] | None:
    """读取最近一次可验证的 Writer dispatch 绑定。

    `worker_dispatch_reconciled` 只在 recovery 已经同时拿到保留的 operation
    身份和 execution 证据时追加，因此仍然属于同一条可审计绑定链，而不是
    用 Worker 自述补写一个“看起来成功”的 dispatch。
    """

    try:
        trace_items = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise AgentTraceReadError(
            f"Agent run `{run_dir.name}` 的 trace.jsonl 无法安全读取。"
        ) from exc
    return _latest_dispatched_execution(
        trace_items,
        expected_run_id=state.run_id,
    )


def latest_worker_dispatch_binding(
    run_dir: Path,
    state: AgentState,
) -> tuple[str, str] | None:
    """只读取最近一次真实 Worker dispatch，不把验证恢复冒充成 Worker。"""

    try:
        trace_items = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise AgentTraceReadError(
            f"Agent run `{run_dir.name}` 的 trace.jsonl 无法安全读取。"
        ) from exc
    return _latest_dispatched_execution(
        trace_items,
        expected_run_id=state.run_id,
        events={"worker_dispatch_committed", "worker_dispatch_reconciled"},
    )


def trusted_worker_label(
    run_dir: Path,
    state: AgentState,
    *,
    observation: AgentObservation | None,
    checkpoint_status: str | None,
) -> str:
    """为状态卡生成不越过证据边界的 Worker 标签。"""

    try:
        return latest_trusted_child_run(
            run_dir,
            state,
            observation=observation,
        ) or "未启动"
    except AgentTraceReadError:
        if checkpoint_status != "blocked":
            raise
        # Trace 损坏时只能说明 binding 仍被保留，不能把未验证 child
        # 当成可信证据展示；详细原因由 Checkpoint 和 recovery 报告承载。
        return "未验证（保留 binding）"


def agent_status_lines(payload: dict[str, Any]) -> list[str]:
    if payload.get("kind") != "agent":
        return []
    lines = [
        f"- Agent 阶段：`{payload['agent_phase']}`",
        f"- Work Item：`{payload.get('current_work_item') or '未记录'}`",
        f"- Checkpoint：`{payload.get('latest_checkpoint_id') or '尚无'}`",
        f"- 允许动作：`{', '.join(payload.get('allowed_actions') or []) or '无'}`",
    ]
    if payload.get("integrity_warning"):
        lines.insert(0, f"- 证据告警：{payload['integrity_warning']}")
    if payload.get("evidence_health"):
        lines.append(f"- 证据健康：`{payload['evidence_health']}`")
    if payload.get("workspace_current") is not None:
        lines.append(
            f"- Workspace 与证据一致：`{'是' if payload['workspace_current'] else '否'}`"
        )
    if payload.get("commit_recommended") is not None:
        lines.append(
            f"- 建议提交：`{'是' if payload['commit_recommended'] else '否'}`"
        )
    if payload.get("active_child_run"):
        lines.append(f"- active child：`{payload['active_child_run']}`")
    if payload.get("live_child_stage"):
        lines.append(f"- Core 子流程：`{payload['live_child_stage']}`")
    if payload.get("terminal_status"):
        lines.append(f"- Finish：`{payload['terminal_status']}`")
    return lines


def agent_live_stage_payload(state: dict[str, Any]) -> dict[str, str]:
    stage = state.get("live_child_stage")
    return {"live_child_stage": stage} if isinstance(stage, str) else {}


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
            "当前本机 run 已停止，不能使用 resume-local；如需继续，请人工创建 Handoff 或新的 Agent run。",
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
    events: set[str] | None = None,
) -> tuple[str, str] | None:
    latest_execution: tuple[str, str] | None = None
    operation_bindings: dict[str, str] = {}
    accepted_events = events or {
        "worker_dispatch_committed",
        "worker_dispatch_reconciled",
        "verification_retry_committed",
    }
    for item in trace_items:
        event = item.get("event")
        if event not in accepted_events:
            continue
        expected_phase = (
            "observing" if event == "verification_retry_committed" else "acting"
        )
        if item.get("run_id") != expected_run_id or item.get("phase") != expected_phase:
            raise ValueError("Agent operation Trace 与 Agent run 身份不一致")
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
