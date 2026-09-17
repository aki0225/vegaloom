from __future__ import annotations

from .agent_contract import GateStatus
from .models import LoopIterationState
from .agent_change_contract import ChangeContract
from .review_contract import RequiredReviewHit, ReviewVerdict
from .risk_review import validate_required_risk_disclosures


def pending_risk_repair_is_authorized(
    contract: ChangeContract | None,
    latest: LoopIterationState | None,
    finish: dict[str, object],
) -> bool:
    """人工只授权范围内返修；风险状态和最终交付确认不在这里改变。"""
    if (
        contract is None or not contract.allow_pending_risk_repair
        or not contract.approval_is_current() or contract.approval_source != "human"
        or latest is None or finish_evidence_untrusted(finish)
        or verification_status(latest, finish) != "passed"
        or review_status(latest) != "failed" or latest.risk_gate_status != "success"
        or latest.risk_gate_recommendation != "human-review"
        or not scope_remained_inside_plan(latest)
        or any(status != "success" for status in (
            latest.scope_gate_status, latest.scope_gate_post_verification_status,
            latest.scope_gate_pre_review_status,
        ))
        or contract.side_effect_policy.external_write_during_validation
        or contract.side_effect_policy.deployment_action
    ):
        return False
    try:
        screen = finish["first_screen"]
        changes = screen["actual_changes"]
        required = [RequiredReviewHit.model_validate(item) for item in changes["required_reviews"]]
        review = ReviewVerdict.model_validate(finish["latest_verdict"])
        findings = changes["high_risk_findings"]
        allowed = set(contract.authorized_risk_reviews)
        return (
            bool(required) and {hit.id for hit in required}.issubset(allowed)
            and not changes["budget_findings"]
            and all(item["code"] == "required_risk_review" for item in findings)
            and review.verdict == "request_changes"
            and any(item.severity != "suggestion" and item.file in changes["changed_files"]
                    and item.line > 0 and item.evidence.strip() and item.recommendation.strip()
                    for item in review.findings)
            and all(item.assessment != "insufficient_evidence" for item in review.risk_disclosures)
            and validate_required_risk_disclosures(required, review.risk_disclosures, review.findings).valid
        )
    except (KeyError, TypeError, ValueError):
        return False


_BLOCKING_VERIFICATION_INTERRUPTION = {
    "timed_out",
    "stopped",
    "termination-unconfirmed",
}


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
    if _verification_requires_human(latest, finish_summary):
        return "blocked"
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


def _verification_requires_human(
    latest: LoopIterationState | None,
    finish_summary: dict[str, object],
) -> bool:
    if latest is not None and latest.verification_failure_kind is not None:
        return True
    results = finish_summary.get("verification_results")
    if not isinstance(results, list):
        return False
    expected_iteration = latest.iteration if latest is not None else None
    for payload in reversed(results):
        if not isinstance(payload, dict):
            continue
        iteration = payload.get("iteration")
        if expected_iteration is not None and iteration != expected_iteration:
            continue
        if payload.get("interruption_status") in _BLOCKING_VERIFICATION_INTERRUPTION:
            return True
        command_results = payload.get("results")
        if isinstance(command_results, list) and any(
            isinstance(item, dict)
            and item.get("interruption_status")
            in _BLOCKING_VERIFICATION_INTERRUPTION
            for item in command_results
        ):
            return True
        # 最新有效结果已找到，旧失败不能改变当前路由。
        return False
    return False


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
