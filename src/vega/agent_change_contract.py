from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .agent_contract import (
    NonEmptyText,
    Sha256Text,
    StrictAgentModel,
    canonical_digest,
)
from .scope_path_matching import path_matches_pattern, validate_scope_pattern


RevisionDecision = Literal["unchanged", "auto_apply", "requires_approval"]


class ChangeSideEffectPolicy(StrictAgentModel):
    """人工明确授权的高影响行为；默认值保持保守。"""

    database_schema_change: bool = False
    public_api_change: bool = False
    new_dependency: bool = False
    deployment_action: bool = False
    external_write_during_validation: bool = False
    payment_or_funds_change: bool = False
    permission_change: bool = False
    data_deletion: bool = False


class ChangeAuthorityEnvelope(StrictAgentModel):
    """限制自主循环可以触及的仓库范围和自动重试预算。"""

    allowed_paths: list[NonEmptyText] = Field(min_length=1, max_length=128)
    forbidden_paths: list[NonEmptyText] = Field(default_factory=list, max_length=128)
    max_changed_files: int | None = Field(default=None, ge=1, le=10_000)
    max_repair_rounds: int = Field(default=3, ge=0, le=20)
    max_auto_replans: int = Field(default=1, ge=0, le=10)
    max_review_rounds: int = Field(default=4, ge=1, le=50)
    max_verification_retries: int = Field(default=1, ge=0, le=10)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_patterns(cls, values: list[str], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "paths")
        normalized = [
            validate_scope_pattern(value, f"authority_envelope.{field_name}")
            for value in values
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"authority_envelope.{field_name} 不能包含重复路径")
        return normalized

    @model_validator(mode="after")
    def validate_path_sets(self) -> ChangeAuthorityEnvelope:
        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            raise ValueError(f"授权范围同时允许并禁止相同路径：{sorted(overlap)}")
        return self


class ChangeContract(StrictAgentModel):
    """由人工批准并冻结的业务目标、风险边界和验收合同。"""

    task_id: NonEmptyText
    contract_revision: int = Field(default=1, ge=1)
    goal: NonEmptyText
    acceptance: list[NonEmptyText] = Field(min_length=1)
    invariants: list[NonEmptyText] = Field(default_factory=list)
    non_goals: list[NonEmptyText] = Field(default_factory=list)
    authorized_risk_reviews: list[NonEmptyText] = Field(
        default_factory=list,
        max_length=64,
    )
    side_effect_policy: ChangeSideEffectPolicy = Field(
        default_factory=ChangeSideEffectPolicy
    )
    required_verification: list[NonEmptyText] = Field(min_length=1)
    authority_envelope: ChangeAuthorityEnvelope
    approved: bool = False
    approved_at: str | None = None
    approved_by: str | None = None
    approved_digest: Sha256Text | None = None

    @field_validator("authorized_risk_reviews")
    @classmethod
    def validate_authorized_risk_reviews(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("authorized_risk_reviews 不能包含重复风险领域")
        return values

    @model_validator(mode="after")
    def validate_approval(self) -> ChangeContract:
        approval_fields = (self.approved_at, self.approved_by, self.approved_digest)
        if self.approved and any(value is None for value in approval_fields):
            raise ValueError("已批准合同必须包含批准时间、批准人和批准摘要")
        if not self.approved and any(value is not None for value in approval_fields):
            raise ValueError("未批准合同不能包含批准记录")
        return self

    def semantic_content(self) -> dict[str, object]:
        """返回真正需要人工判断的内容，不把 revision 计作业务变化。"""

        return self.model_dump(
            mode="json",
            exclude={
                "contract_revision",
                "approved",
                "approved_at",
                "approved_by",
                "approved_digest",
            },
        )

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


class ExecutionWorkItem(StrictAgentModel):
    work_item_id: NonEmptyText
    objective: NonEmptyText
    depends_on: list[NonEmptyText] = Field(default_factory=list)
    likely_files: list[NonEmptyText] = Field(default_factory=list)
    verification: list[NonEmptyText] = Field(default_factory=list)
    risk_notes: list[NonEmptyText] = Field(default_factory=list)

    @field_validator("likely_files")
    @classmethod
    def validate_likely_files(cls, values: list[str]) -> list[str]:
        normalized = [_normalize_repo_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("likely_files 不能包含重复路径")
        return normalized


class ExecutionPlan(StrictAgentModel):
    """Agent 可以在 Approved Contract 内自主修订的施工计划。"""

    task_id: NonEmptyText
    contract_revision: int = Field(ge=1)
    plan_revision: int = Field(default=1, ge=1)
    observed_facts: list[NonEmptyText] = Field(default_factory=list)
    hypotheses: list[NonEmptyText] = Field(default_factory=list)
    work_items: list[ExecutionWorkItem] = Field(min_length=1, max_length=8)
    implementation_strategy: list[NonEmptyText] = Field(default_factory=list)
    additional_checks: list[NonEmptyText] = Field(default_factory=list)
    unresolved_decisions: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_work_items(self) -> ExecutionPlan:
        seen: set[str] = set()
        for item in self.work_items:
            if item.work_item_id in seen:
                raise ValueError(f"work_item_id 不能重复：{item.work_item_id}")
            unknown_or_late = set(item.depends_on) - seen
            if unknown_or_late:
                raise ValueError(
                    f"{item.work_item_id} 只能依赖前面已经定义的 Work Item："
                    f"{sorted(unknown_or_late)}"
                )
            seen.add(item.work_item_id)
        return self

    def semantic_content(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_revision"})


class DeclaredRevisionAssessment(StrictAgentModel):
    decision: RevisionDecision
    changed_fields: list[NonEmptyText] = Field(default_factory=list)
    reason: NonEmptyText


def approve_change_contract(
    contract: ChangeContract,
    *,
    actor: str,
    approved_at: str | None = None,
) -> ChangeContract:
    if not actor.strip():
        raise ValueError("批准人不能为空")
    payload = contract.model_dump(mode="json")
    payload.update(
        {
            "approved": True,
            "approved_by": actor.strip(),
            "approved_at": approved_at or datetime.now(UTC).isoformat(),
            "approved_digest": contract.expected_approval_digest(),
        }
    )
    return ChangeContract.model_validate(payload)


def validate_execution_plan_against_contract(
    contract: ChangeContract,
    plan: ExecutionPlan,
) -> None:
    if plan.task_id != contract.task_id:
        raise ValueError("Execution Plan 与 Approved Contract 的 task_id 不一致")
    if plan.contract_revision != contract.contract_revision:
        raise ValueError("Execution Plan 绑定的 contract_revision 已过期")

    envelope = contract.authority_envelope
    for item in plan.work_items:
        for path in item.likely_files:
            if any(
                path_matches_pattern(path, pattern)
                for pattern in envelope.forbidden_paths
            ):
                raise ValueError(
                    f"Execution Plan 候选路径命中禁止范围：{path}"
                )
            if not any(
                path_matches_pattern(path, pattern)
                for pattern in envelope.allowed_paths
            ):
                raise ValueError(
                    f"Execution Plan 候选路径越出授权范围：{path}"
                )


def classify_declared_revision(
    *,
    current_contract: ChangeContract,
    proposed_contract: ChangeContract,
    current_plan: ExecutionPlan,
    proposed_plan: ExecutionPlan,
) -> DeclaredRevisionAssessment:
    """只裁决声明内容；实际 Git Diff 和风险路径仍由运行时单独检查。"""

    if not current_contract.approval_is_current():
        raise ValueError("当前 Approved Contract 缺失或批准摘要已过期")
    if proposed_contract.task_id != current_contract.task_id:
        raise ValueError("Contract revision 不能改变 task_id")

    contract_changes = _changed_fields(
        current_contract.semantic_content(),
        proposed_contract.semantic_content(),
    )
    if contract_changes:
        if proposed_contract.contract_revision != current_contract.contract_revision + 1:
            raise ValueError("合同内容变化时 contract_revision 必须递增 1")
        validate_execution_plan_against_contract(proposed_contract, proposed_plan)
        return DeclaredRevisionAssessment(
            decision="requires_approval",
            changed_fields=contract_changes,
            reason="Approved Contract 的人工授权字段发生变化",
        )

    if proposed_contract.contract_revision != current_contract.contract_revision:
        raise ValueError("合同内容未变化时不能单独修改 contract_revision")
    validate_execution_plan_against_contract(current_contract, current_plan)
    validate_execution_plan_against_contract(current_contract, proposed_plan)

    plan_changes = _changed_fields(
        current_plan.semantic_content(),
        proposed_plan.semantic_content(),
    )
    if not plan_changes:
        if proposed_plan.plan_revision != current_plan.plan_revision:
            raise ValueError("执行内容未变化时不能单独修改 plan_revision")
        return DeclaredRevisionAssessment(
            decision="unchanged",
            reason="合同与执行计划均未变化",
        )
    if proposed_plan.plan_revision != current_plan.plan_revision + 1:
        raise ValueError("执行计划变化时 plan_revision 必须递增 1")
    return DeclaredRevisionAssessment(
        decision="auto_apply",
        changed_fields=plan_changes,
        reason="变化仅发生在 Approved Contract 内的执行计划",
    )


def _normalize_repo_path(value: str) -> str:
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


def _changed_fields(
    current: object,
    proposed: object,
    *,
    prefix: str = "",
) -> list[str]:
    if isinstance(current, dict) and isinstance(proposed, dict):
        changes: list[str] = []
        for key in sorted(set(current) | set(proposed)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in current or key not in proposed:
                changes.append(child)
                continue
            changes.extend(
                _changed_fields(current[key], proposed[key], prefix=child)
            )
        return changes
    if current != proposed:
        return [prefix]
    return []
