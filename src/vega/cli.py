from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

from . import __version__
from .adapter_runtime import init_adapter, render_adapter_init_summary
from .brief_runtime import BriefRuntime
from .change_plan_runtime import ChangePlanRuntime
from .decision import append_run_decision, list_run_decisions
from .execution_control import request_stop_for_active_executions
from .finish_runtime import FinishRuntime
from .gate_runtime import GateRuntime
from .goal_runtime import GoalRuntime
from .loop_spec import list_loop_specs, load_loop_spec
from .loop_runtime import LoopAutomationRuntime
from .models import BriefInput
from .project_config import check_project_config, render_project_config_check
from .project_profile import ProjectProfileRuntime
from .redaction import redact_text, sensitive_path_reason
from .reflect_runtime import ReflectRuntime
from .memory import MemoryLedgerStore, MemoryProposalStore
from .recovery_runtime import RecoveryRuntime
from .review_runtime import ReviewPackRuntime, ReviewRuntime
from .run_status import latest_run_dir, render_run_status, run_status_payload
from .run_utils import resolve_run_dir
from .runtime import EngineeringChangeRuntime
from .trace import TraceWriter
from .tools.git_tools import run_git

app = typer.Typer(help="Vega 本地 Agent Loop Runtime。", invoke_without_command=True)
memory_app = typer.Typer(help="管理 memory proposal 的显式接受、拒绝和检索。")
brief_app = typer.Typer(help="生成 bug/feature 的 agent brief。")
loop_app = typer.Typer(help="执行轻量自动化研发 loop。")
do_app = typer.Typer(help="日常一键研发入口，默认执行 auto loop。")
adapters_app = typer.Typer(help="生成 Codex / Claude 等工具的轻量接入文件。")
decision_app = typer.Typer(help="记录和查看本地人工决策。")
config_app = typer.Typer(help="检查 Vega 项目级配置。")
goal_app = typer.Typer(help="管理人工驱动的长任务 goal 状态层。")
app.add_typer(memory_app, name="memory")
app.add_typer(brief_app, name="brief")
app.add_typer(loop_app, name="loop")
app.add_typer(do_app, name="do")
app.add_typer(adapters_app, name="adapters")
app.add_typer(decision_app, name="decision")
app.add_typer(config_app, name="config")
app.add_typer(goal_app, name="goal")

KNOWN_RUN_KINDS = {
    "all",
    "loop",
    "review",
    "review-pack",
    "reflect",
    "gate",
    "brief",
    "project-profile",
    "change-plan",
    "goal",
    "engineering-change",
}


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="显示 Vega 版本并退出。",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def run(
    loop_name: str = typer.Argument(..., help="Loop 名称，目前主线为 engineering-change。"),
    task: Path = typer.Option(..., "--task", help="任务 Markdown 文件。"),
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
) -> None:
    """运行本地 loop 并写入可复盘 artifacts。"""
    _reject_sensitive_input_path(task, "--task")
    if not task.exists():
        raise typer.BadParameter(f"任务文件不存在：{_safe_path_display(task)}")
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    _ensure_git_ready(repo.resolve())

    workspace = Path.cwd()
    try:
        spec = load_loop_spec(workspace, loop_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    runtime = EngineeringChangeRuntime(workspace=workspace, loop_spec=spec)
    run_dir = runtime.run(task_file=task.resolve(), repo_path=repo.resolve())
    status = _read_engineering_change_status(run_dir)
    if status == "success":
        typer.echo(f"运行成功：status={status}，run={run_dir}")
        return
    typer.echo(f"运行失败：status={status}，run={run_dir}")
    raise typer.Exit(code=1)


@app.command("profile")
def profile(repo: Path = typer.Option(..., "--repo", help="目标仓库路径。")) -> None:
    """生成项目画像，识别技术栈、测试命令、入口和项目规则。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    run_dir = ProjectProfileRuntime(workspace=Path.cwd()).run(repo.resolve())
    typer.echo(f"项目画像生成完成：{run_dir}")
    _exit_if_failed(run_dir)


@app.command("reflect")
def reflect(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    run: str | None = typer.Option(None, "--run", help="关联的上游 run_id 或 runs/<run_id>。"),
    test_log: Path | None = typer.Option(None, "--test-log", help="测试输出日志文件。"),
    note: str | None = typer.Option(None, "--note", help="本次复盘备注。"),
    lesson: str | None = typer.Option(
        None,
        "--lesson",
        help="可选的跨任务经验候选；只有显式提供时才生成 Memory Proposal。",
    ),
) -> None:
    """基于当前 diff、测试日志和项目知识生成执行后复盘。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    _ensure_git_ready(repo.resolve())
    if test_log and not test_log.exists():
        raise typer.BadParameter(f"测试日志不存在：{_safe_path_display(test_log)}")
    run_dir = ReflectRuntime(workspace=Path.cwd()).run(
        repo.resolve(),
        source_run=run,
        test_log=test_log.resolve() if test_log else None,
        note=note,
        lesson=lesson,
    )
    typer.echo(f"复盘生成完成：{run_dir}")
    typer.echo("")
    typer.echo("下一步：")
    typer.echo(f"- 生成隔离审查包：vega review-pack --repo {repo.resolve()} --run {run_dir.name}")
    typer.echo(f"- 或直接审查：vega review --repo {repo.resolve()} --run {run_dir.name} --runner codex-exec")


@app.command("plan")
def plan_change(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="大目标或重构说明文件。"),
    text: str | None = typer.Option(None, "--text", help="大目标一句话或短描述。"),
    scope: str | None = typer.Option(None, "--scope", help="scope profile，例如 small、refactor、migration。"),
) -> None:
    """为大目标生成 change-plan，不直接修改目标仓库。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    content, source = _load_brief_input(input_path, text)
    try:
        run_dir = ChangePlanRuntime(workspace=Path.cwd()).run(
            repo.resolve(),
            goal_text=content,
            input_source=source,
            scope_profile=scope,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"change plan 生成完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@app.command("review-pack")
def review_pack(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    run: str = typer.Option(..., "--run", help="reflect run_id 或 runs/<run_id>。"),
) -> None:
    """基于 reflect run 生成隔离 reviewer 的上下文包。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    _ensure_git_ready(repo.resolve())
    try:
        run_dir = ReviewPackRuntime(workspace=Path.cwd()).run(repo.resolve(), run)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"review pack 生成完成：{run_dir}")
    typer.echo("")
    typer.echo("下一步：")
    typer.echo(f"- 调用隔离 reviewer：vega review --repo {repo.resolve()} --run {run} --runner codex-exec")
    typer.echo(f"- 或手动查看 prompt：{run_dir / 'review-prompt.md'}")


@app.command("review")
def review(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    run: str = typer.Option(..., "--run", help="reflect run_id 或 runs/<run_id>。"),
    runner: str = typer.Option("codex-exec", "--runner", help="reviewer runner：codex-exec 或 none。"),
) -> None:
    """调用隔离 reviewer 审查当前 reflect run。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    _ensure_git_ready(repo.resolve())
    _ensure_runner_ready(runner, "reviewer")
    try:
        run_dir = ReviewRuntime(workspace=Path.cwd()).run(repo.resolve(), run, runner_name=runner)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"review 运行完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@app.command("gate")
def gate(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    run: str = typer.Option(..., "--run", help="reflect run_id 或 runs/<run_id>。"),
    scope: str | None = typer.Option(None, "--scope", help="预算 scope profile，例如 small、refactor、migration。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
) -> None:
    """基于 reflect run 评估风险门禁，判断是否适合 self-check、isolated-review 或 human-review。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    _ensure_git_ready(repo.resolve())
    try:
        run_dir = GateRuntime(workspace=Path.cwd()).run(repo.resolve(), run, scope_profile=scope)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(run_dir.joinpath("gate-result.json").read_text(encoding="utf-8").strip())
        return
    typer.echo(f"gate 运行完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@config_app.command("check")
def config_check(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
) -> None:
    """只读检查 `.vega.yaml` 是否能被 runtime 安全理解。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    result = check_project_config(repo.resolve())
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(render_project_config_check(result))
    if result.has_errors:
        raise typer.Exit(code=1)


@app.command("finish")
def finish(
    run: str = typer.Option(..., "--run", help="loop run_id 或 runs/<run_id>。"),
    engine: str | None = typer.Option(None, "--engine", help="校验 run 创建时固定的编排引擎。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 finish-summary.json。"),
) -> None:
    """汇总 loop 交付结论、review verdict、测试摘要和提交前 checklist。"""
    try:
        run_dir = FinishRuntime(workspace=Path.cwd()).run(run, engine=engine)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except ValueError as exc:
        raise typer.BadParameter(f"finish 只能用于 loop run：{exc}") from exc
    if json_output:
        typer.echo(run_dir.joinpath("finish-summary.json").read_text(encoding="utf-8").strip())
        return
    typer.echo(f"finish 生成完成：{run_dir}")
    typer.echo("")
    typer.echo(f"- 交付报告：{run_dir / 'finish-report.md'}")
    typer.echo(f"- 结构化摘要：{run_dir / 'finish-summary.json'}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@app.command("recover")
def recover(
    run: str = typer.Option(..., "--run", help="需要恢复的 loop run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="恢复原因，例如 worker 超时、CLI 中断或半完成。"),
    engine: str | None = typer.Option(None, "--engine", help="校验 run 创建时固定的编排引擎。"),
) -> None:
    """Linear run 安全停到人工；LangGraph run 按 checkpoint 与外部证据对账恢复。"""
    try:
        run_dir = RecoveryRuntime(workspace=Path.cwd()).recover_loop(run, reason, engine=engine)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    status, _, _ = _read_loop_outcome(run_dir)
    if status == "needs_human":
        typer.echo(
            f"recover 已安全停止，未恢复为成功：{run_dir}"
        )
    else:
        typer.echo(f"recover 完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))
    _exit_for_loop_result(
        run_dir,
        allow_initial_assist_wait=False,
    )


@app.command("resume")
def resume(
    run: str = typer.Option(..., "--run", help="等待 HITL decision 的 LangGraph loop run。"),
    decision_id: str = typer.Option(..., "--decision-id", help="已写入 decisions.jsonl 的 decision id。"),
    engine: str = typer.Option("langgraph", "--engine", help="必须与 run 固定的编排引擎一致。"),
) -> None:
    """消费一次已落盘 decision id，恢复结构化 LangGraph interrupt。"""

    try:
        run_dir = LoopAutomationRuntime(
            workspace=Path.cwd()
        ).resume_langgraph_decision(
            run,
            decision_id,
            engine=engine,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    status, _, _ = _read_loop_outcome(run_dir)
    if status == "needs_human":
        typer.echo(
            f"resume 已安全停止，未恢复为成功：{run_dir}"
        )
    else:
        typer.echo(f"resume 完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))
    _exit_for_loop_result(
        run_dir,
        allow_initial_assist_wait=False,
    )


@app.command("stop")
def stop(
    run: str = typer.Option(..., "--run", help="正在执行 worker/reviewer 的 run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="停止原因。"),
) -> None:
    """建立 run 级停止闩锁，并向全部 active execution 广播。"""
    if not reason.strip():
        raise typer.BadParameter("stop 必须提供原因，方便后续追溯。")
    try:
        run_dir = resolve_run_dir(Path.cwd(), run)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        records = request_stop_for_active_executions(run_dir, reason)
    except ValueError as exc:
        typer.echo(f"stop 广播未完整确认：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    trace_path = run_dir / "trace.jsonl"
    if trace_path.exists():
        TraceWriter(trace_path).write(
            "execution_stop_broadcast_requested",
            execution_count=len(records),
            execution_refs=[
                record.path.relative_to(run_dir).as_posix()
                for record in records
            ],
            reason=reason,
        )
    typer.echo(f"stop 广播已建立：{run_dir / 'stop-latch.json'}")
    typer.echo(f"- active execution 数：{len(records)}")
    for record in records:
        typer.echo(
            "- "
            f"{record.path.relative_to(run_dir).as_posix()}："
            f"step={record.lease.step}，"
            f"child_pid={record.lease.child_pid or '尚未启动'}"
        )
    typer.echo("- Vega runner 将负责安全停止；本命令不会扫描或 kill 无关进程。")


@goal_app.command("start")
def goal_start(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="Goal contract Markdown 文件。"),
    text: str | None = typer.Option(None, "--text", help="Goal 一句话或短描述。"),
    scope: str | None = typer.Option(None, "--scope", help="scope profile，例如 refactor、migration。"),
) -> None:
    """创建 goal contract 和状态文件，不调用 worker。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    content, source = _load_brief_input(input_path, text)
    try:
        run_dir = GoalRuntime(workspace=Path.cwd()).start(repo.resolve(), content, source, scope)
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
            typer.echo(json.dumps(run_status_payload(Path.cwd(), run), ensure_ascii=False, indent=2))
        else:
            typer.echo(render_run_status(Path.cwd(), run))
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc


@goal_app.command("step")
def goal_step(run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。")) -> None:
    """只生成下一个 checkpoint plan，不自动执行 worker。"""
    try:
        run_dir = GoalRuntime(workspace=Path.cwd()).step(run)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"checkpoint plan 生成完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


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
        run_dir = GoalRuntime(workspace=Path.cwd()).attach(
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
        run_dir = GoalRuntime(workspace=Path.cwd()).checkpoint_done(
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


@goal_app.command("handoff")
def goal_handoff(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    checkpoint: str = typer.Option(..., "--checkpoint", help="已完成 checkpoint 编号，例如 01。"),
    input_path: Path = typer.Option(
        ...,
        "--input",
        help="versioned handoff 输入 JSON 文件。",
    ),
) -> None:
    """为已完成 checkpoint 创建不可覆盖的 versioned handoff。"""
    if not input_path.is_file():
        raise typer.BadParameter(f"handoff input 文件不存在：{_safe_path_display(input_path)}")
    try:
        run_dir = GoalRuntime(workspace=Path.cwd()).handoff(
            run,
            checkpoint=checkpoint,
            input_path=str(input_path),
        )
    except (FileNotFoundError, ValueError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal handoff 创建完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("handoff-context")
def goal_handoff_context(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    checkpoint: str = typer.Option(..., "--checkpoint", help="已完成 checkpoint 编号，例如 01。"),
    version: str = typer.Option(..., "--version", help="handoff 版本，例如 v0001。"),
    consumer_session_id: str = typer.Option(
        ...,
        "--consumer-session-id",
        "--consumer-session",
        help="全新 consumer session identity，不能与 source session 相同。",
    ),
    consumer_worker_epoch: str = typer.Option(
        ...,
        "--consumer-worker-epoch",
        "--worker-epoch",
        help="consumer worker epoch identity。",
    ),
    max_chars: int = typer.Option(
        12000,
        "--max-chars",
        min=1,
        max=1_000_000,
        help="compiled context 最大字符数。",
    ),
) -> None:
    """用 fresh workspace/policy/evidence 编译 consumer context。"""
    try:
        run_dir = GoalRuntime(workspace=Path.cwd()).handoff_context(
            run,
            checkpoint=checkpoint,
            version=version,
            consumer_session_id=consumer_session_id,
            consumer_worker_epoch=consumer_worker_epoch,
            max_chars=max_chars,
        )
    except (FileNotFoundError, ValueError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal handoff context 编译完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@goal_app.command("complete")
def goal_complete(
    run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。"),
    note: str = typer.Option(..., "--note", help="完成说明，必须说明如何确认 success conditions。"),
) -> None:
    """在全部 checkpoint 完成后收口 goal，并生成最终报告和 eval。"""
    try:
        run_dir = GoalRuntime(workspace=Path.cwd()).complete(run, note)
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
        run_dir = GoalRuntime(workspace=Path.cwd()).pause(run, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal 已暂停：{run_dir}")


@goal_app.command("resume")
def goal_resume(run: str = typer.Option(..., "--run", help="goal run_id 或 runs/<run_id>。")) -> None:
    """恢复 paused goal 的状态，不恢复外部 worker 上下文。"""
    try:
        run_dir = GoalRuntime(workspace=Path.cwd()).resume(run)
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
        run_dir = GoalRuntime(workspace=Path.cwd()).stop(run, reason)
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
        run_dir = GoalRuntime(workspace=Path.cwd()).recover(run, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"goal recover 完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@app.command("list-loops")
def list_loops() -> None:
    """列出当前 workspace 中可用的 loop。"""
    specs = list_loop_specs(Path.cwd())
    if not specs:
        typer.echo("未找到 loop 配置。")
        return
    for spec in specs:
        typer.echo(f"{spec.name}\t{spec.description}")


@app.command("latest")
def latest(
    kind: str = typer.Option("all", "--kind", help="运行类型：all、loop、review、reflect、brief 等。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
) -> None:
    """显示最近一次 run，并给出下一步指引。"""
    if kind not in KNOWN_RUN_KINDS:
        available = "、".join(sorted(KNOWN_RUN_KINDS))
        raise typer.BadParameter(f"未知 run 类型：{kind}；可用值：{available}")
    try:
        run_dir = latest_run_dir(Path.cwd(), kind)
        if run_dir is None:
            typer.echo("未找到匹配的 run。")
            raise typer.Exit(code=1)
        if json_output:
            typer.echo(
                json.dumps(
                    run_status_payload(Path.cwd(), run_dir.name),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(render_run_status(Path.cwd(), run_dir.name))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("status")
def status(
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
) -> None:
    """显示指定 run 的状态、关键产物和下一步。"""
    try:
        if json_output:
            typer.echo(json.dumps(run_status_payload(Path.cwd(), run), ensure_ascii=False, indent=2))
        else:
            typer.echo(render_run_status(Path.cwd(), run))
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@adapters_app.command("init")
def adapters_init(
    target: str = typer.Argument(..., help="adapter 目标；当前支持 codex。"),
    repo: Path = typer.Option(Path("."), "--repo", help="目标仓库路径。"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的 adapter 文件。"),
) -> None:
    """生成工具侧轻量 skill adapter，不安装 hook，不修改全局配置。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    try:
        result = init_adapter(repo.resolve(), target, force=force)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(render_adapter_init_summary(result))


@decision_app.command("approve")
def decision_approve(
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    decision_type: str = typer.Option(..., "--type", help="决策类型：gate、review、finish、memory 或 custom。"),
    reason: str = typer.Option(..., "--reason", help="审批原因，必须写清楚为什么允许继续。"),
    actor: str = typer.Option("human", "--actor", help="决策人，默认 human。"),
    reference: list[str] | None = typer.Option(None, "--ref", help="关联证据文件，可重复。"),
) -> None:
    """记录一次 approve 决策。"""
    _append_decision(run, decision_type, "approved", reason, actor, reference or [])


@decision_app.command("reject")
def decision_reject(
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    decision_type: str = typer.Option(..., "--type", help="决策类型：gate、review、finish、memory 或 custom。"),
    reason: str = typer.Option(..., "--reason", help="拒绝原因，必须写清楚为什么停止或退回。"),
    actor: str = typer.Option("human", "--actor", help="决策人，默认 human。"),
    reference: list[str] | None = typer.Option(None, "--ref", help="关联证据文件，可重复。"),
) -> None:
    """记录一次 reject 决策。"""
    _append_decision(run, decision_type, "rejected", reason, actor, reference or [])


@decision_app.command("list")
def decision_list(
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    decision_type: str | None = typer.Option(None, "--type", help="按决策类型过滤。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 JSON。"),
) -> None:
    """列出某个 run 的人工决策记录。"""
    try:
        entries = list_run_decisions(Path.cwd(), run, decision_type=decision_type)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False, indent=2))
        return
    if not entries:
        typer.echo("暂无 decision 记录。")
        return
    for entry in entries:
        typer.echo(f"{entry.created_at}\t{entry.type}\t{entry.decision}\t{entry.reason}")


def _append_decision(
    run: str,
    decision_type: str,
    decision: str,
    reason: str,
    actor: str,
    references: list[str],
) -> None:
    try:
        entry = append_run_decision(
            Path.cwd(),
            run,
            decision_type=decision_type,
            decision=decision,  # type: ignore[arg-type]
            reason=reason,
            actor=actor,
            references=references,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    action = "通过" if entry.decision == "approved" else "拒绝"
    typer.echo(f"已记录 {entry.type} {action}决策：{entry.id}")


@brief_app.command("bug")
def brief_bug(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="Bug 描述文件。"),
    text: str | None = typer.Option(None, "--text", help="Bug 一句话或短描述。"),
) -> None:
    """生成 Bug 修复前可交给编码 AI 的 agent brief。"""
    _run_brief("bug", repo, input_path, text)


@brief_app.command("feature")
def brief_feature(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="需求描述文件。"),
    text: str | None = typer.Option(None, "--text", help="需求一句话或短描述。"),
) -> None:
    """生成需求开发前可交给编码 AI 的 agent brief。"""
    _run_brief("feature", repo, input_path, text)


@loop_app.command("bug")
def loop_bug(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="Bug 描述文件。"),
    text: str | None = typer.Option(None, "--text", help="Bug 一句话或短描述。"),
    mode: str = typer.Option("assist", "--mode", help="自动化模式：assist 或 auto。"),
    worker: str = typer.Option("codex-exec", "--worker", help="auto 模式 worker runner。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    engine: str = typer.Option("linear", "--engine", help="编排引擎：linear 或 langgraph。"),
    max_iterations: int = typer.Option(2, "--max-iterations", min=1, max=5, help="最大自动迭代轮数。"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="worker 后自动执行项目画像识别出的验证命令。"),
) -> None:
    """按 bug 修复流程启动轻量自动化 loop。"""
    _run_loop(
        "bug",
        repo,
        input_path,
        text,
        mode,
        worker,
        reviewer,
        engine,
        max_iterations,
        verify,
        allow_initial_assist_wait=True,
    )


@loop_app.command("feature")
def loop_feature(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="需求描述文件。"),
    text: str | None = typer.Option(None, "--text", help="需求一句话或短描述。"),
    mode: str = typer.Option("assist", "--mode", help="自动化模式：assist 或 auto。"),
    worker: str = typer.Option("codex-exec", "--worker", help="auto 模式 worker runner。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    engine: str = typer.Option("linear", "--engine", help="编排引擎：linear 或 langgraph。"),
    max_iterations: int = typer.Option(2, "--max-iterations", min=1, max=5, help="最大自动迭代轮数。"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="worker 后自动执行项目画像识别出的验证命令。"),
) -> None:
    """按需求开发流程启动轻量自动化 loop。"""
    _run_loop(
        "feature",
        repo,
        input_path,
        text,
        mode,
        worker,
        reviewer,
        engine,
        max_iterations,
        verify,
        allow_initial_assist_wait=True,
    )


@do_app.command("bug")
def do_bug(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="Bug 描述文件。"),
    text: str | None = typer.Option(None, "--text", help="Bug 一句话或短描述。"),
    mode: str = typer.Option("auto", "--mode", help="自动化模式：auto 或 assist。"),
    worker: str = typer.Option("codex-exec", "--worker", help="auto 模式 worker runner。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    engine: str = typer.Option("linear", "--engine", help="编排引擎：linear 或 langgraph。"),
    max_iterations: int = typer.Option(2, "--max-iterations", min=1, max=5, help="最大自动迭代轮数。"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="worker 后自动执行项目画像识别出的验证命令。"),
) -> None:
    """日常 bug 修复入口；默认直接跑 auto loop。"""
    _run_loop(
        "bug",
        repo,
        input_path,
        text,
        mode,
        worker,
        reviewer,
        engine,
        max_iterations,
        verify,
        allow_initial_assist_wait=True,
    )


@do_app.command("feature")
def do_feature(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    input_path: Path | None = typer.Option(None, "--input", help="需求描述文件。"),
    text: str | None = typer.Option(None, "--text", help="需求一句话或短描述。"),
    mode: str = typer.Option("auto", "--mode", help="自动化模式：auto 或 assist。"),
    worker: str = typer.Option("codex-exec", "--worker", help="auto 模式 worker runner。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    engine: str = typer.Option("linear", "--engine", help="编排引擎：linear 或 langgraph。"),
    max_iterations: int = typer.Option(2, "--max-iterations", min=1, max=5, help="最大自动迭代轮数。"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="worker 后自动执行项目画像识别出的验证命令。"),
) -> None:
    """日常需求开发入口；默认直接跑 auto loop。"""
    _run_loop(
        "feature",
        repo,
        input_path,
        text,
        mode,
        worker,
        reviewer,
        engine,
        max_iterations,
        verify,
        allow_initial_assist_wait=True,
    )


@loop_app.command("continue")
def loop_continue(
    run: str = typer.Option(..., "--run", help="assist loop run_id 或 runs/<run_id>。"),
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    engine: str | None = typer.Option(None, "--engine", help="校验 run 创建时固定的编排引擎。"),
    test_log: Path | None = typer.Option(None, "--test-log", help="测试输出日志文件。"),
    note: str | None = typer.Option(None, "--note", help="本轮复盘备注。"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="未提供 --test-log 时自动执行验证命令。"),
) -> None:
    """在主会话/人工完成修改后，继续 needs_human loop 的 reflect + review。"""
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    _ensure_git_ready(repo.resolve())
    if test_log and not test_log.exists():
        raise typer.BadParameter(f"测试日志不存在：{_safe_path_display(test_log)}")
    _ensure_runner_ready(reviewer, "reviewer")
    try:
        run_dir = LoopAutomationRuntime(workspace=Path.cwd()).continue_assist(
            run,
            repo.resolve(),
            reviewer_name=reviewer,
            engine=engine,
            test_log=test_log.resolve() if test_log else None,
            note=note,
            verify=verify,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"loop continue 完成：{run_dir}")
    typer.echo("")
    _echo_run_status(run_dir)
    _exit_for_loop_result(run_dir, allow_initial_assist_wait=False)


def _run_brief(mode: str, repo: Path, input_path: Path | None, text: str | None) -> None:
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    content, source = _load_brief_input(input_path, text)
    brief_input = BriefInput(
        mode=mode,  # type: ignore[arg-type]
        text=content,
        source=source,
        repo_path=str(repo.resolve()),
    )
    run_dir = BriefRuntime(workspace=Path.cwd()).run(brief_input)
    typer.echo(f"brief 生成完成：{run_dir}")
    typer.echo("")
    typer.echo("下一步：")
    typer.echo(f"- 把 brief 交给 worker：{run_dir / 'agent-brief.md'}")
    typer.echo(f"- 修改完成后复盘：vega reflect --repo {repo.resolve()} --run {run_dir.name} --test-log <log>")


def _run_loop(
    task_mode: str,
    repo: Path,
    input_path: Path | None,
    text: str | None,
    automation_mode: str,
    worker: str,
    reviewer: str,
    engine: str,
    max_iterations: int,
    verify: bool,
    *,
    allow_initial_assist_wait: bool,
) -> None:
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{_safe_path_display(repo)}")
    if automation_mode not in {"assist", "auto"}:
        raise typer.BadParameter("--mode 只能是 assist 或 auto")
    _ensure_git_ready(repo.resolve())
    _validate_runner_name(worker, "worker")
    _validate_runner_name(reviewer, "reviewer")
    if automation_mode == "auto":
        _ensure_runner_ready(worker, "worker")
        _ensure_runner_ready(reviewer, "reviewer")
    content, source = _load_brief_input(input_path, text)
    brief_input = BriefInput(
        mode=task_mode,  # type: ignore[arg-type]
        text=content,
        source=source,
        repo_path=str(repo.resolve()),
    )
    try:
        run_dir = LoopAutomationRuntime(workspace=Path.cwd()).start(
            brief_input,
            automation_mode=automation_mode,  # type: ignore[arg-type]
            worker_name=worker,
            reviewer_name=reviewer,
            engine=engine,
            max_iterations=max_iterations,
            verify=verify,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"loop 运行完成：{run_dir}")
    typer.echo("")
    _echo_run_status(run_dir)
    _exit_for_loop_result(
        run_dir,
        allow_initial_assist_wait=allow_initial_assist_wait,
    )


def _load_brief_input(input_path: Path | None, text: str | None) -> tuple[str, str]:
    if input_path and text:
        raise typer.BadParameter("--input 和 --text 只能二选一")
    if not input_path and not text:
        raise typer.BadParameter("必须提供 --input 或 --text")
    if input_path:
        _reject_sensitive_input_path(input_path, "--input")
        if not input_path.exists():
            raise typer.BadParameter(f"输入文件不存在：{_safe_path_display(input_path)}")
        return input_path.read_text(encoding="utf-8"), str(input_path.resolve())
    assert text is not None
    if not text.strip():
        raise typer.BadParameter("--text 不能为空")
    return text, "inline-text"


def _ensure_runner_ready(runner: str, role: str) -> None:
    normalized = _validate_runner_name(runner, role)
    if normalized not in {"codex", "codex-exec"}:
        return
    if shutil.which("codex"):
        return
    raise typer.BadParameter(
        f"{role} 配置为 codex-exec，但当前 PATH 中未找到 Codex CLI；"
        "请先安装并登录 Codex CLI，或显式选择 none/prompt-only runner。"
    )


def _validate_runner_name(runner: str, role: str) -> str:
    normalized = runner.strip().lower()
    if normalized not in {"none", "prompt-only", "codex", "codex-exec"}:
        raise typer.BadParameter(
            f"{role} runner 不受支持：{runner}；可用值为 "
            "none、prompt-only、codex、codex-exec。"
        )
    return normalized


def _ensure_git_ready(repo: Path) -> None:
    returncode, _, stderr = run_git(repo, "git.status")
    if returncode == 0:
        return
    detail = redact_text(stderr.strip()) or f"git status 退出码 {returncode}"
    raise typer.BadParameter(f"目标仓库 Git 预检失败：\n{detail}")


def _reject_sensitive_input_path(path: Path, option_name: str) -> None:
    reason = sensitive_path_reason(path)
    if reason:
        raise typer.BadParameter(f"{option_name} 拒绝读取敏感路径（{reason}）")


def _safe_path_display(path: Path) -> str:
    return redact_text(str(path))


def _read_engineering_change_status(run_dir: Path) -> str:
    state_path = run_dir / "state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    status = payload.get("status")
    return status if isinstance(status, str) and status else "unknown"


def _exit_if_failed(run_dir: Path) -> None:
    if _read_engineering_change_status(run_dir) == "failed":
        raise typer.Exit(code=1)


def _echo_run_status(run_dir: Path) -> None:
    try:
        typer.echo(render_run_status(Path.cwd(), run_dir.name))
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _exit_for_loop_result(
    run_dir: Path,
    *,
    allow_initial_assist_wait: bool,
) -> None:
    status, current_step, automation_mode = _read_loop_outcome(run_dir)
    if status == "success":
        return
    if status == "running" and current_step == "human_decision":
        return
    if (
        allow_initial_assist_wait
        and automation_mode == "assist"
        and status == "needs_human"
        and current_step == "waiting_for_worker"
    ):
        return
    raise typer.Exit(code=1)


def _read_loop_outcome(run_dir: Path) -> tuple[str, str, str]:
    state_path = run_dir / "state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown", "unknown", "unknown"
    if not isinstance(payload, dict):
        return "unknown", "unknown", "unknown"
    status = payload.get("status")
    current_step = payload.get("current_step")
    automation_mode = payload.get("automation_mode")
    return (
        status if isinstance(status, str) and status else "unknown",
        current_step if isinstance(current_step, str) and current_step else "unknown",
        automation_mode
        if isinstance(automation_mode, str) and automation_mode
        else "unknown",
    )


@memory_app.command("list")
def memory_list(
    status: str | None = typer.Option(None, "--status", help="按 accepted 或 rejected 过滤。"),
) -> None:
    """列出长期 memory ledger 中的决策记录。"""
    store = MemoryLedgerStore(Path.cwd())
    entries = store.list_entries()
    if status:
        entries = [entry for entry in entries if entry.status == status]
    if not entries:
        typer.echo("暂无 memory ledger 记录。")
        return
    for entry in entries:
        typer.echo(f"{entry.status}\t{entry.proposal_id}\t{entry.title}")


@memory_app.command("search")
def memory_search(query: str = typer.Argument(..., help="检索关键词。")) -> None:
    """检索已接受的长期 memory。"""
    store = MemoryLedgerStore(Path.cwd())
    results = store.search(query, accepted_only=True)
    if not results:
        typer.echo("未命中已接受 memory。")
        return
    for entry in results:
        typer.echo(f"{entry.proposal_id}\t{entry.title}\n{entry.content}\n")


@memory_app.command("accept")
def memory_accept(
    proposal_id: str = typer.Argument(..., help="memory proposal ID。"),
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    reason: str | None = typer.Option(None, "--reason", help="接受原因。"),
) -> None:
    """显式接受某次 run 生成的 memory proposal。"""
    _decide_memory(proposal_id, run, "accepted", reason)


@memory_app.command("reject")
def memory_reject(
    proposal_id: str = typer.Argument(..., help="memory proposal ID。"),
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    reason: str | None = typer.Option(None, "--reason", help="拒绝原因。"),
) -> None:
    """显式拒绝某次 run 生成的 memory proposal。"""
    _decide_memory(proposal_id, run, "rejected", reason)


def _decide_memory(proposal_id: str, run: str, status: str, reason: str | None) -> None:
    workspace = Path.cwd()
    run_dir = _resolve_run_dir(workspace, run)
    proposal = MemoryProposalStore(run_dir).get(proposal_id)
    if proposal is None:
        raise typer.BadParameter(f"指定 run 中不存在 proposal：{proposal_id}")

    ledger = MemoryLedgerStore(workspace)
    if ledger.has_decision(proposal_id):
        raise typer.BadParameter(f"proposal 已经有决策记录：{proposal_id}")
    entry = ledger.append_decision(proposal, status, reason)
    action = "接受" if entry.status == "accepted" else "拒绝"
    typer.echo(f"已{action} memory proposal：{entry.proposal_id}")


def _resolve_run_dir(workspace: Path, run: str) -> Path:
    try:
        return resolve_run_dir(workspace, run)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc


if __name__ == "__main__":
    app()
