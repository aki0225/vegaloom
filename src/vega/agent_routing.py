from __future__ import annotations

from uuid import uuid4

from .agent_contract import (
    AgentAction,
    AgentDecision,
    AgentObservation,
    AgentPhase,
    AgentPlan,
    AgentState,
    canonical_digest,
    utc_now,
)


_TRANSITION_PHASES: dict[AgentAction, AgentPhase] = {
    "next": "ready",
    "repair": "ready",
    "replan": "planning",
    "human": "needs_human",
    "finalize": "finalizing",
}


def decide_next_action(plan: AgentPlan, observation: AgentObservation) -> AgentDecision:
    allowed_actions, selected_action, reason = _route(plan, observation)
    return AgentDecision(
        decision_id=f"decision-{uuid4().hex[:12]}",
        observation_id=observation.observation_id,
        allowed_actions=allowed_actions,
        selected_action=selected_action,
        reason=reason,
        source="deterministic",
    )


def transition_state(
    state: AgentState,
    plan: AgentPlan,
    observation: AgentObservation,
    decision: AgentDecision,
) -> AgentState:
    expected = decide_next_action(plan, observation)
    if decision.observation_id != observation.observation_id:
        raise ValueError("Decision 与 Observation 身份不一致")
    if decision.selected_action not in expected.allowed_actions:
        raise ValueError(
            f"动作 {decision.selected_action} 已被确定性规则拒绝；"
            f"允许动作：{expected.allowed_actions}"
        )
    if state.approved_plan_digest != plan.approved_digest:
        raise ValueError("Agent State 与已批准 Plan digest 不一致")
    if (
        state.workspace_fingerprint is not None
        and state.workspace_fingerprint != observation.workspace_fingerprint
        and not observation.workspace_explained
    ):
        raise ValueError("Workspace 已漂移且尚未完成解释")

    next_state = state.model_copy(deep=True)
    next_state.phase = _TRANSITION_PHASES[decision.selected_action]
    next_state.state_version += 1
    next_state.workspace_fingerprint = observation.workspace_fingerprint
    next_state.allowed_actions = list(expected.allowed_actions)
    next_state.updated_at = utc_now()
    if decision.selected_action in {"next", "repair", "replan", "human"}:
        next_state.active_child_run = None
        next_state.active_operation_id = None
    if decision.selected_action == "replan":
        next_state.approved_plan_digest = None
    return AgentState.model_validate(next_state.model_dump(mode="json"))


def _route(
    plan: AgentPlan,
    observation: AgentObservation,
) -> tuple[list[AgentAction], AgentAction, str]:
    precondition = _precondition_route(plan, observation)
    if precondition is not None:
        return precondition

    blocking_gate = _blocking_gate(observation)
    if blocking_gate is not None:
        if observation.repairable_in_scope:
            return (
                ["repair", "replan", "human"],
                "repair",
                f"{blocking_gate} 未通过，但问题可在批准范围内修复",
            )
        return (
            ["replan", "human"],
            "replan",
            f"{blocking_gate} 未通过，当前范围不足以直接修复",
        )

    if observation.all_work_items_completed:
        incomplete_gate = _incomplete_final_gate(observation)
        if incomplete_gate is not None:
            return (
                ["human"],
                "human",
                f"全部 Work Item 已完成，但 {incomplete_gate} 证据不足或过期",
            )
        return ["finalize"], "finalize", "全部 Work Item 与完成门禁均已通过"

    if observation.work_item_completed:
        return ["next", "replan", "human"], "next", "当前 Work Item 已完成，可进入下一项"

    if observation.repairable_in_scope:
        return (
            ["repair", "replan", "human"],
            "repair",
            "当前 Work Item 尚未完成，问题仍可在批准范围内修复",
        )

    return ["human"], "human", "当前 Work Item 没有可信完成或可修复证据"


def _precondition_route(
    plan: AgentPlan,
    observation: AgentObservation,
) -> tuple[list[AgentAction], AgentAction, str] | None:
    checks = (
        (
            not plan.approval_is_current(),
            (["replan", "human"], "replan", "Plan 未批准或批准摘要已过期"),
        ),
        (
            observation.worker_alive,
            (["human"], "human", "旧 Worker 仍存活，禁止启动第二 Writer"),
        ),
        (
            not observation.workspace_explained,
            (["human"], "human", "Workspace 变化尚未完成机器对账"),
        ),
        (
            observation.external_side_effects == "unknown",
            (["human"], "human", "外部副作用未知，禁止自动重试"),
        ),
        (
            observation.plan_contradicted,
            (["replan", "human"], "replan", "新证据推翻已批准 Plan"),
        ),
    )
    return next((route for matched, route in checks if matched), None)


def _blocking_gate(observation: AgentObservation) -> str | None:
    for label, status in (
        ("Verification", observation.verification),
        ("Risk Gate", observation.risk),
        ("Reviewer", observation.review),
    ):
        if status in {"failed", "blocked"}:
            return label
    return None


def _incomplete_final_gate(observation: AgentObservation) -> str | None:
    for label, status in (
        ("Verification", observation.verification),
        ("Risk Gate", observation.risk),
        ("Reviewer", observation.review),
    ):
        if status != "passed":
            return label
    return None


def decision_input_digest(plan: AgentPlan, observation: AgentObservation) -> str:
    return canonical_digest(
        {
            "plan_digest": plan.approved_digest,
            "observation": observation.model_dump(mode="json"),
        }
    )
