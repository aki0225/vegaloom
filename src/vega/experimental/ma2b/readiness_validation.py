from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from .execution_binding import MA2BExecutionBinding


class _ExecutionAuthorizationView(Protocol):
    execution_binding_sha256: str
    pricing_manifest_sha256: str
    case_set_sha256: str
    authorized_at_utc: str


def validate_authorization_binding(
    authorization: _ExecutionAuthorizationView,
    *,
    binding: MA2BExecutionBinding | None,
    execution_binding_sha256: str | None,
    case_set_sha256: str | None,
) -> list[str]:
    """校验 owner authorization 与 binding、pricing 和 case-set 的绑定关系。"""

    issues: list[str] = []
    if execution_binding_sha256 is None:
        issues.append("execution_authorization_binding_unverifiable")
    elif authorization.execution_binding_sha256 != execution_binding_sha256:
        issues.append("execution_authorization_binding_hash_mismatch")
    _validate_authorization_pricing(authorization, binding, issues)
    if case_set_sha256 is None:
        issues.append("execution_authorization_case_set_unverifiable")
    elif authorization.case_set_sha256 != case_set_sha256:
        issues.append("execution_authorization_case_set_hash_mismatch")
    return issues


def _validate_authorization_pricing(
    authorization: _ExecutionAuthorizationView,
    binding: MA2BExecutionBinding | None,
    issues: list[str],
) -> None:
    if binding is None:
        issues.append("execution_authorization_pricing_unverifiable")
        return
    if authorization.pricing_manifest_sha256 != binding.pricing_manifest_ref.sha256:
        issues.append("execution_authorization_pricing_hash_mismatch")
        return
    authorized_at = parse_utc_timestamp(authorization.authorized_at_utc)
    if authorized_at < parse_utc_timestamp(binding.availability_observed_at_utc):
        issues.append("execution_authorization_before_binding_observation")
    elif authorized_at > parse_utc_timestamp(binding.execution_window_start_utc):
        issues.append("execution_authorization_after_execution_window_start")


def parse_utc_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("时间戳必须使用 UTC Z 格式")
    try:
        timestamp = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("时间戳必须是 ISO-8601 UTC") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(None):
        raise ValueError("时间戳必须是 UTC")
    return timestamp


__all__ = ["parse_utc_timestamp", "validate_authorization_binding"]
