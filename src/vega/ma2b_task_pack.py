from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .delegation import (
    AcceptanceId,
    ArtifactReference,
    BudgetEligibilityLimits,
    GitObjectId,
    Sha256,
    SliceVerification,
)
from .redaction import redact_text


MA2B_TASK_PACK_SCHEMA_VERSION = 1
MAX_MA2B_TASK_PACK_ARTIFACT_BYTES = 1024 * 1024
MAX_MA2B_WORKSPACE_FILE_BYTES = 512 * 1024
MAX_MA2B_WORKSPACE_TOTAL_BYTES = 4 * 1024 * 1024

MA2BCaseClass = Literal[
    "code_change",
    "human_required",
    "stale_evidence",
    "invalid_verifier",
]
MA2BPackageRole = Literal["fake_driver_fixture", "pilot_case"]
MA2BExpectedOutcome = Literal["accepted_change", "safe_deferral", "safe_block"]
MA2BWorkspaceSourceKind = Literal["synthetic_fixture", "git_snapshot"]

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)
_ZERO_HASH = "0" * 64
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
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class MA2BTaskPackError(ValueError):
    """加载 task-pack 时只暴露稳定 issue code，避免把本机路径写入诊断。"""

    def __init__(self, issue_code: str) -> None:
        self.issue_code = issue_code
        super().__init__(issue_code)


class MA2BAcceptanceFact(BaseModel):
    model_config = _STRICT_MODEL

    fact_id: AcceptanceId
    statement: NonEmptyText

    @field_validator("statement")
    @classmethod
    def validate_public_statement(cls, value: str) -> str:
        return _validate_public_text(value, "acceptance statement")


class MA2BUnresolvedDecision(BaseModel):
    model_config = _STRICT_MODEL

    decision_id: DecisionId
    question: NonEmptyText

    @field_validator("question")
    @classmethod
    def validate_public_question(cls, value: str) -> str:
        return _validate_public_text(value, "decision question")


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
        return _validate_public_text(value, "task summary")

    @field_validator("non_goals", "constraints")
    @classmethod
    def validate_public_text_list(cls, values: list[str]) -> list[str]:
        normalized = [_validate_public_text(value, "task text") for value in values]
        _require_unique(normalized, "task text 不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_task_identity(self) -> MA2BTaskArtifact:
        expected_task_id = f"TASK-{self.case_id}"
        if self.task_id != expected_task_id:
            raise ValueError("task_id 必须由 case_id 确定性派生")
        _require_unique(
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
        return _validate_contract_path(value)


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
        return _validate_contract_path(value)

    @model_validator(mode="after")
    def validate_source_and_tree(self) -> MA2BInitialWorkspaceArtifact:
        if self.source_kind == "synthetic_fixture":
            if self.origin_repository_id is not None or self.origin_head_sha is not None:
                raise ValueError("synthetic fixture 不能伪造来源仓库或 Git commit")
        elif self.origin_repository_id is None or self.origin_head_sha is None:
            raise ValueError("git snapshot 必须绑定来源仓库与 Git commit")

        paths = [item.relative_path for item in self.files]
        _require_unique(paths, "initial workspace file 不能重复")
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
    budget_limits: BudgetEligibilityLimits

    @field_validator("allowed_read_paths", "allowed_write_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [_validate_contract_path(value) for value in values]
        _require_unique(normalized, "project policy path 不能重复")
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
        validated = SliceVerification.model_validate(
            {
                "commands": values,
                "oracle": {"kind": "all_commands_exit_zero"},
            }
        )
        return list(validated.commands)


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
        _validate_case_role_and_class(
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
        _require_unique(values, "ground truth acceptance fact id 不能重复")
        return values

    @field_validator("required_verification_commands")
    @classmethod
    def validate_verification_commands(cls, values: list[str]) -> list[str]:
        validated = SliceVerification.model_validate(
            {
                "commands": values,
                "oracle": {"kind": "all_commands_exit_zero"},
            }
        )
        return list(validated.commands)

    @field_validator("manual_adjudication_rule")
    @classmethod
    def validate_public_rule(cls, value: str) -> str:
        return _validate_public_text(value, "manual adjudication rule")

    @model_validator(mode="after")
    def validate_ground_truth_semantics(self) -> MA2BGroundTruthArtifact:
        _validate_case_role_and_class(
            case_id=self.case_id,
            package_role=self.package_role,
            case_class=self.case_class,
        )
        expected_outcome = _expected_outcome_for_class(self.case_class)
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


def load_ma2b_case_package(
    *,
    repo_root: Path,
    case_id: str,
    task_pack_root: Path,
    ground_truth_root: Path,
) -> MA2BCasePackage:
    """从仓库内固定目录加载一个 case；任一缺失、篡改或错绑均抛出稳定错误。"""

    if not _CASE_ID_PATTERN.fullmatch(case_id):
        raise MA2BTaskPackError("case_id_invalid")
    repo = _resolve_repository_root(repo_root)
    task_pack_base = _resolve_contract_directory(
        repo,
        task_pack_root,
        issue_code="task_pack_root_invalid",
    )
    ground_truth_base = _resolve_contract_directory(
        repo,
        ground_truth_root,
        issue_code="ground_truth_root_invalid",
    )
    case_dir = _resolve_existing_directory(
        repo,
        task_pack_base.relative_to(repo) / case_id,
        issue_code="case_directory_invalid",
    )
    manifest_path = _resolve_existing_file(
        repo,
        case_dir.relative_to(repo) / "case-manifest.json",
        issue_code="case_manifest_invalid",
    )
    manifest, manifest_raw = _read_json_model(
        manifest_path,
        MA2BCaseManifest,
        invalid_json_code="case_manifest_invalid_json",
        invalid_schema_code="case_manifest_schema_invalid",
    )
    if manifest.case_id != case_id:
        raise MA2BTaskPackError("case_identity_mismatch")

    task = _read_bound_case_artifact(
        repo=repo,
        case_dir=case_dir,
        reference=manifest.task_ref,
        expected_name="task.json",
        model=MA2BTaskArtifact,
    )
    initial_workspace = _read_bound_case_artifact(
        repo=repo,
        case_dir=case_dir,
        reference=manifest.initial_workspace_ref,
        expected_name="initial-workspace.json",
        model=MA2BInitialWorkspaceArtifact,
    )
    project_policy = _read_bound_case_artifact(
        repo=repo,
        case_dir=case_dir,
        reference=manifest.project_policy_ref,
        expected_name="project-policy.json",
        model=MA2BProjectPolicyArtifact,
    )
    verification = _read_bound_case_artifact(
        repo=repo,
        case_dir=case_dir,
        reference=manifest.verification_manifest_ref,
        expected_name="verification-manifest.json",
        model=MA2BVerificationManifest,
    )

    ground_truth_path = _resolve_existing_file(
        repo,
        ground_truth_base.relative_to(repo) / f"{case_id}.json",
        issue_code="ground_truth_invalid",
    )
    ground_truth, _ = _read_json_model(
        ground_truth_path,
        MA2BGroundTruthArtifact,
        invalid_json_code="ground_truth_invalid_json",
        invalid_schema_code="ground_truth_schema_invalid",
    )

    _validate_cross_artifact_semantics(
        manifest=manifest,
        task=task,
        initial_workspace=initial_workspace,
        project_policy=project_policy,
        verification=verification,
        ground_truth=ground_truth,
    )
    _validate_initial_workspace_tree(repo, initial_workspace)

    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if ground_truth.case_manifest_sha256 != manifest_sha256:
        raise MA2BTaskPackError("case_manifest_hash_mismatch")
    task_pack_sha256 = compute_ma2b_task_pack_sha256(
        manifest,
        case_manifest_sha256=manifest_sha256,
    )
    if ground_truth.task_pack_sha256 != task_pack_sha256:
        raise MA2BTaskPackError("task_pack_hash_mismatch")

    return MA2BCasePackage(
        case_dir=case_dir,
        ground_truth_path=ground_truth_path,
        task=task,
        initial_workspace=initial_workspace,
        project_policy=project_policy,
        verification=verification,
        manifest=manifest,
        ground_truth=ground_truth,
        task_pack_sha256=task_pack_sha256,
    )


def compute_ma2b_workspace_tree_sha256(files: list[MA2BWorkspaceFile]) -> str:
    payload = {
        "schema": "ma2b-workspace-tree-v1",
        "files": [item.model_dump(mode="json") for item in files],
    }
    return _sha256_json(payload)


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
    return _sha256_json(payload)


def _read_bound_case_artifact(
    *,
    repo: Path,
    case_dir: Path,
    reference: ArtifactReference,
    expected_name: str,
    model: type[_ModelT],
) -> _ModelT:
    if reference.relative_path != expected_name:
        raise MA2BTaskPackError("artifact_reference_path_mismatch")
    artifact_path = _resolve_existing_file(
        repo,
        case_dir.relative_to(repo) / expected_name,
        issue_code="task_pack_artifact_invalid",
    )
    raw = _read_bounded_file(artifact_path)
    if hashlib.sha256(raw).hexdigest() != reference.sha256:
        raise MA2BTaskPackError("artifact_reference_hash_mismatch")
    return _validate_json_model(
        raw,
        model,
        invalid_json_code="task_pack_artifact_invalid_json",
        invalid_schema_code="task_pack_artifact_schema_invalid",
    )


def _validate_cross_artifact_semantics(
    *,
    manifest: MA2BCaseManifest,
    task: MA2BTaskArtifact,
    initial_workspace: MA2BInitialWorkspaceArtifact,
    project_policy: MA2BProjectPolicyArtifact,
    verification: MA2BVerificationManifest,
    ground_truth: MA2BGroundTruthArtifact,
) -> None:
    case_ids = {
        manifest.case_id,
        task.case_id,
        initial_workspace.case_id,
        project_policy.case_id,
        verification.case_id,
        ground_truth.case_id,
    }
    if len(case_ids) != 1:
        raise MA2BTaskPackError("case_identity_mismatch")
    if ground_truth.package_role != manifest.package_role:
        raise MA2BTaskPackError("package_role_mismatch")
    if ground_truth.case_class != manifest.case_class:
        raise MA2BTaskPackError("case_class_mismatch")

    acceptance_ids = [item.fact_id for item in task.acceptance_facts]
    if ground_truth.acceptance_fact_ids != acceptance_ids:
        raise MA2BTaskPackError("acceptance_fact_mismatch")
    if ground_truth.required_verification_commands != verification.commands:
        raise MA2BTaskPackError("verification_command_mismatch")

    if manifest.case_class == "human_required":
        if task.unresolved_decision is None:
            raise MA2BTaskPackError("unresolved_decision_missing")
    elif task.unresolved_decision is not None:
        raise MA2BTaskPackError("unexpected_unresolved_decision")

    if manifest.package_role == "fake_driver_fixture":
        if initial_workspace.source_kind != "synthetic_fixture":
            raise MA2BTaskPackError("fixture_source_kind_mismatch")
    elif initial_workspace.source_kind != "git_snapshot":
        raise MA2BTaskPackError("pilot_source_kind_mismatch")


def _validate_initial_workspace_tree(
    repo: Path,
    initial_workspace: MA2BInitialWorkspaceArtifact,
) -> None:
    source_tree = _resolve_existing_directory(
        repo,
        Path(initial_workspace.source_tree),
        issue_code="initial_workspace_source_invalid",
    )
    actual_files = _collect_workspace_files(repo, source_tree)
    declared = {
        item.relative_path: item
        for item in initial_workspace.files
    }
    if set(actual_files) != set(declared):
        raise MA2BTaskPackError("initial_workspace_file_set_mismatch")

    for relative_path, raw in actual_files.items():
        expected = declared[relative_path]
        if len(raw) != expected.size_bytes:
            raise MA2BTaskPackError("initial_workspace_file_size_mismatch")
        if hashlib.sha256(raw).hexdigest() != expected.sha256:
            raise MA2BTaskPackError("initial_workspace_file_hash_mismatch")


def _collect_workspace_files(repo: Path, source_tree: Path) -> dict[str, bytes]:
    collected: dict[str, bytes] = {}
    total_bytes = 0

    def visit(directory: Path) -> None:
        nonlocal total_bytes
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise MA2BTaskPackError("initial_workspace_source_unreadable") from exc
        for entry in entries:
            if _is_link_or_reparse_point(entry):
                raise MA2BTaskPackError("initial_workspace_link_forbidden")
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise MA2BTaskPackError("initial_workspace_source_unreadable") from exc
            if stat.S_ISDIR(metadata.st_mode):
                visit(entry)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise MA2BTaskPackError("initial_workspace_special_file_forbidden")
            raw = _read_bounded_file(
                entry,
                maximum_bytes=MAX_MA2B_WORKSPACE_FILE_BYTES,
            )
            total_bytes += len(raw)
            if total_bytes > MAX_MA2B_WORKSPACE_TOTAL_BYTES:
                raise MA2BTaskPackError("initial_workspace_too_large")
            relative_path = entry.relative_to(source_tree).as_posix()
            _validate_contract_path(relative_path)
            collected[relative_path] = raw
            if len(collected) > 512:
                raise MA2BTaskPackError("initial_workspace_file_count_exceeded")

    if not source_tree.is_relative_to(repo):
        raise MA2BTaskPackError("initial_workspace_source_invalid")
    visit(source_tree)
    return collected


def _read_json_model(
    path: Path,
    model: type[_ModelT],
    *,
    invalid_json_code: str,
    invalid_schema_code: str,
) -> tuple[_ModelT, bytes]:
    raw = _read_bounded_file(path)
    return (
        _validate_json_model(
            raw,
            model,
            invalid_json_code=invalid_json_code,
            invalid_schema_code=invalid_schema_code,
        ),
        raw,
    )


def _validate_json_model(
    raw: bytes,
    model: type[_ModelT],
    *,
    invalid_json_code: str,
    invalid_schema_code: str,
) -> _ModelT:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MA2BTaskPackError(invalid_json_code) from exc
    try:
        return model.model_validate(payload)
    except (ValidationError, RecursionError, TypeError, ValueError) as exc:
        raise MA2BTaskPackError(invalid_schema_code) from exc


def _read_bounded_file(
    path: Path,
    *,
    maximum_bytes: int = MAX_MA2B_TASK_PACK_ARTIFACT_BYTES,
) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except (OSError, ValueError) as exc:
        raise MA2BTaskPackError("artifact_unreadable") from exc
    if len(payload) > maximum_bytes:
        raise MA2BTaskPackError("artifact_too_large")
    return payload


def _resolve_repository_root(repo_root: Path) -> Path:
    try:
        resolved = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BTaskPackError("repository_root_invalid") from exc
    if not resolved.is_dir():
        raise MA2BTaskPackError("repository_root_invalid")
    return resolved


def _resolve_contract_directory(
    repo: Path,
    relative_path: Path,
    *,
    issue_code: str,
) -> Path:
    if relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
        raise MA2BTaskPackError(issue_code)
    return _resolve_existing_directory(repo, relative_path, issue_code=issue_code)


def _resolve_existing_directory(
    repo: Path,
    relative_path: Path,
    *,
    issue_code: str,
) -> Path:
    candidate = _resolve_existing_repo_path(repo, relative_path, issue_code=issue_code)
    if not candidate.is_dir():
        raise MA2BTaskPackError(issue_code)
    return candidate


def _resolve_existing_file(
    repo: Path,
    relative_path: Path,
    *,
    issue_code: str,
) -> Path:
    candidate = _resolve_existing_repo_path(repo, relative_path, issue_code=issue_code)
    if not candidate.is_file():
        raise MA2BTaskPackError(issue_code)
    return candidate


def _resolve_existing_repo_path(
    repo: Path,
    relative_path: Path,
    *,
    issue_code: str,
) -> Path:
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise MA2BTaskPackError(issue_code)
    candidate = repo.joinpath(relative_path)
    if _path_contains_link_or_reparse(repo, candidate):
        raise MA2BTaskPackError(issue_code)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MA2BTaskPackError(issue_code) from exc
    if not resolved.is_relative_to(repo):
        raise MA2BTaskPackError(issue_code)
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
    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(file_attributes & reparse_flag)


def _validate_case_role_and_class(
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
    if case_class != _expected_case_class(case_id):
        raise ValueError("case_class 与预注册 case 编号不一致")


def _expected_case_class(case_id: str) -> MA2BCaseClass:
    case_number = int(case_id[-2:])
    if case_number <= 8:
        return "code_change"
    if case_number <= 10:
        return "human_required"
    if case_number == 11:
        return "stale_evidence"
    return "invalid_verifier"


def _expected_outcome_for_class(case_class: MA2BCaseClass) -> MA2BExpectedOutcome:
    if case_class == "code_change":
        return "accepted_change"
    if case_class == "human_required":
        return "safe_deferral"
    return "safe_block"


def _validate_contract_path(value: str) -> str:
    try:
        reference = ArtifactReference.model_validate(
            {
                "relative_path": value,
                "sha256": _ZERO_HASH,
            }
        )
    except ValidationError as exc:
        raise ValueError("必须使用安全的仓库相对路径") from exc
    return reference.relative_path


def _validate_public_text(value: str, field_name: str) -> str:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 不能包含换行或 NUL")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(f"{field_name} 不能包含控制字符或双向格式字符")
    if redact_text(value) != value:
        raise ValueError(f"{field_name} 会触发脱敏，不能进入公开 task-pack")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("JSON key 不能重复")
        payload[key] = value
    return payload


def _require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _sha256_json(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "MA2B_TASK_PACK_SCHEMA_VERSION",
    "MA2BAcceptanceFact",
    "MA2BCaseManifest",
    "MA2BCasePackage",
    "MA2BExpectedOutcome",
    "MA2BGroundTruthArtifact",
    "MA2BInitialWorkspaceArtifact",
    "MA2BPackageRole",
    "MA2BProjectPolicyArtifact",
    "MA2BTaskArtifact",
    "MA2BTaskPackError",
    "MA2BUnresolvedDecision",
    "MA2BVerificationManifest",
    "MA2BWorkspaceFile",
    "compute_ma2b_task_pack_sha256",
    "compute_ma2b_workspace_tree_sha256",
    "load_ma2b_case_package",
]
