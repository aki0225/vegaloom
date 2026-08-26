from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from .agent_change_contract import (
    ChangeContract,
    ExecutionPlan,
    approve_change_contract,
    validate_execution_plan_against_contract,
)
from .agent_change_run import (
    CHANGE_CONTRACT_ARTIFACT,
    EXECUTION_PLAN_ARTIFACT,
    change_run_metadata,
    change_worktree_root,
    current_change_work_item,
    load_candidate_artifact,
    load_change_run_context,
    project_agent_plan,
    save_change_run_artifacts,
    write_candidate_artifact,
)
from .agent_contract import AgentPlan, AgentState
from .agent_git_candidate import (
    CandidateCommit,
    restore_candidate_for_repair,
    validate_candidate_binding,
)
from .agent_git_worktree import prepare_managed_worktree
from .agent_persistence import append_agent_trace, load_agent_checkpoint, save_agent_state
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    require_git_root,
    save_agent_plan,
    write_checkpoint,
    write_run_metadata,
    write_status_card,
    write_task_brief,
)
from .repository_identity import repository_scope, resolve_git_revision
from .run_utils import create_run_dir
from .verification_command_preflight import require_verification_commands_preflight
from .workspace_check import capture_review_workspace
from .workspace_inventory import prepare_verification_temp_root


def start_change_run(
    workspace: Path,
    repo: Path,
    *,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    require_dependencies: Callable[[], None],
) -> AgentRun:
    """创建由 Approved Contract 和隔离 Worktree 驱动的 ChangeRun。"""

    repo_root = require_git_root(repo)
    require_dependencies()
    revision = resolve_git_revision(repo_root)
    if revision is None:
        raise ValueError("目标目录不是 Git 仓库")
    if contract.approved:
        raise ValueError("新 ChangeRun 不能接受预先批准的 Contract")
    validate_execution_plan_against_contract(contract, execution_plan)
    projected = project_agent_plan(contract, execution_plan)
    run_id, run_dir = create_run_dir(
        workspace,
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-agent",
    )
    handle = prepare_managed_worktree(
        repo_root,
        workspace_root=change_worktree_root(workspace, repo_root),
        run_id=run_id,
        base_revision=revision.commit,
    )
    snapshot = capture_review_workspace(
        handle.worktree_path,
        comparison_base_sha=revision.commit,
    )
    state = AgentState(
        run_id=run_id,
        task_id=contract.task_id,
        repository_id=repository_scope(handle.worktree_path),
        run_kind="change",
        phase="awaiting_approval",
        goal_revision=contract.contract_revision,
        plan_revision=execution_plan.plan_revision,
        contract_revision=contract.contract_revision,
        execution_plan_revision=execution_plan.plan_revision,
        accepted_checkpoint_sha=revision.commit,
        current_work_item=execution_plan.work_items[0].work_item_id,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["replan", "human"],
    )
    write_run_metadata(
        run_dir,
        handle.worktree_path,
        revision.commit,
        comparison_base_revision=revision.commit,
        comparison_paths=[],
        change_run=change_run_metadata(handle),
    )
    save_change_run_artifacts(run_dir, contract, execution_plan)
    save_agent_plan(run_dir, projected)
    save_agent_state(run_dir / "agent-state.json", state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="change_run_started",
        state=state,
        observation_summary="隔离 Worktree 已建立，等待人工批准 Contract",
        artifact_refs=[
            CHANGE_CONTRACT_ARTIFACT,
            EXECUTION_PLAN_ARTIFACT,
            "agent-plan.json",
        ],
    )
    write_status_card(
        run_dir,
        state,
        projected,
        next_step="人工审查 Approved Contract 与 Execution Plan",
    )
    return AgentRun(run_dir=run_dir, state=state, plan=projected)


def approve_change_run(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    *,
    actor: str,
    write_checkpoint_fn=write_checkpoint,
    write_task_brief_fn=write_task_brief,
) -> AgentRun:
    if state.phase != "awaiting_approval" or state.active_child_run:
        raise ValueError("当前状态不允许批准 Contract")
    context = load_change_run_context(run_dir, state, plan, metadata)
    assert context is not None
    if context.execution_plan.unresolved_decisions:
        raise ValueError("Execution Plan 仍有未解决决策，不能批准")
    work_item = current_change_work_item(plan, state)
    repo = bound_repo(run_dir)
    require_verification_commands_preflight(repo, work_item.verification)
    approved_contract = approve_change_contract(context.contract, actor=actor)
    approved_plan = project_agent_plan(
        approved_contract,
        context.execution_plan,
        current=plan,
    )
    prepare_verification_temp_root(repo)
    snapshot = capture_bound_workspace(run_dir)
    ready_state = update_state(
        state,
        phase="ready",
        state_version=state.state_version + 1,
        goal_revision=approved_contract.contract_revision,
        plan_revision=context.execution_plan.plan_revision,
        approved_plan_digest=approved_plan.approved_digest,
        approved_contract_digest=approved_contract.approved_digest,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["next", "replan", "human"],
    )
    save_change_run_artifacts(run_dir, approved_contract, context.execution_plan)
    save_agent_plan(run_dir, approved_plan)
    checkpoint = write_checkpoint_fn(
        run_dir,
        ready_state,
        snapshot,
        reason="Approved Contract 已批准",
        status="safe",
        pending_actions=["next", "replan", "human"],
        evidence_refs=[CHANGE_CONTRACT_ARTIFACT, EXECUTION_PLAN_ARTIFACT],
    )
    ready_state = update_state(
        ready_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=ready_state.state_version + 1,
    )
    task_brief = write_task_brief_fn(
        run_dir,
        approved_plan,
        ready_state,
        checkpoint,
    )
    save_agent_state(run_dir / "agent-state.json", ready_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="change_contract_approved",
        state=ready_state,
        observation_summary=f"Task Brief {task_brief.utf8_bytes} bytes",
        artifact_refs=[
            CHANGE_CONTRACT_ARTIFACT,
            EXECUTION_PLAN_ARTIFACT,
            "agent-plan.json",
            "task-brief.md",
            "task-brief-manifest.json",
        ],
    )
    write_status_card(run_dir, ready_state, approved_plan)
    return AgentRun(run_dir=run_dir, state=ready_state, plan=approved_plan)


def bind_change_candidate(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    candidate: CandidateCommit,
) -> tuple[AgentRun, str]:
    """在 Core 启动前把 Candidate SHA 绑定到当前 Writer operation。"""

    _require_candidate_writer_binding(state, candidate)
    context = load_change_run_context(run_dir, state, plan, metadata)
    assert context is not None
    validate_candidate_binding(
        context.worktree,
        candidate=candidate,
        contract=context.contract,
        execution_plan=context.execution_plan,
    )
    candidate_ref = write_candidate_artifact(run_dir, candidate)
    snapshot = capture_bound_workspace(run_dir)
    bound_state = update_state(
        state,
        state_version=state.state_version + 1,
        active_candidate_sha=candidate.candidate_sha,
        workspace_fingerprint=snapshot.fingerprint,
    )
    save_agent_state(run_dir / "agent-state.json", bound_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="candidate_bound",
        state=bound_state,
        observation_summary=(
            f"{candidate.work_item_id} Candidate 已冻结："
            f"{candidate.candidate_sha[:12]}"
        ),
        artifact_refs=[candidate_ref],
    )
    write_status_card(
        run_dir,
        bound_state,
        plan,
        next_step="在同一 Candidate SHA 上运行 Verification、Risk 与 Reviewer",
    )
    return AgentRun(run_dir=run_dir, state=bound_state, plan=plan), candidate_ref


def settle_change_candidate(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    *,
    candidate_ref: str,
    outcome: str,
) -> AgentRun:
    """采用已通过 Candidate，或把失败 Candidate 还原为可继续修复的 WIP。"""

    if outcome not in {"accept", "repair"}:
        raise ValueError("Candidate outcome 只能是 accept 或 repair")
    if state.run_kind != "change" or state.active_child_run:
        raise ValueError("当前状态不能处理 ChangeRun Candidate")
    context = load_change_run_context(run_dir, state, plan, metadata)
    assert context is not None
    candidate = load_candidate_artifact(run_dir, candidate_ref)
    if (
        state.active_candidate_sha != candidate.candidate_sha
        or candidate.run_id != state.run_id
    ):
        raise ValueError("Candidate Artifact 与当前 ChangeRun State 不一致")
    previous = _latest_checkpoint(run_dir, state)
    evidence_refs = list(dict.fromkeys([*previous.evidence_refs, candidate_ref]))
    next_state, reason = _candidate_outcome(
        run_dir,
        state,
        plan,
        context,
        candidate,
        outcome,
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        capture_bound_workspace(run_dir),
        reason=reason,
        status="safe",
        pending_actions=list(next_state.allowed_actions),
        evidence_refs=evidence_refs,
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    if next_state.phase == "ready":
        write_task_brief(
            run_dir,
            plan,
            next_state,
            checkpoint,
            artifact_refs=[candidate_ref],
        )
    save_agent_state(run_dir / "agent-state.json", next_state)
    event = (
        "candidate_accepted"
        if outcome == "accept"
        else "candidate_restored_for_repair"
    )
    append_agent_trace(
        run_dir / "trace.jsonl",
        event=event,
        state=next_state,
        observation_summary=reason,
        artifact_refs=[
            candidate_ref,
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ],
    )
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=_candidate_next_step(outcome, next_state),
    )
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def _candidate_outcome(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    context,
    candidate: CandidateCommit,
    outcome: str,
) -> tuple[AgentState, str]:
    if outcome == "repair":
        return _restore_failed_candidate(run_dir, state, context, candidate)
    if state.phase not in {"ready", "finalizing"}:
        raise ValueError("只有 next/finalize 路由可以接受 Candidate")
    completed = next(
        (
            item
            for item in plan.work_items
            if item.work_item_id == candidate.work_item_id
        ),
        None,
    )
    if completed is None or completed.status != "completed":
        raise ValueError("Candidate 对应 Work Item 尚未通过 Core")
    validate_candidate_binding(
        context.worktree,
        candidate=candidate,
        contract=context.contract,
        execution_plan=context.execution_plan,
    )
    snapshot = capture_bound_workspace(run_dir)
    return (
        update_state(
            state,
            state_version=state.state_version + 1,
            accepted_checkpoint_sha=candidate.candidate_sha,
            active_candidate_sha=None,
            workspace_fingerprint=snapshot.fingerprint,
        ),
        f"{candidate.work_item_id} Candidate 已成为 Accepted Checkpoint",
    )


def _restore_failed_candidate(
    run_dir: Path,
    state: AgentState,
    context,
    candidate: CandidateCommit,
) -> tuple[AgentState, str]:
    if state.phase != "ready" or "repair" not in state.allowed_actions:
        raise ValueError("当前路由没有授权 Candidate repair")
    if state.current_work_item != candidate.work_item_id:
        raise ValueError("repair Candidate 与当前 Work Item 不一致")
    restore_candidate_for_repair(
        context.worktree,
        candidate=candidate,
        contract=context.contract,
        execution_plan=context.execution_plan,
    )
    snapshot = capture_bound_workspace(run_dir)
    return (
        update_state(
            state,
            state_version=state.state_version + 1,
            active_candidate_sha=None,
            workspace_fingerprint=snapshot.fingerprint,
        ),
        f"{candidate.work_item_id} Candidate 已还原为待修复 WIP",
    )


def _require_candidate_writer_binding(
    state: AgentState,
    candidate: CandidateCommit,
) -> None:
    if state.run_kind != "change":
        raise ValueError("legacy Agent run 不接受 Git Candidate")
    if (
        state.phase != "acting"
        or not state.active_operation_id
        or not state.active_child_run
        or candidate.operation_id != state.active_operation_id
        or candidate.work_item_id != state.current_work_item
        or candidate.run_id != state.run_id
    ):
        raise ValueError("Candidate 与当前 Writer binding 不一致")


def _latest_checkpoint(run_dir: Path, state: AgentState):
    if state.latest_checkpoint_id is None:
        raise ValueError("ChangeRun 缺少最新 Checkpoint")
    return load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )


def _candidate_next_step(outcome: str, state: AgentState) -> str:
    if outcome == "repair":
        return "启动新的 Worker attempt 修复当前 WIP"
    if state.phase == "ready":
        return "继续下一个 Work Item"
    return "采用当前 Accepted Checkpoint 发布最终结论"
