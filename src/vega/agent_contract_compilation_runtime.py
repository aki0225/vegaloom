from __future__ import annotations

import hashlib
from pathlib import Path

from .agent_change_run import (
    CHANGE_CONTRACT_ARTIFACT,
    EXECUTION_PLAN_ARTIFACT,
    project_agent_plan,
    save_change_run_artifacts,
)
from .agent_contract import AgentPlan, AgentState
from .agent_contract_compiler import (
    PLAN_CARD_ARTIFACT,
    CompiledPlanningContract,
    compile_planning_proposal,
    render_plan_card,
)
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_planning import (
    PLANNING_CONTEXT_ARTIFACT,
    PLANNING_PROPOSAL_ARTIFACT,
    PLANNING_REPORT_ARTIFACT,
    PLANNING_REQUEST_ARTIFACT,
    PlanningProposal,
    PlanningRequest,
    validate_published_planning_proposal,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    save_agent_plan,
    write_checkpoint,
    write_status_card,
)
from .project_config import load_project_config
from .redaction import redact_text, write_redacted_text


def compile_planning_run(workspace: Path, run: str) -> AgentRun:
    """把已发布 Proposal 编译进同一条未批准 ChangeRun。"""

    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    if (
        state.run_kind != "change"
        or state.contract_revision is not None
        or state.phase != "planning"
        or state.active_planning_execution_id is not None
    ):
        raise ValueError("当前状态不能执行 Contract Compiler")
    repo = bound_repo(run_dir)
    snapshot = capture_bound_workspace(run_dir)
    if (
        snapshot.fingerprint != state.workspace_fingerprint
        or snapshot.head_sha != state.accepted_checkpoint_sha
    ):
        return _publish_compilation_failure(
            run_dir,
            state,
            plan,
            snapshot,
            "workspace：Contract Compiler 启动前 Workspace 已漂移",
        )
    try:
        request = _load_request(run_dir, state)
        _validate_context_digest(run_dir, request)
        if metadata.get("base_revision") != request.source_revision:
            raise ValueError("source_revision：run 基线与 Planning Request 不一致")
        proposal = validate_published_planning_proposal(
            run_dir,
            repo,
            state,
            plan,
            request,
        )
        config = load_project_config(
            repo,
            tracked_only=True,
            tracked_revision=request.source_revision,
        )
        compiled = compile_planning_proposal(repo, proposal, config)
    except (OSError, RuntimeError, ValueError) as exc:
        return _publish_compilation_failure(
            run_dir,
            state,
            plan,
            snapshot,
            str(exc),
        )
    return _publish_compiled_contract(
        run_dir,
        state,
        snapshot,
        proposal,
        compiled,
    )


def _load_request(run_dir: Path, state: AgentState) -> PlanningRequest:
    try:
        request = PlanningRequest.model_validate_json(
            (run_dir / PLANNING_REQUEST_ARTIFACT).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("planning_request：无法读取") from exc
    if request.task_id != state.task_id:
        raise ValueError("planning_request.task_id：与 Agent State 不一致")
    return request


def _validate_context_digest(run_dir: Path, request: PlanningRequest) -> None:
    try:
        content = (run_dir / PLANNING_CONTEXT_ARTIFACT).read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise ValueError("project_context：无法读取") from exc
    if (
        hashlib.sha256(content.encode("utf-8")).hexdigest()
        != request.project_context_sha256
    ):
        raise ValueError("project_context：内容摘要与 Planning Request 不一致")


def _publish_compiled_contract(
    run_dir: Path,
    state: AgentState,
    snapshot,
    proposal: PlanningProposal,
    compiled: CompiledPlanningContract,
) -> AgentRun:
    projected = project_agent_plan(
        compiled.contract,
        compiled.execution_plan,
    )
    next_state = update_state(
        state,
        phase="awaiting_approval",
        state_version=state.state_version + 1,
        goal_revision=compiled.contract.contract_revision,
        plan_revision=compiled.execution_plan.plan_revision,
        contract_revision=compiled.contract.contract_revision,
        execution_plan_revision=compiled.execution_plan.plan_revision,
        current_work_item=compiled.execution_plan.work_items[0].work_item_id,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["replan", "human"],
    )
    save_change_run_artifacts(
        run_dir,
        compiled.contract,
        compiled.execution_plan,
    )
    write_redacted_text(
        run_dir / PLAN_CARD_ARTIFACT,
        render_plan_card(proposal, compiled),
    )
    save_agent_plan(run_dir, projected)
    checkpoint = write_checkpoint(
        run_dir,
        next_state,
        snapshot,
        reason="Contract Compiler 已生成未批准合同，等待人工检查",
        status="blocked",
        pending_actions=["replan", "human"],
        evidence_refs=[
            PLANNING_PROPOSAL_ARTIFACT,
            PLANNING_REPORT_ARTIFACT,
            CHANGE_CONTRACT_ARTIFACT,
            EXECUTION_PLAN_ARTIFACT,
            PLAN_CARD_ARTIFACT,
        ],
        allow_work_item_advance=True,
    )
    next_state = update_state(
        next_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=next_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", next_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="contract_compilation_completed",
        state=next_state,
        observation_summary=(
            f"{len(compiled.execution_plan.work_items)} 个 Work Item，"
            f"{len(compiled.contract.authority_envelope.allowed_paths)} 个允许文件"
        ),
        artifact_refs=[
            CHANGE_CONTRACT_ARTIFACT,
            EXECUTION_PLAN_ARTIFACT,
            PLAN_CARD_ARTIFACT,
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ],
    )
    write_status_card(
        run_dir,
        next_state,
        projected,
        checkpoint=checkpoint,
        next_step="读取 plan-card.md，确认后运行 vega approve",
    )
    return AgentRun(run_dir=run_dir, state=next_state, plan=projected)


def _publish_compilation_failure(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    snapshot,
    reason: str,
) -> AgentRun:
    safe_reason = redact_text(reason.strip())[:2000]
    blocked_state = update_state(
        state,
        phase="needs_human",
        state_version=state.state_version + 1,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        blocked_state,
        snapshot,
        reason=f"Contract Compiler 拒绝：{safe_reason}",
        status="blocked",
        pending_actions=["human"],
        evidence_refs=[
            PLANNING_PROPOSAL_ARTIFACT,
            PLANNING_REPORT_ARTIFACT,
        ],
        operation_started=False,
        external_side_effects="none",
    )
    blocked_state = update_state(
        blocked_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=blocked_state.state_version + 1,
    )
    save_agent_state(run_dir / "agent-state.json", blocked_state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="contract_compilation_rejected",
        state=blocked_state,
        route_reason=safe_reason,
        artifact_refs=[f"checkpoints/{checkpoint.checkpoint_id}.json"],
    )
    write_status_card(
        run_dir,
        blocked_state,
        plan,
        checkpoint=checkpoint,
        next_step=(
            f"Contract Compiler 拒绝：{safe_reason}；"
            "修订仓库配置或重新生成 Planning Proposal 后再开始新的 ChangeRun"
        ),
    )
    return AgentRun(run_dir=run_dir, state=blocked_state, plan=plan)
