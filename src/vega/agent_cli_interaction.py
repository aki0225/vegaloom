from __future__ import annotations

import time
import json
import os
import select
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .provider_session import (
    PendingInteraction,
    ProviderSessionState,
    load_provider_sessions,
    respond_to_interaction,
)
from .codex_approval_context import read_approval_context


PumpStatus = Literal["idle", "waiting", "attention"]

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
    """在主线程轮询原生请求；只有独立临时上下文允许知情响应。"""

    def __init__(self, run_dir: Path, *, prompt: TerminalApprovalPrompt | None = None) -> None:
        self.run_dir = run_dir.resolve()
        self.prompt = prompt

    def close(self) -> None:
        if self.prompt is not None:
            self.prompt.close()

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
            self.close()
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
        if self.prompt is not None and interaction.method in {_COMMAND_APPROVAL, _FILE_APPROVAL}:
            try:
                return self._respond_inline(interaction)
            except (OSError, ValueError):
                self.close()
        return _attention(
            interaction,
            reason_code="provider.interaction_requires_advanced_response",
            message=(
                f"{eligibility_error} "
                "Vega 将停止当前 attempt；停止后请使用 status、explain、"
                "recover 或 takeover 对账，确认后创建新 attempt。"
            ),
        )

    def _respond_inline(self, interaction: PendingInteraction) -> InteractionPumpUpdate:
        assert self.prompt is not None
        context = read_approval_context(self.run_dir, interaction)
        decision = self.prompt.poll_approval(interaction, context)
        message = "等待本次执行许可；默认拒绝，可随时停止。"
        if decision is not None:
            if decision not in {"accept", "decline"}:
                raise ValueError("仅允许本次 accept/decline")
            read_approval_context(self.run_dir, interaction)
            respond_to_interaction(
                self.run_dir, interaction.interaction_id, {"decision": decision},
                expected=interaction, expected_provider="codex",
            )
            self.close()
            message = "已提交本次响应，等待 Provider 继续。"
        return InteractionPumpUpdate(
            status="waiting", interaction_id=interaction.interaction_id, message=message,
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
        # 摘要不能替代独立临时上下文，也不能证明未支持的权限增量。
        return (
            "当前通路缺少本机可交互的完整目标，或包含未支持的网络、额外权限、策略增量；"
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


class TerminalApprovalPrompt:
    """临时原文只写本机 stderr TTY；主线程非阻塞读键，不创建 stdin 线程。"""

    def __init__(self, reporter: Callable[[InteractionPumpUpdate], None]) -> None:
        self.reporter = reporter
        self.shown: tuple[str, str | None] | None = None
        self.buffer = ""
        self.extended_key = False
        self.overflow = False
        self.last_update: InteractionPumpUpdate | None = None

    def __call__(self, update: InteractionPumpUpdate) -> None:
        if update != self.last_update:
            self.reporter(update)
            self.last_update = update

    def poll_approval(self, interaction: PendingInteraction, context: dict) -> str | None:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValueError("知情授权只支持本机交互终端")
        identity = (interaction.interaction_id, interaction.context_digest)
        if self.shown != identity:
            self.close()
            self._discard_input()
            self.shown = identity
            text = json.dumps(context, ensure_ascii=False, indent=2)
            visible = "".join(char if char.isprintable() or char == "\n" else f"\\u{ord(char):04x}" for char in text)
            sys.stderr.write(
                "\n[本次原生请求原文；不改变任务合同；不写入持久审计]\n"
                + visible + "\n仅允许本次执行？[y/N]（Enter 拒绝，Ctrl+C 停止） "
            )
            sys.stderr.flush()
            return None
        chars = self._read_available()
        if chars is None:
            return None
        if chars == "":
            return "decline"
        for char in chars:
            if char == "\x03":
                raise KeyboardInterrupt
            if char in "\r\n":
                sys.stderr.write("\n")
                sys.stderr.flush()
                return "accept" if not self.overflow and self.buffer.lower() == "y" else "decline"
            if char in "\b\x7f":
                self.buffer = self.buffer[:-1]
            else:
                self.overflow = self.overflow or len(self.buffer) >= 256
                if not self.overflow:
                    self.buffer += char
        return None

    def _read_available(self) -> str | None:
        if os.name == "nt":
            import msvcrt

            if not msvcrt.kbhit():
                return None
            char = msvcrt.getwch()
            if self.extended_key or char in {"\x00", "\xe0"}:
                self.extended_key = not self.extended_key
                return None
            return char
        descriptor = sys.stdin.fileno()
        if not select.select([descriptor], [], [], 0)[0]:
            return None
        return os.read(descriptor, 256).decode("utf-8", errors="replace")

    def _discard_input(self) -> None:
        if os.name == "nt":
            import msvcrt

            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios

            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)

    def close(self) -> None:
        self.shown = None
        self.buffer = ""
        self.extended_key = False
        self.overflow = False
