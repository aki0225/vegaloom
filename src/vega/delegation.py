from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .redaction import redact_text, sensitive_path_reason, write_redacted_json


DELEGATION_SCHEMA_VERSION = 1
DELEGATION_READINESS_ARTIFACT = "delegation-readiness.json"
MAX_DELEGATION_INPUT_BYTES = 1024 * 1024

_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)
_ZERO_HASH = hashlib.sha256(b"").hexdigest()
_PATH_GLOB_CHARACTERS = frozenset("*?[]{}")
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(
        r"(?<![A-Za-z0-9._\\-])(?:\\\\){1,2}"
        r"[A-Za-z0-9._$-]+\\+[A-Za-z0-9._$-]+"
    ),
    re.compile(
        r"(?<![A-Za-z0-9:/])/"
        r"(?![A-Za-z?](?:[\s\"'`<>]|$))"
    ),
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitObjectId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]
PlanId = Annotated[
    str,
    StringConstraints(pattern=r"^PLAN-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
TaskId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"),
]
SliceId = Annotated[
    str,
    StringConstraints(pattern=r"^S-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
AcceptanceId = Annotated[
    str,
    StringConstraints(pattern=r"^A-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
ThreatId = Annotated[
    str,
    StringConstraints(pattern=r"^T-[A-Z0-9][A-Z0-9._-]{0,99}$"),
]
ReasonCode = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9._-]{0,99}$"),
]
IssueCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]
CommandText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
RouteEligibility = Literal["budget_eligible", "premium_required", "human_required"]


class ArtifactReference(BaseModel):
    """只保存仓库相对路径与内容哈希，不把本机位置变成业务身份。"""

    model_config = _STRICT_MODEL

    relative_path: str
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_repo_relative_path(value, "relative_path")


class DelegationSnapshot(BaseModel):
    """委派前必须冻结的代码与策略身份。"""

    model_config = _STRICT_MODEL

    head_sha: GitObjectId
    workspace_fingerprint: Sha256
    project_policy_sha256: Sha256
    scope_policy_sha256: Sha256


class AcceptanceFact(BaseModel):
    """计划必须显式覆盖的一个验收事实。"""

    model_config = _STRICT_MODEL

    fact_id: AcceptanceId
    statement: NonEmptyText


class PlanGoal(BaseModel):
    model_config = _STRICT_MODEL

    acceptance_facts: list[AcceptanceFact] = Field(min_length=1, max_length=64)
    non_goals: list[NonEmptyText] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_unique_goal_items(self) -> PlanGoal:
        _require_unique(
            [item.fact_id for item in self.acceptance_facts],
            "acceptance_facts 中的 fact_id 不能重复",
        )
        _require_unique(self.non_goals, "non_goals 不能重复")
        return self


class VerificationOracle(BaseModel):
    """MA-1 只接受可由结构化执行结果判定的最小 oracle。"""

    model_config = _STRICT_MODEL

    kind: Literal["all_commands_exit_zero"]


class SliceVerification(BaseModel):
    model_config = _STRICT_MODEL

    commands: list[CommandText] = Field(min_length=1, max_length=8)
    oracle: VerificationOracle

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, values: list[str]) -> list[str]:
        normalized = [_validate_command(value) for value in values]
        _require_unique(normalized, "verification commands 不能重复")
        return normalized


class TaskSlice(BaseModel):
    """一个可独立委派、验证并在失败后交还的最小工作单元。"""

    model_config = _STRICT_MODEL

    slice_id: SliceId
    read_paths: list[str] = Field(min_length=1, max_length=128)
    allowed_write_paths: list[str] = Field(min_length=1, max_length=64)
    dependencies: list[SliceId] = Field(max_length=64)
    preconditions: list[NonEmptyText] = Field(max_length=64)
    expected_change: NonEmptyText
    acceptance_refs: list[AcceptanceId] = Field(min_length=1, max_length=64)
    input_artifact_refs: list[ArtifactReference] = Field(max_length=64)
    verification: SliceVerification
    failure_and_recovery: NonEmptyText

    @field_validator("read_paths", "allowed_write_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [_validate_repo_relative_path(value, "slice path") for value in values]
        _require_unique(normalized, "slice path 不能重复")
        return normalized

    @field_validator("dependencies", "acceptance_refs")
    @classmethod
    def validate_unique_references(cls, values: list[str]) -> list[str]:
        _require_unique(values, "slice reference 不能重复")
        return values

    @field_validator("preconditions")
    @classmethod
    def validate_unique_preconditions(cls, values: list[str]) -> list[str]:
        _require_unique(values, "preconditions 不能重复")
        return values

    @field_validator("input_artifact_refs")
    @classmethod
    def validate_unique_input_artifacts(
        cls,
        values: list[ArtifactReference],
    ) -> list[ArtifactReference]:
        _require_unique(
            [item.relative_path for item in values],
            "input_artifact_refs 不能对同一路径声明多个版本",
        )
        return values

    @model_validator(mode="after")
    def validate_self_dependency(self) -> TaskSlice:
        if self.slice_id in self.dependencies:
            raise ValueError("slice 不能依赖自身")
        return self


class PlanDecisions(BaseModel):
    model_config = _STRICT_MODEL

    resolved: list[NonEmptyText] = Field(max_length=64)
    unresolved: list[NonEmptyText] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_decisions(self) -> PlanDecisions:
        _require_unique(self.resolved, "resolved decisions 不能重复")
        _require_unique(self.unresolved, "unresolved decisions 不能重复")
        if set(self.resolved).intersection(self.unresolved):
            raise ValueError("同一 decision 不能同时处于 resolved 与 unresolved")
        return self


class PlanRisk(BaseModel):
    model_config = _STRICT_MODEL

    threat_refs: list[ThreatId] = Field(max_length=64)
    human_required: bool
    premium_worker_required: bool

    @field_validator("threat_refs")
    @classmethod
    def validate_unique_threats(cls, values: list[str]) -> list[str]:
        _require_unique(values, "threat_refs 不能重复")
        return values


class PlanBudget(BaseModel):
    model_config = _STRICT_MODEL

    max_changed_files: int = Field(ge=1, le=1000)
    max_diff_lines: int = Field(ge=1, le=1_000_000)
    max_new_files: int = Field(ge=0, le=1000)
    context_limit_tokens: int = Field(ge=1, le=10_000_000)
    worker_time_limit_seconds: int = Field(ge=1, le=86_400)
    worker_token_limit: int = Field(ge=1, le=10_000_000)


class PlanContract(BaseModel):
    """执行前的权威委派输入；它本身不能声明 route 结果。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    plan_id: PlanId
    plan_revision: int = Field(ge=1, le=10_000)
    parent_plan_ref: ArtifactReference | None = None
    change_reason_code: ReasonCode | None = None
    change_summary: ShortText | None = None
    invalidated_slice_ids: list[SliceId] = Field(max_length=128)
    task_id: TaskId
    task_ref: ArtifactReference
    baseline: DelegationSnapshot
    goal: PlanGoal
    task_dag: list[TaskSlice] = Field(min_length=1, max_length=64)
    decisions: PlanDecisions
    risk: PlanRisk
    budget: PlanBudget

    @field_validator("invalidated_slice_ids")
    @classmethod
    def validate_invalidated_slices(cls, values: list[str]) -> list[str]:
        _require_unique(values, "invalidated_slice_ids 不能重复")
        return values

    @model_validator(mode="after")
    def validate_revision_and_graph(self) -> PlanContract:
        self._validate_revision()
        self._validate_task_graph()
        return self

    def _validate_revision(self) -> None:
        revision_fields_present = (
            self.parent_plan_ref is not None
            or self.change_reason_code is not None
            or self.change_summary is not None
            or bool(self.invalidated_slice_ids)
        )
        if self.plan_revision == 1 and revision_fields_present:
            raise ValueError("首版计划不能声明 parent、change reason 或 invalidated slices")
        if self.plan_revision > 1 and (
            self.parent_plan_ref is None
            or self.change_reason_code is None
            or self.change_summary is None
        ):
            raise ValueError("修订计划必须绑定 parent_plan_ref、change_reason_code 和 change_summary")

    def _validate_task_graph(self) -> None:
        slice_ids = [item.slice_id for item in self.task_dag]
        _require_unique(slice_ids, "task_dag 中的 slice_id 不能重复")
        known_slices = set(slice_ids)
        acceptance_ids = {item.fact_id for item in self.goal.acceptance_facts}
        covered_acceptance: set[str] = set()
        dependencies: dict[str, list[str]] = {}

        for item in self.task_dag:
            unknown_dependencies = set(item.dependencies).difference(known_slices)
            if unknown_dependencies:
                raise ValueError("dependencies 只能引用当前 task_dag 中的 slice")
            unknown_acceptance = set(item.acceptance_refs).difference(acceptance_ids)
            if unknown_acceptance:
                raise ValueError("acceptance_refs 只能引用当前 goal 中的验收事实")
            covered_acceptance.update(item.acceptance_refs)
            dependencies[item.slice_id] = list(item.dependencies)

        if covered_acceptance != acceptance_ids:
            raise ValueError("每个 acceptance fact 必须至少由一个 slice 覆盖")
        if _contains_cycle(dependencies):
            raise ValueError("task_dag 不能包含依赖环")


class BudgetEligibilityLimits(BaseModel):
    """由预注册实验或项目策略提供，避免把阈值藏在 Router 代码里。"""

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


class DelegationValidationContext(BaseModel):
    """来自 task / policy compiler 的当前事实，不接受 PlanContract 自报。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    task_id: TaskId
    task_ref: ArtifactReference
    baseline: DelegationSnapshot
    allowed_read_paths: list[str] = Field(min_length=1, max_length=4096)
    allowed_write_paths: list[str] = Field(min_length=1, max_length=4096)
    allowed_verification_commands: list[CommandText] = Field(min_length=1, max_length=128)
    available_artifacts: list[ArtifactReference] = Field(max_length=512)
    budget_limits: BudgetEligibilityLimits

    @field_validator("allowed_read_paths", "allowed_write_paths")
    @classmethod
    def validate_allowed_paths(cls, values: list[str]) -> list[str]:
        normalized = [
            _validate_repo_relative_path(value, "allowed_paths") for value in values
        ]
        _require_unique(normalized, "allowed_paths 不能重复")
        return normalized

    @field_validator("allowed_verification_commands")
    @classmethod
    def validate_allowed_commands(cls, values: list[str]) -> list[str]:
        normalized = [_validate_command(value) for value in values]
        _require_unique(normalized, "allowed_verification_commands 不能重复")
        return normalized

    @field_validator("available_artifacts")
    @classmethod
    def validate_available_artifacts(
        cls,
        values: list[ArtifactReference],
    ) -> list[ArtifactReference]:
        _require_unique(
            [item.relative_path for item in values],
            "available_artifacts 不能对同一路径声明多个版本",
        )
        return values


class DelegationReadinessResult(BaseModel):
    """确定性 route evidence；同一输入与 context 必须得到同一结果。"""

    model_config = _STRICT_MODEL

    schema_version: Literal[1]
    status: RouteEligibility
    task_id: TaskId
    plan_id: PlanId | None
    plan_revision: int | None = Field(default=None, ge=1, le=10_000)
    contract_valid: bool
    binding_valid: bool
    input_sha256: Sha256
    plan_sha256: Sha256 | None
    context_sha256: Sha256
    checked_slice_ids: list[SliceId] = Field(max_length=64)
    issue_codes: list[IssueCode] = Field(max_length=256)

    @model_validator(mode="after")
    def validate_status_consistency(self) -> DelegationReadinessResult:
        if self.status == "budget_eligible" and (
            not self.contract_valid or not self.binding_valid or self.issue_codes
        ):
            raise ValueError("budget_eligible 不能携带无效合同、错绑或 issue")
        if self.status == "premium_required" and (
            not self.contract_valid or not self.binding_valid or not self.issue_codes
        ):
            raise ValueError("premium_required 必须来自有效合同上的明确升级原因")
        if (not self.contract_valid or not self.binding_valid) and self.status != "human_required":
            raise ValueError("合同无效或绑定失败时必须 human_required")
        return self


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
    binding_issues: list[str] = []
    readiness_issues: list[str] = []
    premium_reasons: list[str] = []

    if plan.task_id != expected.task_id:
        binding_issues.append("task_identity_mismatch")
    if plan.task_ref != expected.task_ref:
        binding_issues.append("task_artifact_mismatch")
    _compare_snapshot(plan.baseline, expected.baseline, binding_issues)

    allowed_read_paths = set(expected.allowed_read_paths)
    allowed_write_paths = set(expected.allowed_write_paths)
    allowed_commands = set(expected.allowed_verification_commands)
    available_artifacts = {
        item.relative_path: item.sha256 for item in expected.available_artifacts
    }
    write_path_owners: dict[str, list[str]] = {}

    if plan.parent_plan_ref is not None and not _artifact_available(
        plan.parent_plan_ref,
        available_artifacts,
    ):
        binding_issues.append("parent_plan_artifact_unavailable")

    for task_slice in plan.task_dag:
        for path in task_slice.read_paths:
            if path not in allowed_read_paths:
                binding_issues.append(
                    f"read_path_outside_compiled_scope:{task_slice.slice_id}"
                )
        for path in task_slice.allowed_write_paths:
            write_path_owners.setdefault(path, []).append(task_slice.slice_id)
            if path not in allowed_write_paths:
                binding_issues.append(
                    f"write_path_outside_compiled_scope:{task_slice.slice_id}"
                )
        for command in task_slice.verification.commands:
            if command not in allowed_commands:
                binding_issues.append(
                    f"verification_command_not_authorized:{task_slice.slice_id}"
                )
        for artifact_ref in task_slice.input_artifact_refs:
            if not _artifact_available(artifact_ref, available_artifacts):
                binding_issues.append(
                    f"input_artifact_unavailable:{task_slice.slice_id}"
                )

    if plan.decisions.unresolved:
        readiness_issues.append("unresolved_decisions")
    if plan.risk.human_required:
        readiness_issues.append("risk_requires_human")

    limits = expected.budget_limits
    dependency_edges = sum(len(item.dependencies) for item in plan.task_dag)
    write_path_count = len(write_path_owners)
    if plan.risk.premium_worker_required:
        premium_reasons.append("risk_requires_premium")
    if len(plan.task_dag) > limits.max_slices:
        premium_reasons.append("slice_count_exceeds_budget_limit")
    if dependency_edges > limits.max_dependency_edges:
        premium_reasons.append("dependency_count_exceeds_budget_limit")
    if write_path_count > limits.max_write_paths:
        premium_reasons.append("write_path_count_exceeds_budget_limit")
    if any(len(owners) > 1 for owners in write_path_owners.values()):
        premium_reasons.append("write_path_shared_across_slices")
    _compare_budget(plan.budget, limits, premium_reasons)

    plan_sha256 = _sha256_json(plan.model_dump(mode="json"))
    context_sha256 = _sha256_json(expected.model_dump(mode="json"))
    blocking_issues = _sorted_unique(binding_issues + readiness_issues)
    if blocking_issues:
        status: RouteEligibility = "human_required"
        issue_codes = blocking_issues
    elif premium_reasons:
        status = "premium_required"
        issue_codes = _sorted_unique(premium_reasons)
    else:
        status = "budget_eligible"
        issue_codes = []

    return DelegationReadinessResult(
        schema_version=DELEGATION_SCHEMA_VERSION,
        status=status,
        task_id=expected.task_id,
        plan_id=plan.plan_id,
        plan_revision=plan.plan_revision,
        contract_valid=True,
        binding_valid=not binding_issues,
        input_sha256=input_sha256,
        plan_sha256=plan_sha256,
        context_sha256=context_sha256,
        checked_slice_ids=sorted(item.slice_id for item in plan.task_dag),
        issue_codes=issue_codes,
    )


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


def _contains_cycle(dependencies: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(slice_id: str) -> bool:
        if slice_id in visiting:
            return True
        if slice_id in visited:
            return False
        visiting.add(slice_id)
        for dependency in dependencies[slice_id]:
            if visit(dependency):
                return True
        visiting.remove(slice_id)
        visited.add(slice_id)
        return False

    return any(visit(slice_id) for slice_id in dependencies)


def _read_bounded_bytes(path: Path, maximum_bytes: int) -> bytes:
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except (OSError, ValueError) as exc:
        raise _DelegationInputReadError("artifact_unreadable") from exc
    if len(payload) > maximum_bytes:
        raise _DelegationInputReadError("artifact_too_large")
    return payload


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


def _validate_command(value: str) -> str:
    if value != value.strip():
        raise ValueError("verification command 不能包含首尾空白")
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


def _require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


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
