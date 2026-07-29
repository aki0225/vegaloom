from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.loop_runtime as loop_runtime_module
import vega.recovery_runtime as recovery_runtime_module
from vega.cli import app
from vega.execution_control import ExecutionLease, is_process_alive
from vega.loop_evidence import validate_loop_artifact_integrity
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput, LoopAutomationState, LoopIterationState
from vega.recovery_runtime import RecoveryRuntime
from vega.run_lock import RunMutationLock
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.trace import TraceWriter


OWNER_SCRIPT = textwrap.dedent(
    r"""
    import sys
    from pathlib import Path

    from vega.execution_control import run_owned_process
    from vega.loop_runtime import LoopAutomationRuntime
    from vega.models import BriefInput
    from vega.runner import RunnerResult

    CHILD_SCRIPT = '''
    import sys
    import time
    from pathlib import Path

    readme = Path(sys.argv[1])
    ready = Path(sys.argv[2])
    release = Path(sys.argv[3])
    readme.write_text(
        readme.read_text(encoding="utf-8").rstrip() + "\\npartial worker change\\n",
        encoding="utf-8",
        newline="\\n",
    )
    ready.write_text("ready\\n", encoding="utf-8", newline="\\n")
    while not release.exists():
        time.sleep(0.05)
    '''

    class BlockingWorker:
        def __init__(self, ready: Path, release: Path) -> None:
            self.ready = ready
            self.release = release

        def run(
            self,
            prompt,
            repo_path,
            *,
            sandbox,
            timeout_seconds,
            execution_context=None,
        ):
            if execution_context is None:
                raise RuntimeError("missing execution context")
            result = run_owned_process(
                [
                    sys.executable,
                    "-c",
                    CHILD_SCRIPT,
                    str(repo_path / "README.md"),
                    str(self.ready),
                    str(self.release),
                ],
                "",
                repo_path,
                timeout_seconds,
                execution_context,
            )
            return RunnerResult(
                status=result.status,
                output=result.output,
                error=result.error,
                command=["recovery-chaos-child"],
            )

    workspace = Path(sys.argv[1])
    repo = Path(sys.argv[2])
    ready = Path(sys.argv[3])
    release = Path(sys.argv[4])
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=BlockingWorker(ready, release),
        timeout_seconds=60,
    )
    runtime.start(
        BriefInput(
            mode="bug",
            text="保留 owner crash 后的部分 README 修改",
            source="recovery-chaos",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )
    """
)


class StaticReviewer:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, repo_path, sandbox, timeout_seconds, execution_context
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "恢复后的独立 iteration 证据完整。",
                    "findings": [],
                    "checked_items": ["scope", "tests", "recovery evidence"],
                },
                ensure_ascii=False,
            ),
            command=["static-reviewer"],
        )


class TrackedChangeWorker:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds, execution_context
        repo_path.joinpath("README.md").write_text(
            "# recovery demo\ncompleted worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker completed",
            command=["tracked-change-worker"],
        )


def test_owner_crash_recovery_preserves_evidence_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace with spaces 空格"
    repo = tmp_path / "repo with spaces 空格"
    _init_git_repo(repo, with_verification=True)
    ready = workspace / "control" / "child-ready"
    release = workspace / "control" / "child-release"
    ready.parent.mkdir(parents=True)

    environment = os.environ.copy()
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in [src_path, environment.get("PYTHONPATH", "")] if item
    )
    owner = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-c",
            OWNER_SCRIPT,
            str(workspace),
            str(repo),
            str(ready),
            str(release),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    child_pid: int | None = None
    try:
        run_dir = _wait_for_loop_run(workspace, owner)
        _wait_until(lambda: ready.exists(), owner=owner, description="child ready marker")
        execution_path = run_dir / "iterations" / "01" / "executions" / "worker" / "execution.json"
        lease = ExecutionLease.model_validate_json(execution_path.read_text(encoding="utf-8"))
        child_pid = lease.child_pid
        assert child_pid is not None
        assert is_process_alive(owner.pid)
        assert is_process_alive(child_pid)
        assert "partial worker change" in repo.joinpath("README.md").read_text(encoding="utf-8")
        state_before_busy_recover = run_dir.joinpath("state.json").read_bytes()
        trace_before_busy_recover = run_dir.joinpath("trace.jsonl").read_bytes()
        monkeypatch.chdir(workspace)
        owner_alive_recover = CliRunner().invoke(
            app,
            ["recover", "--run", run_dir.name, "--reason", "owner still alive"],
        )
        assert owner_alive_recover.exit_code != 0
        lock_owner = _read_json(
            run_dir / ".control" / "run-mutation-owner.json"
        )
        assert lock_owner["operation"] == "loop.start"
        assert lock_owner["owner_pid"] == owner.pid
        assert run_dir.joinpath("state.json").read_bytes() == state_before_busy_recover
        assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before_busy_recover

        owner.kill()
        owner.wait(timeout=10)
        assert is_process_alive(child_pid)
        crashed_execution = execution_path.read_bytes()
        with RunMutationLock.acquire(run_dir, "loop.finish"):
            assert is_process_alive(child_pid)

        blocked = CliRunner().invoke(
            app,
            ["recover", "--run", run_dir.name, "--reason", "owner crash child alive"],
        )
        assert blocked.exit_code != 0
        blocked_state = _read_json(run_dir / "state.json")
        assert blocked_state["status"] == "running"
        assert execution_path.read_bytes() == crashed_execution

        release.write_text("release\n", encoding="utf-8")
        _wait_until(
            lambda: not is_process_alive(child_pid),
            description="owned child exit",
        )
        recovered = CliRunner().invoke(
            app,
            ["recover", "--run", run_dir.name, "--reason", "owner crash child gone"],
        )
        assert recovered.exit_code == 0, recovered.output

        recovered_state = _read_json(run_dir / "state.json")
        assert recovered_state["status"] == "needs_human"
        assert recovered_state["current_step"] == "recovered"
        assert recovered_state["current_iteration"] == 1
        assert len(recovered_state["iterations"]) == 1
        assert recovered_state["iterations"][0]["lifecycle"] == "interrupted"
        assert recovered_state["iterations"][0]["interrupted_step"] == "worker"
        assert recovered_state["iterations"][0]["interrupted_at"]
        assert (run_dir / "iterations" / "01" / "interruption-report.md").is_file()
        assert "loop_iteration_interrupted" in run_dir.joinpath("trace.jsonl").read_text(
            encoding="utf-8"
        )
        assert "iterations/01/interruption-report.md" in recovered_state["artifacts"]
        assert execution_path.read_bytes() == crashed_execution

        resumed = LoopAutomationRuntime(
            workspace,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
        )
        final_state = LoopAutomationState.model_validate_json(
            resumed.joinpath("state.json").read_text(encoding="utf-8")
        )
        assert final_state.status == "success"
        assert final_state.current_iteration == 2
        assert [item.iteration for item in final_state.iterations] == [1, 2]
        assert final_state.iterations[0].lifecycle == "interrupted"
        assert final_state.iterations[1].lifecycle == "completed"
        assert execution_path.read_bytes() == crashed_execution
        assert (run_dir / "iterations" / "02" / "review-verdict.json").is_file()

        integrity = validate_loop_artifact_integrity(workspace, repo, run_dir)
        assert integrity.valid, integrity.issues
        eval_results = run_loop_eval(run_dir, final_state.artifacts)
        assert not [item for item in eval_results if item.startswith("FAIL:")]
        assert "recovery-report.md" in final_state.artifacts
        assert "iterations/01/interruption-report.md" in final_state.artifacts

        interruption_report = run_dir / "iterations" / "01" / "interruption-report.md"
        interruption_report.write_text(
            interruption_report.read_text(encoding="utf-8").replace(
                "- 原步骤：`worker`",
                "- 原步骤：`reviewer`",
            ),
            encoding="utf-8",
        )
        tampered_integrity = validate_loop_artifact_integrity(workspace, repo, run_dir)
        assert not tampered_integrity.valid
        assert (
            "iteration_01_interruption_report_step_mismatch"
            in tampered_integrity.issues
        )
    finally:
        release.parent.mkdir(parents=True, exist_ok=True)
        release.touch()
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
        if child_pid is not None and is_process_alive(child_pid):
            _terminate_test_process(child_pid)


def test_recovery_supersedes_uncommitted_terminal_trace_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    original_write = TraceWriter.write
    crashed = False

    def crash_after_loop_terminal(
        writer: TraceWriter,
        event: str,
        **payload: object,
    ) -> None:
        nonlocal crashed
        original_write(writer, event, **payload)
        if (
            not crashed
            and event == "run_finished"
            and writer.trace_path.parent.name.endswith("-loop")
        ):
            crashed = True
            raise RuntimeError("simulated crash after loop terminal trace")

    monkeypatch.setattr(TraceWriter, "write", crash_after_loop_terminal)
    with pytest.raises(RuntimeError, match="simulated crash"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).start(
            BriefInput(
                mode="bug",
                text="验证终态 trace 崩溃恢复",
                source="recovery-terminal-window",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=1,
            verify=True,
        )

    run_dir = next((workspace / "runs").glob("*-loop"))
    crashed_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert crashed_state.status == "running"
    assert crashed_state.current_iteration == 1
    assert [
        item
        for item in _read_jsonl(run_dir / "trace.jsonl")
        if item.get("event") == "run_finished"
    ]

    monkeypatch.setattr(TraceWriter, "write", original_write)
    monkeypatch.setattr(
        recovery_runtime_module,
        "inspect_execution_for_recovery",
        lambda _run_dir: recovery_runtime_module.ExecutionRecoveryInspection(
            True,
            "测试已模拟原 owner 崩溃退出。",
        ),
    )
    pending_path = (
        run_dir / ".control" / "recovery-transaction.json"
    )
    original_render = recovery_runtime_module.render_recovery_report
    render_crashed = False

    def crash_before_recovery_state(**kwargs: object) -> str:
        nonlocal render_crashed
        if not render_crashed:
            render_crashed = True
            raise RuntimeError("simulated crash before recovery state commit")
        return original_render(**kwargs)

    monkeypatch.setattr(
        recovery_runtime_module,
        "render_recovery_report",
        crash_before_recovery_state,
    )
    with pytest.raises(RuntimeError, match="recovery state commit"):
        RecoveryRuntime(workspace).recover_loop(
            run_dir.name,
            "owner crashed after terminal trace",
        )
    assert pending_path.is_file()
    pre_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert pre_state.status == "running"
    pending_bytes = pending_path.read_bytes()
    tampered_pending = json.loads(pending_bytes)
    tampered_pending["interrupted_iteration"] = 99
    pending_path.write_text(
        json.dumps(tampered_pending, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="interrupted_iteration"):
        RecoveryRuntime(workspace).recover_loop(
            run_dir.name,
            "tampered pending transaction must fail closed",
        )
    pending_path.write_bytes(pending_bytes)

    monkeypatch.setattr(
        recovery_runtime_module,
        "render_recovery_report",
        original_render,
    )
    original_ensure = recovery_runtime_module._ensure_recovery_trace_events
    recovery_crashed = False

    def crash_before_recovery_trace(
        recovery_run_dir: Path,
        transaction: recovery_runtime_module.RecoveryTransaction,
    ) -> None:
        nonlocal recovery_crashed
        if not recovery_crashed:
            recovery_crashed = True
            raise RuntimeError("simulated crash before recovery trace commit")
        original_ensure(recovery_run_dir, transaction)

    monkeypatch.setattr(
        recovery_runtime_module,
        "_ensure_recovery_trace_events",
        crash_before_recovery_trace,
    )
    with pytest.raises(RuntimeError, match="recovery trace commit"):
        RecoveryRuntime(workspace).recover_loop(
            run_dir.name,
            "retry pending recovery before state commit",
        )
    assert pending_path.is_file()
    pending_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert pending_state.status == "needs_human"
    committed_pending_bytes = pending_path.read_bytes()
    tampered_committed = json.loads(committed_pending_bytes)
    tampered_committed["interrupted_iteration"] = None
    pending_path.write_text(
        json.dumps(tampered_committed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="interrupted_iteration"):
        RecoveryRuntime(workspace).recover_loop(
            run_dir.name,
            "tampered committed transaction must fail closed",
        )
    pending_path.write_bytes(committed_pending_bytes)
    with pytest.raises(ValueError, match="transaction 尚未提交"):
        LoopAutomationRuntime(
            workspace,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not run_dir.joinpath("iterations", "02").exists()

    monkeypatch.setattr(
        recovery_runtime_module,
        "_ensure_recovery_trace_events",
        original_ensure,
    )
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "retry pending recovery transaction",
    )
    assert not pending_path.exists()
    resumed = LoopAutomationRuntime(
        workspace,
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
    )

    final_state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    events = _read_jsonl(run_dir / "trace.jsonl")
    terminal_indices = [
        index for index, item in enumerate(events) if item.get("event") == "run_finished"
    ]
    superseded = [
        item for item in events if item.get("event") == "run_terminal_superseded"
    ]
    assert final_state.status == "success"
    assert [item.lifecycle for item in final_state.iterations] == [
        "interrupted",
        "completed",
    ]
    assert len(terminal_indices) == 2
    assert len(superseded) == 1
    assert superseded[0]["terminal_event_index"] == terminal_indices[0]
    assert superseded[0]["terminal_status"] == "success"
    assert len(final_state.superseded_terminal_events) == 1
    superseded_record = final_state.superseded_terminal_events[0]
    assert superseded_record.terminal_event_index == terminal_indices[0]
    assert superseded_record.terminal_status == "success"
    assert superseded[0]["recovery_id"] == superseded_record.recovery_id
    recovered_events = [
        item
        for item in events
        if item.get("event") == "loop_recovered"
        and item.get("recovery_id") == superseded_record.recovery_id
    ]
    assert len(recovered_events) == 1
    assert recovered_events[0]["superseded_terminal_event"] == terminal_indices[0]
    assert events[-1]["event"] == "run_finished"
    assert not [
        item
        for item in run_loop_eval(run_dir, final_state.artifacts)
        if item.startswith("FAIL:")
    ]


def test_recovery_rejects_semantically_corrupt_iteration_gap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    run_dir = tmp_path / "runs" / "corrupt-iteration-loop"
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(repo),
        input_source="inline-text",
        status="running",
        current_step="worker",
        current_iteration=2,
    )
    state.save(run_dir / "state.json")
    original_state = run_dir.joinpath("state.json").read_bytes()

    with pytest.raises(ValueError, match="iteration"):
        RecoveryRuntime(tmp_path).recover_loop(
            run_dir.name,
            "iteration gap must fail closed",
        )

    assert run_dir.joinpath("state.json").read_bytes() == original_state
    assert "loop_recovery_blocked" in run_dir.joinpath("trace.jsonl").read_text(
        encoding="utf-8"
    )
    assert "自动 recovery 已停止" in run_dir.joinpath(
        "recovery-report.md"
    ).read_text(encoding="utf-8")


def test_recovery_marks_existing_current_iteration_without_duplicate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "existing-iteration-loop"
    iteration_dir = run_dir / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(repo),
        input_source="inline-text",
        status="running",
        current_step="reviewer",
        current_iteration=1,
        iterations=[
            LoopIterationState(
                iteration=1,
                worker_status="success",
                reviewer_status="success",
                verdict="approve",
            )
        ],
    )
    state.save(run_dir / "state.json")

    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "owner exited before terminal state",
    )

    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert len(recovered_state.iterations) == 1
    assert recovered_state.iterations[0].lifecycle == "interrupted"
    assert recovered_state.iterations[0].interrupted_step == "reviewer"


def test_recovery_before_first_iteration_does_not_claim_interruption_report(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "pre-iteration-loop"
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(repo),
        input_source="inline-text",
        status="running",
        current_step="brief",
        current_iteration=0,
    )
    state.save(run_dir / "state.json")

    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "owner exited before iteration start",
    )

    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert recovered_state.status == "needs_human"
    assert recovered_state.current_step == "recovered_initialization_incomplete"
    assert recovered_state.iterations == []
    assert not list(run_dir.glob("iterations/*/interruption-report.md"))
    next_steps = run_status_payload(workspace, run_dir.name)["next_steps"]
    assert not any("interruption-report.md" in item for item in next_steps)
    assert not any("loop continue" in item for item in next_steps)
    with pytest.raises(ValueError, match="初始化未完成"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )


def test_recovery_transaction_temp_path_preserves_windows_path_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedUuid:
        hex = "f" * 32

    monkeypatch.setattr(recovery_runtime_module, "uuid4", lambda: FixedUuid())

    initial_path = recovery_runtime_module._recovery_transaction_path(tmp_path / "r")
    padding = 230 - len(str(initial_path))
    assert padding >= 0
    run_dir = tmp_path / ("r" * (padding + 1))
    transaction_path = recovery_runtime_module._recovery_transaction_path(run_dir)
    temp_path = recovery_runtime_module._recovery_transaction_temp_path(
        transaction_path
    )

    assert len(str(transaction_path)) == 230
    assert temp_path.parent == transaction_path.parent
    assert temp_path.name == f".r.{'f' * 16}"
    assert len(str(temp_path)) < 260


def test_recovery_after_partial_initialization_rejects_continue_before_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    original_copy = loop_runtime_module._copy_if_exists
    copy_count = 0

    def crash_after_first_copy(source: Path, target: Path) -> None:
        nonlocal copy_count
        original_copy(source, target)
        copy_count += 1
        if copy_count == 1:
            raise RuntimeError("simulated crash during root initialization")

    monkeypatch.setattr(
        loop_runtime_module,
        "_copy_if_exists",
        crash_after_first_copy,
    )
    with pytest.raises(RuntimeError, match="root initialization"):
        LoopAutomationRuntime(workspace).start(
            BriefInput(
                mode="bug",
                text="验证部分初始化恢复边界",
                source="partial-initialization",
                repo_path=str(repo),
            ),
            "assist",
        )

    run_dir = next((workspace / "runs").glob("*-loop"))
    assert run_dir.joinpath("agent-brief.md").is_file()
    assert not run_dir.joinpath("project-context.md").exists()
    monkeypatch.setattr(
        loop_runtime_module,
        "_copy_if_exists",
        original_copy,
    )

    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "owner exited during root initialization",
    )

    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert recovered_state.current_step == "recovered_initialization_incomplete"
    assert "project-context.md" in run_dir.joinpath(
        "recovery-report.md"
    ).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="初始化未完成"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))


def test_continue_rejects_pending_recovery_without_terminal_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    run_dir = LoopAutomationRuntime(workspace).start(
        BriefInput(
            mode="bug",
            text="验证普通 recovery 的 pending transaction",
            source="pending-recovery",
            repo_path=str(repo),
        ),
        "assist",
    )
    state_path = run_dir / "state.json"
    state = LoopAutomationState.model_validate_json(
        state_path.read_text(encoding="utf-8")
    )
    state.status = "running"
    state.current_step = "workspace_check"
    state.save(state_path)
    original_ensure = recovery_runtime_module._ensure_recovery_trace_events

    def crash_before_trace(
        recovery_run_dir: Path,
        transaction: recovery_runtime_module.RecoveryTransaction,
    ) -> None:
        del recovery_run_dir, transaction
        raise RuntimeError("simulated recovery trace crash")

    monkeypatch.setattr(
        recovery_runtime_module,
        "_ensure_recovery_trace_events",
        crash_before_trace,
    )
    with pytest.raises(RuntimeError, match="recovery trace crash"):
        RecoveryRuntime(workspace).recover_loop(
            run_dir.name,
            "owner exited before first iteration",
        )

    with pytest.raises(ValueError, match="transaction 尚未提交"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )

    monkeypatch.setattr(
        recovery_runtime_module,
        "_ensure_recovery_trace_events",
        original_ensure,
    )
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "retry pending recovery",
    )
    assert not run_dir.joinpath(
        ".control",
        "recovery-transaction.json",
    ).exists()
    resumed = LoopAutomationRuntime(workspace).continue_assist(
        run_dir.name,
        repo,
        verify=False,
    )
    resumed_state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert resumed_state.current_iteration == 1


def test_success_eval_rejects_interrupted_latest_iteration(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "interrupted-success-loop"
    iteration_dir = run_dir / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    interrupted_at = "2026-07-16T12:00:00+00:00"
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(tmp_path / "repo"),
        input_source="inline-text",
        status="success",
        current_step="done",
        current_iteration=1,
        iterations=[
            LoopIterationState(
                iteration=1,
                lifecycle="interrupted",
                interrupted_step="worker",
                interrupted_at=interrupted_at,
            )
        ],
    )
    state.save(run_dir / "state.json")
    iteration_dir.joinpath("interruption-report.md").write_text(
        "\n".join(
            [
                "# Iteration Interruption Report",
                "",
                "- 迭代：`1`",
                "- 原步骤：`worker`",
                f"- 冻结时间：`{interrupted_at}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    results = run_loop_eval(
        run_dir,
        [],
        require_terminal=False,
        status_for_eval="success",
    )

    assert "FAIL: success loop 的最新 iteration 必须为 completed" in results


def test_continue_rejects_existing_next_iteration_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    run_dir = LoopAutomationRuntime(workspace).start(
        BriefInput(
            mode="bug",
            text="验证已有 iteration 目录不得覆盖",
            source="iteration-collision",
            repo_path=str(repo),
        ),
        "assist",
    )
    first_dir = run_dir / "iterations" / "01"
    second_dir = run_dir / "iterations" / "02"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    sentinel = second_dir / "existing-evidence.txt"
    sentinel.write_text("do not overwrite\n", encoding="utf-8")
    state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    state.status = "needs_human"
    state.current_step = "recovered"
    state.current_iteration = 1
    state.iterations = [
        LoopIterationState(
            iteration=1,
            lifecycle="interrupted",
            interrupted_step="worker",
            interrupted_at="2026-07-16T12:00:00+00:00",
        )
    ]
    state.save(run_dir / "state.json")
    TraceWriter(run_dir / "trace.jsonl").write(
        "loop_recovered",
        previous_step="worker",
        previous_iteration=1,
        continuation_allowed=True,
    )

    with pytest.raises(ValueError, match="下一 iteration 目录已存在"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )

    unchanged = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert unchanged.status == "needs_human"
    assert unchanged.current_iteration == 1
    assert sentinel.read_text(encoding="utf-8") == "do not overwrite\n"


def _init_git_repo(path: Path, *, with_verification: bool = False) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    path.joinpath("README.md").write_text(
        "# recovery demo\n",
        encoding="utf-8",
        newline="\n",
    )
    tracked_paths = ["README.md"]
    if with_verification:
        path.joinpath(".vega.yaml").write_text(
            "\n".join(
                [
                    "version: 1",
                    "verification:",
                    "  commands:",
                    "    - python -c \"print('recovery verification passed')\"",
                    "  max_commands: 1",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        tracked_paths.append(".vega.yaml")
    subprocess.run(
        ["git", "add", "--", *tracked_paths],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Recovery Tests",
            "-c",
            "user.email=vega-recovery@example.invalid",
            "commit",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_for_loop_run(workspace: Path, owner: subprocess.Popen[str]) -> Path:
    result: Path | None = None

    def locate() -> bool:
        nonlocal result
        candidates = sorted((workspace / "runs").glob("*-loop"))
        if not candidates:
            return False
        candidate = candidates[-1]
        execution_path = (
            candidate
            / "iterations"
            / "01"
            / "executions"
            / "worker"
            / "execution.json"
        )
        if execution_path.is_file():
            result = candidate
            return True
        return False

    _wait_until(
        locate,
        owner=owner,
        description="loop execution",
        timeout_seconds=30.0,
    )
    assert result is not None
    return result


def _wait_until(
    predicate,
    *,
    owner: subprocess.Popen[str] | None = None,
    description: str,
    timeout_seconds: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        if owner is not None and owner.poll() is not None:
            output = owner.stdout.read() if owner.stdout is not None else ""
            pytest.fail(
                f"owner exited before {description}: code={owner.returncode}\n{output}"
            )
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {description}")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _terminate_test_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    os.kill(pid, signal.SIGKILL)
