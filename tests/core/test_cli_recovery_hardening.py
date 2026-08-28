from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega import git_read as git_read_module
from vega import models
from vega.execution_control import ExecutionLease, inspect_execution_for_recovery
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import LoopAutomationState
from vega.project_config import check_project_config
from vega.recovery_runtime import RecoveryRuntime
from vega.run_status import latest_run_dir, render_run_status, run_status_payload
from vega.run_utils import resolve_run_dir
from vega.tools import git_tools
from vega.verification import run_project_verification

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    path.joinpath("README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
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


def _loop_state(repo: Path, run_id: str) -> LoopAutomationState:
    return LoopAutomationState(
        run_id=run_id,
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="inline-text",
    )


def _write_execution(
    run_dir: Path,
    name: str,
    *,
    step: str,
    status: str,
    last_heartbeat: str,
    child_pid: int | None = None,
) -> None:
    execution_dir = run_dir / "executions" / name
    execution_dir.mkdir(parents=True)
    lease = ExecutionLease(
        run_id=run_dir.name,
        step=step,
        owner_pid=os.getpid(),
        child_pid=child_pid,
        command=[step],
        started_at=last_heartbeat,
        last_heartbeat=last_heartbeat,
        lease_expires_at=last_heartbeat,
        deadline=last_heartbeat,
        status=status,
        finished_at=last_heartbeat if status not in {"starting", "running", "stop_requested"} else None,
    )
    execution_dir.joinpath("execution.json").write_text(
        lease.model_dump_json(indent=2),
        encoding="utf-8",
    )


def test_model_save_uses_atomic_replace_without_truncating_existing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text('{"sentinel": true}\n', encoding="utf-8")
    monkeypatch.setattr(models.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        models.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(PermissionError("locked")),
    )

    with pytest.raises(PermissionError, match="locked"):
        _loop_state(tmp_path, "atomic-run").save(state_path)

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"sentinel": True}
    assert list(tmp_path.glob(".m.*")) == []


def test_model_temp_path_preserves_windows_path_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedUuid:
        hex = "f" * 32

    monkeypatch.setattr(models, "uuid4", lambda: FixedUuid())

    initial_path = tmp_path / "r" / "state.json"
    padding = 240 - len(str(initial_path))
    assert padding >= 0
    run_dir = tmp_path / ("r" * (padding + 1))
    state_path = run_dir / "state.json"
    temp_path = models._model_temp_path(state_path)

    assert len(str(state_path)) == 240
    assert temp_path.parent == state_path.parent
    assert temp_path.name == f".m.{'f' * 16}"
    assert len(str(temp_path)) < 260


def test_recovery_writes_diagnostic_for_corrupt_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "corrupt-loop"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("state.json").write_text('{"status":', encoding="utf-8")

    with pytest.raises(ValueError, match="state.json 无法解析"):
        RecoveryRuntime(tmp_path).recover_loop(run_dir.name, "模拟断电")

    report = run_dir.joinpath("recovery-report.md").read_text(encoding="utf-8")
    trace = run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8")
    assert "自动 recovery 已停止" in report
    assert "loop_recovery_blocked" in trace


def test_recovery_rejects_state_from_another_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "current-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(tmp_path / "repo", "other-loop")
    state.status = "running"
    state.save(run_dir / "state.json")

    with pytest.raises(ValueError, match="state.run_id 与 run 目录身份不一致"):
        RecoveryRuntime(tmp_path).recover_loop(run_dir.name, "拒绝错身份证据")


def test_continue_rejects_state_from_another_run_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "current-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(repo, "other-loop")
    state.status = "needs_human"
    state.save(run_dir / "state.json")

    with pytest.raises(ValueError, match="state.run_id 与 run 目录身份不一致"):
        LoopAutomationRuntime(tmp_path).continue_assist(run_dir.name, repo, verify=False)


def test_recovery_rejects_expired_lease_while_owned_pid_is_alive(tmp_path: Path) -> None:
    execution_dir = tmp_path / "runs" / "live-loop" / "executions" / "worker"
    execution_dir.mkdir(parents=True)
    expired = datetime.now(UTC) - timedelta(minutes=1)
    lease = ExecutionLease(
        run_id="live-loop",
        step="worker",
        owner_pid=os.getpid(),
        command=["worker"],
        started_at=expired.isoformat(),
        last_heartbeat=expired.isoformat(),
        lease_expires_at=expired.isoformat(),
        deadline=expired.isoformat(),
        status="running",
    )
    execution_dir.joinpath("execution.json").write_text(
        lease.model_dump_json(indent=2),
        encoding="utf-8",
    )

    inspection = inspect_execution_for_recovery(execution_dir.parents[1])

    assert not inspection.can_recover
    assert "PID" in inspection.summary


def test_recovery_rejects_terminal_execution_while_child_pid_is_alive(tmp_path: Path) -> None:
    execution_dir = tmp_path / "runs" / "terminal-live" / "executions" / "worker"
    execution_dir.mkdir(parents=True)
    now = datetime.now(UTC)
    lease = ExecutionLease(
        run_id="terminal-live",
        step="worker",
        owner_pid=os.getpid(),
        child_pid=os.getpid(),
        command=["worker"],
        started_at=(now - timedelta(minutes=1)).isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
        deadline=(now + timedelta(minutes=2)).isoformat(),
        status="timed_out",
        reason="timeout",
        finished_at=now.isoformat(),
    )
    execution_path = execution_dir / "execution.json"
    execution_path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")

    inspection = inspect_execution_for_recovery(execution_dir.parents[1])

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "terminal execution" in inspection.summary
    assert "process tree 仍存活" in inspection.summary


def test_recovery_rejects_corrupt_execution_record(tmp_path: Path) -> None:
    execution_dir = tmp_path / "runs" / "corrupt-execution" / "executions" / "worker"
    execution_dir.mkdir(parents=True)
    execution_dir.joinpath("execution.json").write_text(
        "{not-valid-json",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="无法解析 execution 记录"):
        inspect_execution_for_recovery(execution_dir.parents[1])


def test_verification_timeout_terminates_owned_descendants(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    marker = repo / "late-child.txt"
    repo.joinpath("child.py").write_text(
        "import pathlib, time\n"
        "time.sleep(2.0)\n"
        "pathlib.Path('late-child.txt').write_text('late', encoding='utf-8')\n",
        encoding="utf-8",
    )
    repo.joinpath("slow_verify.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    command_parts = [sys.executable, "slow_verify.py"]
    command = subprocess.list2cmdline(command_parts) if os.name == "nt" else shlex.join(command_parts)
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  max_commands: 1",
                "  timeout_seconds: 1",
                "  commands:",
                f"    - {json.dumps(command)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    started = time.perf_counter()
    result = run_project_verification(tmp_path, repo, tmp_path / "runs" / "verify-run" / "verification")
    elapsed = time.perf_counter() - started
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    time.sleep(2.5)

    assert elapsed < 4.0
    assert payload["results"][0]["status"] == "timeout"
    assert not marker.exists()
    execution = result.result_path.parent / "executions" / "verification-01" / "execution.json"
    assert json.loads(execution.read_text(encoding="utf-8"))["status"] == "timed_out"


def test_git_dubious_ownership_error_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git_read_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=128,
            stdout="",
            stderr="fatal: detected dubious ownership in repository",
        ),
    )

    returncode, _, stderr = git_tools.run_git(tmp_path, "git.status")

    assert returncode == 128
    assert "safe.directory" in stderr
    assert "VEGA_GIT_SAFE_DIRECTORY" in stderr
    assert "git config --global" not in stderr



def test_config_check_warns_when_codex_cli_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("vega.project_config.shutil.which", lambda _: None)

    result = check_project_config(repo)

    assert any(issue.code == "codex_cli_missing" for issue in result.issues)
    assert not result.has_errors


def test_latest_all_prefers_parent_loop_over_newer_internal_child(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    parent = runs / "parent-loop"
    child = runs / "child-review"
    parent.mkdir(parents=True)
    child.mkdir()
    state = _loop_state(tmp_path, parent.name)
    state.brief_run = "child-brief"
    state.save(parent / "state.json")
    payload = json.loads(parent.joinpath("state.json").read_text(encoding="utf-8"))
    payload["iterations"] = [{"iteration": 1, "review_run": child.name}]
    parent.joinpath("state.json").write_text(json.dumps(payload), encoding="utf-8")
    child.joinpath("state.json").write_text(
        json.dumps({"run_id": child.name, "runner": "codex-exec"}),
        encoding="utf-8",
    )
    now = time.time()
    os.utime(parent, (now - 10, now - 10))
    os.utime(child, (now, now))

    assert latest_run_dir(tmp_path) == parent


def test_latest_ignores_non_run_directories(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    noise = runs / ".pytest-check-noise"
    noise.mkdir()
    real = runs / "real-loop"
    real.mkdir()
    _loop_state(tmp_path / "repo", real.name).save(real / "state.json")
    os.utime(noise, (time.time() + 10, time.time() + 10))

    assert latest_run_dir(tmp_path) == real


def test_latest_ignores_run_directory_link_outside_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    real = runs / "real-loop"
    real.mkdir()
    _loop_state(tmp_path / "repo", real.name).save(real / "state.json")
    outside = tmp_path / "outside-run"
    outside.mkdir()
    _loop_state(tmp_path / "repo", "escaped-run").save(outside / "state.json")
    link = runs / "escaped-run"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"当前平台不能创建目录 symlink：{exc}")

    assert latest_run_dir(tmp_path) == real


def test_status_rejects_state_from_another_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "current-loop"
    run_dir.mkdir(parents=True)
    _loop_state(tmp_path / "repo", "other-loop").save(run_dir / "state.json")

    with pytest.raises(ValueError, match="state.json run_id 与 run 目录身份不一致"):
        run_status_payload(tmp_path, run_dir.name)



def test_status_prefers_active_execution_over_newer_terminal(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "active-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(tmp_path / "repo", run_dir.name)
    state.status = "running"
    state.current_step = "worker"
    state.save(run_dir / "state.json")
    _write_execution(
        run_dir,
        "worker",
        step="worker",
        status="running",
        last_heartbeat="2026-07-11T01:00:00+00:00",
    )
    _write_execution(
        run_dir,
        "reviewer",
        step="reviewer",
        status="completed",
        last_heartbeat="2026-07-11T02:00:00+00:00",
    )

    execution = run_status_payload(tmp_path, run_dir.name)["execution"]

    assert execution["status"] == "running"
    assert execution["step"] == "worker"


def test_status_success_prefers_terminal_execution_over_historical_active(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "continued-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(tmp_path / "repo", run_dir.name)
    state.status = "success"
    state.current_step = "done"
    state.save(run_dir / "state.json")
    _write_execution(
        run_dir,
        "interrupted-worker",
        step="worker",
        status="running",
        last_heartbeat="2026-07-11T03:00:00+00:00",
        child_pid=24680,
    )
    _write_execution(
        run_dir,
        "continued-reviewer",
        step="reviewer",
        status="completed",
        last_heartbeat="2026-07-11T02:00:00+00:00",
        child_pid=13579,
    )

    text = render_run_status(tmp_path, run_dir.name)
    execution = run_status_payload(tmp_path, run_dir.name)["execution"]

    assert execution["status"] == "completed"
    assert execution["step"] == "reviewer"
    assert "当前 `worker` 仍在运行" not in text
    assert "历史 owned child PID（仅供审计，不表示当前存活）：`13579`" in text


def test_status_recovered_marks_stale_active_execution_as_history(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "recovered-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(tmp_path / "repo", run_dir.name)
    state.status = "needs_human"
    state.current_step = "recovered"
    state.save(run_dir / "state.json")
    _write_execution(
        run_dir,
        "interrupted-worker",
        step="worker",
        status="running",
        last_heartbeat="2026-07-11T01:00:00+00:00",
        child_pid=24680,
    )

    text = render_run_status(tmp_path, run_dir.name)

    assert "当前 `worker` 仍在运行" not in text
    assert "历史 owned child PID（仅供审计，不表示当前存活）：`24680`" in text
    assert "读取 `" + str((run_dir / "recovery-report.md").resolve()) + "`" in text


def test_status_recovered_auto_worker_without_diff_guides_explicit_rerun(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    run_dir = tmp_path / "runs" / "recovered-auto-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(repo, run_dir.name)
    state.automation_mode = "auto"
    state.status = "needs_human"
    state.current_step = "recovered"
    state.current_iteration = 1
    state.max_iterations = 2
    state.iterations = [
        models.LoopIterationState(
            iteration=1,
            lifecycle="interrupted",
            interrupted_step="worker",
            interrupted_at="2026-08-09T12:00:00+00:00",
        )
    ]
    state.save(run_dir / "state.json")

    text = render_run_status(tmp_path, run_dir.name)

    assert "如果没有新的 tracked 或非 ignored untracked diff" in text
    assert "由所属 ChangeRun 决定是否建立新的 Worker attempt" in text
    assert "如果已有 partial work，不要启动第二个 Writer" in text
    assert "vega loop continue" not in text


def test_status_recovered_auto_worker_at_iteration_limit_hides_rerun(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    run_dir = tmp_path / "runs" / "recovered-auto-loop-at-limit"
    run_dir.mkdir(parents=True)
    state = _loop_state(repo, run_dir.name)
    state.automation_mode = "auto"
    state.status = "needs_human"
    state.current_step = "recovered"
    state.current_iteration = 2
    state.max_iterations = 2
    state.iterations = [
        models.LoopIterationState(iteration=1),
        models.LoopIterationState(
            iteration=2,
            lifecycle="interrupted",
            interrupted_step="worker",
            interrupted_at="2026-08-09T12:00:00+00:00",
        ),
    ]
    state.save(run_dir / "state.json")

    text = render_run_status(tmp_path, run_dir.name)

    assert "已达到自动 Worker 迭代上限" in text
    assert "回到所属 ChangeRun" in text
    assert "vega loop continue" not in text


def test_status_text_keeps_active_owned_child_pid(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "active-child-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(tmp_path / "repo", run_dir.name)
    state.status = "running"
    state.current_step = "worker"
    state.save(run_dir / "state.json")
    _write_execution(
        run_dir,
        "worker",
        step="worker",
        status="running",
        last_heartbeat="2026-07-11T01:00:00+00:00",
        child_pid=24680,
    )

    text = render_run_status(tmp_path, run_dir.name)

    assert "- owned child PID：`24680`" in text
    assert "历史 owned child PID" not in text


def test_status_text_marks_terminal_child_pid_as_audit_history(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "terminal-child-loop"
    run_dir.mkdir(parents=True)
    _loop_state(tmp_path / "repo", run_dir.name).save(run_dir / "state.json")
    _write_execution(
        run_dir,
        "worker",
        step="worker",
        status="completed",
        last_heartbeat="2026-07-11T01:00:00+00:00",
        child_pid=24680,
    )

    text = render_run_status(tmp_path, run_dir.name)

    assert "- 历史 owned child PID（仅供审计，不表示当前存活）：`24680`" in text
    assert "- owned child PID：`24680`" not in text


def test_status_text_terminal_without_child_pid_reports_not_recorded_and_preserves_payload(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "terminal-no-child-loop"
    run_dir.mkdir(parents=True)
    _loop_state(tmp_path / "repo", run_dir.name).save(run_dir / "state.json")
    heartbeat = "2026-07-11T01:00:00+00:00"
    _write_execution(
        run_dir,
        "worker",
        step="worker",
        status="failed",
        last_heartbeat=heartbeat,
    )

    text = render_run_status(tmp_path, run_dir.name)
    payload = run_status_payload(tmp_path, run_dir.name)

    assert "- 历史 owned child PID（仅供审计，不表示当前存活）：`未记录`" in text
    assert "尚未启动" not in text
    assert payload["execution"] == {
        "status": "failed",
        "step": "worker",
        "iteration": None,
        "owner_pid": os.getpid(),
        "child_pid": None,
        "termination_unconfirmed": False,
        "last_heartbeat": heartbeat,
        "deadline": heartbeat,
        "path": str((run_dir / "executions" / "worker" / "execution.json").resolve()),
    }


def test_status_sorts_active_execution_heartbeat_as_utc(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "timezone-loop"
    run_dir.mkdir(parents=True)
    state = _loop_state(tmp_path / "repo", run_dir.name)
    state.status = "running"
    state.current_step = "worker"
    state.save(run_dir / "state.json")
    _write_execution(
        run_dir,
        "offset-worker",
        step="offset-worker",
        status="running",
        last_heartbeat="2026-07-11T09:30:00+08:00",
    )
    _write_execution(
        run_dir,
        "utc-worker",
        step="utc-worker",
        status="running",
        last_heartbeat="2026-07-11T02:00:00+00:00",
    )

    execution = run_status_payload(tmp_path, run_dir.name)["execution"]

    assert execution["step"] == "utc-worker"
    assert execution["last_heartbeat"] == "2026-07-11T02:00:00+00:00"



def test_missing_run_error_names_current_workspace(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="当前 workspace"):
        resolve_run_dir(tmp_path, "missing-run")
