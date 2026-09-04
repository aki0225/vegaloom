from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_contract import AgentCheckpoint, AgentPlan, AgentState
from .agent_planning import (
    PLANNING_PROPOSAL_ARTIFACT,
    PLANNING_REPORT_ARTIFACT,
    PLANNING_REQUEST_ARTIFACT,
    PlanningProposal,
    PlanningRequest,
    validate_published_planning_proposal,
)
from .agent_task_card import PlanningRunResume


@dataclass(frozen=True)
class PlanningHandoffContext:
    resume: PlanningRunResume | None = None
    non_goals: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.resume is not None


def planning_handoff_checkpoint_refs(
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> list[str]:
    required = {PLANNING_PROPOSAL_ARTIFACT, PLANNING_REPORT_ARTIFACT}
    if (
        state.run_kind == "change"
        and state.contract_revision is None
        and checkpoint is not None
        and required.issubset(checkpoint.evidence_refs)
    ):
        return sorted(required)
    return []


def can_offer_handoff(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    checkpoint: AgentCheckpoint | None,
) -> bool:
    """判断状态展示是否可以把 Handoff 列为安全动作。

    这里只检查当前 State、Checkpoint 和必要 Artifact 的最低前提；真正生成
    Task Card 时仍会重新校验仓库、Proposal、Workspace 和敏感路径。
    """

    if (
        state.handoff_status != "none"
        or not state.current_work_item
        or checkpoint is None
        or checkpoint.run_id != state.run_id
        or checkpoint.checkpoint_id != state.latest_checkpoint_id
    ):
        return False
    if state.contract_revision is not None:
        return (
            state.phase in {"ready", "needs_human", "stopped"}
            and plan.approval_is_current()
        )
    if state.phase not in {"planning", "needs_human", "stopped"}:
        return False
    refs = planning_handoff_checkpoint_refs(state, checkpoint)
    required = [PLANNING_REQUEST_ARTIFACT, *refs]
    return bool(refs) and all((run_dir / name).is_file() for name in required)


def planning_stop_trace_event(state: AgentState) -> str:
    if state.run_kind == "change" and state.contract_revision is None:
        return "planning_stopped"
    return "agent_stopped"


def load_planning_handoff_context(
    run_dir: Path,
    repo: Path,
    state: AgentState,
    plan: AgentPlan,
) -> PlanningHandoffContext:
    if state.run_kind != "change" or state.contract_revision is not None:
        return PlanningHandoffContext()
    if state.phase not in {"planning", "needs_human", "stopped"}:
        raise ValueError(
            f"当前阶段 {state.phase} 不能生成 Planning Handoff；"
            "必须先完成只读调查和现场对账"
        )
    try:
        request = PlanningRequest.model_validate_json(
            (run_dir / PLANNING_REQUEST_ARTIFACT).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("Planning Handoff 缺少有效 Planning Request") from exc
    proposal = validate_published_planning_proposal(
        run_dir,
        repo,
        state,
        plan,
        request,
    )
    resume = PlanningRunResume(
        source_revision=request.source_revision,
        proposal=PlanningProposal.model_validate(
            proposal.model_dump(mode="json")
        ),
    )
    contract = proposal.contract_proposal
    return PlanningHandoffContext(
        resume=resume,
        non_goals=tuple(contract.non_goals),
        risk_notes=tuple(
            f"建议人工风险复核：{value}"
            for value in contract.authorized_risk_reviews
        ),
        verification=tuple(contract.verification_suggestions),
    )
