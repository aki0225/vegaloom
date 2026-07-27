from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints,
    field_validator, model_validator,
)

from ...redaction import redact_text, sensitive_path_reason


MA2B_TASK_PACK_SCHEMA_VERSION = 1
MAX_MA2B_TASK_PACK_ARTIFACT_BYTES = 1024 * 1024
MAX_MA2B_WORKSPACE_FILE_BYTES = 512 * 1024
MAX_MA2B_WORKSPACE_TOTAL_BYTES = 4 * 1024 * 1024

MA2BCaseClass = Literal[
    "code_change", "human_required", "stale_evidence", "invalid_verifier"
]
MA2BPackageRole = Literal["fake_driver_fixture", "pilot_case"]
MA2BExpectedOutcome = Literal["accepted_change", "safe_deferral", "safe_block"]
MA2BWorkspaceSourceKind = Literal["synthetic_fixture", "git_snapshot"]

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)
_PATH_GLOB_CHARACTERS = frozenset("*?[]{}")
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(
        r"(?<![A-Za-z0-9._\\-])(?:\\\\){1,2}[A-Za-z0-9._$-]+"
        r"\\+[A-Za-z0-9._$-]+"
    ),
    re.compile(r"(?<![A-Za-z0-9:/])/(?![A-Za-z?](?:[\s\"'`<>]|$))"),
)
_CASE_ID_PATTERN = re.compile(r"^MA2B-(?:C|F)(?:0[1-9]|1[0-2])$")
_TASK_ID_PATTERN = r"^TASK-MA2B-(?:C|F)(?:0[1-9]|1[0-2])$"
_DECISION_ID_PATTERN = r"^D-MA2B-(?:C|F)(?:0[1-9]|1[0-2])-[A-Z0-9][A-Z0-9._-]{0,49}$"
_REPOSITORY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$"
_ALL_OUTCOMES: frozenset[MA2BExpectedOutcome] = frozenset(
    {"accepted_change", "safe_deferral", "safe_block"}
)

CaseId = Annotated[str, StringConstraints(pattern=_CASE_ID_PATTERN.pattern)]
MA2BTaskId = Annotated[str, StringConstraints(pattern=_TASK_ID_PATTERN)]
DecisionId = Annotated[str, StringConstraints(pattern=_DECISION_ID_PATTERN)]
RepositoryId = Annotated[str, StringConstraints(pattern=_REPOSITORY_ID_PATTERN)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitObjectId = Annotated[
    str, StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
]
AcceptanceId = Annotated[
    str, StringConstraints(pattern=r"^A-[A-Z0-9][A-Z0-9._-]{0,99}$")
]
NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class MA2BTaskPackError(ValueError):
    """加载 task-pack 时只暴露稳定 issue code，避免把本机路径写入诊断。"""

    def __init__(self, issue_code: str) -> None:
        self.issue_code = issue_code
        super().__init__(issue_code)


class ArtifactReference(BaseModel):
    model_config = _STRICT_MODEL

    relative_path: str
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_repo_relative_path(value, "relative_path")


class MA2BCaseBudget(BaseModel):
    model_config = _STRICT_MODEL

    max_slices: int = Field(ge=1, le=64)
    max_dependency_edges: int = Field(ge=0, le=4096)
    max_write_paths: int = Field(ge=1, le=4096)
    max_changed_files: int = Field(ge=1, le=1000)
    max_diff_lines: int = Field(ge=1, le=1_000_000)
    max_new_files: int = Field(ge=0, le=1000)
    max_context_tokens: int = Field(ge=1, le=10_000_000)
    max_worker_time_seconds: int = Field(ge=1, le=86_400)
    max_worker_tokens: int = Field(ge=1, le=10_000_000)


class MA2BAcceptanceFact(BaseModel):
    model_config = _STRICT_MODEL

    fact_id: AcceptanceId
    statement: NonEmptyText

    @field_validator("statement")
    @classmethod
    def validate_public_statement(cls, value: str) -> str:
        return validate_public_text(value, "acceptance statement")


class MA2BUnresolvedDecision(BaseModel):
    model_config = _STRICT_MODEL

    decision_id: DecisionId
    question: NonEmptyText

    @field_validator("question")
    @classmethod
    def validate_public_question(cls, value: str) -> str:
        return validate_public_text(value, "decision question")


class MA2BTaskArtifact(BaseModel):
    """只保存任务事实，不允许混入参考补丁、Provider prompt 或运行结果。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    case_id: CaseId
    task_id: MA2BTaskId
    summary: NonEmptyText
    acceptance_facts: list[MA2BAcceptanceFact] = Field(min_length=1, max_length=64)
    non_goals: list[NonEmptyText] = Field(max_length=64)
    constraints: list[NonEmptyText] = Field(min_length=1, max_length=64)
    unresolved_decision: MA2BUnresolvedDecision | None

    @field_validator("summary")
    @classmethod
    def validate_public_summary(cls, value: str) -> str:
        return validate_public_text(value, "task summary")

    @field_validator("non_goals", "constraints")
    @classmethod
    def validate_public_text_list(cls, values: list[str]) -> list[str]:
        normalized = [validate_public_text(value, "task text") for value in values]
        require_unique(normalized, "task text 不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_task_identity(self) -> MA2BTaskArtifact:
        if self.task_id != f"TASK-{self.case_id}":
            raise ValueError("task_id 必须由 case_id 确定性派生")
        require_unique(
            [item.fact_id for item in self.acceptance_facts],
            "acceptance fact id 不能重复",
        )
        return self


class MA2BWorkspaceFile(BaseModel):
    model_config = _STRICT_MODEL

    relative_path: str
    sha256: Sha256
    size_bytes: int = Field(ge=0, le=MAX_MA2B_WORKSPACE_FILE_BYTES)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_contract_path(value)


class MA2BInitialWorkspaceArtifact(BaseModel):
    """绑定可复制的初始树；真实 Pilot 还必须绑定来源仓库与 Git commit。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    case_id: CaseId
    source_kind: MA2BWorkspaceSourceKind
    source_tree: str
    origin_repository_id: RepositoryId | None
    origin_head_sha: GitObjectId | None
    workspace_tree_sha256: Sha256
    files: list[MA2BWorkspaceFile] = Field(min_length=1, max_length=512)

    @field_validator("source_tree")
    @classmethod
    def validate_source_tree(cls, value: str) -> str:
        return validate_contract_path(value)

    @model_validator(mode="after")
    def validate_source_and_tree(self) -> MA2BInitialWorkspaceArtifact:
        if self.source_kind == "synthetic_fixture":
            if self.origin_repository_id is not None or self.origin_head_sha is not None:
                raise ValueError("synthetic fixture 不能伪造来源仓库或 Git commit")
        elif self.origin_repository_id is None or self.origin_head_sha is None:
            raise ValueError("git snapshot 必须绑定来源仓库与 Git commit")

        paths = [item.relative_path for item in self.files]
        require_unique(paths, "initial workspace file 不能重复")
        if paths != sorted(paths):
            raise ValueError("initial workspace files 必须按路径排序")
        if self.workspace_tree_sha256 != compute_ma2b_workspace_tree_sha256(self.files):
            raise ValueError("initial workspace tree hash 不匹配")
        return self


class MA2BProjectPolicyArtifact(BaseModel):
    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    case_id: CaseId
    allowed_read_paths: list[str] = Field(min_length=1, max_length=256)
    allowed_write_paths: list[str] = Field(min_length=1, max_length=128)
    budget_limits: MA2BCaseBudget

    @field_validator("allowed_read_paths", "allowed_write_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [validate_contract_path(value) for value in values]
        require_unique(normalized, "project policy path 不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_policy_scope(self) -> MA2BProjectPolicyArtifact:
        if not set(self.allowed_write_paths).issubset(self.allowed_read_paths):
            raise ValueError("allowed_write_paths 必须是 allowed_read_paths 的子集")
        if len(self.allowed_write_paths) > self.budget_limits.max_write_paths:
            raise ValueError("allowed_write_paths 超出冻结预算")
        return self


class MA2BVerificationManifest(BaseModel):
    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    case_id: CaseId
    commands: list[str] = Field(min_length=1, max_length=8)
    oracle: Literal["all_commands_exit_zero"]
    deterministic: Literal[True]
    network_access: Literal["prohibited"]
    credentials_required: Literal["prohibited"]

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, values: list[str]) -> list[str]:
        return _validate_verification_commands(values)


class MA2BCaseManifest(BaseModel):
    """绑定 task-pack 内部四个输入 artifact；ground truth 反向绑定本文件。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    case_id: CaseId
    package_role: MA2BPackageRole
    case_class: MA2BCaseClass
    task_ref: ArtifactReference
    initial_workspace_ref: ArtifactReference
    project_policy_ref: ArtifactReference
    verification_manifest_ref: ArtifactReference

    @model_validator(mode="after")
    def validate_case_role_and_class(self) -> MA2BCaseManifest:
        validate_case_role_and_class(
            case_id=self.case_id,
            package_role=self.package_role,
            case_class=self.case_class,
        )
        return self


class MA2BGroundTruthArtifact(BaseModel):
    """评分规则只绑定任务事实与确定性命令，不保存参考 patch。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    case_id: CaseId
    package_role: MA2BPackageRole
    case_class: MA2BCaseClass
    case_manifest_sha256: Sha256
    task_pack_sha256: Sha256
    acceptance_fact_ids: list[AcceptanceId] = Field(min_length=1, max_length=64)
    required_verification_commands: list[str] = Field(min_length=1, max_length=8)
    expected_outcome: MA2BExpectedOutcome
    forbidden_outcomes: list[MA2BExpectedOutcome] = Field(min_length=2, max_length=2)
    quality_scored: bool
    target_workspace_change: Literal["allowed", "forbidden"]
    manual_adjudication_rule: NonEmptyText

    @field_validator("acceptance_fact_ids")
    @classmethod
    def validate_acceptance_ids(cls, values: list[str]) -> list[str]:
        require_unique(values, "ground truth acceptance fact id 不能重复")
        return values

    @field_validator("required_verification_commands")
    @classmethod
    def validate_verification_commands(cls, values: list[str]) -> list[str]:
        return _validate_verification_commands(values)

    @field_validator("manual_adjudication_rule")
    @classmethod
    def validate_public_rule(cls, value: str) -> str:
        return validate_public_text(value, "manual adjudication rule")

    @model_validator(mode="after")
    def validate_ground_truth_semantics(self) -> MA2BGroundTruthArtifact:
        validate_case_role_and_class(
            case_id=self.case_id,
            package_role=self.package_role,
            case_class=self.case_class,
        )
        expected_outcome = {
            "code_change": "accepted_change",
            "human_required": "safe_deferral",
            "stale_evidence": "safe_block",
            "invalid_verifier": "safe_block",
        }[self.case_class]
        if self.expected_outcome != expected_outcome:
            raise ValueError("expected_outcome 与 case_class 不一致")
        if set(self.forbidden_outcomes) != _ALL_OUTCOMES.difference({expected_outcome}):
            raise ValueError("forbidden_outcomes 必须精确覆盖其他结果")
        expected_quality_scored = self.case_class == "code_change"
        if self.quality_scored is not expected_quality_scored:
            raise ValueError("quality_scored 与 case_class 不一致")
        expected_workspace_change = (
            "allowed" if self.case_class == "code_change" else "forbidden"
        )
        if self.target_workspace_change != expected_workspace_change:
            raise ValueError("target_workspace_change 与 case_class 不一致")
        return self


@dataclass(frozen=True)
class MA2BCasePackage:
    """已完成结构、内容哈希、初始树和 ground truth 交叉校验的案例。"""

    case_dir: Path
    ground_truth_path: Path
    task: MA2BTaskArtifact
    initial_workspace: MA2BInitialWorkspaceArtifact
    project_policy: MA2BProjectPolicyArtifact
    verification: MA2BVerificationManifest
    manifest: MA2BCaseManifest
    ground_truth: MA2BGroundTruthArtifact
    task_pack_sha256: str


def compute_ma2b_workspace_tree_sha256(files: list[MA2BWorkspaceFile]) -> str:
    payload = {
        "schema": "ma2b-workspace-tree-v1",
        "files": [item.model_dump(mode="json") for item in files],
    }
    return sha256_json(payload)


def compute_ma2b_task_pack_sha256(
    manifest: MA2BCaseManifest,
    *,
    case_manifest_sha256: str,
) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", case_manifest_sha256):
        raise ValueError("case_manifest_sha256 格式无效")
    payload = {
        "schema": "ma2b-task-pack-v1",
        "case_id": manifest.case_id,
        "package_role": manifest.package_role,
        "case_class": manifest.case_class,
        "case_manifest_sha256": case_manifest_sha256,
        "artifact_refs": {
            "task": manifest.task_ref.model_dump(mode="json"),
            "initial_workspace": manifest.initial_workspace_ref.model_dump(mode="json"),
            "project_policy": manifest.project_policy_ref.model_dump(mode="json"),
            "verification_manifest": manifest.verification_manifest_ref.model_dump(mode="json"),
        },
    }
    return sha256_json(payload)


def validate_case_role_and_class(
    *,
    case_id: str,
    package_role: MA2BPackageRole,
    case_class: MA2BCaseClass,
) -> None:
    expected_role: MA2BPackageRole = (
        "pilot_case" if case_id.startswith("MA2B-C") else "fake_driver_fixture"
    )
    if package_role != expected_role:
        raise ValueError("package_role 与 case_id 不一致")
    if case_class != expected_case_class(case_id):
        raise ValueError("case_class 与预注册 case 编号不一致")


def expected_case_class(case_id: str) -> MA2BCaseClass:
    case_number = int(case_id[-2:])
    if case_number <= 8:
        return "code_change"
    if case_number <= 10:
        return "human_required"
    if case_number == 11:
        return "stale_evidence"
    return "invalid_verifier"


def validate_contract_path(value: str) -> str:
    return _validate_repo_relative_path(value, "contract path")


def validate_public_text(value: str, field_name: str) -> str:
    _reject_unsafe_text(value, field_name)
    if redact_text(value) != value:
        raise ValueError(f"{field_name} 会触发脱敏，不能进入公开 task-pack")
    return value


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("JSON key 不能重复")
        payload[key] = value
    return payload


def require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _validate_repo_relative_path(value: str, field_name: str) -> str:
    if value != value.strip():
        raise ValueError(f"{field_name} 不能包含首尾空白")
    if not value or len(value) > 512:
        raise ValueError(f"{field_name} 必须是长度不超过 512 的非空路径")
    _reject_unsafe_text(value, field_name)
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{field_name} 只能使用仓库相对路径")
    if "\\" in value or ":" in value:
        raise ValueError(f"{field_name} 必须使用无盘符的 POSIX 相对路径")
    if any(character in value for character in _PATH_GLOB_CHARACTERS):
        raise ValueError(f"{field_name} 必须是精确路径，不能使用 glob")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"{field_name} 不能包含空路径段、'.' 或 '..'")
    if segments[0] == ".git":
        raise ValueError(f"{field_name} 不能指向 Git 控制目录")
    sensitive_reason = sensitive_path_reason(value)
    if sensitive_reason:
        raise ValueError(f"{field_name} 不能指向敏感路径（{sensitive_reason}）")
    if redact_text(value) != value:
        raise ValueError(f"{field_name} 会触发脱敏，不能作为稳定 artifact 身份")
    return value


def _validate_verification_commands(values: list[str]) -> list[str]:
    normalized = [_validate_verification_command(value) for value in values]
    require_unique(normalized, "verification commands 不能重复")
    return normalized


def _validate_verification_command(value: str) -> str:
    if value != value.strip():
        raise ValueError("verification command 不能包含首尾空白")
    if not value or len(value) > 2000:
        raise ValueError("verification command 必须是长度不超过 2000 的非空命令")
    _reject_unsafe_text(value, "verification command")
    if any(pattern.search(value) for pattern in _LOCAL_PATH_PATTERNS):
        raise ValueError("verification command 不能包含本机绝对路径")
    if redact_text(value) != value:
        raise ValueError("verification command 会触发脱敏，不能进入公开合同")
    return value


def _reject_unsafe_text(value: str, field_name: str) -> None:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 不能包含换行或 NUL")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(f"{field_name} 不能包含控制字符或双向格式字符")


def sha256_json(payload: object) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "ArtifactReference", "MA2B_TASK_PACK_SCHEMA_VERSION", "MA2BAcceptanceFact",
    "MA2BCaseManifest", "MA2BCasePackage", "MA2BExpectedOutcome",
    "MA2BGroundTruthArtifact", "MA2BInitialWorkspaceArtifact", "MA2BPackageRole",
    "MA2BProjectPolicyArtifact", "MA2BTaskArtifact", "MA2BTaskPackError",
    "MA2BUnresolvedDecision", "MA2BVerificationManifest", "MA2BWorkspaceFile",
    "Sha256", "compute_ma2b_task_pack_sha256", "compute_ma2b_workspace_tree_sha256",
]
