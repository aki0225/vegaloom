from __future__ import annotations

from datetime import UTC, datetime

from .agent_contract import AgentObservation, AgentPlan, AgentState, AgentWorkItem


def new_task_id() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-agent-task")


def apply_work_item_progress(
    plan: AgentPlan,
    state: AgentState,
    observation: AgentObservation,
    action: str,
) -> AgentPlan:
    if state.current_work_item is None:
        return plan
    updated = plan.model_copy(deep=True)
    current = next(
        item for item in updated.work_items if item.work_item_id == state.current_work_item
    )
    if action == "next" and observation.work_item_completed:
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
