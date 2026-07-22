from __future__ import annotations

from typing import Literal

from .models import (
    InterventionCandidate,
    MemorySnapshot,
    PlannedAction,
)

_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
_SOURCE_ORDER = {"canonical_state": 0, "run_memory": 1}
_POLICY_METADATA_KEYS = frozenset({"blocked_action"})


def build_candidates(
    snapshot: MemorySnapshot,
    canonical_candidates: list[InterventionCandidate],
    action: PlannedAction,
) -> list[InterventionCandidate]:
    """统一 Canonical State 与 active Run Memory，但不提升 candidate item。

    applicability 缺字段与明确不匹配必须分开：前者保留为 unknown，交给策略决定是否
    fail-closed；后者才从候选中排除。这样不会把“当前信息不足”伪装成“确定不适用”。
    """
    candidates: list[InterventionCandidate] = []
    for candidate in canonical_candidates:
        if not candidate.applicable:
            continue
        status = _applicability_status(candidate.applicability, action.context)
        if status == "not_applicable":
            continue
        candidates.append(
            candidate.model_copy(update={"applicability_status": status})
        )

    conflict_groups = {
        item_id: conflict.conflict_key
        for conflict in snapshot.conflicts
        for item_id in conflict.item_ids
    }
    for item in snapshot.active_items:
        status = _applicability_status(item.applicability, action.context)
        if status == "not_applicable":
            continue
        candidates.append(
            InterventionCandidate(
                candidate_id=f"memory:{item.id}",
                source_layer="run_memory",
                source_ref=item.source_ref,
                kind=item.kind,
                statement=item.statement,
                authority="verified",
                risk=item.risk,
                applicable=True,
                applicability_status=status,
                applicability=item.applicability,
                conflict_group=conflict_groups.get(item.id),
            )
        )
    return sorted(
        candidates,
        key=lambda item: (
            _SOURCE_ORDER[item.source_layer],
            0 if item.conflict_group else 1,
            _RISK_ORDER[item.risk],
            item.candidate_id,
        ),
    )


def build_always_on_candidates(
    snapshot: MemorySnapshot,
    canonical_candidates: list[InterventionCandidate],
) -> list[InterventionCandidate]:
    """构建 B 组完整 active Memory 视图，不按 planned action 做相关性过滤。"""
    candidates = [
        candidate
        for candidate in canonical_candidates
        if candidate.applicable
    ]
    conflict_groups = {
        item_id: conflict.conflict_key
        for conflict in snapshot.conflicts
        for item_id in conflict.item_ids
    }
    candidates.extend(
        InterventionCandidate(
            candidate_id=f"memory:{item.id}",
            source_layer="run_memory",
            source_ref=item.source_ref,
            kind=item.kind,
            statement=item.statement,
            authority="verified",
            risk=item.risk,
            applicable=True,
            applicability_status="applicable",
            applicability=item.applicability,
            conflict_group=conflict_groups.get(item.id),
        )
        for item in snapshot.active_items
    )
    return sorted(
        candidates,
        key=lambda item: (
            _SOURCE_ORDER[item.source_layer],
            0 if item.conflict_group else 1,
            _RISK_ORDER[item.risk],
            item.candidate_id,
        ),
    )


def select_top_k(
    candidates: list[InterventionCandidate],
    top_k: int = 5,
) -> list[InterventionCandidate]:
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    return candidates[:top_k]


def render_candidate_context(candidates: list[InterventionCandidate]) -> str:
    """B/C 只计算额外 Run Memory 文本，Canonical State 是四组共享输入。"""
    lines = [
        (
            f"- [{candidate.kind}] {candidate.statement}"
            + (
                "（适用条件待确认）"
                if candidate.applicability_status == "unknown"
                else ""
            )
        )
        for candidate in candidates
        if candidate.source_layer == "run_memory"
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _applicability_status(
    requirements: dict[str, str],
    context: dict[str, str],
) -> Literal["applicable", "not_applicable", "unknown"]:
    relevant = {
        key: value
        for key, value in requirements.items()
        if key not in _POLICY_METADATA_KEYS
    }
    if any(key in context and context[key] != value for key, value in relevant.items()):
        return "not_applicable"
    if any(key not in context for key in relevant):
        return "unknown"
    return "applicable"
