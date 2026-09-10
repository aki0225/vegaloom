from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator


AGENT_SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_digest(value: BaseModel | dict[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_schema_version(value: int) -> int:
    if value != AGENT_SCHEMA_VERSION:
        raise ValueError(
            f"不支持的 Agent schema_version：{value}；"
            f"当前仅支持 {AGENT_SCHEMA_VERSION}"
        )
    return value


class StrictAgentModel(BaseModel):
    """共享严格模型与版本检查，任务模型只定义各自业务字段。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    schema_version: int = AGENT_SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        return validate_schema_version(value)


def normalize_repo_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"路径必须是仓库相对路径：{value}")
    return candidate.as_posix()


def normalize_relative_paths(values: list[str]) -> list[str]:
    normalized = [normalize_repo_relative_path(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError("仓库相对路径不能重复")
    return normalized
