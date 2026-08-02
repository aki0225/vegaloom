from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .project_config import VERIFICATION_TEMP_ROOT
from .redaction import is_sensitive_path, redact_text, sensitive_path_reason
from .run_utils import resolve_runs_root


@dataclass(frozen=True)
class WorkspaceSnapshot:
    raw_status: str
    tracked_files: frozenset[str]
    untracked_files: frozenset[str]
    ignored_path_exclusions: frozenset[str] = frozenset()
    head_sha: str = ""
    untracked_manifest_sha256: str = ""
    ignored_manifest_sha256: str = ""
    ignored_manifest_complete: bool = False
    ignored_content_complete: bool = False
    git_control_sha256: str = ""
    git_control_complete: bool = False
    capture_complete: bool = True

    @property
    def has_tracked_changes(self) -> bool:
        """启动前已有 tracked diff 时，loop 无法安全归因本轮成果。"""
        return bool(self.tracked_files)


@dataclass(frozen=True)
class CurrentWorkspaceInventory:
    head_sha: str
    raw_status: str
    ignored_manifest_sha256: str
    ignored_manifest_complete: bool
    ignored_content_complete: bool
    git_control_sha256: str
    git_control_complete: bool


@dataclass(frozen=True)
class ContentManifestBudget:
    max_content_files: int
    max_file_bytes: int
    max_content_bytes: int
    max_metadata_files: int | None = None


@dataclass(frozen=True)
class ContentManifest:
    """有界内容清单的摘要与两层完整性。

    ``metadata_complete`` 表示所有枚举路径都已稳定读取到可用于变更比较的
    元数据（以及已尝试读取内容的稳定结果）。它不要求读取敏感文件或超出预算
    的普通文件内容；这些刻意的内容省略由 ``content_complete`` 单独表达。
    """

    sha256: str
    metadata_complete: bool
    content_complete: bool


@dataclass(frozen=True)
class _EntryFingerprint:
    payload: bytes
    metadata_complete: bool
    content_complete: bool
    content_bytes: int = 0
    content_file_read: bool = False


@dataclass
class _BudgetUsage:
    content_files: int = 0
    content_bytes: int = 0


def ignored_coverage_level(
    manifest_complete: object,
    content_complete: object,
) -> str:
    if manifest_complete is not True:
        return "incomplete"
    if content_complete is True:
        return "full_content"
    return "metadata_bounded"


def build_content_manifest(
    repo_path: Path,
    paths: list[str],
    *,
    version: str,
    budget: ContentManifestBudget,
) -> ContentManifest:
    repo_root = repo_path.resolve(strict=True)
    unique_paths = sorted(dict.fromkeys(paths))
    manifest = hashlib.sha256()
    manifest.update(f"{version}:{len(unique_paths)}".encode("ascii"))
    manifest.update(b"\0")
    usage = _BudgetUsage()
    metadata_complete = True
    content_complete = True
    for index, relative_path in enumerate(unique_paths):
        manifest.update(relative_path.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
        if budget.max_metadata_files is not None and index >= budget.max_metadata_files:
            manifest.update(b"<metadata-budget-exceeded>\0")
            metadata_complete = False
            content_complete = False
            continue
        path = _contained_manifest_path(repo_root, relative_path)
        if path is None:
            manifest.update(b"<path-outside-repo>\0")
            metadata_complete = False
            content_complete = False
            continue
        entry = _fingerprint_entry(
            path,
            relative_path,
            budget,
            usage,
        )
        manifest.update(entry.payload)
        manifest.update(b"\0")
        metadata_complete = metadata_complete and entry.metadata_complete
        content_complete = content_complete and entry.content_complete
        if entry.content_file_read:
            usage.content_files += 1
            usage.content_bytes += entry.content_bytes
    return ContentManifest(
        sha256=manifest.hexdigest(),
        metadata_complete=metadata_complete,
        content_complete=content_complete,
    )


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


def _validate_verification_temp_root(repo: Path, root: Path) -> None:
    if not root.is_relative_to(repo):
        raise ValueError("verification 临时目录根路径逃出目标仓库")
    if root.relative_to(repo) != VERIFICATION_TEMP_ROOT:
        raise ValueError("verification 临时目录根路径不能经链接或 reparse point 改道")


def _fingerprint_entry(
    path: Path,
    relative_path: str,
    budget: ContentManifestBudget,
    usage: _BudgetUsage,
) -> _EntryFingerprint:
    try:
        stat_result = path.lstat()
    except OSError:
        return _EntryFingerprint(b"<unreadable>", False, False)

    metadata = stat_metadata(stat_result)
    if stat.S_ISLNK(stat_result.st_mode):
        return _symlink_fingerprint(path, metadata)
    if _is_reparse_point(stat_result):
        return _EntryFingerprint(
            metadata + b"<reparse-point-not-read>",
            False,
            False,
        )
    if stat.S_ISDIR(stat_result.st_mode):
        return _EntryFingerprint(
            metadata + b"<directory-content-not-read>",
            True,
            False,
        )
    if is_sensitive_path(relative_path):
        return _EntryFingerprint(
            metadata + b"<sensitive-content-not-read>",
            True,
            False,
        )
    if stat.S_ISREG(stat_result.st_mode):
        return _regular_file_fingerprint(path, stat_result, metadata, budget, usage)
    return _EntryFingerprint(metadata, True, True)


def _contained_manifest_path(
    repo_root: Path,
    relative_path: str,
) -> Path | None:
    if not relative_path or "\0" in relative_path:
        return None
    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
        or ".." in posix_path.parts
        or ".." in windows_path.parts
        or not posix_path.parts
    ):
        return None

    candidate = repo_root.joinpath(*posix_path.parts)
    try:
        resolved_parent = candidate.parent.resolve(strict=False)
    except OSError:
        return None
    if not resolved_parent.is_relative_to(repo_root):
        return None
    return candidate


def _is_reparse_point(stat_result) -> bool:
    file_attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(file_attributes & reparse_flag)


def _symlink_fingerprint(path: Path, metadata: bytes) -> _EntryFingerprint:
    try:
        target = str(path.readlink()).encode("utf-8", errors="replace")
    except OSError:
        return _EntryFingerprint(metadata + b"<unreadable-link>", False, False)
    return _EntryFingerprint(metadata + target, True, True)


def _regular_file_fingerprint(
    path: Path,
    stat_result,
    metadata: bytes,
    budget: ContentManifestBudget,
    usage: _BudgetUsage,
) -> _EntryFingerprint:
    remaining_bytes = budget.max_content_bytes - usage.content_bytes
    if usage.content_files >= budget.max_content_files:
        return _EntryFingerprint(
            metadata + b"<content-file-budget-exceeded>",
            True,
            False,
        )
    if stat_result.st_size > budget.max_file_bytes:
        return _EntryFingerprint(
            metadata + b"<content-file-too-large>",
            True,
            False,
        )
    if stat_result.st_size > remaining_bytes:
        return _EntryFingerprint(
            metadata + b"<content-byte-budget-exceeded>",
            True,
            False,
        )

    content_hash = bounded_file_hash(path, stat_result.st_size)
    try:
        final_stat = path.lstat()
    except OSError:
        content_hash = b"<content-changed-during-read>"
    else:
        if stat_metadata(final_stat) != metadata:
            content_hash = b"<content-changed-during-read>"
    return _EntryFingerprint(
        metadata + content_hash,
        is_complete_content_hash(content_hash),
        is_complete_content_hash(content_hash),
        stat_result.st_size,
        True,
    )


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


def stat_metadata(stat_result) -> bytes:
    return (
        f"{stat_result.st_mode}:{stat_result.st_size}:{stat_result.st_mtime_ns}:"
        f"{stat_result.st_ctime_ns}:{stat_result.st_dev}:{stat_result.st_ino}"
    ).encode("ascii")


def bounded_file_hash(path: Path, expected_size: int) -> bytes:
    file_hash = hashlib.sha256()
    read_bytes = 0
    try:
        with path.open("rb") as stream:
            while read_bytes <= expected_size:
                chunk = stream.read(min(1024 * 1024, expected_size - read_bytes + 1))
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > expected_size:
                    return b"<content-changed-during-read>"
                file_hash.update(chunk)
    except OSError:
        return b"<content-unreadable>"
    if read_bytes != expected_size:
        return b"<content-changed-during-read>"
    return b"<content-sha256:" + file_hash.hexdigest().encode("ascii") + b">"


def is_complete_content_hash(content_hash: bytes) -> bool:
    return content_hash.startswith(b"<content-sha256:")
