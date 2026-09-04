from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .agent_contract import AgentState
from .models import LoopAutomationState
from .progress import safe_run_step
from .run_utils import resolve_run_dir


_LIVE_CHILD_WAITING = "等待子流程状态"


@dataclass(frozen=True)
class AgentChildStatusSnapshot:
    """一次状态查询中复用的可信 child 视图。"""

    child_run: str | None
    child_dir: Path | None
    child_state: LoopAutomationState | None
    live_stage: str | None


def read_live_child_stage(run_dir: Path, state: AgentState) -> str | None:
    """读取 active assist child 的当前步骤，仅用于状态展示。"""

    return capture_trusted_child_status(
        run_dir,
        state,
        state.active_child_run,
    ).live_stage


def capture_trusted_child_status(
    run_dir: Path,
    state: AgentState,
    child_run: str | None,
) -> AgentChildStatusSnapshot:
    """读取一次可信 child 状态，供阶段、队列和状态卡共同使用。"""

    if child_run is None:
        return AgentChildStatusSnapshot(None, None, None, None)
    workspace = run_dir.parent.parent
    try:
        child_dir = resolve_run_dir(workspace, child_run)
    except FileNotFoundError as exc:
        if "run 不存在于当前 workspace" in str(exc):
            return AgentChildStatusSnapshot(
                child_run,
                None,
                None,
                _LIVE_CHILD_WAITING if _is_live_child(state, child_run) else None,
            )
        raise ValueError(
            f"child `{child_run}` 的路径无法安全解析；已拒绝展示。"
        ) from exc
    child_state = _load_child_state(child_dir, child_run)
    if child_state is None:
        return AgentChildStatusSnapshot(
            child_run,
            child_dir,
            None,
            _LIVE_CHILD_WAITING if _is_live_child(state, child_run) else None,
        )
    _require_child_repo_binding(run_dir, child_run, child_state)
    if _is_live_child(state, child_run) and not child_state.current_step.strip():
        raise ValueError(
            "active child state.json 缺少有效 current_step；已拒绝展示。"
        )
    return AgentChildStatusSnapshot(
        child_run,
        child_dir,
        child_state,
        (
            safe_run_step(child_state.current_step)
            if _is_live_child(state, child_run)
            else None
        ),
    )


def _is_live_child(state: AgentState, child_run: str) -> bool:
    return (
        state.phase in {"acting", "observing", "needs_human"}
        and state.active_child_run == child_run
    )


def _load_child_state(
    child_dir: Path,
    child_run: str,
) -> LoopAutomationState | None:
    state_path = child_dir / "state.json"
    if not state_path.exists():
        return None
    try:
        child_state = LoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(
            f"child `{child_run}` 的 state.json 无法验证；已拒绝展示。"
        ) from exc
    if child_state.run_id != child_run:
        raise ValueError(
            "child state.json 的 run_id 与绑定 child 不一致；已拒绝展示错误证据。"
        )
    return child_state


def _require_child_repo_binding(
    run_dir: Path,
    child_run: str,
    child_state: LoopAutomationState,
) -> None:
    try:
        metadata = json.loads(
            (run_dir / "agent-run.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Agent run `{run_dir.name}` 的 agent-run.json 无法验证；"
            "已拒绝核对 child 仓库身份。"
        ) from exc
    if not isinstance(metadata, dict) or not isinstance(
        metadata.get("repo_path"),
        str,
    ):
        raise ValueError(
            f"Agent run `{run_dir.name}` 缺少可验证的 repo binding；"
            "已拒绝展示 child。"
        )
    try:
        parent_repo = Path(metadata["repo_path"]).resolve()
        child_repo = Path(child_state.repo_path).resolve()
    except (OSError, ValueError) as exc:
        raise ValueError(
            "child 与 Agent run 的 repo binding 无法解析；已拒绝展示。"
        ) from exc
    if child_repo != parent_repo:
        raise ValueError(
            "child 与 Agent run 的仓库身份不一致；已拒绝展示。"
        )
