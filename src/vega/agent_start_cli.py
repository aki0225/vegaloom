from __future__ import annotations

from pathlib import Path

import typer

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_runtime import SupervisorAgentRuntime
from .cli_support import require_repo_directory


def agent_start(
    repo: Path = typer.Option(..., "--repo", help="目标 Git 仓库根目录。"),
    text: str | None = typer.Option(None, "--text", help="自然语言任务目标。"),
    contract_path: Path | None = typer.Option(
        None, "--contract", help="Change Contract JSON。"
    ),
    execution_plan_path: Path | None = typer.Option(
        None, "--execution-plan", help="Execution Plan JSON。"
    ),
) -> None:
    """从自然语言调查或显式合同创建同一条 ChangeRun。"""

    repo = require_repo_directory(repo)
    try:
        runtime = SupervisorAgentRuntime(Path.cwd())
        if text is not None:
            if contract_path is not None or execution_plan_path is not None:
                raise ValueError("--text 不能与 --contract 或 --execution-plan 同时使用")
            result = runtime.start_planning(repo, goal=text)
            message = "Planning ChangeRun 已创建"
        else:
            if contract_path is None or execution_plan_path is None:
                raise ValueError(
                    "必须提供 --text，或同时提供 --contract 与 --execution-plan"
                )
            result = runtime.start_change(
                repo,
                contract=_load_change_contract(contract_path),
                execution_plan=_load_execution_plan(execution_plan_path),
            )
            message = "ChangeRun 已创建"
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{message}：{result.run_dir.name}")
    typer.echo("")
    typer.echo(runtime.status(result.run_dir.name))


def _load_change_contract(path: Path) -> ChangeContract:
    try:
        return ChangeContract.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取 Change Contract：{path.name}") from exc


def _load_execution_plan(path: Path) -> ExecutionPlan:
    try:
        return ExecutionPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取 Execution Plan：{path.name}") from exc
