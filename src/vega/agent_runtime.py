from __future__ import annotations

from pathlib import Path

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_change_control import prepare_change_decision
from .agent_change_run import current_change_work_item
from .agent_change_runtime import (
    approve_change_run,
    bind_change_candidate,
    settle_change_candidate,
    start_change_run,
)
from .agent_change_revision_mixin import ChangeRevisionRuntimeMixin
from .agent_git_candidate import CandidateCommit
from .agent_handoff import HandoffResult, create_handoff
from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    ObservationAuthority,
    validate_v1_execution_binding,
    validate_v1_execution_plan,
)
from .agent_finalization import finalize_agent_state
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_legacy_lifecycle import approve_legacy_plan, start_legacy_agent
from .agent_mutation import agent_mutation
from .agent_planning_start import start_planning_run
from .agent_plan_archive import archive_agent_plan_revision
from .agent_run import AgentRun
from .agent_routing import decide_next_action, transition_state
from .agent_status_card import read_status_card
from .agent_runtime_logic import (
    apply_work_item_progress,
    invalidate_plan_for_steer,
    next_pending_work_item,
    reconcile_observation,
    update_state,
    validate_machine_trace_binding,
    validate_observation_binding,
)
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
    publish_observation_transition,
    resume_agent_task_card,
    save_agent_plan,
    write_checkpoint,
    write_status_card,
    write_task_brief,
)
from .redaction import write_redacted_json, write_redacted_json_once

class SupervisorAgentRuntime(ChangeRevisionRuntimeMixin):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def start(
        self,
        repo: Path,
        *,
        goal: str,
        plan: AgentPlan | None = None,
    ) -> AgentRun:
        return start_legacy_agent(
            self.workspace,
            repo,
            goal=goal,
            plan=plan,
        )

    def start_change(
        self,
        repo: Path,
        *,
        contract: ChangeContract,
        execution_plan: ExecutionPlan,
    ) -> AgentRun:
        return start_change_run(
            self.workspace,
            repo,
            contract=contract,
            execution_plan=execution_plan,
        )

    def start_planning(self, repo: Path, *, goal: str) -> AgentRun:
        return start_planning_run(self.workspace, repo, goal=goal)

    @agent_mutation("agent.approve")
    def approve(self, run: str, *, actor: str = "human") -> AgentRun:
        run_dir, state, plan, metadata = self._load_run(run)
        if state.run_kind == "change":
            return approve_change_run(
                run_dir,
                state,
                plan,
                metadata,
                actor=actor,
                write_checkpoint_fn=write_checkpoint,
                write_task_brief_fn=write_task_brief,
            )
        return approve_legacy_plan(
            run_dir,
            state,
            plan,
            actor=actor,
            write_checkpoint_fn=write_checkpoint,
            write_task_brief_fn=write_task_brief,
        )

    @agent_mutation("agent.plan")
    def update_plan(self, run: str, draft: AgentPlan) -> AgentRun:
        run_dir, state, current, _ = self._load_run(run)
        if state.run_kind == "change":
            raise ValueError(
                "ChangeRun 的合同内计划修订由 AUTO-02 接口处理，不能写回 legacy Plan"
            )
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
        executable_item = validate_v1_execution_plan(revised)
        snapshot = capture_bound_workspace(run_dir)
        archived_plan_ref = archive_agent_plan_revision(run_dir, current)
        guarded_state = update_state(
            state,
            phase="awaiting_approval",
            state_version=state.state_version + 1,
            approved_plan_digest=None,
            workspace_fingerprint=snapshot.fingerprint,
            allowed_actions=["replan", "human"],
        )
        save_agent_state(run_dir / "agent-state.json", guarded_state)
        self._save_plan(run_dir, revised)
        revised_state = update_state(
            guarded_state,
            goal_revision=revised.goal_revision,
            plan_revision=revised.plan_revision,
            current_work_item=executable_item.work_item_id,
        )
        checkpoint = write_checkpoint(
            run_dir,
            revised_state,
            snapshot,
            reason="主会话提交新的 Plan revision，等待人工批准",
            status="blocked",
            pending_actions=["replan", "human"],
        )
        revised_state = update_state(
            revised_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=revised_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", revised_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="plan_revised",
            state=revised_state,
            observation_summary=f"Plan revision {revised.plan_revision} 已写入",
            artifact_refs=[
                "agent-plan.json", archived_plan_ref,
                f"checkpoints/{checkpoint.checkpoint_id}.json",
            ],
        )
        write_status_card(
            run_dir,
            revised_state,
            revised,
            checkpoint=checkpoint,
            next_step="人工审查新 Plan revision；未批准前不能启动 Worker",
        )
        return AgentRun(run_dir=run_dir, state=revised_state, plan=revised)

    @agent_mutation("agent.observe")
    def observe(
        self,
        run: str,
        observation: AgentObservation,
    ) -> AgentRun:
        return self._observe_locked(
            run,
            observation,
            authority="external_claim",
        )

    @agent_mutation("agent.observe")
    def observe_fake_worker(
        self,
        run: str,
        observation: AgentObservation,
    ) -> AgentRun:
        """Gate 1 可控 Fake Worker 的内部观测入口，不暴露给 CLI。"""

        return self._observe_locked(
            run,
            observation,
            authority="fake_worker",
        )

    @agent_mutation("agent.observe")
    def observe_machine(
        self,
        run: str,
        observation: AgentObservation,
    ) -> AgentRun:
        """真实 Adapter 的内部机器对账入口，不接受 CLI 直接提交。"""

        return self._observe_locked(
            run,
            observation,
            authority="machine_reconcile",
        )

    def _observe_locked(
        self,
        run: str,
        observation: AgentObservation,
        *,
        authority: ObservationAuthority,
    ) -> AgentRun:
        run_dir, state, plan, metadata = self._load_run(run)
        work_item = (
            current_change_work_item(plan, state)
            if state.run_kind == "change"
            else validate_v1_execution_binding(plan, state.current_work_item)
        )
        validate_observation_binding(state, observation, authority)
        # 真实现场只能在当前 State 与已提交 dispatch Trace 同时证明执行身份后发布；
        # 否则后续 Observation、Decision 与 Checkpoint 会把篡改现场固化为权威状态。
        validate_machine_trace_binding(run_dir, state, observation, authority)
        previous_state = state
        attempt = state.active_child_run
        actual = capture_bound_workspace(run_dir)
        reconciled = reconcile_observation(
            state,
            observation,
            authority,
            actual,
            declared_external_side_effects=work_item.external_side_effects,
        )
        observation_path = (
            run_dir / "observations" / f"{reconciled.observation_id}.json"
        )
        try:
            write_redacted_json_once(
                observation_path,
                reconciled.model_dump(mode="json"),
            )
        except FileExistsError as exc:
            raise ValueError(
                f"Observation ID 已存在，拒绝覆盖历史证据："
                f"{reconciled.observation_id}"
            ) from exc
        decision = decide_next_action(plan, reconciled)
        prepared_decision = prepare_change_decision(
            self.workspace,
            run_dir,
            state,
            plan,
            metadata,
            reconciled,
            decision,
        )
        decision = prepared_decision.decision
        write_redacted_json(
            run_dir / "decisions" / f"{decision.decision_id}.json",
            decision.model_dump(mode="json"),
        )
        if state.workspace_fingerprint != reconciled.workspace_fingerprint:
            state = update_state(
                state,
                workspace_fingerprint=reconciled.workspace_fingerprint,
            )
        plan = apply_work_item_progress(plan, state, reconciled, decision.selected_action)
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
                *prepared_decision.evidence_refs,
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
            operation_started=(
                False
                if checkpoint_status == "safe"
                else reconciled.operation_started
            ),
            external_side_effects=reconciled.external_side_effects,
            allow_work_item_advance=decision.selected_action == "next",
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        if decision.selected_action in {"next", "repair"}:
            write_task_brief(
                run_dir,
                plan,
                state,
                checkpoint,
                failed_attempts=checkpoint.failed_attempts,
                artifact_refs=prepared_decision.task_brief_refs,
            )
        # Plan 进度只有在 Checkpoint 与下一轮 Task Brief 均成功后才发布。
        # State 仍是最后的调度安全闩；此前任一步失败都会保留旧 active Writer。
        publish_observation_transition(
            run_dir,
            previous_state,
            state,
            plan,
            reconciled,
            decision,
            checkpoint,
            authority,
            next_step=(
                "Core Finish 已满足可信终态；采用同一证据发布 Supervisor completed"
                if decision.selected_action == "finalize"
                else decision.reason
            ),
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)

    @agent_mutation("agent.candidate")
    def bind_candidate(
        self,
        run: str,
        *,
        candidate: CandidateCommit,
    ) -> tuple[AgentRun, str]:
        run_dir, state, plan, metadata = self._load_run(run)
        return bind_change_candidate(
            run_dir,
            state,
            plan,
            metadata,
            candidate,
        )

    @agent_mutation("agent.candidate")
    def settle_candidate(
        self,
        run: str,
        *,
        candidate_ref: str,
        outcome: str,
    ) -> AgentRun:
        run_dir, state, plan, metadata = self._load_run(run)
        return settle_change_candidate(
            run_dir,
            state,
            plan,
            metadata,
            candidate_ref=candidate_ref,
            outcome=outcome,
        )

    @agent_mutation("agent.finalize")
    def finalize(self, run: str) -> AgentRun:
        """采用可信 Core Finish 证据，完成 Supervisor 自身的终态发布。"""

        run_dir, state, plan, _ = self._load_run(run)
        completed = finalize_agent_state(
            self.workspace,
            run_dir,
            state,
            plan,
        )
        return AgentRun(run_dir=run_dir, state=completed, plan=plan)

    @agent_mutation("agent.steer")
    def steer(self, run: str, *, instruction: str) -> AgentRun:
        if not instruction.strip():
            raise ValueError("steer instruction 不能为空")
        run_dir, state, plan, _ = self._load_run(run)
        if state.run_kind == "change":
            raise ValueError(
                "ChangeRun steer 必须生成 Contract 或 Execution Plan revision；"
                "当前版本拒绝把它降级为 legacy Plan 修改"
            )
        if state.active_child_run:
            raise ValueError("Writer 仍在运行，先停止并对账后才能修改 Plan")
        snapshot = capture_bound_workspace(run_dir)
        revised = invalidate_plan_for_steer(plan, instruction)
        guarded_state = update_state(
            state,
            phase="awaiting_approval",
            state_version=state.state_version + 1,
            approved_plan_digest=None,
            allowed_actions=["replan", "human"],
        )
        save_agent_state(run_dir / "agent-state.json", guarded_state)
        self._save_plan(run_dir, revised)
        revised_state = update_state(
            guarded_state,
            goal_revision=revised.goal_revision,
            plan_revision=revised.plan_revision,
            workspace_fingerprint=snapshot.fingerprint,
        )
        checkpoint = write_checkpoint(
            run_dir,
            revised_state,
            snapshot,
            reason="人工 steer 改变约束，旧 Plan 批准已失效",
            status="blocked",
            pending_actions=["replan", "human"],
        )
        revised_state = update_state(
            revised_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=revised_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", revised_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="plan_invalidated_by_steer",
            state=revised_state,
            observation_summary="新增人工约束，等待新 Plan revision 批准",
            route_reason=instruction.strip(),
            artifact_refs=["agent-plan.json", f"checkpoints/{checkpoint.checkpoint_id}.json"],
        )
        write_status_card(
            run_dir,
            revised_state,
            revised,
            checkpoint=checkpoint,
            next_step="根据新增约束修订 Plan，并重新请求人工批准",
        )
        return AgentRun(run_dir=run_dir, state=revised_state, plan=revised)

    def status(self, run: str) -> str:
        run_dir, state, plan, _ = self._load_run(run)
        return read_status_card(run_dir, state, plan)

    def state_path(self, run: str) -> Path:
        run_dir, _, _, _ = self._load_run(run)
        return run_dir / "agent-state.json"
    def resume_task_card(self, repo: Path, task_path: Path | None = None) -> AgentRun:
        return resume_agent_task_card(self.workspace, repo, task_path)

    @agent_mutation("agent.handoff")
    def handoff(self, run: str, *, reason: str) -> HandoffResult:
        return create_handoff(self.workspace, run, reason=reason)

    def _load_run(self, run: str) -> tuple[Path, AgentState, AgentPlan, dict[str, object]]:
        return load_agent_bundle(self.workspace, run)

    def _save_plan(self, run_dir: Path, plan: AgentPlan) -> None:
        save_agent_plan(run_dir, plan)
