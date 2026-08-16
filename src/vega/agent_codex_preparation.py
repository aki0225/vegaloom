from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentState
from .agent_persistence import read_agent_trace
from .comparison_binding import comparison_binding_from_mapping
from .workspace_check import ReviewWorkspaceSnapshot


def comparison_binding_from_metadata(
    metadata: dict[str, str],
) -> tuple[str | None, tuple[str, ...]]:
    comparison_base_sha, comparison_paths, _ = comparison_binding_from_mapping(
        metadata,
        base_key="comparison_base_revision",
    )
    return comparison_base_sha, comparison_paths


def validate_prepared_workspace(
    snapshot: ReviewWorkspaceSnapshot,
    *,
    expected_fingerprint: str,
    attempt_number: int,
) -> None:
    if snapshot.fingerprint != expected_fingerprint:
        raise ValueError("创建 child 前 Workspace 已漂移，必须先重新对账")
    if attempt_number == 1 and (
        snapshot.staged_diff.strip()
        or snapshot.unstaged_diff.strip()
        or snapshot.untracked_files
    ):
        raise ValueError(
            "Gate 2B 首次真实 Worker 要求干净 Workspace；"
            "已有 Diff 的跨机器接力和累计归因属于后续 Gate"
        )


def read_task_brief(run_dir: Path) -> str:
    try:
        content = (run_dir / "task-brief.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("无法读取当前 Task Brief") from exc
    if not content.strip():
        raise ValueError("当前 Task Brief 为空")
    return content


def next_attempt_number(run_dir: Path, state: AgentState) -> int:
    attempts = sum(
        1
        for item in read_agent_trace(run_dir / "trace.jsonl")
        if item.get("event") == "worker_dispatch_committed"
        and item.get("work_item") == state.current_work_item
    )
    if attempts >= 2:
        raise ValueError(
            "当前 Work Item 已用完一次初始 attempt 和一次 repair attempt；"
            "必须由人工修改 Plan 或停止任务"
        )
    return attempts + 1
