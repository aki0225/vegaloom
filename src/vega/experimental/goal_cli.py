from __future__ import annotations

import json
from pathlib import Path

import typer

from ..cli_support import (
    ensure_runner_ready,
    load_brief_input,
    report_execution_progress,
    require_repo_directory,
)
from ..run_status import render_run_status, run_status_payload

goal_app = typer.Typer(help="管理人工驱动的长任务 goal 状态层。")


@goal_app.command("start")
def goal_start(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="Goal contract Markdown 文件。"),
    text: str | None = typer.Option(None, "--text", help="Goal 一句话或短描述。"),
    scope: str | None = typer.Option(None, "--scope", help="scope profile，例如 refactor、migration。"),
) -> None:
    """创建 goal contract 和状态文件，不调用 worker。"""
    repo = require_repo_directory(repo)
    content, source = load_brief_input(input_path, text)
    try:
        run_dir = _goal_runtime().start(repo, content, source, scope)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal 创建完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("status")
def goal_status(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
) -> None:
    """显示 goal 状态、关键产物和下一步。"""
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
            typer.echo(render_run_status(Path.cwd(), run))
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc


@goal_app.command("step")
def goal_step(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    input_path: Path | None = typer.Option(None, "--input", help="checkpoint 任务 Markdown 文件。"),
    text: str | None = typer.Option(None, "--text", help="checkpoint 任务描述。"),
) -> None:
    """生成下一个 checkpoint plan；提供任务后可交给 Goal P1 实验执行。"""
    task_text = None
    task_source = None
    if input_path or text:
        task_text, task_source = load_brief_input(input_path, text)
    try:
        run_dir = _goal_runtime().step(
            run,
            task_text=task_text,
            task_source=task_source,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"checkpoint plan 生成完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("run")
def goal_run(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    max_checkpoints: int = typer.Option(
        1,
        "--max-checkpoints",
        min=1,
        max=1,
        help="当前实验固定为 1；checkpoint 边界会暂停。",
    ),
    worker: str = typer.Option("codex-exec", "--worker", help="auto 模式 worker runner。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    max_iterations: int = typer.Option(2, "--max-iterations", min=1, max=5),
    runner_timeout_seconds: int = typer.Option(
        900,
        "--runner-timeout",
        min=60,
        max=3600,
        help="单个 Worker 或 Reviewer 外部进程的超时秒数。",
    ),
    verify: bool = typer.Option(True, "--verify/--no-verify"),
) -> None:
    """自动执行一个明确 checkpoint，并在证据边界停下。"""
    ensure_runner_ready(worker, "worker")
    ensure_runner_ready(reviewer, "reviewer")
    try:
        run_dir = _goal_runtime().run_one(
            run,
            worker_name=worker,
            reviewer_name=reviewer,
            max_iterations=max_iterations,
            verify=verify,
            max_checkpoints=max_checkpoints,
            runner_timeout_seconds=runner_timeout_seconds,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal checkpoint 执行完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))
    if run_status_payload(Path.cwd(), run_dir.name)["status"] != "checkpoint_done":
        raise typer.Exit(code=1)


@goal_app.command("reconcile")
def goal_reconcile(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
) -> None:
    """重启后重新核对已记录 child，不启动新的 worker。"""
    try:
        run_dir = _goal_runtime().reconcile(run)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal child 重新核对完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))
    if run_status_payload(Path.cwd(), run_dir.name)["status"] != "checkpoint_done":
        raise typer.Exit(code=1)


@goal_app.command("attach")
def goal_attach(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    checkpoint: str = typer.Option(..., "--checkpoint", help="checkpoint 编号，例如 01。"),
    child_run: str = typer.Option(
        ...,
        "--ref",
        help="自动证据使用子 run_id；manual 证据使用 workspace/目标仓库内的真实文件。",
    ),
    evidence_type: str = typer.Option(
        ...,
        "--type",
        help="证据类型：loop、reflect、gate、review、finish 或 manual。",
    ),
    note: str | None = typer.Option(None, "--note", help="证据备注。"),
) -> None:
    """把人工完成的子 run 或证据引用挂到 checkpoint，不自动执行。"""
    try:
        run_dir = _goal_runtime().attach(
            run,
            checkpoint=checkpoint,
            child_run=child_run,
            evidence_type=evidence_type,
            note=note,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"checkpoint 证据已挂载：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("checkpoint-done")
def goal_checkpoint_done(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    checkpoint: str = typer.Option(..., "--checkpoint", help="checkpoint 编号，例如 01。"),
    note: str | None = typer.Option(None, "--note", help="完成备注。"),
    allow_manual_evidence: bool = typer.Option(
        False,
        "--allow-manual-evidence",
        help="显式允许仅使用 manual 文件证据完成 checkpoint；必须同时提供 --note。",
    ),
) -> None:
    """标记 checkpoint 完成并写 checkpoint-report.md。"""
    try:
        run_dir = _goal_runtime().checkpoint_done(
            run,
            checkpoint,
            note=note,
            allow_manual_evidence=allow_manual_evidence,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"checkpoint 已完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("complete")
def goal_complete(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    note: str = typer.Option(..., "--note", help="完成说明，必须说明如何确认 success conditions。"),
) -> None:
    """在全部 checkpoint 完成后结束 goal，并生成最终报告和 eval。"""
    try:
        run_dir = _goal_runtime().complete(run, note)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal 已完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("pause")
def goal_pause(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="暂停原因。"),
) -> None:
    """暂停 goal，不清理工作区。"""
    try:
        run_dir = _goal_runtime().pause(run, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal 已暂停：{run_dir}")


@goal_app.command("resume")
def goal_resume(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
) -> None:
    """恢复 paused goal 的状态，不恢复外部 worker 上下文。"""
    try:
        run_dir = _goal_runtime().resume(run)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal 已恢复：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("stop")
def goal_stop(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="停止原因。"),
) -> None:
    """停止 goal 后续调度，不回滚、不删除、不提交。"""
    try:
        run_dir = _goal_runtime().stop(run, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal 已停止：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("recover")
def goal_recover(
    run: str = typer.Option(..., "--run", help="running goal run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="恢复原因，例如 CLI 中断。"),
) -> None:
    """把 running goal 标记为 needs_human，保留现场并交还人工。"""
    try:
        run_dir = _goal_runtime().recover(run, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal recover 完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


def _goal_runtime():
    from .goal_runtime import GoalRuntime

    return GoalRuntime(
        workspace=Path.cwd(),
        progress_reporter=report_execution_progress,
    )
