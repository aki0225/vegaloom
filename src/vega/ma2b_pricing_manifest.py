from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .redaction import redact_text


MA2B_PRICING_MANIFEST_SCHEMA_VERSION = 1
MAX_MA2B_PRICING_MANIFEST_BYTES = 512 * 1024

PricingRole = Literal["planner_premium", "worker_budget", "reviewer_balanced"]
PricingSourceKind = Literal[
    "published_rate_snapshot",
    "provider_dashboard_snapshot",
    "approved_public_estimate",
]

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)
_EXPECTED_ROLE_ORDER: tuple[PricingRole, ...] = (
    "planner_premium",
    "worker_budget",
    "reviewer_balanced",
)
_LATEST_ALIAS = re.compile(r"(?i)(^|[-_/.:])(?:latest|default|auto)(?:$|[-_/.:])")
_DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,8})?$")
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
UsdAmount = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32),
]


class MA2BPricingManifestError(ValueError):
    """定价清单只暴露稳定 issue code，避免泄露 Provider 或本机细节。"""

    def __init__(self, issue_code: str) -> None:
        self.issue_code = issue_code
        super().__init__(issue_code)


class MA2BModelPricing(BaseModel):
    model_config = _STRICT_MODEL

    role: PricingRole
    model_id: ModelId
    billing_unit: Literal["usd_per_1m_tokens"]
    input_usd_per_1m_tokens: UsdAmount
    output_usd_per_1m_tokens: UsdAmount
    cached_input_usd_per_1m_tokens: UsdAmount | None = None

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        value = _validate_public_text(value)
        if _LATEST_ALIAS.search(value):
            raise ValueError("模型标识不能使用 latest/default/auto 别名")
        return value

    @field_validator(
        "input_usd_per_1m_tokens",
        "output_usd_per_1m_tokens",
        "cached_input_usd_per_1m_tokens",
    )
    @classmethod
    def validate_amount(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_usd_amount(value, allow_zero=True)

    @model_validator(mode="after")
    def validate_billable_amounts(self) -> MA2BModelPricing:
        if _decimal(self.input_usd_per_1m_tokens) <= 0:
            raise ValueError("input token 单价必须大于 0")
        if _decimal(self.output_usd_per_1m_tokens) <= 0:
            raise ValueError("output token 单价必须大于 0")
        return self


class MA2BPricingManifest(BaseModel):
    """MA-2B 真实执行前的公开定价快照合同。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    currency: Literal["USD"]
    source_kind: PricingSourceKind
    source_label: ShortPublicText
    observed_at_utc: UtcTimestamp
    effective_start_utc: UtcTimestamp
    effective_end_utc: UtcTimestamp
    case_count: Literal[12]
    treatment_count: Literal[3]
    maximum_case_cost_usd: UsdAmount
    maximum_total_cost_usd: UsdAmount
    model_pricing: list[MA2BModelPricing] = Field(min_length=3, max_length=3)

    @field_validator("source_label")
    @classmethod
    def validate_source_label(cls, value: str) -> str:
        return _validate_public_text(value)

    @field_validator("observed_at_utc", "effective_start_utc", "effective_end_utc")
    @classmethod
    def validate_utc_timestamp(cls, value: str) -> str:
        _parse_utc_timestamp(value)
        return value

    @field_validator("maximum_case_cost_usd", "maximum_total_cost_usd")
    @classmethod
    def validate_budget_amount(cls, value: str) -> str:
        return _validate_usd_amount(value, allow_zero=False)

    @model_validator(mode="after")
    def validate_manifest_semantics(self) -> MA2BPricingManifest:
        roles = [item.role for item in self.model_pricing]
        if tuple(roles) != _EXPECTED_ROLE_ORDER:
            raise ValueError("model_pricing 必须按固定角色顺序覆盖三类模型")
        model_ids = [item.model_id for item in self.model_pricing]
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("model_pricing 不能重复使用模型标识")
        observed = _parse_utc_timestamp(self.observed_at_utc)
        effective_start = _parse_utc_timestamp(self.effective_start_utc)
        effective_end = _parse_utc_timestamp(self.effective_end_utc)
        if observed > effective_end:
            raise ValueError("observed_at_utc 不能晚于定价有效窗口结束")
        if effective_start >= effective_end:
            raise ValueError("定价有效窗口开始必须早于结束")
        if _decimal(self.maximum_total_cost_usd) < _decimal(self.maximum_case_cost_usd):
            raise ValueError("总预算上限不能小于单 case 上限")
        return self


def load_ma2b_pricing_manifest(
    *,
    repo_root: Path,
    manifest_path: Path,
    expected_model_ids: Mapping[PricingRole, str] | None = None,
    maximum_observed_at_utc: str | None = None,
) -> MA2BPricingManifest:
    """从仓库内相对路径加载定价清单；不读取凭据、不调用 Provider。"""

    repo = _resolve_repository_root(repo_root)
    path = _resolve_existing_file(repo, manifest_path)
    raw = _read_bounded_file(path)
    return parse_ma2b_pricing_manifest(
        raw,
        expected_model_ids=expected_model_ids,
        maximum_observed_at_utc=maximum_observed_at_utc,
    )


def parse_ma2b_pricing_manifest(
    raw: bytes,
    *,
    expected_model_ids: Mapping[PricingRole, str] | None = None,
    maximum_observed_at_utc: str | None = None,
) -> MA2BPricingManifest:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MA2BPricingManifestError("pricing_manifest_invalid_utf8") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise MA2BPricingManifestError("pricing_manifest_invalid_json") from exc
    if not isinstance(payload, dict):
        raise MA2BPricingManifestError("pricing_manifest_not_mapping")
    try:
        manifest = MA2BPricingManifest.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise MA2BPricingManifestError("pricing_manifest_schema_invalid") from exc
    _validate_expected_model_ids(manifest, expected_model_ids)
    _validate_observed_time(manifest, maximum_observed_at_utc)
    return manifest


def _validate_expected_model_ids(
    manifest: MA2BPricingManifest,
    expected_model_ids: Mapping[PricingRole, str] | None,
) -> None:
    if expected_model_ids is None:
        return
    if set(expected_model_ids) != set(_EXPECTED_ROLE_ORDER):
        raise MA2BPricingManifestError("pricing_manifest_model_binding_mismatch")
    actual = {item.role: item.model_id for item in manifest.model_pricing}
    if actual != dict(expected_model_ids):
        raise MA2BPricingManifestError("pricing_manifest_model_binding_mismatch")


def _validate_observed_time(
    manifest: MA2BPricingManifest,
    maximum_observed_at_utc: str | None,
) -> None:
    if maximum_observed_at_utc is None:
        return
    if _parse_utc_timestamp(manifest.observed_at_utc) > _parse_utc_timestamp(
        maximum_observed_at_utc
    ):
        raise MA2BPricingManifestError("pricing_manifest_observed_after_binding")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MA2BPricingManifestError("pricing_manifest_duplicate_key")
        result[key] = value
    return result


def _validate_public_text(value: str) -> str:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError("定价清单公开字段不能包含换行或 NUL")
    if redact_text(value) != value:
        raise ValueError("定价清单公开字段不能包含凭据或敏感值")
    if any(pattern.search(value) for pattern in _LOCAL_PATH_PATTERNS):
        raise ValueError("定价清单公开字段不能包含 endpoint、本机路径或路径逃逸")
    return value


def _validate_usd_amount(value: str, *, allow_zero: bool) -> str:
    if not _DECIMAL_TEXT.fullmatch(value):
        raise ValueError("USD 金额必须是固定小数字符串")
    amount = _decimal(value)
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("USD 金额超出允许范围")
    return value


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("USD 金额无效") from exc


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
        raise MA2BPricingManifestError("repository_root_invalid") from exc
    if not resolved.is_dir():
        raise MA2BPricingManifestError("repository_root_invalid")
    return resolved


def _resolve_existing_file(repo: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise MA2BPricingManifestError("pricing_manifest_path_invalid")
    candidate = repo.joinpath(relative_path)
    if _path_contains_link_or_reparse(repo, candidate):
        raise MA2BPricingManifestError("pricing_manifest_path_invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BPricingManifestError("pricing_manifest_path_invalid") from exc
    if not resolved.is_relative_to(repo) or not resolved.is_file():
        raise MA2BPricingManifestError("pricing_manifest_path_invalid")
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
            raw = stream.read(MAX_MA2B_PRICING_MANIFEST_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise MA2BPricingManifestError("pricing_manifest_artifact_unreadable") from exc
    if len(raw) > MAX_MA2B_PRICING_MANIFEST_BYTES:
        raise MA2BPricingManifestError("pricing_manifest_artifact_too_large")
    return raw


__all__ = [
    "MA2B_PRICING_MANIFEST_SCHEMA_VERSION",
    "MA2BModelPricing",
    "MA2BPricingManifest",
    "MA2BPricingManifestError",
    "load_ma2b_pricing_manifest",
    "parse_ma2b_pricing_manifest",
]
