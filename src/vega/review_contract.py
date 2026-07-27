from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["blocker", "major", "minor", "suggestion"] = "minor"
    file: str = ""
    line: int = Field(default=0, ge=0)
    title: str
    evidence: str = ""
    recommendation: str = ""


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approve", "request_changes", "needs_human"] = "needs_human"
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    checked_items: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "ReviewVerdict":
        if not self.summary.strip():
            raise ValueError("review summary 不能为空")
        if any(not item.strip() for item in self.checked_items):
            raise ValueError("review checked_items 不能包含空项")
        if self.verdict != "approve":
            return self
        if not self.checked_items:
            raise ValueError("approve 必须声明至少一个 checked_item")
        if any(
            finding.severity in {"blocker", "major"}
            for finding in self.findings
        ):
            raise ValueError("approve 不能同时包含 blocker 或 major finding")
        return self
