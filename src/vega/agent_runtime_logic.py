from __future__ import annotations

from datetime import UTC, datetime

from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
    ObservationAuthority,
)
from .workspace_check import ReviewWorkspaceSnapshot


def new_task_id() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-agent-task")


def validate_observation_binding(
    state: AgentState,
    observation: AgentObservation,
    authority: ObservationAuthority,
) -> None:
    if not state.active_child_run or not state.active_operation_id:
        raise ValueError("Observation 必须绑定当前 active Writer，不能从空闲状态伪造完成")
    if state.phase not in {"acting", "observing", "needs_human"}:
        raise ValueError(f"当前阶段不能接受 Observation：{state.phase}")
    if state.current_work_item is None:
        raise ValueError("当前 Agent State 没有可对账的 Work Item")
    if authority == "machine_reconcile" and not observation.evidence_refs:
        raise ValueError("机器对账 Observation 必须包含 evidence_refs")
    if authority == "external_claim":
        return
    if (
        observation.child_run != state.active_child_run
        or observation.operation_id != state.active_operation_id
    ):
        raise ValueError("受信 Observation 与当前 Writer binding 不一致")
    if observation.work_item_id != state.current_work_item:
        raise ValueError("受信 Observation 与当前 Work Item 不一致")
    if observation.worker_alive and observation.work_item_completed:
        raise ValueError("Worker 仍存活时不能接受 Work Item 完成 Observation")
    if observation.operation_started != state.operation_started:
        raise ValueError("Observation 与持久化 operation_started 不一致")


def reconcile_observation(
    state: AgentState,
    observation: AgentObservation,
    authority: ObservationAuthority,
    snapshot: ReviewWorkspaceSnapshot,
) -> AgentObservation:
    common = {
        "authority": authority,
        "work_item_id": state.current_work_item,
        "child_run": state.active_child_run,
        "operation_id": state.active_operation_id,
        "workspace_fingerprint": snapshot.fingerprint,
        "changed_files": list(snapshot.changed_files),
        "unknown_file_count": len(snapshot.untracked_files),
    }
    if authority != "external_claim":
        return observation.model_copy(
            update={
                **common,
                "workspace_explained": (
                    observation.workspace_explained
                    and not snapshot.unsafe_index_paths
                    and snapshot.git_control_complete
                ),
            }
        )
    return observation.model_copy(
        update={
            **common,
            "machine_summary": (
                "外部 Observation 已记录为 Claim；"
                f"{observation.machine_summary}"
            ),
            "worker_alive": True,
            "operation_started": state.operation_started,
            "workspace_explained": False,
            "work_item_completed": False,
            "all_work_items_completed": False,
            "plan_contradicted": False,
            "repairable_in_scope": False,
            "verification": "not_run",
            "risk": "not_run",
            "review": "not_run",
            "external_side_effects": _external_claim_side_effects(
                observation,
                state,
            ),
        }
    )


def _external_claim_side_effects(
    observation: AgentObservation,
    state: AgentState,
) -> str:
    if observation.external_side_effects != "none":
        return observation.external_side_effects
    return "unknown" if state.operation_started else "none"


def apply_work_item_progress(
    plan: AgentPlan,
    state: AgentState,
    observation: AgentObservation,
    action: str,
) -> AgentPlan:
    if observation.authority == "external_claim":
        return plan
    if state.current_work_item is None:
        return plan
    updated = plan.model_copy(deep=True)
    current = next(
        item for item in updated.work_items if item.work_item_id == state.current_work_item
    )
    if observation.worker_alive:
        # 等待人工不等于当前 attempt 已失败；仍存活的 Writer 必须保留原 Work Item 状态。
        return plan
    if action in {"next", "finalize"} and observation.work_item_completed:
        current.status = "completed"
    elif action == "repair":
        current.status = "active"
    elif action == "replan":
        current.status = "superseded"
    elif action == "human":
        current.status = "blocked"
    return AgentPlan.model_validate(updated.model_dump(mode="json"))


def next_pending_work_item(
    plan: AgentPlan,
    current_work_item: str | None,
) -> AgentWorkItem | None:
    passed_current = current_work_item is None
    for item in plan.work_items:
        if not passed_current:
            passed_current = item.work_item_id == current_work_item
            continue
        if item.status == "pending":
            return item
    return None


def invalidate_plan_for_steer(plan: AgentPlan, instruction: str) -> AgentPlan:
    payload = plan.model_dump(mode="json")
    payload.update(
        {
            "goal_revision": plan.goal_revision + 1,
            "plan_revision": plan.plan_revision + 1,
            "approved": False,
            "approved_at": None,
            "approved_by": None,
            "approved_digest": None,
            "unresolved_decisions": [
                *plan.unresolved_decisions,
                f"人工 steer：{instruction.strip()}",
            ],
        }
    )
    return AgentPlan.model_validate(payload)


def update_state(state: AgentState, **changes: object) -> AgentState:
    payload = state.model_dump(mode="json")
    payload.update(changes)
    return AgentState.model_validate(payload)
