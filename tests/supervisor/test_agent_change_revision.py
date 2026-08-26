from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_change_control import (
    ChangeBudgetSnapshot,
    change_budget_snapshot,
    guard_change_decision_budget,
)
from vega.agent_contract import AgentDecision, AgentState
from vega.agent_persistence import append_agent_trace
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_verification_retry import SupervisorAgentVerificationRetry
from vega.cli_entrypoint import app


def test_execution_plan_revision_auto_applies_inside_approved_contract(
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
    approved = runtime.approve(started.run_dir.name, actor="user")
    contract = _load_contract(approved.run_dir)
    proposed = _execution_plan().model_copy(
        update={
            "plan_revision": 2,
            "hypotheses": ["先核对调用方，再修改实现"],
        }
    )

    revised = runtime.revise_change(
        approved.run_dir.name,
        proposed_contract=contract,
        proposed_execution_plan=proposed,
    )

    assert revised.state.phase == "ready"
    assert revised.state.execution_plan_revision == 2
    assert revised.plan.plan_revision == 2
    assert revised.plan.approval_is_current()
    assert (revised.run_dir / "execution-plans/execution-plan-revision-001.json").is_file()
    assert "change_execution_plan_auto_applied" in (
        revised.run_dir / "trace.jsonl"
    ).read_text(encoding="utf-8")


def test_actual_risk_path_requires_contract_revision_and_human_approval(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo", required_review=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    metadata = json.loads(
        (approved.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    (managed_repo / "src/one.py").write_text(
        "value = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    current_contract = _load_contract(approved.run_dir)
    plan_revision = _execution_plan().model_copy(
        update={"plan_revision": 2, "hypotheses": ["需要调整当前实现"]}
    )

    blocked = runtime.revise_change(
        approved.run_dir.name,
        proposed_contract=current_contract,
        proposed_execution_plan=plan_revision,
    )

    assert blocked.state.phase == "needs_human"
    assessment = _latest_assessment(blocked.run_dir)
    assert assessment["outcome"] == "needs_human"
    assert assessment["actual_changed_files"] == ["src/one.py"]
    assert assessment["missing_risk_authorizations"] == ["payment"]

    proposed_contract = ChangeContract.model_validate(
        {
            **current_contract.model_dump(
                mode="json",
                exclude={
                    "approved",
                    "approved_at",
                    "approved_by",
                    "approved_digest",
                },
            ),
            "contract_revision": 2,
            "authorized_risk_reviews": ["payment"],
        }
    )
    proposed_plan = plan_revision.model_copy(update={"contract_revision": 2})
    pending = runtime.revise_change(
        blocked.run_dir.name,
        proposed_contract=proposed_contract,
        proposed_execution_plan=proposed_plan,
    )

    assert pending.state.phase == "awaiting_approval"
    assert pending.state.approved_contract_digest is None
    assert "authorized_risk_reviews" in _latest_assessment(
        pending.run_dir
    )["declared"]["changed_fields"]

    reapproved = runtime.approve(pending.run_dir.name, actor="user")
    assert reapproved.state.phase == "ready"
    assert reapproved.state.contract_revision == 2
    assert _load_contract(reapproved.run_dir).authorized_risk_reviews == ["payment"]


def test_auto_replan_budget_exhaustion_stops_for_human(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = _contract().model_copy(
        update={
            "authority_envelope": _contract().authority_envelope.model_copy(
                update={"max_auto_replans": 0}
            )
        }
    )
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    proposed = _execution_plan().model_copy(
        update={"plan_revision": 2, "hypotheses": ["更换实现顺序"]}
    )

    result = runtime.revise_change(
        approved.run_dir.name,
        proposed_contract=_load_contract(approved.run_dir),
        proposed_execution_plan=proposed,
    )

    assert result.state.phase == "needs_human"
    assert "自动 Replan 预算已用完" in _latest_assessment(
        result.run_dir
    )["approval_question"]


def test_change_run_verification_retry_respects_contract_budget(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = _contract().model_copy(
        update={
            "authority_envelope": _contract().authority_envelope.model_copy(
                update={"max_verification_retries": 0}
            )
        }
    )
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve(started.run_dir.name, actor="user")

    with pytest.raises(ValueError, match="验证重试预算已用完：0/0"):
        SupervisorAgentVerificationRetry(workspace).run(approved.run_dir.name)


def test_review_budget_fails_closed_on_invalid_observation(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "agent-budget"
    (run_dir / "observations").mkdir(parents=True)
    state = AgentState(
        run_id="agent-budget",
        task_id="task-change-revision",
        repository_id="repo@example",
        run_kind="change",
        phase="ready",
        goal_revision=1,
        plan_revision=1,
        approved_plan_digest="a" * 64,
        contract_revision=1,
        approved_contract_digest="b" * 64,
        execution_plan_revision=1,
        accepted_checkpoint_sha="c" * 40,
        current_work_item="WI-01",
    )
    append_agent_trace(
        run_dir / "trace.jsonl",
        event="supervisor_repair",
        state=state,
        artifact_refs=["observations/observation-broken.json"],
    )
    (run_dir / "observations/observation-broken.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Observation 无法验证"):
        change_budget_snapshot(run_dir, state, _contract())


@pytest.mark.parametrize(
    ("repair_used", "repair_limit", "review_used", "review_limit", "label"),
    [
        (1, 1, 1, 4, "Repair 1/1"),
        (0, 3, 1, 1, "Review 1/1"),
    ],
)
def test_repair_decision_respects_explicit_stop_budgets(
    repair_used: int,
    repair_limit: int,
    review_used: int,
    review_limit: int,
    label: str,
) -> None:
    decision = AgentDecision(
        decision_id="decision-budget",
        observation_id="observation-budget",
        allowed_actions=["repair", "replan", "human"],
        selected_action="repair",
        reason="当前问题可以在合同内修复",
        source="deterministic",
    )
    budget = ChangeBudgetSnapshot(
        run_id="run-budget",
        work_item_id="WI-01",
        worker_attempts_used=repair_used + 1,
        repair_rounds_used=repair_used,
        auto_replans_used=0,
        review_rounds_used=review_used,
        verification_retries_used=0,
        max_repair_rounds=repair_limit,
        max_auto_replans=1,
        max_review_rounds=review_limit,
        max_verification_retries=1,
    )

    guarded = guard_change_decision_budget(decision, budget)

    assert guarded.selected_action == "human"
    assert guarded.allowed_actions == ["human"]
    assert label in guarded.reason


def test_agent_replan_command_is_registered() -> None:
    result = CliRunner().invoke(app, ["agent", "replan", "--help"])

    assert result.exit_code == 0, result.output
    assert "--contract" in result.output
    assert "--execution-plan" in result.output


def _contract() -> ChangeContract:
    return ChangeContract(
        task_id="task-change-revision",
        goal="更新一个模块",
        acceptance=["模块行为符合要求"],
        required_verification=["python -m compileall -q src"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/**", "tests/**"],
            forbidden_paths=["src/generated/**"],
            max_changed_files=4,
        ),
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-change-revision",
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="更新第一个模块",
                likely_files=["src/one.py"],
                verification=["python -m pytest tests/test_one.py -q"],
            )
        ],
    )


def _repo(path: Path, *, required_review: bool = False) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    config = [
        "version: 1",
        "scope:",
        "  allowed_paths:",
        "    - src/**",
        "    - tests/**",
        "verification:",
        "  commands:",
        "    - python -m compileall -q src",
        "  max_commands: 4",
    ]
    if required_review:
        config.extend(
            [
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: 支付与资金",
                "      paths:",
                "        - src/one.py",
            ]
        )
    (path / ".vega.yaml").write_text(
        "\n".join(config) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / ".gitignore").write_text(
        ".vega/\n__pycache__/\n*.pyc\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / "src").mkdir()
    (path / "src/__init__.py").write_text("", encoding="utf-8")
    (path / "src/one.py").write_text("value = 0\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests/test_one.py").write_text(
        "from src.one import value\n\n\ndef test_one():\n    assert value >= 0\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "初始化")
    return path


def _load_contract(run_dir: Path) -> ChangeContract:
    return ChangeContract.model_validate_json(
        (run_dir / "change-contract.json").read_text(encoding="utf-8")
    )


def _latest_assessment(run_dir: Path) -> dict[str, object]:
    trace = [
        json.loads(line)
        for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ref = next(
        ref
        for item in reversed(trace)
        for ref in item.get("artifact_refs", [])
        if ref.startswith("revisions/")
    )
    path = run_dir / ref
    return json.loads(path.read_text(encoding="utf-8"))


def _git(repo: Path, *args: object) -> str:
    process = subprocess.run(
        ["git", *(str(arg) for arg in args)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()
