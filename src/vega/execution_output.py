from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, BinaryIO


ExecutionOutputLineObserver = Callable[[str], None]
MAX_JSONL_LINE_CHARS = 4 * 1024 * 1024

_READ_CHUNK_BYTES = 64 * 1024
_MAX_OBSERVED_LINE_BYTES = 256 * 1024
_MAX_QUEUED_LINES = 128
_DISPATCH_POLL_SECONDS = 0.05
_DISPATCH_JOIN_SECONDS = 0.05
_REDACTION_UNAVAILABLE_OUTPUT = "[REDACTION_UNAVAILABLE]"


class ProcessOutputCapture:
    """持续排空外部进程输出，并异步投递有界实时提示。"""

    def __init__(
        self,
        output_file: BinaryIO,
        observer: ExecutionOutputLineObserver | None,
        stderr_file: BinaryIO | None = None,
        *,
        separate_stderr: bool = False,
    ) -> None:
        self.output_file = output_file
        self.stderr_file = stderr_file if separate_stderr else None
        self.observer = observer
        # 观察器只负责实时提示，不能反过来阻塞 PIPE reader；队列满时丢弃提示，
        # 完整 stdout（以及默认模式下合并的 stderr）仍写入 output_file，
        # 最终结果从完整输出独立提取。
        self._lines: queue.Queue[str] = queue.Queue(maxsize=_MAX_QUEUED_LINES)
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._dispatcher: threading.Thread | None = None
        self._stream: BinaryIO | None = None
        self._stderr_stream: BinaryIO | None = None
        self._reader_error: BaseException | None = None
        self._stderr_reader_error: BaseException | None = None
        self._write_lock = threading.Lock()
        self._stderr_write_lock = threading.Lock()
        self._accept_writes = True
        self._accept_stderr_writes = True
        self._observer_closed = threading.Event()
        self._dispatch_stop = threading.Event()

    @property
    def popen_stdout(self) -> int | BinaryIO:
        return subprocess.PIPE if self.observer is not None else self.output_file

    @property
    def popen_stderr(self) -> int | BinaryIO:
        return subprocess.PIPE if self.stderr_file is not None else subprocess.STDOUT

    def start(self, process: subprocess.Popen[bytes]) -> None:
        if self.observer is None and self.stderr_file is None:
            return
        try:
            if self.observer is not None:
                stream = process.stdout
                if stream is None:
                    raise OSError("实时输出观察要求外部进程提供 stdout PIPE")
                self._stream = stream
                dispatcher = threading.Thread(
                    target=self._dispatch_lines,
                    name="vega-output-dispatcher",
                    daemon=True,
                )
                dispatcher.start()
                self._dispatcher = dispatcher
                reader = threading.Thread(
                    target=self._read_stream,
                    args=(stream,),
                    name="vega-output-reader",
                    daemon=True,
                )
                reader.start()
                self._reader = reader
            if self.stderr_file is not None:
                stderr_stream = process.stderr
                if stderr_stream is None:
                    raise OSError("stderr 分流要求外部进程提供 stderr PIPE")
                self._stderr_stream = stderr_stream
                stderr_reader = threading.Thread(
                    target=self._read_stderr_stream,
                    args=(stderr_stream,),
                    name="vega-stderr-reader",
                    daemon=True,
                )
                stderr_reader.start()
                self._stderr_reader = stderr_reader
        except RuntimeError as exc:
            self._stop_dispatcher()
            raise OSError("runner 输出读取线程启动失败") from exc
        except OSError:
            self._stop_dispatcher()
            raise

    def poll(self, *args: object, **kwargs: object) -> int:
        """保留控制循环调用点；实时提示已由独立 dispatcher 异步处理。"""

        del args, kwargs
        return 0

    def finish(self, timeout_seconds: float) -> None:
        """关闭 reader/dispatcher，并在 bounded join 后报告不完整输出。"""

        issues: list[str] = []
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        try:
            self._finish_reader(
                self._reader,
                deadline,
                "runner 输出",
                issues,
            )
            self._finish_reader(
                self._stderr_reader,
                deadline,
                "runner stderr",
                issues,
            )
            if self._reader is None:
                self._close_stream()
            if self._stderr_reader is None:
                self._close_stderr_stream()
            if self._reader_error is not None:
                issues.append("runner 输出读取失败")
            if self._stderr_reader_error is not None:
                issues.append("runner stderr 读取失败")
            if issues:
                raise OSError("；".join(issues))
        finally:
            # finish 返回或抛错后，reader 即使从阻塞 read 中苏醒也不能再写 sink；
            # 这样 execution_control 才能安全读取稳定的 partial/full 快照。
            self._freeze_output()
            # 卡死 observer 不能被抢占；停止继续投递并只等待一个很短的窗口，
            # dispatcher 保持 daemon，不能阻塞 timeout/stop 或进程返回。
            self._stop_dispatcher()

    def finish_and_persist(
        self,
        timeout_seconds: float,
        persist_output: Callable[[object], str],
        persist_stderr: Callable[[object], str] | None = None,
    ) -> tuple[str, str | None]:
        """在 sink 冻结后保存完整或 partial 输出，并返回 fail-closed 错误。"""

        errors: list[str] = []
        try:
            self.finish(timeout_seconds)
        except OSError as exc:
            errors.append(f"runner 输出读取失败：{exc}")
        try:
            output = persist_output(self.output_file)
        except Exception as exc:  # noqa: BLE001 - artifact 写入异常必须转为受控失败
            output = ""
            errors.append(f"runner 输出持久化失败：{exc}")
        if self.stderr_file is not None and persist_stderr is not None:
            try:
                persist_stderr(self.stderr_file)
            except Exception as exc:  # noqa: BLE001 - stderr 写入异常必须转为受控失败
                errors.append(f"runner stderr 持久化失败：{exc}")
        return output, "；".join(errors) or None

    def _read_stream(self, stream: BinaryIO) -> None:
        pending = bytearray()
        oversized = False
        try:
            read_available = getattr(stream, "read1", None)
            if read_available is None:
                read_available = stream.read
            while chunk := read_available(_READ_CHUNK_BYTES):
                self._write_chunk(chunk)
                if self._observer_closed.is_set():
                    pending.clear()
                    oversized = False
                    continue
                oversized = self._consume_chunk(chunk, pending, oversized)
            if not self._observer_closed.is_set() and pending and not oversized:
                self._enqueue_pending_line(pending, oversized)
        except Exception as exc:  # noqa: BLE001 - reader 异常必须收紧执行结果
            self._reader_error = exc
        finally:
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001 - close 异常同样不能静默成功
                if self._reader_error is None:
                    self._reader_error = exc
            if self._stream is stream:
                self._stream = None

    def _read_stderr_stream(self, stream: BinaryIO) -> None:
        try:
            read_available = getattr(stream, "read1", None)
            if read_available is None:
                read_available = stream.read
            while chunk := read_available(_READ_CHUNK_BYTES):
                self._write_stderr_chunk(chunk)
        except Exception as exc:  # noqa: BLE001 - stderr reader 异常必须收紧执行结果
            self._stderr_reader_error = exc
        finally:
            try:
                stream.close()
            except Exception as exc:  # noqa: BLE001 - close 异常同样不能静默成功
                if self._stderr_reader_error is None:
                    self._stderr_reader_error = exc
            if self._stderr_stream is stream:
                self._stderr_stream = None

    def _write_chunk(self, chunk: bytes) -> None:
        with self._write_lock:
            if self._accept_writes:
                self.output_file.write(chunk)

    def _write_stderr_chunk(self, chunk: bytes) -> None:
        stderr_file = self.stderr_file
        if stderr_file is None:
            return
        with self._stderr_write_lock:
            if self._accept_stderr_writes:
                stderr_file.write(chunk)

    def _consume_chunk(
        self,
        chunk: bytes,
        pending: bytearray,
        oversized: bool,
    ) -> bool:
        start = 0
        while True:
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline
            oversized = self._append_observed_part(
                pending,
                chunk[start:end],
                oversized,
            )
            if newline < 0:
                return oversized
            self._enqueue_pending_line(pending, oversized)
            pending.clear()
            oversized = False
            start = newline + 1

    def _append_observed_part(
        self,
        pending: bytearray,
        part: bytes,
        oversized: bool,
    ) -> bool:
        if oversized:
            return True
        if len(pending) + len(part) > _MAX_OBSERVED_LINE_BYTES:
            pending.clear()
            return True
        pending.extend(part)
        return False

    def _enqueue_pending_line(self, pending: bytearray, oversized: bool) -> None:
        if not oversized:
            self._enqueue_line(pending.decode("utf-8", errors="replace"))

    def _dispatch_lines(self) -> None:
        while not self._dispatch_stop.is_set():
            try:
                line = self._lines.get(timeout=_DISPATCH_POLL_SECONDS)
            except queue.Empty:
                continue
            if self._observer_closed.is_set():
                continue
            observer = self.observer
            if observer is None:
                continue
            try:
                observer(line.removesuffix("\r"))
            except Exception:  # noqa: BLE001 - 可见性回调失败不能改变执行结果
                self._disable_observer()

    def _enqueue_line(self, line: str) -> None:
        if self._observer_closed.is_set() or self.observer is None:
            return
        try:
            self._lines.put_nowait(line)
        except queue.Full:
            # 实时提示可以丢弃，不能让高频输出反向阻塞外部进程的 PIPE。
            return

    def _disable_observer(self) -> None:
        self.observer = None
        self._observer_closed.set()

    def _freeze_output(self) -> None:
        with self._write_lock:
            self._accept_writes = False
        with self._stderr_write_lock:
            self._accept_stderr_writes = False

    def _stop_dispatcher(self) -> None:
        self._disable_observer()
        self._dispatch_stop.set()
        dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher is not threading.current_thread():
            dispatcher.join(_DISPATCH_JOIN_SECONDS)

    def _close_stream(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001 - 强制关闭异常必须 fail-closed
            if self._reader_error is None:
                self._reader_error = exc

    def _close_stderr_stream(self) -> None:
        stream = self._stderr_stream
        if stream is None:
            return
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001 - 强制关闭异常必须 fail-closed
            if self._stderr_reader_error is None:
                self._stderr_reader_error = exc

    @staticmethod
    def _finish_reader(
        reader: threading.Thread | None,
        deadline: float,
        label: str,
        issues: list[str],
    ) -> None:
        if reader is None:
            return
        reader.join(max(0.0, deadline - time.monotonic()))
        if reader.is_alive():
            issues.append(f"{label}读取线程关闭超时，输出可能不完整")


def redact_process_output(output: str) -> str:
    if not output:
        return output
    redactor = _load_redact_text()
    if redactor is None:
        return _REDACTION_UNAVAILABLE_OUTPUT
    try:
        return redactor(output)
    except Exception:
        return _REDACTION_UNAVAILABLE_OUTPUT


def redact_jsonl_output(output: str) -> str:
    """逐行解析、递归脱敏并重新序列化 JSONL，避免文本替换破坏 JSON 结构。"""

    if not output:
        return output
    redactor = _load_redact_value()
    if redactor is None:
        return _safe_jsonl_event("vega.redaction_unavailable")

    safe_lines: list[str] = []
    start = 0
    output_length = len(output)
    while start < output_length:
        newline = output.find("\n", start)
        line_end = output_length if newline < 0 else newline
        safe_line = _redact_jsonl_line(output, start, line_end, redactor)
        if safe_line is not None:
            safe_lines.append(safe_line)
        if newline < 0:
            break
        start = newline + 1
    return "\n".join(safe_lines) + ("\n" if safe_lines else "")


def redact_optional_text(output: str | None) -> str | None:
    return redact_process_output(output) if output is not None else None


def _load_redact_text() -> Callable[[str], str] | None:
    try:
        from .redaction import redact_text
    except Exception:  # noqa: BLE001 - 脱敏器加载失败必须 fail closed
        return None
    return redact_text


def _load_redact_value() -> Callable[[Any], Any] | None:
    try:
        from .redaction import redact_value
    except Exception:  # noqa: BLE001 - 脱敏器加载失败必须 fail closed
        return None
    return redact_value


def _redact_jsonl_line(
    output: str,
    start: int,
    line_end: int,
    redactor: Callable[[Any], Any],
) -> str | None:
    if line_end - start > MAX_JSONL_LINE_CHARS:
        return _safe_jsonl_event("vega.oversized_jsonl").rstrip("\n")
    line = output[start:line_end]
    if not line.strip():
        return None
    try:
        payload = json.loads(line)
    except Exception:  # noqa: BLE001 - 不可信 JSON 解析失败必须替换为安全事件
        return _safe_jsonl_event("vega.invalid_jsonl").rstrip("\n")
    try:
        safe_payload = redactor(payload)
        return json.dumps(
            safe_payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except Exception:  # noqa: BLE001 - 结构化脱敏失败必须替换为安全事件
        return _safe_jsonl_event("vega.redaction_failed").rstrip("\n")


def _safe_jsonl_event(event_type: str) -> str:
    return json.dumps({"type": event_type}, separators=(",", ":")) + "\n"
