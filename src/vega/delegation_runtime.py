from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .delegation import (
    DELEGATION_READINESS_ARTIFACT,
    ArtifactReference,
    DelegationReadinessResult,
    DelegationValidationContext,
    GitObjectId,
    PlanContract,
    PlanId,
    RouteEligibility,
    Sha256,
    SliceId,
    TaskSlice,
    evaluate_delegation_payload,
)
from .execution_control import (
    ExecutionLease,
    RunnerExecutionContext,
)
from .redaction import redact_value
from .run_utils import resolve_runs_root
from .runner import Runner, RunnerResult
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


DELEGATION_PLAN_ARTIFACT = "delegation-plan.json"
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
_SUPPORTED_SHELL_KINDS = frozenset({"cmd", "posix", "posix-sh"})
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_REFERENCE_FIELDS = (
    "plan_ref",
    "readiness_ref",
    "execution_ref",
    "snapshot_before_ref",
    "snapshot_after_ref",
    "scope_ref",
    "verification_ref",
)
_EXPECTED_REFERENCE_NAMES = {
    "plan_ref": DELEGATION_PLAN_ARTIFACT,
    "readiness_ref": DELEGATION_READINESS_ARTIFACT,
    "execution_ref": "execution.json",
    "snapshot_before_ref": DELEGATION_SNAPSHOT_BEFORE_ARTIFACT,
    "snapshot_after_ref": DELEGATION_SNAPSHOT_AFTER_ARTIFACT,
    "scope_ref": DELEGATION_SCOPE_ARTIFACT,
    "verification_ref": DELEGATION_VERIFICATION_ARTIFACT,
}


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
    readiness_ref: ArtifactReference
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
    changed_files: list[str] = Field(max_length=10000)
    untracked_files: list[str] = Field(max_length=10000)
    unsafe_index_paths: list[str] = Field(max_length=10000)
    untracked_content_complete: bool


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
    readiness: Path
    execution: Path
    snapshot_before: Path
    snapshot_after: Path
    scope: Path
    verification: Path
    attempt: Path

    def generated_artifacts(self) -> tuple[Path, ...]:
        return (
            self.plan,
            self.readiness,
            self.snapshot_before,
            self.snapshot_after,
            self.scope,
            self.verification,
            self.attempt,
        )


class DelegationRuntimeBridge:
    """默认不接入产品路径的单 slice 委派桥接。"""

    def __init__(
        self,
        *,
        run_dir: Path,
        repo_path: Path,
        worker_runner: Runner,
        worker_tier: str,
        validation_context: DelegationValidationContext,
        shell_kind: str | None,
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
        self.validation_context = validation_context
        self.shell_kind = shell_kind
        self.scope_gate = scope_gate
        self.verification_runner = verification_runner

    def run(
        self,
        *,
        plan: PlanContract | dict[str, Any],
        slice_id: str,
        prompt: str,
    ) -> DelegationRuntimeOutcome:
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
        readiness = evaluate_delegation_payload(
            plan,
            expected=self.validation_context,
        )
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            paths = self._runtime_paths()
            _write_json_artifact(paths.plan, plan_payload)
            _write_json_artifact(
                paths.readiness,
                readiness.model_dump(mode="json"),
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
            prompt=prompt,
        )
        if runtime_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=runtime_issues,
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        validated_plan = PlanContract.model_validate(plan_payload)
        task_slice = validated_plan.task_dag[0]
        try:
            snapshot_before = capture_review_workspace(self.repo_path)
            _write_json_artifact(
                paths.snapshot_before,
                _snapshot_payload(snapshot_before),
            )
        except Exception:  # noqa: BLE001 - 快照无法完整捕获时禁止启动 worker
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=["snapshot_before_capture_failed"],
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )
        snapshot_before_issues = _snapshot_before_issues(snapshot_before)
        if snapshot_before_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=snapshot_before_issues,
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
                prompt,
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
            snapshot_after = capture_review_workspace(self.repo_path)
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
        if worker_issue or worker_result_issue or execution_issues or snapshot_after_issues:
            return self._blocked(
                readiness_status=readiness.status,
                issue_codes=_sorted_unique(
                    [
                        issue
                        for issue in [worker_issue, worker_result_issue]
                        if issue
                    ]
                    + execution_issues
                    + snapshot_after_issues
                ),
                plan_path=paths.plan,
                readiness_path=paths.readiness,
            )

        try:
            scope_status = self._run_scope_probe(
                validated_plan,
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

        try:
            verification_status = self._run_verification_probe(
                validated_plan,
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

        attempt = DelegationAttempt(
            schema_version=1,
            plan_id=validated_plan.plan_id,
            slice_id=task_slice.slice_id,
            worker_tier="budget",
            plan_ref=_artifact_reference(self.run_dir, paths.plan),
            readiness_ref=_artifact_reference(self.run_dir, paths.readiness),
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
            readiness=artifact_dir / DELEGATION_READINESS_ARTIFACT,
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
        prompt: str,
    ) -> list[str]:
        issues = list(readiness.issue_codes)
        if readiness.status != "budget_eligible":
            issues.append(f"readiness_status:{readiness.status}")
        if self.worker_tier not in {"budget", "premium"}:
            issues.append("worker_tier_unknown")
        elif self.worker_tier != "budget":
            issues.append("worker_tier_mismatch")
        if self.shell_kind is None or not self.shell_kind.strip():
            issues.append("shell_kind_missing")
        elif self.shell_kind not in _SUPPORTED_SHELL_KINDS:
            issues.append("shell_kind_unsupported")
        if not isinstance(prompt, str) or not prompt.strip():
            issues.append("worker_prompt_missing")
        elif "\0" in prompt:
            issues.append("worker_prompt_invalid")
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
        task_slice: TaskSlice,
        snapshot_before: ReviewWorkspaceSnapshot,
        snapshot_after: ReviewWorkspaceSnapshot,
        artifact_path: Path,
    ) -> str:
        result_path = self.scope_gate(
            repo_path=self.repo_path,
            plan=plan,
            task_slice=task_slice,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            artifact_path=artifact_path,
        )
        return _validate_probe_artifact(
            self.run_dir,
            result_path,
            expected_path=artifact_path,
            label="scope",
        )

    def _run_verification_probe(
        self,
        plan: PlanContract,
        task_slice: TaskSlice,
        snapshot_before: ReviewWorkspaceSnapshot,
        snapshot_after: ReviewWorkspaceSnapshot,
        artifact_path: Path,
    ) -> str:
        result_path = self.verification_runner(
            repo_path=self.repo_path,
            plan=plan,
            task_slice=task_slice,
            shell_kind=self.shell_kind,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            artifact_path=artifact_path,
        )
        return _validate_probe_artifact(
            self.run_dir,
            result_path,
            expected_path=artifact_path,
            label="verification",
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
        issues.extend(_attempt_semantic_issues(attempt, referenced_raw, run_root.name))
    return _attempt_validation_result(issues)


def _attempt_semantic_issues(
    attempt: DelegationAttempt,
    referenced_raw: dict[str, bytes],
    run_id: str,
) -> list[str]:
    issues: list[str] = []
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

    try:
        execution = ExecutionLease.model_validate_json(
            referenced_raw["execution_ref"]
        )
    except (KeyError, ValidationError, ValueError):
        issues.append("execution_artifact_invalid")
    else:
        issues.extend(_execution_lease_issues(execution, run_id=run_id))

    for field_name in ("snapshot_before_ref", "snapshot_after_ref"):
        try:
            _WorkspaceSnapshotArtifact.model_validate_json(referenced_raw[field_name])
        except (KeyError, ValidationError, ValueError):
            issues.append(f"{field_name.removesuffix('_ref')}_artifact_invalid")

    for field_name in ("scope_ref", "verification_ref"):
        try:
            payload = json.loads(referenced_raw[field_name])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            issues.append(f"{field_name.removesuffix('_ref')}_artifact_invalid")
            continue
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("status"), str)
            or not payload["status"].strip()
        ):
            issues.append(f"{field_name.removesuffix('_ref')}_artifact_invalid")
            continue
        status = payload["status"].strip()
        if status not in {"passed", "success"}:
            issues.append(
                f"{field_name.removesuffix('_ref')}_status_not_passed:{status}"
            )

    if attempt.worker_tier != "budget":
        issues.append("worker_tier_not_budget")
    return _sorted_unique(issues)


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


def _snapshot_payload(snapshot: ReviewWorkspaceSnapshot) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fingerprint": snapshot.fingerprint,
        "head_sha": snapshot.head_sha,
        "status_sha256": snapshot.status_sha256,
        "full_diff_sha256": snapshot.full_diff_sha256,
        "staged_diff_sha256": snapshot.staged_diff_sha256,
        "unstaged_diff_sha256": snapshot.unstaged_diff_sha256,
        "untracked_manifest_sha256": snapshot.untracked_manifest_sha256,
        "ignored_manifest_sha256": snapshot.ignored_manifest_sha256,
        "index_flags_sha256": snapshot.index_flags_sha256,
        "changed_files": list(snapshot.changed_files),
        "untracked_files": list(snapshot.untracked_files),
        "unsafe_index_paths": list(snapshot.unsafe_index_paths),
        "untracked_content_complete": snapshot.untracked_content_complete,
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
    before: ReviewWorkspaceSnapshot,
    after: ReviewWorkspaceSnapshot,
    *,
    task_slice: TaskSlice,
    plan: PlanContract,
) -> list[str]:
    issues: list[str] = []
    if after.head_sha != before.head_sha:
        issues.append("worker_changed_head")
    if after.unsafe_index_paths:
        issues.append("unsafe_index_paths_after_worker")
    if not after.untracked_content_complete:
        issues.append("snapshot_after_incomplete")

    changed_files = set(after.changed_files)
    allowed_write_paths = set(task_slice.allowed_write_paths)
    if changed_files.difference(allowed_write_paths):
        issues.append("worker_changed_path_outside_slice_scope")
    if len(changed_files) > plan.budget.max_changed_files:
        issues.append("changed_files_exceed_plan_budget")

    new_untracked = set(after.untracked_files).difference(before.untracked_files)
    if len(new_untracked) > plan.budget.max_new_files:
        issues.append("new_files_exceed_plan_budget")
    if _changed_diff_line_count(after.full_diff) > plan.budget.max_diff_lines:
        issues.append("diff_lines_exceed_plan_budget")
    return _sorted_unique(issues)


def _changed_diff_line_count(diff_text: str) -> int:
    return sum(
        1
        for line in diff_text.splitlines()
        if (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
    )


def _validate_probe_artifact(
    run_dir: Path,
    result_path: Path,
    *,
    expected_path: Path,
    label: str,
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
    return payload["status"].strip()


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
    "DELEGATION_PLAN_ARTIFACT",
    "DelegationAttempt",
    "DelegationAttemptValidationResult",
    "DelegationRuntimeBridge",
    "DelegationRuntimeOutcome",
    "validate_delegation_attempt",
]
