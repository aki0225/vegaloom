from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

import typer

from . import __version__
from .agent_cli_snapshot import (
    build_agent_cli_snapshot,
    resolve_agent_cli_run,
)
from .agent_cli_status import (
    explanation_json_payload,
    render_agent_explanation,
    render_status_snapshot,
)
from .agent_run_selection import ChangeRunSelectionError
from .cli_support import require_repo_directory
from .progress import (
    render_progress_items,
    safe_run_id,
    safe_run_status,
    safe_run_step,
)
from .project_config import check_project_config, render_project_config_check
from .run_progress import progress_items_for_run
from .run_status import latest_run_dir, render_run_status, run_status_payload
from .run_utils import resolve_run_dir


app = typer.Typer(
    help="Vega 软件工程 Agent。",
    invoke_without_command=True,
)
adapters_app = typer.Typer(help="生成 Codex 等宿主的轻量接入文件。")
config_app = typer.Typer(help="检查 Vega 项目配置。")
app.add_typer(adapters_app, name="adapters")
app.add_typer(config_app, name="config")


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


@config_app.command("check")
def config_check(
    repo: Path = typer.Option(..., "--repo", help="目标仓库路径。"),
    provider: Literal["codex", "claude"] | None = typer.Option(
        None,
        "--provider",
        help="本次使用的 Provider；--change 默认 codex，普通检查读取项目 runner。",
    ),
    change: bool = typer.Option(
        False,
        "--change",
        help="按自然语言 `vega change` 的固定配置要求预检。",
    ),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    """只读检查 `.vega.yaml` 是否能被运行时安全理解。"""

    repo = require_repo_directory(repo)
    result = check_project_config(
        repo,
        provider=provider,
        require_change_config=change,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo(render_project_config_check(result))
    if result.has_errors:
        raise typer.Exit(code=1)


@app.command("latest")
def latest(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    """显示最近一次 Agent run。"""

    try:
        run_dir = latest_run_dir(Path.cwd(), "agent")
        if run_dir is None:
            typer.echo("未找到 Agent run。")
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
            typer.echo(_render_status(run_dir.name))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("status", rich_help_panel="日常使用")
def status(
    run: str | None = typer.Option(
        None,
        "--run",
        help="ChangeRun ID 或 runs/<run-id>；省略时按当前仓库选择。",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="文本输出显示完整状态卡；JSON 始终保留完整排障字段。",
    ),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    """显示当前阶段、会话、Diff、门禁和下一步。"""

    try:
        target = resolve_agent_cli_run(Path.cwd(), run)
        snapshot = build_agent_cli_snapshot(
            target,
            include_full=full and not json_output,
        )
        if json_output:
            typer.echo(
                json.dumps(
                    snapshot.status,
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(render_status_snapshot(snapshot, full=full))
    except ChangeRunSelectionError as exc:
        typer.echo(f"无法选择 ChangeRun：{exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("explain", rich_help_panel="日常使用")
def explain(
    run: str | None = typer.Option(
        None,
        "--run",
        help="ChangeRun ID 或 runs/<run-id>；省略时按当前仓库选择。",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="附带完整状态卡或完整 JSON 状态。",
    ),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    """只读解释当前决定、已确认事实、未知项和安全动作。"""

    try:
        target = resolve_agent_cli_run(Path.cwd(), run)
        snapshot = build_agent_cli_snapshot(
            target,
            include_full=full and not json_output,
        )
        if json_output:
            typer.echo(
                json.dumps(
                    explanation_json_payload(snapshot, full=full),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(render_agent_explanation(snapshot, full=full))
    except ChangeRunSelectionError as exc:
        typer.echo(f"无法选择 ChangeRun：{exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("watch")
def watch(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    follow: bool = typer.Option(
        False,
        "--follow/--no-follow",
        help="持续显示新增安全事件。",
    ),
    interval: float = typer.Option(1.0, "--interval", min=0.2, max=30.0),
    limit: int = typer.Option(20, "--limit", min=1, max=200),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
) -> None:
    """查看低频安全进度；不显示推理、模型正文或原始命令参数。"""

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
            items = progress_items_for_run(Path.cwd(), run_dir, payload)
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
                f"run={run_id_value} status={status_value} step={step_value}"
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
                f"run={run_id_value} status={status_value} step={step_value}"
            )
        if not follow or is_terminal:
            return
        time.sleep(interval)


@adapters_app.command("init")
def adapters_init(
    target: str = typer.Argument(..., help="宿主；当前支持 codex。"),
    repo: Path = typer.Option(Path("."), "--repo", help="目标仓库路径。"),
    force: bool = typer.Option(False, "--force", help="覆盖已有接入文件。"),
) -> None:
    """生成宿主侧 Skill，不安装 Hook 或修改全局配置。"""

    from .experimental.adapter_runtime import (
        init_adapter,
        render_adapter_init_summary,
    )

    repo = require_repo_directory(repo)
    try:
        result = init_adapter(repo, target, force=force)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(render_adapter_init_summary(result))


def _render_status(run: str) -> str:
    payload = run_status_payload(Path.cwd(), run)
    if payload.get("kind") == "agent":
        from .agent_runtime import SupervisorAgentRuntime

        return SupervisorAgentRuntime(Path.cwd()).status(run)
    return render_run_status(Path.cwd(), run)


if __name__ == "__main__":
    app()
