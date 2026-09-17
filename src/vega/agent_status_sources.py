from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime

from .agent_candidate_evidence import matches_accepted_candidate_transition
from .agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentObservation,
    AgentState,
    AgentStatusCard,
    AgentPlan,
)
from .agent_repository_binding import capture_bound_workspace
from .agent_status_artifacts import (
    checkpoint_ref,
    load_bounded_checkpoint,
    load_bounded_decision,
    load_bounded_observation,
)
from .provider_session import ProviderSessionState, load_provider_sessions
from .workspace_snapshot import ReviewWorkspaceSnapshot
from .agent_change_run import load_candidate_artifact, load_change_run_context
from .agent_child_status import AgentChildStatusSnapshot
from .agent_repository_binding import load_run_metadata
from .project_config import load_project_config
from .run_execution_status import latest_execution_payload
from .verification_command_preflight import preparation_policy_issue


def known_candidate_transition(
    run_dir: Path, state: AgentState, plan: AgentPlan,
    workspace: ReviewWorkspaceSnapshot | None, child: AgentChildStatusSnapshot,
    execution: dict | None, operation_kind: str | None,
) -> bool:
    """识别同口径的 Candidate 绑定快照；不证明进程存活或 Core 通过。"""
    if (
        state.run_kind != "change" or state.execution_protocol != 2
        or state.phase not in {"acting", "observing"} or operation_kind != "worker"
        or not state.active_candidate_sha or not state.active_operation_id
        or workspace is None or workspace.head_sha != state.active_candidate_sha
        or workspace.fingerprint != state.workspace_fingerprint
        or child.child_run != state.active_child_run or child.child_state is None
        or child.child_state.status != "running" or child.live_stage != "verify"
        or child.child_state.initial_head_sha != state.active_candidate_sha
        or not execution or execution.get("run_id") != child.child_run
        or execution.get("step") != "verification"
        or execution.get("iteration") != child.child_state.current_iteration
        or execution.get("status") not in {"starting", "running"}
        or execution.get("termination_unconfirmed") is not False
    ):
        return False
    try:
        expiry = min(datetime.fromisoformat(execution[key]) for key in ("deadline", "lease_expires_at"))
        if datetime.now(UTC) >= expiry:
            return False
        context = load_change_run_context(run_dir, state, plan, load_run_metadata(run_dir))
        candidate = load_candidate_artifact(run_dir, f"candidates/{state.active_operation_id}.json")
        return bool(
            context is not None and candidate.run_id == state.run_id
            and candidate.operation_id == state.active_operation_id
            and candidate.work_item_id == state.current_work_item
            and candidate.candidate_sha == state.active_candidate_sha
            and candidate.parent_sha == state.accepted_checkpoint_sha
            and candidate.branch == context.worktree.branch
            and candidate.contract_revision == context.contract.contract_revision
            and candidate.approved_contract_digest == context.contract.approved_digest
            and candidate.execution_plan_revision == context.execution_plan.plan_revision
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def verification_interruption_detail(finish: dict, observation: AgentObservation) -> str | None:
    """只解释已由调用方核对身份和摘要的 Finish，不改变门禁结果。"""
    try:
        latest = finish["iterations"][-1]
        matches = [item for item in finish["verification_results"]
                   if item["iteration"] == latest["iteration"]]
        if len(matches) != 1 or finish["artifact_integrity"]["valid"] is not True:
            return None
        result = matches[0]
        # 父 Agent 的 comparison binding 不同；这里只核对同一 Core Finish 的采集口径。
        core_fingerprint = finish["evidence_freshness"]["current_workspace_fingerprint"]
        if (result["run_id"] != observation.child_run
                or not isinstance(core_fingerprint, str) or not core_fingerprint
                or result["workspace_fingerprint"] != core_fingerprint
                or observation.external_side_effects == "unknown"
                or set(finish["evidence_freshness"]["issues"]) - {"trusted_review_missing"}):
            return None
        interruption = result.get("interruption_status")
        if any(item.get("interruption_status") == "termination-unconfirmed" for item in result.get("results", [])):
            interruption = "termination-unconfirmed"
        if interruption not in {"timed_out", "stopped", "termination-unconfirmed"}:
            return None
        remaining = len(result["skipped_commands"])
        review = "；本轮 Reviewer 未运行" if latest["reviewer_status"] == "skipped" else ""
        return f"Core 验证中断：{interruption}；后续 {remaining} 条验证未运行{review}（已绑定的历史记录）。"
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def explanation_detail(status: dict, fallback: str) -> str:
    """保留首要告警；阶段和门禁明细只补充上下文，不推导新动作。"""
    details = [item["detail"] for item in status.get("supervisor_evidence", [])
               if item.get("status") != "passed"]
    note = status.get("core_stage_note")
    return " ".join([fallback, *details, *([note] if note else [])])


def preparation_issue_for_display(run_dir: Path, state: AgentState, plan: AgentPlan) -> str | None:
    """调用方先排除证据和活动执行异常；这里只读当前绑定的准备策略。"""
    metadata = load_run_metadata(run_dir)
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return None
    return preparation_policy_issue(
        load_project_config(context.worktree.worktree_path), context.contract.prepare_commands,
    )


def load_provider_sessions_for_display(
    run_dir: Path,
) -> tuple[ProviderSessionState | None, str | None]:
    try:
        return load_provider_sessions(run_dir), None
    except ValueError:
        return None, "Provider Session 协调状态无法验证；Core 证据不受影响。"


def load_status_decision_for_display(
    run_dir: Path,
    checkpoint: AgentCheckpoint | None,
    *,
    decisions: tuple[AgentDecision, ...] | None = None,
) -> tuple[AgentDecision | None, str | None]:
    """读取与最近 Checkpoint 绑定的 Decision，供状态和解释共用。"""

    if checkpoint is None:
        return None, None
    decision_refs = [
        ref
        for ref in checkpoint.evidence_refs
        if ref.startswith("decisions/")
    ]
    if not decision_refs:
        return None, None
    if len(decision_refs) != 1:
        return None, "最近 Checkpoint 无法唯一定位 Decision。"
    decision_ref = decision_refs[0]
    if decisions is None:
        try:
            decision, _ = load_bounded_decision(run_dir, decision_ref)
        except ValueError:
            return None, "最近 Decision 无法验证。"
    else:
        matched = [
            item
            for item in decisions
            if decision_ref == f"decisions/{item.decision_id}.json"
        ]
        if len(matched) != 1:
            return None, "最近 Decision 无法从状态快照唯一定位。"
        decision = matched[0]
    observation_ref = f"observations/{decision.observation_id}.json"
    if (
        decision_ref != f"decisions/{decision.decision_id}.json"
        or observation_ref not in checkpoint.evidence_refs
        or not _decision_action_matches_checkpoint(checkpoint, decision)
    ):
        return None, "最近 Decision 与 Checkpoint 的身份或动作绑定不一致。"
    return decision, None


def _decision_action_matches_checkpoint(
    checkpoint: AgentCheckpoint,
    decision: AgentDecision,
) -> bool:
    if decision.selected_action in checkpoint.pending_actions:
        return True
    return (
        checkpoint.phase == "completed"
        and checkpoint.status == "safe"
        and not checkpoint.pending_actions
        and decision.selected_action == "finalize"
    )


def capture_live_workspace(
    run_dir: Path,
) -> tuple[ReviewWorkspaceSnapshot | None, str | None]:
    try:
        return capture_bound_workspace(run_dir), None
    except (OSError, RuntimeError, ValueError):
        return None, "当前 Workspace 无法重新采集或绑定无法验证"


def load_status_checkpoint(
    run_dir: Path,
    state: AgentState,
) -> AgentCheckpoint | None:
    if state.latest_checkpoint_id is None:
        return None
    expected_ref = checkpoint_ref(state.latest_checkpoint_id)
    checkpoint, _ = load_bounded_checkpoint(
        run_dir,
        state.latest_checkpoint_id,
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.checkpoint_id != state.latest_checkpoint_id
        or checkpoint.current_work_item != state.current_work_item
        or expected_ref != f"checkpoints/{checkpoint.checkpoint_id}.json"
    ):
        raise ValueError("最新 Checkpoint 与 Agent State 不一致，拒绝展示状态卡")
    return checkpoint


def load_status_checkpoint_for_display(
    run_dir: Path,
    state: AgentState,
) -> tuple[AgentCheckpoint | None, str | None]:
    """读取展示用 Checkpoint；损坏证据不应让状态查询崩溃。"""

    if state.latest_checkpoint_id is None:
        if state.phase in {
            "ready",
            "acting",
            "observing",
            "needs_human",
            "finalizing",
            "completed",
            "stopped",
        }:
            return None, "最近 Checkpoint 缺失。"
        return None, None
    try:
        return load_status_checkpoint(run_dir, state), None
    except ValueError:
        return None, "最近 Checkpoint 缺失、损坏或与 Agent State 绑定不一致。"


def load_status_observation(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> AgentObservation | None:
    if checkpoint is None:
        return None
    refs = [
        ref
        for ref in checkpoint.evidence_refs
        if ref.startswith("observations/")
    ]
    if not refs:
        return None
    if len(refs) != 1:
        raise ValueError("最新 Checkpoint 无法唯一定位 Observation")
    try:
        observation, _ = load_bounded_observation(run_dir, refs[0])
    except ValueError as exc:
        raise ValueError("最新 Observation 无法验证，拒绝展示状态卡") from exc
    if (
        refs[0] != f"observations/{observation.observation_id}.json"
        or (
            observation.work_item_id != state.current_work_item
            and not matches_accepted_candidate_transition(
                run_dir,
                state,
                checkpoint,
                observation,
            )
        )
        or observation.workspace_fingerprint != checkpoint.workspace_fingerprint
    ):
        raise ValueError("最新 Observation 与 Checkpoint 不一致，拒绝展示状态卡")
    return observation


def load_status_observation_for_display(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> tuple[AgentObservation | None, str | None]:
    """读取展示用 Observation；损坏证据只降级展示，不改变权威 State。"""

    try:
        return load_status_observation(run_dir, state, checkpoint), None
    except ValueError:
        return None, "最近 Observation 缺失、损坏或与 Checkpoint 绑定不一致。"


def execution_for_display(
    run_dir: Path, state: AgentState, child: AgentChildStatusSnapshot,
    operation_kind: str | None, card: AgentStatusCard, run_status: str,
) -> tuple[dict | None, AgentStatusCard]:
    execution_dir = child.child_dir if state.active_child_run and operation_kind != "environment_prepare" else run_dir
    try:
        execution = latest_execution_payload(execution_dir, run_status) if execution_dir else None
        return execution, card
    except (OSError, ValueError):
        return None, card.model_copy(update={
            "integrity_warning": card.integrity_warning or "执行记录缺失、损坏或无法验证；请人工核对。",
        })


def core_verification_stage_note(state: AgentState, child: AgentChildStatusSnapshot) -> str | None:
    return (
        "绑定 Core 最近记录为验证阶段；进程状态及最终结果待核对。"
        if state.active_child_run == child.child_run and state.active_operation_id
        and child.live_stage == "verify" else None
    )
