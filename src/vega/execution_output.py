from __future__ import annotations

import queue
import subprocess
import threading
from collections.abc import Callable
from typing import BinaryIO


ExecutionOutputLineObserver = Callable[[str], None]

_READ_CHUNK_BYTES = 64 * 1024
_MAX_OBSERVED_LINE_BYTES = 256 * 1024


class ProcessOutputCapture:
    """持续排空外部进程输出，同时只把有界完整行交给可选观察器。"""

    def __init__(
        self,
        output_file: BinaryIO,
        observer: ExecutionOutputLineObserver | None,
    ) -> None:
        self.output_file = output_file
        self.observer = observer
        self._lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._reader: threading.Thread | None = None
        self._reader_error: BaseException | None = None

    @property
    def popen_stdout(self) -> int | BinaryIO:
        return subprocess.PIPE if self.observer is not None else self.output_file

    def start(self, process: subprocess.Popen[bytes]) -> None:
        if self.observer is None:
            return
        if process.stdout is None:
            raise OSError("实时输出观察要求外部进程提供 stdout PIPE")
        try:
            reader = threading.Thread(
                target=self._read_stream,
                args=(process.stdout,),
                name="vega-output-reader",
                daemon=True,
            )
            reader.start()
        except RuntimeError as exc:
            raise OSError("runner 输出读取线程启动失败") from exc
        self._reader = reader

    def poll(self) -> None:
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                return
            line = line.removesuffix("\r")
            observer = self.observer
            if observer is None:
                continue
            try:
                observer(line)
            except Exception:  # noqa: BLE001 - 可见性回调失败不能改变执行结果
                self.observer = None

    def finish(self, timeout_seconds: float) -> None:
        reader = self._reader
        if reader is None:
            return
        reader.join(max(0.1, timeout_seconds))
        self.poll()
        if reader.is_alive():
            raise OSError("runner 输出读取线程未在限定时间内结束")
        if self._reader_error is not None:
            raise OSError("runner 输出读取失败") from self._reader_error

    def _read_stream(self, stream: BinaryIO) -> None:
        pending = bytearray()
        oversized = False
        read_available = getattr(stream, "read1", stream.read)
        try:
            while chunk := read_available(_READ_CHUNK_BYTES):
                self.output_file.write(chunk)
                start = 0
                while True:
                    newline = chunk.find(b"\n", start)
                    end = len(chunk) if newline < 0 else newline
                    part = chunk[start:end]
                    if not oversized:
                        if len(pending) + len(part) <= _MAX_OBSERVED_LINE_BYTES:
                            pending.extend(part)
                        else:
                            pending.clear()
                            oversized = True
                    if newline < 0:
                        break
                    if not oversized:
                        self._lines.put(pending.decode("utf-8", errors="replace"))
                    pending.clear()
                    oversized = False
                    start = newline + 1
            if pending and not oversized:
                self._lines.put(pending.decode("utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            self._reader_error = exc
        finally:
            try:
                stream.close()
            except OSError:
                pass
