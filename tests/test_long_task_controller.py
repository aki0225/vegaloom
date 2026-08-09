from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.cli as cli_module
import vega.experimental.goal_controller as goal_controller_module
from vega.cli import app
from vega.execution_control import RunnerExecutionContext
from vega.experimental.goal_runtime import GoalRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput
from vega.progress import RunProgressLog, render_progress_items
from vega.run_lock import RunMutationBusyError, RunMutationLock
from vega.runner import RunnerResult
from vega.run_status import run_status_payload


FAKE_SECRET = "sk-test-secret-long-task-123456"

class _TrackedWorker:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, sandbox, timeout_seconds
        if execution_context and execution_context.progress_reporter:
            execution_context.progress_reporter("worker.turn_started", 1)
            execution_context.progress_reporter("worker.file_changed", 2)
        readme = repo_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8").rstrip() + "\nworker change\n",
            encoding="utf-8",
            newline="\n",
        )
        return RunnerResult(
            status="success",
            output="worker complete",
            command=[f"worker --api-key={FAKE_SECRET}"],
        )


class _ApprovingReviewer:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, repo_path, sandbox, timeout_seconds
        if execution_context and execution_context.progress_reporter:
            execution_context.progress_reporter("reviewer.turn_started", 1)
            execution_context.progress_reporter("reviewer.turn_completed", 2)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "review complete",
                    "findings": [],
                    "reviewed_files": ["README.md"],
                    "checked_items": ["scope", "tests"],
                }
            ),
            command=[f"reviewer --token={FAKE_SECRET}"],
        )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8", newline="\n")
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('goal verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "README.md", ".vega.yaml"],
        cwd=repo,
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
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _start_goal(workspace: Path, repo: Path) -> tuple[GoalRuntime, Path]:
    runtime = GoalRuntime(workspace)
    run_dir = runtime.start(
        repo,
        "# Goal\n\nObjective: 完成一个有界 checkpoint\n",
        "test",
        None,
    )
    return runtime, run_dir


def _read_tree(path: Path) -> str:
    return "\n".join(
        item.read_text(encoding="utf-8", errors="replace")
        for item in path.rglob("*")
        if item.is_file()
    )


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _mark_child_running(child_dir: Path) -> None:
    state = _read_json(child_dir / "state.json")
    state["status"] = "running"
    state["current_step"] = "worker"
    child_dir.joinpath("state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_progress_log_redacts_secret_and_tolerates_partial_trailing_line(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "progress-run"
    run_dir.mkdir(parents=True)
    progress = RunProgressLog(run_dir)

    progress.append(
        "goal.child_run_created",
        checkpoint="01",
        child_run=FAKE_SECRET,
    )
    with progress.path.open("a", encoding="utf-8") as stream:
        stream.write('{"version": 1')

    items = progress.read()
    raw = progress.path.read_text(encoding="utf-8")

    assert len(items) == 1
    assert items[0]["child_run"] == "[REDACTED]"
    assert FAKE_SECRET not in raw

    rendered = render_progress_items(
        [
            {
                "step": "loop",
                "event": "run_finished",
                "iteration": 1,
                "status": "needs_human",
            }
        ]
    )
    assert "运行已结束" in rendered
    assert "status=needs_human" in rendered


def test_watch_supports_snapshot_and_legacy_run_without_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _, run_dir = _start_goal(tmp_path, repo)
    run_dir.joinpath("progress.jsonl").unlink()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["watch", "--run", run_dir.name])

    assert result.exit_code == 0, result.output
    assert f"run={run_dir.name}" in result.output
    assert "暂无安全进度事件" in result.output


def test_watch_whitelists_tampered_progress_without_leaking_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _, run_dir = _start_goal(tmp_path, repo)
    for name in ("state.json", "goal-state.json"):
        path = run_dir / name
        state = json.loads(path.read_text(encoding="utf-8"))
        state["current_step"] = f"\x1b[31m{FAKE_SECRET}\x1b[0m"
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    run_dir.joinpath("progress.jsonl").write_text(
        json.dumps(
            {
                "version": 1,
                "ts": FAKE_SECRET,
                "step": FAKE_SECRET,
                "event": FAKE_SECRET,
                "child_run": FAKE_SECRET,
                "checkpoint": "\x1b[31mnot-a-checkpoint\x1b[0m",
                "unknown": FAKE_SECRET,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    text_result = CliRunner().invoke(app, ["watch", "--run", run_dir.name])
    json_result = CliRunner().invoke(
        app,
        ["watch", "--run", run_dir.name, "--json"],
    )

    assert text_result.exit_code == 0, text_result.output
    assert json_result.exit_code == 0, json_result.output
    assert FAKE_SECRET not in text_result.output
    assert FAKE_SECRET not in json_result.output
    assert '"unknown":' not in json_result.output
    assert "[REDACTED]" in text_result.output
    assert "\x1b" not in text_result.output
    assert '"current_step": "unknown"' in json_result.output


def test_progress_append_rejects_preexisting_hardlink(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "hardlink-progress"
    run_dir.mkdir(parents=True)
    sentinel = tmp_path / "outside-sentinel.jsonl"
    sentinel.write_text("preserve\n", encoding="utf-8")
    progress = RunProgressLog(run_dir)
    try:
        os.link(sentinel, progress.path)
    except OSError as exc:
        pytest.skip(f"当前文件系统不支持 hardlink probe：{type(exc).__name__}")

    with pytest.raises(ValueError, match="hardlink"):
        progress.append("worker.turn_started", iteration=1)

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_watch_follow_prints_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _, run_dir = _start_goal(tmp_path, repo)
    monkeypatch.chdir(tmp_path)

    def finish_goal(_: float) -> None:
        for name in ("state.json", "goal-state.json"):
            path = run_dir / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["status"] = "stopped"
            payload["current_step"] = "stopped"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        RunProgressLog(run_dir).append("goal.run_finished", status="stopped")

    monkeypatch.setattr(cli_module.time, "sleep", finish_goal)

    result = CliRunner().invoke(
        app,
        ["watch", "--run", run_dir.name, "--follow", "--interval", "0.2"],
    )

    assert result.exit_code == 0, result.output
    assert f"run={run_dir.name} status=stopped step=stopped" in result.output
    assert "运行已结束" in result.output
    assert "status=stopped" in result.output


def test_goal_step_saves_explicit_checkpoint_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, run_dir = _start_goal(tmp_path, repo)

    runtime.step(
        run_dir.name,
        task_text=f"更新 README；api_key={FAKE_SECRET}",
        task_source="inline-text",
    )

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    plan = run_dir.joinpath("checkpoints", "01", "checkpoint-plan.md").read_text(
        encoding="utf-8"
    )
    progress = RunProgressLog(run_dir).read()

    assert state["checkpoint_records"][0]["task_text"] == "更新 README；api_key=[REDACTED]"
    assert state["checkpoint_records"][0]["task_source"] == "inline-text"
    assert "## Checkpoint Task" in plan
    assert FAKE_SECRET not in _read_tree(run_dir)
    assert any(item["event"] == "checkpoint_planned" for item in progress)


def test_goal_run_one_completes_one_verified_child_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, run_dir = _start_goal(tmp_path, repo)
    runtime.step(run_dir.name, task_text="更新 README", task_source="inline-text")
    real_loop_runtime = LoopAutomationRuntime
    observed_timeouts: list[int] = []

    def make_child_runtime(
        workspace: Path,
        progress_reporter=None,
        timeout_seconds: int = 900,
    ):
        observed_timeouts.append(timeout_seconds)
        return real_loop_runtime(
            workspace,
            worker_runner=_TrackedWorker(),
            reviewer_runner=_ApprovingReviewer(),
            progress_reporter=progress_reporter,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(
        goal_controller_module,
        "LoopAutomationRuntime",
        make_child_runtime,
    )

    completed = runtime.run_one(
        run_dir.name,
        max_iterations=1,
        verify=True,
        max_checkpoints=1,
        runner_timeout_seconds=3600,
    )

    state = json.loads(completed.joinpath("goal-state.json").read_text(encoding="utf-8"))
    progress = RunProgressLog(completed).read()
    child_run = state["last_child_run"]
    checkpoint_report = completed.joinpath(
        "checkpoints",
        "01",
        "checkpoint-report.md",
    ).read_text(encoding="utf-8")
    child_progress = RunProgressLog(completed.parent / child_run).read()

    assert state["status"] == "checkpoint_done"
    assert state["current_step"] == "checkpoint_done"
    assert state["active_child_run"] is None
    assert state["last_child_status"] == "success"
    assert state["checkpoint_records"][0]["status"] == "done"
    assert state["checkpoint_records"][0]["bound_child_run"] == child_run
    assert state["checkpoint_records"][0]["runner_timeout_seconds"] == 3600
    assert observed_timeouts == [3600]
    assert child_run
    assert run_status_payload(tmp_path, run_dir.name)["last_child_run"] == child_run
    assert any(item["event"] == "child_run_created" for item in progress)
    assert any(item["event"] == "child_run_finished" for item in progress)
    assert any(item["event"] == "checkpoint_done" for item in progress)
    assert child_progress[-1]["event"] == "run_finished"
    assert child_progress[-1]["status"] == "success"
    assert "是否调用 worker/reviewer" in checkpoint_report
    assert "未自动调用 worker/reviewer" not in checkpoint_report
    assert FAKE_SECRET not in _read_tree(tmp_path / "runs")


def test_goal_mutators_share_one_run_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, run_dir = _start_goal(tmp_path, repo)
    before_state = run_dir.joinpath("goal-state.json").read_bytes()

    with RunMutationLock.acquire(run_dir, "goal.run"):
        with pytest.raises(RunMutationBusyError):
            runtime.stop(run_dir.name, "concurrent stop")
        with pytest.raises(RunMutationBusyError):
            runtime.complete(run_dir.name, "concurrent complete")
        with pytest.raises(RunMutationBusyError):
            runtime.step(run_dir.name, task_text="concurrent step")
        with pytest.raises(RunMutationBusyError):
            runtime.reconcile(run_dir.name)

    assert run_dir.joinpath("goal-state.json").read_bytes() == before_state
    assert run_dir.joinpath("state.json").read_bytes() == before_state


def test_goal_run_one_marks_malformed_child_as_needs_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, run_dir = _start_goal(tmp_path, repo)
    runtime.step(run_dir.name, task_text="更新 README", task_source="inline-text")

    class _MalformedChildRuntime:
        def __init__(
            self,
            workspace: Path,
            progress_reporter=None,
            timeout_seconds: int = 900,
        ) -> None:
            del progress_reporter, timeout_seconds
            self.workspace = workspace

        def start(self, brief_input, automation_mode, **kwargs) -> Path:
            del brief_input, automation_mode
            child_dir = self.workspace / "runs" / "malformed-child-loop"
            child_dir.mkdir()
            kwargs["on_run_created"](child_dir)
            child_dir.joinpath("state.json").write_text("{broken", encoding="utf-8")
            return child_dir

    monkeypatch.setattr(
        goal_controller_module,
        "LoopAutomationRuntime",
        _MalformedChildRuntime,
    )

    blocked = runtime.run_one(run_dir.name, max_checkpoints=1)

    state = json.loads(blocked.joinpath("goal-state.json").read_text(encoding="utf-8"))

    assert state["status"] == "needs_human"
    assert state["current_step"] == "checkpoint_blocked"
    assert state["active_child_run"] == "malformed-child-loop"
    assert state["last_child_run"] == "malformed-child-loop"
    assert state["last_child_status"] == "running"
    assert state["checkpoint_records"][0]["bound_child_run"] == "malformed-child-loop"
    assert blocked.joinpath("checkpoints", "01", "checkpoint-blocked.md").exists()


def test_goal_run_one_rejects_multiple_checkpoint_dispatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, run_dir = _start_goal(tmp_path, repo)
    runtime.step(run_dir.name, task_text="更新 README", task_source="inline-text")

    with pytest.raises(ValueError, match="只允许 --max-checkpoints 1"):
        runtime.run_one(run_dir.name, max_checkpoints=2)


@pytest.mark.parametrize("timeout_seconds", [59, 3601])
def test_goal_run_one_rejects_runner_timeout_outside_one_hour_bound(
    tmp_path: Path,
    timeout_seconds: int,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, run_dir = _start_goal(tmp_path, repo)
    runtime.step(run_dir.name, task_text="更新 README", task_source="inline-text")

    with pytest.raises(ValueError, match="60 到 3600"):
        runtime.run_one(
            run_dir.name,
            max_checkpoints=1,
            runner_timeout_seconds=timeout_seconds,
        )


def test_goal_reconcile_refreshes_recovered_child_without_duplicate_ref(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    child_dir = LoopAutomationRuntime(
        tmp_path,
        worker_runner=_TrackedWorker(),
        reviewer_runner=_ApprovingReviewer(),
    ).start(
        BriefInput(
            mode="feature",
            text="更新 README",
            source="test",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=True,
    )
    child_state_path = child_dir / "state.json"
    successful_child_state = child_state_path.read_bytes()
    stale_child_state = _read_json(child_state_path)
    stale_child_state["status"] = "needs_human"
    stale_child_state["current_step"] = "done"
    child_state_path.write_text(
        json.dumps(stale_child_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    runtime, run_dir = _start_goal(tmp_path, repo)
    runtime.step(run_dir.name, task_text="更新 README", task_source="inline-text")
    _bind_goal_child(run_dir, child_dir)
    runtime.attach(
        run_dir.name,
        "01",
        child_dir.name,
        "loop",
        "模拟 child 恢复前保存的旧证据。",
    )
    blocked_state = _read_json(run_dir / "goal-state.json")
    blocked_state["status"] = "needs_human"
    blocked_state["current_step"] = "checkpoint_blocked"
    blocked_state["active_child_run"] = None
    blocked_state["last_child_status"] = "needs_human"
    blocked_text = json.dumps(blocked_state, ensure_ascii=False, indent=2) + "\n"
    for name in ("goal-state.json", "state.json"):
        run_dir.joinpath(name).write_text(
            blocked_text,
            encoding="utf-8",
            newline="\n",
        )
    child_state_path.write_bytes(successful_child_state)

    record = blocked_state["checkpoint_records"][0]
    assert isinstance(record, dict)
    child_run = record["bound_child_run"]
    assert isinstance(child_run, str)
    assert blocked_state["status"] == "needs_human"
    assert len(record["refs"]) == 1
    assert record["refs"][0]["completion_eligible"] is False
    with pytest.raises(ValueError, match="不允许 resume"):
        GoalRuntime(tmp_path).resume(run_dir.name)

    reconciled = GoalRuntime(tmp_path).reconcile(run_dir.name)
    reconciled_state = _read_json(reconciled / "goal-state.json")
    refreshed = reconciled_state["checkpoint_records"][0]
    trace_before_repeat = reconciled.joinpath("goal-trace.jsonl").read_bytes()

    assert reconciled_state["status"] == "checkpoint_done"
    assert reconciled_state["current_step"] == "checkpoint_done"
    assert reconciled_state["active_child_run"] is None
    assert refreshed["status"] == "done"
    assert refreshed["bound_child_run"] == child_run
    assert len(refreshed["refs"]) == 1
    assert refreshed["refs"][0]["run"] == child_run
    assert refreshed["refs"][0]["completion_eligible"] is True
    assert reconciled.joinpath(
        "checkpoints",
        "01",
        "checkpoint-reconcile.md",
    ).is_file()

    GoalRuntime(tmp_path).reconcile(run_dir.name)
    repeated = _read_json(reconciled / "goal-state.json")
    assert len(repeated["checkpoint_records"][0]["refs"]) == 1
    assert reconciled.joinpath("goal-trace.jsonl").read_bytes() == trace_before_repeat


def test_goal_reconcile_waits_for_live_running_child(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, goal_dir = _start_goal(tmp_path, repo)
    runtime.step(goal_dir.name, task_text="更新 README", task_source="inline-text")
    child_dir = LoopAutomationRuntime(tmp_path).start(
        BriefInput(
            mode="feature",
            text="等待外部 worker",
            source="test",
            repo_path=str(repo),
        ),
        "assist",
    )
    _bind_goal_child(goal_dir, child_dir)
    _mark_child_running(child_dir)
    execution_dir = child_dir / "iterations" / "01" / "executions" / "worker"
    from vega.execution_control import ExecutionController

    controller = ExecutionController(
        RunnerExecutionContext(
            execution_root=child_dir,
            execution_dir=execution_dir,
            run_id=child_dir.name,
            step="worker",
            iteration=1,
        )
    )
    controller.prepare(["live-worker"], 3600)
    controller.child_started(os.getpid())

    GoalRuntime(tmp_path).reconcile(goal_dir.name)
    state = _read_json(goal_dir / "goal-state.json")

    assert state["status"] == "running"
    assert state["current_step"] == "waiting_for_worker"
    assert state["active_child_run"] == child_dir.name
    assert state["checkpoint_records"][0]["bound_child_run"] == child_dir.name


def test_goal_reconcile_requires_recovery_for_orphaned_running_child(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    runtime, goal_dir = _start_goal(tmp_path, repo)
    runtime.step(goal_dir.name, task_text="更新 README", task_source="inline-text")
    child_dir = LoopAutomationRuntime(tmp_path).start(
        BriefInput(
            mode="feature",
            text="模拟 CLI 中断",
            source="test",
            repo_path=str(repo),
        ),
        "assist",
    )
    _bind_goal_child(goal_dir, child_dir)
    _mark_child_running(child_dir)

    GoalRuntime(tmp_path).reconcile(goal_dir.name)
    state = _read_json(goal_dir / "goal-state.json")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "child_recovery_required"
    assert state["active_child_run"] == child_dir.name
    with pytest.raises(ValueError, match="不允许 resume"):
        GoalRuntime(tmp_path).resume(goal_dir.name)
