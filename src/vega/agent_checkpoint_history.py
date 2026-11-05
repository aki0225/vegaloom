from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentCheckpoint, AgentDecision, AgentObservation, AgentState
from .agent_persistence import load_agent_checkpoint, read_agent_trace


LOCAL_RESUME_SOURCE = "local_resume.source_preserved"


def checkpoint_matches_state(checkpoint: AgentCheckpoint, state: AgentState) -> bool:
    """只比较现场绑定；安全状态和允许动作的业务要求由调用方决定。"""
    return (
        checkpoint.run_id == state.run_id
        and checkpoint.phase == state.phase
        and checkpoint.pending_actions == state.allowed_actions
        and checkpoint.current_work_item == state.current_work_item
        and checkpoint.workspace_fingerprint == state.workspace_fingerprint
        and checkpoint.state_version + 1 == state.state_version
    )


def load_checkpoint_source_pair(
    run_dir: Path, checkpoint: AgentCheckpoint,
) -> tuple[str, AgentObservation, str, AgentDecision]:
    """读取唯一来源对并绑定身份，不决定该来源是否允许返修或恢复。"""
    observation_refs = [ref for ref in checkpoint.evidence_refs if ref.startswith("observations/")]
    decision_refs = [ref for ref in checkpoint.evidence_refs if ref.startswith("decisions/")]
    if len(observation_refs) != 1 or len(decision_refs) != 1:
        raise ValueError("Checkpoint 没有唯一 Observation 与 Decision 来源")
    observation_ref, decision_ref = observation_refs[0], decision_refs[0]
    try:
        root = run_dir.resolve(strict=True)
        paths = [(root / ref).resolve(strict=True) for ref in (observation_ref, decision_ref)]
        if paths[0].parent != root / "observations" or paths[1].parent != root / "decisions":
            raise ValueError("Checkpoint 来源引用越过证据目录边界")
        observation = AgentObservation.model_validate_json(paths[0].read_text(encoding="utf-8"))
        decision = AgentDecision.model_validate_json(paths[1].read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError("Checkpoint 来源证据无法解析或越过证据目录边界") from exc
    if (
        observation_ref != f"observations/{observation.observation_id}.json"
        or decision_ref != f"decisions/{decision.decision_id}.json"
        or decision.observation_id != observation.observation_id
    ):
        raise ValueError("Checkpoint 来源引用或 Observation/Decision 身份不一致")
    return observation_ref, observation, decision_ref, decision


def _checkpoint_ref(checkpoint: AgentCheckpoint) -> str:
    return f"checkpoints/{checkpoint.checkpoint_id}.json"


def _read_source(run_dir: Path, ref: str) -> AgentCheckpoint:
    path = (run_dir / ref).resolve(strict=True)
    if path.parent != run_dir.resolve(strict=True) / "checkpoints":
        raise ValueError("本机恢复来源越过 Checkpoint 边界")
    source = load_agent_checkpoint(path)
    if ref != _checkpoint_ref(source):
        raise ValueError("本机恢复来源 Checkpoint 身份不一致")
    return source


def _require_safe_checkpoint(checkpoint: AgentCheckpoint, state: AgentState) -> None:
    if (
        not checkpoint_matches_state(checkpoint, state)
        or checkpoint.status != "safe" or checkpoint.active_child_run is not None
        or checkpoint.operation_started or checkpoint.external_side_effects != "none"
    ):
        raise ValueError("本机恢复来源不是同一现场的安全 Checkpoint")


def _historical_state(state: AgentState, checkpoint: AgentCheckpoint) -> AgentState:
    return state.model_copy(update={
        "phase": checkpoint.phase, "state_version": checkpoint.state_version + 1,
        "latest_checkpoint_id": checkpoint.checkpoint_id,
        "allowed_actions": list(checkpoint.pending_actions),
    })


def _require_link_event(run_dir: Path, checkpoint: AgentCheckpoint, events: set[str]) -> None:
    records = [item for item in read_agent_trace(run_dir / "trace.jsonl")
               if item.get("state_version") == checkpoint.state_version + 1]
    expected = [_checkpoint_ref(checkpoint), checkpoint.resume_source_checkpoint]
    if (
        len(records) != 1 or records[0].get("event") not in events
        or records[0].get("route_reason") != LOCAL_RESUME_SOURCE
        or records[0].get("run_id") != checkpoint.run_id
        or records[0].get("work_item") != checkpoint.current_work_item
        or records[0].get("workspace_fingerprint") != checkpoint.workspace_fingerprint
        or records[0].get("artifact_refs", [])[:2] != expected
        or checkpoint.resume_source_checkpoint not in checkpoint.evidence_refs
    ):
        raise ValueError("本机恢复缺少绑定来源的暂停或恢复事件")


def _require_source_field(run_dir: Path, checkpoint: AgentCheckpoint) -> None:
    if checkpoint.resume_source_checkpoint is None and any(
        item.get("state_version") == checkpoint.state_version + 1
        and item.get("route_reason") == LOCAL_RESUME_SOURCE
        for item in read_agent_trace(run_dir / "trace.jsonl")
    ):
        raise ValueError("本机恢复来源字段缺失")


def held_ready_source(
    run_dir: Path, state: AgentState, checkpoint: AgentCheckpoint,
) -> tuple[AgentState, AgentCheckpoint] | None:
    """沿无变化的 pause/stop 后继链寻找被暂停的 ready，而非凭 safe 标签猜测动作。"""
    _require_source_field(run_dir, checkpoint)
    if checkpoint.resume_source_checkpoint is None:
        return None
    while checkpoint.phase != "ready":
        _require_safe_checkpoint(checkpoint, state)
        if checkpoint.phase not in {"needs_human", "stopped"}:
            raise ValueError("本机恢复来源不是暂停或停止边界")
        _require_link_event(run_dir, checkpoint, {"agent_paused", "agent_stopped"})
        source = _read_source(run_dir, checkpoint.resume_source_checkpoint)
        if source.state_version + 2 != checkpoint.state_version:
            raise ValueError("本机恢复 Checkpoint 版本链不连续")
        if source.failed_attempts != checkpoint.failed_attempts:
            raise ValueError("本机恢复不能改变失败历史")
        state, checkpoint = _historical_state(state, source), source
    _require_safe_checkpoint(checkpoint, state)
    if checkpoint.pending_actions not in (["next", "replan", "human"], ["repair", "replan", "human"]):
        raise ValueError("本机恢复来源没有可执行的 next 或 repair")
    return state, checkpoint


def ready_checkpoint_origin(run_dir: Path, state: AgentState) -> tuple[AgentState, AgentCheckpoint]:
    """只读验证恢复链并投影原 ready 版本；不回写状态，也不建立新的尝试 epoch。"""
    checkpoint = load_agent_checkpoint(run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json")
    while True:
        _require_source_field(run_dir, checkpoint)
        if checkpoint.resume_source_checkpoint is None:
            return state, checkpoint
        _require_safe_checkpoint(checkpoint, state)
        if checkpoint.phase != "ready":
            raise ValueError("派发来源不是 ready Checkpoint")
        _require_link_event(run_dir, checkpoint, {"agent_resumed"})
        held = _read_source(run_dir, checkpoint.resume_source_checkpoint)
        if held.state_version + 2 != checkpoint.state_version:
            raise ValueError("本机恢复 Checkpoint 版本链不连续")
        source = held_ready_source(run_dir, _historical_state(state, held), held)
        if source is None:
            raise ValueError("本机恢复缺少原可执行来源")
        original_state, original = source
        expected_refs = [*dict.fromkeys([*original.evidence_refs, _checkpoint_ref(held)])]
        if (
            checkpoint.pending_actions != original.pending_actions
            or checkpoint.evidence_refs != expected_refs
            or checkpoint.failed_attempts != original.failed_attempts
        ):
            raise ValueError("本机恢复改变了原动作或证据引用")
        state, checkpoint = original_state, original


def inherited_failed_attempts(
    run_dir: Path,
    state: AgentState,
    *,
    allow_work_item_advance: bool,
) -> list[str]:
    """只在同一 Work Item 内继承失败历史；推进必须由显式路由授权。"""

    if state.latest_checkpoint_id is None:
        return []
    previous = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (previous.run_id, previous.checkpoint_id) != (
        state.run_id,
        state.latest_checkpoint_id,
    ):
        raise ValueError("前序 Checkpoint 与当前 Agent State 不一致，拒绝继承失败历史")
    if previous.current_work_item == state.current_work_item:
        return previous.failed_attempts
    if not allow_work_item_advance:
        raise ValueError("前序 Checkpoint 属于不同 Work Item，拒绝隐式推进")
    return []
