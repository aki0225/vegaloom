from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Protocol

from .git_read import coerce_git_output_bytes, format_git_error, run_git_capture
from .workspace_inventory import (
    bounded_file_hash,
    is_complete_content_hash,
    stat_metadata,
)


class GitBytesReader(Protocol):
    def __call__(
        self,
        repo_path: Path,
        command: list[str],
        *,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> bytes: ...


def read_ignored_paths(repo_path: Path) -> tuple[list[str], bool]:
    result = run_git_capture(
        repo_path,
        [
            "git",
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
        ],
    )
    stdout = coerce_git_output_bytes(result.stdout)
    stderr = coerce_git_output_bytes(result.stderr)
    if result.returncode != 0:
        output = stdout.decode("utf-8", errors="replace") + format_git_error(
            repo_path,
            stderr.decode("utf-8", errors="replace"),
        )
        raise RuntimeError(output.strip())
    return _decode_nul_paths(stdout), not bool(stderr.strip())


def build_git_control_manifest(
    repo_path: Path,
    *,
    run_git_bytes: GitBytesReader,
    max_file_bytes: int,
) -> tuple[str, bool]:
    git_dir = _resolve_git_directory(repo_path, "--git-dir", run_git_bytes)
    common_dir = _resolve_git_directory(repo_path, "--git-common-dir", run_git_bytes)
    candidates = [
        ("common-config", common_dir / "config"),
        ("worktree-config", git_dir / "config.worktree"),
        ("common-exclude", common_dir / "info" / "exclude"),
        ("worktree-exclude", git_dir / "info" / "exclude"),
        ("common-attributes", common_dir / "info" / "attributes"),
        ("worktree-attributes", git_dir / "info" / "attributes"),
        ("alternates", common_dir / "objects" / "info" / "alternates"),
    ]
    manifest = hashlib.sha256()
    manifest.update(b"git-control-v1\0")
    complete = True
    seen: set[Path] = set()
    for label, path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        manifest.update(label.encode("ascii"))
        manifest.update(b"\0")
        payload, entry_complete = _git_control_entry(path, max_file_bytes)
        manifest.update(payload)
        manifest.update(b"\0")
        complete = complete and entry_complete
    return manifest.hexdigest(), complete


def read_head_sha(
    repo_path: Path,
    *,
    run_git_bytes: GitBytesReader,
) -> str:
    head_sha = run_git_bytes(
        repo_path.resolve(),
        ["git", "rev-parse", "--verify", "HEAD"],
    ).decode("utf-8", errors="replace").strip()
    if not head_sha:
        raise RuntimeError("git HEAD 为空")
    return head_sha


def read_core_ignorecase(
    repo_path: Path,
    *,
    run_git_bytes: GitBytesReader,
) -> bool | None:
    raw = run_git_bytes(
        repo_path.resolve(),
        ["git", "config", "--bool", "--local", "core.ignorecase"],
        allowed_returncodes=(0, 1),
    ).decode("utf-8", errors="replace").strip().casefold()
    if not raw:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise RuntimeError("git core.ignorecase 返回非布尔值")


def _git_control_entry(path: Path, max_file_bytes: int) -> tuple[bytes, bool]:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return b"<missing>", True
    except OSError:
        return b"<unreadable>", False

    metadata = stat_metadata(stat_result)
    if stat.S_ISLNK(stat_result.st_mode):
        return metadata + b"<symlink>", False
    if not stat.S_ISREG(stat_result.st_mode):
        return metadata + b"<not-regular-file>", False
    if stat_result.st_size > max_file_bytes:
        return metadata + b"<file-too-large>", False
    content_hash = bounded_file_hash(path, stat_result.st_size)
    return metadata + content_hash, is_complete_content_hash(content_hash)


def _resolve_git_directory(
    repo_path: Path,
    option: str,
    run_git_bytes: GitBytesReader,
) -> Path:
    raw = run_git_bytes(
        repo_path,
        ["git", "rev-parse", "--path-format=absolute", option],
    ).decode("utf-8", errors="replace").strip()
    if not raw:
        raise RuntimeError(f"git rev-parse {option} 返回空路径")
    path = Path(raw)
    if not path.is_absolute():
        path = repo_path / path
    return path.resolve()


def _decode_nul_paths(payload: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="replace")
        for item in payload.split(b"\0")
        if item
    ]
