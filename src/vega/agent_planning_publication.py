from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from .agent_contract import AgentPlan, AgentState
from .agent_persistence import (
    append_agent_trace,
    append_agent_trace_commit,
    load_agent_checkpoint,
    read_agent_trace,
    save_agent_state,
)
from .agent_planning import (
    PLANNING_PROPOSAL_ARTIFACT,
    PLANNING_REPORT_ARTIFACT,
    PlanningProposal,
    PlanningRequest,
    render_planning_proposal,
    validate_planning_proposal,
    validate_published_planning_proposal,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    save_agent_plan,
    write_checkpoint,
    write_status_card,
)
from .redaction import (
    redact_text,
    write_redacted_json_once,
    write_redacted_text,
)


def reuse_or_publish_planning_proposal(
    run_dir: Path,
    repo: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    request: PlanningRequest,
    *,
    event_reporter: Callable[[str], None] | None,
) -> AgentRun | None:
    """恢复已经写出的 Proposal；没有 Proposal 时返回 None。"""

    proposal_path = run_dir / PLANNING_PROPOSAL_ARTIFACT
    if not proposal_path.exists():
        return None
    if planning_publication_committed(run_dir, state):
        try:
            proposal = validate_published_planning_proposal(
                run_dir,
                repo,
                state,
                plan,
                request,
            )
            repair_published_planning_presentation(run_dir, state, plan, proposal)
        except ValueError as exc:
            return publish_planning_failure(
                run_dir,
                state,
                plan,
                snapshot,
                str(exc),
                needs_human=True,
                evidence_refs=[],
                event_reporter=event_reporter,
            )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)
    try:
        proposal = PlanningProposal.model_validate_json(
            proposal_path.read_text(encoding="utf-8")
        )
        validate_planning_proposal(
            repo,
            proposal,
            task_id=request.task_id,
            user_goal=request.user_goal,
            source_revision=request.source_revision,
        )
    except (OSError, ValidationError, ValueError) as exc:
        return publish_planning_failure(
            run_dir,
            state,
            plan,
            snapshot,
            f"未完成发布的 Planning Proposal 无法恢复：{exc}",
            needs_human=True,
            evidence_refs=[],
            event_reporter=event_reporter,
        )
    return publish_planning_proposal(
        run_dir,
        state,
        plan,
        snapshot,
        proposal,
        evidence_refs=[],
        event_reporter=event_reporter,
    )


def publish_planning_proposal(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    proposal: PlanningProposal,
    *,
    evidence_refs: list[str],
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    _write_or_validate_proposal_json(run_dir, proposal)
    write_redacted_text(
        run_dir / PLANNING_REPORT_ARTIFACT,
        render_planning_proposal(proposal),
    )
    projected = plan.model_copy(
        update={
            "observed_facts": [
                fact.statement for fact in proposal.observed_facts
            ],
            "hypotheses": list(proposal.hypotheses),
            "unresolved_decisions": [
                *proposal.unresolved_questions,
                "Planning Proposal 尚未经过 Contract Compiler",
            ],
        }
    )
    next_state = update_state(
        state,
        state_version=state.state_version + 1,
        active_planning_execution_id=None,
        workspace_fingerprint=snapshot.fingerprint,
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        snapshot,
        reason="Planning Proposal 已生成，等待编译为可批准合同",
        status="safe",
        pending_actions=["replan", "human"],
        evidence_refs=[
            *evidence_refs,
            PLANNING_PROPOSAL_ARTIFACT,
            PLANNING_REPORT_ARTIFACT,
        ],
        operation_started=False,
        external_side_effects="none",
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_plan(run_dir, projected)
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace_commit(
        run_dir / "trace.jsonl",
        event="planning_proposal_created",
        state=next_state,
        observation_summary=(
            f"{len(proposal.observed_facts)} 条事实，"
            f"{len(proposal.hypotheses)} 条假设，"
            f"{len(proposal.unresolved_questions)} 个未决问题"
        ),
        artifact_refs=[
            PLANNING_PROPOSAL_ARTIFACT,
            PLANNING_REPORT_ARTIFACT,
            f"checkpoints/{checkpoint.checkpoint_id}.json",
            *evidence_refs,
        ],
        writer=append_agent_trace,
    )
    write_status_card(
        run_dir,
        next_state,
        projected,
        checkpoint=checkpoint,
        next_step=checkpoint.reason,
    )
    _report(event_reporter, "Planning Proposal 已生成")
    return AgentRun(run_dir=run_dir, state=next_state, plan=projected)


def publish_planning_failure(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    reason: str,
    *,
    needs_human: bool,
    evidence_refs: list[str],
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    safe_reason = redact_text(reason.strip())[:2000]
    next_state = update_state(
        state,
        phase="needs_human" if needs_human else "planning",
        state_version=state.state_version + 1,
        active_planning_execution_id=None,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["human"] if needs_human else ["replan", "human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        snapshot,
        reason=safe_reason,
        status="blocked" if needs_human else "safe",
        pending_actions=["human"] if needs_human else ["replan", "human"],
        evidence_refs=evidence_refs,
        operation_started=False,
        external_side_effects="unknown" if needs_human else "none",
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="planning_blocked" if needs_human else "planning_retry_required",
        state=next_state,
        route_reason=safe_reason,
        artifact_refs=[
            f"checkpoints/{checkpoint.checkpoint_id}.json",
            *evidence_refs,
        ],
    )
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=safe_reason,
    )
    _report(event_reporter, safe_reason)
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def publish_active_planning_blocked(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    reason: str,
    *,
    evidence_refs: list[str],
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    """外部进程尚未静默时保留 execution binding，禁止发布 Proposal。"""

    safe_reason = redact_text(reason.strip())[:2000]
    next_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        snapshot,
        reason=safe_reason,
        status="blocked",
        pending_actions=["human"],
        evidence_refs=evidence_refs,
        operation_started=True,
        external_side_effects="unknown",
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="planning_blocked",
        state=next_state,
        route_reason=safe_reason,
        artifact_refs=[
            f"checkpoints/{checkpoint.checkpoint_id}.json",
            *evidence_refs,
        ],
    )
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step=(
            f"{safe_reason}；先人工终止并核对 owned process，"
            "确认进程退出后重新运行同一 ChangeRun 完成对账"
        ),
    )
    _report(event_reporter, safe_reason)
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def publish_planning_stopped(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    reason: str,
    *,
    evidence_refs: list[str],
    event_reporter: Callable[[str], None] | None,
) -> AgentRun:
    safe_reason = redact_text(reason.strip())[:2000]
    next_state = update_state(
        state,
        phase="stopped",
        state_version=state.state_version + 1,
        active_planning_execution_id=None,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=[],
    )
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        snapshot,
        reason=safe_reason,
        status="safe",
        pending_actions=[],
        evidence_refs=evidence_refs,
        operation_started=False,
        external_side_effects="none",
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="planning_stopped",
        state=next_state,
        route_reason=safe_reason,
        artifact_refs=[
            f"checkpoints/{checkpoint.checkpoint_id}.json",
            *evidence_refs,
        ],
    )
    write_status_card(
        run_dir,
        next_state,
        plan,
        checkpoint=checkpoint,
        next_step="Planning 已停止；如需继续，请从 Task Card 或新的 ChangeRun 开始",
    )
    _report(event_reporter, safe_reason)
    return AgentRun(run_dir=run_dir, state=next_state, plan=plan)


def repair_published_planning_presentation(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    proposal: PlanningProposal,
) -> None:
    assert state.latest_checkpoint_id is not None
    checkpoint = load_agent_checkpoint(
        run_dir
        / "checkpoints"
        / f"{state.latest_checkpoint_id}.json"
    )
    if not _planning_trace_exists(run_dir, state):
        append_agent_trace_commit(
            run_dir / "trace.jsonl",
            event="planning_proposal_created",
            state=state,
            observation_summary=(
                f"{len(proposal.observed_facts)} 条事实，"
                f"{len(proposal.hypotheses)} 条假设，"
                f"{len(proposal.unresolved_questions)} 个未决问题"
            ),
            artifact_refs=[
                PLANNING_PROPOSAL_ARTIFACT,
                PLANNING_REPORT_ARTIFACT,
                f"checkpoints/{checkpoint.checkpoint_id}.json",
            ],
            writer=append_agent_trace,
        )
    write_status_card(
        run_dir,
        state,
        plan,
        checkpoint=checkpoint,
        next_step=checkpoint.reason,
    )


def planning_publication_committed(
    run_dir: Path,
    state: AgentState,
) -> bool:
    if state.latest_checkpoint_id is None:
        return False
    checkpoint_path = (
        run_dir
        / "checkpoints"
        / f"{state.latest_checkpoint_id}.json"
    )
    if not checkpoint_path.exists():
        return True
    try:
        checkpoint = load_agent_checkpoint(checkpoint_path)
    except (OSError, ValueError):
        return True
    return {
        PLANNING_PROPOSAL_ARTIFACT,
        PLANNING_REPORT_ARTIFACT,
    }.issubset(checkpoint.evidence_refs)


def _write_or_validate_proposal_json(
    run_dir: Path,
    proposal: PlanningProposal,
) -> None:
    path = run_dir / PLANNING_PROPOSAL_ARTIFACT
    payload = proposal.model_dump(mode="json")
    if not path.exists():
        write_redacted_json_once(path, payload)
        return
    try:
        existing = PlanningProposal.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("既有 Planning Proposal 无法验证，拒绝覆盖") from exc
    if existing.model_dump(mode="json") != payload:
        raise ValueError("既有 Planning Proposal 与本轮结果不一致，拒绝覆盖")


def _planning_trace_exists(
    run_dir: Path,
    state: AgentState,
) -> bool:
    try:
        trace = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        return False
    return any(
        item.get("event") == "planning_proposal_created"
        and item.get("run_id") == state.run_id
        and item.get("state_version") == state.state_version
        and PLANNING_PROPOSAL_ARTIFACT
        in (
            item.get("artifact_refs")
            if isinstance(item.get("artifact_refs"), list)
            else []
        )
        for item in trace
    )


def _report(
    event_reporter: Callable[[str], None] | None,
    message: str,
) -> None:
    if event_reporter is not None:
        event_reporter(message)
