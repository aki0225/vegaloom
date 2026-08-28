from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from .agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentObservation,
    AgentPlan,
    AgentState,
)
from .agent_persistence import (
    append_agent_trace_commit,
    load_agent_checkpoint,
    read_agent_trace,
    save_agent_state,
)
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    write_checkpoint,
    write_status_card,
)
from .agent_visibility import write_agent_final_report
from .run_utils import resolve_run_dir


def finalize_agent_state(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
) -> AgentState:
    """采用已绑定的 Core Finish 证据，发布 Supervisor 可信终态。"""

    if state.phase == "completed":
        if state.terminal_status != "ready_to_commit":
            raise ValueError("已完成 Agent run 的终态不是 ready_to_commit")
        observation, checkpoint, evidence_refs = _load_completed_evidence(
            workspace,
            run_dir,
            state,
        )
        _publish_completed_artifacts(
            workspace,
            run_dir,
            state,
            plan,
            observation,
            checkpoint,
            evidence_refs,
        )
        return state
    _require_finalizable(state, plan)
    observation, evidence_refs = _load_finalization_evidence(
        workspace,
        run_dir,
        state,
    )
    snapshot = capture_bound_workspace(run_dir)
    if snapshot.fingerprint != state.workspace_fingerprint:
        raise ValueError("Core Finish 后 Workspace 已漂移，拒绝采用过期终态")

    completed = update_state(
        state,
        phase="completed",
        state_version=state.state_version + 1,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=[],
        terminal_status="ready_to_commit",
    )
    checkpoint = write_checkpoint(
        run_dir,
        completed,
        snapshot,
        reason="已采用可信 Core Finish ready_to_commit 终态",
        status="safe",
        pending_actions=[],
        evidence_refs=evidence_refs,
    )
    completed = update_state(
        completed,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=completed.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", completed)
    _publish_completed_artifacts(
        workspace,
        run_dir,
        completed,
        plan,
        observation,
        checkpoint,
        evidence_refs,
    )
    return completed


def _publish_completed_artifacts(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    observation: AgentObservation,
    checkpoint: AgentCheckpoint,
    evidence_refs: list[str],
) -> None:
    trace_path = run_dir / "trace.jsonl"
    if not _completion_trace_exists(trace_path, state, evidence_refs):
        append_agent_trace_commit(
            trace_path,
            event="agent_completed",
            state=state,
            observation_summary=observation.machine_summary,
            route_reason="Core Finish=ready_to_commit，Supervisor 终态已发布",
            artifact_refs=evidence_refs,
        )
    write_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
        next_step=_completed_next_step(state),
    )
    if state.run_kind == "change":
        write_agent_final_report(
            workspace,
            run_dir,
            state,
            plan,
            observation,
        )


def _completed_next_step(state: AgentState) -> str:
    if state.run_kind == "change":
        return (
            "读取 Core Finish 与 Accepted Checkpoint，人工检查累计 Diff，"
            "再决定是否 push、创建 PR 或合并"
        )
    return "读取 Core Finish 报告并完成人工提交前检查，再决定 commit 与 push"


def _completion_trace_exists(
    trace_path: Path,
    state: AgentState,
    evidence_refs: list[str],
) -> bool:
    try:
        items = read_agent_trace(trace_path)
    except (OSError, ValueError) as exc:
        raise ValueError("无法验证 Agent 完成 Trace") from exc
    return any(
        item.get("event") == "agent_completed"
        and item.get("run_id") == state.run_id
        and item.get("state_version") == state.state_version
        and item.get("artifact_refs") == evidence_refs
        for item in items
    )


def _require_finalizable(state: AgentState, plan: AgentPlan) -> None:
    if state.phase != "finalizing":
        raise ValueError("只有 finalizing 状态可以采用 Core Finish 终态")
    if state.active_child_run or state.active_operation_id:
        raise ValueError("仍有 active Writer，不能完成 Agent run")
    if any(
        item.status not in {"completed", "superseded"}
        for item in plan.work_items
    ):
        raise ValueError("Plan 仍有未完成 Work Item，不能完成 Agent run")


def _load_finalization_evidence(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
) -> tuple[AgentObservation, list[str]]:
    checkpoint_ref, observation_ref, decision_ref = _load_checkpoint_refs(
        run_dir,
        state,
    )
    observation, decision = _load_observation_and_decision(
        run_dir,
        observation_ref,
        decision_ref,
    )
    _validate_observation_and_decision(state, observation, decision)
    child_ref, finish_sha256 = _load_child_binding(run_dir, state, observation)
    _validate_child_finish(workspace, observation, finish_sha256)
    return observation, [checkpoint_ref, observation_ref, decision_ref, child_ref]


def _load_completed_evidence(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
) -> tuple[AgentObservation, AgentCheckpoint, list[str]]:
    if state.latest_checkpoint_id is None:
        raise ValueError("completed State 缺少最新 Checkpoint")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.phase != "completed"
        or checkpoint.status != "safe"
        or checkpoint.state_version + 1 != state.state_version
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.active_child_run is not None
        or checkpoint.operation_started
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.pending_actions
    ):
        raise ValueError("completed State 没有匹配的可信 Checkpoint")
    observation_ref = _single_evidence_ref(
        checkpoint.evidence_refs,
        prefix="observations/",
        label="Observation",
    )
    decision_ref = _single_evidence_ref(
        checkpoint.evidence_refs,
        prefix="decisions/",
        label="Decision",
    )
    observation, decision = _load_observation_and_decision(
        run_dir,
        observation_ref,
        decision_ref,
    )
    _validate_observation_and_decision(state, observation, decision)
    _, finish_sha256 = _load_child_binding(run_dir, state, observation)
    _validate_child_finish(workspace, observation, finish_sha256)
    return observation, checkpoint, list(checkpoint.evidence_refs)


def _load_checkpoint_refs(
    run_dir: Path,
    state: AgentState,
) -> tuple[str, str, str]:
    if state.latest_checkpoint_id is None:
        raise ValueError("finalizing State 缺少最新 Checkpoint")
    checkpoint_ref = f"checkpoints/{state.latest_checkpoint_id}.json"
    checkpoint = load_agent_checkpoint(run_dir / checkpoint_ref)
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.phase != "finalizing"
        or checkpoint.status != "safe"
        or checkpoint.state_version + 1 != state.state_version
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.pending_actions != ["finalize"]
    ):
        raise ValueError("finalizing State 没有匹配的可信 Checkpoint")
    return (
        checkpoint_ref,
        _single_evidence_ref(
            checkpoint.evidence_refs,
            prefix="observations/",
            label="Observation",
        ),
        _single_evidence_ref(
            checkpoint.evidence_refs,
            prefix="decisions/",
            label="Decision",
        ),
    )


def _load_observation_and_decision(
    run_dir: Path,
    observation_ref: str,
    decision_ref: str,
) -> tuple[AgentObservation, AgentDecision]:
    try:
        observation = AgentObservation.model_validate_json(
            (run_dir / observation_ref).read_text(encoding="utf-8")
        )
        decision = AgentDecision.model_validate_json(
            (run_dir / decision_ref).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("无法读取可信 finalize Observation 或 Decision") from exc
    return observation, decision


def _validate_observation_and_decision(
    state: AgentState,
    observation: AgentObservation,
    decision: AgentDecision,
) -> None:
    if (
        observation.authority != "machine_reconcile"
        or not observation.work_item_completed
        or not observation.all_work_items_completed
        or observation.worker_alive
        or observation.workspace_fingerprint != state.workspace_fingerprint
        or observation.verification != "passed"
        or observation.risk != "passed"
        or observation.review != "passed"
        or observation.external_side_effects != "none"
    ):
        raise ValueError("finalize Observation 不满足 Agent 完成条件")
    if (
        decision.observation_id != observation.observation_id
        or decision.selected_action != "finalize"
        or decision.allowed_actions != ["finalize"]
    ):
        raise ValueError("finalize Decision 与最新 Observation 不一致")


def _load_child_binding(
    run_dir: Path,
    state: AgentState,
    observation: AgentObservation,
) -> tuple[str, str]:
    child_ref = _single_evidence_ref(
        observation.evidence_refs,
        prefix="children/",
        label="child Finish 摘要",
    )
    try:
        child_summary = json.loads((run_dir / child_ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 child Finish 绑定摘要") from exc
    if not isinstance(child_summary, dict):
        raise ValueError("child Finish 绑定摘要必须是 JSON object")
    core = child_summary.get("core")
    if (
        child_summary.get("authority") != "child_binding_summary"
        or child_summary.get("agent_run_id") != state.run_id
        or child_summary.get("work_item_id") != state.current_work_item
        or child_summary.get("child_run") != observation.child_run
        or child_summary.get("operation_id") != observation.operation_id
        or not isinstance(core, dict)
        or core.get("finish_status") != "ready_to_commit"
        or not isinstance(core.get("finish_sha256"), str)
    ):
        raise ValueError("child Finish 绑定摘要与 Agent 终态不一致")
    return child_ref, core["finish_sha256"]


def _validate_child_finish(
    workspace: Path,
    observation: AgentObservation,
    finish_sha256: str,
) -> None:
    if observation.child_run is None:
        raise ValueError("finalize Observation 缺少 child run")
    finish_path = (
        resolve_run_dir(workspace, observation.child_run) / "finish-summary.json"
    )
    try:
        finish_bytes = finish_path.read_bytes()
        finish_summary = json.loads(finish_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 child finish-summary.json") from exc
    if (
        hashlib.sha256(finish_bytes).hexdigest() != finish_sha256
        or not isinstance(finish_summary, dict)
        or finish_summary.get("run_id") != observation.child_run
        or finish_summary.get("finish_status") != "ready_to_commit"
        or finish_summary.get("verification_passed") is not True
        or finish_summary.get("latest_verification_failed") is not False
        or not _nested_flag(finish_summary, "artifact_integrity", "valid")
        or not _nested_flag(finish_summary, "evidence_freshness", "fresh")
    ):
        raise ValueError("child Finish Artifact 已变化或不满足可信完成条件")


def _single_evidence_ref(
    refs: list[str],
    *,
    prefix: str,
    label: str,
) -> str:
    matches = [ref for ref in refs if ref.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"必须唯一定位 {label} Artifact")
    return matches[0]


def _nested_flag(payload: dict[str, object], key: str, nested: str) -> bool:
    value = payload.get(key)
    return isinstance(value, dict) and value.get(nested) is True
