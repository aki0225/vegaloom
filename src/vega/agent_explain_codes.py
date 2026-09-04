from __future__ import annotations

from typing import Literal


BlockCategory = Literal[
    "authorization",
    "transient",
    "configuration",
    "evidence",
    "budget",
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


def block_category_for_reason_code(
    reason_code: str | None,
) -> BlockCategory | None:
    """使用稳定代码做静态分类，不解析人类可读 reason。"""

    if reason_code is None:
        return None
    if reason_code.startswith("budget."):
        return "budget"
    return _BLOCK_CATEGORIES.get(reason_code)
