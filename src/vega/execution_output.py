from __future__ import annotations

import queue
import subprocess
import threading
from collections.abc import Callable
from typing import BinaryIO


ExecutionOutputLineObserver = Callable[[str], None]

_READ_CHUNK_BYTES = 64 * 1024
_MAX_OBSERVED_LINE_BYTES = 256 * 1024
_MAX_QUEUED_LINES = 128
_DISPATCH_POLL_SECONDS = 0.05
_DISPATCH_JOIN_SECONDS = 0.05
_READER_SHUTDOWN_SECONDS = 0.5


class ProcessOutputCapture:
    """持续排空外部进程输出，并异步投递有界实时提示。"""

    def __init__(
        self,
        output_file: BinaryIO,
        observer: ExecutionOutputLineObserver | None,
    ) -> None:
        self.output_file = output_file
        self.observer = observer
        # 观察器只负责实时提示，不能反过来阻塞 PIPE reader；队列满时丢弃提示，
        # 完整 stdout/stderr 仍继续写入 output_file，最终结果从完整输出独立提取。
        self._lines: queue.Queue[str] = queue.Queue(maxsize=_MAX_QUEUED_LINES)
        self._reader: threading.Thread | None = None
        self._dispatcher: threading.Thread | None = None
        self._stream: BinaryIO | None = None
        self._reader_error: BaseException | None = None
        self._write_lock = threading.Lock()
        self._accept_writes = True
        self._observer_closed = threading.Event()
        self._dispatch_stop = threading.Event()

    @property
    def popen_stdout(self) -> int | BinaryIO:
        return subprocess.PIPE if self.observer is not None else self.output_file

    def start(self, process: subprocess.Popen[bytes]) -> None:
        if self.observer is None:
            return
        stream = process.stdout
        if stream is None:
            raise OSError("实时输出观察要求外部进程提供 stdout PIPE")
        self._stream = stream
        try:
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
        except RuntimeError as exc:
            self._stop_dispatcher()
            self._close_stream()
            raise OSError("runner 输出读取线程启动失败") from exc

    def poll(self, *args: object, **kwargs: object) -> int:
        """保留控制循环调用点；实时提示已由独立 dispatcher 异步处理。"""

        del args, kwargs
        return 0

    def finish(self, timeout_seconds: float) -> None:
        """关闭 reader/dispatcher，并在 bounded join 后报告不完整输出。"""

        reader = self._reader
        try:
            if reader is not None:
                reader.join(max(0.1, timeout_seconds))
                if reader.is_alive():
                    # 外部进程树已结束但继承的 PIPE 仍可能保持打开；先显式关闭，
                    # 再给 reader 一个短的退出窗口，避免 reader 持有临时输出文件。
                    self._close_stream()
                    reader.join(_READER_SHUTDOWN_SECONDS)
            if reader is not None and reader.is_alive():
                raise OSError("runner 输出读取线程关闭超时，输出可能不完整")
            if self._reader_error is not None:
                raise OSError("runner 输出读取失败") from self._reader_error
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
    ) -> tuple[str, str | None]:
        """在 sink 冻结后保存完整或 partial 输出，并返回 fail-closed 错误。"""

        errors: list[str] = []
        try:
            self.finish(timeout_seconds)
        except OSError as exc:
            errors.append(f"runner 输出读取失败：{exc}")
        try:
            output = persist_output(self.output_file)
        except OSError as exc:
            output = ""
            errors.append(f"runner 输出持久化失败：{exc}")
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

    def _write_chunk(self, chunk: bytes) -> None:
        with self._write_lock:
            if self._accept_writes:
                self.output_file.write(chunk)

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
