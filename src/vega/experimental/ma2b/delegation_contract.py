from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .delegation_validation import (
    contains_cycle,
    require_unique,
    validate_command,
    validate_repo_relative_path,
)


_STRICT_MODEL = ConfigDict(extra="forbid", strict=True)

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
        return validate_repo_relative_path(value, "relative_path")


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
        require_unique(
            [item.fact_id for item in self.acceptance_facts],
            "acceptance_facts 中的 fact_id 不能重复",
        )
        require_unique(self.non_goals, "non_goals 不能重复")
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
        normalized = [validate_command(value) for value in values]
        require_unique(normalized, "verification commands 不能重复")
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
        normalized = [validate_repo_relative_path(value, "slice path") for value in values]
        require_unique(normalized, "slice path 不能重复")
        return normalized

    @field_validator("dependencies", "acceptance_refs")
    @classmethod
    def validate_unique_references(cls, values: list[str]) -> list[str]:
        require_unique(values, "slice reference 不能重复")
        return values

    @field_validator("preconditions")
    @classmethod
    def validate_unique_preconditions(cls, values: list[str]) -> list[str]:
        require_unique(values, "preconditions 不能重复")
        return values

    @field_validator("input_artifact_refs")
    @classmethod
    def validate_unique_input_artifacts(
        cls,
        values: list[ArtifactReference],
    ) -> list[ArtifactReference]:
        require_unique(
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
        require_unique(self.resolved, "resolved decisions 不能重复")
        require_unique(self.unresolved, "unresolved decisions 不能重复")
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
        require_unique(values, "threat_refs 不能重复")
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
        require_unique(values, "invalidated_slice_ids 不能重复")
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
        require_unique(slice_ids, "task_dag 中的 slice_id 不能重复")
        known_slices = set(slice_ids)
        acceptance_ids = {item.fact_id for item in self.goal.acceptance_facts}
        covered_acceptance: set[str] = set()
        dependencies: dict[str, list[str]] = {}

        for item in self.task_dag:
            if set(item.dependencies).difference(known_slices):
                raise ValueError("dependencies 只能引用当前 task_dag 中的 slice")
            if set(item.acceptance_refs).difference(acceptance_ids):
                raise ValueError("acceptance_refs 只能引用当前 goal 中的验收事实")
            covered_acceptance.update(item.acceptance_refs)
            dependencies[item.slice_id] = list(item.dependencies)

        if covered_acceptance != acceptance_ids:
            raise ValueError("每个 acceptance fact 必须至少由一个 slice 覆盖")
        if contains_cycle(dependencies):
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
        normalized = [validate_repo_relative_path(value, "allowed_paths") for value in values]
        require_unique(normalized, "allowed_paths 不能重复")
        return normalized

    @field_validator("allowed_verification_commands")
    @classmethod
    def validate_allowed_commands(cls, values: list[str]) -> list[str]:
        normalized = [validate_command(value) for value in values]
        require_unique(normalized, "allowed_verification_commands 不能重复")
        return normalized

    @field_validator("available_artifacts")
    @classmethod
    def validate_available_artifacts(
        cls,
        values: list[ArtifactReference],
    ) -> list[ArtifactReference]:
        require_unique(
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
