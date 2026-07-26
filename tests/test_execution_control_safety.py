from __future__ import annotations

import _thread
import ctypes
import io
import json
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
import vega.execution_feedback as execution_feedback
import vega.execution_process as execution_process
from vega.execution_control import (
    ExecutionLease,
    RunnerExecutionContext,
    inspect_execution_for_recovery,
    request_stop_for_run,
    run_owned_process,
)


def test_execution_model_temp_path_preserves_windows_path_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution_control.os, "getpid", lambda: 0x7FFFFFFF)
    monkeypatch.setattr(
        execution_control,
        "uuid4",
        lambda: SimpleNamespace(hex="a" * 32),
    )

    initial_path = tmp_path / "r" / "execution.json"
    padding = 250 - len(str(initial_path))
    assert padding >= 0
    execution_dir = tmp_path / ("r" * (padding + 1))
    execution_path = execution_dir / "execution.json"
    temp_path = execution_control._execution_model_temp_path(execution_path)

    assert len(str(execution_path)) == 250
    assert temp_path.parent == execution_path.parent
    assert temp_path.name == ".e.7fffffff.aaaaaaaa"
    assert len(str(temp_path)) < 260


def test_execution_model_temp_paths_are_unique_within_one_process(
    tmp_path: Path,
) -> None:
    execution_dir = tmp_path / "executions" / "worker"

    execution_temp = execution_control._execution_model_temp_path(
        execution_dir / "execution.json"
    )
    stop_temp = execution_control._execution_model_temp_path(
        execution_dir / "stop-request.json"
    )

    assert execution_temp.parent == stop_temp.parent
    assert execution_temp != stop_temp


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
    assert all(thread.name != "vega-stdin-writer" for thread in threading.enumerate())


def test_owned_process_redacts_output_before_persisting_and_returning(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "redacted-output" / "executions" / "worker",
        run_id="redacted-output",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )
    fake_secret = "sk-runner-fake-secret-123456"

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
    assert "prompt should only go to stdin" not in execution_payload


def test_owned_process_reports_bounded_progress_without_persisting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(execution_feedback, "PROGRESS_INTERVAL_SECONDS", 0.03)
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "progress" / "executions" / "worker",
        run_id="progress",
        step="worker",
        heartbeat_interval_seconds=0.01,
        lease_timeout_seconds=0.5,
        progress_reporter=lambda step, elapsed: events.append((step, elapsed)),
    )

    result = run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(0.12)"],
        "",
        tmp_path,
        5,
        context,
    )

    execution_payload = context.execution_dir.joinpath("execution.json").read_text(
        encoding="utf-8"
    )
    assert result.status == "success"
    assert events[0] == ("worker", 0)
    assert len(events) >= 2
    assert all(step == "worker" and elapsed >= 0 for step, elapsed in events)
    assert "progress_reporter" not in execution_payload

    def broken_reporter(step: str, elapsed: int) -> None:
        raise RuntimeError(f"progress failed: {step}/{elapsed}")

    broken_context = RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "broken-progress" / "executions" / "reviewer",
        run_id="broken-progress",
        step="reviewer",
        heartbeat_interval_seconds=0.01,
        lease_timeout_seconds=0.5,
        progress_reporter=broken_reporter,
    )
    broken_result = run_owned_process(
        [sys.executable, "-c", "print('review completed')"],
        "",
        tmp_path,
        5,
        broken_context,
    )
    broken_lease = ExecutionLease.model_validate_json(
        broken_context.execution_dir.joinpath("execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert broken_result.status == "success"
    assert "review completed" in broken_result.output
    assert broken_lease.status == "completed"


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


def test_windows_access_denied_probe_blocks_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "access-denied"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=4242,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _install_fake_windows_process_api(
        monkeypatch,
        open_handle=0,
        last_error=5,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path


def test_windows_recovery_allows_reused_pid_with_different_creation_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "reused-pid"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=4242,
            owner_creation_token=100,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _install_fake_windows_process_api(
        monkeypatch,
        open_handle=1,
        exit_code=259,
        creation_token=200,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "PID" in inspection.summary


def test_windows_recovery_keeps_legacy_lease_without_creation_token_conservative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "legacy-lease"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=4242,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _install_fake_windows_process_api(
        monkeypatch,
        open_handle=1,
        exit_code=259,
        creation_token=200,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path


def test_windows_recovery_blocks_when_named_job_still_has_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "detached-descendant"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=1111,
            owner_creation_token=100,
            child_pid=2222,
            child_creation_token=200,
            windows_job_name="Local\\Vega-test-detached-descendant",
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(minutes=1)).isoformat(),
            lease_expires_at=(now - timedelta(seconds=30)).isoformat(),
            deadline=(now - timedelta(seconds=10)).isoformat(),
            status="running",
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda *_: execution_control.ProcessProbe("gone"),
    )
    monkeypatch.setattr(
        execution_control,
        "_probe_windows_job",
        lambda _: execution_control.WindowsJobProbe("active", active_processes=1),
        raising=False,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "Job Object" in inspection.summary


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object 专项回归")
def test_windows_detached_grandchild_prevents_success_and_is_terminated_on_timeout(
    tmp_path: Path,
) -> None:
    run_id = "detached-grandchild-timeout"
    execution_dir = tmp_path / "runs" / run_id / "executions" / "worker"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    grandchild_code = (
        "from pathlib import Path; import os, time; "
        f"Path({str(grandchild_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(30)"
    )
    root_code = (
        "import subprocess, sys; "
        "subprocess.Popen("
        f"[sys.executable, '-c', {grandchild_code!r}], "
        "creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP"
        ")"
    )
    context = RunnerExecutionContext(
        execution_dir=execution_dir,
        run_id=run_id,
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    grandchild_pid: int | None = None

    try:
        result = run_owned_process(
            [sys.executable, "-c", root_code],
            "",
            tmp_path,
            1,
            context,
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not grandchild_pid_path.exists():
            time.sleep(0.02)
        if grandchild_pid_path.exists():
            grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))

        lease = ExecutionLease.model_validate_json(
            execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
        )
        assert grandchild_pid is not None
        assert result.status == "timed_out"
        assert lease.status == "timed_out"
        assert lease.windows_job_name is not None
        assert not execution_control.is_process_alive(grandchild_pid)
        assert execution_control._probe_windows_job(lease.windows_job_name).status == "gone"
    finally:
        if grandchild_pid is not None and execution_control.is_process_alive(grandchild_pid):
            subprocess.run(
                ["taskkill", "/PID", str(grandchild_pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object 专项回归")
@pytest.mark.parametrize("failure_stage", ["assign", "resume"])
def test_windows_job_startup_failure_never_runs_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    run_id = f"job-startup-{failure_stage}"
    execution_dir = tmp_path / "runs" / run_id / "executions" / "worker"
    child_marker = tmp_path / f"{failure_stage}.started"
    context = RunnerExecutionContext(
        execution_dir=execution_dir,
        run_id=run_id,
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )

    if failure_stage == "assign":
        monkeypatch.setattr(
            execution_control.NamedWindowsJob,
            "assign_process_id",
            lambda *_: (_ for _ in ()).throw(
                execution_control.WindowsJobError("simulated assignment failure")
            ),
        )
    else:
        monkeypatch.setattr(
            execution_process,
            "resume_suspended_process",
            lambda *_: (_ for _ in ()).throw(
                execution_control.WindowsJobError("simulated resume failure")
            ),
        )

    result = run_owned_process(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                f"Path({str(child_marker)!r}).write_text('started', encoding='utf-8')"
            ),
        ],
        "",
        tmp_path,
        5,
        context,
    )

    lease = ExecutionLease.model_validate_json(
        execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "error"
    assert lease.status == "failed"
    assert lease.windows_job_name is not None
    assert lease.child_pid is not None
    assert not child_marker.exists()
    assert not execution_control.is_process_alive(lease.child_pid)
    assert execution_control._probe_windows_job(lease.windows_job_name).status == "gone"


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
    assert set(json.loads(request_payload)) == {
        "reason",
        "requested_at",
        "requester_pid",
    }


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
    monkeypatch.setattr(
        execution_control,
        "_create_windows_job_for_execution",
        lambda *_: None,
    )
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


def test_windows_taskkill_replaces_undecodable_localized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_kwargs: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed_kwargs.update(kwargs)
        if kwargs.get("errors") != "replace":
            raise UnicodeDecodeError(
                "utf-8",
                b"\xb3",
                0,
                1,
                "invalid start byte",
            )
        return SimpleNamespace(
            returncode=5,
            stdout="",
            stderr="localized output \ufffd",
        )

    monkeypatch.setattr(execution_control.subprocess, "run", fake_run)

    result = execution_control._run_windows_taskkill(4242, force=False, timeout=1)

    assert observed_kwargs["text"] is True
    assert observed_kwargs["errors"] == "replace"
    assert result == "taskkill \u9000\u51fa\u7801 5\uff1alocalized output \ufffd"


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


def test_windows_does_not_force_kill_reused_pid_after_owned_handle_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=4444, wait_times_out=False)
    taskkill_commands: list[list[str]] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "is_process_alive",
        lambda _: True,
    )

    result = execution_control._terminate_owned_process(process, 0.1)

    assert result.succeeded
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]
    assert not process.kill_called


def test_posix_termination_requires_owned_process_group_to_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=5353, wait_times_out=False)
    signals: list[int] = []
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


def test_posix_process_group_ignores_terminal_linux_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        execution_control,
        "_create_windows_job_for_execution",
        lambda *_: None,
    )
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


class _FakeWindowsFunction:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)  # type: ignore[operator]


class _FakeWindowsKernel32:
    def __init__(
        self,
        *,
        open_handle: int,
        exit_code: int,
        creation_token: int,
    ) -> None:
        self.OpenProcess = _FakeWindowsFunction(lambda *_: open_handle)
        self.GetExitCodeProcess = _FakeWindowsFunction(
            lambda _handle, pointer: self._set_exit_code(pointer, exit_code)
        )
        self.GetProcessTimes = _FakeWindowsFunction(
            lambda _handle, creation, _exit, _kernel, _user: self._set_creation_token(
                creation,
                creation_token,
            )
        )
        self.CloseHandle = _FakeWindowsFunction(lambda _handle: 1)

    @staticmethod
    def _set_exit_code(pointer: object, exit_code: int) -> int:
        pointer._obj.value = exit_code  # type: ignore[attr-defined]
        return 1

    @staticmethod
    def _set_creation_token(pointer: object, creation_token: int) -> int:
        pointer._obj.dwLowDateTime = creation_token & 0xFFFFFFFF  # type: ignore[attr-defined]
        pointer._obj.dwHighDateTime = creation_token >> 32  # type: ignore[attr-defined]
        return 1


def _install_fake_windows_process_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    open_handle: int,
    last_error: int = 0,
    exit_code: int = 0,
    creation_token: int = 0,
) -> None:
    kernel32 = _FakeWindowsKernel32(
        open_handle=open_handle,
        exit_code=exit_code,
        creation_token=creation_token,
    )
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)


def _write_execution(path: Path, lease: ExecutionLease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")
