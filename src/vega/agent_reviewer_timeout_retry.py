from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from .agent_change_control import (
    ChangeBudgetSnapshot,
    require_change_verification_retry_budget,
)
from .agent_change_run import load_candidate_artifact, load_change_run_context
from .agent_contract import (
    AgentDecision,
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
)
from .agent_git_candidate import CandidateCommit, validate_candidate_binding
from .agent_operation import child_summary_ref
from .agent_persistence import load_agent_checkpoint
from .agent_run import AgentRun
from .agent_run_status import latest_worker_dispatch_binding
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
)
from .agent_status_evidence import build_supervisor_evidence
from .agent_verification_retry_archive import reviewer_timeout_finish_is_valid
from .agent_verification_retry_evidence import load_bound_source_finish
from .agent_worker_evidence import (
    WorkerClaim,
    load_child_state,
    require_child_quiescent,
    require_single_executable_work_item,
)
from .execution_control import find_execution_records
from .loop_evidence import (
    LoopEvidenceValidationSnapshot,
    validate_loop_evidence_snapshot,
)
from .models import LoopAutomationState
from .run_utils import resolve_run_dir
from .verification_command_preflight import require_verification_commands_preflight
from .workspace_snapshot import ReviewWorkspaceSnapshot

if TYPE_CHECKING:
    from .agent_provider_adapter import SupervisorAgentProviderAdapter


@dataclass(frozen=True)
class ReviewerTimeoutRetrySource:
    run_dir: Path
    state: AgentState
    plan: AgentPlan
    metadata: dict[str, object]
    work_item: AgentWorkItem
    repo: Path
    before: ReviewWorkspaceSnapshot
    child_dir: Path
    child_state: LoopAutomationState
    source_observation: AgentObservation
    source_claim: WorkerClaim
    source_summary_ref: str
    source_operation_id: str
    source_finish_sha256: str
    candidate_sha: str
    candidate_ref: str
    reviewer_role_key: str


def auto_retry_reviewer_timeout(
    adapter: SupervisorAgentProviderAdapter,
    result: AgentRun,
) -> AgentRun:
    """让既有恢复 Runtime 在持锁状态下判断并执行一次 Reviewer 重试。"""

    if (
        result.state.run_kind != "change"
        or result.state.phase != "needs_human"
        or result.state.active_candidate_sha is None
    ):
        return result
    # 延迟导入避免 Provider Adapter 与验证恢复 Runtime 形成模块循环。
    from .agent_verification_retry import SupervisorAgentVerificationRetry

    retried = SupervisorAgentVerificationRetry(
        adapter.workspace,
        loop_runtime=adapter.loop_runtime,
        finish_runtime=adapter.finish_runtime,
        progress_reporter=adapter.progress_reporter,
        event_reporter=adapter.event_reporter,
        provider=adapter.provider,
        persistent_sessions=adapter.persistent_sessions,
    ).run_reviewer_timeout_if_eligible(result.run_dir.name)
    return retried or result


def prepare_reviewer_timeout_source(
    workspace: Path,
    run: str,
) -> ReviewerTimeoutRetrySource:
    """验证 Core Reviewer timeout 现场，不运行命令也不修改状态。"""

    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    if (
        state.run_kind != "change"
        or state.phase != "needs_human"
        or state.active_child_run
        or state.active_operation_id
        or state.operation_started
        or state.active_candidate_sha is None
    ):
        raise ValueError("当前状态不是可恢复的 Reviewer timeout")
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        raise ValueError("Reviewer timeout 自动恢复只支持 ChangeRun")
    if any(
        context.contract.side_effect_policy.model_dump(
            mode="json",
            exclude={"schema_version"},
        ).values()
    ):
        raise ValueError("当前合同声明了高影响副作用，禁止自动恢复 Reviewer")
    budget = require_change_verification_retry_budget(
        run_dir,
        state,
        plan,
        metadata,
    )
    _require_review_budget(budget)
    work_item = require_single_executable_work_item(plan, state)
    if work_item.external_side_effects != "none":
        raise ValueError("Reviewer timeout 自动恢复只接受无外部副作用的 Work Item")
    repo = bound_repo(run_dir)
    require_verification_commands_preflight(repo, work_item.verification)
    before = capture_bound_workspace(run_dir)
    if before.fingerprint != state.workspace_fingerprint:
        raise ValueError("Reviewer timeout 恢复前 Workspace 已漂移")
    checkpoint = _reviewer_timeout_checkpoint(run_dir, state)
    child_dir = resolve_run_dir(workspace, checkpoint.failed_attempts[0])
    child_state = load_child_state(child_dir, repo)
    require_child_quiescent(child_dir)
    latest = child_state.iterations[-1] if child_state.iterations else None
    if (
        child_state.automation_mode != "assist"
        or child_state.status != "needs_human"
        or child_state.current_step != "timed_out"
        or child_state.current_iteration >= child_state.max_iterations
        or latest is None
        or latest.lifecycle != "completed"
        or latest.reviewer_status != "timed_out"
        or latest.verification_status != "passed"
        or latest.verification_failed_count
        or latest.risk_gate_status != "success"
        or latest.risk_gate_recommendation == "human-review"
        or latest.verdict != "needs_human"
        or not latest.review_run
    ):
        raise ValueError("Core child 不是可自动恢复的 Reviewer timeout")
    _require_review_execution_quiescent(
        workspace,
        child_dir,
        latest.iteration,
        latest.review_run,
    )
    source_operation_id = _source_operation_id(run_dir, state, child_dir.name)
    source_observation = _checkpoint_observation(
        run_dir,
        checkpoint.evidence_refs,
        child_dir.name,
        source_operation_id,
    )
    _require_timeout_decision(
        run_dir,
        checkpoint.evidence_refs,
        source_observation,
    )
    if (
        source_observation.reviewer_runner_status != "timed_out"
        or source_observation.reviewer_retry_attempt != 0
        or source_observation.verification != "passed"
        or source_observation.risk != "passed"
        or source_observation.review != "blocked"
        or source_observation.external_side_effects != "none"
        or source_observation.worker_alive
        or not source_observation.operation_started
        or not source_observation.workspace_explained
        or source_observation.plan_contradicted
        or source_observation.workspace_fingerprint != before.fingerprint
    ):
        raise ValueError("Reviewer timeout Observation 不满足自动恢复前提")
    candidate, candidate_ref = _bound_timeout_candidate(
        run_dir,
        state.active_candidate_sha,
        source_observation,
    )
    validate_candidate_binding(
        context.worktree,
        candidate=candidate,
        contract=context.contract,
        execution_plan=context.execution_plan,
    )
    if sorted(candidate.changed_files) != sorted(source_observation.changed_files):
        raise ValueError("Reviewer timeout Candidate 与 Observation 文件清单不一致")
    _require_candidate_not_retried(run_dir, candidate.candidate_sha)
    source_summary_ref, source_claim, source_finish_sha256 = (
        _reviewer_timeout_source_artifacts(
            run_dir,
            state,
            plan,
            source_observation,
            child_dir,
            source_operation_id,
        )
    )
    evidence = validate_loop_evidence_snapshot(
        workspace,
        repo,
        child_dir,
        state=child_state,
    )
    if (
        not evidence.artifact_integrity.valid
        or not evidence.evidence_freshness.fresh
        or not _trusted_verification_before_timeout(child_state, evidence)
    ):
        raise ValueError("Reviewer timeout 前没有可复用的可信 Verification 证据")
    return ReviewerTimeoutRetrySource(
        run_dir=run_dir,
        state=state,
        plan=plan,
        metadata=metadata,
        work_item=work_item,
        repo=repo,
        before=before,
        child_dir=child_dir,
        child_state=child_state,
        source_observation=source_observation,
        source_claim=source_claim,
        source_summary_ref=source_summary_ref,
        source_operation_id=source_operation_id,
        source_finish_sha256=source_finish_sha256,
        candidate_sha=candidate.candidate_sha,
        candidate_ref=candidate_ref,
        reviewer_role_key=(
            f"reviewer:{work_item.work_item_id}:"
            f"candidate-{candidate.candidate_sha[:12]}:retry-1"
        ),
    )


def _source_operation_id(
    run_dir: Path,
    state: AgentState,
    child_run: str,
) -> str:
    worker_binding = latest_worker_dispatch_binding(run_dir, state)
    if worker_binding is None or worker_binding[0] != child_run:
        raise ValueError("无法把失败 child 绑定到原始真实 Worker")
    return worker_binding[1]


def _require_review_budget(budget: ChangeBudgetSnapshot | None) -> None:
    if budget is None:
        raise ValueError("Reviewer timeout 自动恢复只支持带预算的 ChangeRun")
    if budget.review_rounds_used >= budget.max_review_rounds:
        raise ValueError(
            "当前 Work Item 的 Review 预算已用完："
            f"{budget.review_rounds_used}/{budget.max_review_rounds}"
        )


def _reviewer_timeout_checkpoint(run_dir: Path, state: AgentState):
    if state.latest_checkpoint_id is None:
        raise ValueError("Reviewer timeout 状态缺少 Checkpoint")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.status != "blocked"
        or checkpoint.phase != "needs_human"
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.external_side_effects != "none"
        or checkpoint.state_version + 1 != state.state_version
        or checkpoint.pending_actions != ["human"]
        or len(checkpoint.failed_attempts) != 1
    ):
        raise ValueError("Reviewer timeout 没有匹配的 blocked Checkpoint")
    return checkpoint


def _checkpoint_observation(
    run_dir: Path,
    refs: list[str],
    child_run: str,
    operation_id: str,
) -> AgentObservation:
    observation_refs = [ref for ref in refs if ref.startswith("observations/")]
    if len(observation_refs) != 1:
        raise ValueError("Reviewer timeout Checkpoint 没有唯一 Observation")
    try:
        observation = AgentObservation.model_validate_json(
            (run_dir / observation_refs[0]).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("Reviewer timeout Observation 无法验证") from exc
    if (
        observation_refs[0]
        != f"observations/{observation.observation_id}.json"
        or observation.child_run != child_run
        or observation.operation_id != operation_id
    ):
        raise ValueError("Reviewer timeout Observation 身份不一致")
    return observation


def _require_timeout_decision(
    run_dir: Path,
    refs: list[str],
    observation: AgentObservation,
) -> None:
    decision_refs = [ref for ref in refs if ref.startswith("decisions/")]
    if len(decision_refs) != 1:
        raise ValueError("Reviewer timeout Checkpoint 没有唯一 Decision")
    try:
        decision = AgentDecision.model_validate_json(
            (run_dir / decision_refs[0]).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("Reviewer timeout Decision 无法验证") from exc
    if (
        decision_refs[0] != f"decisions/{decision.decision_id}.json"
        or decision.observation_id != observation.observation_id
        or decision.selected_action != "human"
        or decision.reason_code != "review.runner_timed_out"
    ):
        raise ValueError("Reviewer timeout Decision 不允许自动恢复")


def _bound_timeout_candidate(
    run_dir: Path,
    candidate_sha: str,
    observation: AgentObservation,
) -> tuple[CandidateCommit, str]:
    refs = [ref for ref in observation.evidence_refs if ref.startswith("candidates/")]
    if len(refs) != 1:
        raise ValueError("Reviewer timeout Observation 没有唯一 Candidate")
    candidate = load_candidate_artifact(run_dir, refs[0])
    if (
        candidate.candidate_sha != candidate_sha
        or candidate.operation_id != observation.operation_id
        or candidate.work_item_id != observation.work_item_id
    ):
        raise ValueError("Reviewer timeout Candidate 身份不一致")
    return candidate, refs[0]


def _require_candidate_not_retried(run_dir: Path, candidate_sha: str) -> None:
    for path in (run_dir / "operations").glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Reviewer timeout 历史 operation 无法验证") from exc
        if (
            isinstance(payload, dict)
            and payload.get("operation_kind") == "verification_retry"
            and payload.get("retry_reason") == "reviewer_timeout"
            and payload.get("candidate_sha") == candidate_sha
        ):
            raise ValueError("同一 Candidate 已自动恢复过一次 Reviewer timeout")


def _reviewer_timeout_source_artifacts(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    observation: AgentObservation,
    child_dir: Path,
    operation_id: str,
) -> tuple[str, WorkerClaim, str]:
    evidence = build_supervisor_evidence(run_dir, state, observation, plan)
    if len(evidence) < 3 or any(item.status != "passed" for item in evidence[:3]):
        raise ValueError("原始 Worker 或 Plan Scope 证据无法重新验证")
    summary_ref = child_summary_ref(child_dir.name, operation_id)
    try:
        summary = json.loads((run_dir / summary_ref).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Reviewer timeout child 摘要无法读取") from exc
    worker = summary.get("worker") if isinstance(summary, dict) else None
    claim_payload = worker.get("claim") if isinstance(worker, dict) else None
    try:
        claim = WorkerClaim.model_validate(claim_payload)
    except ValidationError as exc:
        raise ValueError("Reviewer timeout 原始 Worker Claim 无法验证") from exc
    if (
        summary.get("operation_kind") == "verification_retry"
        or claim.claimed_status != "completed"
    ):
        raise ValueError("Reviewer timeout child 摘要身份不一致")
    finish, finish_sha256 = load_bound_source_finish(summary, child_dir)
    if not reviewer_timeout_finish_is_valid(finish):
        raise ValueError("Reviewer timeout Finish 不满足自动恢复前提")
    return summary_ref, claim, finish_sha256


def _require_review_execution_quiescent(
    workspace: Path,
    child_dir: Path,
    iteration: int,
    review_run: str,
) -> None:
    review_dir = resolve_run_dir(workspace, review_run)
    try:
        state = json.loads((review_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Reviewer timeout review state 无法读取") from exc
    records = [
        record
        for record in find_execution_records(child_dir)
        if record.lease.step == "reviewer"
        and record.lease.iteration == iteration
    ]
    if (
        not isinstance(state, dict)
        or state.get("status") != "needs_human"
        or state.get("current_step") != "timed_out"
        or state.get("runner_status") != "timed_out"
        or state.get("verdict") != "needs_human"
        or not records
        or not any(record.lease.status == "timed_out" for record in records)
        or any(record.lease.termination_unconfirmed for record in records)
    ):
        raise ValueError("Reviewer timeout 执行终态不可信或终止未确认")


def _trusted_verification_before_timeout(
    state: LoopAutomationState,
    evidence: LoopEvidenceValidationSnapshot,
) -> bool:
    """Reviewer 未成功时，仍要求 Verification 与其前置 Reflect 绑定同一现场。"""

    if not state.iterations or state.verification_artifact_version != 2:
        return False
    latest = state.iterations[-1]
    if (
        latest.lifecycle != "completed"
        or latest.verification_status != "passed"
        or latest.verification_failed_count
        or latest.verification_failure_kind is not None
    ):
        return False
    matches = [
        payload
        for payload in evidence.artifact_integrity.verification_results
        if payload.get("iteration") == latest.iteration
    ]
    if len(matches) != 1:
        return False
    payload = matches[0]
    return bool(
        payload.get("artifact_version") == 2
        and payload.get("failure_kind") is None
        and payload.get("failed_count") == 0
        and payload.get("interruption_status") is None
        and payload.get("workspace_fingerprint")
        == evidence.evidence_freshness.trusted_workspace_fingerprint
    )
