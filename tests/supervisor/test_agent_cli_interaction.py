from __future__ import annotations

from pathlib import Path

import pytest

from vega.agent_cli_interaction import ProviderInteractionPump
from vega.provider_session import (
    PendingInteraction,
    ProviderSessionHandle,
    ProviderSessionState,
    load_provider_sessions,
    mutate_provider_sessions,
    save_provider_sessions,
    summarize_provider_interaction,
)


class _TtyInput:
    def __init__(self, line: str) -> None:
        self.line = line
        self.read_count = 0

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        self.read_count += 1
        return self.line


@pytest.mark.parametrize(
    ("method", "params"),
    [
        (
            "item/commandExecution/requestApproval",
            {
                "command": "rg TODO",
                "cwd": "managed-worktree",
                "commandActions": [{"type": "search"}],
            },
        ),
        (
            "item/commandExecution/requestApproval",
            {
                "command": "rg TODO",
                "cwd": "managed-worktree",
                "commandActions": [{"type": "search"}],
                "networkApprovalContext": {"host": "example.invalid"},
                "proposedExecpolicyAmendment": ["rg"],
                "additionalPermissions": {"network": {"enabled": True}},
            },
        ),
        (
            "item/fileChange/requestApproval",
            {
                "grantRoot": "requested-root",
                "reason": "需要扩大写入范围",
            },
        ),
    ],
)
def test_interaction_pump_never_approves_from_redacted_friendly_summary(
    tmp_path: Path,
    method: str,
    params: dict[str, object],
) -> None:
    summary = summarize_provider_interaction(method, params)
    run_dir = _run_dir(
        tmp_path,
        method=method,
        summary=summary,
    )
    input_stream = _TtyInput("y\n")
    pump = ProviderInteractionPump(run_dir, input_stream=input_stream)

    update = pump.poll()

    assert update.status == "attention"
    assert (
        update.reason_code
        == "provider.interaction_requires_advanced_response"
    )
    assert "原生会话" in (update.message or "")
    assert input_stream.read_count == 0
    interaction = load_provider_sessions(run_dir).interactions[0]
    assert interaction.status == "pending"
    assert interaction.response is None
    serialized = (run_dir / "provider-sessions.json").read_text(encoding="utf-8")
    assert "rg TODO" not in serialized
    assert "example.invalid" not in serialized
    assert "requested-root" not in serialized


def test_interaction_pump_never_reads_non_tty_or_json_input(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(
        tmp_path,
        method="item/commandExecution/requestApproval",
        summary="读取文件；检查项目规则",
    )
    input_stream = _TtyInput("y\n")

    non_tty = ProviderInteractionPump(
        run_dir,
        input_stream=input_stream,
        interactive=False,
    ).poll()
    json_output = ProviderInteractionPump(
        run_dir,
        input_stream=input_stream,
        json_output=True,
    ).poll()

    assert non_tty.status == "attention"
    assert (
        non_tty.reason_code
        == "provider.interaction_requires_advanced_response"
    )
    assert json_output.status == "attention"
    assert input_stream.read_count == 0
    assert load_provider_sessions(run_dir).interactions[0].status == "pending"


@pytest.mark.parametrize(
    ("method", "summary"),
    [
        (
            "item/commandExecution/requestApproval",
            "未分类命令执行（请接管原生会话确认）",
        ),
        ("item/fileChange/requestApproval", "文件修改"),
        ("item/permissions/requestApproval", "权限提升；需要网络访问"),
        ("item/tool/requestUserInput", "工具请求用户输入"),
        ("mcpServer/elicitation/request", "MCP 请求：example"),
    ],
)
def test_interaction_pump_routes_unsafe_requests_to_advanced_path(
    tmp_path: Path,
    method: str,
    summary: str,
) -> None:
    run_dir = _run_dir(tmp_path, method=method, summary=summary)
    input_stream = _TtyInput("y\n")

    update = ProviderInteractionPump(
        run_dir,
        input_stream=input_stream,
    ).poll()

    assert update.status == "attention"
    assert (
        update.reason_code
        == "provider.interaction_requires_advanced_response"
    )
    assert input_stream.read_count == 0
    assert load_provider_sessions(run_dir).interactions[0].status == "pending"


def test_interaction_pump_rejects_stale_turn_binding(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(
        tmp_path,
        method="item/fileChange/requestApproval",
        summary="文件修改；应用已批准范围内的补丁",
    )

    def change_turn(state: ProviderSessionState) -> None:
        state.handles["worker"].last_turn_id = "turn-replaced"

    mutate_provider_sessions(run_dir, "agent.session", change_turn)
    final = ProviderInteractionPump(
        run_dir,
        input_stream=_TtyInput("y\n"),
    ).poll()

    assert final.reason_code == "provider.interaction_binding_invalid"
    interaction = load_provider_sessions(run_dir).interactions[0]
    assert interaction.status == "pending"
    assert interaction.response is None


def test_command_summary_marks_mixed_unknown_actions_unclassified() -> None:
    summary = summarize_provider_interaction(
        "item/commandExecution/requestApproval",
        {
            "commandActions": [
                {"type": "read"},
                {"type": "unknown"},
            ],
        },
    )

    assert summary == "未分类命令执行（请接管原生会话确认）"


def _run_dir(tmp_path: Path, *, method: str, summary: str) -> Path:
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    state = ProviderSessionState(
        run_id=run_dir.name,
        handles={
            "worker": ProviderSessionHandle(
                provider="codex",
                role="worker",
                thread_id="thread-1",
                owner="vega",
                lifecycle="waiting_user",
                sandbox="workspace-write",
                approval_policy="on-request",
                permissions_verified=True,
                last_turn_id="turn-1",
            )
        },
        interactions=[
            PendingInteraction(
                interaction_id="request-1",
                role_key="worker",
                rpc_request_id="1",
                method=method,
                thread_id="thread-1",
                turn_id="turn-1",
                summary=summary,
            )
        ],
    )
    save_provider_sessions(run_dir, state)
    return run_dir
