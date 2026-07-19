from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .execution_control import ExecutionLease
from .models import (
    GateResult,
    GateState,
    GoalCheckpointEvidenceType,
    GoalCheckpointRef,
    LoopAutomationState,
    LoopIterationState,
    ReviewState,
    ReviewVerdict,
)
from .project_config import (
    VERIFICATION_TEMP_PLACEHOLDER,
    VERIFICATION_TEMP_ROOT,
    build_verification_shell_command,
    load_project_config,
    project_policy_snapshot,
    render_verification_command,
    scope_policy_sha256,
)
from .redaction import sensitive_path_reason
from .risk_gate_evidence import validate_iteration_risk_gate_artifacts
from .run_status import run_status_payload
from .run_utils import resolve_run_dir
from .scope_gate import validate_iteration_scope_gate_artifacts
from .workspace_check import capture_review_workspace


@dataclass(frozen=True)
class EvidenceFreshness:
    fresh: bool
    issues: tuple[str, ...]
    current_workspace_fingerprint: str
    trusted_workspace_fingerprint: str = ""
    source_run: str = ""
    review_run: str = ""
    snapshot_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "fresh": self.fresh,
            "issues": list(self.issues),
            "current_workspace_fingerprint": self.current_workspace_fingerprint,
            "trusted_workspace_fingerprint": self.trusted_workspace_fingerprint,
            "source_run": self.source_run,
            "review_run": self.review_run,
            "snapshot_id": self.snapshot_id,
        }


@dataclass(frozen=True)
class LoopArtifactIntegrity:
    valid: bool
    issues: tuple[str, ...]
    review_verdicts: tuple[ReviewVerdict, ...] = ()
    verification_results: tuple[dict[str, Any], ...] = ()
    risk_gate_results: tuple[GateResult, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": list(self.issues),
            "review_verdict_count": len(self.review_verdicts),
            "verification_result_count": len(self.verification_results),
            "risk_gate_result_count": len(self.risk_gate_results),
        }


def validate_reflect_evidence_freshness(
    workspace: Path,
    repo_path: Path,
    source_run: str,
    *,
    current_workspace_fingerprint: str | None = None,
) -> EvidenceFreshness:
    repo = repo_path.resolve()
    source_dir = resolve_run_dir(workspace, source_run)
    state = _read_json(source_dir / "state.json")
    evidence = _read_json(source_dir / "review-evidence.json")
    current_fingerprint, snapshot_issues = _current_workspace_fingerprint(
        repo,
        current_workspace_fingerprint,
    )
    issues = list(snapshot_issues)
    if str(state.get("run_id") or "") != source_dir.name:
        issues.append("source_run_id_mismatch")
    if state.get("status") != "success":
        issues.append("source_reflect_not_success")
    source_repo = str(state.get("repo_path") or "")
    if not source_repo or _normalized_path(source_repo) != _normalized_path(repo):
        issues.append("source_repo_mismatch")
    if not evidence:
        issues.append("source_snapshot_missing")
        return _freshness(
            issues,
            current_fingerprint,
            source_run=source_dir.name,
        )

    schema_version = evidence.get("schema_version")
    if schema_version not in {2, 3}:
        issues.append("source_evidence_schema_unsupported")
    if schema_version == 3:
        for key in ("staged_diff_sha256", "unstaged_diff_sha256"):
            if not _is_sha256(evidence.get(key)):
                issues.append(f"{key}_invalid")
    snapshot_id = str(evidence.get("snapshot_id") or "")
    snapshot_payload = {key: value for key, value in evidence.items() if key != "snapshot_id"}
    if not snapshot_id or snapshot_id != _sha256_json(snapshot_payload):
        issues.append("snapshot_metadata_invalid")
    if str(evidence.get("source_run") or "") != source_dir.name:
        issues.append("source_evidence_run_id_mismatch")
    upstream_source_run = state.get("source_run")
    if evidence.get("upstream_source_run") != upstream_source_run:
        issues.append("upstream_source_run_mismatch")

    state_changed_files_value = state.get("changed_files")
    evidence_changed_files_value = evidence.get("changed_files")
    state_changed_files_valid = _is_string_list(state_changed_files_value)
    evidence_changed_files_valid = _is_string_list(evidence_changed_files_value)
    if not state_changed_files_valid:
        issues.append("source_changed_files_invalid")
    if not evidence_changed_files_valid:
        issues.append("evidence_changed_files_invalid")
    if state_changed_files_value != evidence_changed_files_value:
        issues.append("changed_files_mismatch")
    if str(evidence.get("changed_files_sha256") or "") != _sha256_json(
        evidence_changed_files_value
        if isinstance(evidence_changed_files_value, list)
        else []
    ):
        issues.append("changed_files_hash_mismatch")

    for artifact_name, hash_key, issue_prefix in (
        ("full-diff.patch", "full_diff_sha256", "full_diff"),
        ("test-summary.md", "test_summary_sha256", "test_summary"),
        ("reflection.md", "reflection_sha256", "reflection"),
        ("diff-summary.md", "diff_summary_sha256", "diff_summary"),
    ):
        _validate_text_artifact_hash(
            source_dir / artifact_name,
            evidence,
            hash_key,
            issue_prefix,
            issues,
        )

    source_brief_issues = evidence.get("source_brief_evidence_issues")
    if not isinstance(source_brief_issues, list) or not all(
        isinstance(item, str) for item in source_brief_issues
    ):
        issues.append("source_brief_evidence_issues_invalid")
    else:
        issues.extend(source_brief_issues)
    source_brief = _load_source_brief_artifact(
        workspace,
        repo,
        upstream_source_run,
        issues,
    )
    if str(evidence.get("source_brief_sha256") or "") != _sha256_text(source_brief):
        issues.append("source_brief_hash_mismatch")
    if evidence.get("untracked_content_complete") is not True:
        issues.append("source_untracked_content_incomplete")

    trusted_fingerprint = str(evidence.get("workspace_fingerprint") or "")
    if (
        not trusted_fingerprint
        or state.get("workspace_fingerprint") != trusted_fingerprint
    ):
        issues.append("source_fingerprint_invalid")
    if state.get("review_snapshot_id") != snapshot_id:
        issues.append("source_snapshot_id_invalid")
    if current_fingerprint != trusted_fingerprint:
        issues.append("workspace_changed_since_reflect")
    return _freshness(
        issues,
        current_fingerprint,
        trusted_workspace_fingerprint=trusted_fingerprint,
        source_run=source_dir.name,
        snapshot_id=snapshot_id,
    )


def validate_review_evidence_freshness(
    workspace: Path,
    repo_path: Path,
    review_run: str,
    *,
    current_workspace_fingerprint: str | None = None,
) -> EvidenceFreshness:
    repo = repo_path.resolve()
    review_dir = resolve_run_dir(workspace, review_run)
    state, state_issue = _load_review_state(review_dir / "state.json")
    context, context_issue = _load_json_object(review_dir / "review-context.json")
    verdict, verdict_issue = _load_review_verdict(review_dir / "review-verdict.json")
    current_fingerprint, snapshot_issues = _current_workspace_fingerprint(
        repo,
        current_workspace_fingerprint,
    )
    issues = list(snapshot_issues)
    if state_issue:
        issues.append(f"review_state_{state_issue}")
    if context_issue:
        issues.append(f"review_context_{context_issue}")
    if verdict_issue:
        issues.append(f"review_verdict_{verdict_issue}")
    state_payload = state.model_dump() if state else {}
    context = context or {}
    review_repo = str(state_payload.get("repo_path") or "")
    if state and state.run_id != review_dir.name:
        issues.append("review_run_id_mismatch")
    if not review_repo or _normalized_path(review_repo) != _normalized_path(repo):
        issues.append("review_repo_mismatch")
    if state_payload.get("status") != "success" or state_payload.get("verdict") != "approve":
        issues.append("review_not_approved")
    if verdict and state and verdict.verdict != state.verdict:
        issues.append("review_state_verdict_mismatch")
    source_run = str(state_payload.get("source_run") or "")
    if not source_run or context.get("source_run") != source_run:
        issues.append("review_source_mismatch")
    if not context.get("evidence_consistent"):
        issues.append("review_evidence_inconsistent")
    if context.get("workspace_changed_during_review"):
        issues.append("workspace_changed_during_review")
    if source_run:
        try:
            reflect_freshness = validate_reflect_evidence_freshness(
                workspace,
                repo,
                source_run,
                current_workspace_fingerprint=current_fingerprint,
            )
        except FileNotFoundError:
            issues.append("source_reflect_missing")
            trusted_fingerprint = ""
            snapshot_id = ""
        else:
            issues.extend(reflect_freshness.issues)
            trusted_fingerprint = reflect_freshness.trusted_workspace_fingerprint
            snapshot_id = reflect_freshness.snapshot_id
        _validate_review_risk_gate(
            workspace,
            repo,
            source_run,
            context,
            issues,
        )
    else:
        trusted_fingerprint = ""
        snapshot_id = ""

    review_fingerprints = {
        str(context.get("source_workspace_fingerprint") or ""),
        str(context.get("current_workspace_fingerprint") or ""),
        str(context.get("reviewer_start_workspace_fingerprint") or ""),
        str(context.get("reviewer_end_workspace_fingerprint") or ""),
    }
    if "" in review_fingerprints or review_fingerprints != {trusted_fingerprint}:
        issues.append("review_fingerprint_invalid")
    if current_fingerprint != trusted_fingerprint:
        issues.append("workspace_changed_since_review")
    if context.get("source_snapshot_id") != snapshot_id:
        issues.append("review_snapshot_id_invalid")
    return _freshness(
        issues,
        current_fingerprint,
        trusted_workspace_fingerprint=trusted_fingerprint,
        source_run=source_run,
        review_run=review_dir.name,
        snapshot_id=snapshot_id,
    )


def _validate_review_risk_gate(
    workspace: Path,
    repo_path: Path,
    source_run: str,
    context: dict[str, Any],
    issues: list[str],
) -> None:
    """重算 review 所依赖 Reflect 的风险门禁，禁止本地伪造 approve 覆盖人工审查。"""
    recorded_gate = context.get("risk_gate")
    if not isinstance(recorded_gate, dict):
        issues.append("review_risk_gate_missing")
        return
    if recorded_gate.get("source_run") != source_run:
        issues.append("review_risk_gate_source_mismatch")
    if recorded_gate.get("status") != "success":
        issues.append("review_risk_gate_failed")
        return
    try:
        recorded_result = GateResult.model_validate(recorded_gate.get("result"))
    except ValidationError:
        issues.append("review_risk_gate_result_invalid")
        return

    try:
        # gate_runtime 在模块加载时依赖本模块；延迟导入可避免循环依赖。
        from .gate_runtime import evaluate_risk

        expected_result = evaluate_risk(workspace, repo_path, source_run)
    except Exception:  # noqa: BLE001 - 无法重算时不得信任 review 的自动通过
        issues.append("review_risk_gate_recomputation_failed")
        return
    if _gate_result_semantics(recorded_result) != _gate_result_semantics(expected_result):
        issues.append("review_risk_gate_result_mismatch")
    if expected_result.recommendation == "human-review":
        issues.append("review_risk_gate_requires_human")


def _gate_result_semantics(result: GateResult) -> tuple[object, ...]:
    return (
        result.risk,
        result.recommendation,
        tuple((reason.code, reason.severity) for reason in result.reasons),
        result.scope_profile,
    )


def validate_loop_artifact_integrity(
    workspace: Path,
    repo_path: Path,
    loop_run: str | Path,
    *,
    state: LoopAutomationState | None = None,
) -> LoopArtifactIntegrity:
    loop_dir = (
        loop_run.resolve()
        if isinstance(loop_run, Path)
        else resolve_run_dir(workspace, loop_run)
    )
    issues: list[str] = []
    if state is None:
        state, state_issue = _load_loop_state(loop_dir / "state.json")
        if state_issue:
            issues.append(f"loop_state_{state_issue}")
        if state is None:
            return _artifact_integrity(issues)

    if _normalized_path(state.repo_path) != _normalized_path(repo_path):
        issues.append("loop_repo_mismatch")
    if state.run_id != loop_dir.name:
        issues.append("loop_run_id_mismatch")
    if state.status == "success" and state.iterations and not state.scope_gate_required:
        # 旧 run 仍可查看和复盘，但缺少三阶段 scope 证据时不能自动升级为 ready_to_commit。
        issues.append("legacy_scope_gate_unverified")
    if (
        state.status == "success"
        and state.iterations
        and state.iterations[-1].lifecycle != "completed"
    ):
        issues.append("loop_success_latest_iteration_interrupted")
    _validate_project_policy_snapshot(loop_dir, repo_path, state, issues)

    verdicts: list[ReviewVerdict] = []
    verification_results: list[dict[str, Any]] = []
    risk_gate_results: list[GateResult] = []
    expected_iteration_dirs: set[Path] = set()
    seen_iterations: set[int] = set()
    latest_iteration = state.iterations[-1].iteration if state.iterations else 0
    for expected_iteration, iteration in enumerate(state.iterations, start=1):
        prefix = f"iteration_{iteration.iteration:02d}"
        if iteration.iteration != expected_iteration:
            issues.append(f"{prefix}_sequence_mismatch")
        if iteration.iteration in seen_iterations:
            issues.append(f"{prefix}_duplicate")
            continue
        seen_iterations.add(iteration.iteration)
        iteration_dir = loop_dir / "iterations" / f"{iteration.iteration:02d}"
        expected_iteration_dirs.add(iteration_dir.resolve())
        if not iteration_dir.is_dir():
            issues.append(f"{prefix}_directory_missing")
            continue
        if iteration.lifecycle == "interrupted":
            issues.extend(
                _validate_interrupted_iteration(
                    iteration_dir,
                    iteration,
                )
            )
            continue
        if iteration.interrupted_step is not None or iteration.interrupted_at is not None:
            issues.append(f"{prefix}_completed_with_interruption_metadata")
        # 前序 iteration 的 Reflect 会被后续 worker 的合法改动自然淘汰，不能拿终态
        # 工作区重算其历史风险；否则多轮 loop 会产生错误的 fail-closed。最终 iteration
        # 仍必须以当前可信工作区重算，防止影响终态结论的风险结果被同步降级。
        recompute_from_current_workspace = iteration.iteration == latest_iteration
        scope_gate_integrity = validate_iteration_scope_gate_artifacts(
            iteration_dir,
            iteration,
            phase="pre_verification",
            # verification 可能在 pre gate 之后修改工作区；只对 post gate 使用终态
            # 工作区重算，pre gate 保持 result/report/state/trace 的绑定校验。
            repo_path=None,
            trace_path=loop_dir / "trace.jsonl",
            required=state.scope_gate_required,
            expected_head_sha=state.initial_head_sha,
            expected_policy_sha256=state.scope_policy_sha256,
        )
        issues.extend(f"{prefix}_{issue}" for issue in scope_gate_integrity.issues)
        post_scope_gate_integrity = validate_iteration_scope_gate_artifacts(
            iteration_dir,
            iteration,
            phase="post_verification",
            # 最终工作区只用于重算真正紧邻 reviewer 的 pre-review gate。
            repo_path=None,
            trace_path=loop_dir / "trace.jsonl",
            required=state.scope_gate_required,
            expected_head_sha=state.initial_head_sha,
            expected_policy_sha256=state.scope_policy_sha256,
        )
        issues.extend(
            f"{prefix}_post_verification_{issue}"
            for issue in post_scope_gate_integrity.issues
        )
        review_scope_gate_integrity = validate_iteration_scope_gate_artifacts(
            iteration_dir,
            iteration,
            phase="pre_review",
            repo_path=repo_path if recompute_from_current_workspace else None,
            trace_path=loop_dir / "trace.jsonl",
            required=state.scope_gate_required,
            expected_head_sha=state.initial_head_sha,
            expected_policy_sha256=state.scope_policy_sha256,
        )
        issues.extend(
            f"{prefix}_pre_review_{issue}" for issue in review_scope_gate_integrity.issues
        )
        gate_integrity = validate_iteration_risk_gate_artifacts(
            iteration_dir,
            iteration,
            workspace=workspace if recompute_from_current_workspace else None,
            repo_path=repo_path if recompute_from_current_workspace else None,
            trace_path=loop_dir / "trace.jsonl",
        )
        issues.extend(f"{prefix}_{issue}" for issue in gate_integrity.issues)
        if gate_integrity.result is not None:
            risk_gate_results.append(gate_integrity.result)
        _validate_iteration_review(
            workspace,
            repo_path,
            iteration_dir,
            iteration,
            issues,
            verdicts,
        )
        _validate_iteration_verification(
            repo_path,
            iteration_dir,
            iteration,
            issues,
            verification_results,
            expected_artifact_version=state.verification_artifact_version,
        )

    if state.iterations:
        if state.current_iteration != state.iterations[-1].iteration:
            issues.append("loop_current_iteration_mismatch")
    elif state.current_iteration != 0:
        issues.append("loop_current_iteration_unexpected")

    for artifact_name, issue_name in (
        ("scope-gate-result.json", "unbound_scope_gate_result"),
        ("scope-gate-report.md", "unbound_scope_gate_report"),
        (
            "scope-gate-post-verification-result.json",
            "unbound_post_verification_scope_gate_result",
        ),
        (
            "scope-gate-post-verification-report.md",
            "unbound_post_verification_scope_gate_report",
        ),
        ("scope-gate-pre-review-result.json", "unbound_pre_review_scope_gate_result"),
        ("scope-gate-pre-review-report.md", "unbound_pre_review_scope_gate_report"),
        ("risk-gate-result.json", "unbound_risk_gate_result"),
        ("risk-gate-report.md", "unbound_risk_gate_report"),
        ("review-verdict.json", "unbound_review_verdict"),
        ("verification-result.json", "unbound_verification_result"),
    ):
        for path in sorted(loop_dir.glob(f"iterations/*/{artifact_name}")):
            if path.parent.resolve() not in expected_iteration_dirs:
                issues.append(f"{issue_name}:{path.relative_to(loop_dir).as_posix()}")

    return _artifact_integrity(
        issues,
        review_verdicts=verdicts,
        verification_results=verification_results,
        risk_gate_results=risk_gate_results,
    )


def _validate_project_policy_snapshot(
    loop_dir: Path,
    repo_path: Path,
    state: LoopAutomationState,
    issues: list[str],
) -> None:
    """绑定启动策略 artifact，并在 Finish/Goal 校验时复查当前策略。"""
    expected_hash = state.project_policy_snapshot_sha256
    if expected_hash is not None:
        path = loop_dir / "project-policy-snapshot.json"
        if not path.is_file():
            issues.append("project_policy_snapshot_missing")
        else:
            try:
                text = path.read_text(encoding="utf-8")
                payload = json.loads(text)
            except OSError:
                issues.append("project_policy_snapshot_unreadable")
            except json.JSONDecodeError:
                issues.append("project_policy_snapshot_invalid_json")
            else:
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash:
                    issues.append("project_policy_snapshot_hash_mismatch")
                if payload != state.project_policy_snapshot:
                    issues.append("project_policy_snapshot_state_mismatch")
    if state.project_policy_snapshot:
        try:
            current_snapshot = project_policy_snapshot(repo_path)
            current_config = load_project_config(repo_path)
        except Exception:  # noqa: BLE001 - 当前策略无法解析时不能信任旧的完成结论
            issues.append("project_policy_snapshot_current_unreadable")
        else:
            if current_snapshot != state.project_policy_snapshot:
                issues.append("project_policy_changed_since_loop_start")
            if (
                state.scope_policy_sha256 is not None
                and scope_policy_sha256(current_config.scope)
                != state.scope_policy_sha256
            ):
                issues.append("scope_policy_changed_since_loop_start")


def validate_loop_evidence_freshness(
    workspace: Path,
    repo_path: Path,
    loop_run: str | Path,
) -> EvidenceFreshness:
    repo = repo_path.resolve()
    loop_dir = (
        loop_run.resolve()
        if isinstance(loop_run, Path)
        else resolve_run_dir(workspace, loop_run)
    )
    state = _read_json(loop_dir / "state.json")
    current_fingerprint, snapshot_issues = _current_workspace_fingerprint(repo, None)
    issues = list(snapshot_issues)
    if str(state.get("run_id") or "") != loop_dir.name:
        issues.append("loop_run_id_mismatch")
    loop_repo = str(state.get("repo_path") or "")
    if not loop_repo or _normalized_path(loop_repo) != _normalized_path(repo):
        issues.append("loop_repo_mismatch")
    iterations = state.get("iterations") or []
    latest = iterations[-1] if iterations else {}
    review_run = str(latest.get("review_run") or "")
    reflect_run = str(latest.get("reflect_run") or "")
    if not review_run:
        issues.append("trusted_review_missing")
        return _freshness(
            issues,
            current_fingerprint,
            source_run=reflect_run,
        )
    try:
        review_freshness = validate_review_evidence_freshness(
            workspace,
            repo,
            review_run,
            current_workspace_fingerprint=current_fingerprint,
        )
    except FileNotFoundError:
        issues.append("trusted_review_missing")
        return _freshness(
            issues,
            current_fingerprint,
            source_run=reflect_run,
            review_run=review_run,
        )
    issues.extend(review_freshness.issues)
    if reflect_run != review_freshness.source_run:
        issues.append("loop_review_source_mismatch")
    if latest.get("verdict") != "approve":
        issues.append("latest_iteration_not_approved")
    integrity = validate_loop_artifact_integrity(
        workspace,
        repo,
        loop_dir,
    )
    issues.extend(integrity.issues)
    return _freshness(
        issues,
        current_fingerprint,
        trusted_workspace_fingerprint=review_freshness.trusted_workspace_fingerprint,
        source_run=review_freshness.source_run,
        review_run=review_freshness.review_run,
        snapshot_id=review_freshness.snapshot_id,
    )


def validate_goal_evidence(
    workspace: Path,
    goal_repo_path: Path,
    reference: str,
    evidence_type: GoalCheckpointEvidenceType,
    note: str | None,
) -> GoalCheckpointRef:
    """解析并校验 checkpoint 证据，避免把任意字符串当作已完成证明。

    自动运行证据必须指向真实 run，并且仓库与 goal 一致；manual 证据必须是
    workspace 或目标仓库内的真实文件，同时要求备注解释其用途。
    """
    if evidence_type == "manual":
        return _validate_manual_evidence(workspace, goal_repo_path, reference, note)
    return _validate_run_evidence(workspace, goal_repo_path, reference, evidence_type, note)


def _validate_run_evidence(
    workspace: Path,
    goal_repo_path: Path,
    reference: str,
    evidence_type: GoalCheckpointEvidenceType,
    note: str | None,
) -> GoalCheckpointRef:
    child_dir = resolve_run_dir(workspace, reference)
    runs_root = (workspace / "runs").resolve()
    if not _is_within(child_dir, runs_root):
        raise ValueError("自动证据必须来自当前 Vega workspace 的 runs/ 目录。")
    payload = run_status_payload(workspace, child_dir.name)
    expected_kind = _expected_kind(evidence_type)
    actual_kind = str(payload.get("kind") or "")
    if actual_kind != expected_kind:
        raise ValueError(
            f"证据类型与 child run 不匹配：type={evidence_type} 需要 kind={expected_kind}，"
            f"实际为 {actual_kind or 'unknown'}"
        )

    child_repo = str(payload.get("repo_path") or "")
    if not child_repo:
        raise ValueError(f"child run 缺少 repo_path，不能作为 goal 证据：{child_dir.name}")
    if _normalized_path(child_repo) != _normalized_path(goal_repo_path):
        raise ValueError(
            "child run 与 goal 不属于同一仓库："
            f"goal={goal_repo_path.resolve()}，child={Path(child_repo).resolve()}"
        )

    status = str(payload.get("status") or "unknown")
    eligible, summary = _completion_eligibility(
        workspace,
        goal_repo_path,
        child_dir,
        evidence_type,
        status,
    )
    return GoalCheckpointRef(
        run=child_dir.name,
        type=evidence_type,
        note=note.strip() if note and note.strip() else None,
        kind=actual_kind,
        status=status,
        repo_path=str(Path(child_repo).resolve()),
        validated=True,
        completion_eligible=eligible,
        validation_summary=summary,
        artifacts=list(payload.get("key_artifacts") or []),
    )


def _validate_manual_evidence(
    workspace: Path,
    goal_repo_path: Path,
    reference: str,
    note: str | None,
) -> GoalCheckpointRef:
    if not note or not note.strip():
        raise ValueError("manual 证据必须提供 --note，说明证据内容和人工判断依据。")
    reference_reason = sensitive_path_reason(reference)
    if reference_reason:
        raise ValueError(f"manual 证据拒绝敏感路径：{reference_reason}")
    candidate = _resolve_manual_path(workspace, goal_repo_path, reference)
    candidate_reason = sensitive_path_reason(candidate)
    if candidate_reason:
        raise ValueError(f"manual 证据拒绝敏感路径：{candidate_reason}")
    if not candidate.is_file():
        raise ValueError(f"manual 证据必须指向真实文件：{reference}")
    if not (_is_within(candidate, workspace.resolve()) or _is_within(candidate, goal_repo_path.resolve())):
        raise ValueError("manual 证据只能来自 Vega workspace 或目标仓库。")
    artifact_ref = _manual_artifact_ref(workspace, goal_repo_path, candidate)
    return GoalCheckpointRef(
        run=str(candidate),
        type="manual",
        note=note.strip(),
        kind="manual",
        status="present",
        repo_path=str(goal_repo_path.resolve()),
        validated=True,
        completion_eligible=False,
        validation_summary="manual 证据文件存在，但需要显式人工 override 才能完成 checkpoint。",
        artifacts=[artifact_ref],
    )


def _completion_eligibility(
    workspace: Path,
    goal_repo_path: Path,
    child_dir: Path,
    evidence_type: GoalCheckpointEvidenceType,
    status: str,
) -> tuple[bool, str]:
    freshness = _run_evidence_freshness(
        workspace,
        goal_repo_path,
        child_dir,
        evidence_type,
    )
    freshness_summary = (
        "fresh"
        if freshness.fresh
        else f"stale({','.join(freshness.issues)})"
    )
    if evidence_type == "loop":
        eligible = status == "success" and freshness.fresh
        return eligible, f"loop status={status}, evidence={freshness_summary}"
    if evidence_type == "reflect":
        return (
            False,
            f"reflect status={status}, evidence={freshness_summary}；"
            "reflect 只能证明已复盘，不能单独证明 checkpoint 完成",
        )
    if evidence_type == "gate":
        _, result, gate_issues = _gate_artifact_integrity(child_dir, goal_repo_path)
        recommendation = result.recommendation if result else "unknown"
        gate_issue_summary = f"({','.join(gate_issues)})" if gate_issues else ""
        eligible = (
            status == "success"
            and recommendation == "self-check"
            and freshness.fresh
            and not gate_issues
        )
        return (
            eligible,
            f"gate status={status}, recommendation={recommendation}, "
            f"artifact_integrity={'valid' if not gate_issues else 'invalid'}"
            f"{gate_issue_summary}, "
            f"evidence={freshness_summary}",
        )
    if evidence_type == "review":
        payload = _read_json(child_dir / "review-verdict.json")
        verdict = str(payload.get("verdict") or "unknown")
        eligible = status == "success" and verdict == "approve" and freshness.fresh
        return (
            eligible,
            f"review status={status}, verdict={verdict}, evidence={freshness_summary}",
        )
    if evidence_type == "finish":
        payload = _read_json(child_dir / "finish-summary.json")
        finish_status = str(payload.get("finish_status") or "missing")
        integrity = payload.get("artifact_integrity")
        integrity_valid = isinstance(integrity, dict) and integrity.get("valid") is True
        finish_issues = _finish_summary_integrity(
            workspace,
            child_dir,
            goal_repo_path,
            status,
            payload,
            evidence_fresh=freshness.fresh,
        )
        finish_issue_summary = f"({','.join(finish_issues)})" if finish_issues else ""
        eligible = (
            status == "success"
            and finish_status == "ready_to_commit"
            and freshness.fresh
            and integrity_valid
            and not finish_issues
        )
        return (
            eligible,
            f"loop status={status}, finish_status={finish_status}, "
            f"artifact_integrity={'valid' if integrity_valid else 'invalid'}, "
            f"finish_identity={'valid' if not finish_issues else 'invalid'}"
            f"{finish_issue_summary}, "
            f"evidence={freshness_summary}",
        )
    return False, "未知证据类型"


def _run_evidence_freshness(
    workspace: Path,
    goal_repo_path: Path,
    child_dir: Path,
    evidence_type: GoalCheckpointEvidenceType,
) -> EvidenceFreshness:
    if evidence_type in {"loop", "finish"}:
        return validate_loop_evidence_freshness(
            workspace,
            goal_repo_path,
            child_dir,
        )
    if evidence_type == "review":
        return validate_review_evidence_freshness(
            workspace,
            goal_repo_path,
            child_dir.name,
        )
    if evidence_type == "reflect":
        return validate_reflect_evidence_freshness(
            workspace,
            goal_repo_path,
            child_dir.name,
        )
    if evidence_type == "gate":
        gate_state, _, gate_issues = _gate_artifact_integrity(
            child_dir,
            goal_repo_path,
        )
        source_run = gate_state.source_run if gate_state else ""
        if not source_run:
            return _freshness(
                [*gate_issues, "gate_source_missing"],
                "",
            )
        source_freshness = validate_reflect_evidence_freshness(
            workspace,
            goal_repo_path,
            source_run,
        )
        return _freshness(
            [*gate_issues, *source_freshness.issues],
            source_freshness.current_workspace_fingerprint,
            trusted_workspace_fingerprint=source_freshness.trusted_workspace_fingerprint,
            source_run=source_freshness.source_run,
            snapshot_id=source_freshness.snapshot_id,
        )
    return _freshness(["unsupported_evidence_type"], "")


def _gate_artifact_integrity(
    gate_dir: Path,
    repo_path: Path,
) -> tuple[GateState | None, GateResult | None, list[str]]:
    issues: list[str] = []
    state_payload, state_issue = _load_json_object(gate_dir / "state.json")
    result_payload, result_issue = _load_json_object(gate_dir / "gate-result.json")
    if state_issue:
        issues.append(f"gate_state_{state_issue}")
    if result_issue:
        issues.append(f"gate_result_{result_issue}")

    state: GateState | None = None
    result: GateResult | None = None
    if state_payload is not None:
        try:
            state = GateState.model_validate(state_payload)
        except ValidationError:
            issues.append("gate_state_schema_invalid")
    if result_payload is not None:
        try:
            result = GateResult.model_validate(result_payload)
        except ValidationError:
            issues.append("gate_result_schema_invalid")

    if state:
        if state.run_id != gate_dir.name:
            issues.append("gate_run_id_mismatch")
        if _normalized_path(state.repo_path) != _normalized_path(repo_path):
            issues.append("gate_repo_mismatch")
        if not state.source_run:
            issues.append("gate_source_missing")
    if result_payload is not None:
        identity_keys = {"run_id", "repo_path", "source_run"}
        present_identity_keys = identity_keys.intersection(result_payload)
        if present_identity_keys:
            if present_identity_keys != identity_keys:
                issues.append("gate_result_identity_incomplete")
            if str(result_payload.get("run_id") or "") != gate_dir.name:
                issues.append("gate_result_run_id_mismatch")
            result_repo = str(result_payload.get("repo_path") or "")
            if not result_repo or _normalized_path(result_repo) != _normalized_path(repo_path):
                issues.append("gate_result_repo_mismatch")
            if state and result_payload.get("source_run") != state.source_run:
                issues.append("gate_result_source_mismatch")
    if state and result:
        if result.risk != state.risk:
            issues.append("gate_result_risk_mismatch")
        if result.recommendation != state.recommendation:
            issues.append("gate_result_recommendation_mismatch")
        if result.changed_files != state.changed_files:
            issues.append("gate_result_changed_files_mismatch")
        if result.scope_profile != state.scope_profile:
            issues.append("gate_result_scope_profile_mismatch")
    return state, result, list(dict.fromkeys(issues))


def _finish_summary_integrity(
    workspace: Path,
    loop_dir: Path,
    repo_path: Path,
    loop_status: str,
    payload: dict[str, Any],
    *,
    evidence_fresh: bool,
) -> list[str]:
    issues: list[str] = []
    if str(payload.get("run_id") or "") != loop_dir.name:
        issues.append("finish_run_id_mismatch")
    finish_repo = str(payload.get("repo_path") or "")
    if not finish_repo or _normalized_path(finish_repo) != _normalized_path(repo_path):
        issues.append("finish_repo_mismatch")
    finish_run_dir = str(payload.get("run_dir") or "")
    if not finish_run_dir or _normalized_path(finish_run_dir) != _normalized_path(loop_dir):
        issues.append("finish_run_dir_mismatch")
    if str(payload.get("loop_status") or "") != loop_status:
        issues.append("finish_loop_status_mismatch")
    state, state_issue = _load_loop_state(loop_dir / "state.json")
    if state_issue:
        issues.append(f"finish_loop_state_{state_issue}")
        return issues
    assert state is not None
    trusted_integrity = validate_loop_artifact_integrity(
        workspace,
        repo_path,
        loop_dir,
        state=state,
    )
    declared_integrity = payload.get("artifact_integrity")
    if not isinstance(declared_integrity, dict):
        issues.append("finish_artifact_integrity_missing")
    else:
        if declared_integrity.get("valid") is not trusted_integrity.valid:
            issues.append("finish_artifact_integrity_mismatch")
        if _int_or_none(declared_integrity.get("review_verdict_count")) != len(
            trusted_integrity.review_verdicts
        ):
            issues.append("finish_review_verdict_count_mismatch")
        if _int_or_none(declared_integrity.get("verification_result_count")) != len(
            trusted_integrity.verification_results
        ):
            issues.append("finish_verification_result_count_mismatch")
        if _int_or_none(declared_integrity.get("risk_gate_result_count")) != len(
            trusted_integrity.risk_gate_results
        ):
            issues.append("finish_risk_gate_result_count_mismatch")
    has_verification_failures = any(
        (item.get("failed_count") or 0) > 0
        for item in trusted_integrity.verification_results
    )
    latest_verdict = (
        trusted_integrity.review_verdicts[-1].verdict
        if trusted_integrity.review_verdicts
        else None
    )
    expected_finish_status = _trusted_finish_status(
        loop_status,
        latest_verdict,
        has_verification_failures,
        evidence_fresh=evidence_fresh,
        artifact_integrity_valid=trusted_integrity.valid,
    )
    if str(payload.get("finish_status") or "missing") != expected_finish_status:
        issues.append("finish_status_mismatch")
    return issues


def _expected_kind(evidence_type: GoalCheckpointEvidenceType) -> str:
    if evidence_type == "finish":
        return "loop"
    return evidence_type


def _trusted_finish_status(
    loop_status: str,
    latest_verdict: str | None,
    has_verification_failures: bool,
    *,
    evidence_fresh: bool,
    artifact_integrity_valid: bool,
) -> str:
    if not artifact_integrity_valid:
        return "needs_human"
    if not evidence_fresh:
        return "needs_human"
    if has_verification_failures:
        return "needs_fix"
    if loop_status == "success" and latest_verdict == "approve":
        return "ready_to_commit"
    if latest_verdict == "request_changes":
        return "needs_fix"
    if loop_status in {"failed", "needs_human"}:
        return "needs_human"
    return "incomplete"


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _manual_artifact_ref(workspace: Path, goal_repo_path: Path, candidate: Path) -> str:
    resolved = candidate.resolve()
    repo = goal_repo_path.resolve()
    workspace_root = workspace.resolve()
    if _is_within(resolved, repo):
        return resolved.relative_to(repo).as_posix()
    if _is_within(resolved, workspace_root):
        return resolved.relative_to(workspace_root).as_posix()
    return "<manual-evidence>"


def _resolve_manual_path(workspace: Path, goal_repo_path: Path, reference: str) -> Path:
    raw = Path(reference)
    candidates = [raw] if raw.is_absolute() else [goal_repo_path / raw, workspace / raw]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[0].resolve()


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _validate_iteration_review(
    workspace: Path,
    repo_path: Path,
    iteration_dir: Path,
    iteration: LoopIterationState,
    issues: list[str],
    verdicts: list[ReviewVerdict],
) -> None:
    prefix = f"iteration_{iteration.iteration:02d}"
    local_verdict_path = iteration_dir / "review-verdict.json"
    if iteration.verdict is None:
        if local_verdict_path.exists():
            issues.append(f"{prefix}_review_verdict_unexpected")
        return
    review_issue_start = len(issues)
    local_verdict, local_verdict_issue = _load_review_verdict(local_verdict_path)
    if local_verdict_issue:
        issues.append(f"{prefix}_local_review_verdict_{local_verdict_issue}")
    if local_verdict:
        if local_verdict.verdict != iteration.verdict:
            issues.append(f"{prefix}_local_review_verdict_mismatch")
        if len(local_verdict.findings) != iteration.findings_count:
            issues.append(f"{prefix}_local_review_findings_count_mismatch")
    if not iteration.review_run:
        issues.append(f"{prefix}_review_run_missing")
        return

    try:
        review_dir = resolve_run_dir(workspace, iteration.review_run)
    except (FileNotFoundError, ValueError):
        issues.append(f"{prefix}_review_run_unavailable")
        return

    child_state_path = review_dir / "state.json"
    child_context_path = review_dir / "review-context.json"
    child_verdict_path = review_dir / "review-verdict.json"
    child_state, child_state_issue = _load_review_state(child_state_path)
    child_context, child_context_issue = _load_json_object(child_context_path)
    child_verdict, child_verdict_issue = _load_review_verdict(child_verdict_path)
    if child_state_issue:
        issues.append(f"{prefix}_child_review_state_{child_state_issue}")
    if child_context_issue:
        issues.append(f"{prefix}_child_review_context_{child_context_issue}")
    if child_verdict_issue:
        issues.append(f"{prefix}_child_review_verdict_{child_verdict_issue}")

    if child_state:
        if child_state.run_id != review_dir.name:
            issues.append(f"{prefix}_child_review_run_id_mismatch")
        if _normalized_path(child_state.repo_path) != _normalized_path(repo_path):
            issues.append(f"{prefix}_child_review_repo_mismatch")
        if child_state.source_run != iteration.reflect_run:
            issues.append(f"{prefix}_child_review_source_mismatch")
        if child_state.runner_status != iteration.reviewer_status:
            issues.append(f"{prefix}_child_review_runner_status_mismatch")
        if not _review_state_allows_verdict(child_state, iteration.verdict):
            issues.append(f"{prefix}_child_review_state_not_trusted")
    if child_context:
        if child_context.get("source_run") != iteration.reflect_run:
            issues.append(f"{prefix}_child_review_context_source_mismatch")
        child_context_repo = child_context.get("repo_path")
        if (
            not isinstance(child_context_repo, str)
            or _normalized_path(child_context_repo) != _normalized_path(repo_path)
        ):
            issues.append(f"{prefix}_child_review_context_repo_mismatch")
    if child_verdict and child_verdict.verdict != iteration.verdict:
        issues.append(f"{prefix}_child_review_verdict_mismatch")

    for local_name, child_path, issue_name in (
        ("review-state.json", child_state_path, "review_state_hash_mismatch"),
        ("review-context.json", child_context_path, "review_context_hash_mismatch"),
        ("review-verdict.json", child_verdict_path, "review_verdict_hash_mismatch"),
    ):
        local_path = iteration_dir / local_name
        if not local_path.exists():
            issues.append(f"{prefix}_local_{local_name.replace('.', '_')}_missing")
        elif child_path.exists() and not _json_artifacts_equivalent(
            local_path,
            child_path,
        ):
            issues.append(f"{prefix}_{issue_name}")

    if local_verdict and len(issues) == review_issue_start:
        verdicts.append(local_verdict)


def _validate_interrupted_iteration(
    iteration_dir: Path,
    iteration: LoopIterationState,
) -> list[str]:
    prefix = f"iteration_{iteration.iteration:02d}"
    issues: list[str] = []
    if not iteration.interrupted_step:
        issues.append(f"{prefix}_interrupted_step_missing")
    if not iteration.interrupted_at:
        issues.append(f"{prefix}_interrupted_at_missing")

    report_path = iteration_dir / "interruption-report.md"
    if not report_path.is_file():
        issues.append(f"{prefix}_interruption_report_missing")
        return issues
    try:
        report = report_path.read_text(encoding="utf-8")
    except OSError:
        issues.append(f"{prefix}_interruption_report_unreadable")
        return issues
    if f"- 迭代：`{iteration.iteration}`" not in report:
        issues.append(f"{prefix}_interruption_report_iteration_mismatch")
    if (
        iteration.interrupted_step
        and f"- 原步骤：`{iteration.interrupted_step}`" not in report
    ):
        issues.append(f"{prefix}_interruption_report_step_mismatch")
    if iteration.interrupted_at and iteration.interrupted_at not in report:
        issues.append(f"{prefix}_interruption_report_time_mismatch")
    return issues


def _validate_iteration_verification(
    repo_path: Path,
    iteration_dir: Path,
    iteration: LoopIterationState,
    issues: list[str],
    results: list[dict[str, Any]],
    *,
    expected_artifact_version: int | None,
) -> None:
    prefix = f"iteration_{iteration.iteration:02d}"
    result_path = iteration_dir / "verification-result.json"
    if not result_path.exists():
        if iteration.verification_status in {"passed", "failed"}:
            issues.append(f"{prefix}_verification_result_missing")
        return

    verification_issue_start = len(issues)
    payload, payload_issue = _load_json_object(result_path)
    if payload_issue:
        issues.append(f"{prefix}_verification_result_{payload_issue}")
        return
    assert payload is not None
    commands = payload.get("commands")
    command_results = payload.get("results")
    command_count = payload.get("command_count")
    failed_count = payload.get("failed_count")
    artifact_version = payload.get("artifact_version")
    expected_run_id = iteration_dir.parent.parent.name
    recorded_run_id = payload.get("run_id")
    recorded_iteration = payload.get("iteration")
    shell_kind = payload.get("shell_kind")
    if expected_artifact_version is not None:
        if artifact_version != expected_artifact_version:
            issues.append(f"{prefix}_verification_artifact_version_invalid")
    elif artifact_version is not None and artifact_version != 2:
        issues.append(f"{prefix}_verification_artifact_version_invalid")
    if artifact_version == 2:
        if recorded_run_id != expected_run_id:
            issues.append(f"{prefix}_verification_run_id_mismatch")
        if recorded_iteration != iteration.iteration:
            issues.append(f"{prefix}_verification_iteration_binding_mismatch")
        if shell_kind not in {"cmd", "posix-sh"}:
            issues.append(f"{prefix}_verification_shell_kind_invalid")
    if not isinstance(payload.get("repo_path"), str):
        issues.append(f"{prefix}_verification_repo_missing")
    elif _normalized_path(payload["repo_path"]) != _normalized_path(repo_path):
        issues.append(f"{prefix}_verification_repo_mismatch")
    if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
        issues.append(f"{prefix}_verification_commands_schema_invalid")
    if not isinstance(command_results, list) or not all(
        isinstance(item, dict) for item in command_results
    ):
        issues.append(f"{prefix}_verification_results_schema_invalid")
    if not _is_non_negative_int(command_count):
        issues.append(f"{prefix}_verification_command_count_invalid")
    if not _is_non_negative_int(failed_count):
        issues.append(f"{prefix}_verification_failed_count_invalid")

    if isinstance(commands, list) and _is_non_negative_int(command_count):
        if command_count != len(commands):
            issues.append(f"{prefix}_verification_command_count_mismatch")
    if isinstance(command_results, list) and _is_non_negative_int(command_count):
        if command_count != len(command_results):
            issues.append(f"{prefix}_verification_result_count_mismatch")
    if isinstance(commands, list) and isinstance(command_results, list):
        for index, item in enumerate(command_results):
            if not isinstance(item, dict):
                continue
            if index >= len(commands) or item.get("command") != commands[index]:
                issues.append(f"{prefix}_verification_command_binding_mismatch")
                break
            if item.get("status") not in {"passed", "failed", "timeout"}:
                issues.append(f"{prefix}_verification_result_status_invalid")
                break
            if artifact_version == 2:
                _validate_versioned_verification_result(
                    iteration_dir,
                    iteration,
                    index,
                    commands[index],
                    item,
                    expected_run_id,
                    shell_kind if isinstance(shell_kind, str) else "",
                    issues,
                )
    if isinstance(command_results, list) and _is_non_negative_int(failed_count):
        actual_failed_count = sum(
            1
            for item in command_results
            if not isinstance(item, dict) or item.get("status") != "passed"
        )
        if failed_count != actual_failed_count:
            issues.append(f"{prefix}_verification_failed_count_mismatch")
    if _is_non_negative_int(failed_count):
        if failed_count != iteration.verification_failed_count:
            issues.append(f"{prefix}_verification_iteration_failed_count_mismatch")
    if _is_non_negative_int(command_count) and _is_non_negative_int(failed_count):
        expected_status = _verification_status(command_count, failed_count)
        if iteration.verification_status != expected_status:
            issues.append(f"{prefix}_verification_iteration_status_mismatch")
    if iteration.verification_status in {"passed", "failed"}:
        for filename in ("verification-summary.md", "test-summary.md"):
            if not (iteration_dir / filename).is_file():
                issues.append(f"{prefix}_{filename.replace('.', '_')}_missing")

    if len(issues) == verification_issue_start:
        trusted_payload = dict(payload)
        trusted_payload["path"] = str(result_path.resolve())
        results.append(trusted_payload)


def _validate_versioned_verification_result(
    iteration_dir: Path,
    iteration: LoopIterationState,
    index: int,
    configured_command: str,
    item: dict[str, Any],
    run_id: str,
    shell_kind: str,
    issues: list[str],
) -> None:
    prefix = f"iteration_{iteration.iteration:02d}"
    command_index = index + 1
    if item.get("command_index") != command_index:
        issues.append(f"{prefix}_verification_command_index_mismatch")
    if item.get("configured_command") != configured_command:
        issues.append(f"{prefix}_verification_configured_command_binding_mismatch")

    expected_executed_command = ""
    if shell_kind in {"cmd", "posix-sh"}:
        try:
            expected_executed_command = render_verification_command(
                configured_command,
                shell_kind,
            )
        except ValueError:
            issues.append(f"{prefix}_verification_executed_command_unrenderable")
        else:
            if item.get("executed_command") != expected_executed_command:
                issues.append(f"{prefix}_verification_executed_command_binding_mismatch")

    expected_temp = None
    if VERIFICATION_TEMP_PLACEHOLDER in configured_command:
        expected_temp = (
            VERIFICATION_TEMP_ROOT
            / run_id
            / f"iteration-{iteration.iteration}"
            / f"command-{command_index}"
        ).as_posix()
    if item.get("verification_temp") != expected_temp:
        issues.append(f"{prefix}_verification_temp_path_mismatch")

    execution_path = (
        iteration_dir
        / "executions"
        / f"verification-{command_index:02d}"
        / "execution.json"
    )
    execution_payload, execution_issue = _load_json_object(execution_path)
    if execution_issue:
        issues.append(f"{prefix}_verification_execution_{execution_issue}")
        return
    assert execution_payload is not None
    try:
        execution = ExecutionLease.model_validate(execution_payload)
    except ValidationError:
        issues.append(f"{prefix}_verification_execution_schema_invalid")
        return

    if execution.run_id != run_id:
        issues.append(f"{prefix}_verification_execution_run_id_mismatch")
    if execution.step != "verification":
        issues.append(f"{prefix}_verification_execution_step_mismatch")
    if execution.iteration != iteration.iteration:
        issues.append(f"{prefix}_verification_execution_iteration_mismatch")
    if execution.returncode != item.get("returncode"):
        issues.append(f"{prefix}_verification_execution_returncode_mismatch")
    expected_statuses = {
        "passed": {"completed"},
        "failed": {"failed", "stopped"},
        "timeout": {"timed_out"},
    }.get(item.get("status"), set())
    if execution.status not in expected_statuses:
        issues.append(f"{prefix}_verification_execution_status_mismatch")
    if expected_executed_command:
        expected_process_command = build_verification_shell_command(
            expected_executed_command,
            shell_kind,
        )
        expected_command_parts = (
            [expected_process_command]
            if isinstance(expected_process_command, str)
            else expected_process_command
        )
        if execution.command != expected_command_parts:
            issues.append(f"{prefix}_verification_execution_command_mismatch")


def _load_loop_state(path: Path) -> tuple[LoopAutomationState | None, str | None]:
    payload, issue = _load_json_object(path)
    if issue:
        return None, issue
    assert payload is not None
    if "automation_mode" not in payload:
        return None, "not_loop"
    try:
        return LoopAutomationState.model_validate(payload), None
    except ValidationError:
        return None, "schema_invalid"


def _load_review_state(path: Path) -> tuple[ReviewState | None, str | None]:
    payload, issue = _load_json_object(path)
    if issue:
        return None, issue
    assert payload is not None
    try:
        return ReviewState.model_validate(payload), None
    except ValidationError:
        return None, "schema_invalid"


def _load_review_verdict(path: Path) -> tuple[ReviewVerdict | None, str | None]:
    payload, issue = _load_json_object(path)
    if issue:
        return None, issue
    assert payload is not None
    try:
        return ReviewVerdict.model_validate(payload), None
    except ValidationError:
        return None, "schema_invalid"


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, "unreadable"
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "schema_invalid"
    return payload, None


def _review_state_allows_verdict(state: ReviewState, verdict: str) -> bool:
    if state.verdict != verdict:
        return False
    if state.status == "success":
        return verdict == "approve"
    return state.status == "needs_human" and verdict in {
        "request_changes",
        "needs_human",
    }


def _verification_status(command_count: int, failed_count: int) -> str:
    if command_count == 0:
        return "skipped"
    if failed_count:
        return "failed"
    return "passed"


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _artifact_integrity(
    issues: list[str],
    *,
    review_verdicts: list[ReviewVerdict] | None = None,
    verification_results: list[dict[str, Any]] | None = None,
    risk_gate_results: list[GateResult] | None = None,
) -> LoopArtifactIntegrity:
    unique_issues = tuple(dict.fromkeys(issues))
    return LoopArtifactIntegrity(
        valid=not unique_issues,
        issues=unique_issues,
        review_verdicts=tuple(review_verdicts or []),
        verification_results=tuple(verification_results or []),
        risk_gate_results=tuple(risk_gate_results or []),
    )


def _json_artifacts_equivalent(first: Path, second: Path) -> bool:
    first_payload, first_issue = _load_json_object(first)
    second_payload, second_issue = _load_json_object(second)
    if first_issue or second_issue:
        return False
    return _sha256_json(first_payload or {}) == _sha256_json(second_payload or {})


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _validate_text_artifact_hash(
    path: Path,
    evidence: dict[str, Any],
    hash_key: str,
    issue_prefix: str,
    issues: list[str],
) -> str:
    if not path.is_file():
        issues.append(f"{issue_prefix}_missing")
        text = ""
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            issues.append(f"{issue_prefix}_unreadable")
            text = ""
    if str(evidence.get(hash_key) or "") != _sha256_text(text):
        issues.append(f"{issue_prefix}_hash_mismatch")
    return text


def _load_source_brief_artifact(
    workspace: Path,
    repo_path: Path,
    source_run: object,
    issues: list[str],
) -> str:
    if source_run is None:
        return ""
    if not isinstance(source_run, str) or not source_run.strip():
        issues.append("source_brief_run_invalid")
        return ""
    try:
        source_dir = resolve_run_dir(workspace, source_run)
    except (FileNotFoundError, ValueError):
        issues.append("source_brief_run_invalid")
        return ""

    source_state = _read_json(source_dir / "state.json")
    if str(source_state.get("run_id") or "") != source_dir.name:
        issues.append("source_brief_run_id_mismatch")
    source_repo = str(source_state.get("repo_path") or "")
    if not source_repo or _normalized_path(source_repo) != _normalized_path(repo_path):
        issues.append("source_brief_repo_mismatch")
    if source_state.get("status") != "success":
        issues.append("source_brief_state_not_success")

    source_brief_path = source_dir / "agent-brief.md"
    if not source_brief_path.is_file():
        issues.append("source_brief_missing")
        return ""
    try:
        return source_brief_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        issues.append("source_brief_unreadable")
        return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _current_workspace_fingerprint(
    repo_path: Path,
    current_workspace_fingerprint: str | None,
) -> tuple[str, list[str]]:
    if current_workspace_fingerprint is not None:
        return current_workspace_fingerprint, []
    try:
        return capture_review_workspace(repo_path).fingerprint, []
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return "", ["workspace_snapshot_failed"]


def _freshness(
    issues: list[str],
    current_workspace_fingerprint: str,
    *,
    trusted_workspace_fingerprint: str = "",
    source_run: str = "",
    review_run: str = "",
    snapshot_id: str = "",
) -> EvidenceFreshness:
    unique_issues = tuple(dict.fromkeys(issues))
    return EvidenceFreshness(
        fresh=not unique_issues,
        issues=unique_issues,
        current_workspace_fingerprint=current_workspace_fingerprint,
        trusted_workspace_fingerprint=trusted_workspace_fingerprint,
        source_run=source_run,
        review_run=review_run,
        snapshot_id=snapshot_id,
    )


def _sha256_json(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
