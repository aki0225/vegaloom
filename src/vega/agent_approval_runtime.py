from __future__ import annotations

from pathlib import Path

from .agent_approval_policy import evaluate_bounded_approval
from .agent_change_run import (
    CHANGE_CONTRACT_ARTIFACT,
    EXECUTION_PLAN_ARTIFACT,
    load_change_run_context,
)
from .agent_change_runtime import approve_change_run
from .agent_contract import AgentPlan, AgentState
from .agent_mutation import agent_mutation
from .agent_persistence import append_agent_trace
from .agent_run import AgentRun
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    write_status_card,
)
from .approval_policy_config import bounded_approval_policy_digest
from .project_config import load_project_config
from .redaction import redact_text


class BoundedApprovalRuntimeMixin:
    @agent_mutation("agent.approve")
    def approve_bounded(self, run: str) -> AgentRun:
        run_dir, state, plan, metadata = self._load_run(run)
        if state.run_kind != "change":
            raise ValueError("bounded 自动批准只适用于 ChangeRun")
        return approve_bounded_change_run(run_dir, state, plan, metadata)


def approve_bounded_change_run(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
) -> AgentRun:
    """在仓库策略和调用方同时选择时尝试 bounded 自动批准。"""

    if state.phase != "awaiting_approval" or state.active_child_run:
        raise ValueError("当前状态不允许执行 bounded 自动批准")
    context = load_change_run_context(run_dir, state, plan, metadata)
    assert context is not None
    snapshot = capture_bound_workspace(run_dir)
    if (
        snapshot.fingerprint != state.workspace_fingerprint
        or snapshot.head_sha != state.accepted_checkpoint_sha
    ):
        return _reject(run_dir, state, plan, "bounded 批准前 Workspace 已漂移")

    raw_revision = metadata.get("base_revision")
    if not isinstance(raw_revision, str) or not raw_revision:
        return _reject(
            run_dir,
            state,
            plan,
            "ChangeRun 缺少 bounded 策略基线 revision",
        )
    repo = bound_repo(run_dir)
    try:
        source_config = load_project_config(
            repo,
            tracked_only=True,
            tracked_revision=raw_revision,
        )
        current_config = load_project_config(repo)
        if (
            bounded_approval_policy_digest(source_config)
            != bounded_approval_policy_digest(current_config)
        ):
            return _reject(
                run_dir,
                state,
                plan,
                "`.vega.yaml` 在 Planning 或创建 ChangeRun 后发生变化",
            )
        decision = evaluate_bounded_approval(
            repo,
            context.contract,
            context.execution_plan,
            source_config,
            policy_revision=raw_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _reject(run_dir, state, plan, str(exc))
    if (
        not decision.eligible
        or decision.policy_id is None
        or decision.policy_digest is None
    ):
        return _reject(run_dir, state, plan, decision.summary)
    return approve_change_run(
        run_dir,
        state,
        plan,
        metadata,
        actor=f"bounded:{decision.policy_id}",
        approval_source="bounded",
        policy_id=decision.policy_id,
        policy_digest=decision.policy_digest,
        policy_revision=decision.policy_revision,
    )


def _reject(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    reason: str,
) -> AgentRun:
    safe_reason = redact_text(reason.strip())[:2000]
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="bounded_approval_rejected",
        state=state,
        route_reason=safe_reason,
        artifact_refs=[
            CHANGE_CONTRACT_ARTIFACT,
            EXECUTION_PLAN_ARTIFACT,
        ],
    )
    write_status_card(
        run_dir,
        state,
        plan,
        next_step=(
            f"bounded 自动批准未放行：{safe_reason}；"
            "检查 plan-card.md 后使用 vega approve，或修订任务边界"
        ),
    )
    return AgentRun(run_dir=run_dir, state=state, plan=plan)
