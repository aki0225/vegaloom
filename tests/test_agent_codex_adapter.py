from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega import agent_finalization as agent_finalization_module
from vega.agent_codex_adapter import SupervisorAgentCodexAdapter
from vega.agent_codex_evidence import _verification_status
from vega.agent_contract import AgentPlan, AgentWorkItem, approve_plan
from vega.agent_persistence import (
    append_agent_trace,
    load_agent_state,
    read_agent_trace,
)
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_task_card import (
    AgentTaskCard,
    ResumeCapsule,
    compute_handoff_workspace_digest,
    save_task_card,
)
from vega.execution_control import ExecutionController, RunnerExecutionContext
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput, LoopAutomationState, LoopIterationState
from vega.project_config import ProjectConfig
from vega.runner import CodexExecRunner, RunnerResult
from vega.workspace_inventory import prepare_verification_temp_root


class _FakeLoopRuntime:
    def __init__(
        self,
        workspace: Path,
        *,
        finish_status: str = "ready_to_commit",
        core_mutation_relative: str | None = None,
        prepare_runtime_root: bool = False,
    ) -> None:
        self.workspace = workspace
        self.finish_status = finish_status
        self.core_mutation_relative = core_mutation_relative
        self.prepare_runtime_root = prepare_runtime_root
        self.continued = False
        self.start_count = 0
        self.continue_count = 0
        self.child_dir: Path | None = None
        self.verification_commands: list[str] | None = None
        self.comparison_base_sha: str | None = None
        self.comparison_paths: tuple[str, ...] = ()

    def start(
        self,
        brief_input: BriefInput,
        automation_mode: str,
        worker_name: str = "codex-exec",
        reviewer_name: str = "codex-exec",
        max_iterations: int = 2,
        verify: bool = True,
        on_run_created=None,
        comparison_base_sha: str | None = None,
        comparison_paths: tuple[str, ...] = (),
    ) -> Path:
        del worker_name, reviewer_name, max_iterations, verify, on_run_created
        assert automation_mode == "assist"
        if self.prepare_runtime_root:
            prepare_verification_temp_root(Path(brief_input.repo_path))
        self.start_count += 1
        self.comparison_base_sha = comparison_base_sha
        self.comparison_paths = comparison_paths
        child_dir = self.workspace / "runs" / "fake-assist-child"
        child_dir.mkdir(parents=True)
        LoopAutomationState(
            run_id=child_dir.name,
            status="needs_human",
            task_mode=brief_input.mode,
            automation_mode="assist",
            repo_path=brief_input.repo_path,
            input_source=brief_input.source,
            current_step="waiting_for_worker",
        ).save(child_dir / "state.json")
        (child_dir / "worker-prompt.md").write_text(
            "根据 Task Brief 完成当前修改。\n",
            encoding="utf-8",
        )
        self.child_dir = child_dir
        return child_dir

    def continue_assist(
        self,
        run: str,
        repo_path: Path,
        worker_name: str = "codex-exec",
        reviewer_name: str = "codex-exec",
        test_log: Path | None = None,
        note: str | None = None,
        verify: bool = True,
        rerun_worker: bool = False,
        verification_commands: list[str] | None = None,
    ) -> Path:
        del (
            worker_name,
            reviewer_name,
            test_log,
            note,
            verify,
            rerun_worker,
        )
        self.verification_commands = verification_commands
        assert self.child_dir is not None
        assert run == self.child_dir.name
        self.continued = True
        self.continue_count += 1
        if self.core_mutation_relative is not None:
            target = repo_path / self.core_mutation_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("core mutation\n", encoding="utf-8")
        state = LoopAutomationState.model_validate_json(
            (self.child_dir / "state.json").read_text(encoding="utf-8")
        )
        if self.finish_status == "ready_to_commit":
            state.status = "success"
            state.current_step = "completed"
            state.iterations = [
                LoopIterationState(
                    iteration=1,
                    workspace_status="passed",
                    scope_gate_status="success",
                    scope_gate_post_verification_status="success",
                    scope_gate_pre_review_status="success",
                    verification_status="passed",
                    risk_gate_status="success",
                    risk_gate_risk="low",
                    risk_gate_recommendation="isolated-review",
                    reviewer_status="success",
                    verdict="approve",
                )
            ]
        else:
            state.status = "needs_human"
            state.current_step = "review_complete"
            state.iterations = [
                LoopIterationState(
                    iteration=1,
                    workspace_status="passed",
                    scope_gate_status="success",
                    scope_gate_post_verification_status="success",
                    scope_gate_pre_review_status="success",
                    verification_status="passed",
                    risk_gate_status="success",
                    risk_gate_risk="low",
                    risk_gate_recommendation="isolated-review",
                    reviewer_status="success",
                    verdict="request_changes",
                )
            ]
            fix_prompt = self.child_dir / "iterations" / "iteration-01" / "fix-prompt.md"
            fix_prompt.parent.mkdir(parents=True, exist_ok=True)
            fix_prompt.write_text(
                "根据 Reviewer finding 修复当前问题。\n",
                encoding="utf-8",
            )
        state.save(self.child_dir / "state.json")
        return self.child_dir


class _FakeFinishRuntime:
    def __init__(
        self,
        loop: _FakeLoopRuntime,
        *,
        evidence_trusted: bool = True,
    ) -> None:
        self.loop = loop
        self.evidence_trusted = evidence_trusted

    def run(self, run: str) -> Path:
        assert self.loop.child_dir is not None
        assert run == self.loop.child_dir.name
        ready = self.loop.finish_status == "ready_to_commit"
        (self.loop.child_dir / "finish-summary.json").write_text(
            json.dumps(
                {
                    "run_id": run,
                    "finish_status": self.loop.finish_status,
                    "verification_passed": True,
                    "latest_verification_failed": False,
                    "artifact_integrity": {"valid": self.evidence_trusted},
                    "evidence_freshness": {"fresh": self.evidence_trusted},
                    "latest_verdict": {
                        "verdict": "approve" if ready else "request_changes"
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return self.loop.child_dir


def test_adapter_configures_mcp_isolated_default_reviewer(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = SupervisorAgentCodexAdapter(workspace)

    adapter._ensure_isolated_reviewer(ProjectConfig())

    reviewer = adapter.loop_runtime.reviewer_runner
    assert isinstance(reviewer, CodexExecRunner)
    assert reviewer.isolate_mcp is True
    assert reviewer.single_writer is False


def test_adapter_serializes_child_creation_before_writer_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    adapter = SupervisorAgentCodexAdapter(workspace)
    first_entered = threading.Event()
    release_first = threading.Event()
    child_count = 0
    child_count_lock = threading.Lock()
    prepared = SimpleNamespace(run_dir=run_dir)

    monkeypatch.setattr(
        adapter,
        "_prepare_attempt",
        lambda run, timeout_seconds: prepared,
    )

    def prepare_child(_prepared):
        nonlocal child_count
        with child_count_lock:
            child_count += 1
            current = child_count
        if current == 1:
            first_entered.set()
            assert release_first.wait(5)
        return workspace / "runs" / f"child-{current}", "prompt"

    monkeypatch.setattr(adapter, "_prepare_child", prepare_child)
    monkeypatch.setattr(
        adapter.worker,
        "_bind_locked",
        lambda *args, **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        adapter,
        "_execute_worker",
        lambda *args, **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(adapter, "_reconcile_attempt", lambda executed: executed)

    def invoke() -> str:
        try:
            adapter.run(run_dir.name, timeout_seconds=60)
        except ValueError as exc:
            return f"blocked:{exc}"
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke)
        assert first_entered.wait(2)
        second = pool.submit(invoke)
        second_outcome = second.result(timeout=2)
        release_first.set()
        first_outcome = first.result(timeout=2)

    assert sorted([first_outcome, second_outcome]) == [
        "blocked:run 正由当前进程修改：run=agent-run，operation=agent.dispatch",
        "completed",
    ]
    assert child_count == 1


def test_verification_failure_takes_precedence_over_passed_finish_flag() -> None:
    trusted_finish = {
        "verification_passed": True,
        "latest_verification_failed": True,
        "artifact_integrity": {"valid": True},
        "evidence_freshness": {"fresh": True},
    }
    failed_iteration = LoopIterationState(
        iteration=1,
        verification_status="failed",
    )

    assert _verification_status(None, trusted_finish) == "failed"
    trusted_finish["latest_verification_failed"] = False
    assert _verification_status(failed_iteration, trusted_finish) == "failed"


def test_untrusted_finish_evidence_blocks_passed_verification_flag() -> None:
    untrusted_finish = {
        "verification_passed": True,
        "latest_verification_failed": False,
        "artifact_integrity": {"valid": False},
        "evidence_freshness": {"fresh": True},
    }

    assert _verification_status(None, untrusted_finish) == "blocked"


def test_iteration_verification_remains_passed_when_finish_evidence_is_untrusted() -> None:
    iteration = LoopIterationState(
        iteration=1,
        verification_status="passed",
    )
    untrusted_finish = {
        "verification_passed": True,
        "latest_verification_failed": False,
        "artifact_integrity": {"valid": False},
        "evidence_freshness": {"fresh": False},
    }

    assert _verification_status(iteration, untrusted_finish) == "passed"


class _FakeWorkerRunner:
    def __init__(
        self,
        *,
        status: str = "success",
        output: str | None = None,
        target_relative: str = "src/example.py",
        mutate_each_run: bool = True,
    ) -> None:
        self.status = status
        self.output = output
        self.target_relative = target_relative
        self.mutate_each_run = mutate_each_run
        self.execution_id: str | None = None
        self.run_count = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, timeout_seconds
        assert sandbox == "workspace-write"
        assert execution_context is not None
        self.run_count += 1
        self.execution_id = execution_context.execution_id
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-codex"], 60)
        target = repo_path / self.target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        value = self.run_count if self.mutate_each_run else 1
        target.write_text(f"value = {value}\n", encoding="utf-8", newline="\n")
        if self.status == "success":
            controller.finish("success", reason=None, returncode=0)
            return RunnerResult(
                status="success",
                output=self.output
                or json.dumps(
                    {
                        "claimed_status": "completed",
                        "summary": "已完成最小修改",
                        "tests_claimed": [],
                        "remaining_questions": [],
                    },
                    ensure_ascii=False,
                ),
                error=None,
                command=["fake-codex"],
            )
        controller.finish("timed_out", reason="模拟超时", returncode=None)
        return RunnerResult(
            status="timed_out",
            output="",
            error="模拟超时",
            command=["fake-codex"],
        )


class _FakeReviewerRunner:
    def __init__(self, changed_files: tuple[str, ...]) -> None:
        self.changed_files = changed_files
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, repo_path, timeout_seconds, execution_context
        assert sandbox == "read-only"
        self.calls += 1
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "committed handoff reviewed",
                    "findings": [],
                    "reviewed_files": list(self.changed_files),
                    "checked_items": ["scope", "tests"],
                }
            ),
            command=["fake-reviewer"],
        )


def test_resumed_committed_handoff_runs_core_with_capsule_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "feature/committed-handoff")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text(
        "from src.example import value\n\n\ndef test_value():\n    assert value == 0\n",
        encoding="utf-8",
        newline="\n",
    )
    (repo / ".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "scope:",
                "  allowed_paths:",
                "    - src/example.py",
                "    - tests/test_example.py",
                "verification:",
                "  commands:",
                "    - python -m pytest tests/test_example.py -q",
                "  max_commands: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".vega.yaml", "tests/test_example.py")
    _git(repo, "commit", "-m", "测试：建立跨机器基线")
    comparison_base = _head(repo)
    changed_files = ("src/example.py", "tests/test_example.py")
    (repo / "src" / "example.py").write_text(
        "value = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (repo / "tests" / "test_example.py").write_text(
        "from src.example import value\n\n\ndef test_value():\n    assert value == 1\n",
        encoding="utf-8",
        newline="\n",
    )
    plan = approve_plan(
        AgentPlan(
            task_id="task-committed-handoff",
            user_goal="验证已提交 WIP 的跨机器恢复。",
            success_conditions=["定向测试通过并完成隔离审查"],
            work_items=[
                AgentWorkItem(
                    work_item_id="W1",
                    objective="重新验证已提交 WIP",
                    allowed_paths=list(changed_files),
                    verification=["python -m pytest tests/test_example.py -q"],
                )
            ],
        ),
        actor="user",
        approved_at="2026-08-16T00:00:00+00:00",
    )
    digest = compute_handoff_workspace_digest(repo, list(changed_files))
    card = AgentTaskCard(
        task_id=plan.task_id,
        status="paused",
        branch="feature/committed-handoff",
        base_revision=comparison_base,
        plan=plan,
        current_work_item="W1",
        handoff_sequence=1,
        handoff_status="handoff_ready",
        handoff_base_revision=comparison_base,
        handoff_workspace_digest=digest,
        last_handoff_checkpoint="checkpoint-001",
        resume_capsule=ResumeCapsule(
            current_work_item="W1",
            stopped_at="WIP 已提交但尚未在新机器复验",
            confirmed_facts=["两个允许文件已完成最小修改"],
            changed_files=list(changed_files),
            workspace_digest=digest,
            writer_stopped=True,
            workspace_explained=True,
            allowed_actions=["repair", "human"],
            next_step="在新机器重新执行 Core 门禁",
        ),
    )
    task_path = (
        repo
        / ".vega"
        / "tasks"
        / "2026-08"
        / "committed-handoff.md"
    )
    save_task_card(task_path, card)
    _git(
        repo,
        "add",
        *changed_files,
        task_path.relative_to(repo).as_posix(),
    )
    _git(repo, "commit", "-m", "测试：提交可恢复 WIP")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    resumed = SupervisorAgentRuntime(workspace).resume_task_card(repo)
    reviewer = _FakeReviewerRunner(changed_files)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(mutate_each_run=False),
        loop_runtime=LoopAutomationRuntime(
            workspace,
            reviewer_runner=reviewer,
        ),
        finish_runtime=FinishRuntime(workspace),
    )

    result = adapter.run(resumed.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "completed"
    assert result.state.terminal_status == "ready_to_commit"
    assert reviewer.calls == 1
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert observation["changed_files"] == list(changed_files)
    assert observation["verification"] == "passed"
    assert observation["risk"] == "passed"
    assert observation["review"] == "passed"
    child = workspace / "runs" / observation["child_run"]
    child_state = json.loads(
        (child / "state.json").read_text(encoding="utf-8")
    )
    scope = json.loads(
        (
            child
            / "iterations"
            / "01"
            / "scope-gate-result.json"
        ).read_text(encoding="utf-8")
    )
    assert child_state["status"] == "success"
    assert child_state["comparison_base_sha"] == comparison_base
    assert child_state["comparison_paths"] == list(changed_files)
    assert scope["changed_files"] == list(changed_files)
    assert scope["committed_changed_files"] == list(changed_files)


def test_agent_success_path_preserves_completed_worker_in_status_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    runner = _FakeWorkerRunner()
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=runner,
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "completed"
    assert result.state.terminal_status == "ready_to_commit"
    assert result.state.active_child_run is None
    assert result.plan.work_items[0].status == "completed"
    assert runner.execution_id is not None
    assert len(runner.execution_id) == 32
    assert set(runner.execution_id) <= set("0123456789abcdef")
    assert loop.child_dir is not None
    execution = json.loads(
        next(
            (loop.child_dir / "executions" / "worker").glob(
                "*/execution.json"
            )
        ).read_text(encoding="utf-8")
    )
    assert execution["execution_id"] == runner.execution_id
    assert loop.verification_commands == ["运行定向测试"]
    observations = list((result.run_dir / "observations").glob("*.json"))
    assert len(observations) == 1
    payload = json.loads(observations[0].read_text(encoding="utf-8"))
    assert payload["authority"] == "machine_reconcile"
    assert payload["verification"] == "passed"
    assert payload["risk"] == "passed"
    assert payload["review"] == "passed"
    assert read_agent_trace(result.run_dir / "trace.jsonl")[-1]["event"] == (
        "agent_completed"
    )
    status_card = (result.run_dir / "status-card.md").read_text(encoding="utf-8")
    assert "阶段：已完成" in status_card
    assert f"Worker：{loop.child_dir.name}" in status_card
    assert "Worker：未启动" not in status_card
    assert "Finish：`ready_to_commit`" in status_card
    assert payload["changed_files"] == ["src/example.py"]


def test_agent_finalize_recovers_after_terminal_publish_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    def interrupt_finalize(run: str) -> None:
        del run
        raise OSError("模拟 Supervisor 终态发布前中断")

    monkeypatch.setattr(adapter.runtime, "finalize", interrupt_finalize)

    with pytest.raises(OSError, match="模拟 Supervisor 终态发布前中断"):
        adapter.run(run_id, timeout_seconds=60)

    interrupted = load_agent_state(
        workspace / "runs" / run_id / "agent-state.json"
    )
    assert interrupted.phase == "finalizing"
    assert interrupted.terminal_status is None

    runtime = SupervisorAgentRuntime(workspace)
    original_status_writer = agent_finalization_module.write_status_card

    def interrupt_status_card(*args, **kwargs) -> None:
        del args, kwargs
        raise OSError("模拟 completed State 发布后的状态卡中断")

    monkeypatch.setattr(
        agent_finalization_module,
        "write_status_card",
        interrupt_status_card,
    )
    with pytest.raises(OSError, match="模拟 completed State 发布后的状态卡中断"):
        runtime.finalize(run_id)
    persisted = load_agent_state(
        workspace / "runs" / run_id / "agent-state.json"
    )
    assert persisted.phase == "completed"
    monkeypatch.setattr(
        agent_finalization_module,
        "write_status_card",
        original_status_writer,
    )

    completed = runtime.finalize(run_id)
    repeated = runtime.finalize(run_id)

    assert completed.state.phase == "completed"
    assert completed.state.terminal_status == "ready_to_commit"
    assert repeated.state.state_version == completed.state.state_version
    assert "阶段：已完成" in (
        completed.run_dir / "status-card.md"
    ).read_text(encoding="utf-8")
    assert [
        item["event"]
        for item in read_agent_trace(completed.run_dir / "trace.jsonl")
    ].count("agent_completed") == 1


def test_adapter_keeps_untrusted_ready_finish_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop, evidence_trusted=False),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert observation["verification"] == "passed"
    assert observation["work_item_completed"] is False
    assert "Verification=passed" in observation["machine_summary"]


def test_approved_checkpoint_includes_first_assist_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    repo.joinpath(".gitignore").write_text(
        ".tmp/\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "测试：忽略运行目录")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace, prepare_runtime_root=True)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert repo.joinpath(".tmp", "vega-verification").is_dir()
    assert result.state.phase == "completed"
    assert loop.start_count == 1


def test_repo_local_agent_runs_do_not_drift_on_own_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    repo.joinpath(".gitignore").write_text(
        "runs/\n.tmp/\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "测试：忽略本地运行目录")
    monkeypatch.chdir(repo)
    runtime = SupervisorAgentRuntime(repo)
    plan = _plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    loop = _FakeLoopRuntime(repo, prepare_runtime_root=True)
    adapter = SupervisorAgentCodexAdapter(
        repo,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "completed"
    assert loop.start_count == 1


def test_repo_local_agent_runs_still_detect_other_ignored_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    repo.joinpath(".gitignore").write_text(
        "runs/\n.tmp/\n.local-cache/\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "测试：忽略本地运行目录")
    monkeypatch.chdir(repo)
    runtime = SupervisorAgentRuntime(repo)
    plan = _plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    repo.joinpath(".local-cache").mkdir()
    repo.joinpath(".local-cache", "unexpected.txt").write_text(
        "unexpected\n",
        encoding="utf-8",
    )
    adapter = SupervisorAgentCodexAdapter(
        repo,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=_FakeLoopRuntime(repo),
    )

    with pytest.raises(ValueError, match="Workspace 已漂移"):
        adapter.run(approved.run_dir.name, timeout_seconds=60)


def test_worker_timeout_preserves_partial_diff_and_skips_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    runner = _FakeWorkerRunner(status="timed_out")
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=runner,
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    assert result.state.active_child_run is None
    assert loop.continued is False
    assert (repo / "src" / "example.py").read_text(encoding="utf-8") == "value = 1\n"
    observation_path = next((result.run_dir / "observations").glob("*.json"))
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["external_side_effects"] == "unknown"
    assert observation["verification"] == "not_run"
    assert observation["review"] == "not_run"


def test_missing_codex_preflight_failure_releases_writer_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr("vega.runner.shutil.which", lambda _: None)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=CodexExecRunner(executable="missing-codex"),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    assert result.state.active_child_run is None
    assert result.state.active_operation_id is None
    assert loop.continued is False
    assert loop.child_dir is not None
    execution_path = next(
        (loop.child_dir / "executions" / "worker").glob("*/execution.json")
    )
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    assert execution["status"] == "failed"
    assert execution["child_pid"] is None


def test_invalid_worker_claim_skips_core_and_routes_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(output='{"claimed_status":"completed"}'),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    assert loop.continued is False
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert "不符合窄 Schema" in observation["machine_summary"]
    assert observation["external_side_effects"] == "unknown"


def test_blocked_worker_claim_skips_core_and_routes_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    blocked_claim = json.dumps(
        {
            "claimed_status": "blocked",
            "summary": "Windows sandbox 无法启动本地工具",
            "tests_claimed": [],
            "remaining_questions": ["修复执行环境后再运行"],
        },
        ensure_ascii=False,
    )
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(output=blocked_claim),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "needs_human"
    assert loop.continued is False
    assert loop.child_dir is not None
    assert not loop.child_dir.joinpath("finish-summary.json").exists()
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert observation["worker_claim"] == "Windows sandbox 无法启动本地工具"
    assert observation["verification"] == "not_run"
    assert observation["external_side_effects"] == "unknown"
    child_summary = json.loads(
        next((result.run_dir / "children").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert child_summary["worker"]["claim"]["claimed_status"] == "blocked"
    assert child_summary["core"]["status"] == "not_run"


def test_reviewer_request_changes_routes_back_to_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace, finish_status="needs_fix")
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "ready"
    assert result.state.allowed_actions == ["repair", "replan", "human"]
    assert result.plan.work_items[0].status == "active"
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert observation["verification"] == "passed"
    assert observation["review"] == "failed"
    assert observation["repairable_in_scope"] is True


def test_adapter_rejects_third_attempt_before_creating_child(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    state = load_agent_state(run_dir / "agent-state.json")
    for index in range(2):
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="worker_dispatch_committed",
            state=state,
            observation_summary=f"历史 attempt {index + 1}",
        )
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    with pytest.raises(ValueError, match="已用完一次初始 attempt"):
        adapter.run(run_id, timeout_seconds=60)

    assert loop.child_dir is None


def test_repair_attempt_reuses_child_and_preserves_execution_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace, finish_status="needs_fix")
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    first = adapter.run(run_id, timeout_seconds=60)
    second = adapter.run(first.run_dir.name, timeout_seconds=60)

    assert first.state.phase == "ready"
    assert second.state.phase == "ready"
    assert loop.start_count == 1
    assert loop.continue_count == 2
    assert loop.child_dir is not None
    executions = list(
        (loop.child_dir / "executions" / "worker").glob("*/execution.json")
    )
    assert len(executions) == 2
    assert len(
        {
            json.loads(path.read_text(encoding="utf-8"))["execution_id"]
            for path in executions
        }
    ) == 2


def test_adapter_rejects_multi_work_item_plan_before_creating_child(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _plan().model_copy(deep=True)
    plan.work_items.append(
        AgentWorkItem(
            work_item_id="W2",
            objective="完成第二项修改",
            allowed_paths=["src/second.py"],
            verification=["运行定向测试"],
        )
    )
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    with pytest.raises(ValueError, match="只接受一个未完成 Work Item"):
        adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert loop.child_dir is None


@pytest.mark.parametrize(
    ("verification", "message"),
    [
        ([], "至少一个验证命令"),
        (["运行测试\n运行 Ruff"], "必须是单行"),
        (["运行测试", " 运行测试 "], "不能重复"),
    ],
)
def test_adapter_rejects_invalid_verification_before_creating_child(
    tmp_path: Path,
    verification: list[str],
    message: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _plan().model_copy(deep=True)
    plan.work_items[0].verification = verification
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    with pytest.raises(ValueError, match=message):
        adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert loop.child_dir is None


def test_adapter_rejects_dirty_initial_workspace_before_creating_child(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "src" / "example.py").write_text("value = 9\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    with pytest.raises(ValueError, match="要求干净 Workspace"):
        adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert loop.child_dir is None


def test_repair_without_new_workspace_change_does_not_reuse_old_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace, finish_status="needs_fix")
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(mutate_each_run=False),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    first = adapter.run(run_id, timeout_seconds=60)
    second = adapter.run(first.run_dir.name, timeout_seconds=60)

    assert first.state.phase == "ready"
    assert second.state.phase == "needs_human"
    assert loop.continue_count == 1
    observations = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (second.run_dir / "observations").glob("*.json")
    ]
    observation = next(
        item
        for item in observations
        if "未产生新的 Workspace 变化" in item["machine_summary"]
    )
    assert observation["external_side_effects"] == "none"


def test_core_side_effect_outside_plan_is_reconciled_before_supervisor_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(
        workspace,
        core_mutation_relative="README.md",
    )
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "planning"
    assert loop.continue_count == 1
    scope_files = sorted((result.run_dir / "plan-scope").glob("*.json"))
    assert len(scope_files) == 2
    final_scope_path = next(
        path for path in scope_files if path.name.startswith("post-core-")
    )
    final_scope = json.loads(final_scope_path.read_text(encoding="utf-8"))
    assert final_scope["status"] == "failed"
    assert "README.md" in final_scope["changed_files"]
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert observation["plan_contradicted"] is True
    assert "Core 执行后" in observation["machine_summary"]


def test_outside_approved_plan_scope_skips_core_and_requires_replan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    monkeypatch.chdir(workspace)
    loop = _FakeLoopRuntime(workspace)
    adapter = SupervisorAgentCodexAdapter(
        workspace,
        worker_runner=_FakeWorkerRunner(target_relative="README.md"),
        loop_runtime=loop,
        finish_runtime=_FakeFinishRuntime(loop),
    )

    result = adapter.run(run_id, timeout_seconds=60)

    assert result.state.phase == "planning"
    assert result.state.active_child_run is None
    assert result.state.approved_plan_digest is None
    assert loop.continued is False
    scope_path = next((result.run_dir / "plan-scope").glob("*.json"))
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    assert scope["status"] == "failed"
    assert scope["changed_files"] == ["README.md"]
    assert scope["violations"][0]["code"] == "outside_allowed_paths"
    observation = json.loads(
        next((result.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert observation["plan_contradicted"] is True
    assert observation["external_side_effects"] == "none"
    assert scope_path.relative_to(result.run_dir).as_posix() in observation[
        "evidence_refs"
    ]


def _approved_run(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    return repo, workspace, approved.run_dir.name


def _plan(*, task_id: str = "task-adapter") -> AgentPlan:
    return AgentPlan(
        task_id=task_id,
        user_goal="修复示例问题",
        success_conditions=["定向验证通过"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="完成最小修复",
                allowed_paths=["src/example.py"],
                verification=["运行定向测试"],
            )
        ],
    )


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    _git(path, "config", "core.autocrlf", "false")
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "example.py").write_text("value = 0\n", encoding="utf-8")
    _git(path, "add", "README.md", "src/example.py")
    _git(path, "commit", "-m", "测试：初始化仓库")
    return path


def _head(repo: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()


def _git(repo: Path, *args: str) -> None:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
