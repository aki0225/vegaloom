from __future__ import annotations

import _thread
import io
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import vega.execution_control as execution_control
from vega.execution_control import (
    ExecutionLease,
    ExecutionStopLatchedError,
    RunnerExecutionContext,
    inspect_execution_for_recovery,
    request_stop_for_active_executions,
    request_stop_for_run,
    run_owned_process,
)


def test_atomic_execution_write_uses_unique_temp_file_per_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = (
        tmp_path
        / "runs"
        / "atomic-thread-write"
        / "executions"
        / "worker"
        / "execution.json"
    )
    timestamp = datetime.now(UTC).isoformat()
    leases = [
        ExecutionLease(
            run_id="atomic-thread-write",
            step="worker",
            iteration=index,
            owner_pid=os.getpid(),
            command=["worker", str(index)],
            started_at=timestamp,
            last_heartbeat=timestamp,
            lease_expires_at=timestamp,
            deadline=timestamp,
            status="starting",
        )
        for index in (1, 2)
    ]
    real_replace = execution_control.os.replace
    replace_sources: list[Path] = []
    start_barrier = threading.Barrier(len(leases))
    replace_sources_lock = threading.Lock()
    failures: list[BaseException] = []

    def recording_replace(source: Path, target: Path) -> None:
        with replace_sources_lock:
            replace_sources.append(Path(source))
        real_replace(source, target)

    monkeypatch.setattr(
        execution_control.os,
        "replace",
        recording_replace,
    )

    def write_lease(lease: ExecutionLease) -> None:
        try:
            start_barrier.wait(timeout=5)
            execution_control._write_model_atomic(path, lease)
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=write_lease, args=(lease,))
        for lease in leases
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert len(set(replace_sources)) == len(leases)
    persisted = ExecutionLease.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    assert persisted in leases
    assert list(path.parent.glob(".aw-*")) == []


def test_stop_latch_wins_before_start_lock_and_marker_process_never_starts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "stop-wins-start-race"
    marker = tmp_path / "stop-wins-marker.txt"
    prepare_checked = threading.Event()
    release_runner = threading.Event()
    result: dict[str, object] = {}

    def pause_after_prepare_check(phase: str) -> None:
        if phase != "after_stop_latch_final_prepare_check_before_start_lock":
            return
        prepare_checked.set()
        assert release_runner.wait(timeout=5)

    context = RunnerExecutionContext(
        execution_dir=run_dir / "executions" / "worker",
        run_id=run_dir.name,
        step="worker",
        fault_injector=pause_after_prepare_check,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).write_text('started', encoding='utf-8')"
        ),
        str(marker),
    ]

    def start_runner() -> None:
        try:
            result["value"] = run_owned_process(
                command,
                "",
                tmp_path,
                5,
                context,
            )
        except BaseException as exc:
            result["error"] = exc

    runner = threading.Thread(target=start_runner)
    runner.start()
    assert prepare_checked.wait(timeout=5)
    try:
        records = request_stop_for_active_executions(
            run_dir,
            "stop wins before process launch",
        )
    finally:
        release_runner.set()
        runner.join(timeout=5)

    assert not runner.is_alive()
    assert len(records) == 1
    assert isinstance(result.get("error"), ExecutionStopLatchedError)
    assert "value" not in result
    assert not marker.exists()
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert lease.status == "stopped"
    assert lease.child_pid is None


def test_start_lock_wins_and_stop_latch_linearizes_after_marker_process_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "start-wins-stop-race"
    marker = tmp_path / "start-wins-marker.txt"
    start_lock_held = threading.Event()
    release_runner = threading.Event()
    stop_called = threading.Event()
    runner_result: dict[str, object] = {}
    stop_result: dict[str, object] = {}
    linearization_order: list[str] = []
    real_popen = subprocess.Popen
    real_create_once = execution_control._write_model_create_once

    def pause_before_popen(phase: str) -> None:
        if phase != "after_locked_stop_latch_check_before_popen":
            return
        start_lock_held.set()
        assert release_runner.wait(timeout=5)

    command: list[str]

    def marker_observing_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched_command = args[0] if args else kwargs.get("args")
        if launched_command != command:
            return process
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.01)
        assert marker.exists()
        linearization_order.append("popen")
        return process

    def recording_create_once(path: Path, model) -> None:
        if path.name == "stop-latch.json":
            linearization_order.append("stop-latch")
        real_create_once(path, model)

    monkeypatch.setattr(
        execution_control.subprocess,
        "Popen",
        marker_observing_popen,
    )
    monkeypatch.setattr(
        execution_control,
        "_write_model_create_once",
        recording_create_once,
    )
    context = RunnerExecutionContext(
        execution_dir=run_dir / "executions" / "worker",
        run_id=run_dir.name,
        step="worker",
        fault_injector=pause_before_popen,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=1.0,
    )
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('started', encoding='utf-8'); "
            "time.sleep(5)"
        ),
        str(marker),
    ]

    def start_runner() -> None:
        try:
            runner_result["value"] = run_owned_process(
                command,
                "",
                tmp_path,
                10,
                context,
            )
        except BaseException as exc:
            runner_result["error"] = exc

    def stop_runner() -> None:
        stop_called.set()
        try:
            stop_result["records"] = request_stop_for_active_executions(
                run_dir,
                "stop waits for process launch",
            )
        except BaseException as exc:
            stop_result["error"] = exc

    runner = threading.Thread(target=start_runner)
    runner.start()
    assert start_lock_held.wait(timeout=5)
    stopper = threading.Thread(target=stop_runner)
    stopper.start()
    assert stop_called.wait(timeout=5)
    assert not run_dir.joinpath("stop-latch.json").exists()
    release_runner.set()
    runner.join(timeout=10)
    stopper.join(timeout=10)

    assert not runner.is_alive()
    assert not stopper.is_alive()
    assert "error" not in runner_result
    assert "error" not in stop_result
    assert marker.read_text(encoding="utf-8") == "started"
    assert linearization_order == ["popen", "stop-latch"]
    records = stop_result["records"]
    assert isinstance(records, list)
    assert len(records) == 1


def test_large_stdin_does_not_delay_owned_process_timeout(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "stdin-timeout" / "executions" / "worker",
        run_id="stdin-timeout",
        step="worker",
        iteration=1,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    large_input = "x" * (16 * 1024 * 1024)

    started = time.monotonic()
    result = run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(8)"],
        large_input,
        tmp_path,
        1,
        context,
    )
    elapsed = time.monotonic() - started

    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "timed_out"
    assert lease.status == "timed_out"
    assert elapsed < 5


def test_owned_process_observed_after_deadline_is_not_reported_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_dir=tmp_path
        / "runs"
        / "late-completion"
        / "executions"
        / "worker",
        run_id="late-completion",
        step="worker",
    )
    process = _AlreadyCompletedProcess(pid=4242)
    ticks = iter((100.0, 100.0, 102.0))

    monkeypatch.setattr(
        execution_control.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        execution_control,
        "_process_group_options",
        lambda: {},
    )
    monkeypatch.setattr(
        execution_control.time,
        "monotonic",
        lambda: next(ticks),
    )

    result = run_owned_process(
        ["completed-after-deadline"],
        "",
        tmp_path,
        1,
        context,
    )
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.status == "timed_out"
    assert lease.status == "timed_out"
    assert lease.returncode == 0


def test_owned_process_redacts_output_before_persisting_and_returning(tmp_path: Path) -> None:
    fake_secret = "sk-runner-fake-secret-123456"
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "redacted-output" / "executions" / "worker",
        run_id="redacted-output",
        step="worker",
        runner_identity={
            "profile": fake_secret,
            "api_key": "short-credential",
        },
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )

    result = run_owned_process(
        [
            sys.executable,
            "-c",
            f"print('api_key={fake_secret}')",
            f"--api-key={fake_secret}",
        ],
        "prompt should only go to stdin",
        tmp_path,
        5,
        context,
    )
    persisted_output = context.execution_dir.joinpath("process-output.txt").read_text(
        encoding="utf-8"
    )
    execution_payload = context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")

    assert result.status == "success"
    assert result.output == persisted_output
    assert fake_secret not in result.output
    assert fake_secret not in persisted_output
    assert fake_secret not in execution_payload
    assert "short-credential" not in execution_payload
    assert "[REDACTED]" in execution_payload
    assert "prompt should only go to stdin" not in execution_payload
    lease = ExecutionLease.model_validate_json(execution_payload)
    assert lease.runner_identity["api_key"] == "[REDACTED]"


def test_owned_process_redacts_provider_credential_diagnostics(
    tmp_path: Path,
) -> None:
    masked_key = "PROXY_MA*AGED"
    request_id = "req_fake_provider_request_123"
    diagnostic = (
        "ERROR: unexpected status 401 Unauthorized: "
        f"Incorrect API key provided: {masked_key}, "
        "url: https://api.openai.com/v1/responses, "
        f"request id: {request_id}"
    )
    context = RunnerExecutionContext(
        execution_dir=tmp_path
        / "runs"
        / "provider-diagnostic"
        / "executions"
        / "worker",
        run_id="provider-diagnostic",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )

    result = run_owned_process(
        [sys.executable, "-c", f"print({diagnostic!r})"],
        "",
        tmp_path,
        5,
        context,
    )
    persisted_output = context.execution_dir.joinpath(
        "process-output.txt"
    ).read_text(encoding="utf-8")

    assert result.status == "success"
    assert result.output == persisted_output
    assert masked_key not in persisted_output
    assert request_id not in persisted_output
    assert "Incorrect API key provided: [REDACTED]" in persisted_output
    assert "request id: [REDACTED]" in persisted_output


def test_owned_process_persists_partial_output_after_keyboard_interrupt(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "interrupted-output" / "executions" / "worker",
        run_id="interrupted-output",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    ready_path = tmp_path / "child-ready"
    script = (
        "from pathlib import Path; import time; "
        "print('partial-output', flush=True); "
        f"Path({str(ready_path)!r}).write_text('ready', encoding='utf-8'); "
        "time.sleep(8)"
    )

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready_path.exists():
            time.sleep(0.02)
        _thread.interrupt_main()

    interrupter = threading.Thread(target=interrupt_when_ready, daemon=True)
    interrupter.start()
    result = run_owned_process(
        [sys.executable, "-u", "-c", script],
        "",
        tmp_path,
        10,
        context,
    )
    interrupter.join(timeout=1)

    persisted_output = context.execution_dir.joinpath("process-output.txt").read_text(
        encoding="utf-8"
    )
    assert result.status == "stopped"
    assert "partial-output" in result.output
    assert persisted_output == result.output


def test_recovery_rejects_older_fresh_active_execution_when_newer_terminal_exists(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "masked-active"
    now = datetime.now(UTC)
    active_path = run_dir / "executions" / "worker" / "execution.json"
    terminal_path = run_dir / "executions" / "reviewer" / "execution.json"

    _write_execution(
        active_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            iteration=1,
            owner_pid=os.getpid(),
            child_pid=os.getpid(),
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(seconds=10)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _write_execution(
        terminal_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="reviewer",
            owner_pid=os.getpid(),
            child_pid=None,
            command=["reviewer"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="completed",
            returncode=0,
            finished_at=now.isoformat(),
        ),
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert inspection.can_recover is False
    assert inspection.record is not None
    assert inspection.record.path == active_path
    assert "PID 仍存活" in inspection.summary


def test_recovery_rejects_execution_record_from_another_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "current-run"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id="other-run",
            step="worker",
            owner_pid=999999,
            command=["worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
            deadline=(now - timedelta(seconds=1)).isoformat(),
            status="timed_out",
        ),
    )

    with pytest.raises(ValueError, match="execution 记录身份不一致"):
        inspect_execution_for_recovery(run_dir)


def test_stop_request_reason_is_redacted_before_persisting(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "stop-redaction"
    now = datetime.now(UTC)
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    fake_secret = "sk-stop-fake-secret-123456"
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=os.getpid(),
            child_pid=os.getpid(),
            command=["worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )

    request_stop_for_run(run_dir, f"api_key={fake_secret}")

    request_payload = execution_path.with_name("stop-request.json").read_text(
        encoding="utf-8"
    )
    assert fake_secret not in request_payload
    assert "[REDACTED]" in request_payload


def test_stop_prefers_latest_live_active_execution_over_newer_stale_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "stop-live-active"
    now = datetime.now(UTC)
    live_path = run_dir / "executions" / "worker" / "execution.json"
    stale_path = run_dir / "executions" / "verification" / "execution.json"
    live_pid = 1111
    stale_pid = 2222
    _write_execution(
        live_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=live_pid,
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(seconds=10)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _write_execution(
        stale_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="verification",
            owner_pid=stale_pid,
            command=["verification"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "is_process_alive",
        lambda pid: pid == live_pid,
    )

    record = request_stop_for_run(run_dir, "stop live execution")

    assert record.path == live_path
    assert live_path.with_name("stop-request.json").exists()
    assert not stale_path.with_name("stop-request.json").exists()


def test_windows_taskkill_nonzero_keeps_stop_execution_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "taskkill-failure" / "executions" / "worker",
        run_id="taskkill-failure",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )
    process = _FakeOwnedProcess(pid=4242, wait_times_out=False)
    taskkill_commands: list[list[str]] = []
    request = execution_control.StopRequest(
        reason="manual stop",
        requested_at=datetime.now(UTC).isoformat(),
        requester_pid=os.getpid(),
    )

    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(execution_control.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=5, stdout="", stderr="access denied")
        ),
    )
    monkeypatch.setattr(
        execution_control.ExecutionController,
        "read_stop_request",
        lambda _: request,
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    result = run_owned_process(["fake-runner"], "", tmp_path, 5, context)
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    inspection = inspect_execution_for_recovery(context.execution_dir.parents[1])

    assert result.status == "error"
    assert lease.status == "stop_requested"
    assert lease.finished_at is None
    assert lease.termination_unconfirmed is True
    assert lease.reason is not None
    assert "taskkill 退出码 5" in lease.reason
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]
    assert not inspection.can_recover


def test_recovery_rejects_persisted_unconfirmed_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "unconfirmed-tree"
    execution_path = run_dir / "executions" / "verification" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="verification",
            owner_pid=1111,
            child_pid=2222,
            termination_unconfirmed=True,
            command=["verification"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
            reason="owned process tree termination unconfirmed",
        ),
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: False)

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "进程全部退出" in inspection.summary
    assert "不允许自动 recovery" in inspection.summary


def test_windows_taskkill_failure_keeps_tree_termination_unconfirmed_when_root_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=4343, wait_times_out=False)
    taskkill_commands: list[list[str]] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=5, stdout="", stderr="access denied")
        ),
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: False)

    result = execution_control._terminate_owned_process(process, 0.1)

    assert not result.succeeded
    assert "taskkill 退出码 5" in result.detail
    assert "process tree" in result.detail
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]


def test_posix_termination_requires_owned_process_group_to_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=5353, wait_times_out=False)
    signals: list[int] = []
    proc_root = tmp_path / "proc"
    for pid, state in [(101, "Z"), (102, "X"), (103, "S")]:
        process_dir = proc_root / str(pid)
        process_dir.mkdir(parents=True)
        process_dir.joinpath("stat").write_text(
            f"{pid} (child process) {state} 1 5353 5353 0",
            encoding="utf-8",
        )

    states = execution_control._linux_process_group_states(5353, proc_root)

    assert sorted(states) == ["S", "X", "Z"]
    malformed_proc_root = tmp_path / "malformed-proc"
    malformed_process_dir = malformed_proc_root / "104"
    malformed_process_dir.mkdir(parents=True)
    malformed_process_dir.joinpath("stat").write_text("malformed", encoding="utf-8")
    assert execution_control._linux_process_group_states(5353, malformed_proc_root) is None

    undecodable_proc_root = tmp_path / "undecodable-proc"
    undecodable_process_dir = undecodable_proc_root / "105"
    undecodable_process_dir.mkdir(parents=True)
    undecodable_process_dir.joinpath("stat").write_bytes(b"\xff")
    assert execution_control._linux_process_group_states(5353, undecodable_proc_root) is None

    unreadable_proc_root = tmp_path / "unreadable-proc"
    unreadable_process_dir = unreadable_proc_root / "106"
    unreadable_process_dir.joinpath("stat").mkdir(parents=True)
    assert execution_control._linux_process_group_states(5353, unreadable_proc_root) is None

    monkeypatch.setattr(execution_control.os, "killpg", lambda *_: None, raising=False)
    monkeypatch.setattr(
        execution_control,
        "_linux_process_group_states",
        lambda _: ["Z", "X"],
    )
    assert not execution_control._is_posix_process_group_alive(5353)

    monkeypatch.setattr(
        execution_control,
        "_linux_process_group_states",
        lambda _: ["Z", "S"],
    )
    assert execution_control._is_posix_process_group_alive(5353)

    monkeypatch.setattr(
        execution_control,
        "_linux_process_group_states",
        lambda _: None,
    )
    assert execution_control._is_posix_process_group_alive(5353)

    monkeypatch.setattr(
        execution_control,
        "_linux_process_group_states",
        lambda _: [],
    )
    assert execution_control._is_posix_process_group_alive(5353)

    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(execution_control.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        execution_control.os,
        "killpg",
        lambda pid, sent_signal: signals.append(sent_signal),
        raising=False,
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: False)
    monkeypatch.setattr(
        execution_control,
        "_is_posix_process_group_alive",
        lambda _: True,
    )

    result = execution_control._terminate_owned_process(process, 0.1)

    assert not result.succeeded
    assert execution_control.signal.SIGTERM in signals
    assert execution_control.signal.SIGKILL in signals
    assert f"process group {process.pid} 仍存活" in result.detail


def test_windows_final_wait_timeout_keeps_timeout_execution_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "wait-timeout" / "executions" / "worker",
        run_id="wait-timeout",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )
    process = _FakeOwnedProcess(pid=5252, wait_times_out=True)
    taskkill_commands: list[list[str]] = []

    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(execution_control.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    result = run_owned_process(["fake-runner"], "", tmp_path, 1, context)
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    inspection = inspect_execution_for_recovery(context.execution_dir.parents[1])

    assert result.status == "error"
    assert lease.status == "running"
    assert lease.finished_at is None
    assert lease.reason is not None
    assert "最终 wait 超时" in lease.reason
    assert f"PID {process.pid} 仍存活" in lease.reason
    assert process.kill_called
    assert taskkill_commands[-1][-1] == "/F"
    assert not inspection.can_recover


class _FakeOwnedProcess:
    def __init__(self, *, pid: int, wait_times_out: bool) -> None:
        self.pid = pid
        self.wait_times_out = wait_times_out
        self.returncode: int | None = None
        self.stdin = io.BytesIO()
        self.kill_called = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_times_out:
            raise subprocess.TimeoutExpired(cmd=["fake-runner"], timeout=timeout)
        self.returncode = 1
        return self.returncode

    def kill(self) -> None:
        self.kill_called = True


class _AlreadyCompletedProcess:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.returncode = 0
        self.stdin = io.BytesIO()

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def _write_execution(path: Path, lease: ExecutionLease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")
