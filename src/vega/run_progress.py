from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_run_status import agent_progress_items
from .progress import RunProgressLog


def progress_items_for_run(
    workspace: Path,
    run_dir: Path,
    status_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    if status_payload["kind"] == "agent":
        return agent_progress_items(workspace, run_dir, status_payload)
    return RunProgressLog(run_dir).read()
