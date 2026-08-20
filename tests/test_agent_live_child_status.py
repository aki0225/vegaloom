from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega.agent_contract import AgentObservation, AgentPlan, AgentWorkItem
from vega.agent_persistence import save_agent_state
from vega.cli_entrypoint import app
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_worker import SupervisorAgentWorker
from vega.models import LoopAutomationState
from vega.run_status import render_run_status, run_status_payload


@pytest.mark.parametrize(
    ("phase", "current_step"),
    [
        ("acting", "verify"),
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
    assert f"- Core 子流程：`{current_step}`" in text


def test_agent_status_waits_when_child_state_has_not_been_persisted(
    tmp_path: Path,
) -> None:
    workspace, _, parent, child_dir = _acting_parent(tmp_path)
    child_dir.rmdir()

    payload = run_status_payload(workspace, parent.run_dir.name)

    assert payload["agent_phase"] == "acting"
    assert payload["current_step"] == "acting"
    assert payload["live_child_stage"] == "等待子流程状态"


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
        ["agent", "status", "--run", parent.run_dir.name],
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
) -> None:
    state = LoopAutomationState(
        run_id=run_id or child_dir.name,
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="测试",
        status="running",
        current_step=current_step,
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
