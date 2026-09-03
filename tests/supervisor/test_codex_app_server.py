from __future__ import annotations

import json
import hashlib
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega import codex_app_server_process
from vega.codex_app_server import (
    _AppServerClient,
    _app_server_command,
)
from vega.codex_app_server_permissions import require_thread_permissions
from vega.codex_app_server_runner import (
    CodexAppServerRunner,
    _strict_output_schema,
)
from vega.codex_app_server_process import (
    AppServerInvocation,
    app_server_process_options,
    install_parent_termination_handler,
)
from vega.execution_control import RunnerExecutionContext
from vega.project_config import CodexExecOptions
from vega.provider_session import (
    PendingSteer,
    ensure_session_handle,
    load_provider_sessions,
    mutate_provider_sessions,
    queue_steer,
    respond_to_interaction,
    set_session_owner,
    summarize_provider_interaction,
)
from vega.review_contract import ReviewVerdict


def test_command_approval_summary_hides_unknown_action_label() -> None:
    summary = summarize_provider_interaction(
        "item/commandExecution/requestApproval",
        {
            "commandActions": [
                {"type": "unknown", "command": "opaque"},
            ],
            "reason": "需要删除生成缓存",
        },
    )

    assert summary == (
        "未分类命令执行（请接管原生会话确认）；需要删除生成缓存"
    )


def test_app_server_reuses_thread_and_injects_pending_anchor(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_app_server(repo)
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    _write_task_anchor_inputs(run_dir)
    events: list[str] = []
    runner = _runner(run_dir)

    first = runner.run(
        "第一轮 EARLY_COMPLETE",
        repo,
        sandbox="read-only",
        timeout_seconds=30,
        execution_context=_execution_context(
            run_dir,
            "first",
            events,
        ),
    )
    assert first.status == "success"
    first_state = load_provider_sessions(run_dir)
    assert first_state.handles["worker"].thread_id == "thread-1"
    assert first_state.handles["worker"].compaction_pending is True
    assert first_state.handles["worker"].total_tokens == 42

    queue_steer(run_dir, "worker", "补充检查边界条件")
    second = runner.run(
        "第二轮",
        repo,
        sandbox="workspace-write",
        timeout_seconds=30,
        execution_context=_execution_context(
            run_dir,
            "second",
            events,
        ),
    )

    assert second.status == "success"
    fake_state = json.loads(
        (repo / ".fake-app-server-state.json").read_text(encoding="utf-8")
    )
    assert fake_state["starts"] == 1
    assert fake_state["resumes"] == 1
    assert fake_state["thread_start_params"]["model"] == "fake-model"
    assert fake_state["thread_start_params"]["approvalPolicy"] == "never"
    assert fake_state["turn_start_params"][0]["effort"] == "high"
    assert fake_state["resume_params"][0]["sandbox"] == "workspace-write"
    assert fake_state["resume_params"][0]["approvalPolicy"] == "on-request"
    assert fake_state["server_args"] == ["--listen", "stdio://"]
    opt_out = fake_state["initialize_params"]["capabilities"][
        "optOutNotificationMethods"
    ]
    assert "item/agentMessage/delta" in opt_out
    assert "item/completed" not in opt_out
    assert "Vega Task Anchor" in fake_state["prompts"][1]
    assert "补充检查边界条件" in fake_state["prompts"][1]
    assert any(event.endswith("thread_ready") for event in events)
    assert any(event.endswith("context_compacted") for event in events)
    current = load_provider_sessions(run_dir).handles["worker"]
    assert current.sandbox == "workspace-write"
    assert current.approval_policy == "on-request"
    assert current.permissions_verified is True
    profile_runner = CodexAppServerRunner(
        run_dir,
        "worker",
        work_item_id="WI-01",
        contract_revision=1,
        plan_revision=1,
        options=CodexExecOptions(profile="vega-test"),
    )
    assert profile_runner._global_args() == ["--profile", "vega-test"]


def test_app_server_wraps_windows_batch_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = tmp_path / "codex.CMD"
    launcher.write_text("@echo off\n", encoding="utf-8")
    command_shell = tmp_path / "cmd.exe"
    monkeypatch.setenv("COMSPEC", str(command_shell))
    invocation = AppServerInvocation(
        executable=str(launcher),
        run_dir=str(tmp_path / "run"),
        role_key="worker",
        repo_path=str(tmp_path / "repo"),
        sandbox="workspace-write",
    )

    command = _app_server_command(invocation, windows=True)

    assert isinstance(command, list)
    assert command[:5] == [
        str(command_shell),
        "/d",
        "/v:off",
        "/s",
        "/c",
    ]
    assert str(launcher) in command[5]
    assert "app-server" in command[5]
    assert app_server_process_options(windows=True) == {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
    }
    assert app_server_process_options(windows=False) == {
        "start_new_session": True
    }


def test_posix_helper_installs_controlled_termination_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: dict[int, object] = {}
    monkeypatch.setattr(
        signal,
        "signal",
        lambda signum, handler: installed.__setitem__(signum, handler),
    )

    install_parent_termination_handler(windows=False)

    handler = installed[signal.SIGTERM]
    with pytest.raises(SystemExit, match=str(128 + signal.SIGTERM)):
        handler(signal.SIGTERM, None)


def test_posix_root_probe_explicitly_uses_posix_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        codex_app_server_process,
        "is_process_alive",
        lambda process_id, *, windows: observed.append((process_id, windows))
        or False,
    )

    assert codex_app_server_process._posix_root_process_alive(42) is False
    assert observed == [(42, False)]


def test_posix_shutdown_checks_process_group_after_root_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(pid=42, poll=lambda: 0)
    monkeypatch.setattr(
        codex_app_server_process,
        "_posix_process_group_alive",
        lambda process_group_id: process_group_id == 42,
    )
    observed: list[int] = []

    def terminate(current, *_args):
        observed.append(current.pid)
        return codex_app_server_process.ProcessTerminationResult(True, "done")

    monkeypatch.setattr(
        codex_app_server_process,
        "terminate_posix_process",
        terminate,
    )

    result = codex_app_server_process.terminate_app_server_tree(
        process,  # type: ignore[arg-type]
        windows=False,
    )

    assert result.succeeded is True
    assert observed == [42]


def test_app_server_rejects_unconfirmed_process_tree_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = AppServerInvocation(
        executable="codex",
        run_dir=str(tmp_path),
        role_key="worker",
        repo_path=str(tmp_path),
        sandbox="workspace-write",
    )
    client = _AppServerClient(invocation)
    client.process = SimpleNamespace(stdin=None)
    emitted: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "vega.codex_app_server.terminate_app_server_tree",
        lambda *_args, **_kwargs: codex_app_server_process.ProcessTerminationResult(
            False,
            "unconfirmed",
        ),
    )
    monkeypatch.setattr(client, "_set_lifecycle", lambda *_args: None)
    monkeypatch.setattr(
        "vega.codex_app_server._emit_result",
        lambda status, **kwargs: emitted.append((status, kwargs.get("error"))),
    )

    assert client._shutdown() is False
    assert emitted == [("error", "App Server 进程树终止未确认。")]


def test_app_server_normalizes_reviewer_output_schema() -> None:
    original = ReviewVerdict.model_json_schema()

    normalized = _strict_output_schema(original)

    assert isinstance(normalized, dict)
    assert normalized["required"] == list(normalized["properties"])
    finding = normalized["$defs"]["ReviewFinding"]
    assert finding["required"] == list(finding["properties"])
    assert original["required"] == ["summary"]


def test_app_server_rejects_unverified_read_only_permission() -> None:
    with pytest.raises(RuntimeError, match="实际 sandbox 与请求不一致"):
        require_thread_permissions(
            {
                "sandbox": {"type": "workspaceWrite"},
                "approvalPolicy": "never",
            },
            requested_sandbox="read-only",
        )


def test_app_server_waits_for_explicit_approval_response(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_app_server(repo)
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    runner = _runner(run_dir)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            runner.run,
            "ASK_APPROVAL",
            repo,
            sandbox="workspace-write",
            timeout_seconds=30,
            execution_context=_execution_context(run_dir, "approval", []),
        )
        interaction_id = _wait_for_interaction(run_dir)
        respond_to_interaction(
            run_dir,
            interaction_id,
            {"decision": "accept"},
        )
        result = future.result(timeout=20)

    assert result.status == "success"
    state = load_provider_sessions(run_dir)
    interaction = next(
        item
        for item in state.interactions
        if item.interaction_id == interaction_id
    )
    assert interaction.status == "closed"
    fake_state = json.loads(
        (repo / ".fake-app-server-state.json").read_text(encoding="utf-8")
    )
    assert fake_state["approval_response"] == {"decision": "accept"}


def test_app_server_preserves_safe_turn_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_app_server(repo)
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)

    result = _runner(run_dir).run(
        "FAIL_TURN",
        repo,
        sandbox="workspace-write",
        timeout_seconds=30,
        execution_context=_execution_context(run_dir, "failure", []),
    )

    assert result.status == "error"
    assert result.error is not None
    assert "fake turn failed" in result.error


def test_app_server_ignores_unknown_event_flood(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_app_server(repo)
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)

    result = _runner(run_dir).run(
        "UNKNOWN_EVENT_FLOOD",
        repo,
        sandbox="workspace-write",
        timeout_seconds=30,
        execution_context=_execution_context(run_dir, "unknown-events", []),
    )

    assert result.status == "success"
    assert "fake worker 完成" in result.output


def test_app_server_retries_overload_with_fixed_budget(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_app_server(repo)
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)

    recovered = _runner(run_dir).run(
        "OVERLOAD_THEN_SUCCESS",
        repo,
        sandbox="workspace-write",
        timeout_seconds=30,
        execution_context=_execution_context(run_dir, "overload-recovered", []),
    )
    exhausted = _runner(run_dir).run(
        "OVERLOAD_ALWAYS",
        repo,
        sandbox="workspace-write",
        timeout_seconds=30,
        execution_context=_execution_context(run_dir, "overload-exhausted", []),
    )

    assert recovered.status == "success"
    fake_state = json.loads(
        (repo / ".fake-app-server-state.json").read_text(encoding="utf-8")
    )
    assert fake_state["recoverable_overloads"] == 2
    assert fake_state["exhausted_overloads"] == 4
    assert exhausted.status == "error"
    assert exhausted.error is not None
    assert "-32001" in exhausted.error


def test_app_server_does_not_wait_for_inherited_stderr_handle(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fake_app_server(repo)
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    started_at = time.monotonic()

    result = _runner(run_dir).run(
        "HOLD_STDERR",
        repo,
        sandbox="workspace-write",
        timeout_seconds=30,
        execution_context=_execution_context(run_dir, "stderr-handle", []),
    )

    assert result.status == "success"
    assert time.monotonic() - started_at < 6


def test_provider_session_resets_on_contract_revision_and_has_one_owner(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)

    def first(state) -> None:
        handle = ensure_session_handle(
            state,
            "worker",
            work_item_id="WI-01",
            contract_revision=1,
            plan_revision=1,
        )
        handle.thread_id = "thread-old"
        handle.lifecycle = "idle"

    mutate_provider_sessions(run_dir, "agent.session", first)
    set_session_owner(run_dir, "worker", "human")
    with pytest.raises(ValueError, match="人工接管"):
        queue_steer(run_dir, "worker", "继续执行")

    def revise(state) -> None:
        ensure_session_handle(
            state,
            "worker",
            work_item_id="WI-01",
            contract_revision=2,
            plan_revision=2,
        )

    mutate_provider_sessions(run_dir, "agent.session", revise)
    handle = load_provider_sessions(run_dir).handles["worker"]
    assert handle.thread_id is None
    assert handle.lifecycle == "new"
    assert handle.owner == "human"

    def reviewer(state) -> None:
        handle = ensure_session_handle(
            state,
            "reviewer:WI-01",
            work_item_id="WI-01",
            contract_revision=2,
            plan_revision=1,
        )
        handle.thread_id = "review-thread-old"
        handle.turn_count = 2

    mutate_provider_sessions(run_dir, "agent.session", reviewer)

    def replan(state) -> None:
        ensure_session_handle(
            state,
            "reviewer:WI-01",
            work_item_id="WI-01",
            contract_revision=2,
            plan_revision=2,
        )

    mutate_provider_sessions(run_dir, "agent.session", replan)
    reviewer_handle = load_provider_sessions(run_dir).handles["reviewer:WI-01"]
    assert reviewer_handle.thread_id is None
    assert reviewer_handle.turn_count == 0
    assert reviewer_handle.last_event == "review_plan_revision_changed"


def test_provider_session_history_keeps_pending_items(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)

    def fill(state) -> None:
        handle = ensure_session_handle(
            state,
            "worker",
            work_item_id="WI-01",
            contract_revision=1,
            plan_revision=1,
        )
        handle.thread_id = "thread-1"
        for index in range(101):
            state.steers.append(
                PendingSteer(
                    steer_id=f"closed-{index}",
                    role_key="worker",
                    instruction="历史",
                    status="rejected",
                    result_note="已关闭",
                )
            )

    mutate_provider_sessions(run_dir, "agent.session", fill)
    pending = queue_steer(run_dir, "worker", "仍需发送")
    state = load_provider_sessions(run_dir)

    assert len(state.steers) == 101
    assert any(item.steer_id == pending.steer_id for item in state.steers)


def _runner(run_dir: Path) -> CodexAppServerRunner:
    return CodexAppServerRunner(
        run_dir,
        "worker",
        work_item_id="WI-01",
        contract_revision=1,
        plan_revision=1,
        executable=sys.executable,
        options=CodexExecOptions(
            model="fake-model",
            reasoning_effort="high",
        ),
    )


def _execution_context(
    run_dir: Path,
    name: str,
    events: list[str],
) -> RunnerExecutionContext:
    execution_id = hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]
    return RunnerExecutionContext(
        execution_root=run_dir,
        execution_dir=run_dir / "executions" / name,
        run_id=run_dir.name,
        step="worker",
        execution_id=execution_id,
        heartbeat_interval_seconds=0.05,
        progress_reporter=lambda event, elapsed: events.append(event),
    )


def _write_task_anchor_inputs(run_dir: Path) -> None:
    (run_dir / "agent-state.json").write_text(
        json.dumps(
            {
                "data": {
                    "run_id": run_dir.name,
                    "contract_revision": 1,
                    "execution_plan_revision": 1,
                    "current_work_item": "WI-01",
                    "phase": "acting",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "task-brief.md").write_text(
        "继续当前 Work Item，保持已批准合同不变。\n",
        encoding="utf-8",
    )


def _wait_for_interaction(run_dir: Path) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = load_provider_sessions(run_dir)
        pending = [
            item
            for item in state.interactions
            if item.status == "pending"
        ]
        if pending:
            return pending[0].interaction_id
        time.sleep(0.05)
    raise AssertionError("App Server 没有发布待响应请求")


def _write_fake_app_server(repo: Path) -> None:
    script = r'''
import json
import subprocess
import sys
from pathlib import Path

state_path = Path(__file__).with_name(".fake-app-server-state.json")
state = (
    json.loads(state_path.read_text(encoding="utf-8"))
    if state_path.exists()
    else {"starts": 0, "resumes": 0, "prompts": []}
)
state["server_args"] = sys.argv[1:]
thread_id = "thread-1"
turn_id = None

def save():
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

def send(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)

def thread_result(params):
    sandbox_types = {
        "read-only": "readOnly",
        "workspace-write": "workspaceWrite",
        "danger-full-access": "dangerFullAccess",
    }
    return {
        "thread": {"id": thread_id},
        "sandbox": {"type": sandbox_types[params["sandbox"]]},
        "approvalPolicy": params.get("approvalPolicy", "on-request"),
    }

def complete():
    message = json.dumps({
        "claimed_status": "completed",
        "summary": "fake worker 完成",
        "tests_claimed": [],
        "remaining_questions": [],
    }, ensure_ascii=False)
    send({"method": "item/started", "params": {
        "item": {"type": "commandExecution"}}})
    send({"method": "item/completed", "params": {
        "item": {"type": "commandExecution"}}})
    send({"method": "thread/tokenUsage/updated", "params": {
        "tokenUsage": {
            "total": {"totalTokens": 42, "cachedInputTokens": 21},
            "modelContextWindow": 1000,
        }}})
    send({"method": "thread/compacted", "params": {"threadId": thread_id}})
    send({"method": "item/completed", "params": {
        "item": {"type": "agentMessage", "text": message}}})
    send({"method": "turn/completed", "params": {
        "turn": {"id": turn_id, "status": "completed"}}})

def fail():
    send({"method": "error", "params": {
        "error": {
            "message": "fake turn failed",
            "additionalDetails": None,
            "codexErrorInfo": "badRequest",
        },
        "threadId": thread_id,
        "turnId": turn_id,
        "willRetry": False,
    }})
    send({"method": "turn/completed", "params": {
        "turn": {"id": turn_id, "status": "failed"}}})

for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    if method == "initialize":
        state["initialize_params"] = request["params"]
        save()
        send({"id": request["id"], "result": {}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        state["starts"] += 1
        state["thread_start_params"] = request["params"]
        save()
        send({"method": "thread/started", "params": {"thread": {"id": thread_id}}})
        send({"id": request["id"], "result": thread_result(request["params"])})
    elif method == "thread/resume":
        state["resumes"] += 1
        state.setdefault("resume_params", []).append(request["params"])
        save()
        send({"id": request["id"], "result": thread_result(request["params"])})
    elif method == "turn/start":
        prompt = request["params"]["input"][0]["text"]
        if "OVERLOAD_THEN_SUCCESS" in prompt:
            count = state.get("recoverable_overloads", 0)
            if count < 2:
                state["recoverable_overloads"] = count + 1
                save()
                send({"id": request["id"], "error": {
                    "code": -32001,
                    "message": "Server overloaded",
                }})
                continue
        if "OVERLOAD_ALWAYS" in prompt:
            state["exhausted_overloads"] = state.get("exhausted_overloads", 0) + 1
            save()
            send({"id": request["id"], "error": {
                "code": -32001,
                "message": "Server overloaded",
            }})
            continue
        turn_id = f"turn-{len(state['prompts']) + 1}"
        state["prompts"].append(prompt)
        state.setdefault("turn_start_params", []).append(request["params"])
        save()
        if "UNKNOWN_EVENT_FLOOD" in prompt:
            for index in range(600):
                send({"method": "future/event", "params": {"index": index}})
        if "HOLD_STDERR" in prompt:
            subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                stdout=subprocess.DEVNULL,
            )
        send({"method": "turn/started", "params": {"turn": {"id": turn_id}}})
        if "EARLY_COMPLETE" in prompt:
            complete()
            send({"id": request["id"], "result": {"turn": {"id": turn_id}}})
        else:
            send({"id": request["id"], "result": {"turn": {"id": turn_id}}})
        if "FAIL_TURN" in prompt:
            fail()
        elif "ASK_APPROVAL" in prompt:
            send({
                "id": "99",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "reason": "测试审批",
                },
            })
        elif "EARLY_COMPLETE" not in prompt:
            complete()
    elif request.get("id") == "99":
        state["approval_response"] = request["result"]
        save()
        complete()
    elif method == "turn/steer":
        state.setdefault("steers", []).append(
            request["params"]["input"][0]["text"]
        )
        save()
        send({"id": request["id"], "result": {}})
'''
    (repo / "app-server").write_text(
        script.lstrip(),
        encoding="utf-8",
        newline="\n",
    )
