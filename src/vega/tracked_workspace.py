from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .git_read import run_git_bytes
from .workspace_status import parse_porcelain_v2_status


@dataclass(frozen=True)
class TrackedScopeSnapshot:
    """scope gate 读取到的一致 HEAD、tracked status 与双 diff 路径快照。"""

    head_sha: str
    status_sha256: str
    index_flags_sha256: str
    staged_files: tuple[str, ...]
    unstaged_files: tuple[str, ...]
    untracked_files: tuple[str, ...] = ()
    unsafe_index_paths: tuple[str, ...] = ()

    @property
    def changed_paths_sha256(self) -> str:
        payload = json.dumps(
            {
                "staged": self.staged_files,
                "unstaged": self.unstaged_files,
                "untracked": self.untracked_files,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _sha256(payload)


def collect_tracked_diff_parts(
    repo_path: Path,
    options: list[str],
    *,
    run_git: Callable[..., bytes] = run_git_bytes,
) -> tuple[str, str]:
    """分别读取 index 对 HEAD 与工作区对 index 的差异。"""

    allowed_returncodes = (0, 1, 2) if "--check" in options else (0,)
    diff_command = ["git", "-c", "core.autocrlf=input", "diff"]
    staged = run_git(
        repo_path,
        [
            *diff_command,
            "--no-ext-diff",
            "--no-textconv",
            "--cached",
            *options,
            "HEAD",
            "--",
        ],
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace")
    unstaged = run_git(
        repo_path,
        [*diff_command, "--no-ext-diff", "--no-textconv", *options, "--"],
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace")
    return _normalize_newlines(staged), _normalize_newlines(unstaged)


def render_tracked_diff_sections(staged_diff: str, unstaged_diff: str) -> str:
    """生成明确区分 index 与工作区差异的双事实流 patch。"""

    sections: list[str] = []
    if staged_diff.strip():
        sections.append(
            "# --- Vega staged diff: index vs HEAD ---\n"
            + staged_diff.rstrip()
        )
    if unstaged_diff.strip():
        sections.append(
            "# --- Vega unstaged diff: working tree vs index ---\n"
            + unstaged_diff.rstrip()
        )
    return "\n\n".join(sections).rstrip() + ("\n" if sections else "")


def capture_tracked_scope_snapshot(
    repo_path: Path,
    *,
    include_untracked: bool = False,
) -> TrackedScopeSnapshot:
    """用前后两次一致读取绑定 HEAD、status 与 index flags。"""

    repo = repo_path.resolve()
    command = [
        "git",
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        f"--untracked-files={'all' if include_untracked else 'no'}",
    ]
    index_flags_before = run_git_bytes(repo, ["git", "ls-files", "-v", "-z"])
    status_before = run_git_bytes(repo, command)
    status_after = run_git_bytes(repo, command)
    index_flags_after = run_git_bytes(repo, ["git", "ls-files", "-v", "-z"])
    if status_before != status_after or index_flags_before != index_flags_after:
        raise RuntimeError("scope gate 采集期间 HEAD、tracked status 或 index 标记发生变化")
    head_sha, staged, unstaged, untracked = parse_porcelain_v2_status(
        status_after
    )
    return TrackedScopeSnapshot(
        head_sha=head_sha,
        status_sha256=_sha256(status_after),
        index_flags_sha256=_sha256(index_flags_after),
        staged_files=tuple(staged),
        unstaged_files=tuple(unstaged),
        untracked_files=tuple(untracked),
        unsafe_index_paths=tuple(unsafe_index_paths(index_flags_after)),
    )


def unsafe_index_paths(payload: bytes) -> list[str]:
    """找出会让 Git 工作区视图忽略真实文件变化的 index 标记。"""

    paths: list[str] = []
    for item in payload.split(b"\0"):
        if not item:
            continue
        record = item.decode("utf-8", errors="replace")
        if len(record) < 3 or record[1] != " ":
            raise RuntimeError("git ls-files -v 输出格式不完整")
        tag = record[0]
        if tag == "S" or tag.islower():
            paths.append(record[2:])
    return list(dict.fromkeys(paths))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
