from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .redaction import redact_text


PARALLEL_REVIEW_SCHEMA_VERSION = 2
PARALLEL_REVIEW_POLICY_VERSION = "adaptive-review-v1"
PARALLEL_REVIEW_PROMPT_VERSION = "parallel-review-role-v1"
AVAILABLE_REVIEWER_ROLES = (
    "correctness_reviewer",
    "verification_adequacy_reviewer",
    "security_design_reviewer",
)

ReviewerRole: TypeAlias = Literal[
    "correctness_reviewer",
    "verification_adequacy_reviewer",
    "security_design_reviewer",
]
ReviewExecutionStatus: TypeAlias = Literal[
    "completed",
    "timed_out",
    "provider_error",
    "stopped",
    "active",
    "termination_unconfirmed",
    "parse_error",
]
ReviewVerdictValue: TypeAlias = Literal[
    "approve",
    "request_changes",
    "needs_human",
]
ReviewTopology: TypeAlias = Literal[
    "single",
    "fixed_three",
    "adaptive",
]


@dataclass(frozen=True)
class ParallelReviewAttemptIdentity:
    attempt_id: str
    step_id: str
    idempotency_key: str
    input_fingerprint: str
    public_evidence_sha256: str
    role_prompt_sha256: str
AggregateReasonCode: TypeAlias = Literal[
    "verification_failed",
    "verification_unresolved",
    "evidence_stale",
    "evidence_truncated",
    "evidence_hash_mismatch",
    "reviewer_result_set_incomplete",
    "reviewer_result_identity_mismatch",
    "reviewer_execution_unresolved",
    "major_findings",
    "high_risk_without_approval",
    "reviewer_needs_human",
    "reviewer_requested_changes",
]

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WORKSPACE_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_FINDING_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,119}$")
_LOCATION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/#-]{0,199}$")
_PLAN_REASON_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,199}$")
_SEVERITY_RANK = {
    "suggestion": 0,
    "minor": 1,
    "major": 2,
    "blocker": 3,
}
_REASON_ORDER: tuple[AggregateReasonCode, ...] = (
    "verification_failed",
    "verification_unresolved",
    "evidence_stale",
    "evidence_truncated",
    "evidence_hash_mismatch",
    "reviewer_result_set_incomplete",
    "reviewer_result_identity_mismatch",
    "reviewer_execution_unresolved",
    "major_findings",
    "high_risk_without_approval",
    "reviewer_needs_human",
    "reviewer_requested_changes",
)
_VERIFICATION_GATE_REASONS = frozenset({"missing_tests"})
_SECURITY_DESIGN_GATE_REASONS = frozenset(
    {
        "budget_changed_files",
        "budget_diff_lines",
        "budget_new_files",
        "deleted_files",
        "high_risk_paths",
        "large_generated_files",
        "many_files",
        "medium_risk_paths",
        "new_dependencies",
        "project_requires_human_review",
        "several_files",
    }
)
_PRE_REVIEW_DETERMINISTIC_BLOCKERS = frozenset(
    {
        "diff_check_failed",
        "no_diff",
        "project_config_invalid",
        "risk_evaluation_failed",
    }
)


class ReviewEvidenceSnapshot(BaseModel):
    """同一 ReviewPlan 下各 reviewer 共享的、内容寻址的证据身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = PARALLEL_REVIEW_SCHEMA_VERSION
    run_id: str
    iteration: int = Field(ge=1)
    workspace_fingerprint: str
    policy_snapshot_sha256: str
    verification_result_sha256: str
    risk_result_sha256: str
    acceptance_evidence_manifest_sha256: str
    evidence_snapshot_sha256: str

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_identifier(value, "run_id")

    @field_validator("workspace_fingerprint")
    @classmethod
    def validate_workspace_fingerprint(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _WORKSPACE_FINGERPRINT_PATTERN.fullmatch(normalized):
            raise ValueError("workspace_fingerprint 必须是 sha256:<64 hex>")
        return normalized

    @field_validator(
        "policy_snapshot_sha256",
        "verification_result_sha256",
        "risk_result_sha256",
        "acceptance_evidence_manifest_sha256",
        "evidence_snapshot_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> Self:
        expected = _sha256_json(_snapshot_identity_payload(self))
        if self.evidence_snapshot_sha256 != expected:
            raise ValueError("evidence_snapshot_sha256 与证据身份不一致")
        return self


class ParallelReviewRoutingContext(BaseModel):
    """只接受确定性机器事实的 Reviewer 路由输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    iteration: int = Field(ge=1)
    evidence_snapshot_sha256: str
    verification_status: Literal[
        "passed",
        "failed",
        "skipped",
        "timed_out",
        "stopped",
        "termination_unconfirmed",
    ]
    verification_failed_count: int = Field(ge=0)
    risk: Literal["low", "medium", "high"]
    changed_files: list[str] = Field(default_factory=list, max_length=500)
    gate_reason_codes: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_identifier(value, "run_id")

    @field_validator("evidence_snapshot_sha256")
    @classmethod
    def validate_snapshot_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @field_validator("changed_files")
    @classmethod
    def validate_changed_files(cls, value: list[str]) -> list[str]:
        normalized = sorted({_normalize_repo_path(path) for path in value})
        if len(normalized) != len(value):
            raise ValueError("changed_files 必须唯一并按仓库相对路径排序")
        return normalized

    @field_validator("gate_reason_codes")
    @classmethod
    def validate_gate_reason_codes(cls, value: list[str]) -> list[str]:
        normalized = sorted({_normalize_plan_reason(code) for code in value})
        if len(normalized) != len(value):
            raise ValueError("gate_reason_codes 必须唯一并按 code 排序")
        return normalized


class ParallelReviewPlan(BaseModel):
    """内容寻址的 Reviewer 执行计划；角色数量由确定性策略决定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = PARALLEL_REVIEW_SCHEMA_VERSION
    policy_version: Literal["adaptive-review-v1"] = (
        PARALLEL_REVIEW_POLICY_VERSION
    )
    plan_id: str
    topology: ReviewTopology
    run_id: str
    iteration: int = Field(ge=1)
    evidence_snapshot_sha256: str
    required_roles: list[ReviewerRole] = Field(
        default_factory=list,
        min_length=1,
        max_length=3,
    )
    role_reasons: dict[ReviewerRole, list[str]] = Field(default_factory=dict)
    max_parallelism: int = Field(ge=1, le=3)

    @field_validator("plan_id")
    @classmethod
    def validate_plan_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(value, "review-plan-", "plan_id")

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_identifier(value, "run_id")

    @field_validator("evidence_snapshot_sha256")
    @classmethod
    def validate_snapshot_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @field_validator("required_roles")
    @classmethod
    def validate_required_roles(
        cls,
        value: list[ReviewerRole],
    ) -> list[ReviewerRole]:
        expected = sorted(set(value), key=AVAILABLE_REVIEWER_ROLES.index)
        if value != expected:
            raise ValueError("required_roles 必须唯一并按固定角色顺序排列")
        return value

    @field_validator("role_reasons")
    @classmethod
    def validate_role_reasons(
        cls,
        value: dict[ReviewerRole, list[str]],
    ) -> dict[ReviewerRole, list[str]]:
        normalized: dict[ReviewerRole, list[str]] = {}
        for role in AVAILABLE_REVIEWER_ROLES:
            if role not in value:
                continue
            reasons = sorted(
                {_normalize_plan_reason(reason) for reason in value[role]}
            )
            if not reasons or len(reasons) != len(value[role]):
                raise ValueError("每个 reviewer role 必须有唯一且非空的触发原因")
            normalized[role] = reasons
        if value != normalized:
            raise ValueError("role_reasons 必须按固定角色和 reason 顺序排列")
        return value

    @model_validator(mode="after")
    def validate_plan_identity(self) -> Self:
        if set(self.role_reasons) != set(self.required_roles):
            raise ValueError("role_reasons 必须与 required_roles 精确对应")
        if self.max_parallelism > len(self.required_roles):
            raise ValueError("max_parallelism 不能超过 required_roles 数量")
        expected = _sha256_json(_review_plan_identity_payload(self))
        if self.plan_id != f"review-plan-{expected}":
            raise ValueError("plan_id 与 Reviewer 执行计划不一致")
        return self


class ParallelReviewFinding(BaseModel):
    """单路 reviewer 的完整 finding；自由文本不得进入 Graph State。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    severity: Literal["blocker", "major", "minor", "suggestion"] = "minor"
    category: str
    rule_id: str
    normalized_path: str
    normalized_location: str
    title: str
    evidence: str = ""
    recommendation: str = ""

    @field_validator("finding_id")
    @classmethod
    def validate_finding_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(value, "finding-", "finding_id")

    @field_validator("category", "rule_id")
    @classmethod
    def validate_finding_component(cls, value: str) -> str:
        normalized = _normalize_finding_component(value)
        if not _FINDING_COMPONENT_PATTERN.fullmatch(normalized):
            raise ValueError(
                "finding category/rule_id 只能包含小写字母、数字、点、下划线、冒号和连字符"
            )
        return normalized

    @field_validator("normalized_path")
    @classmethod
    def validate_normalized_path(cls, value: str) -> str:
        return _normalize_repo_path(value)

    @field_validator("normalized_location")
    @classmethod
    def validate_normalized_location(cls, value: str) -> str:
        normalized = _normalize_location(value)
        if not _LOCATION_PATTERN.fullmatch(normalized):
            raise ValueError(
                "normalized_location 只能包含小写字母、数字和结构化位置字符"
            )
        return normalized

    @field_validator("title", "evidence", "recommendation")
    @classmethod
    def sanitize_free_text(cls, value: str) -> str:
        return _sanitize_text(value, max_chars=4000)


class ParallelReviewResult(BaseModel):
    """单路 reviewer 的结构化结果和 execution 绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = PARALLEL_REVIEW_SCHEMA_VERSION
    result_id: str
    review_plan_id: str
    run_id: str
    iteration: int = Field(ge=1)
    reviewer_role: ReviewerRole
    attempt_id: str
    evidence_snapshot_sha256: str
    execution_ref: str
    execution_sha256: str
    status: ReviewExecutionStatus
    verdict: ReviewVerdictValue
    summary: str
    findings: list[ParallelReviewFinding] = Field(
        default_factory=list,
        max_length=200,
    )
    checked_items: list[str] = Field(default_factory=list)

    @field_validator("result_id")
    @classmethod
    def validate_result_id(cls, value: str) -> str:
        return _normalize_result_id(value)

    @field_validator("review_plan_id")
    @classmethod
    def validate_review_plan_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(
            value,
            "review-plan-",
            "review_plan_id",
        )

    @field_validator("run_id", "attempt_id")
    @classmethod
    def validate_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _normalize_identifier(value, info.field_name)

    @field_validator("evidence_snapshot_sha256", "execution_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @field_validator("execution_ref")
    @classmethod
    def validate_execution_ref(cls, value: str) -> str:
        return _normalize_relative_ref(value, "execution_ref")

    @field_validator("summary")
    @classmethod
    def sanitize_summary(cls, value: str) -> str:
        normalized = _sanitize_text(value, max_chars=2000)
        if not normalized:
            raise ValueError("summary 不能为空")
        return normalized

    @field_validator("checked_items")
    @classmethod
    def sanitize_checked_items(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            safe_item = _sanitize_text(item, max_chars=500)
            if safe_item:
                normalized.append(safe_item)
        if len(normalized) > 100:
            raise ValueError("checked_items 不能超过 100 项")
        return normalized

    @model_validator(mode="after")
    def validate_result_identity_and_status(self) -> Self:
        expected_result_id = _review_result_id(
            review_plan_id=self.review_plan_id,
            run_id=self.run_id,
            iteration=self.iteration,
            reviewer_role=self.reviewer_role,
            attempt_id=self.attempt_id,
            evidence_snapshot_sha256=self.evidence_snapshot_sha256,
            execution_sha256=self.execution_sha256,
        )
        if self.result_id != expected_result_id:
            raise ValueError("result_id 与 reviewer attempt 身份不一致")

        finding_ids: set[str] = set()
        for finding in self.findings:
            expected_finding_id = _finding_id(
                category=finding.category,
                rule_id=finding.rule_id,
                normalized_path=finding.normalized_path,
                normalized_location=finding.normalized_location,
                evidence_snapshot_sha256=self.evidence_snapshot_sha256,
            )
            if finding.finding_id != expected_finding_id:
                raise ValueError("finding_id 与当前 evidence snapshot 不一致")
            if finding.finding_id in finding_ids:
                raise ValueError("单路 reviewer 不能重复输出同一 finding identity")
            finding_ids.add(finding.finding_id)

        if self.status != "completed":
            if self.verdict != "needs_human":
                raise ValueError("非 completed reviewer 只能输出 needs_human")
            if self.findings:
                raise ValueError("非 completed reviewer 的 findings 不可信，必须为空")
        return self


class ParallelReviewResultRef(BaseModel):
    """Graph State 可保存的窄引用，不携带 reviewer 自由文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = PARALLEL_REVIEW_SCHEMA_VERSION
    result_id: str
    review_plan_id: str
    reviewer_role: ReviewerRole
    evidence_snapshot_sha256: str
    attempt_id: str
    artifact_ref: str
    artifact_sha256: str

    @field_validator("result_id")
    @classmethod
    def validate_result_id(cls, value: str) -> str:
        return _normalize_result_id(value)

    @field_validator("review_plan_id")
    @classmethod
    def validate_review_plan_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(
            value,
            "review-plan-",
            "review_plan_id",
        )

    @field_validator("attempt_id")
    @classmethod
    def validate_attempt_id(cls, value: str) -> str:
        return _normalize_identifier(value, "attempt_id")

    @field_validator("evidence_snapshot_sha256", "artifact_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @field_validator("artifact_ref")
    @classmethod
    def validate_artifact_ref(cls, value: str) -> str:
        return _normalize_relative_ref(value, "artifact_ref")


class ParallelReviewArtifactDigest(BaseModel):
    """Compatibility binding 中不携带业务内容的工件摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_ref: str
    artifact_sha256: str

    @field_validator("artifact_ref")
    @classmethod
    def validate_artifact_ref(cls, value: str) -> str:
        return _normalize_relative_ref(value, "artifact_ref")

    @field_validator("artifact_sha256")
    @classmethod
    def validate_artifact_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)


class ParallelReviewCompatibilityBinding(BaseModel):
    """旧 Review 视图回溯 Gate 5 权威来源所需的窄绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["parallel_review"] = "parallel_review"
    source_run: str
    iteration: int = Field(ge=1)
    review_plan_id: str
    evidence_snapshot_sha256: str
    plan_artifact: ParallelReviewArtifactDigest
    result_artifacts: list[ParallelReviewResultRef]
    result_pointer_artifacts: list[ParallelReviewArtifactDigest]
    aggregate_artifact: ParallelReviewArtifactDigest
    aggregate_sha256: str

    @field_validator("source_run")
    @classmethod
    def validate_source_run(cls, value: str) -> str:
        return _normalize_identifier(value, "source_run")

    @field_validator("review_plan_id")
    @classmethod
    def validate_review_plan_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(
            value,
            "review-plan-",
            "review_plan_id",
        )

    @field_validator("evidence_snapshot_sha256", "aggregate_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @model_validator(mode="after")
    def validate_result_binding(self) -> Self:
        roles = [item.reviewer_role for item in self.result_artifacts]
        expected_roles = sorted(set(roles), key=AVAILABLE_REVIEWER_ROLES.index)
        if not roles or roles != expected_roles:
            raise ValueError(
                "result_artifacts 必须非空、角色唯一并按固定顺序排列"
            )
        if len(self.result_pointer_artifacts) != len(self.result_artifacts):
            raise ValueError(
                "result pointer 必须与 result artifacts 数量一致"
            )
        for result_ref in self.result_artifacts:
            if (
                result_ref.review_plan_id != self.review_plan_id
                or result_ref.evidence_snapshot_sha256
                != self.evidence_snapshot_sha256
            ):
                raise ValueError(
                    "Compatibility result ref 未绑定当前 plan 或 snapshot"
                )
        return self


class ParallelReviewAggregationContext(BaseModel):
    """确定性 aggregator 所需的机器事实，不接受自然语言覆盖。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    iteration: int = Field(ge=1)
    evidence_snapshot_sha256: str
    review_plan: ParallelReviewPlan
    verification_status: Literal[
        "passed",
        "failed",
        "skipped",
        "timed_out",
        "stopped",
        "termination_unconfirmed",
    ]
    verification_failed_count: int = Field(ge=0)
    risk: Literal["low", "medium", "high"]
    human_approval_valid: bool = False
    evidence_fresh: bool = True
    evidence_truncated: bool = False
    evidence_hash_valid: bool = True

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_identifier(value, "run_id")

    @field_validator("evidence_snapshot_sha256")
    @classmethod
    def validate_snapshot_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @model_validator(mode="after")
    def validate_review_plan_binding(self) -> Self:
        if (
            self.review_plan.run_id != self.run_id
            or self.review_plan.iteration != self.iteration
            or self.review_plan.evidence_snapshot_sha256
            != self.evidence_snapshot_sha256
        ):
            raise ValueError("review_plan 与 aggregation context 身份不一致")
        return self


class AggregatedReviewFinding(BaseModel):
    """Aggregator 只保留稳定身份和来源，不传播 reviewer 私有自由文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    severity: Literal["blocker", "major", "minor", "suggestion"]
    category: str
    rule_id: str
    normalized_path: str
    normalized_location: str
    reviewer_roles: list[ReviewerRole]

    @field_validator("finding_id")
    @classmethod
    def validate_finding_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(value, "finding-", "finding_id")

    @field_validator("category", "rule_id")
    @classmethod
    def validate_finding_component(cls, value: str) -> str:
        normalized = _normalize_finding_component(value)
        if not _FINDING_COMPONENT_PATTERN.fullmatch(normalized):
            raise ValueError("聚合 finding category/rule_id 不合法")
        return normalized

    @field_validator("normalized_path")
    @classmethod
    def validate_normalized_path(cls, value: str) -> str:
        return _normalize_repo_path(value)

    @field_validator("normalized_location")
    @classmethod
    def validate_normalized_location(cls, value: str) -> str:
        normalized = _normalize_location(value)
        if not _LOCATION_PATTERN.fullmatch(normalized):
            raise ValueError("聚合 finding location 不合法")
        return normalized

    @field_validator("reviewer_roles")
    @classmethod
    def validate_reviewer_roles(
        cls,
        value: list[ReviewerRole],
    ) -> list[ReviewerRole]:
        expected = sorted(set(value), key=AVAILABLE_REVIEWER_ROLES.index)
        if not expected or value != expected:
            raise ValueError("reviewer_roles 必须非空、唯一并按固定角色顺序排列")
        return value


class ParallelReviewAggregate(BaseModel):
    """可从结构化 reviewer 结果确定性重建的聚合终态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = PARALLEL_REVIEW_SCHEMA_VERSION
    run_id: str
    iteration: int = Field(ge=1)
    evidence_snapshot_sha256: str
    review_plan_id: str
    verdict: ReviewVerdictValue
    reasons: list[AggregateReasonCode] = Field(default_factory=list)
    reviewer_result_ids: dict[ReviewerRole, str] = Field(default_factory=dict)
    observed_result_ids: list[str] = Field(default_factory=list)
    findings: list[AggregatedReviewFinding] = Field(default_factory=list)
    aggregate_sha256: str

    @field_validator("aggregate_sha256", "evidence_snapshot_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return _normalize_identifier(value, "run_id")

    @field_validator("review_plan_id")
    @classmethod
    def validate_review_plan_id(cls, value: str) -> str:
        return _normalize_prefixed_sha256(
            value,
            "review-plan-",
            "review_plan_id",
        )

    @field_validator("reasons")
    @classmethod
    def validate_reasons(
        cls,
        value: list[AggregateReasonCode],
    ) -> list[AggregateReasonCode]:
        expected = sorted(set(value), key=_REASON_ORDER.index)
        if value != expected:
            raise ValueError("aggregate reasons 必须唯一并按固定规则顺序排列")
        return value

    @field_validator("reviewer_result_ids")
    @classmethod
    def validate_reviewer_result_ids(
        cls,
        value: dict[ReviewerRole, str],
    ) -> dict[ReviewerRole, str]:
        for result_id in value.values():
            _normalize_result_id(result_id)
        return value

    @field_validator("observed_result_ids")
    @classmethod
    def validate_observed_result_ids(cls, value: list[str]) -> list[str]:
        normalized = [_normalize_result_id(item) for item in value]
        if normalized != sorted(set(normalized)):
            raise ValueError("observed_result_ids 必须唯一并按 result_id 排序")
        return normalized

    @field_validator("findings")
    @classmethod
    def validate_findings(
        cls,
        value: list[AggregatedReviewFinding],
    ) -> list[AggregatedReviewFinding]:
        finding_ids = [item.finding_id for item in value]
        if finding_ids != sorted(set(finding_ids)):
            raise ValueError("聚合 findings 必须按 finding_id 唯一排序")
        return value

    @model_validator(mode="after")
    def validate_aggregate_identity(self) -> Self:
        expected = _sha256_json(
            self.model_dump(mode="json", exclude={"aggregate_sha256"})
        )
        if self.aggregate_sha256 != expected:
            raise ValueError("aggregate_sha256 与聚合内容不一致")
        return self


def build_review_evidence_snapshot(
    *,
    run_id: str,
    iteration: int,
    workspace_fingerprint: str,
    policy_snapshot_sha256: str,
    verification_result_sha256: str,
    risk_result_sha256: str,
    acceptance_evidence_manifest_sha256: str,
) -> ReviewEvidenceSnapshot:
    payload = {
        "schema_version": PARALLEL_REVIEW_SCHEMA_VERSION,
        "run_id": _normalize_identifier(run_id, "run_id"),
        "iteration": iteration,
        "workspace_fingerprint": workspace_fingerprint.strip().lower(),
        "policy_snapshot_sha256": _normalize_sha256(policy_snapshot_sha256),
        "verification_result_sha256": _normalize_sha256(
            verification_result_sha256
        ),
        "risk_result_sha256": _normalize_sha256(risk_result_sha256),
        "acceptance_evidence_manifest_sha256": _normalize_sha256(
            acceptance_evidence_manifest_sha256
        ),
    }
    return ReviewEvidenceSnapshot(
        **payload,
        evidence_snapshot_sha256=_sha256_json(payload),
    )


def build_parallel_review_plan(
    context: ParallelReviewRoutingContext | Mapping[str, object],
    *,
    topology: ReviewTopology = "adaptive",
    max_parallelism: int | None = None,
) -> ParallelReviewPlan:
    """根据确定性 risk、verification 和变更事实生成可审计 Reviewer 计划。"""

    validated = _validated_routing_context(context)
    role_reasons: dict[ReviewerRole, set[str]] = {
        "correctness_reviewer": {"policy:baseline-correctness"},
    }

    if topology == "fixed_three":
        role_reasons["verification_adequacy_reviewer"] = {
            "topology:fixed-three-reference"
        }
        role_reasons["security_design_reviewer"] = {
            "topology:fixed-three-reference"
        }
    elif topology == "adaptive":
        verification_failed = (
            validated.verification_status == "failed"
            or validated.verification_failed_count > 0
        )
        high_needs_specialists = (
            not verification_failed
            and validated.risk == "high"
            and (
                not validated.gate_reason_codes
                or any(
                    code not in _PRE_REVIEW_DETERMINISTIC_BLOCKERS
                    for code in validated.gate_reason_codes
                )
            )
        )
        verification_reasons: set[str] = set()
        if (
            not verification_failed
            and validated.verification_status not in {"passed", "failed"}
        ):
            verification_reasons.add(
                f"verification:{validated.verification_status}"
            )
        if not verification_failed:
            verification_reasons.update(
                f"gate:{code}"
                for code in validated.gate_reason_codes
                if code in _VERIFICATION_GATE_REASONS
            )
        if (
            not verification_failed
            and any(
                _is_test_scope_path(path)
                for path in validated.changed_files
            )
        ):
            verification_reasons.add("path:test-scope")
        if high_needs_specialists:
            verification_reasons.add("risk:high-cross-check")
        if verification_reasons:
            role_reasons["verification_adequacy_reviewer"] = (
                verification_reasons
            )

        risk_design_reasons = (
            {
                f"gate:{code}"
                for code in validated.gate_reason_codes
                if code in _SECURITY_DESIGN_GATE_REASONS
            }
            if not verification_failed
            else set()
        )
        if high_needs_specialists:
            risk_design_reasons.add("risk:high-fail-closed")
        if risk_design_reasons:
            role_reasons["security_design_reviewer"] = risk_design_reasons

    normalized_role_reasons: dict[ReviewerRole, list[str]] = {
        role: sorted(role_reasons[role])
        for role in AVAILABLE_REVIEWER_ROLES
        if role in role_reasons
    }
    required_roles = list(normalized_role_reasons)
    parallelism = (
        len(required_roles)
        if max_parallelism is None
        else max_parallelism
    )
    payload = {
        "schema_version": PARALLEL_REVIEW_SCHEMA_VERSION,
        "policy_version": PARALLEL_REVIEW_POLICY_VERSION,
        "topology": topology,
        "run_id": validated.run_id,
        "iteration": validated.iteration,
        "evidence_snapshot_sha256": validated.evidence_snapshot_sha256,
        "required_roles": required_roles,
        "role_reasons": normalized_role_reasons,
        "max_parallelism": parallelism,
    }
    return ParallelReviewPlan(
        **payload,
        plan_id=f"review-plan-{_sha256_json(payload)}",
    )


def build_parallel_review_attempt_identity(
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    public_evidence_sha256: str,
    role_prompt_sha256: str,
) -> ParallelReviewAttemptIdentity:
    validated_plan = ParallelReviewPlan.model_validate(
        plan.model_dump(mode="json")
    )
    if reviewer_role not in validated_plan.required_roles:
        raise ValueError("不能为 ReviewPlan 之外的角色构造 attempt identity")
    normalized_public_sha256 = _normalize_sha256(public_evidence_sha256)
    normalized_prompt_sha256 = _normalize_sha256(role_prompt_sha256)
    payload = {
        "prompt_version": PARALLEL_REVIEW_PROMPT_VERSION,
        "plan_id": validated_plan.plan_id,
        "reviewer_role": reviewer_role,
        "evidence_snapshot_sha256": (
            validated_plan.evidence_snapshot_sha256
        ),
        "public_evidence_sha256": normalized_public_sha256,
        "role_prompt_sha256": normalized_prompt_sha256,
    }
    digest = _sha256_json(payload)
    return ParallelReviewAttemptIdentity(
        attempt_id=f"review-{reviewer_role}-{digest[:16]}",
        step_id=(
            f"review-{reviewer_role}-iteration-"
            f"{validated_plan.iteration:02d}"
        ),
        idempotency_key=f"sha256:{digest}",
        input_fingerprint=f"sha256:{digest}",
        public_evidence_sha256=normalized_public_sha256,
        role_prompt_sha256=normalized_prompt_sha256,
    )


def build_parallel_review_finding(
    *,
    evidence_snapshot_sha256: str,
    severity: Literal["blocker", "major", "minor", "suggestion"],
    category: str,
    rule_id: str,
    path: str,
    location: str,
    title: str,
    evidence: str = "",
    recommendation: str = "",
) -> ParallelReviewFinding:
    normalized_category = _normalize_finding_component(category)
    normalized_rule_id = _normalize_finding_component(rule_id)
    normalized_path = _normalize_repo_path(path)
    normalized_location = _normalize_location(location)
    snapshot_sha256 = _normalize_sha256(evidence_snapshot_sha256)
    return ParallelReviewFinding(
        finding_id=_finding_id(
            category=normalized_category,
            rule_id=normalized_rule_id,
            normalized_path=normalized_path,
            normalized_location=normalized_location,
            evidence_snapshot_sha256=snapshot_sha256,
        ),
        severity=severity,
        category=normalized_category,
        rule_id=normalized_rule_id,
        normalized_path=normalized_path,
        normalized_location=normalized_location,
        title=title,
        evidence=evidence,
        recommendation=recommendation,
    )


def build_parallel_review_result(
    *,
    review_plan_id: str,
    run_id: str,
    iteration: int,
    reviewer_role: ReviewerRole,
    attempt_id: str,
    evidence_snapshot_sha256: str,
    execution_ref: str,
    execution_sha256: str,
    status: ReviewExecutionStatus,
    verdict: ReviewVerdictValue,
    summary: str,
    findings: Iterable[ParallelReviewFinding] = (),
    checked_items: Iterable[str] = (),
) -> ParallelReviewResult:
    normalized_run_id = _normalize_identifier(run_id, "run_id")
    normalized_attempt_id = _normalize_identifier(attempt_id, "attempt_id")
    normalized_review_plan_id = _normalize_prefixed_sha256(
        review_plan_id,
        "review-plan-",
        "review_plan_id",
    )
    snapshot_sha256 = _normalize_sha256(evidence_snapshot_sha256)
    normalized_execution_sha256 = _normalize_sha256(execution_sha256)
    return ParallelReviewResult(
        result_id=_review_result_id(
            review_plan_id=normalized_review_plan_id,
            run_id=normalized_run_id,
            iteration=iteration,
            reviewer_role=reviewer_role,
            attempt_id=normalized_attempt_id,
            evidence_snapshot_sha256=snapshot_sha256,
            execution_sha256=normalized_execution_sha256,
        ),
        review_plan_id=normalized_review_plan_id,
        run_id=normalized_run_id,
        iteration=iteration,
        reviewer_role=reviewer_role,
        attempt_id=normalized_attempt_id,
        evidence_snapshot_sha256=snapshot_sha256,
        execution_ref=execution_ref,
        execution_sha256=normalized_execution_sha256,
        status=status,
        verdict=verdict,
        summary=summary,
        findings=list(findings),
        checked_items=list(checked_items),
    )


def build_parallel_review_result_ref(
    result: ParallelReviewResult,
    *,
    artifact_ref: str,
    artifact_sha256: str,
) -> ParallelReviewResultRef:
    validated = _validated_result(result)
    return ParallelReviewResultRef(
        result_id=validated.result_id,
        review_plan_id=validated.review_plan_id,
        reviewer_role=validated.reviewer_role,
        evidence_snapshot_sha256=validated.evidence_snapshot_sha256,
        attempt_id=validated.attempt_id,
        artifact_ref=artifact_ref,
        artifact_sha256=artifact_sha256,
    )


def merge_parallel_review_results(
    current: Mapping[
        str,
        ParallelReviewResult | Mapping[str, object],
    ],
    update: Mapping[
        str,
        ParallelReviewResult | Mapping[str, object],
    ],
) -> dict[str, ParallelReviewResult]:
    """按 result identity 幂等合并，并兼容 checkpoint 反序列化后的字典。"""

    merged: dict[str, ParallelReviewResult] = {}
    for identity, raw_result in current.items():
        result = _validated_result(raw_result)
        if identity != result.result_id:
            raise ValueError("review result map key 与 result_id 不一致")
        merged[identity] = result
    for identity, raw_result in update.items():
        result = _validated_result(raw_result)
        if identity != result.result_id:
            raise ValueError("review result map key 与 result_id 不一致")
        existing = merged.get(identity)
        if existing is not None and existing != result:
            raise ValueError(f"review result identity 冲突：{identity}")
        merged[identity] = result
    return {identity: merged[identity] for identity in sorted(merged)}


def merge_parallel_review_result_refs(
    current: Mapping[
        str,
        ParallelReviewResultRef | Mapping[str, object],
    ],
    update: Mapping[
        str,
        ParallelReviewResultRef | Mapping[str, object],
    ],
) -> dict[str, ParallelReviewResultRef]:
    """供 LangGraph reducer 使用，并兼容 checkpoint 反序列化后的字典。"""

    merged: dict[str, ParallelReviewResultRef] = {}
    for source in (current, update):
        for identity, raw_ref in source.items():
            result_ref = _validated_result_ref(raw_ref)
            if identity != result_ref.result_id:
                raise ValueError("review result ref map key 与 result_id 不一致")
            existing = merged.get(identity)
            if existing is not None and existing != result_ref:
                raise ValueError(
                    f"review result ref identity 冲突：{identity}"
                )
            merged[identity] = result_ref
    return {identity: merged[identity] for identity in sorted(merged)}


def aggregate_parallel_reviews(
    context: ParallelReviewAggregationContext,
    results: Iterable[ParallelReviewResult],
) -> ParallelReviewAggregate:
    """按 ReviewPlan 聚合可变 N 路结果，输入完成顺序不得影响输出。"""

    required_roles = tuple(context.review_plan.required_roles)
    validated_results = sorted(
        (_validated_result(result) for result in results),
        key=lambda item: (item.reviewer_role, item.result_id),
    )
    by_role: dict[ReviewerRole, list[ParallelReviewResult]] = defaultdict(list)
    identity_mismatch = False
    for result in validated_results:
        by_role[result.reviewer_role].append(result)
        if (
            result.run_id != context.run_id
            or result.iteration != context.iteration
            or result.evidence_snapshot_sha256
            != context.evidence_snapshot_sha256
            or result.review_plan_id != context.review_plan.plan_id
            or result.reviewer_role not in required_roles
        ):
            identity_mismatch = True

    selected: dict[ReviewerRole, ParallelReviewResult] = {}
    incomplete = len(validated_results) != len(required_roles)
    for role in required_roles:
        candidates = [
            item
            for item in by_role[role]
            if (
                item.run_id == context.run_id
                and item.iteration == context.iteration
                and item.evidence_snapshot_sha256
                == context.evidence_snapshot_sha256
                and item.review_plan_id == context.review_plan.plan_id
            )
        ]
        if len(candidates) != 1:
            incomplete = True
            continue
        selected[role] = candidates[0]

    unresolved_execution = any(
        result.status != "completed"
        for result in selected.values()
    )
    aggregated_findings = _aggregate_findings(selected, required_roles)
    has_major_findings = any(
        finding.severity in {"blocker", "major"}
        for finding in aggregated_findings
    )
    reviewer_needs_human = any(
        result.verdict == "needs_human"
        for result in selected.values()
    )
    reviewer_requested_changes = any(
        result.verdict == "request_changes"
        for result in selected.values()
    )
    verification_failed = (
        context.verification_status == "failed"
        or context.verification_failed_count > 0
    )
    verification_unresolved = (
        context.verification_status not in {"passed", "failed"}
    )
    evidence_invalid = (
        not context.evidence_fresh
        or context.evidence_truncated
        or not context.evidence_hash_valid
    )
    high_risk_without_approval = (
        context.risk == "high"
        and not context.human_approval_valid
    )

    reasons: list[AggregateReasonCode] = []
    if verification_failed:
        reasons.append("verification_failed")
    if verification_unresolved:
        reasons.append("verification_unresolved")
    if not context.evidence_fresh:
        reasons.append("evidence_stale")
    if context.evidence_truncated:
        reasons.append("evidence_truncated")
    if not context.evidence_hash_valid:
        reasons.append("evidence_hash_mismatch")
    if incomplete:
        reasons.append("reviewer_result_set_incomplete")
    if identity_mismatch:
        reasons.append("reviewer_result_identity_mismatch")
    if unresolved_execution:
        reasons.append("reviewer_execution_unresolved")
    if has_major_findings:
        reasons.append("major_findings")
    if high_risk_without_approval:
        reasons.append("high_risk_without_approval")
    if reviewer_needs_human:
        reasons.append("reviewer_needs_human")
    if reviewer_requested_changes:
        reasons.append("reviewer_requested_changes")

    if verification_failed:
        verdict: ReviewVerdictValue = "request_changes"
    elif (
        verification_unresolved
        or evidence_invalid
        or incomplete
        or identity_mismatch
        or unresolved_execution
    ):
        verdict = "needs_human"
    elif has_major_findings:
        verdict = "request_changes"
    elif high_risk_without_approval or reviewer_needs_human:
        verdict = "needs_human"
    elif reviewer_requested_changes:
        verdict = "request_changes"
    else:
        verdict = "approve"

    payload = {
        "schema_version": PARALLEL_REVIEW_SCHEMA_VERSION,
        "run_id": context.run_id,
        "iteration": context.iteration,
        "evidence_snapshot_sha256": context.evidence_snapshot_sha256,
        "review_plan_id": context.review_plan.plan_id,
        "verdict": verdict,
        "reasons": reasons,
        "reviewer_result_ids": {
            role: selected[role].result_id
            for role in required_roles
            if role in selected
        },
        "observed_result_ids": sorted(
            result.result_id for result in validated_results
        ),
        "findings": [
            finding.model_dump(mode="json")
            for finding in aggregated_findings
        ],
    }
    return ParallelReviewAggregate(
        **payload,
        aggregate_sha256=_sha256_json(payload),
    )


def _aggregate_findings(
    results: Mapping[ReviewerRole, ParallelReviewResult],
    required_roles: Iterable[ReviewerRole],
) -> list[AggregatedReviewFinding]:
    grouped: dict[
        str,
        list[tuple[ReviewerRole, ParallelReviewFinding]],
    ] = defaultdict(list)
    for role in required_roles:
        result = results.get(role)
        if result is None or result.status != "completed":
            continue
        for finding in result.findings:
            grouped[finding.finding_id].append((role, finding))

    aggregated: list[AggregatedReviewFinding] = []
    for finding_id in sorted(grouped):
        candidates = grouped[finding_id]
        identity_fields = {
            (
                finding.category,
                finding.rule_id,
                finding.normalized_path,
                finding.normalized_location,
            )
            for _, finding in candidates
        }
        if len(identity_fields) != 1:
            raise ValueError(f"finding identity 哈希冲突：{finding_id}")
        _, representative = max(
            candidates,
            key=lambda item: (
                _SEVERITY_RANK[item[1].severity],
                -AVAILABLE_REVIEWER_ROLES.index(item[0]),
            ),
        )
        aggregated.append(
            AggregatedReviewFinding(
                finding_id=finding_id,
                severity=max(
                    (finding.severity for _, finding in candidates),
                    key=_SEVERITY_RANK.__getitem__,
                ),
                category=representative.category,
                rule_id=representative.rule_id,
                normalized_path=representative.normalized_path,
                normalized_location=representative.normalized_location,
                reviewer_roles=sorted(
                    {role for role, _ in candidates},
                    key=AVAILABLE_REVIEWER_ROLES.index,
                ),
            )
        )
    return aggregated


def _validated_result(
    result: ParallelReviewResult | Mapping[str, object],
) -> ParallelReviewResult:
    # Pydantic 默认不会重新验证同类型实例；先转成 JSON payload，防止
    # model_copy(update=...) 之类绕过 validator 的对象进入 reducer。
    payload = (
        result.model_dump(mode="json")
        if isinstance(result, ParallelReviewResult)
        else dict(result)
    )
    return ParallelReviewResult.model_validate(payload)


def _validated_result_ref(
    result_ref: ParallelReviewResultRef | Mapping[str, object],
) -> ParallelReviewResultRef:
    # LangGraph checkpoint 恢复后通常得到普通 dict，不能假设仍是 Pydantic 实例。
    payload = (
        result_ref.model_dump(mode="json")
        if isinstance(result_ref, ParallelReviewResultRef)
        else dict(result_ref)
    )
    return ParallelReviewResultRef.model_validate(payload)


def _validated_routing_context(
    context: ParallelReviewRoutingContext | Mapping[str, object],
) -> ParallelReviewRoutingContext:
    payload = (
        context.model_dump(mode="json")
        if isinstance(context, ParallelReviewRoutingContext)
        else dict(context)
    )
    return ParallelReviewRoutingContext.model_validate(payload)


def _snapshot_identity_payload(
    snapshot: ReviewEvidenceSnapshot,
) -> dict[str, object]:
    return snapshot.model_dump(
        mode="json",
        exclude={"evidence_snapshot_sha256"},
    )


def _review_plan_identity_payload(
    plan: ParallelReviewPlan,
) -> dict[str, object]:
    return plan.model_dump(mode="json", exclude={"plan_id"})


def _finding_id(
    *,
    category: str,
    rule_id: str,
    normalized_path: str,
    normalized_location: str,
    evidence_snapshot_sha256: str,
) -> str:
    payload = {
        "category": category,
        "rule_id": rule_id,
        "normalized_path": normalized_path,
        "normalized_location": normalized_location,
        "evidence_snapshot_sha256": evidence_snapshot_sha256,
    }
    return f"finding-{_sha256_json(payload)}"


def _review_result_id(
    *,
    review_plan_id: str,
    run_id: str,
    iteration: int,
    reviewer_role: ReviewerRole,
    attempt_id: str,
    evidence_snapshot_sha256: str,
    execution_sha256: str,
) -> str:
    payload = {
        "review_plan_id": review_plan_id,
        "run_id": run_id,
        "iteration": iteration,
        "reviewer_role": reviewer_role,
        "attempt_id": attempt_id,
        "evidence_snapshot_sha256": evidence_snapshot_sha256,
        "execution_sha256": execution_sha256,
    }
    return f"review-result-{_sha256_json(payload)}"


def _normalize_identifier(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} 只能包含字母、数字、点、下划线、冒号和连字符"
        )
    return normalized


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("SHA-256 必须是 64 位小写十六进制")
    return normalized


def _normalize_prefixed_sha256(
    value: str,
    prefix: str,
    field_name: str,
) -> str:
    normalized = value.strip().lower()
    if not normalized.startswith(prefix):
        raise ValueError(f"{field_name} 必须以 {prefix} 开头")
    _normalize_sha256(normalized.removeprefix(prefix))
    return normalized


def _normalize_result_id(value: str) -> str:
    return _normalize_prefixed_sha256(
        value,
        "review-result-",
        "result_id",
    )


def _normalize_finding_component(value: str) -> str:
    return re.sub(r"\s+", "-", value.strip().lower())


def _normalize_plan_reason(value: str) -> str:
    normalized = value.strip().lower()
    if not _PLAN_REASON_PATTERN.fullmatch(normalized):
        raise ValueError("Reviewer 计划原因只能包含小写标识符字符")
    return normalized


def _normalize_repo_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if len(normalized) > 500:
        raise ValueError("finding path 长度不能超过 500")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized == "."
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
        or "\0" in normalized
    ):
        raise ValueError("finding path 必须是安全的仓库相对路径")
    return path.as_posix()


def _is_test_scope_path(value: str) -> bool:
    path = PurePosixPath(value)
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    return (
        "tests" in lowered_parts
        or "test" in lowered_parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".spec." in name
        or ".test." in name
    )


def _normalize_relative_ref(value: str, field_name: str) -> str:
    try:
        return _normalize_repo_path(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是安全的 run 相对路径") from exc


def _normalize_location(value: str) -> str:
    normalized = re.sub(r"\s+", "", value.strip().lower())
    if "\0" in normalized:
        raise ValueError("normalized_location 不能包含 NUL")
    return normalized


def _sanitize_text(value: str, *, max_chars: int) -> str:
    normalized = redact_text(value).strip()
    if len(normalized) > max_chars:
        raise ValueError(f"自由文本长度不能超过 {max_chars}")
    return normalized


def _sha256_json(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
