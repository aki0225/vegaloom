from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_contract_compiler import PLAN_CARD_ARTIFACT
from .agent_codex_adapter import SupervisorAgentCodexAdapter
from .agent_planning import PLANNING_PROPOSAL_ARTIFACT
from .agent_planning_runtime import PlanningProposalRunner
from .agent_run import AgentRun
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_support import load_agent_bundle
from .cli_support import (
    ensure_runner_ready,
    report_execution_progress,
    require_repo_directory,
)


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


def agent_run(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    timeout_seconds: int = typer.Option(
        900,
        "--timeout",
        min=60,
        max=3600,
        help="单次 Planning、Worker 或 Reviewer 外部进程超时秒数。",
    ),
    fresh_session: bool = typer.Option(
        False,
        "--fresh-session",
        help="显式改用短生命周期 codex exec；默认复用 App Server Thread。",
    ),
    approval: Literal["human", "bounded"] = typer.Option(
        "human",
        "--approval",
        help="批准模式；bounded 还要求仓库策略显式启用。",
    ),
) -> None:
    """执行当前 Planning 或 Work Item。"""

    try:
        workspace = Path.cwd()
        runtime = SupervisorAgentRuntime(workspace)
        result, should_run_provider = _advance_current_phase(
            workspace,
            runtime,
            run,
            timeout_seconds=timeout_seconds,
            fresh_session=fresh_session,
        )
        result, should_run_provider = _apply_requested_approval(
            runtime,
            result,
            approval=approval,
            should_run_provider=should_run_provider,
        )
        if should_run_provider:
            ensure_runner_ready("codex-exec", "Coding Agent")
            result = SupervisorAgentCodexAdapter(
                workspace,
                persistent_sessions=not fresh_session,
                progress_reporter=report_execution_progress,
                event_reporter=_event,
            ).run(run, timeout_seconds=timeout_seconds)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def _advance_current_phase(
    workspace: Path,
    runtime: SupervisorAgentRuntime,
    run: str,
    *,
    timeout_seconds: int,
    fresh_session: bool,
) -> tuple[AgentRun, bool]:
    run_dir, state, plan, _ = load_agent_bundle(workspace, run)
    if state.run_kind == "change" and state.contract_revision is None:
        result = _run_planning_phase(
            workspace,
            runtime,
            run,
            run_dir=run_dir,
            timeout_seconds=timeout_seconds,
            fresh_session=fresh_session,
        )
        return result, False
    if state.phase == "finalizing":
        return runtime.finalize(run), False
    return (
        AgentRun(run_dir=run_dir, state=state, plan=plan),
        state.phase != "awaiting_approval",
    )


def _run_planning_phase(
    workspace: Path,
    runtime: SupervisorAgentRuntime,
    run: str,
    *,
    run_dir: Path,
    timeout_seconds: int,
    fresh_session: bool,
) -> AgentRun:
    if not (run_dir / PLANNING_PROPOSAL_ARTIFACT).is_file():
        ensure_runner_ready("codex-exec", "Coding Agent")
    result = PlanningProposalRunner(
        workspace,
        persistent_session=not fresh_session,
        progress_reporter=report_execution_progress,
        event_reporter=_event,
    ).run(run, timeout_seconds=timeout_seconds)
    if (
        result.state.phase == "planning"
        and (result.run_dir / PLANNING_PROPOSAL_ARTIFACT).is_file()
    ):
        result = runtime.compile_planning(result.run_dir.name)
        if (result.run_dir / PLAN_CARD_ARTIFACT).is_file():
            _event("Contract Compiler 已生成未批准合同")
    return result


def _apply_requested_approval(
    runtime: SupervisorAgentRuntime,
    result: AgentRun,
    *,
    approval: Literal["human", "bounded"],
    should_run_provider: bool,
) -> tuple[AgentRun, bool]:
    if result.state.phase != "awaiting_approval":
        return result, should_run_provider
    if approval == "human":
        _event("当前 Contract 等待人工批准")
        return result, False
    approved = runtime.approve_bounded(result.run_dir.name)
    if approved.state.phase != "ready":
        _event("bounded 策略未放行，仍等待人工批准")
        return approved, False
    _event("bounded 策略已批准当前 Contract")
    return approved, True


def _event(message: str) -> None:
    typer.echo(f"[vega] {message}", err=True)


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
