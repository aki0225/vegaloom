from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent_contract_support import utc_now
from .claude_code_process import (
    CLAUDE_PROGRESS_TYPE,
    CLAUDE_RESULT_TYPE,
    ClaudeCodeInvocation,
    resolve_claude_executable,
)
from .execution_control import RunnerExecutionContext, run_owned_process
from .execution_output import MAX_JSONL_LINE_CHARS
from .execution_paths import ExecutionPathGuard
from .project_config import ClaudeCodeOptions
from .provider_session import (
    PendingSteer,
    ensure_session_handle,
    mutate_provider_sessions,
)
from .redaction import redact_text
from .runner import RunnerResult


_PROVIDER = "claude-code"
_READ_ONLY_TOOLS = "Read,Glob,Grep"
_WRITER_TOOLS = "Read,Glob,Grep,Edit,Write"


class ClaudeCodeRunner:
    """通过 Claude Code CLI 执行受控 Turn，并只保留脱敏后的结构化终态。"""

    def __init__(
        self,
        run_dir: Path,
        role_key: str,
        *,
        work_item_id: str | None,
        contract_revision: int | None,
        plan_revision: int | None,
        output_schema: dict[str, Any] | None = None,
        executable: str = "claude",
        persistent_session: bool = True,
        options: ClaudeCodeOptions | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.role_key = role_key
        self.work_item_id = work_item_id
        self.contract_revision = contract_revision
        self.plan_revision = plan_revision
        self.output_schema = output_schema
        self.executable = executable
        self.persistent_session = persistent_session
        self.options = options or ClaudeCodeOptions()

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
            raise ValueError("Claude Code Runner 必须绑定 execution context")
        if sandbox not in {"read-only", "workspace-write"}:
            return RunnerResult(
                status="error",
                output="",
                error=f"Claude Code Provider 不支持 sandbox={sandbox}",
                command=[self.executable, "-p"],
            )
        resolved = resolve_claude_executable(self.executable)
        if resolved is None:
            return RunnerResult(
                status="error",
                output="",
                error="未找到 Claude Code CLI；请先安装并登录。",
                command=[self.executable, "-p"],
            )
        session_id: str | None = None
        queued: list[PendingSteer] = []
        resume_session = False
        try:
            session_id, queued, resume_session = self._prepare_handle(sandbox)
            prompt = _prompt_with_queued_steers(prompt, queued)
            expected_tools = _tools_for_sandbox(sandbox)
            permission_mode = _permission_mode_for_sandbox(sandbox)
            invocation = ClaudeCodeInvocation(
                executable=resolved,
                repo_path=str(repo_path.resolve()),
                arguments=self._arguments(
                    sandbox,
                    session_id=session_id,
                    resume_session=resume_session,
                ),
                expected_tools=expected_tools.split(","),
                expected_permission_mode=permission_mode,
            )
            request_path = (
                execution_context.execution_dir / "claude-code-request.json"
            )
            guard = ExecutionPathGuard(
                execution_context.execution_root,
                execution_context.execution_dir,
            )
            guard.prepare()
            guard.validate_artifact(request_path)
            request_path.write_text(
                invocation.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except (OSError, TypeError, ValueError) as exc:
            self._mark_preflight_failed(resume_session=resume_session)
            return RunnerResult(
                status="error",
                output="",
                error=f"Claude Code 启动准备失败：{exc}",
                command=[resolved, "-p"],
            )

        command = [
            sys.executable,
            "-m",
            "vega.claude_code_process",
            str(request_path.resolve()),
        ]
        observer = _ClaudeProgress(execution_context)
        context = replace(
            execution_context,
            output_line_observer=observer.observe,
            capture_stderr_separately=True,
        )
        owned = run_owned_process(
            command,
            redact_text(prompt),
            repo_path.resolve(),
            timeout_seconds,
            context,
            environment={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            },
        )
        parsed = _extract_claude_result(owned.output)
        if parsed is None:
            self._complete_handle(
                status=owned.status,
                session_id=session_id,
                queued=queued,
                usage={},
                session_confirmed=False,
                turn_id=None,
            )
            return RunnerResult(
                status=owned.status if owned.status != "success" else "error",
                output="",
                error=owned.error or "Claude Code helper 未返回合法终态。",
                command=command,
                termination_unconfirmed=owned.termination_unconfirmed,
            )
        helper_status = parsed.get("status")
        parsed_session_id = parsed.get("session_id")
        if (
            self.persistent_session
            and parsed_session_id is not None
            and parsed_session_id != session_id
        ):
            self._complete_handle(
                status="error",
                session_id=session_id,
                queued=queued,
                usage={},
                session_confirmed=False,
                turn_id=None,
            )
            return RunnerResult(
                status="error",
                output="",
                error="Claude Code 返回了不同的 Session ID，拒绝采用结果。",
                command=command,
            )
        permissions_verified = parsed.get("permissions_verified") is True
        result_status = (
            "success"
            if (
                owned.status == "success"
                and helper_status == "success"
                and permissions_verified
            )
            else "stopped"
            if owned.status == "stopped"
            else "timed_out"
            if owned.status == "timed_out"
            else "error"
        )
        usage = parsed.get("usage")
        self._complete_handle(
            status=result_status,
            session_id=(
                parsed_session_id
                if isinstance(parsed_session_id, str)
                else session_id
            ),
            queued=queued,
            usage=usage if isinstance(usage, dict) else {},
            session_confirmed=isinstance(parsed_session_id, str),
            turn_id=(
                parsed.get("turn_id")
                if isinstance(parsed.get("turn_id"), str)
                else None
            ),
            permissions_verified=permissions_verified,
        )
        message = parsed.get("message")
        error = parsed.get("error")
        if result_status != "success" or not isinstance(message, str):
            return RunnerResult(
                status=result_status,
                output="",
                error=(
                    redact_text(error)
                    if isinstance(error, str) and error.strip()
                    else owned.error or "Claude Code Turn 未形成结构化结果。"
                ),
                command=command,
                termination_unconfirmed=owned.termination_unconfirmed,
            )
        return RunnerResult(
            status="success",
            output=redact_text(message),
            command=command,
        )

    def _arguments(
        self,
        sandbox: str,
        *,
        session_id: str | None,
        resume_session: bool,
    ) -> list[str]:
        tools = _tools_for_sandbox(sandbox)
        permission_mode = _permission_mode_for_sandbox(sandbox)
        arguments = [
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
            "--safe-mode",
            "--permission-mode",
            permission_mode,
            "--tools",
            tools,
            "--allowed-tools",
            tools,
            "--no-chrome",
            "--disable-slash-commands",
            "--prompt-suggestions",
            "false",
            "--exclude-dynamic-system-prompt-sections",
        ]
        if self.output_schema is not None:
            arguments.extend(
                [
                    "--json-schema",
                    json.dumps(
                        self.output_schema,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
        if self.options.model:
            arguments.extend(["--model", self.options.model])
        if self.options.effort:
            arguments.extend(["--effort", self.options.effort])
        if self.persistent_session:
            assert session_id is not None
            if resume_session:
                arguments.extend(["--resume", session_id])
            else:
                arguments.extend(["--session-id", session_id])
        else:
            arguments.append("--no-session-persistence")
        return arguments

    def _prepare_handle(
        self,
        sandbox: str,
    ) -> tuple[str | None, list[PendingSteer], bool]:
        session_id: str | None = None
        queued: list[PendingSteer] = []
        resume_session = False

        def mutation(state) -> None:
            nonlocal session_id, queued, resume_session
            handle = ensure_session_handle(
                state,
                self.role_key,
                provider=_PROVIDER,
                work_item_id=self.work_item_id,
                contract_revision=self.contract_revision,
                plan_revision=self.plan_revision,
            )
            if handle.owner != "vega":
                raise ValueError("Provider Session 当前由人工接管")
            if handle.lifecycle in {"active", "waiting_user"}:
                raise ValueError("Provider Session 已有活动 Turn")
            if self.persistent_session:
                resume_session = handle.thread_id is not None
                session_id = handle.thread_id or str(uuid4())
                handle.thread_id = session_id
            handle.lifecycle = "active"
            handle.sandbox = sandbox
            handle.approval_policy = (
                _permission_mode_for_sandbox(sandbox)
            )
            handle.permissions_verified = False
            handle.turn_count += 1
            handle.last_event = "turn_preparing"
            handle.updated_at = utc_now()
            queued = [
                item.model_copy(deep=True)
                for item in state.steers
                if item.role_key == self.role_key and item.status == "queued"
            ]

        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        return session_id, queued, resume_session

    def _complete_handle(
        self,
        *,
        status: str,
        session_id: str | None,
        queued: list[PendingSteer],
        usage: dict[str, object],
        session_confirmed: bool,
        turn_id: str | None,
        permissions_verified: bool = False,
    ) -> None:
        def mutation(state) -> None:
            handle = state.handles.get(self.role_key)
            if handle is None:
                return
            if self.persistent_session and session_confirmed and session_id is not None:
                handle.thread_id = session_id
            elif (
                self.persistent_session
                and not session_confirmed
                and handle.turn_count == 1
            ):
                # 首次会话未返回 Session ID 时不能猜测该 Session 已经可恢复。
                handle.thread_id = None
            handle.lifecycle = "idle" if status == "success" else "unavailable"
            handle.last_event = (
                "turn_completed" if status == "success" else f"turn_{status}"
            )
            handle.last_turn_id = turn_id
            handle.permissions_verified = permissions_verified
            handle.updated_at = utc_now()
            input_tokens = _non_negative_int(usage.get("input_tokens"))
            cache_create = _non_negative_int(
                usage.get("cache_creation_input_tokens")
            )
            cached = _non_negative_int(usage.get("cache_read_input_tokens"))
            output_tokens = _non_negative_int(usage.get("output_tokens"))
            values = [
                value
                for value in (input_tokens, cache_create, cached, output_tokens)
                if value is not None
            ]
            handle.total_tokens = sum(values) if values else None
            handle.cached_input_tokens = cached
            queued_ids = {item.steer_id for item in queued}
            for steer in state.steers:
                if steer.steer_id in queued_ids and steer.status == "queued":
                    steer.status = (
                        "delivered" if session_confirmed else "rejected"
                    )
                    steer.delivered_turn_id = turn_id
                    steer.result_note = (
                        "随下一次 Claude Code Turn 输入发送"
                        if session_confirmed
                        else "Turn 终态未确认，无法证明补充指令已经送达"
                    )

        try:
            mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        except (OSError, ValueError):
            pass

    def _mark_preflight_failed(self, *, resume_session: bool) -> None:
        def mutation(state) -> None:
            handle = state.handles.get(self.role_key)
            if handle is not None:
                if self.persistent_session and not resume_session:
                    handle.thread_id = None
                handle.turn_count = max(0, handle.turn_count - 1)
                handle.lifecycle = "unavailable"
                handle.last_event = "runner_preflight_failed"
                handle.updated_at = utc_now()

        try:
            mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        except (OSError, ValueError):
            pass


class _ClaudeProgress:
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
        if not isinstance(payload, dict) or payload.get("type") != CLAUDE_PROGRESS_TYPE:
            return
        event = payload.get("event")
        if not isinstance(event, str):
            return
        try:
            self.reporter(
                f"{self.step}.{event}",
                int(time.monotonic() - self.started),
            )
        except Exception:  # noqa: BLE001 - 展示失败不能改变 Provider 结果
            self.reporter = None


def _extract_claude_result(output: str) -> dict[str, object] | None:
    result: dict[str, object] | None = None
    for line in output.splitlines():
        if len(line) > MAX_JSONL_LINE_CHARS:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == CLAUDE_RESULT_TYPE:
            result = payload
    return result


def _prompt_with_queued_steers(
    prompt: str,
    queued: list[PendingSteer],
) -> str:
    if not queued:
        return prompt
    additions = "\n".join(f"- {item.instruction}" for item in queued)
    return f"{prompt.rstrip()}\n\n## 用户补充指令\n{additions}\n"


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _tools_for_sandbox(sandbox: str) -> str:
    return _READ_ONLY_TOOLS if sandbox == "read-only" else _WRITER_TOOLS


def _permission_mode_for_sandbox(sandbox: str) -> str:
    return "dontAsk" if sandbox == "read-only" else "acceptEdits"
