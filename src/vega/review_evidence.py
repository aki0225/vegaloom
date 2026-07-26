from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .redaction import redact_value
from .workspace_check import ReviewWorkspaceSnapshot

SUPPORTED_REVIEW_EVIDENCE_SCHEMAS = frozenset({2, 3, 4})


def make_review_evidence(
    workspace_snapshot: ReviewWorkspaceSnapshot,
    test_summary: str,
    full_diff: str,
    changed_files: list[str],
    *,
    review_source_run: str,
    upstream_source_run: str | None,
    source_brief: str,
    reflection: str,
    diff_summary: str,
    source_brief_issues: list[str],
    source_brief_diagnostics: list[str],
) -> dict[str, object]:
    evidence = redact_value(
        {
            "schema_version": 4,
            "captured_at": datetime.now(UTC).isoformat(),
            "source_run": review_source_run,
            "upstream_source_run": upstream_source_run,
            "workspace_fingerprint": workspace_snapshot.fingerprint,
            "head_sha": workspace_snapshot.head_sha,
            "status_sha256": workspace_snapshot.status_sha256,
            "full_diff_sha256": _sha256_text(full_diff),
            "staged_diff_sha256": workspace_snapshot.staged_diff_sha256,
            "unstaged_diff_sha256": workspace_snapshot.unstaged_diff_sha256,
            "untracked_manifest_sha256": workspace_snapshot.untracked_manifest_sha256,
            "untracked_content_complete": workspace_snapshot.untracked_content_complete,
            "ignored_manifest_sha256": workspace_snapshot.ignored_manifest_sha256,
            "ignored_content_complete": workspace_snapshot.ignored_content_complete,
            "git_control_sha256": workspace_snapshot.git_control_sha256,
            "git_control_complete": workspace_snapshot.git_control_complete,
            "test_summary_sha256": _sha256_text(test_summary),
            "changed_files": changed_files,
            "changed_files_sha256": _sha256_json_value(changed_files),
            "source_brief_sha256": _sha256_text(source_brief),
            "source_brief_evidence_issues": source_brief_issues,
            "source_brief_evidence_diagnostics": source_brief_diagnostics,
            "reflection_sha256": _sha256_text(reflection),
            "diff_summary_sha256": _sha256_text(diff_summary),
            "untracked_files": list(workspace_snapshot.untracked_files),
        }
    )
    evidence["snapshot_id"] = _sha256_json_value(evidence)
    return evidence


def review_evidence_issues(
    repo_path: Path,
    source_run: str,
    reflect_state: dict[str, Any],
    source_evidence: dict[str, Any],
    source_brief: str,
    reflection: str,
    diff_summary: str,
    full_diff: str,
    test_summary: str,
    current_snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues = _initial_evidence_issues(
        source_run,
        reflect_state,
        source_evidence,
        full_diff,
        current_snapshot,
    )
    if not source_evidence:
        return _unique(issues)

    schema_version = source_evidence.get("schema_version")
    issues.extend(_schema_issues(schema_version, source_evidence, current_snapshot))
    issues.extend(
        _source_identity_issues(
            repo_path,
            source_run,
            reflect_state,
            source_evidence,
        )
    )
    issues.extend(_changed_files_issues(reflect_state, source_evidence))
    issues.extend(_snapshot_metadata_issues(source_evidence))
    issues.extend(
        _artifact_hash_issues(
            source_evidence,
            source_brief,
            reflection,
            diff_summary,
            full_diff,
            test_summary,
        )
    )
    issues.extend(
        _workspace_binding_issues(
            reflect_state,
            source_evidence,
            current_snapshot,
        )
    )
    return _unique(issues)


def _initial_evidence_issues(
    source_run: str,
    reflect_state: dict[str, Any],
    source_evidence: dict[str, Any],
    full_diff: str,
    current_snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues: list[str] = []
    if str(reflect_state.get("run_id") or "") != source_run:
        issues.append("source_reflect_run_id_mismatch")
    if reflect_state.get("status") != "success":
        # Reflect 的确定性 eval 失败时，其 patch 不能作为可信审查输入。
        issues.append("source_reflect_not_success")
    if not full_diff.strip():
        issues.append("tracked_diff_empty")
    if source_evidence.get("untracked_files"):
        issues.append("source_untracked_files_present")
    if current_snapshot.untracked_files:
        issues.append("current_untracked_files_present")
    if current_snapshot.unsafe_index_paths:
        issues.append("current_unsafe_index_flags_present")
    issues.extend(_string_list(source_evidence.get("source_brief_evidence_issues")))
    return issues


def _schema_issues(
    schema_version: object,
    source_evidence: dict[str, Any],
    current_snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues: list[str] = []
    if schema_version not in SUPPORTED_REVIEW_EVIDENCE_SCHEMAS:
        issues.append("source_evidence_schema_unsupported")
    if schema_version in {3, 4}:
        issues.extend(_tracked_diff_hash_issues(source_evidence, current_snapshot))
    if schema_version == 4:
        issues.extend(_schema_v4_issues(source_evidence, current_snapshot))
    return issues


def _tracked_diff_hash_issues(
    source_evidence: dict[str, Any],
    current_snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues: list[str] = []
    for key, current_hash in (
        ("staged_diff_sha256", current_snapshot.staged_diff_sha256),
        ("unstaged_diff_sha256", current_snapshot.unstaged_diff_sha256),
    ):
        if not _matches_sha256(source_evidence.get(key), current_hash):
            issues.append(f"{key}_mismatch")
    return issues


def _schema_v4_issues(
    source_evidence: dict[str, Any],
    current_snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues: list[str] = []
    if source_evidence.get("ignored_content_complete") not in {True, False}:
        issues.append("ignored_content_complete_invalid")
    if not _matches_sha256(
        source_evidence.get("git_control_sha256"),
        current_snapshot.git_control_sha256,
    ):
        issues.append("git_control_sha256_mismatch")
    if source_evidence.get("git_control_complete") is not True:
        issues.append("source_git_control_incomplete")
    if not current_snapshot.git_control_complete:
        issues.append("current_git_control_incomplete")
    return issues


def _source_identity_issues(
    repo_path: Path,
    source_run: str,
    reflect_state: dict[str, Any],
    source_evidence: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if str(source_evidence.get("source_run") or "") != source_run:
        issues.append("source_run_mismatch")
    source_repo = str(reflect_state.get("repo_path") or "")
    if not source_repo or Path(source_repo).resolve() != repo_path.resolve():
        issues.append("source_repo_mismatch")
    if reflect_state.get("source_run") != source_evidence.get("upstream_source_run"):
        issues.append("upstream_source_run_mismatch")
    return issues


def _changed_files_issues(
    reflect_state: dict[str, Any],
    source_evidence: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    state_changed_files = _string_list(reflect_state.get("changed_files"))
    evidence_changed_files = _string_list(source_evidence.get("changed_files"))
    if not isinstance(reflect_state.get("changed_files"), list):
        issues.append("source_changed_files_invalid")
    if not isinstance(source_evidence.get("changed_files"), list):
        issues.append("evidence_changed_files_invalid")
    if state_changed_files != evidence_changed_files:
        issues.append("changed_files_mismatch")
    if str(source_evidence.get("changed_files_sha256") or "") != _sha256_json_value(
        evidence_changed_files
    ):
        issues.append("changed_files_hash_mismatch")
    return issues


def _snapshot_metadata_issues(source_evidence: dict[str, Any]) -> list[str]:
    snapshot_id = str(source_evidence.get("snapshot_id") or "")
    snapshot_payload = {
        key: value
        for key, value in source_evidence.items()
        if key != "snapshot_id"
    }
    if snapshot_id and snapshot_id == _sha256_json_value(snapshot_payload):
        return []
    return ["snapshot_metadata_invalid"]


def _artifact_hash_issues(
    source_evidence: dict[str, Any],
    source_brief: str,
    reflection: str,
    diff_summary: str,
    full_diff: str,
    test_summary: str,
) -> list[str]:
    issues: list[str] = []
    for key, text in (
        ("source_brief_sha256", source_brief),
        ("reflection_sha256", reflection),
        ("diff_summary_sha256", diff_summary),
        ("full_diff_sha256", full_diff),
        ("test_summary_sha256", test_summary),
    ):
        if str(source_evidence.get(key) or "") != _sha256_text(text):
            issues.append(f"{key.removesuffix('_sha256')}_hash_mismatch")
    return issues


def _workspace_binding_issues(
    reflect_state: dict[str, Any],
    source_evidence: dict[str, Any],
    current_snapshot: ReviewWorkspaceSnapshot,
) -> list[str]:
    issues: list[str] = []
    if source_evidence.get("untracked_content_complete") is not True:
        issues.append("source_untracked_content_incomplete")
    if not current_snapshot.untracked_content_complete:
        issues.append("current_untracked_content_incomplete")
    source_workspace_fingerprint = str(
        source_evidence.get("workspace_fingerprint") or ""
    )
    if (
        not source_workspace_fingerprint
        or reflect_state.get("workspace_fingerprint") != source_workspace_fingerprint
    ):
        issues.append("source_fingerprint_invalid")
    snapshot_id = str(source_evidence.get("snapshot_id") or "")
    if reflect_state.get("review_snapshot_id") != snapshot_id:
        issues.append("source_snapshot_id_invalid")
    if current_snapshot.fingerprint != source_workspace_fingerprint:
        issues.append("workspace_changed_since_reflect")
    return issues


def _matches_sha256(value: object, current_hash: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and value == current_hash


def _sha256_json_value(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
