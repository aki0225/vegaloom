from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.agent_cli_interaction import ProviderInteractionPump, TerminalApprovalPrompt
from vega.codex_approval_context import AppServerApprovals
from vega.agent_contract import AgentState
from vega.agent_persistence import save_agent_state
from vega.cli_entrypoint import app
from vega.provider_session import (
    PendingInteraction,
    ProviderSessionHandle,
    ProviderSessionState,
    close_pending_interactions,
    ensure_session_handle,
    load_provider_sessions,
    mutate_provider_sessions,
    respond_to_interaction,
    save_provider_sessions,
    summarize_provider_interaction,
)


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
    pump = ProviderInteractionPump(run_dir)

    update = pump.poll()

    assert update.status == "attention"
    assert (
        update.reason_code
        == "provider.interaction_requires_advanced_response"
    )
    assert "原生会话" in (update.message or "")
    interaction = load_provider_sessions(run_dir).interactions[0]
    assert interaction.status == "pending"
    assert interaction.response is None
    serialized = (run_dir / "provider-sessions.json").read_text(encoding="utf-8")
    assert "rg TODO" not in serialized
    assert "example.invalid" not in serialized
    assert "requested-root" not in serialized


def test_interaction_pump_never_reads_non_tty_or_json_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, approvals = _complete_request(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    output = io.StringIO()
    monkeypatch.setattr("sys.stderr", output)
    monkeypatch.setattr(TerminalApprovalPrompt, "_read_available", lambda self: pytest.fail("不得读取 stdin"))
    for prompt in (None, TerminalApprovalPrompt(lambda update: None)):
        update = ProviderInteractionPump(run_dir, prompt=prompt).poll()
        assert update.status == "attention"
    assert output.getvalue() == ""
    approvals.close()

    assert update.status == "attention"
    assert (
        update.reason_code
        == "provider.interaction_requires_advanced_response"
    )
    assert load_provider_sessions(run_dir).interactions[0].status == "pending"


@pytest.mark.parametrize(("keys", "decision"), [("y\n", "accept"), ("\n", "decline")])
def test_terminal_approval_displays_bound_context_and_sends_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys: str, decision: str,
) -> None:
    class Tty(io.StringIO):
        def isatty(self):
            return True

    run_dir, approvals = _complete_request(tmp_path)
    output = Tty()
    monkeypatch.setattr("sys.stdin", Tty())
    monkeypatch.setattr("sys.stderr", output)
    monkeypatch.setattr(TerminalApprovalPrompt, "_discard_input", lambda self: None)

    def read_keys(self):
        assert threading.current_thread() is threading.main_thread()
        return keys

    monkeypatch.setattr(TerminalApprovalPrompt, "_read_available", read_keys)
    pump = ProviderInteractionPump(run_dir, prompt=TerminalApprovalPrompt(lambda update: None))
    assert pump.poll().status == "waiting"
    displayed = output.getvalue()
    assert "DO_NOT_PERSIST_APPROVAL" in displayed and "managed-worktree" in displayed
    assert "thread-1" in displayed and "turn-1" in displayed
    assert "\x1b" not in displayed and "\u202e" not in displayed
    assert "\\u001b" in displayed and "\\u202e" in displayed
    assert pump.poll().status == "waiting"
    sent = []
    assert approvals.send_responses(lambda rpc, response: sent.append((rpc, response)))
    assert not approvals.send_responses(lambda *args: pytest.fail("不得重复发送"))
    assert sent == [(99, {"decision": decision})]
    assert pump.poll().status == "idle"
    assert not list((run_dir / ".provider-approvals").glob("*.json"))
    assert "DO_NOT_PERSIST_APPROVAL" not in (run_dir / "provider-sessions.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["turn", "owner", "duplicate", "context", "display-turn", "native-resolved"])
def test_approval_rechecks_binding_after_display_and_before_native_send(
    tmp_path: Path, change: str,
) -> None:
    run_dir, approvals = _complete_request(tmp_path)

    class Prompt(TerminalApprovalPrompt):
        def poll_approval(self, interaction, context):
            if self.shown is None:
                self.shown = (interaction.interaction_id, interaction.context_digest)
                return None
            if change == "display-turn":
                mutate_provider_sessions(run_dir, "agent.session", lambda state: setattr(
                    state.handles["worker"], "last_turn_id", "turn-new",
                ))
            return "accept"

    prompt = Prompt(lambda update: None)
    pump = ProviderInteractionPump(run_dir, prompt=prompt)
    assert pump.poll().status == "waiting"
    interaction = load_provider_sessions(run_dir).interactions[0]
    # 响应已排队后接管/换 Turn 仍须在发送锁内拒绝。
    if change in {"turn", "owner"}:
        assert pump.poll().status == "waiting"
    elif change == "display-turn":
        assert pump.poll().status == "attention"
        assert load_provider_sessions(run_dir).interactions[0].response is None

    def mutation(state):
        if change == "turn":
            state.handles["worker"].last_turn_id = "turn-new"
        elif change == "owner":
            state.handles["worker"].owner = "human"
        elif change == "duplicate":
            state.interactions[0].status = "closed"

    mutate_provider_sessions(run_dir, "agent.session", mutation)
    if change == "native-resolved":
        approvals.resolve({"requestId": 99, "threadId": "unrelated-thread"})
        assert load_provider_sessions(run_dir).interactions[0].status == "pending"
        approvals.resolve({"requestId": 99, "threadId": "thread-1"})
        assert pump.poll().status == "idle"
        assert load_provider_sessions(run_dir).interactions[0].status == "closed"
    if change == "context":
        (run_dir / ".provider-approvals" / interaction.context_ref).write_text("{}", encoding="utf-8")
        assert pump.poll().status == "attention"
    elif change == "duplicate":
        assert pump.poll().status == "idle"
        with pytest.raises(ValueError):
            respond_to_interaction(run_dir, interaction.interaction_id, {"decision": "accept"}, expected=interaction)
    if change in {"turn", "owner"}:
        with pytest.raises(ValueError, match="owner/Thread/Turn"):
            approvals.send_responses(lambda *args: pytest.fail("不得发送旧授权"))
    else:
        assert not approvals.send_responses(lambda *args: pytest.fail("不得发送旧授权"))
    approvals.close()
    assert not list((run_dir / ".provider-approvals").glob("*.json"))


@pytest.mark.parametrize("extra", [
    {"networkApprovalContext": {"host": "example.invalid"}},
    {"additionalPermissions": {"network": {"enabled": True}}},
    {"proposedExecpolicyAmendment": ["echo"]},
])
def test_complete_command_with_unsupported_scope_never_prompts(tmp_path: Path, extra: dict) -> None:
    run_dir, approvals = _complete_request(tmp_path, extra=extra)

    class Prompt(TerminalApprovalPrompt):
        def poll_approval(self, interaction, context):
            pytest.fail("不支持的授权不能进入简化问答")

    assert ProviderInteractionPump(run_dir, prompt=Prompt(lambda update: None)).poll().status == "attention"
    assert load_provider_sessions(run_dir).interactions[0].context_ref is None
    approvals.close()


@pytest.mark.parametrize("variant", ["missing-item", "grant-root"])
def test_file_approval_requires_matching_changes_without_root_grant(tmp_path: Path, variant: str) -> None:
    method = "item/fileChange/requestApproval"
    run_dir = _run_dir(tmp_path, method=method, summary="文件修改")
    mutate_provider_sessions(run_dir, "agent.session", lambda state: state.interactions.clear())
    approvals = AppServerApprovals(run_dir, "worker", "managed-worktree", None)
    approvals.observe_item({"threadId": "thread-1", "turnId": "turn-1", "item": {
        "id": "file-1", "type": "fileChange", "changes": [{
            "path": "example.txt", "kind": {"type": "add"}, "diff": "+content",
        }],
    }})
    params = {"threadId": "thread-1", "turnId": "turn-1", "itemId": "file-1"}
    if variant == "missing-item":
        params["itemId"] = "file-unobserved"
    else:
        params["grantRoot"] = "expanded-root"
    approvals.record({"id": 99, "method": method, "params": params}, "thread-1", "turn-1")
    assert load_provider_sessions(run_dir).interactions[0].context_ref is None
    assert ProviderInteractionPump(run_dir, prompt=TerminalApprovalPrompt(lambda update: None)).poll().status == "attention"
    approvals.close()


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
    update = ProviderInteractionPump(run_dir).poll()

    assert update.status == "attention"
    assert (
        update.reason_code
        == "provider.interaction_requires_advanced_response"
    )
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
    final = ProviderInteractionPump(run_dir).poll()

    assert final.reason_code == "provider.interaction_binding_invalid"
    interaction = load_provider_sessions(run_dir).interactions[0]
    assert interaction.status == "pending"
    assert interaction.response is None


def test_respond_to_interaction_rejects_unowned_handle_without_expected_provider(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(
        tmp_path,
        method="item/fileChange/requestApproval",
        summary="文件修改；应用已批准范围内的补丁",
    )

    def handoff(state: ProviderSessionState) -> None:
        state.handles["worker"].owner = "human"

    mutate_provider_sessions(run_dir, "agent.takeover", handoff)

    with pytest.raises(ValueError, match="当前 Provider Turn"):
        respond_to_interaction(
            run_dir,
            "request-1",
            {"decision": "accept"},
        )

    interaction = load_provider_sessions(run_dir).interactions[0]
    assert interaction.status == "pending"
    assert interaction.response is None


@pytest.mark.parametrize("arguments", [
    ["steer", "--text", "继续检查"],
    ["respond", "--interaction", "request-1", "--decision", "accept"],
    ["takeover"],
    ["reclaim"],
])
def test_legacy_protocol_rejects_provider_controls_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    run_dir = _run_dir(tmp_path, method="item/commandExecution/requestApproval", summary="执行命令")
    save_agent_state(run_dir / "agent-state.json", AgentState(
        run_id=run_dir.name, task_id="task-1", repository_id="repo-1",
    ))
    before = (run_dir / "provider-sessions.json").read_bytes()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, [*arguments, "--run", run_dir.name])

    assert result.exit_code != 0
    assert "执行协议" in result.output
    assert (run_dir / "provider-sessions.json").read_bytes() == before


@pytest.mark.parametrize("role", ["worker", "reviewer:WI-01"])
def test_steer_checks_current_revision_inside_enqueue_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str,
) -> None:
    run_dir = _run_dir(tmp_path, method="item/fileChange/requestApproval", summary="修改文件")
    current = AgentState(
        run_id=run_dir.name, task_id="task-1", repository_id="repo-1",
        run_kind="change", execution_protocol=2, contract_revision=1,
        execution_plan_revision=1, accepted_checkpoint_sha="a" * 40,
    )
    save_agent_state(run_dir / "agent-state.json", current)

    def prepare_session(state):
        ensure_session_handle(
            state, role, work_item_id="WI-01", contract_revision=current.contract_revision,
            plan_revision=current.plan_revision,
        )

    mutate_provider_sessions(run_dir, "agent.session", prepare_session)
    monkeypatch.chdir(tmp_path)
    args = ["steer", "--run", run_dir.name, "--role", role, "--text", "继续检查"]
    assert CliRunner().invoke(app, args).exit_code == 0
    before = (run_dir / "provider-sessions.json").read_bytes()

    def revise_before_enqueue(text, path):
        save_agent_state(run_dir / "agent-state.json", current.model_copy(update={
            "contract_revision": 2, "execution_plan_revision": 2, "plan_revision": 2,
        }))
        return text

    # CLI 已读取旧状态后发生 revision；必须在入队锁内重读，而非只校验锁外快照。
    with monkeypatch.context() as patch:
        patch.setattr("vega.agent_cli._load_text_choice", revise_before_enqueue)
        result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    assert "尚未发送" in result.output and "新会话准备后重新提交" in result.output
    assert (run_dir / "provider-sessions.json").read_bytes() == before

    current = current.model_copy(update={
        "contract_revision": 2, "execution_plan_revision": 2, "plan_revision": 2,
    })
    mutate_provider_sessions(run_dir, "agent.session", prepare_session)
    assert CliRunner().invoke(app, args).exit_code == 0
    steers = load_provider_sessions(run_dir).steers
    assert [item.status for item in steers] == ["rejected", "queued"]

    save_agent_state(run_dir / "agent-state.json", current.model_copy(update={
        "execution_plan_revision": 3, "plan_revision": 3,
    }))
    result = CliRunner().invoke(app, args)
    assert (result.exit_code == 0) == (role == "worker")
    before = (run_dir / "provider-sessions.json").read_bytes()
    (run_dir / "agent-state.json").unlink()
    assert CliRunner().invoke(app, args).exit_code != 0
    assert (run_dir / "provider-sessions.json").read_bytes() == before


def test_close_pending_interactions_removes_response_dead_end(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(
        tmp_path,
        method="item/fileChange/requestApproval",
        summary="文件修改；应用已批准范围内的补丁",
    )

    assert close_pending_interactions(run_dir) == 1
    interaction = load_provider_sessions(run_dir).interactions[0]
    assert interaction.status == "closed"
    assert interaction.response is None
    with pytest.raises(ValueError, match="不存在"):
        respond_to_interaction(
            run_dir,
            "request-1",
            {"decision": "accept"},
        )


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


def _complete_request(tmp_path: Path, *, extra: dict | None = None) -> tuple[Path, AppServerApprovals]:
    method = "item/commandExecution/requestApproval"
    run_dir = _run_dir(tmp_path, method=method, summary="命令请求")
    mutate_provider_sessions(run_dir, "agent.session", lambda state: state.interactions.clear())
    approvals = AppServerApprovals(run_dir, "worker", "managed-worktree", None)
    approvals.record({"id": 99, "method": method, "params": {
        "threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "startedAtMs": 0,
        "command": "echo DO_NOT_PERSIST_APPROVAL\x1b[2J\u202e", "cwd": "managed-worktree",
        **(extra or {}),
    }}, "thread-1", "turn-1")
    return run_dir, approvals


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
