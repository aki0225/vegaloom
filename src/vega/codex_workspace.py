from __future__ import annotations

import stat
from pathlib import Path

from .project_config import VERIFICATION_TEMP_ROOT
from .workspace_inventory import filter_ignored_paths, untracked_paths


def filter_codex_runtime_ignored_paths(
    repo_path: Path,
    paths: list[str],
    exclusions: frozenset[str],
) -> list[str]:
    filtered = filter_ignored_paths(paths, exclusions)
    if not _is_empty_root_agents_directory(repo_path):
        return filtered
    return [
        path
        for path in filtered
        if path.replace("\\", "/").rstrip("/") != ".agents"
    ]


def filter_codex_runtime_untracked_paths(
    repo_path: Path,
    paths: list[str],
    exclusions: frozenset[str],
) -> list[str]:
    verification_root = VERIFICATION_TEMP_ROOT.as_posix()
    untracked_exclusions = frozenset(
        path
        for path in exclusions
        if path.replace("\\", "/").rstrip("/") != verification_root
    )
    return filter_codex_runtime_ignored_paths(
        repo_path,
        paths,
        untracked_exclusions,
    )


def filter_codex_runtime_short_status(
    repo_path: Path,
    status: str,
    exclusions: frozenset[str],
) -> str:
    hidden_paths = _hidden_runtime_paths(
        repo_path,
        untracked_paths(status),
        exclusions,
    )
    if not hidden_paths:
        return status
    return "\n".join(
        line
        for line in status.splitlines()
        if not (line.startswith("?? ") and line[3:].strip() in hidden_paths)
    )


def filter_codex_runtime_porcelain_v1_status(
    repo_path: Path,
    status: bytes,
    exclusions: frozenset[str],
) -> bytes:
    records = status.split(b"\0")
    untracked = [
        record[3:].decode("utf-8", errors="replace")
        for record in records
        if record.startswith(b"?? ")
    ]
    hidden_paths = _hidden_runtime_paths(repo_path, untracked, exclusions)
    if not hidden_paths:
        return status
    return b"\0".join(
        record
        for record in records
        if not (
            record.startswith(b"?? ")
            and record[3:].decode("utf-8", errors="replace") in hidden_paths
        )
    )


def _hidden_runtime_paths(
    repo_path: Path,
    paths: list[str],
    exclusions: frozenset[str],
) -> set[str]:
    visible = filter_codex_runtime_untracked_paths(repo_path, paths, exclusions)
    return set(paths) - set(visible)


def _is_empty_root_agents_directory(repo_path: Path) -> bool:
    """识别 Codex 写工具留下的空目录；任何不确定状态都不豁免。"""
    agents_dir = repo_path / ".agents"
    try:
        before = agents_dir.lstat()
        before_identity = _plain_directory_identity(before)
        if before_identity is None:
            return False
        if next(agents_dir.iterdir(), None) is not None:
            return False
        after_identity = _plain_directory_identity(agents_dir.lstat())
        return after_identity is not None and after_identity == before_identity
    except OSError:
        return False


def _plain_directory_identity(path_stat: object) -> tuple[object, ...] | None:
    mode = getattr(path_stat, "st_mode")
    file_attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISDIR(mode)
        or stat.S_ISLNK(mode)
        or bool(file_attributes & reparse_flag)
    ):
        return None
    return (
        getattr(path_stat, "st_dev"),
        getattr(path_stat, "st_ino"),
        mode,
        getattr(path_stat, "st_ctime_ns"),
        getattr(path_stat, "st_mtime_ns"),
        file_attributes,
    )
