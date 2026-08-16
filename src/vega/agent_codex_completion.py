from __future__ import annotations

from .agent_contract import GateStatus
from .models import LoopIterationState


def finish_evidence_untrusted(finish_summary: dict[str, object]) -> bool:
    integrity = finish_summary.get("artifact_integrity")
    freshness = finish_summary.get("evidence_freshness")
    return (
        not isinstance(integrity, dict)
        or integrity.get("valid") is not True
        or not isinstance(freshness, dict)
        or freshness.get("fresh") is not True
    )


def verification_status(
    latest: LoopIterationState | None,
    finish_summary: dict[str, object],
) -> GateStatus:
    if finish_summary.get("latest_verification_failed") is True:
        return "failed"
    if latest is not None:
        if latest.verification_status == "failed":
            return "failed"
        if latest.verification_status == "passed":
            return "passed"
    if finish_evidence_untrusted(finish_summary):
        return "blocked"
    if finish_summary.get("verification_passed") is True:
        return "passed"
    return "not_run"


def risk_status(latest: LoopIterationState | None) -> GateStatus:
    if latest is None or latest.risk_gate_status == "skipped":
        return "not_run"
    if latest.risk_gate_status == "failed":
        return "failed"
    if latest.risk_gate_recommendation == "human-review":
        return "blocked"
    return "passed"


def review_status(latest: LoopIterationState | None) -> GateStatus:
    if latest is None or latest.reviewer_status == "skipped":
        return "not_run"
    if latest.reviewer_status != "success":
        return "blocked"
    if latest.verdict == "approve":
        return "passed"
    if latest.verdict == "request_changes":
        return "failed"
    return "blocked"


def scope_remained_inside_plan(latest: LoopIterationState) -> bool:
    statuses = (
        latest.scope_gate_status,
        latest.scope_gate_post_verification_status,
        latest.scope_gate_pre_review_status,
    )
    return all(status in {"skipped", "success"} for status in statuses) and not (
        latest.scope_gate_violations
        or latest.scope_gate_post_verification_violations
        or latest.scope_gate_pre_review_violations
    )
