from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ChangeSideEffectPolicy,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_run import AgentRun
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_runtime_support import load_agent_bundle
from vega.cli_entrypoint import app


def test_cli_bounded_mode_requires_explicit_opt_in_and_repository_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    adapter_calls: list[str] = []

    class StaticAdapter:
        def __init__(self, workspace_path: Path, **_: object) -> None:
            self.workspace = workspace_path

        def run(self, run: str, *, timeout_seconds: int) -> AgentRun:
            assert timeout_seconds == 60
            run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
            assert state.phase == "ready"
            adapter_calls.append(run)
            return AgentRun(run_dir=run_dir, state=state, plan=plan)

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "vega.agent_start_cli.SupervisorAgentCodexAdapter",
        StaticAdapter,
    )
    monkeypatch.setattr(
        "vega.agent_start_cli.ensure_runner_ready",
        lambda *_args, **_kwargs: None,
    )

    human = CliRunner().invoke(
        app,
        ["run", "--run", started.run_dir.name, "--timeout", "60"],
    )

    assert human.exit_code == 0, human.output
    assert "等待人工批准" in human.output
    assert adapter_calls == []
    _, state, _, _ = load_agent_bundle(workspace, started.run_dir.name)
    assert state.phase == "awaiting_approval"

    bounded = CliRunner().invoke(
        app,
        [
            "run",
            "--run",
            started.run_dir.name,
            "--timeout",
            "60",
            "--approval",
            "bounded",
        ],
    )

    assert bounded.exit_code == 0, bounded.output
    assert "bounded 策略已批准当前 Contract" in bounded.output
    assert adapter_calls == [started.run_dir.name]
    run_dir, state, _, _ = load_agent_bundle(workspace, started.run_dir.name)
    assert state.phase == "ready"
    contract = json.loads(
        (run_dir / "change-contract.json").read_text(encoding="utf-8")
    )
    assert contract["approval_source"] == "bounded"
    assert contract["approval_policy_id"] == "low-risk-v1"
    assert len(contract["approval_policy_digest"]) == 64
    assert contract["approval_policy_revision"] == _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "case",
    [
        "policy_disabled",
        "unresolved_decision",
        "side_effect",
        "risk_path",
        "scope",
        "budget",
        "verification",
    ],
)
def test_bounded_approval_rejects_unclear_or_high_risk_contracts(
    tmp_path: Path,
    case: str,
) -> None:
    repo = _repo(
        tmp_path / "repo",
        policy_enabled=case != "policy_disabled",
        policy_allowed_paths=(
            ["docs/**"]
            if case == "scope"
            else ["src/**", "tests/**"]
        ),
        policy_max_changed_files=1 if case == "budget" else 4,
        required_review=case == "risk_path",
    )
    contract = _contract()
    plan = _execution_plan()
    if case == "unresolved_decision":
        plan = plan.model_copy(
            update={"unresolved_decisions": ["需要确认公共行为"]}
        )
    elif case == "side_effect":
        contract = contract.model_copy(
            update={
                "side_effect_policy": ChangeSideEffectPolicy(
                    public_api_change=True
                )
            }
        )
    elif case == "risk_path":
        contract = contract.model_copy(
            update={"authorized_risk_reviews": ["sensitive"]}
        )
    elif case == "verification":
        plan = plan.model_copy(
            update={
                "work_items": [
                    plan.work_items[0].model_copy(
                        update={"verification": ["python -m pytest -q tests/other"]}
                    )
                ]
            }
        )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=plan,
    )

    result = runtime.approve_bounded(started.run_dir.name)

    assert result.state.phase == "awaiting_approval"
    assert result.state.active_child_run is None
    persisted = json.loads(
        (result.run_dir / "change-contract.json").read_text(encoding="utf-8")
    )
    assert persisted["approved"] is False
    assert not (result.run_dir / "task-brief.md").exists()
    trace = [
        json.loads(line)
        for line in (result.run_dir / "trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert trace[-1]["event"] == "bounded_approval_rejected"


def test_bounded_approval_becomes_stale_when_policy_changes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve_bounded(started.run_dir.name)
    metadata = json.loads(
        (approved.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    config_path = managed_repo / ".vega.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "max_changed_files: 4",
            "max_changed_files: 3",
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="bounded 批准已过期"):
        runtime.status(approved.run_dir.name)


def _contract() -> ChangeContract:
    return ChangeContract(
        task_id="task-bounded",
        goal="修复示例函数",
        acceptance=["示例函数返回 2"],
        non_goals=["不修改其他模块"],
        required_verification=["python -m pytest -q"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/example.py", "tests/test_example.py"],
            max_changed_files=2,
            max_repair_rounds=1,
            max_auto_replans=0,
        ),
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-bounded",
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="修改实现并补回归测试",
                likely_files=["src/example.py", "tests/test_example.py"],
                verification=["python -m pytest -q"],
            )
        ],
    )


def _repo(
    path: Path,
    *,
    policy_enabled: bool = True,
    policy_allowed_paths: list[str] | None = None,
    policy_max_changed_files: int = 4,
    required_review: bool = False,
) -> Path:
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
    allowed_paths = policy_allowed_paths or ["src/**", "tests/**"]
    required_reviews = (
        [
            "  required_reviews:",
            "    - id: sensitive",
            "      label: 敏感逻辑",
            "      paths:",
            "        - src/example.py",
        ]
        if required_review
        else ["  required_reviews: []"]
    )
    config_lines = [
        "version: 1",
        "verification:",
        "  commands:",
        "    - python -m pytest -q",
        "  max_commands: 2",
        "scope:",
        "  allowed_paths:",
        "    - src/**",
        "    - tests/**",
        "  forbidden_paths: []",
        "risk:",
        *required_reviews,
        "budget:",
        "  max_changed_files: 4",
        "approval:",
        "  bounded:",
        f"    enabled: {str(policy_enabled).lower()}",
        "    policy_id: low-risk-v1",
        "    allowed_paths:",
        *[f"      - {item}" for item in allowed_paths],
        f"    max_changed_files: {policy_max_changed_files}",
        "    max_work_items: 2",
        "    max_repair_rounds: 1",
        "    max_auto_replans: 0",
    ]
    (path / ".vega.yaml").write_text(
        "\n".join(config_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "初始化测试仓库")
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
