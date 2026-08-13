from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentObservation, AgentPlan, AgentState
from .agent_mutation import agent_mutation
from .execution_control import inspect_execution_for_recovery
from .agent_persistence import (
    append_agent_trace,
    save_agent_state,
)
from .agent_recovery_request import AgentRecoveryRequest
from .agent_recovery_support import (
    agent_trace_issue,
    blocked_recovery_reason,
    latest_checkpoint,
    require_recovery_request,
    validate_resume_checkpoint,
    write_load_failure_report,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
    write_checkpoint,
    write_status_card,
    write_task_brief,
)
from .redaction import write_redacted_json
from .run_utils import resolve_run_dir
from .workspace_check import ReviewWorkspaceSnapshot


class SupervisorAgentRecovery:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    @agent_mutation("agent.recover")
    def recover(self, run: str, request: AgentRecoveryRequest) -> AgentRun:
        run_dir = resolve_run_dir(self.workspace, run)
        try:
            _, state, plan, _ = load_agent_bundle(self.workspace, run)
        except ValueError as exc:
            write_load_failure_report(run_dir, request.reason, exc)
            raise
        require_recovery_request(state, request)
        actual = capture_bound_workspace(run_dir)
        try:
            process_inspection = inspect_execution_for_recovery(run_dir)
        except ValueError as exc:
            return _block_on_execution_issue(
                run_dir,
                state,
                plan,
                actual,
                request,
                f"Worker execution 证据无法安全解析：{exc}",
            )
        if not process_inspection.can_recover:
            raise ValueError(process_inspection.summary)
        trace_issue = agent_trace_issue(run_dir / "trace.jsonl")
        workspace_unchanged = actual.fingerprint == state.workspace_fingerprint
        workspace_clear = (
            request.workspace_explained
            and not actual.unsafe_index_paths
            and actual.git_control_complete
        )
        recoverable = (
            not request.operation_started
            and workspace_unchanged
            and workspace_clear
            and request.external_side_effects == "none"
            and trace_issue is None
        )
        if trace_issue is not None:
            return _block_on_trace_issue(
                run_dir,
                state,
                plan,
                actual,
                request,
                trace_issue,
            )

        previous_child = state.active_child_run
        observation = AgentObservation(
            observation_id=f"recovery-{state.state_version + 1:04d}",
            work_item_id=state.current_work_item,
            machine_summary=(
                f"{request.reason.strip()}；进程检查：{process_inspection.summary}"
            ),
            workspace_fingerprint=actual.fingerprint,
            changed_files=list(actual.changed_files),
            worker_alive=False,
            operation_started=request.operation_started,
            workspace_explained=workspace_clear,
            unknown_file_count=len(actual.untracked_files),
            external_side_effects=request.external_side_effects,
        )
        write_redacted_json(
            run_dir / "observations" / f"{observation.observation_id}.json",
            observation.model_dump(mode="json"),
        )
        # 先把未知旧 Writer 解除结果持久化，再允许后续新 dispatch；崩溃在此前只会保守保留旧绑定。
        releasing_state = update_state(
            state,
            phase="needs_human",
            state_version=state.state_version + 1,
            active_child_run=None,
            active_operation_id=None,
            operation_started=False,
            workspace_fingerprint=actual.fingerprint,
            allowed_actions=["human"],
        )
        save_agent_state(run_dir / "agent-state.json", releasing_state)
        if recoverable:
            next_state = update_state(
                releasing_state,
                phase="ready",
                state_version=releasing_state.state_version + 1,
                workspace_fingerprint=actual.fingerprint,
                allowed_actions=["next", "replan", "human"],
            )
            checkpoint_status = "safe"
            pending_actions = ["next", "replan", "human"]
            next_step = "原 operation 未开始且 Workspace 未变；可显式派发新的 child attempt"
        else:
            next_state = releasing_state
            checkpoint_status = "blocked"
            pending_actions = ["human"]
            next_step = blocked_recovery_reason(
                request,
                workspace_unchanged=workspace_unchanged,
                workspace_clear=workspace_clear,
            )

        checkpoint = write_checkpoint(
            run_dir,
            next_state,
            actual,
            reason=next_step,
            status=checkpoint_status,
            pending_actions=pending_actions,
            evidence_refs=[f"observations/{observation.observation_id}.json"],
            failed_attempts=[previous_child] if previous_child else [],
            operation_started=request.operation_started,
            external_side_effects=request.external_side_effects,
        )
        next_state = update_state(
            next_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", next_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="agent_recovered" if recoverable else "agent_recovery_blocked",
            state=next_state,
            observation_summary=observation.machine_summary,
            route_reason=next_step,
            artifact_refs=[
                f"observations/{observation.observation_id}.json",
                f"checkpoints/{checkpoint.checkpoint_id}.json",
            ],
        )
        if recoverable:
            write_task_brief(
                run_dir,
                plan,
                next_state,
                checkpoint,
                failed_attempts=[previous_child] if previous_child else [],
            )
        write_status_card(
            run_dir,
            next_state,
            plan,
            observation=observation,
            checkpoint=checkpoint,
            next_step=next_step,
        )
        return AgentRun(run_dir=run_dir, state=next_state, plan=plan)

    @agent_mutation("agent.pause")
    def pause(self, run: str, *, reason: str) -> AgentRun:
        return self._hold(run, reason=reason, stopped=False)

    @agent_mutation("agent.stop")
    def stop(self, run: str, *, reason: str) -> AgentRun:
        return self._hold(run, reason=reason, stopped=True)

    @agent_mutation("agent.resume")
    def resume_local(self, run: str) -> AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
        if state.phase != "needs_human" or state.active_child_run:
            raise ValueError("只有已暂停且没有 active Writer 的 run 可以本机恢复")
        checkpoint = latest_checkpoint(run_dir, state)
        actual = capture_bound_workspace(run_dir)
        validate_resume_checkpoint(plan, checkpoint, actual)
        next_state = update_state(
            state,
            phase="ready",
            state_version=state.state_version + 1,
            workspace_fingerprint=actual.fingerprint,
            allowed_actions=["next", "replan", "human"],
        )
        resumed = write_checkpoint(
            run_dir,
            next_state,
            actual,
            reason="人工从 safe Checkpoint 恢复本机调度",
            status="safe",
            pending_actions=["next", "replan", "human"],
            operation_started=False,
        )
        next_state = update_state(
            next_state,
            latest_checkpoint_id=resumed.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", next_state)
        write_task_brief(run_dir, plan, next_state, resumed)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="agent_resumed",
            state=next_state,
            observation_summary="已重新采集 Workspace，并从 safe Checkpoint 恢复",
            artifact_refs=[f"checkpoints/{resumed.checkpoint_id}.json", "task-brief.md"],
        )
        write_status_card(
            run_dir,
            next_state,
            plan,
            checkpoint=resumed,
            next_step="人工确认后显式派发当前 Work Item",
        )
        return AgentRun(run_dir=run_dir, state=next_state, plan=plan)

    def _hold(self, run: str, *, reason: str, stopped: bool) -> AgentRun:
        if not reason.strip():
            raise ValueError("pause/stop 必须提供原因")
        run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
        action = "stop" if stopped else "pause"
        if state.phase == "completed":
            raise ValueError("已完成的 Agent run 不能改写为 pause/stop")
        if state.active_child_run:
            raise ValueError("Writer 仍绑定；必须先确认停止并运行 recover 完成现场对账")
        if not stopped and state.phase != "ready":
            raise ValueError("V1 只允许在 ready 阶段主动 pause")
        if stopped and state.phase == "stopped":
            return AgentRun(run_dir=run_dir, state=state, plan=plan)
        if not stopped and state.phase == "needs_human":
            raise ValueError("当前 run 已在等待人工；无需重复 pause")
        actual = capture_bound_workspace(run_dir)
        if state.workspace_fingerprint != actual.fingerprint:
            blocked_reason = (
                f"{action} 前 Workspace 已漂移；现场已保留并交由人工对账"
            )
            blocked_state = update_state(
                state,
                phase="needs_human",
                state_version=state.state_version + 1,
                workspace_fingerprint=actual.fingerprint,
                allowed_actions=["human"],
            )
            checkpoint = write_checkpoint(
                run_dir,
                blocked_state,
                actual,
                reason=blocked_reason,
                status="blocked",
                pending_actions=["human"],
                operation_started=False,
            )
            blocked_state = update_state(
                blocked_state,
                latest_checkpoint_id=checkpoint.checkpoint_id,
                state_version=blocked_state.state_version + 1,
            )
            save_agent_state(run_dir / "agent-state.json", blocked_state)
            append_agent_trace(
                run_dir / "trace.jsonl",
                event=f"agent_{action}_blocked",
                state=blocked_state,
                route_reason=checkpoint.reason,
                artifact_refs=[f"checkpoints/{checkpoint.checkpoint_id}.json"],
            )
            write_status_card(
                run_dir,
                blocked_state,
                plan,
                checkpoint=checkpoint,
                next_step=checkpoint.reason,
            )
            return AgentRun(run_dir=run_dir, state=blocked_state, plan=plan)
        safe = not actual.unsafe_index_paths and actual.git_control_complete
        if stopped and safe:
            phase = "stopped"
            allowed_actions: list[str] = []
            status = "safe"
            pending_actions: list[str] = []
        elif stopped:
            phase = "needs_human"
            allowed_actions = ["human"]
            status = "blocked"
            pending_actions = ["human"]
        else:
            phase = "needs_human"
            allowed_actions = ["human"]
            status = "safe" if safe else "blocked"
            pending_actions = ["human"]
        next_state = update_state(
            state,
            phase=phase,
            state_version=state.state_version + 1,
            workspace_fingerprint=actual.fingerprint,
            allowed_actions=allowed_actions,
        )
        checkpoint = write_checkpoint(
            run_dir,
            next_state,
            actual,
            reason=reason.strip(),
            status=status,
            pending_actions=pending_actions,
            operation_started=False,
        )
        next_state = update_state(
            next_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", next_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="agent_stopped" if stopped else "agent_paused",
            state=next_state,
            route_reason=reason.strip(),
            artifact_refs=[f"checkpoints/{checkpoint.checkpoint_id}.json"],
        )
        write_status_card(
            run_dir,
            next_state,
            plan,
            checkpoint=checkpoint,
            next_step=(
                "任务已停止；代码、Goal、Plan 和现场均保留，不执行自动回滚或删除"
                if phase == "stopped"
                else "停止前 Workspace 控制信息不完整；任务仍等待人工处理"
                if stopped
                else "任务已暂停；需要继续时先重新检查 safe Checkpoint 与真实 Workspace"
            ),
        )
        return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def _block_on_trace_issue(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    actual: ReviewWorkspaceSnapshot,
    request: AgentRecoveryRequest,
    issue: str,
) -> AgentRun:
    # Trace 只负责审计。损坏时保留 active binding，避免新 Writer 与未知旧 Writer 并发。
    next_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=actual.fingerprint,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        actual,
        reason=f"{issue}；已保留原 Writer binding，禁止自动接管",
        status="blocked",
        pending_actions=["human"],
        failed_attempts=[],
        operation_started=request.operation_started,
        external_side_effects=request.external_side_effects,
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=f"{issue}；人工核对旧 Writer、Workspace 和原始 Artifact",
    )
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def _block_on_execution_issue(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    actual: ReviewWorkspaceSnapshot,
    request: AgentRecoveryRequest,
    issue: str,
) -> AgentRun:
    # execution 证据损坏时也保留旧 Writer binding；否则人工误判后可能启动第二 Writer。
    next_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=actual.fingerprint,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        actual,
        reason=f"{issue}；已保留原 Writer binding，禁止自动接管",
        status="blocked",
        pending_actions=["human"],
        operation_started=request.operation_started,
        external_side_effects=request.external_side_effects,
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="agent_recovery_execution_blocked",
        state=next_state,
        route_reason=checkpoint.reason,
        artifact_refs=[f"checkpoints/{checkpoint.checkpoint_id}.json"],
    )
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=f"{issue}；人工核对旧 Worker、进程与 Workspace",
    )
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)
