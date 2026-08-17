from __future__ import annotations

import ctypes
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ctypes import wintypes

from .windows_job import (
    CREATE_SUSPENDED,
    NamedWindowsJob,
    WindowsJobError,
    WindowsJobProbe,
    create_named_job,
    make_job_name,
    probe_named_job,
    resume_suspended_process,
)

ProcessProbeStatus = Literal["alive", "gone", "unknown"]

_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_WINDOWS_BATCH_SUFFIXES = {".bat", ".cmd"}


@dataclass(frozen=True)
class ProcessProbe:
    status: ProcessProbeStatus
    creation_token: int | None = None


@dataclass(frozen=True)
class ProcessTerminationResult:
    succeeded: bool
    detail: str


def prepare_subprocess_command(
    command: list[str] | str,
    *,
    windows: bool,
) -> list[str] | str:
    """让 Windows batch launcher 可由 subprocess 可靠启动。"""

    if (
        isinstance(command, str)
        or not command
        or not windows
        or Path(command[0]).suffix.lower() not in _WINDOWS_BATCH_SUFFIXES
    ):
        return command
    return [
        os.environ.get("COMSPEC") or "cmd.exe",
        "/d",
        "/v:off",
        "/s",
        "/c",
        subprocess.list2cmdline(command),
    ]


def process_group_options(*, windows: bool) -> dict[str, object]:
    if windows:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def create_windows_job_for_execution(
    execution_id: str | None,
    attach_job: Callable[[str], None],
    *,
    windows: bool,
) -> NamedWindowsJob | None:
    if not windows:
        return None
    if execution_id is None:
        raise WindowsJobError("Windows Job Object 缺少 execution_id")
    job = create_named_job(make_job_name(execution_id))
    try:
        attach_job(job.name)
    except Exception:
        job.close()
        raise
    return job


def add_windows_job_creation_flag(
    process_options: dict[str, object],
    windows_job: NamedWindowsJob | None,
) -> dict[str, object]:
    if windows_job is None:
        return process_options
    process_options["creationflags"] = (
        int(process_options.get("creationflags", 0)) | CREATE_SUSPENDED
    )
    return process_options


def activate_windows_job_process(
    windows_job: NamedWindowsJob | None,
    process_id: int,
    record_child: Callable[[int], None],
) -> None:
    if windows_job is None:
        return
    record_child(process_id)
    windows_job.assign_process_id(process_id)
    resume_suspended_process(process_id)


def close_windows_job(windows_job: NamedWindowsJob | None) -> None:
    if windows_job is not None:
        windows_job.close()


def owned_process_tree_is_active(
    process: subprocess.Popen[bytes],
    windows_job: NamedWindowsJob | None,
    *,
    process_group_alive: Callable[[int], bool] | None = None,
) -> bool:
    process_alive = process.poll() is None
    if windows_job is not None and windows_job.active_process_count() > 0:
        return True
    if process_group_alive is not None and process_group_alive(process.pid):
        return True
    return process_alive


def owned_process_tree_may_be_active(
    process: subprocess.Popen[bytes],
    windows_job: NamedWindowsJob | None,
    *,
    process_group_alive: Callable[[int], bool] | None = None,
) -> bool:
    try:
        return owned_process_tree_is_active(
            process,
            windows_job,
            process_group_alive=process_group_alive,
        )
    except (OSError, ValueError):
        return True


def probe_windows_job(job_name: str) -> WindowsJobProbe:
    return probe_named_job(job_name)


def windows_job_blocks_recovery(probe: WindowsJobProbe | None) -> bool:
    return probe is not None and probe.status in {"active", "unknown"}


def windows_job_recovery_summary(probe: WindowsJobProbe | None) -> str | None:
    if not windows_job_blocks_recovery(probe):
        return None
    assert probe is not None
    detail = (
        f"，active process={probe.active_processes}"
        if probe.active_processes is not None
        else ""
    )
    return (
        "active execution 的 Windows Job Object 仍有成员或状态无法确认"
        f"{detail}；请先使用 vega stop 请求安全停止。"
    )


def is_process_alive(pid: int, *, windows: bool) -> bool:
    if pid <= 0:
        return False
    if windows:
        return probe_windows_process(pid).status != "gone"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def posix_process_group_is_alive(
    process_group_id: int,
    *,
    signal_probe: Callable[[int, int], None],
    states_probe: Callable[[int], list[str] | None],
) -> bool:
    try:
        signal_probe(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    linux_states = states_probe(process_group_id)
    if linux_states:
        # Zombie/dead 进程已不能继续执行或写文件，不应把已终止的进程树误报为存活。
        return any(state not in {"Z", "X", "x"} for state in linux_states)
    return True


def linux_process_group_states(
    process_group_id: int,
    proc_root: Path = Path("/proc"),
) -> list[str] | None:
    if not proc_root.is_dir():
        return None
    states: list[str] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = entry.joinpath("stat").read_text(encoding="utf-8")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            continue
        parsed = _parse_linux_process_group_stat(stat)
        if parsed is None:
            continue
        state, member_group_id = parsed
        if member_group_id == process_group_id:
            states.append(state)
    return states


def _parse_linux_process_group_stat(stat: str) -> tuple[str, int] | None:
    command_end = stat.rfind(")")
    if command_end < 0:
        return None
    fields = stat[command_end + 1 :].split()
    if len(fields) < 3:
        return None
    try:
        return fields[0], int(fields[2])
    except ValueError:
        return None


def probe_process(
    pid: int,
    expected_creation_token: int | None,
    *,
    windows: bool,
    alive_probe: Callable[[int], bool],
) -> ProcessProbe:
    if pid <= 0:
        return ProcessProbe("gone")
    if expected_creation_token is None or not windows:
        return ProcessProbe("alive" if alive_probe(pid) else "gone")
    probe = probe_windows_process(pid)
    if probe.status != "alive":
        return probe
    if probe.creation_token is None:
        return ProcessProbe("unknown")
    if probe.creation_token != expected_creation_token:
        return ProcessProbe("gone", probe.creation_token)
    return probe


def get_process_creation_token(pid: int, *, windows: bool) -> int | None:
    if not windows:
        return None
    probe = probe_windows_process(pid)
    if probe.status != "alive":
        return None
    return probe.creation_token


def probe_windows_process(pid: int) -> ProcessProbe:
    kernel32 = _windows_process_api()
    if kernel32 is None:
        return ProcessProbe("unknown")
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return _open_process_failure_probe()
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return ProcessProbe("unknown")
        if exit_code.value != _STILL_ACTIVE:
            return ProcessProbe("gone")
        return ProcessProbe("alive", _read_process_creation_token(kernel32, handle))
    finally:
        kernel32.CloseHandle(handle)


def _windows_process_api() -> object | None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return None
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _open_process_failure_probe() -> ProcessProbe:
    error_code = ctypes.get_last_error()
    if error_code == _ERROR_INVALID_PARAMETER:
        return ProcessProbe("gone")
    if error_code == _ERROR_ACCESS_DENIED:
        return ProcessProbe("unknown")
    return ProcessProbe("unknown")


def _read_process_creation_token(kernel32: object, handle: object) -> int | None:
    creation_time = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation_time),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        return None
    return (creation_time.dwHighDateTime << 32) | creation_time.dwLowDateTime


def terminate_posix_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    process_alive: Callable[[int], bool],
    process_group_alive: Callable[[int], bool],
    terminate_signal: int,
    kill_signal: int,
) -> ProcessTerminationResult:
    failures: list[str] = []
    if _send_posix_terminate(process.pid, terminate_signal, failures):
        return _confirm_posix_termination(
            process,
            failures,
            process_alive,
            process_group_alive,
        )
    _wait_for_process(
        process,
        grace_seconds,
        failures,
        timeout_message=None,
        error_prefix="等待 owned process 退出失败",
    )
    confirmation = _confirm_posix_termination(
        process,
        [*failures],
        process_alive,
        process_group_alive,
    )
    if confirmation.succeeded:
        return confirmation
    _send_posix_kill(process.pid, kill_signal, failures)
    _wait_for_process(
        process,
        5,
        failures,
        timeout_message="强制终止后最终 wait 超时。",
        error_prefix="强制终止后最终 wait 失败",
    )
    return _confirm_posix_termination(
        process,
        failures,
        process_alive,
        process_group_alive,
    )


def _send_posix_terminate(
    process_group_id: int,
    terminate_signal: int,
    failures: list[str],
) -> bool:
    try:
        os.killpg(process_group_id, terminate_signal)
    except ProcessLookupError:
        return True
    except OSError as exc:
        failures.append(f"发送 SIGTERM 失败：{exc}")
    return False


def _send_posix_kill(
    process_group_id: int,
    kill_signal: int,
    failures: list[str],
) -> None:
    try:
        os.killpg(process_group_id, kill_signal)
    except ProcessLookupError:
        pass
    except OSError as exc:
        failures.append(f"发送 SIGKILL 失败：{exc}")


def _confirm_posix_termination(
    process: subprocess.Popen[bytes],
    failures: list[str],
    process_alive_probe: Callable[[int], bool],
    process_group_alive_probe: Callable[[int], bool],
) -> ProcessTerminationResult:
    process_alive = _posix_process_alive(process, failures, process_alive_probe)
    process_group_alive = _posix_process_group_alive(
        process.pid,
        failures,
        process_group_alive_probe,
    )
    if process_alive:
        failures.append(f"owned process PID {process.pid} 仍存活。")
    if process_group_alive:
        failures.append(f"owned process group {process.pid} 仍存活。")
    return _termination_result(failures, process_alive or process_group_alive)


def _posix_process_alive(
    process: subprocess.Popen[bytes],
    failures: list[str],
    alive_probe: Callable[[int], bool],
) -> bool:
    try:
        process_alive = process.poll() is None
        if process_alive:
            process_alive = alive_probe(process.pid)
        return process_alive
    except Exception as exc:
        failures.append(f"无法确认 owned process PID 是否存活：{exc}")
        return True


def _posix_process_group_alive(
    process_group_id: int,
    failures: list[str],
    alive_probe: Callable[[int], bool],
) -> bool:
    try:
        return alive_probe(process_group_id)
    except OSError as exc:
        failures.append(f"无法确认 owned process group 是否存活：{exc}")
        return True


def _wait_for_process(
    process: subprocess.Popen[bytes],
    timeout: float,
    failures: list[str],
    *,
    timeout_message: str | None,
    error_prefix: str,
) -> None:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if timeout_message is not None:
            failures.append(timeout_message)
    except OSError as exc:
        failures.append(f"{error_prefix}：{exc}")


def _termination_result(
    failures: list[str],
    blocked: bool,
) -> ProcessTerminationResult:
    if blocked:
        return ProcessTerminationResult(False, "；".join(failures))
    if failures:
        return ProcessTerminationResult(
            True,
            "owned process tree 已确认退出；终止过程中出现非致命诊断："
            + "；".join(failures),
        )
    return ProcessTerminationResult(True, "owned process tree 已确认退出。")
