from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .codex_app_server_process import AppServerInvocation
from .codex_mcp_isolation import (
    CodexMcpIsolationError,
    build_mcp_disable_overrides,
)
from .execution_control import RunnerExecutionContext, run_owned_process
from .execution_output import MAX_JSONL_LINE_CHARS
from .execution_paths import ExecutionPathGuard
from .project_config import CodexExecOptions
from .provider_session import (
    ensure_session_handle,
    load_provider_sessions,
    mutate_provider_sessions,
)
from .redaction import redact_text
from .runner import RunnerResult


_RESULT_TYPE = "vega.app_server_result"
_PROGRESS_TYPE = "vega.app_server_progress"
_TASK_ANCHOR_SOFT_BYTES = 32 * 1024


class CodexAppServerRunner:
    """通过短生命周期 App Server 进程复用持久 Codex Thread。"""

    def __init__(
        self,
        run_dir: Path,
        role_key: str,
        *,
        work_item_id: str | None,
        contract_revision: int | None,
        plan_revision: int | None,
        output_schema: dict[str, Any] | None = None,
        executable: str = "codex",
        isolate_reviewer: bool = False,
        options: CodexExecOptions | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.role_key = role_key
        self.work_item_id = work_item_id
        self.contract_revision = contract_revision
        self.plan_revision = plan_revision
        self.output_schema = output_schema
        self.executable = executable
        self.isolate_reviewer = isolate_reviewer
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
        if execution_context is None:
            raise ValueError("App Server Runner 必须绑定 execution context")
        if self.options.ephemeral:
            return RunnerResult(
                status="error",
                output="",
                error="持久 App Server Thread 不支持 ephemeral；请显式使用 --fresh-session。",
                command=[self.executable, "app-server"],
            )
        resolved = shutil.which(self.executable)
        if resolved is None:
            return RunnerResult(
                status="error",
                output="",
                error="未找到 codex，无法启动 App Server。",
                command=[self.executable, "app-server"],
            )
        try:
            prompt = self._prompt_with_pending_anchor(prompt)
            self._prepare_handle()
            request_path = execution_context.execution_dir / "app-server-request.json"
            guard = ExecutionPathGuard(
                execution_context.execution_root,
                execution_context.execution_dir,
            )
            guard.prepare()
            guard.validate_artifact(request_path)
            invocation = AppServerInvocation(
                executable=resolved,
                run_dir=str(self.run_dir),
                role_key=self.role_key,
                repo_path=str(repo_path.resolve()),
                sandbox=sandbox,
                output_schema=_strict_output_schema(self.output_schema),
                model=self.options.model,
                reasoning_effort=self.options.reasoning_effort,
                global_args=self._global_args(),
                server_args=self._server_args(resolved, repo_path),
            )
            request_path.write_text(
                invocation.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except (CodexMcpIsolationError, OSError, ValueError) as exc:
            return RunnerResult(
                status="error",
                output="",
                error=f"App Server 启动准备失败：{exc}",
                command=[resolved, "app-server"],
            )

        command = [
            sys.executable,
            "-m",
            "vega.codex_app_server",
            str(request_path.resolve()),
        ]
        observer = _AppServerProgress(execution_context)
        context = RunnerExecutionContext(
            execution_root=execution_context.execution_root,
            execution_dir=execution_context.execution_dir,
            run_id=execution_context.run_id,
            step=execution_context.step,
            execution_id=execution_context.execution_id,
            iteration=execution_context.iteration,
            heartbeat_interval_seconds=execution_context.heartbeat_interval_seconds,
            lease_timeout_seconds=execution_context.lease_timeout_seconds,
            terminate_grace_seconds=execution_context.terminate_grace_seconds,
            progress_reporter=execution_context.progress_reporter,
            output_line_observer=observer.observe,
            capture_stderr_separately=True,
        )
        owned = run_owned_process(
            command,
            prompt,
            repo_path.resolve(),
            timeout_seconds,
            context,
            environment={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            },
        )
        parsed = _extract_helper_result(owned.output)
        if owned.status != "success":
            self._mark_unavailable()
            return RunnerResult(
                status=owned.status,
                output="",
                error=owned.error,
                command=command,
                termination_unconfirmed=owned.termination_unconfirmed,
            )
        if parsed is None:
            self._mark_unavailable()
            return RunnerResult(
                status="error",
                output="",
                error="App Server helper 未返回合法终态。",
                command=command,
            )
        status = parsed.get("status")
        message = parsed.get("message")
        error = parsed.get("error")
        if status != "success" or not isinstance(message, str) or not message.strip():
            self._mark_unavailable()
            return RunnerResult(
                status="error" if status != "stopped" else "stopped",
                output="",
                error=str(error or "App Server Turn 未形成最终 agentMessage。"),
                command=command,
            )
        return RunnerResult(
            status="success",
            output=redact_text(message),
            error=None,
            command=command,
        )

    def _global_args(self) -> list[str]:
        if self.options.profile:
            return ["--profile", self.options.profile]
        return []

    def _server_args(self, resolved: str, repo_path: Path) -> list[str]:
        args: list[str] = []
        if not self.isolate_reviewer:
            return args
        args.extend([
            "--disable",
            "hooks",
            "--disable",
            "memories",
            "--disable",
            "plugins",
        ])
        for override in build_mcp_disable_overrides(
            resolved,
            repo_path,
            profile=self.options.profile,
        ):
            args.extend(["--config", override])
        return args

    def _prepare_handle(self) -> None:
        def mutation(state) -> None:
            handle = ensure_session_handle(
                state,
                self.role_key,
                work_item_id=self.work_item_id,
                contract_revision=self.contract_revision,
                plan_revision=self.plan_revision,
            )
            if handle.owner != "vega":
                raise ValueError("Provider Session 当前由人工接管")

        mutate_provider_sessions(self.run_dir, "agent.session", mutation)

    def _prompt_with_pending_anchor(self, prompt: str) -> str:
        state = load_provider_sessions(self.run_dir)
        handle = state.handles.get(self.role_key)
        if handle is None or not handle.compaction_pending:
            return prompt
        return f"{prompt.rstrip()}\n\n{_build_task_anchor(self.run_dir)}\n"

    def _mark_unavailable(self) -> None:
        def mutation(state) -> None:
            handle = state.handles.get(self.role_key)
            if handle is not None:
                handle.lifecycle = "unavailable"
                handle.last_event = "runner_failed"

        try:
            mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        except (OSError, ValueError):
            pass


class _AppServerProgress:
    def __init__(self, context: RunnerExecutionContext) -> None:
        self.reporter = context.progress_reporter
        self.step = context.step
        self.started = time.monotonic()

    def observe(self, line: str) -> None:
        if self.reporter is None or len(line) > MAX_JSONL_LINE_CHARS:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get("type") != _PROGRESS_TYPE:
            return
        event = payload.get("event")
        if not isinstance(event, str):
            return
        try:
            self.reporter(
                f"{self.step}.{event}",
                int(time.monotonic() - self.started),
            )
        except Exception:  # noqa: BLE001 - 展示失败不能改变执行结果
            self.reporter = None


def _extract_helper_result(output: str) -> dict[str, object] | None:
    result: dict[str, object] | None = None
    for line in output.splitlines():
        if len(line) > MAX_JSONL_LINE_CHARS:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == _RESULT_TYPE:
            result = payload
    return result


def _strict_output_schema(value: object) -> object:
    """补齐 Structured Outputs 要求的完整 required 列表。"""

    if isinstance(value, list):
        return [_strict_output_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: _strict_output_schema(item)
        for key, item in value.items()
    }
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["required"] = list(properties)
        normalized.setdefault("additionalProperties", False)
    return normalized


def _build_task_anchor(run_dir: Path) -> str:
    state_summary = "状态 Artifact 无法读取"
    try:
        envelope = json.loads(
            (run_dir / "agent-state.json").read_text(encoding="utf-8")
        )
        data = envelope.get("data", {}) if isinstance(envelope, dict) else {}
        if isinstance(data, dict):
            state_summary = "\n".join(
                [
                    f"- run：{data.get('run_id')}",
                    f"- contract revision：{data.get('contract_revision')}",
                    f"- plan revision：{data.get('execution_plan_revision')}",
                    f"- Work Item：{data.get('current_work_item')}",
                    f"- accepted checkpoint：{data.get('accepted_checkpoint_sha')}",
                    f"- active candidate：{data.get('active_candidate_sha')}",
                    f"- phase：{data.get('phase')}",
                ]
            )
    except (OSError, json.JSONDecodeError):
        pass
    try:
        brief = (run_dir / "task-brief.md").read_text(encoding="utf-8").strip()
    except OSError:
        brief = "完整 Task Brief 暂时无法读取。"
    prefix = (
        "## Vega Task Anchor\n\n"
        "以下内容用于上下文压缩后的任务定位，不覆盖 Change Contract、项目规则或当前 Git 事实。\n\n"
        f"{state_summary}\n\n"
        "### 当前 Task Brief\n\n"
    )
    budget = max(0, _TASK_ANCHOR_SOFT_BYTES - len(prefix.encode("utf-8")) - 256)
    brief_bytes = brief.encode("utf-8")
    if len(brief_bytes) > budget:
        brief = brief_bytes[:budget].decode("utf-8", errors="ignore").rstrip()
        brief += "\n\n[内容达到 32 KiB 软上限；完整内容见 task-brief.md]"
    return prefix + brief
