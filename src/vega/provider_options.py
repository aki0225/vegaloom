from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


_CODEX_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class CodexExecOptions(BaseModel):
    """项目可以按角色覆盖的 Codex 参数；权限和隔离不在此开放。"""

    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    model: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    ephemeral: bool = False

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str | None) -> str | None:
        normalized = _normalize_cli_value(value, "profile")
        if normalized is not None and not _CODEX_PROFILE_PATTERN.fullmatch(normalized):
            raise ValueError("profile 只能包含字母、数字、点、下划线和连字符")
        return normalized

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None, info: ValidationInfo) -> str | None:
        return _normalize_cli_value(value, info.field_name)


class CodexExecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: CodexExecOptions = Field(default_factory=CodexExecOptions)
    reviewer: CodexExecOptions = Field(default_factory=CodexExecOptions)


class ClaudeCodeOptions(BaseModel):
    """Claude 只开放模型和 effort；工具、权限与 safe-mode 由 Vega 固定。"""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None, info: ValidationInfo) -> str | None:
        return _normalize_cli_value(value, info.field_name)


class ClaudeCodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: ClaudeCodeOptions = Field(default_factory=ClaudeCodeOptions)
    reviewer: ClaudeCodeOptions = Field(default_factory=ClaudeCodeOptions)


def render_codex_options(role: str, options: CodexExecOptions) -> list[str]:
    return [
        f"- `{role}.profile`：`{options.profile or '继承用户配置'}`",
        f"- `{role}.model`：`{options.model or '继承用户配置'}`",
        f"- `{role}.reasoning_effort`：`{options.reasoning_effort or '继承用户配置'}`",
        f"- `{role}.ephemeral`：`{options.ephemeral}`；个人上下文禁用 memories / plugins / hooks / notify",
    ]


def render_claude_options(role: str, options: ClaudeCodeOptions) -> list[str]:
    return [
        f"- `{role}.model`：`{options.model or '继承用户配置'}`",
        f"- `{role}.effort`：`{options.effort or '继承用户配置'}`",
        f"- `{role}.isolation`：safe-mode + Vega 固定工具白名单",
    ]


def _normalize_cli_value(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if len(normalized) > 200:
        raise ValueError(f"{field_name} 长度不能超过 200")
    if normalized.startswith("-"):
        raise ValueError(f"{field_name} 不能以 '-' 开头")
    if any(character in normalized for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 不能包含换行或 NUL")
    return normalized
