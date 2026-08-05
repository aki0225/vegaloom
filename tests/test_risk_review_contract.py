from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from vega.review_contract import (
    ReviewFinding,
    ReviewRiskDisclosure,
    ReviewVerdict,
)
from vega.review_coverage import (
    build_review_file_coverage,
    review_file_coverage_issues,
)
from vega.risk_review import (
    build_insufficient_evidence_disclosures,
    validate_required_risk_disclosures,
)


@dataclass(frozen=True)
class RequiredReviewHit:
    id: str
    label: str
    matched_files: list[str]


def _disclosure(
    risk_id: str = "payment",
    *,
    assessment: str = "no_obvious_issue",
    file: str = "src/payments/charge.py",
    line: int = 1,
) -> ReviewRiskDisclosure:
    return ReviewRiskDisclosure.model_validate(
        {
            "risk_id": risk_id,
            "assessment": assessment,
            "locations": [{"file": file, "line": line}],
            "change_summary": "修改了扣款重试逻辑。",
            "evidence": "已检查当前 diff 和已有测试摘要。",
            "residual_risk": "人工确认网关超时后的并发重试行为。",
        }
    )


def test_legacy_verdict_defaults_to_empty_risk_disclosures() -> None:
    verdict = ReviewVerdict.model_validate(
        {
            "verdict": "approve",
            "summary": "未发现阻塞问题。",
            "findings": [],
            "checked_items": ["需求覆盖"],
        }
    )

    assert verdict.risk_disclosures == []
    assert verdict.reviewed_files == []


def test_reviewed_files_are_normalized_and_must_be_unique() -> None:
    verdict = ReviewVerdict.model_validate(
        {
            "verdict": "approve",
            "summary": "已检查全部变更文件。",
            "findings": [],
            "reviewed_files": [".\\src\\core.py", "tests/test_core.py"],
            "checked_items": ["需求覆盖"],
        }
    )

    assert verdict.reviewed_files == ["src/core.py", "tests/test_core.py"]

    payload = verdict.model_dump(mode="json")
    payload["reviewed_files"] = ["src/core.py", "./src/core.py"]
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate(payload)

    coverage = build_review_file_coverage(
        ["src/core.py"],
        ["src/core.py", "README.md"],
    )
    assert coverage["complete"] is False
    assert coverage["unexpected_files"] == ["README.md"]
    assert review_file_coverage_issues(coverage) == [
        "reviewed_files_unknown:README.md"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_id", " "),
        ("change_summary", ""),
        ("evidence", "\t"),
        ("residual_risk", "\n"),
    ],
)
def test_risk_disclosure_rejects_empty_text_fields(field: str, value: str) -> None:
    payload = _disclosure().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError):
        ReviewRiskDisclosure.model_validate(payload)


def test_risk_disclosure_requires_location() -> None:
    assert _disclosure().locations[0].line == 1
    payload = _disclosure().model_dump(mode="json")
    payload["locations"] = []

    with pytest.raises(ValidationError):
        ReviewRiskDisclosure.model_validate(payload)


def test_risk_disclosure_rejects_empty_or_duplicate_locations() -> None:
    empty_file = _disclosure().model_dump(mode="json")
    empty_file["locations"] = [{"file": " ", "line": 0}]
    duplicate = _disclosure().model_dump(mode="json")
    duplicate["locations"] = [
        {"file": "src/payments/charge.py", "line": 0},
        {"file": "src/payments/charge.py", "line": 0},
    ]

    with pytest.raises(ValidationError):
        ReviewRiskDisclosure.model_validate(empty_file)
    with pytest.raises(ValidationError):
        ReviewRiskDisclosure.model_validate(duplicate)


def test_review_verdict_rejects_duplicate_risk_ids() -> None:
    with pytest.raises(ValidationError):
        ReviewVerdict(
            verdict="request_changes",
            summary="存在需要处理的问题。",
            findings=[],
            risk_disclosures=[_disclosure(), _disclosure()],
            checked_items=["支付风险"],
        )


@pytest.mark.parametrize("assessment", ["issue_found", "insufficient_evidence"])
def test_approve_rejects_non_clear_risk_assessment(assessment: str) -> None:
    with pytest.raises(ValidationError):
        ReviewVerdict(
            verdict="approve",
            summary="结论自相矛盾。",
            findings=[],
            risk_disclosures=[_disclosure(assessment=assessment)],
            checked_items=["支付风险"],
        )


def test_required_risk_disclosures_accept_exact_mapping() -> None:
    result = validate_required_risk_disclosures(
        [
            RequiredReviewHit(
                id="payment",
                label="支付与资金",
                matched_files=["src/payments/charge.py"],
            )
        ],
        [_disclosure()],
        [],
    )

    assert result.valid
    assert result.issues == ()


def test_substantive_risk_disclosure_requires_positive_line() -> None:
    result = validate_required_risk_disclosures(
        [
            RequiredReviewHit(
                id="payment",
                label="支付与资金",
                matched_files=["src/payments/charge.py"],
            )
        ],
        [_disclosure(line=0)],
        [],
    )

    assert "risk_disclosure_location_line_missing" in {
        issue.code for issue in result.issues
    }


def test_required_risk_disclosure_must_cover_every_matched_file() -> None:
    required = [
        RequiredReviewHit(
            id="payment",
            label="支付与资金",
            matched_files=[
                "src/payments/charge.py",
                "src/payments/refund.py",
            ],
        )
    ]
    partial = validate_required_risk_disclosures(
        required,
        [_disclosure()],
        [],
    )
    complete_payload = _disclosure().model_dump(mode="json")
    complete_payload["locations"].append(
        {"file": "src/payments/refund.py", "line": 1}
    )
    complete = validate_required_risk_disclosures(
        required,
        [ReviewRiskDisclosure.model_validate(complete_payload)],
        [],
    )

    assert "risk_disclosure_location_missing" in {
        issue.code for issue in partial.issues
    }
    assert complete.valid


def test_build_insufficient_evidence_disclosures_covers_every_required_review() -> None:
    disclosures = build_insufficient_evidence_disclosures(
        [
            RequiredReviewHit(
                id="database",
                label="数据库与迁移",
                matched_files=[
                    "db/migrations/024_add_status.sql",
                    "db/migrations/024_add_status.sql",
                ],
            ),
            RequiredReviewHit(
                id="concurrency",
                label="并发与异步",
                matched_files=["src/jobs/retry.py"],
            ),
        ],
        evidence="Reviewer 输出无法解析。",
    )

    assert [item.risk_id for item in disclosures] == ["database", "concurrency"]
    assert all(item.assessment == "insufficient_evidence" for item in disclosures)
    assert [location.file for location in disclosures[0].locations] == [
        "db/migrations/024_add_status.sql"
    ]
    assert all(
        location.line == 0
        for disclosure in disclosures
        for location in disclosure.locations
    )
    assert validate_required_risk_disclosures(
        [
            RequiredReviewHit(
                item.risk_id,
                item.risk_id,
                [location.file for location in item.locations],
            )
            for item in disclosures
        ],
        disclosures,
        [],
    ).valid


@pytest.mark.parametrize(
    ("required", "disclosures", "expected_code"),
    [
        (
            [RequiredReviewHit("payment", "支付与资金", ["src/payments/charge.py"])],
            [],
            "risk_disclosure_missing",
        ),
        (
            [],
            [_disclosure()],
            "risk_disclosure_unknown",
        ),
        (
            [
                RequiredReviewHit("payment", "支付与资金", ["src/payments/charge.py"]),
                RequiredReviewHit("payment", "重复规则", ["src/payments/refund.py"]),
            ],
            [_disclosure()],
            "required_review_id_duplicate",
        ),
        (
            [RequiredReviewHit("payment", "支付与资金", ["src/payments/charge.py"])],
            [_disclosure(), _disclosure()],
            "risk_disclosure_id_duplicate",
        ),
        (
            [RequiredReviewHit("payment", "支付与资金", ["src/payments/charge.py"])],
            [_disclosure(file="src/payments/refund.py")],
            "risk_disclosure_location_mismatch",
        ),
        (
            [RequiredReviewHit("payment", "支付与资金", [])],
            [_disclosure()],
            "required_review_matched_files_missing",
        ),
    ],
)
def test_required_risk_disclosures_fail_closed(
    required: list[RequiredReviewHit],
    disclosures: list[ReviewRiskDisclosure],
    expected_code: str,
) -> None:
    result = validate_required_risk_disclosures(required, disclosures, [])

    assert not result.valid
    assert expected_code in {issue.code for issue in result.issues}


def test_issue_found_requires_finding_on_disclosed_file() -> None:
    required = [
        RequiredReviewHit(
            "payment",
            "支付与资金",
            ["src/payments/charge.py"],
        )
    ]
    disclosure = _disclosure(assessment="issue_found")

    missing = validate_required_risk_disclosures(required, [disclosure], [])
    matched = validate_required_risk_disclosures(
        required,
        [disclosure],
        [
            ReviewFinding(
                severity="major",
                file="src/payments/charge.py",
                line=80,
                title="重试可能造成重复扣款",
                evidence="幂等键在重试时发生变化。",
                recommendation="复用稳定幂等键。",
            )
        ],
    )

    assert "risk_disclosure_issue_without_finding" in {
        issue.code for issue in missing.issues
    }
    assert matched.valid


@pytest.mark.parametrize(
    ("evidence", "recommendation"),
    [
        ("", "复用稳定幂等键。"),
        ("幂等键在重试时发生变化。", ""),
        (" ", "复用稳定幂等键。"),
        ("幂等键在重试时发生变化。", "\t"),
    ],
)
def test_issue_found_requires_actionable_matching_finding(
    evidence: str,
    recommendation: str,
) -> None:
    result = validate_required_risk_disclosures(
        [
            RequiredReviewHit(
                "payment",
                "支付与资金",
                ["src/payments/charge.py"],
            )
        ],
        [_disclosure(assessment="issue_found")],
        [
            ReviewFinding(
                severity="major",
                file="src/payments/charge.py",
                line=80,
                title="重试可能造成重复扣款",
                evidence=evidence,
                recommendation=recommendation,
            )
        ],
    )

    assert "risk_disclosure_issue_without_finding" in {
        issue.code for issue in result.issues
    }
