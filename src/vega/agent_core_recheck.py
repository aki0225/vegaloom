from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_contract import AgentObservation, AgentState
    from .agent_verification_retry_evidence import PreparedVerificationRetry
    from .models import LoopAutomationState


def core_recheck_available(workspace: Path, run: str) -> bool:
    """展示与执行共用完整只读资格，不把缺失证据解释为可继续。"""

    try:
        prepare_core_recheck(workspace, run)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return False
    return True


def reuse_core_integration_review(
    prepared: PreparedVerificationRetry, observation: AgentObservation,
) -> AgentObservation:
    from .agent_worker_evidence import hash_evidence_refs

    source = prepared.source_observation
    if not any(ref.startswith("integration-reviews/") for ref in source.evidence_refs):
        return observation
    assert prepared.candidate_sha is not None
    ref = require_existing_integration_review(prepared.run_dir, source, prepared.candidate_sha)
    refs = [*observation.evidence_refs, ref]
    return observation.model_copy(update={
        "evidence_refs": refs, "evidence_sha256": hash_evidence_refs(prepared.run_dir, refs),
    })


def prepare_core_recheck(workspace: Path, run: str) -> PreparedVerificationRetry:
    # 展示模块也使用本入口，延迟加载执行依赖，避免 CLI 与 Runtime 相互导入。
    from .agent_change_control import change_budget_snapshot, requires_final_integration_review
    from .agent_change_run import load_candidate_artifact, load_change_run_context
    from .agent_core_observation import finish_evidence_untrusted
    from .agent_git_candidate import validate_candidate_binding
    from .agent_operation import child_summary_ref
    from .agent_runtime_support import bound_repo, capture_bound_workspace, load_agent_bundle
    from .agent_status_evidence import build_supervisor_evidence
    from .agent_verification_retry_evidence import load_bound_source_finish
    from .agent_verification_retry_preparation import (
        build_prepared_verification_retry, reactivate_current_work_item,
    )
    from .agent_worker_evidence import (
        WorkerClaim,
        require_child_quiescent, require_executable_work_item,
    )
    from .run_utils import resolve_run_dir

    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    _require_recheck_state(state)
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None or any(context.contract.side_effect_policy.model_dump(
        mode="json", exclude={"schema_version"},
    ).values()):
        raise ValueError("核心证据重算不接受外部副作用")
    observation = _load_recheck_observation(run_dir, state)
    candidate_refs = [ref for ref in observation.evidence_refs if ref.startswith("candidates/")]
    if len(candidate_refs) != 1:
        raise ValueError("核心证据重算缺少唯一 Candidate")
    candidate = load_candidate_artifact(run_dir, candidate_refs[0])
    if candidate.candidate_sha != state.active_candidate_sha:
        raise ValueError("核心证据重算 Candidate 已变化")
    validate_candidate_binding(
        context.worktree, candidate=candidate, contract=context.contract,
        execution_plan=context.execution_plan,
    )
    _require_not_rechecked(run_dir, candidate.candidate_sha)
    repo = bound_repo(run_dir)
    before = capture_bound_workspace(run_dir)
    if (before.fingerprint != state.workspace_fingerprint
            or before.fingerprint != observation.workspace_fingerprint):
        raise ValueError("核心证据重算前 Workspace 已漂移")
    source_evidence = build_supervisor_evidence(run_dir, state, observation, plan)
    if len(source_evidence) != 4 or any(item.status != "passed" for item in source_evidence[:3]):
        raise ValueError("原始 Worker 或范围证据不可复用")
    child_dir = resolve_run_dir(workspace, observation.child_run)
    require_child_quiescent(run_dir)
    child_state, finish = _fresh_core_finish(workspace, repo, child_dir)
    summary_ref = child_summary_ref(child_dir.name, observation.operation_id)
    source = _read_ref(run_dir, summary_ref)
    claim = WorkerClaim.model_validate(source.get("worker", {}).get("claim"))
    old_finish, finish_sha = load_bound_source_finish(source, child_dir)
    if (source.get("operation_kind") == "verification_retry"
            or claim.claimed_status != "completed" or not finish_evidence_untrusted(old_finish)):
        raise ValueError("核心证据重算只接受已完成 Worker 的原始不可信 Finish")
    _require_same_core_artifacts(old_finish, finish)
    last_item = all(item.work_item_id == state.current_work_item
                    or item.status in {"completed", "superseded"} for item in plan.work_items)
    if (finish.get("finish_status") == "ready_to_commit" and last_item
            and requires_final_integration_review(
                context,
                attempt_number=change_budget_snapshot(run_dir, state, context.contract).worker_attempts_used,
            )):
        require_existing_integration_review(run_dir, observation, candidate.candidate_sha)
    active_plan = reactivate_current_work_item(plan, state.current_work_item)
    work_item = require_executable_work_item(active_plan, state)
    if work_item.external_side_effects != "none":
        raise ValueError("核心证据重算不接受外部副作用")
    return build_prepared_verification_retry(
        workspace, run_dir=run_dir, state=state, plan=active_plan, metadata=metadata,
        work_item=work_item, repo=repo, before=before, child_dir=child_dir,
        child_state=child_state, source_plan=plan, source_observation=observation,
        source_claim=claim, source_summary_ref=summary_ref,
        source_operation_id=observation.operation_id, source_finish_sha256=finish_sha,
        retry_reason="core_evidence_recheck", candidate_sha=candidate.candidate_sha,
        candidate_ref=candidate_refs[0],
    )


def _require_recheck_state(state: AgentState) -> None:
    if (
        state.execution_protocol != 2 or state.run_kind != "change"
        or state.phase != "needs_human" or state.active_child_run
        or state.active_operation_id or state.operation_started
        or state.active_candidate_sha is None or state.latest_checkpoint_id is None
        or state.allowed_actions != ["human"]
    ):
        raise ValueError("当前状态不允许重算核心证据")


def _require_same_core_artifacts(old_finish: dict[str, object], current: dict[str, object]) -> None:
    # 只允许重新判断既有证据的可信度；新 iteration、Review 或验证结果不能免费采用。
    fields = ("loop_status", "iterations", "review_verdicts", "verification_results", "risk_gate_results")
    if any(field not in old_finish or old_finish[field] != current.get(field) for field in fields):
        raise ValueError("原失败 Finish 与当前 Core 的 iteration 或证据内容不一致")


def require_rechecked_finish(
    prepared: PreparedVerificationRetry, operation_id: str, finish: dict[str, object],
) -> None:
    import hashlib

    from .agent_verification_retry_archive import retry_source_finish_ref

    ref = retry_source_finish_ref(operation_id)
    archived = _read_ref(prepared.run_dir, ref)
    if hashlib.sha256((prepared.run_dir / ref).read_bytes()).hexdigest() != prepared.source_finish_sha256:
        raise ValueError("核心重算采用前原始 Finish 归档已变化")
    _require_same_core_artifacts(archived, finish)


def _fresh_core_finish(
    workspace: Path, repo: Path, child_dir: Path,
) -> tuple[LoopAutomationState, dict[str, object]]:
    from .agent_worker_evidence import load_child_state, require_child_quiescent
    from .finish_runtime import build_finish_summary
    from .loop_evidence import validate_loop_evidence_snapshot
    from .run_utils import resolve_run_dir

    require_child_quiescent(child_dir)
    child_state = load_child_state(child_dir, repo)
    for iteration in child_state.iterations:
        if iteration.review_run:
            require_child_quiescent(resolve_run_dir(workspace, iteration.review_run))
    evidence = validate_loop_evidence_snapshot(workspace, repo, child_dir, state=child_state)
    if not evidence.artifact_integrity.valid or not evidence.evidence_freshness.fresh:
        raise ValueError("当前 Core 证据仍缺失、损坏或过期")
    finish = build_finish_summary(
        child_dir, child_state, evidence.evidence_freshness, evidence.artifact_integrity,
    )
    if not _finish_allows_recheck(child_state, finish):
        raise ValueError("当前核心证据不能形成继续或修复结论")
    return child_state, finish


def _finish_allows_recheck(state: LoopAutomationState, finish: dict[str, object]) -> bool:
    from .agent_core_observation import review_status, risk_status, verification_status

    if finish.get("finish_status") in {"ready_to_commit", "needs_fix"}:
        return True
    # Core 的最终通过要求 Reviewer approve；request_changes 的既有修复路由
    # 则依据当前 iteration 的可信验证与风险状态，不把它改写成 Finish 成功。
    latest = state.iterations[-1] if state.iterations else None
    return bool(
        finish.get("finish_status") == "needs_human"
        and review_status(latest) == "failed" and risk_status(latest) == "passed"
        and verification_status(latest, finish) == "passed"
    )


def _load_recheck_observation(run_dir: Path, state: AgentState) -> AgentObservation:
    from .agent_contract import AgentDecision, AgentObservation
    from .agent_persistence import load_agent_checkpoint
    from .agent_worker_evidence import hash_evidence_refs

    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    # Checkpoint 的 operation_started 记录历史尝试，不能当作当前活动进程；
    # 当前 State 的绑定与实际 execution quiescence 分别在入口和 Core 检查中验证。
    if (
        checkpoint.run_id != state.run_id or checkpoint.status != "blocked"
        or checkpoint.phase != "needs_human"
        or checkpoint.active_child_run is not None or checkpoint.pending_actions != ["human"]
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.state_version + 1 != state.state_version
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.external_side_effects != "none"
        or len(checkpoint.failed_attempts) != 1
    ):
        raise ValueError("核心证据重算缺少同现场的失败 Checkpoint")
    observation_refs = [ref for ref in checkpoint.evidence_refs if ref.startswith("observations/")]
    decision_refs = [ref for ref in checkpoint.evidence_refs if ref.startswith("decisions/")]
    if len(observation_refs) != 1 or len(decision_refs) != 1:
        raise ValueError("核心证据重算没有唯一 Observation 与 Decision")
    observation = AgentObservation.model_validate(_read_ref(run_dir, observation_refs[0]))
    decision = AgentDecision.model_validate(_read_ref(run_dir, decision_refs[0]))
    if (
        observation_refs[0] != f"observations/{observation.observation_id}.json"
        or decision_refs[0] != f"decisions/{decision.decision_id}.json"
        or decision.observation_id != observation.observation_id
        or decision.selected_action != "human" or decision.reason_code != "evidence.core_untrusted"
        or observation.core_evidence != "stale" or observation.authority != "machine_reconcile"
        or observation.worker_alive or not observation.operation_started
        or not observation.workspace_explained or observation.plan_contradicted
        or observation.external_side_effects != "none"
        or observation.child_run != checkpoint.failed_attempts[0]
        or observation.work_item_id != state.current_work_item or observation.operation_id is None
        or hash_evidence_refs(run_dir, observation.evidence_refs) != observation.evidence_sha256
    ):
        raise ValueError("原始 Observation 不是可重算的核心证据失败")
    return observation


def _require_not_rechecked(run_dir: Path, candidate_sha: str) -> None:
    for path in (run_dir / "operations").glob("*.json"):
        operation = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(operation, dict):
            raise ValueError("核心证据重算历史 operation 无效")
        if (operation.get("retry_reason") == "core_evidence_recheck"
                and operation.get("candidate_sha") == candidate_sha):
            raise ValueError("同一 Candidate 已重算过核心证据")


def require_existing_integration_review(
    run_dir: Path, observation: AgentObservation, candidate_sha: str,
) -> str:
    import hashlib

    refs = [ref for ref in observation.evidence_refs if ref.startswith("integration-reviews/")]
    if len(refs) != 1:
        raise ValueError("最终集成审查尚未形成受信证据，不能仅靠核心重算通过")
    payload = _read_ref(run_dir, refs[0])
    if (payload.get("status") != "approve" or payload.get("candidate_sha") != candidate_sha
            or hashlib.sha256((run_dir / refs[0]).read_bytes()).hexdigest()
            != observation.evidence_sha256.get(refs[0])):
        raise ValueError("最终集成审查与当前 Candidate 不一致")
    return refs[0]


def _read_ref(run_dir: Path, ref: str) -> dict[str, object]:
    path = (run_dir / ref).resolve(strict=True)
    if not path.is_relative_to(run_dir.resolve(strict=True)):
        raise ValueError("核心证据引用越过 run 边界")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("核心证据引用不是 JSON object")
    return payload
