from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryKind = Literal[
    "confirmed_fact",
    "constraint_interpretation",
    "failed_attempt",
    "open_hypothesis",
]
MemoryStatus = Literal["candidate", "active", "invalidated", "superseded", "rejected"]
MemoryAuthority = Literal["verified", "inferred", "untrusted"]
MemorySourceType = Literal["verification", "worker", "reviewer", "tool"]
RiskLevel = Literal["low", "medium", "high"]
EventOperation = Literal["add", "update", "invalidate"]
Decision = Literal["allow", "remind", "block", "escalate"]
ApplicabilityStatus = Literal["applicable", "unknown"]
ReasonCode = Literal[
    "repeats_failed_attempt",
    "violates_constraint",
    "pending_approval_conflict",
    "superseded_goal",
    "conflicting_candidates",
    "applicability_unknown",
    "evidence_stale",
    "session_resume_risk",
    "none",
]

UPDATE_ALLOWED_FIELDS = frozenset({"evidence_refs", "risk", "applicability", "updated_seq"})
INVALIDATE_ALLOWED_FIELDS = frozenset(
    {"status", "invalidation_reason", "replacement_id", "updated_seq"}
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    artifact: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("artifact")
    @classmethod
    def validate_artifact(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError("evidence artifact 必须是实验目录内的相对路径")
        return normalized


class RunMemoryItem(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    repo_identity: str = Field(min_length=1)
    kind: MemoryKind
    statement: str = Field(min_length=1)
    status: MemoryStatus
    source_type: MemorySourceType
    source_ref: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    authority: MemoryAuthority
    risk: RiskLevel
    applicability: dict[str, str] = Field(default_factory=dict)
    created_seq: int = Field(ge=1)
    updated_seq: int = Field(ge=1)
    replacement_id: str | None = None
    invalidation_reason: str | None = None
    conflict_group: str | None = None

    @field_validator("statement")
    @classmethod
    def normalize_statement(cls, value: str) -> str:
        statement = " ".join(value.split())
        if not statement:
            raise ValueError("statement 不能为空")
        return statement

    @field_validator("applicability")
    @classmethod
    def validate_applicability(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, item in value.items():
            clean_key = key.strip()
            clean_value = item.strip()
            if not clean_key or not clean_value:
                raise ValueError("applicability 的键和值不能为空")
            normalized[clean_key] = clean_value
        return normalized

    @model_validator(mode="after")
    def validate_authority_and_lifecycle(self) -> RunMemoryItem:
        if self.updated_seq < self.created_seq:
            raise ValueError("updated_seq 不能早于 created_seq")

        expected_authority = {
            "verification": "verified",
            "worker": "inferred",
            "reviewer": "inferred",
            "tool": "untrusted",
        }[self.source_type]
        if self.authority != expected_authority:
            raise ValueError("source_type 与 authority 不匹配，禁止推断自动晋升")
        if self.authority == "verified" and not self.evidence_refs:
            raise ValueError("verified item 必须绑定 evidence_refs")
        if self.authority != "verified" and self.status == "active":
            raise ValueError("inferred/untrusted item 只能保持 candidate，不能自动进入 active")

        terminal = self.status in {"invalidated", "superseded", "rejected"}
        if terminal and not self.invalidation_reason:
            raise ValueError("终态 item 必须记录 invalidation_reason")
        if self.status == "superseded" and not self.replacement_id:
            raise ValueError("superseded item 必须记录 replacement_id")
        if not terminal and (self.replacement_id or self.invalidation_reason):
            raise ValueError("非终态 item 不应携带失效字段")
        return self


class MemoryEvent(StrictModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    repo_identity: str = Field(min_length=1)
    op: EventOperation
    memory_id: str = Field(min_length=1)
    item: RunMemoryItem | None = None
    patch: dict[str, Any] = Field(default_factory=dict)
    source_type: MemorySourceType
    source_ref: str = Field(min_length=1)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @model_validator(mode="after")
    def validate_operation(self) -> MemoryEvent:
        if self.op == "add":
            if self.item is None or self.patch:
                raise ValueError("add 必须提供 item，且不能同时提供 patch")
            if (
                self.item.id != self.memory_id
                or self.item.task_id != self.task_id
                or self.item.run_id != self.run_id
                or self.item.repo_identity != self.repo_identity
            ):
                raise ValueError("add event 与 item 的身份绑定不一致")
            if self.item.created_seq != self.seq or self.item.updated_seq != self.seq:
                raise ValueError("add item 的 created_seq/updated_seq 必须等于 event.seq")
        elif self.op == "update":
            if self.item is not None or not self.patch:
                raise ValueError("update 只能提供非空 patch")
            unexpected = set(self.patch) - UPDATE_ALLOWED_FIELDS
            if unexpected:
                raise ValueError(
                    "update 不能静默重写语义字段：" + ", ".join(sorted(unexpected))
                )
            if self.patch.get("updated_seq") != self.seq:
                raise ValueError("update.patch.updated_seq 必须等于 event.seq")
        else:
            if self.item is not None or not self.patch:
                raise ValueError("invalidate 只能提供非空 patch")
            unexpected = set(self.patch) - INVALIDATE_ALLOWED_FIELDS
            if unexpected:
                raise ValueError(
                    "invalidate 包含未允许字段：" + ", ".join(sorted(unexpected))
                )
            if self.patch.get("status") not in {"invalidated", "superseded", "rejected"}:
                raise ValueError("invalidate 必须写入合法终态")
            if not self.patch.get("invalidation_reason"):
                raise ValueError("invalidate 必须记录原因")
            if self.patch.get("status") == "superseded" and not self.patch.get(
                "replacement_id"
            ):
                raise ValueError("superseded 必须记录 replacement_id")
            if self.patch.get("updated_seq") != self.seq:
                raise ValueError("invalidate.patch.updated_seq 必须等于 event.seq")
        return self

    def assert_can_mutate(self, item: RunMemoryItem) -> None:
        """低权限来源不能改写 verified item 的决策语义或生命周期。"""
        if self.op == "add":
            return
        if self.source_type == "verification":
            return
        if item.authority == "verified":
            raise ValueError(
                f"{self.source_type} event 不能修改 verified memory：{item.id}"
            )


class MemoryConflict(StrictModel):
    conflict_key: str
    item_ids: list[str] = Field(min_length=2)
    kind: MemoryKind
    applicability: dict[str, str]


class MemorySnapshot(StrictModel):
    schema_version: Literal[1] = 1
    task_id: str
    run_id: str
    repo_identity: str
    source_event_count: int = Field(ge=0)
    source_events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_items: list[RunMemoryItem] = Field(default_factory=list)
    candidate_items: list[RunMemoryItem] = Field(default_factory=list)
    invalidated_items: list[RunMemoryItem] = Field(default_factory=list)
    conflicts: list[MemoryConflict] = Field(default_factory=list)


class InterventionCandidate(StrictModel):
    candidate_id: str
    source_layer: Literal["canonical_state", "run_memory"]
    source_ref: str
    kind: Literal[
        "confirmed_fact",
        "constraint_interpretation",
        "failed_attempt",
        "open_hypothesis",
        "pending_approval",
        "superseded_goal",
        "current_constraint",
    ]
    statement: str
    authority: Literal["authoritative", "verified"]
    risk: RiskLevel
    applicable: bool = True
    applicability_status: ApplicabilityStatus = "applicable"
    applicability: dict[str, str] = Field(default_factory=dict)
    conflict_group: str | None = None

    @model_validator(mode="after")
    def validate_source_authority(self) -> InterventionCandidate:
        expected = "authoritative" if self.source_layer == "canonical_state" else "verified"
        if self.authority != expected:
            raise ValueError("candidate 的 source_layer 与 authority 不匹配")
        if not self.applicable and self.applicability_status != "applicable":
            raise ValueError("显式不适用的 candidate 不应同时标记 applicability unknown")
        return self


class PlannedAction(StrictModel):
    checkpoint_id: str
    action: str
    summary: str
    context: dict[str, str] = Field(default_factory=dict)
    session_resumed: bool = False


class ReminderDecision(StrictModel):
    checkpoint_id: str
    decision: Decision
    reason_code: ReasonCode
    risk: RiskLevel
    candidate_ids: list[str] = Field(default_factory=list)
    reminder: str = ""
    dedupe_key: str = ""
    suppressed_by_dedupe: bool = False
    decision_source: Literal["deterministic_rule"] = "deterministic_rule"


class GoldenLabel(StrictModel):
    checkpoint_id: str
    expected_decision: Decision
    expected_reason_code: ReasonCode
    expected_candidate_ids: list[str] = Field(default_factory=list)
    is_high_risk: bool = False
    expected_suppressed: bool = False
    expected_next_action: str
    authority_rule: str
    derivation: str


class OfflineCheckpoint(StrictModel):
    checkpoint_id: str
    event_seq: int = Field(ge=0)
    planned_action: PlannedAction
    canonical_candidates: list[InterventionCandidate] = Field(default_factory=list)
    rebuild_snapshot: bool = False

    @model_validator(mode="after")
    def validate_checkpoint_identity(self) -> OfflineCheckpoint:
        if self.planned_action.checkpoint_id != self.checkpoint_id:
            raise ValueError("checkpoint 与 planned_action 身份不一致")
        if any(item.source_layer != "canonical_state" for item in self.canonical_candidates):
            raise ValueError("canonical_candidates 只能来自 canonical_state")
        return self


class OfflineCase(StrictModel):
    schema_version: Literal[1] = 1
    case_id: str
    task_id: str
    run_id: str
    repo_identity: str
    evidence_hashes: dict[str, str] = Field(default_factory=dict)
    events: list[MemoryEvent] = Field(default_factory=list)
    checkpoints: list[OfflineCheckpoint] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bindings(self) -> OfflineCase:
        for event in self.events:
            if (
                event.task_id != self.task_id
                or event.run_id != self.run_id
                or event.repo_identity != self.repo_identity
            ):
                raise ValueError("case 与 event 的 repo/run/task 绑定不一致")
        max_seq = len(self.events)
        if any(checkpoint.event_seq > max_seq for checkpoint in self.checkpoints):
            raise ValueError("checkpoint.event_seq 超过事件数量")
        return self


def canonical_json_sha256(value: Any) -> str:
    """对 JSON-like 值生成稳定哈希，避免格式化差异影响 replay 结果。"""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
