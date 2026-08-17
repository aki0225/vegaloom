from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .execution_process import prepare_subprocess_command


_MCP_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_MCP_SERVER_COUNT = 128


class CodexMcpIsolationError(RuntimeError):
    """无法证明 Codex 外部 MCP 已关闭。"""


def build_mcp_disable_overrides(
    executable: str,
    repo_path: Path,
    *,
    profile: str | None,
    timeout_seconds: float = 10.0,
) -> tuple[str, ...]:
    """读取 Codex 的有效 MCP 配置，并生成逐项禁用覆盖。

    Codex 的配置层采用合并语义，传入空表不能可靠移除已经继承的 MCP。这里先让
    Codex 自己解析配置层，只提取服务器名称，再逐项禁用并复查结果。探针输出可能
    含有服务器启动参数，因此任何错误路径都不能回显 stdout 或 stderr。
    """

    discovered = _list_mcp_servers(
        executable,
        repo_path,
        profile=profile,
        overrides=(),
        timeout_seconds=timeout_seconds,
    )
    overrides = tuple(
        f"mcp_servers.{name}.enabled=false" for name, _ in discovered
    )
    if not overrides:
        return ()

    verified = _list_mcp_servers(
        executable,
        repo_path,
        profile=profile,
        overrides=overrides,
        timeout_seconds=timeout_seconds,
    )
    if {name for name, _ in verified} != {name for name, _ in discovered}:
        raise CodexMcpIsolationError(
            "Codex MCP 配置在隔离检查期间发生变化，拒绝启动 Supervisor Worker。"
        )
    if any(enabled for _, enabled in verified):
        raise CodexMcpIsolationError(
            "无法确认 Codex MCP 已全部关闭，拒绝启动 Supervisor Worker。"
        )
    return overrides


def _list_mcp_servers(
    executable: str,
    repo_path: Path,
    *,
    profile: str | None,
    overrides: tuple[str, ...],
    timeout_seconds: float,
) -> tuple[tuple[str, bool], ...]:
    command = [executable]
    if profile:
        command.extend(["--profile", profile])
    command.extend(["mcp", "list", "--json"])
    for override in overrides:
        command.extend(["--config", override])

    try:
        completed = subprocess.run(
            prepare_subprocess_command(command, windows=os.name == "nt"),
            cwd=repo_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CodexMcpIsolationError(
            "无法读取 Codex MCP 配置，拒绝启动 Supervisor Worker。"
        ) from None
    if completed.returncode != 0:
        raise CodexMcpIsolationError(
            "Codex MCP 配置检查失败，拒绝启动 Supervisor Worker。"
        )

    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        raise CodexMcpIsolationError(
            "Codex MCP 配置输出无效，拒绝启动 Supervisor Worker。"
        ) from None
    if not isinstance(payload, list) or len(payload) > _MAX_MCP_SERVER_COUNT:
        raise CodexMcpIsolationError(
            "Codex MCP 配置结构无效，拒绝启动 Supervisor Worker。"
        )

    servers: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise CodexMcpIsolationError(
                "Codex MCP 配置结构无效，拒绝启动 Supervisor Worker。"
            )
        name = item.get("name")
        enabled = item.get("enabled")
        if (
            not isinstance(name, str)
            or not _MCP_SERVER_NAME_PATTERN.fullmatch(name)
            or not isinstance(enabled, bool)
            or name in seen
        ):
            raise CodexMcpIsolationError(
                "Codex MCP 标识或状态无效，拒绝启动 Supervisor Worker。"
            )
        seen.add(name)
        servers.append((name, enabled))
    return tuple(servers)
