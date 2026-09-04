from __future__ import annotations

import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO

from .provider_session import (
    PendingInteraction,
    ProviderSessionState,
    load_provider_sessions,
    respond_to_interaction,
)


PumpStatus = Literal["idle", "prompting", "responded", "attention"]
InlineDecision = Literal["accept", "decline"]

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
    prompt: str | None = None
    message: str | None = None
    reason_code: str | None = None
    decision: InlineDecision | None = None


@dataclass
class _PendingRead:
    interaction: PendingInteraction
    result: queue.SimpleQueue[tuple[str | None, BaseException | None]]


class ProviderInteractionPump:
    """从 Provider Session 读取审批请求，并在当前终端安全响应。"""

    def __init__(
        self,
        run_dir: Path,
        *,
        input_stream: TextIO | None = None,
        interactive: bool = True,
        json_output: bool = False,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.input_stream = input_stream or sys.stdin
        self.interactive = (
            interactive
            and not json_output
            and _stream_is_tty(self.input_stream)
        )
        self._pending_read: _PendingRead | None = None

    def poll(self) -> InteractionPumpUpdate:
        """执行一次无阻塞轮询；调用方可与 Provider future 放在同一循环。"""

        try:
            state = _load_current_state(self.run_dir)
        except ValueError as exc:
            return _attention(
                reason_code="provider.session_state_invalid",
                message=str(exc),
            )

        if self._pending_read is not None:
            return self._poll_pending_read(state)

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
        if eligibility_error is not None:
            return _attention(
                interaction,
                reason_code="provider.interaction_requires_advanced_response",
                message=(
                    f"{eligibility_error} "
                    f"{_advanced_guidance(self.run_dir.name, interaction)}"
                ),
            )
        if not self.interactive:
            return _attention(
                interaction,
                reason_code="provider.interaction_requires_tty",
                message=(
                    "当前请求需要人工授权；JSON 或非交互终端不会读取 stdin。"
                    f"请使用 `vega respond --run {self.run_dir.name} "
                    f"--interaction {interaction.interaction_id}` 处理。"
                ),
            )

        self._start_read(interaction)
        return InteractionPumpUpdate(
            status="prompting",
            interaction_id=interaction.interaction_id,
            summary=interaction.summary,
            prompt=_approval_prompt(interaction),
        )

    def _start_read(self, interaction: PendingInteraction) -> None:
        result: queue.SimpleQueue[tuple[str | None, BaseException | None]] = (
            queue.SimpleQueue()
        )
        pending = _PendingRead(
            interaction=interaction.model_copy(deep=True),
            result=result,
        )
        self._pending_read = pending

        def read_line() -> None:
            try:
                result.put((self.input_stream.readline(), None))
            except Exception as exc:  # noqa: BLE001 - 输入失败必须转为拒绝
                result.put((None, exc))

        threading.Thread(
            target=read_line,
            name="vega-provider-interaction",
            daemon=True,
        ).start()

    def _poll_pending_read(
        self,
        state: ProviderSessionState,
    ) -> InteractionPumpUpdate:
        assert self._pending_read is not None
        pending = self._pending_read
        current = _current_pending(state, pending.interaction.interaction_id)
        if current is None:
            return _attention(
                pending.interaction,
                reason_code="provider.interaction_changed",
                message="等待输入期间 Provider 请求已关闭或变化，未发送旧响应。",
            )
        if _interaction_key(current) != _interaction_key(pending.interaction):
            return _attention(
                pending.interaction,
                reason_code="provider.interaction_changed",
                message="等待输入期间 Provider 请求绑定已变化，未发送旧响应。",
            )
        try:
            line, input_error = pending.result.get_nowait()
        except queue.Empty:
            return InteractionPumpUpdate(
                status="prompting",
                interaction_id=current.interaction_id,
                summary=current.summary,
            )

        self._pending_read = None
        decision: InlineDecision = (
            "accept"
            if input_error is None
            and isinstance(line, str)
            and line.strip().lower() in {"y", "yes"}
            else "decline"
        )
        try:
            responded = respond_to_interaction(
                self.run_dir,
                current.interaction_id,
                {"decision": decision},
                expected=pending.interaction,
                expected_provider="codex",
            )
        except ValueError as exc:
            return _attention(
                current,
                reason_code="provider.interaction_changed",
                message=str(exc),
            )
        suffix = (
            "输入读取失败，已按默认 No 拒绝本次请求。"
            if input_error is not None
            else (
                "已批准本次请求。"
                if decision == "accept"
                else "已拒绝本次请求。"
            )
        )
        return InteractionPumpUpdate(
            status="responded",
            interaction_id=responded.interaction_id,
            summary=responded.summary,
            message=suffix,
            decision=decision,
        )


def _binding_error(
    state: ProviderSessionState,
    interaction: PendingInteraction,
) -> str | None:
    handle = state.handles.get(interaction.role_key)
    if handle is None:
        return "待响应请求没有对应的 Provider Session。"
    if handle.provider != "codex":
        return "当前 Provider 不支持 Vega 同终端响应，请使用原生会话处理。"
    if handle.owner != "vega":
        return "当前 Provider Session 已由人工接管。"
    if (
        handle.lifecycle != "waiting_user"
        or not handle.permissions_verified
        or not interaction.thread_id
        or handle.thread_id != interaction.thread_id
        or (
            interaction.turn_id is not None
            and handle.last_turn_id != interaction.turn_id
        )
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


def _approval_prompt(interaction: PendingInteraction) -> str:
    return (
        "\nVega 需要授权\n"
        f"角色：{interaction.role_key}\n"
        f"请求：{interaction.summary}\n"
        "批准本次操作？[y/N] "
    )


def _advanced_guidance(
    run_id: str,
    interaction: PendingInteraction,
) -> str:
    command = (
        f"`vega respond --run {run_id} "
        f"--interaction {interaction.interaction_id}"
    )
    if interaction.method in _ADVANCED_METHODS:
        return f"{command} --input <response.json>`。"
    if interaction.method in {_COMMAND_APPROVAL, _FILE_APPROVAL}:
        return (
            "先在原生会话核对完整请求，再使用 "
            f"{command} --decision accept|decline`。"
        )
    return "请接管原生会话处理。"


def _current_pending(
    state: ProviderSessionState,
    interaction_id: str,
) -> PendingInteraction | None:
    matches = [
        item
        for item in state.interactions
        if item.interaction_id == interaction_id and item.status == "pending"
    ]
    return matches[0] if len(matches) == 1 else None


def _interaction_key(
    interaction: PendingInteraction,
) -> tuple[str, str, str, str, str, str | None, str, str]:
    return (
        interaction.interaction_id,
        interaction.role_key,
        interaction.rpc_request_id,
        interaction.method,
        interaction.thread_id,
        interaction.turn_id,
        interaction.summary,
        interaction.created_at,
    )


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


def _stream_is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


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
