from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.agent_change_cli as change_cli_module
import vega.agent_change_driver as driver_module
import vega.agent_change_execution as execution_module
from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ChangeSideEffectPolicy,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_change_driver import AgentChangeDriver, ChangeDriverResult
from vega.agent_change_presentation import build_change_approval_snapshot
from vega.agent_cli_interaction import InteractionPumpUpdate
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
from vega.agent_runtime_support import bound_repo, load_agent_bundle
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


def test_change_rejects_approval_when_prompted_revision_changes(
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

    def revise_after_prompt(prompt: str) -> bool:
        assert "Contract revision：1" in prompt
        revised_contract = ChangeContract.model_validate(
            {
                **_contract().model_dump(mode="json"),
                "contract_revision": 2,
                "acceptance": ["示例函数返回 3"],
            }
        )
        revised_plan = ExecutionPlan.model_validate(
            {
                **_execution_plan().model_dump(mode="json"),
                "contract_revision": 2,
                "plan_revision": 2,
                "work_items": [
                    {
                        **_execution_plan().work_items[0].model_dump(mode="json"),
                        "objective": "修改实现使示例函数返回 3",
                    }
                ],
            }
        )
        runtime.revise_change(
            started.run_dir.name,
            proposed_contract=revised_contract,
            proposed_execution_plan=revised_plan,
        )
        return True

    monkeypatch.setattr(
        driver_module,
        "ensure_change_provider_ready",
        lambda _: pytest.fail("版本变化后不得启动 Worker"),
    )
    result = AgentChangeDriver(
        repo,
        repo,
        provider="claude",
        interactive=True,
        confirm=revise_after_prompt,
        timeout_seconds=60,
    ).change()

    assert result.reason_code == "approval.snapshot_changed"
    assert result.run is not None
    assert result.run.state.phase == "awaiting_approval"
    contract = ChangeContract.model_validate_json(
        (started.run_dir / "change-contract.json").read_text(encoding="utf-8")
    )
    assert contract.contract_revision == 2
    assert not contract.approved


def test_change_rejects_approval_when_workspace_changes_during_prompt(
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

    def change_workspace_after_prompt(_: str) -> bool:
        (bound_repo(started.run_dir) / "src" / "example.py").write_text(
            "def value():\n    return 999\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(
        driver_module,
        "ensure_change_provider_ready",
        lambda _: pytest.fail("Workspace 变化后不得启动 Worker"),
    )
    result = AgentChangeDriver(
        repo,
        repo,
        provider="claude",
        interactive=True,
        confirm=change_workspace_after_prompt,
        timeout_seconds=60,
    ).change()

    assert result.reason_code == "approval.snapshot_changed"
    assert result.run is not None
    assert result.run.state.phase == "awaiting_approval"
    with pytest.raises(ValueError, match="Workspace 已漂移"):
        runtime.approve(started.run_dir.name)
    contract = ChangeContract.model_validate_json(
        (started.run_dir / "change-contract.json").read_text(encoding="utf-8")
    )
    assert not contract.approved


def test_change_approval_prompt_includes_non_default_authority_and_plan_fields(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    private_path = "Q:" + "\\Users\\example\\private\\request.txt"  # repo-path-policy: allow-test-fixture
    contract = ChangeContract(
        task_id="task-detailed",
        goal=f"根据 {private_path} 修改支付重试",
        acceptance=["重复请求只产生一次扣款"],
        invariants=["账本记录保持唯一"],
        non_goals=["不更换支付 SDK"],
        authorized_risk_reviews=["payment"],
        side_effect_policy=ChangeSideEffectPolicy(
            database_schema_change=True,
            payment_or_funds_change=True,
        ),
        required_verification=["python -m pytest tests/payment"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/payments/**", "tests/payment/**"],
            forbidden_paths=["src/payments/legacy/**"],
            max_changed_files=7,
            max_repair_rounds=2,
            max_auto_replans=1,
            max_review_rounds=3,
            max_verification_retries=2,
        ),
    )
    plan = ExecutionPlan(
        task_id="task-detailed",
        contract_revision=1,
        observed_facts=["支付重试当前生成新幂等键"],
        hypotheses=["重复扣款来自幂等键漂移"],
        implementation_strategy=["先固定幂等键，再补回归测试"],
        additional_checks=["git diff --check"],
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="修复幂等键复用",
                likely_files=["src/payments/service.py"],
                verification=["python -m pytest tests/payment/test_retry.py"],
                risk_notes=["核对并发重试"],
            )
        ],
    )
    started = SupervisorAgentRuntime(repo).start_change(
        repo,
        contract=contract,
        execution_plan=plan,
    )

    prompt = build_change_approval_snapshot(started).prompt

    assert private_path not in prompt
    assert "目标：根据 <redacted-path> 修改支付重试" in prompt
    for expected in (
        "必须保持：\n- 账本记录保持唯一",
        "不在本次范围：\n- 不更换支付 SDK",
        "最多修改文件数：7",
        "verification retry：2",
        "database_schema_change：允许",
        "public_api_change：禁止",
        "风险复核：\n- payment",
        "已确认事实：\n- 支付重试当前生成新幂等键",
        "待验证假设：\n- 重复扣款来自幂等键漂移",
        "实现策略：\n- 先固定幂等键，再补回归测试",
        "额外检查：\n- git diff --check",
        "候选文件：src/payments/service.py",
        "验证：python -m pytest tests/payment/test_retry.py",
        "风险说明：核对并发重试",
        "Execution Plan digest：",
    ):
        assert expected in prompt


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


def test_concurrent_new_text_creates_one_run_and_rechecks_active_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    original_start_planning = SupervisorAgentRuntime.start_planning
    first_entered = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []
    calls_lock = threading.Lock()

    def delayed_start_planning(
        runtime: SupervisorAgentRuntime,
        source_repo: Path,
        *,
        goal: str,
    ) -> AgentRun:
        with calls_lock:
            calls.append(goal)
            call_number = len(calls)
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        return original_start_planning(runtime, source_repo, goal=goal)

    def stop_after_creation(
        driver: AgentChangeDriver,
        current: AgentRun,
    ) -> ChangeDriverResult:
        return ChangeDriverResult(
            run=current,
            outcome="completed",
            reason_code="test.created",
            message="测试已创建",
        )

    monkeypatch.setattr(
        SupervisorAgentRuntime,
        "start_planning",
        delayed_start_planning,
    )
    monkeypatch.setattr(AgentChangeDriver, "_drive", stop_after_creation)

    results: list[ChangeDriverResult] = []
    errors: list[BaseException] = []

    def invoke(text: str) -> None:
        try:
            results.append(
                AgentChangeDriver(
                    repo,
                    repo,
                    provider="claude",
                    timeout_seconds=60,
                ).change(text=text)
            )
        except BaseException as exc:  # pragma: no cover - 仅用于线程回收诊断
            errors.append(exc)

    first = threading.Thread(target=invoke, args=("第一个任务",))
    first.start()
    assert first_entered.wait(timeout=5)
    second = threading.Thread(target=invoke, args=("第二个任务",))
    second.start()
    second.join(timeout=5)
    assert not second.is_alive()
    release_first.set()
    first.join(timeout=10)

    assert not first.is_alive()
    assert errors == []
    assert calls == ["第一个任务"]
    assert sorted(result.reason_code for result in results) == [
        "change.repository_busy",
        "test.created",
    ]
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
    updates: list[InteractionPumpUpdate] = []
    events: list[str] = []
    fake_path = "Q:" + "\\Users\\example\\private\\config.json"  # repo-path-policy: allow-test-fixture
    fake_secret = "sk-change-fake-secret-123456"

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
                            summary=(
                                "执行命令；需要核对 "
                                f"{fake_path} api_key={fake_secret}"
                            ),
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
            raise ValueError(
                f"停止失败：{fake_path} token={fake_secret}"
            )

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
        interaction_reporter=updates.append,
        event_reporter=events.append,
        timeout_seconds=60,
    ).change()

    assert result.reason_code == "provider.interaction_requires_advanced_response"
    assert result.run is not None
    assert load_provider_sessions(result.run.run_dir).interactions[0].status == "pending"
    assert [update.status for update in updates] == ["attention"]
    visible = repr([result.message, updates, events])
    assert fake_path not in visible
    assert fake_secret not in visible
    assert "<redacted-path>" in visible
    assert "[REDACTED]" in visible


def test_change_message_redaction_preserves_urls_and_api_routes() -> None:
    message = (
        "访问 https://example.test/api/v1 和 /v1/users/123；"
        "日志位于 /tmp/vega/private.log、/workspace/project/run.log、"
        "/mnt/secret/cache、/opt/local/config"
    )

    safe = execution_module.redact_change_message(message)

    assert "https://example.test/api/v1" in safe
    assert "/v1/users/123" in safe
    for path in (
        "/tmp/vega/private.log",
        "/workspace/project/run.log",
        "/mnt/secret/cache",
        "/opt/local/config",
    ):
        assert path not in safe
    assert safe.count("<redacted-path>") == 4


def test_stop_request_closes_pending_provider_interactions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path
    run_dir = workspace / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    save_provider_sessions(
        run_dir,
        ProviderSessionState(
            run_id=run_dir.name,
            handles={
                "worker": ProviderSessionHandle(
                    provider="codex",
                    role="worker",
                    thread_id="thread-1",
                    owner="vega",
                    lifecycle="waiting_user",
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
                    summary="命令执行",
                )
            ],
        ),
    )

    class StaticRecovery:
        def __init__(self, actual_workspace: Path) -> None:
            assert actual_workspace == workspace

        def stop(self, run: str, *, reason: str) -> None:
            assert run == run_dir.name
            assert reason

    monkeypatch.setattr(
        execution_module,
        "SupervisorAgentRecovery",
        StaticRecovery,
    )

    assert execution_module._request_stop(
        workspace,
        run_dir.name,
        "Provider 请求缺少完整权限上下文",
        None,
    )
    assert load_provider_sessions(run_dir).interactions[0].status == "closed"


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

    fake_path = "Q:" + "\\Users\\example\\private\\error.log"  # repo-path-policy: allow-test-fixture
    fake_secret = "sk-change-json-fake-secret-123456"

    def fail_change(*_: object, **__: object) -> None:
        raise ValueError(f"读取 {fake_path} 失败，api_key={fake_secret}")

    monkeypatch.setattr(change_cli_module.AgentChangeDriver, "change", fail_change)
    failed = CliRunner().invoke(app, ["change", "--json"])
    assert failed.exit_code == 1
    error_payload = json.loads(failed.output)
    assert fake_path not in failed.output
    assert fake_secret not in failed.output
    assert error_payload["message"] == "读取 <redacted-path> 失败，api_key=[REDACTED]"


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
