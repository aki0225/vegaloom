from __future__ import annotations

import shutil
from pathlib import Path

import typer

from .redaction import redact_text


_PROGRESS_STEP_LABELS = {
    "worker": "worker",
    "reviewer": "reviewer",
    "verification": "verification",
    "runner": "runner",
}
_PROGRESS_EVENT_MESSAGES = {
    "thread_ready": "Provider Thread 已就绪",
    "turn_started": "开始分析任务",
    "waiting_user": "等待人工响应",
    "user_response_sent": "已发送人工响应",
    "context_compacted": "已压缩会话上下文",
    "command_started": "开始执行命令",
    "command_completed": "命令执行完成",
    "command_failed": "命令执行失败",
    "file_change_started": "开始修改文件",
    "file_changed": "已应用文件修改",
    "file_change_failed": "文件修改失败",
    "plan_updated": "更新执行计划",
    "subagent_started": "已启动子会话",
    "subagent_updated": "子会话状态已更新",
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
    typer.echo(message, err=True)


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


def ensure_runner_ready(runner: str, role: str) -> None:
    if runner.strip().lower() not in {"codex", "codex-exec"}:
        raise typer.BadParameter(f"{role} 只支持 Codex CLI")
    if shutil.which("codex"):
        return
    raise typer.BadParameter(
        f"{role} 需要 Codex CLI，但当前 PATH 中未找到 codex；请先安装并登录。"
    )
