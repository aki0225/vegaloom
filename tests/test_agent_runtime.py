from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentWorkItem,
)
from vega.agent_graph import compile_gate1_graph
from vega.agent_persistence import read_agent_trace
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_worker import SupervisorAgentWorker
from vega.agent_task_card import (
    AgentTaskCard,
    ResumeCapsule,
    compute_handoff_workspace_digest,
    save_task_card,
)
from vega.cli_entrypoint import app


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
    first_result = runtime.observe(
        first.run_dir.name,
        AgentObservation(
            observation_id="obs-one",
            work_item_id="W1",
            machine_summary="第一项文件已落盘",
            workspace_fingerprint="0" * 64,
            work_item_completed=True,
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
    final = runtime.observe(
        second.run_dir.name,
        AgentObservation(
            observation_id="obs-two",
            work_item_id="W2",
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
    assert "调用现有 Vega Finish" in runtime.status(final.run_dir.name)
    events = [item["event"] for item in read_agent_trace(final.run_dir / "trace.jsonl")]
    assert events == [
        "agent_started",
        "plan_approved",
        "worker_started",
        "supervisor_next",
        "worker_started",
        "supervisor_finalize",
    ]


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

    repair = runtime.observe(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-repair",
            work_item_id="W1",
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
    human = runtime.observe(
        run.run_dir.name,
        AgentObservation(
            observation_id="obs-human",
            work_item_id="W1",
            machine_summary="外部副作用未知",
            workspace_fingerprint="0" * 64,
            external_side_effects="unknown",
        ),
    )
    assert human.state.phase == "needs_human"
    assert human.state.allowed_actions == ["human"]


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
    assert json.loads(capabilities.output)["langgraph"] is True


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

    restored = SupervisorAgentRuntime(workspace).resume_task_card(repo)

    assert restored.state.phase == "ready"
    assert restored.state.handoff_status == "handoff_ready"
    assert "先重新验证当前实现" in (
        restored.run_dir / "status-card.md"
    ).read_text(encoding="utf-8")
    assert "旧门禁已作为历史证据" in (
        restored.run_dir / "trace.jsonl"
    ).read_text(encoding="utf-8")


def _single_item_plan() -> AgentPlan:
    return AgentPlan(
        task_id="task-single",
        user_goal="修复问题",
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
        operation_started=True,
    )


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
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
