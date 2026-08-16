from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .git_read import run_git_bytes, run_git_capture
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
    comparison_base_sha: str | None = None
    comparison_paths: tuple[str, ...] = ()
    committed_files: tuple[str, ...] = ()

    @property
    def changed_paths_sha256(self) -> str:
        payload = json.dumps(
            {
                "comparison_base_sha": self.comparison_base_sha,
                "comparison_paths": self.comparison_paths,
                "committed": self.committed_files,
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
    head_sha: str = "HEAD",
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
            head_sha,
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


def collect_committed_diff(
    repo_path: Path,
    comparison_base_sha: str | None,
    options: list[str],
    *,
    run_git: Callable[..., bytes] = run_git_bytes,
    comparison_paths: tuple[str, ...] = (),
    comparison_head_sha: str = "HEAD",
) -> str:
    """读取 comparison base 到当前 HEAD 的已提交 WIP 差异。"""

    if comparison_base_sha is None:
        if comparison_paths:
            raise ValueError("comparison paths 必须与 comparison base 一起使用")
        return ""
    allowed_returncodes = (0, 1, 2) if "--check" in options else (0,)
    output = run_git(
        repo_path,
        [
            "git",
            "-c",
            "core.autocrlf=input",
            "--literal-pathspecs",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            *options,
            comparison_base_sha,
            comparison_head_sha,
            "--",
            *comparison_paths,
        ],
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace")
    return _normalize_newlines(output)


def render_tracked_diff_sections(
    staged_diff: str,
    unstaged_diff: str,
    *,
    committed_diff: str = "",
    comparison_base_sha: str | None = None,
) -> str:
    """生成明确区分 index 与工作区差异的双事实流 patch。"""

    sections: list[str] = []
    if committed_diff.strip():
        sections.append(
            "# --- Vega committed diff: "
            f"{comparison_base_sha or '<unknown>'}..HEAD ---\n"
            + committed_diff.rstrip()
        )
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
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
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
    resolved_comparison_base = validate_comparison_base(
        repo,
        comparison_base_sha,
        head_sha=head_sha,
    )
    normalized_comparison_paths = normalize_comparison_paths(
        comparison_paths
    )
    committed = collect_comparison_changed_paths(
        repo,
        resolved_comparison_base,
        comparison_paths=normalized_comparison_paths,
        comparison_head_sha=head_sha,
    )
    final_head_sha = run_git_bytes(
        repo,
        ["git", "rev-parse", "--verify", "HEAD"],
    ).decode("utf-8", errors="replace").strip()
    if final_head_sha != head_sha:
        raise RuntimeError("scope gate 采集期间 Git HEAD 发生变化")
    return TrackedScopeSnapshot(
        head_sha=head_sha,
        status_sha256=_sha256(status_after),
        index_flags_sha256=_sha256(index_flags_after),
        staged_files=tuple(staged),
        unstaged_files=tuple(unstaged),
        untracked_files=tuple(untracked),
        unsafe_index_paths=tuple(unsafe_index_paths(index_flags_after)),
        comparison_base_sha=resolved_comparison_base,
        comparison_paths=normalized_comparison_paths,
        committed_files=tuple(committed),
    )


def validate_comparison_base(
    repo_path: Path,
    comparison_base_sha: str | None,
    *,
    head_sha: str | None = None,
) -> str | None:
    """确认 comparison base 是当前 HEAD 的真实祖先提交。"""

    if comparison_base_sha is None:
        return None
    normalized = comparison_base_sha.strip().lower()
    if len(normalized) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("comparison base 必须是完整的小写 Git commit 摘要")
    repo = repo_path.resolve()
    resolved = run_git_bytes(
        repo,
        ["git", "rev-parse", "--verify", f"{normalized}^{{commit}}"],
    ).decode("utf-8", errors="replace").strip()
    if resolved != normalized:
        raise ValueError("comparison base 未解析为指定 Git commit")
    current_head = head_sha or run_git_bytes(
        repo,
        ["git", "rev-parse", "--verify", "HEAD"],
    ).decode("utf-8", errors="replace").strip()
    ancestor = run_git_capture(
        repo,
        ["git", "merge-base", "--is-ancestor", normalized, current_head],
    )
    if ancestor.returncode != 0:
        raise ValueError("comparison base 不是当前 HEAD 的祖先提交")
    return normalized


def collect_comparison_changed_paths(
    repo_path: Path,
    comparison_base_sha: str | None,
    *,
    comparison_paths: tuple[str, ...] = (),
    comparison_head_sha: str = "HEAD",
) -> list[str]:
    if comparison_base_sha is None:
        if comparison_paths:
            raise ValueError("comparison paths 必须与 comparison base 一起使用")
        return []
    payload = run_git_bytes(
        repo_path,
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--name-status",
            "--find-renames",
            "-z",
            comparison_base_sha,
            comparison_head_sha,
            "--",
            *comparison_paths,
        ],
    )
    return _parse_comparison_name_status(payload)


def _parse_comparison_name_status(payload: bytes) -> list[str]:
    tokens = [item for item in payload.split(b"\0") if item]
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", errors="replace")
        index += 1
        if not status or status[0] not in {"A", "B", "C", "D", "M", "R", "T", "U", "X"}:
            raise RuntimeError("comparison diff 包含未知 name-status 记录")
        if status[0] in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise RuntimeError("comparison diff rename/copy 记录不完整")
            source = tokens[index].decode("utf-8", errors="replace")
            target = tokens[index + 1].decode("utf-8", errors="replace")
            index += 2
            if status[0] == "R":
                paths.extend([source, target])
            else:
                paths.append(target)
            continue
        if index >= len(tokens):
            raise RuntimeError("comparison diff 路径记录不完整")
        paths.append(tokens[index].decode("utf-8", errors="replace"))
        index += 1
    return list(dict.fromkeys(paths))


def normalize_comparison_paths(
    comparison_paths: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_path in comparison_paths:
        path = raw_path.strip()
        if (
            not path
            or path != raw_path
            or any(character in path for character in ("\r", "\n", "\0"))
            or any(
                unicodedata.category(character) in {"Cc", "Cf"}
                for character in path
            )
            or path.startswith(("/", "\\"))
            or "\\" in path
            or (len(path) >= 2 and path[0].isalpha() and path[1] == ":")
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("comparison path 必须是安全的仓库相对路径")
        normalized.append(path)
    if len(set(normalized)) != len(normalized):
        raise ValueError("comparison path 不能重复")
    return tuple(normalized)


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
