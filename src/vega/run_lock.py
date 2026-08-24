from __future__ import annotations

import errno
import json
import os
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


_ALLOWED_OPERATIONS = {
    "agent.adjudicate_side_effects",
    "agent.approve",
    "agent.dispatch",
    "agent.finalize",
    "agent.handoff",
    "agent.observe",
    "agent.pause",
    "agent.plan",
    "agent.recover",
    "agent.resume",
    "agent.retry-verification",
    "agent.steer",
    "agent.stop",
    "decision.append",
    "goal.attach",
    "goal.checkpoint_done",
    "goal.complete",
    "goal.pause",
    "goal.reconcile",
    "goal.recover",
    "goal.resume",
    "goal.run",
    "goal.step",
    "goal.stop",
    "loop.continue",
    "loop.finish",
    "loop.recover",
    "loop.start",
}
_BUSY_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}
_BUSY_WINERRORS = {32, 33}
_HELD_PATHS: set[Path] = set()
_HELD_PATHS_GUARD = threading.Lock()


class RunMutationBusyError(ValueError):
    """同一 run 已经由另一个生命周期写者持有。"""


class RunMutationLockError(ValueError):
    """run 锁无法安全建立或释放。"""


class RunMutationLock:
    """保护单个 run 的跨文件生命周期修改临界区。"""

    def __init__(
        self,
        *,
        run_dir: Path,
        operation: str,
        lock_path: Path,
        owner_path: Path,
        stream: BinaryIO,
    ) -> None:
        self.run_dir = run_dir
        self.operation = operation
        self.lock_path = lock_path
        self.owner_path = owner_path
        self._stream = stream
        self._released = False

    @classmethod
    def acquire(cls, run_dir: Path, operation: str) -> RunMutationLock:
        normalized_operation = operation.strip()
        if normalized_operation not in _ALLOWED_OPERATIONS:
            raise RunMutationLockError(
                f"不支持的 run mutation operation：{normalized_operation or '<empty>'}"
            )

        resolved_run = run_dir.resolve(strict=True)
        if not resolved_run.is_dir():
            raise RunMutationLockError(f"run 不是目录：{resolved_run}")
        control_dir = _prepare_control_dir(resolved_run)
        lock_path = control_dir / "run-mutation.lock"
        owner_path = control_dir / "run-mutation-owner.json"
        canonical_lock_path = lock_path.resolve(strict=False)

        with _HELD_PATHS_GUARD:
            if canonical_lock_path in _HELD_PATHS:
                raise RunMutationBusyError(
                    f"run 正由当前进程修改：run={resolved_run.name}，"
                    f"operation={normalized_operation}"
                )
            _HELD_PATHS.add(canonical_lock_path)

        stream: BinaryIO | None = None
        locked = False
        try:
            stream = _open_lock_file(lock_path)
            try:
                _lock_stream(stream)
                locked = True
            except OSError as exc:
                if _is_busy_error(exc):
                    raise RunMutationBusyError(
                        _render_busy_message(
                            resolved_run,
                            normalized_operation,
                            owner_path,
                        )
                    ) from exc
                raise RunMutationLockError(
                    f"无法为 run 建立修改锁：run={resolved_run.name}，"
                    f"error={type(exc).__name__}"
                ) from exc

            _write_owner_metadata(
                owner_path,
                {
                    "run_id": resolved_run.name,
                    "operation": normalized_operation,
                    "owner_pid": os.getpid(),
                    "acquired_at": datetime.now(UTC).isoformat(),
                },
            )
            return cls(
                run_dir=resolved_run,
                operation=normalized_operation,
                lock_path=lock_path,
                owner_path=owner_path,
                stream=stream,
            )
        except Exception:
            if stream is not None:
                if locked:
                    try:
                        _unlock_stream(stream)
                    except OSError:
                        pass
                stream.close()
            with _HELD_PATHS_GUARD:
                _HELD_PATHS.discard(canonical_lock_path)
            raise

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        unlock_error: OSError | None = None
        try:
            try:
                self.owner_path.unlink(missing_ok=True)
            except OSError:
                # 崩溃恢复允许残留诊断元数据；真正所有权只由内核锁决定。
                pass
            try:
                _unlock_stream(self._stream)
            except OSError as exc:
                unlock_error = exc
        finally:
            self._stream.close()
            with _HELD_PATHS_GUARD:
                _HELD_PATHS.discard(self.lock_path.resolve(strict=False))
        if unlock_error is not None:
            raise RunMutationLockError(
                f"run 修改锁释放异常：run={self.run_dir.name}，"
                f"error={type(unlock_error).__name__}"
            ) from unlock_error

    def __enter__(self) -> RunMutationLock:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.release()
        except RunMutationLockError:
            if exc is None:
                raise


def _prepare_control_dir(run_dir: Path) -> Path:
    control_dir = run_dir / ".control"
    try:
        control_dir.mkdir(exist_ok=True)
    except OSError as exc:
        raise RunMutationLockError(
            f"无法创建 run 控制目录：run={run_dir.name}，error={type(exc).__name__}"
        ) from exc
    if _is_link_or_reparse_point(control_dir):
        raise RunMutationLockError("run 控制目录不能是符号链接、junction 或 reparse point")
    try:
        resolved_control = control_dir.resolve(strict=True)
    except OSError as exc:
        raise RunMutationLockError("无法解析 run 控制目录") from exc
    if resolved_control.parent != run_dir:
        raise RunMutationLockError("run 控制目录越过 run 边界")
    return resolved_control


def _open_lock_file(lock_path: Path) -> BinaryIO:
    if os.path.lexists(lock_path) and _is_link_or_reparse_point(lock_path):
        raise RunMutationLockError("run lock 不能是符号链接或 reparse point")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        flags |= no_follow
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RunMutationLockError(
            f"无法打开 run lock：error={type(exc).__name__}"
        ) from exc
    stream: BinaryIO | None = None
    try:
        os.set_inheritable(descriptor, False)
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise RunMutationLockError("run lock 必须是普通文件")
        if descriptor_stat.st_nlink != 1:
            raise RunMutationLockError("run lock 不能是 hardlink")
        if lock_path.exists():
            path_stat = lock_path.stat()
            if (
                descriptor_stat.st_dev != path_stat.st_dev
                or descriptor_stat.st_ino != path_stat.st_ino
                or path_stat.st_nlink != 1
            ):
                raise RunMutationLockError("run lock 在打开期间被替换")
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        descriptor = -1
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
        stream.seek(0)
        return stream
    except Exception:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)
        raise


def _lock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _write_owner_metadata(path: Path, payload: dict[str, object]) -> None:
    # 临时文件必须独占创建。固定文件名可能被预先放置为 hardlink，导致 write_text
    # 在原子 replace 之前先覆盖 run 边界外的目标。
    temp_path = _owner_metadata_temp_path(path)
    descriptor = -1
    try:
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temp_path, flags, 0o600)
        os.set_inheritable(descriptor, False)
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise RunMutationLockError("run lock owner 临时文件必须是普通文件")
        if descriptor_stat.st_nlink != 1:
            raise RunMutationLockError("run lock owner 临时文件不能是 hardlink")
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            descriptor = -1
            stream.write(data)

        path_stat = temp_path.lstat()
        if (
            descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_ino != path_stat.st_ino
            or not stat.S_ISREG(path_stat.st_mode)
        ):
            raise RunMutationLockError("run lock owner 临时文件在写入期间被替换")
        os.replace(temp_path, path)
    except (OSError, RunMutationLockError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)
        if isinstance(exc, RunMutationLockError):
            raise
        raise RunMutationLockError(
            f"无法写入 run lock owner 元数据：error={type(exc).__name__}"
        ) from exc


def _owner_metadata_temp_path(path: Path) -> Path:
    # 保留完整随机标识，同时压缩深层 Windows worktree 中的路径开销。
    return path.with_name(f".o.{uuid4().hex}")


def _render_busy_message(
    run_dir: Path,
    requested_operation: str,
    owner_path: Path,
) -> str:
    owner = _read_owner_metadata(owner_path, run_dir.name)
    if owner is None:
        return (
            f"run 正由其他进程修改：run={run_dir.name}，"
            f"requested_operation={requested_operation}"
        )
    return (
        f"run 正由其他进程修改：run={run_dir.name}，"
        f"operation={owner['operation']}，owner_pid={owner['owner_pid']}，"
        f"acquired_at={owner['acquired_at']}，"
        f"requested_operation={requested_operation}"
    )


def _read_owner_metadata(path: Path, expected_run_id: str) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("run_id")
    operation = payload.get("operation")
    owner_pid = payload.get("owner_pid")
    acquired_at = payload.get("acquired_at")
    if (
        run_id != expected_run_id
        or operation not in _ALLOWED_OPERATIONS
        or not isinstance(owner_pid, int)
        or owner_pid < 1
        or not isinstance(acquired_at, str)
    ):
        return None
    return {
        "operation": operation,
        "owner_pid": owner_pid,
        "acquired_at": acquired_at,
    }


def _is_busy_error(error: OSError) -> bool:
    return error.errno in _BUSY_ERRNOS or getattr(error, "winerror", None) in _BUSY_WINERRORS


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
