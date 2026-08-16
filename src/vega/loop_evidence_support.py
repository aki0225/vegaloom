from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        return current_workspace_snapshot, []
    try:
        snapshot = capture_workspace(
            workspace,
            repo_path,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
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
