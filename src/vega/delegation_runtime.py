from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .delegation import (
    DELEGATION_READINESS_ARTIFACT,
    ArtifactReference,
    BudgetEligibilityLimits,
    DelegationReadinessResult,
    DelegationValidationContext,
    DelegationSnapshot,
    GitObjectId,
    PlanContract,
    PlanId,
    RouteEligibility,
    Sha256,
    SliceId,
    TaskId,
    TaskSlice,
    evaluate_delegation_payload,
)
from .execution_control import (
    ExecutionLease,
    RunnerExecutionContext,
)
from .redaction import redact_value
from .project_config import (
    CONFIG_FILENAMES,
    current_verification_shell_kind,
    load_project_config,
    project_policy_snapshot,
    scope_policy_sha256,
)
from .run_utils import resolve_runs_root
from .runner import Runner, RunnerResult
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


DELEGATION_PLAN_ARTIFACT = "delegation-plan.json"
DELEGATION_CONTEXT_ARTIFACT = "delegation-context.json"
DELEGATION_PROMPT_ARTIFACT = "delegation-prompt.json"
DELEGATION_ATTEMPT_ARTIFACT = "delegation-attempt.json"
DELEGATION_SNAPSHOT_BEFORE_ARTIFACT = "workspace-snapshot-before.json"
DELEGATION_SNAPSHOT_AFTER_ARTIFACT = "workspace-snapshot-after.json"
DELEGATION_SCOPE_ARTIFACT = "scope-gate.json"
DELEGATION_VERIFICATION_ARTIFACT = "verification.json"
MAX_DELEGATION_RUNTIME_ARTIFACT_BYTES = 1024 * 1024

BridgeStatus = Literal["blocked", "attempt_recorded"]
AttemptValidationStatus = Literal["valid", "human_required"]
WorkerTier = Literal["budget", "premium"]

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_REFERENCE_FIELDS = (
    "plan_ref",
    "context_ref",
    "readiness_ref",
    "prompt_ref",
    "execution_ref",
    "snapshot_before_ref",
    "snapshot_after_ref",
    "scope_ref",
    "verification_ref",
)
_EXPECTED_REFERENCE_NAMES = {
    "plan_ref": DELEGATION_PLAN_ARTIFACT,
    "context_ref": DELEGATION_CONTEXT_ARTIFACT,
    "readiness_ref": DELEGATION_READINESS_ARTIFACT,
    "prompt_ref": DELEGATION_PROMPT_ARTIFACT,
    "execution_ref": "execution.json",
    "snapshot_before_ref": DELEGATION_SNAPSHOT_BEFORE_ARTIFACT,
    "snapshot_after_ref": DELEGATION_SNAPSHOT_AFTER_ARTIFACT,
    "scope_ref": DELEGATION_SCOPE_ARTIFACT,
    "verification_ref": DELEGATION_VERIFICATION_ARTIFACT,
}
_ZERO_HASH = "0" * 64
_GLOB_CHARACTERS = frozenset("*?[]")


class DelegationContextCompilationError(ValueError):
    """权威 Context 无法无歧义编译时携带稳定 issue code。"""

    def __init__(self, *issue_codes: str) -> None:
        self.issue_codes = _sorted_unique(list(issue_codes))
        super().__init__(", ".join(self.issue_codes))


class DelegationContextSource(BaseModel):
    """只声明 run-owned 来源位置，不接受调用方自报事实或内容哈希。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    task_artifact_path: str
    delegation_policy_path: str
    input_artifact_paths: list[str] = Field(max_length=512)

    @field_validator(
        "task_artifact_path",
        "delegation_policy_path",
    )
    @classmethod
    def validate_single_path(cls, value: str) -> str:
        return _validate_source_relative_path(value)

    @field_validator("input_artifact_paths")
    @classmethod
    def validate_input_paths(cls, values: list[str]) -> list[str]:
        normalized = [_validate_source_relative_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("input_artifact_paths 不能重复")
        return normalized

    def all_relative_paths(self) -> tuple[str, ...]:
        values = (
            self.task_artifact_path,
            self.delegation_policy_path,
            *self.input_artifact_paths,
        )
        if len(set(values)) != len(values):
            raise ValueError("Context source 路径不能相互重叠")
        return values


class _DelegationTaskArtifact(BaseModel):
    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    task_id: TaskId
    summary: str = Field(min_length=1, max_length=10_000)


class _DelegationPolicyArtifact(BaseModel):
    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    budget_limits: BudgetEligibilityLimits


class CompiledDelegationContext(BaseModel):
    """由实时 workspace、项目配置和 run-owned sources 编译的权威 Context。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    validation_context: DelegationValidationContext
    verification_shell_kind: Literal["cmd", "posix-sh"]
    project_config_ref: ArtifactReference
    task_artifact_ref: ArtifactReference
    delegation_policy_ref: ArtifactReference
    input_artifact_refs: list[ArtifactReference] = Field(max_length=512)


class _DelegationPromptArtifact(BaseModel):
    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    plan_id: PlanId
    slice_id: SliceId
    compiler: Literal["plan-contract-v1"]
    prompt_sha256: Sha256


class ArtifactProducer(Protocol):
    """MA-2A 只接受显式注入、把结果写到指定路径的确定性探针。"""

    def __call__(
        self,
        *args: Any,
        artifact_path: Path,
        **kwargs: Any,
    ) -> Path:
        ...


class DelegationAttempt(BaseModel):
    """一次单 slice 尝试的最小引用清单，不复制进程或验证事实。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    plan_id: PlanId
    slice_id: SliceId
    worker_tier: WorkerTier
    plan_ref: ArtifactReference
    context_ref: ArtifactReference
    readiness_ref: ArtifactReference
    prompt_ref: ArtifactReference
    execution_ref: ArtifactReference
    snapshot_before_ref: ArtifactReference
    snapshot_after_ref: ArtifactReference
    scope_ref: ArtifactReference
    verification_ref: ArtifactReference


class _WorkspaceSnapshotArtifact(BaseModel):
    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    fingerprint: Sha256
    head_sha: GitObjectId
    status_sha256: Sha256
    full_diff_sha256: Sha256
    staged_diff_sha256: Sha256
    unstaged_diff_sha256: Sha256
    untracked_manifest_sha256: Sha256
    ignored_manifest_sha256: Sha256
    index_flags_sha256: Sha256
    tracked_files_manifest_sha256: Sha256
    tracked_file_count: int = Field(ge=0, le=10_000_000)
    changed_files: list[str] = Field(max_length=10000)
    untracked_files: list[str] = Field(max_length=10000)
    unsafe_index_paths: list[str] = Field(max_length=10000)
    untracked_content_complete: bool


@dataclass(frozen=True)
class _RuntimeWorkspaceSnapshot:
    review: ReviewWorkspaceSnapshot
    tracked_files: frozenset[str]
    tracked_files_manifest_sha256: str


@dataclass(frozen=True)
class DelegationRuntimeOutcome:
    status: BridgeStatus
    readiness_status: RouteEligibility
    issue_codes: list[str] = field(default_factory=list)
    plan_path: Path | None = None
    readiness_path: Path | None = None
    attempt_path: Path | None = None
    attempt_sha256: str | None = None
    execution_sha256: str | None = None
    run_dir: Path | None = None

    @property
    def delegation_summary(self) -> dict[str, str]:
        if (
            self.status != "attempt_recorded"
            or self.attempt_sha256 is None
            or self.execution_sha256 is None
            or self.attempt_path is None
            or self.run_dir is None
        ):
            return {}
        validation = validate_delegation_attempt(
            self.attempt_path,
            run_dir=self.run_dir,
        )
        if validation.status != "valid":
            return {}
        try:
            current_attempt_sha256 = _sha256_file(self.attempt_path)
            attempt = DelegationAttempt.model_validate_json(
                _read_bounded_file(self.attempt_path)
            )
            execution_path = _reference_path(
                self.run_dir,
                attempt.execution_ref,
                label="execution_ref",
            )
            current_execution_sha256 = _sha256_file(execution_path)
        except (OSError, ValidationError, ValueError):
            return {}
        if (
            current_attempt_sha256 != self.attempt_sha256
            or current_execution_sha256 != self.execution_sha256
        ):
            return {}
        return {
            "plan_id": attempt.plan_id,
            "slice_id": attempt.slice_id,
            "readiness_status": self.readiness_status,
            "attempt_sha256": self.attempt_sha256,
            "execution_sha256": self.execution_sha256,
        }


class DelegationAttemptValidationResult(BaseModel):
    model_config = _STRICT_MODEL

    status: AttemptValidationStatus
    issue_codes: list[str] = Field(max_length=256)


@dataclass(frozen=True)
class _RuntimePaths:
    plan: Path
    context: Path
    readiness: Path
    prompt: Path
    execution: Path
    snapshot_before: Path
    snapshot_after: Path
    scope: Path
    verification: Path
    attempt: Path

    def generated_artifacts(self) -> tuple[Path, ...]:
        return (
            self.plan,
            self.context,
            self.readiness,
            self.prompt,
            self.snapshot_before,
            self.snapshot_after,
            self.scope,
            self.verification,
            self.attempt,
        )


def compile_delegation_context(
    *,
    run_dir: Path,
    repo_path: Path,
    source: DelegationContextSource,
) -> CompiledDelegationContext:
    """从实时事实编译 Context；调用方只能选择来源位置，不能注入权威内容。"""

    compiled, _ = _compile_delegation_context_with_snapshot(
        run_dir=run_dir,
        repo_path=repo_path,
        source=source,
    )
    return compiled


def _compile_delegation_context_with_snapshot(
    *,
    run_dir: Path,
    repo_path: Path,
    source: DelegationContextSource,
) -> tuple[CompiledDelegationContext, _RuntimeWorkspaceSnapshot]:
    try:
        run_root = _resolve_existing_run_dir(Path(run_dir))
        repo = _resolve_path(Path(repo_path))
    except ValueError as exc:
        raise DelegationContextCompilationError("context_boundary_invalid") from exc
    if _paths_overlap(run_root, repo):
        raise DelegationContextCompilationError("run_dir_overlaps_repo")

    try:
        source_paths = _resolve_context_source_paths(run_root, source)
    except (OSError, ValueError) as exc:
        raise DelegationContextCompilationError("context_source_path_invalid") from exc

    try:
        snapshot = _capture_runtime_workspace(repo)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise DelegationContextCompilationError("snapshot_before_capture_failed") from exc
    snapshot_issues = _snapshot_before_issues(snapshot.review)
    if snapshot_issues:
        raise DelegationContextCompilationError(*snapshot_issues)

    try:
        policy_snapshot = project_policy_snapshot(repo)
    except OSError as exc:
        raise DelegationContextCompilationError(
            "project_policy_snapshot_unreadable"
        ) from exc
    policy_path_value = policy_snapshot.get("path")
    policy_sha256 = policy_snapshot.get("sha256")
    if not policy_path_value or not policy_sha256:
        raise DelegationContextCompilationError("project_policy_missing")
    project_config_path = repo / policy_path_value
    try:
        _require_tracked_regular_file(
            repo,
            project_config_path,
            snapshot.review.head_sha,
        )
        project_config_ref = ArtifactReference(
            relative_path=policy_path_value,
            sha256=_sha256_file(project_config_path),
        )
        config = load_project_config(
            repo,
            tracked_only=True,
            tracked_revision=snapshot.review.head_sha,
        )
    except (
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        raise DelegationContextCompilationError("project_policy_invalid") from exc

    scope_issues = _compiled_scope_issues(config.scope.allowed_paths, config.scope.forbidden_paths)
    if scope_issues:
        raise DelegationContextCompilationError(*scope_issues)
    commands = list(config.verification.commands or [])
    if not commands:
        raise DelegationContextCompilationError("verification_commands_missing")
    if len(commands) > config.verification.max_commands:
        raise DelegationContextCompilationError("verification_commands_exceed_policy")

    try:
        task_raw = _read_bounded_file(source_paths["task"])
    except OSError as exc:
        raise DelegationContextCompilationError("task_artifact_unreadable") from exc
    try:
        task = _DelegationTaskArtifact.model_validate_json(task_raw)
    except (ValidationError, ValueError) as exc:
        raise DelegationContextCompilationError("task_artifact_invalid") from exc

    try:
        delegation_policy_raw = _read_bounded_file(source_paths["policy"])
    except OSError as exc:
        raise DelegationContextCompilationError(
            "delegation_policy_artifact_unreadable"
        ) from exc
    try:
        delegation_policy = _DelegationPolicyArtifact.model_validate_json(
            delegation_policy_raw
        )
    except (ValidationError, ValueError) as exc:
        raise DelegationContextCompilationError(
            "delegation_policy_artifact_invalid"
        ) from exc

    input_raws: list[tuple[Path, bytes]] = []
    for path in source_paths["inputs"]:
        try:
            input_raws.append((path, _read_bounded_file(path)))
        except OSError as exc:
            raise DelegationContextCompilationError(
                "input_artifact_unreadable"
            ) from exc
    try:
        task_ref = _artifact_reference_from_raw(
            run_root,
            source_paths["task"],
            task_raw,
        )
        delegation_policy_ref = _artifact_reference_from_raw(
            run_root,
            source_paths["policy"],
            delegation_policy_raw,
        )
        input_refs: list[ArtifactReference] = []
        for path, raw in input_raws:
            input_refs.append(_artifact_reference_from_raw(run_root, path, raw))
    except (ValidationError, ValueError) as exc:
        raise DelegationContextCompilationError(
            "context_source_reference_invalid"
        ) from exc

    combined_policy_sha256 = _sha256_json(
        {
            "project_config_ref": project_config_ref.model_dump(mode="json"),
            "delegation_policy_ref": delegation_policy_ref.model_dump(mode="json"),
        }
    )
    try:
        validation_context = DelegationValidationContext(
            schema_version=1,
            task_id=task.task_id,
            task_ref=task_ref,
            baseline=DelegationSnapshot(
                head_sha=snapshot.review.head_sha,
                workspace_fingerprint=snapshot.review.fingerprint,
                project_policy_sha256=combined_policy_sha256,
                scope_policy_sha256=scope_policy_sha256(config.scope),
            ),
            allowed_read_paths=list(config.scope.allowed_paths),
            allowed_write_paths=list(config.scope.allowed_paths),
            allowed_verification_commands=commands,
            available_artifacts=input_refs,
            budget_limits=_effective_budget_limits(
                delegation_policy.budget_limits,
                max_changed_files=config.budget.max_changed_files,
                max_diff_lines=config.budget.max_diff_lines,
                max_new_files=config.budget.max_new_files,
            ),
        )
        compiled = CompiledDelegationContext(
            schema_version=1,
            validation_context=validation_context,
            verification_shell_kind=current_verification_shell_kind(),
            project_config_ref=project_config_ref,
            task_artifact_ref=task_ref,
            delegation_policy_ref=delegation_policy_ref,
            input_artifact_refs=input_refs,
        )
    except (ValidationError, ValueError) as exc:
        raise DelegationContextCompilationError("compiled_context_invalid") from exc
    return compiled, snapshot


class DelegationRuntimeBridge:
    """默认不接入产品路径的单 slice 委派桥接。"""

    def __init__(
        self,
        *,
        run_dir: Path,
        repo_path: Path,
        worker_runner: Runner,
        worker_tier: str,
        context_source: DelegationContextSource,
        scope_gate: ArtifactProducer,
        verification_runner: ArtifactProducer,
        artifact_dir: Path | None = None,
    ) -> None:
        self.run_dir = _prepare_run_dir(Path(run_dir))
        raw_artifact_dir = (
            Path(artifact_dir)
            if artifact_dir is not None
            else self.run_dir / "delegation"
        )
        if artifact_dir is not None and _contains_parent_segment(raw_artifact_dir):
            raise ValueError("artifact_dir 必须是无路径穿越的 run-owned 路径")
        if not raw_artifact_dir.is_absolute():
            raw_artifact_dir = self.run_dir / raw_artifact_dir
        self.artifact_dir = _require_run_owned_path(
            self.run_dir,
            raw_artifact_dir,
            label="artifact_dir",
        )
        self.repo_path = _resolve_path(Path(repo_path))
        self.worker_runner = worker_runner
        self.worker_tier = worker_tier
        source_payload = (
            context_source.model_dump(mode="json")
            if isinstance(context_source, DelegationContextSource)
            else context_source
        )
        self.context_source = DelegationContextSource.model_validate(source_payload)
        self.scope_gate = scope_gate
        self.verification_runner = verification_runner

    def run(
        self,
        *,
        plan: PlanContract | dict[str, Any],
        slice_id: str,
    ) -> DelegationRuntimeOutcome:
        if _paths_overlap(self.run_dir, self.repo_path):
            return self._blocked(
                readiness_status="human_required",
                issue_codes=["run_dir_overlaps_repo"],
            )
        paths = self._runtime_paths()
        preexisting = [
            path for path in (*paths.generated_artifacts(), paths.execution) if path.exists()
        ]
        if preexisting:
            return self._blocked(
                readiness_status="human_required",
                issue_codes=[
                    f"artifact_already_exists:{path.name}" for path in preexisting
                ],
            )

        plan_payload = (
            plan.model_dump(mode="json")
            if isinstance(plan, PlanContract)
            else plan
        )
        try:
            compiled_context, snapshot_before = _compile_delegation_context_with_snapshot(
                run_dir=self.run_dir,
                repo_path=self.repo_path,
                source=self.context_source,
            )
        except DelegationContextCompilationError as exc:
            return self._blocked(
                readiness_status="human_required",
                issue_codes=exc.issue_codes,
            )
        readiness = evaluate_delegation_payload(
            plan,
            expected=compiled_context.validation_context,
        )
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            paths = self._runtime_paths()
            _write_json_artifact(paths.plan, plan_payload)
            _write_json_artifact(
                paths.context,
                compiled_context.model_dump(mode="json"),
            )
            _write_json_artifact(
                paths.readiness,
                readiness.model_dump(mode="json"),
            )
            _write_json_artifact(
                paths.snapshot_before,
                _snapshot_payload(snapshot_before),
            )
        except (OSError, TypeError, ValueError):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["delegation_artifact_write_failed"],
                plan_path=paths.plan if paths.plan.exists() else None,
                readiness_path=paths.readiness if paths.readiness.exists() else None,
            )

        runtime_issues = self._pre_worker_issues(
            readiness=readiness,
            plan=plan_payload,
            slice_id=slice_id,
        )
        if runtime_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=runtime_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        artifact_binding_issues = _pre_worker_artifact_issues(
            paths=paths,
            readiness=readiness,
            compiled_context=compiled_context,
        )
        if artifact_binding_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=artifact_binding_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        validated_plan = PlanContract.model_validate(plan_payload)
        task_slice = validated_plan.task_dag[0]
        worker_prompt = _compile_worker_prompt(validated_plan, task_slice)
        prompt_artifact = _DelegationPromptArtifact(
            schema_version=1,
            plan_id=validated_plan.plan_id,
            slice_id=task_slice.slice_id,
            compiler="plan-contract-v1",
            prompt_sha256=hashlib.sha256(worker_prompt.encode("utf-8")).hexdigest(),
        )
        try:
            _write_json_artifact(
                paths.prompt,
                prompt_artifact.model_dump(mode="json"),
            )
            control_hashes = _capture_control_artifact_hashes(
                _control_artifact_paths(
                    run_dir=self.run_dir,
                    repo_path=self.repo_path,
                    source=self.context_source,
                    compiled=compiled_context,
                    paths=paths,
                )
            )
        except (OSError, TypeError, ValueError):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["control_artifact_freeze_failed"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        source_issues = _compiled_context_source_issues(
            compiled_context,
            run_root=self.run_dir,
        ) + _compiled_project_config_issues(
            compiled_context,
            repo_path=self.repo_path,
        )
        if source_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=source_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        pre_worker_issues = _control_artifact_issues(control_hashes)
        try:
            current_before_worker = _capture_runtime_workspace(self.repo_path)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            pre_worker_issues.append("workspace_recheck_before_worker_failed")
        else:
            pre_worker_issues.extend(
                _runtime_workspace_identity_issues(
                    snapshot_before,
                    current_before_worker,
                    issue_code="workspace_changed_before_worker",
                )
            )
        if pre_worker_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=pre_worker_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        execution_context = RunnerExecutionContext(
            execution_dir=paths.execution.parent,
            run_id=self.run_dir.name,
            step="worker",
            iteration=1,
        )
        worker_result: RunnerResult | None = None
        try:
            worker_result = self.worker_runner.run(
                worker_prompt,
                self.repo_path,
                sandbox="workspace-write",
                timeout_seconds=validated_plan.budget.worker_time_limit_seconds,
                execution_context=execution_context,
            )
        except Exception:  # noqa: BLE001 - 注入式 worker 异常也必须保留现场并停止
            worker_issue = "worker_invocation_failed"
        else:
            worker_issue = None

        try:
            snapshot_after = _capture_runtime_workspace(self.repo_path)
            _write_json_artifact(
                paths.snapshot_after,
                _snapshot_payload(snapshot_after),
            )
        except Exception:  # noqa: BLE001 - 缺少执行后快照时不能形成可信 attempt
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=_sorted_unique(
                    [issue for issue in [worker_issue, "snapshot_after_capture_failed"] if issue]
                ),
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        control_issues = _control_artifact_issues(control_hashes)
        execution_issues = _execution_artifact_issues(
            paths.execution,
            run_id=self.run_dir.name,
        )
        worker_result_issue = (
            f"worker_status:{worker_result.status}"
            if worker_result is not None and worker_result.status != "success"
            else None
        )
        snapshot_after_issues = _snapshot_after_issues(
            snapshot_before,
            snapshot_after,
            task_slice=task_slice,
            plan=validated_plan,
        )
        if (
            worker_issue
            or worker_result_issue
            or control_issues
            or execution_issues
            or snapshot_after_issues
        ):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=_sorted_unique(
                    [
                        issue
                        for issue in [worker_issue, worker_result_issue]
                        if issue
                    ]
                    + control_issues
                    + execution_issues
                    + snapshot_after_issues
                ),
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        try:
            post_worker_hashes = dict(control_hashes)
            post_worker_hashes.update(
                _capture_control_artifact_hashes(
                    (
                        paths.execution,
                        paths.snapshot_after,
                    )
                )
            )
        except (OSError, ValueError):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["post_worker_artifact_freeze_failed"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        try:
            scope_status = self._run_scope_probe(
                validated_plan,
                compiled_context,
                task_slice,
                snapshot_before,
                snapshot_after,
                paths.scope,
            )
        except (OSError, TypeError, ValueError):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["scope_probe_failed"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        if scope_status not in {"passed", "success"}:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=[f"scope_status:{scope_status}"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        probe_control_issues = _control_artifact_issues(post_worker_hashes)
        try:
            workspace_after_scope = _capture_runtime_workspace(self.repo_path)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            probe_control_issues.append("workspace_recheck_after_scope_failed")
        else:
            probe_control_issues.extend(
                _runtime_workspace_identity_issues(
                    snapshot_after,
                    workspace_after_scope,
                    issue_code="workspace_changed_during_scope_probe",
                )
            )
        if probe_control_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=probe_control_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        try:
            scope_hash = _sha256_file(paths.scope)
        except OSError:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["scope_artifact_unreadable"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        try:
            verification_status = self._run_verification_probe(
                validated_plan,
                compiled_context,
                task_slice,
                snapshot_before,
                snapshot_after,
                paths.verification,
            )
        except (OSError, TypeError, ValueError):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["verification_probe_failed"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        if verification_status not in {"passed", "success"}:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=[f"verification_status:{verification_status}"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        final_control_issues = _control_artifact_issues(post_worker_hashes)
        try:
            if _sha256_file(paths.scope) != scope_hash:
                final_control_issues.append("control_artifact_changed:scope-gate.json")
        except OSError:
            final_control_issues.append("control_artifact_missing:scope-gate.json")
        try:
            workspace_after_verification = _capture_runtime_workspace(self.repo_path)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            final_control_issues.append("workspace_recheck_after_verification_failed")
        else:
            final_control_issues.extend(
                _runtime_workspace_identity_issues(
                    snapshot_after,
                    workspace_after_verification,
                    issue_code="workspace_changed_during_verification_probe",
                )
            )
        if final_control_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=final_control_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        attempt = DelegationAttempt(
            schema_version=1,
            plan_id=validated_plan.plan_id,
            slice_id=task_slice.slice_id,
            worker_tier="budget",
            plan_ref=_artifact_reference(self.run_dir, paths.plan),
            context_ref=_artifact_reference(self.run_dir, paths.context),
            readiness_ref=_artifact_reference(self.run_dir, paths.readiness),
            prompt_ref=_artifact_reference(self.run_dir, paths.prompt),
            execution_ref=_artifact_reference(self.run_dir, paths.execution),
            snapshot_before_ref=_artifact_reference(
                self.run_dir,
                paths.snapshot_before,
            ),
            snapshot_after_ref=_artifact_reference(
                self.run_dir,
                paths.snapshot_after,
            ),
            scope_ref=_artifact_reference(self.run_dir, paths.scope),
            verification_ref=_artifact_reference(
                self.run_dir,
                paths.verification,
            ),
        )
        try:
            _write_json_artifact(paths.attempt, attempt.model_dump(mode="json"))
        except (OSError, TypeError, ValueError):
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["delegation_attempt_write_failed"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        validation = validate_delegation_attempt(
            paths.attempt,
            run_dir=self.run_dir,
        )
        if validation.status != "valid":
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=validation.issue_codes,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
                attempt_path=paths.attempt,
            )

        return DelegationRuntimeOutcome(
            status="attempt_recorded",
            readiness_status=readiness.status,
            issue_codes=[],
            plan_path=paths.plan,
            readiness_path=paths.readiness,
            attempt_path=paths.attempt,
            attempt_sha256=_sha256_file(paths.attempt),
            execution_sha256=_sha256_file(paths.execution),
            run_dir=self.run_dir,
        )

    def _runtime_paths(self) -> _RuntimePaths:
        artifact_dir = _require_run_owned_path(
            self.run_dir,
            self.artifact_dir,
            label="artifact_dir",
        )
        execution = _require_run_owned_path(
            self.run_dir,
            self.run_dir / "executions" / "worker" / "execution.json",
            label="execution artifact",
        )
        paths = _RuntimePaths(
            plan=artifact_dir / DELEGATION_PLAN_ARTIFACT,
            context=artifact_dir / DELEGATION_CONTEXT_ARTIFACT,
            readiness=artifact_dir / DELEGATION_READINESS_ARTIFACT,
            prompt=artifact_dir / DELEGATION_PROMPT_ARTIFACT,
            execution=execution,
            snapshot_before=artifact_dir / DELEGATION_SNAPSHOT_BEFORE_ARTIFACT,
            snapshot_after=artifact_dir / DELEGATION_SNAPSHOT_AFTER_ARTIFACT,
            scope=artifact_dir / DELEGATION_SCOPE_ARTIFACT,
            verification=artifact_dir / DELEGATION_VERIFICATION_ARTIFACT,
            attempt=artifact_dir / DELEGATION_ATTEMPT_ARTIFACT,
        )
        for path in paths.generated_artifacts():
            _require_run_owned_path(
                self.run_dir,
                path,
                label=f"artifact {path.name}",
            )
        return paths

    def _pre_worker_issues(
        self,
        *,
        readiness: DelegationReadinessResult,
        plan: object,
        slice_id: str,
    ) -> list[str]:
        issues = list(readiness.issue_codes)
        if readiness.status != "budget_eligible":
            issues.append(f"readiness_status:{readiness.status}")
        if self.worker_tier not in {"budget", "premium"}:
            issues.append("worker_tier_unknown")
        elif self.worker_tier != "budget":
            issues.append("worker_tier_mismatch")
        if not readiness.contract_valid:
            return _sorted_unique(issues)
        try:
            validated_plan = PlanContract.model_validate(plan)
        except (ValidationError, TypeError, ValueError):
            issues.append("contract_schema_invalid")
            return _sorted_unique(issues)
        if len(validated_plan.task_dag) != 1:
            issues.append("single_slice_required")
        selected = [
            item for item in validated_plan.task_dag if item.slice_id == slice_id
        ]
        if len(selected) != 1:
            issues.append("slice_not_found")
        elif len(validated_plan.task_dag) == 1 and selected[0] != validated_plan.task_dag[0]:
            issues.append("slice_selection_mismatch")
        return _sorted_unique(issues)

    def _run_scope_probe(
        self,
        plan: PlanContract,
        compiled_context: CompiledDelegationContext,
        task_slice: TaskSlice,
        snapshot_before: _RuntimeWorkspaceSnapshot,
        snapshot_after: _RuntimeWorkspaceSnapshot,
        artifact_path: Path,
    ) -> str:
        expected_artifact = _scope_probe_expected(
            plan,
            compiled_context,
            task_slice,
            snapshot_before.review,
            snapshot_after.review,
        )
        result_path = self.scope_gate(
            repo_path=self.repo_path,
            plan=plan.model_copy(deep=True),
            compiled_context=compiled_context.model_copy(deep=True),
            task_slice=task_slice.model_copy(deep=True),
            snapshot_before=snapshot_before.review,
            snapshot_after=snapshot_after.review,
            expected_artifact=expected_artifact,
            artifact_path=artifact_path,
        )
        return _validate_probe_artifact(
            self.run_dir,
            result_path,
            expected_path=artifact_path,
            label="scope",
            expected_artifact=expected_artifact,
        )

    def _run_verification_probe(
        self,
        plan: PlanContract,
        compiled_context: CompiledDelegationContext,
        task_slice: TaskSlice,
        snapshot_before: _RuntimeWorkspaceSnapshot,
        snapshot_after: _RuntimeWorkspaceSnapshot,
        artifact_path: Path,
    ) -> str:
        expected_artifact = _verification_probe_expected(
            plan,
            compiled_context,
            task_slice,
            snapshot_before.review,
            snapshot_after.review,
        )
        result_path = self.verification_runner(
            repo_path=self.repo_path,
            plan=plan.model_copy(deep=True),
            compiled_context=compiled_context.model_copy(deep=True),
            task_slice=task_slice.model_copy(deep=True),
            shell_kind=compiled_context.verification_shell_kind,
            snapshot_before=snapshot_before.review,
            snapshot_after=snapshot_after.review,
            expected_artifact=expected_artifact,
            artifact_path=artifact_path,
        )
        return _validate_probe_artifact(
            self.run_dir,
            result_path,
            expected_path=artifact_path,
            label="verification",
            expected_artifact=expected_artifact,
        )

    def _blocked(
        self,
        *,
        readiness_status: RouteEligibility,
        issue_codes: list[str],
        plan_path: Path | None = None,
        readiness_path: Path | None = None,
        attempt_path: Path | None = None,
    ) -> DelegationRuntimeOutcome:
        return DelegationRuntimeOutcome(
            status="blocked",
            readiness_status=readiness_status,
            issue_codes=_sorted_unique(issue_codes),
            plan_path=plan_path,
            readiness_path=readiness_path,
            attempt_path=attempt_path,
            run_dir=self.run_dir,
        )


def _pre_worker_artifact_issues(
    *,
    paths: _RuntimePaths,
    readiness: DelegationReadinessResult,
    compiled_context: CompiledDelegationContext,
) -> list[str]:
    issues: list[str] = []
    try:
        persisted_plan = PlanContract.model_validate_json(_read_bounded_file(paths.plan))
    except (OSError, ValidationError, ValueError):
        issues.append("plan_artifact_invalid_before_worker")
    else:
        if readiness.plan_sha256 != _sha256_json(
            persisted_plan.model_dump(mode="json")
        ):
            issues.append("plan_artifact_hash_mismatch_before_worker")

    try:
        persisted_context = CompiledDelegationContext.model_validate_json(
            _read_bounded_file(paths.context)
        )
    except (OSError, ValidationError, ValueError):
        issues.append("context_artifact_invalid_before_worker")
    else:
        if persisted_context != compiled_context:
            issues.append("context_artifact_mismatch_before_worker")
        if readiness.context_sha256 != _sha256_json(
            persisted_context.validation_context.model_dump(mode="json")
        ):
            issues.append("context_artifact_hash_mismatch_before_worker")

    try:
        persisted_readiness = DelegationReadinessResult.model_validate_json(
            _read_bounded_file(paths.readiness)
        )
    except (OSError, ValidationError, ValueError):
        issues.append("readiness_artifact_invalid_before_worker")
    else:
        if persisted_readiness != readiness:
            issues.append("readiness_artifact_mismatch_before_worker")

    try:
        snapshot = _WorkspaceSnapshotArtifact.model_validate_json(
            _read_bounded_file(paths.snapshot_before)
        )
    except (OSError, ValidationError, ValueError):
        issues.append("snapshot_before_artifact_invalid_before_worker")
    else:
        baseline = compiled_context.validation_context.baseline
        if (
            snapshot.head_sha != baseline.head_sha
            or snapshot.fingerprint != baseline.workspace_fingerprint
        ):
            issues.append("snapshot_before_artifact_mismatch_before_worker")
    return _sorted_unique(issues)


def validate_delegation_attempt(
    attempt_path: Path,
    *,
    run_dir: Path,
) -> DelegationAttemptValidationResult:
    """验证 attempt 自身及其全部 run-owned 引用，任何歧义都交还人工。"""

    issues: list[str] = []
    try:
        run_root = _resolve_existing_run_dir(Path(run_dir))
    except ValueError:
        return _attempt_validation_result(["run_dir_invalid"])
    try:
        resolved_attempt = _require_run_owned_path(
            run_root,
            Path(attempt_path),
            label="attempt path",
        )
    except ValueError:
        return _attempt_validation_result(["attempt_path_outside_run_owned"])
    if resolved_attempt.name != DELEGATION_ATTEMPT_ARTIFACT:
        issues.append("attempt_path_not_authoritative")

    try:
        attempt_raw = _read_bounded_file(resolved_attempt)
    except OSError:
        return _attempt_validation_result(["attempt_artifact_unreadable"])
    try:
        attempt = DelegationAttempt.model_validate_json(attempt_raw)
    except (ValidationError, ValueError):
        return _attempt_validation_result(["attempt_schema_invalid"])

    referenced_paths: dict[str, Path] = {}
    referenced_raw: dict[str, bytes] = {}
    for field_name in _REFERENCE_FIELDS:
        reference = getattr(attempt, field_name)
        try:
            artifact_path = _reference_path(
                run_root,
                reference,
                label=field_name,
            )
        except ValueError:
            issues.append(f"{field_name.removesuffix('_ref')}_ref_outside_run_owned")
            continue
        referenced_paths[field_name] = artifact_path
        if field_name == "execution_ref":
            expected_path = _resolve_path(
                run_root / "executions" / "worker" / "execution.json"
            )
        else:
            expected_path = _resolve_path(
                resolved_attempt.parent / _EXPECTED_REFERENCE_NAMES[field_name]
            )
        if artifact_path != expected_path:
            issues.append(f"{field_name.removesuffix('_ref')}_ref_not_authoritative")
        try:
            raw = _read_bounded_file(artifact_path)
        except OSError:
            issues.append(f"{field_name.removesuffix('_ref')}_artifact_unreadable")
            continue
        referenced_raw[field_name] = raw
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            issues.append(f"{field_name.removesuffix('_ref')}_sha256_mismatch")

    if len(set(referenced_paths.values())) != len(referenced_paths):
        issues.append("artifact_ref_collision")
    if resolved_attempt in referenced_paths.values():
        issues.append("attempt_self_reference")

    if not issues:
        issues.extend(
            _attempt_semantic_issues(
                attempt,
                referenced_raw,
                run_id=run_root.name,
                run_root=run_root,
            )
        )
    return _attempt_validation_result(issues)


def _attempt_semantic_issues(
    attempt: DelegationAttempt,
    referenced_raw: dict[str, bytes],
    *,
    run_id: str,
    run_root: Path,
) -> list[str]:
    issues: list[str] = []
    plan: PlanContract | None = None
    compiled_context: CompiledDelegationContext | None = None
    readiness: DelegationReadinessResult | None = None
    snapshot_before: _WorkspaceSnapshotArtifact | None = None
    snapshot_after: _WorkspaceSnapshotArtifact | None = None

    try:
        plan = PlanContract.model_validate_json(referenced_raw["plan_ref"])
    except (KeyError, ValidationError, ValueError):
        issues.append("plan_artifact_invalid")
    else:
        if plan.plan_id != attempt.plan_id:
            issues.append("attempt_plan_id_mismatch")
        if len(plan.task_dag) != 1:
            issues.append("attempt_plan_not_single_slice")
        if not any(item.slice_id == attempt.slice_id for item in plan.task_dag):
            issues.append("attempt_slice_id_mismatch")

    try:
        compiled_context = CompiledDelegationContext.model_validate_json(
            referenced_raw["context_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("context_artifact_invalid")
    else:
        issues.extend(
            _compiled_context_source_issues(
                compiled_context,
                run_root=run_root,
            )
        )

    try:
        readiness = DelegationReadinessResult.model_validate_json(
            referenced_raw["readiness_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("readiness_artifact_invalid")
    else:
        if readiness.status != "budget_eligible":
            issues.append("readiness_not_budget_eligible")
        if readiness.plan_id != attempt.plan_id:
            issues.append("readiness_plan_id_mismatch")
        if readiness.checked_slice_ids != [attempt.slice_id]:
            issues.append("readiness_slice_binding_mismatch")
        if plan is not None and readiness.plan_sha256 != _sha256_json(
            plan.model_dump(mode="json")
        ):
            issues.append("readiness_plan_sha256_mismatch")
        if compiled_context is not None and readiness.context_sha256 != _sha256_json(
            compiled_context.validation_context.model_dump(mode="json")
        ):
            issues.append("readiness_context_sha256_mismatch")
        if plan is not None and compiled_context is not None:
            recomputed_readiness = evaluate_delegation_payload(
                plan,
                expected=compiled_context.validation_context,
            )
            if recomputed_readiness != readiness:
                issues.append("readiness_recomputed_mismatch")

    try:
        execution = ExecutionLease.model_validate_json(
            referenced_raw["execution_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("execution_artifact_invalid")
    else:
        issues.extend(_execution_lease_issues(execution, run_id=run_id))

    try:
        snapshot_before = _WorkspaceSnapshotArtifact.model_validate_json(
            referenced_raw["snapshot_before_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("snapshot_before_artifact_invalid")
    try:
        snapshot_after = _WorkspaceSnapshotArtifact.model_validate_json(
            referenced_raw["snapshot_after_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("snapshot_after_artifact_invalid")

    if compiled_context is not None and snapshot_before is not None:
        baseline = compiled_context.validation_context.baseline
        if snapshot_before.head_sha != baseline.head_sha:
            issues.append("context_snapshot_head_mismatch")
        if snapshot_before.fingerprint != baseline.workspace_fingerprint:
            issues.append("context_snapshot_workspace_mismatch")

    try:
        prompt = _DelegationPromptArtifact.model_validate_json(
            referenced_raw["prompt_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("prompt_artifact_invalid")
    else:
        if prompt.plan_id != attempt.plan_id:
            issues.append("prompt_plan_id_mismatch")
        if prompt.slice_id != attempt.slice_id:
            issues.append("prompt_slice_id_mismatch")
        if plan is not None:
            selected = [item for item in plan.task_dag if item.slice_id == attempt.slice_id]
            if len(selected) == 1:
                expected_prompt = _compile_worker_prompt(plan, selected[0])
                if prompt.prompt_sha256 != hashlib.sha256(
                    expected_prompt.encode("utf-8")
                ).hexdigest():
                    issues.append("prompt_sha256_mismatch")

    if (
        plan is not None
        and compiled_context is not None
        and snapshot_before is not None
        and snapshot_after is not None
    ):
        selected = [item for item in plan.task_dag if item.slice_id == attempt.slice_id]
        if len(selected) == 1:
            expected_scope = _scope_probe_expected_from_artifacts(
                plan,
                compiled_context,
                selected[0],
                snapshot_before,
                snapshot_after,
            )
            expected_verification = _verification_probe_expected_from_artifacts(
                plan,
                compiled_context,
                selected[0],
                snapshot_before,
                snapshot_after,
            )
            issues.extend(
                _probe_semantic_issues(
                    referenced_raw,
                    field_name="scope_ref",
                    expected=expected_scope,
                )
            )
            issues.extend(
                _probe_semantic_issues(
                    referenced_raw,
                    field_name="verification_ref",
                    expected=expected_verification,
                )
            )
    else:
        try:
            json.loads(referenced_raw["scope_ref"])
            json.loads(referenced_raw["verification_ref"])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            issues.append("probe_artifact_invalid")

    if attempt.worker_tier != "budget":
        issues.append("worker_tier_not_budget")
    return _sorted_unique(issues)


def _compiled_context_source_issues(
    compiled: CompiledDelegationContext,
    *,
    run_root: Path,
) -> list[str]:
    issues: list[str] = []
    context = compiled.validation_context
    if compiled.project_config_ref.relative_path not in CONFIG_FILENAMES:
        issues.append("context_project_config_ref_not_authoritative")
    if context.task_ref != compiled.task_artifact_ref:
        issues.append("context_task_ref_mismatch")
    if context.available_artifacts != compiled.input_artifact_refs:
        issues.append("context_input_artifact_refs_mismatch")
    expected_policy_sha256 = _sha256_json(
        {
            "project_config_ref": compiled.project_config_ref.model_dump(mode="json"),
            "delegation_policy_ref": compiled.delegation_policy_ref.model_dump(
                mode="json"
            ),
        }
    )
    if context.baseline.project_policy_sha256 != expected_policy_sha256:
        issues.append("context_project_policy_sha256_mismatch")

    run_refs = [
        ("task", compiled.task_artifact_ref),
        ("delegation_policy", compiled.delegation_policy_ref),
        *[("input", item) for item in compiled.input_artifact_refs],
    ]
    resolved_paths: set[Path] = set()
    raw_by_label: dict[str, list[bytes]] = {}
    for label, reference in run_refs:
        try:
            _validate_source_relative_path(reference.relative_path)
        except (ValidationError, ValueError):
            issues.append(f"context_{label}_ref_invalid")
            continue
        try:
            path = _reference_path(run_root, reference, label=f"context_{label}_ref")
            raw = _read_bounded_file(path)
        except (OSError, ValueError):
            issues.append(f"context_{label}_artifact_unreadable")
            continue
        if path in resolved_paths:
            issues.append("context_source_ref_collision")
        resolved_paths.add(path)
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            issues.append(f"context_{label}_sha256_mismatch")
        raw_by_label.setdefault(label, []).append(raw)

    task_values = raw_by_label.get("task", [])
    if len(task_values) == 1:
        try:
            task = _DelegationTaskArtifact.model_validate_json(task_values[0])
        except (ValidationError, ValueError):
            issues.append("context_task_artifact_invalid")
        else:
            if task.task_id != context.task_id:
                issues.append("context_task_id_mismatch")

    policy_values = raw_by_label.get("delegation_policy", [])
    if len(policy_values) == 1:
        try:
            policy = _DelegationPolicyArtifact.model_validate_json(policy_values[0])
        except (ValidationError, ValueError):
            issues.append("context_delegation_policy_invalid")
        else:
            if policy.budget_limits != context.budget_limits:
                issues.append("context_budget_limits_mismatch")
    return _sorted_unique(issues)


def _compiled_project_config_issues(
    compiled: CompiledDelegationContext,
    *,
    repo_path: Path,
) -> list[str]:
    try:
        path = _require_repo_owned_path(
            repo_path,
            compiled.project_config_ref.relative_path,
        )
        current_sha256 = _sha256_file(path)
    except (OSError, ValueError):
        return ["context_project_config_unreadable"]
    if current_sha256 != compiled.project_config_ref.sha256:
        return ["context_project_config_sha256_mismatch"]
    return []


def _scope_probe_expected_from_artifacts(
    plan: PlanContract,
    compiled_context: CompiledDelegationContext,
    task_slice: TaskSlice,
    snapshot_before: _WorkspaceSnapshotArtifact,
    snapshot_after: _WorkspaceSnapshotArtifact,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "delegation_scope",
        "plan_id": plan.plan_id,
        "slice_id": task_slice.slice_id,
        "plan_sha256": _sha256_json(plan.model_dump(mode="json")),
        "context_sha256": _sha256_json(
            compiled_context.validation_context.model_dump(mode="json")
        ),
        "snapshot_before_fingerprint": snapshot_before.fingerprint,
        "snapshot_after_fingerprint": snapshot_after.fingerprint,
        "scope_policy_sha256": compiled_context.validation_context.baseline.scope_policy_sha256,
        "allowed_write_paths": list(task_slice.allowed_write_paths),
        "changed_files": list(snapshot_after.changed_files),
    }


def _verification_probe_expected_from_artifacts(
    plan: PlanContract,
    compiled_context: CompiledDelegationContext,
    task_slice: TaskSlice,
    snapshot_before: _WorkspaceSnapshotArtifact,
    snapshot_after: _WorkspaceSnapshotArtifact,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "delegation_verification",
        "plan_id": plan.plan_id,
        "slice_id": task_slice.slice_id,
        "plan_sha256": _sha256_json(plan.model_dump(mode="json")),
        "context_sha256": _sha256_json(
            compiled_context.validation_context.model_dump(mode="json")
        ),
        "snapshot_before_fingerprint": snapshot_before.fingerprint,
        "snapshot_after_fingerprint": snapshot_after.fingerprint,
        "commands": list(task_slice.verification.commands),
        "shell_kind": compiled_context.verification_shell_kind,
        "oracle_kind": task_slice.verification.oracle.kind,
    }


def _probe_semantic_issues(
    referenced_raw: dict[str, bytes],
    *,
    field_name: str,
    expected: dict[str, Any],
) -> list[str]:
    label = field_name.removesuffix("_ref")
    try:
        payload = json.loads(referenced_raw[field_name])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return [f"{label}_artifact_invalid"]
    if not isinstance(payload, dict):
        return [f"{label}_artifact_invalid"]
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        return [f"{label}_artifact_invalid"]
    issues: list[str] = []
    if status.strip() not in {"passed", "success"}:
        issues.append(f"{label}_status_not_passed:{status.strip()}")
    actual_binding = dict(payload)
    actual_binding.pop("status", None)
    if actual_binding != expected:
        issues.append(f"{label}_binding_mismatch")
    return issues


def _execution_artifact_issues(path: Path, *, run_id: str) -> list[str]:
    try:
        raw = _read_bounded_file(path)
    except OSError:
        return ["execution_artifact_unreadable"]
    try:
        lease = ExecutionLease.model_validate_json(raw)
    except (ValidationError, ValueError):
        return ["execution_artifact_invalid"]
    return _execution_lease_issues(lease, run_id=run_id)


def _execution_lease_issues(
    lease: ExecutionLease,
    *,
    run_id: str,
) -> list[str]:
    issues: list[str] = []
    if lease.run_id != run_id:
        issues.append("execution_run_id_mismatch")
    if lease.step != "worker":
        issues.append("execution_step_mismatch")
    if lease.iteration != 1:
        issues.append("execution_iteration_mismatch")
    if not lease.execution_id:
        issues.append("execution_id_missing")
    if lease.status != "completed":
        issues.append(f"execution_status_not_completed:{lease.status}")
    if lease.returncode != 0:
        issues.append("execution_returncode_nonzero")
    if lease.termination_unconfirmed:
        issues.append("execution_termination_unconfirmed")
    if lease.finished_at is None:
        issues.append("execution_finished_at_missing")
    if not lease.command:
        issues.append("execution_command_missing")
    return issues


def _snapshot_payload(snapshot: _RuntimeWorkspaceSnapshot) -> dict[str, Any]:
    review = snapshot.review
    return {
        "schema_version": 1,
        "fingerprint": review.fingerprint,
        "head_sha": review.head_sha,
        "status_sha256": review.status_sha256,
        "full_diff_sha256": review.full_diff_sha256,
        "staged_diff_sha256": review.staged_diff_sha256,
        "unstaged_diff_sha256": review.unstaged_diff_sha256,
        "untracked_manifest_sha256": review.untracked_manifest_sha256,
        "ignored_manifest_sha256": review.ignored_manifest_sha256,
        "index_flags_sha256": review.index_flags_sha256,
        "tracked_files_manifest_sha256": snapshot.tracked_files_manifest_sha256,
        "tracked_file_count": len(snapshot.tracked_files),
        "changed_files": list(review.changed_files),
        "untracked_files": list(review.untracked_files),
        "unsafe_index_paths": list(review.unsafe_index_paths),
        "untracked_content_complete": review.untracked_content_complete,
    }


def _snapshot_before_issues(
    snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues: list[str] = []
    if snapshot.changed_files:
        issues.append("workspace_not_clean_before_worker")
    if snapshot.unsafe_index_paths:
        issues.append("unsafe_index_paths_before_worker")
    if not snapshot.untracked_content_complete:
        issues.append("snapshot_before_incomplete")
    return _sorted_unique(issues)


def _snapshot_after_issues(
    before: _RuntimeWorkspaceSnapshot,
    after: _RuntimeWorkspaceSnapshot,
    *,
    task_slice: TaskSlice,
    plan: PlanContract,
) -> list[str]:
    before_review = before.review
    after_review = after.review
    issues: list[str] = []
    if after_review.head_sha != before_review.head_sha:
        issues.append("worker_changed_head")
    if after_review.unsafe_index_paths:
        issues.append("unsafe_index_paths_after_worker")
    if not after_review.untracked_content_complete:
        issues.append("snapshot_after_incomplete")

    changed_files = set(after_review.changed_files)
    allowed_write_paths = set(task_slice.allowed_write_paths)
    if changed_files.difference(allowed_write_paths):
        issues.append("worker_changed_path_outside_slice_scope")
    if len(changed_files) > plan.budget.max_changed_files:
        issues.append("changed_files_exceed_plan_budget")

    new_untracked = set(after_review.untracked_files).difference(
        before_review.untracked_files
    )
    new_tracked = set(after.tracked_files).difference(before.tracked_files)
    new_files = new_untracked.union(new_tracked)
    if len(new_files) > plan.budget.max_new_files:
        issues.append("new_files_exceed_plan_budget")
    if _changed_diff_line_count(after_review.full_diff) > plan.budget.max_diff_lines:
        issues.append("diff_lines_exceed_plan_budget")
    return _sorted_unique(issues)


def _runtime_workspace_identity_issues(
    expected: _RuntimeWorkspaceSnapshot,
    actual: _RuntimeWorkspaceSnapshot,
    *,
    issue_code: str,
) -> list[str]:
    """探针和控制面检查都不能让已绑定的 workspace 在后台漂移。"""

    if (
        actual.review.head_sha != expected.review.head_sha
        or actual.review.fingerprint != expected.review.fingerprint
        or actual.tracked_files != expected.tracked_files
        or actual.tracked_files_manifest_sha256
        != expected.tracked_files_manifest_sha256
    ):
        return [issue_code]
    return []


def _changed_diff_line_count(diff_text: str) -> int:
    return sum(
        1
        for line in diff_text.splitlines()
        if (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
    )


def _capture_runtime_workspace(repo_path: Path) -> _RuntimeWorkspaceSnapshot:
    """把 reviewer snapshot 与完整 tracked manifest 绑定到同一稳定观察窗口。"""

    repo = _resolve_path(repo_path)
    before = capture_review_workspace(repo)
    raw_tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=10,
    ).stdout
    tracked_files = frozenset(
        item.decode("utf-8", errors="strict")
        for item in raw_tracked.split(b"\0")
        if item
    )
    after = capture_review_workspace(repo)
    if before.fingerprint != after.fingerprint:
        raise RuntimeError("workspace 在 Context 编译期间发生变化")
    manifest = hashlib.sha256()
    manifest.update(f"tracked-v1:{len(tracked_files)}".encode("ascii"))
    manifest.update(b"\0")
    for path in sorted(tracked_files):
        manifest.update(path.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
    return _RuntimeWorkspaceSnapshot(
        review=after,
        tracked_files=tracked_files,
        tracked_files_manifest_sha256=manifest.hexdigest(),
    )


def _compile_worker_prompt(plan: PlanContract, task_slice: TaskSlice) -> str:
    """只从冻结合同编译 Worker 输入，避免调用方另行注入未绑定 prompt。"""

    payload = {
        "plan_id": plan.plan_id,
        "plan_revision": plan.plan_revision,
        "task_id": plan.task_id,
        "task_ref": plan.task_ref.model_dump(mode="json"),
        "baseline": plan.baseline.model_dump(mode="json"),
        "slice": task_slice.model_dump(mode="json"),
    }
    return (
        "执行以下冻结的单 Slice 委派合同。只能修改 allowed_write_paths，"
        "完成后保留工作区供确定性验证；不得提交、推送或改写控制面 artifact。\n"
        + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _scope_probe_expected(
    plan: PlanContract,
    compiled_context: CompiledDelegationContext,
    task_slice: TaskSlice,
    snapshot_before: ReviewWorkspaceSnapshot,
    snapshot_after: ReviewWorkspaceSnapshot,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "delegation_scope",
        "plan_id": plan.plan_id,
        "slice_id": task_slice.slice_id,
        "plan_sha256": _sha256_json(plan.model_dump(mode="json")),
        "context_sha256": _sha256_json(
            compiled_context.validation_context.model_dump(mode="json")
        ),
        "snapshot_before_fingerprint": snapshot_before.fingerprint,
        "snapshot_after_fingerprint": snapshot_after.fingerprint,
        "scope_policy_sha256": compiled_context.validation_context.baseline.scope_policy_sha256,
        "allowed_write_paths": list(task_slice.allowed_write_paths),
        "changed_files": list(snapshot_after.changed_files),
    }


def _verification_probe_expected(
    plan: PlanContract,
    compiled_context: CompiledDelegationContext,
    task_slice: TaskSlice,
    snapshot_before: ReviewWorkspaceSnapshot,
    snapshot_after: ReviewWorkspaceSnapshot,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "delegation_verification",
        "plan_id": plan.plan_id,
        "slice_id": task_slice.slice_id,
        "plan_sha256": _sha256_json(plan.model_dump(mode="json")),
        "context_sha256": _sha256_json(
            compiled_context.validation_context.model_dump(mode="json")
        ),
        "snapshot_before_fingerprint": snapshot_before.fingerprint,
        "snapshot_after_fingerprint": snapshot_after.fingerprint,
        "commands": list(task_slice.verification.commands),
        "shell_kind": compiled_context.verification_shell_kind,
        "oracle_kind": task_slice.verification.oracle.kind,
    }


def _control_artifact_paths(
    *,
    run_dir: Path,
    repo_path: Path,
    source: DelegationContextSource,
    compiled: CompiledDelegationContext,
    paths: _RuntimePaths,
) -> tuple[Path, ...]:
    source_paths = _resolve_context_source_paths(run_dir, source)
    project_config_path = _require_repo_owned_path(
        repo_path,
        compiled.project_config_ref.relative_path,
    )
    return (
        paths.plan,
        paths.context,
        paths.readiness,
        paths.prompt,
        paths.snapshot_before,
        source_paths["task"],
        source_paths["policy"],
        *source_paths["inputs"],
        project_config_path,
    )


def _capture_control_artifact_hashes(paths: tuple[Path, ...]) -> dict[Path, str]:
    hashes: dict[Path, str] = {}
    for path in paths:
        resolved = _resolve_path(path)
        if resolved in hashes:
            raise ValueError("控制面 artifact 路径发生碰撞")
        hashes[resolved] = _sha256_file(resolved)
    return hashes


def _control_artifact_issues(expected_hashes: dict[Path, str]) -> list[str]:
    issues: list[str] = []
    for path, expected_sha256 in expected_hashes.items():
        try:
            current_sha256 = _sha256_file(path)
        except OSError:
            issues.append(f"control_artifact_missing:{path.name}")
            continue
        if current_sha256 != expected_sha256:
            issues.append(f"control_artifact_changed:{path.name}")
    return _sorted_unique(issues)


def _compiled_scope_issues(
    allowed_paths: list[str],
    forbidden_paths: list[str],
) -> list[str]:
    issues: list[str] = []
    if not allowed_paths:
        issues.append("scope_allowed_paths_missing")
    if forbidden_paths:
        issues.append("scope_forbidden_patterns_unsupported")
    if any(any(character in path for character in _GLOB_CHARACTERS) for path in allowed_paths):
        issues.append("scope_glob_patterns_unsupported")
    return _sorted_unique(issues)


def _effective_budget_limits(
    limits: BudgetEligibilityLimits,
    *,
    max_changed_files: int | None,
    max_diff_lines: int | None,
    max_new_files: int | None,
) -> BudgetEligibilityLimits:
    """项目配置只能收紧 route policy，不能被实验策略放宽。"""

    return limits.model_copy(
        update={
            "max_changed_files": min(
                limits.max_changed_files,
                max_changed_files
                if max_changed_files is not None
                else limits.max_changed_files,
            ),
            "max_diff_lines": min(
                limits.max_diff_lines,
                max_diff_lines
                if max_diff_lines is not None
                else limits.max_diff_lines,
            ),
            "max_new_files": min(
                limits.max_new_files,
                max_new_files if max_new_files is not None else limits.max_new_files,
            ),
        }
    )


def _resolve_context_source_paths(
    run_dir: Path,
    source: DelegationContextSource,
) -> dict[str, Any]:
    run_root = _resolve_existing_run_dir(run_dir)
    source.all_relative_paths()
    return {
        "task": _source_path(run_root, source.task_artifact_path, label="task artifact"),
        "policy": _source_path(
            run_root,
            source.delegation_policy_path,
            label="delegation policy artifact",
        ),
        "inputs": tuple(
            _source_path(run_root, value, label="input artifact")
            for value in source.input_artifact_paths
        ),
    }


def _source_path(run_dir: Path, relative_path: str, *, label: str) -> Path:
    candidate = run_dir.joinpath(*relative_path.split("/"))
    return _require_run_owned_path(run_dir, candidate, label=label)


def _artifact_reference_from_raw(
    run_dir: Path,
    artifact_path: Path,
    raw: bytes,
) -> ArtifactReference:
    resolved = _require_run_owned_path(
        run_dir,
        artifact_path,
        label=f"source artifact {artifact_path.name}",
    )
    return ArtifactReference(
        relative_path=resolved.relative_to(run_dir).as_posix(),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _validate_source_relative_path(value: str) -> str:
    reference = ArtifactReference(
        relative_path=value,
        sha256=_ZERO_HASH,
    )
    if reference.relative_path.startswith(("delegation/", "executions/")):
        raise ValueError("Context source 不能位于运行时生成目录")
    return reference.relative_path


def _require_tracked_regular_file(
    repo_path: Path,
    path: Path,
    head_sha: str,
) -> None:
    resolved = _require_repo_owned_path(repo_path, path.relative_to(repo_path).as_posix())
    _read_bounded_file(resolved)
    subprocess.run(
        ["git", "cat-file", "-e", f"{head_sha}:{resolved.relative_to(repo_path).as_posix()}"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        timeout=10,
    )


def _require_repo_owned_path(repo_path: Path, relative_path: str) -> Path:
    repo = _resolve_path(repo_path)
    candidate = Path(os.path.abspath(repo / Path(*relative_path.split("/"))))
    if not candidate.is_relative_to(repo):
        raise ValueError("项目策略路径必须位于目标仓库")
    if _path_contains_link_or_reparse(repo, candidate):
        raise ValueError("项目策略路径不能经过链接或 reparse point")
    return candidate


def _paths_overlap(first: Path, second: Path) -> bool:
    first_resolved = _resolve_path(first)
    second_resolved = _resolve_path(second)
    return first_resolved.is_relative_to(second_resolved) or second_resolved.is_relative_to(
        first_resolved
    )


def _sha256_json(payload: object) -> str:
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _validate_probe_artifact(
    run_dir: Path,
    result_path: Path,
    *,
    expected_path: Path,
    label: str,
    expected_artifact: dict[str, Any],
) -> str:
    if not isinstance(result_path, Path):
        raise ValueError(f"{label} probe 必须返回 artifact Path")
    resolved_result = _require_run_owned_path(
        run_dir,
        result_path,
        label=f"{label} artifact",
    )
    resolved_expected = _require_run_owned_path(
        run_dir,
        expected_path,
        label=f"{label} expected artifact",
    )
    if resolved_result != resolved_expected:
        raise ValueError(f"{label} probe 返回了非权威 artifact 路径")
    raw = _read_bounded_file(resolved_expected)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} artifact 必须是 JSON") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("status"), str)
        or not payload["status"].strip()
    ):
        raise ValueError(f"{label} artifact 缺少结构化 status")
    status = payload["status"].strip()
    actual_binding = dict(payload)
    actual_binding.pop("status", None)
    if actual_binding != expected_artifact:
        raise ValueError(f"{label} artifact 与当前委派事实绑定不一致")
    return status


def _artifact_reference(run_dir: Path, artifact_path: Path) -> ArtifactReference:
    resolved = _require_run_owned_path(
        run_dir,
        artifact_path,
        label=f"artifact {artifact_path.name}",
    )
    raw = _read_bounded_file(resolved)
    return ArtifactReference(
        relative_path=resolved.relative_to(run_dir).as_posix(),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _reference_path(
    run_dir: Path,
    reference: ArtifactReference,
    *,
    label: str,
) -> Path:
    candidate = run_dir.joinpath(*reference.relative_path.split("/"))
    return _require_run_owned_path(run_dir, candidate, label=label)


def _write_json_artifact(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(
            redact_value(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    descriptor = -1
    try:
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise OSError("delegation artifact 必须是非 hardlink 普通文件")
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            descriptor = -1
            stream.write(data)
        current = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise OSError("delegation artifact 在写入期间被替换")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_bounded_file(path: Path) -> bytes:
    if _is_link_or_reparse_point(path):
        raise OSError("delegation artifact 不能是链接或 reparse point")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError("delegation artifact 必须是非 hardlink 普通文件")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise OSError("delegation artifact 在打开期间被替换")
        raw = stream.read(MAX_DELEGATION_RUNTIME_ARTIFACT_BYTES + 1)
    after = path.lstat()
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
    ):
        raise OSError("delegation artifact 在读取期间被替换")
    if len(raw) > MAX_DELEGATION_RUNTIME_ARTIFACT_BYTES:
        raise OSError("delegation runtime artifact 超过大小上限")
    return raw


def _require_run_owned_path(
    run_dir: Path,
    candidate: Path,
    *,
    label: str,
) -> Path:
    run_root = _resolve_path(run_dir)
    lexical = candidate if candidate.is_absolute() else run_root / candidate
    lexical = Path(os.path.abspath(lexical))
    if not lexical.is_relative_to(run_root):
        raise ValueError(f"{label} 必须位于 run-owned 目录")
    if _path_contains_link_or_reparse(run_root, lexical):
        raise ValueError(f"{label} 不能经过符号链接、junction 或 reparse point")
    resolved = _resolve_path(lexical)
    if not resolved.is_relative_to(run_root):
        raise ValueError(f"{label} 必须位于 run-owned 目录")
    return resolved


def _prepare_run_dir(run_dir: Path) -> Path:
    return _resolve_run_dir_boundary(run_dir, create=True)


def _resolve_existing_run_dir(run_dir: Path) -> Path:
    return _resolve_run_dir_boundary(run_dir, create=False)


def _resolve_run_dir_boundary(run_dir: Path, *, create: bool) -> Path:
    if _contains_parent_segment(run_dir):
        raise ValueError("run_dir 不能包含路径穿越")
    absolute = Path(os.path.abspath(run_dir))
    if absolute.parent.name != "runs" or not _SAFE_RUN_ID.fullmatch(absolute.name):
        raise ValueError("run_dir 必须是 workspace/runs/<run_id>")
    runs_root = resolve_runs_root(absolute.parent.parent, create=create)
    if runs_root is None:
        raise ValueError("runs 根目录不存在")
    candidate = runs_root / absolute.name
    if os.path.lexists(candidate):
        if _is_link_or_reparse_point(candidate):
            raise ValueError("run_dir 不能是符号链接、junction 或 reparse point")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError("无法安全解析 run_dir") from exc
        if not resolved.is_dir() or not resolved.is_relative_to(runs_root):
            raise ValueError("run_dir 必须是 runs 根目录内的真实目录")
        return resolved
    if not create:
        raise ValueError("run_dir 不存在")
    try:
        candidate.mkdir()
    except OSError as exc:
        raise ValueError("无法创建 run_dir") from exc
    return candidate.resolve(strict=True)


def _path_contains_link_or_reparse(run_root: Path, candidate: Path) -> bool:
    try:
        relative_parts = candidate.relative_to(run_root).parts
    except ValueError:
        return True
    current = run_root
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


def _resolve_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("无法安全解析 run-owned 路径") from exc


def _contains_parent_segment(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(_read_bounded_file(path)).hexdigest()


def _attempt_validation_result(
    issues: list[str],
) -> DelegationAttemptValidationResult:
    normalized = _sorted_unique(issues)
    return DelegationAttemptValidationResult(
        status="human_required" if normalized else "valid",
        issue_codes=normalized,
    )


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


__all__ = [
    "DELEGATION_ATTEMPT_ARTIFACT",
    "DELEGATION_CONTEXT_ARTIFACT",
    "DELEGATION_PLAN_ARTIFACT",
    "DELEGATION_PROMPT_ARTIFACT",
    "CompiledDelegationContext",
    "DelegationAttempt",
    "DelegationAttemptValidationResult",
    "DelegationContextCompilationError",
    "DelegationContextSource",
    "DelegationRuntimeBridge",
    "DelegationRuntimeOutcome",
    "compile_delegation_context",
    "validate_delegation_attempt",
]
