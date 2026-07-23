from __future__ import annotations

from pathlib import Path
from ..loop_evidence import (
    EvidenceFreshness,
    _freshness,
    _load_loop_state,
    _normalized_path,
    _read_json,
    trusted_verification_passed,
    validate_loop_artifact_integrity,
    validate_loop_evidence_freshness,
    validate_reflect_evidence_freshness,
    validate_review_evidence_freshness,
)
from ..models import GoalCheckpointEvidenceType, GoalCheckpointRef
from ..redaction import sensitive_path_reason
from ..run_status import run_status_payload
from ..run_utils import resolve_run_dir
from .goal_integrity import (
    validate_finish_summary_integrity,
    validate_gate_artifact_integrity,
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
        integrity = validate_loop_artifact_integrity(
            workspace,
            goal_repo_path,
            child_dir,
        )
        state, state_issue = _load_loop_state(child_dir / "state.json")
        verification_passed = (
            state is not None
            and state_issue is None
            and trusted_verification_passed(state, integrity)
        )
        eligible = (
            status == "success"
            and freshness.fresh
            and integrity.valid
            and verification_passed
        )
        return (
            eligible,
            f"loop status={status}, "
            f"artifact_integrity={'valid' if integrity.valid else 'invalid'}, "
            f"verification={'passed' if verification_passed else 'unverified'}, "
            f"evidence={freshness_summary}",
        )
    if evidence_type == "reflect":
        return (
            False,
            f"reflect status={status}, evidence={freshness_summary}；"
            "reflect 只能证明已复盘，不能单独证明 checkpoint 完成",
        )
    if evidence_type == "gate":
        _, result, gate_issues = validate_gate_artifact_integrity(child_dir, goal_repo_path)
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
        finish_issues = validate_finish_summary_integrity(
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
        gate_state, _, gate_issues = validate_gate_artifact_integrity(
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


def _expected_kind(evidence_type: GoalCheckpointEvidenceType) -> str:
    if evidence_type == "finish":
        return "loop"
    return evidence_type


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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
