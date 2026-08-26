from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentState
from .agent_persistence import load_agent_checkpoint


def inherited_failed_attempts(
    run_dir: Path,
    state: AgentState,
    *,
    allow_work_item_advance: bool,
) -> list[str]:
    """只在同一 Work Item 内继承失败历史；推进必须由显式路由授权。"""

    if state.latest_checkpoint_id is None:
        return []
    previous = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (previous.run_id, previous.checkpoint_id) != (
        state.run_id,
        state.latest_checkpoint_id,
    ):
        raise ValueError("前序 Checkpoint 与当前 Agent State 不一致，拒绝继承失败历史")
    if previous.current_work_item == state.current_work_item:
        return previous.failed_attempts
    if not allow_work_item_advance:
        raise ValueError("前序 Checkpoint 属于不同 Work Item，拒绝隐式推进")
    return []
