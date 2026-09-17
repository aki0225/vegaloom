from __future__ import annotations

import os
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from vega.agent_contract import AgentObservation, AgentPlan, AgentWorkItem
from vega.agent_persistence import save_agent_state
from vega.cli_entrypoint import app
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_worker import SupervisorAgentWorker
from vega.execution_control import ExecutionLease
from vega.execution_process import ProcessProbe
from vega.models import LoopAutomationState, LoopIterationState
from vega.review_queue_contract import ReviewQueue, ReviewQueueItem
from vega.run_status import render_run_status, run_status_payload


def test_real_candidate_transition_is_pending_in_public_status_and_explain(tmp_path, monkeypatch):
    from vega.agent_change_contract import ChangeContract, ChangeAuthorityEnvelope, ExecutionPlan, ExecutionWorkItem
    from vega.agent_change_run import load_change_run_context
    from vega.agent_runtime_support import load_agent_bundle
    from vega.agent_git_candidate import freeze_candidate_commit

    repo = _git_repo(tmp_path / "repo")
    workspace = tmp_path / "w"
    workspace.mkdir()
    contract = ChangeContract(task_id="transition", goal="修改说明", acceptance=["说明已更新"],
        required_verification=["git diff --check"], authority_envelope=ChangeAuthorityEnvelope(allowed_paths=["README.md"]))
    plan = ExecutionPlan(task_id="transition", contract_revision=1, work_items=[
        ExecutionWorkItem(work_item_id="WI-01", objective="修改说明", likely_files=["README.md"]),
    ])
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(repo, contract=contract, execution_plan=plan)
    approved = runtime.approve(started.run_dir.name, actor="test")
    run_dir, state, agent_plan, metadata = load_agent_bundle(workspace, approved.run_dir.name)
    context = load_change_run_context(run_dir, state, agent_plan, metadata)
    child_dir = workspace / "runs" / "transition-child"
    child_dir.mkdir()
    SupervisorAgentWorker(workspace).bind(approved.run_dir.name, child_run=child_dir.name, operation_id="transition-operation")
    (context.worktree.worktree_path / "README.md").write_text("候选说明\n", encoding="utf-8")
    candidate = freeze_candidate_commit(context.worktree, expected_parent_sha=context.worktree.base_sha,
        contract=context.contract, execution_plan=context.execution_plan, work_item_id="WI-01", operation_id="transition-operation")
    bound, _ = runtime.bind_candidate(approved.run_dir.name, candidate=candidate)
    LoopAutomationState(run_id=child_dir.name, task_mode="bug", automation_mode="assist",
        repo_path=str(context.worktree.worktree_path), input_source="受控验证阶段", status="running",
        current_step="verify", current_iteration=1, initial_head_sha=candidate.candidate_sha).save(child_dir / "state.json")
    now = datetime.now(UTC)
    lease = ExecutionLease(run_id=child_dir.name, execution_id="verification-01", step="verification", iteration=1,
        owner_pid=os.getpid(), started_at=now.isoformat(), last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(minutes=5)).isoformat(), deadline=(now + timedelta(minutes=5)).isoformat(), status="running")
    execution = child_dir / "executions/verification/execution.json"
    execution.parent.mkdir(parents=True)
    execution.write_text(lease.model_dump_json(), encoding="utf-8")
    before = (run_dir / "agent-state.json").read_bytes()
    monkeypatch.chdir(workspace)
    for command in ("status", "explain"):
        result = CliRunner().invoke(app, [command, "--run", bound.run_dir.name, "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        explanation = payload["explanation"]
        assert explanation["phase"] == "acting"
        assert explanation["reason_code"] == "workspace.candidate_transition"
        assert explanation["outcome"] == "in_progress"
        assert explanation["safe_actions"] == ["status.view", "run.stop"]
    assert (run_dir / "agent-state.json").read_bytes() == before


@pytest.mark.parametrize("case", ["known", "cumulative", "parent", "drift", "candidate", "operation", "child", "revision", "unconfirmed", "stopping", "failed", "expired"])
def test_candidate_transition_uses_bound_snapshot_without_hiding_faults(tmp_path, monkeypatch, case):
    from vega import agent_status_projection as projection
    from vega.agent_child_status import AgentChildStatusSnapshot
    from vega.agent_contract import AgentState, AgentStatusCard
    from vega.agent_git_candidate import CandidateCommit
    from vega.agent_operation import reserve_operation_identity
    from vega.agent_explain import build_agent_explanation

    run_dir = tmp_path / "runs" / "parent"
    run_dir.mkdir(parents=True)
    child_dir = run_dir.parent / "child"
    child_dir.mkdir()
    state = AgentState(run_id="parent", task_id="task", repository_id="repo", run_kind="change",
        execution_protocol=2, phase="acting", current_work_item="W1", active_operation_id="operation",
        active_child_run="child", active_candidate_sha="a" * 40, workspace_fingerprint="b" * 64,
        contract_revision=1, execution_plan_revision=1, approved_contract_digest="c" * 64, accepted_checkpoint_sha="d" * 40)
    plan = AgentPlan(task_id="task", user_goal="修复", work_items=[AgentWorkItem(work_item_id="W1", objective="修复")])
    reserve_operation_identity(run_dir, state, child_run="foreign" if case == "operation" else "child", operation_id="operation")
    candidate = CandidateCommit(run_id="parent", work_item_id="W1", operation_id="operation",
        branch="vega/task", candidate_ref="HEAD", parent_sha=("e" if case == "parent" else "d") * 40, candidate_sha="a" * 40,
        contract_revision=2 if case == "revision" else 1, approved_contract_digest="c" * 64,
        execution_plan_revision=1, changed_files=["sample.py"], created_at="2026-09-16T00:00:00Z")
    (run_dir / "candidates").mkdir()
    (run_dir / "candidates/operation.json").write_text("{}" if case == "candidate" else candidate.model_dump_json(), encoding="utf-8")
    child = LoopAutomationState(run_id="child", task_mode="bug", automation_mode="assist", repo_path=str(tmp_path),
        input_source="测试", status="failed" if case == "failed" else "running", current_step="verify",
        initial_head_sha="a" * 40, current_iteration=1)
    snapshot = AgentChildStatusSnapshot("foreign" if case == "child" else "child", child_dir, child, "verify")
    expiry = datetime.now(UTC) + timedelta(minutes=-1 if case == "expired" else 5)
    execution = dict(run_id="child", step="verification", iteration=1, status="stop_requested" if case == "stopping" else "running",
                     termination_unconfirmed=case == "unconfirmed", deadline=expiry.isoformat(), lease_expires_at=expiry.isoformat())
    card = AgentStatusCard(run_id="parent", task_id="task", phase="acting", task_goal="修复", work_item_label="W1",
        worker_label="已结束", risk="not_run", next_step="对账", workspace_current=False,
        integrity_warning="当前 Workspace 与旧证据不一致。")
    monkeypatch.setattr(projection, "load_status_checkpoint_for_display", lambda *_: (None, None))
    monkeypatch.setattr(projection, "load_status_observation_for_display", lambda *_: (None, None))
    monkeypatch.setattr(projection, "trusted_worker_status", lambda *a, **k: ("已结束", "child"))
    monkeypatch.setattr(projection, "capture_trusted_child_status", lambda *_: snapshot)
    monkeypatch.setattr(projection, "_build_status_card", lambda *a, **k: card)
    def execution_projection(directory, _):
        assert directory == child_dir
        return execution
    monkeypatch.setattr("vega.agent_status_sources.latest_execution_payload", execution_projection)
    monkeypatch.setattr("vega.agent_status_sources.load_run_metadata", lambda *_: {})
    monkeypatch.setattr("vega.agent_status_sources.load_change_run_context", lambda *_: SimpleNamespace(
        contract=SimpleNamespace(contract_revision=1, approved_digest="c" * 64), execution_plan=SimpleNamespace(plan_revision=1),
        worktree=SimpleNamespace(branch="vega/task")))
    workspace = SimpleNamespace(head_sha="a" * 40, fingerprint="e" * 64 if case == "drift" else "b" * 64,
                                changed_files=["earlier.py", "sample.py"] if case == "cumulative" else ["sample.py"])
    if case == "operation":
        with pytest.raises(ValueError, match="身份或类型不一致"):
            projection.build_agent_status_projection(run_dir, state, plan, workspace_capture=(workspace, None))
        return
    view = projection.build_agent_status_projection(run_dir, state, plan, workspace_capture=(workspace, None))
    explained = build_agent_explanation(run_dir, state, plan, status_projection=view)
    assert view.payload["workspace_current"] is False and not view.card.commit_recommended
    assert view.state == state and view.card.allowed_actions == card.allowed_actions
    assert view.payload["candidate_transition"] is (case in {"known", "cumulative"})
    assert explained.reason_code == ("workspace.candidate_transition" if case in {"known", "cumulative"} else "workspace.snapshot_stale")
    from vega.agent_cli_snapshot import AgentCliRun, AgentCliSnapshot
    from vega.agent_cli_status import render_compact_agent_status
    from vega.agent_status_projection import read_status_card

    snapshot = AgentCliSnapshot(target=AgentCliRun(tmp_path, run_dir, "explicit"), status=view.payload, explanation=explained)
    expected = "已进入验证阶段，结果待对账" if case in {"known", "cumulative"} else "尚未运行"
    assert f"Verification：{expected}" in render_compact_agent_status(snapshot)
    assert f"Verification：{expected}" in read_status_card(run_dir, state, plan, status_projection=view)
    assert view.payload["verification"] == "not_run"
    if case in {"known", "cumulative"}:
        assert explained.safe_actions == ["status.view", "run.stop"]


@pytest.mark.parametrize(
    ("phase", "current_step"),
    [
        ("acting", "verify"),
        ("acting", "workspace_changed_before_worker"),
        ("observing", "review"),
        ("needs_human", "review"),
    ],
)
def test_agent_status_projects_live_child_stage_without_changing_parent(
    tmp_path: Path,
    phase: str,
    current_step: str,
) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    _write_child_state(child_dir, repo, current_step=current_step)
    if phase != "acting":
        save_agent_state(
            parent.run_dir / "agent-state.json",
            parent.state.model_copy(
                update={
                    "phase": phase,
                    "allowed_actions": ["human"],
                }
            ),
        )

    payload = run_status_payload(workspace, parent.run_dir.name)
    text = render_run_status(workspace, parent.run_dir.name)

    assert payload["agent_phase"] == phase
    assert payload["current_step"] == phase
    assert payload["live_child_stage"] == current_step
    if current_step == "verify":
        assert "绑定 Core 最近记录为验证阶段" in payload["core_stage_note"]
        assert payload["explanation"]["reason_code"] == "execution.core_pending"
        assert "进程状态及最终结果待核对" in payload["explanation"]["reason"]
    assert f"- Core 子流程：`{current_step}`" in text
    assert "next_steps" not in payload
    assert payload["explanation"]["safe_actions"]
    assert "\n- " in text.partition("## 下一步\n")[2].partition("## 关键产物")[0]


@pytest.mark.parametrize(
    "case", ["absent", "running", "stop_requested", "unconfirmed", "corrupt", "workspace_failed", "foreign_execution", "expired_unknown", "malformed_time"],
)
def test_agent_status_waits_when_child_state_has_not_been_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    (repo / "README.md").write_text("受控 Worker 正在修改\n", encoding="utf-8")
    if case == "absent":
        child_dir.rmdir()
    else:
        _write_worker_execution(child_dir, case)
    if case == "expired_unknown":
        monkeypatch.setattr(
            "vega.execution_control._probe_process", lambda *_: ProcessProbe("unknown"),
        )
    if case == "workspace_failed":
        monkeypatch.setattr(
            "vega.agent_status_projection.capture_live_workspace",
            lambda _: (None, "当前 Workspace 无法重新采集或绑定无法验证"),
        )
    payload = run_status_payload(workspace, parent.run_dir.name)

    assert payload["agent_phase"] == ("needs_human" if case == "corrupt" else "acting")
    assert payload["current_step"] == ("evidence_invalid" if case == "corrupt" else "acting")
    assert payload["live_child_stage"] == "等待子流程状态"
    assert payload["workspace_current"] is False
    assert payload["commit_recommended"] is False
    explanation = payload["explanation"]
    assert "run.continue" not in explanation["safe_actions"]
    if case == "running":
        assert payload["integrity_warning"] is None
        assert explanation["phase"] == "acting"
        assert explanation["reason_code"] == "execution.worker_active"
    else:
        assert payload["integrity_warning"]
        assert explanation["phase"] == "needs_human"


def test_agent_status_projects_child_while_worker_alive_requires_human(
    tmp_path: Path,
) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    _write_child_state(child_dir, repo, current_step="review")
    runtime = SupervisorAgentRuntime(workspace)

    result = runtime.observe_fake_worker(
        parent.run_dir.name,
        AgentObservation(
            observation_id="obs-worker-alive",
            work_item_id="W1",
            child_run=child_dir.name,
            operation_id="operation-live-child",
            machine_summary="Worker 仍在运行",
            workspace_fingerprint=parent.state.workspace_fingerprint or "0" * 64,
            worker_alive=True,
            operation_started=True,
        ),
    )

    payload = run_status_payload(workspace, result.run_dir.name)

    assert result.state.phase == "needs_human"
    assert result.state.active_child_run == child_dir.name
    assert payload["agent_phase"] == "needs_human"
    assert payload["current_step"] == "needs_human"
    assert payload["live_child_stage"] == "review"


def test_agent_status_projects_latest_child_review_queue(
    tmp_path: Path,
) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    iteration_dir = child_dir / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    _write_child_state(
        child_dir,
        repo,
        current_step="review",
        iterations=[
            LoopIterationState(
                iteration=1,
                lifecycle="completed",
                review_run="review-run",
            )
        ],
    )
    queue = ReviewQueue(
        source_run="reflect-run",
        candidate_sha="a" * 40,
        workspace_fingerprint="b" * 64,
        trigger=["diff_budget"],
        status="completed",
        max_items=8,
        max_prompt_chars=60000,
        max_diff_chars=1000,
        items=[
            ReviewQueueItem(
                item_id="RQ-01",
                status="completed",
                target_files=["src/example.py"],
                covered=["src/example.py"],
                verdict="approve",
                runner_status="success",
                artifact_dir="review-queue/rq-01",
            )
        ],
        covered=["src/example.py"],
        verdict="approve",
    )
    (iteration_dir / "review-queue.json").write_text(
        queue.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    child_payload = run_status_payload(workspace, child_dir.name)
    parent_payload = run_status_payload(workspace, parent.run_dir.name)
    parent_text = render_run_status(workspace, parent.run_dir.name)

    assert child_payload["review_queue_status"] == "completed"
    assert parent_payload["review_queue_status"] == "completed"
    assert parent_payload["review_queue_completed"] == 1
    assert parent_payload["review_queue_total"] == 1
    assert "Review Queue：`completed` / `1`/`1`" in parent_text


def test_agent_status_does_not_project_previous_iteration_review_queue(
    tmp_path: Path,
) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    previous_iteration_dir = child_dir / "iterations" / "01"
    previous_iteration_dir.mkdir(parents=True)
    _write_child_state(
        child_dir,
        repo,
        current_step="review",
        current_iteration=2,
        iterations=[
            LoopIterationState(
                iteration=1,
                lifecycle="completed",
                review_run="previous-review-run",
            )
        ],
    )
    queue = ReviewQueue(
        source_run="previous-reflect-run",
        candidate_sha="a" * 40,
        workspace_fingerprint="b" * 64,
        trigger=["diff_budget"],
        status="completed",
        max_items=8,
        max_prompt_chars=60000,
        max_diff_chars=1000,
        items=[
            ReviewQueueItem(
                item_id="RQ-01",
                status="completed",
                target_files=["src/previous.py"],
                covered=["src/previous.py"],
                verdict="approve",
                runner_status="success",
                artifact_dir="review-queue/rq-01",
            )
        ],
        covered=["src/previous.py"],
        verdict="approve",
    )
    (previous_iteration_dir / "review-queue.json").write_text(
        queue.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    child_payload = run_status_payload(workspace, child_dir.name)
    parent_payload = run_status_payload(workspace, parent.run_dir.name)

    assert child_payload["review_queue_status"] == "not_used"
    assert parent_payload["review_queue_status"] == "not_used"


def test_agent_cli_status_projects_latest_child_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    _write_child_state(child_dir, repo, current_step="review")
    persisted = (parent.run_dir / "status-card.md").read_text(encoding="utf-8")
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        ["status", "--run", parent.run_dir.name, "--full"],
    )

    assert result.exit_code == 0, result.output
    assert "Core 子流程：`review`" in result.output
    assert "## 计划风险提示" in result.output
    assert "涉及异步状态，需要人工关注" in result.output
    assert "- Risk：尚未运行" in result.output
    assert "Core 子流程：`review`" not in persisted


def test_agent_status_rejects_tampered_child_run_id(tmp_path: Path) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    _write_child_state(
        child_dir,
        repo,
        current_step="verify",
        run_id="tampered-child",
    )

    with pytest.raises(ValueError, match="run_id 与绑定 child 不一致"):
        run_status_payload(workspace, parent.run_dir.name)


def test_agent_status_rejects_tampered_child_repo_binding(tmp_path: Path) -> None:
    workspace, repo, parent, child_dir = _acting_parent(tmp_path)
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    _write_child_state(child_dir, other_repo, current_step="review")

    with pytest.raises(ValueError, match="仓库身份不一致"):
        run_status_payload(workspace, parent.run_dir.name)


def _write_worker_execution(child_dir: Path, case: str) -> None:
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=-1 if case == "expired_unknown" else 5)
    path = child_dir / "executions" / "worker" / "execution.json"
    path.parent.mkdir(parents=True)
    lease = ExecutionLease(
        run_id=child_dir.name,
        execution_id="other-operation" if case == "foreign_execution" else "operation-live-child",
        step="worker",
        owner_pid=os.getpid(),
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=expiry.isoformat(),
        deadline="not-a-time" if case == "malformed_time" else expiry.isoformat(),
        status="stop_requested" if case == "stop_requested" else "running",
        termination_unconfirmed=case == "unconfirmed",
    )
    path.write_text(
        "{" if case == "corrupt" else lease.model_dump_json(indent=2), encoding="utf-8",
    )


def _acting_parent(
    tmp_path: Path,
) -> tuple[Path, Path, object, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _git_repo(tmp_path / "repo")
    runtime = SupervisorAgentRuntime(workspace)
    plan = AgentPlan(
        task_id="task-live-child-status",
        user_goal="验证 Agent 实时状态",
        success_conditions=["状态字段可解释"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="观察 child 阶段",
                allowed_paths=["src/example.py"],
                verification=["运行定向测试"],
                risk_notes=["涉及异步状态，需要人工关注"],
            )
        ],
    )
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    child_run = "20260820-120000-live-child"
    acting = SupervisorAgentWorker(workspace).bind(
        approved.run_dir.name,
        child_run=child_run,
        operation_id="operation-live-child",
    )
    child_dir = workspace / "runs" / child_run
    child_dir.mkdir()
    return workspace, repo, acting, child_dir


def _write_child_state(
    child_dir: Path,
    repo: Path,
    *,
    current_step: str,
    run_id: str | None = None,
    current_iteration: int | None = None,
    iterations: list[LoopIterationState] | None = None,
) -> None:
    state = LoopAutomationState(
        run_id=run_id or child_dir.name,
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="测试",
        status="running",
        current_step=current_step,
        current_iteration=(
            current_iteration
            if current_iteration is not None
            else iterations[-1].iteration
            if iterations
            else 0
        ),
        iterations=iterations or [],
    )
    state.save(child_dir / "state.json")


def _git_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    _git(path, "config", "core.autocrlf", "false")
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "测试：初始化仓库")
    return path


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("phase", ["ready", "completed"])
def test_corrupt_execution_downgrades_ready_and_completed_display(tmp_path, monkeypatch, phase):
    from vega import agent_status_projection as projection
    from vega.agent_contract import AgentState, AgentStatusCard
    from vega.agent_explain import build_agent_explanation

    run_dir = tmp_path / "runs" / "parent"
    run_dir.mkdir(parents=True)
    state = AgentState(run_id="parent", task_id="task", repository_id="repo", phase=phase,
                       terminal_status="ready_to_commit" if phase == "completed" else None,
                       allowed_actions=["next"])
    original = state.model_dump()
    monkeypatch.setattr(projection, "trusted_worker_status", lambda *a, **k: ("已结束", None))
    plan = AgentPlan(task_id="task", user_goal="修复", work_items=[AgentWorkItem(work_item_id="W1", objective="修复")])
    card = AgentStatusCard(run_id="parent", task_id="task", phase=phase, task_goal="修复", work_item_label="W1",
                           worker_label="已结束", next_step="继续", terminal_status=state.terminal_status,
                           allowed_actions=["next"], commit_recommended=phase == "completed")
    monkeypatch.setattr(projection, "_build_status_card", lambda *a, **k: card)
    def corrupt(*args):
        raise ValueError("损坏的 execution")
    monkeypatch.setattr("vega.agent_status_sources.latest_execution_payload", corrupt)
    guidance = []
    monkeypatch.setattr(projection, "_existing_agent_artifacts", lambda run, current: guidance.append(current) or [])
    view = projection.build_agent_status_projection(run_dir, state, plan, workspace_capture=(None, None))
    explanation = build_agent_explanation(run_dir, state, plan, status_projection=view)
    assert view.payload["effective_phase"] == "needs_human"
    assert view.payload["terminal_status"] is None
    assert view.payload["commit_recommended"] is False
    assert view.payload["allowed_actions"] == ["human"]
    assert view.payload["integrity_warning"]
    assert explanation.outcome == "attention_required"
    assert "run.continue" not in explanation.safe_actions
    assert guidance[0]["agent_phase"] == "needs_human"
    assert state.model_dump() == original
