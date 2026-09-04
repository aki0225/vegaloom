from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .agent_cli_snapshot_token import read_agent_snapshot_token
from .agent_explain import AgentExplanation, build_agent_explanation
from .agent_runtime_support import load_agent_bundle
from .agent_run_selection import (
    ACTIVE_CHANGE_PHASES,
    ChangeRunSelectionError,
    select_repository_change_run,
)
from .agent_status_projection import (
    AgentStatusProjection,
    build_agent_status_projection,
    capture_status_workspace,
    read_status_card,
)
from .run_status import run_status_payload
from .run_utils import resolve_run_dir


_PHASE_STATUS = {
    "planning": "created",
    "awaiting_approval": "paused",
    "ready": "paused",
    "acting": "running",
    "observing": "running",
    "needs_human": "needs_human",
    "finalizing": "running",
    "completed": "success",
    "stopped": "stopped",
}


RunSelectionSource = Literal[
    "explicit",
    "repository_active",
    "repository_recent_terminal",
]


@dataclass(frozen=True)
class AgentCliRun:
    workspace: Path
    run_dir: Path
    selection_source: RunSelectionSource


@dataclass(frozen=True)
class AgentCliSnapshot:
    target: AgentCliRun
    status: dict[str, object]
    explanation: AgentExplanation | None
    full_status: str | None = None
    status_projection: AgentStatusProjection | None = None


def resolve_agent_cli_run(
    location: Path,
    run: str | None,
) -> AgentCliRun:
    """解析显式 Run，或按当前 Git 仓库选择唯一 ChangeRun。"""

    if run is not None:
        run_dir = resolve_run_dir(location, run)
        return AgentCliRun(
            workspace=run_dir.parent.parent,
            run_dir=run_dir,
            selection_source="explicit",
        )
    selected = select_repository_change_run(location)
    if selected is None:
        raise ChangeRunSelectionError("当前仓库没有可读取的 ChangeRun。")
    return AgentCliRun(
        workspace=selected.run_dir.parent.parent,
        run_dir=selected.run_dir,
        selection_source=(
            "repository_active"
            if selected.is_active
            else "repository_recent_terminal"
        ),
    )


def build_agent_cli_snapshot(
    target: AgentCliRun,
    *,
    include_full: bool = False,
) -> AgentCliSnapshot:
    """构建父控制面版本绑定的只读快照。

    父 State、Plan、Provider、Checkpoint 和 Workspace 持续变化时重试；
    child、execution 与 review queue 是只读实时提示，在共享投影中各捕获一次，
    允许短暂陈旧，但不参与成功裁决。
    """

    if not (target.run_dir / "agent-state.json").exists():
        payload = run_status_payload(target.workspace, target.run_dir.name)
        payload["selected_run"] = selected_run_payload(target, payload)
        payload["explanation"] = None
        return AgentCliSnapshot(
            target=target,
            status=payload,
            explanation=None,
        )

    for _ in range(2):
        workspace_capture = capture_status_workspace(target.run_dir)
        token_before = read_agent_snapshot_token(
            target.run_dir,
            workspace_capture=workspace_capture,
        )
        _, state, plan, metadata = load_agent_bundle(
            target.workspace,
            target.run_dir.name,
        )
        if state.state_version != token_before.state_version:
            continue
        projection = build_agent_status_projection(
            target.run_dir,
            state,
            plan,
            workspace_capture=workspace_capture,
            repo_path=(
                metadata.get("repo_path")
                if isinstance(metadata.get("repo_path"), str)
                else None
            ),
        )
        payload = _agent_status_payload(target, projection)
        projection.payload.update(payload)
        explanation = build_agent_explanation(
            target.run_dir,
            state,
            plan,
            status_projection=projection,
        )
        full_status = (
            read_status_card(
                target.run_dir,
                state,
                plan,
                status_projection=projection,
            )
            if include_full
            else None
        )
        token_after = read_agent_snapshot_token(target.run_dir)
        if token_before != token_after:
            continue
        payload["selected_run"] = selected_run_payload(target, payload)
        payload["explanation"] = explanation.model_dump(mode="json")
        return AgentCliSnapshot(
            target=target,
            status=payload,
            explanation=explanation,
            full_status=full_status,
            status_projection=projection,
        )
    raise ValueError(
        "Agent State 在状态快照构建期间持续变化；"
        "已拒绝拼接不同版本的 status 与 explain，请稍后重试。"
    )


def _agent_status_payload(
    target: AgentCliRun,
    projection: AgentStatusProjection,
) -> dict[str, object]:
    """只从共享投影生成 CLI 状态，避免再次读取 child、Trace 或队列。"""

    state = projection.state
    card = projection.card
    decisions = list(projection.decision_history)
    run_status = _PHASE_STATUS[card.phase]
    payload = dict(projection.payload)
    payload.update(
        {
            "run_id": target.run_dir.name,
            "run_dir": str(target.run_dir.resolve()),
            "kind": "agent",
            "status": run_status,
            "current_step": (
                "evidence_invalid"
                if card.phase != state.phase
                else state.phase
            ),
            "repo_path": projection.repo_path,
            "recommendation": None,
            "active_child_run": state.active_child_run,
            "last_child_run": projection.last_child_run,
            "last_child_status": None,
            "decision_count": len(decisions),
            "latest_decisions": decisions[-3:],
            "execution": projection.execution,
            "agent_phase": card.phase,
            "current_work_item": state.current_work_item,
            "latest_checkpoint_id": state.latest_checkpoint_id,
            "allowed_actions": list(card.allowed_actions),
            "terminal_status": card.terminal_status,
            "agent_run_kind": state.run_kind,
            "accepted_checkpoint_sha": state.accepted_checkpoint_sha,
            "active_candidate_sha": state.active_candidate_sha,
            "active_planning_execution_id": state.active_planning_execution_id,
            "persisted_agent_state": state.model_dump(mode="json"),
            "recorded_agent_phase": state.phase,
            "recorded_terminal_status": state.terminal_status,
            "live_child_stage": card.live_child_stage,
            "next_steps": list(projection.next_steps),
            "key_artifacts": list(projection.key_artifacts),
            **projection.review_queue,
        }
    )
    return payload


def selected_run_payload(
    target: AgentCliRun,
    status: dict[str, object],
) -> dict[str, object]:
    recorded_phase = status.get("recorded_agent_phase")
    return {
        "run_id": target.run_dir.name,
        "selection_source": target.selection_source,
        "active": (
            recorded_phase in ACTIVE_CHANGE_PHASES
            if isinstance(recorded_phase, str)
            else None
        ),
    }
