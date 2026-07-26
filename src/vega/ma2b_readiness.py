from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .delegation import Sha256
from .ma2b_execution_binding import MA2BExecutionBinding, MA2BExecutionBindingError
from .ma2b_execution_binding import load_ma2b_execution_binding as _load_execution_binding
from .ma2b_task_pack import MA2BCasePackage, MA2BTaskPackError
from .ma2b_task_pack import load_ma2b_case_package as _load_case_package
from .redaction import redact_text


MA2B_READINESS_SCHEMA_VERSION = 1
MAX_MA2B_AUTHORIZATION_BYTES = 256 * 1024

MA2B_PILOT_CASE_IDS: tuple[str, ...] = tuple(f"MA2B-C{index:02d}" for index in range(1, 13))
MA2B_TASK_PACK_ROOT = Path("eval/experiments/multi-agent-coordination/task-pack")
MA2B_GROUND_TRUTH_ROOT = Path("eval/experiments/multi-agent-coordination/ground-truth")
MA2B_EXECUTION_BINDING_PATH = Path(
    "eval/experiments/multi-agent-coordination/MA-2B-execution-binding.md"
)
MA2B_EXECUTION_AUTHORIZATION_PATH = Path(
    "eval/experiments/multi-agent-coordination/MA-2B-execution-authorization.json"
)

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

ShortPublicText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]
UtcTimestamp = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=20, max_length=40),
]


class _CasePackageLoader(Protocol):
    def __call__(
        self,
        *,
        repo_root: Path,
        case_id: str,
        task_pack_root: Path,
        ground_truth_root: Path,
    ) -> MA2BCasePackage: ...


class _ExecutionBindingLoader(Protocol):
    def __call__(self, *, repo_root: Path, binding_path: Path) -> MA2BExecutionBinding: ...


class MA2BReadinessError(ValueError):
    """readiness gate 只暴露稳定 issue code。"""

    def __init__(self, issue_code: str) -> None:
        self.issue_code = issue_code
        super().__init__(issue_code)


class MA2BExecutionAuthorization(BaseModel):
    """owner 或独立复审后的执行授权快照，不包含 Provider 凭据。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    scope: Literal["ma2b_pilot_execution"]
    decision: Literal["authorized"]
    authorized_by: ShortPublicText
    authorized_at_utc: UtcTimestamp
    execution_binding_sha256: Sha256
    pricing_manifest_sha256: Sha256
    case_set_sha256: Sha256
    notes: ShortPublicText | None = None

    @field_validator("authorized_by", "notes")
    @classmethod
    def validate_public_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_public_text(value)

    @field_validator("authorized_at_utc")
    @classmethod
    def validate_utc_timestamp(cls, value: str) -> str:
        _parse_utc_timestamp(value)
        return value


class MA2BReadinessResult(BaseModel):
    """真实 Pilot 启动前的可审计阻断结果。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    status: Literal["ready", "blocked"]
    issue_codes: list[str]
    loaded_case_ids: list[str]
    case_set_sha256: Sha256 | None
    execution_binding_loaded: bool
    authorization_loaded: bool

    @model_validator(mode="after")
    def validate_status(self) -> MA2BReadinessResult:
        if self.issue_codes and self.status != "blocked":
            raise ValueError("存在 issue 时 readiness 必须 blocked")
        if not self.issue_codes and self.status != "ready":
            raise ValueError("无 issue 时 readiness 必须 ready")
        return self


def check_ma2b_pilot_readiness(
    *,
    repo_root: Path,
    task_pack_root: Path = MA2B_TASK_PACK_ROOT,
    ground_truth_root: Path = MA2B_GROUND_TRUTH_ROOT,
    execution_binding_path: Path = MA2B_EXECUTION_BINDING_PATH,
    authorization_path: Path = MA2B_EXECUTION_AUTHORIZATION_PATH,
    case_loader: _CasePackageLoader = _load_case_package,
    execution_binding_loader: _ExecutionBindingLoader = _load_execution_binding,
) -> MA2BReadinessResult:
    """汇总 MA-2B 真实执行前置条件；任何缺失都 fail-closed。"""

    repo = _resolve_repository_root(repo_root)
    issues: list[str] = []
    packages: list[MA2BCasePackage] = []

    for case_id in MA2B_PILOT_CASE_IDS:
        try:
            package = case_loader(
                repo_root=repo,
                case_id=case_id,
                task_pack_root=task_pack_root,
                ground_truth_root=ground_truth_root,
            )
        except MA2BTaskPackError as exc:
            issues.append(f"pilot_case:{case_id}:{exc.issue_code}")
            continue
        except (OSError, ValueError) as exc:
            issues.append(f"pilot_case:{case_id}:{type(exc).__name__}")
            continue
        _validate_loaded_case(case_id, package, issues)
        packages.append(package)

    loaded_case_ids = [package.manifest.case_id for package in packages]
    case_set_sha256 = (
        compute_ma2b_case_set_sha256(packages)
        if len(packages) == len(MA2B_PILOT_CASE_IDS)
        else None
    )

    binding: MA2BExecutionBinding | None = None
    execution_binding_sha256: str | None = None
    try:
        binding = execution_binding_loader(
            repo_root=repo,
            binding_path=execution_binding_path,
        )
        execution_binding_sha256 = _sha256_repo_file(repo, execution_binding_path)
    except MA2BExecutionBindingError as exc:
        issues.append(exc.issue_code)
    except (OSError, ValueError) as exc:
        issues.append(f"execution_binding:{type(exc).__name__}")

    authorization: MA2BExecutionAuthorization | None = None
    try:
        authorization = load_ma2b_execution_authorization(
            repo_root=repo,
            authorization_path=authorization_path,
        )
    except MA2BReadinessError as exc:
        issues.append(exc.issue_code)

    if authorization is not None:
        if execution_binding_sha256 is None:
            issues.append("execution_authorization_binding_unverifiable")
        elif authorization.execution_binding_sha256 != execution_binding_sha256:
            issues.append("execution_authorization_binding_hash_mismatch")
        if binding is None:
            issues.append("execution_authorization_pricing_unverifiable")
        elif authorization.pricing_manifest_sha256 != binding.pricing_manifest_ref.sha256:
            issues.append("execution_authorization_pricing_hash_mismatch")
        elif _parse_utc_timestamp(authorization.authorized_at_utc) < _parse_utc_timestamp(
            binding.availability_observed_at_utc
        ):
            issues.append("execution_authorization_before_binding_observation")
        elif _parse_utc_timestamp(authorization.authorized_at_utc) > _parse_utc_timestamp(
            binding.execution_window_start_utc
        ):
            issues.append("execution_authorization_after_execution_window_start")
        if case_set_sha256 is None:
            issues.append("execution_authorization_case_set_unverifiable")
        elif authorization.case_set_sha256 != case_set_sha256:
            issues.append("execution_authorization_case_set_hash_mismatch")

    unique_issues = list(dict.fromkeys(issues))
    return MA2BReadinessResult(
        schema_version=MA2B_READINESS_SCHEMA_VERSION,
        status="blocked" if unique_issues else "ready",
        issue_codes=unique_issues,
        loaded_case_ids=loaded_case_ids,
        case_set_sha256=case_set_sha256,
        execution_binding_loaded=binding is not None,
        authorization_loaded=authorization is not None,
    )


def load_ma2b_execution_authorization(
    *,
    repo_root: Path,
    authorization_path: Path,
) -> MA2BExecutionAuthorization:
    repo = _resolve_repository_root(repo_root)
    path = _resolve_existing_file(repo, authorization_path)
    raw = _read_bounded_file(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MA2BReadinessError("execution_authorization_invalid_utf8") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise MA2BReadinessError("execution_authorization_invalid_json") from exc
    if not isinstance(payload, dict):
        raise MA2BReadinessError("execution_authorization_not_mapping")
    try:
        return MA2BExecutionAuthorization.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise MA2BReadinessError("execution_authorization_schema_invalid") from exc


def compute_ma2b_case_set_sha256(packages: list[MA2BCasePackage]) -> str:
    records = []
    for package in sorted(packages, key=lambda item: item.manifest.case_id):
        records.append(
            {
                "case_id": package.manifest.case_id,
                "case_class": package.manifest.case_class,
                "package_role": package.manifest.package_role,
                "task_pack_sha256": package.task_pack_sha256,
                "expected_outcome": package.ground_truth.expected_outcome,
                "quality_scored": package.ground_truth.quality_scored,
                "target_workspace_change": package.ground_truth.target_workspace_change,
                "verification_commands": list(package.verification.commands),
            }
        )
    serialized = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_loaded_case(
    expected_case_id: str,
    package: MA2BCasePackage,
    issues: list[str],
) -> None:
    if package.manifest.case_id != expected_case_id:
        issues.append(f"pilot_case:{expected_case_id}:case_identity_mismatch")
    if package.manifest.package_role != "pilot_case":
        issues.append(f"pilot_case:{expected_case_id}:package_role_mismatch")
    if package.ground_truth.case_id != expected_case_id:
        issues.append(f"pilot_case:{expected_case_id}:ground_truth_identity_mismatch")
    if package.task_pack_sha256 != package.ground_truth.task_pack_sha256:
        issues.append(f"pilot_case:{expected_case_id}:task_pack_hash_mismatch")


def _sha256_repo_file(repo: Path, relative_path: Path) -> str:
    path = _resolve_existing_file(
        repo,
        relative_path,
        issue_code="execution_binding_path_invalid",
    )
    return hashlib.sha256(
        _read_bounded_file(
            path,
            unreadable_issue_code="execution_binding_artifact_unreadable",
            too_large_issue_code="execution_binding_artifact_too_large",
        )
    ).hexdigest()


def _resolve_repository_root(repo_root: Path) -> Path:
    try:
        resolved = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BReadinessError("repository_root_invalid") from exc
    if not resolved.is_dir():
        raise MA2BReadinessError("repository_root_invalid")
    return resolved


def _resolve_existing_file(
    repo: Path,
    relative_path: Path,
    *,
    issue_code: str = "execution_authorization_path_invalid",
) -> Path:
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise MA2BReadinessError(issue_code)
    candidate = repo.joinpath(relative_path)
    if _path_contains_link_or_reparse(repo, candidate):
        raise MA2BReadinessError(issue_code)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BReadinessError(issue_code) from exc
    if not resolved.is_relative_to(repo) or not resolved.is_file():
        raise MA2BReadinessError(issue_code)
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


def _read_bounded_file(
    path: Path,
    *,
    unreadable_issue_code: str = "execution_authorization_artifact_unreadable",
    too_large_issue_code: str = "execution_authorization_artifact_too_large",
) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MA2B_AUTHORIZATION_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise MA2BReadinessError(unreadable_issue_code) from exc
    if len(raw) > MAX_MA2B_AUTHORIZATION_BYTES:
        raise MA2BReadinessError(too_large_issue_code)
    return raw


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MA2BReadinessError("execution_authorization_duplicate_key")
        result[key] = value
    return result


def _validate_public_text(value: str) -> str:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError("执行授权公开字段不能包含换行或 NUL")
    if redact_text(value) != value:
        raise ValueError("执行授权公开字段不能包含凭据或敏感值")
    if any(pattern.search(value) for pattern in _LOCAL_PATH_PATTERNS):
        raise ValueError("执行授权公开字段不能包含 endpoint、本机路径或路径逃逸")
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


__all__ = [
    "MA2B_EXECUTION_AUTHORIZATION_PATH",
    "MA2B_EXECUTION_BINDING_PATH",
    "MA2B_GROUND_TRUTH_ROOT",
    "MA2B_PILOT_CASE_IDS",
    "MA2B_READINESS_SCHEMA_VERSION",
    "MA2B_TASK_PACK_ROOT",
    "MA2BExecutionAuthorization",
    "MA2BReadinessError",
    "MA2BReadinessResult",
    "check_ma2b_pilot_readiness",
    "compute_ma2b_case_set_sha256",
    "load_ma2b_execution_authorization",
]
