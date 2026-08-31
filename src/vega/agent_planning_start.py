from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .agent_change_run import change_run_metadata, change_worktree_root
from .agent_contract import AgentPlan, AgentState, AgentWorkItem
from .agent_git_worktree import prepare_managed_worktree
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_planning import (
    PLANNING_CONTEXT_ARTIFACT,
    PLANNING_REQUEST_ARTIFACT,
    PlanningRequest,
)
from .agent_repository_binding import require_git_root
from .agent_run import AgentRun
from .agent_runtime_support import (
    save_agent_plan,
    write_run_metadata,
    write_status_card,
)
from .project_context import build_project_context
from .redaction import redact_text, write_redacted_json, write_redacted_text
from .repository_identity import repository_scope, resolve_git_revision
from .run_utils import create_run_dir
from .workspace_check import capture_review_workspace


def start_planning_run(workspace: Path, repo: Path, *, goal: str) -> AgentRun:
    """从自然语言建立同一条 ChangeRun 的只读 Planning 阶段。"""

    user_goal = redact_text(goal.strip())
    if not user_goal:
        raise ValueError("自然语言目标不能为空")
    repo_root = require_git_root(repo)
    revision = resolve_git_revision(repo_root)
    if revision is None:
        raise ValueError("目标目录不是 Git 仓库")
    project_context = build_project_context(
        workspace,
        repo_root,
        user_goal,
        tracked_revision=revision,
    )
    task_id = f"planning-{uuid4().hex[:16]}"
    plan = _planning_projection(task_id, user_goal)
    run_id, run_dir = create_run_dir(
        workspace,
        (
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid4().hex[:12]}-agent"
        ),
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
        task_id=task_id,
        repository_id=repository_scope(handle.worktree_path),
        run_kind="change",
        phase="planning",
        accepted_checkpoint_sha=revision.commit,
        current_work_item="WI-PLANNING",
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["replan", "human"],
    )
    context_sha256 = hashlib.sha256(project_context.encode("utf-8")).hexdigest()
    write_run_metadata(
        run_dir,
        handle.worktree_path,
        revision.commit,
        comparison_base_revision=revision.commit,
        comparison_paths=[],
        change_run=change_run_metadata(handle),
    )
    write_redacted_json(
        run_dir / PLANNING_REQUEST_ARTIFACT,
        PlanningRequest(
            task_id=task_id,
            user_goal=user_goal,
            source_revision=revision.commit,
            project_context_sha256=context_sha256,
        ).model_dump(mode="json"),
    )
    write_redacted_text(run_dir / PLANNING_CONTEXT_ARTIFACT, project_context)
    save_agent_plan(run_dir, plan)
    save_agent_state(run_dir / "agent-state.json", state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="planning_run_started",
        state=state,
        observation_summary="受管 Planning Workspace 已建立，尚未启动写入 Worker",
        artifact_refs=[
            PLANNING_REQUEST_ARTIFACT,
            PLANNING_CONTEXT_ARTIFACT,
            "agent-plan.json",
        ],
    )
    write_status_card(
        run_dir,
        state,
        plan,
        next_step="运行只读调查，生成带来源引用的 Planning Proposal",
    )
    return AgentRun(run_dir=run_dir, state=state, plan=plan)


def _planning_projection(task_id: str, user_goal: str) -> AgentPlan:
    return AgentPlan(
        task_id=task_id,
        user_goal=user_goal,
        unresolved_decisions=["需要完成只读调查并生成 Planning Proposal"],
        work_items=[
            AgentWorkItem(
                work_item_id="WI-PLANNING",
                objective="只读调查用户目标并形成带来源引用的 Planning Proposal",
            )
        ],
    )
