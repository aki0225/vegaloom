from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

import typer

from .agent_change_driver import AgentChangeDriver, ChangeDriverResult
from .agent_change_presentation import redact_change_message
from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_cli_interaction import InteractionPumpUpdate
from .agent_recovery import SupervisorAgentRecovery
from .agent_recovery_request import AgentRecoveryRequest
from .agent_provider import resolve_run_provider
from .agent_run_selection import resolve_repository_root
from .agent_runtime import SupervisorAgentRuntime
from .agent_side_effect_adjudication import SupervisorAgentSideEffectAdjudicator
from .agent_verification_retry import SupervisorAgentVerificationRetry
from .cli_support import report_execution_progress
from .redaction import redact_text
from .run_utils import resolve_run_dir


def agent_change(
    text: str | None = typer.Argument(
        None,
        help="自然语言变更目标；省略时继续当前仓库唯一未完成 ChangeRun。",
    ),
    run: str | None = typer.Option(
        None,
        "--run",
        help="显式继续指定 ChangeRun。",
    ),
    task: Path | None = typer.Option(
        None,
        "--task",
        help="从指定 Git Task Card 恢复。",
    ),
    provider: Literal["codex", "claude"] | None = typer.Option(
        None,
        "--provider",
        help="Coding Agent Provider；已有 Run 默认沿用原 Provider。",
    ),
    approval: Literal["human", "bounded"] = typer.Option(
        "human",
        "--approval",
        help="human 在当前终端确认；bounded 还要求仓库策略显式放行。",
    ),
    timeout_seconds: int = typer.Option(
        900,
        "--timeout",
        min=60,
        max=3600,
        help="单次 Planning、Worker 或 Reviewer 外部进程超时秒数。",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="只输出一个稳定 JSON object，不读取 stdin。",
    ),
) -> None:
    """创建或继续一个日常代码变更，直到完成或遇到授权边界。"""

    try:
        repo = resolve_repository_root(Path.cwd())
        interactive = not json_output and _stream_is_tty(sys.stdin)
        driver = AgentChangeDriver(
            repo,
            repo,
            provider=provider,
            approval=approval,
            timeout_seconds=timeout_seconds,
            interactive=interactive,
            json_output=json_output,
            confirm=_confirm if interactive else None,
            input_stream=sys.stdin,
            event_reporter=(
                None
                if json_output
                else lambda message: typer.echo(
                    f"[vega] {message}",
                    err=True,
                )
            ),
            interaction_reporter=(
                None if json_output else _render_interaction_update
            ),
            progress_reporter=(
                None if json_output else report_execution_progress
            ),
        )
        result = driver.change(text=text, run=run, task=task)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": None,
                        "phase": None,
                        "outcome": "error",
                        "reason_code": "change.request_failed",
                        "message": redact_change_message(str(exc)),
                        "safe_actions": [],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            typer.echo(f"错误：{redact_change_message(str(exc))}", err=True)
        raise typer.Exit(code=1) from exc

    _render_change_result(repo, result, json_output=json_output)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


def agent_replan(
    run: str = typer.Option(..., "--run", help="ChangeRun run_id 或 runs/<run_id>。"),
    contract_path: Path = typer.Option(
        ...,
        "--contract",
        help="提议的 Change Contract JSON。",
    ),
    execution_plan_path: Path = typer.Option(
        ...,
        "--execution-plan",
        help="提议的 Execution Plan JSON。",
    ),
) -> None:
    """按合同字段、真实 Diff 和风险路径裁决 ChangeRun revision。"""

    try:
        result = SupervisorAgentRuntime(Path.cwd()).revise_change(
            run,
            proposed_contract=_load_change_contract(contract_path),
            proposed_execution_plan=_load_execution_plan(execution_plan_path),
        )
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if result.state.phase == "awaiting_approval":
        typer.echo("Contract revision 已写入，等待人工批准。")
    elif result.state.phase == "needs_human":
        typer.echo("Revision 触及合同、风险或预算边界，已停止自动执行。")
    else:
        typer.echo("Execution Plan revision 已在原合同内采用。")
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def agent_retry(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="默认沿用当前 ChangeRun 的 Coding Agent Provider。",
    ),
) -> None:
    """保留当前 Diff，只重跑验证、风险门禁和独立 Reviewer。"""

    try:
        workspace = Path.cwd()
        selected_provider = resolve_run_provider(
            resolve_run_dir(workspace, run),
            provider,
        )
        result = SupervisorAgentVerificationRetry(
            workspace,
            progress_reporter=report_execution_progress,
            event_reporter=lambda message: typer.echo(
                f"[vega] {message}",
                err=True,
            ),
            provider=selected_provider,
        ).run(run)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def agent_recover(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    input_path: Path = typer.Option(
        ...,
        "--input",
        help="结构化 Recovery Request JSON。",
    ),
) -> None:
    """Worker 失去可信终态后，对账进程、Workspace 和副作用。"""

    try:
        request = AgentRecoveryRequest.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = SupervisorAgentRecovery(Path.cwd()).recover(run, request)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Worker 现场已重新对账。")
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def agent_adjudicate(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    input_path: Path = typer.Option(
        ...,
        "--input",
        help="结构化 Recovery Request JSON。",
    ),
) -> None:
    """记录人工核对后的外部副作用结论。"""

    try:
        request = AgentRecoveryRequest.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = SupervisorAgentSideEffectAdjudicator(Path.cwd()).adjudicate(
            run,
            request,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(redact_text(str(exc))) from exc
    message = (
        "外部副作用已确认不存在，可以准备 Handoff。"
        if result.state.phase == "stopped"
        else "外部副作用已确认为 known，任务继续等待人工处理。"
    )
    typer.echo(message)
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def _load_change_contract(path: Path) -> ChangeContract:
    try:
        return ChangeContract.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"无法读取 Change Contract：{path.name}") from exc


def _load_execution_plan(path: Path) -> ExecutionPlan:
    try:
        return ExecutionPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"无法读取 Execution Plan：{path.name}") from exc


def _render_change_result(
    workspace: Path,
    result: ChangeDriverResult,
    *,
    json_output: bool,
) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                result.as_payload(),
                ensure_ascii=False,
            )
        )
        return
    typer.echo(result.message)
    if result.run is None:
        return
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(workspace).status(result.run.run_dir.name))


def _confirm(prompt: str) -> bool:
    return typer.confirm(prompt, default=False)


def _render_interaction_update(update: InteractionPumpUpdate) -> None:
    if update.prompt is not None:
        typer.echo(update.prompt, nl=False, err=True)
    if update.message is not None:
        typer.echo(f"[vega] {update.message}", err=True)


def _stream_is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
