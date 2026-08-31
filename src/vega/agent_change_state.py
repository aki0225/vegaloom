from __future__ import annotations

from typing import Protocol


class ChangeStateView(Protocol):
    run_kind: str
    phase: str
    contract_revision: int | None
    approved_contract_digest: str | None
    execution_plan_revision: int | None
    accepted_checkpoint_sha: str | None
    active_candidate_sha: str | None


def validate_change_state_bindings(state: ChangeStateView) -> None:
    """校验 ChangeRun 专属字段，避免 legacy 状态携带另一套执行语义。"""

    change_fields = (
        state.contract_revision,
        state.approved_contract_digest,
        state.execution_plan_revision,
        state.accepted_checkpoint_sha,
        state.active_candidate_sha,
    )
    if state.run_kind != "change":
        if any(value is not None for value in change_fields):
            raise ValueError("legacy Agent State 不能携带 ChangeRun 字段")
        return
    if state.accepted_checkpoint_sha is None:
        raise ValueError("ChangeRun State 缺少 Accepted Checkpoint")
    if state.contract_revision is None or state.execution_plan_revision is None:
        if (
            state.contract_revision is not None
            or state.execution_plan_revision is not None
            or state.phase not in {"planning", "needs_human", "stopped"}
            or state.approved_contract_digest is not None
            or state.active_candidate_sha is not None
        ):
            raise ValueError("未编译的 Planning ChangeRun 携带了不可执行合同状态")
        return
    if (
        state.phase not in {"planning", "awaiting_approval"}
        and state.approved_contract_digest is None
    ):
        raise ValueError("已启动的 ChangeRun 缺少 Approved Contract digest")
