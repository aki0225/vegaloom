from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .agent_worker_evidence import WorkerClaim, load_child_state, load_finish_summary
from .agent_plan_scope import PlanScopeBaseline
from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
    canonical_digest,
)
from .agent_operation import child_summary_ref
from .agent_status_evidence import build_supervisor_evidence
from .agent_verification_retry_archive import retry_source_finish_ref
from .models import LoopAutomationState
from .redaction import write_redacted_json_once
from .review_evidence import review_evidence_schema_issues
from .run_utils import resolve_run_dir
from .scope_gate import ScopeGateResult
from .workspace_check import snapshot_workspace
from .workspace_inventory import (
    WorkspaceSnapshot,
    hash_tracked_diff,
    workspace_ignored_path_exclusions,
)
from .workspace_snapshot import ReviewWorkspaceSnapshot


_ALLOWED_RETRY_ENVIRONMENT_DRIFT_ISSUES = frozenset(
    {
        "ignored_manifest_sha256_mismatch",
        "current_ignored_manifest_incomplete",
        "ignored_content_complete_mismatch",
    }
)


@dataclass(frozen=True)
class PreparedVerificationRetry:
    run_dir: Path
    state: AgentState
    plan: AgentPlan
    work_item: AgentWorkItem
    repo: Path
    before: ReviewWorkspaceSnapshot
    child_dir: Path
    child_state: LoopAutomationState
    source_plan: AgentPlan
    source_observation: AgentObservation
    source_claim: WorkerClaim
    source_summary_ref: str
    source_operation_id: str
    source_finish_sha256: str
    core_workspace_baseline: WorkspaceSnapshot
    plan_scope_baseline: PlanScopeBaseline
    pre_core_scope: ScopeGateResult
    comparison_base_sha: str | None
    comparison_paths: tuple[str, ...]
    retry_reason: Literal["verification_failure", "reviewer_timeout"] = (
        "verification_failure"
    )
    candidate_sha: str | None = None
    candidate_ref: str | None = None
    reviewer_retry_attempt: int = 0
    reviewer_role_key: str | None = None


def load_source_observation(
    run_dir: Path,
    child_run: str,
    operation_id: str,
) -> AgentObservation:
    matches: list[AgentObservation] = []
    for path in (run_dir / "observations").glob("*.json"):
        try:
            observation = AgentObservation.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError):
            continue
        if (
            observation.child_run == child_run
            and observation.operation_id == operation_id
        ):
            matches.append(observation)
    if len(matches) != 1:
        raise ValueError("无法唯一定位原始 Worker 的受信 Observation")
    return matches[0]


def matching_source_plans(run_dir: Path, current: AgentPlan) -> list[AgentPlan]:
    matches: list[AgentPlan] = []
    for path in sorted((run_dir / "plans").glob("plan-revision-*.json")):
        try:
            candidate = AgentPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError):
            continue
        if (
            candidate.approval_is_current()
            and candidate.plan_revision < current.plan_revision
            and _plan_without_verification(candidate) == _plan_without_verification(current)
        ):
            matches.append(candidate)
    if not matches:
        raise ValueError("缺少可验证的旧 Plan revision，不能证明只修改了验证命令")
    return matches


def select_source_plan(
    run_dir: Path,
    state: AgentState,
    candidates: list[AgentPlan],
    observation: AgentObservation,
) -> AgentPlan:
    trusted = [
        candidate
        for candidate in candidates
        if all(
            item.status == "passed"
            for item in build_supervisor_evidence(
                run_dir,
                state,
                observation,
                candidate,
            )[:3]
        )
    ]
    if len(trusted) != 1:
        raise ValueError("旧 Plan revision 无法唯一绑定原始 Worker 与范围证据")
    return trusted[0]


def _plan_without_verification(plan: AgentPlan) -> dict[str, object]:
    payload = plan.content_for_approval()
    payload.pop("plan_revision", None)
    work_items = payload.get("work_items")
    if isinstance(work_items, list):
        for item in work_items:
            if isinstance(item, dict):
                item["verification"] = []
    return payload


def validate_retry_source(
    run_dir: Path,
    state: AgentState,
    source_plan: AgentPlan,
    observation: AgentObservation,
    child_dir: Path,
    snapshot: ReviewWorkspaceSnapshot,
) -> tuple[str, WorkerClaim, str]:
    if (
        observation.authority != "machine_reconcile"
        or observation.worker_alive
        or not observation.operation_started
        or not observation.workspace_explained
        or observation.external_side_effects != "none"
        or observation.plan_contradicted
        or observation.verification not in {"failed", "blocked"}
        or observation.risk != "passed"
        or observation.review not in {"failed", "blocked"}
        or sorted(observation.changed_files) != sorted(snapshot.changed_files)
        or snapshot.untracked_files
        or not snapshot.untracked_content_complete
        or snapshot.unsafe_index_paths
    ):
        raise ValueError("原始 Observation 不满足验证专用恢复前提")
    evidence = build_supervisor_evidence(
        run_dir,
        state,
        observation,
        source_plan,
    )
    if len(evidence) < 3 or any(item.status != "passed" for item in evidence[:3]):
        raise ValueError("原始 Worker 或 Plan Scope 证据无法重新验证")
    summary_refs = [
        ref for ref in observation.evidence_refs if ref.startswith("children/")
    ]
    if len(summary_refs) != 1:
        raise ValueError("原始 Observation 没有唯一 child 摘要")
    source_summary_ref = summary_refs[0]
    try:
        source_summary = json.loads(
            (run_dir / source_summary_ref).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取原始 child 证据") from exc
    worker = source_summary.get("worker") if isinstance(source_summary, dict) else None
    claim_payload = worker.get("claim") if isinstance(worker, dict) else None
    try:
        claim = WorkerClaim.model_validate(claim_payload)
    except ValidationError as exc:
        raise ValueError("原始 Worker Claim 无法验证") from exc
    finish, finish_sha256 = load_bound_source_finish(source_summary, child_dir)
    if claim.claimed_status != "completed":
        raise ValueError("原始 child 的失败原因不属于验证专用恢复")
    if observation.workspace_fingerprint != snapshot.fingerprint:
        _require_tracked_workspace_continuity(
            run_dir,
            observation,
            finish,
            snapshot,
        )
    if not _finish_allows_verification_retry(finish, snapshot):
        raise ValueError("原始 child 的失败原因不属于验证专用恢复")
    return source_summary_ref, claim, finish_sha256


def load_bound_source_finish(
    source_summary: object,
    child_dir: Path,
) -> tuple[dict[str, object], str]:
    core = source_summary.get("core") if isinstance(source_summary, dict) else None
    expected_sha256 = core.get("finish_sha256") if isinstance(core, dict) else None
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("原始 child 没有绑定可验证的 Finish 摘要")
    try:
        finish_bytes = (child_dir / "finish-summary.json").read_bytes()
        finish = json.loads(finish_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取原始 child 的 Finish 证据") from exc
    if (
        hashlib.sha256(finish_bytes).hexdigest() != expected_sha256
        or not isinstance(finish, dict)
        or finish.get("run_id") != child_dir.name
    ):
        raise ValueError("原始 child 的 Finish 证据缺失、已变化或身份不一致")
    return finish, expected_sha256


def _require_tracked_workspace_continuity(
    run_dir: Path,
    observation: AgentObservation,
    finish: dict[str, object],
    snapshot: ReviewWorkspaceSnapshot,
) -> None:
    """允许验证环境变化，但要求待提交的 Git 事实与原审查快照完全一致。"""

    freshness = finish.get("evidence_freshness")
    if not isinstance(freshness, dict):
        raise ValueError("原始 Finish 缺少可绑定的审查快照")
    source_run = freshness.get("source_run")
    snapshot_id = freshness.get("snapshot_id")
    trusted_fingerprint = freshness.get("trusted_workspace_fingerprint")
    if (
        not isinstance(source_run, str)
        or not isinstance(snapshot_id, str)
        or trusted_fingerprint != observation.workspace_fingerprint
    ):
        raise ValueError("原始 Finish 无法绑定受信审查快照")
    try:
        reflect_dir = resolve_run_dir(run_dir.parent.parent, source_run)
        evidence = json.loads(
            (reflect_dir / "review-evidence.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取原始受信审查快照") from exc
    if not isinstance(evidence, dict):
        raise ValueError("原始受信审查快照格式无效")
    evidence_snapshot_id = evidence.get("snapshot_id")
    snapshot_payload = {
        key: value for key, value in evidence.items() if key != "snapshot_id"
    }
    if (
        evidence_snapshot_id != snapshot_id
        or canonical_digest(snapshot_payload) != snapshot_id
        or evidence.get("source_run") != source_run
        or evidence.get("workspace_fingerprint") != observation.workspace_fingerprint
    ):
        raise ValueError("原始受信审查快照的身份或摘要不匹配")

    current_hashes = {
        "head_sha": snapshot.head_sha,
        "status_sha256": snapshot.status_sha256,
        "untracked_manifest_sha256": snapshot.untracked_manifest_sha256,
    }
    if any(evidence.get(key) != value for key, value in current_hashes.items()):
        raise ValueError("验证恢复前 tracked Workspace 已变化，必须重新调查")
    if (
        evidence.get("untracked_files") != []
        or snapshot.untracked_files
        or not snapshot.untracked_content_complete
        or snapshot.unsafe_index_paths
    ):
        raise ValueError("验证恢复前出现未受信的未跟踪文件或 Git index 状态")
    issues = set(review_evidence_schema_issues(evidence, snapshot))
    unexpected = issues - _ALLOWED_RETRY_ENVIRONMENT_DRIFT_ISSUES
    if unexpected:
        raise ValueError("验证恢复前 tracked Workspace 已变化或无法与原审查快照对账")


def capture_verification_retry_baseline(
    workspace: Path,
    repo: Path,
    review_snapshot: ReviewWorkspaceSnapshot,
) -> WorkspaceSnapshot:
    baseline = snapshot_workspace(
        repo,
        ignored_path_exclusions=workspace_ignored_path_exclusions(
            workspace,
            repo,
        ),
    )
    expected_tracked_diff = hash_tracked_diff(
        review_snapshot.staged_diff,
        review_snapshot.unstaged_diff,
    )
    if (
        not baseline.capture_complete
        or not baseline.tracked_diff_complete
        or baseline.head_sha != review_snapshot.head_sha
        or baseline.tracked_diff_sha256 != expected_tracked_diff
        or baseline.untracked_files
        or baseline.untracked_manifest_sha256
        != review_snapshot.untracked_manifest_sha256
        or baseline.git_control_sha256 != review_snapshot.git_control_sha256
        or baseline.unsafe_index_paths
    ):
        raise ValueError("无法为验证专用恢复封存稳定的当前 Workspace 基线")
    return baseline


def same_tracked_workspace(
    before: ReviewWorkspaceSnapshot,
    after: ReviewWorkspaceSnapshot,
) -> bool:
    return bool(
        before.head_sha == after.head_sha
        and before.status_sha256 == after.status_sha256
        and before.staged_diff_sha256 == after.staged_diff_sha256
        and before.unstaged_diff_sha256 == after.unstaged_diff_sha256
        and before.committed_diff_sha256 == after.committed_diff_sha256
        and before.untracked_manifest_sha256 == after.untracked_manifest_sha256
        and before.git_control_sha256 == after.git_control_sha256
        and before.index_flags_sha256 == after.index_flags_sha256
        and before.comparison_base_sha == after.comparison_base_sha
        and before.comparison_paths == after.comparison_paths
        and before.changed_files == after.changed_files
        and not before.untracked_files
        and not after.untracked_files
        and not before.unsafe_index_paths
        and not after.unsafe_index_paths
    )


def _finish_allows_verification_retry(
    finish: object,
    snapshot: ReviewWorkspaceSnapshot,
) -> bool:
    if not isinstance(finish, dict):
        return False
    first_screen = finish.get("first_screen")
    if not isinstance(first_screen, dict):
        return False
    actual_changes = first_screen.get("actual_changes")
    gates = first_screen.get("gates")
    review = first_screen.get("review")
    risk = gates.get("risk") if isinstance(gates, dict) else None
    findings = review.get("findings") if isinstance(review, dict) else None
    if not isinstance(findings, list):
        return False
    return bool(
        finish.get("finish_status") == "needs_fix"
        and finish.get("verification_passed") is False
        and finish.get("latest_verification_failed") is True
        and _nested_flag(finish, "artifact_integrity", "valid")
        and _nested_flag(finish, "evidence_freshness", "fresh")
        and isinstance(actual_changes, dict)
        and sorted(actual_changes.get("changed_files") or [])
        == sorted(snapshot.changed_files)
        and isinstance(gates, dict)
        and gates.get("verification") == "failed"
        and isinstance(risk, dict)
        and risk.get("status") == "success"
        and isinstance(review, dict)
        and review.get("verdict") in {"needs_human", "request_changes"}
        and all(_finding_is_verification_only(item) for item in findings)
    )


def _finding_is_verification_only(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("file") in {None, ""} and value.get("line") in {None, 0}


def write_retry_child_summary(
    prepared: PreparedVerificationRetry,
    operation_id: str,
    child_state: LoopAutomationState | None,
    finish_summary: dict[str, object] | None,
    *,
    failure_reason: str | None = None,
) -> str:
    relative = child_summary_ref(prepared.child_dir.name, operation_id)
    source_path = prepared.run_dir / prepared.source_summary_ref
    finish_path = prepared.child_dir / "finish-summary.json"
    write_redacted_json_once(
        prepared.run_dir / relative,
        {
            "schema_version": 1,
            "authority": "child_binding_summary",
            "operation_kind": "verification_retry",
            "retry_reason": prepared.retry_reason,
            "candidate_sha": prepared.candidate_sha,
            "reviewer_retry_attempt": prepared.reviewer_retry_attempt,
            "agent_run_id": prepared.state.run_id,
            "work_item_id": prepared.state.current_work_item,
            "child_run": prepared.child_dir.name,
            "operation_id": operation_id,
            "worker": {
                "source_child_run": prepared.child_dir.name,
                "source_operation_id": prepared.source_operation_id,
                "source_summary_ref": prepared.source_summary_ref,
                "source_summary_sha256": hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest(),
                "source_finish_ref": retry_source_finish_ref(operation_id),
                "source_finish_sha256": prepared.source_finish_sha256,
            },
            "core": {
                "status": child_state.status if child_state is not None else "unknown",
                "current_step": (
                    child_state.current_step if child_state is not None else "unknown"
                ),
                "finish_status": (
                    finish_summary.get("finish_status")
                    if finish_summary is not None
                    else None
                ),
                "finish_sha256": (
                    hashlib.sha256(finish_path.read_bytes()).hexdigest()
                    if finish_path.is_file()
                    else None
                ),
                "failure_reason": failure_reason,
            },
        },
    )
    return relative


def load_optional_child_state(
    child_dir: Path,
    repo: Path,
) -> LoopAutomationState | None:
    try:
        return load_child_state(child_dir, repo)
    except ValueError:
        return None


def load_optional_finish(
    child_dir: Path,
    child_run: str,
) -> dict[str, object] | None:
    try:
        return load_finish_summary(child_dir, child_run)
    except ValueError:
        return None


def _nested_flag(payload: dict[str, object], key: str, nested: str) -> bool:
    value = payload.get(key)
    return isinstance(value, dict) and value.get(nested) is True
