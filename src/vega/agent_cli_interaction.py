from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .provider_session import (
    PendingInteraction,
    ProviderSessionState,
    load_provider_sessions,
)


PumpStatus = Literal["idle", "attention"]

_COMMAND_APPROVAL = "item/commandExecution/requestApproval"
_FILE_APPROVAL = "item/fileChange/requestApproval"
_ADVANCED_METHODS = {
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
}


@dataclass(frozen=True)
class InteractionPumpUpdate:
    """供 CLI 渲染的低频交互状态，不包含原始 Provider 参数。"""

    status: PumpStatus
    interaction_id: str | None = None
    summary: str | None = None
    message: str | None = None
    reason_code: str | None = None


class ProviderInteractionPump:
    """只检测 Provider 请求，不在简化终端读取输入或代发响应。"""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()

    def poll(self) -> InteractionPumpUpdate:
        """执行一次非阻塞状态轮询；调用方可与 Provider future 并行等待。"""

        try:
            state = _load_current_state(self.run_dir)
        except ValueError as exc:
            return _attention(
                reason_code="provider.session_state_invalid",
                message=str(exc),
            )

        pending = [
            item
            for item in state.interactions
            if item.status == "pending"
        ]
        if not pending:
            return InteractionPumpUpdate(status="idle")
        if len(pending) != 1:
            return _attention(
                reason_code="provider.multiple_pending_interactions",
                message="当前存在多个待响应请求，简化交互拒绝猜测处理顺序。",
            )

        interaction = pending[0]
        binding_error = _binding_error(state, interaction)
        if binding_error is not None:
            return _attention(
                interaction,
                reason_code="provider.interaction_binding_invalid",
                message=binding_error,
            )
        eligibility_error = _inline_eligibility_error(interaction)
        return _attention(
            interaction,
            reason_code="provider.interaction_requires_advanced_response",
            message=(
                f"{eligibility_error} "
                "Vega 将停止当前 attempt；停止后请使用 status、explain、"
                "recover 或 takeover 对账，确认后创建新 attempt。"
            ),
        )


def _binding_error(
    state: ProviderSessionState,
    interaction: PendingInteraction,
) -> str | None:
    handle = state.handles.get(interaction.role_key)
    if handle is None:
        return "待响应请求没有对应的 Provider Session。"
    if handle.role != interaction.role_key:
        return "待响应请求角色与 Provider Session 不一致。"
    if handle.provider != "codex":
        return "当前 Provider 不支持 Vega 同终端响应，请使用原生会话处理。"
    if handle.owner != "vega":
        return "当前 Provider Session 已由人工接管。"
    if (
        handle.lifecycle != "waiting_user"
        or not handle.permissions_verified
        or not interaction.thread_id
        or not interaction.turn_id
        or handle.thread_id != interaction.thread_id
        or handle.last_turn_id != interaction.turn_id
    ):
        return "待响应请求不再绑定当前活动 Provider Turn。"
    return None


def _inline_eligibility_error(
    interaction: PendingInteraction,
) -> str | None:
    if interaction.method in {_COMMAND_APPROVAL, _FILE_APPROVAL}:
        # Provider Session 只保存脱敏摘要，无法重新证明 cwd、目标路径、
        # 网络上下文或策略增量。基于 friendly display 标签批准会把展示提示
        # 错当成权限事实，因此简化终端只能提示接管，不能内联 accept。
        return (
            "简化状态没有保存知情授权所需的完整目标与权限上下文；"
            "请接管原生会话核对请求，Vega 不会仅凭脱敏摘要批准。"
        )
    if interaction.method in _ADVANCED_METHODS:
        return "权限、工具输入和 MCP 请求需要结构化或敏感响应。"
    return "当前 Provider 请求类型不受简化交互支持，请接管原生会话处理。"


def _attention(
    interaction: PendingInteraction | None = None,
    *,
    reason_code: str,
    message: str,
) -> InteractionPumpUpdate:
    return InteractionPumpUpdate(
        status="attention",
        interaction_id=(
            interaction.interaction_id if interaction is not None else None
        ),
        summary=interaction.summary if interaction is not None else None,
        message=message,
        reason_code=reason_code,
    )


def _load_current_state(run_dir: Path) -> ProviderSessionState:
    last_error: ValueError | None = None
    for attempt in range(5):
        try:
            return load_provider_sessions(run_dir)
        except ValueError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.02)
    assert last_error is not None
    raise last_error
