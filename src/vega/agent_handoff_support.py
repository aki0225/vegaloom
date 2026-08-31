from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
    utc_now,
)
from .agent_handoff_safety import (
    TaskCardError,
    prepare_task_card_root,
    require_plain_task_card_root,
    require_plain_task_card_tree,
)
from .agent_persistence import read_agent_trace
from .agent_task_card import (
    discover_local_handoff_task_cards,
    load_task_card,
)

_HANDOFF_PHASES = frozenset({"ready", "needs_human", "stopped"})


def validate_handoff_bindings(state: AgentState) -> None:
    if state.handoff_status != "none":
        raise ValueError("当前 Agent run 已经生成 Handoff，拒绝重复发布 Task Card")
    if state.active_child_run or state.active_operation_id:
        raise ValueError("Writer 仍处于 active binding；先 stop 并完成 recover 对账")
    if state.active_planning_execution_id is not None:
        raise ValueError("Planning Turn 仍在执行；先 stop 并等待只读进程终态")
    if not state.current_work_item:
        raise ValueError("当前 Agent run 没有可交接的 Work Item")
    if state.latest_checkpoint_id is None:
        raise ValueError("当前 Agent run 缺少最近 Checkpoint，拒绝生成 Handoff")


def validate_handoff_state(
    state: AgentState,
    plan: AgentPlan,
    *,
    planning: bool,
) -> None:
    if not planning and state.phase not in _HANDOFF_PHASES:
        raise ValueError(
            f"当前阶段 {state.phase} 不能生成 Handoff；必须先停止调度并完成现场对账"
        )
    if not planning and not plan.approval_is_current():
        raise ValueError("只有当前已批准 Plan 才能生成 Handoff")


def latest_observation(
    run_dir: Path,
) -> tuple[AgentObservation | None, str]:
    try:
        trace = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        return None, utc_now()
    for item in reversed(trace):
        refs = item.get("artifact_refs")
        if not isinstance(refs, list):
            continue
        for ref in reversed(refs):
            if not isinstance(ref, str) or not ref.startswith("observations/"):
                continue
            path = run_dir / ref
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                observation = AgentObservation.model_validate(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            timestamp = item.get("ts")
            return observation, timestamp if isinstance(timestamp, str) else utc_now()
    return None, utc_now()


def current_work_item(plan: AgentPlan, state: AgentState) -> AgentWorkItem:
    try:
        return next(
            item for item in plan.work_items if item.work_item_id == state.current_work_item
        )
    except StopIteration as exc:
        raise ValueError("当前 Work Item 不属于已批准 Plan") from exc


def task_card_status(
    state: AgentState,
    handoff_status: str,
    *,
    planning: bool,
) -> str:
    if planning:
        return "planning"
    if handoff_status == "handoff_blocked":
        return "needs_human"
    if state.phase == "stopped":
        return "ready"
    if state.phase not in {"ready", "needs_human"}:
        raise ValueError(f"当前阶段不能写入 Task Card：{state.phase}")
    return state.phase


def metadata_revision(metadata: dict[str, object], fallback: str) -> str:
    revision = metadata.get("base_revision")
    return revision if isinstance(revision, str) and revision else fallback


def task_card_path(repo: Path, task_id: str) -> Path:
    date = datetime.now(UTC)
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:48] or "task"
    root = require_plain_task_card_root(repo, date.strftime("%Y-%m"))
    base = root / f"{date.strftime('%Y-%m-%d')}-{slug}-handoff.md"
    candidate = base
    suffix = 2
    while os.path.lexists(candidate):
        candidate = root / f"{date.strftime('%Y-%m-%d')}-{slug}-handoff-{suffix:02d}.md"
        suffix += 1
    return candidate


def prepare_task_card_parent(repo: Path, card_path: Path) -> None:
    prepared_root = prepare_task_card_root(repo, card_path.parent.name)
    if prepared_root != card_path.parent:
        raise TaskCardError("Task Card 目录与预期路径不一致")


def ensure_no_existing_handoff(
    repo: Path,
    task_id: str,
    branch: str,
    *,
    allowed_existing: str | None = None,
) -> None:
    task_root = repo / ".vega" / "tasks"
    if not os.path.lexists(task_root):
        return
    require_plain_task_card_tree(repo, task_root)
    for path in discover_local_handoff_task_cards(repo, branch=branch):
        card = load_task_card(path)
        if (
            card.task_id == task_id
            and path.relative_to(repo).as_posix() != allowed_existing
        ):
            raise TaskCardError(
                "当前任务和分支已存在未终止 Handoff Task Card；"
                "请先人工处理旧卡，拒绝生成重复交接"
            )


def compact(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in unique:
            unique.append(text)
    return unique
