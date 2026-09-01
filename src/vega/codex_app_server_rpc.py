from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Final

from .execution_output import MAX_JSONL_LINE_CHARS
from .redaction import redact_text


ServerRequestHandler = Callable[[dict[str, object]], None]

_REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_BUFFERED_MESSAGES: Final[int] = 512
_MAX_OVERLOAD_RETRIES: Final[int] = 3
_OVERLOAD_ERROR_CODE: Final[int] = -32001

_OBSERVED_NOTIFICATIONS: Final[frozenset[str]] = frozenset(
    {
        "error",
        "item/completed",
        "item/started",
        "thread/compacted",
        "thread/tokenUsage/updated",
        "turn/completed",
    }
)

_OPT_OUT_NOTIFICATIONS: Final[tuple[str, ...]] = (
    "command/exec/outputDelta",
    "item/agentMessage/delta",
    "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta",
    "item/fileChange/patchUpdated",
    "item/mcpToolCall/progress",
    "item/plan/delta",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/textDelta",
    "process/outputDelta",
    "turn/diff/updated",
    "turn/plan/updated",
)

CODEX_SUPPORTED_SERVER_REQUESTS: Final[frozenset[str]] = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
    }
)


class CodexAppServerRpc:
    """处理 App Server JSON-RPC 传输；业务事件仍由上层 Client 解释。"""

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[dict[str, object]] = queue.Queue(
            maxsize=_MAX_BUFFERED_MESSAGES
        )
        self.deferred: deque[dict[str, object]] = deque()
        self.next_id = 1

    def attach(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        on_server_request: ServerRequestHandler,
    ) -> dict[str, object]:
        deadline = time.monotonic() + _REQUEST_TIMEOUT_SECONDS
        overload_retries = 0
        while True:
            request_id = self.next_id
            self.next_id += 1
            self.write({"id": request_id, "method": method, "params": params})
            message = self._wait_for_response(
                request_id,
                method,
                deadline,
                on_server_request,
            )
            error = message.get("error")
            if error is None:
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            if (
                not _is_overload_error(error)
                or overload_retries >= _MAX_OVERLOAD_RETRIES
            ):
                raise RuntimeError(f"{method} 失败：{redact_text(str(error))}")
            overload_retries += 1
            delay = _overload_retry_delay_seconds(overload_retries)
            if time.monotonic() + delay >= deadline:
                raise RuntimeError(f"{method} 响应超时")
            time.sleep(delay)

    def notify(self, method: str, params: dict[str, object] | None = None) -> None:
        payload: dict[str, object] = {"method": method}
        if params is not None:
            payload["params"] = params
        self.write(payload)

    def respond(self, request_id: str | int, result: dict[str, object]) -> None:
        self.write({"id": request_id, "result": result})

    def receive(self, *, timeout: float) -> dict[str, object] | None:
        if self.deferred:
            return self.deferred.popleft()
        return self._receive_incoming(timeout=timeout)

    def _receive_incoming(self, *, timeout: float) -> dict[str, object] | None:
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, payload: dict[str, object]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("App Server stdin 不可用")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(line) > MAX_JSONL_LINE_CHARS:
            raise RuntimeError("App Server 请求超过单行安全上限")
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def _wait_for_response(
        self,
        request_id: int,
        method: str,
        deadline: float,
        on_server_request: ServerRequestHandler,
    ) -> dict[str, object]:
        while time.monotonic() < deadline:
            message = self._receive_incoming(timeout=0.1)
            if message is None:
                if self.process is not None and self.process.poll() is not None:
                    raise RuntimeError(f"App Server 在 {method} 响应前退出")
                continue
            if message.get("id") == request_id:
                return message
            if "id" in message and "method" in message:
                on_server_request(message)
                continue
            if len(self.deferred) >= _MAX_BUFFERED_MESSAGES:
                raise RuntimeError("App Server 关键事件积压超过安全上限")
            self.deferred.append(message)
        raise RuntimeError(f"{method} 响应超时")

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if len(line) > MAX_JSONL_LINE_CHARS:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or not _keep_message(payload):
                continue
            self.messages.put(payload)


def _keep_message(payload: dict[str, object]) -> bool:
    if "id" in payload:
        return True
    method = payload.get("method")
    return isinstance(method, str) and method in _OBSERVED_NOTIFICATIONS


def codex_initialize_capabilities() -> dict[str, object]:
    """关闭 Vega 不消费的正文增量，代码事实仍从 Git Candidate 获取。"""

    return {"optOutNotificationMethods": list(_OPT_OUT_NOTIFICATIONS)}


def _is_overload_error(error: object) -> bool:
    return isinstance(error, dict) and error.get("code") == _OVERLOAD_ERROR_CODE


def _overload_retry_delay_seconds(retry_number: int) -> float:
    return min(0.1 * (2 ** (retry_number - 1)), 0.8)
