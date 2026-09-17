from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .brief_runtime import read_acceptance_supplement
from .runtime_workspace import capture_runtime_workspace
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


def capture_current_workspace_snapshot(
    workspace: Path,
    repo_path: Path,
    current_workspace_snapshot: ReviewWorkspaceSnapshot | None,
    *,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
    capture_workspace: Callable[..., ReviewWorkspaceSnapshot] = capture_runtime_workspace,
) -> tuple[ReviewWorkspaceSnapshot | None, list[str]]:
    if current_workspace_snapshot is not None:
        issues: list[str] = []
        if current_workspace_snapshot.comparison_base_sha != comparison_base_sha:
            issues.append("workspace_snapshot_comparison_base_mismatch")
        if current_workspace_snapshot.comparison_paths != comparison_paths:
            issues.append("workspace_snapshot_comparison_paths_mismatch")
        return current_workspace_snapshot, issues
    try:
        snapshot = capture_workspace(
            workspace,
            repo_path,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None, ["workspace_snapshot_failed"]
    return snapshot, []


def freshness(
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


def sha256_json(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(serialized)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_text_artifact_hash(
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
    if str(evidence.get(hash_key) or "") != sha256_text(text):
        issues.append(f"{issue_prefix}_hash_mismatch")
    return text



def acceptance_supplement_freshness_issues(
    workspace: Path, source_run: object, reflect_run: str,
    evidence: dict[str, Any], snapshot: ReviewWorkspaceSnapshot | None,
) -> list[str]:
    try:
        supplement = read_acceptance_supplement(
            workspace, source_run, snapshot.head_sha if snapshot else "",
            reflect_run=reflect_run,
        )
    except (OSError, ValueError):
        return ["acceptance_supplement_invalid"]
    if evidence.get("acceptance_supplement_sha256", sha256_text("")) != sha256_text(supplement):
        return ["acceptance_supplement_hash_mismatch"]
    return []
