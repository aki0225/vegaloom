from __future__ import annotations

from pathlib import Path
from typing import Literal

from . import agent_provider_preparation as provider_preparation
from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_change_control import require_change_verification_retry_budget
from .agent_change_run import load_change_run_context, validate_change_projection
from .agent_contract import AgentPlan, AgentState, AgentWorkItem
from .agent_persistence import load_agent_checkpoint, read_agent_trace
from .agent_plan_scope import (
    capture_plan_scope_baseline,
    evaluate_plan_scope,
    plan_scope_failure,
)
from .agent_provider import AgentProvider
from .agent_provider_factory import ensure_reviewer_runner
from .agent_reviewer_timeout_retry import prepare_reviewer_timeout_source
from .agent_run_status import latest_dispatch_binding, latest_worker_dispatch_binding
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    validate_dispatch_artifacts,
)
from .agent_verification_retry_evidence import (
    PreparedVerificationRetry,
    VerificationRetryMode,
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


def verification_retry_requested(workspace: Path, run: str) -> bool:
    """识别人工修订验证后的继续意图，完整执行资格仍由恢复入口检查。"""

    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    if (
        state.run_kind != "change"
        or state.phase != "ready"
        or state.active_child_run
        or state.active_operation_id
        or "repair" in state.allowed_actions
        or state.latest_checkpoint_id is None
    ):
        return False
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if not checkpoint.failed_attempts:
        return False
    if _approved_implementation_revision(run_dir, state, plan, metadata):
        return False
    if len(checkpoint.failed_attempts) == 1:
        child_dir = resolve_run_dir(workspace, checkpoint.failed_attempts[0])
        if not (child_dir / "state.json").exists():
            binding = latest_dispatch_binding(run_dir, state)
            if binding is None or binding[0] != child_dir.name:
                raise ValueError("失败 Checkpoint 与原 Worker 绑定不一致")
            # 缺文件不是重跑依据；只有完整证明 Core 尚未启动的显式恢复才可继续 Worker。
            provider_preparation.require_pre_core_resume(workspace, run_dir, state)
            return False
    # 有失败现场时不能因资格校验失败回退到新 Worker；由恢复路径明确拒绝。
    return True


def _approved_implementation_revision(
    run_dir: Path, state: AgentState, plan: AgentPlan, metadata: dict[str, object],
) -> bool:
    """失败历史继续保留；只有新的人工实现授权才开始新的 Worker epoch。"""

    context = load_change_run_context(run_dir, state, plan, metadata)
    assert context is not None
    contract, execution_plan = context.contract, context.execution_plan
    if contract.approval_source != "human" or contract.contract_revision <= 1:
        return False
    binding = latest_dispatch_binding(run_dir, state)
    if binding is None:
        return False
    trace = read_agent_trace(run_dir / "trace.jsonl")
    last_dispatch = max(
        index for index, item in enumerate(trace)
        if (item.get("child_run"), item.get("operation_id")) == binding
    )
    revisions = [
        item for item in trace[last_dispatch + 1:]
        if item.get("event") in {
            "change_revision_requires_approval", "change_contract_approved",
            "change_execution_plan_auto_applied",
        }
    ]
    # auto_apply 不是本分支的新人工批准，不借 attempt 预算重置推断实现意图。
    if len(revisions) < 2 or [item.get("event") for item in revisions[-2:]] != [
        "change_revision_requires_approval", "change_contract_approved",
    ]:
        return False
    revision, approval = revisions[-2:]
    if any(
        item.get("run_id") != state.run_id or item.get("work_item") != state.current_work_item
        for item in (revision, approval)
    ) or revision.get("phase") != "awaiting_approval" or approval.get("phase") != "ready":
        raise ValueError("新实现授权的 revision Trace 身份不一致")
    contract_ref = f"contracts/contract-revision-{contract.contract_revision - 1:03d}.json"
    plan_ref = (
        "execution-plans/"
        f"execution-plan-revision-{execution_plan.plan_revision - 1:03d}.json"
    )
    projection_ref = f"plans/plan-revision-{execution_plan.plan_revision - 1:03d}.json"
    if not {contract_ref, plan_ref, projection_ref}.issubset(revision.get("artifact_refs", [])):
        raise ValueError("新实现授权缺少绑定的旧 Contract 或 Execution Plan 归档")
    try:
        previous_contract = ChangeContract.model_validate_json(
            (run_dir / contract_ref).read_text(encoding="utf-8")
        )
        previous_plan = ExecutionPlan.model_validate_json(
            (run_dir / plan_ref).read_text(encoding="utf-8")
        )
        previous_projection = AgentPlan.model_validate_json(
            (run_dir / projection_ref).read_text(encoding="utf-8")
        )
        validate_change_projection(previous_contract, previous_plan, previous_projection)
    except (OSError, ValueError) as exc:
        raise ValueError("新实现授权的旧 Contract 或 Execution Plan 归档无法验证") from exc
    if (
        not previous_contract.approval_is_current()
        or previous_contract.task_id != state.task_id
        or previous_contract.contract_revision + 1 != contract.contract_revision
        or previous_plan.plan_revision + 1 != execution_plan.plan_revision
    ):
        raise ValueError("新实现授权的旧 Contract 或 Execution Plan 归档不一致")
    implementations = []
    for source_contract, source_plan in (
        (previous_contract, previous_projection), (contract, plan),
    ):
        contract_content = source_contract.semantic_content()
        contract_content.pop("required_verification")
        contract_content["authority_envelope"].pop("max_verification_retries")
        # 策略说明没有进入旧批准投影，不能单凭它的差异授予新 Worker。
        plan_content = source_plan.content_for_approval()
        plan_content.pop("goal_revision")
        plan_content.pop("plan_revision")
        for item in plan_content["work_items"]:
            item.pop("verification")
        implementations.append((contract_content, plan_content))
    # 仅改变验证要求仍必须复用原 Worker 证据；匹配失败绝不作为改派依据。
    return implementations[0] != implementations[1]


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

    if retry_reason not in {"verification_failure", "reviewer_timeout"}:
        raise ValueError("只读核心重算必须使用专用入口，不能作为验证重跑原因")
    if retry_reason == "reviewer_timeout":
        source = prepare_reviewer_timeout_source(workspace, run)
        active_plan = reactivate_current_work_item(
            source.plan,
            source.state.current_work_item,
        )
        prepared = build_prepared_verification_retry(
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


def reactivate_current_work_item(
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
        raise ValueError("恢复目标的当前 Work Item 不是 blocked 状态")
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
    return build_prepared_verification_retry(
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


def build_prepared_verification_retry(
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
    retry_reason: VerificationRetryMode,
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
    next_iteration = child_state.current_iteration + int(retry_reason != "core_evidence_recheck")
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
