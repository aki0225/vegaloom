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

import vega.loop_continue_support as loop_continue_support_module
import vega.loop_runtime as loop_runtime_module
import vega.recovery_runtime as recovery_runtime_module
import vega.worker_rerun_planning as worker_rerun_planning_module
import vega.worker_rerun_transaction as worker_rerun_transaction_module
from vega.cli import app
from vega.execution_control import ExecutionLease, is_process_alive
from vega.experimental.goal_runtime import GoalRuntime
from vega.loop_evidence import validate_loop_artifact_integrity
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput, LoopAutomationState, LoopIterationState
from vega.recovery_runtime import RecoveryRuntime
from vega.run_lock import RunMutationBusyError, RunMutationLock
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.trace import TraceWriter
from vega.worker_rerun import worker_rerun_binding_issues
from vega.workspace_baseline import (
    is_legacy_assist_initialization_unavailable,
    recovered_initialization_step,
)


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


class CountingTrackedChangeWorker(TrackedChangeWorker):
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *args, **kwargs) -> RunnerResult:
        self.calls += 1
        return super().run(*args, **kwargs)


class CrashingWorker:
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
        raise RuntimeError("simulated worker crash before tracked diff")


class PartialChangeCrashingWorker:
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
            "# recovery demo\npartial worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        raise RuntimeError("simulated worker crash after tracked diff")


class UntrackedChangeCrashingWorker:
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
        repo_path.joinpath("partial-note.txt").write_text(
            "partial worker output\n",
            encoding="utf-8",
            newline="\n",
        )
        raise RuntimeError("simulated worker crash after untracked work")


class IgnoredChangeCrashingWorker:
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
        repo_path.joinpath("ignored-partial.txt").write_text(
            "ignored partial worker output\n",
            encoding="utf-8",
            newline="\n",
        )
        raise RuntimeError("simulated worker crash after ignored work")


class IteratingWorker:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del sandbox, timeout_seconds, execution_context
        self.prompts.append(prompt)
        call = len(self.prompts)
        if call == 1:
            repo_path.joinpath("README.md").write_text(
                "# recovery demo\nfirst worker change\n",
                encoding="utf-8",
                newline="\n",
            )
        elif call == 2:
            raise RuntimeError("simulated second worker crash")
        else:
            repo_path.joinpath("README.md").write_text(
                "# recovery demo\nfinal worker change\n",
                encoding="utf-8",
                newline="\n",
            )
        return RunnerResult(
            status="success",
            output=f"worker call {call} completed",
            command=["iterating-worker"],
        )


class IteratingReviewer:
    def __init__(self) -> None:
        self.calls = 0

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
        self.calls += 1
        if self.calls == 1:
            verdict = {
                "verdict": "request_changes",
                "summary": "首轮改动仍需修正。",
                "findings": [
                    {
                        "severity": "major",
                        "file": "README.md",
                        "line": 2,
                        "title": "替换首轮占位内容",
                        "evidence": "README 仍包含 first worker change。",
                        "recommendation": "改为最终内容。",
                    }
                ],
                "reviewed_files": ["README.md"],
                "checked_items": ["scope", "tests"],
            }
        else:
            verdict = {
                "verdict": "approve",
                "summary": "恢复后的修正已完成。",
                "findings": [],
                "reviewed_files": ["README.md"],
                "checked_items": ["scope", "tests", "recovery evidence"],
            }
        return RunnerResult(
            status="success",
            output=json.dumps(verdict, ensure_ascii=False),
            command=["iterating-reviewer"],
        )


def test_recovered_auto_worker_without_diff_requires_explicit_rerun(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()

    with pytest.raises(ValueError, match="--rerun-worker"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "02").exists()
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\n"
    )


def test_recovered_auto_worker_can_explicitly_rerun_same_child(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)

    resumed = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )

    state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    events = _read_jsonl(resumed / "trace.jsonl")

    assert resumed == run_dir
    assert state.status == "success"
    assert state.current_iteration == 2
    assert [item.iteration for item in state.iterations] == [1, 2]
    assert state.iterations[0].lifecycle == "interrupted"
    assert state.iterations[0].interrupted_step == "worker"
    assert state.iterations[1].worker_status == "success"
    assert state.iterations[1].verification_status == "passed"
    assert state.iterations[1].verdict == "approve"
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\ncompleted worker change\n"
    )
    assert state.worker_baseline_artifact_version == 2
    assert state.worker_baseline_iteration == 2
    assert state.worker_baseline_sha256
    assert len(state.worker_rerun_authorizations) == 1
    authorization = state.worker_rerun_authorizations[0]
    assert authorization.rerun_iteration == 2
    assert authorization.source_interrupted_iteration == 1
    assert authorization.recovery_id == state.last_recovery_id
    assert any(
        item.get("event") == "auto_worker_rerun_requested"
        and item.get("rerun_iteration") == 2
        and item.get("source_interrupted_iteration") == 1
        and item.get("source_worker_baseline_sha256")
        == authorization.source_worker_baseline_sha256
        for item in events
    )
    assert any(
        item.get("event") == "worker_started" and item.get("iteration") == 2
        for item in events
    )


def test_recovered_auto_worker_rerun_rejects_partial_tracked_work(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    with pytest.raises(RuntimeError, match="after tracked diff"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=PartialChangeCrashingWorker(),
        ).start(
            BriefInput(
                mode="bug",
                text="验证 partial tracked work 不被覆盖",
                source="worker-rerun-partial",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=2,
            verify=True,
        )
    run_dir = next((workspace / "runs").glob("*-loop"))
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "模拟 Worker 在形成 tracked diff 后中断",
    )
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()

    with pytest.raises(ValueError, match="partial work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "02").exists()
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\npartial worker change\n"
    )


def test_recovered_auto_worker_rerun_rejects_partial_untracked_work(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    with pytest.raises(RuntimeError, match="after untracked work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=UntrackedChangeCrashingWorker(),
        ).start(
            BriefInput(
                mode="bug",
                text="验证 untracked partial work 不被覆盖",
                source="worker-rerun-untracked-partial",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=2,
            verify=True,
        )
    run_dir = next((workspace / "runs").glob("*-loop"))
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "模拟 Worker 在形成 untracked 文件后中断",
    )
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()

    with pytest.raises(ValueError, match="partial work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "02").exists()
    assert repo.joinpath("partial-note.txt").read_text(encoding="utf-8") == (
        "partial worker output\n"
    )


def test_recovered_auto_worker_rerun_rejects_partial_ignored_work(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    with pytest.raises(RuntimeError, match="after ignored work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=IgnoredChangeCrashingWorker(),
        ).start(
            BriefInput(
                mode="bug",
                text="验证 ignored partial work 不被覆盖",
                source="worker-rerun-ignored-partial",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=2,
            verify=True,
        )
    run_dir = next((workspace / "runs").glob("*-loop"))
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "模拟 Worker 在形成 ignored 文件后中断",
    )
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()

    with pytest.raises(ValueError, match="partial work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "02").exists()
    assert repo.joinpath("ignored-partial.txt").read_text(encoding="utf-8") == (
        "ignored partial worker output\n"
    )


@pytest.mark.parametrize("damage_kind", ["missing", "tampered"])
def test_recovered_auto_worker_rerun_rejects_invalid_source_baseline(
    tmp_path: Path,
    damage_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    baseline_path = run_dir / "iterations" / "01" / "worker-baseline.json"
    if damage_kind == "missing":
        baseline_path.unlink()
    else:
        baseline_path.write_bytes(baseline_path.read_bytes() + b" ")
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()

    with pytest.raises(ValueError, match="partial work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "02").exists()


def test_recovered_auto_worker_rerun_rechecks_workspace_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()
    snapshot_workspace = (
        loop_continue_support_module.snapshot_worker_workspace
    )
    snapshot_calls = 0

    def change_workspace_before_start(*args, **kwargs):
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 2:
            repo.joinpath("README.md").write_text(
                "# recovery demo\nexternal change\n",
                encoding="utf-8",
                newline="\n",
            )
        return snapshot_workspace(*args, **kwargs)

    monkeypatch.setattr(
        loop_continue_support_module,
        "snapshot_worker_workspace",
        change_workspace_before_start,
    )
    monkeypatch.setattr(
        worker_rerun_planning_module,
        "snapshot_worker_workspace",
        change_workspace_before_start,
    )

    with pytest.raises(ValueError, match="Worker 启动前发生变化"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=TrackedChangeWorker(),
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "02").exists()
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\nexternal change\n"
    )


def test_recovered_later_worker_rerun_detects_same_path_content_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=IteratingWorker(),
        reviewer_runner=IteratingReviewer(),
    )
    with pytest.raises(RuntimeError, match="second worker crash"):
        runtime.start(
            BriefInput(
                mode="bug",
                text="验证后续 Worker 同路径内容变化不会逃逸",
                source="later-worker-rerun-content-change",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=3,
            verify=True,
        )
    run_dir = next((workspace / "runs").glob("*-loop"))
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "模拟第二轮 Worker 未产生新 diff 时中断",
    )
    state_before = run_dir.joinpath("state.json").read_bytes()
    trace_before = run_dir.joinpath("trace.jsonl").read_bytes()
    snapshot_workspace = (
        loop_continue_support_module.snapshot_worker_workspace
    )
    snapshot_calls = 0

    def change_same_path_before_start(*args, **kwargs):
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 2:
            repo.joinpath("README.md").write_text(
                "# recovery demo\nexternal replacement\n",
                encoding="utf-8",
                newline="\n",
            )
        return snapshot_workspace(*args, **kwargs)

    monkeypatch.setattr(
        loop_continue_support_module,
        "snapshot_worker_workspace",
        change_same_path_before_start,
    )
    monkeypatch.setattr(
        worker_rerun_planning_module,
        "snapshot_worker_workspace",
        change_same_path_before_start,
    )

    with pytest.raises(ValueError, match="Worker 启动前发生变化"):
        runtime.continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert run_dir.joinpath("trace.jsonl").read_bytes() == trace_before
    assert not run_dir.joinpath("iterations", "03").exists()
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\nexternal replacement\n"
    )


def test_recovered_later_worker_reuses_trusted_diff_and_previous_findings(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    worker = IteratingWorker()
    reviewer = IteratingReviewer()
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    with pytest.raises(RuntimeError, match="second worker crash"):
        runtime.start(
            BriefInput(
                mode="bug",
                text="验证后续 Worker 中断恢复",
                source="later-worker-rerun",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=3,
            verify=True,
        )
    run_dir = next((workspace / "runs").glob("*-loop"))
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "模拟第二轮 Worker 未产生新 diff 时中断",
    )

    resumed = runtime.continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )

    state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert state.status == "success"
    assert [item.iteration for item in state.iterations] == [1, 2, 3]
    assert state.iterations[0].verdict == "request_changes"
    assert state.iterations[1].lifecycle == "interrupted"
    assert state.iterations[2].verdict == "approve"
    assert "替换首轮占位内容" in worker.prompts[2]
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\nfinal worker change\n"
    )


def test_successful_worker_rerun_requires_authorization_trace_binding(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )
    state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert validate_loop_artifact_integrity(workspace, repo, run_dir).valid
    assert not any(
        item.startswith("FAIL:")
        for item in run_loop_eval(run_dir, state.artifacts)
    )

    trace_path = run_dir / "trace.jsonl"
    trace_items = [
        item
        for item in _read_jsonl(trace_path)
        if item.get("event") != "auto_worker_rerun_requested"
    ]
    trace_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in trace_items
        ),
        encoding="utf-8",
        newline="\n",
    )

    eval_results = run_loop_eval(run_dir, state.artifacts)
    integrity = validate_loop_artifact_integrity(workspace, repo, run_dir)

    assert any(
        "worker_rerun_authorization_trace_count_mismatch" in item
        for item in eval_results
    )
    assert not integrity.valid
    assert "worker_rerun_authorization_trace_count_mismatch" in integrity.issues


@pytest.mark.parametrize("damage_kind", ["missing", "duplicate"])
def test_worker_rerun_rejects_invalid_source_baseline_trace_before_start(
    tmp_path: Path,
    damage_kind: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    trace_path = run_dir / "trace.jsonl"
    items = _read_jsonl(trace_path)
    baseline = next(
        item
        for item in items
        if item.get("event") == "worker_baseline_captured"
        and item.get("iteration") == 1
    )
    if damage_kind == "missing":
        items.remove(baseline)
    else:
        items.append(dict(baseline))
    _write_jsonl(trace_path, items)
    worker = CountingTrackedChangeWorker()

    with pytest.raises(ValueError, match="baseline artifact 或 trace"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert worker.calls == 0
    assert not run_dir.joinpath("iterations", "02").exists()
    assert repo.joinpath("README.md").read_text(encoding="utf-8") == (
        "# recovery demo\n"
    )


@pytest.mark.parametrize(
    "index_flag",
    ["--assume-unchanged", "--skip-worktree"],
)
def test_worker_rerun_rejects_hidden_tracked_partial_work(
    tmp_path: Path,
    index_flag: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    _git(repo, "update-index", index_flag, "README.md")
    repo.joinpath("README.md").write_text(
        "# recovery demo\nhidden external change\n",
        encoding="utf-8",
        newline="\n",
    )
    worker = CountingTrackedChangeWorker()

    with pytest.raises(
        ValueError,
        match="assume-unchanged/skip-worktree",
    ):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert worker.calls == 0
    assert not run_dir.joinpath("iterations", "02").exists()
    assert "hidden external change" in repo.joinpath("README.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("mutation", ["content", "add", "delete"])
def test_worker_rerun_rejects_ignored_directory_descendant_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    repo.joinpath(".gitignore").write_text(
        "ignored-dir/\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "--", ".gitignore")
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "ignore fixture directory",
    )
    ignored_dir = repo / "ignored-dir"
    ignored_dir.mkdir()
    payload = ignored_dir / "payload.txt"
    payload.write_text("AAAA\n", encoding="utf-8", newline="\n")
    run_dir = _create_recovered_worker_crash(workspace, repo)
    if mutation == "content":
        payload.write_text("BBBB\n", encoding="utf-8", newline="\n")
    elif mutation == "add":
        ignored_dir.joinpath("added.txt").write_text(
            "added\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        payload.unlink()
    worker = CountingTrackedChangeWorker()

    with pytest.raises(ValueError, match="partial work"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert worker.calls == 0
    assert not run_dir.joinpath("iterations", "02").exists()


def test_worker_baseline_does_not_persist_sensitive_path_names(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    fake_secret = "sk-proj-VEGA-FAKE-SECRET"
    repo.joinpath(f"{fake_secret}.txt").write_text(
        "fixture\n",
        encoding="utf-8",
        newline="\n",
    )
    run_dir = _create_recovered_worker_crash(workspace, repo)

    leaked = [
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and fake_secret.encode("utf-8") in path.read_bytes()
    ]

    assert leaked == []
    baseline_text = run_dir.joinpath(
        "iterations",
        "01",
        "worker-baseline.json",
    ).read_text(encoding="utf-8")
    assert '"artifact_version": 2' in baseline_text
    assert "tracked_files" not in baseline_text
    assert "untracked_files" not in baseline_text


@pytest.mark.parametrize(
    "failure_stage",
    ["authorization_state", "authorization_trace", "iteration_claim"],
)
def test_worker_rerun_transaction_replays_after_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    worker = CountingTrackedChangeWorker()
    original_state_save = LoopAutomationState.save
    original_trace_write = TraceWriter.write
    crashed = False

    def flaky_state_save(state: LoopAutomationState, path: Path) -> None:
        nonlocal crashed
        should_crash = (
            failure_stage == "authorization_state"
            and state.status == "needs_human"
            and bool(state.worker_rerun_authorizations)
        ) or (
            failure_stage == "iteration_claim"
            and state.status == "running"
            and state.current_iteration == 2
            and bool(state.worker_rerun_authorizations)
        )
        if should_crash and not crashed:
            crashed = True
            raise RuntimeError(f"simulated {failure_stage} crash")
        original_state_save(state, path)

    def flaky_trace_write(
        writer: TraceWriter,
        event: str,
        **payload: object,
    ) -> None:
        nonlocal crashed
        if (
            failure_stage == "authorization_trace"
            and event == "auto_worker_rerun_requested"
            and not crashed
        ):
            crashed = True
            raise RuntimeError("simulated authorization_trace crash")
        original_trace_write(writer, event, **payload)

    monkeypatch.setattr(LoopAutomationState, "save", flaky_state_save)
    monkeypatch.setattr(TraceWriter, "write", flaky_trace_write)

    with pytest.raises(RuntimeError, match=failure_stage):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert worker.calls == 0
    monkeypatch.setattr(LoopAutomationState, "save", original_state_save)
    monkeypatch.setattr(TraceWriter, "write", original_trace_write)
    resumed = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )

    final_state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert final_state.status == "success"
    assert worker.calls == 1
    assert not run_dir.joinpath(
        ".control",
        "worker-rerun-transaction.json",
    ).exists()
    assert len(final_state.worker_rerun_authorizations) == 1


def test_worker_rerun_reuses_prepared_baseline_after_preclaim_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    worker = CountingTrackedChangeWorker()
    original_write_transaction = (
        worker_rerun_transaction_module._write_worker_rerun_transaction
    )
    crashed = False

    def crash_after_baseline_prepared(
        target_run_dir: Path,
        transaction: worker_rerun_transaction_module.WorkerRerunTransaction,
    ) -> None:
        nonlocal crashed
        if (
            transaction.rerun_worker_baseline_sha256 is not None
            and not crashed
        ):
            crashed = True
            raise RuntimeError("simulated baseline prepared crash")
        original_write_transaction(target_run_dir, transaction)

    monkeypatch.setattr(
        worker_rerun_transaction_module,
        "_write_worker_rerun_transaction",
        crash_after_baseline_prepared,
    )

    with pytest.raises(RuntimeError, match="baseline prepared"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    crashed_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert crashed_state.status == "needs_human"
    assert crashed_state.current_step == "recovered"
    assert crashed_state.current_iteration == 1
    assert worker.calls == 0
    assert run_dir.joinpath(
        "iterations",
        "02",
        "worker-baseline.json",
    ).is_file()

    monkeypatch.setattr(
        worker_rerun_transaction_module,
        "_write_worker_rerun_transaction",
        original_write_transaction,
    )
    resumed = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )

    final_state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert final_state.status == "success"
    assert worker.calls == 1
    assert not run_dir.joinpath(
        ".control",
        "worker-rerun-transaction.json",
    ).exists()


def test_worker_rerun_recovers_persisted_claim_before_worker_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    worker = CountingTrackedChangeWorker()
    original_state_save = LoopAutomationState.save
    crashed = False

    def crash_after_claim_persisted(
        state: LoopAutomationState,
        path: Path,
    ) -> None:
        nonlocal crashed
        should_crash = (
            state.status == "running"
            and state.current_iteration == 2
            and state.worker_baseline_iteration == 2
            and not crashed
        )
        original_state_save(state, path)
        if should_crash:
            crashed = True
            raise RuntimeError("simulated persisted claim crash")

    monkeypatch.setattr(
        LoopAutomationState,
        "save",
        crash_after_claim_persisted,
    )

    with pytest.raises(RuntimeError, match="persisted claim"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    claimed_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert claimed_state.status == "running"
    assert claimed_state.current_iteration == 2
    assert claimed_state.worker_baseline_iteration == 2
    assert worker.calls == 0

    monkeypatch.setattr(
        LoopAutomationState,
        "save",
        original_state_save,
    )
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "恢复 Worker 启动前已持久化的 iteration claim",
    )
    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert recovered_state.status == "needs_human"
    assert recovered_state.current_step == "recovered"
    assert recovered_state.current_iteration == 1
    assert [item.iteration for item in recovered_state.iterations] == [1]
    assert run_dir.joinpath(
        ".control",
        "worker-rerun-transaction.json",
    ).is_file()

    resumed = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )
    final_state = LoopAutomationState.model_validate_json(
        resumed.joinpath("state.json").read_text(encoding="utf-8")
    )
    events = _read_jsonl(resumed / "trace.jsonl")

    assert final_state.status == "success"
    assert worker.calls == 1
    assert len(
        [
            item
            for item in events
            if item.get("event") == "worker_started"
            and item.get("iteration") == 2
        ]
    ) == 1
    assert any(
        item.get("event") == "auto_worker_rerun_claim_recovered"
        and item.get("rerun_iteration") == 2
        for item in events
    )


def test_worker_rerun_recovery_clears_transaction_after_started_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    worker = CountingTrackedChangeWorker()
    original_trace_write = TraceWriter.write
    crashed = False

    def crash_after_worker_started(
        writer: TraceWriter,
        event: str,
        **payload: object,
    ) -> None:
        nonlocal crashed
        original_trace_write(writer, event, **payload)
        if (
            event == "worker_started"
            and payload.get("iteration") == 2
            and not crashed
        ):
            crashed = True
            raise RuntimeError("simulated worker_started crash")

    monkeypatch.setattr(TraceWriter, "write", crash_after_worker_started)

    with pytest.raises(RuntimeError, match="worker_started"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=worker,
            reviewer_runner=StaticReviewer(),
        ).continue_assist(
            run_dir.name,
            repo,
            verify=True,
            rerun_worker=True,
        )

    assert worker.calls == 0
    assert run_dir.joinpath(
        ".control",
        "worker-rerun-transaction.json",
    ).is_file()
    monkeypatch.setattr(TraceWriter, "write", original_trace_write)

    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "恢复已经写入 Worker 启动边界的重跑",
    )
    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    events = _read_jsonl(run_dir / "trace.jsonl")

    assert recovered_state.status == "needs_human"
    assert recovered_state.current_step == "recovered"
    assert recovered_state.current_iteration == 2
    assert recovered_state.iterations[-1].lifecycle == "interrupted"
    assert recovered_state.iterations[-1].interrupted_step == "worker"
    assert not run_dir.joinpath(
        ".control",
        "worker-rerun-transaction.json",
    ).exists()
    assert len(
        [
            item
            for item in events
            if item.get("event") == "worker_started"
            and item.get("iteration") == 2
        ]
    ) == 1


def test_worker_rerun_final_boundary_change_stops_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    snapshot_worker_workspace = (
        loop_continue_support_module.snapshot_worker_workspace
    )
    snapshot_calls = 0

    def change_at_final_boundary(*args, **kwargs):
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 4:
            repo.joinpath("README.md").write_text(
                "# recovery demo\nfinal boundary change\n",
                encoding="utf-8",
                newline="\n",
            )
        return snapshot_worker_workspace(*args, **kwargs)

    monkeypatch.setattr(
        loop_continue_support_module,
        "snapshot_worker_workspace",
        change_at_final_boundary,
    )
    monkeypatch.setattr(
        worker_rerun_planning_module,
        "snapshot_worker_workspace",
        change_at_final_boundary,
    )
    worker = CountingTrackedChangeWorker()

    result = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )

    state = LoopAutomationState.model_validate_json(
        result.joinpath("state.json").read_text(encoding="utf-8")
    )
    events = _read_jsonl(result / "trace.jsonl")
    assert state.status == "needs_human"
    assert state.current_step == "workspace_changed_before_worker"
    assert worker.calls == 0
    assert not [
        item
        for item in events
        if item.get("event") == "worker_started"
        and item.get("iteration") == 2
    ]


def test_worker_rerun_integrity_derives_missing_authorization_and_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, with_verification=True)
    run_dir = _create_recovered_worker_crash(workspace, repo)
    LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeWorker(),
        reviewer_runner=StaticReviewer(),
    ).continue_assist(
        run_dir.name,
        repo,
        verify=True,
        rerun_worker=True,
    )
    state_path = run_dir / "state.json"
    trace_path = run_dir / "trace.jsonl"
    state_bytes = state_path.read_bytes()
    trace_items = _read_jsonl(trace_path)

    state_payload = json.loads(state_bytes)
    state_payload["worker_rerun_authorizations"] = []
    state_without_authorization = LoopAutomationState.model_validate(
        state_payload
    )
    trace_without_request = [
        item
        for item in trace_items
        if item.get("event") != "auto_worker_rerun_requested"
    ]
    issues = worker_rerun_binding_issues(
        run_dir,
        state_without_authorization,
        trace_without_request,
    )
    assert "worker_rerun_02_authorization_missing" in issues
    assert "worker_rerun_02_request_trace_missing" in issues

    duplicated = [*trace_items]
    request = next(
        item
        for item in trace_items
        if item.get("event") == "auto_worker_rerun_requested"
    )
    duplicated.append(dict(request))
    state = LoopAutomationState.model_validate_json(state_bytes)
    duplicate_issues = worker_rerun_binding_issues(
        run_dir,
        state,
        duplicated,
    )
    assert "worker_rerun_authorization_trace_count_mismatch" in duplicate_issues

    reordered = [
        item
        for item in trace_items
        if item.get("event") != "auto_worker_rerun_requested"
    ]
    recovery_index = next(
        index
        for index, item in enumerate(reordered)
        if item.get("event") == "loop_recovered"
    )
    reordered.insert(recovery_index, request)
    order_issues = worker_rerun_binding_issues(
        run_dir,
        state,
        reordered,
    )
    assert "worker_rerun_02_causal_order_invalid" in order_issues


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


def test_recovery_after_baseline_capture_before_worker_prompt_rejects_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"

    def crash_before_worker_prompt(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("simulated crash after workspace baseline")

    monkeypatch.setattr(
        loop_runtime_module,
        "build_worker_prompt",
        crash_before_worker_prompt,
    )
    with pytest.raises(RuntimeError, match="after workspace baseline"):
        LoopAutomationRuntime(workspace).start(
            BriefInput(
                mode="bug",
                text="验证基线后初始化中断",
                source="baseline-initialization",
                repo_path=str(repo),
            ),
            "assist",
        )

    run_dir = next((workspace / "runs").glob("*-loop"))
    assert run_dir.joinpath("workspace-baseline.json").is_file()
    assert not run_dir.joinpath("worker-prompt.md").exists()

    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "owner exited after workspace baseline",
    )

    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert recovered_state.current_step == "recovered_initialization_incomplete"
    report = run_dir.joinpath("recovery-report.md").read_text(encoding="utf-8")
    assert "worker-prompt.md" in report
    with pytest.raises(ValueError, match="初始化未完成"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))


def test_recovery_after_baseline_state_save_before_trace_rejects_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    original_write = TraceWriter.write

    def crash_before_baseline_trace(
        writer: TraceWriter,
        event: str,
        **payload: object,
    ) -> None:
        if event == "workspace_baseline_captured":
            raise RuntimeError("simulated crash before workspace baseline trace")
        original_write(writer, event, **payload)

    monkeypatch.setattr(TraceWriter, "write", crash_before_baseline_trace)
    with pytest.raises(RuntimeError, match="before workspace baseline trace"):
        LoopAutomationRuntime(workspace).start(
            BriefInput(
                mode="bug",
                text="验证基线状态已保存但 trace 尚未写入的恢复边界",
                source="baseline-trace-window",
                repo_path=str(repo),
            ),
            "assist",
        )

    run_dir = next((workspace / "runs").glob("*-loop"))
    crashed_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert crashed_state.workspace_baseline_artifact_version == 1
    assert crashed_state.workspace_baseline_sha256
    assert run_dir.joinpath("workspace-baseline.json").is_file()
    assert not run_dir.joinpath("worker-prompt.md").exists()
    assert not any(
        item.get("event") == "workspace_baseline_captured"
        for item in _read_jsonl(run_dir / "trace.jsonl")
    )

    monkeypatch.setattr(TraceWriter, "write", original_write)
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "owner exited before workspace baseline trace",
    )

    recovered_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert recovered_state.current_step == "recovered_initialization_incomplete"
    report = run_dir.joinpath("recovery-report.md").read_text(encoding="utf-8")
    assert "workspace_baseline_trace_event_count_invalid" in report
    with pytest.raises(ValueError, match="初始化未完成"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))


@pytest.mark.parametrize(
    "damage_kind",
    [
        "workspace_baseline_event_missing",
        "loop_initialized_event_missing",
        "workspace_baseline_artifact_missing",
    ],
)
def test_status_rejects_modern_assist_initialization_damage(
    tmp_path: Path,
    damage_kind: str,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    run_dir = LoopAutomationRuntime(workspace).start(
        BriefInput(
            mode="bug",
            text="验证现代 assist 初始化证据损坏时不建议 continue",
            source="modern-assist-initialization-damage",
            repo_path=str(repo),
        ),
        "assist",
    )

    if damage_kind == "workspace_baseline_artifact_missing":
        run_dir.joinpath("workspace-baseline.json").unlink()
    else:
        removed_event = {
            "workspace_baseline_event_missing": "workspace_baseline_captured",
            "loop_initialized_event_missing": "loop_initialized",
        }[damage_kind]
        trace_path = run_dir / "trace.jsonl"
        trace_items = [
            item
            for item in _read_jsonl(trace_path)
            if item.get("event") != removed_event
        ]
        trace_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n"
                for item in trace_items
            ),
            encoding="utf-8",
            newline="\n",
        )

    status = run_status_payload(workspace, run_dir.name)
    assert status["current_step"] == "initialization_evidence_unavailable"
    assert not any("loop continue" in item for item in status["next_steps"])
    with pytest.raises(ValueError, match="初始化未完成"):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))


def test_recovery_classifies_legacy_assist_run_as_view_only(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    workspace = tmp_path / "workspace"
    run_dir = LoopAutomationRuntime(workspace).start(
        BriefInput(
            mode="bug",
            text="验证旧版 assist 初始化协议只读兼容",
            source="legacy-assist-initialization",
            repo_path=str(repo),
        ),
        "assist",
    )

    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    state.pop("workspace_baseline_artifact_version")
    state.pop("workspace_baseline_sha256")
    state["current_step"] = "waiting_for_worker"
    state["artifacts"] = [
        item
        for item in state["artifacts"]
        if item != "workspace-baseline.json"
    ]
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    run_dir.joinpath("workspace-baseline.json").unlink()
    legacy_trace_with_baseline = _read_jsonl(run_dir / "trace.jsonl")
    assert not is_legacy_assist_initialization_unavailable(
        run_dir,
        LoopAutomationState.model_validate(state),
        legacy_trace_with_baseline,
    )
    legacy_trace: list[dict[str, object]] = []
    legacy_initialization_artifacts = [
        "agent-brief.md",
        "project-context.md",
        "project-policy-snapshot.json",
        "loop-plan.md",
        "worker-prompt.md",
        "worker-prompt-metrics.json",
        "worker-prompt-metrics.md",
    ]
    for item in legacy_trace_with_baseline:
        if item.get("event") == "workspace_baseline_captured":
            continue
        if item.get("event") == "loop_initialized":
            item["artifacts"] = legacy_initialization_artifacts
        legacy_trace.append(item)
    legacy_trace_text = "".join(
        json.dumps(item, ensure_ascii=False) + "\n"
        for item in legacy_trace
    )
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text(
        legacy_trace_text,
        encoding="utf-8",
        newline="\n",
    )

    incomplete_legacy_trace = [
        item
        for item in legacy_trace
        if item.get("event") != "loop_initialized"
    ]
    trace_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in incomplete_legacy_trace
        ),
        encoding="utf-8",
        newline="\n",
    )
    incomplete_legacy_status = run_status_payload(workspace, run_dir.name)
    assert (
        incomplete_legacy_status["current_step"]
        == "initialization_evidence_unavailable"
    )
    assert not any(
        "loop continue" in item
        for item in incomplete_legacy_status["next_steps"]
    )

    trace_path.write_text("{invalid-json\n", encoding="utf-8", newline="\n")
    invalid_trace_status = run_status_payload(workspace, run_dir.name)
    assert invalid_trace_status["current_step"] == "initialization_trace_unavailable"
    assert not any(
        "loop continue" in item
        for item in invalid_trace_status["next_steps"]
    )
    trace_path.write_text(legacy_trace_text, encoding="utf-8", newline="\n")

    metrics_path = run_dir / "worker-prompt-metrics.json"
    original_metrics = metrics_path.read_text(encoding="utf-8")
    metrics = json.loads(original_metrics)
    metrics["chars"] += 1
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    mixed_damage_status = run_status_payload(workspace, run_dir.name)
    assert mixed_damage_status["current_step"] == "initialization_evidence_unavailable"
    assert not any(
        "loop continue" in item
        for item in mixed_damage_status["next_steps"]
    )
    metrics_path.write_text(
        original_metrics,
        encoding="utf-8",
        newline="\n",
    )

    status = run_status_payload(workspace, run_dir.name)
    assert status["current_step"] == "legacy_workspace_baseline_unavailable"
    assert not any("loop continue" in item for item in status["next_steps"])
    with pytest.raises(
        ValueError,
        match="legacy_workspace_baseline_unavailable",
    ):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))

    state["status"] = "running"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "owner exited from legacy assist run",
    )

    recovered_state = LoopAutomationState.model_validate_json(
        state_path.read_text(encoding="utf-8")
    )
    assert (
        recovered_state.current_step
        == "legacy_workspace_baseline_unavailable"
    )
    report = run_dir.joinpath("recovery-report.md").read_text(encoding="utf-8")
    assert "legacy_workspace_baseline_unavailable" in report
    with pytest.raises(
        ValueError,
        match="legacy_workspace_baseline_unavailable",
    ):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))
    recovered_trace = _read_jsonl(run_dir / "trace.jsonl")
    recovered_event = next(
        item
        for item in recovered_trace
        if item.get("event") == "loop_recovered"
    )
    recovered_event["recovery_id"] = "tampered-recovery-id"
    run_dir.joinpath("trace.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in recovered_trace
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(
        ValueError,
        match="loop_recovered 与 state.last_recovery_id 不一致",
    ):
        LoopAutomationRuntime(workspace).continue_assist(
            run_dir.name,
            repo,
            verify=False,
        )
    assert not list(run_dir.glob("iterations/*"))


def test_legacy_recovery_step_does_not_hide_additional_initialization_damage(
) -> None:
    assert recovered_initialization_step(
        [
            "legacy_workspace_baseline_unavailable",
            "worker_prompt_metrics_mismatch",
        ]
    ) == "recovered_initialization_incomplete"


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


def _create_recovered_worker_crash(workspace: Path, repo: Path) -> Path:
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        LoopAutomationRuntime(
            workspace,
            worker_runner=CrashingWorker(),
        ).start(
            BriefInput(
                mode="bug",
                text="验证 Worker 中断后显式重跑",
                source="worker-rerun-recovery",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=2,
            verify=True,
        )
    run_dir = next((workspace / "runs").glob("*-loop"))
    RecoveryRuntime(workspace).recover_loop(
        run_dir.name,
        "模拟 Worker 在形成 tracked diff 前中断",
    )
    recovered = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert recovered.status == "needs_human"
    assert recovered.current_step == "recovered"
    assert recovered.current_iteration == 1
    assert recovered.iterations[0].interrupted_step == "worker"
    return run_dir


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
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


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, items: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in items
        ),
        encoding="utf-8",
        newline="\n",
    )


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
