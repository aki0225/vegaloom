from __future__ import annotations

import json
from pathlib import Path

from .agent_contract import AgentCheckpoint, AgentPlan, AgentState
from .agent_run import AgentRun
from .agent_persistence import (
    append_agent_trace,
    load_agent_checkpoint,
    load_agent_state,
    read_agent_trace,
    save_agent_state,
)
from .agent_recovery_request import AgentRecoveryRequest
from .redaction import write_redacted_json
from .agent_run_status import latest_dispatch_binding
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    validate_run_repository_binding,
    write_checkpoint,
    write_status_card,
)
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


def agent_trace_issue(trace_path: Path) -> str | None:
    try:
        read_agent_trace(trace_path)
    except (OSError, ValueError):
        return "Trace 尾部或结构损坏"
    return None


def blocked_recovery_reason(
    request: AgentRecoveryRequest,
    *,
    operation_started: bool,
    workspace_unchanged: bool,
    workspace_clear: bool,
) -> str:
    reasons: list[str] = []
    if operation_started:
        reasons.append("operation 已开始或无法证明未开始")
    if not workspace_unchanged:
        reasons.append("Workspace 已产生 partial diff 或其他变化")
    if not workspace_clear:
        reasons.append("Workspace 控制信息不完整")
    if request.external_side_effects == "unknown":
        reasons.append("外部副作用未知，禁止自动重放")
    elif request.external_side_effects == "known":
        reasons.append("已存在外部副作用，是否重试必须由人工决定")
    return "；".join(reasons) or "恢复证据不足，交由人工判断"


def reconcile_missing_dispatch_trace(
    run_dir: Path,
    state: AgentState,
    evidence_refs: list[str],
) -> str | None:
    """用保留的 operation 与 execution 证据补记丢失的 dispatch Trace。"""

    dispatch_binding = latest_dispatch_binding(run_dir, state)
    expected_binding = (state.active_child_run, state.active_operation_id)
    if dispatch_binding is not None and dispatch_binding != expected_binding:
        return "Worker dispatch Trace 与当前保留的 Writer binding 不一致"
    if dispatch_binding is None:
        # bind 已经先写入 State 和 operation reservation，但 dispatch Trace
        # 可能在进程崩溃或短暂 I/O 故障时没有落盘。此处只在 execution
        # 已被调用方严格核对后补记，不能把缺失 Trace 当成成功。
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="worker_dispatch_reconciled",
            state=state,
            observation_summary=(
                "根据保留的 operation reservation 与已核对的 execution "
                "证据补记 Writer dispatch 绑定"
            ),
            artifact_refs=list(evidence_refs),
        )
    return None


def block_on_trace_issue(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    actual: ReviewWorkspaceSnapshot,
    request: AgentRecoveryRequest,
    issue: str,
) -> AgentRun:
    """Trace 不可信时保留原 Writer binding，并写入阻断 Checkpoint。"""

    next_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=actual.fingerprint,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        actual,
        reason=f"{issue}；已保留原 Writer binding，禁止自动接管",
        status="blocked",
        pending_actions=["human"],
        failed_attempts=[],
        operation_started=state.operation_started,
        external_side_effects=request.external_side_effects,
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=f"{issue}；人工核对旧 Writer、Workspace 和原始 Artifact",
    )
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def block_on_execution_issue(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    actual: ReviewWorkspaceSnapshot,
    request: AgentRecoveryRequest,
    issue: str,
) -> AgentRun:
    """execution 证据不可信时保留原 Writer binding。"""

    next_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=actual.fingerprint,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        actual,
        reason=f"{issue}；已保留原 Writer binding，禁止自动接管",
        status="blocked",
        pending_actions=["human"],
        operation_started=state.operation_started,
        external_side_effects=request.external_side_effects,
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="agent_recovery_execution_blocked",
        state=next_state,
        route_reason=checkpoint.reason,
        artifact_refs=[f"checkpoints/{checkpoint.checkpoint_id}.json"],
    )
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=f"{issue}；人工核对旧 Worker、进程与 Workspace",
    )
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def require_recovery_request(
    state: AgentState,
    request: AgentRecoveryRequest,
) -> None:
    if not request.reason.strip():
        raise ValueError("recover 必须提供原因")
    if state.phase not in {"acting", "observing", "needs_human"}:
        raise ValueError(f"当前阶段不需要 Worker recovery：{state.phase}")
    if not state.active_child_run or not state.active_operation_id:
        raise ValueError("当前 run 没有可对账的 Writer binding")


def latest_checkpoint(run_dir: Path, state: AgentState) -> AgentCheckpoint:
    if state.latest_checkpoint_id is None:
        raise ValueError("当前 run 没有 Checkpoint")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if checkpoint.run_id != state.run_id:
        raise ValueError("Checkpoint 与 Agent run 身份不一致")
    return checkpoint


def write_load_failure_report(
    run_dir: Path,
    reason: str,
    error: ValueError,
) -> None:
    """状态损坏时只记录现场诊断，不猜测或覆盖 Agent State。"""

    workspace_status: dict[str, object] = {
        "captured": False,
        "fingerprint": None,
        "changed_files": [],
    }
    try:
        state = load_agent_state(run_dir / "agent-state.json")
        metadata = json.loads(
            (run_dir / "agent-run.json").read_text(encoding="utf-8")
        )
        if not isinstance(metadata, dict):
            raise ValueError("agent-run.json 必须是 JSON object")
        repo = validate_run_repository_binding(run_dir, state, metadata)
        snapshot = capture_review_workspace(repo)
        workspace_status = {
            "captured": True,
            "fingerprint": snapshot.fingerprint,
            "changed_files": list(snapshot.changed_files),
        }
    except (OSError, ValueError, json.JSONDecodeError):
        workspace_status["issue"] = (
            "Agent State 与 repo binding 无法共同验证，未重新采集 Workspace"
        )
    write_redacted_json(
        run_dir / "agent-recovery-report.json",
        {
            "schema_version": 1,
            "status": "blocked",
            "reason": reason.strip() or "未提供恢复原因",
            "error_type": type(error).__name__,
            "state_preserved": True,
            "workspace": workspace_status,
            "next_step": "人工检查损坏的 Agent Artifact 与真实 Workspace",
        },
    )


def validate_resume_checkpoint(
    plan: AgentPlan,
    checkpoint: AgentCheckpoint,
    actual: ReviewWorkspaceSnapshot,
) -> None:
    if (
        checkpoint.status != "safe"
        or actual.fingerprint != checkpoint.workspace_fingerprint
        or checkpoint.operation_started
        or checkpoint.external_side_effects != "none"
        or not plan.approval_is_current()
    ):
        raise ValueError("最近 Checkpoint 不能证明现场可恢复；请先重新对账或修订 Plan")
