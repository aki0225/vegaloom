from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
