from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .redaction import redact_text
from .scope_path_matching import validate_scope_pattern


class RequiredReviewRule(BaseModel):
    """声明必须由 Reviewer 单独披露、并交由人工确认的风险领域。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=100)
    paths: list[str] = Field(min_length=1, max_length=128)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("required_reviews.label 不能包含首尾空白")
        if any(character in value for character in ("\r", "\n", "\0")):
            raise ValueError("required_reviews.label 不能包含换行或 NUL")
        if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
            raise ValueError(
                "required_reviews.label 不能包含控制字符或双向格式字符"
            )
        if redact_text(value) != value:
            raise ValueError(
                "required_reviews.label 会触发脱敏，无法作为稳定的审查领域名称"
            )
        return value

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        validated = [
            validate_scope_pattern(value, "required_reviews.paths")
            for value in values
        ]
        if len(validated) != len(set(validated)):
            raise ValueError("required_reviews.paths 不能包含重复规则")
        return validated


def ensure_unique_required_review_ids(
    rules: list[RequiredReviewRule],
) -> list[RequiredReviewRule]:
    ids = [item.id for item in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("risk.required_reviews 的 id 必须唯一")
    return rules


def render_required_review_config_lines(
    rules: list[RequiredReviewRule],
) -> list[str]:
    if not rules:
        return []
    return [
        "- 必须披露的风险审查：",
        *[
            f"  - `{item.id}` / {item.label}："
            + "、".join(f"`{path}`" for path in item.paths)
            for item in rules
        ],
    ]
