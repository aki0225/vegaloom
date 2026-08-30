from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Literal

from .agent_contract import canonical_digest
from .agent_contract_support import normalize_relative_paths
from .agent_handoff_safety import TaskCardError
from .git_read import run_git_bytes, run_git_capture


PORTABLE_WORKSPACE_DIGEST_KIND = "git-blob-v1"
WorkspaceDigestKind = Literal["workspace-bytes-v1", "git-blob-v1"]
_GIT_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def compute_handoff_workspace_digest(
    repo: Path,
    changed_files: list[str],
    *,
    digest_kind: WorkspaceDigestKind = "workspace-bytes-v1",
    revision: str | None = None,
) -> str:
    """计算交接 WIP 摘要；新版按 Git 条目身份消除签出差异。"""

    root = repo.resolve(strict=True)
    relative_paths = _normalize_handoff_paths(changed_files)
    if digest_kind == "workspace-bytes-v1":
        if revision is not None:
            raise TaskCardError("旧版 Workspace 摘要不能从 Git revision 计算")
        entries = _legacy_workspace_digest_entries(root, relative_paths)
        return canonical_digest({"changed_files": entries})
    if digest_kind != PORTABLE_WORKSPACE_DIGEST_KIND:
        raise TaskCardError(f"不支持的 Workspace 摘要类型：{digest_kind}")
    entries = (
        _git_tree_digest_entries(root, relative_paths, revision)
        if revision is not None
        else _worktree_git_blob_entries(root, relative_paths)
    )
    return canonical_digest(
        {
            "digest_kind": PORTABLE_WORKSPACE_DIGEST_KIND,
            "changed_files": entries,
        }
    )


def _legacy_workspace_digest_entries(
    repo: Path,
    changed_files: list[str],
) -> list[dict[str, object]]:
    """保留旧 Task Card 的原始字节摘要语义，避免已发布交接失效。"""

    entries: list[dict[str, object]] = []
    for relative in changed_files:
        path = (repo / PurePosixPath(relative)).resolve(strict=False)
        if not path.is_relative_to(repo):
            raise TaskCardError(f"交接文件越过仓库边界：{relative}")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            entries.append({"path": relative, "kind": "missing"})
            continue
        if path.is_symlink():
            entries.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "target": os.readlink(path),
                }
            )
            continue
        if not path.is_file():
            raise TaskCardError(f"交接文件不是普通文件：{relative}")
        entries.append(
            {
                "path": relative,
                "kind": "file",
                "size": metadata.st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return entries


def _worktree_git_blob_entries(
    repo: Path,
    changed_files: list[str],
) -> list[dict[str, object]]:
    index_modes = _git_index_modes(repo, changed_files)
    filemode_enabled = _git_boolean_config(repo, "core.filemode", default=True)
    symlinks_enabled = _git_boolean_config(repo, "core.symlinks", default=True)
    entries: list[dict[str, object]] = []
    for relative in changed_files:
        path = _handoff_path(repo, relative)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            entries.append({"path": relative, "kind": "missing"})
            continue
        git_mode = _worktree_git_mode(
            relative,
            metadata.st_mode,
            index_mode=index_modes.get(relative),
            filemode_enabled=filemode_enabled,
            symlinks_enabled=symlinks_enabled,
        )
        if git_mode == "120000":
            content = (
                os.fsencode(os.readlink(path))
                if stat.S_ISLNK(metadata.st_mode)
                else path.read_bytes()
            )
            object_id = _git_hash_object(repo, content)
            entries.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "git_mode": git_mode,
                    "git_blob": object_id,
                }
            )
            continue
        object_id = _git_hash_object(
            repo,
            path.read_bytes(),
            path_hint=relative,
        )
        entries.append(
            {
                "path": relative,
                "kind": "file",
                "git_mode": git_mode,
                "git_blob": object_id,
            }
        )
    return entries


def _git_tree_digest_entries(
    repo: Path,
    changed_files: list[str],
    revision: str,
) -> list[dict[str, object]]:
    if not changed_files:
        return []
    output = run_git_bytes(
        repo,
        [
            "git",
            "--literal-pathspecs",
            "ls-tree",
            "-z",
            "--full-tree",
            revision,
            "--",
            *changed_files,
        ],
    )
    tree_entries: dict[str, tuple[str, str, str]] = {}
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        parts = metadata.decode("ascii", errors="strict").split()
        if not separator or len(parts) != 3:
            raise TaskCardError("Git Tree 返回了无法解释的交接条目")
        mode, object_type, object_id = parts
        relative = raw_path.decode("utf-8", errors="strict")
        tree_entries[relative] = (mode, object_type, object_id)

    entries: list[dict[str, object]] = []
    for relative in changed_files:
        tree_entry = tree_entries.get(relative)
        if tree_entry is None:
            entries.append({"path": relative, "kind": "missing"})
            continue
        mode, object_type, object_id = tree_entry
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise TaskCardError(f"Git Tree 中的交接文件类型不受支持：{relative}")
        if not _GIT_OBJECT_ID_PATTERN.fullmatch(object_id):
            raise TaskCardError("Git Tree 返回了无效的 Blob 身份")
        entries.append(
            {
                "path": relative,
                "kind": "symlink" if mode == "120000" else "file",
                "git_mode": mode,
                "git_blob": object_id,
            }
        )
    return entries


def _git_hash_object(
    repo: Path,
    content: bytes,
    *,
    path_hint: str | None = None,
) -> str:
    command = ["git", "hash-object"]
    if path_hint is not None:
        command.append(f"--path={path_hint}")
    command.append("--stdin")
    try:
        result = run_git_capture(repo, command, input_data=content)
    except OSError as exc:
        raise TaskCardError("无法计算交接文件的 Git Blob 身份") from exc
    object_id = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or not _GIT_OBJECT_ID_PATTERN.fullmatch(object_id):
        raise TaskCardError("无法计算交接文件的 Git Blob 身份")
    return object_id


def _git_index_modes(repo: Path, changed_files: list[str]) -> dict[str, str]:
    if not changed_files:
        return {}
    output = run_git_bytes(
        repo,
        [
            "git",
            "--literal-pathspecs",
            "ls-files",
            "--stage",
            "-z",
            "--",
            *changed_files,
        ],
    )
    modes: dict[str, str] = {}
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        fields = metadata.decode("ascii", errors="strict").split()
        if not separator or len(fields) != 3:
            raise TaskCardError("Git Index 返回了无法解释的交接条目")
        mode, _, stage = fields
        relative = raw_path.decode("utf-8", errors="strict")
        if stage != "0" or relative in modes:
            raise TaskCardError(f"交接文件存在未解决的 Git Index 状态：{relative}")
        modes[relative] = mode
    return modes


def _git_boolean_config(repo: Path, key: str, *, default: bool) -> bool:
    result = run_git_capture(repo, ["git", "config", "--bool", "--get", key])
    if result.returncode == 1:
        return default
    value = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or value not in {"true", "false"}:
        raise TaskCardError(f"无法读取 Git 配置：{key}")
    return value == "true"


def _worktree_git_mode(
    relative: str,
    file_mode: int,
    *,
    index_mode: str | None,
    filemode_enabled: bool,
    symlinks_enabled: bool,
) -> str:
    if stat.S_ISLNK(file_mode):
        return "120000"
    if not stat.S_ISREG(file_mode):
        raise TaskCardError(f"交接文件不是普通文件：{relative}")
    if index_mode == "120000" and not symlinks_enabled:
        return "120000"
    if index_mode not in {None, "100644", "100755", "120000"}:
        raise TaskCardError(f"交接文件的 Git mode 不受支持：{relative}")
    if not filemode_enabled and index_mode in {"100644", "100755"}:
        return index_mode
    return "100755" if file_mode & stat.S_IXUSR else "100644"


def _handoff_path(repo: Path, relative: str) -> Path:
    path = repo.joinpath(*PurePosixPath(relative).parts)
    try:
        parent = path.parent.resolve(strict=False)
    except OSError as exc:
        raise TaskCardError(f"无法解析交接文件父目录：{relative}") from exc
    if not parent.is_relative_to(repo):
        raise TaskCardError(f"交接文件越过仓库边界：{relative}")
    return path


def _normalize_handoff_paths(values: list[str]) -> list[str]:
    try:
        return normalize_relative_paths(values)
    except ValueError as exc:
        raise TaskCardError(str(exc)) from exc
