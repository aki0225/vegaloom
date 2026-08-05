from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_review_path(value: str) -> str:
    """统一 Reviewer 返回的仓库相对路径格式。"""

    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["blocker", "major", "minor", "suggestion"] = "minor"
    file: str = ""
    line: int = Field(default=0, ge=0)
    title: str
    evidence: str = ""
    recommendation: str = ""

class GateReason(BaseModel):
    code: str
    severity: Literal["low", "medium", "high"]
    message: str
    evidence: str = ""


class RequiredReviewHit(BaseModel):
    """一次 Gate 评估命中的必须披露风险领域。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    matched_files: list[str] = Field(min_length=1)


class ReviewRiskLocation(BaseModel):
    """Reviewer 对高风险变更给出的仓库相对位置。"""

    model_config = ConfigDict(extra="forbid")

    file: str
    line: int = Field(default=0, ge=0)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("risk disclosure location.file 不能为空")
        return normalized


class ReviewRiskDisclosure(BaseModel):
    """命中项目高风险规则后，Reviewer 必须逐类给出的明确披露。"""

    model_config = ConfigDict(extra="forbid")

    risk_id: str
    assessment: Literal[
        "issue_found",
        "no_obvious_issue",
        "insufficient_evidence",
    ]
    locations: list[ReviewRiskLocation] = Field(min_length=1)
    change_summary: str
    evidence: str
    residual_risk: str

    @field_validator("risk_id", "change_summary", "evidence", "residual_risk")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("risk disclosure 字段不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_unique_locations(self) -> "ReviewRiskDisclosure":
        locations = [(location.file, location.line) for location in self.locations]
        if len(locations) != len(set(locations)):
            raise ValueError("risk disclosure locations 不能重复")
        return self


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approve", "request_changes", "needs_human"] = "needs_human"
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    # 保持默认空列表，确保升级后仍能读取历史 review-verdict.json。
    risk_disclosures: list[ReviewRiskDisclosure] = Field(default_factory=list)
    # 历史 verdict 允许缺少该字段，但新 Review Runtime 会做确定性覆盖校验。
    reviewed_files: list[str] = Field(default_factory=list)
    checked_items: list[str] = Field(default_factory=list)

    @field_validator("reviewed_files")
    @classmethod
    def validate_reviewed_files(cls, value: list[str]) -> list[str]:
        normalized = [normalize_review_path(item) for item in value]
        if any(not item for item in normalized):
            raise ValueError("reviewed_files 不能包含空路径")
        if len(normalized) != len(set(normalized)):
            raise ValueError("reviewed_files 不能包含重复路径")
        return normalized

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "ReviewVerdict":
        if not self.summary.strip():
            raise ValueError("review summary 不能为空")
        if any(not item.strip() for item in self.checked_items):
            raise ValueError("review checked_items 不能包含空项")
        risk_ids = [disclosure.risk_id for disclosure in self.risk_disclosures]
        if len(risk_ids) != len(set(risk_ids)):
            raise ValueError("risk_disclosures 中的 risk_id 不能重复")
        if self.verdict != "approve":
            return self
        if not self.checked_items:
            raise ValueError("approve 必须声明至少一个 checked_item")
        if any(
            finding.severity in {"blocker", "major"}
            for finding in self.findings
        ):
            raise ValueError("approve 不能同时包含 blocker 或 major finding")
        if any(
            disclosure.assessment != "no_obvious_issue"
            for disclosure in self.risk_disclosures
        ):
            raise ValueError("approve 不能包含已发现问题或证据不足的风险披露")
        return self
