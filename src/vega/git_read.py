from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_git_bytes(
    repo_path: Path,
    command: list[str],
    *,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    result = run_git_capture(repo_path, command)
    stdout = coerce_git_output_bytes(result.stdout)
    stderr = coerce_git_output_bytes(result.stderr)
    if result.returncode not in allowed_returncodes:
        output = stdout.decode("utf-8", errors="replace") + format_git_error(
            repo_path,
            stderr.decode("utf-8", errors="replace"),
        )
        raise RuntimeError(output.strip())
    if result.returncode:
        return stdout + stderr
    return stdout


def run_git_text(
    repo_path: Path,
    command: list[str],
) -> str:
    result = run_git_capture(repo_path, command)
    stdout = coerce_git_output_bytes(result.stdout).decode(
        "utf-8",
        errors="replace",
    )
    stderr = coerce_git_output_bytes(result.stderr).decode(
        "utf-8",
        errors="replace",
    )
    return stdout + stderr


def run_git_capture(
    repo_path: Path,
    command: list[str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        harden_git_read_command(command),
        cwd=repo_path,
        capture_output=True,
        env=git_read_environment(),
        timeout=30,
        check=False,
    )


def harden_git_read_command(command: list[str]) -> list[str]:
    if not command or command[0] != "git":
        raise ValueError("Git 读取命令必须以 git 开头")
    return [
        "git",
        "-c",
        f"core.excludesFile={os.devnull}",
        "-c",
        f"core.attributesFile={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "diff.external=",
        *command[1:],
    ]


def git_read_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    return environment


def coerce_git_output_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return value or b""


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
