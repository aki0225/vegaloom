from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, ValidationError

from .agent_change_contract import ChangeContract
from .agent_change_fix_packet import write_change_fix_packet
from .agent_change_run import load_change_run_context
from .agent_contract import (
    AgentDecision,
    AgentObservation,
    AgentPlan,
    AgentState,
    NonEmptyText,
    StrictAgentModel,
)
from .agent_persistence import read_agent_trace
from .redaction import write_redacted_json_once


class ChangeBudgetSnapshot(StrictAgentModel):
    """当前 Work Item 的确定性预算计数。"""

    run_id: NonEmptyText
    work_item_id: NonEmptyText
    worker_attempts_used: int = Field(ge=0)
    repair_rounds_used: int = Field(ge=0)
    auto_replans_used: int = Field(ge=0)
    review_rounds_used: int = Field(ge=0)
    verification_retries_used: int = Field(ge=0)
    max_repair_rounds: int = Field(ge=0)
    max_auto_replans: int = Field(ge=0)
    max_review_rounds: int = Field(ge=1)
    max_verification_retries: int = Field(ge=0)


@dataclass(frozen=True)
class PreparedChangeDecision:
    decision: AgentDecision
    evidence_refs: tuple[str, ...] = ()
    fix_packet_ref: str | None = None

    @property
    def task_brief_refs(self) -> list[str] | None:
        return [self.fix_packet_ref] if self.fix_packet_ref is not None else None


def prepare_change_decision(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    observation: AgentObservation,
    decision: AgentDecision,
) -> PreparedChangeDecision:
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return PreparedChangeDecision(decision=decision)
    budget = change_budget_snapshot(
        run_dir,
        state,
        context.contract,
        current_observation=observation,
    )
    guarded = guard_change_decision_budget(decision, budget)
    fix_packet_ref: str | None = None
    if guarded.selected_action == "repair":
        try:
            fix_packet_ref = write_change_fix_packet(
                workspace,
                run_dir,
                state,
                observation,
                guarded,
                budget,
            )
        except ValueError as exc:
            guarded = guarded.model_copy(
                update={
                    "allowed_actions": ["human"],
                    "selected_action": "human",
                    "reason": (
                        "无法生成可信 Fix Packet，自动 Repair 已停止："
                        f"{exc}"
                    ),
                }
            )
    budget_ref = write_change_budget_artifact(
        run_dir,
        guarded,
        budget,
    )
    refs = [budget_ref]
    if fix_packet_ref is not None:
        refs.append(fix_packet_ref)
    return PreparedChangeDecision(
        decision=guarded,
        evidence_refs=tuple(refs),
        fix_packet_ref=fix_packet_ref,
    )


def change_budget_snapshot(
    run_dir: Path,
    state: AgentState,
    contract: ChangeContract,
    *,
    current_observation: AgentObservation | None = None,
) -> ChangeBudgetSnapshot:
    if state.current_work_item is None:
        raise ValueError("ChangeRun 缺少当前 Work Item，无法计算预算")
    trace = read_agent_trace(run_dir / "trace.jsonl")
    attempts = sum(
        item.get("event") == "worker_dispatch_committed"
        and item.get("work_item") == state.current_work_item
        for item in trace
    )
    auto_replans = sum(
        item.get("event") == "change_execution_plan_auto_applied"
        for item in trace
    )
    verification_retries = sum(
        item.get("event") == "verification_retry_committed"
        and item.get("work_item") == state.current_work_item
        for item in trace
    )
    review_rounds = _review_rounds_for_work_item(
        run_dir,
        state.current_work_item,
        trace,
        current_observation=current_observation,
    )
    envelope = contract.authority_envelope
    return ChangeBudgetSnapshot(
        run_id=state.run_id,
        work_item_id=state.current_work_item,
        worker_attempts_used=attempts,
        repair_rounds_used=max(0, attempts - 1),
        auto_replans_used=auto_replans,
        review_rounds_used=review_rounds,
        verification_retries_used=verification_retries,
        max_repair_rounds=envelope.max_repair_rounds,
        max_auto_replans=envelope.max_auto_replans,
        max_review_rounds=envelope.max_review_rounds,
        max_verification_retries=envelope.max_verification_retries,
    )


def require_change_verification_retry_budget(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
) -> ChangeBudgetSnapshot | None:
    """ChangeRun 重跑门禁前先消费人工批准的验证重试预算。"""

    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return None
    budget = change_budget_snapshot(run_dir, state, context.contract)
    if budget.verification_retries_used >= budget.max_verification_retries:
        raise ValueError(
            "当前 Work Item 的验证重试预算已用完："
            f"{budget.verification_retries_used}/"
            f"{budget.max_verification_retries}"
        )
    return budget


def require_change_review_budget(
    run_dir: Path,
    state: AgentState,
    contract: ChangeContract,
) -> ChangeBudgetSnapshot:
    """启动下一次 Writer 前确认该 Work Item 仍有一次 Review 配额。"""

    budget = change_budget_snapshot(run_dir, state, contract)
    if budget.review_rounds_used >= budget.max_review_rounds:
        raise ValueError(
            "当前 Work Item 的 Review 预算已用完："
            f"{budget.review_rounds_used}/{budget.max_review_rounds}；"
            "必须由人工修改 Contract、调整 Plan 或停止任务"
        )
    return budget


def guard_change_decision_budget(
    decision: AgentDecision,
    budget: ChangeBudgetSnapshot,
) -> AgentDecision:
    """在发布 repair 前确认下一轮仍有 Worker 与 Review 预算。"""

    if decision.selected_action != "repair":
        return decision
    exhausted: list[str] = []
    if budget.repair_rounds_used >= budget.max_repair_rounds:
        exhausted.append(
            f"Repair {budget.repair_rounds_used}/{budget.max_repair_rounds}"
        )
    if budget.review_rounds_used >= budget.max_review_rounds:
        exhausted.append(
            f"Review {budget.review_rounds_used}/{budget.max_review_rounds}"
        )
    if not exhausted:
        return decision
    return decision.model_copy(
        update={
            "allowed_actions": ["human"],
            "selected_action": "human",
            "reason": (
                "当前 Work Item 已达到自动停止条件："
                + "，".join(exhausted)
                + "；需要人工决定修改合同、调整计划或停止任务"
            ),
        }
    )


def write_change_budget_artifact(
    run_dir: Path,
    decision: AgentDecision,
    budget: ChangeBudgetSnapshot,
) -> str:
    relative = f"budgets/{decision.decision_id}.json"
    write_redacted_json_once(
        run_dir / relative,
        budget.model_dump(mode="json"),
    )
    return relative


def _review_rounds_for_work_item(
    run_dir: Path,
    work_item_id: str,
    trace: list[dict[str, object]],
    *,
    current_observation: AgentObservation | None = None,
) -> int:
    refs = list(
        dict.fromkeys(
            ref
            for item in trace
            for ref in item.get("artifact_refs", [])
            if isinstance(ref, str) and ref.startswith("observations/")
        )
    )
    if current_observation is not None:
        if current_observation.work_item_id != work_item_id:
            raise ValueError("当前 Observation 与 Review 预算 Work Item 不一致")
        current_ref = (
            f"observations/{current_observation.observation_id}.json"
        )
        if current_ref not in refs:
            refs.append(current_ref)
    total = 0
    observations_root = (run_dir / "observations").resolve()
    for ref in refs:
        path = (run_dir / ref).resolve()
        if not path.is_relative_to(observations_root):
            raise ValueError("Review 预算引用越出 observations 目录")
        try:
            observation = AgentObservation.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise ValueError("Review 预算引用的 Observation 无法验证") from exc
        if path.name != f"{observation.observation_id}.json":
            raise ValueError("Review 预算引用的 Observation 身份不一致")
        if (
            observation.work_item_id == work_item_id
            and observation.review != "not_run"
        ):
            total += 1
    return total
