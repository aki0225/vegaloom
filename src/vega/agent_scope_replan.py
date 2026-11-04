from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .agent_approval_policy import validate_bounded_approval_freshness
from .agent_change_contract import ExecutionPlan
from .agent_change_control import change_budget_snapshot
from .agent_change_revision import assess_change_revision
from .agent_change_revision_runtime import revise_change_run
from .agent_change_run import load_change_run_context
from .agent_contract import AgentDecision, AgentObservation, AgentPlan, AgentState
from .agent_persistence import load_agent_checkpoint, read_agent_trace
from .agent_pre_core_resume import require_pre_core_evidence
from .agent_run import AgentRun
from .agent_run_status import latest_trusted_child_run
from .agent_runtime_support import bound_repo, capture_bound_workspace, load_agent_bundle
from .agent_status_evidence import build_supervisor_evidence
from .agent_worker_evidence import hash_evidence_refs, require_child_quiescent
from .scope_gate import ScopeGateResult
from .scope_path_matching import matching_patterns, scope_paths_are_case_insensitive


def replan_scope_if_eligible(
    workspace: Path, run_dir: Path, state: AgentState,
    plan: AgentPlan, metadata: dict[str, object],
) -> AgentRun | None:
    """在调用方持有 RunMutationLock 时，裁决唯一可确定修正的范围遗漏。"""
    try:
        if (
            state.run_kind != "change" or state.phase != "planning"
            or state.active_child_run or state.active_operation_id or state.operation_started
            or state.active_candidate_sha or state.active_planning_execution_id
            or state.handoff_status != "none" or state.allowed_actions != ["replan", "human"]
            or state.latest_checkpoint_id is None
        ):
            return None
        context = load_change_run_context(run_dir, state, plan, metadata)
        if context is None or not context.contract.approval_is_current() or (
            state.approved_contract_digest != context.contract.approved_digest
        ):
            return None
        if any(context.contract.side_effect_policy.model_dump(
            mode="json", exclude={"schema_version"},
        ).values()):
            return None
        repo = bound_repo(run_dir)
        validate_bounded_approval_freshness(repo, context.contract, context.execution_plan)
        require_child_quiescent(run_dir)
        require_pre_core_evidence(workspace, run_dir, state)
        before = capture_bound_workspace(run_dir)
        if (
            before.fingerprint != state.workspace_fingerprint
            or before.head_sha != state.accepted_checkpoint_sha
            or before.unsafe_index_paths or not before.git_control_complete
        ):
            return None
        source_ref = f"checkpoints/{state.latest_checkpoint_id}.json"
        source = load_agent_checkpoint(_ref_path(run_dir, source_ref))
        if source.state_version + 1 != state.state_version:
            return None
        observation, scope = _scope_source(run_dir, state, plan, source_ref)
        proposed = _correct_scope(repo, context.execution_plan, state, scope)
        validate_bounded_approval_freshness(repo, context.contract, proposed)
        budget = change_budget_snapshot(run_dir, state, context.contract)
        # 自动修订不能开启新的 attempt epoch 来绕过原合同的 Worker 配额。
        if budget.worker_attempts_used >= budget.max_repair_rounds + 1:
            return None
        assessment = assess_change_revision(
            repo=repo, changed_files=list(before.changed_files),
            current_contract=context.contract, proposed_contract=context.contract,
            current_plan=context.execution_plan, proposed_plan=proposed, budget=budget,
        )
        if assessment.outcome != "auto_apply" or set(before.changed_files) != set(observation.changed_files):
            return None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # 资格不足不写状态、不启动模型，也不把一般 replan 解释为可自动继续。
        return None
    return revise_change_run(
        run_dir, state, plan, metadata, proposed_contract=context.contract,
        proposed_execution_plan=proposed, scope_replan_source=source_ref,
    )


def require_scope_replan_resume(workspace: Path, run_dir: Path, state: AgentState) -> None:
    """重入时重算范围修订，不凭 ready 或 auto_apply 事件授予 Writer。"""
    _, persisted, plan, metadata = load_agent_bundle(workspace, run_dir.name)
    context = load_change_run_context(run_dir, state, plan, metadata)
    if persisted != state or context is None or state.phase != "ready":
        raise ValueError("自动范围修订的当前状态不一致")
    trace = read_agent_trace(run_dir / "trace.jsonl")
    event = trace[-1] if trace else {}
    current_ref = f"checkpoints/{state.latest_checkpoint_id}.json"
    checkpoint = load_agent_checkpoint(_ref_path(run_dir, current_ref))
    source_refs = [ref for ref in checkpoint.evidence_refs if ref.startswith("checkpoints/")]
    if (
        event.get("event") != "change_execution_plan_auto_applied"
        or event.get("run_id") != state.run_id or event.get("state_version") != state.state_version
        or event.get("work_item") != state.current_work_item
        or event.get("workspace_fingerprint") != state.workspace_fingerprint
        or current_ref not in event.get("artifact_refs", []) or len(source_refs) != 1
        or source_refs[0] not in event.get("artifact_refs", [])
        or checkpoint.run_id != state.run_id or checkpoint.state_version + 1 != state.state_version
        or checkpoint.phase != "ready" or checkpoint.status != "safe"
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.pending_actions != ["next", "replan", "human"]
    ):
        raise ValueError("自动范围修订缺少绑定的来源 Checkpoint")
    previous = context.execution_plan.plan_revision - 1
    execution_ref = f"execution-plans/execution-plan-revision-{previous:03d}.json"
    plan_ref = f"plans/plan-revision-{previous:03d}.json"
    if not {execution_ref, plan_ref}.issubset(checkpoint.evidence_refs):
        raise ValueError("自动范围修订缺少原计划归档")
    old_execution = ExecutionPlan.model_validate(_read_ref(run_dir, execution_ref))
    old_plan = AgentPlan.model_validate(_read_ref(run_dir, plan_ref))
    from .agent_change_run import validate_change_projection

    validate_change_projection(context.contract, old_execution, old_plan)
    _, scope = _scope_source(run_dir, state, old_plan, source_refs[0])
    expected = _correct_scope(bound_repo(run_dir), old_execution, state, scope)
    if expected != context.execution_plan:
        raise ValueError("自动范围修订改变了文件清单以外的执行安排")


def _scope_source(
    run_dir: Path, state: AgentState, plan: AgentPlan, source_ref: str,
) -> tuple[AgentObservation, ScopeGateResult]:
    _require_scope_progress(plan, state)
    source = load_agent_checkpoint(_ref_path(run_dir, source_ref))
    if (
        source_ref != f"checkpoints/{source.checkpoint_id}.json"
        or source.run_id != state.run_id or source.phase != "planning" or source.status != "blocked"
        or source.current_work_item != state.current_work_item or source.active_child_run
        or source.workspace_fingerprint != state.workspace_fingerprint
        or source.external_side_effects != "none" or source.pending_actions != ["replan", "human"]
        or not source.failed_attempts
    ):
        raise ValueError("自动范围修订缺少同现场的失败 Checkpoint")
    observation_refs = [ref for ref in source.evidence_refs if ref.startswith("observations/")]
    decision_refs = [ref for ref in source.evidence_refs if ref.startswith("decisions/")]
    if len(observation_refs) != 1 or len(decision_refs) != 1:
        raise ValueError("自动范围修订缺少唯一 Observation 与 Decision")
    observation = AgentObservation.model_validate(_read_ref(run_dir, observation_refs[0]))
    decision = AgentDecision.model_validate(_read_ref(run_dir, decision_refs[0]))
    if (
        observation_refs[0] != f"observations/{observation.observation_id}.json"
        or decision_refs[0] != f"decisions/{decision.decision_id}.json"
        or decision.observation_id != observation.observation_id or decision.selected_action != "replan"
        or decision.reason_code != "approval.plan_contradicted"
        or observation.authority != "machine_reconcile" or not observation.plan_contradicted
        or observation.worker_alive or not observation.operation_started or not observation.workspace_explained
        or observation.external_side_effects != "none"
        or observation.work_item_completed or observation.work_item_id != state.current_work_item
        or observation.child_run != source.failed_attempts[-1]
        or observation.workspace_fingerprint != state.workspace_fingerprint
        or any(value != "not_run" for value in (
            observation.verification, observation.risk, observation.review, observation.core_evidence,
        ))
        or hash_evidence_refs(run_dir, observation.evidence_refs) != observation.evidence_sha256
    ):
        raise ValueError("自动范围修订的机器来源不可信")
    latest_trusted_child_run(run_dir, state, observation=observation)
    evidence = build_supervisor_evidence(run_dir, state, observation, plan)
    if len(evidence) != 4 or evidence[0].status != "passed" or evidence[1].status != "failed":
        raise ValueError("自动范围修订缺少可信 Worker 执行或范围失败证据")
    refs = [ref for ref in observation.evidence_refs if ref.startswith("plan-scope/")]
    if len(refs) != 1 or not refs[0].startswith("plan-scope/post-worker-"):
        raise ValueError("自动范围修订只接受 Worker 后的范围遗漏")
    scope = ScopeGateResult.model_validate(_read_ref(run_dir, refs[0]))
    if (
        scope.status != "failed" or scope.failure_code or scope.unsafe_index_paths
        or scope.expected_head_sha != state.accepted_checkpoint_sha
        or scope.current_head_sha != state.accepted_checkpoint_sha
        or not scope.violations or any(item.code != "outside_allowed_paths" for item in scope.violations)
    ):
        raise ValueError("范围失败包含不能自动修订的问题")
    return observation, scope


def _require_scope_progress(plan: AgentPlan, state: AgentState) -> None:
    """只重激活本次失败项，避免通用 revision 投影顺带恢复其他被废弃的项。"""
    found = False
    for item in plan.work_items:
        if item.work_item_id == state.current_work_item:
            if item.status != "superseded":
                raise ValueError("当前项不是范围失败产生的 superseded 项")
            found = True
        elif item.status not in {"completed", "pending"} or (not found and item.status != "completed"):
            raise ValueError("范围修订不能重激活其他工作项或改变执行顺序")
    if not found:
        raise ValueError("范围修订缺少当前工作项")


def _correct_scope(
    repo: Path, execution: ExecutionPlan, state: AgentState, scope: ScopeGateResult,
) -> ExecutionPlan:
    current = next(item for item in execution.work_items if item.work_item_id == state.current_work_item)
    additions = sorted({item.path for item in scope.violations})
    case_sensitive = not scope_paths_are_case_insensitive(repo)
    other_paths = [path for item in execution.work_items if item != current for path in item.likely_files]
    if any(
        matching_patterns(path, [*current.likely_files, *other_paths], case_sensitive=case_sensitive)
        or any(character in path for character in "*?[")
        for path in additions
    ):
        raise ValueError("遗漏路径属于其他工作项、已被覆盖或含通配符，不能自动扩大当前项")
    proposed = execution.model_copy(deep=True)
    proposed.plan_revision += 1
    item = next(item for item in proposed.work_items if item.work_item_id == state.current_work_item)
    item.likely_files.extend(additions)
    return ExecutionPlan.model_validate(proposed.model_dump(mode="json"))


def _ref_path(run_dir: Path, ref: str) -> Path:
    path = (run_dir / ref).resolve(strict=True)
    if not path.is_relative_to(run_dir.resolve(strict=True)):
        raise ValueError("范围修订证据越过 run 边界")
    return path


def _read_ref(run_dir: Path, ref: str) -> object:
    return json.loads(_ref_path(run_dir, ref).read_text(encoding="utf-8"))
