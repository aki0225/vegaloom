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
from .tracked_workspace import capture_tracked_scope_snapshot


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
