from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .agent_repository_binding import require_git_root
from .git_read import git_read_environment, run_git_bytes, run_git_capture
from .redaction import redact_text
from .repository_identity import resolve_git_revision
from .tracked_workspace import (
    capture_tracked_scope_snapshot,
    collect_comparison_changed_paths,
)


_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class GitCandidateError(ValueError):
    """隔离 Worktree 或 Candidate Commit 无法安全建立。"""


@dataclass(frozen=True)
class ManagedChangeWorktree:
    run_id: str
    source_repo: Path
    worktree_path: Path
    branch: str
    base_sha: str

    def require_state(self, expected_head: str) -> None:
        branch = self.git_text(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"]
        )
        head = self.git_text(["git", "rev-parse", "--verify", "HEAD"])
        if branch != self.branch:
            raise GitCandidateError("Worker 改变了 ChangeRun 任务分支")
        if head != expected_head:
            raise GitCandidateError("Worker 改变了 Git HEAD，拒绝接管其提交")

    def git_text(self, command: list[str]) -> str:
        return self.git_bytes(command).decode("utf-8", errors="replace").strip()

    def git_bytes(self, command: list[str]) -> bytes:
        try:
            return run_git_bytes(self.worktree_path, command)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            raise GitCandidateError("无法读取 ChangeRun Git 状态") from exc

    def run_write(self, command: list[str], label: str) -> None:
        _run_git_write(self.worktree_path, command, label)

    def empty_hooks_dir(self) -> Path:
        root = self.worktree_path.parent
        hooks = root / ".empty-hooks"
        _prepare_plain_directory(hooks, root)
        return hooks


def prepare_managed_worktree(
    source_repo: Path,
    *,
    workspace_root: Path,
    run_id: str,
    base_revision: str = "HEAD",
) -> ManagedChangeWorktree:
    """从固定 revision 建立 Vega 自有的本地任务分支和隔离 Worktree。"""

    _validate_run_id(run_id)
    repo = require_git_root(source_repo)
    root = _prepare_workspace_root(repo, workspace_root)
    destination = root / run_id
    if os.path.lexists(destination):
        raise GitCandidateError("目标 ChangeRun Worktree 已存在，拒绝覆盖")

    resolved_base = resolve_git_revision(repo, base_revision)
    if resolved_base is None:
        raise GitCandidateError("无法解析 ChangeRun base revision")
    branch = f"vega/{run_id}"
    validate_managed_branch_name(branch)
    if _branch_exists(repo, branch):
        raise GitCandidateError("ChangeRun 本地任务分支已存在，必须显式恢复原任务")

    source_head = _read_git_text(
        repo,
        ["git", "rev-parse", "--verify", "HEAD"],
    )
    source_status = _source_status(repo)
    hooks_dir = root / ".empty-hooks"
    _prepare_plain_directory(hooks_dir, root)
    _run_git_write(
        repo,
        [
            "git",
            "-c",
            f"core.hooksPath={hooks_dir}",
            "worktree",
            "add",
            "-b",
            branch,
            str(destination),
            resolved_base.commit,
        ],
        "创建隔离 Worktree",
    )

    handle = _load_created_worktree(
        destination=destination,
        run_id=run_id,
        source_repo=repo,
        branch=branch,
        base_sha=resolved_base.commit,
    )
    _require_source_unchanged(
        repo,
        expected_head=source_head,
        expected_status=source_status,
    )
    return handle


def create_resume_checkpoint(
    handle: ManagedChangeWorktree,
    *,
    source_revision: str,
    task_card_path: str,
) -> str:
    """只提交本次 Task Card；旧卡已属于前一个 Accepted Checkpoint。"""

    handle.require_state(handle.base_sha)
    changed_cards = _changed_literal_paths(
        handle.source_repo,
        handle.base_sha,
        source_revision,
        [task_card_path],
    )
    if changed_cards != [task_card_path]:
        raise GitCandidateError("当前 Handoff 没有唯一新增的 Task Card")
    handle.run_write(
        [
            "git",
            "--literal-pathspecs",
            "checkout",
            source_revision,
            "--",
            *changed_cards,
        ],
        "恢复 Task Card 链",
    )
    staged = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    if (
        staged.head_sha != handle.base_sha
        or set(staged.staged_files) != set(changed_cards)
        or staged.unstaged_files
        or staged.untracked_files
        or staged.unsafe_index_paths
    ):
        raise GitCandidateError("Task Card 链无法安全写入恢复 Checkpoint")
    handle.run_write(
        [
            "git",
            "-c",
            "user.name=Vega Checkpoint",
            "-c",
            "user.email=vega-checkpoint@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={handle.empty_hooks_dir()}",
            "commit",
            "--no-gpg-sign",
            "--no-verify",
            "-m",
            "检查点：恢复跨机器 Task Card",
        ],
        "创建跨机器恢复 Checkpoint",
    )
    checkpoint_sha = handle.git_text(["git", "rev-parse", "--verify", "HEAD"])
    actual_files = collect_comparison_changed_paths(
        handle.worktree_path,
        handle.base_sha,
        comparison_head_sha=checkpoint_sha,
    )
    if set(actual_files) != set(changed_cards):
        raise GitCandidateError("恢复 Checkpoint 包含 Task Card 之外的变化")
    return checkpoint_sha


def restore_handoff_wip(
    handle: ManagedChangeWorktree,
    *,
    handoff_revision: str,
    restored_checkpoint_sha: str,
    changed_files: list[str],
) -> None:
    """把 Handoff 提交中的代码变化恢复为未暂存 WIP，旧门禁保持历史状态。"""

    handle.require_state(restored_checkpoint_sha)
    if not changed_files:
        return
    handle.run_write(
        [
            "git",
            "read-tree",
            "--reset",
            "-u",
            handoff_revision,
        ],
        "恢复 Handoff Git Tree",
    )
    staged = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    if (
        staged.head_sha != restored_checkpoint_sha
        or set(staged.staged_files) != set(changed_files)
        or staged.unstaged_files
        or staged.untracked_files
        or staged.unsafe_index_paths
    ):
        raise GitCandidateError("Handoff Git Tree 无法安全签出")
    handle.run_write(
        ["git", "reset", "--mixed", "HEAD"],
        "把 Handoff Git Tree 转为未暂存 WIP",
    )
    snapshot = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    observed = {
        *snapshot.staged_files,
        *snapshot.unstaged_files,
        *snapshot.untracked_files,
    }
    if (
        snapshot.head_sha != restored_checkpoint_sha
        or snapshot.staged_files
        or snapshot.unsafe_index_paths
        or observed != set(changed_files)
    ):
        raise GitCandidateError("Handoff WIP 恢复后的 Git 现场与 Resume Capsule 不一致")


def validate_managed_branch_name(branch: str) -> None:
    if (
        not branch.startswith("vega/")
        or branch in {".", ".."}
        or branch.endswith(("/", "."))
        or ".." in branch
        or "@{" in branch
        or re.search(r"[\x00-\x20~^:?*\\\[]", branch)
    ):
        raise ValueError("Candidate branch 不是合法的 Vega 本地任务分支")


def _load_created_worktree(
    *,
    destination: Path,
    run_id: str,
    source_repo: Path,
    branch: str,
    base_sha: str,
) -> ManagedChangeWorktree:
    try:
        worktree = require_git_root(destination)
        handle = ManagedChangeWorktree(
            run_id=run_id,
            source_repo=source_repo,
            worktree_path=worktree,
            branch=branch,
            base_sha=base_sha,
        )
        handle.require_state(base_sha)
        snapshot = capture_tracked_scope_snapshot(
            worktree,
            include_untracked=True,
        )
    except Exception as exc:
        if isinstance(exc, GitCandidateError):
            raise
        raise GitCandidateError("隔离 Worktree 创建后无法验证") from exc
    if (
        snapshot.staged_files
        or snapshot.unstaged_files
        or snapshot.untracked_files
        or snapshot.unsafe_index_paths
    ):
        raise GitCandidateError("新建 ChangeRun Worktree 不是干净状态")
    return handle


def _prepare_workspace_root(source_repo: Path, workspace_root: Path) -> Path:
    root = workspace_root
    if not root.is_absolute():
        raise GitCandidateError("ChangeRun workspace_root 必须是绝对路径")
    if not os.path.lexists(root):
        root.mkdir(parents=True)
    if _is_link_or_reparse(root):
        raise GitCandidateError("ChangeRun workspace_root 不能是链接或 reparse point")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise GitCandidateError("ChangeRun workspace_root 不是目录")
    if resolved == source_repo or resolved.is_relative_to(source_repo):
        raise GitCandidateError("隔离 Worktree 根目录不能位于用户当前工作区内")
    return resolved


def _prepare_plain_directory(path: Path, expected_parent: Path) -> None:
    if not os.path.lexists(path):
        path.mkdir()
    if _is_link_or_reparse(path):
        raise GitCandidateError("Vega Git 控制目录不能是链接或 reparse point")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or resolved.parent != expected_parent.resolve(strict=True):
        raise GitCandidateError("Vega Git 控制目录越过预期边界")


def _branch_exists(repo: Path, branch: str) -> bool:
    result = run_git_capture(
        repo,
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
    )
    if result.returncode not in {0, 1}:
        raise GitCandidateError("无法检查 ChangeRun 任务分支")
    return result.returncode == 0


def _require_source_unchanged(
    source_repo: Path,
    *,
    expected_head: str,
    expected_status: bytes,
) -> None:
    current_head = _read_git_text(
        source_repo,
        ["git", "rev-parse", "--verify", "HEAD"],
    )
    if current_head != expected_head or _source_status(source_repo) != expected_status:
        raise GitCandidateError("创建隔离 Worktree 期间用户当前工作区发生变化")


def _source_status(source_repo: Path) -> bytes:
    return run_git_bytes(
        source_repo,
        [
            "git",
            "status",
            "--porcelain=v2",
            "--branch",
            "-z",
            "--untracked-files=all",
        ],
    )


def _run_git_write(repo: Path, command: list[str], label: str) -> None:
    environment = git_read_environment()
    try:
        process = subprocess.run(
            command,
            cwd=repo,
            capture_output=True,
            env=environment,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitCandidateError(f"{label}无法执行") from exc
    if process.returncode != 0:
        stderr = process.stderr.decode("utf-8", errors="replace").strip()
        detail = redact_text(stderr) if stderr else "Git 返回非零状态"
        raise GitCandidateError(f"{label}失败：{detail}")


def _changed_literal_paths(
    repo: Path,
    base_revision: str,
    head_revision: str,
    paths: list[str],
) -> list[str]:
    raw = run_git_bytes(
        repo,
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--name-only",
            "-z",
            "--no-ext-diff",
            base_revision,
            head_revision,
            "--",
            *paths,
        ],
    )
    return [
        value.decode("utf-8", errors="strict")
        for value in raw.split(b"\0")
        if value
    ]


def _read_git_text(repo: Path, command: list[str]) -> str:
    try:
        return run_git_bytes(repo, command).decode(
            "utf-8",
            errors="replace",
        ).strip()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise GitCandidateError("无法读取 ChangeRun Git 状态") from exc


def _validate_run_id(run_id: str) -> None:
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise GitCandidateError("ChangeRun run_id 只能使用安全的短标识")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)
