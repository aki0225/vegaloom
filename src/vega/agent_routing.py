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
    allowed_actions, selected_action, reason_code, reason = _route(plan, observation)
    return AgentDecision(
        decision_id=f"decision-{uuid4().hex[:12]}",
        observation_id=observation.observation_id,
        allowed_actions=allowed_actions,
        selected_action=selected_action,
        reason_code=reason_code,
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
    if (
        decision.selected_action not in decision.allowed_actions
        or not set(decision.allowed_actions).issubset(expected.allowed_actions)
    ):
        raise ValueError("Decision 声明了确定性规则未授权的动作")
    if state.approved_plan_digest != plan.approved_digest:
        raise ValueError("Agent State 与已批准 Plan digest 不一致")
    if (
        state.workspace_fingerprint is not None
        and state.workspace_fingerprint != observation.workspace_fingerprint
        and not observation.workspace_explained
    ):
        raise ValueError("Workspace 已漂移且尚未完成解释")

    payload = state.model_dump(mode="json")
    payload.update(
        {
            "phase": _TRANSITION_PHASES[decision.selected_action],
            "state_version": state.state_version + 1,
            "workspace_fingerprint": observation.workspace_fingerprint,
            "allowed_actions": list(decision.allowed_actions),
            "updated_at": utc_now(),
        }
    )
    if observation.authority != "external_claim" and not observation.worker_alive:
        payload.update(
            {
                "active_child_run": None,
                "active_operation_id": None,
                "operation_started": False,
            }
        )
    if decision.selected_action == "replan":
        payload["approved_plan_digest"] = None
    return AgentState.model_validate(payload)


def _route(
    plan: AgentPlan,
    observation: AgentObservation,
) -> tuple[list[AgentAction], AgentAction, str, str]:
    precondition = _precondition_route(plan, observation)
    if precondition is not None:
        return precondition

    blocked_gate = _human_blocked_gate(observation)
    if blocked_gate is not None:
        label, code = blocked_gate
        return (
            ["human"],
            "human",
            f"gate.{code}.blocked",
            f"{label} 明确要求人工处理，不能自动 repair 或 replan",
        )

    blocking_gate = _failed_gate(observation)
    if blocking_gate is not None:
        label, code = blocking_gate
        if observation.repairable_in_scope:
            return (
                ["repair", "replan", "human"],
                "repair",
                f"gate.{code}.failed_repairable",
                f"{label} 未通过，但问题可在批准范围内修复",
            )
        return (
            ["replan", "human"],
            "replan",
            f"gate.{code}.failed_replan",
            f"{label} 未通过，当前范围不足以直接修复",
        )

    if observation.all_work_items_completed:
        incomplete_gate = _incomplete_final_gate(observation)
        if incomplete_gate is not None:
            label, code = incomplete_gate
            return (
                ["human"],
                "human",
                f"gate.{code}.incomplete",
                f"全部 Work Item 已完成，但 {label} 证据不足或过期",
            )
        return (
            ["finalize"],
            "finalize",
            "workflow.all_work_items_completed",
            "全部 Work Item 与完成门禁均已通过",
        )

    if observation.work_item_completed:
        incomplete_gate = _incomplete_final_gate(observation)
        if incomplete_gate is not None:
            label, code = incomplete_gate
            return (
                ["human"],
                "human",
                f"gate.{code}.incomplete",
                f"当前 Work Item 已完成，但 {label} 证据不足或过期",
            )
        return (
            ["next", "replan", "human"],
            "next",
            "workflow.work_item_completed",
            "当前 Work Item 已完成，可进入下一项",
        )

    if observation.repairable_in_scope:
        return (
            ["repair", "replan", "human"],
            "repair",
            "workflow.repairable_in_scope",
            "当前 Work Item 尚未完成，问题仍可在批准范围内修复",
        )

    return (
        ["human"],
        "human",
        "evidence.no_trusted_progress",
        "当前 Work Item 没有可信完成或可修复证据",
    )


def _precondition_route(
    plan: AgentPlan,
    observation: AgentObservation,
) -> tuple[list[AgentAction], AgentAction, str, str] | None:
    checks = (
        (
            observation.authority == "external_claim",
            (
                ["human"],
                "human",
                "evidence.external_claim_only",
                "外部 Observation 只作为 Claim 记录，不能授予进度或门禁通过资格",
            ),
        ),
        (
            not plan.approval_is_current(),
            (
                ["replan", "human"],
                "replan",
                "approval.plan_stale",
                "Plan 未批准或批准摘要已过期",
            ),
        ),
        (
            observation.all_work_items_completed
            and not _finalization_claim_matches_plan(plan, observation),
            (
                ["human"],
                "human",
                "evidence.plan_completion_mismatch",
                "Observation 声称全部完成，但 Plan 仍有未完成 Work Item",
            ),
        ),
        (
            observation.worker_alive,
            (
                ["human"],
                "human",
                "execution.writer_still_active",
                "旧 Worker 仍存活，禁止启动第二 Writer",
            ),
        ),
        (
            not observation.workspace_explained,
            (
                ["human"],
                "human",
                "workspace.unexplained_change",
                "Workspace 变化尚未完成机器对账",
            ),
        ),
        (
            observation.external_side_effects != "none",
            (
                ["human"],
                "human",
                (
                    "side_effects.unknown"
                    if observation.external_side_effects == "unknown"
                    else "side_effects.declared"
                ),
                (
                    "外部副作用未知，禁止自动重试"
                    if observation.external_side_effects == "unknown"
                    else "已声明存在外部副作用，必须由人工确认后续动作"
                ),
            ),
        ),
        (
            observation.plan_contradicted,
            (
                ["replan", "human"],
                "replan",
                "approval.plan_contradicted",
                "新证据推翻已批准 Plan",
            ),
        ),
    )
    return next((route for matched, route in checks if matched), None)


def _finalization_claim_matches_plan(
    plan: AgentPlan,
    observation: AgentObservation,
) -> bool:
    if not observation.work_item_completed or observation.work_item_id is None:
        return False
    return all(
        item.work_item_id == observation.work_item_id
        or item.status in {"completed", "superseded"}
        for item in plan.work_items
    )


def _human_blocked_gate(
    observation: AgentObservation,
) -> tuple[str, str] | None:
    for label, code, status in (
        ("Verification", "verification", observation.verification),
        ("Risk Gate", "risk", observation.risk),
        ("Reviewer", "review", observation.review),
    ):
        if status == "blocked":
            return label, code
    return None


def _failed_gate(observation: AgentObservation) -> tuple[str, str] | None:
    for label, code, status in (
        ("Verification", "verification", observation.verification),
        ("Risk Gate", "risk", observation.risk),
        ("Reviewer", "review", observation.review),
    ):
        if status == "failed":
            return label, code
    return None


def _incomplete_final_gate(
    observation: AgentObservation,
) -> tuple[str, str] | None:
    for label, code, status in (
        ("Verification", "verification", observation.verification),
        ("Risk Gate", "risk", observation.risk),
        ("Reviewer", "review", observation.review),
    ):
        if status != "passed":
            return label, code
    return None


def decision_input_digest(plan: AgentPlan, observation: AgentObservation) -> str:
    return canonical_digest(
        {
            "plan_digest": plan.approved_digest,
            "observation": observation.model_dump(mode="json"),
        }
    )
