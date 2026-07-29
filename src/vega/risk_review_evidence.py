from __future__ import annotations

from .models import GateResult
from .review_contract import ReviewVerdict
from .risk_review import validate_required_risk_disclosures


def gate_result_semantics(result: GateResult) -> tuple[object, ...]:
    """返回可跨阶段重算比较的风险门禁语义。"""
    return (
        result.risk,
        result.recommendation,
        tuple((reason.code, reason.severity) for reason in result.reasons),
        tuple(result.changed_files),
        tuple(
            (
                item.id,
                item.label,
                tuple(item.matched_files),
            )
            for item in result.required_reviews
        ),
        result.scope_profile,
    )


def disclosure_issues(
    prefix: str,
    risk_gate_result: GateResult | None,
    verdict: ReviewVerdict | None,
) -> list[str]:
    if (
        risk_gate_result is None
        or not risk_gate_result.required_reviews
        or verdict is None
    ):
        return []
    validation = validate_required_risk_disclosures(
        risk_gate_result.required_reviews,
        verdict.risk_disclosures,
        verdict.findings,
    )
    return [
        _issue_name(prefix, issue.code, issue.risk_id, issue.file)
        for issue in validation.issues
    ]


def required_review_iteration_eval_results(
    risk_gate_result: GateResult | None,
    verdict: ReviewVerdict,
) -> list[str]:
    if risk_gate_result is None or not risk_gate_result.required_reviews:
        return []
    validation = validate_required_risk_disclosures(
        risk_gate_result.required_reviews,
        verdict.risk_disclosures,
        verdict.findings,
    )
    if validation.valid:
        return ["PASS: 必审高风险披露与 Gate 命中范围一致"]
    return [
        "FAIL: required risk disclosure："
        + ":".join(
            item
            for item in (issue.code, issue.risk_id, issue.file)
            if item
        )
        for issue in validation.issues
    ]


def gate_blocks_reviewer_before_execution(result: GateResult) -> bool:
    """普通 human-review 继续早停；命名必审风险允许只读 Reviewer 生成披露。"""
    if result.recommendation != "human-review":
        return False
    if not required_review_policy_consistent(result):
        return True
    return any(
        reason.severity == "high"
        and reason.code != "required_risk_review"
        for reason in result.reasons
    )


def required_review_policy_consistent(result: GateResult) -> bool:
    if not result.required_reviews:
        return True
    return (
        result.risk == "high"
        and result.recommendation == "human-review"
        and any(
            reason.code == "required_risk_review"
            and reason.severity == "high"
            for reason in result.reasons
        )
    )


def _issue_name(
    prefix: str,
    code: str,
    risk_id: str,
    file: str,
) -> str:
    suffix = ":".join(item for item in (risk_id, file) if item)
    return f"{prefix}_{code}" + (f":{suffix}" if suffix else "")
