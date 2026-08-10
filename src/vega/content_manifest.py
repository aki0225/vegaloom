from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .redaction import is_sensitive_path


@dataclass(frozen=True)
class ContentManifestBudget:
    max_content_files: int
    max_file_bytes: int
    max_content_bytes: int
    max_metadata_files: int | None = None
    expand_directories: bool = False


@dataclass(frozen=True)
class ContentManifest:
    """有界内容清单的摘要与两层完整性。"""

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
    metadata_entries: int = 0
    content_files: int = 0
    content_bytes: int = 0


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
    for relative_path in unique_paths:
        manifest.update(relative_path.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
        path = _contained_manifest_path(repo_root, relative_path)
        if path is None:
            manifest.update(b"<path-outside-repo>\0")
            metadata_complete = False
            content_complete = False
            continue
        entry = _fingerprint_entry(path, relative_path, budget, usage)
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


def _fingerprint_entry(
    path: Path,
    relative_path: str,
    budget: ContentManifestBudget,
    usage: _BudgetUsage,
) -> _EntryFingerprint:
    if (
        budget.max_metadata_files is not None
        and usage.metadata_entries >= budget.max_metadata_files
    ):
        return _EntryFingerprint(b"<metadata-budget-exceeded>", False, False)
    usage.metadata_entries += 1
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
        if budget.expand_directories:
            return _directory_fingerprint(
                path,
                relative_path,
                metadata,
                budget,
                usage,
            )
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


def _directory_fingerprint(
    path: Path,
    relative_path: str,
    metadata: bytes,
    budget: ContentManifestBudget,
    usage: _BudgetUsage,
) -> _EntryFingerprint:
    """在同一预算内稳定读取目录后代；预算不足时只返回不完整标记。"""

    remaining = (
        None
        if budget.max_metadata_files is None
        else max(budget.max_metadata_files - usage.metadata_entries, 0)
    )
    names: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                names.append(entry.name)
                if remaining is not None and len(names) > remaining:
                    return _EntryFingerprint(
                        metadata + b"<directory-metadata-budget-exceeded>",
                        False,
                        False,
                    )
    except OSError:
        return _EntryFingerprint(
            metadata + b"<directory-unreadable>",
            False,
            False,
        )

    manifest = hashlib.sha256()
    manifest.update(f"directory-v1:{len(names)}".encode("ascii"))
    manifest.update(b"\0")
    metadata_complete = True
    content_complete = True
    for name in sorted(names):
        child = _fingerprint_entry(
            path / name,
            f"{relative_path.rstrip('/')}/{name}",
            budget,
            usage,
        )
        manifest.update(name.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
        manifest.update(child.payload)
        manifest.update(b"\0")
        metadata_complete = metadata_complete and child.metadata_complete
        content_complete = content_complete and child.content_complete
    return _EntryFingerprint(
        metadata
        + b"<directory-sha256:"
        + manifest.hexdigest().encode("ascii")
        + b">",
        metadata_complete,
        content_complete,
    )


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
