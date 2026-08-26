from __future__ import annotations

from pathlib import Path

import typer

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_runtime import SupervisorAgentRuntime


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
