from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega import agent_runtime as agent_runtime_module
from vega import agent_runtime_support as agent_runtime_support_module
from vega.agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentWorkItem,
)
from vega.agent_graph import compile_gate1_graph
from vega.agent_persistence import (
    append_agent_trace,
    load_agent_state,
    read_agent_trace,
    save_agent_state,
)
from vega.agent_run_status import load_agent_status_state
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_worker import SupervisorAgentWorker
from vega.agent_task_card import (
    AgentTaskCard,
    ResumeCapsule,
    compute_handoff_workspace_digest,
    save_task_card,
)
from vega.cli_entrypoint import app
from vega.comparison_binding import require_comparison_binding_from_mapping
from vega.progress import RunProgressLog


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def test_comparison_paths_require_comparison_base() -> None:
    with pytest.raises(ValueError, match="comparison_paths_without_base"):
        require_comparison_binding_from_mapping(
            {"comparison_paths": ["src/example.py"]}
        )


def test_generic_status_latest_and_watch_recognize_agent_parent_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    plan = _single_item_plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)

    status = CliRunner().invoke(
        app,
        ["status", "--run", run.run_dir.name, "--json"],
    )
    latest = CliRunner().invoke(app, ["latest", "--kind", "agent", "--json"])
    watch = CliRunner().invoke(
        app,
        ["watch", "--run", run.run_dir.name, "--no-follow"],
    )

    assert status.exit_code == 0, status.output
    assert latest.exit_code == 0, latest.output
    assert watch.exit_code == 0, watch.output
    status_payload = json.loads(status.output)
    latest_payload = json.loads(latest.output)
    assert status_payload["kind"] == "agent"
    assert status_payload["status"] == "paused"
    assert status_payload["current_step"] == "awaiting_approval"
    assert status_payload["agent_phase"] == "awaiting_approval"
    assert status_payload["active_child_run"] is None
    assert status_payload["last_child_run"] is None
    assert latest_payload["run_id"] == run.run_dir.name
    assert f"run={run.run_dir.name} status=paused step=awaiting_approval" in (
        watch.output
    )
    assert "agent / 已启动" in watch.output


def test_generic_status_retains_latest_child_after_binding_is_cleared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    child_run = "20260816-120000-child-loop"
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run=child_run,
        operation_id="operation-completed-child",
    )
    child_dir = workspace / "runs" / child_run
    child_dir.mkdir()
    RunProgressLog(child_dir).append(
        "worker.command_completed",
        elapsed_seconds=3,
    )
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")

    result = runtime.observe_fake_worker(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-completed-child",
            work_item_id="W1",
            child_run=child_run,
            operation_id="operation-completed-child",
            machine_summary="Worker 与全部门禁已完成",
            workspace_fingerprint="0" * 64,
            work_item_completed=True,
            all_work_items_completed=True,
            verification="passed",
            risk="passed",
            review="passed",
        ),
    )

    status = CliRunner().invoke(
        app,
        ["status", "--run", result.run_dir.name, "--json"],
    )
    latest = CliRunner().invoke(app, ["latest", "--kind", "agent", "--json"])
    watch = CliRunner().invoke(
        app,
        ["watch", "--run", result.run_dir.name, "--no-follow"],
    )
    loaded = load_agent_status_state(
        result.run_dir,
        ordinary_state_exists=False,
    )

    assert result.state.phase == "finalizing"
    assert result.state.active_child_run is None
    assert status.exit_code == 0, status.output
    assert latest.exit_code == 0, latest.output
    assert watch.exit_code == 0, watch.output
    status_payload = json.loads(status.output)
    latest_payload = json.loads(latest.output)
    assert status_payload["active_child_run"] is None
    assert status_payload["last_child_run"] == child_run
    assert latest_payload["active_child_run"] is None
    assert latest_payload["last_child_run"] == child_run
    assert loaded["brief_run"] == child_run
    assert f"child={child_run}" in watch.output


def test_agent_status_rejects_active_operation_trace_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    approved = runtime.approve(run.run_dir.name)
    bound = _started_worker(
        workspace,
        approved.run_dir.name,
        child_run="attempt-current",
        operation_id="operation-traced",
    )
    mismatched_state = bound.state.model_copy(
        update={"active_operation_id": "operation-state"}
    )
    save_agent_state(
        bound.run_dir / "agent-state.json",
        mismatched_state,
    )

    with pytest.raises(
        ValueError,
        match="active operation 与最近可信 dispatch Trace 不一致",
    ):
        load_agent_status_state(
            bound.run_dir,
            ordinary_state_exists=False,
        )

    for command in (
        ["status", "--run", bound.run_dir.name, "--json"],
        ["watch", "--run", bound.run_dir.name, "--no-follow"],
    ):
        result = CliRunner().invoke(app, command)

        assert result.exit_code != 0
        assert "active operation" in _ANSI_ESCAPE_PATTERN.sub("", result.output)


def test_agent_status_rejects_observation_operation_trace_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    approved = runtime.approve(run.run_dir.name)
    child_run = "attempt-reused"
    bound = _started_worker(
        workspace,
        approved.run_dir.name,
        child_run=child_run,
        operation_id="operation-original",
    )
    repair_state = bound.state.model_copy(
        update={
            "state_version": bound.state.state_version + 1,
            "active_operation_id": "operation-repair",
        }
    )
    append_agent_trace(
        bound.run_dir / "trace.jsonl",
        event="worker_dispatch_committed",
        state=repair_state,
        observation_summary="同一 child 的 repair 已绑定新 operation",
    )
    display_state = repair_state.model_copy(
        update={
            "phase": "ready",
            "active_child_run": None,
            "active_operation_id": None,
            "operation_started": False,
        }
    )
    stale_observation = AgentObservation(
        observation_id="obs-stale-operation",
        work_item_id="W1",
        child_run=child_run,
        operation_id="operation-original",
        machine_summary="旧 operation 的可信 Observation",
        workspace_fingerprint="0" * 64,
        authority="machine_reconcile",
    )
    status_path = bound.run_dir / "status-card.md"
    original_status = status_path.read_bytes()

    with pytest.raises(
        ValueError,
        match="可信 Observation 与最近 dispatch Trace 的 operation 不一致",
    ):
        agent_runtime_support_module.write_status_card(
            bound.run_dir,
            display_state,
            bound.plan,
            observation=stale_observation,
        )

    assert status_path.read_bytes() == original_status
    current_observation = stale_observation.model_copy(
        update={
            "observation_id": "obs-current-operation",
            "operation_id": "operation-repair",
        }
    )
    agent_runtime_support_module.write_status_card(
        bound.run_dir,
        display_state,
        bound.plan,
        observation=current_observation,
    )
    assert f"Worker：{child_run}" in status_path.read_text(encoding="utf-8")


def test_reconcile_rejects_trace_operation_mismatch_before_artifact_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-trace-mismatch",
        operation_id="operation-current",
    )
    trace_path = run.run_dir / "trace.jsonl"
    trace_items = read_agent_trace(trace_path)
    dispatch = next(
        item
        for item in trace_items
        if item.get("event") == "worker_dispatch_committed"
    )
    dispatch["operation_id"] = "operation-tampered"
    trace_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in trace_items
        ),
        encoding="utf-8",
        newline="\n",
    )

    checkpoint_path = (
        run.run_dir
        / "checkpoints"
        / f"{run.state.latest_checkpoint_id}.json"
    )
    protected_paths = [
        run.run_dir / "agent-plan.json",
        run.run_dir / "agent-state.json",
        trace_path,
        run.run_dir / "status-card.md",
        checkpoint_path,
    ]
    original_bytes = {
        path: path.read_bytes()
        for path in protected_paths
    }
    original_observations = set((run.run_dir / "observations").glob("*.json"))
    original_decisions = set((run.run_dir / "decisions").glob("*.json"))
    original_checkpoints = set((run.run_dir / "checkpoints").glob("*.json"))

    with pytest.raises(
        ValueError,
        match="active operation 与最近可信 dispatch Trace 不一致",
    ):
        runtime.observe_machine(
            run.run_dir.name,
            AgentObservation(
                observation_id="obs-trace-mismatch",
                work_item_id="W1",
                child_run="attempt-trace-mismatch",
                operation_id="operation-current",
                machine_summary="机器已重新采集全部门禁证据",
                workspace_fingerprint="0" * 64,
                evidence_refs=["children/trace-mismatch.json"],
                work_item_completed=True,
                all_work_items_completed=True,
                verification="passed",
                risk="passed",
                review="passed",
            ),
        )

    assert {
        path: path.read_bytes()
        for path in protected_paths
    } == original_bytes
    assert set((run.run_dir / "observations").glob("*.json")) == (
        original_observations
    )
    assert set((run.run_dir / "decisions").glob("*.json")) == original_decisions
    assert set((run.run_dir / "checkpoints").glob("*.json")) == (
        original_checkpoints
    )


def test_agent_parent_watch_includes_bound_child_safe_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    plan = _single_item_plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    child_run = "20260816-120000-child-loop"
    _started_worker(
        workspace,
        approved.run_dir.name,
        child_run=child_run,
        operation_id="operation-watch-child",
    )
    child_dir = workspace / "runs" / child_run
    child_dir.mkdir()
    RunProgressLog(child_dir).append(
        "worker.command_started",
        elapsed_seconds=2,
    )

    watch = CliRunner().invoke(
        app,
        ["watch", "--run", run.run_dir.name, "--no-follow"],
    )

    assert watch.exit_code == 0, watch.output
    assert f"run={run.run_dir.name} status=running step=acting" in watch.output
    assert "worker / 开始执行命令" in watch.output
    assert f"child={child_run}" in watch.output


def test_fake_worker_two_items_route_next_then_finalize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = AgentPlan(
        task_id="task-two-items",
        user_goal="完成两个串行修改",
        success_conditions=["两个定向检查通过"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="写入第一项",
                allowed_paths=["one.txt"],
                verification=["检查 one.txt"],
            ),
            AgentWorkItem(
                work_item_id="W2",
                objective="写入第二项",
                allowed_paths=["two.txt"],
                verification=["检查 two.txt"],
            ),
        ],
    )
    monkeypatch.chdir(workspace)
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    first = _started_worker(
        workspace,
        approved.run_dir.name,
        child_run="attempt-01",
        operation_id="operation-01",
    )
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    first_result = runtime.observe_fake_worker(
        first.run_dir.name,
        AgentObservation(
            observation_id="obs-one",
            work_item_id="W1",
            child_run="attempt-01",
            operation_id="operation-01",
            machine_summary="第一项文件已落盘",
            workspace_fingerprint="0" * 64,
            work_item_completed=True,
            verification="passed",
            risk="passed",
            review="passed",
        ),
    )

    assert first_result.state.phase == "ready"
    assert first_result.state.current_work_item == "W2"
    assert first_result.plan.work_items[0].status == "completed"

    second = _started_worker(
        workspace,
        first_result.run_dir.name,
        child_run="attempt-02",
        operation_id="operation-02",
    )
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    final = runtime.observe_fake_worker(
        second.run_dir.name,
        AgentObservation(
            observation_id="obs-two",
            work_item_id="W2",
            child_run="attempt-02",
            operation_id="operation-02",
            machine_summary="第二项和全部门禁已完成",
            workspace_fingerprint="0" * 64,
            work_item_completed=True,
            all_work_items_completed=True,
            verification="passed",
            risk="passed",
            review="passed",
        ),
    )

    assert final.state.phase == "finalizing"
    assert final.state.terminal_status is None
    assert final.plan.work_items[1].status == "completed"
    assert "采用同一证据发布 Supervisor completed" in runtime.status(
        final.run_dir.name
    )
    events = [item["event"] for item in read_agent_trace(final.run_dir / "trace.jsonl")]
    routed_events = [
        event
        for event in events
        if event in {
            "agent_started",
            "plan_approved",
            "worker_dispatch_committed",
            "supervisor_next",
            "supervisor_finalize",
        }
    ]
    assert routed_events == [
        "agent_started",
        "plan_approved",
        "worker_dispatch_committed",
        "supervisor_next",
        "worker_dispatch_committed",
        "supervisor_finalize",
    ]


def test_machine_observation_can_advance_but_external_claim_cannot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-machine",
        operation_id="operation-machine",
    )

    machine = runtime.observe_machine(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-machine",
            work_item_id="W1",
            child_run="attempt-machine",
            operation_id="operation-machine",
            machine_summary="机器已重新采集 Workspace 与门禁证据",
            workspace_fingerprint="0" * 64,
            evidence_refs=["operations/machine.json"],
            work_item_completed=True,
            all_work_items_completed=True,
            verification="passed",
            risk="passed",
            review="passed",
        ),
    )

    assert machine.state.phase == "finalizing"
    saved = json.loads(
        (machine.run_dir / "observations" / "obs-machine.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["authority"] == "machine_reconcile"


def test_blocked_risk_gate_routes_human_instead_of_replan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-risk-human",
        operation_id="operation-risk-human",
    )

    routed = runtime.observe_machine(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-risk-human",
            work_item_id="W1",
            child_run="attempt-risk-human",
            operation_id="operation-risk-human",
            machine_summary="Risk Gate 要求人工审查",
            workspace_fingerprint="0" * 64,
            evidence_refs=["children/risk-human.json"],
            risk="blocked",
        ),
    )

    assert routed.state.phase == "needs_human"
    assert routed.state.allowed_actions == ["human"]


def test_fake_worker_failure_routes_repair_and_unknown_side_effect_routes_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-repair",
        operation_id="operation-repair",
    )

    repair = runtime.observe_fake_worker(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-repair",
            work_item_id="W1",
            child_run="attempt-repair",
            operation_id="operation-repair",
            machine_summary="验证失败但可原范围修复",
            workspace_fingerprint="0" * 64,
            verification="failed",
            repairable_in_scope=True,
        ),
    )
    assert repair.state.phase == "ready"
    assert repair.plan.work_items[0].status == "active"

    run = _started_worker(
        workspace,
        repair.run_dir.name,
        child_run="attempt-human",
        operation_id="operation-human",
    )
    human = runtime.observe_fake_worker(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-human",
            work_item_id="W1",
            child_run="attempt-human",
            operation_id="operation-human",
            machine_summary="外部副作用未知",
            workspace_fingerprint="0" * 64,
            external_side_effects="unknown",
        ),
    )
    assert human.state.phase == "needs_human"
    assert human.state.allowed_actions == ["human"]


@pytest.mark.parametrize(
    ("child_run", "operation_id", "work_item_id", "error"),
    [
        ("attempt-old", "operation-current", "W1", "Writer binding"),
        ("attempt-current", "operation-old", "W1", "Writer binding"),
        ("attempt-current", "operation-current", "W9", "Work Item"),
    ],
)
def test_trusted_observation_must_match_current_execution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_run: str,
    operation_id: str,
    work_item_id: str,
    error: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-current",
        operation_id="operation-current",
    )

    with pytest.raises(ValueError, match=error):
        runtime.observe_fake_worker(
            run.run_dir.name,
            AgentObservation(
                observation_id=f"obs-{child_run}-{operation_id}-{work_item_id}",
                work_item_id=work_item_id,
                child_run=child_run,
                operation_id=operation_id,
                machine_summary="伪造旧 attempt 或错误 Work Item",
                workspace_fingerprint="0" * 64,
                work_item_completed=True,
            ),
        )

    assert not any((run.run_dir / "observations").glob("obs-*.json"))


def test_duplicate_writer_and_workspace_drift_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-01",
        operation_id="operation-01",
    )

    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        SupervisorAgentWorker(workspace).bind(
            run.run_dir.name,
            child_run="attempt-02",
            operation_id="operation-02",
        )

    runtime = SupervisorAgentRuntime(workspace)
    second_run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    second_run = runtime.approve(second_run.run_dir.name)
    (repo / "drift.txt").write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Workspace 已漂移"):
        SupervisorAgentWorker(workspace).bind(
            second_run.run_dir.name,
            child_run="attempt-drift",
            operation_id="operation-drift",
        )


def test_steer_invalidates_old_plan_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    approved = runtime.approve(run.run_dir.name)

    steered = runtime.steer(
        approved.run_dir.name,
        instruction="禁止修改数据库迁移文件",
    )

    assert steered.state.phase == "awaiting_approval"
    assert steered.state.approved_plan_digest is None
    assert steered.plan.plan_revision == 2
    assert not steered.plan.approved
    assert "禁止修改数据库迁移文件" in steered.plan.unresolved_decisions[-1]


def test_plan_write_failure_revokes_dispatch_before_plan_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    for index, operation in enumerate(("update_plan", "steer"), start=1):
        runtime = SupervisorAgentRuntime(workspace)
        run = runtime.start(
            repo,
            goal=f"修复问题 {index}",
            plan=_single_item_plan(
                task_id=f"task-plan-failure-{index}",
                user_goal=f"修复问题 {index}",
            ),
        )
        approved = runtime.approve(run.run_dir.name)

        def fail_plan_write(*args, **kwargs):
            raise OSError("simulated plan write failure")

        monkeypatch.setattr(runtime, "_save_plan", fail_plan_write)
        with pytest.raises(OSError, match="simulated plan write failure"):
            if operation == "update_plan":
                runtime.update_plan(
                    approved.run_dir.name,
                    _single_item_plan(
                        task_id=approved.plan.task_id,
                        user_goal=approved.plan.user_goal,
                    ),
                )
            else:
                runtime.steer(
                    approved.run_dir.name,
                    instruction="新增人工约束",
                )

        state = load_agent_state(approved.run_dir / "agent-state.json")
        assert state.phase == "awaiting_approval"
        assert state.approved_plan_digest is None
        with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
            SupervisorAgentWorker(workspace).bind(
                approved.run_dir.name,
                child_run=f"attempt-blocked-{index}",
                operation_id=f"operation-blocked-{index}",
            )


def test_approve_artifact_failure_never_publishes_ready_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    for index, target in enumerate(("checkpoint", "task_brief"), start=1):
        runtime = SupervisorAgentRuntime(workspace)
        plan = _single_item_plan(
            task_id=f"task-approve-failure-{index}",
            user_goal=f"批准失败现场 {index}",
        )
        run = runtime.start(repo, goal=plan.user_goal, plan=plan)
        attribute = "write_checkpoint" if target == "checkpoint" else "write_task_brief"
        original = getattr(agent_runtime_module, attribute)

        def fail_artifact(*args, **kwargs):
            raise OSError(f"simulated {target} failure")

        monkeypatch.setattr(agent_runtime_module, attribute, fail_artifact)
        with pytest.raises(OSError, match=f"simulated {target} failure"):
            runtime.approve(run.run_dir.name)
        monkeypatch.setattr(agent_runtime_module, attribute, original)

        state = load_agent_state(run.run_dir / "agent-state.json")
        assert state.phase == "awaiting_approval"
        assert state.active_child_run is None
        with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
            SupervisorAgentWorker(workspace).bind(
                run.run_dir.name,
                child_run=f"attempt-blocked-{index}",
                operation_id=f"operation-blocked-{index}",
            )


@pytest.mark.parametrize(
    ("attribute", "label"),
    [
        ("write_checkpoint", "checkpoint"),
        ("write_task_brief", "task brief"),
    ],
)
def test_observe_artifact_failure_keeps_plan_and_state_unpublished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    label: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    plan = AgentPlan(
        task_id="task-observe-failure",
        user_goal="验证 Observation 发布顺序",
        success_conditions=["两个 Work Item 完成"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="完成第一项",
                allowed_paths=["one.txt"],
                verification=["检查第一项"],
            ),
            AgentWorkItem(
                work_item_id="W2",
                objective="完成第二项",
                allowed_paths=["two.txt"],
                verification=["检查第二项"],
            ),
        ],
    )
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-observe-failure",
        operation_id="operation-observe-failure",
    )
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    plan_path = run.run_dir / "agent-plan.json"
    state_path = run.run_dir / "agent-state.json"
    original_plan = plan_path.read_bytes()
    original_state = state_path.read_bytes()

    def fail_artifact(*args, **kwargs):
        raise OSError(f"simulated {label} failure")

    monkeypatch.setattr(agent_runtime_module, attribute, fail_artifact)
    observation = AgentObservation(
        observation_id=f"obs-{label.replace(' ', '-')}",
        work_item_id="W1",
        child_run="attempt-observe-failure",
        operation_id="operation-observe-failure",
        machine_summary="第一项已完成",
        workspace_fingerprint="0" * 64,
        work_item_completed=True,
        verification="passed",
        risk="passed",
        review="passed",
    )

    with pytest.raises(OSError, match=f"simulated {label} failure"):
        runtime.observe_fake_worker(run.run_dir.name, observation)

    assert plan_path.read_bytes() == original_plan
    assert state_path.read_bytes() == original_state
    preserved = load_agent_state(state_path)
    assert preserved.phase == "acting"
    assert preserved.active_child_run == "attempt-observe-failure"
    assert preserved.active_operation_id == "operation-observe-failure"
    with pytest.raises(ValueError, match="拒绝覆盖历史证据"):
        runtime.observe_fake_worker(run.run_dir.name, observation)


def test_dispatch_rejects_stale_plan_or_task_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    runtime = SupervisorAgentRuntime(workspace)
    stale_run = runtime.start(repo, goal="拒绝 stale Plan", plan=_single_item_plan(
        task_id="task-stale-plan",
        user_goal="拒绝 stale Plan",
    ))
    stale_run = runtime.approve(stale_run.run_dir.name)
    stale_plan = stale_run.plan.model_copy(
        update={
            "plan_revision": stale_run.plan.plan_revision + 1,
            "approved": False,
            "approved_at": None,
            "approved_by": None,
            "approved_digest": None,
        }
    )
    (stale_run.run_dir / "agent-plan.json").write_text(
        stale_plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="Agent State 与当前批准 Plan 不一致"):
        SupervisorAgentWorker(workspace).bind(
            stale_run.run_dir.name,
            child_run="attempt-stale-plan",
            operation_id="operation-stale-plan",
        )

    brief_run = runtime.start(
        repo,
        goal="拒绝 stale Brief",
        plan=_single_item_plan(
            task_id="task-stale-brief",
            user_goal="拒绝 stale Brief",
        ),
    )
    brief_run = runtime.approve(brief_run.run_dir.name)
    manifest_path = brief_run.run_dir / "task-brief-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["checkpoint_id"] = "checkpoint-stale"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="Task Brief 与当前 Plan"):
        SupervisorAgentWorker(workspace).bind(
            brief_run.run_dir.name,
            child_run="attempt-stale-brief",
            operation_id="operation-stale-brief",
        )


def test_external_observation_cannot_finalize_or_release_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-forged",
        operation_id="operation-forged",
    )

    observed = runtime.observe(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-forged",
            work_item_id="W1",
            child_run="attempt-fake",
            operation_id="operation-fake",
            machine_summary="外部输入声称全部通过",
            workspace_fingerprint="0" * 64,
            authority="fake_worker",
            work_item_completed=True,
            all_work_items_completed=True,
            verification="passed",
            risk="passed",
            review="passed",
        ),
    )

    assert observed.state.phase == "needs_human"
    assert observed.state.active_child_run == "attempt-forged"
    assert observed.state.active_operation_id == "operation-forged"
    assert observed.plan.work_items[0].status == "pending"
    saved = json.loads(
        (
            observed.run_dir / "observations" / "obs-forged.json"
        ).read_text(encoding="utf-8")
    )
    assert saved["authority"] == "external_claim"
    assert saved["verification"] == "not_run"
    assert saved["review"] == "not_run"
    assert saved["work_item_completed"] is False


def test_duplicate_observation_id_cannot_overwrite_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="修复问题", plan=_single_item_plan())
    run = runtime.approve(run.run_dir.name)
    run = _started_worker(
        workspace,
        run.run_dir.name,
        child_run="attempt-duplicate",
        operation_id="operation-duplicate",
    )
    observation_path = run.run_dir / "observations" / "obs-duplicate.json"

    runtime.observe(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-duplicate",
            machine_summary="第一次外部 Claim",
            workspace_fingerprint="0" * 64,
        ),
    )
    original = observation_path.read_bytes()

    with pytest.raises(ValueError, match="拒绝覆盖历史证据"):
        runtime.observe(
            run.run_dir.name,
            AgentObservation(
                observation_id="obs-duplicate",
                machine_summary="第二次外部 Claim",
                workspace_fingerprint="0" * 64,
            ),
        )

    assert observation_path.read_bytes() == original


def test_default_start_requires_investigation_plan_before_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal="用户只知道现象，不知道根因")

    with pytest.raises(ValueError, match="仍有未解决决策"):
        runtime.approve(run.run_dir.name)

    draft = AgentPlan(
        task_id=run.plan.task_id,
        user_goal=run.plan.user_goal,
        observed_facts=["已通过失败测试定位入口"],
        hypotheses=["超时检查可能被高频输出饿死"],
        success_conditions=["定向测试通过"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="修复轮询预算",
                allowed_paths=["src/example.py"],
                verification=["运行定向测试"],
            )
        ],
    )
    planned = runtime.update_plan(run.run_dir.name, draft)
    approved = runtime.approve(planned.run_dir.name)

    assert planned.plan.plan_revision == 2
    assert approved.state.phase == "ready"
    assert approved.plan.approval_is_current()


def test_langgraph_route_and_interrupt_are_visible() -> None:
    graph = compile_gate1_graph()
    next_result = graph.invoke(
        {
            "run_id": "run-next",
            "phase": "observing",
            "route": "next",
            "route_reason": "当前项完成",
        },
        {"configurable": {"thread_id": "thread-next"}},
    )
    human_result = graph.invoke(
        {
            "run_id": "run-human",
            "phase": "needs_human",
            "route": "human",
            "route_reason": "副作用未知",
        },
        {"configurable": {"thread_id": "thread-human"}},
    )

    assert "__interrupt__" not in next_result
    assert human_result["__interrupt__"][0].value["reason"] == "副作用未知"
    state = graph.get_state({"configurable": {"thread_id": "thread-human"}})
    assert state.next == ("await_human",)


def test_agent_cli_status_card_and_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan_path = workspace / "plan.json"
    plan_path.write_text(
        _single_item_plan().model_dump_json(indent=2),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    started = CliRunner().invoke(
        app,
        [
            "agent",
            "start",
            "--repo",
            str(repo),
            "--text",
            "修复问题",
            "--plan",
            str(plan_path),
        ],
    )
    capabilities = CliRunner().invoke(app, ["agent", "capabilities"])

    assert started.exit_code == 0, started.output
    assert "阶段：等待批准" in started.output
    assert capabilities.exit_code == 0
    capability_payload = json.loads(capabilities.output)
    assert capability_payload["langgraph"] is True
    assert capability_payload["worker"] == "codex-exec"
    agent_help = CliRunner().invoke(app, ["agent", "--help"])
    assert agent_help.exit_code == 0
    assert "run" in agent_help.output
    assert "finalize" in agent_help.output
    finalize_help = CliRunner().invoke(app, ["agent", "finalize", "--help"])
    assert finalize_help.exit_code == 0
    assert "--run" in _ANSI_ESCAPE_PATTERN.sub("", finalize_help.output)


def test_packaged_cli_entrypoint_preserves_core_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for command in ("do", "loop", "goal", "agent"):
        assert command in result.output


def test_resume_tracked_task_card_rebuilds_local_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "feature/resume")
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    plan = _single_item_plan()
    from vega.agent_contract import approve_plan

    plan = approve_plan(plan, actor="user", approved_at="2026-08-13T00:00:00+00:00")
    digest = compute_handoff_workspace_digest(repo, ["src/example.py"])
    card = AgentTaskCard(
        task_id=plan.task_id,
        status="paused",
        branch="feature/resume",
        base_revision=_head(repo),
        plan=plan,
        current_work_item="W1",
        handoff_sequence=1,
        handoff_status="handoff_ready",
        handoff_base_revision=_head(repo),
        handoff_workspace_digest=digest,
        last_handoff_checkpoint="checkpoint-009",
        resume_capsule=ResumeCapsule(
            current_work_item="W1",
            stopped_at="写入实现后",
            confirmed_facts=["实现文件已创建"],
            changed_files=["src/example.py"],
            workspace_digest=digest,
            writer_stopped=True,
            workspace_explained=True,
            allowed_actions=["repair", "human"],
            next_step="先重新验证当前实现",
        ),
    )
    task_path = repo / ".vega" / "tasks" / "2026-08" / "resume.md"
    save_task_card(task_path, card)
    _git(repo, "add", "src/example.py", ".vega/tasks/2026-08/resume.md")
    _git(repo, "commit", "-m", "测试：保存未完成任务交接")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    failure_targets = (
        ("write_task_brief", "task brief"),
        ("append_agent_trace", "trace"),
        ("write_status_card", "status card"),
    )
    for attribute, label in failure_targets:
        original = getattr(agent_runtime_support_module, attribute)

        def fail_artifact(*args, _label=label, **kwargs):
            raise OSError(f"simulated {_label} failure")

        monkeypatch.setattr(agent_runtime_support_module, attribute, fail_artifact)
        with pytest.raises(OSError, match=f"simulated {label} failure"):
            SupervisorAgentRuntime(workspace).resume_task_card(repo)
        monkeypatch.setattr(agent_runtime_support_module, attribute, original)
        failed_runs = sorted((workspace / "runs").glob("*-agent-resume*"))
        assert all(
            not (failed_run / "agent-state.json").exists()
            for failed_run in failed_runs
        )

    failed_runs = sorted((workspace / "runs").glob("*-agent-resume*"))
    assert len(failed_runs) == len(failure_targets)

    restored = SupervisorAgentRuntime(workspace).resume_task_card(repo)
    published_states = sorted(
        run_dir / "agent-state.json"
        for run_dir in (workspace / "runs").glob("*-agent-resume*")
        if (run_dir / "agent-state.json").exists()
    )

    assert restored.state.phase == "ready"
    assert published_states == [restored.run_dir / "agent-state.json"]
    assert restored.state.handoff_status == "handoff_ready"
    metadata = json.loads(
        (restored.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    assert metadata["comparison_base_revision"] == card.handoff_base_revision
    assert metadata["comparison_paths"] == ["src/example.py"]
    checkpoint = json.loads(
        next((restored.run_dir / "checkpoints").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["data"]["changed_files"] == ["src/example.py"]
    assert "先重新验证当前实现" in (
        restored.run_dir / "status-card.md"
    ).read_text(encoding="utf-8")
    assert "旧门禁已作为历史证据" in (
        restored.run_dir / "trace.jsonl"
    ).read_text(encoding="utf-8")


def _single_item_plan(
    *,
    task_id: str = "task-single",
    user_goal: str = "修复问题",
) -> AgentPlan:
    return AgentPlan(
        task_id=task_id,
        user_goal=user_goal,
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


def _started_worker(
    workspace: Path,
    run: str,
    *,
    child_run: str,
    operation_id: str,
):
    return SupervisorAgentWorker(workspace).bind(
        run,
        child_run=child_run,
        operation_id=operation_id,
    )


def _repo(path: Path) -> Path:
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
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr


def _head(repo: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return process.stdout.strip()
