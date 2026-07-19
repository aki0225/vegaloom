from __future__ import annotations

import os
import subprocess
from hashlib import sha256
from pathlib import Path


def _git_repository_root(repo_path: Path) -> Path | None:
    repo = repo_path.resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法确认目标目录的 Git 仓库根。") from exc
    if result.returncode != 0 or not result.stdout.strip():
        return None

    git_root = Path(result.stdout.strip()).resolve()
    if git_root != repo:
        raise RuntimeError("目标目录必须是 Git 仓库根目录，不支持从仓库子目录编译项目上下文。")
    return git_root


def resolve_git_revision(repo_path: Path, revision: str = "HEAD") -> str | None:
    """在已确认的仓库根上解析固定 commit，避免多段上下文读取漂移。"""
    repo = _git_repository_root(repo_path)
    if repo is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=repo,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法解析 tracked 项目上下文 revision。") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("无法解析 tracked 项目上下文 revision；已拒绝使用空画像继续。")
    return result.stdout.strip()


def repository_scope(repo_path: Path) -> str:
    """生成不暴露绝对路径的本地仓库 scope，隔离同名仓库的 Memory。"""
    repo = repo_path.resolve()
    normalized_path = os.path.normcase(str(repo))
    digest = sha256(normalized_path.encode("utf-8")).hexdigest()[:16]
    return f"{repo.name}@{digest}"
