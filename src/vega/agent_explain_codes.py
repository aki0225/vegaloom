from __future__ import annotations

from collections.abc import Iterable
from typing import Literal


BlockCategory = Literal[
    "authorization",
    "transient",
    "configuration",
    "evidence",
    "budget",
]
PublicActionId = Literal[
    "change.start",
    "diff.inspect",
    "evidence.inspect",
    "handoff.create",
    "human.review",
    "plan.approve",
    "plan.revise",
    "provider.respond_decision",
    "provider.respond_input",
    "provider.steer",
    "provider.takeover",
    "run.continue",
    "run.stop",
    "status.view",
    "status.view_full",
]

_BLOCK_CATEGORIES: dict[str, BlockCategory] = {
    "approval.contract_required": "authorization",
    "approval.plan_contradicted": "authorization",
    "approval.plan_stale": "authorization",
    "budget.automatic_repair_exhausted": "budget",
    "evidence.decision_unverified": "evidence",
    "evidence.external_claim_only": "evidence",
    "evidence.integrity_unverified": "evidence",
    "evidence.no_trusted_progress": "evidence",
    "evidence.plan_completion_mismatch": "evidence",
    "evidence.status_projection_unverified": "evidence",
    "execution.writer_still_active": "transient",
    "gate.review.blocked": "evidence",
    "gate.review.incomplete": "evidence",
    "gate.risk.blocked": "authorization",
    "gate.risk.incomplete": "evidence",
    "gate.verification.blocked": "configuration",
    "gate.verification.incomplete": "evidence",
    "provider.interaction_required": "authorization",
    "provider.session_unverified": "evidence",
    "repair.fix_packet_unavailable": "evidence",
    "review.retry_exhausted": "transient",
    "review.runner_timed_out": "transient",
    "side_effects.declared": "authorization",
    "side_effects.unknown": "authorization",
    "workspace.snapshot_stale": "evidence",
    "workspace.unexplained_change": "evidence",
}
_PUBLIC_ACTIONS: dict[str, PublicActionId] = {
    "approve": "plan.approve",
    "change": "change.start",
    "finalize": "run.continue",
    "handoff": "handoff.create",
    "human": "human.review",
    "inspect_artifacts": "evidence.inspect",
    "inspect_diff": "diff.inspect",
    "next": "run.continue",
    "repair": "run.continue",
    "replan": "plan.revise",
    "provider.respond_decision": "provider.respond_decision",
    "provider.respond_input": "provider.respond_input",
    "revise": "plan.revise",
    "run": "run.continue",
    "status": "status.view",
    "status_full": "status.view_full",
    "steer": "provider.steer",
    "stop": "run.stop",
    "takeover": "provider.takeover",
}
_PUBLIC_ACTIONS.update({item: item for item in _PUBLIC_ACTIONS.values()})
_DECISION_INTERACTIONS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
}
_INPUT_INTERACTIONS = {
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
}


def block_category_for_reason_code(
    reason_code: str | None,
) -> BlockCategory | None:
    """使用稳定代码做静态分类，不解析人类可读 reason。"""

    if reason_code is None:
        return None
    if reason_code.startswith("budget."):
        return "budget"
    return _BLOCK_CATEGORIES.get(reason_code)


def public_action_ids(
    actions: Iterable[str],
    *,
    fallback: Iterable[str] = (),
    replan_action: Literal["plan.revise", "run.continue"] = "plan.revise",
) -> list[PublicActionId]:
    """把内部路由动作投影成稳定公开 ID，未知动作不进入用户界面。"""

    projected = _project_actions(actions, replan_action=replan_action)
    return projected or _project_actions(fallback, replan_action=replan_action)


def provider_interaction_actions(method: str) -> list[PublicActionId]:
    """按 Provider 请求类型给出真实可响应动作，未知方法只允许接管或停止。"""

    actions: list[PublicActionId] = []
    if method in _DECISION_INTERACTIONS:
        actions.append("provider.respond_decision")
    elif method in _INPUT_INTERACTIONS:
        actions.append("provider.respond_input")
    actions.extend(["provider.takeover", "run.stop"])
    return actions


def _project_actions(
    actions: Iterable[str],
    *,
    replan_action: Literal["plan.revise", "run.continue"],
) -> list[PublicActionId]:
    result: list[PublicActionId] = []
    for action in actions:
        public = replan_action if action == "replan" else _PUBLIC_ACTIONS.get(action)
        if public is not None and public not in result:
            result.append(public)
    return result
