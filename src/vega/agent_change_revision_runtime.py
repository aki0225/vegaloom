from __future__ import annotations

from pathlib import Path

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_change_control import change_budget_snapshot
from .agent_change_revision import (
    archive_change_revision,
    assess_change_revision,
    first_pending_work_item,
    project_revised_agent_plan,
    write_revision_assessment,
)
from .agent_change_run import (
    CHANGE_CONTRACT_ARTIFACT,
    EXECUTION_PLAN_ARTIFACT,
    load_change_run_context,
    save_change_run_artifacts,
)
from .agent_contract import AgentPlan, AgentState
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    save_agent_plan,
    write_checkpoint,
    write_status_card,
    write_task_brief,
)
from .workspace_check import ReviewWorkspaceSnapshot


def revise_change_run(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    *,
    proposed_contract: ChangeContract,
    proposed_execution_plan: ExecutionPlan,
) -> AgentRun:
    """按真实 Diff、风险路径和冻结合同裁决 ChangeRun revision。"""

    if (
        state.run_kind != "change"
        or state.active_child_run
        or state.active_operation_id
        or state.active_candidate_sha
    ):
        raise ValueError("当前状态不能修订 ChangeRun")
    if state.phase not in {"planning", "ready", "needs_human"}:
        raise ValueError("ChangeRun 只允许在可重规划或人工处理阶段提交 revision")
    context = load_change_run_context(run_dir, state, plan, metadata)
    assert context is not None
    snapshot = capture_bound_workspace(run_dir)
    if snapshot.unsafe_index_paths or not snapshot.git_control_complete:
        raise ValueError("当前 Workspace 无法完整解释，拒绝自动 Replan")
    budget = change_budget_snapshot(run_dir, state, context.contract)
    assessment = assess_change_revision(
        repo=bound_repo(run_dir),
        changed_files=list(snapshot.changed_files),
        current_contract=context.contract,
        proposed_contract=proposed_contract,
        current_plan=context.execution_plan,
        proposed_plan=proposed_execution_plan,
        budget=budget,
    )
    assessment_ref = write_revision_assessment(run_dir, assessment)
    if assessment.outcome == "unchanged":
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="change_revision_unchanged",
            state=state,
            observation_summary=assessment.reason,
            artifact_refs=[assessment_ref],
        )
        write_status_card(
            run_dir,
            state,
            plan,
            next_step="提案没有实际变化；继续当前允许动作",
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)
    if assessment.outcome == "needs_human":
        return _block_change_revision(
            run_dir,
            state,
            plan,
            snapshot,
            assessment_ref=assessment_ref,
            reason=assessment.approval_question or assessment.reason,
        )

    archived_refs = archive_change_revision(
        run_dir,
        contract=context.contract,
        execution_plan=context.execution_plan,
        projected_plan=plan,
    )
    revised_plan = project_revised_agent_plan(
        current_plan=plan,
        current_execution_plan=context.execution_plan,
        proposed_contract=proposed_contract,
        proposed_execution_plan=proposed_execution_plan,
    )
    current_work_item = first_pending_work_item(revised_plan)
    save_change_run_artifacts(
        run_dir,
        proposed_contract,
        proposed_execution_plan,
    )
    save_agent_plan(run_dir, revised_plan)
    if assessment.outcome == "requires_approval":
        return _publish_approval_revision(
            run_dir,
            state,
            revised_plan,
            proposed_contract,
            proposed_execution_plan,
            snapshot,
            assessment_ref=assessment_ref,
            archived_refs=archived_refs,
            current_work_item=current_work_item,
            reason=assessment.reason,
            question=assessment.approval_question,
        )
    return _publish_auto_revision(
        run_dir,
        state,
        revised_plan,
        proposed_contract,
        proposed_execution_plan,
        snapshot,
        assessment_ref=assessment_ref,
        archived_refs=archived_refs,
        current_work_item=current_work_item,
        reason=assessment.reason,
    )


def _publish_approval_revision(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    snapshot: ReviewWorkspaceSnapshot,
    *,
    assessment_ref: str,
    archived_refs: list[str],
    current_work_item: str,
    reason: str,
    question: str | None,
) -> AgentRun:
    revised_state = update_state(
        state,
        phase="awaiting_approval",
        state_version=state.state_version + 1,
        goal_revision=contract.contract_revision,
        plan_revision=execution_plan.plan_revision,
        approved_plan_digest=None,
        contract_revision=contract.contract_revision,
        approved_contract_digest=None,
        execution_plan_revision=execution_plan.plan_revision,
        current_work_item=current_work_item,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["replan", "human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        revised_state,
        snapshot,
        reason=reason,
        status="blocked",
        pending_actions=["replan", "human"],
        evidence_refs=[assessment_ref, *archived_refs],
    )
    revised_state = update_state(
        revised_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=revised_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", revised_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="change_revision_requires_approval",
        state=revised_state,
        observation_summary=reason,
        route_reason=question,
        artifact_refs=[
            assessment_ref,
            *archived_refs,
            CHANGE_CONTRACT_ARTIFACT,
            EXECUTION_PLAN_ARTIFACT,
            "agent-plan.json",
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ],
    )
    write_status_card(
        run_dir,
        revised_state,
        plan,
        checkpoint=checkpoint,
        next_step=question or "人工批准新 Contract revision",
    )
    return AgentRun(run_dir=run_dir, state=revised_state, plan=plan)


def _publish_auto_revision(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    snapshot: ReviewWorkspaceSnapshot,
    *,
    assessment_ref: str,
    archived_refs: list[str],
    current_work_item: str,
    reason: str,
) -> AgentRun:
    revised_state = update_state(
        state,
        phase="ready",
        state_version=state.state_version + 1,
        goal_revision=contract.contract_revision,
        plan_revision=execution_plan.plan_revision,
        approved_plan_digest=plan.approved_digest,
        contract_revision=contract.contract_revision,
        approved_contract_digest=contract.approved_digest,
        execution_plan_revision=execution_plan.plan_revision,
        current_work_item=current_work_item,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["next", "replan", "human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        revised_state,
        snapshot,
        reason=reason,
        status="safe",
        pending_actions=["next", "replan", "human"],
        evidence_refs=[assessment_ref, *archived_refs],
    )
    revised_state = update_state(
        revised_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=revised_state.state_version + 1,
    )
    write_task_brief(
        run_dir,
        plan,
        revised_state,
        checkpoint,
        artifact_refs=[assessment_ref],
    )
    save_agent_state(run_dir / "agent-state.json", revised_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="change_execution_plan_auto_applied",
        state=revised_state,
        observation_summary=reason,
        artifact_refs=[
            assessment_ref,
            *archived_refs,
            EXECUTION_PLAN_ARTIFACT,
            "agent-plan.json",
            "task-brief.md",
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ],
    )
    write_status_card(
        run_dir,
        revised_state,
        plan,
        checkpoint=checkpoint,
        next_step="Execution Plan 已在原合同内更新，可继续当前 Work Item",
    )
    return AgentRun(run_dir=run_dir, state=revised_state, plan=plan)


def _block_change_revision(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot: ReviewWorkspaceSnapshot,
    *,
    assessment_ref: str,
    reason: str,
) -> AgentRun:
    blocked_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["replan", "human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        blocked_state,
        snapshot,
        reason=reason,
        status="blocked",
        pending_actions=["replan", "human"],
        evidence_refs=[assessment_ref],
    )
    blocked_state = update_state(
        blocked_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=blocked_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", blocked_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="change_revision_blocked",
        state=blocked_state,
        observation_summary="ChangeRun revision 未改变当前合同或计划",
        route_reason=reason,
        artifact_refs=[
            assessment_ref,
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ],
    )
    write_status_card(
        run_dir,
        blocked_state,
        plan,
        checkpoint=checkpoint,
        next_step=reason,
    )
    return AgentRun(run_dir=run_dir, state=blocked_state, plan=plan)
