from __future__ import annotations

import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .agent_cli_interaction import InteractionPumpUpdate, ProviderInteractionPump
from .agent_provider import AgentProvider
from .agent_recovery import SupervisorAgentRecovery
from .agent_run import AgentRun
from .agent_runtime_support import load_agent_bundle


InteractionReporter = Callable[[InteractionPumpUpdate], None]
EventReporter = Callable[[str], None]


@dataclass(frozen=True)
class ProviderOperationBoundary:
    run: AgentRun
    update: InteractionPumpUpdate
    stop_unconfirmed: bool = False


def ensure_change_provider_ready(provider: AgentProvider) -> None:
    executable = "claude" if provider == "claude" else "codex"
    if shutil.which(executable) or (
        executable == "claude" and shutil.which("claude.cmd")
    ):
        return
    label = "Claude Code CLI" if provider == "claude" else "Codex CLI"
    raise ValueError(f"当前 PATH 中未找到 {label}；请先安装并登录。")


def run_provider_operation(
    workspace: Path,
    current: AgentRun,
    provider: AgentProvider,
    operation: Callable[[], AgentRun],
    *,
    interactive: bool,
    json_output: bool,
    input_stream: TextIO | None,
    interaction_reporter: InteractionReporter | None,
    event_reporter: EventReporter | None,
) -> AgentRun | ProviderOperationBoundary:
    """运行 Provider，并在 Codex 等待授权时由当前终端接管展示。"""

    if provider == "claude":
        return operation()
    return _run_codex_operation(
        workspace,
        current,
        operation,
        interactive=interactive,
        json_output=json_output,
        input_stream=input_stream,
        interaction_reporter=interaction_reporter,
        event_reporter=event_reporter,
    )


def _run_codex_operation(
    workspace: Path,
    current: AgentRun,
    operation: Callable[[], AgentRun],
    *,
    interactive: bool,
    json_output: bool,
    input_stream: TextIO | None,
    interaction_reporter: InteractionReporter | None,
    event_reporter: EventReporter | None,
) -> AgentRun | ProviderOperationBoundary:
    results: queue.SimpleQueue[
        tuple[AgentRun | None, BaseException | None]
    ] = queue.SimpleQueue()
    _start_operation_thread(current, operation, results)
    pump = ProviderInteractionPump(
        current.run_dir,
        input_stream=input_stream,
        interactive=interactive,
        json_output=json_output,
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
                update = pump.poll()
                _report_interaction(update, interaction_reporter)
                if update.status == "attention":
                    boundary = update
                    _request_stop(
                        workspace,
                        current.run_dir.name,
                        update.message or "Provider 交互需要人工处理",
                        event_reporter,
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
) -> None:
    try:
        SupervisorAgentRecovery(workspace).stop(run, reason=reason)
    except (FileNotFoundError, OSError, ValueError) as exc:
        if event_reporter is not None:
            event_reporter(f"停止请求未确认：{exc}")


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
        update=update,
        stop_unconfirmed=stop_unconfirmed,
    )


def _report_interaction(
    update: InteractionPumpUpdate,
    reporter: InteractionReporter | None,
) -> None:
    if reporter is not None and update.status != "idle":
        reporter(update)
