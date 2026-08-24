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

from vega.cli import app
from vega.execution_control import ExecutionLease, is_process_alive
from vega.experimental.goal_runtime import GoalRuntime
from vega.loop_evidence import validate_loop_artifact_integrity
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import LoopAutomationState
from vega.run_lock import RunMutationBusyError, RunMutationLock
from vega.runner import RunnerResult

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
                    "reviewed_files": ["README.md"],
                    "checked_items": ["scope", "tests", "recovery evidence"],
                },
                ensure_ascii=False,
            ),
            command=["static-reviewer"],
        )


def test_owner_crash_recovery_preserves_evidence_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace with spaces 空格"
    repo = tmp_path / "repo with spaces 空格"
    _init_git_repo(repo, with_verification=True)
    goal_runtime = GoalRuntime(workspace)
    goal_dir = goal_runtime.start(
        repo,
        "# Goal\n\nObjective: 验证长任务中断恢复\n\n"
        "Success conditions:\n- checkpoint 证据通过\n",
        "recovery-chaos",
        None,
    )
    goal_runtime.step(
        goal_dir.name,
        task_text="完成 README 的受限修改",
        task_source="recovery-chaos",
    )
    ready = workspace / "control" / "child-ready"
    release = workspace / "control" / "child-release"
    ready.parent.mkdir(parents=True)

    environment = os.environ.copy()
    src_path = str(Path(__file__).resolve().parents[2] / "src")
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
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    child_pid: int | None = None
    try:
        run_dir = _wait_for_loop_run(workspace, owner)
        _bind_goal_child(goal_dir, run_dir)
        _wait_until(lambda: ready.exists(), owner=owner, description="child ready marker")
        execution_path = run_dir / "iterations" / "01" / "executions" / "worker" / "execution.json"
        lease = ExecutionLease.model_validate_json(execution_path.read_text(encoding="utf-8"))
        child_pid = lease.child_pid
        assert child_pid is not None
        assert is_process_alive(owner.pid)
        assert is_process_alive(child_pid)
        assert "partial worker change" in repo.joinpath("README.md").read_text(encoding="utf-8")
        parent_before_busy_reconcile = goal_dir.joinpath(
            "goal-state.json"
        ).read_bytes()
        with pytest.raises(RunMutationBusyError):
            GoalRuntime(workspace).reconcile(goal_dir.name)
        assert (
            goal_dir.joinpath("goal-state.json").read_bytes()
            == parent_before_busy_reconcile
        )
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
        with pytest.raises(ValueError, match="仍存在可确认执行主体"):
            GoalRuntime(workspace).recover(goal_dir.name, "controller exited")
        assert (
            goal_dir.joinpath("goal-state.json").read_bytes()
            == parent_before_busy_reconcile
        )
        GoalRuntime(workspace).reconcile(goal_dir.name)
        waiting_parent = _read_json(goal_dir / "goal-state.json")
        assert waiting_parent["status"] == "running"
        assert waiting_parent["current_step"] == "waiting_for_worker"
        assert waiting_parent["active_child_run"] == run_dir.name
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
        GoalRuntime(workspace).recover(goal_dir.name, "controller exited")
        recovered_parent = _read_json(goal_dir / "goal-state.json")
        assert recovered_parent["status"] == "needs_human"
        assert recovered_parent["current_step"] == "recovered"
        assert recovered_parent["active_child_run"] is None
        assert (
            recovered_parent["checkpoint_records"][0]["bound_child_run"]
            == run_dir.name
        )
        with pytest.raises(ValueError, match="仍绑定未完成 child"):
            GoalRuntime(workspace).resume(goal_dir.name)
        GoalRuntime(workspace).reconcile(goal_dir.name)
        orphaned_parent = _read_json(goal_dir / "goal-state.json")
        assert orphaned_parent["current_step"] == "child_recovery_required"
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
        GoalRuntime(workspace).reconcile(goal_dir.name)
        final_parent = _read_json(goal_dir / "goal-state.json")
        final_record = final_parent["checkpoint_records"][0]
        assert final_parent["status"] == "checkpoint_done"
        assert final_parent["current_step"] == "checkpoint_done"
        assert final_parent["active_child_run"] is None
        assert final_record["bound_child_run"] == run_dir.name
        assert len(final_record["refs"]) == 1
        assert final_record["refs"][0]["run"] == run_dir.name
        assert final_record["refs"][0]["completion_eligible"] is True

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
    path.joinpath(".gitignore").write_text(
        "ignored-partial.txt\n",
        encoding="utf-8",
        newline="\n",
    )
    tracked_paths = ["README.md", ".gitignore"]
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


def _bind_goal_child(goal_dir: Path, child_dir: Path) -> None:
    state = _read_json(goal_dir / "goal-state.json")
    record = state["checkpoint_records"][0]
    assert isinstance(record, dict)
    record["bound_child_run"] = child_dir.name
    record["runner_timeout_seconds"] = 3600
    state["status"] = "running"
    state["current_step"] = "waiting_for_worker"
    state["active_child_run"] = child_dir.name
    state["last_child_run"] = child_dir.name
    state["last_child_status"] = "running"
    text = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    for name in ("goal-state.json", "state.json"):
        goal_dir.joinpath(name).write_text(
            text,
            encoding="utf-8",
            newline="\n",
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
