from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .execution_process import (
    ProcessTerminationResult,
    is_process_alive,
    linux_process_group_states,
    posix_process_group_is_alive,
    terminate_posix_process,
)
from .windows_process_termination import terminate_windows_process

_POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


class AppServerInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: str
    run_dir: str
    role_key: str
    repo_path: str
    sandbox: str
    output_schema: dict[str, Any] | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    global_args: list[str] = Field(default_factory=list)
    server_args: list[str] = Field(default_factory=list)


def install_parent_termination_handler(*, windows: bool) -> None:
    """让 POSIX helper 收到终止信号时先清理独立 App Server 进程组。"""

    if windows:
        return
    signal.signal(signal.SIGTERM, _raise_system_exit)


def app_server_process_options(*, windows: bool) -> dict[str, object]:
    """让 App Server 子树不占用当前终端，并可按进程树终止。"""

    if windows:
        return {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        }
    return {"start_new_session": True}


def terminate_app_server_tree(
    process: subprocess.Popen[str],
    *,
    windows: bool,
) -> ProcessTerminationResult:
    if process.poll() is not None and (
        windows or not _posix_process_group_alive(process.pid)
    ):
        return ProcessTerminationResult(True, "App Server 根进程已经退出。")
    if windows:
        return terminate_windows_process(
            process, 2, None, subprocess.run  # type: ignore[arg-type]
        )
    return terminate_posix_process(
        process,  # type: ignore[arg-type]
        2,
        _posix_root_process_alive,
        _posix_process_group_alive,
        signal.SIGTERM,
        _POSIX_KILL_SIGNAL,
    )


def _posix_root_process_alive(process_id: int) -> bool:
    return is_process_alive(process_id, windows=False)


def _posix_process_group_alive(process_group_id: int) -> bool:
    return posix_process_group_is_alive(
        process_group_id,
        signal_probe=os.killpg,
        states_probe=linux_process_group_states,
    )


def _raise_system_exit(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)
