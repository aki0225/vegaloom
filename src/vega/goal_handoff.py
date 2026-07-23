from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .goal_evidence import validate_goal_evidence
from .models import GoalCheckpointRecord, GoalContract, GoalState
from .project_config import CONFIG_FILENAMES
from .redaction import redact_text, redact_value, sensitive_path_reason
from .run_utils import resolve_run_dir
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


HANDOFF_SCHEMA_VERSION = 1
DEFAULT_CONTEXT_MAX_CHARS = 12_000
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_VERSION_PATTERN = re.compile(r"^v([0-9]{4})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_POLICY_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".tmp",
    ".local-validation",
    "__pycache__",
    "build",
    "dist",
    "memory",
    "node_modules",
    "runs",
}
_POLICY_ROOT_FILES = (*CONFIG_FILENAMES, "AGENTS.md", "docs/PRODUCT-CONTRACT.md")
_MAX_POLICY_FILES = 100
_MAX_LIST_ITEMS = 50
_MAX_TEXT_CHARS = 20_000

GoalHandoffArtifactScope = Literal["goal_run", "repo", "workspace"]


class GoalHandoffArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: Literal["goal_run", "repo"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value, "artifact.path")


class GoalHandoffInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = HANDOFF_SCHEMA_VERSION
    handoff_version: int = Field(default=1, ge=1, le=9999)
    source_worker_epoch: str
    target_worker_epoch: str
    source_session_id: str
    next_action: str
    hard_constraints: list[str] = Field(default_factory=list, max_length=_MAX_LIST_ITEMS)
    verified_facts: list[str] = Field(default_factory=list, max_length=_MAX_LIST_ITEMS)
    failed_approaches: list[str] = Field(default_factory=list, max_length=_MAX_LIST_ITEMS)
    open_questions: list[str] = Field(default_factory=list, max_length=_MAX_LIST_ITEMS)
    authoritative_artifacts: list[GoalHandoffArtifactInput] = Field(
        default_factory=list,
        max_length=_MAX_LIST_ITEMS,
    )
    memory_mode: Literal["off"] = "off"
    source_chat_included: Literal[False] = False

    @field_validator(
        "source_worker_epoch",
        "target_worker_epoch",
        "source_session_id",
    )
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _normalize_identity(value, "handoff identity")

    @field_validator("next_action")
    @classmethod
    def validate_next_action(cls, value: str) -> str:
        return _validate_safe_text(value, "next_action")

    @field_validator(
        "hard_constraints",
        "verified_facts",
        "failed_approaches",
        "open_questions",
    )
    @classmethod
    def validate_text_list(cls, value: list[str]) -> list[str]:
        result = [_validate_safe_text(item, "handoff text") for item in value]
        if len(result) != len(set(result)):
            raise ValueError("handoff 列表不能包含重复项")
        return result


class GoalHandoffArtifactBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: GoalHandoffArtifactScope
    path: str
    sha256: str
    size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value, "artifact.path")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value, "artifact.sha256")


class GoalHandoffWorkspaceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    fingerprint: str
    head_sha: str
    status_sha256: str
    full_diff_sha256: str
    staged_diff_sha256: str
    unstaged_diff_sha256: str
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    index_flags_sha256: str
    untracked_content_complete: bool
    changed_files: list[str]
    untracked_files: list[str]
    unsafe_index_paths: list[str]

    @field_validator(
        "fingerprint",
        "status_sha256",
        "full_diff_sha256",
        "staged_diff_sha256",
        "unstaged_diff_sha256",
        "untracked_manifest_sha256",
        "ignored_manifest_sha256",
        "index_flags_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value, "workspace sha256")

    @field_validator("head_sha")
    @classmethod
    def validate_head_sha(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", normalized):
            raise ValueError("workspace HEAD 必须是 40..64 位十六进制 Git object id")
        return normalized

    @field_validator("changed_files", "untracked_files", "unsafe_index_paths")
    @classmethod
    def validate_paths(cls, value: list[str]) -> list[str]:
        normalized = [
            _normalize_relative_path(item, "workspace path")
            for item in value
        ]
        if len(normalized) != len(set(normalized)):
            raise ValueError("workspace path 不能包含重复项")
        return normalized


class GoalHandoffPolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    sha256: str
    size: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value, "policy.path")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value, "policy.sha256")


class GoalHandoffPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    files: list[GoalHandoffPolicyFile]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value, "policy snapshot sha256")


class CheckpointHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = HANDOFF_SCHEMA_VERSION
    handoff_version: int = Field(ge=1, le=9999)
    handoff_sha256: str
    goal_run_id: str
    checkpoint: str
    source_worker_epoch: str
    target_worker_epoch: str
    source_session_id: str
    objective: str
    goal_contract_sha256: str
    scope_profile: str | None
    non_goals: list[str]
    success_conditions: list[str]
    next_action: str
    hard_constraints: list[str]
    verified_facts: list[str]
    failed_approaches: list[str]
    open_questions: list[str]
    authoritative_artifacts: list[GoalHandoffArtifactBinding]
    checkpoint_record_sha256: str
    workspace: GoalHandoffWorkspaceBinding
    project_policy_snapshot: GoalHandoffPolicySnapshot
    memory_mode: Literal["off"] = "off"
    source_chat_included: Literal[False] = False
    created_at: str

    @field_validator(
        "handoff_sha256",
        "checkpoint_record_sha256",
        "goal_contract_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value, "handoff sha256")

    @field_validator(
        "source_worker_epoch",
        "target_worker_epoch",
        "source_session_id",
    )
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return _normalize_identity(value, "handoff identity")

    @field_validator("objective", "next_action")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _validate_safe_text(value, "handoff text")

    @field_validator(
        "non_goals",
        "success_conditions",
    )
    @classmethod
    def validate_contract_text_list(cls, value: list[str]) -> list[str]:
        return [_validate_safe_text(item, "handoff text") for item in value]

    @field_validator(
        "hard_constraints",
        "verified_facts",
        "failed_approaches",
        "open_questions",
    )
    @classmethod
    def validate_text_list(cls, value: list[str]) -> list[str]:
        result = [_validate_safe_text(item, "handoff text") for item in value]
        if len(result) != len(set(result)):
            raise ValueError("handoff 列表不能包含重复项")
        return result


class GoalHandoffWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["created", "adopted"] = "created"
    version: str
    handoff_path: str
    handoff_sha256: str
    artifact_paths: list[str]

    @field_validator("handoff_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _validate_sha256(value, "handoff result sha256")


class GoalHandoffCompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["ready", "split_required", "blocked"]
    version: str
    consumer_session_id: str
    consumer_worker_epoch: str
    max_chars: int
    context_chars: int = 0
    context_sha256: str | None = None
    handoff_sha256: str | None = None
    issues: list[str] = Field(default_factory=list)
    artifact_paths: list[str]

    @field_validator("context_sha256", "handoff_sha256")
    @classmethod
    def validate_optional_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_sha256(value, "handoff compile sha256")


def create_goal_handoff(
    handoff_input: GoalHandoffInput,
    *,
    workspace: Path,
    run_id: str,
    checkpoint: str,
    objective: str,
    repo_path: str | Path,
    created_at: str | None = None,
) -> GoalHandoffWriteResult:
    """创建一个不可覆盖的 checkpoint handoff 版本。"""

    validated_input = GoalHandoffInput.model_validate(
        handoff_input.model_dump(mode="python"),
        strict=True,
    )
    if (
        validated_input.source_worker_epoch.casefold()
        == validated_input.target_worker_epoch.casefold()
    ):
        raise ValueError("source_worker_epoch 与 target_worker_epoch 必须不同")
    run_dir, contract, record, _ = _load_goal_checkpoint(
        workspace,
        run_id,
        checkpoint,
    )
    repo = Path(repo_path).resolve()
    if repo != Path(contract.repo_path).resolve():
        raise ValueError("handoff repo_path 与 Goal contract 不一致")
    if objective.strip() != contract.objective:
        raise ValueError("handoff objective 与 Goal contract 不一致")

    workspace_binding = _capture_workspace_binding(repo)
    policy_snapshot = _capture_policy_snapshot(repo)
    artifacts = _bind_authoritative_artifacts(
        workspace.resolve(),
        run_dir,
        repo,
        checkpoint,
        record,
        validated_input.authoritative_artifacts,
    )
    version = _format_version(validated_input.handoff_version)
    published_version_dir = (
        run_dir
        / "checkpoints"
        / _normalize_checkpoint(checkpoint)
        / "handoffs"
        / version
    )
    existing_created_at = _existing_handoff_created_at(
        run_dir,
        published_version_dir,
    )
    version_dir, published_version_dir = _prepare_version_dir(
        run_dir,
        checkpoint,
        version,
    )
    handoff_path = version_dir / "checkpoint-handoff.json"
    published_handoff_path = published_version_dir / "checkpoint-handoff.json"

    payload = CheckpointHandoff(
        handoff_version=validated_input.handoff_version,
        handoff_sha256="0" * 64,
        goal_run_id=run_dir.name,
        checkpoint=_normalize_checkpoint(checkpoint),
        source_worker_epoch=validated_input.source_worker_epoch,
        target_worker_epoch=validated_input.target_worker_epoch,
        source_session_id=validated_input.source_session_id,
        objective=contract.objective,
        goal_contract_sha256=_sha256_json(contract.model_dump(mode="json")),
        scope_profile=contract.scope_profile,
        non_goals=contract.non_goals,
        success_conditions=contract.success_conditions,
        next_action=validated_input.next_action,
        hard_constraints=validated_input.hard_constraints,
        verified_facts=validated_input.verified_facts,
        failed_approaches=validated_input.failed_approaches,
        open_questions=validated_input.open_questions,
        authoritative_artifacts=artifacts,
        checkpoint_record_sha256=_sha256_json(record.model_dump(mode="json")),
        workspace=workspace_binding,
        project_policy_snapshot=policy_snapshot,
        memory_mode="off",
        source_chat_included=False,
        created_at=(
            created_at
            or existing_created_at
            or datetime.now(UTC).isoformat()
        ),
    )
    payload_dict = payload.model_dump(mode="json")
    payload_dict["handoff_sha256"] = _handoff_payload_sha256(payload_dict)
    safe_payload = redact_value(payload_dict)
    if safe_payload != payload_dict:
        raise ValueError("handoff 内容触发敏感信息检测，已拒绝写入")
    _write_json_atomic(handoff_path, payload_dict)

    report_path = version_dir / "handoff-report.md"
    _write_text_atomic(
        report_path,
        _render_handoff_report(
            CheckpointHandoff.model_validate(payload_dict),
            published_handoff_path.relative_to(run_dir).as_posix(),
        ),
    )
    publication_status = _publish_package_directory(
        version_dir,
        published_version_dir,
        run_dir=run_dir,
        label=f"handoff version {version}",
    )
    artifact_paths = [
        published_handoff_path.relative_to(run_dir).as_posix(),
        (published_version_dir / report_path.name).relative_to(run_dir).as_posix(),
    ]
    return GoalHandoffWriteResult(
        status=publication_status,
        version=version,
        handoff_path=artifact_paths[0],
        handoff_sha256=payload_dict["handoff_sha256"],
        artifact_paths=artifact_paths,
    )


def compile_goal_handoff_context(
    *,
    workspace: Path,
    run_id: str,
    checkpoint: str,
    version: str,
    consumer_session_id: str,
    consumer_worker_epoch: str,
    max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
    objective: str,
    repo_path: str | Path,
) -> GoalHandoffCompileResult:
    """重读当前事实并编译 fresh session 可消费的 checkpoint context。"""

    normalized_version = _normalize_version(version)
    normalized_session = _normalize_identity(
        consumer_session_id,
        "consumer_session_id",
    )
    normalized_epoch = _normalize_identity(
        consumer_worker_epoch,
        "consumer_worker_epoch",
    )
    if max_chars < 1 or max_chars > 1_000_000:
        raise ValueError("max_chars 必须在 1..1000000 之间")

    run_dir = resolve_run_dir(workspace, run_id)
    checkpoint_id = _normalize_checkpoint(checkpoint)
    version_dir = (
        run_dir
        / "checkpoints"
        / checkpoint_id
        / "handoffs"
        / normalized_version
    )
    _require_existing_directory_within(
        run_dir,
        version_dir,
        "handoff version",
    )
    _require_registered_handoff_version(
        run_dir,
        checkpoint_id,
        normalized_version,
    )
    published_consumer_dir = (
        version_dir / "consumers" / normalized_session
    )
    consumer_dir, published_consumer_dir = _prepare_consumer_dir(
        published_consumer_dir,
        version_dir,
        run_dir,
    )

    issues: list[str] = []
    handoff_path = version_dir / "checkpoint-handoff.json"
    handoff = _read_handoff(handoff_path, issues)
    current_contract: GoalContract | None = None
    current_record: GoalCheckpointRecord | None = None
    checkpoint_record_path: Path | None = None
    try:
        _, current_contract, current_record, checkpoint_record_path = (
            _load_goal_checkpoint(
                workspace,
                run_id,
                checkpoint_id,
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        issues.append(f"checkpoint_evidence_stale:{_safe_issue(exc)}")

    repo = Path(repo_path).resolve()
    current_workspace: GoalHandoffWorkspaceBinding | None = None
    current_policy: GoalHandoffPolicySnapshot | None = None
    try:
        current_workspace = _capture_workspace_binding(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        issues.append(f"workspace_snapshot_failed:{_safe_issue(exc)}")
    try:
        current_policy = _capture_policy_snapshot(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        issues.append(f"policy_snapshot_failed:{_safe_issue(exc)}")

    if handoff is not None:
        _validate_handoff_identity(
            handoff,
            workspace=workspace.resolve(),
            run_dir=run_dir,
            checkpoint=checkpoint_id,
            version=normalized_version,
            consumer_session_id=normalized_session,
            consumer_worker_epoch=normalized_epoch,
            objective=objective,
            repo=repo,
            current_contract=current_contract,
            current_record=current_record,
            checkpoint_record_path=checkpoint_record_path,
            current_workspace=current_workspace,
            current_policy=current_policy,
            issues=issues,
        )

    if issues or handoff is None or current_workspace is None or current_policy is None:
        blocked_result = _write_blocked_result(
            run_dir=run_dir,
            consumer_dir=consumer_dir,
            artifact_dir=published_consumer_dir,
            version=normalized_version,
            consumer_session_id=normalized_session,
            consumer_worker_epoch=normalized_epoch,
            max_chars=max_chars,
            handoff=handoff,
            issues=issues or ["handoff_invalid"],
        )
        return _finalize_compile_result(
            run_dir=run_dir,
            staging_dir=consumer_dir,
            published_dir=published_consumer_dir,
            result=blocked_result,
            handoff=handoff,
            workspace=current_workspace,
            policy=current_policy,
        )

    sections = _context_sections(
        handoff,
        consumer_session_id=normalized_session,
        consumer_worker_epoch=normalized_epoch,
        workspace=current_workspace,
        policy=current_policy,
    )
    context_text = _render_context_text(sections)
    context_chars = len(context_text)
    context_sha256 = _sha256_text(context_text)

    late_issues: list[str] = []
    refreshed_workspace: GoalHandoffWorkspaceBinding | None = None
    refreshed_policy: GoalHandoffPolicySnapshot | None = None
    try:
        refreshed_workspace = _capture_workspace_binding(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        late_issues.append(
            f"workspace_snapshot_failed_during_compile:{_safe_issue(exc)}"
        )
    try:
        refreshed_policy = _capture_policy_snapshot(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        late_issues.append(
            f"policy_snapshot_failed_during_compile:{_safe_issue(exc)}"
        )
    if (
        refreshed_workspace is not None
        and refreshed_workspace != current_workspace
    ):
        late_issues.append("workspace_drift_during_compile")
    if refreshed_policy is not None and refreshed_policy != current_policy:
        late_issues.append("project_policy_drift_during_compile")
    _validate_bound_artifacts(
        handoff.authoritative_artifacts,
        workspace=workspace.resolve(),
        run_dir=run_dir,
        repo=repo,
        issues=late_issues,
    )
    if late_issues:
        blocked_result = _write_blocked_result(
            run_dir=run_dir,
            consumer_dir=consumer_dir,
            artifact_dir=published_consumer_dir,
            version=normalized_version,
            consumer_session_id=normalized_session,
            consumer_worker_epoch=normalized_epoch,
            max_chars=max_chars,
            handoff=handoff,
            issues=late_issues,
        )
        return _finalize_compile_result(
            run_dir=run_dir,
            staging_dir=consumer_dir,
            published_dir=published_consumer_dir,
            result=blocked_result,
            handoff=handoff,
            workspace=refreshed_workspace,
            policy=refreshed_policy,
        )

    if refreshed_workspace is None or refreshed_policy is None:
        raise RuntimeError(
            "handoff 编译内部状态不一致：二次 workspace/policy 快照缺失"
        )
    current_workspace = refreshed_workspace
    current_policy = refreshed_policy
    metrics = {
        "schema_version": 1,
        "status": "ready" if context_chars <= max_chars else "split_required",
        "max_chars": max_chars,
        "context_chars": context_chars,
        "over_by_chars": max(0, context_chars - max_chars),
        "context_sha256": context_sha256,
        "section_chars": [
            {
                "section": name,
                "chars": len(_render_context_text([(name, text)])),
            }
            for name, text in sections
        ],
    }
    metrics_path = consumer_dir / "context-metrics.json"
    _write_json_atomic(metrics_path, metrics)

    if context_chars > max_chars:
        split_plan = _build_split_plan(sections, max_chars, context_chars)
        split_json_path = consumer_dir / "checkpoint-split-plan.json"
        split_report_path = consumer_dir / "checkpoint-split-plan.md"
        _write_json_atomic(split_json_path, split_plan)
        _write_text_atomic(
            split_report_path,
            _render_split_plan(split_plan),
        )
        artifacts = [
            (published_consumer_dir / metrics_path.name)
            .relative_to(run_dir)
            .as_posix(),
            (published_consumer_dir / split_json_path.name)
            .relative_to(run_dir)
            .as_posix(),
            (published_consumer_dir / split_report_path.name)
            .relative_to(run_dir)
            .as_posix(),
        ]
        split_result = GoalHandoffCompileResult(
            status="split_required",
            version=normalized_version,
            consumer_session_id=normalized_session,
            consumer_worker_epoch=normalized_epoch,
            max_chars=max_chars,
            context_chars=context_chars,
            context_sha256=context_sha256,
            handoff_sha256=handoff.handoff_sha256,
            artifact_paths=artifacts,
        )
        return _finalize_compile_result(
            run_dir=run_dir,
            staging_dir=consumer_dir,
            published_dir=published_consumer_dir,
            result=split_result,
            handoff=handoff,
            workspace=current_workspace,
            policy=current_policy,
        )

    context_path = consumer_dir / "checkpoint-context.md"
    context_json_path = consumer_dir / "checkpoint-context.json"
    context_payload = {
        "schema_version": 1,
        "status": "ready",
        "handoff_sha256": handoff.handoff_sha256,
        "goal_run_id": handoff.goal_run_id,
        "checkpoint": handoff.checkpoint,
        "handoff_version": handoff.handoff_version,
        "source_worker_epoch": handoff.source_worker_epoch,
        "target_worker_epoch": handoff.target_worker_epoch,
        "source_session_id": handoff.source_session_id,
        "consumer_worker_epoch": normalized_epoch,
        "consumer_session_id": normalized_session,
        "objective": handoff.objective,
        "goal_contract_sha256": handoff.goal_contract_sha256,
        "scope_profile": handoff.scope_profile,
        "non_goals": handoff.non_goals,
        "success_conditions": handoff.success_conditions,
        "next_action": handoff.next_action,
        "hard_constraints": handoff.hard_constraints,
        "verified_facts": handoff.verified_facts,
        "failed_approaches": handoff.failed_approaches,
        "open_questions": handoff.open_questions,
        "authoritative_artifacts": [
            item.model_dump(mode="json")
            for item in handoff.authoritative_artifacts
        ],
        "workspace": current_workspace.model_dump(mode="json"),
        "project_policy_snapshot": current_policy.model_dump(mode="json"),
        "memory_mode": "off",
        "source_chat_included": False,
        "context_chars": context_chars,
        "context_sha256": context_sha256,
    }
    _write_text_atomic(context_path, context_text)
    _write_json_atomic(context_json_path, context_payload)
    artifacts = [
        (published_consumer_dir / context_path.name)
        .relative_to(run_dir)
        .as_posix(),
        (published_consumer_dir / context_json_path.name)
        .relative_to(run_dir)
        .as_posix(),
        (published_consumer_dir / metrics_path.name)
        .relative_to(run_dir)
        .as_posix(),
    ]
    ready_result = GoalHandoffCompileResult(
        status="ready",
        version=normalized_version,
        consumer_session_id=normalized_session,
        consumer_worker_epoch=normalized_epoch,
        max_chars=max_chars,
        context_chars=context_chars,
        context_sha256=context_sha256,
        handoff_sha256=handoff.handoff_sha256,
        artifact_paths=artifacts,
    )
    return _finalize_compile_result(
        run_dir=run_dir,
        staging_dir=consumer_dir,
        published_dir=published_consumer_dir,
        result=ready_result,
        handoff=handoff,
        workspace=current_workspace,
        policy=current_policy,
    )


def _load_goal_checkpoint(
    workspace: Path,
    run_id: str,
    checkpoint: str,
) -> tuple[Path, GoalContract, GoalCheckpointRecord, Path]:
    run_dir = resolve_run_dir(workspace, run_id)
    state_payload = _read_json_object(run_dir / "goal-state.json", "goal-state.json")
    mirror_payload = _read_json_object(run_dir / "state.json", "state.json")
    contract_payload = _read_json_object(
        run_dir / "goal-contract.json",
        "goal-contract.json",
    )
    if state_payload != mirror_payload:
        raise ValueError("goal-state.json 与 state.json 不一致")
    state = GoalState.model_validate(state_payload)
    contract = GoalContract.model_validate(contract_payload)
    if state.run_id != run_dir.name:
        raise ValueError("goal state.run_id 与目录身份不一致")
    contract_run_id = contract_payload.get("run_id")
    if contract_run_id is not None and str(contract_run_id) != run_dir.name:
        raise ValueError("goal contract.run_id 与目录身份不一致")
    if Path(state.repo_path).resolve() != Path(contract.repo_path).resolve():
        raise ValueError("goal state 与 contract repo_path 不一致")

    checkpoint_id = _normalize_checkpoint(checkpoint)
    record = next(
        (
            item
            for item in state.checkpoint_records
            if item.checkpoint == checkpoint_id
        ),
        None,
    )
    if record is None:
        raise ValueError(f"checkpoint 不存在：{checkpoint_id}")
    if record.status != "done":
        raise ValueError(f"checkpoint {checkpoint_id} 尚未完成")
    checkpoint_record_relative = (
        f"checkpoints/{checkpoint_id}/checkpoint-evidence.json"
    )
    checkpoint_record_path = _resolve_bound_file(
        run_dir,
        checkpoint_record_relative,
        f"checkpoint {checkpoint_id} evidence",
    )
    artifact_record = GoalCheckpointRecord.model_validate(
        _read_json_object(
            checkpoint_record_path,
            f"checkpoint {checkpoint_id} evidence",
        )
    )
    if artifact_record.model_dump(mode="json") != record.model_dump(mode="json"):
        raise ValueError(f"checkpoint {checkpoint_id} evidence 与 state 不一致")
    _resolve_bound_file(
        run_dir,
        f"checkpoints/{checkpoint_id}/checkpoint-report.md",
        f"checkpoint {checkpoint_id} report",
    )

    refreshed_refs = []
    for reference in record.refs:
        current = validate_goal_evidence(
            workspace,
            Path(state.repo_path),
            reference.run,
            reference.type,
            reference.note,
        )
        current.attached_at = reference.attached_at
        refreshed_refs.append(current)
    refreshed_record = record.model_copy(update={"refs": refreshed_refs})
    if not _checkpoint_completion_is_valid(refreshed_record):
        raise ValueError(f"checkpoint {checkpoint_id} 完成证据已失效")
    return run_dir, contract, refreshed_record, checkpoint_record_path


def _require_registered_handoff_version(
    run_dir: Path,
    checkpoint: str,
    version: str,
) -> None:
    state_payload = _read_json_object(
        run_dir / "goal-state.json",
        "goal-state.json",
    )
    mirror_payload = _read_json_object(
        run_dir / "state.json",
        "state.json",
    )
    if state_payload != mirror_payload:
        raise ValueError("goal-state.json 与 state.json 不一致")
    state = GoalState.model_validate(state_payload)
    if state.run_id != run_dir.name:
        raise ValueError("goal state.run_id 与目录身份不一致")

    prefix = f"checkpoints/{checkpoint}/handoffs/{version}/"
    required = {
        f"{prefix}checkpoint-handoff.json",
        f"{prefix}handoff-report.md",
    }
    missing = sorted(required.difference(state.artifacts))
    if missing:
        raise ValueError(
            "handoff version 尚未完整登记到 Goal state，不能编译 consumer；"
            "请先重新执行 goal handoff 完成 orphan adoption："
            + "，".join(missing)
        )


def _checkpoint_completion_is_valid(record: GoalCheckpointRecord) -> bool:
    if record.status != "done" or not record.refs:
        return False
    if record.completion_mode == "validated":
        return any(
            item.validated and item.completion_eligible
            for item in record.refs
        )
    if record.completion_mode == "manual_override":
        return any(
            item.validated and item.type == "manual"
            for item in record.refs
        )
    return False


def _bind_authoritative_artifacts(
    workspace: Path,
    run_dir: Path,
    repo: Path,
    checkpoint: str,
    record: GoalCheckpointRecord,
    requested: list[GoalHandoffArtifactInput],
) -> list[GoalHandoffArtifactBinding]:
    checkpoint_id = _normalize_checkpoint(checkpoint)
    candidates: list[tuple[GoalHandoffArtifactScope, str]] = [
        (
            "goal_run",
            f"checkpoints/{checkpoint_id}/checkpoint-evidence.json",
        ),
        (
            "goal_run",
            f"checkpoints/{checkpoint_id}/checkpoint-report.md",
        ),
        *_manual_evidence_artifacts(workspace, repo, record),
        *((item.scope, item.path) for item in requested),
    ]
    bindings: list[GoalHandoffArtifactBinding] = []
    seen: set[tuple[str, str]] = set()
    for scope, relative_path in candidates:
        key = (scope, relative_path)
        if key in seen:
            continue
        seen.add(key)
        root = _artifact_root(
            scope,
            workspace=workspace,
            run_dir=run_dir,
            repo=repo,
        )
        path = _resolve_bound_file(root, relative_path, scope)
        bindings.append(
            GoalHandoffArtifactBinding(
                scope=scope,
                path=relative_path,
                sha256=_sha256_file(path),
                size=path.stat().st_size,
            )
        )
    return bindings


def _manual_evidence_artifacts(
    workspace: Path,
    repo: Path,
    record: GoalCheckpointRecord,
) -> list[tuple[GoalHandoffArtifactScope, str]]:
    artifacts: list[tuple[GoalHandoffArtifactScope, str]] = []
    workspace_root = workspace.resolve(strict=True)
    repo_root = repo.resolve(strict=True)
    for reference in record.refs:
        if reference.type != "manual":
            continue
        try:
            path = Path(reference.run).resolve(strict=True)
        except OSError as exc:
            raise ValueError("manual checkpoint 证据无法解析，不能创建 handoff") from exc
        repo_relative = _relative_path_within(path, repo_root)
        if repo_relative is not None:
            artifacts.append(("repo", repo_relative))
            continue
        workspace_relative = _relative_path_within(path, workspace_root)
        if workspace_relative is not None:
            artifacts.append(("workspace", workspace_relative))
            continue
        raise ValueError("manual checkpoint 证据越过 repo/workspace 边界")
    return artifacts


def _relative_path_within(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _artifact_root(
    scope: GoalHandoffArtifactScope,
    *,
    workspace: Path,
    run_dir: Path,
    repo: Path,
) -> Path:
    if scope == "goal_run":
        return run_dir
    if scope == "repo":
        return repo
    if scope == "workspace":
        return workspace
    raise ValueError(f"未知 artifact scope：{scope}")


def _validate_handoff_identity(
    handoff: CheckpointHandoff,
    *,
    workspace: Path,
    run_dir: Path,
    checkpoint: str,
    version: str,
    consumer_session_id: str,
    consumer_worker_epoch: str,
    objective: str,
    repo: Path,
    current_contract: GoalContract | None,
    current_record: GoalCheckpointRecord | None,
    checkpoint_record_path: Path | None,
    current_workspace: GoalHandoffWorkspaceBinding | None,
    current_policy: GoalHandoffPolicySnapshot | None,
    issues: list[str],
) -> None:
    if handoff.goal_run_id != run_dir.name:
        issues.append("goal_run_identity_mismatch")
    if handoff.checkpoint != checkpoint:
        issues.append("checkpoint_identity_mismatch")
    if _format_version(handoff.handoff_version) != version:
        issues.append("handoff_version_mismatch")
    if handoff.source_session_id.casefold() == consumer_session_id.casefold():
        issues.append("source_consumer_session_must_differ")
    if (
        handoff.source_worker_epoch.casefold()
        == handoff.target_worker_epoch.casefold()
    ):
        issues.append("source_target_worker_epoch_must_differ")
    if handoff.target_worker_epoch != consumer_worker_epoch:
        issues.append("target_worker_epoch_mismatch")
    if handoff.memory_mode != "off":
        issues.append("memory_mode_must_be_off")
    if handoff.source_chat_included is not False:
        issues.append("source_chat_must_not_be_included")
    if current_contract is not None:
        if handoff.objective != current_contract.objective:
            issues.append("goal_objective_drift")
        if handoff.scope_profile != current_contract.scope_profile:
            issues.append("goal_scope_profile_drift")
        if handoff.non_goals != current_contract.non_goals:
            issues.append("goal_non_goals_drift")
        if handoff.success_conditions != current_contract.success_conditions:
            issues.append("goal_success_conditions_drift")
        if objective.strip() != current_contract.objective:
            issues.append("requested_objective_mismatch")
        if repo != Path(current_contract.repo_path).resolve():
            issues.append("repo_identity_mismatch")
        current_contract_sha256 = _sha256_json(
            current_contract.model_dump(mode="json")
        )
        if current_contract_sha256 != handoff.goal_contract_sha256:
            issues.append("goal_contract_drift")
    if current_record is not None:
        current_record_sha256 = _sha256_json(
            current_record.model_dump(mode="json")
        )
        if current_record_sha256 != handoff.checkpoint_record_sha256:
            issues.append("checkpoint_record_hash_mismatch")
    if checkpoint_record_path is not None and not checkpoint_record_path.is_file():
        issues.append("checkpoint_record_missing")
    if current_workspace is not None and current_workspace != handoff.workspace:
        issues.append("workspace_drift")
    if (
        current_policy is not None
        and current_policy != handoff.project_policy_snapshot
    ):
        issues.append("project_policy_drift")
    _validate_bound_artifacts(
        handoff.authoritative_artifacts,
        workspace=workspace,
        run_dir=run_dir,
        repo=repo,
        issues=issues,
    )


def _read_handoff(
    path: Path,
    issues: list[str],
) -> CheckpointHandoff | None:
    try:
        safe_path = _resolve_bound_file(
            path.parent,
            path.name,
            "checkpoint-handoff.json",
        )
        payload = _read_json_object(safe_path, "checkpoint-handoff.json")
        handoff = CheckpointHandoff.model_validate(payload)
    except (OSError, ValueError) as exc:
        issues.append(f"handoff_schema_invalid:{_safe_issue(exc)}")
        return None
    actual_hash = _handoff_payload_sha256(payload)
    if actual_hash != handoff.handoff_sha256:
        issues.append("handoff_self_hash_mismatch")
    return handoff


def _validate_bound_artifacts(
    artifacts: list[GoalHandoffArtifactBinding],
    *,
    workspace: Path,
    run_dir: Path,
    repo: Path,
    issues: list[str],
) -> None:
    for artifact in artifacts:
        root = _artifact_root(
            artifact.scope,
            workspace=workspace,
            run_dir=run_dir,
            repo=repo,
        )
        label = f"{artifact.scope}:{artifact.path}"
        try:
            path = _resolve_bound_file(root, artifact.path, artifact.scope)
        except (FileNotFoundError, ValueError) as exc:
            issues.append(f"artifact_missing_or_invalid:{label}:{_safe_issue(exc)}")
            continue
        if path.stat().st_size != artifact.size:
            issues.append(f"artifact_size_mismatch:{label}")
        if _sha256_file(path) != artifact.sha256:
            issues.append(f"artifact_hash_mismatch:{label}")


def _capture_workspace_binding(repo: Path) -> GoalHandoffWorkspaceBinding:
    snapshot = capture_review_workspace(repo)
    if snapshot.unsafe_index_paths:
        raise ValueError(
            "Git index 包含 skip-worktree、assume-unchanged 或 sparse 标记，"
            "禁止创建或编译 handoff"
        )
    if not snapshot.untracked_content_complete:
        raise ValueError(
            "未跟踪文件内容快照不完整，禁止创建或编译 handoff"
        )
    return _workspace_binding_from_snapshot(snapshot)


def _workspace_binding_from_snapshot(
    snapshot: ReviewWorkspaceSnapshot,
) -> GoalHandoffWorkspaceBinding:
    return GoalHandoffWorkspaceBinding(
        fingerprint=snapshot.fingerprint,
        head_sha=snapshot.head_sha,
        status_sha256=snapshot.status_sha256,
        full_diff_sha256=snapshot.full_diff_sha256,
        staged_diff_sha256=snapshot.staged_diff_sha256,
        unstaged_diff_sha256=snapshot.unstaged_diff_sha256,
        untracked_manifest_sha256=snapshot.untracked_manifest_sha256,
        ignored_manifest_sha256=snapshot.ignored_manifest_sha256,
        index_flags_sha256=snapshot.index_flags_sha256,
        untracked_content_complete=snapshot.untracked_content_complete,
        changed_files=list(snapshot.changed_files),
        untracked_files=list(snapshot.untracked_files),
        unsafe_index_paths=list(snapshot.unsafe_index_paths),
    )


def _capture_policy_snapshot(repo: Path) -> GoalHandoffPolicySnapshot:
    _require_existing_real_directory(repo, "repo")
    policy_relatives: set[str] = set()
    for name in _POLICY_ROOT_FILES:
        path = repo / name
        if os.path.lexists(path):
            policy_relatives.add(
                _normalize_relative_path(name, "policy.path")
            )

    for current_root, directory_names, filenames in os.walk(
        repo,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        retained_directories: list[str] = []
        for directory_name in directory_names:
            if directory_name in _POLICY_EXCLUDED_DIRS:
                continue
            directory = current / directory_name
            metadata = directory.lstat()
            if _is_link_or_reparse_stat(metadata):
                # policy 扫描绝不跟随目录 alias；显式 artifact 路径仍会单独 fail-closed。
                continue
            retained_directories.append(directory_name)
        directory_names[:] = retained_directories
        if "AGENTS.md" in filenames:
            relative = (current / "AGENTS.md").relative_to(repo).as_posix()
            policy_relatives.add(
                _normalize_relative_path(relative, "policy.path")
            )

    unique_relatives = sorted(policy_relatives)
    if len(unique_relatives) > _MAX_POLICY_FILES:
        raise ValueError(
            f"项目 policy 文件超过上限：{len(unique_relatives)} > {_MAX_POLICY_FILES}"
        )
    files: list[GoalHandoffPolicyFile] = []
    for relative in unique_relatives:
        path = _resolve_bound_file(repo, relative, "policy")
        files.append(
            GoalHandoffPolicyFile(
                path=relative,
                sha256=_sha256_file(path),
                size=path.stat().st_size,
            )
        )
    payload = [item.model_dump(mode="json") for item in files]
    return GoalHandoffPolicySnapshot(
        files=files,
        sha256=_sha256_json(payload),
    )


def _context_sections(
    handoff: CheckpointHandoff,
    *,
    consumer_session_id: str,
    consumer_worker_epoch: str,
    workspace: GoalHandoffWorkspaceBinding,
    policy: GoalHandoffPolicySnapshot,
) -> list[tuple[str, str]]:
    identity = "\n".join(
        [
            "# Checkpoint Handoff Context",
            "",
            "## Identity",
            "",
            f"- goal run：`{handoff.goal_run_id}`",
            f"- checkpoint：`{handoff.checkpoint}`",
            f"- handoff version：`v{handoff.handoff_version:04d}`",
            f"- handoff SHA-256：`{handoff.handoff_sha256}`",
            f"- source worker epoch：`{handoff.source_worker_epoch}`",
            f"- target worker epoch：`{handoff.target_worker_epoch}`",
            f"- source session：`{handoff.source_session_id}`",
            f"- consumer worker epoch：`{consumer_worker_epoch}`",
            f"- consumer session：`{consumer_session_id}`",
        ]
    )
    objective = "\n".join(
        [
            "## Objective And Next Action",
            "",
            handoff.objective,
            "",
            f"- 下一步：{handoff.next_action}",
        ]
    )
    contract = "\n".join(
        [
            "## Goal Contract Binding",
            "",
            f"- contract SHA-256：`{handoff.goal_contract_sha256}`",
            f"- scope profile：`{handoff.scope_profile or 'default'}`",
            "",
            "### Non-goals",
            "",
            *[f"- {item}" for item in handoff.non_goals or ["无。"]],
            "",
            "### Success Conditions",
            "",
            *[
                f"- {item}"
                for item in handoff.success_conditions or ["无。"]
            ],
        ]
    )
    constraints = _render_list_section(
        "Hard Constraints",
        handoff.hard_constraints,
    )
    facts = _render_list_section("Verified Facts", handoff.verified_facts)
    failed = _render_list_section(
        "Verified Failed Approaches",
        handoff.failed_approaches,
    )
    questions = _render_list_section("Open Questions", handoff.open_questions)
    evidence_lines = ["## Authoritative Artifact Refs", ""]
    evidence_lines.extend(
        (
            f"- `{item.scope}:{item.path}` "
            f"sha256=`{item.sha256}` size=`{item.size}`"
        )
        for item in handoff.authoritative_artifacts
    )
    evidence = "\n".join(evidence_lines)
    workspace_text = "\n".join(
        [
            "## Fresh Workspace Binding",
            "",
            f"- HEAD：`{workspace.head_sha}`",
            f"- fingerprint：`{workspace.fingerprint}`",
            f"- status SHA-256：`{workspace.status_sha256}`",
            f"- staged diff SHA-256：`{workspace.staged_diff_sha256}`",
            f"- unstaged diff SHA-256：`{workspace.unstaged_diff_sha256}`",
            (
                "- untracked manifest SHA-256："
                f"`{workspace.untracked_manifest_sha256}`"
            ),
            (
                "- ignored manifest SHA-256："
                f"`{workspace.ignored_manifest_sha256}`"
            ),
            (
                "- index flags SHA-256："
                f"`{workspace.index_flags_sha256}`"
            ),
            (
                "- untracked content complete："
                f"`{str(workspace.untracked_content_complete).lower()}`"
            ),
            "- unsafe index paths：`0`",
        ]
    )
    policy_text = "\n".join(
        [
            "## Fresh Project Policy Binding",
            "",
            f"- snapshot SHA-256：`{policy.sha256}`",
            *(
                f"- `{item.path}` sha256=`{item.sha256}` size=`{item.size}`"
                for item in policy.files
            ),
        ]
    )
    safety = "\n".join(
        [
            "## Safety Boundary",
            "",
            "- 必须从当前 workspace 重新读取代码事实，handoff 不能覆盖 Git 或验证结果。",
            "- `source_chat_included=false`，不得读取或依赖 source session 聊天。",
            "- `memory_mode=off`，不得读取或写入 accepted memory。",
            "- 不自动 commit、push、release。",
        ]
    )
    return [
        ("identity", identity),
        ("objective", objective),
        ("goal_contract", contract),
        ("constraints", constraints),
        ("verified_facts", facts),
        ("failed_approaches", failed),
        ("open_questions", questions),
        ("authoritative_artifacts", evidence),
        ("workspace_binding", workspace_text),
        ("policy_binding", policy_text),
        ("safety_boundary", safety),
    ]


def _build_split_plan(
    sections: list[tuple[str, str]],
    max_chars: int,
    context_chars: int,
) -> dict[str, Any]:
    section_items = [
        {
            "section": name,
            "chars": len(_render_context_text([(name, text)])),
        }
        for name, text in sections
    ]
    groups: list[dict[str, Any]] = []
    current_sections: list[tuple[str, str]] = []
    for section in sections:
        candidate_sections = [*current_sections, section]
        candidate_chars = len(_render_context_text(candidate_sections))
        if current_sections and candidate_chars > max_chars:
            current_chars = len(_render_context_text(current_sections))
            groups.append(
                {
                    "sections": [name for name, _ in current_sections],
                    "chars": current_chars,
                    "within_budget": current_chars <= max_chars,
                }
            )
            current_sections = [section]
        else:
            current_sections = candidate_sections
    if current_sections:
        current_chars = len(_render_context_text(current_sections))
        groups.append(
            {
                "sections": [name for name, _ in current_sections],
                "chars": current_chars,
                "within_budget": current_chars <= max_chars,
            }
        )
    return {
        "schema_version": 1,
        "status": "split_required",
        "max_chars": max_chars,
        "context_chars": context_chars,
        "over_by_chars": context_chars - max_chars,
        "sections": section_items,
        "recommended_checkpoint_groups": groups,
        "automatic_checkpoint_creation": False,
        "truncation_applied": False,
    }


def _render_context_text(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(text.rstrip() for _, text in sections).rstrip() + "\n"


def _write_blocked_result(
    *,
    run_dir: Path,
    consumer_dir: Path,
    artifact_dir: Path,
    version: str,
    consumer_session_id: str,
    consumer_worker_epoch: str,
    max_chars: int,
    handoff: CheckpointHandoff | None,
    issues: list[str],
) -> GoalHandoffCompileResult:
    normalized_issues = list(dict.fromkeys(issues))
    payload = {
        "schema_version": 1,
        "status": "blocked",
        "version": version,
        "consumer_session_id": consumer_session_id,
        "consumer_worker_epoch": consumer_worker_epoch,
        "max_chars": max_chars,
        "handoff_sha256": handoff.handoff_sha256 if handoff else None,
        "issues": normalized_issues,
    }
    json_path = consumer_dir / "handoff-blocked.json"
    report_path = consumer_dir / "handoff-blocked.md"
    _write_json_atomic(json_path, payload)
    _write_text_atomic(
        report_path,
        "\n".join(
            [
                "# Handoff Blocked",
                "",
                f"- version：`{version}`",
                f"- consumer session：`{consumer_session_id}`",
                f"- consumer worker epoch：`{consumer_worker_epoch}`",
                "",
                "## Issues",
                "",
                *(f"- `{item}`" for item in normalized_issues),
                "",
                "当前结果不是 ready，禁止启动 consumer worker。",
            ]
        ).rstrip()
        + "\n",
    )
    artifacts = [
        (artifact_dir / json_path.name).relative_to(run_dir).as_posix(),
        (artifact_dir / report_path.name).relative_to(run_dir).as_posix(),
    ]
    return GoalHandoffCompileResult(
        status="blocked",
        version=version,
        consumer_session_id=consumer_session_id,
        consumer_worker_epoch=consumer_worker_epoch,
        max_chars=max_chars,
        handoff_sha256=handoff.handoff_sha256 if handoff else None,
        issues=normalized_issues,
        artifact_paths=artifacts,
    )


def _render_handoff_report(
    handoff: CheckpointHandoff,
    handoff_path: str,
) -> str:
    return "\n".join(
        [
            "# Checkpoint Handoff",
            "",
            f"- artifact：`{handoff_path}`",
            f"- goal run：`{handoff.goal_run_id}`",
            f"- checkpoint：`{handoff.checkpoint}`",
            f"- version：`v{handoff.handoff_version:04d}`",
            f"- handoff SHA-256：`{handoff.handoff_sha256}`",
            f"- goal contract SHA-256：`{handoff.goal_contract_sha256}`",
            f"- source worker epoch：`{handoff.source_worker_epoch}`",
            f"- target worker epoch：`{handoff.target_worker_epoch}`",
            f"- source session：`{handoff.source_session_id}`",
            f"- workspace fingerprint：`{handoff.workspace.fingerprint}`",
            (
                "- policy snapshot SHA-256："
                f"`{handoff.project_policy_snapshot.sha256}`"
            ),
            f"- authoritative artifacts：`{len(handoff.authoritative_artifacts)}`",
            "- memory mode：`off`",
            "- source chat included：`false`",
        ]
    ).rstrip() + "\n"


def _render_split_plan(payload: dict[str, Any]) -> str:
    lines = [
        "# Checkpoint Split Plan",
        "",
        "- status：`split_required`",
        f"- max chars：`{payload['max_chars']}`",
        f"- context chars：`{payload['context_chars']}`",
        f"- over by chars：`{payload['over_by_chars']}`",
        "- truncation applied：`false`",
        "- automatic checkpoint creation：`false`",
        "",
        "## Recommended Groups",
        "",
    ]
    for index, group in enumerate(
        payload["recommended_checkpoint_groups"],
        start=1,
    ):
        sections = ", ".join(group["sections"])
        lines.append(
            f"- group {index:02d}：sections=`{sections}`，"
            f"chars=`{group['chars']}`，"
            f"within_budget=`{str(group['within_budget']).lower()}`"
        )
    lines.extend(
        [
            "",
            "本计划只提供确定性 section grouping，不会自动创建 checkpoint。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_list_section(title: str, items: list[str]) -> str:
    lines = [f"## {title}", ""]
    lines.extend(f"- {item}" for item in items or ["无。"])
    return "\n".join(lines)


def _prepare_version_dir(
    run_dir: Path,
    checkpoint: str,
    version: str,
) -> tuple[Path, Path]:
    checkpoint_dir = run_dir / "checkpoints" / _normalize_checkpoint(checkpoint)
    _require_existing_directory_within(run_dir, checkpoint_dir, "checkpoint")
    handoffs_dir = checkpoint_dir / "handoffs"
    if os.path.lexists(handoffs_dir):
        _require_existing_directory_within(run_dir, handoffs_dir, "handoffs")
    else:
        handoffs_dir.mkdir()
        _require_existing_directory_within(run_dir, handoffs_dir, "handoffs")
    version_dir = handoffs_dir / version
    staging_dir = Path(tempfile.mkdtemp(prefix=".h-", dir=handoffs_dir))
    _require_existing_directory_within(
        run_dir,
        staging_dir,
        "handoff staging",
    )
    return staging_dir, version_dir


def _prepare_consumer_dir(
    consumer_dir: Path,
    version_dir: Path,
    run_dir: Path,
) -> tuple[Path, Path]:
    _require_existing_directory_within(
        run_dir,
        version_dir,
        "handoff version",
    )
    consumers_dir = version_dir / "consumers"
    if os.path.lexists(consumers_dir):
        _require_existing_directory_within(
            run_dir,
            consumers_dir,
            "handoff consumers",
        )
    else:
        consumers_dir.mkdir()
        _require_existing_directory_within(
            run_dir,
            consumers_dir,
            "handoff consumers",
        )
    # staging 放在 version 根目录，避免 Windows 深路径下比最终 consumer 路径更长。
    staging_dir = Path(tempfile.mkdtemp(prefix=".c-", dir=version_dir))
    _require_existing_directory_within(
        run_dir,
        staging_dir,
        "handoff consumer staging",
    )
    return staging_dir, consumer_dir


def _existing_handoff_created_at(
    run_dir: Path,
    version_dir: Path,
) -> str | None:
    if not os.path.lexists(version_dir):
        return None
    _require_existing_directory_within(
        run_dir,
        version_dir,
        "handoff version",
    )
    issues: list[str] = []
    handoff = _read_handoff(
        version_dir / "checkpoint-handoff.json",
        issues,
    )
    try:
        _resolve_bound_file(
            version_dir,
            "handoff-report.md",
            "handoff report",
        )
    except (FileNotFoundError, ValueError) as exc:
        issues.append(f"handoff_report_invalid:{_safe_issue(exc)}")
    if handoff is None or issues:
        issue_text = "；".join(issues or ["handoff_invalid"])
        raise ValueError(
            "handoff version 已存在但不是可安全接管的完整发布结果："
            f"{version_dir.name}；{issue_text}"
        )
    return handoff.created_at


def _finalize_compile_result(
    *,
    run_dir: Path,
    staging_dir: Path,
    published_dir: Path,
    result: GoalHandoffCompileResult,
    handoff: CheckpointHandoff | None,
    workspace: GoalHandoffWorkspaceBinding | None,
    policy: GoalHandoffPolicySnapshot | None,
) -> GoalHandoffCompileResult:
    artifact_bindings: list[dict[str, Any]] = []
    for artifact_path in result.artifact_paths:
        published_path = run_dir.joinpath(
            *PurePosixPath(artifact_path).parts
        )
        if published_path.parent != published_dir:
            raise ValueError(
                "compile result artifact 不在当前 consumer 发布目录内"
            )
        staged_path = _resolve_bound_file(
            staging_dir,
            published_path.name,
            "handoff compile artifact",
        )
        artifact_bindings.append(
            {
                "path": artifact_path,
                "sha256": _sha256_file(staged_path),
                "size": staged_path.stat().st_size,
            }
        )

    manifest_path = staging_dir / "handoff-compile-result.json"
    manifest_payload = {
        "schema_version": 1,
        "result": result.model_dump(mode="json"),
        "handoff_sha256": handoff.handoff_sha256 if handoff else None,
        "workspace_fingerprint": workspace.fingerprint if workspace else None,
        "policy_sha256": policy.sha256 if policy else None,
        "artifact_bindings": artifact_bindings,
    }
    _write_json_atomic(manifest_path, manifest_payload)
    manifest_artifact = (
        published_dir / manifest_path.name
    ).relative_to(run_dir).as_posix()
    final_result = result.model_copy(
        update={
            "artifact_paths": [
                *result.artifact_paths,
                manifest_artifact,
            ]
        }
    )
    _publish_package_directory(
        staging_dir,
        published_dir,
        run_dir=run_dir,
        label=(
            "handoff consumer "
            f"{result.consumer_session_id}/{result.status}"
        ),
    )
    return final_result


def _publish_package_directory(
    staging_dir: Path,
    published_dir: Path,
    *,
    run_dir: Path,
    label: str,
) -> Literal["created", "adopted"]:
    _require_existing_directory_within(
        run_dir,
        staging_dir,
        f"{label} staging",
    )
    staging_snapshot = _snapshot_flat_package_directory(
        staging_dir,
        f"{label} staging",
    )

    if os.path.lexists(published_dir):
        _require_existing_directory_within(
            run_dir,
            published_dir,
            label,
        )
        if not _package_directory_matches_snapshot(
            published_dir,
            staging_snapshot,
            label,
        ):
            raise ValueError(
                f"{label} 已存在但内容不完整或与本次结果不一致，不能覆盖"
            )
        _discard_flat_staging_directory(staging_dir)
        return "adopted"

    try:
        os.rename(staging_dir, published_dir)
    except OSError as exc:
        if os.path.lexists(published_dir):
            _require_existing_directory_within(
                run_dir,
                published_dir,
                label,
            )
            if _package_directory_matches_snapshot(
                published_dir,
                staging_snapshot,
                label,
            ):
                _discard_flat_staging_directory(staging_dir)
                return "adopted"
        raise ValueError(f"{label} 原子发布失败，未覆盖既有结果") from exc

    _require_existing_directory_within(
        run_dir,
        published_dir,
        label,
    )
    published_snapshot = _snapshot_flat_package_directory(
        published_dir,
        label,
    )
    if published_snapshot != staging_snapshot:
        raise ValueError(
            f"{label} 发布后内容发生变化，拒绝登记不完整结果"
        )
    return "created"


def _validate_flat_package_directory(path: Path, label: str) -> list[str]:
    names: list[str] = []
    for child in path.iterdir():
        _require_regular_file(child, f"{label}:{child.name}")
        names.append(child.name)
    if not names:
        raise ValueError(f"{label} 不能为空")
    return sorted(names)


def _snapshot_flat_package_directory(
    path: Path,
    label: str,
) -> dict[str, tuple[int, str]]:
    names = _validate_flat_package_directory(path, label)
    snapshot: dict[str, tuple[int, str]] = {}
    for name in names:
        child = path / name
        before = child.stat()
        digest = _sha256_file(child)
        _require_regular_file(child, f"{label}:{name}")
        after = child.stat()
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        )
        if before_identity != after_identity:
            raise ValueError(f"{label}:{name} 在读取期间发生变化")
        snapshot[name] = (after.st_size, digest)
    return snapshot


def _package_directory_matches_snapshot(
    path: Path,
    expected: dict[str, tuple[int, str]],
    label: str,
) -> bool:
    try:
        actual = _snapshot_flat_package_directory(path, label)
    except (FileNotFoundError, OSError, ValueError):
        return False
    return actual == expected


def _discard_flat_staging_directory(staging_dir: Path) -> None:
    parent = staging_dir.parent.resolve(strict=True)
    resolved = staging_dir.resolve(strict=True)
    if resolved.parent != parent or not staging_dir.name.startswith("."):
        raise ValueError("拒绝清理非预期 staging 目录")
    _require_existing_real_directory(resolved, "staging")
    children = list(resolved.iterdir())
    for child in children:
        _require_regular_file(child, f"staging:{child.name}")
    for child in children:
        child.unlink()
    resolved.rmdir()


def _require_existing_directory_within(
    root: Path,
    path: Path,
    label: str,
) -> None:
    root_resolved = root.resolve(strict=True)
    _require_existing_real_directory(root_resolved, "goal run")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 目录越过 goal run 边界：{path}") from exc
    current = root_resolved
    for part in relative.parts:
        current = current / part
        _require_existing_real_directory(current, label)
        try:
            current.resolve(strict=True).relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"{label} 目录越过 goal run 边界：{path}"
            ) from exc


def _require_existing_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} 目录不存在：{path}") from exc
    if _is_link_or_reparse_stat(metadata):
        raise ValueError(f"{label} 目录不能是链接或 reparse point：{path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise FileNotFoundError(f"{label} 目录不存在：{path}")


def _resolve_bound_file(root: Path, relative: str, label: str) -> Path:
    normalized = _normalize_relative_path(relative, label)
    root_path = Path(root)
    _require_existing_real_directory(root_path, f"{label} root")
    root_resolved = root_path.resolve(strict=True)
    parts = PurePosixPath(normalized).parts
    current = root_path
    for part in parts[:-1]:
        current = current / part
        _require_existing_real_directory(current, label)
        try:
            current.resolve(strict=True).relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"{label} artifact 越过根目录：{normalized}"
            ) from exc

    candidate = current / parts[-1]
    try:
        # 必须先检查调用方提供的原始路径，不能在 resolve 后丢失 alias 身份。
        _require_regular_file(candidate, f"{label}:{normalized}")
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} artifact 不存在：{normalized}") from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} artifact 越过根目录：{normalized}") from exc
    _require_regular_file(resolved, f"{label}:{normalized}")
    return resolved


def _require_regular_file(path: Path, label: str) -> None:
    metadata = path.lstat()
    if _is_link_or_reparse_stat(metadata):
        raise ValueError(f"{label} 不能是链接或 reparse point")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} 必须是普通文件")
    if metadata.st_nlink != 1:
        raise ValueError(f"{label} 不能是 hardlink")


def _is_link_or_reparse_stat(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _normalize_relative_path(value: str, label: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or windows_path.drive
        or any(":" in part for part in path.parts)
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise ValueError(f"{label} 必须是安全的相对路径")
    lowered = normalized.casefold()
    if (
        re.search(r"(^|/)source-session-[^/]+\.chat$", lowered)
        or lowered.endswith("accepted-memory.json")
    ):
        raise ValueError(f"{label} 不能指向 source chat 或 accepted memory")
    reason = sensitive_path_reason(normalized)
    if reason is not None:
        raise ValueError(f"{label} 指向敏感路径：{reason}")
    return path.as_posix()


def _normalize_checkpoint(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("checkpoint 不能为空")
    if normalized.isdigit():
        normalized = f"{int(normalized):02d}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", normalized):
        raise ValueError("checkpoint identity 不合法")
    return normalized


def _normalize_identity(value: str, label: str) -> str:
    normalized = value.strip()
    if not _IDENTITY_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} 只能包含字母、数字、点、下划线和连字符")
    return normalized


def _format_version(version: int) -> str:
    if version < 1 or version > 9999:
        raise ValueError("handoff version 必须在 1..9999 之间")
    return f"v{version:04d}"


def _normalize_version(value: str) -> str:
    normalized = value.strip().lower()
    match = _VERSION_PATTERN.fullmatch(normalized)
    if match is None or int(match.group(1)) < 1:
        raise ValueError("handoff version 必须使用 vNNNN，且版本大于 0")
    return normalized


def _validate_safe_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} 不能为空")
    if len(normalized) > _MAX_TEXT_CHARS:
        raise ValueError(f"{label} 超过 {_MAX_TEXT_CHARS} 字符")
    if "\0" in normalized:
        raise ValueError(f"{label} 不能包含 NUL")
    if re.search(
        r"(?i)(private[_ -]?canary|source[_ -]?chat[_ -]?private|"
        r"accepted-memory\.json|source-session-[^/\s]+\.chat)",
        normalized,
    ):
        raise ValueError(f"{label} 不能包含 source chat 或 private canary")
    if redact_text(normalized) != normalized:
        raise ValueError(f"{label} 触发敏感信息检测")
    return normalized


def _validate_sha256(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} 必须是 64 位小写十六进制 SHA-256")
    return normalized


def _handoff_payload_sha256(payload: dict[str, Any]) -> str:
    return _sha256_json(
        {
            key: value
            for key, value in payload.items()
            if key != "handoff_sha256"
        }
    )


def _sha256_json(payload: Any) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{label} 无法读取") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 顶层必须是 JSON object")
    return payload


def _write_json_atomic(path: Path, payload: Any) -> None:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _write_text_atomic(path, content)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".v-",
        dir=path.parent,
    )
    try:
        with os.fdopen(
            descriptor,
            mode="w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(content)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _safe_issue(exc: BaseException) -> str:
    return redact_text(str(exc)).replace("\r", " ").replace("\n", " ")[:500]


__all__ = [
    "DEFAULT_CONTEXT_MAX_CHARS",
    "GoalHandoffArtifactInput",
    "GoalHandoffCompileResult",
    "GoalHandoffInput",
    "GoalHandoffWriteResult",
    "compile_goal_handoff_context",
    "create_goal_handoff",
]
