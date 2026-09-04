from __future__ import annotations

from pathlib import Path
from typing import Literal

from . import agent_provider_preparation as provider_preparation
from .agent_change_control import require_change_verification_retry_budget
from .agent_contract import AgentPlan, AgentState, AgentWorkItem
from .agent_persistence import load_agent_checkpoint
from .agent_plan_scope import (
    capture_plan_scope_baseline,
    evaluate_plan_scope,
    plan_scope_failure,
)
from .agent_provider import AgentProvider
from .agent_provider_factory import ensure_reviewer_runner
from .agent_reviewer_timeout_retry import prepare_reviewer_timeout_source
from .agent_run_status import latest_worker_dispatch_binding
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    validate_dispatch_artifacts,
)
from .agent_verification_retry_evidence import (
    PreparedVerificationRetry,
    capture_verification_retry_baseline,
    load_source_observation,
    matching_source_plans,
    select_source_plan,
    validate_retry_source,
)
from .agent_worker_evidence import (
    load_child_state,
    require_child_quiescent,
    require_single_executable_work_item,
)
from .loop_runtime import LoopAutomationRuntime
from .models import LoopAutomationState
from .project_config import load_project_config
from .run_utils import resolve_run_dir
from .verification_command_preflight import require_verification_commands_preflight
from .workspace_snapshot import ReviewWorkspaceSnapshot


VerificationRetryReason = Literal["verification_failure", "reviewer_timeout"]


def prepare_verification_retry(
    workspace: Path,
    run: str,
    *,
    loop_runtime: LoopAutomationRuntime,
    provider: AgentProvider,
    persistent_sessions: bool,
    retry_reason: VerificationRetryReason,
) -> PreparedVerificationRetry:
    """验证恢复现场，并为既有 Core 门禁链准备只读输入。"""

    if retry_reason == "reviewer_timeout":
        source = prepare_reviewer_timeout_source(workspace, run)
        active_plan = _reactivate_current_work_item(
            source.plan,
            source.state.current_work_item,
        )
        prepared = _build_prepared(
            workspace,
            run_dir=source.run_dir,
            state=source.state,
            plan=active_plan,
            metadata=source.metadata,
            work_item=source.work_item,
            repo=source.repo,
            before=source.before,
            child_dir=source.child_dir,
            child_state=source.child_state,
            source_plan=source.plan,
            source_observation=source.source_observation,
            source_claim=source.source_claim,
            source_summary_ref=source.source_summary_ref,
            source_operation_id=source.source_operation_id,
            source_finish_sha256=source.source_finish_sha256,
            retry_reason=retry_reason,
            candidate_sha=source.candidate_sha,
            candidate_ref=source.candidate_ref,
            reviewer_retry_attempt=1,
            reviewer_role_key=source.reviewer_role_key,
        )
    else:
        prepared = _prepare_verification_failure(workspace, run)
    ensure_reviewer_runner(
        loop_runtime,
        load_project_config(prepared.repo),
        agent_run_dir=prepared.run_dir,
        state=prepared.state,
        provider=provider,
        persistent_session=persistent_sessions,
        role_key=prepared.reviewer_role_key,
    )
    return prepared


def _reactivate_current_work_item(
    plan: AgentPlan,
    work_item_id: str | None,
) -> AgentPlan:
    updated = plan.model_copy(deep=True)
    current = next(
        (
            item
            for item in updated.work_items
            if item.work_item_id == work_item_id
        ),
        None,
    )
    if current is None or current.status != "blocked":
        raise ValueError("Reviewer timeout 的当前 Work Item 不是 blocked 状态")
    current.status = "active"
    return AgentPlan.model_validate(updated.model_dump(mode="json"))


def _prepare_verification_failure(
    workspace: Path,
    run: str,
) -> PreparedVerificationRetry:
    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    if state.phase != "ready" or state.active_child_run or state.active_operation_id:
        raise ValueError("当前 Agent 状态不允许验证专用恢复")
    validate_dispatch_artifacts(run_dir, state, plan)
    require_change_verification_retry_budget(run_dir, state, plan, metadata)
    work_item = require_single_executable_work_item(plan, state)
    if work_item.external_side_effects != "none":
        raise ValueError("验证专用恢复只接受 external_side_effects=none 的 Work Item")
    repo = bound_repo(run_dir)
    require_verification_commands_preflight(repo, work_item.verification)
    before = capture_bound_workspace(run_dir)
    if before.fingerprint != state.workspace_fingerprint:
        raise ValueError("验证恢复前 Workspace 已漂移，必须先重新对账")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if len(checkpoint.failed_attempts) != 1:
        raise ValueError("当前 Checkpoint 没有唯一失败 child，不能执行验证专用恢复")
    child_dir = resolve_run_dir(workspace, checkpoint.failed_attempts[0])
    child_state = load_child_state(child_dir, repo)
    require_child_quiescent(child_dir)
    if (
        child_state.status != "needs_human"
        or not child_state.iterations
        or child_state.current_iteration >= child_state.max_iterations
    ):
        raise ValueError("失败 child 没有可追加的 Core iteration")
    worker_binding = latest_worker_dispatch_binding(run_dir, state)
    if worker_binding is None or worker_binding[0] != child_dir.name:
        raise ValueError("无法把失败 child 绑定到原始真实 Worker")
    source_operation_id = worker_binding[1]
    source_observation = load_source_observation(
        run_dir,
        child_dir.name,
        source_operation_id,
    )
    source_plan = select_source_plan(
        run_dir,
        state,
        matching_source_plans(run_dir, plan),
        source_observation,
    )
    source_summary_ref, source_claim, source_finish_sha256 = validate_retry_source(
        run_dir,
        state,
        source_plan,
        source_observation,
        child_dir,
        before,
    )
    return _build_prepared(
        workspace,
        run_dir=run_dir,
        state=state,
        plan=plan,
        metadata=metadata,
        work_item=work_item,
        repo=repo,
        before=before,
        child_dir=child_dir,
        child_state=child_state,
        source_plan=source_plan,
        source_observation=source_observation,
        source_claim=source_claim,
        source_summary_ref=source_summary_ref,
        source_operation_id=source_operation_id,
        source_finish_sha256=source_finish_sha256,
        retry_reason="verification_failure",
    )


def _build_prepared(
    workspace: Path,
    *,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    work_item: AgentWorkItem,
    repo: Path,
    before: ReviewWorkspaceSnapshot,
    child_dir: Path,
    child_state: LoopAutomationState,
    source_plan: AgentPlan,
    source_observation,
    source_claim,
    source_summary_ref: str,
    source_operation_id: str,
    source_finish_sha256: str,
    retry_reason: VerificationRetryReason,
    candidate_sha: str | None = None,
    candidate_ref: str | None = None,
    reviewer_retry_attempt: int = 0,
    reviewer_role_key: str | None = None,
) -> PreparedVerificationRetry:
    core_workspace_baseline = capture_verification_retry_baseline(
        workspace,
        repo,
        before,
    )
    comparison_base_sha, comparison_paths = (
        provider_preparation.comparison_binding_from_metadata(metadata)
    )
    next_iteration = child_state.current_iteration + 1
    plan_scope_baseline = capture_plan_scope_baseline(
        repo,
        plan,
        work_item,
        expected_head_sha=before.head_sha,
        iteration=next_iteration,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )
    pre_core_scope = evaluate_plan_scope(
        repo,
        plan_scope_baseline,
        expected_head_sha=before.head_sha,
        iteration=next_iteration,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )
    if pre_core_scope.status == "failed":
        raise ValueError(plan_scope_failure(pre_core_scope))
    return PreparedVerificationRetry(
        run_dir=run_dir,
        state=state,
        plan=plan,
        work_item=work_item,
        repo=repo,
        before=before,
        child_dir=child_dir,
        child_state=child_state,
        source_plan=source_plan,
        source_observation=source_observation,
        source_claim=source_claim,
        source_summary_ref=source_summary_ref,
        source_operation_id=source_operation_id,
        source_finish_sha256=source_finish_sha256,
        core_workspace_baseline=core_workspace_baseline,
        plan_scope_baseline=plan_scope_baseline,
        pre_core_scope=pre_core_scope,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
        retry_reason=retry_reason,
        candidate_sha=candidate_sha,
        candidate_ref=candidate_ref,
        reviewer_retry_attempt=reviewer_retry_attempt,
        reviewer_role_key=reviewer_role_key,
    )
