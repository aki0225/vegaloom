from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeSideEffectPolicy,
    ExecutionWorkItem,
)
from vega.agent_planning import (
    PlanningContractProposal,
    PlanningExecutionPlan,
    PlanningObservedFact,
    PlanningProposal,
    PlanningSourceRef,
    validate_planning_proposal,
)
from vega.agent_planning_runtime import PlanningProposalRunner
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_task_card import load_task_card
from vega.cli_entrypoint import app
from vega.execution_control import ExecutionController, run_owned_process
from vega.runner import RunnerResult


def test_planning_proposal_binds_source_refs_to_fixed_revision(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    proposal = _proposal(repo, task_id="task-1", goal="修复示例函数")

    validate_planning_proposal(
        repo,
        proposal,
        task_id="task-1",
        user_goal="修复示例函数",
        source_revision=_git(repo, "rev-parse", "HEAD"),
    )
    test_ref = PlanningSourceRef(
        kind="test",
        path="tests/test_service.py",
        symbol="test_handles_blank_name",
        summary="测试函数直接覆盖目标行为",
    )
    assert test_ref.symbol == "test_handles_blank_name"
    normalized_goal = proposal.model_copy(deep=True)
    normalized_goal.contract_proposal.goal = "修复函数行为"
    validate_planning_proposal(
        repo,
        normalized_goal,
        task_id="task-1",
        user_goal="修复示例函数",
        source_revision=_git(repo, "rev-parse", "HEAD"),
    )

    stale = proposal.model_copy(deep=True)
    stale.observed_facts[0].refs[0].line_end = 99
    with pytest.raises(ValueError, match="行号越过文件范围"):
        validate_planning_proposal(
            repo,
            stale,
            task_id="task-1",
            user_goal="修复示例函数",
            source_revision=_git(repo, "rev-parse", "HEAD"),
        )


def test_agent_start_text_creates_clean_planning_change_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    source_head = _git(repo, "rev-parse", "HEAD")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        ["start", "--repo", str(repo), "--text", "修复示例函数"],
    )

    assert result.exit_code == 0, result.output
    assert "Planning ChangeRun 已创建" in result.output
    [run_dir] = list((workspace / "runs").iterdir())
    state = json.loads(
        (run_dir / "agent-state.json").read_text(encoding="utf-8")
    )["data"]
    metadata = json.loads(
        (run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    assert state["phase"] == "planning"
    assert state["run_kind"] == "change"
    assert state["contract_revision"] is None
    assert state["accepted_checkpoint_sha"] == source_head
    assert _git(managed_repo, "status", "--short") == ""
    assert not (run_dir / "change-contract.json").exists()
    assert not (run_dir / "execution-plan.json").exists()


def test_planning_runner_publishes_proposal_without_starting_worker(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="修复示例函数",
    )
    proposal = _proposal_for_run(started.run_dir)

    result = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(proposal.model_dump_json()),
    ).run(started.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "planning"
    assert result.state.active_child_run is None
    assert result.state.active_candidate_sha is None
    assert (result.run_dir / "planning-proposal.json").is_file()
    assert (result.run_dir / "planning-proposal.md").is_file()
    assert not (result.run_dir / "change-contract.json").exists()
    status = SupervisorAgentRuntime(workspace).status(result.run_dir.name)
    assert "Planning Proposal 已生成" in status


def test_planning_runner_rejects_incomplete_published_proposal(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="修复示例函数",
    )
    proposal = _proposal_for_run(started.run_dir)
    runner = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(proposal.model_dump_json()),
    )
    published = runner.run(started.run_dir.name, timeout_seconds=60)
    (published.run_dir / "planning-proposal.md").write_text(
        "损坏的报告\n",
        encoding="utf-8",
    )

    result = runner.run(started.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    assert "报告与结构化 Artifact 不一致" in SupervisorAgentRuntime(
        workspace
    ).status(result.run_dir.name)


def test_planning_runner_fails_closed_when_read_only_workspace_changes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="修复示例函数",
    )
    proposal = _proposal_for_run(started.run_dir)

    result = PlanningProposalRunner(
        workspace,
        runner=_WritingRunner(proposal.model_dump_json()),
    ).run(started.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    assert result.state.allowed_actions == ["human"]
    assert not (result.run_dir / "planning-proposal.json").exists()
    status = SupervisorAgentRuntime(workspace).status(result.run_dir.name)
    assert "只读 Planning Turn 改变了 Workspace" in status
    with pytest.raises(ValueError, match="Planning ChangeRun 不能恢复为 ready"):
        SupervisorAgentRecovery(workspace).resume_local(result.run_dir.name)


@pytest.mark.parametrize(
    ("output", "status", "error", "expected"),
    [
        ("{}", "success", None, "Planning Proposal 无效"),
        ("", "timed_out", "Provider timeout", "Provider timeout"),
    ],
)
def test_planning_runner_keeps_retryable_failures_in_planning(
    tmp_path: Path,
    output: str,
    status: str,
    error: str | None,
    expected: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="修复示例函数",
    )

    result = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(output, status=status, error=error),
    ).run(
        started.run_dir.name,
        timeout_seconds=60,
    )

    assert result.state.phase == "planning"
    assert not (result.run_dir / "planning-proposal.json").exists()
    status = SupervisorAgentRuntime(workspace).status(result.run_dir.name)
    assert expected in status


def test_planning_stop_terminates_bound_owned_process(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="调查一个需要等待的任务",
    )
    holder: dict[str, object] = {}
    runner = _OwnedBlockingRunner()

    def run_planner() -> None:
        holder["result"] = PlanningProposalRunner(
            workspace,
            runner=runner,
        ).run(started.run_dir.name, timeout_seconds=60)

    thread = threading.Thread(target=run_planner, daemon=True)
    thread.start()
    state_path = started.run_dir / "agent-state.json"
    deadline = time.monotonic() + 10
    execution_id = None
    while time.monotonic() < deadline:
        state = json.loads(state_path.read_text(encoding="utf-8"))["data"]
        execution_id = state.get("active_planning_execution_id")
        if execution_id and runner.entered.is_set():
            break
        time.sleep(0.05)
    assert execution_id
    execution_dir = (
        started.run_dir / "executions" / "planning" / execution_id
    )
    reservation = json.loads(
        (execution_dir / "execution.json").read_text(encoding="utf-8")
    )
    assert reservation["status"] == "starting"
    assert reservation["child_pid"] is None

    with pytest.raises(ValueError, match="当前 Planning Turn 仍在运行"):
        PlanningProposalRunner(
            workspace,
            runner=_FailIfCalledRunner(),
        ).run(started.run_dir.name, timeout_seconds=60)
    still_bound = json.loads(state_path.read_text(encoding="utf-8"))["data"]
    assert still_bound["active_planning_execution_id"] == execution_id

    requested = SupervisorAgentRecovery(workspace).stop(
        started.run_dir.name,
        reason="测试主动停止 Planning",
    )

    assert requested.state.active_planning_execution_id == execution_id
    assert (execution_dir / "stop-request.json").is_file()
    runner.release.set()
    thread.join(timeout=15)
    assert not thread.is_alive()
    result = holder["result"]
    assert result.state.phase == "stopped"  # type: ignore[union-attr]
    assert result.state.active_planning_execution_id is None  # type: ignore[union-attr]


def test_planning_stop_discards_racing_success_result(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="调查一个很快结束的任务",
    )
    proposal = _proposal_for_run(started.run_dir)
    runner = _BlockingSuccessRunner(proposal.model_dump_json())
    holder: dict[str, object] = {}

    def run_planner() -> None:
        holder["result"] = PlanningProposalRunner(
            workspace,
            runner=runner,
        ).run(started.run_dir.name, timeout_seconds=60)

    thread = threading.Thread(target=run_planner, daemon=True)
    thread.start()
    assert runner.entered.wait(timeout=10)
    requested = SupervisorAgentRecovery(workspace).stop(
        started.run_dir.name,
        reason="成功结果发布前停止 Planning",
    )
    assert requested.state.active_planning_execution_id is not None

    runner.release.set()
    thread.join(timeout=15)
    assert not thread.is_alive()
    result = holder["result"]
    assert result.state.phase == "stopped"  # type: ignore[union-attr]
    assert not (result.run_dir / "planning-proposal.json").exists()  # type: ignore[union-attr]


def test_planning_rejects_success_while_bound_process_is_still_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="调查一个返回过早的 Provider",
    )
    proposal = _proposal_for_run(started.run_dir)
    runner = _LeakingSuccessRunner(proposal.model_dump_json())

    try:
        result = PlanningProposalRunner(
            workspace,
            runner=runner,
        ).run(started.run_dir.name, timeout_seconds=60)

        assert result.state.phase == "needs_human"
        assert result.state.active_planning_execution_id is not None
        assert not (result.run_dir / "planning-proposal.json").exists()
        assert "Planning execution 终态无法确认" in (
            result.run_dir / "status-card.md"
        ).read_text(encoding="utf-8")
    finally:
        runner.close()

    execution_id = result.state.active_planning_execution_id
    assert execution_id is not None
    execution_path = (
        started.run_dir
        / "executions"
        / "planning"
        / execution_id
        / "execution.json"
    )
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    # CliRunner 与原调用共用 pytest 进程；改成已退出的 owner，模拟原 CLI 终止后的恢复。
    execution["owner_pid"] = 2_147_483_647
    execution["owner_creation_token"] = None
    execution["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    execution["deadline"] = "2000-01-01T00:00:00+00:00"
    execution_path.write_text(
        json.dumps(execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "vega.agent_start_cli.ensure_runner_ready",
        lambda *_args, **_kwargs: None,
    )
    reconciled = CliRunner().invoke(
        app,
        ["run", "--run", started.run_dir.name, "--timeout", "60"],
    )
    assert reconciled.exit_code == 0, reconciled.output
    current = json.loads(
        (started.run_dir / "agent-state.json").read_text(encoding="utf-8")
    )["data"]
    assert current["phase"] == "planning"
    assert current["active_planning_execution_id"] is None


def test_planning_runner_exception_keeps_live_execution_bound(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="调查一个异常退出的 Provider",
    )
    runner = _LeakingExceptionRunner()

    try:
        result = PlanningProposalRunner(
            workspace,
            runner=runner,
        ).run(started.run_dir.name, timeout_seconds=60)

        assert result.state.phase == "needs_human"
        assert result.state.active_planning_execution_id is not None
        assert not (result.run_dir / "planning-proposal.json").exists()
    finally:
        runner.close()


def test_planning_partial_publication_recovers_without_second_runner_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vega.agent_planning_publication as planning_publication_module

    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="修复示例函数",
    )
    proposal = _proposal_for_run(started.run_dir)
    runner = _CountingRunner(proposal.model_dump_json())
    original = planning_publication_module.write_redacted_text
    failed = False

    def fail_once(path: Path, text: str) -> None:
        nonlocal failed
        if path.name == "planning-proposal.md" and not failed:
            failed = True
            raise OSError("simulated report failure")
        original(path, text)

    monkeypatch.setattr(
        planning_publication_module,
        "write_redacted_text",
        fail_once,
    )
    runtime = PlanningProposalRunner(workspace, runner=runner)

    interrupted = runtime.run(started.run_dir.name, timeout_seconds=60)
    recovered = runtime.run(started.run_dir.name, timeout_seconds=60)

    assert interrupted.state.phase == "planning"
    assert interrupted.state.active_planning_execution_id is None
    assert recovered.state.phase == "planning"
    assert runner.calls == 1
    assert (recovered.run_dir / "planning-proposal.md").is_file()


def test_planning_committed_publication_repairs_status_without_second_runner_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vega.agent_planning_publication as planning_publication_module

    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started = SupervisorAgentRuntime(workspace).start_planning(
        repo,
        goal="修复示例函数",
    )
    proposal = _proposal_for_run(started.run_dir)
    runner = _CountingRunner(proposal.model_dump_json())
    original = planning_publication_module.write_status_card
    failed = False

    def fail_once(*args, **kwargs) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated status failure")
        original(*args, **kwargs)

    monkeypatch.setattr(
        planning_publication_module,
        "write_status_card",
        fail_once,
    )

    result = PlanningProposalRunner(workspace, runner=runner).run(
        started.run_dir.name,
        timeout_seconds=60,
    )

    assert result.state.phase == "planning"
    assert result.state.active_planning_execution_id is None
    assert runner.calls == 1
    assert "Planning Proposal 已生成" in (
        result.run_dir / "status-card.md"
    ).read_text(encoding="utf-8")


def test_planning_proposal_handoff_resumes_on_isolated_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_planning(repo, goal="修复示例函数")
    proposal = _proposal_for_run(started.run_dir)
    published = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(proposal.model_dump_json()),
    ).run(started.run_dir.name, timeout_seconds=60)
    stopped = SupervisorAgentRecovery(workspace).stop(
        published.run_dir.name,
        reason="已生成 Proposal，准备换机继续",
    )
    monkeypatch.chdir(workspace)
    watch = CliRunner().invoke(
        app,
        ["watch", "--run", stopped.run_dir.name, "--no-follow"],
    )
    assert watch.exit_code == 0, watch.output
    assert "agent / 开始只读调查" in watch.output
    assert "agent / Planning Proposal 已生成" in watch.output
    assert "agent / Planning 已停止" in watch.output
    handoff = runtime.handoff(
        stopped.run_dir.name,
        reason="换机继续编译 Planning Proposal",
    )
    card = load_task_card(handoff.task_card_path)
    metadata = json.loads(
        (published.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    task_path = handoff.task_card_path.relative_to(managed_repo).as_posix()
    _git(managed_repo, "add", task_path)
    _git(managed_repo, "commit", "-m", "测试：提交 Planning Handoff")

    clone = tmp_path / "clone"
    _git(
        tmp_path,
        "clone",
        "--branch",
        card.branch,
        str(repo),
        str(clone),
    )
    next_workspace = tmp_path / "next-workspace"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(clone)
    no_call = _FailIfCalledRunner()
    resumed = PlanningProposalRunner(
        next_workspace,
        runner=no_call,
    ).run(restored.run_dir.name, timeout_seconds=60)

    assert restored.state.phase == "planning"
    assert restored.state.accepted_checkpoint_sha != proposal.source_revision
    assert (restored.run_dir / "planning-proposal.json").is_file()
    assert resumed.run_dir == restored.run_dir
    assert no_call.calls == 0


def test_blocked_planning_handoff_still_restores_for_human_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vega.agent_handoff as handoff_module

    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_planning(repo, goal="修复示例函数")
    proposal = _proposal_for_run(started.run_dir)
    published = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(proposal.model_dump_json()),
    ).run(started.run_dir.name, timeout_seconds=60)
    monkeypatch.setattr(
        handoff_module,
        "collect_handoff_issues",
        lambda *_: ["现场需要人工核对"],
    )
    handoff = runtime.handoff(published.run_dir.name, reason="换机人工核对")
    card = load_task_card(handoff.task_card_path)
    metadata = json.loads(
        (published.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    task_path = handoff.task_card_path.relative_to(managed_repo).as_posix()
    _git(managed_repo, "add", task_path)
    _git(managed_repo, "commit", "-m", "测试：提交 blocked Planning Handoff")

    clone = tmp_path / "clone"
    _git(
        tmp_path,
        "clone",
        "--branch",
        card.branch,
        str(repo),
        str(clone),
    )
    next_workspace = tmp_path / "next-workspace"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(clone)

    assert card.status == "planning"
    assert restored.state.phase == "needs_human"
    assert restored.state.allowed_actions == ["human"]
    assert "Planning Handoff 仍需人工核对" in (
        restored.run_dir / "status-card.md"
    ).read_text(encoding="utf-8")


class _StaticRunner:
    def __init__(
        self,
        output: str,
        *,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        self.output = output
        self.status = status
        self.error = error

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        assert prompt
        assert repo_path.is_dir()
        assert sandbox == "read-only"
        assert timeout_seconds == 60
        return RunnerResult(
            status=self.status,  # type: ignore[arg-type]
            output=self.output,
            error=self.error,
        )


class _WritingRunner(_StaticRunner):
    def run(self, prompt: str, repo_path: Path, **kwargs) -> RunnerResult:
        (repo_path / "src" / "example.py").write_text(
            "def value():\n    return 2\n",
            encoding="utf-8",
        )
        return super().run(prompt, repo_path, **kwargs)


class _CountingRunner(_StaticRunner):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.calls = 0

    def run(self, prompt: str, repo_path: Path, **kwargs) -> RunnerResult:
        self.calls += 1
        return super().run(prompt, repo_path, **kwargs)


class _FailIfCalledRunner(_StaticRunner):
    def __init__(self) -> None:
        super().__init__("{}")
        self.calls = 0

    def run(self, prompt: str, repo_path: Path, **kwargs) -> RunnerResult:
        self.calls += 1
        raise AssertionError("已恢复的 Proposal 不应重新调用 Planner")


class _OwnedBlockingRunner:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        assert prompt
        assert sandbox == "read-only"
        assert execution_context is not None
        self.entered.set()
        assert self.release.wait(timeout=10)
        owned = run_owned_process(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ],
            "",
            repo_path,
            timeout_seconds,
            execution_context,
        )
        return RunnerResult(
            status=owned.status,
            output=owned.output,
            error=owned.error,
            termination_unconfirmed=owned.termination_unconfirmed,
        )


class _BlockingSuccessRunner(_StaticRunner):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, prompt: str, repo_path: Path, **kwargs) -> RunnerResult:
        self.entered.set()
        assert self.release.wait(timeout=10)
        return super().run(prompt, repo_path, **kwargs)


class _LeakingSuccessRunner(_StaticRunner):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.process: subprocess.Popen[bytes] | None = None

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        assert prompt
        assert sandbox == "read-only"
        assert execution_context is not None
        controller = ExecutionController(execution_context)
        controller.prepare(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds,
        )
        self.process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=repo_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        controller.child_started(self.process.pid)
        return RunnerResult(status="success", output=self.output)

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        self.process.wait(timeout=10)


class _LeakingExceptionRunner(_LeakingSuccessRunner):
    def __init__(self) -> None:
        super().__init__("{}")

    def run(self, prompt: str, repo_path: Path, **kwargs) -> RunnerResult:
        super().run(prompt, repo_path, **kwargs)
        raise RuntimeError("simulated provider crash")


def _proposal_for_run(run_dir: Path) -> PlanningProposal:
    request = json.loads(
        (run_dir / "planning-request.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    return _proposal(
        Path(metadata["repo_path"]),
        task_id=request["task_id"],
        goal=request["user_goal"],
    )


def _proposal(repo: Path, *, task_id: str, goal: str) -> PlanningProposal:
    return PlanningProposal(
        task_id=task_id,
        user_goal=goal,
        source_revision=_git(repo, "rev-parse", "HEAD"),
        observed_facts=[
            PlanningObservedFact(
                statement="示例函数当前返回 1",
                refs=[
                    PlanningSourceRef(
                        kind="symbol",
                        path="src/example.py",
                        line_start=1,
                        line_end=2,
                        symbol="value",
                        summary="当前实现",
                    )
                ],
            )
        ],
        hypotheses=["返回值与预期不一致"],
        unresolved_questions=[],
        contract_proposal=PlanningContractProposal(
            goal=goal,
            acceptance=["示例函数返回 2"],
            non_goals=["不修改无关模块"],
            side_effect_policy=ChangeSideEffectPolicy(),
            verification_suggestions=["python -m pytest -q"],
            authority_envelope=ChangeAuthorityEnvelope(
                allowed_paths=["src/**", "tests/**"],
                forbidden_paths=[],
                max_changed_files=2,
            ),
        ),
        execution_plan=PlanningExecutionPlan(
            work_items=[
                ExecutionWorkItem(
                    work_item_id="WI-01",
                    objective="修复示例函数并补回归测试",
                    likely_files=["src/example.py", "tests/test_example.py"],
                    verification=["python -m pytest -q"],
                )
            ],
            implementation_strategy=["先复现，再做最小修复"],
        ),
    )


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Vega Test")
    (path / "src").mkdir()
    (path / "tests").mkdir()
    (path / "src" / "example.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_example.py").write_text(
        "from src.example import value\n\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")
    return path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()
