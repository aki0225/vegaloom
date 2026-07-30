from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from .git_read import coerce_git_output_bytes, run_git_capture


_RESOLVED_GIT_REVISION_PROOF = object()


@dataclass(frozen=True, init=False)
class ResolvedGitRevision:
    """绑定仓库根目录的已校验 commit，仅在同一次读取事务中复用。"""

    repo_key: str
    commit: str
    _proof: object = field(repr=False, compare=False)


def _make_resolved_git_revision(
    *,
    repo_key: str,
    commit: str,
) -> ResolvedGitRevision:
    resolved = object.__new__(ResolvedGitRevision)
    object.__setattr__(resolved, "repo_key", repo_key)
    object.__setattr__(resolved, "commit", commit)
    object.__setattr__(resolved, "_proof", _RESOLVED_GIT_REVISION_PROOF)
    return resolved


def _is_full_object_id(value: str) -> bool:
    return len(value) in {40, 64} and all(
        character in "0123456789abcdefABCDEF"
        for character in value
    )


def _git_repository_root(repo_path: Path) -> Path | None:
    repo = repo_path.resolve()
    try:
        result = run_git_capture(
            repo,
            ["git", "rev-parse", "--show-toplevel"],
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法确认目标目录的 Git 仓库根。") from exc
    stdout = coerce_git_output_bytes(result.stdout).decode(
        "utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not stdout.strip():
        return None

    git_root = Path(stdout.strip()).resolve()
    if git_root != repo:
        raise RuntimeError("目标目录必须是 Git 仓库根目录，不支持从仓库子目录编译项目上下文。")
    return git_root


def resolve_git_revision(
    repo_path: Path,
    revision: str | ResolvedGitRevision = "HEAD",
) -> ResolvedGitRevision | None:
    """在已确认的仓库根上解析固定 commit，避免多段上下文读取漂移。"""
    requested_repo = repo_path.resolve()
    requested_repo_key = os.path.normcase(str(requested_repo))
    if isinstance(revision, ResolvedGitRevision):
        if (
            getattr(revision, "_proof", None)
            is not _RESOLVED_GIT_REVISION_PROOF
        ):
            raise RuntimeError("已解析 revision 未经过当前读取事务校验。")
        if revision.repo_key != requested_repo_key:
            raise RuntimeError("已解析 revision 与目标仓库不匹配。")
        return revision

    repo = _git_repository_root(requested_repo)
    if repo is None:
        return None
    try:
        result = run_git_capture(
            repo,
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法解析 tracked 项目上下文 revision。") from exc
    stdout = coerce_git_output_bytes(result.stdout).decode(
        "utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not stdout.strip():
        raise RuntimeError("无法解析 tracked 项目上下文 revision；已拒绝使用空画像继续。")
    commit = stdout.strip()
    if not _is_full_object_id(commit):
        raise RuntimeError("tracked 项目上下文 revision 不是完整 commit OID。")
    return _make_resolved_git_revision(
        repo_key=os.path.normcase(str(repo)),
        commit=commit,
    )


def repository_scope(repo_path: Path) -> str:
    """生成不暴露绝对路径的本地仓库 scope，隔离同名仓库的 Memory。"""
    repo = repo_path.resolve()
    normalized_path = os.path.normcase(str(repo))
    digest = sha256(normalized_path.encode("utf-8")).hexdigest()[:16]
    return f"{repo.name}@{digest}"
