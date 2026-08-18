from __future__ import annotations

import json
import os

from .execution_control import RunnerExecutionContext
from .execution_paths import ExecutionPathGuard


def prepare_single_writer_environment(
    command: list[str],
    environment: dict[str, str],
    context: RunnerExecutionContext,
) -> str | None:
    """把单 Writer 的临时写入约束到当前 execution。"""

    try:
        temp_root = ExecutionPathGuard(
            context.execution_root,
            context.execution_dir / "worker-temp",
        ).prepare()
    except OSError as exc:
        return f"无法准备 Worker 隔离临时目录：{type(exc).__name__}"

    path_literal = json.dumps(temp_root.as_posix(), ensure_ascii=True)
    command.extend(
        [
            "--config",
            f"sandbox_workspace_write.writable_roots=[{path_literal}]",
        ]
    )
    temp_value = os.fspath(temp_root)
    environment.update(
        {
            "TEMP": temp_value,
            "TMP": temp_value,
            "TMPDIR": temp_value,
        }
    )
    return None
