from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentState
from .agent_persistence import read_agent_trace
from .comparison_binding import require_comparison_binding_from_mapping
from .loop_runtime import LoopAutomationRuntime
from .project_config import ProjectConfig
from .runner import CodexExecRunner
from .workspace_check import ReviewWorkspaceSnapshot


def comparison_binding_from_metadata(
    metadata: dict[str, str],
) -> tuple[str | None, tuple[str, ...]]:
    comparison_base_sha, comparison_paths = require_comparison_binding_from_mapping(
        metadata,
        base_key="comparison_base_revision",
    )
    return comparison_base_sha, comparison_paths


def validate_prepared_workspace(
    snapshot: ReviewWorkspaceSnapshot,
    *,
    expected_fingerprint: str,
    requires_clean_workspace: bool,
) -> None:
    if snapshot.fingerprint != expected_fingerprint:
        raise ValueError("创建 child 前 Workspace 已漂移，必须先重新对账")
    if requires_clean_workspace and (
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


def next_attempt_context(run_dir: Path, state: AgentState) -> tuple[int, bool]:
    trace = read_agent_trace(run_dir / "trace.jsonl")
    epoch_indexes = [
        index
        for index, item in enumerate(trace)
        if item.get("event") in {"plan_approved", "task_card_resumed"}
    ]
    if not epoch_indexes:
        raise ValueError("当前 Plan 缺少可验证的 attempt epoch，拒绝启动 Worker")
    epoch_index = epoch_indexes[-1]
    attempts = sum(
        1
        for item in trace[epoch_index + 1 :]
        if item.get("event") == "worker_dispatch_committed"
        and item.get("work_item") == state.current_work_item
    )
    if attempts >= 2:
        raise ValueError(
            "当前 Work Item 已用完一次初始 attempt 和一次 repair attempt；"
            "必须由人工修改 Plan 或停止任务"
        )
    has_historical_dispatch = any(
        item.get("event") == "worker_dispatch_committed" for item in trace
    )
    requires_clean_workspace = (
        not has_historical_dispatch
        and trace[epoch_index].get("event") == "plan_approved"
    )
    return attempts + 1, requires_clean_workspace


def ensure_isolated_reviewer(
    loop_runtime: object,
    config: ProjectConfig,
) -> None:
    """只为默认 Core Reviewer 注入 MCP 隔离，不覆盖显式测试或替代 runner。"""

    if (
        isinstance(loop_runtime, LoopAutomationRuntime)
        and loop_runtime.reviewer_runner is None
    ):
        loop_runtime.reviewer_runner = CodexExecRunner(
            options=config.runner.codex_exec.reviewer,
            isolate_mcp=True,
        )
