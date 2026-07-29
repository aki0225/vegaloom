from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from .models import GateReason
from .review_contract import (
    RequiredReviewHit,
    ReviewFinding,
    ReviewRiskDisclosure,
)
from .risk_review_config import RequiredReviewRule
from .scope_path_matching import (
    path_matches_pattern,
    scope_paths_are_case_insensitive,
)


class RequiredRiskReviewHit(Protocol):
    """Gate 输出的必审风险命中项所需最小接口。"""

    id: str
    label: str
    matched_files: Sequence[str]


RiskReviewIssueCode = Literal[
    "required_review_id_invalid",
    "required_review_id_duplicate",
    "required_review_matched_files_missing",
    "risk_disclosure_id_duplicate",
    "risk_disclosure_missing",
    "risk_disclosure_unknown",
    "risk_disclosure_location_missing",
    "risk_disclosure_location_mismatch",
    "risk_disclosure_issue_without_finding",
]


@dataclass(frozen=True, slots=True)
class RiskReviewValidationIssue:
    code: RiskReviewIssueCode
    risk_id: str = ""
    file: str = ""


@dataclass(frozen=True, slots=True)
class RiskReviewValidationResult:
    issues: tuple[RiskReviewValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


def match_required_reviews(
    repo_path: Path,
    paths: list[str],
    rules: list[RequiredReviewRule],
) -> list[RequiredReviewHit]:
    """按仓库文件系统大小写语义执行 segment glob，不做模糊子串匹配。"""
    if not rules:
        return []
    case_sensitive = not scope_paths_are_case_insensitive(repo_path)
    normalized_paths = [
        (path, path.replace("\\", "/"))
        for path in dict.fromkeys(paths)
    ]
    hits: list[RequiredReviewHit] = []
    for rule in rules:
        matched_files = [
            original_path
            for original_path, normalized_path in normalized_paths
            if any(
                path_matches_pattern(
                    normalized_path,
                    pattern,
                    case_sensitive=case_sensitive,
                )
                for pattern in rule.paths
            )
        ]
        if matched_files:
            hits.append(
                RequiredReviewHit(
                    id=rule.id,
                    label=rule.label,
                    matched_files=matched_files,
                )
            )
    return hits


def build_required_review_reasons(
    hits: Sequence[RequiredRiskReviewHit],
) -> list[GateReason]:
    if not hits:
        return []
    return [
        GateReason(
            code="required_risk_review",
            severity="high",
            message=(
                "变更命中项目配置的必须披露风险领域，Reviewer 必须逐项说明，"
                "最终仍需人工确认。"
            ),
            evidence="；".join(
                f"{item.id}: {', '.join(item.matched_files[:8])}"
                for item in hits
            ),
        )
    ]


def render_required_review_gate_lines(
    hits: Sequence[RequiredRiskReviewHit],
) -> list[str]:
    lines = ["", "## 必须披露的风险审查", ""]
    if not hits:
        return [*lines, "- 未命中项目配置的必须披露风险领域。"]
    for item in hits:
        lines.extend(
            [
                f"### {item.label} (`{item.id}`)",
                "",
                "- 命中文件：",
                *[f"  - `{path}`" for path in item.matched_files],
                "- 要求：Reviewer 必须单独说明修改内容、判断依据和剩余风险，"
                "最终由人工确认。",
                "",
            ]
        )
    return lines


def build_insufficient_evidence_disclosures(
    required_reviews: Sequence[RequiredRiskReviewHit],
    *,
    evidence: str,
) -> list[ReviewRiskDisclosure]:
    """Reviewer 结果不可采用时，为每个 Gate 必审项生成明确的人工接管披露。"""

    normalized_evidence = evidence.strip()
    if not normalized_evidence:
        raise ValueError("insufficient_evidence disclosure 的 evidence 不能为空")
    disclosures: list[ReviewRiskDisclosure] = []
    seen_ids: set[str] = set()
    for hit in required_reviews:
        risk_id = hit.id.strip()
        if not risk_id or risk_id in seen_ids:
            raise ValueError("required_reviews 必须包含唯一且非空的 id")
        matched_files = list(
            dict.fromkeys(
                normalized
                for file in hit.matched_files
                if (normalized := _normalize_repo_path(file))
            )
        )
        if not matched_files:
            raise ValueError(f"required_review {risk_id} 缺少 matched_files")
        label = hit.label.strip() or risk_id
        disclosures.append(
            ReviewRiskDisclosure(
                risk_id=risk_id,
                assessment="insufficient_evidence",
                locations=[
                    {"file": file, "line": 0}
                    for file in matched_files
                ],
                change_summary=(
                    f"Gate 检测到“{label}”风险领域包含变更，"
                    "但 Reviewer 未完成可采用的评估。"
                ),
                evidence=normalized_evidence,
                residual_risk="该风险领域尚未完成有效审查，必须人工检查全部命中文件。",
            )
        )
        seen_ids.add(risk_id)
    return disclosures


def validate_required_risk_disclosures(
    required_reviews: Sequence[RequiredRiskReviewHit],
    disclosures: Sequence[ReviewRiskDisclosure],
    findings: Sequence[ReviewFinding],
) -> RiskReviewValidationResult:
    """确定性核对 Gate 必审项与 Reviewer 披露，任何不一致都返回失败问题。

    Reviewer 的自然语言结论不能自行决定审查范围；范围只由 Gate 的命中结果提供。
    """

    expected_files_by_id, issues = _collect_expected_files(required_reviews)
    disclosures_by_id, index_issues = _index_disclosures(disclosures)
    issues.extend(index_issues)
    issues.extend(
        RiskReviewValidationIssue("risk_disclosure_missing", risk_id=risk_id)
        for risk_id in expected_files_by_id
        if risk_id not in disclosures_by_id
    )
    for risk_id, disclosure in disclosures_by_id.items():
        expected_files = expected_files_by_id.get(risk_id)
        if expected_files is None:
            issues.append(
                RiskReviewValidationIssue(
                    "risk_disclosure_unknown",
                    risk_id=risk_id,
                )
            )
            continue
        issues.extend(
            _validate_one_disclosure(
                risk_id,
                expected_files,
                disclosure,
                findings,
            )
        )
    return RiskReviewValidationResult(tuple(issues))


def _collect_expected_files(
    required_reviews: Sequence[RequiredRiskReviewHit],
) -> tuple[dict[str, set[str]], list[RiskReviewValidationIssue]]:
    expected_files_by_id: dict[str, set[str]] = {}
    issues: list[RiskReviewValidationIssue] = []
    for hit in required_reviews:
        risk_id = hit.id.strip()
        if not risk_id:
            issues.append(RiskReviewValidationIssue("required_review_id_invalid"))
            continue
        if risk_id in expected_files_by_id:
            issues.append(
                RiskReviewValidationIssue(
                    "required_review_id_duplicate",
                    risk_id=risk_id,
                )
            )
            continue
        matched_files = {
            normalized
            for file in hit.matched_files
            if (normalized := _normalize_repo_path(file))
        }
        expected_files_by_id[risk_id] = matched_files
        if not matched_files:
            issues.append(
                RiskReviewValidationIssue(
                    "required_review_matched_files_missing",
                    risk_id=risk_id,
                )
            )
    return expected_files_by_id, issues


def _index_disclosures(
    disclosures: Sequence[ReviewRiskDisclosure],
) -> tuple[dict[str, ReviewRiskDisclosure], list[RiskReviewValidationIssue]]:
    disclosures_by_id: dict[str, ReviewRiskDisclosure] = {}
    issues: list[RiskReviewValidationIssue] = []
    for disclosure in disclosures:
        if disclosure.risk_id in disclosures_by_id:
            issues.append(
                RiskReviewValidationIssue(
                    "risk_disclosure_id_duplicate",
                    risk_id=disclosure.risk_id,
                )
            )
            continue
        disclosures_by_id[disclosure.risk_id] = disclosure
    return disclosures_by_id, issues


def _validate_one_disclosure(
    risk_id: str,
    expected_files: set[str],
    disclosure: ReviewRiskDisclosure,
    findings: Sequence[ReviewFinding],
) -> list[RiskReviewValidationIssue]:
    issues: list[RiskReviewValidationIssue] = []
    disclosure_files = {
        _normalize_repo_path(location.file)
        for location in disclosure.locations
    }
    issues.extend(
        RiskReviewValidationIssue(
            "risk_disclosure_location_missing",
            risk_id=risk_id,
            file=missing_file,
        )
        for missing_file in sorted(expected_files - disclosure_files)
    )
    for location in disclosure.locations:
        if _normalize_repo_path(location.file) not in expected_files:
            issues.append(
                RiskReviewValidationIssue(
                    "risk_disclosure_location_mismatch",
                    risk_id=risk_id,
                    file=location.file,
                )
            )
    if disclosure.assessment == "issue_found" and not _has_matching_finding(
        disclosure_files,
        findings,
    ):
        issues.append(
            RiskReviewValidationIssue(
                "risk_disclosure_issue_without_finding",
                risk_id=risk_id,
            )
        )
    return issues


def _has_matching_finding(
    disclosure_files: set[str],
    findings: Sequence[ReviewFinding],
) -> bool:
    return any(
        (
            _normalize_repo_path(finding.file) in disclosure_files
            and bool(finding.title.strip())
            and bool(finding.evidence.strip())
            and bool(finding.recommendation.strip())
        )
        for finding in findings
        if finding.file.strip()
    )


def _normalize_repo_path(value: str) -> str:
    return value.strip().replace("\\", "/").removeprefix("./")
