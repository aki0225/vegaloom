from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .scope_path_matching import validate_scope_pattern

if TYPE_CHECKING:
    from .project_config import ProjectConfig


BOUNDED_POLICY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class BoundedApprovalConfig(BaseModel):
    """仓库维护者预先批准的低风险自动批准边界。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    policy_id: str | None = None
    allowed_paths: list[str] = Field(default_factory=list, max_length=128)
    max_changed_files: int | None = Field(default=None, ge=1, le=10_000)
    max_work_items: int | None = Field(default=None, ge=1, le=8)
    max_repair_rounds: int | None = Field(default=None, ge=0, le=20)
    max_auto_replans: int | None = Field(default=None, ge=0, le=10)

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not BOUNDED_POLICY_ID_PATTERN.fullmatch(normalized):
            raise ValueError(
                "approval.bounded.policy_id 只能包含字母、数字、点、下划线和连字符"
            )
        return normalized

    @field_validator("allowed_paths")
    @classmethod
    def validate_allowed_paths(cls, values: list[str]) -> list[str]:
        normalized = [
            validate_scope_pattern(value, "approval.bounded.allowed_paths")
            for value in values
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("approval.bounded.allowed_paths 不能包含重复路径")
        return normalized

    @model_validator(mode="after")
    def validate_enabled_policy(self) -> BoundedApprovalConfig:
        if not self.enabled:
            return self
        missing = [
            field
            for field, value in {
                "policy_id": self.policy_id,
                "allowed_paths": self.allowed_paths,
                "max_changed_files": self.max_changed_files,
                "max_work_items": self.max_work_items,
                "max_repair_rounds": self.max_repair_rounds,
                "max_auto_replans": self.max_auto_replans,
            }.items()
            if value is None or value == []
        ]
        if missing:
            raise ValueError(
                "启用 approval.bounded 时必须显式配置：" + "、".join(missing)
            )
        return self


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bounded: BoundedApprovalConfig = Field(default_factory=BoundedApprovalConfig)


def bounded_approval_policy_digest(config: ProjectConfig) -> str:
    """计算影响 bounded 批准资格的跨机器稳定摘要。"""

    payload = {
        "version": config.version,
        "config_path": (
            Path(config.source_path).name if config.source_path is not None else None
        ),
        "approval": config.approval.bounded.model_dump(mode="json"),
        "verification": config.verification.model_dump(mode="json"),
        "risk": config.risk.model_dump(mode="json"),
        "budget": config.budget.model_dump(mode="json"),
        "scope": config.scope.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render_bounded_approval_summary(config: BoundedApprovalConfig) -> list[str]:
    paths = (
        "、".join(f"`{item}`" for item in config.allowed_paths)
        if config.allowed_paths
        else "未配置"
    )
    return [
        "## 有界自动批准",
        "",
        f"- 启用：`{config.enabled}`",
        f"- 策略 ID：`{config.policy_id or '未配置'}`",
        f"- 允许路径：{paths}",
        "",
    ]
