from __future__ import annotations

import json
from pathlib import Path

import typer

from .agent_codex_adapter import SupervisorAgentCodexAdapter
from .agent_change_cli import agent_replan
from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_contract import AgentObservation
from .agent_recovery import SupervisorAgentRecovery
from .agent_recovery_request import AgentRecoveryRequest
from .agent_runtime import SupervisorAgentRuntime
from .agent_side_effect_adjudication import SupervisorAgentSideEffectAdjudicator
from .agent_verification_retry import SupervisorAgentVerificationRetry
from .agent_worker import SupervisorAgentWorker
from .cli_support import (
    ensure_runner_ready,
    report_execution_progress,
    require_repo_directory,
)
from .redaction import redact_text
from .run_status import run_status_payload


agent_app = typer.Typer(help="运行轻量 Supervisor Agent 控制层。")
agent_app.command("replan")(agent_replan)


@agent_app.command("start")
def agent_start(
    repo: Path = typer.Option(..., "--repo", help="目标 Git 仓库根目录。"),
    contract_path: Path = typer.Option(
        ...,
        "--contract",
        help="Bounded Change Loop 的 Change Contract JSON。",
    ),
    execution_plan_path: Path = typer.Option(
        ...,
        "--execution-plan",
        help="Bounded Change Loop 的 Execution Plan JSON。",
    ),
) -> None:
    """创建 ChangeRun，捕获隔离 Workspace，并等待人工批准 Contract。"""

    repo = require_repo_directory(repo)
    try:
        result = _runtime().start_change(
            repo,
            contract=_load_change_contract(contract_path),
            execution_plan=_load_execution_plan(execution_plan_path),
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Agent 已创建：{result.run_dir.name}")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("approve")
def agent_approve(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    actor: str = typer.Option("human", "--actor", help="批准人标识。"),
) -> None:
    """批准当前 Contract/Plan revision，并生成 Task Brief 与启动前 Checkpoint。"""

    try:
        result = _runtime().approve(run, actor=actor)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Change Contract 已批准。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("dispatch", hidden=True)
def agent_dispatch(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    child_run: str = typer.Option(..., "--child", help="本次 Worker attempt 身份。"),
    operation_id: str = typer.Option(..., "--operation", help="本次写入 operation 身份。"),
) -> None:
    """绑定唯一 Writer，并保守进入 operation 可能已开始的边界。"""

    try:
        result = _worker().bind(
            run,
            child_run=child_run,
            operation_id=operation_id,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Worker 已绑定。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("run")
def agent_run(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    timeout_seconds: int = typer.Option(
        900,
        "--timeout",
        min=60,
        max=3600,
        help="真实 Codex Worker 超时秒数。",
    ),
) -> None:
    """执行当前已批准 Work Item，并复用现有 Core 完成验证与独立评审。"""

    try:
        ensure_runner_ready("codex-exec", "worker")
        result = _adapter().run(run, timeout_seconds=timeout_seconds)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("finalize")
def agent_finalize(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
) -> None:
    """从已验证的 Core Finish Artifact 恢复或重试 Supervisor 终态发布。"""

    try:
        result = _runtime().finalize(run)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Agent 终态已完成。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("retry-verification")
def agent_retry_verification(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
) -> None:
    """复用原 child 与 Diff，只重跑 Core 验证、风险门禁和独立 Reviewer。"""

    try:
        result = _verification_retry().run(run)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("recover")
def agent_recover(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    input_path: Path = typer.Option(..., "--input", help="结构化 Recovery Request JSON。"),
) -> None:
    """Worker 失去可信终态后，先对账真实现场再决定人工恢复路径。"""

    try:
        request = AgentRecoveryRequest.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = _recovery().recover(run, request)
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Worker 现场已重新对账。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("pause")
def agent_pause(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="暂停原因。"),
) -> None:
    """在没有 active Writer 时保留现场并暂停调度。"""

    try:
        result = _recovery().pause(run, reason=reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Agent 已暂停。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("stop")
def agent_stop(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="停止原因。"),
) -> None:
    """停止自动调度，但保留 Goal、Plan、Diff 和全部 Artifact。"""

    try:
        result = _recovery().stop(run, reason=reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        "Agent 已停止，现场未回滚。"
        if result.state.phase == "stopped"
        else (
            "停止请求已发送；等待当前 Worker 返回终态后完成对账。"
            if result.state.active_child_run
            else "停止请求未取得安全终态，现场已保留并等待人工处理。"
        )
    )
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("resume-local", hidden=True)
def agent_resume_local(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
) -> None:
    """从本机 safe Checkpoint 重新采集 Workspace 并恢复调度。"""

    try:
        result = _recovery().resume_local(run)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Agent 已从本机 Checkpoint 恢复。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("adjudicate-side-effects", hidden=True)
def agent_adjudicate_side_effects(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    input_path: Path = typer.Option(..., "--input", help="结构化 Recovery Request JSON。"),
) -> None:
    """人工核对 unknown 外部副作用，并追加不可变 Checkpoint。"""

    try:
        request = AgentRecoveryRequest.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = _side_effect_adjudicator().adjudicate(run, request)
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(redact_text(str(exc))) from exc
    typer.echo(
        "外部副作用已确认不存在，可继续准备 Handoff。"
        if result.state.phase == "stopped"
        else "外部副作用已确认为 known，任务继续等待人工处理。"
    )
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


@agent_app.command("checkpoint")
def agent_checkpoint(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    handoff: bool = typer.Option(
        False,
        "--handoff",
        help="生成跨机器 Handoff Checkpoint、Resume Capsule 和 Task Card。",
    ),
    reason: str = typer.Option(..., "--reason", help="交接或停止调度的原因。"),
) -> None:
    """在旧 Writer 已停止后生成可人工提交的 Handoff。"""

    if not handoff:
        raise typer.BadParameter("当前仅支持 Gate 3A 的 --handoff 形式")
    runtime = _runtime()
    try:
        result = runtime.handoff(run, reason=reason)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"Handoff 已生成：{result.run.run_dir.name}；"
        f"Task Card：{result.task_card_path}"
    )
    typer.echo("")
    try:
        typer.echo((result.run.run_dir / "status-card.md").read_text(encoding="utf-8"))
    except OSError:
        typer.echo(
            "Handoff 已成功生成，但状态卡暂时无法读取；请直接检查对应 run 目录。",
            err=True,
        )


@agent_app.command("observe", hidden=True)
def agent_observe(
    run: str = typer.Option(..., "--run", help="Agent run_id 或 runs/<run_id>。"),
    input_path: Path = typer.Option(..., "--input", help="结构化 Observation JSON。"),
) -> None:
    """记录外部 Observation Claim；只有受信机器对账才能推进进度。"""

    try:
        observation = AgentObservation.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = _runtime().observe(run, observation)
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("外部 Observation Claim 已记录，尚未取得机器对账资格。")
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
            typer.echo(
                json.dumps(
                    run_status_payload(Path.cwd(), run),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(_runtime().status(run))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


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
    """显示当前 Supervisor 控制面和执行能力。"""

    typer.echo(
        json.dumps(
            {
                "schema_version": 1,
                "supervisor_runtime": True,
                "control_plane": "deterministic-state-machine",
                "worker": "codex-exec",
                "finish_owned_by_core": True,
                "change_run": True,
                "multi_work_item": True,
                "local_candidate_commits": True,
                "automatic_repair": True,
                "contract_aware_replan": True,
                "legacy_task_card_resume": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _runtime() -> SupervisorAgentRuntime:
    return SupervisorAgentRuntime(Path.cwd())


def _recovery() -> SupervisorAgentRecovery:
    return SupervisorAgentRecovery(Path.cwd())


def _worker() -> SupervisorAgentWorker:
    return SupervisorAgentWorker(Path.cwd())


def _side_effect_adjudicator() -> SupervisorAgentSideEffectAdjudicator:
    return SupervisorAgentSideEffectAdjudicator(Path.cwd())


def _adapter() -> SupervisorAgentCodexAdapter:
    return SupervisorAgentCodexAdapter(
        Path.cwd(),
        progress_reporter=report_execution_progress,
        event_reporter=lambda message: typer.echo(f"[vega] {message}", err=True),
    )


def _verification_retry() -> SupervisorAgentVerificationRetry:
    return SupervisorAgentVerificationRetry(
        Path.cwd(),
        progress_reporter=report_execution_progress,
        event_reporter=lambda message: typer.echo(f"[vega] {message}", err=True),
    )


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
