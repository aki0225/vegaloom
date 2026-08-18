from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


_WINDOWS_BATCH_SUFFIXES = {".bat", ".cmd"}


def prepare_windows_command(command: list[str]) -> list[str]:
    """为 Windows 准备可由 ``subprocess`` 安全启动的命令。"""

    launcher = Path(command[0])
    if launcher.suffix.lower() not in _WINDOWS_BATCH_SUFFIXES:
        return command
    npm_codex_command = _prepare_npm_codex_command(command, launcher)
    if npm_codex_command is not None:
        return npm_codex_command
    if launcher.stem.casefold() == "codex" and any(
        '"' in argument for argument in command[1:]
    ):
        raise OSError(
            "Windows Codex batch launcher 无法安全传递带双引号的配置参数"
        )
    return [
        os.environ.get("COMSPEC") or "cmd.exe",
        "/d",
        "/v:off",
        "/s",
        "/c",
        subprocess.list2cmdline(command),
    ]


def _prepare_npm_codex_command(
    command: list[str],
    launcher: Path,
) -> list[str] | None:
    """绕过 npm batch shim，避免 TOML 双引号被 ``%*`` 错误转义。"""

    if launcher.stem.casefold() != "codex":
        return None
    script = (
        launcher.parent
        / "node_modules"
        / "@openai"
        / "codex"
        / "bin"
        / "codex.js"
    )
    if not script.is_file():
        return None
    adjacent_node = launcher.with_name("node.exe")
    node = (
        os.fspath(adjacent_node)
        if adjacent_node.is_file()
        else shutil.which("node")
    )
    if node is None or Path(node).suffix.lower() in _WINDOWS_BATCH_SUFFIXES:
        return None
    return [node, os.fspath(script), *command[1:]]
