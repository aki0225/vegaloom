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
from .loop_integrity import (
    LoopArtifactIntegrity,
    validate_verification_workspace_fingerprint,
    validate_project_config_failure_payload,
    validate_verification_failure_kind_schema,
    validated_review_workspace_fingerprint,
)
from .worker_rerun import worker_rerun_binding_issues
from .models import (
    GateResult,
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
from .risk_gate_evidence import validate_iteration_risk_gate_artifacts
from .review_evidence import review_evidence_schema_issues
from .risk_review_evidence import disclosure_issues, gate_result_semantics
from .run_utils import resolve_run_dir
from .runtime_workspace import capture_runtime_workspace
from .scope_gate import validate_iteration_scope_gate_artifacts
from .workspace_check import ReviewWorkspaceSnapshot


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
class LoopEvidenceValidationSnapshot:
    artifact_integrity: LoopArtifactIntegrity
    evidence_freshness: EvidenceFreshness


def validate_reflect_evidence_freshness(
    workspace: Path,
    repo_path: Path,
    source_run: str,
    *,
    current_workspace_snapshot: ReviewWorkspaceSnapshot | None = None,
) -> EvidenceFreshness:
    repo = repo_path.resolve()
    source_dir = resolve_run_dir(workspace, source_run)
    state = _read_json(source_dir / "state.json")
    evidence = _read_json(source_dir / "review-evidence.json")
    current_snapshot, snapshot_issues = _capture_current_workspace_snapshot(
        workspace, repo, current_workspace_snapshot
    )
    current_fingerprint = current_snapshot.fingerprint if current_snapshot else ""
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

    issues.extend(
        review_evidence_schema_issues(
            evidence,
            current_snapshot,
        )
    )
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
    current_workspace_snapshot: ReviewWorkspaceSnapshot | None = None,
) -> EvidenceFreshness:
    repo = repo_path.resolve()
    review_dir = resolve_run_dir(workspace, review_run)
    state, state_issue = _load_review_state(review_dir / "state.json")
    context, context_issue = _load_json_object(review_dir / "review-context.json")
    verdict, verdict_issue = _load_review_verdict(review_dir / "review-verdict.json")
    current_snapshot, snapshot_issues = _capture_current_workspace_snapshot(
        workspace,
        repo,
        current_workspace_snapshot,
    )
    current_fingerprint = current_snapshot.fingerprint if current_snapshot else ""
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
                current_workspace_snapshot=current_snapshot,
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
            verdict,
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
    verdict: ReviewVerdict | None,
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
    if gate_result_semantics(recorded_result) != gate_result_semantics(expected_result):
        issues.append("review_risk_gate_result_mismatch")
    issues.extend(disclosure_issues("review", expected_result, verdict))


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
    issues.extend(worker_rerun_binding_issues(loop_dir, state))

    verdicts: list[ReviewVerdict] = []
    verification_results: list[dict[str, Any]] = []
    risk_gate_results: list[GateResult] = []
    reviewed_workspace_fingerprints: dict[int, str] = {}
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
        reviewed_workspace_fingerprints[iteration.iteration] = _validate_iteration_review(
            workspace,
            repo_path,
            iteration_dir,
            iteration,
            gate_integrity.result,
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
        reviewed_workspace_fingerprint=reviewed_workspace_fingerprints.get(
            latest_iteration,
            "",
        ),
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
    evidence_freshness, _ = _validate_loop_evidence_freshness(
        workspace,
        repo,
        loop_dir,
    )
    return evidence_freshness


def validate_loop_evidence_snapshot(
    workspace: Path,
    repo_path: Path,
    loop_run: str | Path,
    *,
    state: LoopAutomationState | None = None,
) -> LoopEvidenceValidationSnapshot:
    repo = repo_path.resolve()
    loop_dir = (
        loop_run.resolve()
        if isinstance(loop_run, Path)
        else resolve_run_dir(workspace, loop_run)
    )
    evidence_freshness, artifact_integrity = _validate_loop_evidence_freshness(
        workspace,
        repo,
        loop_dir,
        state=state,
    )
    if artifact_integrity is None:
        artifact_integrity = validate_loop_artifact_integrity(
            workspace,
            repo,
            loop_dir,
            state=state,
        )
    return LoopEvidenceValidationSnapshot(
        artifact_integrity=artifact_integrity,
        evidence_freshness=evidence_freshness,
    )


def _validate_loop_evidence_freshness(
    workspace: Path,
    repo: Path,
    loop_dir: Path,
    *,
    state: LoopAutomationState | None = None,
) -> tuple[EvidenceFreshness, LoopArtifactIntegrity | None]:
    state_payload = (
        state.model_dump(mode="json")
        if state is not None
        else _read_json(loop_dir / "state.json")
    )
    current_snapshot, snapshot_issues = _capture_current_workspace_snapshot(
        workspace,
        repo,
        None,
    )
    current_fingerprint = current_snapshot.fingerprint if current_snapshot else ""
    issues = list(snapshot_issues)
    if str(state_payload.get("run_id") or "") != loop_dir.name:
        issues.append("loop_run_id_mismatch")
    loop_repo = str(state_payload.get("repo_path") or "")
    if not loop_repo or _normalized_path(loop_repo) != _normalized_path(repo):
        issues.append("loop_repo_mismatch")
    iterations = state_payload.get("iterations") or []
    latest = iterations[-1] if iterations else {}
    review_run = str(latest.get("review_run") or "")
    reflect_run = str(latest.get("reflect_run") or "")
    if not review_run:
        issues.append("trusted_review_missing")
        return (
            _freshness(
                issues,
                current_fingerprint,
                source_run=reflect_run,
            ),
            None,
        )
    try:
        review_freshness = validate_review_evidence_freshness(
            workspace,
            repo,
            review_run,
            current_workspace_snapshot=current_snapshot,
        )
    except FileNotFoundError:
        issues.append("trusted_review_missing")
        return (
            _freshness(
                issues,
                current_fingerprint,
                source_run=reflect_run,
                review_run=review_run,
            ),
            None,
        )
    issues.extend(review_freshness.issues)
    if reflect_run != review_freshness.source_run:
        issues.append("loop_review_source_mismatch")
    artifact_integrity = validate_loop_artifact_integrity(
        workspace,
        repo,
        loop_dir,
        state=state,
    )
    issues.extend(artifact_integrity.issues)
    return (
        _freshness(
            issues,
            current_fingerprint,
            trusted_workspace_fingerprint=review_freshness.trusted_workspace_fingerprint,
            source_run=review_freshness.source_run,
            review_run=review_freshness.review_run,
            snapshot_id=review_freshness.snapshot_id,
        ),
        artifact_integrity,
    )


def _validate_iteration_review(
    workspace: Path,
    repo_path: Path,
    iteration_dir: Path,
    iteration: LoopIterationState,
    risk_gate_result: GateResult | None,
    issues: list[str],
    verdicts: list[ReviewVerdict],
) -> str:
    prefix = f"iteration_{iteration.iteration:02d}"
    local_verdict_path = iteration_dir / "review-verdict.json"
    if iteration.verdict is None:
        if local_verdict_path.exists():
            issues.append(f"{prefix}_review_verdict_unexpected")
        return ""
    review_issue_start = len(issues)
    local_verdict, local_verdict_issue = _load_review_verdict(local_verdict_path)
    if local_verdict_issue:
        issues.append(f"{prefix}_local_review_verdict_{local_verdict_issue}")
    if local_verdict:
        if local_verdict.verdict != iteration.verdict:
            issues.append(f"{prefix}_local_review_verdict_mismatch")
        if len(local_verdict.findings) != iteration.findings_count:
            issues.append(f"{prefix}_local_review_findings_count_mismatch")
        issues.extend(disclosure_issues(prefix, risk_gate_result, local_verdict))
    if not iteration.review_run:
        issues.append(f"{prefix}_review_run_missing")
        return ""

    try:
        review_dir = resolve_run_dir(workspace, iteration.review_run)
    except (FileNotFoundError, ValueError):
        issues.append(f"{prefix}_review_run_unavailable")
        return ""

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
    issues.extend(disclosure_issues(f"{prefix}_child", risk_gate_result, child_verdict))

    reviewed_workspace_fingerprint = validated_review_workspace_fingerprint(
        child_context,
        iteration.verdict,
        issues,
        prefix,
        risk_gate_result,
    )
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
        return reviewed_workspace_fingerprint
    return ""


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
    selected_command_count = payload.get("selected_command_count")
    skipped_commands = payload.get("skipped_commands")
    interruption_status = payload.get("interruption_status")
    failure_kind = payload.get("failure_kind")
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
        validate_verification_workspace_fingerprint(payload, prefix, issues)
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
    if artifact_version == 2:
        if not _is_non_negative_int(selected_command_count):
            issues.append(f"{prefix}_verification_selected_command_count_invalid")
        if not isinstance(skipped_commands, list) or not all(
            isinstance(item, str) for item in skipped_commands
        ):
            issues.append(f"{prefix}_verification_skipped_commands_schema_invalid")
        if interruption_status not in {
            None,
            "timed_out",
            "stopped",
            "termination-unconfirmed",
        }:
            issues.append(f"{prefix}_verification_interruption_status_invalid")
    validate_verification_failure_kind_schema(
        artifact_version,
        failure_kind,
        iteration.verification_failure_kind,
        prefix,
        issues,
    )

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
        expected_status = _verification_status(
            command_count,
            failed_count,
            failure_kind if isinstance(failure_kind, str) else None,
        )
        if iteration.verification_status != expected_status:
            issues.append(f"{prefix}_verification_iteration_status_mismatch")
    validate_project_config_failure_payload(
        payload,
        failure_kind,
        commands,
        command_results,
        command_count,
        failed_count,
        selected_command_count,
        skipped_commands,
        prefix,
        issues,
    )
    if iteration.verification_status == "passed" and artifact_version == 2:
        if selected_command_count != command_count:
            issues.append(f"{prefix}_verification_selected_command_count_mismatch")
        if skipped_commands != []:
            issues.append(f"{prefix}_verification_passed_with_skipped_commands")
        if any(
            payload.get(field) is not None
            for field in (
                "interruption_status",
                "interruption_command",
                "interruption_reason",
            )
        ):
            issues.append(f"{prefix}_verification_passed_with_interruption")
        if isinstance(command_results, list) and any(
            isinstance(item, dict)
            and (
                item.get("interruption_status") is not None
                or item.get("interruption_reason") is not None
            )
            for item in command_results
        ):
            issues.append(f"{prefix}_verification_passed_result_interrupted")
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


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _review_state_allows_verdict(state: ReviewState, verdict: str) -> bool:
    if state.verdict != verdict:
        return False
    if state.status == "success":
        return verdict == "approve"
    return state.status == "needs_human" and verdict in {
        "request_changes",
        "needs_human",
    }


def _verification_status(
    command_count: int,
    failed_count: int,
    failure_kind: str | None = None,
) -> str:
    if failure_kind is not None:
        return "failed"
    if command_count == 0:
        return "skipped"
    if failed_count:
        return "failed"
    return "passed"


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


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
    reviewed_workspace_fingerprint: str = "",
) -> LoopArtifactIntegrity:
    unique_issues = tuple(dict.fromkeys(issues))
    return LoopArtifactIntegrity(
        valid=not unique_issues,
        issues=unique_issues,
        review_verdicts=tuple(review_verdicts or []),
        verification_results=tuple(verification_results or []),
        risk_gate_results=tuple(risk_gate_results or []),
        reviewed_workspace_fingerprint=reviewed_workspace_fingerprint,
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


def _capture_current_workspace_snapshot(
    workspace: Path,
    repo_path: Path,
    current_workspace_snapshot: ReviewWorkspaceSnapshot | None,
) -> tuple[ReviewWorkspaceSnapshot | None, list[str]]:
    if current_workspace_snapshot is not None:
        return current_workspace_snapshot, []
    try:
        snapshot = capture_runtime_workspace(workspace, repo_path)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None, ["workspace_snapshot_failed"]
    return snapshot, []


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
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(serialized)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
