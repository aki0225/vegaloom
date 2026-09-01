from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
from . import __version__
from .agent_contract_support import utc_now
from .codex_app_server_process import (
    AppServerInvocation,
    app_server_process_options,
    install_parent_termination_handler,
    terminate_app_server_tree,
)
from .codex_app_server_permissions import require_thread_permissions
from .codex_app_server_rpc import (
    CODEX_SUPPORTED_SERVER_REQUESTS,
    CodexAppServerRpc,
    codex_initialize_capabilities,
)
from .execution_process import prepare_subprocess_command
from .provider_session import (
    PendingInteraction, load_provider_sessions, mutate_provider_sessions,
    summarize_provider_interaction,
)
from .redaction import redact_text
_MAX_AGENT_MESSAGE_CHARS = 1024 * 1024
class _AppServerClient:
    def __init__(self, invocation: AppServerInvocation) -> None:
        self.invocation = invocation
        self.run_dir = Path(invocation.run_dir).resolve()
        self.process: subprocess.Popen[str] | None = None
        self.rpc = CodexAppServerRpc()
        self.final_message: str | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.safe_to_steer = False
        self.pending_rpc: dict[str, str] = {}
        self.turn_error: str | None = None
    def run(self, prompt: str) -> int:
        command = _app_server_command(self.invocation, windows=os.name == "nt")
        exit_code = 1
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.invocation.repo_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # App Server 会启动长期 MCP 子进程；原始 stderr 可能含凭据，也会让管道无法收尾。
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **app_server_process_options(windows=os.name == "nt"),
            )
            self.rpc.attach(self.process)
            self._initialize()
            self._open_thread()
            self._start_turn(prompt)
            self._event_loop()
            exit_code = 0
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_lifecycle("unavailable", "app_server_error")
            _emit_result("error", error=redact_text(str(exc)))
        finally:
            if not self._shutdown():
                exit_code = 1
        return exit_code
    def _initialize(self) -> None:
        client = {"name": "vega", "title": "Vega", "version": __version__}
        self.rpc.request(
            "initialize",
            {
                "clientInfo": client,
                "capabilities": codex_initialize_capabilities(),
            },
            on_server_request=self._record_server_request,
        )
        self.rpc.notify("initialized")
    def _open_thread(self) -> None:
        state = load_provider_sessions(self.run_dir)
        handle = state.handles[self.invocation.role_key]
        approval_policy = "never" if self.invocation.sandbox == "read-only" else "on-request"
        params = {
            "cwd": self.invocation.repo_path,
            "sandbox": self.invocation.sandbox,
            "approvalPolicy": approval_policy,
        }
        if self.invocation.model is not None:
            params["model"] = self.invocation.model
        if handle.thread_id:
            params["threadId"] = handle.thread_id
            result = self.rpc.request(
                "thread/resume",
                params,
                on_server_request=self._record_server_request,
            )
        else:
            result = self.rpc.request(
                "thread/start",
                params,
                on_server_request=self._record_server_request,
            )
        sandbox, approval_policy = require_thread_permissions(
            result, requested_sandbox=self.invocation.sandbox,
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("App Server 未返回 Thread ID")
        self.thread_id = thread_id
        def mutation(session_state) -> None:
            current = session_state.handles[self.invocation.role_key]
            current.thread_id = thread_id
            current.sandbox = sandbox
            current.approval_policy = approval_policy
            current.permissions_verified = True
            current.lifecycle = "active"
            current.last_event = "thread_ready"
            current.updated_at = utc_now()
        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        _emit_progress("thread_ready")
    def _start_turn(self, prompt: str) -> None:
        assert self.thread_id is not None
        state = load_provider_sessions(self.run_dir)
        queued = [
            item
            for item in state.steers
            if item.role_key == self.invocation.role_key and item.status == "queued"
        ]
        if queued:
            additions = "\n".join(f"- {item.instruction}" for item in queued)
            prompt = f"{prompt.rstrip()}\n\n## 用户补充指令\n{additions}\n"
        params: dict[str, object] = {
            "threadId": self.thread_id,
            "cwd": self.invocation.repo_path,
            "input": [{"type": "text", "text": prompt}],
        }
        if self.invocation.output_schema is not None:
            params["outputSchema"] = self.invocation.output_schema
        if self.invocation.reasoning_effort is not None:
            params["effort"] = self.invocation.reasoning_effort
        result = self.rpc.request(
            "turn/start",
            params,
            on_server_request=self._record_server_request,
        )
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("App Server 未返回 Turn ID")
        self.turn_id = turn_id
        def mutation(session_state) -> None:
            current = session_state.handles[self.invocation.role_key]
            current.lifecycle = "active"
            current.last_turn_id = turn_id
            current.turn_count += 1
            current.compaction_pending = False
            current.last_event = "turn_started"
            current.updated_at = utc_now()
            queued_ids = {item.steer_id for item in queued}
            for item in session_state.steers:
                if item.steer_id in queued_ids:
                    item.status = "delivered"
                    item.delivered_turn_id = turn_id
                    item.result_note = "随 Turn 输入发送"
        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        _emit_progress("turn_started")
    def _event_loop(self) -> None:
        while True:
            self._send_pending_responses()
            self._send_pending_steers()
            message = self.rpc.receive(timeout=0.1)
            if message is None:
                if self.process is not None and self.process.poll() is not None:
                    raise RuntimeError("App Server 在 Turn 完成前退出")
                continue
            if "id" in message and "method" in message:
                self._record_server_request(message)
                continue
            method = message.get("method")
            params = message.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                continue
            if self._handle_notification(method, params):
                return
    def _handle_notification(
        self,
        method: str,
        params: dict[str, object],
    ) -> bool:
        handlers = {
            "item/started": self._handle_item_started,
            "thread/tokenUsage/updated": self._handle_token_usage,
        }
        handler = handlers.get(method)
        if handler is not None:
            handler(params)
            return False
        if method == "item/completed":
            self.safe_to_steer = True
            self._handle_item(params)
        elif method == "thread/compacted":
            self._handle_compaction()
        elif method == "turn/completed":
            return self._finish_matching_turn(params)
        elif method == "error":
            self.turn_error = redact_text(str(params.get("error") or "unknown"))[:1000]
            _emit_progress("turn_failed")
        return False
    def _finish_matching_turn(self, params: dict[str, object]) -> bool:
        turn = params.get("turn")
        if not isinstance(turn, dict) or turn.get("id") != self.turn_id:
            return False
        self._complete_turn(str(turn.get("status") or "unknown"))
        return True
    def _handle_item_started(self, params: dict[str, object]) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        events = {
            "commandExecution": "command_started",
            "fileChange": "file_change_started",
            "mcpToolCall": "tool_started",
            "collabAgentToolCall": "subagent_started",
        }
        event = events.get(str(item.get("type")))
        if event:
            _emit_progress(event)
    def _handle_item(self, params: dict[str, object]) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        item_type = item.get("type")
        if item_type == "agentMessage":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                if len(text) > _MAX_AGENT_MESSAGE_CHARS:
                    raise RuntimeError("App Server agentMessage 超过安全上限")
                self.final_message = text
            return
        events = {
            "commandExecution": "command_completed",
            "fileChange": "file_changed",
            "plan": "plan_updated",
            "mcpToolCall": "tool_completed",
            "collabAgentToolCall": "subagent_updated",
            "contextCompaction": "context_compacted",
        }
        event = events.get(str(item_type))
        if event:
            _emit_progress(event)
        if item_type == "contextCompaction":
            self._handle_compaction()
    def _handle_token_usage(self, params: dict[str, object]) -> None:
        usage = params.get("tokenUsage")
        total = usage.get("total") if isinstance(usage, dict) else None
        total_tokens = total.get("totalTokens") if isinstance(total, dict) else None
        cached = total.get("cachedInputTokens") if isinstance(total, dict) else None
        context_window = usage.get("modelContextWindow") if isinstance(usage, dict) else None
        def mutation(session_state) -> None:
            handle = session_state.handles[self.invocation.role_key]
            handle.total_tokens = total_tokens if isinstance(total_tokens, int) else None
            handle.cached_input_tokens = cached if isinstance(cached, int) else None
            handle.context_window = context_window if isinstance(context_window, int) else None
            handle.updated_at = utc_now()
        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
    def _handle_compaction(self) -> None:
        def mutation(session_state) -> None:
            handle = session_state.handles[self.invocation.role_key]
            if not handle.compaction_pending:
                handle.compaction_count += 1
            handle.compaction_pending = True
            handle.last_event = "context_compacted"
            handle.updated_at = utc_now()
        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        _emit_progress("context_compacted")
    def _complete_turn(self, status: str) -> None:
        final_message = self.final_message
        def mutation(session_state) -> None:
            handle = session_state.handles[self.invocation.role_key]
            handle.lifecycle = "idle" if status == "completed" else "unavailable"
            handle.last_event = f"turn_{status}"
            handle.updated_at = utc_now()
        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        if status != "completed":
            _emit_result("error", error=getattr(self, "turn_error", None) or f"App Server Turn 状态：{status}")
            return
        if final_message is None:
            _emit_result("error", error="App Server Turn 没有最终 agentMessage。")
            return
        _emit_progress("turn_completed")
        _emit_result("success", message=final_message)
    def _record_server_request(self, message: dict[str, object]) -> None:
        rpc_id = json.dumps(message["id"], ensure_ascii=False, separators=(",", ":"))
        method = str(message["method"])
        if method not in CODEX_SUPPORTED_SERVER_REQUESTS:
            raise RuntimeError(f"App Server 请求类型不受支持：{method}")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        interaction_id = f"request-{uuid4().hex[:12]}"
        summary = summarize_provider_interaction(method, params)
        interaction = PendingInteraction(
            interaction_id=interaction_id,
            role_key=self.invocation.role_key,
            rpc_request_id=rpc_id,
            method=method,
            thread_id=str(params.get("threadId") or self.thread_id or ""),
            turn_id=str(params["turnId"]) if "turnId" in params else self.turn_id,
            summary=summary,
        )
        def mutation(session_state) -> None:
            session_state.interactions.append(interaction)
            handle = session_state.handles[self.invocation.role_key]
            handle.lifecycle = "waiting_user"
            handle.last_event = "waiting_user"
            handle.updated_at = utc_now()
        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        self.pending_rpc[rpc_id] = interaction_id
        _emit_progress("waiting_user")
    def _send_pending_responses(self) -> None:
        if not self.pending_rpc:
            return
        state = load_provider_sessions(self.run_dir)
        by_id = {item.interaction_id: item for item in state.interactions}
        for rpc_id, interaction_id in list(self.pending_rpc.items()):
            interaction = by_id.get(interaction_id)
            if (
                interaction is None
                or interaction.status != "responded"
                or interaction.response is None
            ):
                continue
            self.rpc.respond(_rpc_id(rpc_id), interaction.response)
            def mutation(session_state, target=interaction_id) -> None:
                for item in session_state.interactions:
                    if item.interaction_id == target:
                        item.status = "closed"
                        item.resolved_at = utc_now()
                handle = session_state.handles[self.invocation.role_key]
                handle.lifecycle = "active"
                handle.last_event = "user_response_sent"
                handle.updated_at = utc_now()
            mutate_provider_sessions(self.run_dir, "agent.session", mutation)
            self.pending_rpc.pop(rpc_id, None)
            _emit_progress("user_response_sent")
    def _send_pending_steers(self) -> None:
        if not self.safe_to_steer or self.thread_id is None or self.turn_id is None:
            return
        state = load_provider_sessions(self.run_dir)
        queued = [
            item
            for item in state.steers
            if item.role_key == self.invocation.role_key and item.status == "queued"
        ]
        for steer in queued:
            try:
                self.rpc.request(
                    "turn/steer",
                    {
                        "threadId": self.thread_id,
                        "expectedTurnId": self.turn_id,
                        "input": [{"type": "text", "text": steer.instruction}],
                    },
                    on_server_request=self._record_server_request,
                )
            except RuntimeError as exc:
                status = "rejected"
                note = redact_text(str(exc))
            else:
                status = "delivered"
                note = "已在安全事件边界发送"
            def mutation(session_state, steer_id=steer.steer_id) -> None:
                for item in session_state.steers:
                    if item.steer_id == steer_id:
                        item.status = status
                        item.delivered_turn_id = self.turn_id
                        item.result_note = note
            mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        self.safe_to_steer = False
    def _set_lifecycle(self, lifecycle: str, event: str) -> None:
        def mutation(session_state) -> None:
            handle = session_state.handles.get(self.invocation.role_key)
            if handle is not None:
                handle.lifecycle = lifecycle
                handle.last_event = event
                handle.updated_at = utc_now()
        try:
            mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        except (OSError, ValueError):
            pass
    def _shutdown(self) -> bool:
        process = self.process
        if process is None:
            return True
        termination = terminate_app_server_tree(process, windows=os.name == "nt")
        if not termination.succeeded:
            self._set_lifecycle("unavailable", "shutdown_unconfirmed")
            _emit_result("error", error="App Server 进程树终止未确认。")
            return False
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        return True
def _rpc_id(value: str) -> str | int:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(decoded, (str, int)) and not isinstance(decoded, bool):
        return decoded
    return value
def _app_server_command(
    invocation: AppServerInvocation,
    *,
    windows: bool,
) -> list[str] | str:
    command = [invocation.executable, *invocation.global_args]
    command.extend(["app-server", "--listen", "stdio://", *invocation.server_args])
    return prepare_subprocess_command(command, windows=windows)
def _emit_progress(event: str) -> None:
    print(
        json.dumps(
            {"type": "vega.app_server_progress", "event": event},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
def _emit_result(
    status: str,
    *,
    message: str | None = None,
    error: str | None = None,
) -> None:
    print(
        json.dumps(
            {
                "type": "vega.app_server_result",
                "status": status,
                "message": redact_text(message) if message is not None else None,
                "error": redact_text(error) if error is not None else None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )
def _helper_main(request_path: Path) -> int:
    install_parent_termination_handler(windows=os.name == "nt")
    try:
        invocation = AppServerInvocation.model_validate_json(
            request_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        _emit_result("error", error=f"无法读取 App Server 请求：{exc}")
        return 2
    prompt = sys.stdin.read()
    if not prompt.strip():
        _emit_result("error", error="App Server prompt 为空")
        return 2
    return _AppServerClient(invocation).run(prompt)
if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m vega.codex_app_server <request.json>")
    raise SystemExit(_helper_main(Path(sys.argv[1])))
