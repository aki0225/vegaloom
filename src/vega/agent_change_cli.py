from __future__ import annotations

from pathlib import Path

import typer

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_recovery import SupervisorAgentRecovery
from .agent_recovery_request import AgentRecoveryRequest
from .agent_runtime import SupervisorAgentRuntime
from .agent_side_effect_adjudication import SupervisorAgentSideEffectAdjudicator
from .agent_verification_retry import SupervisorAgentVerificationRetry
from .cli_support import report_execution_progress
from .redaction import redact_text


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
) -> None:
    """保留当前 Diff，只重跑验证、风险门禁和独立 Reviewer。"""

    try:
        result = SupervisorAgentVerificationRetry(
            Path.cwd(),
            progress_reporter=report_execution_progress,
            event_reporter=lambda message: typer.echo(
                f"[vega] {message}",
                err=True,
            ),
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
