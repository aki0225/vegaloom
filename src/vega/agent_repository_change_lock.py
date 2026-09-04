from __future__ import annotations

import errno
import os
import stat
import subprocess
import threading
from pathlib import Path
from typing import BinaryIO


_BUSY_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}
_BUSY_WINERRORS = {32, 33}


class AgentRepositoryGuardError(ValueError):
    """仓库级 Agent 所有权无法安全建立或释放。"""


class AgentRepositoryGuardBusyError(AgentRepositoryGuardError):
    """仓库级 ChangeRun 创建或恢复正在由另一个调用处理。"""


class RepositoryChangeLock:
    """保护仓库级 ChangeRun 创建/恢复的短临界区。"""

    _local_locks: dict[Path, threading.Lock] = {}
    _local_locks_guard = threading.Lock()

    def __init__(
        self,
        *,
        lock_path: Path,
        stream: BinaryIO,
        local_lock: threading.Lock,
    ) -> None:
        self.lock_path = lock_path
        self._stream = stream
        self._local_lock = local_lock
        self._released = False

    @classmethod
    def acquire(cls, repo: Path) -> RepositoryChangeLock:
        """按 Git common dir 建立跨进程锁，不把不同仓库串行化。"""

        control_dir = _prepare_control_dir(repo)
        lock_path = control_dir / "change-start.lock"
        canonical_lock_path = lock_path.resolve(strict=False)
        local_lock = cls._local_lock_for(canonical_lock_path)
        if not local_lock.acquire(blocking=False):
            raise AgentRepositoryGuardBusyError(
                "当前仓库正在创建或恢复另一个 ChangeRun，请稍后重试。"
            )
        try:
            return cls._acquire_file_lock(lock_path, local_lock)
        except Exception:
            local_lock.release()
            raise

    @classmethod
    def _acquire_file_lock(
        cls,
        lock_path: Path,
        local_lock: threading.Lock,
    ) -> RepositoryChangeLock:
        stream = _open_repository_lock_file(lock_path)
        try:
            _lock_repository_stream(stream)
        except OSError as exc:
            stream.close()
            if _is_repository_lock_busy(exc):
                raise AgentRepositoryGuardBusyError(
                    "当前仓库正在创建或恢复另一个 ChangeRun，请稍后重试。"
                ) from exc
            raise
        return cls(
            lock_path=lock_path,
            stream=stream,
            local_lock=local_lock,
        )

    @classmethod
    def _local_lock_for(cls, path: Path) -> threading.Lock:
        with cls._local_locks_guard:
            lock = cls._local_locks.get(path)
            if lock is None:
                lock = threading.Lock()
                cls._local_locks[path] = lock
            return lock

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        unlock_error: OSError | None = None
        try:
            try:
                _unlock_repository_stream(self._stream)
            except OSError as exc:
                unlock_error = exc
        finally:
            self._stream.close()
            self._local_lock.release()
        if unlock_error is not None:
            raise AgentRepositoryGuardError(
                "仓库 ChangeRun 创建锁释放异常"
            ) from unlock_error

    def __enter__(self) -> RepositoryChangeLock:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.release()
        except AgentRepositoryGuardError:
            if exc is None:
                raise


def _prepare_control_dir(repo: Path) -> Path:
    repo_root = repo.resolve(strict=True)
    process = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    raw = process.stdout.strip()
    if process.returncode != 0 or not raw:
        raise AgentRepositoryGuardError("无法定位 Git common dir")
    common_dir = Path(raw)
    if not common_dir.is_absolute():
        common_dir = repo_root / common_dir
    try:
        common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise AgentRepositoryGuardError("Git common dir 无法解析") from exc
    if not common_dir.is_dir():
        raise AgentRepositoryGuardError("Git common dir 不是目录")
    control_dir = common_dir / "vega"
    _prepare_plain_directory(control_dir, common_dir)
    return control_dir


def _prepare_plain_directory(path: Path, expected_parent: Path) -> None:
    if not os.path.lexists(path):
        try:
            path.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise AgentRepositoryGuardError(
                f"无法创建 Agent 仓库控制目录：{path.name}"
            ) from exc
    if _is_link_or_reparse(path):
        raise AgentRepositoryGuardError(
            f"Agent 仓库控制目录不能是链接或 reparse point：{path.name}"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AgentRepositoryGuardError(
            f"Agent 仓库控制目录无法解析：{path.name}"
        ) from exc
    if not resolved.is_dir() or resolved.parent != expected_parent.resolve(strict=True):
        raise AgentRepositoryGuardError(
            f"Agent 仓库控制目录越过预期边界：{path.name}"
        )


def _open_repository_lock_file(lock_path: Path) -> BinaryIO:
    _assert_lock_path_safe(lock_path)
    descriptor = _open_lock_descriptor(lock_path)
    try:
        _validate_lock_descriptor(lock_path, descriptor)
        stream = os.fdopen(descriptor, "r+b", buffering=0)
    except Exception:
        os.close(descriptor)
        raise
    try:
        _initialize_lock_stream(stream)
    except Exception:
        stream.close()
        raise
    return stream


def _assert_lock_path_safe(lock_path: Path) -> None:
    if os.path.lexists(lock_path) and _is_link_or_reparse(lock_path):
        raise AgentRepositoryGuardError(
            "仓库 ChangeRun 创建锁不能是链接或 reparse point"
        )


def _open_lock_descriptor(lock_path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise AgentRepositoryGuardError(
            "无法打开仓库 ChangeRun 创建锁"
        ) from exc
    try:
        os.set_inheritable(descriptor, False)
    except OSError as exc:
        os.close(descriptor)
        raise AgentRepositoryGuardError(
            "无法打开仓库 ChangeRun 创建锁"
        ) from exc
    return descriptor


def _validate_lock_descriptor(lock_path: Path, descriptor: int) -> None:
    descriptor_stat = os.fstat(descriptor)
    if not stat.S_ISREG(descriptor_stat.st_mode):
        raise AgentRepositoryGuardError("仓库 ChangeRun 创建锁必须是普通文件")
    if descriptor_stat.st_nlink != 1:
        raise AgentRepositoryGuardError("仓库 ChangeRun 创建锁不能是 hardlink")
    path_stat = lock_path.stat()
    if (
        descriptor_stat.st_dev != path_stat.st_dev
        or descriptor_stat.st_ino != path_stat.st_ino
        or path_stat.st_nlink != 1
    ):
        raise AgentRepositoryGuardError("仓库 ChangeRun 创建锁在打开期间被替换")


def _initialize_lock_stream(stream: BinaryIO) -> None:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
    stream.seek(0)


def _lock_repository_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _is_repository_lock_busy(exc: OSError) -> bool:
    return (
        exc.errno in _BUSY_ERRNOS
        or getattr(exc, "winerror", None) in _BUSY_WINERRORS
    )


def _unlock_repository_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)
