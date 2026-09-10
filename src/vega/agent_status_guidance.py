from __future__ import annotations

from typing import Any


def agent_artifact_names(state: dict[str, Any]) -> list[str]:
    names = [
        "agent-state.json",
        "agent-plan.json",
        "status-card.md",
        "task-brief.md",
        "task-brief-manifest.json",
        "trace.jsonl",
        "provider-sessions.json",
    ]
    checkpoint_id = state.get("latest_checkpoint_id")
    if isinstance(checkpoint_id, str):
        names.append(f"checkpoints/{checkpoint_id}.json")
    if state.get("agent_run_kind") == "change":
        persisted = state.get("persisted_agent_state")
        if (
            isinstance(persisted, dict)
            and persisted.get("contract_revision") is None
        ):
            names.extend(
                [
                    "planning-request.json",
                    "project-context.md",
                    "planning-proposal.json",
                    "planning-proposal.md",
                ]
            )
        else:
            names.extend(
                [
                    "change-contract.json",
                    "execution-plan.json",
                    "plan-card.md",
                    "agent-final-report.json",
                    "agent-final-report.md",
                ]
            )
    return names
