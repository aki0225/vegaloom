from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_contract import AgentDecision
from .agent_persistence import AgentArtifactError, load_agent_state
from .agent_runtime_support import load_agent_bundle
from .agent_status_card import build_agent_status_payload
from .decision import DecisionStore


def apply_agent_projection(
    workspace: Path,
    run: str,
    run_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    """把 Agent 的实时证据投影合并到通用 status payload。"""

    _, agent_state, agent_plan, metadata = load_agent_bundle(workspace, run)
    projection = build_agent_status_payload(run_dir, agent_state, agent_plan)
    state["repo_path"] = metadata["repo_path"]
    state["persisted_agent_state"] = agent_state.model_dump(mode="json")
    state.update(
        {
            "recorded_agent_phase": projection["recorded_phase"],
            "recorded_terminal_status": projection["recorded_terminal_status"],
            "agent_phase": projection["effective_phase"],
            "terminal_status": projection["effective_terminal_status"],
            "allowed_actions": projection["allowed_actions"],
            "verification": projection["verification"],
            "risk": projection["risk"],
            "review": projection["review"],
            "changed_files": projection["changed_files"],
            "unknown_file_count": projection["unknown_file_count"],
            "evidence_health": projection["evidence_health"],
            "workspace_current": projection["workspace_current"],
            "commit_recommended": projection["commit_recommended"],
            "supervisor_evidence": projection["supervisor_evidence"],
            "integrity_warning": projection["integrity_warning"],
            "history_note": projection["history_note"],
            "provider_sessions": projection["provider_sessions"],
            "provider_session_warning": projection[
                "provider_session_warning"
            ],
        }
    )
    if projection["effective_phase"] != projection["recorded_phase"]:
        state["status"] = "needs_human"
        state["current_step"] = "evidence_invalid"
    return projection


def combined_decisions(
    run_dir: Path,
    *,
    include_agent: bool,
) -> list[dict[str, Any]]:
    entries = [
        entry.model_dump(mode="json")
        for entry in DecisionStore(run_dir).list()
    ]
    if include_agent:
        entries.extend(
            entry.model_dump(mode="json")
            for entry in _agent_decisions(run_dir)
        )
    entries.sort(key=lambda entry: str(entry.get("created_at", "")))
    return entries


def payload_fields(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_run_kind": state.get("agent_run_kind"),
        "accepted_checkpoint_sha": state.get("accepted_checkpoint_sha"),
        "active_candidate_sha": state.get("active_candidate_sha"),
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
            decision = AgentDecision.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"Agent Decision 无法验证：{path.name}"
            ) from exc
        if path.name != f"{decision.decision_id}.json":
            raise ValueError("Agent Decision 文件名与 decision_id 不一致")
        result.append(decision)
    return result
