from __future__ import annotations

import json
from pathlib import Path

import typer

from .agent_contract import AgentObservation, AgentPlan
from .agent_graph import langgraph_available
from .agent_runtime import SupervisorAgentRuntime
from .cli_support import load_brief_input, require_repo_directory


agent_app = typer.Typer(help="运行轻量 Supervisor Agent 控制层。")


@agent_app.command("start")
def agent_start(
    repo: Path = typer.Option(..., "--repo", help="目标 Git 仓库根目录。"),
    input_path: Path | None = typer.Option(None, "--input", help="任务或 Agent Plan 文件。"),
    text: str | None = typer.Option(None, "--text", help="用户目标。"),
    plan_path: Path | None = typer.Option(None, "--plan", help="可选的结构化 Agent Plan JSON。"),
) -> None:
    """创建 Agent run，捕获 Workspace，并等待人工批准 Plan。"""

    repo = require_repo_directory(repo)
    goal, _ = load_brief_input(input_path, text)
    plan = _load_plan(plan_path) if plan_path else None
    try:
        result = _runtime().start(repo, goal=goal.strip(), plan=plan)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Agent 已创建：{result.run_dir.name}")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("approve")
def agent_approve(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    actor: str = typer.Option("human", "--actor", help="批准人标识。"),
) -> None:
    """批准当前 Plan revision，并生成 Task Brief 与启动前 Checkpoint。"""

    try:
        result = _runtime().approve(run, actor=actor)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Plan 已批准。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("plan")
def agent_plan(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    input_path: Path = typer.Option(..., "--input", help="主会话调查后生成的 Agent Plan JSON。"),
) -> None:
    """写入新的未批准 Plan revision，仍需人工显式 approve。"""

    try:
        draft = AgentPlan.model_validate_json(input_path.read_text(encoding="utf-8"))
        result = _runtime().update_plan(run, draft)
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Plan revision {result.plan.plan_revision} 已写入，等待人工批准。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("dispatch")
def agent_dispatch(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    child_run: str = typer.Option(..., "--child", help="本次 Worker attempt 身份。"),
    operation_id: str = typer.Option(..., "--operation", help="本次写入 operation 身份。"),
) -> None:
    """绑定唯一 Writer；Gate 1 由 Fake Worker 或宿主负责实际执行。"""

    try:
        result = _runtime().start_work_item(
            run,
            child_run=child_run,
            operation_id=operation_id,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Worker 已绑定。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("observe")
def agent_observe(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    input_path: Path = typer.Option(..., "--input", help="结构化 Observation JSON。"),
) -> None:
    """对账真实 Workspace，并把 Observation 路由为下一动作。"""

    try:
        observation = AgentObservation.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = _runtime().observe(run, observation)
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Observation 已对账。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("status")
def agent_status(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    json_output: bool = typer.Option(False, "--json", help="输出结构化状态文件。"),
) -> None:
    """显示主会话状态卡。"""

    try:
        if json_output:
            typer.echo(_runtime().state_path(run).read_text(encoding="utf-8"))
        else:
            typer.echo(_runtime().status(run))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@agent_app.command("steer")
def agent_steer(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    instruction: str = typer.Option(..., "--instruction", help="新增或修改的人工约束。"),
) -> None:
    """使旧 Plan 批准失效，并把新约束写入下一 revision。"""

    try:
        result = _runtime().steer(run, instruction=instruction)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("人工约束已记录，旧 Plan 批准已失效。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("resume")
def agent_resume(
    repo: Path = typer.Option(..., "--repo", help="目标 Git 仓库根目录。"),
    task: Path | None = typer.Option(None, "--task", help="显式 Task Card 路径。"),
) -> None:
    """从当前分支的 Git Task Card 创建新的本机 Agent run。"""

    repo = require_repo_directory(repo)
    try:
        result = _runtime().resume_task_card(repo, task)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Agent 已从 Task Card 恢复：{result.run_dir.name}")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("capabilities")
def agent_capabilities() -> None:
    """显示当前环境能否运行 LangGraph 图游标。"""

    typer.echo(
        json.dumps(
            {
                "schema_version": 1,
                "supervisor_runtime": True,
                "langgraph": langgraph_available(),
                "worker": "fake-or-host",
                "finish_owned_by_core": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _runtime() -> SupervisorAgentRuntime:
    return SupervisorAgentRuntime(Path.cwd())


def _load_plan(path: Path) -> AgentPlan:
    try:
        return AgentPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"无法读取 Agent Plan：{path.name}") from exc
