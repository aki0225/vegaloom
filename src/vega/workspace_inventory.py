from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

from .redaction import is_sensitive_path, redact_text, sensitive_path_reason


@dataclass(frozen=True)
class ContentManifestBudget:
    max_content_files: int
    max_file_bytes: int
    max_content_bytes: int
    max_metadata_files: int | None = None


@dataclass(frozen=True)
class _EntryFingerprint:
    payload: bytes
    complete: bool
    content_bytes: int = 0
    content_file_read: bool = False


@dataclass
class _BudgetUsage:
    content_files: int = 0
    content_bytes: int = 0


def build_content_manifest(
    repo_path: Path,
    paths: list[str],
    *,
    version: str,
    budget: ContentManifestBudget,
) -> tuple[str, bool]:
    unique_paths = sorted(dict.fromkeys(paths))
    manifest = hashlib.sha256()
    manifest.update(f"{version}:{len(unique_paths)}".encode("ascii"))
    manifest.update(b"\0")
    usage = _BudgetUsage()
    content_complete = True
    for index, relative_path in enumerate(unique_paths):
        manifest.update(relative_path.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
        if budget.max_metadata_files is not None and index >= budget.max_metadata_files:
            manifest.update(b"<metadata-budget-exceeded>\0")
            content_complete = False
            continue
        entry = _fingerprint_entry(
            repo_path / relative_path,
            relative_path,
            budget,
            usage,
        )
        manifest.update(entry.payload)
        manifest.update(b"\0")
        content_complete = content_complete and entry.complete
        if entry.content_file_read:
            usage.content_files += 1
            usage.content_bytes += entry.content_bytes
    return manifest.hexdigest(), content_complete


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


def _fingerprint_entry(
    path: Path,
    relative_path: str,
    budget: ContentManifestBudget,
    usage: _BudgetUsage,
) -> _EntryFingerprint:
    try:
        stat_result = path.lstat()
    except OSError:
        return _EntryFingerprint(b"<unreadable>", False)

    metadata = stat_metadata(stat_result)
    if stat.S_ISLNK(stat_result.st_mode):
        return _symlink_fingerprint(path, metadata)
    if is_sensitive_path(relative_path):
        return _EntryFingerprint(metadata + b"<sensitive-content-not-read>", False)
    if stat.S_ISREG(stat_result.st_mode):
        return _regular_file_fingerprint(path, stat_result, metadata, budget, usage)
    return _EntryFingerprint(metadata, True)


def _symlink_fingerprint(path: Path, metadata: bytes) -> _EntryFingerprint:
    try:
        target = str(path.readlink()).encode("utf-8", errors="replace")
    except OSError:
        return _EntryFingerprint(metadata + b"<unreadable-link>", False)
    return _EntryFingerprint(metadata + target, True)


def _regular_file_fingerprint(
    path: Path,
    stat_result,
    metadata: bytes,
    budget: ContentManifestBudget,
    usage: _BudgetUsage,
) -> _EntryFingerprint:
    remaining_bytes = budget.max_content_bytes - usage.content_bytes
    if usage.content_files >= budget.max_content_files:
        return _EntryFingerprint(metadata + b"<content-file-budget-exceeded>", False)
    if stat_result.st_size > budget.max_file_bytes:
        return _EntryFingerprint(metadata + b"<content-file-too-large>", False)
    if stat_result.st_size > remaining_bytes:
        return _EntryFingerprint(metadata + b"<content-byte-budget-exceeded>", False)

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
