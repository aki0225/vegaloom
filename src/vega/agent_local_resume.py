from __future__ import annotations

from pathlib import Path

from .agent_checkpoint_history import LOCAL_RESUME_SOURCE, held_ready_source
from .agent_contract import AgentPlan, AgentState
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_recovery_support import latest_checkpoint, resume_work_item_progress, validate_resume_checkpoint
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace, load_agent_bundle, save_agent_plan, validate_dispatch_artifacts,
    write_checkpoint, write_status_card, write_task_brief,
)


def _require_ready_source(
    workspace: Path, run_dir: Path, state: AgentState, plan: AgentPlan, metadata: dict[str, object],
) -> None:
    from .agent_change_fix_packet import load_current_fix_packet
    from .agent_verification_scope_replan import require_verification_scope_resume

    require_verification_scope_resume(workspace, run_dir, state, plan, metadata)
    if "repair" in state.allowed_actions:
        load_current_fix_packet(workspace, run_dir, state)


def hold_source_reference(
    workspace: Path, run_dir: Path, state: AgentState, plan: AgentPlan, metadata: dict[str, object],
) -> str | None:
    if state.run_kind != "change" or state.latest_checkpoint_id is None:
        return None
    checkpoint = latest_checkpoint(run_dir, state)
    if state.phase == "ready":
        validate_dispatch_artifacts(run_dir, state, plan)
        _require_ready_source(workspace, run_dir, state, plan, metadata)
    elif held_ready_source(run_dir, state, checkpoint) is None:
        return None
    return f"checkpoints/{checkpoint.checkpoint_id}.json"


def resume_local_run(workspace: Path, run: str) -> AgentRun:
    """由 Recovery mutation lock 保护，恢复原动作与证据，不赋予新的授权。"""
    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    if state.run_kind == "change" and state.contract_revision is None:
        raise ValueError(
            "未编译的 Planning ChangeRun 不能恢复为 ready；"
            "可重试调查请继续运行 `vega change --run <run-id>`，"
            "Workspace 漂移或终止不明时请新建 Planning run 或生成可验证 Handoff"
        )
    if state.phase not in {"needs_human", "stopped"}:
        raise ValueError("只有已暂停或停止且没有 active Writer 的 run 可以本机恢复")
    if state.active_child_run or state.active_operation_id:
        raise ValueError("Writer 仍处于 active binding，不能本机恢复")
    if state.handoff_status != "none":
        raise ValueError("当前 run 已发布 Handoff，不能本机恢复；请从 Task Card 恢复")
    checkpoint = latest_checkpoint(run_dir, state)
    actual = capture_bound_workspace(run_dir)
    validate_resume_checkpoint(plan, checkpoint, actual)
    source = held_ready_source(run_dir, state, checkpoint)
    actions, refs, source_ref = ["next", "replan", "human"], [], None
    if source is not None:
        source_state, source_checkpoint = source
        _require_ready_source(workspace, run_dir, source_state, plan, metadata)
        actions = list(source_checkpoint.pending_actions)
        source_ref = f"checkpoints/{checkpoint.checkpoint_id}.json"
        refs = [*dict.fromkeys([*source_checkpoint.evidence_refs, source_ref])]
    plan = resume_work_item_progress(plan, state)
    next_state = update_state(
        state, phase="ready", state_version=state.state_version + 1,
        workspace_fingerprint=actual.fingerprint, allowed_actions=actions,
    )
    resumed = write_checkpoint(
        run_dir, next_state, actual, reason="人工从 safe Checkpoint 恢复本机调度",
        status="safe", pending_actions=actions, operation_started=False,
        evidence_refs=refs, resume_source_checkpoint=source_ref,
    )
    next_state = update_state(
        next_state, latest_checkpoint_id=resumed.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    write_task_brief(run_dir, plan, next_state, resumed)
    save_agent_plan(run_dir, plan)
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl", event="agent_resumed", state=next_state,
        observation_summary="已重新采集 Workspace，并从 safe Checkpoint 恢复",
        route_reason=LOCAL_RESUME_SOURCE if source_ref else None,
        artifact_refs=[f"checkpoints/{resumed.checkpoint_id}.json", *([source_ref] if source_ref else []),
                       "task-brief.md"],
    )
    write_status_card(run_dir, next_state, plan, checkpoint=resumed,
                      next_step="人工确认后显式派发当前 Work Item")
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)
