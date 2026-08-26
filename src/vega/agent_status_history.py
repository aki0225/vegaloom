from __future__ import annotations

from .agent_contract import AgentCheckpoint, AgentObservation, AgentState


def status_history_note(
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
    observation: AgentObservation | None,
) -> str | None:
    """说明旧 attempt 的证据为何没有出现在当前门禁状态中。"""

    if observation is not None or checkpoint is None or not checkpoint.failed_attempts:
        return None
    count = len(checkpoint.failed_attempts)
    contract_revision = state.contract_revision or 0
    execution_plan_revision = state.execution_plan_revision or 0
    if contract_revision > 1 or execution_plan_revision > 1:
        return (
            f"保留 {count} 个历史失败 attempt；当前门禁只对应 Contract r"
            f"{contract_revision} / Plan r{execution_plan_revision}，"
            "旧结果不能作为本 revision 的通过证据。"
        )
    return (
        f"保留 {count} 个历史失败 attempt；当前卡片只显示仍能用于当前状态的门禁证据。"
    )
