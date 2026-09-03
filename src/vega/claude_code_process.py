from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .codex_app_server_process import (
    app_server_process_options,
    install_parent_termination_handler,
    terminate_app_server_tree,
)
from .execution_output import MAX_JSONL_LINE_CHARS
from .execution_process import prepare_subprocess_command
from .redaction import redact_text, redact_value


CLAUDE_PROGRESS_TYPE = "vega.claude_progress"
CLAUDE_RESULT_TYPE = "vega.claude_result"
_MAX_RESULT_CHARS = 1024 * 1024


class ClaudeCodeInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: str
    repo_path: str
    arguments: list[str] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    expected_permission_mode: str


def safe_claude_progress_events(payload: dict[str, object]) -> list[str]:
    """把 Claude JSONL 压缩为不含正文、参数和路径的阶段事件。"""

    event_type = payload.get("type")
    if event_type == "system" and payload.get("subtype") == "init":
        return ["thread_ready", "turn_started"]
    if event_type == "assistant":
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        events: list[str] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name in {"Edit", "Write"}:
                events.append("file_change_started")
            elif name in {"Read", "Glob", "Grep"}:
                events.append("tool_started")
        return list(dict.fromkeys(events))
    if event_type == "user":
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        ):
            return ["tool_completed"]
    return []


def claude_terminal_payload(
    payload: dict[str, object],
) -> dict[str, object] | None:
    if payload.get("type") != "result":
        return None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {
            "status": "error",
            "message": None,
            "error": "Claude Code 终态缺少 Session ID。",
            "session_id": None,
            "turn_id": None,
            "usage": {},
        }
    success = (
        payload.get("subtype") == "success"
        and payload.get("is_error") is False
    )
    message = _structured_message(payload)
    if success and message is None:
        return {
            "status": "error",
            "message": None,
            "error": "Claude Code 终态没有结构化结果。",
            "session_id": session_id,
            "turn_id": _optional_text(payload.get("uuid")),
            "usage": _safe_usage(payload),
        }
    error = None
    if not success:
        raw_error = payload.get("result") or payload.get("error")
        error = (
            redact_text(raw_error)[:2000]
            if isinstance(raw_error, str) and raw_error.strip()
            else f"Claude Code 终态为 {payload.get('subtype') or 'unknown'}。"
        )
    return {
        "status": "success" if success else "error",
        "message": message,
        "error": error,
        "session_id": session_id,
        "turn_id": _optional_text(payload.get("uuid")),
        "usage": _safe_usage(payload),
    }


def claude_init_matches_policy(
    payload: dict[str, object],
    *,
    expected_tools: list[str],
    expected_permission_mode: str,
) -> bool:
    """确认 Claude Code 实际启用的权限和工具面没有越过 Vega 固定边界。"""

    tools = payload.get("tools")
    mcp_servers = payload.get("mcp_servers")
    if not isinstance(tools, list) or not all(
        isinstance(item, str) for item in tools
    ):
        return False
    actual = set(tools)
    expected = set(expected_tools)
    return (
        expected.issubset(actual)
        and not actual - expected - {"StructuredOutput"}
        and payload.get("permissionMode") == expected_permission_mode
        and isinstance(mcp_servers, list)
        and not mcp_servers
    )


def _structured_message(payload: dict[str, object]) -> str | None:
    structured = payload.get("structured_output")
    if structured is not None:
        try:
            message = json.dumps(
                redact_value(structured),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return None
    else:
        raw = payload.get("result")
        if not isinstance(raw, str) or not raw.strip():
            return None
        message = redact_text(raw)
    if not message.strip() or len(message) > _MAX_RESULT_CHARS:
        return None
    return message


def _safe_usage(payload: dict[str, object]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    keys = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    return {
        key: value
        for key in keys
        if isinstance((value := usage.get(key)), int) and value >= 0
    }


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def resolve_claude_executable(executable: str) -> str | None:
    resolved = shutil.which(executable)
    if resolved is None and os.name == "nt":
        resolved = shutil.which(
            executable if executable.lower().endswith(".cmd") else f"{executable}.cmd"
        )
    if resolved is None:
        return None
    path = Path(resolved)
    if os.name != "nt" or path.suffix.lower() != ".cmd":
        return resolved
    native = (
        path.parent
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    return str(native) if native.is_file() else resolved


class _ClaudeCodeClient:
    def __init__(self, invocation: ClaudeCodeInvocation) -> None:
        self.invocation = invocation
        self.process: subprocess.Popen[str] | None = None
        self.terminal: dict[str, object] | None = None
        self.invalid_output = False
        self.permissions_verified = False

    def run(self, prompt: str) -> int:
        command = prepare_subprocess_command(
            [self.invocation.executable, *self.invocation.arguments],
            windows=os.name == "nt",
        )
        exit_code = 1
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.invocation.repo_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **app_server_process_options(windows=os.name == "nt"),
            )
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            self.process.stdin.write(prompt)
            self.process.stdin.close()
            for line in self.process.stdout:
                self._observe_line(line)
            returncode = self.process.wait()
            if self.invalid_output:
                _emit_result(
                    {
                        "status": "error",
                        "error": "Claude Code JSONL 含无效或超长事件。",
                    }
                )
            elif self.terminal is None:
                _emit_result(
                    {
                        "status": "error",
                        "error": (
                            "Claude Code 未返回合法终态；"
                            f"进程退出码 {returncode}。"
                        ),
                    }
                )
            else:
                if (
                    self.terminal.get("status") == "success"
                    and not self.permissions_verified
                ):
                    self.terminal = {
                        **self.terminal,
                        "status": "error",
                        "message": None,
                        "error": "Claude Code 初始化权限与固定工具面不一致。",
                    }
                self.terminal["permissions_verified"] = self.permissions_verified
                _emit_result(self.terminal)
                exit_code = 0 if self.terminal.get("status") == "success" else 1
        except (OSError, ValueError) as exc:
            _emit_result(
                {
                    "status": "error",
                    "error": f"Claude Code 执行失败：{redact_text(str(exc))}",
                }
            )
        finally:
            if not self._shutdown():
                _emit_result(
                    {
                        "status": "error",
                        "error": "Claude Code 进程树终止未确认。",
                    }
                )
                exit_code = 1
        return exit_code

    def _observe_line(self, line: str) -> None:
        if len(line) > MAX_JSONL_LINE_CHARS:
            self.invalid_output = True
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.invalid_output = True
            return
        if not isinstance(payload, dict):
            self.invalid_output = True
            return
        if payload.get("type") == "system" and payload.get("subtype") == "init":
            self.permissions_verified = self._verify_init(payload)
        for event in safe_claude_progress_events(payload):
            _emit_progress(event)
        terminal = claude_terminal_payload(payload)
        if terminal is not None:
            self.terminal = terminal
            _emit_progress(
                "turn_completed"
                if terminal.get("status") == "success"
                else "turn_failed"
            )

    def _verify_init(self, payload: dict[str, object]) -> bool:
        return claude_init_matches_policy(
            payload,
            expected_tools=self.invocation.expected_tools,
            expected_permission_mode=self.invocation.expected_permission_mode,
        )

    def _shutdown(self) -> bool:
        process = self.process
        if process is None:
            return True
        termination = terminate_app_server_tree(process, windows=os.name == "nt")
        return termination.succeeded


def _emit_progress(event: str) -> None:
    print(
        json.dumps(
            {"type": CLAUDE_PROGRESS_TYPE, "event": event},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _emit_result(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            {
                "type": CLAUDE_RESULT_TYPE,
                "status": payload.get("status"),
                "message": payload.get("message"),
                "error": (
                    redact_text(str(payload["error"]))
                    if payload.get("error") is not None
                    else None
                ),
                "session_id": payload.get("session_id"),
                "turn_id": payload.get("turn_id"),
                "permissions_verified": payload.get(
                    "permissions_verified",
                    False,
                ),
                "usage": redact_value(payload.get("usage") or {}),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _helper_main(request_path: Path) -> int:
    install_parent_termination_handler(windows=os.name == "nt")
    try:
        invocation = ClaudeCodeInvocation.model_validate_json(
            request_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        _emit_result(
            {
                "status": "error",
                "error": f"无法读取 Claude Code 请求：{exc}",
            }
        )
        return 2
    prompt = sys.stdin.read()
    if not prompt.strip():
        _emit_result({"status": "error", "error": "Claude Code prompt 为空"})
        return 2
    return _ClaudeCodeClient(invocation).run(prompt)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: python -m vega.claude_code_process <request.json>"
        )
    raise SystemExit(_helper_main(Path(sys.argv[1])))
