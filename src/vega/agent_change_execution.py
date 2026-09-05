from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .agent_cli_interaction import InteractionPumpUpdate, ProviderInteractionPump
from .agent_change_presentation import redact_change_message
from .agent_provider import AgentProvider
from .agent_recovery import SupervisorAgentRecovery
from .agent_run import AgentRun
from .agent_runtime_support import load_agent_bundle
from .provider_session import close_pending_interactions
from .project_config_provider import provider_cli_available
from .run_utils import resolve_run_dir


InteractionReporter = Callable[[InteractionPumpUpdate], None]
EventReporter = Callable[[str], None]


@dataclass(frozen=True)
class ProviderOperationBoundary:
    run: AgentRun
    update: InteractionPumpUpdate
    stop_unconfirmed: bool = False


def ensure_change_provider_ready(provider: AgentProvider) -> None:
    if provider_cli_available(provider):
        return
    label = "Claude Code CLI" if provider == "claude" else "Codex CLI"
    raise ValueError(f"当前 PATH 中未找到 {label}；请先安装并登录。")


def run_provider_operation(
    workspace: Path,
    current: AgentRun,
    provider: AgentProvider,
    operation: Callable[[], AgentRun],
    *,
    interaction_reporter: InteractionReporter | None,
    event_reporter: EventReporter | None,
) -> AgentRun | ProviderOperationBoundary:
    """运行 Provider；Codex 等待授权时只展示边界并停止当前 attempt。"""

    if provider == "claude":
        return operation()
    return _run_codex_operation(
        workspace,
        current,
        operation,
        interaction_reporter=interaction_reporter,
        event_reporter=event_reporter,
    )


def _run_codex_operation(
    workspace: Path,
    current: AgentRun,
    operation: Callable[[], AgentRun],
    *,
    interaction_reporter: InteractionReporter | None,
    event_reporter: EventReporter | None,
) -> AgentRun | ProviderOperationBoundary:
    results: queue.SimpleQueue[
        tuple[AgentRun | None, BaseException | None]
    ] = queue.SimpleQueue()
    _start_operation_thread(current, operation, results)
    pump = ProviderInteractionPump(
        current.run_dir,
    )
    boundary: InteractionPumpUpdate | None = None
    stop_deadline: float | None = None
    try:
        while True:
            completed = _completed_operation(results)
            if completed is not None:
                result, error = completed
                if boundary is not None:
                    return _boundary(workspace, current, boundary)
                if error is not None:
                    raise error
                assert result is not None
                return result
            if boundary is None:
                update = _redacted_update(pump.poll())
                _report_interaction(update, interaction_reporter)
                if update.status == "attention":
                    boundary = update
                    stop_confirmed = _request_stop(
                        workspace,
                        current.run_dir.name,
                        update.message or "Provider 交互需要人工处理",
                        event_reporter,
                        interaction_id=update.interaction_id,
                    )
                    if not stop_confirmed:
                        boundary = replace(
                            boundary,
                            message=(
                                f"{boundary.message or 'Provider 交互需要人工处理'} "
                                "停止请求未确认。"
                            ),
                        )
                    stop_deadline = time.monotonic() + 15
            elif stop_deadline is not None and time.monotonic() >= stop_deadline:
                return _boundary(
                    workspace,
                    current,
                    boundary,
                    stop_unconfirmed=True,
                )
            time.sleep(0.05)
    except KeyboardInterrupt:
        _request_stop(
            workspace,
            current.run_dir.name,
            "用户中断 `vega change`，请求停止当前 Provider execution",
            event_reporter,
        )
        raise


def _start_operation_thread(
    current: AgentRun,
    operation: Callable[[], AgentRun],
    results: queue.SimpleQueue[
        tuple[AgentRun | None, BaseException | None]
    ],
) -> None:
    def invoke() -> None:
        try:
            results.put((operation(), None))
        except BaseException as exc:  # noqa: BLE001 - 回传到主线程统一处理
            results.put((None, exc))

    threading.Thread(
        target=invoke,
        name=f"vega-change-{current.run_dir.name}",
        daemon=True,
    ).start()


def _completed_operation(
    results: queue.SimpleQueue[
        tuple[AgentRun | None, BaseException | None]
    ],
) -> tuple[AgentRun | None, BaseException | None] | None:
    try:
        return results.get_nowait()
    except queue.Empty:
        return None


def _request_stop(
    workspace: Path,
    run: str,
    reason: str,
    event_reporter: EventReporter | None,
    *,
    interaction_id: str | None = None,
) -> bool:
    safe_reason = redact_change_message(reason)
    stop_confirmed = True
    try:
        SupervisorAgentRecovery(workspace).stop(run, reason=safe_reason)
    except (FileNotFoundError, OSError, ValueError) as exc:
        stop_confirmed = False
        if event_reporter is not None:
            event_reporter(redact_change_message(f"停止请求未确认：{exc}"))
    try:
        close_pending_interactions(
            resolve_run_dir(workspace, run),
            interaction_id=interaction_id,
        )
    except (OSError, ValueError) as exc:
        stop_confirmed = False
        if event_reporter is not None:
            event_reporter(redact_change_message(f"待响应请求关闭未确认：{exc}"))
    return stop_confirmed


def _boundary(
    workspace: Path,
    current: AgentRun,
    update: InteractionPumpUpdate,
    *,
    stop_unconfirmed: bool = False,
) -> ProviderOperationBoundary:
    run_dir, state, plan, _ = load_agent_bundle(
        workspace,
        current.run_dir.name,
    )
    return ProviderOperationBoundary(
        run=AgentRun(run_dir=run_dir, state=state, plan=plan),
        update=_redacted_update(update),
        stop_unconfirmed=stop_unconfirmed,
    )


def _report_interaction(
    update: InteractionPumpUpdate,
    reporter: InteractionReporter | None,
) -> None:
    if reporter is not None and update.status != "idle":
        reporter(_redacted_update(update))


def _redacted_update(update: InteractionPumpUpdate) -> InteractionPumpUpdate:
    return replace(
        update,
        summary=_redact_optional(update.summary),
        message=_redact_optional(update.message),
    )


def _redact_optional(value: str | None) -> str | None:
    return redact_change_message(value) if value is not None else None
