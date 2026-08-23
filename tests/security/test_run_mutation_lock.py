from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import vega.execution_control as execution_control_module
import vega.run_lock as run_lock_module
from vega.decision import DecisionStore, append_run_decision
from vega.execution_control import ExecutionLease, request_stop_for_run
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import LoopAutomationState
from vega.recovery_runtime import RecoveryRuntime
from vega.run_lock import (
    RunMutationBusyError,
    RunMutationLock,
    RunMutationLockError,
)


LOCK_HOLDER_SCRIPT = textwrap.dedent(
    """
    import sys
    import time
    from pathlib import Path

    from vega.run_lock import RunMutationLock

    run_dir = Path(sys.argv[1])
    ready = Path(sys.argv[2])
    release = Path(sys.argv[3])
    with RunMutationLock.acquire(run_dir, "loop.continue"):
        ready.write_text("ready\\n", encoding="utf-8")
        while not release.exists():
            time.sleep(0.05)
    """
)


CRASH_OWNER_SCRIPT = textwrap.dedent(
    r"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from vega.run_lock import RunMutationLock

    CHILD_SCRIPT = '''
    import sys
    import time
    from pathlib import Path

    release = Path(sys.argv[1])
    while not release.exists():
        time.sleep(0.05)
    '''

    run_dir = Path(sys.argv[1])
    ready = Path(sys.argv[2])
    release = Path(sys.argv[3])
    child_pid_path = Path(sys.argv[4])
    with RunMutationLock.acquire(run_dir, "loop.start"):
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD_SCRIPT, str(release)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child_pid_path.write_text(str(child.pid), encoding="utf-8")
        ready.write_text("ready\n", encoding="utf-8")
        os._exit(23)
    """
)


def test_cross_process_lock_blocks_without_mutating_business_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run with spaces 空格"
    iteration_dir = run_dir / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    state_path = run_dir / "state.json"
    trace_path = run_dir / "trace.jsonl"
    sentinel_path = iteration_dir / "sentinel.txt"
    state_path.write_text('{"status": "needs_human"}\n', encoding="utf-8")
    trace_path.write_text('{"event": "sentinel"}\n', encoding="utf-8")
    sentinel_path.write_text("preserve\n", encoding="utf-8")
    before = _business_snapshot(state_path, trace_path, sentinel_path)

    ready = tmp_path / "control" / "holder-ready"
    release = tmp_path / "control" / "holder-release"
    ready.parent.mkdir()
    holder = _start_python(
        LOCK_HOLDER_SCRIPT,
        run_dir,
        ready,
        release,
    )
    recorded_owner_pid: int | None = None
    try:
        _wait_until(lambda: ready.exists(), process=holder, description="lock holder")
        with pytest.raises(RunMutationBusyError, match="run 正由其他进程修改"):
            RunMutationLock.acquire(run_dir, "loop.finish")
        assert _business_snapshot(state_path, trace_path, sentinel_path) == before

        owner = json.loads(
            run_dir.joinpath(
                ".control",
                "run-mutation-owner.json",
            ).read_text(encoding="utf-8")
        )
        assert owner["operation"] == "loop.continue"
        recorded_owner_pid = int(owner["owner_pid"])
        assert _process_alive(recorded_owner_pid)
    finally:
        release.touch()
        if holder.poll() is None:
            holder.wait(timeout=10)
        if recorded_owner_pid is not None:
            _wait_until(
                lambda: not _process_alive(recorded_owner_pid),
                description="lock owner exit",
            )

    with RunMutationLock.acquire(run_dir, "loop.finish"):
        assert run_dir.joinpath(".control", "run-mutation-owner.json").is_file()
    assert run_dir.joinpath(".control", "run-mutation.lock").is_file()
    assert not run_dir.joinpath(".control", "run-mutation-owner.json").exists()


def test_crashed_owner_releases_lock_without_child_inheriting_handle(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "crash-release-loop"
    run_dir.mkdir(parents=True)
    ready = tmp_path / "control" / "crash-ready"
    release = tmp_path / "control" / "child-release"
    child_pid_path = tmp_path / "control" / "child.pid"
    ready.parent.mkdir()
    owner = _start_python(
        CRASH_OWNER_SCRIPT,
        run_dir,
        ready,
        release,
        child_pid_path,
    )
    child_pid: int | None = None
    try:
        _wait_until(lambda: ready.exists(), process=owner, description="crash owner")
        owner.wait(timeout=10)
        assert owner.returncode == 23
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        assert _process_alive(child_pid)

        with RunMutationLock.acquire(run_dir, "loop.recover"):
            assert _process_alive(child_pid)
    finally:
        release.touch()
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
        if child_pid is not None:
            _wait_until(
                lambda: not _process_alive(child_pid),
                description="crash owner child exit",
            )


def test_owner_metadata_does_not_follow_preexisting_fixed_temp_hardlink(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "hardlink-safe-loop"
    control_dir = run_dir / ".control"
    control_dir.mkdir(parents=True)
    sentinel = tmp_path / "outside-sentinel.txt"
    sentinel.write_text("preserve\n", encoding="utf-8", newline="\n")
    fixed_temp = control_dir / ".owner.tmp"
    try:
        os.link(sentinel, fixed_temp)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    with RunMutationLock.acquire(run_dir, "loop.start"):
        owner_path = control_dir / "run-mutation-owner.json"
        assert owner_path.is_file()
        assert sentinel.read_text(encoding="utf-8") == "preserve\n"

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    assert fixed_temp.is_file()
    assert not list(control_dir.glob(".o.*"))


def test_owner_metadata_temp_path_preserves_windows_path_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedUuid:
        hex = "f" * 32

    monkeypatch.setattr(run_lock_module, "uuid4", lambda: FixedUuid())

    initial_path = (
        tmp_path / "r" / ".control" / "run-mutation-owner.json"
    )
    padding = 245 - len(str(initial_path))
    assert padding >= 0
    run_dir = tmp_path / ("r" * (padding + 1))
    owner_path = run_dir / ".control" / "run-mutation-owner.json"
    temp_path = run_lock_module._owner_metadata_temp_path(owner_path)

    assert len(str(owner_path)) == 245
    assert temp_path.parent == owner_path.parent
    assert temp_path.name == f".o.{'f' * 32}"
    assert len(str(temp_path)) < 260


def test_lock_file_rejects_preexisting_hardlink_without_mutating_sentinel(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "hardlink-lock-loop"
    control_dir = run_dir / ".control"
    control_dir.mkdir(parents=True)
    sentinel = tmp_path / "outside-empty-sentinel.bin"
    sentinel.write_bytes(b"")
    lock_path = control_dir / "run-mutation.lock"
    try:
        os.link(sentinel, lock_path)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    with pytest.raises(RunMutationLockError, match="hardlink"):
        RunMutationLock.acquire(run_dir, "loop.start")

    assert sentinel.read_bytes() == b""
    assert lock_path.read_bytes() == b""
    assert not control_dir.joinpath("run-mutation-owner.json").exists()


def test_stop_fails_closed_when_active_execution_switches_during_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "stop-switch-loop"
    first_path = run_dir / "iterations" / "01" / "executions" / "worker" / "execution.json"
    first_path.parent.mkdir(parents=True)
    first = _execution_lease(
        run_dir.name,
        execution_id="execution-a",
        step="worker",
        iteration=1,
    )
    _write_execution(first_path, first)
    original_write = execution_control_module._write_model_atomic

    def switch_execution(
        path: Path,
        model: object,
        **kwargs: object,
    ) -> None:
        original_write(path, model, **kwargs)
        if path.name != "stop-request.json":
            return
        completed = first.model_copy(
            update={
                "status": "completed",
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        original_write(first_path, completed)
        second_path = (
            run_dir
            / "iterations"
            / "02"
            / "executions"
            / "reviewer"
            / "execution.json"
        )
        second_path.parent.mkdir(parents=True)
        _write_execution(
            second_path,
            _execution_lease(
                run_dir.name,
                execution_id="execution-b",
                step="reviewer",
                iteration=2,
            ),
        )

    monkeypatch.setattr(
        execution_control_module,
        "_write_model_atomic",
        switch_execution,
    )
    with pytest.raises(ValueError, match="发生切换或并发"):
        request_stop_for_run(run_dir, "stop race probe")

    request = json.loads(
        first_path.parent.joinpath("stop-request.json").read_text(encoding="utf-8")
    )
    assert request["execution_id"] == "execution-a"
    assert not run_dir.joinpath(
        "iterations",
        "02",
        "executions",
        "reviewer",
        "stop-request.json",
    ).exists()


def test_new_execution_ignores_legacy_stop_request_without_identity() -> None:
    request = execution_control_module.StopRequest(
        reason="legacy stop",
        requested_at=datetime.now(UTC).isoformat(),
        requester_pid=os.getpid(),
    )
    new_lease = _execution_lease(
        "legacy-stop-loop",
        execution_id="new-execution",
        step="worker",
        iteration=1,
    )

    assert not execution_control_module._stop_request_matches_lease(
        request,
        new_lease,
    )


def test_stop_accepts_target_that_becomes_stopped_during_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "stop-consumed-loop"
    execution_path = (
        run_dir
        / "iterations"
        / "01"
        / "executions"
        / "worker"
        / "execution.json"
    )
    execution_path.parent.mkdir(parents=True)
    lease = _execution_lease(
        run_dir.name,
        execution_id="execution-consumed",
        step="worker",
        iteration=1,
    )
    _write_execution(execution_path, lease)
    original_write = execution_control_module._write_model_atomic

    def consume_stop_request(
        path: Path,
        model: object,
        **kwargs: object,
    ) -> None:
        original_write(path, model, **kwargs)
        if path.name != "stop-request.json":
            return
        stopped = lease.model_copy(
            update={
                "status": "stopped",
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        original_write(execution_path, stopped)

    monkeypatch.setattr(
        execution_control_module,
        "_write_model_atomic",
        consume_stop_request,
    )

    confirmed = request_stop_for_run(run_dir, "stop consumed probe")

    assert confirmed.path == execution_path
    assert confirmed.lease.status == "stopped"


def test_runtime_mutators_fail_before_writing_when_run_is_busy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = workspace / "runs" / "busy-loop"
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="inline-text",
        status="needs_human",
        current_step="waiting_for_worker",
    )
    state.save(run_dir / "state.json")
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text('{"event": "sentinel"}\n', encoding="utf-8")
    before_state = run_dir.joinpath("state.json").read_bytes()
    before_trace = trace_path.read_bytes()

    with RunMutationLock.acquire(run_dir, "loop.start"):
        with pytest.raises(RunMutationBusyError):
            LoopAutomationRuntime(workspace).continue_assist(
                run_dir.name,
                repo,
                verify=False,
            )
        with pytest.raises(RunMutationBusyError):
            RecoveryRuntime(workspace).recover_loop(run_dir.name, "busy")
        with pytest.raises(RunMutationBusyError):
            FinishRuntime(workspace).run(run_dir.name)
        with pytest.raises(RunMutationBusyError):
            append_run_decision(
                workspace,
                run_dir.name,
                decision_type="finish",
                decision="approved",
                reason="busy must fail before append",
            )
        with pytest.raises(RunMutationBusyError):
            DecisionStore(run_dir).append(
                decision_type="custom",
                decision="approved",
                reason="direct store append must use the same lock",
            )

        assert run_dir.joinpath("state.json").read_bytes() == before_state
        assert trace_path.read_bytes() == before_trace
        assert not run_dir.joinpath("iterations").exists()
        assert not run_dir.joinpath("finish-summary.json").exists()
        assert not run_dir.joinpath("finish-report.md").exists()
        assert not run_dir.joinpath("recovery-report.md").exists()
        assert not run_dir.joinpath("decisions.jsonl").exists()

    decision = append_run_decision(
        workspace,
        run_dir.name,
        decision_type="finish",
        decision="approved",
        reason="lock released",
    )
    assert decision.decision == "approved"
    decision_lines = run_dir.joinpath("decisions.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(decision_lines) == 1
    assert json.loads(decision_lines[0])["reason"] == "lock released"


def test_lock_errors_fail_closed_without_leaking_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "lock-error-loop"
    run_dir.mkdir(parents=True)
    fake_secret = "sk-test-secret"

    with pytest.raises(RunMutationLockError, match="不支持的"):
        RunMutationLock.acquire(run_dir, fake_secret)
    assert not run_dir.joinpath(".control").exists()

    def raise_io_error(stream: object) -> None:
        del stream
        raise OSError(errno.EIO, "simulated lock filesystem error")

    monkeypatch.setattr(run_lock_module, "_lock_stream", raise_io_error)
    with pytest.raises(RunMutationLockError, match="无法为 run 建立修改锁"):
        RunMutationLock.acquire(run_dir, "loop.finish")
    assert not run_dir.joinpath(
        ".control",
        "run-mutation-owner.json",
    ).exists()
    assert fake_secret not in "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in run_dir.rglob("*")
        if path.is_file()
    )


def _business_snapshot(*paths: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in paths}


def _execution_lease(
    run_id: str,
    *,
    execution_id: str,
    step: str,
    iteration: int,
) -> ExecutionLease:
    now = datetime.now(UTC)
    return ExecutionLease(
        run_id=run_id,
        execution_id=execution_id,
        step=step,
        iteration=iteration,
        owner_pid=os.getpid(),
        child_pid=None,
        command=["probe"],
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(seconds=30)).isoformat(),
        deadline=(now + timedelta(seconds=60)).isoformat(),
        status="running",
    )


def _write_execution(path: Path, lease: ExecutionLease) -> None:
    path.write_text(
        lease.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _start_python(script: str, *arguments: Path) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in [src_path, environment.get("PYTHONPATH", "")] if item
    )
    return subprocess.Popen(
        [sys.executable, "-c", script, *(str(item) for item in arguments)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_until(
    predicate,
    *,
    description: str,
    process: subprocess.Popen[str] | None = None,
    timeout_seconds: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        if process is not None and process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            pytest.fail(
                f"process exited before {description}: code={process.returncode}\n{output}"
            )
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {description}")


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
