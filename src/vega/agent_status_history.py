from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentCheckpoint, AgentObservation, AgentState
from .agent_repository_binding import load_run_metadata
from .agent_task_card import HistoricalGateEvidence, load_task_card


_GATE_LABELS = {
    "not_run": "尚未运行",
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻断",
    "stale": "已过期",
}
_OBSERVATION_GATES = ("verification", "risk", "review")


def historical_gate_evidence(
    observation: AgentObservation | None,
    *,
    source_revision: str,
    recorded_at: str,
) -> list[HistoricalGateEvidence]:
    return [
        HistoricalGateEvidence(
            gate=gate,
            status=getattr(observation, gate) if observation else "not_run",
            source_revision=source_revision,
            recorded_at=recorded_at,
            artifact_refs=list(observation.evidence_refs) if observation else [],
        )
        for gate in _OBSERVATION_GATES
    ]


def status_history_note(
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
    observation: AgentObservation | None,
    *,
    run_dir: Path | None = None,
) -> str | None:
    """说明旧 attempt 的证据为何没有出现在当前门禁状态中。"""

    if observation is not None:
        return None
    notes: list[str] = []
    gate_note = _historical_gate_note(run_dir)
    if gate_note is not None:
        notes.append(gate_note)
    if checkpoint is None or not checkpoint.failed_attempts:
        return "；".join(notes) or None

    count = len(checkpoint.failed_attempts)
    contract_revision = state.contract_revision or 0
    execution_plan_revision = state.execution_plan_revision or 0
    if contract_revision > 1 or execution_plan_revision > 1:
        notes.append(
            (
                f"保留 {count} 个历史失败 attempt；当前门禁只对应 Contract r"
                f"{contract_revision} / Plan r{execution_plan_revision}，"
                "旧结果不能作为本 revision 的通过证据。"
            )
        )
    else:
        notes.append(
            f"保留 {count} 个历史失败 attempt；"
            "当前卡片只显示仍能用于当前状态的门禁证据。"
        )
    return "；".join(notes)


def _historical_gate_note(run_dir: Path | None) -> str | None:
    if run_dir is None:
        return None
    try:
        metadata = load_run_metadata(run_dir)
        relative = metadata.get("task_card")
        repo_path = metadata.get("repo_path")
        if not isinstance(relative, str) or not isinstance(repo_path, str):
            return None
        card = load_task_card(Path(repo_path) / relative)
    except (OSError, ValueError):
        return None
    capsule = card.resume_capsule
    if capsule is None or not any(
        evidence.status != "not_run"
        for evidence in capsule.gate_evidence
    ):
        return None
    by_gate = {
        evidence.gate: _GATE_LABELS[evidence.status]
        for evidence in capsule.gate_evidence
    }
    return (
        "历史门禁："
        f"Verification={by_gate.get('verification', '尚未运行')}、"
        f"Risk={by_gate.get('risk', '尚未运行')}、"
        f"Reviewer={by_gate.get('review', '尚未运行')}；"
        "只用于定位，不能作为当前门禁的通过证据。"
    )
