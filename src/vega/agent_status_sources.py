from __future__ import annotations

from pathlib import Path

from .agent_candidate_evidence import matches_accepted_candidate_transition
from .agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentObservation,
    AgentState,
)
from .agent_repository_binding import capture_bound_workspace
from .agent_status_artifacts import (
    checkpoint_ref,
    load_bounded_checkpoint,
    load_bounded_decision,
    load_bounded_observation,
)
from .provider_session import ProviderSessionState, load_provider_sessions
from .workspace_snapshot import ReviewWorkspaceSnapshot


def load_provider_sessions_for_display(
    run_dir: Path,
) -> tuple[ProviderSessionState | None, str | None]:
    try:
        return load_provider_sessions(run_dir), None
    except ValueError:
        return None, "Provider Session 协调状态无法验证；Core 证据不受影响。"


def load_status_decision_for_display(
    run_dir: Path,
    checkpoint: AgentCheckpoint | None,
    *,
    decisions: tuple[AgentDecision, ...] | None = None,
) -> tuple[AgentDecision | None, str | None]:
    """读取与最近 Checkpoint 绑定的 Decision，供状态和解释共用。"""

    if checkpoint is None:
        return None, None
    decision_refs = [
        ref
        for ref in checkpoint.evidence_refs
        if ref.startswith("decisions/")
    ]
    if not decision_refs:
        return None, None
    if len(decision_refs) != 1:
        return None, "最近 Checkpoint 无法唯一定位 Decision。"
    decision_ref = decision_refs[0]
    if decisions is None:
        try:
            decision, _ = load_bounded_decision(run_dir, decision_ref)
        except ValueError:
            return None, "最近 Decision 无法验证。"
    else:
        matched = [
            item
            for item in decisions
            if decision_ref == f"decisions/{item.decision_id}.json"
        ]
        if len(matched) != 1:
            return None, "最近 Decision 无法从状态快照唯一定位。"
        decision = matched[0]
    observation_ref = f"observations/{decision.observation_id}.json"
    if (
        decision_ref != f"decisions/{decision.decision_id}.json"
        or observation_ref not in checkpoint.evidence_refs
        or not _decision_action_matches_checkpoint(checkpoint, decision)
    ):
        return None, "最近 Decision 与 Checkpoint 的身份或动作绑定不一致。"
    return decision, None


def _decision_action_matches_checkpoint(
    checkpoint: AgentCheckpoint,
    decision: AgentDecision,
) -> bool:
    if decision.selected_action in checkpoint.pending_actions:
        return True
    return (
        checkpoint.phase == "completed"
        and checkpoint.status == "safe"
        and not checkpoint.pending_actions
        and decision.selected_action == "finalize"
    )


def capture_live_workspace(
    run_dir: Path,
) -> tuple[ReviewWorkspaceSnapshot | None, str | None]:
    try:
        return capture_bound_workspace(run_dir), None
    except (OSError, RuntimeError, ValueError):
        return None, "当前 Workspace 无法重新采集或绑定无法验证"


def load_status_checkpoint(
    run_dir: Path,
    state: AgentState,
) -> AgentCheckpoint | None:
    if state.latest_checkpoint_id is None:
        return None
    expected_ref = checkpoint_ref(state.latest_checkpoint_id)
    checkpoint, _ = load_bounded_checkpoint(
        run_dir,
        state.latest_checkpoint_id,
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.checkpoint_id != state.latest_checkpoint_id
        or checkpoint.current_work_item != state.current_work_item
        or expected_ref != f"checkpoints/{checkpoint.checkpoint_id}.json"
    ):
        raise ValueError("最新 Checkpoint 与 Agent State 不一致，拒绝展示状态卡")
    return checkpoint


def load_status_checkpoint_for_display(
    run_dir: Path,
    state: AgentState,
) -> tuple[AgentCheckpoint | None, str | None]:
    """读取展示用 Checkpoint；损坏证据不应让状态查询崩溃。"""

    if state.latest_checkpoint_id is None:
        if state.phase in {
            "ready",
            "acting",
            "observing",
            "needs_human",
            "finalizing",
            "completed",
            "stopped",
        }:
            return None, "最近 Checkpoint 缺失。"
        return None, None
    try:
        return load_status_checkpoint(run_dir, state), None
    except ValueError:
        return None, "最近 Checkpoint 缺失、损坏或与 Agent State 绑定不一致。"


def load_status_observation(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> AgentObservation | None:
    if checkpoint is None:
        return None
    refs = [
        ref
        for ref in checkpoint.evidence_refs
        if ref.startswith("observations/")
    ]
    if not refs:
        return None
    if len(refs) != 1:
        raise ValueError("最新 Checkpoint 无法唯一定位 Observation")
    try:
        observation, _ = load_bounded_observation(run_dir, refs[0])
    except ValueError as exc:
        raise ValueError("最新 Observation 无法验证，拒绝展示状态卡") from exc
    if (
        refs[0] != f"observations/{observation.observation_id}.json"
        or (
            observation.work_item_id != state.current_work_item
            and not matches_accepted_candidate_transition(
                run_dir,
                state,
                checkpoint,
                observation,
            )
        )
        or observation.workspace_fingerprint != checkpoint.workspace_fingerprint
    ):
        raise ValueError("最新 Observation 与 Checkpoint 不一致，拒绝展示状态卡")
    return observation


def load_status_observation_for_display(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> tuple[AgentObservation | None, str | None]:
    """读取展示用 Observation；损坏证据只降级展示，不改变权威 State。"""

    try:
        return load_status_observation(run_dir, state, checkpoint), None
    except ValueError:
        return None, "最近 Observation 缺失、损坏或与 Checkpoint 绑定不一致。"
