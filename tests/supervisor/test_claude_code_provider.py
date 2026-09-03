from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega.agent_provider import (
    provider_resume_command,
    resolve_run_provider,
)
from vega.agent_provider_factory import ensure_reviewer_runner
from vega.claude_code_process import (
    CLAUDE_RESULT_TYPE,
    claude_init_matches_policy,
    claude_terminal_payload,
    safe_claude_progress_events,
)
from vega.claude_code_runner import ClaudeCodeRunner
from vega.execution_control import OwnedProcessResult, RunnerExecutionContext
from vega.loop_runtime import LoopAutomationRuntime
from vega.project_config import ProjectConfig, load_project_config
from vega.provider_session import (
    ensure_session_handle,
    load_provider_sessions,
    mutate_provider_sessions,
    queue_steer,
)


def test_claude_event_projection_drops_thinking_and_tool_parameters() -> None:
    thinking = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "thinking",
                    "thinking": "不能进入运行证据的内部推理",
                },
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "private/path.py"},
                },
            ]
        },
    }

    events = safe_claude_progress_events(thinking)

    assert events == ["file_change_started"]
    assert "private/path.py" not in json.dumps(events, ensure_ascii=False)
    terminal = claude_terminal_payload(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "00000000-0000-4000-8000-000000000001",
            "structured_output": {"summary": "完成"},
            "usage": {
                "input_tokens": 10,
                "cache_read_input_tokens": 6,
                "output_tokens": 2,
            },
            "uuid": "turn-1",
        }
    )
    assert terminal == {
        "status": "success",
        "message": '{"summary":"完成"}',
        "error": None,
        "session_id": "00000000-0000-4000-8000-000000000001",
        "turn_id": "turn-1",
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 6,
            "output_tokens": 2,
        },
    }


def test_claude_terminal_requires_session_and_structured_result() -> None:
    assert claude_terminal_payload(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
        }
    )["status"] == "error"
    assert claude_terminal_payload(
        {
            "type": "result",
            "subtype": "success",
            "is_error": "false",
            "session_id": "00000000-0000-4000-8000-000000000002",
            "structured_output": {"summary": "完成"},
        }
    )["status"] == "error"
    assert claude_terminal_payload(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "00000000-0000-4000-8000-000000000002",
        }
    )["status"] == "error"


def test_claude_init_must_match_fixed_permissions_and_tools() -> None:
    init = {
        "type": "system",
        "subtype": "init",
        "tools": ["Read", "Glob", "Grep", "StructuredOutput"],
        "permissionMode": "dontAsk",
        "mcp_servers": [],
    }

    assert claude_init_matches_policy(
        init,
        expected_tools=["Read", "Glob", "Grep"],
        expected_permission_mode="dontAsk",
    )
    assert not claude_init_matches_policy(
        {**init, "tools": [*init["tools"], "Bash"]},
        expected_tools=["Read", "Glob", "Grep"],
        expected_permission_mode="dontAsk",
    )
    assert not claude_init_matches_policy(
        {**init, "mcp_servers": [{"name": "unexpected"}]},
        expected_tools=["Read", "Glob", "Grep"],
        expected_permission_mode="dontAsk",
    )


def test_claude_runner_reuses_session_and_delivers_steer_next_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    repo = tmp_path / "repo"
    run_dir.mkdir(parents=True)
    repo.mkdir()
    requests: list[dict[str, object]] = []
    prompts: list[str] = []

    monkeypatch.setattr(
        "vega.claude_code_runner.resolve_claude_executable",
        lambda _value: "claude-native",
    )

    def fake_owned_process(command, prompt, _repo, _timeout, _context, **_kwargs):
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        requests.append(request)
        prompts.append(prompt)
        arguments = request["arguments"]
        flag = "--resume" if "--resume" in arguments else "--session-id"
        session_id = arguments[arguments.index(flag) + 1]
        output = json.dumps(
            {
                "type": CLAUDE_RESULT_TYPE,
                "status": "success",
                "message": '{"summary":"完成"}',
                "error": None,
                "session_id": session_id,
                "turn_id": f"turn-{len(requests)}",
                "permissions_verified": True,
                "usage": {
                    "input_tokens": 20,
                    "cache_read_input_tokens": 8,
                    "output_tokens": 3,
                },
            },
            ensure_ascii=False,
        )
        return OwnedProcessResult(
            status="success",
            output=output,
            error=None,
            returncode=0,
        )

    monkeypatch.setattr(
        "vega.claude_code_runner.run_owned_process",
        fake_owned_process,
    )
    runner = ClaudeCodeRunner(
        run_dir,
        "worker",
        work_item_id="WI-01",
        contract_revision=1,
        plan_revision=1,
        output_schema={"type": "object"},
    )

    first = runner.run(
        "第一次执行",
        repo,
        sandbox="workspace-write",
        timeout_seconds=60,
        execution_context=_context(run_dir, "first"),
    )
    queued = queue_steer(run_dir, "worker", "只修改批准范围")
    second = runner.run(
        "继续执行",
        repo,
        sandbox="workspace-write",
        timeout_seconds=60,
        execution_context=_context(run_dir, "second"),
    )

    assert first.status == second.status == "success"
    first_args = requests[0]["arguments"]
    second_args = requests[1]["arguments"]
    assert "--session-id" in first_args
    assert "--resume" not in first_args
    assert "--resume" in second_args
    assert first_args[first_args.index("--session-id") + 1] == second_args[
        second_args.index("--resume") + 1
    ]
    assert "--safe-mode" in first_args
    assert first_args[first_args.index("--tools") + 1] == (
        "Read,Glob,Grep,Edit,Write"
    )
    assert "只修改批准范围" in prompts[1]
    state = load_provider_sessions(run_dir)
    handle = state.handles["worker"]
    assert handle.provider == "claude-code"
    assert handle.lifecycle == "idle"
    assert handle.turn_count == 2
    assert handle.last_turn_id == "turn-2"
    assert handle.cached_input_tokens == 8
    delivered = next(
        item for item in state.steers if item.steer_id == queued.steer_id
    )
    assert delivered.status == "delivered"
    assert delivered.delivered_turn_id == "turn-2"


def test_provider_selection_is_sticky_per_change_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)

    def mutation(state) -> None:
        ensure_session_handle(
            state,
            "worker",
            provider="claude-code",
            work_item_id=None,
            contract_revision=None,
            plan_revision=None,
        )

    mutate_provider_sessions(run_dir, "agent.session", mutation)

    assert resolve_run_provider(run_dir, None) == "claude"
    assert (
        provider_resume_command(
            "claude-code",
            "00000000-0000-4000-8000-000000000003",
        )
        == "claude --resume 00000000-0000-4000-8000-000000000003"
    )
    with pytest.raises(ValueError, match="不能切换"):
        resolve_run_provider(run_dir, "codex")


def test_claude_reviewer_uses_independent_read_only_session(
    tmp_path: Path,
) -> None:
    runtime = LoopAutomationRuntime(tmp_path)
    state = SimpleNamespace(
        current_work_item="WI-01",
        contract_revision=1,
        execution_plan_revision=1,
    )

    ensure_reviewer_runner(
        runtime,
        ProjectConfig(),
        agent_run_dir=tmp_path / "runs" / "run-1",
        state=state,
        provider="claude",
        persistent_session=True,
    )

    reviewer = runtime.reviewer_runner
    assert isinstance(reviewer, ClaudeCodeRunner)
    assert reviewer.role_key == "reviewer:WI-01"
    assert reviewer.persistent_session


def test_project_config_accepts_only_allowlisted_claude_options(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".vega.yaml").write_text(
        "\n".join(
            [
                "runner:",
                "  claude_code:",
                "    worker:",
                "      model: sonnet",
                "      effort: high",
                "    reviewer:",
                "      model: opus",
                "      effort: max",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_project_config(repo)

    assert config.runner.claude_code.worker.model == "sonnet"
    assert config.runner.claude_code.worker.effort == "high"
    assert config.runner.claude_code.reviewer.model == "opus"
    assert config.runner.claude_code.reviewer.effort == "max"

    (repo / ".vega.yaml").write_text(
        "\n".join(
            [
                "runner:",
                "  claude_code:",
                "    worker:",
                "      allowed_tools:",
                "        - Bash",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_project_config(repo)


def _context(run_dir: Path, name: str) -> RunnerExecutionContext:
    execution_id = "1" * 32 if name == "first" else "2" * 32
    return RunnerExecutionContext(
        execution_root=run_dir,
        execution_dir=run_dir / "executions" / "worker" / name,
        run_id=run_dir.name,
        step="worker",
        execution_id=execution_id,
    )
