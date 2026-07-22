from __future__ import annotations

from .models import (
    InterventionCandidate,
    MemorySnapshot,
    PlannedAction,
    ReminderDecision,
)

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def decide_reminder(
    action: PlannedAction,
    candidates: list[InterventionCandidate],
    snapshot: MemorySnapshot,
    recent_decisions: list[ReminderDecision] | None = None,
    *,
    apply_dedupe: bool = True,
) -> ReminderDecision:
    """使用固定规则评估干预，不调用 LLM，也不修改任何权威状态。"""
    recent_decisions = recent_decisions or []
    decision = _base_decision(action, candidates, snapshot)
    if (
        apply_dedupe
        and
        decision.decision == "remind"
        and not action.session_resumed
        and _recently_injected(decision.dedupe_key, recent_decisions[-3:])
    ):
        return decision.model_copy(
            update={
                "reminder": "",
                "suppressed_by_dedupe": True,
            }
        )
    return decision


def _base_decision(
    action: PlannedAction,
    candidates: list[InterventionCandidate],
    snapshot: MemorySnapshot,
) -> ReminderDecision:
    candidate_by_id = {item.candidate_id: item for item in candidates}
    for conflict in snapshot.conflicts:
        conflict_ids = [f"memory:{item_id}" for item_id in conflict.item_ids]
        if all(item_id in candidate_by_id for item_id in conflict_ids):
            return _decision(
                action,
                candidates,
                "escalate",
                "conflicting_candidates",
                "high",
                conflict_ids,
                "存在同级且相互冲突的有效信息，必须交由人工确认。",
            )

    pending = _first(candidates, "pending_approval")
    if pending:
        return _decision(
            action,
            candidates,
            "block",
            "pending_approval_conflict",
            "high",
            [pending.candidate_id],
            "当前操作尚未获得有效人工批准，请先完成审批。",
        )

    superseded = _first(candidates, "superseded_goal")
    if superseded:
        return _decision(
            action,
            candidates,
            "block",
            "superseded_goal",
            superseded.risk,
            [superseded.candidate_id],
            "当前计划仍指向已被替代的目标，请先同步最新需求。",
        )

    unknown_high_risk = [
        item
        for item in candidates
        if item.applicability_status == "unknown" and item.risk == "high"
    ]
    if unknown_high_risk:
        return _decision(
            action,
            candidates,
            "escalate",
            "applicability_unknown",
            "high",
            [item.candidate_id for item in unknown_high_risk],
            "高风险信息的适用条件不完整，不能静默放行，请先补齐当前状态。",
        )

    constraints = [
        item
        for item in candidates
        if item.kind in {"current_constraint", "constraint_interpretation"}
        and item.applicability_status == "applicable"
        and item.applicability.get("blocked_action") == action.action
    ]
    if constraints:
        return _decision(
            action,
            candidates,
            "block",
            "violates_constraint",
            _highest_risk(constraints),
            [item.candidate_id for item in constraints],
            "当前计划违反仍然有效的约束，请先调整行动。",
        )

    unscoped_high_risk_failures = [
        item
        for item in candidates
        if item.kind == "failed_attempt"
        and item.applicability_status == "applicable"
        and item.risk == "high"
        and not item.applicability.get("action")
    ]
    if unscoped_high_risk_failures:
        return _decision(
            action,
            candidates,
            "escalate",
            "applicability_unknown",
            "high",
            [item.candidate_id for item in unscoped_high_risk_failures],
            "高风险失败记录缺少具体 action 适用范围，请先人工确认是否相关。",
        )

    failures = [
        item
        for item in candidates
        if item.kind == "failed_attempt"
        and item.applicability_status == "applicable"
        and item.applicability.get("action") == action.action
    ]
    if failures:
        return _decision(
            action,
            candidates,
            "remind",
            "repeats_failed_attempt",
            _highest_risk(failures),
            [item.candidate_id for item in failures],
            "当前适用条件未变化，不要重复已被验证失败的方案。",
        )

    run_memory = [
        item
        for item in candidates
        if item.source_layer == "run_memory"
        and item.applicability_status == "applicable"
    ]
    if action.session_resumed and run_memory:
        return _decision(
            action,
            candidates,
            "remind",
            "session_resume_risk",
            _highest_risk(run_memory),
            [item.candidate_id for item in run_memory],
            "Session 已恢复，请先重新核对仍然有效的执行事实和约束。",
        )

    return _decision(action, candidates, "allow", "none", "low", [], "")


def _decision(
    action: PlannedAction,
    candidates: list[InterventionCandidate],
    decision: str,
    reason_code: str,
    risk: str,
    candidate_ids: list[str],
    reminder: str,
) -> ReminderDecision:
    normalized_ids = sorted(candidate_ids)
    dedupe_key = (
        f"{reason_code}:{','.join(normalized_ids)}:"
        + ",".join(
            sorted(
                f"{candidate.candidate_id}:{candidate.risk}:"
                f"{candidate.source_ref}:{sorted(candidate.applicability.items())}"
                for candidate in candidates
                if candidate.candidate_id in normalized_ids
            )
        )
        if decision != "allow"
        else ""
    )
    return ReminderDecision(
        checkpoint_id=action.checkpoint_id,
        decision=decision,
        reason_code=reason_code,
        risk=risk,
        candidate_ids=normalized_ids,
        reminder=reminder,
        dedupe_key=dedupe_key,
    )


def _first(
    candidates: list[InterventionCandidate],
    kind: str,
) -> InterventionCandidate | None:
    return next((item for item in candidates if item.kind == kind), None)


def _highest_risk(candidates: list[InterventionCandidate]) -> str:
    return max((item.risk for item in candidates), key=_RISK_ORDER.__getitem__)


def _recently_injected(
    dedupe_key: str,
    recent_decisions: list[ReminderDecision],
) -> bool:
    return any(
        item.dedupe_key == dedupe_key
        and item.decision == "remind"
        and not item.suppressed_by_dedupe
        and bool(item.reminder)
        for item in recent_decisions
    )
