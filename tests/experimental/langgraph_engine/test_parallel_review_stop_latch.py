from __future__ import annotations

import json
import os
import stat
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import vega.execution_control as execution_control
from vega.execution_control import (
    ExecutionLease,
    ExecutionStopLatchedError,
    RunnerExecutionContext,
    request_stop_for_active_executions,
    run_owned_process,
)


def test_stop_latch_rejects_attempt_created_during_broadcast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "parallel-stop-race"
    repo_path = tmp_path / "repo"
    run_dir.mkdir(parents=True)
    repo_path.mkdir()
    existing_path = run_dir / "executions" / "reviewer-existing" / "execution.json"
    _write_active_execution(existing_path, run_dir.name)

    precheck_reached = threading.Event()
    release_prepare = threading.Event()
    popen_called = threading.Event()

    def pause_after_precheck(phase: str) -> None:
        if phase != "after_stop_latch_precheck_before_execution_create":
            return
        precheck_reached.set()
        assert release_prepare.wait(timeout=5)

    def reject_popen(*args: object, **kwargs: object) -> None:
        popen_called.set()
        raise AssertionError("命中 stop latch 的迟到 attempt 不得调用 Popen")

    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)
    monkeypatch.setattr(execution_control.subprocess, "Popen", reject_popen)
    late_context = RunnerExecutionContext(
        execution_dir=(
            run_dir
            / "iterations"
            / "01"
            / "parallel-reviews"
            / "correctness_reviewer"
            / "attempt-late"
        ),
        run_id=run_dir.name,
        step="reviewer",
        iteration=1,
        engine="langgraph",
        attempt_id="attempt-late",
        exclusive_create=True,
        fault_injector=pause_after_precheck,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )
    result: dict[str, object] = {}

    def start_late_attempt() -> None:
        try:
            run_owned_process(
                [sys.executable, "-c", "print('must-not-run')"],
                "",
                repo_path,
                5,
                late_context,
            )
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=start_late_attempt)
    thread.start()
    assert precheck_reached.wait(timeout=5)

    fake_secret = "sk-stop-latch-fake-secret-123456"
    records = request_stop_for_active_executions(
        run_dir,
        f"api_key={fake_secret}",
    )
    release_prepare.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(records) == 1
    assert records[0].path == existing_path
    assert isinstance(result.get("error"), ExecutionStopLatchedError)
    assert not popen_called.is_set()

    late_lease = ExecutionLease.model_validate_json(
        late_context.execution_dir.joinpath("execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert late_lease.status == "stopped"
    assert late_lease.child_pid is None
    assert late_context.execution_dir.joinpath("stop-request.json").is_file()

    latch_payload = run_dir.joinpath("stop-latch.json").read_text(
        encoding="utf-8"
    )
    audit_payload = run_dir.joinpath("stop-latch-audit.jsonl").read_text(
        encoding="utf-8"
    )
    existing_stop_payload = existing_path.with_name("stop-request.json").read_text(
        encoding="utf-8"
    )
    assert fake_secret not in latch_payload
    assert fake_secret not in audit_payload
    assert fake_secret not in existing_stop_payload
    assert "[REDACTED]" in latch_payload

    events = [json.loads(line) for line in audit_payload.splitlines()]
    assert any(
        event["event"] == "execution_stop_written"
        and event["execution_ref"] == "executions/reviewer-existing/execution.json"
        for event in events
    )
    assert any(
        event["event"] == "execution_rejected_before_start"
        and event["execution_ref"].endswith("attempt-late/execution.json")
        for event in events
    )
    assert any(event["event"] == "broadcast_completed" for event in events)


def test_stop_latch_survives_partial_write_failure_and_blocks_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "parallel-stop-partial-failure"
    run_dir.mkdir(parents=True)
    first_path = run_dir / "executions" / "reviewer-first" / "execution.json"
    second_path = run_dir / "executions" / "reviewer-second" / "execution.json"
    _write_active_execution(first_path, run_dir.name)
    _write_active_execution(second_path, run_dir.name)
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    original_write = execution_control._write_model_atomic

    def fail_one_stop_write(path: Path, model: object) -> None:
        if path == second_path.with_name("stop-request.json"):
            raise OSError("deterministic stop write failure")
        original_write(path, model)

    monkeypatch.setattr(
        execution_control,
        "_write_model_atomic",
        fail_one_stop_write,
    )
    with pytest.raises(ValueError, match="广播未能完整确认"):
        request_stop_for_active_executions(run_dir, "partial failure")

    monkeypatch.setattr(
        execution_control,
        "_write_model_atomic",
        original_write,
    )
    assert run_dir.joinpath("stop-latch.json").is_file()
    assert first_path.with_name("stop-request.json").is_file()
    assert not second_path.with_name("stop-request.json").exists()

    blocked_context = RunnerExecutionContext(
        execution_dir=run_dir / "executions" / "late-after-failure",
        run_id=run_dir.name,
        step="reviewer",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )
    with pytest.raises(ExecutionStopLatchedError):
        execution_control.ExecutionController(blocked_context).prepare(
            ["reviewer"],
            5,
        )

    audit_events = [
        json.loads(line)
        for line in run_dir.joinpath("stop-latch-audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        event["event"] == "execution_stop_write_failed"
        and event["execution_ref"] == "executions/reviewer-second/execution.json"
        for event in audit_events
    )
    assert any(event["event"] == "broadcast_failed" for event in audit_events)


def test_stop_latch_fails_closed_for_unconfirmed_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "parallel-stop-unconfirmed"
    run_dir.mkdir(parents=True)
    execution_path = run_dir / "executions" / "reviewer" / "execution.json"
    _write_active_execution(
        execution_path,
        run_dir.name,
        termination_unconfirmed=True,
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    with pytest.raises(ValueError, match="终止未确认"):
        request_stop_for_active_executions(run_dir, "unconfirmed process tree")

    assert run_dir.joinpath("stop-latch.json").is_file()
    assert execution_path.with_name("stop-request.json").is_file()
    audit_events = [
        json.loads(line)
        for line in run_dir.joinpath("stop-latch-audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(event["event"] == "broadcast_failed" for event in audit_events)


def test_stop_latch_rejects_unbound_nested_execution_decoy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "parallel-stop-decoy"
    run_dir.mkdir(parents=True)
    decoy_path = run_dir / "damaged" / "nested" / "execution.json"
    _write_active_execution(decoy_path, run_dir.name)
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    with pytest.raises(ValueError, match="路径无法绑定 identity"):
        request_stop_for_active_executions(run_dir, "reject decoy")

    assert run_dir.joinpath("stop-latch.json").is_file()
    assert not decoy_path.with_name("stop-request.json").exists()
    audit_events = [
        json.loads(line)
        for line in run_dir.joinpath("stop-latch-audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    failure = next(
        event
        for event in audit_events
        if event["event"] == "broadcast_failed"
    )
    assert "damaged" in failure["detail"]
    assert "已拒绝 stop/recover" in failure["detail"]


def test_execution_prepare_rejects_reparse_path_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "parallel-stop-reparse"
    linked_dir = run_dir / "linked"
    linked_dir.mkdir(parents=True)
    original_lstat = Path.lstat

    def fake_lstat(path: Path) -> object:
        metadata = original_lstat(path)
        if path == linked_dir:
            return type(
                "ReparseStat",
                (),
                {
                    "st_mode": stat.S_IFDIR,
                    "st_file_attributes": 0x400,
                },
            )()
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    context = RunnerExecutionContext(
        execution_dir=linked_dir / "reviewer",
        run_id=run_dir.name,
        step="reviewer",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )

    with pytest.raises(ValueError, match="链接或 reparse point"):
        execution_control.ExecutionController(context).prepare(
            ["reviewer"],
            5,
        )

    assert not context.execution_dir.joinpath("execution.json").exists()
    assert not run_dir.joinpath("stop-latch.json").exists()


def _write_active_execution(
    path: Path,
    run_id: str,
    *,
    termination_unconfirmed: bool = False,
) -> None:
    now = datetime.now(UTC)
    lease = ExecutionLease(
        run_id=run_id,
        step="reviewer",
        owner_pid=os.getpid(),
        child_pid=os.getpid(),
        termination_unconfirmed=termination_unconfirmed,
        command=["reviewer"],
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
        deadline=(now + timedelta(minutes=2)).isoformat(),
        status="running",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")
