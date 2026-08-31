from __future__ import annotations

import json
import subprocess
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
from vega.agent_runtime import SupervisorAgentRuntime
from vega.cli_entrypoint import app
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
