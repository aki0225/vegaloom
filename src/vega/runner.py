from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from .execution_control import RunnerExecutionContext, run_owned_process
from .project_config import CodexExecOptions
from .redaction import redact_text


RunnerStatus = Literal["success", "error", "timed_out", "stopped", "skipped"]
_MAX_FINAL_JSONL_LINE_CHARS = 4 * 1024 * 1024


@dataclass
class RunnerResult:
    status: RunnerStatus
    output: str
    error: str | None = None
    command: list[str] | None = None
    termination_unconfirmed: bool = False

    def __post_init__(self) -> None:
        self.output = redact_text(self.output)
        self.error = redact_text(self.error) if self.error is not None else None
        if self.command is not None:
            self.command = [redact_text(item) for item in self.command]


@dataclass(frozen=True)
class _FinalMessageScan:
    message: str | None
    complete: bool


class Runner(Protocol):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        ...


class _CodexJsonlProgress:
    """从 Codex JSONL 中提取最终消息，并只发出无正文的安全进度事件。"""

    _EVENT_TYPES = {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "item.started",
        "item.updated",
        "item.completed",
        "error",
    }

    def __init__(self, context: RunnerExecutionContext) -> None:
        self._reporter = context.progress_reporter
        self._step = context.step if context.step in {"worker", "reviewer"} else "runner"
        self._started_at = time.monotonic()

    def observe(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        event_type = event.get("type")
        if not isinstance(event_type, str) or event_type not in self._EVENT_TYPES:
            return
        if event_type == "turn.started":
            self._emit("turn_started")
        elif event_type == "turn.completed":
            self._emit("turn_completed")
        elif event_type in {"turn.failed", "error"}:
            self._emit("turn_failed")
        elif event_type.startswith("item."):
            self._observe_item(event_type, event.get("item"))

    def _observe_item(self, event_type: str, item: object) -> None:
        if not isinstance(item, dict):
            return
        item_type = item.get("type")
        if not isinstance(item_type, str):
            return
        if event_type == "item.completed" and item_type == "agent_message":
            return
        handlers = {
            "command_execution": self._observe_command,
            "file_change": self._observe_file_change,
            "todo_list": self._observe_plan,
            "mcp_tool_call": self._observe_tool,
            "collab_tool_call": self._observe_tool,
            "web_search": self._observe_tool,
        }
        handler = handlers.get(item_type)
        if handler is not None:
            handler(event_type, item)

    def _observe_command(self, event_type: str, item: dict[str, object]) -> None:
        if event_type == "item.started":
            self._emit("command_started")
        elif event_type == "item.completed":
            status = item.get("status")
            if status is not None and not isinstance(status, str):
                return
            failed = isinstance(status, str) and status in {"failed", "declined"}
            self._emit("command_failed" if failed else "command_completed")

    def _observe_file_change(self, event_type: str, item: dict[str, object]) -> None:
        if event_type != "item.completed":
            return
        status = item.get("status")
        if status is not None and not isinstance(status, str):
            return
        failed = isinstance(status, str) and status == "failed"
        self._emit("file_change_failed" if failed else "file_changed")

    def _observe_plan(self, event_type: str, item: dict[str, object]) -> None:
        del item
        if event_type in {"item.started", "item.updated"}:
            self._emit("plan_updated")

    def _observe_tool(self, event_type: str, item: dict[str, object]) -> None:
        if event_type == "item.started":
            self._emit("tool_started")
        elif event_type == "item.completed":
            status = item.get("status")
            if status is not None and not isinstance(status, str):
                return
            failed = isinstance(status, str) and status == "failed"
            self._emit("tool_failed" if failed else "tool_completed")

    def _emit(self, event: str) -> None:
        reporter = self._reporter
        if reporter is None:
            return
        try:
            reporter(f"{self._step}.{event}", int(time.monotonic() - self._started_at))
        except Exception:  # noqa: BLE001 - 进度输出失败不能改变 runner 结果
            self._reporter = None


def _extract_final_agent_message(output: str) -> _FinalMessageScan:
    """从完整 JSONL 输出提取最后一条合法 agent_message，并标记扫描完整性。"""

    final_message: str | None = None
    complete = True
    start = 0
    output_length = len(output)
    while start < output_length:
        newline = output.find("\n", start)
        line_end = output_length if newline < 0 else newline + 1
        line_length = line_end - start
        # 先检查长度再切片，避免异常巨型单行产生不受控的临时副本。
        if line_length > _MAX_FINAL_JSONL_LINE_CHARS:
            complete = False
            if newline < 0:
                break
            start = newline + 1
            continue
        line = output[start:line_end]
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(event, dict) and event.get("type") == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        final_message = text
        if newline < 0:
            break
        start = newline + 1
    return _FinalMessageScan(message=final_message, complete=complete)


class NoneRunner:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        return RunnerResult(
            status="skipped",
            output="",
            error="runner=none，仅生成 prompt，不调用外部 AI。",
            command=[],
        )


class CodexExecRunner:
    """通过 codex exec 启动短生命周期隔离会话。

    reviewer 默认使用 read-only sandbox；worker 只在 auto 模式中使用 workspace-write。
    这里不传 bypass sandbox / bypass approval，避免把自动 loop 变成无边界执行器。
    """

    def __init__(
        self,
        executable: str = "codex",
        options: CodexExecOptions | None = None,
    ) -> None:
        self.executable = executable
        self.options = options or CodexExecOptions()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        resolved = shutil.which(self.executable)
        if not resolved:
            return RunnerResult(
                status="error",
                output="",
                error=f"未找到 {self.executable}，无法启动 codex exec。",
                command=[self.executable, "exec"],
            )

        command = [
            resolved,
            "exec",
            "--cd",
            str(repo_path.resolve()),
            "--sandbox",
            sandbox,
            "--config",
            "notify=[]",
            "--disable",
            "hooks",
            "--disable",
            "memories",
            "--disable",
            "plugins",
        ]
        if self.options.profile:
            command.extend(["--profile", self.options.profile])
        if self.options.model:
            command.extend(["--model", self.options.model])
        if self.options.reasoning_effort:
            command.extend(
                [
                    "--config",
                    f'model_reasoning_effort="{self.options.reasoning_effort}"',
                ]
            )
        if self.options.ephemeral:
            command.append("--ephemeral")
        command.append("--json")
        command.append("-")
        prompt = redact_text(prompt)
        standalone_root = Path.cwd()
        context = execution_context or RunnerExecutionContext(
            execution_root=standalone_root,
            execution_dir=standalone_root
            / "runs"
            / "_standalone-executions"
            / f"codex-{uuid4().hex[:12]}",
            run_id="standalone-runner",
            step="codex-exec",
        )
        progress = _CodexJsonlProgress(context)
        context = replace(context, output_line_observer=progress.observe)
        result = run_owned_process(
            command,
            prompt,
            repo_path,
            timeout_seconds,
            context,
        )
        status = result.status
        error = result.error
        final_scan = _extract_final_agent_message(result.output)
        output = final_scan.message if final_scan.complete and final_scan.message else ""
        if status == "success" and (
            final_scan.message is None or not final_scan.complete
        ):
            status = "error"
            error = (
                "codex exec JSONL 包含超出终态扫描上限的行，无法确认最终 agent_message。"
                if not final_scan.complete
                else "codex exec JSONL 未包含最终 agent_message。"
            )
        return RunnerResult(
            status=status,
            output=output,
            error=error,
            command=command,
            termination_unconfirmed=getattr(result, "termination_unconfirmed", False),
        )


def make_runner(name: str, options: CodexExecOptions | None = None) -> Runner:
    normalized = name.strip().lower()
    if normalized in {"none", "prompt-only"}:
        return NoneRunner()
    if normalized in {"codex-exec", "codex"}:
        return CodexExecRunner(options=options)
    raise ValueError(f"不支持的 runner：{name}")
