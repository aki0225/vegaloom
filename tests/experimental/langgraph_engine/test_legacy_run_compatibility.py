from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vega.execution_control import ExecutionLease
from vega.finish_runtime import FinishRuntime, _load_finish_state
from vega.loop_runtime import LoopAutomationRuntime
from vega.recovery_runtime import RecoveryRuntime
from vega.run_status import run_status_payload


def _legacy_payload(
    repo: Path,
    run_id: str,
    *,
    status: str,
    current_step: str,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "status": status,
        "task_mode": "bug",
        "automation_mode": "assist",
        "repo_path": str(repo),
        "input_source": "legacy-fixture",
        "current_step": current_step,
        "project_policy_snapshot": {},
        "current_iteration": 0,
        "max_iterations": 2,
        "iterations": [],
        "artifacts": [],
        "eval_results": [],
        "memory_proposals": [],
    }


def _write_legacy_state(
    workspace: Path,
    repo: Path,
    run_id: str,
    *,
    status: str,
    current_step: str,
) -> Path:
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True)
    payload = _legacy_payload(
        repo,
        run_id,
        status=status,
        current_step=current_step,
    )
    run_dir.joinpath("state.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_legacy_state_without_engine_defaults_to_linear_for_status_and_finish(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        "legacy-status",
        status="needs_human",
        current_step="waiting_for_worker",
    )

    assert run_status_payload(tmp_path, run_dir.name)["engine"] == "linear"
    assert _load_finish_state(run_dir).engine == "linear"

    FinishRuntime(tmp_path).run(run_dir.name, engine="linear")

    assert run_dir.joinpath("finish-summary.json").exists()


def test_legacy_continue_loads_as_linear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        "legacy-continue",
        status="needs_human",
        current_step="waiting_for_worker",
    )
    monkeypatch.setattr("vega.loop_runtime._project_policy_changed", lambda *args: True)

    result = LoopAutomationRuntime(tmp_path).continue_assist(
        run_dir.name,
        repo,
        verify=False,
        engine="linear",
    )

    payload = json.loads(result.joinpath("state.json").read_text(encoding="utf-8"))
    assert payload["engine"] == "linear"
    assert payload["current_step"] == "project_policy_changed"


def test_legacy_recover_loads_as_linear(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        "legacy-recover",
        status="running",
        current_step="worker",
    )

    result = RecoveryRuntime(tmp_path).recover_loop(
        run_dir.name,
        "测试旧 run recovery",
        engine="linear",
    )

    payload = json.loads(result.joinpath("state.json").read_text(encoding="utf-8"))
    assert payload["engine"] == "linear"
    assert payload["status"] == "needs_human"


@pytest.mark.parametrize("operation", ["continue", "recover"])
def test_engine_mismatch_is_rejected_before_state_mutation(
    tmp_path: Path,
    operation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    status = "needs_human" if operation == "continue" else "running"
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        f"legacy-mismatch-{operation}",
        status=status,
        current_step="worker",
    )
    state_path = run_dir / "state.json"
    before = state_path.read_bytes()

    with pytest.raises(ValueError, match="不能切换"):
        if operation == "continue":
            LoopAutomationRuntime(tmp_path).continue_assist(
                run_dir.name,
                repo,
                verify=False,
                engine="langgraph",
            )
        else:
            RecoveryRuntime(tmp_path).recover_loop(
                run_dir.name,
                "拒绝切换 engine",
                engine="langgraph",
            )

    assert state_path.read_bytes() == before


@pytest.mark.parametrize("operation", ["continue", "recover", "finish"])
@pytest.mark.parametrize("requested_engine", [None, "langgraph"])
def test_persisted_langgraph_run_is_rejected_without_any_artifact_mutation(
    tmp_path: Path,
    operation: str,
    requested_engine: str | None,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    status = "running" if operation == "recover" else "needs_human"
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        f"graph-{operation}-{requested_engine or 'implicit'}",
        status=status,
        current_step="worker",
    )
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["engine"] = "langgraph"
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    before = _tree_bytes(run_dir)

    expected_error = (
        "graph run config" if operation == "recover" else "langgraph engine"
    )
    with pytest.raises(ValueError, match=expected_error):
        if operation == "continue":
            LoopAutomationRuntime(tmp_path).continue_assist(
                run_dir.name,
                repo,
                verify=False,
                engine=requested_engine,
            )
        elif operation == "recover":
            RecoveryRuntime(tmp_path).recover_loop(
                run_dir.name,
                "拒绝 graph run",
                engine=requested_engine,
            )
        else:
            FinishRuntime(tmp_path).run(
                run_dir.name,
                engine=requested_engine,
            )

    assert _tree_bytes(run_dir) == before


def test_graph_status_does_not_recommend_linear_mutation_commands(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        "graph-status",
        status="needs_human",
        current_step="waiting_for_worker",
    )
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["engine"] = "langgraph"
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    status = run_status_payload(tmp_path, run_dir.name)
    next_steps = "\n".join(status["next_steps"])

    assert status["engine"] == "langgraph"
    assert "旧版顺序 graph run" in next_steps
    assert "loop continue" not in next_steps
    assert "vega finish" not in next_steps


def test_explicit_graph_engine_cannot_bypass_loop_schema_status_validation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "graph-status-missing-automation-mode"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("state.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "status": "success",
                "engine": "langgraph",
                "repo_path": str(repo),
                "input_source": "test",
                "current_step": "done",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    run_dir.joinpath("agent-brief.md").write_text(
        "# Misclassification Fixture\n",
        encoding="utf-8",
    )
    before = _tree_bytes(run_dir)

    with pytest.raises(ValueError, match="loop state.json schema 不合法"):
        run_status_payload(tmp_path, run_dir.name)

    assert _tree_bytes(run_dir) == before


def test_active_graph_status_does_not_recommend_linear_recovery(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        "graph-active-status",
        status="running",
        current_step="worker",
    )
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["engine"] = "langgraph"
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    now = datetime.now(UTC)
    execution_dir = run_dir / "iterations" / "01" / "executions" / "worker"
    execution_dir.mkdir(parents=True)
    lease = ExecutionLease(
        run_id=run_dir.name,
        step="worker",
        iteration=1,
        owner_pid=1234,
        child_pid=5678,
        command=["worker"],
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
        deadline=(now + timedelta(minutes=2)).isoformat(),
        status="running",
    )
    execution_dir.joinpath("execution.json").write_text(
        lease.model_dump_json(indent=2),
        encoding="utf-8",
    )

    status = run_status_payload(tmp_path, run_dir.name)
    next_steps = "\n".join(status["next_steps"])

    assert "旧版 langgraph run" in next_steps
    assert "不能自动 recover" in next_steps
    assert "只有 heartbeat 过期" not in next_steps


@pytest.mark.parametrize("invalid_engine", [None, 1, "", "unknown"])
def test_status_rejects_explicit_invalid_engine_instead_of_downgrading_to_linear(
    tmp_path: Path,
    invalid_engine: object,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        f"invalid-engine-{invalid_engine!s}",
        status="needs_human",
        current_step="waiting_for_worker",
    )
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["engine"] = invalid_engine
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="engine 字段不合法"):
        run_status_payload(tmp_path, run_dir.name)


@pytest.mark.parametrize("operation", ["continue", "recover", "finish"])
def test_graph_engine_preflight_wins_before_other_schema_diagnostics(
    tmp_path: Path,
    operation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / f"graph-corrupt-{operation}"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("state.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "automation_mode": "assist",
                "engine": "langgraph",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    before = _tree_bytes(run_dir)

    expected_error = (
        "state.json 无法解析"
        if operation == "recover"
        else "langgraph engine"
    )
    with pytest.raises(ValueError, match=expected_error):
        if operation == "continue":
            LoopAutomationRuntime(tmp_path).continue_assist(
                run_dir.name,
                repo,
                verify=False,
            )
        elif operation == "recover":
            RecoveryRuntime(tmp_path).recover_loop(
                run_dir.name,
                "拒绝损坏 graph run",
            )
        else:
            FinishRuntime(tmp_path).run(run_dir.name)

    if operation == "recover":
        assert run_dir.joinpath("recovery-report.md").is_file()
        assert _tree_bytes(run_dir) != before
    else:
        assert _tree_bytes(run_dir) == before


@pytest.mark.parametrize("operation", ["continue", "recover", "finish"])
def test_truncated_graph_state_is_rejected_before_diagnostic_write(
    tmp_path: Path,
    operation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / f"truncated-graph-{operation}"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("state.json").write_text(
        '{"run_id":"truncated","engine":"langgraph","status":',
        encoding="utf-8",
    )
    before = _tree_bytes(run_dir)

    expected_error = (
        "state.json 无法解析"
        if operation == "recover"
        else "langgraph engine"
    )
    with pytest.raises(ValueError, match=expected_error):
        if operation == "continue":
            LoopAutomationRuntime(tmp_path).continue_assist(
                run_dir.name,
                repo,
                verify=False,
            )
        elif operation == "recover":
            RecoveryRuntime(tmp_path).recover_loop(
                run_dir.name,
                "拒绝截断 graph run",
            )
        else:
            FinishRuntime(tmp_path).run(run_dir.name)

    if operation == "recover":
        assert run_dir.joinpath("recovery-report.md").is_file()
        assert _tree_bytes(run_dir) != before
    else:
        assert _tree_bytes(run_dir) == before


@pytest.mark.parametrize("operation", ["status", "continue", "recover", "finish"])
def test_duplicate_engine_keys_are_rejected_without_artifact_mutation(
    tmp_path: Path,
    operation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / f"duplicate-engine-{operation}"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("state.json").write_text(
        "\n".join(
            [
                "{",
                f'  "run_id": "{run_dir.name}",',
                '  "status": "running",',
                '  "task_mode": "bug",',
                '  "automation_mode": "assist",',
                '  "engine": "linear",',
                '  "engine": "langgraph",',
                f'  "repo_path": {json.dumps(str(repo))},',
                '  "input_source": "test"',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    before = _tree_bytes(run_dir)

    with pytest.raises(ValueError, match="重复 engine"):
        if operation == "status":
            run_status_payload(tmp_path, run_dir.name)
        elif operation == "continue":
            LoopAutomationRuntime(tmp_path).continue_assist(
                run_dir.name,
                repo,
                verify=False,
            )
        elif operation == "recover":
            RecoveryRuntime(tmp_path).recover_loop(
                run_dir.name,
                "拒绝重复 engine",
            )
        else:
            FinishRuntime(tmp_path).run(run_dir.name)

    assert _tree_bytes(run_dir) == before


@pytest.mark.parametrize("operation", ["continue", "recover", "finish"])
def test_unreadable_state_is_rejected_without_artifact_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        f"unreadable-{operation}",
        status="running",
        current_step="worker",
    )
    state_path = run_dir / "state.json"
    before = _tree_bytes(run_dir)
    original_read_text = Path.read_text

    def unreadable_state(path: Path, *args, **kwargs) -> str:
        if path == state_path:
            raise PermissionError("测试不可读状态")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable_state)

    with pytest.raises(ValueError, match="无法读取"):
        if operation == "continue":
            LoopAutomationRuntime(tmp_path).continue_assist(
                run_dir.name,
                repo,
                verify=False,
            )
        elif operation == "recover":
            RecoveryRuntime(tmp_path).recover_loop(
                run_dir.name,
                "拒绝不可读状态",
            )
        else:
            FinishRuntime(tmp_path).run(run_dir.name)

    assert _tree_bytes(run_dir) == before


@pytest.mark.parametrize("invalid_status", [None, "unknown", 1])
def test_status_validates_full_loop_state_schema(
    tmp_path: Path,
    invalid_status: object,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _write_legacy_state(
        tmp_path,
        repo,
        f"invalid-status-{invalid_status!s}",
        status="needs_human",
        current_step="worker",
    )
    state_path = run_dir / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["status"] = invalid_status
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema 不合法"):
        run_status_payload(tmp_path, run_dir.name)
