from __future__ import annotations

from pathlib import Path

from .agent_mutation import agent_mutation
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
    write_status_card,
)


class SupervisorAgentWorker:
    """管理单 Writer 绑定；真正的 Coding Agent 仍由宿主 Adapter 启动。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    @agent_mutation("agent.dispatch")
    def bind(
        self,
        run: str,
        *,
        child_run: str,
        operation_id: str,
        operation_started: bool = False,
    ) -> AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
        if state.phase != "ready" or not {"next", "repair"}.intersection(
            state.allowed_actions
        ):
            raise ValueError("当前状态不允许启动 Worker")
        if state.active_child_run or state.active_operation_id:
            raise ValueError("当前 run 已绑定 Writer，禁止启动第二 Writer")
        snapshot = capture_bound_workspace(run_dir)
        if snapshot.fingerprint != state.workspace_fingerprint:
            raise ValueError("Worker 启动前 Workspace 已漂移，必须先重新对账")
        state = update_state(
            state,
            phase="acting",
            state_version=state.state_version + 1,
            active_child_run=child_run,
            active_operation_id=operation_id,
            operation_started=operation_started,
            allowed_actions=["human"],
        )
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="worker_started" if operation_started else "worker_reserved",
            state=state,
            observation_summary=(
                "已绑定单一 Writer；Worker Claim 尚不是完成证据"
                if operation_started
                else "已预留单一 Writer；尚未证明 operation 已开始"
            ),
            artifact_refs=["task-brief.md"],
        )
        write_status_card(
            run_dir,
            state,
            plan,
            next_step="等待 Worker 终态；失去终态时先检查进程并对账 Workspace",
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)

    @agent_mutation("agent.dispatch")
    def confirm_started(
        self,
        run: str,
        *,
        child_run: str,
        operation_id: str,
    ) -> AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
        if state.phase != "acting":
            raise ValueError("当前状态没有等待启动确认的 Worker")
        if (
            state.active_child_run != child_run
            or state.active_operation_id != operation_id
        ):
            raise ValueError("启动确认与当前 Writer binding 不一致")
        if state.operation_started:
            return AgentRun(run_dir=run_dir, state=state, plan=plan)
        state = update_state(
            state,
            state_version=state.state_version + 1,
            operation_started=True,
        )
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="worker_started",
            state=state,
            observation_summary="宿主已确认 operation 开始；后续失联不得按未启动重试",
            artifact_refs=["task-brief.md"],
        )
        write_status_card(
            run_dir,
            state,
            plan,
            next_step="等待 Worker 终态；失联时必须先对账 Workspace 与外部副作用",
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)
