from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..loop_evidence import (
    _load_json_object,
    _load_loop_state,
    _normalized_path,
    latest_verification_failed,
    trusted_verification_passed,
    validate_loop_artifact_integrity,
)
from ..models import GateResult, GateState


def validate_gate_artifact_integrity(
    gate_dir: Path,
    repo_path: Path,
) -> tuple[GateState | None, GateResult | None, list[str]]:
    """校验 Goal 引用的 Gate artifact，避免只凭状态字段宣布 checkpoint 可完成。"""

    state_payload, result_payload, issues = _load_gate_payloads(gate_dir)
    state = _parse_gate_state(state_payload, issues)
    result = _parse_gate_result(result_payload, issues)
    issues.extend(_gate_state_identity_issues(state, gate_dir, repo_path))
    issues.extend(_gate_result_identity_issues(result_payload, state, gate_dir, repo_path))
    issues.extend(_gate_model_consistency_issues(state, result))
    return state, result, list(dict.fromkeys(issues))


def validate_finish_summary_integrity(
    workspace: Path,
    loop_dir: Path,
    repo_path: Path,
    loop_status: str,
    payload: dict[str, Any],
    *,
    evidence_fresh: bool,
) -> list[str]:
    """重新计算 Finish 结论，防止 Goal 信任可单独篡改的摘要字段。"""

    issues = _finish_identity_issues(payload, loop_dir, repo_path, loop_status)
    state, state_issue = _load_loop_state(loop_dir / "state.json")
    if state_issue:
        return [*issues, f"finish_loop_state_{state_issue}"]
    assert state is not None
    trusted_integrity = validate_loop_artifact_integrity(
        workspace,
        repo_path,
        loop_dir,
        state=state,
    )
    issues.extend(_declared_integrity_issues(payload, trusted_integrity))
    latest_verdict = (
        trusted_integrity.review_verdicts[-1].verdict
        if trusted_integrity.review_verdicts
        else None
    )
    expected_status = _trusted_finish_status(
        loop_status,
        latest_verdict,
        latest_verification_failed(state, trusted_integrity),
        verification_passed=trusted_verification_passed(state, trusted_integrity),
        evidence_fresh=evidence_fresh,
        artifact_integrity_valid=trusted_integrity.valid,
    )
    if str(payload.get("finish_status") or "missing") != expected_status:
        issues.append("finish_status_mismatch")
    return issues


def _load_gate_payloads(gate_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    state_payload, state_issue = _load_json_object(gate_dir / "state.json")
    result_payload, result_issue = _load_json_object(gate_dir / "gate-result.json")
    issues = [
        f"{kind}_{issue}"
        for kind, issue in (("gate_state", state_issue), ("gate_result", result_issue))
        if issue
    ]
    return state_payload, result_payload, issues


def _parse_gate_state(payload: dict[str, Any] | None, issues: list[str]) -> GateState | None:
    if payload is None:
        return None
    try:
        return GateState.model_validate(payload)
    except ValidationError:
        issues.append("gate_state_schema_invalid")
        return None


def _parse_gate_result(payload: dict[str, Any] | None, issues: list[str]) -> GateResult | None:
    if payload is None:
        return None
    try:
        return GateResult.model_validate(payload)
    except ValidationError:
        issues.append("gate_result_schema_invalid")
        return None


def _gate_state_identity_issues(
    state: GateState | None,
    gate_dir: Path,
    repo_path: Path,
) -> list[str]:
    if state is None:
        return []
    checks = (
        ("gate_run_id_mismatch", state.run_id != gate_dir.name),
        ("gate_repo_mismatch", _normalized_path(state.repo_path) != _normalized_path(repo_path)),
        ("gate_source_missing", not state.source_run),
    )
    return [issue for issue, invalid in checks if invalid]


def _gate_result_identity_issues(
    payload: dict[str, Any] | None,
    state: GateState | None,
    gate_dir: Path,
    repo_path: Path,
) -> list[str]:
    if payload is None:
        return []
    identity_keys = {"run_id", "repo_path", "source_run"}
    present_keys = identity_keys.intersection(payload)
    if not present_keys:
        return []
    result_repo = str(payload.get("repo_path") or "")
    checks = (
        ("gate_result_identity_incomplete", present_keys != identity_keys),
        ("gate_result_run_id_mismatch", str(payload.get("run_id") or "") != gate_dir.name),
        (
            "gate_result_repo_mismatch",
            not result_repo or _normalized_path(result_repo) != _normalized_path(repo_path),
        ),
        ("gate_result_source_mismatch", state is not None and payload.get("source_run") != state.source_run),
    )
    return [issue for issue, invalid in checks if invalid]


def _gate_model_consistency_issues(
    state: GateState | None,
    result: GateResult | None,
) -> list[str]:
    if state is None or result is None:
        return []
    checks = (
        ("gate_result_risk_mismatch", result.risk != state.risk),
        ("gate_result_recommendation_mismatch", result.recommendation != state.recommendation),
        ("gate_result_changed_files_mismatch", result.changed_files != state.changed_files),
        ("gate_result_scope_profile_mismatch", result.scope_profile != state.scope_profile),
    )
    return [issue for issue, invalid in checks if invalid]


def _finish_identity_issues(
    payload: dict[str, Any],
    loop_dir: Path,
    repo_path: Path,
    loop_status: str,
) -> list[str]:
    finish_repo = str(payload.get("repo_path") or "")
    finish_run_dir = str(payload.get("run_dir") or "")
    checks = (
        ("finish_run_id_mismatch", str(payload.get("run_id") or "") != loop_dir.name),
        (
            "finish_repo_mismatch",
            not finish_repo or _normalized_path(finish_repo) != _normalized_path(repo_path),
        ),
        (
            "finish_run_dir_mismatch",
            not finish_run_dir or _normalized_path(finish_run_dir) != _normalized_path(loop_dir),
        ),
        ("finish_loop_status_mismatch", str(payload.get("loop_status") or "") != loop_status),
    )
    return [issue for issue, invalid in checks if invalid]


def _declared_integrity_issues(payload: dict[str, Any], trusted_integrity: Any) -> list[str]:
    declared = payload.get("artifact_integrity")
    if not isinstance(declared, dict):
        return ["finish_artifact_integrity_missing"]
    checks = (
        ("finish_artifact_integrity_mismatch", declared.get("valid") is not trusted_integrity.valid),
        (
            "finish_review_verdict_count_mismatch",
            _int_or_none(declared.get("review_verdict_count"))
            != len(trusted_integrity.review_verdicts),
        ),
        (
            "finish_verification_result_count_mismatch",
            _int_or_none(declared.get("verification_result_count"))
            != len(trusted_integrity.verification_results),
        ),
        (
            "finish_risk_gate_result_count_mismatch",
            _int_or_none(declared.get("risk_gate_result_count"))
            != len(trusted_integrity.risk_gate_results),
        ),
    )
    return [issue for issue, invalid in checks if invalid]


def _trusted_finish_status(
    loop_status: str,
    latest_verdict: str | None,
    has_verification_failures: bool,
    *,
    verification_passed: bool,
    evidence_fresh: bool,
    artifact_integrity_valid: bool,
) -> str:
    if not artifact_integrity_valid:
        return "needs_human"
    if not evidence_fresh:
        return "needs_human"
    if has_verification_failures:
        return "needs_fix"
    if not verification_passed:
        return "needs_human"
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
