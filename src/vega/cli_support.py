from __future__ import annotations

import json
from pathlib import Path

import typer

from .loop_runtime import LoopAutomationRuntime
from .redaction import redact_text
from .review_runtime import ReviewRuntime
from .run_status import render_run_status

_PROGRESS_STEP_LABELS = {
    "worker": "worker",
    "reviewer": "reviewer",
    "verification": "verification",
    "runner": "runner",
}
_PROGRESS_EVENT_MESSAGES = {
    "turn_started": "开始分析任务",
    "command_started": "开始执行命令",
    "command_completed": "命令执行完成",
    "command_failed": "命令执行失败",
    "file_changed": "已应用文件修改",
    "file_change_failed": "文件修改失败",
    "plan_updated": "更新执行计划",
    "tool_started": "开始调用工具",
    "tool_completed": "工具调用完成",
    "tool_failed": "工具调用失败",
    "turn_completed": "完成模型回合",
    "turn_failed": "模型回合失败",
}


def report_execution_progress(step: str, elapsed_seconds: int) -> None:
    base_step, _, event = step.partition(".")
    label = _PROGRESS_STEP_LABELS.get(base_step, "runner")
    event_message = _PROGRESS_EVENT_MESSAGES.get(event)
    if event_message is not None:
        message = f"[vega] {label} {event_message}，已用时 {max(0, elapsed_seconds)} 秒"
    else:
        message = f"[vega] {label} 运行中，已用时 {max(0, elapsed_seconds)} 秒"
    typer.echo(
        message,
        err=True,
    )


def make_loop_runtime(workspace: Path) -> LoopAutomationRuntime:
    return LoopAutomationRuntime(
        workspace,
        progress_reporter=report_execution_progress,
    )


def make_review_runtime(workspace: Path) -> ReviewRuntime:
    return ReviewRuntime(
        workspace,
        progress_reporter=report_execution_progress,
    )


def require_repo_directory(repo: Path) -> Path:
    if not repo.exists():
        raise typer.BadParameter(f"目标仓库路径不存在：{redact_text(str(repo))}")
    try:
        resolved = repo.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise typer.BadParameter("无法解析目标仓库路径。") from exc
    if not resolved.is_dir():
        raise typer.BadParameter(f"目标仓库路径必须是目录：{redact_text(str(repo))}")
    return resolved


def read_engineering_change_status(run_dir: Path) -> str:
    """读取兼容 Inspection run 的状态，损坏状态文件一律按未知处理。"""

    state_path = run_dir / "state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    status = payload.get("status")
    return status if isinstance(status, str) and status else "unknown"


def exit_if_failed(run_dir: Path) -> None:
    if read_engineering_change_status(run_dir) == "failed":
        raise typer.Exit(code=1)


def echo_run_status(run_dir: Path) -> None:
    try:
        typer.echo(render_run_status(Path.cwd(), run_dir.name))
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def exit_for_loop_result(
    run_dir: Path,
    *,
    allow_initial_assist_wait: bool,
) -> None:
    status, current_step, automation_mode = read_loop_outcome(run_dir)
    if status == "success":
        return
    if (
        allow_initial_assist_wait
        and automation_mode == "assist"
        and status == "needs_human"
        and current_step == "waiting_for_worker"
    ):
        return
    raise typer.Exit(code=1)


def read_loop_outcome(run_dir: Path) -> tuple[str, str, str]:
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
