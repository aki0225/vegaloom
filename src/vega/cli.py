from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from . import __version__
from .brief_runtime import BriefRuntime
from .change_plan_runtime import ChangePlanRuntime
from .cli_support import (
    echo_run_status,
    ensure_runner_ready as _ensure_runner_ready,
    exit_for_loop_result,
    exit_if_failed,
    load_brief_input as _load_brief_input,
    make_loop_runtime, make_review_runtime, read_engineering_change_status,
    reject_sensitive_input_path as _reject_sensitive_input_path,
    require_repo_directory,
    safe_path_display as _safe_path_display,
    validate_runner_name as _validate_runner_name,
)
from .decision import append_run_decision, list_run_decisions
from .execution_control import request_stop_for_run
from .finish_runtime import FinishRuntime
from .gate_runtime import GateRuntime
from .memory_artifacts import MemoryProposalStore
from .models import BriefInput
from .project_config import check_project_config, render_project_config_check
from .project_profile import ProjectProfileRuntime
from .progress import (
    RunProgressLog,
    render_progress_items,
    safe_run_id,
    safe_run_status,
    safe_run_step,
)
from .redaction import redact_text
from .reflect_runtime import ReflectRuntime
from .recovery_runtime import RecoveryRuntime
from .review_runtime import ReviewPackRuntime
from .run_status import latest_run_dir, render_run_status, run_status_payload
from .run_utils import resolve_run_dir
from .tools.git_tools import run_git

app = typer.Typer(help="Vega 本地 Agent Loop Runtime。", invoke_without_command=True)
memory_app = typer.Typer(help="管理 memory proposal 的显式接受、拒绝和检索。")
brief_app = typer.Typer(help="生成 bug/feature 的 agent brief。")
loop_app = typer.Typer(help="执行轻量自动化研发 loop。")
do_app = typer.Typer(help="日常一键研发入口，默认执行 auto loop。")
adapters_app = typer.Typer(help="生成 Codex / Claude 等工具的轻量接入文件。")
decision_app = typer.Typer(help="记录和查看本地人工决策。")
config_app = typer.Typer(help="检查 Vega 项目级配置。")
app.add_typer(memory_app, name="memory")
app.add_typer(brief_app, name="brief")
app.add_typer(loop_app, name="loop")
app.add_typer(do_app, name="do")
app.add_typer(adapters_app, name="adapters")
app.add_typer(decision_app, name="decision")
app.add_typer(config_app, name="config")


def _register_goal_app() -> None:
    from .experimental.goal_cli import goal_app

    app.add_typer(goal_app, name="goal")


_register_goal_app()

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
    _install_optional_extensions()


@app.command()
def run(
    loop_name: str = typer.Argument(..., help="Loop 名称，目前主线为 engineering-change。"),
    task: Path = typer.Option(..., "--task", help="任务 Markdown 文件。"),
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
) -> None:
    """运行本地 loop 并写入可复盘 artifacts。"""
    from .experimental.inspection.loop_spec import load_loop_spec
    from .experimental.inspection.runtime import EngineeringChangeRuntime

    _reject_sensitive_input_path(task, "--task")
    if not task.exists():
        raise typer.BadParameter(f"任务文件不存在：{_safe_path_display(task)}")
    repo = require_repo_directory(repo)
    _ensure_git_ready(repo)

    workspace = Path.cwd()
    try:
        spec = load_loop_spec(workspace, loop_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    runtime = EngineeringChangeRuntime(workspace=workspace, loop_spec=spec)
    run_dir = runtime.run(task_file=task.resolve(), repo_path=repo)
    status = read_engineering_change_status(run_dir)
    if status == "success":
        typer.echo(f"运行成功：status={status}，run={run_dir}")
        return
    typer.echo(f"运行失败：status={status}，run={run_dir}")
    raise typer.Exit(code=1)


@app.command("profile")
def profile(repo: Path = typer.Option(..., "--repo", help="目标仓库路径。")) -> None:
    """生成项目画像，识别技术栈、测试命令、入口和项目规则。"""
    repo = require_repo_directory(repo)
    run_dir = ProjectProfileRuntime(workspace=Path.cwd()).run(repo)
    typer.echo(f"项目画像生成完成：{run_dir}")
    exit_if_failed(run_dir)


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
    repo = require_repo_directory(repo)
    _ensure_git_ready(repo)
    if test_log and not test_log.exists():
        raise typer.BadParameter(f"测试日志不存在：{_safe_path_display(test_log)}")
    run_dir = ReflectRuntime(workspace=Path.cwd()).run(
        repo,
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
    repo = require_repo_directory(repo)
    content, source = _load_brief_input(input_path, text)
    try:
        run_dir = ChangePlanRuntime(workspace=Path.cwd()).run(
            repo,
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
    repo = require_repo_directory(repo)
    _ensure_git_ready(repo)
    try:
        run_dir = ReviewPackRuntime(workspace=Path.cwd()).run(repo, run)
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
    repo = require_repo_directory(repo)
    _ensure_git_ready(repo)
    _ensure_runner_ready(runner, "reviewer")
    try:
        run_dir = make_review_runtime(Path.cwd()).run(repo, run, runner_name=runner)
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
    repo = require_repo_directory(repo)
    _ensure_git_ready(repo)
    try:
        run_dir = GateRuntime(workspace=Path.cwd()).run(repo, run, scope_profile=scope)
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
    repo = require_repo_directory(repo)
    result = check_project_config(repo)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(render_project_config_check(result))
    if result.has_errors:
        raise typer.Exit(code=1)


@app.command("finish")
def finish(
    run: str = typer.Option(..., "--run", help="loop run_id 或 runs/<run_id>。"),
    json_output: bool = typer.Option(False, "--json", help="输出机器可读 finish-summary.json。"),
) -> None:
    """汇总 loop 交付结论、review verdict、测试摘要和提交前 checklist。"""
    try:
        run_dir = FinishRuntime(workspace=Path.cwd()).run(run)
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
    run: str = typer.Option(..., "--run", help="处于 running 的 loop run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="恢复原因，例如 worker 超时、CLI 中断或半完成。"),
) -> None:
    """把卡在 running 的 loop 标记为 needs_human，保留现场并允许人工继续。"""
    try:
        run_dir = RecoveryRuntime(workspace=Path.cwd()).recover_loop(run, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"recover 完成：{run_dir}")
    typer.echo("")
    typer.echo(render_run_status(Path.cwd(), run_dir.name))


@app.command("stop")
def stop(
    run: str = typer.Option(..., "--run", help="正在执行 worker/reviewer 的 run_id 或 runs/<run_id>。"),
    reason: str = typer.Option(..., "--reason", help="停止原因。"),
) -> None:
    """请求当前 owned process 在安全边界停止，不扫描或终止无关进程。"""
    try:
        run_dir = resolve_run_dir(Path.cwd(), run)
        record = request_stop_for_run(run_dir, reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"stop request 已写入：{record.path.parent / 'stop-request.json'}")
    typer.echo(f"- 步骤：{record.lease.step}")
    typer.echo(f"- owned child PID：{record.lease.child_pid or '尚未启动'}")
    typer.echo("- Vega runner 将负责安全停止；本命令不会扫描或 kill 其他进程。")


@app.command("list-loops")
def list_loops() -> None:
    """列出当前 workspace 中可用的 loop。"""
    from .experimental.inspection.loop_spec import list_loop_specs

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


@app.command("watch")
def watch(
    run: str = typer.Option(..., "--run", help="run_id 或 runs/<run_id>。"),
    follow: bool = typer.Option(False, "--follow/--no-follow", help="持续等待并显示新增安全事件。"),
    interval: float = typer.Option(1.0, "--interval", min=0.2, max=30.0),
    limit: int = typer.Option(20, "--limit", min=1, max=200),
    json_output: bool = typer.Option(False, "--json", help="输出安全的机器可读进度。"),
) -> None:
    """查看 run 的安全进度事件；不显示模型正文、推理或原始命令参数。"""
    try:
        run_dir = resolve_run_dir(Path.cwd(), run)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    printed = 0
    first = True
    while True:
        was_first = first
        try:
            payload = run_status_payload(Path.cwd(), run_dir.name)
            items = RunProgressLog(run_dir).read()
        except (FileNotFoundError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        status_value = safe_run_status(payload["status"])
        step_value = safe_run_step(payload["current_step"])
        run_id_value = safe_run_id(run_dir.name)

        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "run_id": run_id_value,
                        "status": status_value,
                        "current_step": step_value,
                        "progress": items[-limit:],
                    },
                    ensure_ascii=False,
                )
            )
        elif first:
            typer.echo(
                f"run={run_id_value} status={status_value} "
                f"step={step_value}"
            )
            typer.echo(render_progress_items(items, limit=limit))
        else:
            new_items = items[printed:]
            if new_items:
                typer.echo(render_progress_items(new_items, limit=limit))
        printed = len(items)
        first = False

        is_terminal = status_value not in {"created", "running"}
        if follow and not json_output and not was_first and is_terminal:
            typer.echo(
                f"run={run_id_value} status={status_value} "
                f"step={step_value}"
            )
        if not follow or is_terminal:
            return
        time.sleep(interval)


@adapters_app.command("init")
def adapters_init(
    target: str = typer.Argument(..., help="adapter 目标；当前支持 codex。"),
    repo: Path = typer.Option(Path("."), "--repo", help="目标仓库路径。"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的 adapter 文件。"),
) -> None:
    """生成工具侧轻量 skill adapter，不安装 hook，不修改全局配置。"""
    from .experimental.adapter_runtime import init_adapter, render_adapter_init_summary

    repo = require_repo_directory(repo)
    try:
        result = init_adapter(repo, target, force=force)
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
        max_iterations,
        verify,
        allow_initial_assist_wait=True,
    )


@loop_app.command("continue")
def loop_continue(
    run: str = typer.Option(..., "--run", help="assist loop run_id 或 runs/<run_id>。"),
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    reviewer: str = typer.Option("codex-exec", "--reviewer", help="隔离 reviewer runner。"),
    test_log: Path | None = typer.Option(None, "--test-log", help="测试输出日志文件。"),
    note: str | None = typer.Option(None, "--note", help="本轮复盘备注。"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="未提供 --test-log 时自动执行验证命令。"),
) -> None:
    """在主会话/人工完成修改后，继续 needs_human loop 的 reflect + review。"""
    repo = require_repo_directory(repo)
    _ensure_git_ready(repo)
    if test_log and not test_log.exists():
        raise typer.BadParameter(f"测试日志不存在：{_safe_path_display(test_log)}")
    _ensure_runner_ready(reviewer, "reviewer")
    try:
        run_dir = make_loop_runtime(Path.cwd()).continue_assist(
            run,
            repo,
            reviewer_name=reviewer,
            test_log=test_log.resolve() if test_log else None,
            note=note,
            verify=verify,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"loop continue 完成：{run_dir}")
    typer.echo("")
    echo_run_status(run_dir)
    exit_for_loop_result(run_dir, allow_initial_assist_wait=False)


def _run_brief(mode: str, repo: Path, input_path: Path | None, text: str | None) -> None:
    repo = require_repo_directory(repo)
    content, source = _load_brief_input(input_path, text)
    brief_input = BriefInput(
        mode=mode,  # type: ignore[arg-type]
        text=content,
        source=source,
        repo_path=str(repo),
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
    max_iterations: int,
    verify: bool,
    *,
    allow_initial_assist_wait: bool,
) -> None:
    repo = require_repo_directory(repo)
    if automation_mode not in {"assist", "auto"}:
        raise typer.BadParameter("--mode 只能是 assist 或 auto")
    _ensure_git_ready(repo)
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
        repo_path=str(repo),
    )
    try:
        run_dir = make_loop_runtime(Path.cwd()).start(
            brief_input,
            automation_mode=automation_mode,  # type: ignore[arg-type]
            worker_name=worker,
            reviewer_name=reviewer,
            max_iterations=max_iterations,
            verify=verify,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"loop 运行完成：{run_dir}")
    typer.echo("")
    echo_run_status(run_dir)
    exit_for_loop_result(
        run_dir,
        allow_initial_assist_wait=allow_initial_assist_wait,
    )


def _ensure_git_ready(repo: Path) -> None:
    returncode, _, stderr = run_git(repo, "git.status")
    if returncode == 0:
        return
    detail = redact_text(stderr.strip()) or f"git status 退出码 {returncode}"
    raise typer.BadParameter(f"目标仓库 Git 预检失败：\n{detail}")


@memory_app.command("list")
def memory_list(
    status: str | None = typer.Option(None, "--status", help="按 accepted 或 rejected 过滤。"),
) -> None:
    """列出长期 memory ledger 中的决策记录。"""
    store = _memory_ledger_store(Path.cwd())
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
    store = _memory_ledger_store(Path.cwd())
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

    ledger = _memory_ledger_store(workspace)
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


def _install_optional_extensions() -> None:
    from .experimental.memory import install_memory_backend

    install_memory_backend()


def _memory_ledger_store(workspace: Path):
    from .experimental.memory import MemoryLedgerStore

    return MemoryLedgerStore(workspace)


if __name__ == "__main__":
    app()
