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

from .agent_contract_support import (
    AGENT_SCHEMA_VERSION,
    canonical_digest,
    normalize_relative_paths as _normalize_relative_paths,
    normalize_repo_relative_path as _normalize_repo_relative_path,
    utc_now,
    validate_schema_version as _validate_schema_version,
)
from .agent_change_state import validate_change_state_bindings


AgentPhase = Literal[
    "planning",
    "awaiting_approval",
    "ready",
    "acting",
    "observing",
    "needs_human",
    "finalizing",
    "completed",
    "stopped",
]
AgentRunKind = Literal["legacy", "change"]
AgentAction = Literal["next", "repair", "replan", "human", "finalize"]
ObservationAuthority = Literal[
    "external_claim",
    "fake_worker",
    "machine_reconcile",
]
WorkItemStatus = Literal[
    "pending",
    "active",
    "completed",
    "failed",
    "blocked",
    "superseded",
]
CheckpointStatus = Literal["safe", "uncertain", "blocked"]
GateStatus = Literal["not_run", "passed", "failed", "blocked", "stale"]
TerminalStatus = Literal["ready_to_commit", "request_changes", "needs_human"]
SupervisorEvidenceStatus = Literal["passed", "failed", "stale", "unverified"]

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RelativePathText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitOidText = Annotated[str, StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
ArtifactIdText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
class StrictAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: int = AGENT_SCHEMA_VERSION

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: int) -> int:
        return _validate_schema_version(value)


class AgentWorkItem(StrictAgentModel):
    work_item_id: NonEmptyText
    objective: NonEmptyText
    allowed_paths: list[RelativePathText] = Field(default_factory=list)
    forbidden_paths: list[RelativePathText] = Field(default_factory=list)
    verification: list[NonEmptyText] = Field(default_factory=list)
    # 这是批准计划对任务与验证命令的显式声明；Vega 不会仅凭命令退出码猜测外部副作用。
    external_side_effects: Literal["none", "known", "unknown"] = "unknown"
    risk_notes: list[NonEmptyText] = Field(default_factory=list)
    depends_on: list[NonEmptyText] = Field(default_factory=list)
    status: WorkItemStatus = "pending"

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return _normalize_relative_paths(values)

    @model_validator(mode="after")
    def validate_path_sets(self) -> AgentWorkItem:
        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            raise ValueError(f"允许路径与禁止路径冲突：{sorted(overlap)}")
        return self


class AgentPlan(StrictAgentModel):
    task_id: NonEmptyText
    goal_revision: int = Field(default=1, ge=1)
    plan_revision: int = Field(default=1, ge=1)
    user_goal: NonEmptyText
    non_goals: list[NonEmptyText] = Field(default_factory=list)
    success_conditions: list[NonEmptyText] = Field(default_factory=list)
    observed_facts: list[NonEmptyText] = Field(default_factory=list)
    hypotheses: list[NonEmptyText] = Field(default_factory=list)
    unresolved_decisions: list[NonEmptyText] = Field(default_factory=list)
    work_items: list[AgentWorkItem] = Field(min_length=1, max_length=8)
    approved: bool = False
    approved_at: str | None = None
    approved_by: str | None = None
    approved_digest: Sha256Text | None = None

    @model_validator(mode="after")
    def validate_approval(self) -> AgentPlan:
        work_item_ids = [item.work_item_id for item in self.work_items]
        if len(set(work_item_ids)) != len(work_item_ids):
            raise ValueError("work_item_id 不能重复")
        known_ids = set(work_item_ids)
        for item in self.work_items:
            unknown_dependencies = set(item.depends_on) - known_ids
            if unknown_dependencies:
                raise ValueError(
                    f"{item.work_item_id} 引用了未知依赖：{sorted(unknown_dependencies)}"
                )
            if item.work_item_id in item.depends_on:
                raise ValueError(f"{item.work_item_id} 不能依赖自身")

        approval_fields = (self.approved_at, self.approved_by, self.approved_digest)
        if self.approved and any(value is None for value in approval_fields):
            raise ValueError("已批准计划必须包含批准时间、批准人和批准摘要")
        if not self.approved and any(value is not None for value in approval_fields):
            raise ValueError("未批准计划不能包含批准记录")
        return self

    def content_for_approval(self) -> dict[str, object]:
        payload = self.model_dump(
            mode="json",
            exclude={"approved", "approved_at", "approved_by", "approved_digest"},
        )
        # Work Item 进度是运行状态，不属于人工批准的计划内容；推进状态不能让批准自行失效。
        payload["work_items"] = [
            item.model_dump(mode="json", exclude={"status"})
            for item in self.work_items
        ]
        return payload

    def expected_approval_digest(self) -> str:
        return canonical_digest(self.content_for_approval())

    def approval_is_current(self) -> bool:
        return (
            self.approved
            and self.approved_digest is not None
            and self.approved_digest == self.expected_approval_digest()
        )


def validate_v1_execution_plan(plan: AgentPlan) -> AgentWorkItem:
    """校验当前 V1 在人工批准和真实执行前必须冻结的最小范围。"""

    unrestricted = [
        path
        for item in plan.work_items
        for path in item.allowed_paths
        if _scope_pattern_covers_repository(path)
    ]
    if unrestricted:
        raise ValueError(
            "Supervisor Agent V1 不接受覆盖整个仓库的允许路径："
            f"{sorted(dict.fromkeys(unrestricted))}"
        )
    executable = [
        item
        for item in plan.work_items
        if item.status not in {"completed", "superseded"}
    ]
    if len(executable) != 1:
        raise ValueError("Supervisor Agent V1 当前只接受一个未完成 Work Item")
    work_item = executable[0]
    if not work_item.allowed_paths:
        raise ValueError("Supervisor Agent V1 Work Item 必须声明至少一个允许路径")
    return work_item


def _scope_pattern_covers_repository(pattern: str) -> bool:
    segments = pattern.split("/")
    fixed_width = [segment for segment in segments if segment != "**"]
    if "**" not in segments or len(fixed_width) > 1:
        return False
    return not fixed_width or _segment_matches_every_nonempty_name(fixed_width[0])


def _segment_matches_every_nonempty_name(pattern: str) -> bool:
    return "*" in pattern and all(character in {"*", "?"} for character in pattern) and (
        pattern.count("?") <= 1
    )


def validate_v1_execution_binding(
    plan: AgentPlan,
    current_work_item: str | None,
) -> AgentWorkItem:
    """确认 V1 唯一可执行项与持久化 State 指向同一对象。"""

    work_item = validate_v1_execution_plan(plan)
    if work_item.work_item_id != current_work_item:
        raise ValueError("当前可执行 Work Item 与 Agent State 不一致")
    return work_item


class AgentObservation(StrictAgentModel):
    observation_id: ArtifactIdText
    work_item_id: NonEmptyText | None = None
    child_run: NonEmptyText | None = None
    operation_id: NonEmptyText | None = None
    worker_claim: NonEmptyText | None = None
    machine_summary: NonEmptyText
    workspace_fingerprint: Sha256Text
    changed_files: list[RelativePathText] = Field(default_factory=list)
    evidence_refs: list[RelativePathText] = Field(default_factory=list)
    evidence_sha256: dict[str, Sha256Text] = Field(default_factory=dict)
    authority: ObservationAuthority = "external_claim"
    work_item_completed: bool = False
    worker_alive: bool = False
    # 该值只接受持久化状态或受信机器对账；外部 Claim 会在 Runtime 内被覆盖。
    operation_started: bool = True
    workspace_explained: bool = True
    unknown_file_count: int = Field(default=0, ge=0)
    external_side_effects: Literal["none", "known", "unknown"] = "none"
    plan_contradicted: bool = False
    repairable_in_scope: bool = False
    verification: GateStatus = "not_run"
    risk: GateStatus = "not_run"
    review: GateStatus = "not_run"
    all_work_items_completed: bool = False

    @field_validator("changed_files", "evidence_refs")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return _normalize_relative_paths(values)

    @field_validator("evidence_sha256")
    @classmethod
    def validate_evidence_sha256(
        cls,
        values: dict[str, str],
    ) -> dict[str, str]:
        normalized = {
            _normalize_repo_relative_path(path): digest
            for path, digest in values.items()
        }
        if len(normalized) != len(values):
            raise ValueError("evidence_sha256 路径不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_observation(self) -> AgentObservation:
        if (self.child_run is None) != (self.operation_id is None):
            raise ValueError("Observation 的 child_run 与 operation_id 必须同时存在或同时为空")
        if self.authority != "external_claim" and (
            self.work_item_id is None
            or self.child_run is None
            or self.operation_id is None
        ):
            raise ValueError("受信 Observation 必须绑定 Work Item、child 和 operation")
        if self.worker_alive and self.all_work_items_completed:
            raise ValueError("Worker 仍存活时不能声明全部 Work Item 已完成")
        if self.all_work_items_completed and not self.work_item_completed:
            raise ValueError("全部 Work Item 已完成时，当前 Work Item 也必须完成")
        if not self.operation_started and (
            self.work_item_completed or self.all_work_items_completed
        ):
            raise ValueError("operation 尚未开始时不能声明 Work Item 已完成")
        unknown_digests = set(self.evidence_sha256) - set(self.evidence_refs)
        if unknown_digests:
            raise ValueError(
                f"evidence_sha256 引用了未声明证据：{sorted(unknown_digests)}"
            )
        return self


class AgentDecision(StrictAgentModel):
    decision_id: NonEmptyText
    observation_id: NonEmptyText
    allowed_actions: list[AgentAction] = Field(min_length=1)
    selected_action: AgentAction
    reason: NonEmptyText
    source: Literal["deterministic", "supervisor", "human"]
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_selected_action(self) -> AgentDecision:
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions 不能重复")
        if self.selected_action not in self.allowed_actions:
            raise ValueError("selected_action 必须属于 allowed_actions")
        return self


class SupervisorEvidenceItem(StrictAgentModel):
    """状态卡中可追溯的 Supervisor 证据摘要。"""

    label: NonEmptyText
    status: SupervisorEvidenceStatus
    detail: NonEmptyText


class AgentCheckpoint(StrictAgentModel):
    checkpoint_id: NonEmptyText
    run_id: NonEmptyText
    state_version: int = Field(ge=1)
    reason: NonEmptyText
    status: CheckpointStatus
    phase: AgentPhase
    current_work_item: NonEmptyText | None = None
    active_child_run: NonEmptyText | None = None
    operation_started: bool = False
    external_side_effects: Literal["none", "known", "unknown"] = "none"
    workspace_fingerprint: Sha256Text
    changed_files: list[RelativePathText] = Field(default_factory=list)
    completed_attempts: list[NonEmptyText] = Field(default_factory=list)
    failed_attempts: list[NonEmptyText] = Field(default_factory=list)
    pending_actions: list[AgentAction] = Field(default_factory=list)
    evidence_refs: list[RelativePathText] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("changed_files", "evidence_refs")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return _normalize_relative_paths(values)

    @model_validator(mode="after")
    def validate_checkpoint_status(self) -> AgentCheckpoint:
        if self.phase == "stopped" and self.active_child_run is not None:
            raise ValueError("stopped Checkpoint 不能保留 active child")
        if (
            self.status == "safe"
            and not self.pending_actions
            and self.phase not in {"completed", "stopped"}
        ):
            raise ValueError("safe Checkpoint 必须声明后续允许动作")
        if self.phase == "completed" and self.pending_actions:
            raise ValueError("completed Checkpoint 不能保留后续动作")
        if self.status != "safe" and any(
            action in {"next", "repair", "finalize"} for action in self.pending_actions
        ):
            raise ValueError("uncertain/blocked Checkpoint 不能允许自动写入或 finalize")
        return self


class ProviderSessionStatus(StrictAgentModel):
    """主会话可见的本地 Provider Session 摘要。"""

    role: NonEmptyText
    provider: NonEmptyText
    owner: Literal["vega", "human"]
    lifecycle: Literal["new", "idle", "active", "waiting_user", "unavailable"]
    thread_id: NonEmptyText | None = None
    work_item_id: NonEmptyText | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access", "external"] | None = None
    approval_policy: NonEmptyText | None = None
    permissions_verified: bool = False
    turn_count: int = Field(default=0, ge=0)
    compaction_count: int = Field(default=0, ge=0)
    last_event: NonEmptyText | None = None
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=0)
    queued_steers: int = Field(default=0, ge=0)
    pending_interactions: int = Field(default=0, ge=0)


class AgentStatusCard(StrictAgentModel):
    run_id: NonEmptyText
    task_id: NonEmptyText
    phase: AgentPhase
    task_goal: NonEmptyText
    work_item_label: NonEmptyText
    worker_label: NonEmptyText
    live_child_stage: NonEmptyText | None = None
    changed_files: list[RelativePathText] = Field(default_factory=list)
    unknown_file_count: int = Field(default=0, ge=0)
    latest_checkpoint: NonEmptyText | None = None
    checkpoint_status: CheckpointStatus | None = None
    verification: GateStatus = "not_run"
    risk: GateStatus = "not_run"
    review: GateStatus = "not_run"
    terminal_status: TerminalStatus | None = None
    allowed_actions: list[AgentAction] = Field(default_factory=list)
    next_step: NonEmptyText
    evidence_health: Literal[
        "not_applicable",
        "passed",
        "failed",
        "stale",
        "unverified",
    ] = "not_applicable"
    workspace_current: bool | None = None
    commit_recommended: bool = False
    integrity_warning: NonEmptyText | None = None
    # 这些字段都是展示层可选信息，旧的调用方不需要补充即可继续构造状态卡。
    history_note: NonEmptyText | None = None
    plan_risk_notes: list[NonEmptyText] = Field(default_factory=list)
    supervisor_evidence: list[SupervisorEvidenceItem] = Field(default_factory=list)
    provider_sessions: list[ProviderSessionStatus] = Field(default_factory=list)
    provider_session_warning: NonEmptyText | None = None

    @field_validator("changed_files")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return _normalize_relative_paths(values)


class AgentState(StrictAgentModel):
    run_id: NonEmptyText
    task_id: NonEmptyText
    repository_id: NonEmptyText
    run_kind: AgentRunKind = "legacy"
    phase: AgentPhase = "planning"
    state_version: int = Field(default=1, ge=1)
    goal_revision: int = Field(default=1, ge=1)
    plan_revision: int = Field(default=1, ge=1)
    approved_plan_digest: Sha256Text | None = None
    contract_revision: int | None = Field(default=None, ge=1)
    approved_contract_digest: Sha256Text | None = None
    execution_plan_revision: int | None = Field(default=None, ge=1)
    accepted_checkpoint_sha: GitOidText | None = None
    active_candidate_sha: GitOidText | None = None
    current_work_item: NonEmptyText | None = None
    active_child_run: NonEmptyText | None = None
    active_operation_id: NonEmptyText | None = None
    # dispatch 落盘后即保守视为 operation 可能已开始，直到受信执行证据完成对账。
    operation_started: bool = False
    workspace_fingerprint: Sha256Text | None = None
    latest_checkpoint_id: NonEmptyText | None = None
    allowed_actions: list[AgentAction] = Field(default_factory=list)
    handoff_status: Literal["none", "handoff_ready", "handoff_blocked"] = "none"
    terminal_status: TerminalStatus | None = None
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_phase_bindings(self) -> AgentState:
        has_child = self.active_child_run is not None
        has_operation = self.active_operation_id is not None
        if has_child != has_operation:
            raise ValueError("active_child_run 与 active_operation_id 必须同时存在或同时为空")
        if self.phase == "acting" and not has_child:
            raise ValueError("acting 阶段必须绑定 active child 与 operation")
        if self.operation_started and not has_child:
            raise ValueError("没有 active Writer 时 operation_started 必须为 false")
        if self.phase in {"completed", "stopped"} and has_child:
            raise ValueError("终止阶段不能保留 active Writer")
        if self.phase == "completed" and self.terminal_status is None:
            raise ValueError("completed 阶段必须包含 terminal_status")
        if self.phase != "completed" and self.terminal_status is not None:
            raise ValueError("只有 completed 阶段可以包含 terminal_status")
        validate_change_state_bindings(self)
        return self


def approve_plan(plan: AgentPlan, *, actor: str, approved_at: str | None = None) -> AgentPlan:
    if not actor.strip():
        raise ValueError("批准人不能为空")
    validate_v1_execution_plan(plan)
    payload = plan.model_dump(mode="json")
    payload.update(
        {
            "approved": True,
            "approved_by": actor.strip(),
            "approved_at": approved_at or utc_now(),
            "approved_digest": canonical_digest(plan.content_for_approval()),
        }
    )
    # 批准字段必须作为一个原子记录写入，避免出现“已批准但摘要尚未写入”的中间状态。
    return AgentPlan.model_validate(payload)
