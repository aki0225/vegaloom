from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from . import agent_explain_codes as explain_codes
from .agent_core_recheck import core_recheck_available
from .agent_contract import AgentCheckpoint, AgentDecision, AgentPhase, AgentPlan, AgentState
from .agent_planning_handoff import can_offer_handoff
from .agent_provider_explain import with_provider_warnings
from .agent_status_projection import AgentStatusProjection, build_agent_status_projection
from .provider_session import PROVIDER_SESSIONS_ARTIFACT


ExplanationOutcome = Literal["in_progress", "ready", "attention_required", "completed", "stopped", "unknown"]
ExplanationSource = Literal["evidence", "provider", "runtime", "phase", "decision", "checkpoint", "legacy"]


class AgentExplanation(BaseModel):
    """供文本与 JSON 共用的只读解释投影。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    phase: AgentPhase
    outcome: ExplanationOutcome
    reason_code: str
    block_category: explain_codes.BlockCategory | None = None
    source: ExplanationSource
    actor: str
    reason: str
    facts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    safe_actions: list[explain_codes.PublicActionId] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def build_agent_explanation(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    status_projection: AgentStatusProjection | None = None,
) -> AgentExplanation:
    """解释当前 ChangeRun，并优先复用调用方已构建的只读投影。

    CLI 传入 ``status_projection`` 后，本函数不会再次构建 status、读取
    Workspace、Provider Session 或 Checkpoint。直接 Python 调用也沿同一投影
    采集证据，不再维护另一套独立读取路径。
    """

    inputs = _load_explanation_inputs(
        run_dir, state, plan, status_projection=status_projection
    )
    if inputs is None:
        return _unverified_projection_explanation(state)
    status = inputs.payload
    sessions = inputs.provider_interactions
    provider_warnings = inputs.provider_warnings
    checkpoint = inputs.checkpoint
    decision = inputs.decision
    decision_issue = inputs.decision_issue

    effective_phase = cast(AgentPhase, status["effective_phase"])
    if (
        status.get("integrity_warning")
        or effective_phase != status.get("recorded_phase")
    ):
        workspace_current = status.get("workspace_current")
        reason_code = (
            "workspace.snapshot_stale"
            if workspace_current is False
            else "evidence.integrity_unverified"
        )
        return _explanation(
            state,
            phase="needs_human",
            outcome="attention_required",
            reason_code=reason_code,
            source="evidence",
            actor="当前证据投影",
            reason=str(
                status.get("integrity_warning")
                or "当前证据无法支持持久化状态。"
            ),
            facts=[
                f"持久化阶段为 {status.get('recorded_phase')}",
                f"当前有效阶段为 {effective_phase}",
                f"证据健康状态为 {status.get('evidence_health')}",
            ],
            unknowns=["原状态是否仍能由当前 Workspace 和 Artifact 重新证明"],
            safe_actions=_safe_actions(state, status, fallback=["human"]),
            evidence_refs=_base_refs(state),
        )

    if decision_issue is not None:
        return with_provider_warnings(_explanation(
            state,
            phase="needs_human",
            outcome="attention_required",
            reason_code="evidence.decision_unverified",
            source="evidence",
            actor="当前证据投影",
            reason=decision_issue,
            facts=[f"持久化阶段为 {state.phase}"],
            unknowns=["最近 Checkpoint 的路由依据是否完整且绑定正确"],
            safe_actions=["status_full", "inspect_artifacts", "human"],
            evidence_refs=_base_refs(state),
        ), provider_warnings)

    if sessions:
        first = sessions[0]
        return with_provider_warnings(_explanation(
            state,
            phase=state.phase,
            outcome="attention_required",
            reason_code="provider.interaction_required",
            source="provider",
            actor="Provider Session",
            reason=(
                first.summary
                if len(sessions) == 1
                else f"存在 {len(sessions)} 个待响应 Provider 请求；最早请求：{first.summary}"
            ),
            facts=[
                f"请求 ID 为 {first.interaction_id}",
                f"请求角色为 {first.role_key}",
                f"请求类型为 {first.method}",
                f"请求状态为 {first.status}",
            ],
            unknowns=["该请求尚未由人工接受或拒绝"],
            safe_actions=(explain_codes.provider_interaction_actions(first.method)
                          if state.execution_protocol == 2 else ["status", "stop"]),
            evidence_refs=[PROVIDER_SESSIONS_ARTIFACT],
        ), provider_warnings)

    active = _active_execution_explanation(state, status)
    if active is not None:
        return with_provider_warnings(active, provider_warnings)

    phase = _phase_explanation(run_dir, state, plan, status, checkpoint, decision)
    if phase is not None:
        return with_provider_warnings(phase, provider_warnings)
    if decision is not None and checkpoint is not None:
        return with_provider_warnings(
            _decision_explanation(state, status, checkpoint, decision),
            provider_warnings,
        )
    if checkpoint is not None:
        return with_provider_warnings(
            _checkpoint_explanation(state, status, checkpoint),
            provider_warnings,
        )
    fallback = _explanation(
        state,
        phase=state.phase,
        outcome="unknown",
        reason_code="legacy.fallback",
        source="legacy",
        actor="兼容投影",
        reason=str(status.get("next_step") or "当前运行缺少更具体的解释材料。"),
        facts=[f"当前阶段为 {state.phase}"],
        unknowns=["旧版本运行没有稳定 reason_code"],
        safe_actions=_safe_actions(state, status, fallback=["status_full"]),
        evidence_refs=_base_refs(state),
    )
    return with_provider_warnings(fallback, provider_warnings)


def _load_explanation_inputs(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    status_projection: AgentStatusProjection | None,
) -> AgentStatusProjection | None:
    if status_projection is not None:
        if status_projection.state != state or status_projection.plan != plan:
            raise ValueError("Explain 投影与 Agent State/Plan 身份不一致。")
        return status_projection
    try:
        return build_agent_status_projection(run_dir, state, plan)
    except (OSError, RuntimeError, ValueError):
        return None


def _unverified_projection_explanation(state: AgentState) -> AgentExplanation:
    return _explanation(
        state,
        phase="needs_human",
        outcome="attention_required",
        reason_code="evidence.status_projection_unverified",
        source="evidence",
        actor="当前证据投影",
        reason="当前状态证据无法安全投影。",
        facts=[f"持久化阶段为 {state.phase}"],
        unknowns=["当前 Workspace 与运行 Artifact 是否仍属于同一可信现场"],
        safe_actions=["status_full", "inspect_artifacts", "human"],
        evidence_refs=_base_refs(state),
    )


def _active_execution_explanation(
    state: AgentState, status: dict[str, object]
) -> AgentExplanation | None:
    if status.get("active_operation_kind") == "environment_prepare":
        code = "environment.preparation_active"
        reason = "正在执行已批准的环境准备命令；尚未启动 Coding Worker。"
        safe_actions = ["status", "stop"]
    elif state.active_planning_execution_id is not None:
        code = "planning.execution_active"
        reason = "只读调查仍在运行。"
        safe_actions = ["status", "stop"]
    elif state.phase == "acting":
        code = "execution.worker_active"
        reason = "Worker 正在执行当前 Work Item。"
        safe_actions = ["status", "steer", "stop"]
    elif state.phase == "observing":
        code = "execution.observation_active"
        reason = "Vega 正在对账 Candidate 与 Core 证据。"
        safe_actions = ["status", "stop"]
    elif state.phase == "finalizing" and state.execution_protocol == 2:
        code = "execution.finalization_active"
        reason = "Vega 正在采用可信 Core Finish 生成最终结论。"
        safe_actions = ["run", "stop"]
    else:
        return None
    if state.execution_protocol != 2:
        safe_actions = ["status", "stop"]
    facts = [f"当前阶段为 {state.phase}"]
    if state.current_work_item:
        facts.append(f"当前 Work Item 为 {state.current_work_item}")
    if state.active_child_run:
        facts.append(f"当前 child 为 {state.active_child_run}")
    return _explanation(
        state,
        phase=state.phase,
        outcome="in_progress",
        reason_code=code,
        source="runtime",
        actor="Vega Runtime",
        reason=reason,
        facts=facts,
        safe_actions=safe_actions,
        evidence_refs=_base_refs(state),
    )


def _phase_explanation(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    status: dict[str, object],
    checkpoint: AgentCheckpoint | None,
    decision: AgentDecision | None,
) -> AgentExplanation | None:
    if state.execution_protocol != 2 and not (
        state.active_child_run or state.active_operation_id or state.active_planning_execution_id
    ):
        actions = ["status_full", "inspect_artifacts"]
        actions.extend(["handoff"] if can_offer_handoff(run_dir, state, plan, checkpoint) else [])
        actions.append("stop")
        return _explanation(
            state, phase=state.phase, outcome="attention_required",
            reason_code="legacy.execution_protocol", source="legacy", actor="执行协议边界",
            reason="旧执行协议仅保留状态、证据和交接；请从 Task Card 创建新运行。",
            safe_actions=actions, evidence_refs=_checkpoint_refs(state, checkpoint),
        )

    if state.phase == "awaiting_approval":
        return _explanation(
            state,
            phase=state.phase,
            outcome="attention_required",
            reason_code="approval.contract_required",
            source="phase",
            actor="批准策略",
            reason="当前 Change Contract 尚未获得有效批准。",
            facts=[f"Plan revision 为 {state.plan_revision}"],
            unknowns=["人工是否接受当前目标、范围、验证和风险边界"],
            safe_actions=["approve", "revise", "stop"],
            evidence_refs=[*_base_refs(state), "agent-plan.json"],
        )
    if state.phase == "completed":
        terminal = state.terminal_status or "unknown"
        return _explanation(
            state,
            phase=state.phase,
            outcome="completed",
            reason_code=f"run.completed.{terminal}",
            source="phase",
            actor="Vega Runtime",
            reason=f"ChangeRun 已完成，Core Finish 结论为 {terminal}。",
            facts=[f"终态为 {terminal}", f"建议提交为 {bool(status.get('commit_recommended'))}"],
            safe_actions=["status_full", "inspect_diff"],
            evidence_refs=_base_refs(state),
        )
    if state.phase == "stopped":
        reason = (
            checkpoint.reason
            if checkpoint is not None
            else "当前 ChangeRun 已停止，现场保持不变。"
        )
        facts = ["代码、计划和运行现场未自动回滚"]
        if checkpoint is not None:
            facts.extend(
                [
                    f"Checkpoint 状态为 {checkpoint.status}",
                    f"外部副作用为 {checkpoint.external_side_effects}",
                ]
            )
        safe_actions = ["status_full"]
        if can_offer_handoff(run_dir, state, plan, checkpoint):
            safe_actions.append("handoff")
        safe_actions.append("change")
        return _explanation(
            state,
            phase=state.phase,
            outcome="stopped",
            reason_code="run.stopped",
            source="checkpoint" if checkpoint is not None else "phase",
            actor="运行 Checkpoint" if checkpoint is not None else "Vega Runtime",
            reason=reason,
            facts=facts,
            safe_actions=safe_actions,
            evidence_refs=_checkpoint_refs(state, checkpoint),
        )
    if state.phase == "needs_human" and core_recheck_available(run_dir.parent.parent, run_dir.name):
        return _explanation(
            state, phase=state.phase, outcome="ready", source="evidence",
            reason_code="evidence.core_recheck_available", actor="Core 证据对账",
            reason="当前 Candidate 的已有验证证据可以重新核对；不会启动 Worker 或重跑测试。",
            safe_actions=["run.continue", "status_full", "stop"],
            evidence_refs=_checkpoint_refs(state, checkpoint),
        )
    if state.phase == "planning" and state.contract_revision is not None:
        return _explanation(
            state, phase=state.phase, outcome="attention_required",
            reason_code="workflow.replan_required", source="phase", actor="合同修订门禁",
            reason="当前合同需要重新规划；请修订合同和计划，保留原 Candidate 与批准记录。",
            safe_actions=["revise", "status_full", "stop"],
            evidence_refs=_checkpoint_refs(state, checkpoint),
        )
    if state.phase == "planning" and decision is None:
        return _explanation(
            state,
            phase=state.phase,
            outcome="in_progress",
            reason_code="planning.required",
            source="phase",
            actor="Vega Runtime",
            reason=(
                checkpoint.reason
                if checkpoint is not None
                else "当前任务仍需完成只读调查和合同编译。"
            ),
            facts=[f"Plan revision 为 {state.plan_revision}"],
            safe_actions=["run", "stop"],
            evidence_refs=_checkpoint_refs(state, checkpoint),
        )
    return None


def _decision_explanation(
    state: AgentState,
    status: dict[str, object],
    checkpoint: AgentCheckpoint,
    decision: AgentDecision,
) -> AgentExplanation:
    reason_code = decision.reason_code or "decision.legacy"
    category = explain_codes.block_category_for_reason_code(reason_code)
    outcome: ExplanationOutcome = (
        "attention_required"
        if decision.selected_action in {"human", "replan"}
        else "ready"
    )
    unknowns: list[str] = []
    if category == "authorization":
        unknowns.append("所需人工授权尚未取得")
    elif category == "evidence":
        unknowns.append("当前证据不足以安全自动继续")
    elif category == "budget":
        unknowns.append("自动执行预算已经耗尽")
    elif category == "transient":
        unknowns.append("临时故障是否可安全恢复尚未确认")
    elif category == "configuration":
        unknowns.append("当前配置不足以继续")
    if decision.reason_code is None:
        unknowns.append("旧版本 Decision 没有稳定 reason_code")
    return _explanation(
        state,
        phase=state.phase,
        outcome=outcome,
        reason_code=reason_code,
        source="decision",
        actor={
            "deterministic": "确定性规则",
            "supervisor": "Supervisor",
            "human": "人工",
        }[decision.source],
        reason=decision.reason,
        facts=[
            f"选择动作为 {decision.selected_action}",
            f"允许动作为 {', '.join(decision.allowed_actions)}",
            f"绑定 Checkpoint 为 {checkpoint.checkpoint_id}",
        ],
        unknowns=unknowns,
        safe_actions=_safe_actions(state, status, fallback=list(decision.allowed_actions)),
        evidence_refs=_checkpoint_refs(state, checkpoint),
    )


def _checkpoint_explanation(
    state: AgentState, status: dict[str, object], checkpoint: AgentCheckpoint
) -> AgentExplanation:
    outcome: ExplanationOutcome = (
        "attention_required"
        if state.phase == "needs_human"
        else "ready"
        if state.phase == "ready"
        else "in_progress"
    )
    return _explanation(
        state,
        phase=state.phase,
        outcome=outcome,
        reason_code=f"checkpoint.{state.phase}",
        source="checkpoint",
        actor="运行 Checkpoint",
        reason=checkpoint.reason,
        facts=[
            f"Checkpoint 状态为 {checkpoint.status}",
            f"外部副作用为 {checkpoint.external_side_effects}",
        ],
        safe_actions=_safe_actions(state, status, fallback=list(checkpoint.pending_actions)),
        evidence_refs=_checkpoint_refs(state, checkpoint),
    )


def _safe_actions(state: AgentState, status: dict[str, object], *, fallback: list[str]) -> list[str]:
    actions = status.get("allowed_actions")
    valid = actions if isinstance(actions, list) and all(isinstance(item, str) for item in actions) else []
    replan = (
        "run.continue"
        if state.phase == "planning" and state.contract_revision is None
        else "plan.revise"
    )
    return explain_codes.public_action_ids(valid, fallback=fallback, replan_action=replan)


def _base_refs(state: AgentState) -> list[str]:
    refs = ["agent-state.json"]
    if state.latest_checkpoint_id is not None:
        refs.append(f"checkpoints/{state.latest_checkpoint_id}.json")
    return refs


def _checkpoint_refs(state: AgentState, checkpoint: AgentCheckpoint | None) -> list[str]:
    refs = _base_refs(state)
    if checkpoint is not None:
        refs.extend(checkpoint.evidence_refs)
    return list(dict.fromkeys(refs))


def _explanation(
    state: AgentState,
    *,
    phase: AgentPhase,
    outcome: ExplanationOutcome,
    reason_code: str,
    source: ExplanationSource,
    actor: str,
    reason: str,
    facts: list[str] | None = None,
    unknowns: list[str] | None = None,
    safe_actions: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> AgentExplanation:
    return AgentExplanation(
        run_id=state.run_id,
        phase=phase,
        outcome=outcome,
        reason_code=reason_code,
        block_category=explain_codes.block_category_for_reason_code(reason_code),
        source=source,
        actor=actor,
        reason=reason,
        facts=list(dict.fromkeys(facts or [])),
        unknowns=list(dict.fromkeys(unknowns or [])),
        safe_actions=explain_codes.public_action_ids(safe_actions or []),
        evidence_refs=list(dict.fromkeys(evidence_refs or [])),
    )
