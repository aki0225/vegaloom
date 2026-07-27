from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...redaction import write_redacted_json
from .delegation_contract import (
    AcceptanceFact,
    AcceptanceId,
    ArtifactReference,
    BudgetEligibilityLimits,
    CommandText,
    DelegationReadinessResult,
    DelegationSnapshot,
    DelegationValidationContext,
    GitObjectId,
    IssueCode,
    NonEmptyText,
    PlanBudget,
    PlanContract,
    PlanDecisions,
    PlanGoal,
    PlanId,
    PlanRisk,
    ReasonCode,
    RouteEligibility,
    Sha256,
    ShortText,
    SliceId,
    SliceVerification,
    TaskId,
    TaskSlice,
    ThreatId,
    VerificationOracle,
)


DELEGATION_SCHEMA_VERSION = 1
DELEGATION_READINESS_ARTIFACT = "delegation-readiness.json"
MAX_DELEGATION_INPUT_BYTES = 1024 * 1024

_ZERO_HASH = hashlib.sha256(b"").hexdigest()


class _DelegationInputReadError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def evaluate_delegation_payload(
    payload: PlanContract | dict[str, Any],
    *,
    expected: DelegationValidationContext,
) -> DelegationReadinessResult:
    """严格解析并确定性判断一次计划是否可委派。"""

    input_sha256 = _sha256_json(
        payload.model_dump(mode="json") if isinstance(payload, PlanContract) else payload
    )
    try:
        plan = (
            payload
            if isinstance(payload, PlanContract)
            else PlanContract.model_validate(payload)
        )
    except (ValidationError, RecursionError, TypeError, ValueError):
        return _invalid_contract_result(
            expected,
            input_sha256=input_sha256,
            issue_codes=["contract_schema_invalid"],
        )
    return _evaluate_plan(plan, expected=expected, input_sha256=input_sha256)


def evaluate_delegation_file(
    path: Path,
    *,
    expected: DelegationValidationContext,
) -> DelegationReadinessResult:
    """从有界 JSON 文件读取计划；不可读、过大或非法 JSON 一律 fail-closed。"""

    try:
        raw = _read_bounded_bytes(path, MAX_DELEGATION_INPUT_BYTES)
    except _DelegationInputReadError as exc:
        issue = (
            "delegation_artifact_too_large"
            if exc.code == "artifact_too_large"
            else "delegation_artifact_unreadable"
        )
        return _invalid_contract_result(
            expected,
            input_sha256=_ZERO_HASH,
            issue_codes=[issue],
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return _invalid_contract_result(
            expected,
            input_sha256=hashlib.sha256(raw).hexdigest(),
            issue_codes=["delegation_artifact_invalid_json"],
        )

    result = evaluate_delegation_payload(payload, expected=expected)
    return result.model_copy(update={"input_sha256": hashlib.sha256(raw).hexdigest()})


def write_delegation_readiness_result(
    path: Path,
    result: DelegationReadinessResult,
) -> str:
    """写入最小 route evidence artifact，并返回实际文件内容哈希。"""

    write_redacted_json(path, result.model_dump(mode="json"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evaluate_plan(
    plan: PlanContract,
    *,
    expected: DelegationValidationContext,
    input_sha256: str,
) -> DelegationReadinessResult:
    binding_issues, write_path_owners = _collect_binding_issues(plan, expected)
    readiness_issues = _collect_readiness_issues(plan)
    premium_reasons = _collect_premium_reasons(plan, expected, write_path_owners)
    status, issue_codes = _route_from_issues(
        binding_issues=binding_issues,
        readiness_issues=readiness_issues,
        premium_reasons=premium_reasons,
    )

    return DelegationReadinessResult(
        schema_version=DELEGATION_SCHEMA_VERSION,
        status=status,
        task_id=expected.task_id,
        plan_id=plan.plan_id,
        plan_revision=plan.plan_revision,
        contract_valid=True,
        binding_valid=not binding_issues,
        input_sha256=input_sha256,
        plan_sha256=_sha256_json(plan.model_dump(mode="json")),
        context_sha256=_sha256_json(expected.model_dump(mode="json")),
        checked_slice_ids=sorted(item.slice_id for item in plan.task_dag),
        issue_codes=issue_codes,
    )


def _collect_binding_issues(
    plan: PlanContract,
    expected: DelegationValidationContext,
) -> tuple[list[str], dict[str, list[str]]]:
    issues: list[str] = []
    if plan.task_id != expected.task_id:
        issues.append("task_identity_mismatch")
    if plan.task_ref != expected.task_ref:
        issues.append("task_artifact_mismatch")
    _compare_snapshot(plan.baseline, expected.baseline, issues)

    available_artifacts = {
        item.relative_path: item.sha256 for item in expected.available_artifacts
    }
    if plan.parent_plan_ref is not None and not _artifact_available(
        plan.parent_plan_ref,
        available_artifacts,
    ):
        issues.append("parent_plan_artifact_unavailable")

    write_path_owners: dict[str, list[str]] = {}
    for task_slice in plan.task_dag:
        _validate_slice_binding(
            task_slice,
            expected=expected,
            available_artifacts=available_artifacts,
            write_path_owners=write_path_owners,
            issues=issues,
        )
    return issues, write_path_owners


def _validate_slice_binding(
    task_slice: TaskSlice,
    *,
    expected: DelegationValidationContext,
    available_artifacts: dict[str, str],
    write_path_owners: dict[str, list[str]],
    issues: list[str],
) -> None:
    for path in task_slice.read_paths:
        if path not in expected.allowed_read_paths:
            issues.append(f"read_path_outside_compiled_scope:{task_slice.slice_id}")
    for path in task_slice.allowed_write_paths:
        write_path_owners.setdefault(path, []).append(task_slice.slice_id)
        if path not in expected.allowed_write_paths:
            issues.append(f"write_path_outside_compiled_scope:{task_slice.slice_id}")
    for command in task_slice.verification.commands:
        if command not in expected.allowed_verification_commands:
            issues.append(f"verification_command_not_authorized:{task_slice.slice_id}")
    for artifact_ref in task_slice.input_artifact_refs:
        if not _artifact_available(artifact_ref, available_artifacts):
            issues.append(f"input_artifact_unavailable:{task_slice.slice_id}")


def _collect_readiness_issues(plan: PlanContract) -> list[str]:
    issues: list[str] = []
    if plan.decisions.unresolved:
        issues.append("unresolved_decisions")
    if plan.risk.human_required:
        issues.append("risk_requires_human")
    return issues


def _collect_premium_reasons(
    plan: PlanContract,
    expected: DelegationValidationContext,
    write_path_owners: dict[str, list[str]],
) -> list[str]:
    limits = expected.budget_limits
    reasons: list[str] = []
    if plan.risk.premium_worker_required:
        reasons.append("risk_requires_premium")
    if len(plan.task_dag) > limits.max_slices:
        reasons.append("slice_count_exceeds_budget_limit")
    dependency_edges = sum(len(item.dependencies) for item in plan.task_dag)
    if dependency_edges > limits.max_dependency_edges:
        reasons.append("dependency_count_exceeds_budget_limit")
    if len(write_path_owners) > limits.max_write_paths:
        reasons.append("write_path_count_exceeds_budget_limit")
    if any(len(owners) > 1 for owners in write_path_owners.values()):
        reasons.append("write_path_shared_across_slices")
    _compare_budget(plan.budget, limits, reasons)
    return reasons


def _route_from_issues(
    *,
    binding_issues: list[str],
    readiness_issues: list[str],
    premium_reasons: list[str],
) -> tuple[RouteEligibility, list[str]]:
    blocking_issues = _sorted_unique(binding_issues + readiness_issues)
    if blocking_issues:
        return "human_required", blocking_issues
    if premium_reasons:
        return "premium_required", _sorted_unique(premium_reasons)
    return "budget_eligible", []


def _invalid_contract_result(
    expected: DelegationValidationContext,
    *,
    input_sha256: str,
    issue_codes: list[str],
) -> DelegationReadinessResult:
    return DelegationReadinessResult(
        schema_version=DELEGATION_SCHEMA_VERSION,
        status="human_required",
        task_id=expected.task_id,
        plan_id=None,
        plan_revision=None,
        contract_valid=False,
        binding_valid=False,
        input_sha256=input_sha256,
        plan_sha256=None,
        context_sha256=_sha256_json(expected.model_dump(mode="json")),
        checked_slice_ids=[],
        issue_codes=_sorted_unique(issue_codes),
    )


def _compare_snapshot(
    actual: DelegationSnapshot,
    expected: DelegationSnapshot,
    issues: list[str],
) -> None:
    comparisons = (
        ("head_sha", "snapshot_head_mismatch"),
        ("workspace_fingerprint", "snapshot_workspace_mismatch"),
        ("project_policy_sha256", "snapshot_project_policy_mismatch"),
        ("scope_policy_sha256", "snapshot_scope_policy_mismatch"),
    )
    for field_name, issue_code in comparisons:
        if getattr(actual, field_name) != getattr(expected, field_name):
            issues.append(issue_code)


def _compare_budget(
    budget: PlanBudget,
    limits: BudgetEligibilityLimits,
    reasons: list[str],
) -> None:
    comparisons = (
        (budget.max_changed_files, limits.max_changed_files, "changed_files_exceed_budget_limit"),
        (budget.max_diff_lines, limits.max_diff_lines, "diff_lines_exceed_budget_limit"),
        (budget.max_new_files, limits.max_new_files, "new_files_exceed_budget_limit"),
        (
            budget.context_limit_tokens,
            limits.max_context_tokens,
            "context_tokens_exceed_budget_limit",
        ),
        (
            budget.worker_time_limit_seconds,
            limits.max_worker_time_seconds,
            "worker_time_exceeds_budget_limit",
        ),
        (
            budget.worker_token_limit,
            limits.max_worker_tokens,
            "worker_tokens_exceed_budget_limit",
        ),
    )
    for actual, maximum, issue_code in comparisons:
        if actual > maximum:
            reasons.append(issue_code)


def _artifact_available(
    artifact_ref: ArtifactReference,
    available_artifacts: dict[str, str],
) -> bool:
    return available_artifacts.get(artifact_ref.relative_path) == artifact_ref.sha256


def _read_bounded_bytes(path: Path, maximum_bytes: int) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except (OSError, ValueError) as exc:
        raise _DelegationInputReadError("artifact_unreadable") from exc
    if len(payload) > maximum_bytes:
        raise _DelegationInputReadError("artifact_too_large")
    return payload


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted(set(values))


def _sha256_json(payload: object) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        serialized = "<unserializable>"
    return hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()


__all__ = [
    "DELEGATION_READINESS_ARTIFACT",
    "DELEGATION_SCHEMA_VERSION",
    "MAX_DELEGATION_INPUT_BYTES",
    "AcceptanceFact",
    "AcceptanceId",
    "ArtifactReference",
    "BudgetEligibilityLimits",
    "CommandText",
    "DelegationReadinessResult",
    "DelegationSnapshot",
    "DelegationValidationContext",
    "GitObjectId",
    "IssueCode",
    "NonEmptyText",
    "PlanBudget",
    "PlanContract",
    "PlanDecisions",
    "PlanGoal",
    "PlanId",
    "PlanRisk",
    "ReasonCode",
    "RouteEligibility",
    "Sha256",
    "ShortText",
    "SliceId",
    "SliceVerification",
    "TaskId",
    "TaskSlice",
    "ThreatId",
    "VerificationOracle",
    "evaluate_delegation_file",
    "evaluate_delegation_payload",
    "write_delegation_readiness_result",
]
