from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .agent_contract import (
    AgentPlan,
    AgentState,
    AgentWorkItem,
    approve_plan,
    validate_v1_execution_binding,
    validate_v1_execution_plan,
)
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_run import AgentRun
from .agent_runtime_logic import new_task_id, update_state
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


def start_legacy_agent(
    workspace: Path,
    repo: Path,
    *,
    goal: str,
    plan: AgentPlan | None,
) -> AgentRun:
    repo_root = require_git_root(repo)
    revision = resolve_git_revision(repo_root)
    if revision is None:
        raise ValueError("目标目录不是 Git 仓库")
    base_plan = plan or AgentPlan(
        task_id=new_task_id(),
        user_goal=goal,
        unresolved_decisions=["需要主会话完成只读调查并提交可执行 Plan"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="调查任务、确认范围并补充可批准计划",
            )
        ],
    )
    current_work_item = base_plan.work_items[0].work_item_id
    if plan:
        current_work_item = validate_v1_execution_plan(base_plan).work_item_id
    if base_plan.approved:
        raise ValueError("新 Agent run 不能接受预先批准的 Plan")
    if base_plan.user_goal != goal.strip():
        raise ValueError("显式 Plan 与用户目标不一致")
    snapshot = capture_review_workspace(repo_root)
    run_id, run_dir = create_run_dir(
        workspace,
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-agent",
    )
    state = AgentState(
        run_id=run_id,
        task_id=base_plan.task_id,
        repository_id=repository_scope(repo_root),
        phase="awaiting_approval",
        goal_revision=base_plan.goal_revision,
        plan_revision=base_plan.plan_revision,
        current_work_item=current_work_item,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["replan", "human"],
    )
    write_run_metadata(run_dir, repo_root, revision.commit)
    save_agent_plan(run_dir, base_plan)
    save_agent_state(run_dir / "agent-state.json", state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="agent_started",
        state=state,
        observation_summary="已捕获初始 Workspace，等待人工批准计划",
    )
    write_status_card(run_dir, state, base_plan)
    return AgentRun(run_dir=run_dir, state=state, plan=base_plan)


def approve_legacy_plan(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    actor: str,
    write_checkpoint_fn=write_checkpoint,
    write_task_brief_fn=write_task_brief,
) -> AgentRun:
    if state.phase != "awaiting_approval" or state.active_child_run:
        raise ValueError("当前状态不允许批准 Plan")
    if plan.unresolved_decisions:
        raise ValueError("Plan 仍有未解决决策，不能批准")
    work_item = validate_v1_execution_binding(plan, state.current_work_item)
    repo = bound_repo(run_dir)
    require_verification_commands_preflight(repo, work_item.verification)
    approved = approve_plan(plan, actor=actor)
    # 批准前准备 assist 受控目录，让 safe Checkpoint 能解释首个 child 的真实前置现场。
    prepare_verification_temp_root(repo)
    snapshot = capture_bound_workspace(run_dir)
    ready_state = update_state(
        state,
        phase="ready",
        state_version=state.state_version + 1,
        goal_revision=approved.goal_revision,
        plan_revision=approved.plan_revision,
        approved_plan_digest=approved.approved_digest,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["next", "replan", "human"],
    )
    save_agent_plan(run_dir, approved)
    checkpoint = write_checkpoint_fn(
        run_dir,
        ready_state,
        snapshot,
        reason="Plan 已批准",
        status="safe",
        pending_actions=["next", "replan", "human"],
    )
    ready_state = update_state(
        ready_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=ready_state.state_version + 1,
    )
    task_brief = write_task_brief_fn(
        run_dir,
        approved,
        ready_state,
        checkpoint,
    )
    save_agent_state(run_dir / "agent-state.json", ready_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="plan_approved",
        state=ready_state,
        observation_summary=f"Task Brief {task_brief.utf8_bytes} bytes",
        artifact_refs=[
            "agent-plan.json",
            "task-brief.md",
            "task-brief-manifest.json",
        ],
    )
    write_status_card(run_dir, ready_state, approved)
    return AgentRun(run_dir=run_dir, state=ready_state, plan=approved)
