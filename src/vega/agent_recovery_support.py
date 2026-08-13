from __future__ import annotations

import json
from pathlib import Path

from .agent_contract import AgentCheckpoint, AgentPlan, AgentState
from .agent_persistence import load_agent_checkpoint, read_agent_trace
from .agent_recovery_request import AgentRecoveryRequest
from .redaction import write_redacted_json
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
    workspace_unchanged: bool,
    workspace_clear: bool,
) -> str:
    reasons: list[str] = []
    if request.operation_started:
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
    if request.operation_started != state.operation_started:
        raise ValueError("恢复请求与持久化 operation_started 不一致")
    if request.worker_alive:
        raise ValueError("宿主仍报告 Worker 存活；禁止释放当前 Writer binding")


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
        metadata = json.loads(
            (run_dir / "agent-run.json").read_text(encoding="utf-8")
        )
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("repo_path"), str
        ):
            raise ValueError("agent-run.json 缺少 repo binding")
        snapshot = capture_review_workspace(Path(metadata["repo_path"]))
        workspace_status = {
            "captured": True,
            "fingerprint": snapshot.fingerprint,
            "changed_files": list(snapshot.changed_files),
        }
    except (OSError, ValueError, json.JSONDecodeError):
        workspace_status["issue"] = "无法从 agent-run.json 重新采集 Workspace"
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
