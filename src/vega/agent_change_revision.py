from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .agent_change_contract import (
    ChangeContract,
    DeclaredRevisionAssessment,
    ExecutionPlan,
    classify_declared_revision,
)
from .agent_change_control import ChangeBudgetSnapshot
from .agent_contract import (
    AgentPlan,
    NonEmptyText,
    StrictAgentModel,
)
from .agent_change_run import project_agent_plan
from .project_config import load_project_config
from .redaction import write_redacted_json_once
from .risk_review import match_required_reviews
from .scope_path_matching import (
    path_matches_pattern,
    scope_paths_are_case_insensitive,
)


RevisionOutcome = Literal[
    "unchanged",
    "auto_apply",
    "requires_approval",
    "needs_human",
]


class ChangeRiskPathHit(StrictAgentModel):
    risk_id: NonEmptyText
    label: NonEmptyText
    matched_files: list[NonEmptyText] = Field(min_length=1)


class ChangeRevisionAssessment(StrictAgentModel):
    assessment_id: NonEmptyText
    outcome: RevisionOutcome
    declared: DeclaredRevisionAssessment
    actual_changed_files: list[NonEmptyText] = Field(default_factory=list)
    scope_violations: list[NonEmptyText] = Field(default_factory=list)
    risk_path_hits: list[ChangeRiskPathHit] = Field(default_factory=list)
    missing_risk_authorizations: list[NonEmptyText] = Field(default_factory=list)
    unknown_risk_authorizations: list[NonEmptyText] = Field(default_factory=list)
    approval_question: NonEmptyText | None = None
    reason: NonEmptyText
    budget: ChangeBudgetSnapshot


def assess_change_revision(
    *,
    repo: Path,
    changed_files: list[str],
    current_contract: ChangeContract,
    proposed_contract: ChangeContract,
    current_plan: ExecutionPlan,
    proposed_plan: ExecutionPlan,
    budget: ChangeBudgetSnapshot,
) -> ChangeRevisionAssessment:
    declared = classify_declared_revision(
        current_contract=current_contract,
        proposed_contract=proposed_contract,
        current_plan=current_plan,
        proposed_plan=proposed_plan,
    )
    _validate_proposed_approval(
        current_contract,
        proposed_contract,
        declared,
    )
    scope_violations = _scope_violations(
        repo,
        changed_files,
        proposed_contract,
    )
    config = load_project_config(repo)
    hits = [
        ChangeRiskPathHit(
            risk_id=item.id,
            label=item.label,
            matched_files=list(item.matched_files),
        )
        for item in match_required_reviews(
            repo,
            changed_files,
            config.risk.required_reviews,
        )
    ]
    configured_risk_ids = {item.id for item in config.risk.required_reviews}
    authorized = set(proposed_contract.authorized_risk_reviews)
    missing = sorted({item.risk_id for item in hits} - authorized)
    unknown = sorted(authorized - configured_risk_ids)

    outcome, question, reason = _revision_outcome(
        declared,
        scope_violations=scope_violations,
        missing_risk_authorizations=missing,
        unknown_risk_authorizations=unknown,
        budget=budget,
    )
    return ChangeRevisionAssessment(
        assessment_id=f"revision-{uuid4().hex[:12]}",
        outcome=outcome,
        declared=declared,
        actual_changed_files=list(changed_files),
        scope_violations=scope_violations,
        risk_path_hits=hits,
        missing_risk_authorizations=missing,
        unknown_risk_authorizations=unknown,
        approval_question=question,
        reason=reason,
        budget=budget,
    )


def project_revised_agent_plan(
    *,
    current_plan: AgentPlan,
    current_execution_plan: ExecutionPlan,
    proposed_contract: ChangeContract,
    proposed_execution_plan: ExecutionPlan,
) -> AgentPlan:
    """只保留语义未变化的已完成 Work Item，其他项重新进入待执行状态。"""

    previous_items = {
        item.work_item_id: item
        for item in current_execution_plan.work_items
    }
    completed = {
        item.work_item_id
        for item in current_plan.work_items
        if item.status == "completed"
    }
    seed = current_plan.model_copy(deep=True)
    seed_by_id = {item.work_item_id: item for item in seed.work_items}
    proposed_by_id = {
        item.work_item_id: item
        for item in proposed_execution_plan.work_items
    }
    for item_id, item in seed_by_id.items():
        same_completed_item = (
            item_id in completed
            and item_id in previous_items
            and item_id in proposed_by_id
            and previous_items[item_id].model_dump(mode="json")
            == proposed_by_id[item_id].model_dump(mode="json")
        )
        item.status = "completed" if same_completed_item else "pending"
    return project_agent_plan(
        proposed_contract,
        proposed_execution_plan,
        current=seed,
    )


def archive_change_revision(
    run_dir: Path,
    *,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    projected_plan: AgentPlan,
) -> list[str]:
    refs = [
        f"contracts/contract-revision-{contract.contract_revision:03d}.json",
        (
            "execution-plans/"
            f"execution-plan-revision-{execution_plan.plan_revision:03d}.json"
        ),
        f"plans/plan-revision-{projected_plan.plan_revision:03d}.json",
    ]
    payloads = [
        contract.model_dump(mode="json"),
        execution_plan.model_dump(mode="json"),
        projected_plan.model_dump(mode="json"),
    ]
    for relative, payload in zip(refs, payloads, strict=True):
        path = run_dir / relative
        if path.is_file():
            continue
        write_redacted_json_once(path, payload)
    return refs


def write_revision_assessment(
    run_dir: Path,
    assessment: ChangeRevisionAssessment,
) -> str:
    relative = f"revisions/{assessment.assessment_id}.json"
    write_redacted_json_once(
        run_dir / relative,
        assessment.model_dump(mode="json"),
    )
    return relative


def first_pending_work_item(plan: AgentPlan) -> str:
    for item in plan.work_items:
        if item.status == "pending":
            return item.work_item_id
    raise ValueError("修订后的 Execution Plan 没有待执行 Work Item")


def _validate_proposed_approval(
    current: ChangeContract,
    proposed: ChangeContract,
    declared: DeclaredRevisionAssessment,
) -> None:
    if declared.decision == "requires_approval":
        if proposed.approved:
            raise ValueError("合同字段变化时，提案必须先移除旧批准记录")
        return
    if (
        not proposed.approval_is_current()
        or proposed.approved_digest != current.approved_digest
    ):
        raise ValueError("合同内容未变化时必须保留当前有效批准")


def _scope_violations(
    repo: Path,
    changed_files: list[str],
    contract: ChangeContract,
) -> list[str]:
    envelope = contract.authority_envelope
    case_sensitive = not scope_paths_are_case_insensitive(repo)
    violations = [
        path
        for path in changed_files
        if any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in envelope.forbidden_paths
        )
        or not any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in envelope.allowed_paths
        )
    ]
    if (
        envelope.max_changed_files is not None
        and len(changed_files) > envelope.max_changed_files
    ):
        violations.append(
            f"<changed-files:{len(changed_files)}>{envelope.max_changed_files}"
        )
    return list(dict.fromkeys(violations))


def _revision_outcome(
    declared: DeclaredRevisionAssessment,
    *,
    scope_violations: list[str],
    missing_risk_authorizations: list[str],
    unknown_risk_authorizations: list[str],
    budget: ChangeBudgetSnapshot,
) -> tuple[RevisionOutcome, str | None, str]:
    blockers: list[str] = []
    if scope_violations:
        blockers.append("当前 Git Diff 仍越出提案中的 authority_envelope")
    if missing_risk_authorizations:
        blockers.append(
            "当前 Git Diff 命中尚未授权的 risk.required_reviews："
            + "、".join(missing_risk_authorizations)
        )
    if unknown_risk_authorizations:
        blockers.append(
            "合同声明了当前项目策略不存在的风险领域："
            + "、".join(unknown_risk_authorizations)
        )
    if blockers:
        question = (
            "；".join(blockers)
            + "。请修改 Contract、缩小当前 Diff，或停止任务。"
        )
        return "needs_human", question, "当前提案不能解释真实 Diff 与风险路径"

    if declared.decision == "requires_approval":
        fields = "、".join(declared.changed_fields)
        question = f"是否批准 Contract revision 对这些冻结字段的修改：{fields}？"
        return "requires_approval", question, declared.reason
    if declared.decision == "auto_apply":
        if budget.review_rounds_used >= budget.max_review_rounds:
            question = (
                "当前 Work Item 的 Review 预算已用完："
                f"{budget.review_rounds_used}/{budget.max_review_rounds}。"
                "请决定扩大合同预算、人工修订或停止任务。"
            )
            return "needs_human", question, "Review 达到停止条件"
        if budget.auto_replans_used >= budget.max_auto_replans:
            question = (
                "自动 Replan 预算已用完："
                f"{budget.auto_replans_used}/{budget.max_auto_replans}。"
                "请决定扩大合同预算、人工修订或停止任务。"
            )
            return "needs_human", question, "自动 Replan 达到停止条件"
        return "auto_apply", None, declared.reason
    return "unchanged", None, declared.reason
