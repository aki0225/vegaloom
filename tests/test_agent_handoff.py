from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.agent_handoff as agent_handoff_module
from vega.agent_contract import AgentPlan, AgentWorkItem
from vega.agent_persistence import load_agent_state
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_task_card import TaskCardError, load_task_card
from vega.cli_entrypoint import app


def test_cli_handoff_writes_card_checkpoint_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    monkeypatch.chdir(workspace)

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "checkpoint",
            "--run",
            run_id,
            "--handoff",
            "--reason",
            "准备换机继续当前 Work Item",
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = workspace / "runs" / run_id
    state = load_agent_state(run_dir / "agent-state.json")
    assert state.handoff_status == "handoff_ready"
    assert "Handoff 已生成" in result.output

    manifest = json.loads((run_dir / "handoff-manifest.json").read_text(encoding="utf-8"))
    card_path = repo / manifest["task_card"]
    card = load_task_card(card_path)

    assert card.status == "ready"
    assert card.handoff_status == "handoff_ready"
    assert card.resume_capsule is not None
    assert card.resume_capsule.writer_stopped is True
    assert card.resume_capsule.changed_files == ["src/example.py"]
    assert manifest["task_card_sha256"]
    summary = (run_dir / "handoff-summary.md").read_text(encoding="utf-8")
    assert "git diff --cached --check" in summary
    assert str(tmp_path) not in summary
    assert str(tmp_path) not in card_path.read_text(encoding="utf-8")


def test_handoff_rejects_active_writer(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = _plan()
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)

    from vega.agent_worker import SupervisorAgentWorker

    SupervisorAgentWorker(workspace).bind(
        approved.run_dir.name,
        child_run="attempt-live",
        operation_id="operation-live",
    )

    with pytest.raises(ValueError, match="active binding"):
        runtime.handoff(approved.run_dir.name, reason="准备换机")


def test_handoff_records_workspace_drift_as_blocked(tmp_path: Path) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    (repo / "src" / "example.py").write_text("value = 2\n", encoding="utf-8")

    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="发现停下后文件又发生变化",
    )

    assert result.handoff_status == "handoff_blocked"
    card = load_task_card(result.task_card_path)
    assert card.handoff_status == "handoff_blocked"
    assert card.resume_capsule is not None
    assert card.resume_capsule.workspace_explained is False
    assert card.resume_capsule.allowed_actions == ["human"]
    assert any("Workspace fingerprint" in value for value in card.risk_notes)


def test_handoff_round_trip_between_isolated_clones(tmp_path: Path) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="同机隔离副本往返验证",
    )
    _git(repo, "add", "src/example.py", result.task_card_path.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "测试：提交 Handoff WIP")

    clone = tmp_path / "clone"
    _git(tmp_path, "-c", "core.autocrlf=false", "clone", str(repo), str(clone))
    _git(clone, "config", "core.autocrlf", "false")
    next_workspace = tmp_path / "next-workspace"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(clone)

    assert restored.state.phase == "ready"
    assert restored.state.handoff_status == "handoff_ready"
    assert restored.state.current_work_item == "W1"
    assert "重新对账" in (
        next_workspace / "runs" / restored.run_dir.name / "status-card.md"
    ).read_text(encoding="utf-8")
    status_card = (restored.run_dir / "status-card.md").read_text(encoding="utf-8")
    assert "Verification：尚未运行" in status_card
    assert "Risk：尚未运行" in status_card
    assert "Reviewer：尚未运行" in status_card


def test_needs_human_handoff_remains_blocked_after_clone(tmp_path: Path) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    paused = SupervisorAgentRecovery(workspace).pause(
        run_id,
        reason="人工暂停并准备转移现场",
    )

    result = SupervisorAgentRuntime(workspace).handoff(
        paused.run_dir.name,
        reason="把等待人工的现场转移到隔离副本",
    )

    assert result.handoff_status == "handoff_blocked"
    card = load_task_card(result.task_card_path)
    assert card.status == "needs_human"
    assert card.resume_capsule is not None
    assert card.resume_capsule.allowed_actions == ["human"]

    _git(repo, "add", "src/example.py", result.task_card_path.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "测试：提交 blocked Handoff")
    clone = tmp_path / "clone-blocked"
    _git(tmp_path, "-c", "core.autocrlf=false", "clone", str(repo), str(clone))
    _git(clone, "config", "core.autocrlf", "false")
    next_workspace = tmp_path / "next-workspace-blocked"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(clone)

    assert restored.state.phase == "needs_human"
    assert restored.state.allowed_actions == ["human"]


def test_handoff_rejects_task_card_directory_escape(tmp_path: Path) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, repo / ".vega", target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不能创建目录符号链接")

    with pytest.raises(TaskCardError, match="链接|junction|reparse"):
        SupervisorAgentRuntime(workspace).handoff(
            run_id,
            reason="验证 Task Card 路径边界",
        )

    assert not list(outside.rglob("*.md"))


def test_handoff_rejects_local_absolute_path_in_task_card(tmp_path: Path) -> None:
    local_path = r"C:\Users\Example\private.txt"  # repo-path-policy: allow-test-fixture
    plan = _plan(observed_facts=[f"本机调查文件位于 {local_path}"])
    repo, workspace, run_id = _stopped_run(tmp_path, plan=plan)

    with pytest.raises(TaskCardError, match="本机绝对路径"):
        SupervisorAgentRuntime(workspace).handoff(
            run_id,
            reason="验证公开 Task Card 不携带本机路径",
        )

    assert not list((repo / ".vega" / "tasks").rglob("*.md"))


def test_handoff_redacts_fake_key_from_all_artifacts(tmp_path: Path) -> None:
    fake_secret = "sk-test-handoff-secret-123456"
    plan = _plan(observed_facts=[f"临时 API key={fake_secret}"])
    repo, workspace, run_id = _stopped_run(tmp_path, plan=plan)

    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason=f"交接前再次确认 token={fake_secret}",
    )

    run_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in result.run.run_dir.rglob("*")
        if path.is_file()
    )
    card_text = result.task_card_path.read_text(encoding="utf-8")
    assert fake_secret not in run_text
    assert fake_secret not in card_text
    assert "[REDACTED]" in run_text
    assert "[REDACTED]" in card_text


@pytest.mark.parametrize(
    "failed_writer",
    ["manifest", "summary", "state", "trace", "status"],
)
def test_handoff_artifact_failure_does_not_publish_task_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_writer: str,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    if failed_writer == "manifest":
        original = agent_handoff_module.write_redacted_json

        def fail_manifest(path: Path, payload: object) -> None:
            if path.name == "handoff-manifest.json":
                raise OSError("simulated manifest failure")
            original(path, payload)

        monkeypatch.setattr(agent_handoff_module, "write_redacted_json", fail_manifest)
    elif failed_writer == "summary":
        original_text = agent_handoff_module.write_redacted_text

        def fail_summary(path: Path, text: str) -> None:
            if path.name == "handoff-summary.md":
                raise OSError("simulated summary failure")
            original_text(path, text)

        monkeypatch.setattr(agent_handoff_module, "write_redacted_text", fail_summary)
    elif failed_writer == "state":
        original_state = agent_handoff_module.save_agent_state

        def fail_state(path: Path, state: object) -> None:
            if getattr(state, "handoff_status", None) != "none":
                raise OSError("simulated state failure")
            original_state(path, state)

        monkeypatch.setattr(agent_handoff_module, "save_agent_state", fail_state)
    elif failed_writer == "trace":
        original_trace = agent_handoff_module.append_agent_trace

        def fail_trace(*args, **kwargs) -> None:
            if kwargs.get("event") == "agent_handoff_created":
                raise OSError("simulated trace failure")
            original_trace(*args, **kwargs)

        monkeypatch.setattr(agent_handoff_module, "append_agent_trace", fail_trace)
    else:
        def fail_status(*args, **kwargs) -> None:
            raise OSError("simulated status failure")

        monkeypatch.setattr(agent_handoff_module, "write_status_card", fail_status)

    with pytest.raises(OSError, match=f"simulated {failed_writer} failure"):
        SupervisorAgentRuntime(workspace).handoff(
            run_id,
            reason="验证失败发布顺序",
        )

    state = load_agent_state(workspace / "runs" / run_id / "agent-state.json")
    assert state.handoff_status == "none"
    assert not list((repo / ".vega" / "tasks").rglob("*.md"))


def test_resume_rejects_head_after_handoff_commit(tmp_path: Path) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证 Handoff 提交身份",
    )
    _git(repo, "add", "src/example.py", result.task_card_path.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "测试：提交 Handoff WIP")
    (repo / "README.md").write_text("fixture updated\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "测试：推进 Handoff 后的 HEAD")
    next_workspace = tmp_path / "next-workspace-after-head"
    next_workspace.mkdir()

    with pytest.raises(ValueError, match="当前 HEAD 不是"):
        SupervisorAgentRuntime(next_workspace).resume_task_card(repo)


def test_resume_rejects_task_card_copied_to_unrelated_history(tmp_path: Path) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证仓库历史绑定",
    )

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _git(unrelated, "init")
    _git(unrelated, "config", "user.name", "Vega Test")
    _git(unrelated, "config", "user.email", "vega@example.invalid")
    _git(unrelated, "config", "core.autocrlf", "false")
    (unrelated / "README.md").write_text("unrelated history\n", encoding="utf-8")
    _git(unrelated, "add", "README.md")
    _git(unrelated, "commit", "-m", "测试：初始化无关仓库")
    (unrelated / "src").mkdir()
    shutil.copyfile(repo / "src" / "example.py", unrelated / "src" / "example.py")
    copied_card = (
        unrelated
        / result.task_card_path.relative_to(repo)
    )
    copied_card.parent.mkdir(parents=True)
    shutil.copyfile(result.task_card_path, copied_card)
    _git(unrelated, "add", "src/example.py", copied_card.relative_to(unrelated).as_posix())
    _git(unrelated, "commit", "-m", "测试：复制错误仓库的 Handoff")
    next_workspace = tmp_path / "unrelated-workspace"
    next_workspace.mkdir()

    with pytest.raises(ValueError, match="Handoff 历史"):
        SupervisorAgentRuntime(next_workspace).resume_task_card(unrelated)


def _stopped_run(
    tmp_path: Path,
    *,
    plan: AgentPlan | None = None,
) -> tuple[Path, Path, str]:
    repo, workspace, run_id = _approved_run(tmp_path, plan=plan)
    stopped = SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="旧 Writer 已停止，准备生成 Handoff",
    )
    assert stopped.state.phase == "stopped"
    return repo, workspace, stopped.run_dir.name


def _approved_run(
    tmp_path: Path,
    *,
    plan: AgentPlan | None = None,
) -> tuple[Path, Path, str]:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    runtime = SupervisorAgentRuntime(workspace)
    current_plan = plan or _plan()
    run = runtime.start(repo, goal=current_plan.user_goal, plan=current_plan)
    approved = runtime.approve(run.run_dir.name)
    return repo, workspace, approved.run_dir.name


def _plan(*, observed_facts: list[str] | None = None) -> AgentPlan:
    return AgentPlan(
        task_id="task-handoff-test",
        user_goal="完成一个可恢复的最小修改",
        non_goals=["不自动提交或推送"],
        success_conditions=["定向验证通过"],
        observed_facts=observed_facts
        or ["src/example.py 是当前 Work Item 的目标文件"],
        hypotheses=["后续需要在新机器重新验证"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="完成最小修改",
                allowed_paths=["src/example.py"],
                verification=["python -c \"from pathlib import Path; assert Path('src/example.py').exists()\""],
            )
        ],
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
