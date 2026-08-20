from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .agent_contract import AgentCheckpoint, AgentState
from .git_read import run_git_capture


_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)"
    r"[^\r\n\t\"'`<>]*"
)
_POSIX_LOCAL_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_>])/"
    r"(?:home|Users|tmp|private/var|var/folders|var/tmp)"
    r"(?![A-Za-z0-9_.-])"
    r"(?:/[^\r\n\t\"'`<>]*)?"
)
_HTTP_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s\"'`<>]+")
_PLACEHOLDER_PATH_PATTERN = re.compile(
    r"<[A-Za-z0-9_-]+>(?:[\\/][^\s\"'`<>]+)*"
)
_LOCAL_FILE_URI_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9+.-])file:")
_URL_COMPONENT_LOCAL_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_>])/"
    r"(?:home|Users|private/var|var/folders|var/tmp)"
    r"(?![A-Za-z0-9_.-])"
    r"(?:/[^\r\n\t\"'`<>]*)?"
)


class TaskCardError(ValueError):
    pass


class _HandoffCard(Protocol):
    base_revision: str
    handoff_base_revision: str | None


def assert_portable_task_card_payload(value: object) -> None:
    if isinstance(value, str):
        candidate = _PLACEHOLDER_PATH_PATTERN.sub("", value)
        if (
            _LOCAL_FILE_URI_PATTERN.search(candidate)
            or _WINDOWS_ABSOLUTE_PATH_PATTERN.search(candidate)
            or any(
                _url_contains_local_reference(match.group())
                for match in _HTTP_URL_PATTERN.finditer(candidate)
            )
        ):
            raise TaskCardError("Task Card 不能包含本机绝对路径")
        candidate = _HTTP_URL_PATTERN.sub("", candidate)
        if (
            _WINDOWS_ABSOLUTE_PATH_PATTERN.search(candidate)
            or _POSIX_LOCAL_PATH_PATTERN.search(candidate)
        ):
            raise TaskCardError("Task Card 不能包含本机绝对路径")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert_portable_task_card_payload(key)
            assert_portable_task_card_payload(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            assert_portable_task_card_payload(item)


def _url_contains_local_reference(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    for component in (parts.query, parts.fragment):
        candidate = component
        for _ in range(8):
            if (
                _LOCAL_FILE_URI_PATTERN.search(candidate)
                or _WINDOWS_ABSOLUTE_PATH_PATTERN.search(candidate)
                or _URL_COMPONENT_LOCAL_PATH_PATTERN.search(candidate)
            ):
                return True
            decoded = unquote(candidate)
            if decoded == candidate:
                break
            candidate = decoded
        else:
            # 深层重复编码无法可靠解释，公开 Task Card 选择 fail-closed。
            return True
    return False


def collect_handoff_issues(
    state: AgentState,
    checkpoint: AgentCheckpoint,
    snapshot: Any,
) -> list[str]:
    checks = (
        (
            state.phase == "needs_human",
            "源 Agent run 仍处于 needs_human，新机器只能继续人工对账",
        ),
        (
            checkpoint.run_id != state.run_id,
            "最近 Checkpoint 与 Agent run 身份不一致",
        ),
        (checkpoint.status != "safe", "最近 Checkpoint 未证明现场为 safe"),
        (
            checkpoint.phase != state.phase,
            "最近 Checkpoint 与 Agent State 阶段不一致",
        ),
        (
            checkpoint.current_work_item != state.current_work_item,
            "最近 Checkpoint 与 Agent State 的 Work Item 不一致",
        ),
        (checkpoint.active_child_run is not None, "最近 Checkpoint 仍记录 active child"),
        (checkpoint.operation_started, "最近 Checkpoint 仍记录 operation 已开始"),
        (
            bool(state.workspace_fingerprint)
            and state.workspace_fingerprint != snapshot.fingerprint,
            "当前 Workspace fingerprint 与 Agent State 不一致",
        ),
        (
            checkpoint.workspace_fingerprint != snapshot.fingerprint,
            "当前 Workspace fingerprint 与最近 Checkpoint 不一致",
        ),
        (
            bool(snapshot.unsafe_index_paths),
            "Git index 存在无法解释的 skip-worktree 或 assume-unchanged 路径",
        ),
        (not snapshot.git_control_complete, "Git control manifest 不完整"),
        (checkpoint.external_side_effects == "unknown", "存在未知外部副作用"),
    )
    return [message for failed, message in checks if failed]


def prepare_task_card_root(repo: Path, month: str) -> Path:
    root = repo.resolve(strict=True)
    current = root
    for part in (".vega", "tasks", month):
        current = current / part
        if not os.path.lexists(current):
            try:
                current.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise TaskCardError("无法创建 Task Card 目录") from exc
        _require_plain_task_card_directory(root, current)
    return current


def require_plain_task_card_tree(repo: Path, directory: Path) -> None:
    root = repo.resolve(strict=True)
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        _require_plain_task_card_directory(root, current)


def validate_handoff_history(
    repo: Path,
    card: _HandoffCard,
    relative_task: str,
) -> str:
    head = run_git_capture(repo, ["git", "rev-parse", "--verify", "HEAD"])
    if head.returncode != 0:
        raise ValueError("无法读取当前 Git HEAD")
    head_sha = head.stdout.decode("utf-8", errors="replace").strip()

    task_commit = run_git_capture(
        repo,
        ["git", "log", "-1", "--format=%H", "--", relative_task],
    )
    task_commit_sha = task_commit.stdout.decode("utf-8", errors="replace").strip()
    if task_commit.returncode != 0 or not task_commit_sha:
        raise ValueError("无法定位包含 Task Card 的 Handoff 提交")
    if task_commit_sha != head_sha:
        raise ValueError("当前 HEAD 不是包含 Task Card 的 Handoff 提交")

    revisions = (
        ("运行基线", card.base_revision),
        ("Handoff 基线", card.handoff_base_revision),
    )
    for label, revision in revisions:
        if revision is None:
            raise ValueError(f"{label}缺失")
        ancestor = run_git_capture(
            repo,
            ["git", "merge-base", "--is-ancestor", revision, head_sha],
        )
        if ancestor.returncode != 0:
            raise ValueError(f"{label}不属于当前仓库的 Handoff 历史")
    return head_sha


def _require_plain_task_card_directory(repo: Path, path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TaskCardError("Task Card 目录无法读取") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(file_attributes & reparse_flag)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise TaskCardError("Task Card 目录不能包含链接、junction 或 reparse point")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TaskCardError("Task Card 目录无法解析") from exc
    if not resolved.is_relative_to(repo):
        raise TaskCardError("Task Card 目录逃出目标仓库")
