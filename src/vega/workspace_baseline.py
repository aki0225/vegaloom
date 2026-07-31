from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .workspace_check import WorkspaceSnapshot


class WorkspaceBaselineArtifact(BaseModel):
    """可跨进程恢复的 Worker 启动前工作区基线。"""

    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    head_sha: str
    tracked_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    ignored_path_exclusions: list[str] = Field(default_factory=list)
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    ignored_manifest_complete: bool
    ignored_content_complete: bool
    git_control_sha256: str
    git_control_complete: bool
    capture_complete: bool


def write_workspace_baseline(path: Path, snapshot: WorkspaceSnapshot) -> str:
    """封存 workspace baseline，并返回供 state 与 trace 绑定的内容哈希。"""

    artifact = WorkspaceBaselineArtifact(
        head_sha=snapshot.head_sha,
        tracked_files=sorted(snapshot.tracked_files),
        untracked_files=sorted(snapshot.untracked_files),
        ignored_path_exclusions=sorted(snapshot.ignored_path_exclusions),
        untracked_manifest_sha256=snapshot.untracked_manifest_sha256,
        ignored_manifest_sha256=snapshot.ignored_manifest_sha256,
        ignored_manifest_complete=snapshot.ignored_manifest_complete,
        ignored_content_complete=snapshot.ignored_content_complete,
        git_control_sha256=snapshot.git_control_sha256,
        git_control_complete=snapshot.git_control_complete,
        capture_complete=snapshot.capture_complete,
    )
    raw = (
        json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def read_workspace_baseline(
    path: Path,
    *,
    expected_sha256: str,
) -> WorkspaceSnapshot:
    """读取并验证基线；证据缺失、被改写或路径越界时 fail-closed。"""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("workspace baseline 缺失或不可读") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("workspace baseline 内容哈希与 loop state 不一致")
    try:
        artifact = WorkspaceBaselineArtifact.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError("workspace baseline 内容不合法") from exc
    _validate_baseline_paths(artifact.tracked_files, "tracked")
    _validate_baseline_paths(artifact.untracked_files, "untracked")
    _validate_baseline_paths(
        artifact.ignored_path_exclusions,
        "ignored exclusion",
    )
    return WorkspaceSnapshot(
        raw_status="",
        tracked_files=frozenset(artifact.tracked_files),
        untracked_files=frozenset(artifact.untracked_files),
        ignored_path_exclusions=frozenset(artifact.ignored_path_exclusions),
        head_sha=artifact.head_sha,
        untracked_manifest_sha256=artifact.untracked_manifest_sha256,
        ignored_manifest_sha256=artifact.ignored_manifest_sha256,
        ignored_manifest_complete=artifact.ignored_manifest_complete,
        ignored_content_complete=artifact.ignored_content_complete,
        git_control_sha256=artifact.git_control_sha256,
        git_control_complete=artifact.git_control_complete,
        capture_complete=artifact.capture_complete,
    )


def _validate_baseline_paths(paths: list[str], label: str) -> None:
    if paths != sorted(set(paths)):
        raise ValueError(f"workspace baseline 的 {label} 路径未规范化")
    for value in paths:
        normalized = value.replace("\\", "/")
        posix_candidate = PurePosixPath(normalized)
        windows_candidate = PureWindowsPath(value)
        if (
            not value
            or "\x00" in value
            or normalized == "."
            or posix_candidate.is_absolute()
            or windows_candidate.drive
            or windows_candidate.root
            or windows_candidate.anchor
            or ".." in posix_candidate.parts
        ):
            raise ValueError("workspace baseline 包含越过仓库边界的路径")
