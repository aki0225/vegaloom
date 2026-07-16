from __future__ import annotations

import subprocess
from pathlib import Path


ALLOWED_CHECKS = {
    "git.status": ["git", "status", "--short"],
    "git.diff": ["git", "diff", "--stat"],
    "git.diff_check": ["git", "diff", "--check"],
}


def run_git(repo_path: Path, check_id: str) -> tuple[int, str, str]:
    if check_id not in ALLOWED_CHECKS:
        raise ValueError(f"git check 未在允许列表中：{check_id}")
    result = subprocess.run(
        ALLOWED_CHECKS[check_id],
        cwd=repo_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode, result.stdout, format_git_error(repo_path, result.stderr)


def format_git_error(repo_path: Path, stderr: str) -> str:
    if "dubious ownership" not in stderr.lower():
        return stderr
    repo = repo_path.resolve().as_posix()
    guidance = (
        "\nVega 检测到 Git safe.directory 拒绝访问。"
        "Vega 不会自动修改全局 Git 配置；请先确认该目录可信，再手动执行：\n"
        f'git config --global --add safe.directory "{repo}"\n'
    )
    return stderr.rstrip() + guidance
