from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import task_pack_models as _models
from .task_pack_models import (
    ArtifactReference,
    MAX_MA2B_TASK_PACK_ARTIFACT_BYTES,
    MAX_MA2B_WORKSPACE_FILE_BYTES,
    MAX_MA2B_WORKSPACE_TOTAL_BYTES,
    MA2B_TASK_PACK_SCHEMA_VERSION as MA2B_TASK_PACK_SCHEMA_VERSION,
    MA2BAcceptanceFact as MA2BAcceptanceFact,
    MA2BCaseManifest,
    MA2BCasePackage,
    MA2BExpectedOutcome as MA2BExpectedOutcome,
    MA2BGroundTruthArtifact,
    MA2BInitialWorkspaceArtifact,
    MA2BPackageRole as MA2BPackageRole,
    MA2BProjectPolicyArtifact,
    MA2BTaskArtifact,
    MA2BTaskPackError,
    MA2BUnresolvedDecision as MA2BUnresolvedDecision,
    MA2BVerificationManifest,
    MA2BWorkspaceFile as MA2BWorkspaceFile,
    _CASE_ID_PATTERN,
    compute_ma2b_task_pack_sha256,
    compute_ma2b_workspace_tree_sha256 as compute_ma2b_workspace_tree_sha256,
    reject_duplicate_json_keys,
    validate_contract_path,
)


_ModelT = TypeVar("_ModelT", bound=BaseModel)


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
    _validate_case_identities(
        manifest,
        task,
        initial_workspace,
        project_policy,
        verification,
        ground_truth,
    )
    _validate_ground_truth_binding(manifest, task, verification, ground_truth)
    _validate_decision_contract(manifest, task)
    _validate_workspace_source_kind(manifest, initial_workspace)


def _validate_case_identities(
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


def _validate_ground_truth_binding(
    manifest: MA2BCaseManifest,
    task: MA2BTaskArtifact,
    verification: MA2BVerificationManifest,
    ground_truth: MA2BGroundTruthArtifact,
) -> None:
    if ground_truth.package_role != manifest.package_role:
        raise MA2BTaskPackError("package_role_mismatch")
    if ground_truth.case_class != manifest.case_class:
        raise MA2BTaskPackError("case_class_mismatch")
    if ground_truth.acceptance_fact_ids != [
        item.fact_id for item in task.acceptance_facts
    ]:
        raise MA2BTaskPackError("acceptance_fact_mismatch")
    if ground_truth.required_verification_commands != verification.commands:
        raise MA2BTaskPackError("verification_command_mismatch")


def _validate_decision_contract(
    manifest: MA2BCaseManifest,
    task: MA2BTaskArtifact,
) -> None:
    if manifest.case_class == "human_required" and task.unresolved_decision is None:
        raise MA2BTaskPackError("unresolved_decision_missing")
    if manifest.case_class != "human_required" and task.unresolved_decision is not None:
        raise MA2BTaskPackError("unexpected_unresolved_decision")


def _validate_workspace_source_kind(
    manifest: MA2BCaseManifest,
    initial_workspace: MA2BInitialWorkspaceArtifact,
) -> None:
    if (
        manifest.package_role == "fake_driver_fixture"
        and initial_workspace.source_kind != "synthetic_fixture"
    ):
        raise MA2BTaskPackError("fixture_source_kind_mismatch")
    if (
        manifest.package_role == "pilot_case"
        and initial_workspace.source_kind != "git_snapshot"
    ):
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
    declared = {item.relative_path: item for item in initial_workspace.files}
    if set(actual_files) != set(declared):
        raise MA2BTaskPackError("initial_workspace_file_set_mismatch")

    for relative_path, raw in actual_files.items():
        expected = declared[relative_path]
        if len(raw) != expected.size_bytes:
            raise MA2BTaskPackError("initial_workspace_file_size_mismatch")
        if hashlib.sha256(raw).hexdigest() != expected.sha256:
            raise MA2BTaskPackError("initial_workspace_file_hash_mismatch")


@dataclass
class _WorkspaceCollector:
    source_tree: Path
    files: dict[str, bytes] = field(default_factory=dict)
    total_bytes: int = 0

    def collect(self) -> dict[str, bytes]:
        self._visit(self.source_tree)
        return self.files

    def _visit(self, directory: Path) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise MA2BTaskPackError("initial_workspace_source_unreadable") from exc
        for entry in entries:
            self._consume_entry(entry)

    def _consume_entry(self, entry: Path) -> None:
        if _is_link_or_reparse_point(entry):
            raise MA2BTaskPackError("initial_workspace_link_forbidden")
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise MA2BTaskPackError("initial_workspace_source_unreadable") from exc
        if stat.S_ISDIR(metadata.st_mode):
            self._visit(entry)
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise MA2BTaskPackError("initial_workspace_special_file_forbidden")
        self._add_file(entry)

    def _add_file(self, entry: Path) -> None:
        raw = _read_bounded_file(
            entry,
            maximum_bytes=MAX_MA2B_WORKSPACE_FILE_BYTES,
        )
        self.total_bytes += len(raw)
        if self.total_bytes > MAX_MA2B_WORKSPACE_TOTAL_BYTES:
            raise MA2BTaskPackError("initial_workspace_too_large")
        relative_path = entry.relative_to(self.source_tree).as_posix()
        validate_contract_path(relative_path)
        self.files[relative_path] = raw
        if len(self.files) > 512:
            raise MA2BTaskPackError("initial_workspace_file_count_exceeded")


def _collect_workspace_files(repo: Path, source_tree: Path) -> dict[str, bytes]:
    if not source_tree.is_relative_to(repo):
        raise MA2BTaskPackError("initial_workspace_source_invalid")
    return _WorkspaceCollector(source_tree).collect()


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
            object_pairs_hook=reject_duplicate_json_keys,
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
    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
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
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


__all__ = [*_models.__all__, "load_ma2b_case_package"]
