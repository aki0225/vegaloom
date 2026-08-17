from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.codex_mcp_isolation import (
    CodexMcpIsolationError,
    build_mcp_disable_overrides,
)


def test_build_mcp_disable_overrides_disables_and_rechecks_all_servers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "fake-password-for-redaction-test"
    calls: list[list[str]] = []
    responses = iter(
        [
            [
                {
                    "name": "safe-server",
                    "enabled": True,
                    "transport": {
                        "type": "stdio",
                        "command": "fake-mcp",
                        "args": [f"--password={secret}"],
                    },
                },
                {
                    "name": "already_disabled",
                    "enabled": False,
                    "transport": {"type": "stdio"},
                },
            ],
            [
                {"name": "safe-server", "enabled": False},
                {"name": "already_disabled", "enabled": False},
            ],
        ]
    )

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(next(responses)),
            stderr=f"不会回显的敏感值：{secret}",
        )

    monkeypatch.setattr("vega.codex_mcp_isolation.subprocess.run", fake_run)

    overrides = build_mcp_disable_overrides(
        "codex",
        tmp_path,
        profile="vega-safe",
    )

    assert overrides == (
        "mcp_servers.safe-server.enabled=false",
        "mcp_servers.already_disabled.enabled=false",
    )
    assert calls[0] == [
        "codex",
        "--profile",
        "vega-safe",
        "mcp",
        "list",
        "--json",
    ]
    assert calls[1] == [
        *calls[0],
        "--config",
        "mcp_servers.safe-server.enabled=false",
        "--config",
        "mcp_servers.already_disabled.enabled=false",
    ]
    assert secret not in repr(overrides)
    assert secret not in repr(calls)


def test_build_mcp_disable_overrides_rejects_unaddressable_name_without_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsupported_name = "private.example"
    secret = "fake-secret-mcp-argument"

    monkeypatch.setattr(
        "vega.codex_mcp_isolation.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout=json.dumps(
                [
                    {
                        "name": unsupported_name,
                        "enabled": True,
                        "transport": {"args": [secret]},
                    }
                ]
            ),
            stderr="",
        ),
    )

    with pytest.raises(CodexMcpIsolationError) as exc_info:
        build_mcp_disable_overrides("codex", tmp_path, profile=None)

    message = str(exc_info.value)
    assert unsupported_name not in message
    assert secret not in message
    assert "拒绝启动 Supervisor Worker" in message


def test_build_mcp_disable_overrides_does_not_echo_failed_probe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "fake-secret-from-codex-stderr"

    monkeypatch.setattr(
        "vega.codex_mcp_isolation.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout=json.dumps({"password": secret}),
            stderr=secret,
        ),
    )

    with pytest.raises(CodexMcpIsolationError) as exc_info:
        build_mcp_disable_overrides("codex", tmp_path, profile=None)

    assert secret not in str(exc_info.value)


def test_mcp_probe_uses_shared_subprocess_compat_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logical_executable = (
        "C:/Tools/codex.CMD"  # repo-path-policy: allow-test-fixture
    )
    invocation = ["cmd.exe", "/d", "/s", "/c", "wrapped-codex-mcp"]
    captured: list[list[str] | str] = []

    def prepare(
        command: list[str] | str,
        *,
        windows: bool,
    ) -> list[str] | str:
        assert command == [logical_executable, "mcp", "list", "--json"]
        assert windows is True
        return invocation

    def run(command, **kwargs):
        del kwargs
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr(
        "vega.codex_mcp_isolation.prepare_subprocess_command",
        prepare,
    )
    monkeypatch.setattr("vega.codex_mcp_isolation.subprocess.run", run)

    assert build_mcp_disable_overrides(
        logical_executable,
        tmp_path,
        profile=None,
    ) == ()
    assert captured == [invocation]
