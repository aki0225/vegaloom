from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import GateResult
from .redaction import redact_value
from .review_contract import (
    RequiredReviewHit,
    ReviewFinding,
    ReviewRiskDisclosure,
    ReviewVerdict,
)
from .risk_review import (
    build_insufficient_evidence_disclosures,
    validate_required_risk_disclosures,
)


def required_reviews_from_inputs(inputs: dict[str, Any]) -> list[RequiredReviewHit]:
    risk_gate = inputs.get("risk_gate")
    if not isinstance(risk_gate, dict) or risk_gate.get("status") != "success":
        return []
    try:
        result = GateResult.model_validate(risk_gate.get("result"))
    except ValidationError:
        return []
    return result.required_reviews


def build_required_review_failure_reasons(
    *,
    evidence_issues: Sequence[str],
    truncated_sections: Sequence[str],
    workspace_issues: Sequence[str],
    prompt_budget_exceeded: bool,
    runner_status: str,
    runner_error: str | None,
    termination_unconfirmed: bool,
) -> list[str]:
    reasons = [
        *[f"evidence:{item}" for item in evidence_issues],
        *[f"truncated:{item}" for item in truncated_sections],
        *[f"workspace:{item}" for item in workspace_issues],
    ]
    if prompt_budget_exceeded:
        reasons.append("review_prompt_budget_exceeded")
    if runner_status != "success":
        reasons.append(f"reviewer_status:{runner_status}")
    if runner_error is not None:
        reasons.append("reviewer_error_present")
    if termination_unconfirmed:
        reasons.append("reviewer_termination_unconfirmed")
    return reasons


def enforce_required_risk_review(
    verdict: ReviewVerdict,
    risk_gate_result: GateResult | None,
    *,
    evidence_failures: list[str],
) -> tuple[ReviewVerdict, list[str]]:
    """绑定 Gate 风险范围；缺失、越界或执行失败时固定交由人工。"""
    required_reviews = (
        risk_gate_result.required_reviews
        if risk_gate_result is not None
        else []
    )
    if not required_reviews:
        return _enforce_empty_risk_disclosures(verdict)

    validation = validate_required_risk_disclosures(
        required_reviews,
        verdict.risk_disclosures,
        verdict.findings,
    )
    disclosure_issues = [_format_issue(issue) for issue in validation.issues]
    failure_reasons = list(
        dict.fromkeys(
            [
                *[item for item in evidence_failures if item],
                *disclosure_issues,
            ]
        )
    )
    if not failure_reasons:
        return _force_human_verdict(verdict), []
    return (
        _build_insufficient_verdict(
            verdict,
            required_reviews,
            failure_reasons,
            preserve_runner_output=not evidence_failures,
        ),
        failure_reasons,
    )


def render_required_review_pack_lines(
    required_reviews: Sequence[RequiredReviewHit],
) -> list[str]:
    if not required_reviews:
        return []
    lines = [
        "## 必须逐类披露的高风险变更",
        "",
        (
            "- 以下范围由 Gate 确定。每个 ID 必须在 `risk_disclosures` "
            "中恰好出现一次，并覆盖列出的全部文件。"
        ),
        "",
    ]
    for item in required_reviews:
        lines.extend(
            [
                f"### `{item.id}` — {item.label}",
                "",
                *[f"- `{path}`" for path in item.matched_files],
                "",
            ]
        )
    return lines


def render_required_review_prompt_rules(
    required_reviews: Sequence[RequiredReviewHit],
) -> list[str]:
    if not required_reviews:
        return ["- Review Pack 未列出必审风险 ID 时，`risk_disclosures` 必须返回空列表。"]
    return [
        "- Review Pack 列出必须披露的高风险 ID 时，`risk_disclosures` 必须逐个且只出现一次。",
        "- 每项 disclosure 的 locations 必须覆盖全部命中文件并给出大于 0 的关键行号；`assessment=insufficient_evidence` 时可使用 line=0 表示只能定位到文件级。",
        "- `issue_found` 必须同时给出同文件且标题、证据、建议均非空的 finding；未发现明确问题使用 `no_obvious_issue`，证据不足使用 `insufficient_evidence`。",
        "- `no_obvious_issue` 只表示当前证据下未发现明显问题，不代表安全证明；命中必审风险后最终仍由人工确认。",
    ]


def risk_disclosure_schema_example(
    required_reviews: Sequence[RequiredReviewHit],
) -> list[dict[str, Any]]:
    if not required_reviews:
        return []
    first = required_reviews[0]
    first_file = first.matched_files[0]
    return [
        {
            "risk_id": first.id,
            "assessment": "issue_found | no_obvious_issue | insufficient_evidence",
            "locations": [{"file": first_file, "line": 1}],
            "change_summary": "说明该风险领域具体修改了什么。",
            "evidence": "列出 diff、测试和项目规则中的直接证据。",
            "residual_risk": "说明仍需人工确认的问题；没有也要说明原因。",
        }
    ]


def render_review_risk_disclosure_lines(
    disclosures: Sequence[ReviewRiskDisclosure],
    required_reviews: Sequence[RequiredReviewHit],
) -> list[str]:
    if not disclosures:
        return []
    labels = {item.id: item.label for item in required_reviews}
    lines = ["## 必须人工检查", ""]
    for index, disclosure in enumerate(disclosures, start=1):
        label = labels.get(disclosure.risk_id) or disclosure.risk_id
        lines.extend(
            [
                f"### {index}. {label} (`{disclosure.risk_id}`)",
                "",
                *_render_disclosure_details(disclosure),
            ]
        )
    return lines


def render_final_risk_disclosure_lines(
    disclosures: Sequence[ReviewRiskDisclosure],
) -> list[str]:
    if not disclosures:
        return []
    lines = ["## 必须人工检查", ""]
    for disclosure in disclosures:
        lines.extend(
            [
                f"### `{disclosure.risk_id}`",
                "",
                *_render_disclosure_details(disclosure),
            ]
        )
    return lines


def empty_findings_line(verdict: ReviewVerdict) -> str:
    if any(
        disclosure.assessment == "insufficient_evidence"
        for disclosure in verdict.risk_disclosures
    ):
        return "- 未形成可采信的问题结论；请按上述证据不足项人工检查。"
    return "- 未发现阻塞问题。"


def run_required_review_pack_eval(
    result: GateResult,
    verdict_path: Path,
) -> list[str]:
    if result.recommendation != "human-review":
        return ["PASS: review 风险门禁允许隔离审查"]
    if not result.required_reviews:
        return ["FAIL: review 风险门禁要求人工审查"]
    if not verdict_path.exists():
        return ["FAIL: 必审高风险缺少 review-verdict.json"]
    try:
        verdict = ReviewVerdict.model_validate_json(
            verdict_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError):
        return ["FAIL: 必审高风险 verdict 格式不合法"]
    validation = validate_required_risk_disclosures(
        result.required_reviews,
        verdict.risk_disclosures,
        verdict.findings,
    )
    results = [
        "FAIL: required risk disclosure：" + _format_issue(issue)
        for issue in validation.issues
    ]
    if verdict.verdict != "needs_human":
        results.append("FAIL: 必审高风险 verdict 必须为 needs_human")
    if results:
        return results
    if any(
        disclosure.assessment == "insufficient_evidence"
        for disclosure in verdict.risk_disclosures
    ):
        return ["WARN: 必审高风险披露结构完整，但审查证据不足，仍需人工确认"]
    return ["PASS: 必审高风险披露结构完整且固定交由人工确认"]


def run_required_review_prompt_eval(
    result: GateResult,
    prompt_text: str,
) -> list[str]:
    """Review Pack 只验证门禁范围已进入 prompt，不要求尚未生成的 verdict。"""
    if not result.required_reviews:
        return ["PASS: Review Pack 已绑定风险门禁"]
    if result.risk != "high" or result.recommendation != "human-review":
        return ["FAIL: 必审高风险未绑定 high/human-review 语义"]
    required_markers = [
        *[f"`{item.id}`" for item in result.required_reviews],
        *[
            f"`{path}`"
            for item in result.required_reviews
            for path in item.matched_files
        ],
        '"risk_disclosures"',
    ]
    missing = [marker for marker in required_markers if marker not in prompt_text]
    if missing:
        return [
            "FAIL: Review Pack 缺少必审高风险提示："
            + "、".join(missing)
        ]
    return ["PASS: 必审高风险已写入 Review Pack，等待逐类披露"]


def required_review_outcome_line(
    risk_gate_result: GateResult,
    verdict: ReviewVerdict,
    disclosure_issues: Sequence[str],
) -> str:
    if not risk_gate_result.required_reviews or disclosure_issues:
        return "FAIL: review 风险门禁要求人工审查，不能作为自动通过结论"
    if any(
        disclosure.assessment == "insufficient_evidence"
        for disclosure in verdict.risk_disclosures
    ):
        return "WARN: 必审高风险审查证据不足，已固定交由人工确认"
    return "PASS: 必审高风险披露结构完整；Review 结论固定交由人工确认"


def redact_review_verdict(verdict: ReviewVerdict) -> ReviewVerdict:
    return ReviewVerdict.model_validate(redact_value(verdict.model_dump(mode="json")))


def _enforce_empty_risk_disclosures(
    verdict: ReviewVerdict,
) -> tuple[ReviewVerdict, list[str]]:
    if not verdict.risk_disclosures:
        return redact_review_verdict(verdict), []
    issues = [
        f"risk_disclosure_unknown:{disclosure.risk_id}"
        for disclosure in verdict.risk_disclosures
    ]
    evidence = "；".join(issues)
    normalized = ReviewVerdict(
        verdict="needs_human",
        summary="Reviewer 返回了未被 Gate 要求的风险披露，不能作为自动通过结论。",
        findings=[
            *verdict.findings,
            ReviewFinding(
                severity="major",
                title="风险披露范围与 Gate 不一致",
                evidence=evidence,
                recommendation="人工检查原始 Reviewer 输出，并确认风险规则是否需要补充。",
            ),
        ],
        risk_disclosures=[],
        checked_items=list(
            dict.fromkeys([*verdict.checked_items, "风险披露范围"])
        ),
    )
    return redact_review_verdict(normalized), issues


def _force_human_verdict(verdict: ReviewVerdict) -> ReviewVerdict:
    return redact_review_verdict(
        ReviewVerdict(
            verdict="needs_human",
            summary=verdict.summary,
            findings=verdict.findings,
            risk_disclosures=verdict.risk_disclosures,
            checked_items=list(
                dict.fromkeys([*verdict.checked_items, "必须披露的高风险变更"])
            ),
        )
    )


def _build_insufficient_verdict(
    verdict: ReviewVerdict,
    required_reviews: Sequence[RequiredReviewHit],
    failure_reasons: list[str],
    *,
    preserve_runner_output: bool,
) -> ReviewVerdict:
    evidence = "；".join(failure_reasons)
    findings = list(verdict.findings) if preserve_runner_output else []
    checked_items = list(verdict.checked_items) if preserve_runner_output else []
    findings.append(
        ReviewFinding(
            severity="major",
            title="高风险审查证据不足",
            evidence=evidence,
            recommendation="人工检查全部命中文件，并重新取得完整、可采信的审查证据。",
        )
    )
    normalized = ReviewVerdict(
        verdict="needs_human",
        summary="必须披露的高风险审查证据不足，已保留命中范围并交由人工检查。",
        findings=findings,
        risk_disclosures=build_insufficient_evidence_disclosures(
            required_reviews,
            evidence=evidence,
        ),
        checked_items=list(
            dict.fromkeys([*checked_items, "必须披露的高风险变更"])
        ),
    )
    return redact_review_verdict(normalized)


def _render_disclosure_details(
    disclosure: ReviewRiskDisclosure,
) -> list[str]:
    locations = "、".join(
        (
            f"`{location.file}:{location.line}`"
            if location.line
            else f"`{location.file}`"
        )
        for location in disclosure.locations
    )
    return [
        f"- 判断：`{disclosure.assessment}`",
        f"- 位置：{locations}",
        f"- 变更：{disclosure.change_summary}",
        f"- 证据：{disclosure.evidence}",
        f"- 剩余风险：{disclosure.residual_risk}",
        "",
    ]


def _format_issue(issue: object) -> str:
    return ":".join(
        part
        for part in (
            str(getattr(issue, "code", "")),
            str(getattr(issue, "risk_id", "")),
            str(getattr(issue, "file", "")),
        )
        if part
    )
