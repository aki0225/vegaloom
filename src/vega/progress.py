from __future__ import annotations

import json
import os
import re
import stat
import threading
import unicodedata
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .redaction import redact_text, redact_value

PROGRESS_ARTIFACT = "progress.jsonl"
PROGRESS_VERSION = 2

_EVENT_LABELS = {
    "started": "已启动",
    "checkpoint_planned": "已生成 checkpoint 计划",
    "checkpoint_dispatching": "正在调度 checkpoint",
    "child_run_created": "已创建 child run",
    "child_run_finished": "child run 已结束",
    "child_reconciled": "已重新核对 child run",
    "child_recovery_required": "child run 需要先恢复",
    "checkpoint_done": "checkpoint 已完成",
    "checkpoint_blocked": "checkpoint 已交还人工",
    "running": "运行中",
    "turn_started": "开始模型回合",
    "turn_completed": "完成模型回合",
    "turn_failed": "模型回合失败",
    "thread_ready": "Provider Thread 已就绪",
    "waiting_user": "等待人工响应",
    "user_response_sent": "已发送人工响应",
    "context_compacted": "Provider 已压缩上下文",
    "command_started": "开始执行命令",
    "command_completed": "命令执行完成",
    "command_failed": "命令执行失败",
    "file_change_started": "开始修改文件",
    "file_changed": "完成文件修改",
    "file_change_failed": "文件修改失败",
    "plan_updated": "更新执行计划",
    "subagent_started": "子会话已启动",
    "subagent_updated": "子会话已更新",
    "supervisor_next": "Supervisor 选择 next",
    "supervisor_repair": "Supervisor 选择 repair",
    "supervisor_replan": "Supervisor 选择 replan",
    "supervisor_human": "Supervisor 选择 human",
    "supervisor_finalize": "Supervisor 选择 finalize",
    "agent_paused": "Agent 已暂停",
    "agent_resumed": "Agent 已恢复",
    "agent_stopped": "Agent 已停止",
    "agent_handoff_created": "Agent Handoff 已生成",
    "agent_recovery_blocked": "Agent 恢复已交还人工",
    "review_queue_started": "Review Queue 已启动",
    "review_task_started": "Review Queue 任务已启动",
    "review_task_completed": "Review Queue 任务已完成",
    "review_task_blocked": "Review Queue 任务已阻断",
    "review_queue_completed": "Review Queue 已完成",
    "review_queue_blocked": "Review Queue 已交还人工",
    "tool_started": "开始调用工具",
    "tool_completed": "工具调用完成",
    "tool_failed": "工具调用失败",
    "run_finished": "运行已结束",
}
_SAFE_STEPS = frozenset(
    {
        "agent",
        "goal",
        "loop",
        "reviewer",
        "runner",
        "verification",
        "worker",
    }
)
_SAFE_STATUSES = frozenset(
    {
        "checkpoint_done",
        "failed",
        "needs_human",
        "paused",
        "running",
        "stopped",
        "success",
    }
)
_SAFE_RUN_STATUSES = frozenset(
    {
        "accepted",
        "blocked",
        "checkpoint_done",
        "created",
        "failed",
        "needs_human",
        "paused",
        "rejected",
        "running",
        "stale",
        "stopped",
        "success",
        "timeout",
    }
)
_SAFE_RUN_STEPS = frozenset(
    {
        "brief",
        "budget_exceeded",
        "checkpoint_blocked",
        "checkpoint_dispatching",
        "checkpoint_done",
        "checkpoint_evidence_attached",
        "checkpoint_evidence_reconciled",
        "checkpoint_planned",
        "checkpoint_reconciling",
        "collect",
        "completed",
        "completion_eval_failed",
        "context_budget",
        "child_recovery_required",
        "detect",
        "done",
        "eval",
        "evidence_inconsistent",
        "evidence_stale",
        "evidence_truncated",
        "input_loaded",
        "inspect",
        "knowledge",
        "legacy_workspace_baseline_unavailable",
        "no_diff",
        "paused",
        "plan",
        "project_config_check",
        "project_policy_changed",
        "recovered",
        "recovered_initialization_incomplete",
        "reflect",
        "reflect_failed",
        "report",
        "resolve_source",
        "resumed",
        "review",
        "review_run_failed",
        "reviewer",
        "risk_evaluation",
        "risk_gate_failed",
        "risk_gate_needs_human",
        "run_reviewer",
        "scope_gate",
        "scope_gate_failed",
        "scope_gate_post_verification",
        "scope_gate_post_verification_failed",
        "scope_gate_pre_review",
        "scope_gate_pre_review_failed",
        "stopped",
        "task_loaded",
        "termination_unconfirmed",
        "timed_out",
        "untracked_files",
        "verification_termination_unconfirmed",
        "verify",
        "waiting_for_worker",
        "acting",
        "awaiting_approval",
        "finalizing",
        "observing",
        "planning",
        "ready",
        "worker",
        "worker_context_budget",
        "worker_error",
        "worker_termination_unconfirmed",
        "workspace_baseline",
        "workspace_baseline_dirty",
        "workspace_baseline_unavailable",
        "workspace_changed_before_worker",
        "workspace_changed_during_review",
        "workspace_check",
        "workspace_check_failed",
        "workspace_head_changed",
    }
)
_CHECKPOINT_PATTERN = re.compile(r"[0-9]{1,6}")
_RUN_ID_PATTERN = re.compile(
    r"[0-9]{8}-[0-9]{6}(?:-[0-9]{6})?-[A-Za-z0-9][A-Za-z0-9._-]*"
)
_ANSI_ESCAPE_PATTERN = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\)|"
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_])"
)

ExecutionProgressDelegate = Callable[[str, int], None]


class RunProgressLog:
    """把安全的执行阶段事件追加到单个 run 的本地进度日志。"""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()
        self.path = self.run_dir / PROGRESS_ARTIFACT
        self._lock = threading.Lock()

    def append(
        self,
        step: str,
        event: str = "running",
        *,
        elapsed_seconds: int | None = None,
        iteration: int | None = None,
        checkpoint: str | None = None,
        child_run: str | None = None,
        status: str | None = None,
    ) -> None:
        base_step, _, nested_event = step.partition(".")
        payload: dict[str, Any] = {
            "version": PROGRESS_VERSION,
            "ts": datetime.now(UTC).isoformat(),
            "step": base_step or "runner",
            "event": nested_event or event,
        }
        if elapsed_seconds is not None:
            payload["elapsed_seconds"] = max(0, int(elapsed_seconds))
        if iteration is not None:
            payload["iteration"] = iteration
        if checkpoint is not None:
            payload["checkpoint"] = checkpoint
        if child_run is not None:
            payload["child_run"] = child_run
        if status is not None:
            payload["status"] = status
        with self._lock:
            _append_progress_jsonl(self.run_dir, self.path, payload)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        items: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                if line_number == len(lines) and not raw.endswith(("\n", "\r")):
                    break
                raise ValueError(
                    f"progress.jsonl 第 {line_number} 行不是合法 JSON：{exc.msg}"
                ) from exc
            if not isinstance(item, dict):
                raise ValueError(
                    f"progress.jsonl 第 {line_number} 行不是 JSON object"
                )
            items.append(_sanitize_progress_item(item))
        return items


class ExecutionProgressAdapter:
    """同时保留终端进度和 run-local 进度，不携带模型正文。"""

    def __init__(
        self,
        run_dir: Path,
        delegate: ExecutionProgressDelegate | None = None,
        *,
        iteration: int | None = None,
    ) -> None:
        self.log = RunProgressLog(run_dir)
        self.delegate = delegate
        self.iteration = iteration

    def __call__(self, step: str, elapsed_seconds: int) -> None:
        self.log.append(
            step,
            elapsed_seconds=elapsed_seconds,
            iteration=self.iteration,
        )
        if self.delegate is not None:
            self.delegate(step, elapsed_seconds)


def render_progress_items(items: Iterable[dict[str, Any]], *, limit: int = 20) -> str:
    """渲染白名单字段，避免 watch 把任意 artifact 内容当作终端日志。"""

    selected = [
        _sanitize_progress_item(item)
        for item in list(items)[-max(0, limit) :]
    ]
    if not selected:
        return "暂无安全进度事件。"
    lines: list[str] = []
    for item in selected:
        step = str(item.get("step") or "runner")
        event = str(item.get("event") or "running")
        label = _EVENT_LABELS.get(event, event)
        details = [step, label]
        iteration = item.get("iteration")
        if isinstance(iteration, int):
            details.append(f"iteration={iteration}")
        checkpoint = item.get("checkpoint")
        if isinstance(checkpoint, str) and checkpoint:
            details.append(f"checkpoint={checkpoint}")
        child_run = item.get("child_run")
        if isinstance(child_run, str) and child_run:
            details.append(f"child={child_run}")
        elapsed = item.get("elapsed_seconds")
        if isinstance(elapsed, int):
            details.append(f"{elapsed}s")
        status = item.get("status")
        if isinstance(status, str) and status:
            details.append(f"status={status}")
        lines.append("- " + " / ".join(details))
    return "\n".join(lines)


def _sanitize_progress_item(item: dict[str, Any]) -> dict[str, Any]:
    """读取端再次白名单化，避免被篡改的本地 artifact 进入终端或 JSON 输出。"""

    step = item.get("step")
    event = item.get("event")
    safe: dict[str, Any] = {
        "version": (
            item["version"]
            if isinstance(item.get("version"), int)
            and not isinstance(item.get("version"), bool)
            else PROGRESS_VERSION
        ),
        "step": step if isinstance(step, str) and step in _SAFE_STEPS else "runner",
        "event": (
            event
            if isinstance(event, str) and event in _EVENT_LABELS
            else "running"
        ),
    }
    timestamp = _safe_progress_timestamp(item.get("ts"))
    if timestamp:
        safe["ts"] = timestamp
    for key in ("elapsed_seconds", "iteration"):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[key] = value
    checkpoint = _safe_progress_identifier(
        item.get("checkpoint"),
        max_length=32,
        pattern=_CHECKPOINT_PATTERN,
    )
    if checkpoint:
        safe["checkpoint"] = checkpoint
    child_run = _safe_progress_identifier(
        item.get("child_run"),
        max_length=160,
        pattern=_RUN_ID_PATTERN,
    )
    if child_run:
        safe["child_run"] = child_run
    status = item.get("status")
    if isinstance(status, str) and status in _SAFE_STATUSES:
        safe["status"] = status
    return safe


def _safe_progress_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    safe = _ANSI_ESCAPE_PATTERN.sub("", redact_text(value))
    safe = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in safe
    )
    safe = " ".join(safe.split())
    return safe[:max_length]


def _safe_progress_identifier(
    value: object,
    *,
    max_length: int,
    pattern: re.Pattern[str],
) -> str:
    safe = _safe_progress_text(value, max_length=max_length)
    if safe == "[REDACTED]":
        return safe
    return safe if pattern.fullmatch(safe) else ""


def _safe_progress_timestamp(value: object) -> str:
    safe = _safe_progress_text(value, max_length=80)
    if not safe:
        return ""
    try:
        datetime.fromisoformat(safe)
    except ValueError:
        return ""
    return safe


def safe_run_status(value: object) -> str:
    return value if isinstance(value, str) and value in _SAFE_RUN_STATUSES else "unknown"


def safe_run_step(value: object) -> str:
    return value if isinstance(value, str) and value in _SAFE_RUN_STEPS else "unknown"


def safe_run_id(value: object) -> str:
    return _safe_progress_identifier(
        value,
        max_length=160,
        pattern=_RUN_ID_PATTERN,
    ) or "unknown-run"


def _append_progress_jsonl(
    run_dir: Path,
    path: Path,
    payload: dict[str, Any],
) -> None:
    if path.parent.resolve(strict=True) != run_dir:
        raise ValueError("progress.jsonl 写入路径越过 run 边界")
    if os.path.lexists(path) and _is_link_or_reparse_point(path):
        raise ValueError("progress.jsonl 不能是符号链接或 reparse point")
    flags = (
        os.O_APPEND
        | os.O_CREAT
        | os.O_WRONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise ValueError("progress.jsonl 必须是普通文件")
        if descriptor_stat.st_nlink != 1:
            raise ValueError("progress.jsonl 不能是 hardlink")
        if _is_link_or_reparse_point(path):
            raise ValueError("progress.jsonl 不能是符号链接或 reparse point")
        path_stat = path.stat()
        descriptor_stat = os.fstat(descriptor)
        if (
            descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_ino != path_stat.st_ino
            or descriptor_stat.st_nlink != 1
        ):
            raise ValueError("progress.jsonl 在写入期间被替换")
        data = (
            json.dumps(redact_value(payload), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("progress.jsonl 追加写入未取得进展")
            offset += written
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(file_attributes & reparse_flag)


def make_execution_progress_reporter(
    run_dir: Path,
    delegate: ExecutionProgressDelegate | None,
    *,
    iteration: int | None = None,
) -> ExecutionProgressAdapter:
    return ExecutionProgressAdapter(run_dir, delegate, iteration=iteration)


def notify_run_created(callback: Callable[[Path], None] | None, run_dir: Path) -> None:
    if callback is not None:
        callback(run_dir)
