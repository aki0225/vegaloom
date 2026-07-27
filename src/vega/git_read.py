from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

SAFE_DIRECTORY_ENV = "VEGA_GIT_SAFE_DIRECTORY"


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
    repo = repo_path.resolve()
    safe_directory = resolve_explicit_safe_directory(repo)
    safe_config_path = (
        create_safe_directory_global_config(safe_directory)
        if safe_directory is not None
        else None
    )
    environment = git_read_environment()
    if safe_config_path is not None:
        environment["GIT_CONFIG_GLOBAL"] = str(safe_config_path)
    try:
        result = subprocess.run(
            harden_git_read_command(command),
            cwd=repo,
            capture_output=True,
            env=environment,
            timeout=30,
            check=False,
        )
    except BaseException:
        if safe_config_path is not None:
            try:
                safe_config_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    if safe_config_path is not None:
        try:
            safe_config_path.unlink()
        except OSError as exc:
            raise RuntimeError("无法清理隔离的 Git safe.directory 配置") from exc
    return result


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
    # Git 的仓库、index、object store 和命令级配置都可被 GIT_* 重定向。
    # 读取证据时只重新加入本模块明确允许的变量，避免 cwd 指向 A 仓库却读取 B 仓库。
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
        and key.upper() != SAFE_DIRECTORY_ENV
    }
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


def resolve_explicit_safe_directory(repo_path: Path) -> Path | None:
    raw_value = os.environ.get(SAFE_DIRECTORY_ENV)
    if not raw_value:
        return None
    candidate = Path(raw_value)
    if not candidate.is_absolute():
        raise RuntimeError(f"{SAFE_DIRECTORY_ENV} 必须使用目标仓库的绝对路径")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"{SAFE_DIRECTORY_ENV} 指向的目录无法解析") from exc
    if not resolved.is_dir():
        raise RuntimeError(f"{SAFE_DIRECTORY_ENV} 必须指向目标仓库目录")
    if resolved != repo_path.resolve():
        raise RuntimeError(f"{SAFE_DIRECTORY_ENV} 必须与目标仓库完全一致")
    return resolved


def create_safe_directory_global_config(safe_directory: Path) -> Path:
    path_text = safe_directory.as_posix()
    if any(ord(character) < 32 or ord(character) == 127 for character in path_text):
        raise RuntimeError(f"{SAFE_DIRECTORY_ENV} 不得包含控制字符")
    escaped_path = path_text.replace("\\", "\\\\").replace('"', '\\"')
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="vega-git-safe-",
            suffix=".config",
            delete=False,
        ) as stream:
            stream.write(f'[safe]\n\tdirectory = "{escaped_path}"\n')
            return Path(stream.name)
    except OSError as exc:
        raise RuntimeError("无法创建隔离的 Git safe.directory 配置") from exc


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
        "Vega 不会自动信任所有权异常的仓库，也不会修改全局 Git 配置。"
        "确认该目录可信后，请只在当前 shell 会话显式授权，然后重新运行 Vega：\n"
        f'PowerShell: $env:{SAFE_DIRECTORY_ENV} = "{repo}"\n'
        f'POSIX shell: export {SAFE_DIRECTORY_ENV}="{repo}"\n'
    )
    return stderr.rstrip() + guidance
