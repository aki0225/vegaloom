from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


AGENT_SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

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
AgentAction = Literal["next", "repair", "replan", "human", "finalize"]
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

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RelativePathText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_digest(value: BaseModel | dict[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_schema_version(value: int) -> int:
    if value != AGENT_SCHEMA_VERSION:
        raise ValueError(
            f"不支持的 Agent schema_version：{value}；"
            f"当前仅支持 {AGENT_SCHEMA_VERSION}"
        )
    return value


def _normalize_repo_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"路径必须是仓库相对路径：{value}")
    return candidate.as_posix()


def _normalize_relative_paths(values: list[str]) -> list[str]:
    normalized = [_normalize_repo_relative_path(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError("仓库相对路径不能重复")
    return normalized


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
    work_items: list[AgentWorkItem] = Field(min_length=1, max_length=4)
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
        return self.model_dump(
            mode="json",
            exclude={"approved", "approved_at", "approved_by", "approved_digest"},
        )

    def expected_approval_digest(self) -> str:
        return canonical_digest(self.content_for_approval())

    def approval_is_current(self) -> bool:
        return (
            self.approved
            and self.approved_digest is not None
            and self.approved_digest == self.expected_approval_digest()
        )


class AgentObservation(StrictAgentModel):
    observation_id: NonEmptyText
    work_item_id: NonEmptyText | None = None
    worker_claim: NonEmptyText | None = None
    machine_summary: NonEmptyText
    workspace_fingerprint: Sha256Text
    changed_files: list[RelativePathText] = Field(default_factory=list)
    evidence_refs: list[RelativePathText] = Field(default_factory=list)
    worker_alive: bool = False
    workspace_explained: bool = True
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

    @model_validator(mode="after")
    def validate_observation(self) -> AgentObservation:
        if self.worker_alive and self.all_work_items_completed:
            raise ValueError("Worker 仍存活时不能声明全部 Work Item 已完成")
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


class AgentCheckpoint(StrictAgentModel):
    checkpoint_id: NonEmptyText
    run_id: NonEmptyText
    state_version: int = Field(ge=1)
    reason: NonEmptyText
    status: CheckpointStatus
    phase: AgentPhase
    current_work_item: NonEmptyText | None = None
    active_child_run: NonEmptyText | None = None
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


class AgentState(StrictAgentModel):
    run_id: NonEmptyText
    task_id: NonEmptyText
    repository_id: NonEmptyText
    phase: AgentPhase = "planning"
    state_version: int = Field(default=1, ge=1)
    goal_revision: int = Field(default=1, ge=1)
    plan_revision: int = Field(default=1, ge=1)
    approved_plan_digest: Sha256Text | None = None
    current_work_item: NonEmptyText | None = None
    active_child_run: NonEmptyText | None = None
    active_operation_id: NonEmptyText | None = None
    workspace_fingerprint: Sha256Text | None = None
    latest_checkpoint_id: NonEmptyText | None = None
    allowed_actions: list[AgentAction] = Field(default_factory=list)
    handoff_status: Literal["none", "handoff_ready", "handoff_blocked"] = "none"
    terminal_status: Literal["ready_to_commit", "request_changes", "needs_human"] | None = None
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_phase_bindings(self) -> AgentState:
        if self.phase == "acting" and not self.active_child_run:
            raise ValueError("acting 阶段必须绑定 active_child_run")
        if self.phase == "completed" and self.terminal_status is None:
            raise ValueError("completed 阶段必须包含 terminal_status")
        if self.phase != "completed" and self.terminal_status is not None:
            raise ValueError("只有 completed 阶段可以包含 terminal_status")
        return self


def approve_plan(plan: AgentPlan, *, actor: str, approved_at: str | None = None) -> AgentPlan:
    if not actor.strip():
        raise ValueError("批准人不能为空")
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
