from __future__ import annotations

import hashlib
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .pricing import MA2BPricingManifestError, parse_ma2b_pricing_manifest
from .task_pack_models import ArtifactReference, Sha256
from ...redaction import redact_text


MA2B_EXECUTION_BINDING_SCHEMA_VERSION = 1
MAX_MA2B_EXECUTION_BINDING_BYTES = 512 * 1024

ShortPublicText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]
ModelId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
UtcTimestamp = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=20, max_length=40),
]

ProviderInterface = Literal["codex_exec", "responses_api", "approved_single_runtime"]

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]"),
    re.compile(r"(?i)(?<![A-Za-z0-9._\\-])\\\\[^\\/\s]+\\+[^\\/\s]+(?:\\|$)"),
    re.compile(r"(?i)(?<![:A-Za-z0-9._/-])//[A-Za-z0-9._$-]+/[A-Za-z0-9._$-]+(?:/|$)"),
    re.compile(
        r"""(?ix)
        (?<![A-Za-z0-9:/._-])
        /
        (?:
            (?:home|users)/[^/\s"'`<>]+
            | root
            | tmp
            | var/tmp
            | workspace
            | workspaces
            | private
            | github/workspace
            | __w
            | mnt/[a-z]
        )
        (?:/|$)
        """
    ),
    re.compile(r"(?<![A-Za-z0-9._-])\.\.(?:[\\/]|$)"),
    re.compile(r"(?i)\bhttps?://"),
)
_LATEST_ALIAS = re.compile(r"(?i)(^|[-_/.:])(?:latest|default|auto)(?:$|[-_/.:])")
_MARKDOWN_YAML_BLOCK = re.compile(
    r"(?is)```(?:yaml|yml)\s*\n(?P<body>.*?)\n```"
)


class MA2BExecutionBindingError(ValueError):
    """执行绑定只暴露稳定 issue code，避免把 Provider 细节写入公开诊断。"""

    def __init__(self, issue_code: str) -> None:
        self.issue_code = issue_code
        super().__init__(issue_code)


class MA2BExecutionBinding(BaseModel):
    """MA-2B 真实执行前的脱敏 Provider / 模型 / 定价绑定合同。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    provider_family: ShortPublicText
    provider_interface: ProviderInterface
    provider_client_version: ShortPublicText
    premium_model_id: ModelId
    budget_model_id: ModelId
    balanced_reviewer_model_id: ModelId
    planner_reasoning_configuration: ShortPublicText
    worker_reasoning_configuration: ShortPublicText
    reviewer_reasoning_configuration: ShortPublicText
    tool_policy_sha256: Sha256
    pricing_manifest_ref: ArtifactReference
    availability_observed_at_utc: UtcTimestamp
    execution_window_start_utc: UtcTimestamp
    execution_window_end_utc: UtcTimestamp

    @field_validator(
        "provider_family",
        "provider_client_version",
        "planner_reasoning_configuration",
        "worker_reasoning_configuration",
        "reviewer_reasoning_configuration",
    )
    @classmethod
    def validate_public_text(cls, value: str) -> str:
        return _validate_public_text(value)

    @field_validator(
        "premium_model_id",
        "budget_model_id",
        "balanced_reviewer_model_id",
    )
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        value = _validate_public_text(value)
        if _LATEST_ALIAS.search(value):
            raise ValueError("模型标识不能使用 latest/default/auto 别名")
        return value

    @field_validator(
        "availability_observed_at_utc",
        "execution_window_start_utc",
        "execution_window_end_utc",
    )
    @classmethod
    def validate_utc_timestamp(cls, value: str) -> str:
        _parse_utc_timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_binding_semantics(self) -> MA2BExecutionBinding:
        if self.premium_model_id == self.budget_model_id:
            raise ValueError("premium_model_id 与 budget_model_id 必须不同")
        observed = _parse_utc_timestamp(self.availability_observed_at_utc)
        start = _parse_utc_timestamp(self.execution_window_start_utc)
        end = _parse_utc_timestamp(self.execution_window_end_utc)
        if observed > start:
            raise ValueError("availability_observed_at_utc 不能晚于执行窗口开始")
        if start >= end:
            raise ValueError("执行窗口开始必须早于结束")
        return self


def load_ma2b_execution_binding(
    *,
    repo_root: Path,
    binding_path: Path,
) -> MA2BExecutionBinding:
    """加载 future execution binding，并验证 pricing manifest 引用的字节哈希。"""

    repo = _resolve_repository_root(repo_root)
    resolved_binding = _resolve_existing_file(
        repo,
        binding_path,
        issue_code="execution_binding_path_invalid",
    )
    raw = _read_bounded_file(resolved_binding)
    payload = _parse_binding_payload(raw)
    try:
        binding = MA2BExecutionBinding.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise MA2BExecutionBindingError("execution_binding_schema_invalid") from exc
    _validate_pricing_manifest_ref(repo, binding)
    return binding


def _parse_binding_payload(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MA2BExecutionBindingError("execution_binding_invalid_utf8") from exc
    match = _MARKDOWN_YAML_BLOCK.search(text)
    source = match.group("body") if match else text
    try:
        payload = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise MA2BExecutionBindingError("execution_binding_invalid_yaml") from exc
    if not isinstance(payload, dict):
        raise MA2BExecutionBindingError("execution_binding_not_mapping")
    return payload


def _validate_pricing_manifest_ref(repo: Path, binding: MA2BExecutionBinding) -> None:
    path = _resolve_existing_file(
        repo,
        Path(*binding.pricing_manifest_ref.relative_path.split("/")),
        issue_code="pricing_manifest_ref_invalid",
    )
    raw = _read_bounded_file(path)
    if hashlib.sha256(raw).hexdigest() != binding.pricing_manifest_ref.sha256:
        raise MA2BExecutionBindingError("pricing_manifest_hash_mismatch")
    try:
        parse_ma2b_pricing_manifest(
            raw,
            expected_model_ids={
                "planner_premium": binding.premium_model_id,
                "worker_budget": binding.budget_model_id,
                "reviewer_balanced": binding.balanced_reviewer_model_id,
            },
            maximum_observed_at_utc=binding.availability_observed_at_utc,
        )
    except MA2BPricingManifestError as exc:
        raise MA2BExecutionBindingError(exc.issue_code) from exc


def _validate_public_text(value: str) -> str:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError("执行绑定公开字段不能包含换行或 NUL")
    if redact_text(value) != value:
        raise ValueError("执行绑定公开字段不能包含凭据或敏感值")
    if any(pattern.search(value) for pattern in _LOCAL_PATH_PATTERNS):
        raise ValueError("执行绑定公开字段不能包含 endpoint、本机路径或路径逃逸")
    return value


def _parse_utc_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("时间戳必须使用 UTC Z 格式")
    try:
        timestamp = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("时间戳必须是 ISO-8601 UTC") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(None):
        raise ValueError("时间戳必须是 UTC")
    return timestamp


def _resolve_repository_root(repo_root: Path) -> Path:
    try:
        resolved = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BExecutionBindingError("repository_root_invalid") from exc
    if not resolved.is_dir():
        raise MA2BExecutionBindingError("repository_root_invalid")
    return resolved


def _resolve_existing_file(
    repo: Path,
    relative_path: Path,
    *,
    issue_code: str,
) -> Path:
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise MA2BExecutionBindingError(issue_code)
    candidate = repo.joinpath(relative_path)
    if _path_contains_link_or_reparse(repo, candidate):
        raise MA2BExecutionBindingError(issue_code)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BExecutionBindingError(issue_code) from exc
    if not resolved.is_relative_to(repo) or not resolved.is_file():
        raise MA2BExecutionBindingError(issue_code)
    return resolved


def _path_contains_link_or_reparse(repo: Path, candidate: Path) -> bool:
    try:
        relative_parts = candidate.relative_to(repo).parts
    except ValueError:
        return True
    current = repo
    for part in relative_parts:
        current = current / part
        if os.path.lexists(current) and _is_link_or_reparse_point(current):
            return True
    return False


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _read_bounded_file(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MA2B_EXECUTION_BINDING_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise MA2BExecutionBindingError("execution_binding_artifact_unreadable") from exc
    if len(raw) > MAX_MA2B_EXECUTION_BINDING_BYTES:
        raise MA2BExecutionBindingError("execution_binding_artifact_too_large")
    return raw


__all__ = [
    "MA2B_EXECUTION_BINDING_SCHEMA_VERSION",
    "MA2BExecutionBinding",
    "MA2BExecutionBindingError",
    "load_ma2b_execution_binding",
]
