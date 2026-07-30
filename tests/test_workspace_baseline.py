from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vega.workspace_baseline import read_workspace_baseline


def _write_baseline(path: Path, *, tracked_files: list[str]) -> str:
    raw = (
        json.dumps(
            {
                "artifact_version": 1,
                "head_sha": "abc123",
                "tracked_files": tracked_files,
                "untracked_files": [],
                "untracked_manifest_sha256": "manifest-sha",
                "capture_complete": True,
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "invalid_path",
    [
        "/outside.txt",
        "C:outside.txt",
        r"C:\outside.txt",  # repo-path-policy: allow-test-fixture
        "C:/outside.txt",  # repo-path-policy: allow-test-fixture
        r"\outside.txt",
        r"\\server\share\outside.txt",  # repo-path-policy: allow-test-fixture
        r"\\?\C:\outside.txt",  # repo-path-policy: allow-test-fixture
    ],
)
def test_read_workspace_baseline_rejects_windows_boundary_paths(
    tmp_path: Path,
    invalid_path: str,
) -> None:
    baseline_path = tmp_path / "workspace-baseline.json"
    digest = _write_baseline(baseline_path, tracked_files=[invalid_path])

    with pytest.raises(ValueError, match="越过仓库边界"):
        read_workspace_baseline(baseline_path, expected_sha256=digest)


def test_read_workspace_baseline_accepts_repository_relative_paths(tmp_path: Path) -> None:
    baseline_path = tmp_path / "workspace-baseline.json"
    tracked_files = [
        ".github/workflows/ci.yml",
        "docs/path with spaces.md",
        r"src\vega\workspace_baseline.py",
    ]
    digest = _write_baseline(baseline_path, tracked_files=tracked_files)

    snapshot = read_workspace_baseline(baseline_path, expected_sha256=digest)

    assert snapshot.tracked_files == frozenset(tracked_files)
