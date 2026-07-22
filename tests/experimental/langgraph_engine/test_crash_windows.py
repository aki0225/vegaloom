from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")

import vega.loop_graph_runtime as loop_graph_runtime
import vega.loop_graph_recovery as loop_graph_recovery
from vega.execution_control import ExecutionController, ExecutionLease
from vega.loop_graph_checkpoint import (
    GraphCheckpointValidationError,
    capture_trusted_checkpoint_state,
    checkpoint_config,
    validate_checkpoint_manifest,
    write_checkpoint_manifest,
)
from vega.loop_graph_runtime import GraphExecutionInterrupted
from vega.loop_graph_recovery import GraphRecoveryValidationError
from vega.loop_recovery_replay import RecoveryReplayValidationError
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput, GateResult
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.trace import TraceWriter
from vega.verification import VerificationRunResult


class CrashOnce:
    def __init__(self, target: str) -> None:
        self.target = target
        self.triggered = False

    def __call__(self, point: str) -> None:
        if point == self.target and not self.triggered:
            self.triggered = True
            raise GraphExecutionInterrupted(f"fault injected at {point}")


class EvidenceWorker:
    def __init__(self, persistent_counter: Path | None = None) -> None:
        self.calls = 0
        self.write_calls = 0
        self.persistent_counter = persistent_counter

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        return ["gate3-evidence-worker"]

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        assert execution_context is not None
        self.calls += 1
        if self.persistent_counter is not None:
            previous = (
                int(self.persistent_counter.read_text(encoding="utf-8"))
                if self.persistent_counter.is_file()
                else 0
            )
            self.persistent_counter.write_text(
                str(previous + 1),
                encoding="utf-8",
                newline="\n",
            )
        command = self.build_command(repo_path, sandbox)
        controller = ExecutionController(execution_context)
        lease = controller.prepare(command, timeout_seconds)
        # 该模块用异常模拟原进程崩溃；从首次落盘起绑定已退出的 fixture owner，
        # 避免测试进程本身的 PID 被误当成仍在提交证据的真实执行主体。
        lease.owner_pid = 999_999
        controller.heartbeat()
        self.write_calls += 1
        repo_path.joinpath("README.md").write_text(
            "# Demo\nGATE3_WORKER_EFFECT\n",
            encoding="utf-8",
            newline="\n",
        )
        if execution_context.fault_injector is not None:
            execution_context.fault_injector(
                "after_external_effect_before_terminal_execution"
            )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output="GATE3_WORKER_OUTPUT",
            command=command,
        )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    path.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Tests",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _build_runtime(
    workspace: Path,
    worker: EvidenceWorker,
    crash: Callable[[str], None],
) -> LoopAutomationRuntime:
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        graph_fault_injector=crash,
    )

    def prepare_run(request) -> Path:
        brief_run = request.workspace / "runs" / "gate3-fixture-brief"
        brief_run.mkdir(parents=True, exist_ok=True)
        brief_run.joinpath("agent-brief.md").write_text(
            "# Agent Brief\n\n修复 fixture。\n",
            encoding="utf-8",
            newline="\n",
        )
        brief_run.joinpath("project-context.md").write_text(
            "# Project Context\n",
            encoding="utf-8",
            newline="\n",
        )
        return brief_run

    def run_verification(request) -> VerificationRunResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        result_path = request.output_dir / "verification-result.json"
        summary_path = request.output_dir / "verification-summary.md"
        result_path.write_text(
            json.dumps(
                {
                    "command_count": 1,
                    "failed_count": 0,
                    "results": [
                        {
                            "command": "fixture-check",
                            "status": "passed",
                            "returncode": 0,
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        summary_path.write_text(
            "# Verification\n\n- passed\n",
            encoding="utf-8",
            newline="\n",
        )
        return VerificationRunResult(
            summary_path=summary_path,
            result_path=result_path,
            command_count=1,
            failed_count=0,
        )

    def run_reflect(request) -> Path:
        reflect_run = request.workspace / "runs" / "gate3-fixture-reflect"
        reflect_run.mkdir(parents=True, exist_ok=True)
        reflect_run.joinpath("state.json").write_text(
            json.dumps({"status": "failed"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for name in (
            "diff-summary.md",
            "test-summary.md",
            "project-context.md",
            "reflection.md",
        ):
            reflect_run.joinpath(name).write_text(
                f"# {name}\n",
                encoding="utf-8",
                newline="\n",
            )
        return reflect_run

    def evaluate_risk(request) -> GateResult:
        return GateResult(risk="low", recommendation="self-check")

    runtime.step_services = replace(
        runtime.step_services,
        prepare_run=prepare_run,
        run_verification=run_verification,
        run_reflect=run_reflect,
        evaluate_risk=evaluate_risk,
    )
    return runtime


def _start_until_crash(
    tmp_path: Path,
    fault_point: str,
) -> tuple[LoopAutomationRuntime, EvidenceWorker, Path, Path]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    worker = EvidenceWorker()
    crash = CrashOnce(fault_point)
    runtime = _build_runtime(workspace, worker, crash)
    with pytest.raises(GraphExecutionInterrupted, match="fault injected"):
        runtime.start(
            BriefInput(
                mode="bug",
                text="Gate 3 crash fixture",
                source="test",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=1,
            verify=True,
            engine="langgraph",
        )
    run_dir = next(
        path
        for path in workspace.joinpath("runs").iterdir()
        if path.name.endswith("-loop")
    )
    validate_checkpoint_manifest(run_dir)
    return runtime, worker, repo, run_dir


def _state(run_dir: Path) -> dict[str, object]:
    return json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))


def _trace_event_counts(run_dir: Path) -> Counter[str]:
    return Counter(
        json.loads(line)["event"]
        for line in run_dir.joinpath("trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    )


def _stable_business_artifact_hashes(run_dir: Path) -> dict[str, str]:
    mutable_refs = {
        "state.json",
        "trace.jsonl",
        "eval.md",
        "final-report.md",
        "graph-recovery-report.md",
    }
    hashes: dict[str, str] = {}
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir).as_posix()
        if relative in mutable_refs or relative.startswith("graph/"):
            continue
        hashes[relative] = sha256(path.read_bytes()).hexdigest()
    return hashes


def _graph_artifact_hashes(run_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(run_dir).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in run_dir.joinpath("graph").rglob("*")
        if path.is_file()
    }


def _run_artifact_hashes(run_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(run_dir).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in run_dir.rglob("*")
        if path.is_file()
    }


def _write_starting_execution_without_child(
    run_dir: Path,
    *,
    owner_pid: int,
) -> None:
    execution_path = (
        run_dir
        / "iterations"
        / "01"
        / "executions"
        / "worker"
        / "execution.json"
    )
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    execution_path.write_text(
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            iteration=1,
            engine="langgraph",
            owner_pid=owner_pid,
            child_pid=None,
            command=["gate3-evidence-worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="starting",
        ).model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )


def test_final_checkpoint_manifest_failure_enters_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "manifest-failure-loop"
    run_dir.joinpath("graph").mkdir(parents=True)
    run_dir.joinpath("graph", "checkpoints.sqlite").write_bytes(b"checkpoint")
    quarantined: list[Exception] = []

    def fail_final_manifest(*args, **kwargs):
        raise GraphCheckpointValidationError("final manifest write blocked")

    @contextmanager
    def fake_checkpointer(*args, **kwargs):
        yield object()

    class FakeGraph:
        def invoke(self, value, *, config):
            return {"status": "success"}

        def get_state(self, config):
            return SimpleNamespace(tasks=())

    monkeypatch.setattr(
        loop_graph_runtime,
        "write_checkpoint_manifest",
        fail_final_manifest,
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "open_sqlite_checkpointer",
        fake_checkpointer,
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "_compile_graph",
        lambda *args, **kwargs: FakeGraph(),
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "_graph_recursion_limit",
        lambda run_dir: 10,
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "validate_graph_state",
        lambda run_dir, state, **kwargs: state,
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "_quarantine_untrusted_success",
        lambda run_dir, error: quarantined.append(error),
    )

    with pytest.raises(
        GraphCheckpointValidationError,
        match="final manifest write blocked",
    ):
        loop_graph_runtime._run_graph(
            run_dir,
            SimpleNamespace(),
            initial_state={"status": "running"},
            fault_injector=None,
            resume=False,
            reconciliation=None,
            resume_command=None,
        )

    assert len(quarantined) == 1
    assert isinstance(quarantined[0], GraphCheckpointValidationError)


def test_p0_1_crash_before_execution_safely_starts_one_worker(tmp_path: Path) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    assert worker.calls == 0
    assert not run_dir.joinpath(
        "iterations/01/executions/worker/execution.json"
    ).exists()
    status_before_recovery = run_status_payload(
        tmp_path / "workspace",
        run_dir.name,
    )
    assert any(
        "vega recover --run" in item
        for item in status_before_recovery["next_steps"]
    )

    recovered = runtime.recover_langgraph(
        run_dir.name,
        "P0-1 fault injection",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert worker.calls == 1
    assert worker.write_calls == 1
    assert _state(run_dir)["status"] == "needs_human"


@pytest.mark.parametrize(
    "drift_stage",
    ["opened_store", "observed_file"],
)
def test_recovery_rejects_checkpoint_content_drift_during_state_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_stage: str,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    capture = loop_graph_runtime.capture_checkpoint_data_snapshot
    capture_store = loop_graph_runtime.capture_checkpoint_store_identity
    captures = 0
    store_captures = 0

    def capture_with_drift(target_run_dir: Path):
        nonlocal captures
        snapshot = capture(target_run_dir)
        captures += 1
        if drift_stage != "observed_file" or captures != 2:
            return snapshot
        return replace(
            snapshot,
            checkpoint=replace(
                snapshot.checkpoint,
                sha256="f" * 64,
            ),
        )

    def capture_store_with_drift(*args, **kwargs):
        nonlocal store_captures
        identity = capture_store(*args, **kwargs)
        store_captures += 1
        if drift_stage != "opened_store" or store_captures != 1:
            return identity
        return replace(
            identity,
            content_sha256="d" * 64,
        )

    monkeypatch.setattr(
        loop_graph_runtime,
        "capture_checkpoint_data_snapshot",
        capture_with_drift,
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "capture_checkpoint_store_identity",
        capture_store_with_drift,
    )

    recovered = runtime.recover_langgraph(
        run_dir.name,
        "拒绝封存与 get_state 不同的 checkpoint",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert captures == (1 if drift_stage == "opened_store" else 2)
    assert _state(run_dir)["status"] == "needs_human"
    assert "checkpoint_validation_failed" in run_dir.joinpath(
        "graph-recovery-report.md"
    ).read_text(encoding="utf-8")
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_graph_operation_lease_rejects_concurrent_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    entered = threading.Event()
    release = threading.Event()
    original_check = loop_graph_runtime._checkpoint_is_trusted_or_stop

    def blocking_checkpoint_check(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("并发恢复测试未释放首个 recovery")
        return original_check(*args, **kwargs)

    monkeypatch.setattr(
        loop_graph_runtime,
        "_checkpoint_is_trusted_or_stop",
        blocking_checkpoint_check,
    )
    result: list[Path] = []
    errors: list[BaseException] = []

    def run_first_recovery() -> None:
        try:
            result.append(
                runtime.recover_langgraph(
                    run_dir.name,
                    "首个并发 recovery",
                    engine="langgraph",
                )
            )
        except BaseException as exc:  # noqa: BLE001 - 线程错误需回传主测试
            errors.append(exc)

    thread = threading.Thread(target=run_first_recovery, daemon=True)
    thread.start()
    assert entered.wait(timeout=10)
    try:
        with pytest.raises(
            GraphRecoveryValidationError,
            match="已有活跃 Graph 操作",
        ):
            runtime.recover_langgraph(
                run_dir.name,
                "第二个并发 recovery",
                engine="langgraph",
            )
    finally:
        release.set()
        thread.join(timeout=30)

    assert not thread.is_alive()
    assert errors == []
    assert result == [run_dir]
    assert worker.calls == 1
    assert worker.write_calls == 1


def test_starting_execution_without_child_pid_is_not_replayed(
    tmp_path: Path,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    _write_starting_execution_without_child(
        run_dir,
        owner_pid=os.getpid(),
    )
    run_before_recovery = _run_artifact_hashes(run_dir)

    with pytest.raises(
        GraphRecoveryValidationError,
        match="execution owner PID 仍存活",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "存活 owner 的 starting execution 不得并发恢复",
            engine="langgraph",
        )

    assert worker.calls == 0
    assert worker.write_calls == 0
    assert _run_artifact_hashes(run_dir) == run_before_recovery


def test_dead_owner_starting_execution_without_child_pid_needs_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    _write_starting_execution_without_child(
        run_dir,
        owner_pid=999_999,
    )
    monkeypatch.setattr(
        loop_graph_recovery,
        "is_process_alive",
        lambda _pid: False,
    )

    runtime.recover_langgraph(
        run_dir.name,
        "已死亡 owner 的 starting execution 仍不得重放",
        engine="langgraph",
    )

    assert worker.calls == 0
    assert worker.write_calls == 0
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    report = run_dir.joinpath("graph-recovery-report.md").read_text(
        encoding="utf-8"
    )
    assert "child PID 未落盘也不能证明" in report
    assert "禁止重复启动或执行" in report


def test_live_owner_terminal_execution_without_committed_result_blocks_recovery(
    tmp_path: Path,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    execution_path = (
        run_dir
        / "iterations"
        / "01"
        / "executions"
        / "worker"
        / "execution.json"
    )
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    execution_path.write_text(
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            iteration=1,
            engine="langgraph",
            owner_pid=os.getpid(),
            child_pid=None,
            command=["gate3-evidence-worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="completed",
            returncode=0,
            finished_at=now.isoformat(),
        ).model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    before = _run_artifact_hashes(run_dir)

    with pytest.raises(
        GraphRecoveryValidationError,
        match="execution owner PID 仍存活",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "terminal execution 尚未提交 Step Result",
            engine="langgraph",
        )

    assert _run_artifact_hashes(run_dir) == before
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_live_owner_terminal_execution_with_wrong_bound_step_result_blocks_recovery(
    tmp_path: Path,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_step_result_before_state",
    )
    execution_path = (
        run_dir
        / "iterations"
        / "01"
        / "executions"
        / "worker"
        / "execution.json"
    )
    execution = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    drifted_idempotency_key = "sha256:" + "9" * 64
    execution.owner_pid = os.getpid()
    execution.idempotency_key = drifted_idempotency_key
    execution_path.write_text(
        execution.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    step_result_path = (
        run_dir / "step-results" / "worker-iteration-01.json"
    )
    step_result = json.loads(
        step_result_path.read_text(encoding="utf-8")
    )
    step_result["idempotency_key"] = drifted_idempotency_key
    step_result["execution_sha256"] = sha256(
        execution_path.read_bytes()
    ).hexdigest()
    content_payload = dict(step_result)
    content_payload["step_result_id"] = None
    step_result["step_result_id"] = "sha256:" + sha256(
        json.dumps(
            content_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    step_result_path.write_text(
        json.dumps(
            step_result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    before = _run_artifact_hashes(run_dir)

    with pytest.raises(
        GraphRecoveryValidationError,
        match="execution owner PID 仍存活",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "Step Result 与 attempt identity 错绑",
            engine="langgraph",
        )

    assert _run_artifact_hashes(run_dir) == before
    assert worker.calls == 1
    assert worker.write_calls == 1


def test_p0_2_unknown_worker_side_effect_stops_without_replay(tmp_path: Path) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_external_effect_before_terminal_execution",
    )
    execution = json.loads(
        run_dir.joinpath(
            "iterations/01/executions/worker/execution.json"
        ).read_text(encoding="utf-8")
    )
    assert execution["status"] == "starting"
    assert worker.calls == 1
    assert worker.write_calls == 1

    recovered = runtime.recover_langgraph(
        run_dir.name,
        "P0-2 fault injection",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert worker.calls == 1
    assert worker.write_calls == 1
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    report = run_dir.joinpath("graph-recovery-report.md").read_text(
        encoding="utf-8"
    )
    assert "禁止重复启动" in report
    status_after_recovery = run_status_payload(
        tmp_path / "workspace",
        run_dir.name,
    )
    assert any(
        "无法证明可安全重放" in item
        for item in status_after_recovery["next_steps"]
    )


def test_p0_3_step_result_is_reused_without_second_worker(tmp_path: Path) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_step_result_before_state",
    )
    assert worker.calls == 1
    assert run_dir.joinpath(
        "step-results/worker-iteration-01.json"
    ).is_file()
    events_before = _trace_event_counts(run_dir)
    artifacts_before = _stable_business_artifact_hashes(run_dir)

    runtime.recover_langgraph(
        run_dir.name,
        "P0-3 fault injection",
        engine="langgraph",
    )

    assert worker.calls == 1
    assert worker.write_calls == 1
    assert _state(run_dir)["status"] == "needs_human"
    events_after = _trace_event_counts(run_dir)
    for event in (
        "brief_finished",
        "worker_prompt_measured",
        "worker_started",
        "worker_finished",
    ):
        assert events_after[event] == events_before[event]
    artifacts_after = _stable_business_artifact_hashes(run_dir)
    assert {
        ref: artifacts_after[ref]
        for ref in artifacts_before
    } == artifacts_before


def test_p0_4a_state_advances_before_checkpoint_without_worker_replay(
    tmp_path: Path,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_state_before_checkpoint",
    )
    assert _state(run_dir)["current_step"] == "verify"
    assert worker.calls == 1
    events_before = _trace_event_counts(run_dir)
    artifacts_before = _stable_business_artifact_hashes(run_dir)

    runtime.recover_langgraph(
        run_dir.name,
        "P0-4a fault injection",
        engine="langgraph",
    )

    assert worker.calls == 1
    assert worker.write_calls == 1
    assert _state(run_dir)["status"] == "needs_human"
    events_after = _trace_event_counts(run_dir)
    for event in (
        "brief_finished",
        "worker_prompt_measured",
        "worker_started",
        "worker_finished",
        "workspace_check_finished",
    ):
        assert events_after[event] == events_before[event]
    artifacts_after = _stable_business_artifact_hashes(run_dir)
    assert {
        ref: artifacts_after[ref]
        for ref in artifacts_before
    } == artifacts_before


def test_recovery_rejects_replayed_artifact_mismatch_without_overwrite(
    tmp_path: Path,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_step_result_before_state",
    )
    loop_plan = run_dir / "loop-plan.md"
    loop_plan.write_text(
        "# 人工篡改\n",
        encoding="utf-8",
        newline="\n",
    )
    events_before = _trace_event_counts(run_dir)

    with pytest.raises(
        RecoveryReplayValidationError,
        match="artifact 内容不一致",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "replayed artifact mismatch",
            engine="langgraph",
        )

    assert worker.calls == 1
    assert worker.write_calls == 1
    assert loop_plan.read_text(encoding="utf-8") == "# 人工篡改\n"
    events_after = _trace_event_counts(run_dir)
    for event in (
        "brief_finished",
        "worker_prompt_measured",
        "worker_started",
        "worker_finished",
    ):
        assert events_after[event] == events_before[event]


def test_p0_4b_terminal_state_only_repairs_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_terminal_state_before_checkpoint",
    )
    original_open = loop_graph_runtime.open_sqlite_checkpointer
    expected_states: list[object | None] = []

    @contextmanager
    def recording_open(*args, **kwargs):
        expected_states.append(kwargs.get("expected_trusted_state"))
        with original_open(*args, **kwargs) as checkpointer:
            yield checkpointer

    monkeypatch.setattr(
        loop_graph_runtime,
        "open_sqlite_checkpointer",
        recording_open,
    )
    before = _state(run_dir)
    assert before["status"] == "needs_human"
    assert worker.calls == 1

    runtime.recover_langgraph(
        run_dir.name,
        "P0-4b fault injection",
        engine="langgraph",
    )

    after = _state(run_dir)
    assert after["status"] == before["status"]
    assert after["current_step"] == before["current_step"]
    assert worker.calls == 1
    assert len(expected_states) == 2
    assert all(state is not None for state in expected_states)
    assert run_dir.joinpath("graph/graph-state.json").is_file()
    validate_checkpoint_manifest(run_dir)


def test_p0_4b_terminal_recovery_rejects_self_consistent_checkpoint_replacement_after_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "after_terminal_state_before_checkpoint",
    )
    replacement_run = tmp_path / "replacement" / run_dir.name
    shutil.copytree(run_dir / "graph", replacement_run / "graph")
    replacement_checkpoint = replacement_run / "graph" / "checkpoints.sqlite"
    with sqlite3.connect(replacement_checkpoint) as connection:
        connection.execute("PRAGMA user_version = 73")
    write_checkpoint_manifest(
        replacement_run,
        checkpoint_config(replacement_run.name),
    )
    replacement_trusted = capture_trusted_checkpoint_state(
        replacement_run
    )
    validate_checkpoint_manifest(replacement_run)

    original_capture = (
        loop_graph_runtime.capture_trusted_checkpoint_data_for_resume
    )
    original_open = loop_graph_runtime.open_sqlite_checkpointer
    capture_calls = 0
    open_calls = 0
    yielded_open_calls: list[int] = []

    def capture_then_replace(
        target_run_dir: Path,
        expected_state,
    ):
        nonlocal capture_calls
        captured = original_capture(target_run_dir, expected_state)
        capture_calls += 1
        if capture_calls == 2:
            target_graph = target_run_dir / "graph"
            replacement_graph = replacement_run / "graph"
            for candidate in target_graph.glob("checkpoints.sqlite*"):
                candidate.unlink()
            target_graph.joinpath("checkpoint-manifest.json").unlink()
            for candidate in replacement_graph.glob("checkpoints.sqlite*"):
                shutil.copy2(candidate, target_graph / candidate.name)
            shutil.copy2(
                replacement_graph / "checkpoint-manifest.json",
                target_graph / "checkpoint-manifest.json",
            )
            validate_checkpoint_manifest(target_run_dir)
            assert (
                replacement_trusted.files.checkpoint.sha256
                != expected_state.files.checkpoint.sha256
            )
        return captured

    @contextmanager
    def recording_open(*args, **kwargs):
        nonlocal open_calls
        open_calls += 1
        with original_open(*args, **kwargs) as checkpointer:
            yielded_open_calls.append(open_calls)
            yield checkpointer

    monkeypatch.setattr(
        loop_graph_runtime,
        "capture_trusted_checkpoint_data_for_resume",
        capture_then_replace,
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "open_sqlite_checkpointer",
        recording_open,
    )

    recovered = runtime.recover_langgraph(
        run_dir.name,
        "seal 后替换为另一份自洽 checkpoint",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert capture_calls == 2
    assert open_calls == 2
    assert yielded_open_calls == [1]
    assert worker.calls == 1
    assert worker.write_calls == 1
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    assert not run_dir.joinpath("graph/graph-state.json").exists()
    assert _trace_event_counts(run_dir)["graph_terminal_recovered"] == 0
    validate_checkpoint_manifest(run_dir)


class AbruptExitAt:
    def __init__(self, target: str, exit_code: int = 86) -> None:
        self.target = target
        self.exit_code = exit_code

    def __call__(self, point: str) -> None:
        if point == self.target:
            os._exit(self.exit_code)


def _run_abrupt_child(
    workspace: Path,
    repo: Path,
    persistent_counter: Path,
) -> None:
    runtime = _build_runtime(
        workspace,
        EvidenceWorker(persistent_counter),
        AbruptExitAt("after_step_result_before_state"),
    )
    runtime.start(
        BriefInput(
            mode="bug",
            text="Gate 3 abrupt crash fixture",
            source="subprocess-test",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=True,
        engine="langgraph",
    )
    raise AssertionError("abrupt crash fault 未触发")


def test_abrupt_process_exit_with_unsealed_checkpoint_stops_without_replay(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    persistent_counter = tmp_path / "worker-start-count.txt"
    _init_repo(repo)
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(repo_root / "src"),
            *([existing_pythonpath] if existing_pythonpath else []),
        ]
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_abrupt_child",
            str(workspace),
            str(repo),
            str(persistent_counter),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 86, (
        completed.stdout + "\n" + completed.stderr
    )
    run_dir = next(
        path
        for path in workspace.joinpath("runs").iterdir()
        if path.name.endswith("-loop")
    )
    assert persistent_counter.read_text(encoding="utf-8") == "1"
    assert run_dir.joinpath("graph/checkpoints.sqlite").is_file()
    pending_path = run_dir / "graph" / "checkpoint-pending.json"
    assert pending_path.is_file()
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    step_result = json.loads(
        run_dir.joinpath(
            "step-results/worker-iteration-01.json"
        ).read_text(encoding="utf-8")
    )
    assert pending["run_id"] == run_dir.name
    assert pending["step_id"] == step_result["step_id"]
    assert pending["attempt_id"] == step_result["attempt_id"]
    assert pending["step_result_id"] == step_result["step_result_id"]

    pending_only_run = tmp_path / "pending-only-run"
    pending_only_graph = pending_only_run / "graph"
    pending_only_graph.mkdir(parents=True)
    pending_only = dict(pending)
    pending_only["run_id"] = pending_only_run.name
    pending_only_graph.joinpath("checkpoint-pending.json").write_text(
        json.dumps(pending_only, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(
        GraphCheckpointValidationError,
        match="checkpoint manifest 未完成本次 Graph 提交：存在 pending marker",
    ):
        validate_checkpoint_manifest(pending_only_run)

    with pytest.raises(GraphCheckpointValidationError) as exc_info:
        validate_checkpoint_manifest(run_dir)
    validation_error = str(exc_info.value)
    assert (
        validation_error
        == "checkpoint manifest 未完成本次 Graph 提交：存在 pending marker"
    )
    graph_before_recovery = _graph_artifact_hashes(run_dir)

    recovery_worker = EvidenceWorker(persistent_counter)
    runtime = _build_runtime(
        workspace,
        recovery_worker,
        CrashOnce("__never__"),
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "真实子进程 abrupt exit 恢复",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert recovery_worker.calls == 0
    assert persistent_counter.read_text(encoding="utf-8") == "1"
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    assert _graph_artifact_hashes(run_dir) == graph_before_recovery
    report = run_dir.joinpath("graph-recovery-report.md").read_text(
        encoding="utf-8"
    )
    assert "checkpoint_validation_failed" in report
    assert "未调用恢复用可写 SQLite checkpointer" in report
    assert validation_error in report
    assert "不得删除、移动或回放" in report
    assert _trace_event_counts(run_dir)["graph_checkpoint_validation_failed"] == 1
    lease_files = list(
        workspace.joinpath(
            ".tmp",
            "vega",
            "graph-operation-leases",
        ).glob("*.json")
    )
    assert len(lease_files) == 1
    lease = json.loads(lease_files[0].read_text(encoding="utf-8"))
    assert lease["status"] == "released"
    assert lease["operation"] == "recover"
    assert lease["supersedes_lease_id"]


def test_recovery_with_unbound_sqlite_sidecar_stops_without_opening_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    journal = run_dir / "graph" / "checkpoints.sqlite-journal"
    journal.write_bytes(b"unbound rollback journal")
    graph_before_recovery = _graph_artifact_hashes(run_dir)

    def reject_writable_checkpointer(*_args, **_kwargs):
        raise AssertionError("checkpoint 校验失败后不得打开恢复用可写 checkpointer")

    monkeypatch.setattr(
        loop_graph_runtime,
        "open_sqlite_checkpointer",
        reject_writable_checkpointer,
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "检测到未绑定 SQLite 事务侧文件",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert worker.calls == 0
    assert worker.write_calls == 0
    assert _graph_artifact_hashes(run_dir) == graph_before_recovery
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    report = run_dir.joinpath("graph-recovery-report.md").read_text(
        encoding="utf-8"
    )
    assert "checkpoint_validation_failed" in report
    assert "SQLite 事务侧文件未被 manifest 绑定" in report


def _publish_terminal_checkpoint_fixture(
    run_dir: Path,
    terminal_status: str,
) -> tuple[str, str, dict[str, str]]:
    state = _state(run_dir)
    state.update(
        {
            "status": terminal_status,
            "current_step": "done",
            "artifacts": list(
                dict.fromkeys(
                    [
                        *state["artifacts"],
                        "final-report.md",
                        "eval.md",
                    ]
                )
            ),
        }
    )
    run_dir.joinpath("state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    TraceWriter(run_dir / "trace.jsonl").write(
        "run_finished",
        status=terminal_status,
        current_step="done",
    )
    old_final = f"# Final Report\n\n- 旧终态：`{terminal_status}`\n"
    old_finish = f"# Finish Report\n\n- 旧终态：`{terminal_status}`\n"
    run_dir.joinpath("final-report.md").write_text(
        old_final,
        encoding="utf-8",
        newline="\n",
    )
    run_dir.joinpath("eval.md").write_text(
        "PASS: 旧终态\n",
        encoding="utf-8",
        newline="\n",
    )
    run_dir.joinpath("finish-report.md").write_text(
        old_finish,
        encoding="utf-8",
        newline="\n",
    )
    run_dir.joinpath("finish-summary.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "loop_status": terminal_status,
                "finish_status": (
                    "ready_to_commit"
                    if terminal_status == "success"
                    else "needs_human"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    run_dir.joinpath(
        "graph",
        "checkpoints.sqlite-journal",
    ).write_bytes(b"unbound terminal rollback journal")
    return old_final, old_finish, _graph_artifact_hashes(run_dir)


@pytest.mark.parametrize("terminal_status", ["success", "failed"])
def test_checkpoint_drift_after_initial_validation_revokes_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    _publish_terminal_checkpoint_fixture(run_dir, terminal_status)
    capture = loop_graph_runtime.capture_checkpoint_data_snapshot
    captures = 0

    def capture_with_drift(target_run_dir: Path):
        nonlocal captures
        snapshot = capture(target_run_dir)
        captures += 1
        if captures != 2:
            return snapshot
        return replace(
            snapshot,
            checkpoint=replace(
                snapshot.checkpoint,
                sha256="e" * 64,
            ),
        )

    monkeypatch.setattr(
        loop_graph_runtime,
        "capture_checkpoint_data_snapshot",
        capture_with_drift,
    )

    recovered = runtime.recover_langgraph(
        run_dir.name,
        "初始 manifest 后 checkpoint 漂移",
        engine="langgraph",
    )

    assert recovered == run_dir
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    assert state["eval_results"][-1].startswith(
        "FAIL: LangGraph checkpoint 终态证据不可信"
    )
    assert "原终态已撤销" in run_dir.joinpath(
        "final-report.md"
    ).read_text(encoding="utf-8")
    assert _trace_event_counts(run_dir)["run_terminal_state_revoked"] == 1
    assert _trace_event_counts(run_dir)["run_terminal_revoked"] == 1
    assert worker.calls == 0
    assert worker.write_calls == 0


@pytest.mark.parametrize("terminal_status", ["success", "failed"])
def test_checkpoint_validation_failure_revokes_published_terminal_state(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    old_final, old_finish, graph_before = (
        _publish_terminal_checkpoint_fixture(
            run_dir,
            terminal_status,
        )
    )
    if terminal_status == "success":
        with pytest.raises(ValueError, match="checkpoint 不可信"):
            run_status_payload(
                tmp_path / "workspace",
                run_dir.name,
            )

    recovered = runtime.recover_langgraph(
        run_dir.name,
        f"撤销不可信 {terminal_status} 终态",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert worker.calls == 0
    assert worker.write_calls == 0
    assert _graph_artifact_hashes(run_dir) == graph_before
    revoked = _state(run_dir)
    assert revoked["status"] == "needs_human"
    assert revoked["current_step"] == "graph_recovery_needs_human"
    assert revoked["eval_results"][-1].startswith(
        "FAIL: LangGraph checkpoint 终态证据不可信"
    )
    assert not any(
        item.startswith("FAIL: trace")
        for item in revoked["eval_results"]
    )
    for artifact in (
        "final-report.before-checkpoint-revocation.md",
        "finish-report.before-checkpoint-revocation.md",
        "finish-summary.before-checkpoint-revocation.json",
    ):
        assert artifact in revoked["artifacts"]
        assert run_dir.joinpath(artifact).is_file()
    assert (
        run_dir.joinpath(
            "final-report.before-checkpoint-revocation.md"
        ).read_text(encoding="utf-8")
        == old_final
    )
    assert (
        run_dir.joinpath(
            "finish-report.before-checkpoint-revocation.md"
        ).read_text(encoding="utf-8")
        == old_finish
    )
    final_report = run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    )
    assert "原终态已撤销" in final_report
    assert f"原业务状态：`{terminal_status}`" in final_report
    finish_summary = json.loads(
        run_dir.joinpath("finish-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert finish_summary["invalidated"] is True
    assert finish_summary["loop_status"] == "needs_human"
    assert finish_summary["finish_status"] == "needs_human"
    assert finish_summary["previous_loop_status"] == terminal_status
    trace = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert trace[-3]["event"] == "graph_checkpoint_validation_failed"
    assert trace[-3]["original_status"] == terminal_status
    assert trace[-2]["event"] == "run_terminal_state_revoked"
    assert trace[-2]["reason"] == "checkpoint_validation_failed"
    assert trace[-2]["previous_status"] == terminal_status
    assert trace[-2]["status"] == "needs_human"
    assert trace[-1]["event"] == "run_terminal_revoked"
    assert trace[-1]["reason"] == "checkpoint_validation_failed"
    assert trace[-1]["previous_status"] == terminal_status
    assert trace[-1]["status"] == "needs_human"


def test_checkpoint_diagnostic_write_failure_revokes_state_before_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    old_final, _, _ = _publish_terminal_checkpoint_fixture(
        run_dir,
        "success",
    )
    original_write = loop_graph_runtime.write_graph_recovery_report

    def fail_diagnostic_write(*_args, **_kwargs) -> None:
        revoked = _state(run_dir)
        assert revoked["status"] == "needs_human"
        assert revoked["current_step"] == "graph_recovery_needs_human"
        raise OSError("diagnostic write crash")

    monkeypatch.setattr(
        loop_graph_runtime,
        "write_graph_recovery_report",
        fail_diagnostic_write,
    )

    with pytest.raises(OSError, match="diagnostic write crash"):
        runtime.recover_langgraph(
            run_dir.name,
            "诊断写入前必须先撤销终态",
            engine="langgraph",
        )

    assert _state(run_dir)["status"] == "needs_human"
    assert run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    ) == old_final
    assert _trace_event_counts(run_dir)["run_terminal_state_revoked"] == 0
    assert _trace_event_counts(run_dir)["run_terminal_revoked"] == 0
    assert worker.calls == 0
    assert worker.write_calls == 0

    monkeypatch.setattr(
        loop_graph_runtime,
        "write_graph_recovery_report",
        original_write,
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "诊断恢复后补齐撤销事务",
        engine="langgraph",
    )

    assert recovered == run_dir
    counts = _trace_event_counts(run_dir)
    assert counts["graph_checkpoint_validation_failed"] == 1
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert _state(run_dir)["status"] == "needs_human"


def test_checkpoint_revocation_crash_after_eval_retries_report_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    old_final, old_finish, _ = _publish_terminal_checkpoint_fixture(
        run_dir,
        "success",
    )
    original_invalidate = (
        loop_graph_runtime._invalidate_checkpoint_delivery_reports
    )

    def crash_before_reports(*_args, **_kwargs):
        state = _state(run_dir)
        assert state["status"] == "needs_human"
        assert state["eval_results"][-1].startswith(
            "FAIL: LangGraph checkpoint 终态证据不可信"
        )
        assert (
            _trace_event_counts(run_dir)["run_terminal_state_revoked"]
            == 1
        )
        assert _trace_event_counts(run_dir)["run_terminal_revoked"] == 0
        raise GraphExecutionInterrupted(
            "crash after eval before delivery reports"
        )

    monkeypatch.setattr(
        loop_graph_runtime,
        "_invalidate_checkpoint_delivery_reports",
        crash_before_reports,
    )

    with pytest.raises(
        GraphExecutionInterrupted,
        match="after eval before delivery reports",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "eval 已失效但报告尚未失效",
            engine="langgraph",
        )

    assert run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    ) == old_final
    assert run_dir.joinpath("finish-report.md").read_text(
        encoding="utf-8"
    ) == old_finish
    assert _trace_event_counts(run_dir)["run_terminal_state_revoked"] == 1
    assert _trace_event_counts(run_dir)["run_terminal_revoked"] == 0

    monkeypatch.setattr(
        loop_graph_runtime,
        "_invalidate_checkpoint_delivery_reports",
        original_invalidate,
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "重试补齐交付报告失效",
        engine="langgraph",
    )

    assert recovered == run_dir
    counts = _trace_event_counts(run_dir)
    assert counts["graph_checkpoint_validation_failed"] == 1
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert "原终态已撤销" in run_dir.joinpath(
        "final-report.md"
    ).read_text(encoding="utf-8")
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_checkpoint_revocation_crash_after_state_revoked_event_completes_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    old_final, _, _ = _publish_terminal_checkpoint_fixture(
        run_dir,
        "success",
    )
    original_append = loop_graph_runtime.append_graph_recovery_trace

    def crash_after_state_revocation(
        target_run_dir: Path,
        event: str,
        **payload: object,
    ) -> None:
        original_append(target_run_dir, event, **payload)
        if event == "run_terminal_state_revoked":
            raise GraphExecutionInterrupted(
                "crash after terminal state revocation fact"
            )

    monkeypatch.setattr(
        loop_graph_runtime,
        "append_graph_recovery_trace",
        crash_after_state_revocation,
    )

    with pytest.raises(
        GraphExecutionInterrupted,
        match="after terminal state revocation fact",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "撤销事实写后崩溃",
            engine="langgraph",
        )

    assert _state(run_dir)["status"] == "needs_human"
    assert run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    ) == old_final
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 0

    monkeypatch.setattr(
        loop_graph_runtime,
        "append_graph_recovery_trace",
        original_append,
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "撤销事实写后重试",
        engine="langgraph",
    )

    assert recovered == run_dir
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert "原终态已撤销" in run_dir.joinpath(
        "final-report.md"
    ).read_text(encoding="utf-8")
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_checkpoint_revocation_atomic_report_write_preserves_previous_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    old_final, _, _ = _publish_terminal_checkpoint_fixture(
        run_dir,
        "success",
    )
    real_replace = os.replace

    def fail_final_report_replace(source, destination) -> None:
        if Path(destination).name == "final-report.md":
            raise PermissionError("final report atomic replace blocked")
        real_replace(source, destination)

    monkeypatch.setattr(
        "vega.redaction.os.replace",
        fail_final_report_replace,
    )
    monkeypatch.setattr("vega.redaction.time.sleep", lambda _: None)

    with pytest.raises(
        PermissionError,
        match="final report atomic replace blocked",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "final report 原子发布失败",
            engine="langgraph",
        )

    assert run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    ) == old_final
    assert run_dir.joinpath(
        "final-report.before-checkpoint-revocation.md"
    ).read_text(encoding="utf-8") == old_final
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 0

    monkeypatch.setattr("vega.redaction.os.replace", real_replace)
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "final report 原子发布重试",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert "原终态已撤销" in run_dir.joinpath(
        "final-report.md"
    ).read_text(encoding="utf-8")
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_checkpoint_revocation_atomic_archive_publish_remains_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    old_final, _, _ = _publish_terminal_checkpoint_fixture(
        run_dir,
        "success",
    )
    original_create_once = (
        loop_graph_runtime.write_redacted_text_create_once_atomic
    )

    def fail_archive_publish(path: Path, text: str) -> None:
        if path.name == "final-report.before-checkpoint-revocation.md":
            raise PermissionError("archive publish blocked")
        original_create_once(path, text)

    monkeypatch.setattr(
        loop_graph_runtime,
        "write_redacted_text_create_once_atomic",
        fail_archive_publish,
    )

    with pytest.raises(
        GraphRecoveryValidationError,
        match="无法原子归档旧交付报告",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "交付归档原子发布失败",
            engine="langgraph",
        )

    assert run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    ) == old_final
    assert not run_dir.joinpath(
        "final-report.before-checkpoint-revocation.md"
    ).exists()
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 0

    monkeypatch.setattr(
        loop_graph_runtime,
        "write_redacted_text_create_once_atomic",
        original_create_once,
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "交付归档原子发布重试",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert run_dir.joinpath(
        "final-report.before-checkpoint-revocation.md"
    ).read_text(encoding="utf-8") == old_final
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_checkpoint_revocation_crash_after_completion_write_is_not_duplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    _publish_terminal_checkpoint_fixture(run_dir, "success")
    original_append = loop_graph_runtime.append_graph_recovery_trace

    def crash_after_completion_write(
        target_run_dir: Path,
        event: str,
        **payload: object,
    ) -> None:
        original_append(target_run_dir, event, **payload)
        if event == "run_terminal_revoked":
            raise GraphExecutionInterrupted(
                "crash after terminal revocation completion write"
            )

    monkeypatch.setattr(
        loop_graph_runtime,
        "append_graph_recovery_trace",
        crash_after_completion_write,
    )

    with pytest.raises(
        GraphExecutionInterrupted,
        match="after terminal revocation completion write",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            "完成事件写后崩溃",
            engine="langgraph",
        )

    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert "原终态已撤销" in run_dir.joinpath(
        "final-report.md"
    ).read_text(encoding="utf-8")
    assert json.loads(
        run_dir.joinpath("finish-summary.json").read_text(
            encoding="utf-8"
        )
    )["invalidated"] is True

    monkeypatch.setattr(
        loop_graph_runtime,
        "append_graph_recovery_trace",
        original_append,
    )
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "完成事件写后重试",
        engine="langgraph",
    )

    assert recovered == run_dir
    counts = _trace_event_counts(run_dir)
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    state = _state(run_dir)
    assert not any(
        item.startswith("FAIL: trace")
        for item in state["eval_results"]
    )
    assert worker.calls == 0
    assert worker.write_calls == 0


@pytest.mark.parametrize(
    "conflicting_archive",
    [
        "final-report.before-checkpoint-revocation.md",
        "finish-report.before-checkpoint-revocation.md",
        "finish-summary.before-checkpoint-revocation.json",
    ],
)
def test_checkpoint_revocation_archive_conflict_keeps_needs_human_terminal(
    tmp_path: Path,
    conflicting_archive: str,
) -> None:
    runtime, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    _publish_terminal_checkpoint_fixture(run_dir, "success")
    conflicting_path = run_dir / conflicting_archive
    conflicting_content = "conflicting historical archive\n"
    conflicting_path.write_text(
        conflicting_content,
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        GraphRecoveryValidationError,
        match="交付报告归档已存在且内容不同",
    ):
        runtime.recover_langgraph(
            run_dir.name,
            f"归档冲突：{conflicting_archive}",
            engine="langgraph",
        )

    revoked = _state(run_dir)
    assert revoked["status"] == "needs_human"
    assert revoked["current_step"] == "graph_recovery_needs_human"
    assert revoked["eval_results"][-1].startswith(
        "FAIL: LangGraph checkpoint 终态证据不可信"
    )
    assert conflicting_path.read_text(
        encoding="utf-8"
    ) == conflicting_content
    trace_before_retry = run_dir.joinpath("trace.jsonl").read_text(
        encoding="utf-8"
    )
    trace = [
        json.loads(line)
        for line in trace_before_retry.splitlines()
        if line.strip()
    ]
    assert trace[-2]["event"] == "graph_checkpoint_validation_failed"
    assert trace[-1]["event"] == "run_terminal_state_revoked"
    assert trace[-1]["reason"] == "checkpoint_validation_failed"
    assert not any(
        item["event"] == "run_terminal_revoked"
        for item in trace
    )
    assert worker.calls == 0
    assert worker.write_calls == 0

    conflicting_path.unlink()
    recovered = runtime.recover_langgraph(
        run_dir.name,
        "解除归档冲突后补齐撤销事务",
        engine="langgraph",
    )

    assert recovered == run_dir
    assert _state(run_dir)["status"] == "needs_human"
    counts = _trace_event_counts(run_dir)
    assert counts["graph_checkpoint_validation_failed"] == 1
    assert counts["run_terminal_state_revoked"] == 1
    assert counts["run_terminal_revoked"] == 1
    assert conflicting_path.is_file()
    trace_after_completion = run_dir.joinpath("trace.jsonl").read_text(
        encoding="utf-8"
    )

    recovered_again = runtime.recover_langgraph(
        run_dir.name,
        "完成后重复恢复不得追加第二个完成事件",
        engine="langgraph",
    )

    assert recovered_again == run_dir
    assert (
        run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8")
        == trace_after_completion
    )
    assert worker.calls == 0
    assert worker.write_calls == 0


def test_recovery_rejects_timeout_identity_drift(tmp_path: Path) -> None:
    _, worker, _, run_dir = _start_until_crash(
        tmp_path,
        "before_external_execution",
    )
    mismatched_runtime = _build_runtime(
        tmp_path / "workspace",
        worker,
        CrashOnce("__never__"),
    )
    mismatched_runtime.timeout_seconds = 901

    with pytest.raises(ValueError, match="timeout_seconds 已固定为 900"):
        mismatched_runtime.recover_langgraph(
            run_dir.name,
            "timeout identity drift",
            engine="langgraph",
        )

    assert worker.calls == 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "_abrupt_child":
        _run_abrupt_child(
            Path(sys.argv[2]),
            Path(sys.argv[3]),
            Path(sys.argv[4]),
        )
    else:
        raise SystemExit("仅支持 _abrupt_child 测试入口")
