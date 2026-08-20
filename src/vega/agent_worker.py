from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentState, canonical_digest, validate_v1_execution_binding
from .agent_graph import require_agent_runtime_dependencies
from .agent_mutation import agent_mutation
from .agent_persistence import append_agent_trace, read_agent_trace, save_agent_state
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
    validate_dispatch_artifacts,
    write_status_card,
)
from .redaction import write_redacted_json_once


class SupervisorAgentWorker:
    """管理单 Writer 绑定；真正的 Coding Agent 仍由宿主 Adapter 启动。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def bind(
        self,
        run: str,
        *,
        child_run: str,
        operation_id: str,
    ) -> AgentRun:
        require_agent_runtime_dependencies()
        return self._bind_with_lock(
            run,
            child_run=child_run,
            operation_id=operation_id,
        )

    @agent_mutation("agent.dispatch")
    def _bind_with_lock(
        self,
        run: str,
        *,
        child_run: str,
        operation_id: str,
    ) -> AgentRun:
        return self._bind_locked(
            run,
            child_run=child_run,
            operation_id=operation_id,
        )

    def _bind_locked(
        self,
        run: str,
        *,
        child_run: str,
        operation_id: str,
    ) -> AgentRun:
        """在调用方已经持有当前 Agent run mutation lock 时提交 Writer binding。"""

        require_agent_runtime_dependencies()
        run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
        validate_v1_execution_binding(plan, state.current_work_item)
        if state.phase != "ready" or not {"next", "repair"}.intersection(
            state.allowed_actions
        ):
            raise ValueError("当前状态不允许启动 Worker")
        if state.active_child_run or state.active_operation_id:
            raise ValueError("当前 run 已绑定 Writer，禁止启动第二 Writer")
        if any(
            item.get("operation_id") == operation_id
            for item in read_agent_trace(run_dir / "trace.jsonl")
        ):
            raise ValueError("operation_id 已在当前 Agent run 使用，禁止复用旧执行身份")
        validate_dispatch_artifacts(run_dir, state, plan)
        snapshot = capture_bound_workspace(run_dir)
        if snapshot.fingerprint != state.workspace_fingerprint:
            raise ValueError("Worker 启动前 Workspace 已漂移，必须先重新对账")
        operation_ref = _reserve_operation_identity(
            run_dir,
            state,
            child_run=child_run,
            operation_id=operation_id,
        )
        state = update_state(
            state,
            phase="acting",
            state_version=state.state_version + 1,
            active_child_run=child_run,
            active_operation_id=operation_id,
            # dispatch 返回后宿主随时可能启动真实进程。这里保守跨过不可自动
            # 重试边界，避免进程已启动但第二次确认尚未落盘时释放旧 Writer。
            operation_started=True,
            allowed_actions=["human"],
        )
        save_agent_state(run_dir / "agent-state.json", state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="worker_dispatch_committed",
            state=state,
            observation_summary=(
                "已绑定单一 Writer，并保守视为 operation 可能已开始；"
                "Worker Claim 尚不是完成证据"
            ),
            artifact_refs=["task-brief.md", operation_ref],
        )
        write_status_card(
            run_dir,
            state,
            plan,
            next_step="等待 Worker 终态；失去终态时先检查进程并对账 Workspace",
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)


def _reserve_operation_identity(
    run_dir: Path,
    state: AgentState,
    *,
    child_run: str,
    operation_id: str,
) -> str:
    digest = canonical_digest({"operation_id": operation_id})
    relative = f"operations/{digest}.json"
    try:
        write_redacted_json_once(
            run_dir / relative,
            {
                "schema_version": 1,
                "run_id": state.run_id,
                "state_version": state.state_version,
                "work_item_id": state.current_work_item,
                "child_run": child_run,
                "operation_id": operation_id,
            },
        )
    except FileExistsError as exc:
        raise ValueError(
            "operation_id 已在当前 Agent run 使用，禁止复用旧执行身份"
        ) from exc
    return relative
