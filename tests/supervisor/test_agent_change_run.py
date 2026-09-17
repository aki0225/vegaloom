from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from typer.testing import CliRunner

import vega.agent_change_runtime as agent_change_runtime_module
from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_change_fix_packet import load_current_fix_packet
from vega.agent_provider_adapter import SupervisorAgentProviderAdapter
from vega.execution_control import ExecutionController, RunnerExecutionContext
from vega.models import LoopAutomationState, LoopIterationState
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_task_card import load_task_card
from vega.cli_entrypoint import app
from vega.review_evidence import make_review_evidence
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.workspace_check import capture_review_workspace

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


@pytest.mark.parametrize("trigger", ["single", "worker_retry", "multi", "revision", "risk", "side_effect"])
def test_final_review_keeps_existing_triggers(trigger: str) -> None:
    from vega.agent_change_control import requires_final_integration_review

    contract = ChangeContract(
        task_id="review", goal="检查累计变更", acceptance=["验证通过"],
        required_verification=["git diff --check"],
        authority_envelope=ChangeAuthorityEnvelope(allowed_paths=["src/**"]),
    )
    if trigger == "risk":
        contract.authorized_risk_reviews = ["async-session"]
    if trigger == "side_effect":
        contract.side_effect_policy.external_write_during_validation = True
    context = SimpleNamespace(contract=contract, execution_plan=SimpleNamespace(
        work_items=["WI-01", "WI-02"] if trigger == "multi" else ["WI-01"],
        plan_revision=2 if trigger == "revision" else 1,
    ))
    assert requires_final_integration_review(
        context, attempt_number=2 if trigger == "worker_retry" else 1,
    ) is (trigger != "single")


@pytest.mark.parametrize("legacy", [False, True])
def test_final_report_groups_verification_without_losing_failure(legacy: bool) -> None:
    from vega.agent_visibility import _render_verification_summary

    checks = [
        {"iteration": 1, "artifact": "iterations/01/verification-result.json", "command": "old-fail", "status": "failed", "returncode": 1},
        {"iteration": 1, "artifact": "iterations/01/verification-result.json", "command": "old-timeout", "status": "timed_out", "returncode": None},
        {"iteration": 2, "artifact": "iterations/02/verification-result.json", "command": "current", "status": "passed", "returncode": 0},
    ]
    if legacy:
        for item in checks:
            item.pop("iteration")
    text = "\n".join(_render_verification_summary({"checks": checks, "trusted_passed": True, "latest_failed": False}))
    assert "old-fail" in text and "old-timeout" in text
    assert text.count("`current`") == 1
    if legacy:
        assert "旧格式缺少轮次" in text
    else:
        assert text.index("`current`") < text.index("历史验证") < text.index("old-fail")
        assert "iterations/01/verification-result.json" in text


@pytest.mark.parametrize("runtime_ignore", ["", ".tmp/", ".tmp/vega-verification/"])
def test_change_run_starts_in_isolated_worktree_and_approves_contract(
    tmp_path: Path,
    runtime_ignore: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    if runtime_ignore:
        with (repo / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write(runtime_ignore + "\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-m", "测试：登记运行目录")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_status = _git(repo, "status", "--short")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)

    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    metadata = json.loads(
        (started.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])

    assert started.state.run_kind == "change"
    assert started.state.phase == "awaiting_approval"
    assert managed_repo != repo
    assert managed_repo.is_dir()
    assert _git(managed_repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "status", "--short") == source_status
    assert (started.run_dir / "change-contract.json").is_file()
    assert (started.run_dir / "execution-plan.json").is_file()

    approved = runtime.approve(started.run_dir.name, actor="user")

    assert approved.state.phase == "ready"
    assert approved.state.approved_contract_digest
    assert approved.state.accepted_checkpoint_sha == source_head
    assert approved.state.current_work_item == "WI-01"
    assert approved.plan.approval_is_current()
    payload = run_status_payload(workspace, approved.run_dir.name)
    assert payload["agent_phase"] == "ready"
    assert (managed_repo / ".tmp" / "vega-verification").is_dir()
    assert not (repo / ".tmp").exists()
    assert len(approved.plan.work_items) == 2
    assert approved.plan.work_items[0].allowed_paths == ["src/one.py"]
    assert approved.plan.work_items[1].allowed_paths == ["src/two.py"]
    assert approved.plan.work_items[0].verification == [
        "python -m pytest tests/test_one.py -q"
    ]
    assert approved.plan.work_items[1].verification == [
        "python -m pytest tests/test_two.py -q",
        "python -m compileall -q src",
    ]
    task_brief = (approved.run_dir / "task-brief.md").read_text(encoding="utf-8")
    assert "## Worker 最小自检" in task_brief
    assert "## Vega 确定性 Gate（Candidate 冻结后执行）" in task_brief

    if runtime_ignore:
        (managed_repo / ".tmp" / "vega-verification" / "result.txt").write_text(
            "验证输出\n", encoding="utf-8"
        )
        assert run_status_payload(workspace, approved.run_dir.name)["agent_phase"] == "ready"
        (managed_repo / ".tmp" / "user-settings.txt").write_text(
            "非 Vega 文件\n", encoding="utf-8"
        )
        assert (
            run_status_payload(workspace, approved.run_dir.name)["agent_phase"]
            == "needs_human"
        )


def test_change_run_ids_are_unique_across_workspaces_in_same_second(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDatetime:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 8, 30, 6, 16, 1)

    run_ids = iter(
        [
            UUID("11111111-1111-1111-1111-111111111111"),
            UUID("22222222-2222-2222-2222-222222222222"),
        ]
    )
    monkeypatch.setattr(agent_change_runtime_module, "datetime", FixedDatetime)
    monkeypatch.setattr(agent_change_runtime_module, "uuid4", lambda: next(run_ids))
    repo = _repo(tmp_path / "repo")
    first_workspace = tmp_path / "first-workspace"
    second_workspace = tmp_path / "second-workspace"
    first_workspace.mkdir()
    second_workspace.mkdir()

    first = SupervisorAgentRuntime(first_workspace).start_change(
        repo,
        contract=_contract().model_copy(update={"task_id": "task-first"}),
        execution_plan=_execution_plan().model_copy(
            update={"task_id": "task-first"}
        ),
    )
    second = SupervisorAgentRuntime(second_workspace).start_change(
        repo,
        contract=_contract().model_copy(update={"task_id": "task-second"}),
        execution_plan=_execution_plan().model_copy(
            update={"task_id": "task-second"}
        ),
    )

    assert first.run_dir.name != second.run_dir.name


def test_change_run_task_brief_lists_only_applicable_agents_rules(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "AGENTS.md").write_text("# Root\n", encoding="utf-8")
    (repo / "src" / "AGENTS.md").write_text("# Source\n", encoding="utf-8")
    (repo / "tests" / "AGENTS.md").write_text("# Tests\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "增加目录规则")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)

    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    task_brief = (approved.run_dir / "task-brief.md").read_text(encoding="utf-8")

    assert "## 适用项目规则" in task_brief
    assert "`AGENTS.md`（作用域 `.`）" in task_brief
    assert "`src/AGENTS.md`（作用域 `src`）" in task_brief
    assert "tests/AGENTS.md" not in task_brief


@pytest.mark.parametrize(
    ("contract_encoding", "plan_encoding", "invalid_target", "invalid_payload", "expected_error"),
    [
        ("utf-8", "utf-8", None, None, None),
        ("utf-8-sig", "utf-8", None, None, None),
        ("utf-8", "utf-8-sig", None, None, None),
        ("utf-8-sig", "utf-8-sig", None, None, None),
        ("utf-8-sig", "utf-8", "contract", "{", "json_invalid"),
        ("utf-8", "utf-8-sig", "plan", "{", "json_invalid"),
        ("utf-8", "utf-8", "contract", "{}", "Field required"),
        ("utf-8", "utf-8", "plan", "{}", "Field required"),
    ],
)
def test_agent_start_cli_requires_change_contract_and_execution_plan(
    tmp_path: Path,
    monkeypatch,
    contract_encoding, plan_encoding, invalid_target, invalid_payload, expected_error,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract_path = tmp_path / "contract.json"
    plan_path = tmp_path / "execution-plan.json"
    contract_path.write_text(
        invalid_payload if invalid_target == "contract" else _contract().model_dump_json(indent=2),
        encoding=contract_encoding,
    )
    plan_path.write_text(
        invalid_payload if invalid_target == "plan" else _execution_plan().model_dump_json(indent=2),
        encoding=plan_encoding,
    )
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        [
            "start",
            "--repo",
            str(repo),
            "--contract",
            str(contract_path),
            "--execution-plan",
            str(plan_path),
        ],
    )

    if expected_error is not None:
        assert result.exit_code == 2, result.output
        assert expected_error in _ANSI_ESCAPE_PATTERN.sub("", result.output)
        assert not (workspace / "runs").exists()
        return

    assert result.exit_code == 0, result.output
    assert "ChangeRun 已创建" in result.output
    run_dirs = list((workspace / "runs").iterdir())
    assert len(run_dirs) == 1
    state = json.loads(
        (run_dirs[0] / "agent-state.json").read_text(encoding="utf-8")
    )
    assert state["data"]["run_kind"] == "change"
    assert state["data"]["phase"] == "awaiting_approval"


def test_agent_start_cli_rejects_removed_legacy_plan_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_plan = tmp_path / "agent-plan.json"
    legacy_plan.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        [
            "start",
            "--repo",
            str(repo),
            "--plan",
            str(legacy_plan),
        ],
    )

    assert result.exit_code != 0
    assert "No such option: --plan" in _ANSI_ESCAPE_PATTERN.sub("", result.output)
    assert not (workspace / "runs").exists()


def test_change_run_accepts_candidate_and_advances_to_next_work_item(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_status = _git(repo, "status", "--short")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=_contract(),
        execution_plan=_execution_plan(),
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    reviewer = _ReviewerRunner()
    loop_runtime = _ChangeLoopRuntime(workspace, reviewer)
    adapter = SupervisorAgentProviderAdapter(
        workspace,
        worker_runner=_WorkerRunner(["src/one.py", "src/two.py"]),
        loop_runtime=loop_runtime,
        finish_runtime=_ChangeFinishRuntime(loop_runtime),
    )

    result = adapter._run_once(approved.run_dir.name, timeout_seconds=60)
    metadata = json.loads(
        (result.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])
    candidate_artifacts = sorted((result.run_dir / "candidates").glob("*.json"))
    candidates = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in candidate_artifacts
    ]
    candidates.sort(key=lambda item: item["created_at"])

    assert result.state.phase == "ready"
    assert result.state.current_work_item == "WI-02"
    assert "next" in result.state.allowed_actions
    assert result.state.active_candidate_sha is None
    assert result.state.accepted_checkpoint_sha == _git(
        managed_repo,
        "rev-parse",
        "HEAD",
    )
    assert [item.status for item in result.plan.work_items] == [
        "completed",
        "pending",
    ]
    assert [item["work_item_id"] for item in candidates] == ["WI-01"]
    assert candidates[0]["parent_sha"] == source_head
    assert _git(managed_repo, "rev-list", "--count", f"{source_head}..HEAD") == "1"
    assert _git(repo, "rev-parse", "HEAD") == source_head
    assert _git(repo, "status", "--short") == source_status
    assert reviewer.calls == 1
    status = runtime.status(result.run_dir.name)
    assert "- 证据健康：`passed`" in status
    assert "已过期" not in status
    status_payload = run_status_payload(workspace, result.run_dir.name)
    assert status_payload["agent_run_kind"] == "change"
    assert status_payload["accepted_checkpoint_sha"] == result.state.accepted_checkpoint_sha
    assert "next_steps" not in status_payload
    assert any(
        path.endswith("change-contract.json")
        for path in status_payload["key_artifacts"]
    )
    child_states = [
        LoopAutomationState.model_validate_json(
            (path / "state.json").read_text(encoding="utf-8")
        )
        for path in (workspace / "runs").iterdir()
        if path != result.run_dir
        and (path / "state.json").is_file()
        and (path / "loop-plan.md").is_file()
    ]
    comparison_bases = [
        state.comparison_base_sha
        for state in child_states
        if state.input_source.startswith("agent-task-brief:")
    ]
    assert comparison_bases == [
        source_head,
    ]
    next_attempt = adapter._prepare_attempt(result.run_dir.name, 60)
    assert next_attempt.state.current_work_item == "WI-02"
    assert next_attempt.comparison_base_sha == candidates[0]["candidate_sha"]

    candidate_artifacts[0].write_text("{}\n", encoding="utf-8")
    degraded_status = runtime.status(result.run_dir.name)
    assert "证据告警" in degraded_status
    assert "等待人工" in degraded_status


def test_change_run_handoff_only_reports_changes_after_accepted_checkpoint(
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
    reviewer = _ReviewerRunner()
    loop_runtime = _ChangeLoopRuntime(workspace, reviewer)
    result = SupervisorAgentProviderAdapter(
        workspace,
        worker_runner=_WorkerRunner(["src/one.py"]),
        loop_runtime=loop_runtime,
        finish_runtime=_ChangeFinishRuntime(loop_runtime),
    )._run_once(approved.run_dir.name, timeout_seconds=60)

    handoff = runtime.handoff(
        result.run_dir.name,
        reason="换机前保存已完成 Work Item",
    )
    card = load_task_card(handoff.task_card_path)
    manifest = json.loads(
        (result.run_dir / "handoff-manifest.json").read_text(encoding="utf-8")
    )

    assert card.resume_capsule is not None
    assert card.resume_capsule.changed_files == []
    assert card.resume_capsule.allowed_actions == ["next", "human"]
    assert manifest["changed_files"] == []
    assert any(
        "git add -f" in action
        for action in manifest["pending_git_actions"]
    )


def test_change_run_completes_final_work_item(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = _contract().model_copy(
        update={
            "task_id": "task-single",
            "goal": "完成单项修改",
        }
    )
    execution_plan = ExecutionPlan(
        task_id="task-single",
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
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=execution_plan,
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    reviewer = _ReviewerRunner()
    loop_runtime = _ChangeLoopRuntime(workspace, reviewer)
    adapter = SupervisorAgentProviderAdapter(
        workspace,
        worker_runner=_WorkerRunner(["src/one.py"]),
        loop_runtime=loop_runtime,
        finish_runtime=_ChangeFinishRuntime(loop_runtime),
    )

    result = adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "completed"
    assert result.state.terminal_status == "ready_to_commit"
    assert result.state.active_candidate_sha is None
    assert [item.status for item in result.plan.work_items] == ["completed"]
    assert reviewer.calls == 1
    report = json.loads(
        (result.run_dir / "agent-final-report.json").read_text(encoding="utf-8")
    )
    assert report["candidate"]["changed_files"] == ["src/one.py"]
    assert report["integration_review"] is None
    assert report["change_impact_projection"]["items"] == []
    assert report["supervisor_gates"] == {
        "verification": "passed",
        "risk": "passed",
        "review": "passed",
        "external_side_effects": "none",
    }
    assert (result.run_dir / "agent-final-report.md").is_file()

    from vega.agent_change_driver import AgentChangeDriver
    from vega.agent_runtime_support import bound_repo
    driver = AgentChangeDriver(workspace, repo)
    assert driver.change(run=result.run_dir.name).outcome == "completed"
    (bound_repo(result.run_dir) / "src/one.py").write_text("value = 999\n", encoding="utf-8")
    stale = driver.change(run=result.run_dir.name)
    assert stale.outcome == "attention_required"
    assert stale.reason_code == "workspace.snapshot_stale"
    assert "run.continue" not in stale.safe_actions


def test_multi_item_change_run_adds_one_final_integration_review(
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
    reviewer = _ReviewerRunner()
    loop_runtime = _ChangeLoopRuntime(workspace, reviewer)
    adapter = SupervisorAgentProviderAdapter(
        workspace,
        worker_runner=_WorkerRunner(["src/one.py", "src/two.py"]),
        loop_runtime=loop_runtime,
        finish_runtime=_ChangeFinishRuntime(loop_runtime),
    )

    result = adapter.run(approved.run_dir.name, timeout_seconds=60)

    assert result.state.phase == "completed"
    assert reviewer.calls == 3
    integration_paths = list(
        (result.run_dir / "integration-reviews").glob("*.json")
    )
    assert len(integration_paths) == 1
    integration = json.loads(
        integration_paths[0].read_text(encoding="utf-8")
    )
    assert integration["status"] == "approve"
    assert '"verification": "passed"' in reviewer.prompts[-1]
    assert "不要仅因 Reviewer sandbox" in reviewer.prompts[-1]
    report = json.loads(
        (result.run_dir / "agent-final-report.json").read_text(encoding="utf-8")
    )
    assert report["candidate"]["changed_files"] == [
        "src/one.py",
        "src/two.py",
    ]
    assert report["integration_review"]["status"] == "approve"
    projection = report["change_impact_projection"]
    assert projection["candidate_sha"] == report["candidate"]["accepted_sha"]
    assert projection["items"][0]["locations"] == [{"file": "src/one.py", "line": 1}]
    report_text = (result.run_dir / "agent-final-report.md").read_text(encoding="utf-8")
    assert "功能变化与影响（模型意见）" in report_text
    assert "Candidate 已核验位置：`src/one.py:1`" in report_text


@pytest.mark.parametrize("allowed_action", ["next", "repair"])
def test_adapter_automatically_advances_ready_change_items(
    tmp_path: Path,
    monkeypatch,
    allowed_action: str,
) -> None:
    adapter = SupervisorAgentProviderAdapter(tmp_path)
    ready = SimpleNamespace(
        run_dir=tmp_path / "runs" / "change-run",
        state=SimpleNamespace(
            run_kind="change",
            phase="ready",
            allowed_actions=[allowed_action],
        ),
    )
    completed = SimpleNamespace(
        run_dir=ready.run_dir,
        state=SimpleNamespace(
            run_kind="change",
            phase="completed",
            allowed_actions=[],
        ),
    )
    results = iter([ready, completed])
    calls: list[str] = []

    def run_once(run: str, *, timeout_seconds: int):
        assert timeout_seconds == 60
        calls.append(run)
        return next(results)

    monkeypatch.setattr(adapter, "_run_once", run_once)
    monkeypatch.setattr("vega.agent_provider_adapter.change_run_step_limit", lambda workspace, run: 2)

    result = adapter.run("change-run", timeout_seconds=60)

    assert result is completed
    assert calls == ["change-run", "change-run"]


@pytest.mark.parametrize("acceptance_source", ["valid", "outside", "missing"])
def test_failed_candidate_generates_fix_packet_for_next_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    acceptance_source: str,
) -> None:
    import hashlib

    repo = _repo(tmp_path / "repo")
    material = "局部验收原始观察：边界输入返回预期值；尚非控制器验证。\n"
    (repo / "src/acceptance.md").write_text(material, encoding="utf-8", newline="\n")
    _git(repo, "add", "src/acceptance.md")
    _git(repo, "commit", "-m", "准备已批准项目材料")
    source_head = _git(repo, "rev-parse", "HEAD")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = _contract().model_copy(
        update={
            "task_id": "task-repair",
            "goal": "修复第一个模块",
        }
    )
    execution_plan = ExecutionPlan(
        task_id="task-repair",
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="更新第一个模块",
                likely_files=["src/one.py", "src/acceptance.md", "src/missing.md"],
                verification=["python -m pytest tests/test_one.py -q"],
            )
        ],
    )
    started = runtime.start_change(
        repo,
        contract=contract,
        execution_plan=execution_plan,
    )
    approved = runtime.approve(started.run_dir.name, actor="user")
    reviewer = _ReviewerRunner(["request_changes", "approve"])
    original_review = reviewer.run

    def review_with_optional_finding(*args, **kwargs):
        result = original_review(*args, **kwargs)
        payload = json.loads(result.output)
        original = payload["findings"][0]
        payload["findings"].extend([
            {**original, "severity": "minor", "title": "必要验收缺口",
             "recommendation": "补齐必要验收"},
            {**original, "severity": "suggestion", "title": "可选整理",
             "recommendation": "仅建议整理命名"},
        ])
        result.output = json.dumps(payload, ensure_ascii=False)
        return result

    monkeypatch.setattr(reviewer, "run", review_with_optional_finding)
    worker = _WorkerRunner(["src/one.py", "src/one.py"])
    original_work = worker.run

    def work_with_acceptance(*args, **kwargs):
        result = original_work(*args, **kwargs)
        payload = json.loads(result.output)
        payload["acceptance_refs"] = [{
            "path": {"valid": "src/acceptance.md", "outside": "../outside.md",
                     "missing": "src/missing.md"}[acceptance_source],
            "sha256": hashlib.sha256(material.encode()).hexdigest(),
        }]
        result.output = json.dumps(payload, ensure_ascii=False)
        return result

    monkeypatch.setattr(worker, "run", work_with_acceptance)
    from vega.loop_runtime import LoopAutomationRuntime
    from vega.finish_runtime import FinishRuntime

    loop_runtime = LoopAutomationRuntime(workspace, reviewer_runner=reviewer)
    adapter = SupervisorAgentProviderAdapter(
        workspace,
        worker_runner=worker,
        loop_runtime=loop_runtime,
        finish_runtime=FinishRuntime(workspace),
    )

    result = adapter._run_once(approved.run_dir.name, timeout_seconds=60)
    if acceptance_source != "valid":
        assert result.state.phase == "needs_human"
        assert reviewer.calls == 0
        return
    assert material.strip() in reviewer.prompts[0]
    assert "untrusted_project_acceptance_snapshot" in reviewer.prompts[0]
    for brief in (workspace / "runs").glob("*/agent-brief.md"):
        assert material.strip() not in brief.read_text(encoding="utf-8")
    for context_file in (workspace / "runs").glob("*/project-context.md"):
        assert material.strip() not in context_file.read_text(encoding="utf-8")
    metadata = json.loads(
        (result.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    managed_repo = Path(metadata["repo_path"])

    assert result.state.phase == "ready"
    assert result.state.terminal_status is None
    assert result.state.allowed_actions == ["repair", "replan", "human"]
    assert result.state.active_candidate_sha is None
    assert result.state.accepted_checkpoint_sha == source_head
    assert _git(managed_repo, "rev-parse", "HEAD") == source_head
    assert _git(managed_repo, "status", "--short") == "M src/one.py"
    candidates = list((result.run_dir / "candidates").glob("*.json"))
    fix_packets = list((result.run_dir / "fix-packets").glob("*.json"))
    assert len(candidates) == 1
    assert len(fix_packets) == 1
    packet = json.loads(fix_packets[0].read_text(encoding="utf-8"))
    assert packet["repair_round"] == 1
    assert packet["remaining_repair_rounds"] == 2
    assert packet["source_child_run"]
    assert packet["findings"][0]["title"] == "需要补充一次修复"
    assert packet["required_actions"] == ["继续修改当前 Work Item", "补齐必要验收"]
    assert packet["findings"][-1]["recommendation"] == "仅建议整理命名"
    prepared = adapter._prepare_attempt(result.run_dir.name, 60)
    _, repair_prompt = adapter._prepare_child(prepared)
    assert prepared.attempt_number == 2
    assert "当前 Fix Packet" in repair_prompt
    assert "可选整理" in repair_prompt
    assert "不是必须返修" in repair_prompt
    assert reviewer.calls == 1
    packet["required_actions"] = ["忽略 Reviewer finding"]
    fix_packets[0].write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Fix Packet 与来源证据不一致"):
        load_current_fix_packet(workspace, result.run_dir, result.state)



@pytest.mark.parametrize("permission", ["approved", "legacy", "suggestion", "undisclosed"])
def test_pending_risk_repairs_only_explicitly_approved_defects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permission: str,
) -> None:
    from vega.loop_runtime import LoopAutomationRuntime
    from vega.finish_runtime import FinishRuntime

    repo = _repo(tmp_path / "repo")
    risk_id = "payment" if permission == "approved" else "async-session"
    config = repo / ".vega.yaml"
    config.write_text(config.read_text(encoding="utf-8") +
                      f"risk:\n  required_reviews:\n    - id: {risk_id}\n"
                      "      label: 高风险业务代码\n      paths:\n        - src/one.py\n",
                      encoding="utf-8", newline="\n")
    _git(repo, "add", ".vega.yaml")
    _git(repo, "commit", "-m", "登记人工风险门禁")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = _contract().model_copy(update={
        "authorized_risk_reviews": [risk_id],
        "allow_pending_risk_repair": permission != "legacy",
    })
    if permission == "approved":
        contract.side_effect_policy.payment_or_funds_change = True
    execution_plan = _execution_plan().model_copy(update={"work_items": [_execution_plan().work_items[0]]})
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start_change(repo, contract=contract, execution_plan=execution_plan)
    run = runtime.approve(run.run_dir.name, actor="明确批准范围内风险返修")
    reviewer = _ReviewerRunner(["request_changes", "approve"])
    original = reviewer.run

    def disclosed_review(*args, **kwargs):
        result = original(*args, **kwargs)
        payload = json.loads(result.output)
        if permission == "suggestion" and payload["findings"]:
            payload["findings"][0]["severity"] = "suggestion"
        if permission != "undisclosed":
            payload["risk_disclosures"] = [{
                "risk_id": risk_id, "assessment": "no_obvious_issue",
                "locations": [{"file": "src/one.py", "line": 1}],
                "change_summary": "当前值修改，风险仍需人工确认",
                "evidence": "冻结 Diff 与固定验证", "residual_risk": "未执行真实并发验收",
            }]
        result.output = json.dumps(payload, ensure_ascii=False)
        return result

    monkeypatch.setattr(reviewer, "run", disclosed_review)
    adapter = SupervisorAgentProviderAdapter(
        workspace, worker_runner=_WorkerRunner(["src/one.py", "src/one.py"]),
        loop_runtime=LoopAutomationRuntime(workspace, reviewer_runner=reviewer),
        finish_runtime=FinishRuntime(workspace),
    )
    result = adapter._run_once(run.run_dir.name, timeout_seconds=60)
    observation = json.loads(next((run.run_dir / "observations").glob("*.json")).read_text(encoding="utf-8"))
    assert observation["risk"] == "blocked"
    if permission != "approved":
        assert result.state.phase == "needs_human"
        assert result.state.allowed_actions == ["human"]
        assert reviewer.calls == 1
        return
    assert observation["review"] == "failed"
    assert result.state.phase == "ready"
    assert result.state.allowed_actions == ["repair", "human"]
    assert list((run.run_dir / "fix-packets").glob("*.json"))
    result = adapter._run_once(run.run_dir.name, timeout_seconds=60)
    assert result.state.phase == "needs_human"
    assert result.state.allowed_actions == ["human"]
    assert result.state.terminal_status is None
    assert reviewer.calls == 2
    for artifact in (run.run_dir / "observations").glob("*.json"):
        assert json.loads(artifact.read_text(encoding="utf-8"))["risk"] == "blocked"


def test_pre_core_blocked_worker_resumes_same_run_without_new_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vega.agent_change_driver import AgentChangeDriver
    from vega.agent_persistence import load_agent_checkpoint, save_agent_checkpoint
    from vega.agent_recovery import SupervisorAgentRecovery
    from vega.agent_recovery_request import AgentRecoveryRequest
    from vega.agent_side_effect_adjudication import SupervisorAgentSideEffectAdjudicator

    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _execution_plan()
    plan.work_items = plan.work_items[:1]
    started = runtime.start_change(repo, contract=_contract(), execution_plan=plan)
    runtime.approve(started.run_dir.name, actor="user")
    reviewer = _ReviewerRunner()
    loop = _ChangeLoopRuntime(workspace, reviewer)
    worker = _WorkerRunner(["src/one.py"])
    worker.claimed_status = "blocked"
    adapter = SupervisorAgentProviderAdapter(
        workspace, worker_runner=worker, loop_runtime=loop,
        finish_runtime=_ChangeFinishRuntime(loop),
    )
    failed = adapter.run(started.run_dir.name, timeout_seconds=60)
    assert failed.state.phase == "needs_human"
    assert reviewer.calls == 0
    original = {p: p.read_bytes() for p in (failed.run_dir / "observations").glob("*.json")}
    recovery = SupervisorAgentRecovery(workspace)
    recovery.stop(failed.run_dir.name, reason="人工核对依赖准备与本地工具终态")
    (failed.run_dir / "audit.md").write_text("受控替身仅写批准文件，无外部副作用", encoding="utf-8")
    SupervisorAgentSideEffectAdjudicator(workspace).adjudicate(
        failed.run_dir.name,
        AgentRecoveryRequest(reason="已核对替身执行记录", actor="user",
                             external_side_effects="none", evidence_refs=["audit.md"]),
    )
    resumed = recovery.resume_local(failed.run_dir.name)
    assert resumed.state.run_id == failed.state.run_id
    monkeypatch.setattr("vega.agent_change_driver.ensure_change_provider_ready", lambda _: None)
    monkeypatch.setattr(
        "vega.agent_change_execution.SupervisorAgentProviderAdapter", lambda *args, **kwargs: adapter,
    )
    driver = AgentChangeDriver(workspace, repo, timeout_seconds=60, worker_permissions="ask")
    old_child = next((failed.run_dir / "children").glob("*.json"))
    child_id = json.loads(old_child.read_text(encoding="utf-8"))["child_run"]
    core_state = workspace / "runs" / child_id / "state.json"
    core_state.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="无法解析 assist child state"):
        driver.change(run=resumed.run_dir.name)
    core_state.unlink()
    historical = json.loads(next(iter(original.values())))
    historical["verification"] = "failed"
    historical["operation_id"] = "older-operation"
    older = failed.run_dir / "observations" / "older-core.json"
    older.write_text(json.dumps(historical), encoding="utf-8")
    with pytest.raises(ValueError, match="已有 Core 门禁结果"):
        driver.change(run=resumed.run_dir.name)
    older.unlink()
    checkpoint_path = resumed.run_dir / "checkpoints" / f"{resumed.state.latest_checkpoint_id}.json"
    checkpoint = load_agent_checkpoint(checkpoint_path)
    wrong_child = workspace / "runs" / "wrong-child"
    wrong_child.mkdir()
    save_agent_checkpoint(checkpoint_path, checkpoint.model_copy(update={"failed_attempts": ["wrong-child"]}))
    with pytest.raises(ValueError, match="失败 Checkpoint 与原 Worker 绑定不一致"):
        driver.change(run=resumed.run_dir.name)
    save_agent_checkpoint(checkpoint_path, checkpoint)
    assert worker.calls == 1
    adapter.worker_runner = _WorkerRunner(["src/one.py"])
    # 新 Worker 仅确认原 WIP，不制造无意义改动来满足 repair 的 Diff 要求。
    result = driver.change(run=resumed.run_dir.name)
    assert result.run is not None
    completed = result.run
    assert completed.state.phase == "completed"
    assert reviewer.calls == 2  # 后续 attempt 保留原有累计审查规则，不重置预算。
    assert "实现与验证交接" in adapter.worker_runner.prompts[0]
    assert all(path.read_bytes() == content for path, content in original.items())


class _WorkerRunner:
    def __init__(self, targets: list[str]) -> None:
        self.targets = targets
        self.calls = 0
        self.path_calls: dict[str, int] = {}
        self.prompts: list[str] = []
        self.claimed_status = "completed"

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del timeout_seconds
        assert sandbox == "workspace-write"
        assert execution_context is not None
        self.prompts.append(prompt)
        relative = self.targets[self.calls]
        target = repo_path / relative
        self.calls += 1
        self.path_calls[relative] = self.path_calls.get(relative, 0) + 1
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-worker"], 60)
        target.write_text(
            f"value = {self.path_calls[relative]}\n",
            encoding="utf-8",
            newline="\n",
        )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "claimed_status": self.claimed_status,
                    "summary": "当前 Work Item 已修改",
                    "tests_claimed": ["沙箱缺少测试依赖，交由控制器运行已批准验证"],
                    "remaining_questions": [],
                },
                ensure_ascii=False,
            ),
            command=["fake-worker"],
        )


class _ReviewerRunner:
    def __init__(self, verdicts: list[str] | None = None) -> None:
        self.calls = 0
        self.verdicts = verdicts or ["approve"]
        self.prompts: list[str] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del timeout_seconds, execution_context
        assert sandbox == "read-only"
        self.calls += 1
        self.prompts.append(prompt)
        final_batch = re.search(
            r"必须在 reviewed_files 中完整列出：(\[[^\n]+\])",
            prompt,
        )
        reviewed_files = (
            json.loads(final_batch.group(1))
            if final_batch is not None
            else [
                line
                for line in _git(
                    repo_path,
                    "show",
                    "--format=",
                    "--name-only",
                    "HEAD",
                ).splitlines()
                if line
            ]
        )
        verdict = self.verdicts[min(self.calls - 1, len(self.verdicts) - 1)]
        findings = (
            [
                {
                    "severity": "major",
                    "file": reviewed_files[0],
                    "line": 1,
                    "title": "需要补充一次修复",
                    "evidence": "测试 Reviewer 的确定性返回",
                    "recommendation": "继续修改当前 Work Item",
                }
            ]
            if verdict == "request_changes"
            else []
        )
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": verdict,
                    "summary": (
                        "当前 Candidate 需要继续修改"
                        if verdict == "request_changes"
                        else "当前 Candidate 未发现阻断问题"
                    ),
                    "findings": findings,
                    "reviewed_files": reviewed_files,
                    "checked_items": (
                        ["scope", "tests"]
                        if verdict == "approve"
                        else []
                    ),
                    **({"change_impacts": [{
                        "summary": "两个模块的累计修改保持一致",
                        "locations": [{"file": reviewed_files[0], "line": 1}],
                    }]} if final_batch is not None else {}),
                },
                ensure_ascii=False,
            ),
            command=["fake-reviewer"],
        )


class _ChangeLoopRuntime:
    """只替代已有 Core 的重型内部流水线，保留 ChangeRun 边界测试。"""

    def __init__(self, workspace: Path, reviewer: _ReviewerRunner) -> None:
        self.workspace = workspace
        self.reviewer = reviewer
        self.reviewer_runner = reviewer
        self.children: dict[str, Path] = {}
        self.review_payloads: dict[str, dict[str, object]] = {}

    def _start_locked(
        self,
        brief_input,
        automation_mode: str,
        worker_name: str,
        reviewer_name: str,
        max_iterations: int,
        verify: bool,
        run_id: str,
        run_dir: Path,
        config,
        policy_snapshot,
        initial_head_sha: str,
        comparison_base_sha: str | None,
        comparison_paths: tuple[str, ...],
    ) -> Path:
        del worker_name, reviewer_name, config, policy_snapshot
        assert automation_mode == "assist"
        assert max_iterations == 2
        assert verify is True
        LoopAutomationState(
            run_id=run_id,
            status="needs_human",
            task_mode=brief_input.mode,
            automation_mode="assist",
            repo_path=brief_input.repo_path,
            input_source=brief_input.source,
            current_step="waiting_for_worker",
            initial_head_sha=initial_head_sha,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=list(comparison_paths),
        ).save(run_dir / "state.json")
        (run_dir / "worker-prompt.md").write_text(
            "根据 Task Brief 完成当前修改。\n",
            encoding="utf-8",
        )
        (run_dir / "loop-plan.md").write_text(
            "# 测试 Core Plan\n",
            encoding="utf-8",
        )
        self.children[run_id] = run_dir
        return run_dir

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
        verification_retry_baseline=None,
    ) -> Path:
        del (
            worker_name,
            reviewer_name,
            test_log,
            note,
            verify,
            rerun_worker,
            verification_commands,
            verification_retry_baseline,
        )
        child_dir = self.children[run]
        review_result = self.reviewer.run(
            "",
            repo_path,
            sandbox="read-only",
            timeout_seconds=60,
        )
        review_payload = json.loads(review_result.output)
        self.review_payloads[run] = review_payload
        verdict = review_payload["verdict"]
        state = LoopAutomationState.model_validate_json(
            (child_dir / "state.json").read_text(encoding="utf-8")
        )
        state.current_iteration = 1
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
                verdict=verdict,
            )
        ]
        if verdict == "approve":
            state.status = "success"
            state.current_step = "completed"
        else:
            state.status = "needs_human"
            state.current_step = "review_complete"
            fix_prompt = child_dir / "iterations" / "iteration-01" / "fix-prompt.md"
            fix_prompt.parent.mkdir(parents=True, exist_ok=True)
            fix_prompt.write_text(
                "根据 Reviewer finding 修复当前问题。\n",
                encoding="utf-8",
            )
        state.save(child_dir / "state.json")
        return child_dir


class _ChangeFinishRuntime:
    def __init__(self, loop_runtime: _ChangeLoopRuntime) -> None:
        self.loop_runtime = loop_runtime

    def run(self, run: str) -> Path:
        child_dir = self.loop_runtime.children[run]
        state = LoopAutomationState.model_validate_json(
            (child_dir / "state.json").read_text(encoding="utf-8")
        )
        snapshot = capture_review_workspace(
            Path(state.repo_path),
            comparison_base_sha=state.comparison_base_sha,
            comparison_paths=tuple(state.comparison_paths),
        )
        changed_files = list(snapshot.changed_files)
        reflect_run = f"{run}-reflect-{state.current_iteration:02d}"
        reflect_dir = self.loop_runtime.workspace / "runs" / reflect_run
        reflect_dir.mkdir(parents=True, exist_ok=True)
        review_evidence = make_review_evidence(
            snapshot,
            "",
            snapshot.full_diff,
            changed_files,
            review_source_run=reflect_run,
            upstream_source_run=run,
            source_brief="",
            reflection="",
            diff_summary="",
            source_brief_issues=[],
            source_brief_diagnostics=[],
        )
        (reflect_dir / "review-evidence.json").write_text(
            json.dumps(review_evidence, ensure_ascii=False),
            encoding="utf-8",
        )
        ready = state.status == "success"
        review_payload = self.loop_runtime.review_payloads[run]
        (child_dir / "finish-summary.json").write_text(
            json.dumps(
                {
                    "run_id": run,
                    "finish_status": (
                        "ready_to_commit" if ready else "needs_fix"
                    ),
                    "verification_passed": True,
                    "latest_verification_failed": False,
                    "artifact_integrity": {"valid": True},
                    "evidence_freshness": {
                        "fresh": True,
                        "source_run": reflect_run,
                        "snapshot_id": review_evidence["snapshot_id"],
                        "trusted_workspace_fingerprint": snapshot.fingerprint,
                    },
                    "latest_verdict": review_payload,
                    "first_screen": {
                        "actual_changes": {"changed_files": changed_files},
                        "gates": {
                            "verification": "passed",
                            "risk": {"status": "success"},
                        },
                        "review": {
                            "verdict": (
                                "approve" if ready else "request_changes"
                            ),
                            "findings": review_payload["findings"],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return child_dir


def _contract() -> ChangeContract:
    return ChangeContract(
        task_id="task-change-run",
        goal="完成两个顺序修改",
        acceptance=["两个模块均按要求更新"],
        required_verification=["python -m compileall -q src"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/**", "tests/**"],
            forbidden_paths=["src/generated/**"],
            max_changed_files=4,
        ),
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-change-run",
        contract_revision=1,
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="更新第一个模块",
                likely_files=["src/one.py"],
                verification=["python -m pytest tests/test_one.py -q"],
            ),
            ExecutionWorkItem(
                work_item_id="WI-02",
                objective="更新第二个模块",
                depends_on=["WI-01"],
                likely_files=["src/two.py"],
                verification=["python -m pytest tests/test_two.py -q"],
            ),
        ],
    )


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    (path / ".gitignore").write_text(
        ".vega/\n__pycache__/\n*.pyc\n",
        encoding="utf-8",
    )
    (path / ".vega.yaml").write_text(
        "\n".join(
            [
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
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / "src").mkdir()
    (path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (path / "src" / "one.py").write_text("value = 0\n", encoding="utf-8")
    (path / "src" / "two.py").write_text("value = 0\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_one.py").write_text(
        "from src.one import value\n\n\ndef test_one():\n    assert value >= 1\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_two.py").write_text(
        "from src.two import value\n\n\ndef test_two():\n    assert value == 1\n",
        encoding="utf-8",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "初始化")
    return path


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
