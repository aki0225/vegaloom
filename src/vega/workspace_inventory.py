from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .content_manifest import (
    ContentManifest,
    ContentManifestBudget,
    bounded_file_hash,
    build_content_manifest,
    is_complete_content_hash,
    stat_metadata,
)
from .project_config import VERIFICATION_TEMP_ROOT
from .redaction import redact_text, sensitive_path_reason
from .run_utils import resolve_runs_root

__all__ = [
    "ContentManifest",
    "ContentManifestBudget",
    "bounded_file_hash",
    "build_content_manifest",
    "is_complete_content_hash",
    "stat_metadata",
]


@dataclass(frozen=True)
class WorkspaceSnapshot:
    raw_status: str
    tracked_files: frozenset[str]
    untracked_files: frozenset[str]
    ignored_path_exclusions: frozenset[str] = frozenset()
    head_sha: str = ""
    tracked_diff_sha256: str = ""
    tracked_diff_complete: bool = False
    untracked_manifest_sha256: str = ""
    ignored_manifest_sha256: str = ""
    ignored_manifest_complete: bool = False
    ignored_content_complete: bool = False
    git_control_sha256: str = ""
    git_control_complete: bool = False
    index_flags_sha256: str = ""
    unsafe_index_paths: tuple[str, ...] = ()
    ignored_descendants_manifest_sha256: str = ""
    ignored_descendants_complete: bool = False
    capture_complete: bool = True

    @property
    def has_tracked_changes(self) -> bool:
        """启动前已有 tracked diff 时，loop 无法安全归因本轮成果。"""
        return bool(self.tracked_files)


def hash_tracked_diff(staged_diff: str, unstaged_diff: str) -> str:
    payload = f"staged\0{staged_diff}\0unstaged\0{unstaged_diff}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CurrentWorkspaceInventory:
    head_sha: str
    raw_status: str
    ignored_manifest_sha256: str
    ignored_manifest_complete: bool
    ignored_content_complete: bool
    git_control_sha256: str
    git_control_complete: bool


def ignored_coverage_level(
    manifest_complete: object,
    content_complete: object,
) -> str:
    if manifest_complete is not True:
        return "incomplete"
    if content_complete is True:
        return "full_content"
    return "metadata_bounded"


def safe_git_status(status_text: str) -> str:
    safe_lines: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 4:
            safe_lines.append(redact_text(line))
            continue
        prefix = line[:3]
        path_text = line[3:].strip()
        if not path_text:
            safe_lines.append(redact_text(line))
            continue
        safe_lines.append(f"{prefix}{safe_status_path_expression(path_text)}")
    return "\n".join(safe_lines)


def untracked_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for line in status_text.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip()
        if path:
            paths.append(path)
    return paths


def safe_path_for_report(path: str) -> str:
    reason = sensitive_path_reason(path)
    if reason:
        return f"<sensitive-path:{reason}>"
    return redact_text(path)


def filter_ignored_paths(
    paths: list[str],
    exclusions: frozenset[str],
) -> list[str]:
    normalized_exclusions = {
        value.replace("\\", "/").rstrip("/")
        for value in exclusions
    }
    if not normalized_exclusions:
        return paths
    return [
        path
        for path in paths
        if not _matches_ignored_exclusion(path, normalized_exclusions)
    ]


def workspace_ignored_path_exclusions(
    workspace: Path,
    repo_path: Path,
) -> frozenset[str]:
    """排除目标仓库内由 Vega 独占维护的运行目录。"""

    exclusions = {VERIFICATION_TEMP_ROOT.as_posix()}
    runs_root = resolve_runs_root(workspace)
    if runs_root is None:
        return frozenset(exclusions)
    try:
        relative = runs_root.resolve().relative_to(repo_path.resolve())
    except ValueError:
        return frozenset(exclusions)
    if relative.parts:
        exclusions.add(relative.as_posix())
    return frozenset(exclusions)


def prepare_verification_temp_root(repo_path: Path) -> Path:
    """建立受控根目录，供 Assist 基线封存其父目录元数据。"""

    repo = repo_path.resolve(strict=True)
    logical_root = repo / VERIFICATION_TEMP_ROOT
    _validate_verification_temp_root(
        repo,
        logical_root.resolve(strict=False),
    )
    try:
        logical_root.mkdir(parents=True, exist_ok=True)
        root = logical_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("无法创建 verification 临时目录根路径") from exc
    _validate_verification_temp_root(repo, root)
    if not root.is_dir():
        raise ValueError("verification 临时目录根路径不是目录")
    return root


def create_verification_temp_dir(
    repo_path: Path,
    run_id: str,
    iteration: int,
    command_index: int,
) -> Path:
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ValueError("verification run_id 必须是单个安全路径段")
    if iteration < 1 or command_index < 1:
        raise ValueError("verification iteration 和 command index 必须从 1 开始")

    root = prepare_verification_temp_root(repo_path)
    command_dir = (
        root
        / run_id
        / f"iteration-{iteration}"
        / f"command-{command_index}"
    )
    current = root
    for part in command_dir.parent.relative_to(root).parts:
        current = current / part
        if os.path.lexists(current):
            _require_plain_verification_directory(current)
            continue
        try:
            current.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise ValueError("无法创建 verification 临时目录父路径") from exc
        _require_plain_verification_directory(current)

    try:
        command_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("verification 临时目录已存在；拒绝复用或清理预置内容") from exc
    except OSError as exc:
        raise ValueError("无法独占创建 verification 临时目录") from exc
    _require_plain_verification_directory(command_dir)
    resolved = command_dir.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("verification 临时目录逃出受控根路径")
    return resolved


def _validate_verification_temp_root(repo: Path, root: Path) -> None:
    if not root.is_relative_to(repo):
        raise ValueError("verification 临时目录根路径逃出目标仓库")
    if root.relative_to(repo) != VERIFICATION_TEMP_ROOT:
        raise ValueError("verification 临时目录根路径不能经链接或 reparse point 改道")


def _require_plain_verification_directory(path: Path) -> None:
    metadata = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
        raise ValueError("verification 临时目录不能经链接或 reparse point 改道")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("verification 临时目录路径必须是目录")


def safe_status_path_expression(path_text: str) -> str:
    if " -> " in path_text:
        return " -> ".join(
            safe_path_for_report(part.strip())
            for part in path_text.split(" -> ")
        )
    return safe_path_for_report(path_text)


def _matches_ignored_exclusion(path: str, exclusions: set[str]) -> bool:
    normalized = path.replace("\\", "/").rstrip("/")
    return any(
        normalized == excluded or normalized.startswith(f"{excluded}/")
        for excluded in exclusions
    )
