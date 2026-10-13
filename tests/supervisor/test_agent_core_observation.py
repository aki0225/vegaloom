"""受控 Core 投影：展示内容不能决定已批准范围内的风险返修。"""

from copy import deepcopy

import pytest

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope, ChangeContract, approve_change_contract,
)
from vega.agent_core_observation import pending_risk_repair_is_authorized
from vega.models import GateResult, LoopIterationState
from vega.review_contract import ReviewVerdict


def _facts():
    contract = approve_change_contract(ChangeContract(
        task_id="risk-repair", goal="修复范围内问题", acceptance=["保持拒绝边界"],
        required_verification=["python -m pytest"], allow_pending_risk_repair=True,
        authorized_risk_reviews=["access-control"],
        authority_envelope=ChangeAuthorityEnvelope(allowed_paths=["policy.py"]),
    ), actor="user")
    latest = LoopIterationState(
        iteration=1, verification_status="passed", reviewer_status="success",
        verdict="request_changes", risk_gate_status="success",
        risk_gate_recommendation="human-review", scope_gate_status="success",
        scope_gate_post_verification_status="success", scope_gate_pre_review_status="success",
    )
    gate = GateResult.model_validate({
        "risk": "high", "recommendation": "human-review", "changed_files": ["policy.py"],
        "reasons": [{"code": "required_risk_review", "severity": "high", "message": "必审"}],
        "required_reviews": [{"id": "access-control", "label": "权限",
                              "matched_files": ["policy.py"]}],
    })
    review = ReviewVerdict.model_validate({
        "verdict": "request_changes", "summary": "修复范围内拒绝分支",
        "findings": [{"severity": "major", "file": "policy.py", "line": 1,
                      "title": "拒绝分支错误", "evidence": "当前Diff", "recommendation": "修复拒绝"}],
        "risk_disclosures": [{"risk_id": "access-control", "assessment": "issue_found",
            "locations": [{"file": "policy.py", "line": 1}], "change_summary": "权限分支",
            "evidence": "当前Diff", "residual_risk": "仍需人工检查"}],
    })
    finish = {"artifact_integrity": {"valid": True}, "evidence_freshness": {"fresh": True},
              "risk_gate_results": [gate.model_dump()], "latest_verdict": review.model_dump()}
    return contract, latest, finish


@pytest.mark.parametrize("screen", [None, {}, {"actual_changes": {"budget_findings": ["展示文字"]}}])
def test_risk_repair_uses_core_projection_not_first_screen(screen):
    contract, latest, finish = _facts()
    if screen is not None:
        finish["first_screen"] = screen
    assert pending_risk_repair_is_authorized(contract, latest, finish)


@pytest.mark.parametrize("damage", [
    "approval", "authorization", "missing_gate", "bad_gate", "integrity", "stale",
    "verification", "scope", "budget", "other_high_risk", "missing_review", "disclosure",
    "suggestion", "external_write",
])
def test_risk_repair_rejects_missing_or_blocking_facts(damage):
    contract, latest, finish = _facts()
    # 展示仍声称可返修，不能代替缺失或失败的权威事实。
    gate = finish["risk_gate_results"][0]
    finish["first_screen"] = {"actual_changes": {
        "required_reviews": deepcopy(gate["required_reviews"]),
        "high_risk_findings": deepcopy(gate["reasons"]), "budget_findings": [],
        "changed_files": ["policy.py"],
    }}
    if damage == "approval":
        contract = contract.model_copy(update={"approved_digest": "0" * 64})
    elif damage in {"authorization", "external_write"}:
        draft = contract.model_copy(deep=True)
        if damage == "authorization":
            draft.authorized_risk_reviews = []
        else:
            draft.side_effect_policy.external_write_during_validation = True
        contract = approve_change_contract(draft, actor="user")
    elif damage == "missing_gate":
        finish.pop("risk_gate_results")
    elif damage == "bad_gate":
        finish["risk_gate_results"] = [{}]
    elif damage == "integrity":
        finish["artifact_integrity"]["valid"] = False
    elif damage == "stale":
        finish["evidence_freshness"]["fresh"] = False
    elif damage == "verification":
        latest.verification_status = "failed"
    elif damage == "scope":
        latest.scope_gate_pre_review_status = "failed"
    elif damage in {"budget", "other_high_risk"}:
        gate["reasons"].append({"code": "budget_diff_lines" if damage == "budget" else "unknown",
                                "severity": "high", "message": "阻断"})
    elif damage == "missing_review":
        finish.pop("latest_verdict")
    elif damage == "disclosure":
        finish["latest_verdict"]["risk_disclosures"] = []
    elif damage == "suggestion":
        finish["latest_verdict"]["findings"][0]["severity"] = "suggestion"
    assert not pending_risk_repair_is_authorized(contract, latest, finish)
