from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .agent_contract import AgentObservation, AgentPlan, AgentState, AgentWorkItem, approve_plan
from .agent_persistence import (
    append_agent_trace,
    save_agent_state,
)
from .agent_graph import langgraph_available, record_supervisor_route
from .agent_mutation import agent_mutation
from .agent_run import AgentRun
from .agent_routing import decide_next_action, transition_state
from .agent_runtime_logic import (
    apply_work_item_progress,
    invalidate_plan_for_steer,
    new_task_id,
    next_pending_work_item,
    update_state,
)
from .agent_task_card import load_task_card
from .agent_runtime_support import (
    capture_bound_workspace,
    require_git_root,
    resolve_resume_task,
    state_from_task_card,
    validate_resume_workspace,
    write_checkpoint,
    load_agent_bundle,
    save_agent_plan,
    write_run_metadata,
    write_status_card,
    write_task_brief,
)
from .redaction import write_redacted_json
from .repository_identity import repository_scope, resolve_git_revision
from .run_utils import create_run_dir
from .workspace_check import capture_review_workspace


class SupervisorAgentRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def start(
        self,
        repo: Path,
        *,
        goal: str,
        plan: AgentPlan | None = None,
    ) -> AgentRun:
        repo_root = require_git_root(repo)
        if not langgraph_available():
            raise ValueError("当前环境缺少 LangGraph；请先安装项目的 agent 可选依赖")
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
        if base_plan.approved:
            raise ValueError("新 Agent run 不能接受预先批准的 Plan")
        if base_plan.user_goal != goal.strip():
            raise ValueError("显式 Plan 与用户目标不一致")
        snapshot = capture_review_workspace(repo_root)
        run_id, run_dir = create_run_dir(
            self.workspace,
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-agent",
        )
        state = AgentState(
            run_id=run_id,
            task_id=base_plan.task_id,
            repository_id=repository_scope(repo_root),
            phase="awaiting_approval",
            goal_revision=base_plan.goal_revision,
            plan_revision=base_plan.plan_revision,
            current_work_item=base_plan.work_items[0].work_item_id,
            workspace_fingerprint=snapshot.fingerprint,
            allowed_actions=["replan", "human"],
        )
        write_run_metadata(run_dir, repo_root, revision.commit)
        self._save_plan(run_dir, base_plan)
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="agent_started",
            state=state,
            observation_summary="已捕获初始 Workspace，等待人工批准计划",
        )
        write_status_card(run_dir, state, base_plan)
        return AgentRun(run_dir=run_dir, state=state, plan=base_plan)

    @agent_mutation("agent.approve")
    def approve(self, run: str, *, actor: str = "human") -> AgentRun:
        run_dir, state, plan, _ = self._load_run(run)
        if state.phase != "awaiting_approval" or state.active_child_run:
            raise ValueError("当前状态不允许批准 Plan")
        if plan.unresolved_decisions:
            raise ValueError("Plan 仍有未解决决策，不能批准")
        approved = approve_plan(plan, actor=actor)
        snapshot = capture_bound_workspace(run_dir)
        state = update_state(
            state,
            phase="ready",
            state_version=state.state_version + 1,
            approved_plan_digest=approved.approved_digest,
            workspace_fingerprint=snapshot.fingerprint,
            allowed_actions=["next", "replan", "human"],
        )
        self._save_plan(run_dir, approved)
        save_agent_state(run_dir / "agent-state.json", state)
        checkpoint = write_checkpoint(
            run_dir,
            state,
            snapshot,
            reason="Plan 已批准",
            status="safe",
            pending_actions=["next", "replan", "human"],
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", state)
        task_brief = write_task_brief(run_dir, approved, state, checkpoint)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="plan_approved",
            state=state,
            observation_summary=f"Task Brief {task_brief.utf8_bytes} bytes",
            artifact_refs=["agent-plan.json", "task-brief.md", "task-brief-manifest.json"],
        )
        write_status_card(run_dir, state, approved)
        return AgentRun(run_dir=run_dir, state=state, plan=approved)

    @agent_mutation("agent.plan")
    def update_plan(self, run: str, draft: AgentPlan) -> AgentRun:
        run_dir, state, current, _ = self._load_run(run)
        if state.active_child_run:
            raise ValueError("Writer 仍在运行，不能替换 Plan")
        if draft.task_id != current.task_id or draft.user_goal != current.user_goal:
            raise ValueError("新 Plan 必须保持 task_id 与用户目标不变")
        if draft.approved:
            raise ValueError("新 Plan 必须先以未批准状态写入")
        revised = draft.model_copy(
            update={
                "goal_revision": state.goal_revision,
                "plan_revision": state.plan_revision + 1,
            }
        )
        revised = AgentPlan.model_validate(revised.model_dump(mode="json"))
        first_item = revised.work_items[0]
        snapshot = capture_bound_workspace(run_dir)
        state = update_state(
            state,
            phase="awaiting_approval",
            state_version=state.state_version + 1,
            plan_revision=revised.plan_revision,
            approved_plan_digest=None,
            current_work_item=first_item.work_item_id,
            workspace_fingerprint=snapshot.fingerprint,
            allowed_actions=["replan", "human"],
        )
        self._save_plan(run_dir, revised)
        save_agent_state(run_dir / "agent-state.json", state)
        checkpoint = write_checkpoint(
            run_dir,
            state,
            snapshot,
            reason="主会话提交新的 Plan revision，等待人工批准",
            status="blocked",
            pending_actions=["replan", "human"],
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="plan_revised",
            state=state,
            observation_summary=f"Plan revision {revised.plan_revision} 已写入",
            artifact_refs=["agent-plan.json", f"checkpoints/{checkpoint.checkpoint_id}.json"],
        )
        write_status_card(
            run_dir,
            state,
            revised,
            checkpoint=checkpoint,
            next_step="人工审查新 Plan revision；未批准前不能启动 Worker",
        )
        return AgentRun(run_dir=run_dir, state=state, plan=revised)

    @agent_mutation("agent.observe")
    def observe(
        self,
        run: str,
        observation: AgentObservation,
    ) -> AgentRun:
        run_dir, state, plan, _ = self._load_run(run)
        if not state.active_child_run or not state.active_operation_id:
            raise ValueError("Observation 必须绑定当前 active Writer，不能从空闲状态伪造完成")
        if state.phase not in {"acting", "observing", "needs_human"}:
            raise ValueError(f"当前阶段不能接受 Observation：{state.phase}")
        if (
            state.active_child_run
            and observation.worker_alive
            and observation.work_item_completed
        ):
            raise ValueError("Worker 仍存活时不能接受 Work Item 完成 Observation")
        if (
            state.active_child_run
            and observation.operation_started != state.operation_started
        ):
            raise ValueError("Observation 与持久化 operation_started 不一致")
        attempt = state.active_child_run
        actual = capture_bound_workspace(run_dir)
        reconciled = observation.model_copy(
            update={
                "workspace_fingerprint": actual.fingerprint,
                "changed_files": list(actual.changed_files),
                "unknown_file_count": len(actual.untracked_files),
                "workspace_explained": (
                    observation.workspace_explained
                    and not actual.unsafe_index_paths
                    and actual.git_control_complete
                ),
            }
        )
        write_redacted_json(
            run_dir / "observations" / f"{reconciled.observation_id}.json",
            reconciled.model_dump(mode="json"),
        )
        decision = decide_next_action(plan, reconciled)
        write_redacted_json(
            run_dir / "decisions" / f"{decision.decision_id}.json",
            decision.model_dump(mode="json"),
        )
        interrupted = record_supervisor_route(run_dir, state, decision)
        if interrupted != (decision.selected_action in {"replan", "human"}):
            raise ValueError("LangGraph interrupt 与确定性 Decision 不一致")
        if state.workspace_fingerprint != reconciled.workspace_fingerprint:
            state = update_state(
                state,
                workspace_fingerprint=reconciled.workspace_fingerprint,
            )
        plan = apply_work_item_progress(plan, state, reconciled, decision.selected_action)
        self._save_plan(run_dir, plan)
        state = transition_state(state, plan, reconciled, decision)
        if decision.selected_action == "next":
            next_item = next_pending_work_item(plan, state.current_work_item)
            if next_item is None:
                raise ValueError("Decision 选择 next，但 Plan 没有下一项待执行 Work Item")
            state = update_state(state, current_work_item=next_item.work_item_id)
        checkpoint_status = (
            "safe"
            if decision.selected_action in {"next", "repair", "finalize"}
            else "blocked"
        )
        pending_actions = (
            list(decision.allowed_actions)
            if checkpoint_status == "safe"
            else ["replan", "human"]
            if "replan" in decision.allowed_actions
            else ["human"]
        )
        checkpoint = write_checkpoint(
            run_dir,
            state,
            actual,
            reason=decision.reason,
            status=checkpoint_status,
            pending_actions=pending_actions,
            evidence_refs=[
                f"observations/{reconciled.observation_id}.json",
                f"decisions/{decision.decision_id}.json",
            ],
            completed_attempts=(
                [attempt]
                if attempt and reconciled.work_item_completed
                else []
            ),
            failed_attempts=(
                [attempt]
                if attempt
                and not reconciled.work_item_completed
                and not reconciled.worker_alive
                and decision.selected_action in {"repair", "replan", "human"}
                else []
            ),
            operation_started=reconciled.operation_started,
            external_side_effects=reconciled.external_side_effects,
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event=f"supervisor_{decision.selected_action}",
            state=state,
            observation_summary=reconciled.machine_summary,
            route_reason=decision.reason,
            artifact_refs=[
                f"observations/{reconciled.observation_id}.json",
                f"decisions/{decision.decision_id}.json",
                f"checkpoints/{checkpoint.checkpoint_id}.json",
            ],
        )
        write_status_card(
            run_dir,
            state,
            plan,
            observation=reconciled,
            checkpoint=checkpoint,
            next_step=(
                "调用现有 Vega Finish；当前 Graph 仅进入 finalizing，不能自行宣称成功"
                if decision.selected_action == "finalize"
                else decision.reason
            ),
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)

    @agent_mutation("agent.steer")
    def steer(self, run: str, *, instruction: str) -> AgentRun:
        if not instruction.strip():
            raise ValueError("steer instruction 不能为空")
        run_dir, state, plan, _ = self._load_run(run)
        if state.active_child_run:
            raise ValueError("Writer 仍在运行，先停止并对账后才能修改 Plan")
        snapshot = capture_bound_workspace(run_dir)
        revised = invalidate_plan_for_steer(plan, instruction)
        state = update_state(
            state,
            phase="awaiting_approval",
            state_version=state.state_version + 1,
            goal_revision=revised.goal_revision,
            plan_revision=revised.plan_revision,
            approved_plan_digest=None,
            workspace_fingerprint=snapshot.fingerprint,
            allowed_actions=["replan", "human"],
        )
        self._save_plan(run_dir, revised)
        save_agent_state(run_dir / "agent-state.json", state)
        checkpoint = write_checkpoint(
            run_dir,
            state,
            snapshot,
            reason="人工 steer 改变约束，旧 Plan 批准已失效",
            status="blocked",
            pending_actions=["replan", "human"],
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="plan_invalidated_by_steer",
            state=state,
            observation_summary="新增人工约束，等待新 Plan revision 批准",
            route_reason=instruction.strip(),
            artifact_refs=["agent-plan.json", f"checkpoints/{checkpoint.checkpoint_id}.json"],
        )
        write_status_card(
            run_dir,
            state,
            revised,
            checkpoint=checkpoint,
            next_step="根据新增约束修订 Plan，并重新请求人工批准",
        )
        return AgentRun(run_dir=run_dir, state=state, plan=revised)

    def status(self, run: str) -> str:
        run_dir, state, plan, _ = self._load_run(run)
        status_path = run_dir / "status-card.md"
        if not status_path.exists():
            write_status_card(run_dir, state, plan)
        return status_path.read_text(encoding="utf-8")

    def state_path(self, run: str) -> Path:
        run_dir, _, _, _ = self._load_run(run)
        return run_dir / "agent-state.json"

    def resume_task_card(self, repo: Path, task_path: Path | None = None) -> AgentRun:
        repo_root = require_git_root(repo)
        resolved_task, relative_task = resolve_resume_task(repo_root, task_path)
        card = load_task_card(resolved_task)
        snapshot = validate_resume_workspace(repo_root, card)
        revision = resolve_git_revision(repo_root)
        assert revision is not None
        run_id, run_dir = create_run_dir(
            self.workspace,
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-agent-resume",
        )
        state = state_from_task_card(run_id, repo_root, card, snapshot)
        write_run_metadata(run_dir, repo_root, revision.commit, task_card=relative_task)
        self._save_plan(run_dir, card.plan)
        save_agent_state(run_dir / "agent-state.json", state)
        checkpoint = write_checkpoint(
            run_dir,
            state,
            snapshot,
            reason="已从 Git 跟踪的 Resume Capsule 建立新本机 run",
            status="safe" if card.handoff_status == "handoff_ready" else "blocked",
            pending_actions=list(state.allowed_actions),
            evidence_refs=[relative_task],
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", state)
        capsule = card.resume_capsule
        write_task_brief(
            run_dir,
            card.plan,
            state,
            checkpoint,
            confirmed_facts=capsule.confirmed_facts if capsule else [],
            failed_attempts=capsule.failed_attempts if capsule else [],
        )
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="task_card_resumed",
            state=state,
            observation_summary="旧门禁已作为历史证据，当前现场已重新对账",
            artifact_refs=[relative_task, "task-brief.md"],
        )
        write_status_card(
            run_dir,
            state,
            card.plan,
            checkpoint=checkpoint,
            next_step=(
                capsule.next_step if capsule is not None else "人工确认当前 Work Item 后继续"
            ),
        )
        return AgentRun(run_dir=run_dir, state=state, plan=card.plan)

    def _load_run(self, run: str) -> tuple[Path, AgentState, AgentPlan, dict[str, str]]:
        return load_agent_bundle(self.workspace, run)

    def _save_plan(self, run_dir: Path, plan: AgentPlan) -> None:
        save_agent_plan(run_dir, plan)
