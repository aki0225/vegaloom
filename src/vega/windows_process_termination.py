from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .execution_process import (
    ProcessTerminationResult,
    _termination_result,
    _wait_for_process,
)
from .windows_job import NamedWindowsJob, WindowsJobError

TaskkillRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class _WindowsJobTerminationState:
    alive: bool = False
    unknown: bool = False
    active_processes: int | None = None


def terminate_windows_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    windows_job: NamedWindowsJob | None,
    taskkill_runner: TaskkillRunner,
) -> ProcessTerminationResult:
    if windows_job is not None:
        return _terminate_windows_job_process(process, grace_seconds, windows_job)
    return _terminate_windows_taskkill_process(process, grace_seconds, taskkill_runner)


def _terminate_windows_job_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    windows_job: NamedWindowsJob,
) -> ProcessTerminationResult:
    failures: list[str] = []
    try:
        windows_job.terminate()
    except WindowsJobError as exc:
        failures.append(str(exc))
    _wait_after_windows_job_termination(process, grace_seconds, failures)
    return _confirm_windows_termination(process, failures, windows_job=windows_job)


def _wait_after_windows_job_termination(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    failures: list[str],
) -> None:
    try:
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        _kill_process_handle(
            process,
            failures,
            error_prefix="强制终止 owned process handle 失败",
        )
        _wait_for_process(
            process,
            5,
            failures,
            timeout_message="强制终止后最终 wait 超时。",
            error_prefix="强制终止后最终 wait 失败",
        )
    except OSError as exc:
        failures.append(f"等待 owned process 退出失败：{exc}")


def _terminate_windows_taskkill_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    taskkill_runner: TaskkillRunner,
) -> ProcessTerminationResult:
    failures: list[str] = []
    tree_termination_failed = _append_taskkill_failure(
        process.pid,
        force=False,
        timeout=max(1.0, grace_seconds),
        failures=failures,
        runner=taskkill_runner,
    )
    _wait_for_process(
        process,
        grace_seconds,
        failures,
        timeout_message=None,
        error_prefix="等待 owned process 退出失败",
    )
    confirmation = _confirm_windows_termination(
        process,
        [*failures],
        tree_termination_failed=tree_termination_failed,
    )
    if confirmation.succeeded:
        return confirmation
    if tree_termination_failed and process.poll() is not None:
        # taskkill /T 失败且根句柄已退出时，不能再对可能复用的 PID 发起强杀。
        return confirmation
    tree_termination_failed = _force_windows_process_tree(
        process,
        failures,
        taskkill_runner,
    )
    _wait_for_process(
        process,
        5,
        failures,
        timeout_message="强制终止后最终 wait 超时。",
        error_prefix="强制终止后最终 wait 失败",
    )
    return _confirm_windows_termination(
        process,
        failures,
        tree_termination_failed=tree_termination_failed,
    )


def _append_taskkill_failure(
    pid: int,
    *,
    force: bool,
    timeout: float,
    failures: list[str],
    runner: TaskkillRunner,
) -> bool:
    failure = run_windows_taskkill(
        pid,
        force=force,
        timeout=timeout,
        runner=runner,
    )
    if failure is None:
        return False
    failures.append(failure)
    return True


def _force_windows_process_tree(
    process: subprocess.Popen[bytes],
    failures: list[str],
    taskkill_runner: TaskkillRunner,
) -> bool:
    tree_termination_failed = _append_taskkill_failure(
        process.pid,
        force=True,
        timeout=10,
        failures=failures,
        runner=taskkill_runner,
    )
    if process.poll() is None:
        _kill_process_handle(
            process,
            failures,
            error_prefix="强制终止 owned process 失败",
        )
    return tree_termination_failed


def _kill_process_handle(
    process: subprocess.Popen[bytes],
    failures: list[str],
    *,
    error_prefix: str,
) -> None:
    try:
        process.kill()
    except OSError as exc:
        failures.append(f"{error_prefix}：{exc}")


def _confirm_windows_termination(
    process: subprocess.Popen[bytes],
    failures: list[str],
    *,
    tree_termination_failed: bool = False,
    windows_job: NamedWindowsJob | None = None,
) -> ProcessTerminationResult:
    process_alive = _windows_process_handle_alive(process, failures)
    job_state = _windows_job_termination_state(windows_job, failures)
    _append_windows_termination_failures(
        process.pid,
        process_alive,
        job_state,
        tree_termination_failed,
        failures,
    )
    blocked = any(
        (
            process_alive,
            job_state.alive,
            job_state.unknown,
            tree_termination_failed,
        )
    )
    return _termination_result(failures, blocked)


def _windows_process_handle_alive(
    process: subprocess.Popen[bytes],
    failures: list[str],
) -> bool:
    try:
        return process.poll() is None
    except Exception as exc:
        failures.append(f"无法确认 owned process PID 是否存活：{exc}")
        return True


def _windows_job_termination_state(
    windows_job: NamedWindowsJob | None,
    failures: list[str],
) -> _WindowsJobTerminationState:
    if windows_job is None:
        return _WindowsJobTerminationState()
    try:
        active_processes = windows_job.active_process_count()
    except WindowsJobError as exc:
        failures.append(str(exc))
        return _WindowsJobTerminationState(unknown=True)
    return _WindowsJobTerminationState(
        alive=active_processes > 0,
        active_processes=active_processes,
    )


def _append_windows_termination_failures(
    process_id: int,
    process_alive: bool,
    job_state: _WindowsJobTerminationState,
    tree_termination_failed: bool,
    failures: list[str],
) -> None:
    if tree_termination_failed:
        failures.append("taskkill 未能确认 owned process tree 已终止。")
    if process_alive:
        failures.append(f"owned process PID {process_id} 仍存活。")
    if job_state.alive:
        failures.append(
            "Windows Job Object 仍有 "
            f"{job_state.active_processes} 个 active process。"
        )
    if job_state.unknown:
        failures.append("Windows Job Object 是否为空无法确认。")


def run_windows_taskkill(
    pid: int,
    *,
    force: bool,
    timeout: float,
    runner: TaskkillRunner,
) -> str | None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    mode = "强制 taskkill" if force else "taskkill"
    try:
        result = runner(
            command,
            capture_output=True,
            check=False,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{mode} 调用超时。"
    except OSError as exc:
        return f"{mode} 调用失败：{exc}"
    if result.returncode == 0:
        return None
    diagnostic = (result.stderr or result.stdout or "").strip()
    suffix = f"：{diagnostic}" if diagnostic else ""
    return f"{mode} 退出码 {result.returncode}{suffix}"
