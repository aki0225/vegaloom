from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentStatusCard,
)
from .agent_provider_explain import provider_interaction_projection
from .agent_persistence import AgentArtifactError, load_agent_state
from .agent_child_status import (
    AgentChildStatusSnapshot,
    capture_trusted_child_status,
)
from .agent_run_status import (
    trusted_worker_status,
)
from .agent_runtime_support import load_agent_bundle
from .agent_status_card import _build_status_card, render_status_card
from .agent_status_guidance import agent_artifact_names, agent_next_steps
from .agent_status_sources import (
    capture_live_workspace,
    load_provider_sessions_for_display,
    load_status_checkpoint_for_display,
    load_status_decision_for_display,
    load_status_observation_for_display,
)
from .agent_status_artifacts import load_bounded_decision
from .decision import DecisionStore
from .models import LoopAutomationState
from .provider_session import PendingInteraction, ProviderSessionState
from .provider_session_projection import session_status_projection_from_state
from .review_queue_contract import review_queue_status_payload
from .run_execution_status import latest_execution_payload
from .workspace_snapshot import ReviewWorkspaceSnapshot


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


@dataclass(frozen=True)
class AgentStatusProjection:
    """一次状态查询共享的只读证据视图。"""

    state: AgentState
    plan: AgentPlan
    card: AgentStatusCard
    payload: dict[str, object]
    checkpoint: AgentCheckpoint | None
    decision: AgentDecision | None
    decision_history: tuple[dict[str, Any], ...]
    decision_issue: str | None
    observation: AgentObservation | None
    workspace: ReviewWorkspaceSnapshot | None
    workspace_issue: str | None
    provider_sessions: ProviderSessionState | None
    provider_interactions: tuple[PendingInteraction, ...]
    provider_warnings: tuple[str, ...]
    last_child_run: str | None
    execution: dict[str, Any] | None
    review_queue: dict[str, object]
    next_steps: tuple[str, ...]
    key_artifacts: tuple[str, ...]
    repo_path: str | None = None


def capture_status_workspace(
    run_dir: Path,
) -> tuple[ReviewWorkspaceSnapshot | None, str | None]:
    """采集一次状态展示所需的 Workspace 视图。"""

    return capture_live_workspace(run_dir)


def build_agent_status_projection(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    workspace_capture: tuple[ReviewWorkspaceSnapshot | None, str | None] | None = None,
    repo_path: str | None = None,
) -> AgentStatusProjection:
    """一次性读取并构建 Agent 状态及解释需要的共享证据。"""

    checkpoint, checkpoint_issue = load_status_checkpoint_for_display(
        run_dir,
        state,
    )
    observation, observation_issue = load_status_observation_for_display(
        run_dir,
        state,
        checkpoint,
    )
    live_workspace, workspace_issue = (
        capture_live_workspace(run_dir)
        if workspace_capture is None
        else workspace_capture
    )
    provider_state, provider_issue = load_provider_sessions_for_display(run_dir)
    session_rows = (
        []
        if provider_state is None
        else session_status_projection_from_state(provider_state)[0]
    )
    provider_interactions, provider_warnings = provider_interaction_projection(
        run_dir,
        state,
        provider_sessions=provider_state,
        provider_issue=provider_issue,
    )
    agent_decisions = tuple(_agent_decisions(run_dir))
    decision, decision_issue = load_status_decision_for_display(
        run_dir,
        checkpoint,
        decisions=agent_decisions,
    )
    if checkpoint_issue is not None and decision_issue is None:
        decision_issue = checkpoint_issue
    worker_label, last_child_run = trusted_worker_status(
        run_dir,
        state,
        observation=observation,
        checkpoint_status=checkpoint.status if checkpoint else None,
    )
    child_status = capture_trusted_child_status(
        run_dir,
        state,
        last_child_run,
    )
    card = _build_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
        live_workspace=live_workspace,
        workspace_checked=True,
        workspace_issue=workspace_issue,
        checkpoint_issue=checkpoint_issue,
        observation_issue=observation_issue,
        provider_rows=session_rows,
        provider_warning=provider_issue,
        worker_label=worker_label,
        live_child_stage=child_status.live_stage,
        live_child_checked=True,
        next_step=(
            checkpoint.reason
            if checkpoint is not None and state.phase in {"ready", "needs_human"}
            else None
        ),
    )
    guidance_state = _guidance_state(
        state,
        card,
        last_child_run=last_child_run,
    )
    execution = latest_execution_payload(
        run_dir,
        _PHASE_STATUS[card.phase],
    )
    review_queue = _review_queue_projection(run_dir, child_status)
    next_steps = tuple(agent_next_steps(run_dir, guidance_state))
    key_artifacts = tuple(
        _existing_agent_artifacts(run_dir, guidance_state)
    )
    payload = card.model_dump(mode="json")
    payload.update(
        {
            "recorded_phase": state.phase,
            "recorded_terminal_status": state.terminal_status,
            "effective_phase": card.phase,
            "effective_terminal_status": card.terminal_status,
            "last_child_run": last_child_run,
            "execution": execution,
            "next_steps": list(next_steps),
            "key_artifacts": list(key_artifacts),
            **review_queue,
        }
    )
    return AgentStatusProjection(
        state=state,
        plan=plan,
        card=card,
        payload=payload,
        checkpoint=checkpoint,
        decision=decision,
        decision_history=tuple(
            _combined_decision_entries(run_dir, agent_decisions)
        ),
        decision_issue=decision_issue,
        observation=observation,
        workspace=live_workspace,
        workspace_issue=workspace_issue,
        provider_sessions=provider_state,
        provider_interactions=tuple(provider_interactions),
        provider_warnings=tuple(provider_warnings),
        last_child_run=last_child_run,
        execution=execution,
        review_queue=review_queue,
        next_steps=next_steps,
        key_artifacts=key_artifacts,
        repo_path=repo_path,
    )


def _guidance_state(
    state: AgentState,
    card: AgentStatusCard,
    *,
    last_child_run: str | None,
) -> dict[str, Any]:
    return {
        "agent_phase": card.phase,
        "agent_run_kind": state.run_kind,
        "current_work_item": state.current_work_item,
        "latest_checkpoint_id": state.latest_checkpoint_id,
        "last_child_run": last_child_run,
        "persisted_agent_state": state.model_dump(mode="json"),
    }


def _review_queue_projection(
    run_dir: Path,
    child_status: AgentChildStatusSnapshot,
) -> dict[str, object]:
    if child_status.child_dir is None:
        return review_queue_status_payload(run_dir)
    if child_status.child_state is None:
        return {
            "review_queue_status": "invalid",
            "review_queue_completed": 0,
            "review_queue_total": 0,
        }
    return review_queue_status_payload(
        child_status.child_dir,
        iteration_number=_latest_iteration_number(child_status.child_state),
    )


def _latest_iteration_number(
    state: LoopAutomationState,
) -> int | None:
    candidates = [
        item.iteration
        for item in state.iterations
        if item.iteration > 0
    ]
    if state.current_iteration > 0:
        candidates.append(state.current_iteration)
    return max(candidates) if candidates else None


def _existing_agent_artifacts(
    run_dir: Path,
    state: dict[str, Any],
) -> list[str]:
    root = run_dir.resolve()
    result: list[str] = []
    for name in agent_artifact_names(state):
        path = (run_dir / name).resolve()
        if path.is_relative_to(root) and path.exists():
            result.append(str(path))
    decisions = (run_dir / "decisions.jsonl").resolve()
    if decisions.is_relative_to(root) and decisions.exists():
        result.append(str(decisions))
    return list(dict.fromkeys(result))


def read_status_card(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    status_projection: AgentStatusProjection | None = None,
) -> str:
    """按当前 Artifact 渲染状态卡，或复用调用方已构建的共享投影。"""

    projection = status_projection or build_agent_status_projection(
        run_dir,
        state,
        plan,
    )
    if projection.state != state or projection.plan != plan:
        raise ValueError("状态卡投影与 Agent State/Plan 身份不一致。")
    return render_status_card(projection.card)


def build_agent_status_payload(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
) -> dict[str, object]:
    """生成文本与 JSON 共用的经验证状态投影。"""

    return build_agent_status_projection(run_dir, state, plan).payload


def apply_agent_projection(
    workspace: Path,
    run: str,
    run_dir: Path,
    state: dict[str, Any],
    *,
    projection: AgentStatusProjection | None = None,
) -> AgentStatusProjection:
    """把 Agent 的实时证据投影合并到通用 status payload。"""

    if projection is None:
        _, agent_state, agent_plan, metadata = load_agent_bundle(workspace, run)
        projection = build_agent_status_projection(
            run_dir,
            agent_state,
            agent_plan,
            repo_path=(
                metadata.get("repo_path")
                if isinstance(metadata.get("repo_path"), str)
                else None
            ),
        )
    elif projection.state.run_id != run_dir.name:
        raise ValueError("Agent 状态投影与 run 目录身份不一致。")
    state["repo_path"] = projection.repo_path
    state["persisted_agent_state"] = projection.state.model_dump(mode="json")
    state.update(
        {
            "recorded_agent_phase": projection.payload["recorded_phase"],
            "recorded_terminal_status": projection.payload[
                "recorded_terminal_status"
            ],
            "agent_phase": projection.payload["effective_phase"],
            "terminal_status": projection.payload["effective_terminal_status"],
            "allowed_actions": projection.payload["allowed_actions"],
            "verification": projection.payload["verification"],
            "risk": projection.payload["risk"],
            "review": projection.payload["review"],
            "changed_files": projection.payload["changed_files"],
            "unknown_file_count": projection.payload["unknown_file_count"],
            "evidence_health": projection.payload["evidence_health"],
            "workspace_current": projection.payload["workspace_current"],
            "commit_recommended": projection.payload["commit_recommended"],
            "supervisor_evidence": projection.payload["supervisor_evidence"],
            "integrity_warning": projection.payload["integrity_warning"],
            "history_note": projection.payload["history_note"],
            "provider_sessions": projection.payload["provider_sessions"],
            "provider_session_warning": projection.payload[
                "provider_session_warning"
            ],
            "last_child_run": projection.last_child_run,
            "brief_run": projection.last_child_run,
            "live_child_stage": projection.card.live_child_stage,
            "execution": projection.execution,
            "next_steps": list(projection.next_steps),
            "key_artifacts": list(projection.key_artifacts),
            **projection.review_queue,
        }
    )
    if (
        projection.payload["effective_phase"]
        != projection.payload["recorded_phase"]
    ):
        state["status"] = "needs_human"
        state["current_step"] = "evidence_invalid"
    return projection


def combined_decisions(
    run_dir: Path,
    *,
    include_agent: bool,
) -> list[dict[str, Any]]:
    agent_decisions = tuple(_agent_decisions(run_dir)) if include_agent else ()
    return _combined_decision_entries(run_dir, agent_decisions)


def _combined_decision_entries(
    run_dir: Path,
    agent_decisions: tuple[AgentDecision, ...],
) -> list[dict[str, Any]]:
    entries = [
        entry.model_dump(mode="json")
        for entry in DecisionStore(run_dir).list()
    ]
    entries.extend(
        entry.model_dump(mode="json")
        for entry in agent_decisions
    )
    entries.sort(key=lambda entry: str(entry.get("created_at", "")))
    return entries


def payload_fields(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_run_kind": state.get("agent_run_kind"),
        "accepted_checkpoint_sha": state.get("accepted_checkpoint_sha"),
        "active_candidate_sha": state.get("active_candidate_sha"),
        "active_planning_execution_id": state.get(
            "active_planning_execution_id"
        ),
        "persisted_agent_state": state.get("persisted_agent_state"),
        "recorded_agent_phase": state.get("recorded_agent_phase"),
        "recorded_terminal_status": state.get("recorded_terminal_status"),
        "verification": state.get("verification"),
        "risk": state.get("risk"),
        "review": state.get("review"),
        "changed_files": state.get("changed_files"),
        "unknown_file_count": state.get("unknown_file_count"),
        "evidence_health": state.get("evidence_health"),
        "workspace_current": state.get("workspace_current"),
        "commit_recommended": state.get("commit_recommended"),
        "supervisor_evidence": state.get("supervisor_evidence"),
        "integrity_warning": state.get("integrity_warning"),
        "history_note": state.get("history_note"),
        "provider_sessions": state.get("provider_sessions"),
        "provider_session_warning": state.get("provider_session_warning"),
    }


def fallback_agent_selection_state(run_dir: Path) -> dict[str, Any]:
    """Trace 损坏时保留父 Agent 的选择信息，不把 child 当成 latest。"""

    try:
        state = load_agent_state(run_dir / "agent-state.json")
    except AgentArtifactError:
        return {"_run_kind": "agent", "automation_mode": None}
    return {
        "_run_kind": "agent",
        "automation_mode": None,
        "run_id": state.run_id,
        "active_child_run": state.active_child_run,
        "last_child_run": state.active_child_run,
        "brief_run": state.active_child_run,
    }


def _agent_decisions(run_dir: Path) -> list[AgentDecision]:
    decisions_dir = run_dir / "decisions"
    if not decisions_dir.exists():
        return []
    result: list[AgentDecision] = []
    for path in sorted(decisions_dir.glob("decision-*.json")):
        try:
            decision, _ = load_bounded_decision(
                run_dir,
                path.relative_to(run_dir).as_posix(),
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Agent Decision 无法验证：{path.name}"
            ) from exc
        if path.name != f"{decision.decision_id}.json":
            raise ValueError("Agent Decision 文件名与 decision_id 不一致")
        result.append(decision)
    return result
