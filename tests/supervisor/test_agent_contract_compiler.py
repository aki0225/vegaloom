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
from vega.agent_contract_compiler import compile_planning_proposal
from vega.agent_planning import (
    PlanningContractProposal,
    PlanningExecutionPlan,
    PlanningObservedFact,
    PlanningProposal,
    PlanningSourceRef,
)
from vega.agent_planning_runtime import PlanningProposalRunner
from vega.agent_runtime_support import load_agent_bundle
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_task_card import load_task_card
from vega.cli_entrypoint import app
from vega.project_config import load_project_config
from vega.runner import RunnerResult


def test_contract_compiler_enters_existing_approval_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_planning(repo, goal="修复示例函数")
    proposal = _proposal_for_run(started.run_dir)
    proposal.contract_proposal.goal = "让示例函数返回 2"
    published = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(proposal.model_dump_json()),
    ).run(started.run_dir.name, timeout_seconds=60)

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "vega.agent_start_cli.ensure_runner_ready",
        lambda *_args, **_kwargs: pytest.fail(
            "已有 Proposal 的确定性编译不应要求 Provider 可用"
        ),
    )
    result = CliRunner().invoke(
        app,
        ["run", "--run", published.run_dir.name, "--timeout", "60"],
    )
    assert result.exit_code == 0, result.output
    run_dir, state, plan, _ = load_agent_bundle(
        workspace,
        published.run_dir.name,
    )

    assert state.phase == "awaiting_approval"
    assert state.contract_revision == 1
    assert state.execution_plan_revision == 1
    assert state.active_child_run is None
    contract = json.loads(
        (run_dir / "change-contract.json").read_text(encoding="utf-8")
    )
    execution_plan = json.loads(
        (run_dir / "execution-plan.json").read_text(encoding="utf-8")
    )
    assert contract["approved"] is False
    assert contract["required_verification"] == ["python -m pytest -q"]
    assert contract["authority_envelope"]["allowed_paths"] == [
        "src/example.py",
        "tests/test_example.py",
    ]
    assert execution_plan["observed_facts"] == ["示例函数当前返回 1"]
    assert execution_plan["hypotheses"] == ["返回值与预期不一致"]
    assert execution_plan["unresolved_decisions"] == []
    plan_card = (run_dir / "plan-card.md").read_text(encoding="utf-8")
    assert "原始要求：修复示例函数" in plan_card
    assert "建议合同目标：让示例函数返回 2" in plan_card
    assert "## 不做的事" in plan_card
    assert "## 副作用授权" in plan_card
    assert "人工批准前不会启动 Worker" in plan_card
    assert plan.user_goal == proposal.contract_proposal.goal

    approved = runtime.approve(run_dir.name)

    assert approved.state.phase == "ready"
    assert approved.state.approved_contract_digest is not None


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("unknown_verification", "未在 `.vega.yaml` 登记"),
        ("verification_budget", "将执行 2 条命令"),
        ("proposal_revision", "初始 Planning Proposal 必须为 1"),
        ("glob_likely_file", "必须列出具体文件"),
        ("outside_scope", "越出项目允许范围"),
        ("missing_risk", "缺少命中风险领域"),
    ],
)
def test_contract_compiler_rejects_untrusted_proposal_fields(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    repo = _repo(tmp_path / "repo", include_database_risk=True)
    proposal = _proposal(repo, task_id="task-1", goal="修复示例函数")
    config = load_project_config(
        repo,
        tracked_only=True,
        tracked_revision=proposal.source_revision,
    )
    if mutation == "unknown_verification":
        proposal.contract_proposal.verification_suggestions = [
            "python unknown_check.py"
        ]
    elif mutation == "verification_budget":
        proposal.execution_plan.work_items[0].verification = [
            "python -m compileall -q src"
        ]
        config.verification.commands = [
            "python -m pytest -q",
            "python -m compileall -q src",
        ]
        config.verification.max_commands = 1
    elif mutation == "proposal_revision":
        proposal.proposal_revision = 2
    elif mutation == "glob_likely_file":
        proposal.execution_plan.work_items[0].likely_files = ["src/**"]
    elif mutation == "outside_scope":
        proposal.contract_proposal.authority_envelope.allowed_paths.append(
            "docs/**"
        )
        proposal.execution_plan.work_items[0].likely_files.append(
            "docs/guide.md"
        )
    else:
        proposal.contract_proposal.authority_envelope.allowed_paths.append(
            "src/db/**"
        )
        proposal.execution_plan.work_items[0].likely_files.append(
            "src/db/schema.py"
        )

    with pytest.raises(ValueError, match=expected):
        compile_planning_proposal(
            repo,
            proposal,
            config,
        )


def test_contract_compiler_fails_closed_when_source_revision_drifts(
    tmp_path: Path,
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
    request_path = published.run_dir / "planning-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["source_revision"] = "0" * 40
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = runtime.compile_planning(published.run_dir.name)

    assert result.state.phase == "needs_human"
    assert result.state.active_child_run is None
    assert not (result.run_dir / "change-contract.json").exists()
    assert "source_revision" in (
        result.run_dir / "status-card.md"
    ).read_text(encoding="utf-8")


def test_resumed_planning_proposal_compiles_on_another_checkout(
    tmp_path: Path,
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
    handoff = runtime.handoff(published.run_dir.name, reason="换机继续编译")
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
    next_runtime = SupervisorAgentRuntime(next_workspace)
    restored = next_runtime.resume_task_card(clone)

    compiled = next_runtime.compile_planning(restored.run_dir.name)

    assert compiled.state.phase == "awaiting_approval"
    assert (compiled.run_dir / "plan-card.md").is_file()
    assert compiled.state.accepted_checkpoint_sha != proposal.source_revision


def test_compiler_rejection_can_handoff_without_contract(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_planning(repo, goal="修复示例函数")
    proposal = _proposal_for_run(started.run_dir)
    proposal.contract_proposal.verification_suggestions = [
        "python unknown_check.py"
    ]
    published = PlanningProposalRunner(
        workspace,
        runner=_StaticRunner(proposal.model_dump_json()),
    ).run(started.run_dir.name, timeout_seconds=60)
    blocked = runtime.compile_planning(published.run_dir.name)

    assert blocked.state.phase == "needs_human"
    assert not (blocked.run_dir / "change-contract.json").exists()

    handoff = runtime.handoff(blocked.run_dir.name, reason="换机检查编译拒绝")
    card = load_task_card(handoff.task_card_path)
    metadata = json.loads(
        (blocked.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    task_path = handoff.task_card_path.relative_to(managed_repo).as_posix()
    _git(managed_repo, "add", task_path)
    _git(managed_repo, "commit", "-m", "测试：提交编译拒绝交接")

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

    assert restored.state.phase == "needs_human"
    assert restored.state.contract_revision is None
    assert not (restored.run_dir / "change-contract.json").exists()


class _StaticRunner:
    def __init__(self, output: str) -> None:
        self.output = output

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
        return RunnerResult(status="success", output=self.output)


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
                max_changed_files=3,
            ),
        ),
        execution_plan=PlanningExecutionPlan(
            work_items=[
                ExecutionWorkItem(
                    work_item_id="WI-01",
                    objective="修复示例函数并补回归测试",
                    likely_files=[
                        "src/example.py",
                        "tests/test_example.py",
                    ],
                    verification=["python -m pytest -q"],
                )
            ],
            implementation_strategy=["先复现，再做最小修复"],
        ),
    )


def _repo(path: Path, *, include_database_risk: bool = False) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Vega Test")
    (path / "src").mkdir()
    (path / "src" / "db").mkdir()
    (path / "tests").mkdir()
    (path / "src" / "example.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    (path / "src" / "db" / "schema.py").write_text(
        "SCHEMA = 1\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_example.py").write_text(
        "from src.example import value\n\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    required_reviews = (
        """
  required_reviews:
    - id: database
      label: 数据库
      paths:
        - src/db/**
"""
        if include_database_risk
        else "  required_reviews: []\n"
    )
    (path / ".vega.yaml").write_text(
        (
            "version: 1\n"
            "verification:\n"
            "  commands:\n"
            "    - python -m pytest -q\n"
            "  max_commands: 2\n"
            "scope:\n"
            "  allowed_paths:\n"
            "    - src/**\n"
            "    - tests/**\n"
            "  forbidden_paths: []\n"
            "risk:\n"
            f"{required_reviews}"
            "budget:\n"
            "  max_changed_files: 4\n"
        ),
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
