from __future__ import annotations

from pathlib import Path

from ..git_read import coerce_git_output_bytes, format_git_error, run_git_capture


ALLOWED_CHECKS = {
    "git.status": ["git", "status", "--short"],
    "git.diff": ["git", "diff", "--no-ext-diff", "--no-textconv", "--stat"],
    "git.diff_check": ["git", "diff", "--no-ext-diff", "--no-textconv", "--check"],
}


def run_git(repo_path: Path, check_id: str) -> tuple[int, str, str]:
    if check_id not in ALLOWED_CHECKS:
        raise ValueError(f"git check 未在允许列表中：{check_id}")
    result = run_git_capture(
        repo_path,
        ALLOWED_CHECKS[check_id],
    )
    stdout = coerce_git_output_bytes(result.stdout).decode(
        "utf-8",
        errors="replace",
    )
    stderr = coerce_git_output_bytes(result.stderr).decode(
        "utf-8",
        errors="replace",
    )
    return result.returncode, stdout, format_git_error(repo_path, stderr)
