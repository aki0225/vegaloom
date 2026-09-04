from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.agent_change_driver as driver_module
import vega.agent_change_execution as execution_module
from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_change_driver import AgentChangeDriver
from vega.agent_contract import AgentState
from vega.agent_planning import (
    PlanningContractProposal,
    PlanningExecutionPlan,
    PlanningObservedFact,
    PlanningProposal,
    PlanningSourceRef,
)
from vega.agent_planning_runtime import PlanningProposalRunner
from vega.agent_run import AgentRun
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_runtime_support import load_agent_bundle
from vega.cli_entrypoint import app
from vega.provider_session import (
    PendingInteraction,
    ProviderSessionHandle,
    ProviderSessionState,
    load_provider_sessions,
    save_provider_sessions,
)
from vega.runner import RunnerResult


def test_change_creates_planning_run_and_stops_at_non_tty_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    original_planning_runner = PlanningProposalRunner

    class StaticPlanningRunner:
        def __init__(self, workspace: Path, **_: object) -> None:
            self.workspace = workspace

        def run(self, run: str, *, timeout_seconds: int) -> AgentRun:
            run_dir, _, _, _ = load_agent_bundle(self.workspace, run)
            return original_planning_runner(
                self.workspace,
                runner=_StaticRunner(_proposal_for_run(run_dir).model_dump_json()),
            ).run(run, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(driver_module, "PlanningProposalRunner", StaticPlanningRunner)
    monkeypatch.setattr(
        driver_module,
        "ensure_change_provider_ready",
        lambda _: None,
    )

    result = AgentChangeDriver(
        repo,
        repo,
        provider="claude",
        interactive=False,
        json_output=True,
        timeout_seconds=60,
    ).change(text="修复示例函数")

    assert result.exit_code == 2
    assert result.reason_code == "approval.contract_required"
    assert result.run is not None
    assert result.run.state.phase == "awaiting_approval"
    assert not result.run.state.active_child_run
    assert (result.run.run_dir / "plan-card.md").is_file()


@pytest.mark.parametrize(
    ("approval", "policy_enabled"),
    [("human", False), ("bounded", True)],
)
def test_change_continues_unique_run_through_existing_approval_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approval: str,
    policy_enabled: bool,
) -> None:
    repo = _repo(tmp_path / "repo", policy_enabled=policy_enabled)
    runtime = SupervisorAgentRuntime(repo)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    adapter_calls: list[str] = []
    prompts: list[str] = []

    class StaticAdapter:
        def __init__(self, workspace: Path, **_: object) -> None:
            self.workspace = workspace

        def run(self, run: str, *, timeout_seconds: int) -> AgentRun:
            assert timeout_seconds == 60
            run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
            adapter_calls.append(run)
            return AgentRun(
                run_dir=run_dir,
                state=AgentState.model_validate(
                    {
                        **state.model_dump(mode="json"),
                        "phase": "needs_human",
                        "allowed_actions": ["human"],
                    }
                ),
                plan=plan,
            )

    monkeypatch.setattr(
        driver_module,
        "SupervisorAgentProviderAdapter",
        StaticAdapter,
    )
    monkeypatch.setattr(
        driver_module,
        "ensure_change_provider_ready",
        lambda _: None,
    )
    driver = AgentChangeDriver(
        repo,
        repo,
        provider="claude",
        approval=approval,  # type: ignore[arg-type]
        interactive=approval == "human",
        confirm=lambda prompt: prompts.append(prompt) is None,
        timeout_seconds=60,
    )

    result = driver.change()

    assert result.reason_code == "workflow.needs_human"
    assert adapter_calls == [started.run_dir.name]
    _, state, _, _ = load_agent_bundle(repo, started.run_dir.name)
    assert state.phase == "ready"
    if approval == "human":
        assert len(prompts) == 1
        assert "目标：修复示例函数" in prompts[0]
        assert "Contract digest：" in prompts[0]
    else:
        assert prompts == []
        contract = json.loads(
            (started.run_dir / "change-contract.json").read_text(encoding="utf-8")
        )
        assert contract["approval_source"] == "bounded"


def test_change_rejects_new_text_when_repository_has_active_run(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    started = SupervisorAgentRuntime(repo).start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )

    result = AgentChangeDriver(
        repo,
        repo,
        provider="claude",
        timeout_seconds=60,
    ).change(text="再创建一个任务")

    assert result.reason_code == "change.active_run_exists"
    assert result.run is not None
    assert result.run.run_dir == started.run_dir
    assert len(list((repo / "runs").iterdir())) == 1


def test_change_stops_for_codex_interaction_that_requires_full_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    runtime = SupervisorAgentRuntime(repo)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    runtime.approve(started.run_dir.name)
    stop_requested = threading.Event()
    updates: list[str] = []
    input_stream = _TtyInput("y\n")

    class InteractiveAdapter:
        def __init__(self, workspace: Path, **_: object) -> None:
            self.workspace = workspace

        def run(self, run: str, *, timeout_seconds: int) -> AgentRun:
            run_dir, state, plan, _ = load_agent_bundle(self.workspace, run)
            save_provider_sessions(
                run_dir,
                ProviderSessionState(
                    run_id=run,
                    handles={
                        "worker": ProviderSessionHandle(
                            provider="codex",
                            role="worker",
                            thread_id="thread-1",
                            owner="vega",
                            lifecycle="waiting_user",
                            sandbox="workspace-write",
                            approval_policy="on-request",
                            permissions_verified=True,
                            last_turn_id="turn-1",
                        )
                    },
                    interactions=[
                        PendingInteraction(
                            interaction_id="request-1",
                            role_key="worker",
                            rpc_request_id="rpc-1",
                            method="item/commandExecution/requestApproval",
                            thread_id="thread-1",
                            turn_id="turn-1",
                            summary="执行命令；需要在原生会话核对完整请求",
                        )
                    ],
                ),
            )
            assert stop_requested.wait(2), "driver 没有在交互边界请求停止"
            return AgentRun(
                run_dir=run_dir,
                state=AgentState.model_validate(
                    {
                        **state.model_dump(mode="json"),
                        "phase": "needs_human",
                        "allowed_actions": ["human"],
                    }
                ),
                plan=plan,
            )

    class StaticRecovery:
        def __init__(self, workspace: Path) -> None:
            assert workspace == repo

        def stop(self, run: str, *, reason: str) -> None:
            assert run == started.run_dir.name
            assert reason
            stop_requested.set()

    monkeypatch.setattr(
        driver_module,
        "SupervisorAgentProviderAdapter",
        InteractiveAdapter,
    )
    monkeypatch.setattr(
        driver_module,
        "ensure_change_provider_ready",
        lambda _: None,
    )
    monkeypatch.setattr(
        execution_module,
        "SupervisorAgentRecovery",
        StaticRecovery,
    )

    result = AgentChangeDriver(
        repo,
        repo,
        provider="codex",
        interactive=True,
        input_stream=input_stream,
        interaction_reporter=lambda update: updates.append(update.status),
        timeout_seconds=60,
    ).change()

    assert result.reason_code == "provider.interaction_requires_advanced_response"
    assert result.run is not None
    assert input_stream.read_count == 0
    assert load_provider_sessions(result.run.run_dir).interactions[0].status == "pending"
    assert updates == ["attention"]


def test_change_json_never_reads_stdin_without_active_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(app, ["change", "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload == {
        "schema_version": 1,
        "run_id": None,
        "phase": None,
        "outcome": "attention_required",
        "reason_code": "change.no_active_run",
        "message": "当前仓库没有未完成 ChangeRun，也没有可恢复的 Task Card。",
        "safe_actions": ["change <TEXT>", "start"],
    }


def test_change_implicit_task_card_requires_confirmation_before_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    task = repo / ".vega" / "tasks" / "2026-09" / "handoff.md"
    task.parent.mkdir(parents=True)
    task.write_text("测试占位\n", encoding="utf-8")
    monkeypatch.setattr(
        driver_module,
        "discover_handoff_task_cards",
        lambda _: [task],
    )
    driver = AgentChangeDriver(
        repo,
        repo,
        provider="claude",
        interactive=False,
        timeout_seconds=60,
    )

    result = driver.change()

    assert result.reason_code == "handoff.confirmation_required"
    assert result.run is None
    assert "2026-09/handoff.md" in result.message


class _StaticRunner:
    def __init__(self, output: str) -> None:
        self.output = output

    def run(self, *_: object, **__: object) -> RunnerResult:
        return RunnerResult(status="success", output=self.output)


class _TtyInput:
    def __init__(self, line: str) -> None:
        self.line = line
        self.read_count = 0

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        self.read_count += 1
        return self.line


def _proposal_for_run(run_dir: Path) -> PlanningProposal:
    request = json.loads(
        (run_dir / "planning-request.json").read_text(encoding="utf-8")
    )
    metadata = json.loads(
        (run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    repo = Path(metadata["repo_path"])
    return PlanningProposal(
        task_id=request["task_id"],
        user_goal=request["user_goal"],
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
            goal=request["user_goal"],
            acceptance=["示例函数返回 2"],
            non_goals=["不修改无关模块"],
            verification_suggestions=["python -m pytest -q"],
            authority_envelope=ChangeAuthorityEnvelope(
                allowed_paths=["src/**", "tests/**"],
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


def _contract() -> ChangeContract:
    return ChangeContract(
        task_id="task-change",
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
        task_id="task-change",
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


def _repo(path: Path, *, policy_enabled: bool = False) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
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
    (path / ".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -m pytest -q",
                "scope:",
                "  allowed_paths:",
                "    - src/**",
                "    - tests/**",
                "  forbidden_paths: []",
                "risk:",
                "  required_reviews: []",
                "approval:",
                "  bounded:",
                f"    enabled: {str(policy_enabled).lower()}",
                "    policy_id: low-risk-v1",
                "    allowed_paths:",
                "      - src/**",
                "      - tests/**",
                "    max_changed_files: 4",
                "    max_work_items: 2",
                "    max_repair_rounds: 1",
                "    max_auto_replans: 0",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "初始化测试仓库")
    return path.resolve()


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
