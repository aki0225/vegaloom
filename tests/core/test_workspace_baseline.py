from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vega.workspace_baseline import (
    read_workspace_baseline,
    write_workspace_baseline,
)
from vega.workspace_check import WorkspaceSnapshot


def _write_raw_baseline(
    path: Path,
    *,
    tracked_files: list[str] | None = None,
    untracked_files: list[str] | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "artifact_version": 1,
        "head_sha": "abc123",
        "tracked_files": tracked_files if tracked_files is not None else [],
        "untracked_files": untracked_files if untracked_files is not None else [],
        "ignored_path_exclusions": [],
        "untracked_manifest_sha256": "untracked-sha",
        "ignored_manifest_sha256": "ignored-sha",
        "ignored_manifest_complete": True,
        "ignored_content_complete": False,
        "git_control_sha256": "git-control-sha",
        "git_control_complete": True,
        "capture_complete": True,
    }
    payload.update(extra or {})
    raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_workspace_baseline_round_trip_preserves_control_manifests(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace-baseline.json"
    snapshot = WorkspaceSnapshot(
        raw_status="不应写入 artifact",
        tracked_files=frozenset({"src/vega/models.py"}),
        untracked_files=frozenset({"notes.txt"}),
        ignored_path_exclusions=frozenset({"runs"}),
        head_sha="abc123",
        tracked_diff_sha256="tracked-diff-sha",
        tracked_diff_complete=True,
        untracked_manifest_sha256="untracked-sha",
        ignored_manifest_sha256="ignored-sha",
        ignored_manifest_complete=True,
        ignored_content_complete=False,
        git_control_sha256="git-control-sha",
        git_control_complete=True,
        capture_complete=True,
    )

    digest = write_workspace_baseline(path, snapshot)
    restored = read_workspace_baseline(path, expected_sha256=digest)

    assert restored == WorkspaceSnapshot(
        raw_status="",
        tracked_files=frozenset({"src/vega/models.py"}),
        untracked_files=frozenset({"notes.txt"}),
        ignored_path_exclusions=frozenset({"runs"}),
        head_sha="abc123",
        tracked_diff_sha256="tracked-diff-sha",
        tracked_diff_complete=True,
        untracked_manifest_sha256="untracked-sha",
        ignored_manifest_sha256="ignored-sha",
        ignored_manifest_complete=True,
        ignored_content_complete=False,
        git_control_sha256="git-control-sha",
        git_control_complete=True,
        capture_complete=True,
    )
    assert "不应写入 artifact" not in path.read_text(encoding="utf-8")


def test_read_workspace_baseline_rejects_missing_or_tampered_artifact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace-baseline.json"

    with pytest.raises(ValueError, match="缺失或不可读"):
        read_workspace_baseline(path, expected_sha256="missing")

    digest = _write_raw_baseline(path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="内容哈希"):
        read_workspace_baseline(path, expected_sha256=digest)


def test_read_workspace_baseline_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "workspace-baseline.json"
    digest = _write_raw_baseline(path, extra={"unexpected": True})

    with pytest.raises(ValueError, match="内容不合法"):
        read_workspace_baseline(path, expected_sha256=digest)


@pytest.mark.parametrize(
    "invalid_path",
    [
        "",
        ".",
        "../outside.txt",
        "src/../../outside.txt",
        "/outside.txt",
        "C:outside.txt",
        r"C:\outside.txt",  # repo-path-policy: allow-test-fixture
        "C:/outside.txt",  # repo-path-policy: allow-test-fixture
        r"\outside.txt",
        r"\\server\share\outside.txt",  # repo-path-policy: allow-test-fixture
        r"\\?\C:\outside.txt",  # repo-path-policy: allow-test-fixture
        "bad\x00path",
    ],
)
def test_read_workspace_baseline_rejects_boundary_paths(
    tmp_path: Path,
    invalid_path: str,
) -> None:
    path = tmp_path / "workspace-baseline.json"
    digest = _write_raw_baseline(path, tracked_files=[invalid_path])

    with pytest.raises(ValueError, match="越过仓库边界"):
        read_workspace_baseline(path, expected_sha256=digest)


@pytest.mark.parametrize(
    "tracked_files",
    [
        ["src/z.py", "src/a.py"],
        ["src/a.py", "src/a.py"],
    ],
)
def test_read_workspace_baseline_rejects_noncanonical_path_lists(
    tmp_path: Path,
    tracked_files: list[str],
) -> None:
    path = tmp_path / "workspace-baseline.json"
    digest = _write_raw_baseline(path, tracked_files=tracked_files)

    with pytest.raises(ValueError, match="路径未规范化"):
        read_workspace_baseline(path, expected_sha256=digest)


def test_read_workspace_baseline_accepts_repository_relative_windows_separator(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace-baseline.json"
    tracked_files = [
        ".github/workflows/ci.yml",
        "docs/path with spaces.md",
        r"src\vega\workspace_baseline.py",
    ]
    digest = _write_raw_baseline(path, tracked_files=tracked_files)

    snapshot = read_workspace_baseline(path, expected_sha256=digest)

    assert snapshot.tracked_files == frozenset(tracked_files)
    assert snapshot.tracked_diff_sha256 == ""
    assert snapshot.tracked_diff_complete is False
