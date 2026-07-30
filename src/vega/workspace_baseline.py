from __future__ import annotations

import hashlib
import json
from pathlib import Path
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
    untracked_manifest_sha256: str
    capture_complete: bool


def write_workspace_baseline(path: Path, snapshot: WorkspaceSnapshot) -> str:
    """封存 workspace baseline，并返回用于 state/trace 绑定的内容哈希。"""

    artifact = WorkspaceBaselineArtifact(
        head_sha=snapshot.head_sha,
        tracked_files=sorted(snapshot.tracked_files),
        untracked_files=sorted(snapshot.untracked_files),
        untracked_manifest_sha256=snapshot.untracked_manifest_sha256,
        capture_complete=snapshot.capture_complete,
    )
    text = json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def read_workspace_baseline(
    path: Path,
    *,
    expected_sha256: str,
) -> WorkspaceSnapshot:
    """读取并校验封存基线；证据缺失、被改写或路径异常时 fail closed。"""

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
    return WorkspaceSnapshot(
        raw_status="",
        tracked_files=frozenset(artifact.tracked_files),
        untracked_files=frozenset(artifact.untracked_files),
        head_sha=artifact.head_sha,
        untracked_manifest_sha256=artifact.untracked_manifest_sha256,
        capture_complete=artifact.capture_complete,
    )


def _validate_baseline_paths(paths: list[str], label: str) -> None:
    if paths != sorted(set(paths)):
        raise ValueError(f"workspace baseline 的 {label} 路径未规范化")
    for value in paths:
        if not value or "\x00" in value:
            raise ValueError("workspace baseline 包含空路径或 NUL")
        normalized = value.replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("//"):
            raise ValueError("workspace baseline 包含越过仓库边界的路径")
