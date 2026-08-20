from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.agent_handoff as agent_handoff_module
import vega.agent_resume_validation as agent_resume_validation_module
import vega.agent_runtime_support as agent_runtime_support_module
import vega.workspace_check as workspace_check_module
from vega.agent_contract import AgentPlan, AgentWorkItem
from vega.agent_persistence import (
    load_agent_checkpoint,
    load_agent_state,
    read_agent_trace,
    save_agent_checkpoint,
)
from vega.agent_handoff_safety import assert_portable_task_card_payload
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_recovery_request import AgentRecoveryRequest
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_side_effect_adjudication import (
    SupervisorAgentSideEffectAdjudicator,
)
from vega.agent_task_card import TaskCardError, load_task_card, render_task_card
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
    assert restored.state.handoff_status == "none"
    assert restored.state.current_work_item == "W1"
    assert "重新对账" in (
        next_workspace / "runs" / restored.run_dir.name / "status-card.md"
    ).read_text(encoding="utf-8")
    status_card = (restored.run_dir / "status-card.md").read_text(encoding="utf-8")
    assert "Verification：尚未运行" in status_card
    assert "Risk：尚未运行" in status_card
    assert "Reviewer：尚未运行" in status_card


def test_direct_task_card_resume_preflights_dependencies_before_creating_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def missing_dependencies() -> None:
        raise ValueError(
            '当前环境缺少 Supervisor Agent 运行依赖；请执行：'
            'python -m pip install "vegaloom[agent]"'
        )

    monkeypatch.setattr(
        agent_runtime_support_module,
        "require_agent_runtime_dependencies",
        missing_dependencies,
    )

    with pytest.raises(ValueError, match=r'vegaloom\[agent\]'):
        agent_runtime_support_module.resume_agent_task_card(workspace, repo)

    assert not (workspace / "runs").exists()


def test_resumed_run_can_adjudicate_new_unknown_side_effects(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证恢复后仍可处理新的 Worker 失败",
    )
    _git(repo, "add", "src/example.py", result.task_card_path.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "测试：提交待恢复 WIP")
    clone = tmp_path / "clone-new-failure"
    _git(tmp_path, "-c", "core.autocrlf=false", "clone", str(repo), str(clone))
    _git(clone, "config", "core.autocrlf", "false")
    next_workspace = tmp_path / "next-workspace-new-failure"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(clone)
    checkpoint_path = next(
        (restored.run_dir / "checkpoints").glob("checkpoint-*.json")
    )
    checkpoint = load_agent_checkpoint(checkpoint_path)
    payload = checkpoint.model_dump(mode="json")
    payload["external_side_effects"] = "unknown"
    save_agent_checkpoint(checkpoint_path, checkpoint.model_validate(payload))
    stopped = SupervisorAgentRecovery(next_workspace).stop(
        restored.run_dir.name,
        reason="新 Worker 已退出，等待核对外部副作用",
    )
    evidence_path = (
        stopped.run_dir / "manual-evidence" / "side-effects-reviewed.md"
    )
    evidence_path.parent.mkdir()
    evidence_path.write_text(
        "已核对新 Worker 的执行记录；本次只有仓库内修改。\n",
        encoding="utf-8",
    )

    adjudicated = SupervisorAgentSideEffectAdjudicator(next_workspace).adjudicate(
        stopped.run_dir.name,
        AgentRecoveryRequest(
            reason="恢复后的新 Worker 未执行仓库外写入",
            external_side_effects="none",
            actor="operator",
            evidence_refs=["manual-evidence/side-effects-reviewed.md"],
        ),
    )

    assert adjudicated.state.phase == "stopped"
    assert adjudicated.state.handoff_status == "none"


def test_resume_rejects_head_change_after_handoff_history_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证恢复校验与发布快照绑定同一 HEAD",
    )
    _git(
        repo,
        "add",
        "src/example.py",
        result.task_card_path.relative_to(repo).as_posix(),
    )
    _git(repo, "commit", "-m", "测试：提交 Handoff WIP")
    original_validate = agent_resume_validation_module.validate_handoff_history

    def advance_head(repo_path, card, relative_task):
        validated_head = original_validate(repo_path, card, relative_task)
        repo_path.joinpath("unexpected.py").write_text(
            "unexpected = True\n",
            encoding="utf-8",
            newline="\n",
        )
        _git(repo_path, "add", "unexpected.py")
        _git(repo_path, "commit", "-m", "测试：模拟恢复期间并发提交")
        return validated_head

    monkeypatch.setattr(
        agent_resume_validation_module,
        "validate_handoff_history",
        advance_head,
    )
    next_workspace = tmp_path / "next-workspace-race"
    next_workspace.mkdir()

    with pytest.raises(ValueError, match="Git HEAD 已漂移"):
        SupervisorAgentRuntime(next_workspace).resume_task_card(repo)

    assert not list((next_workspace / "runs").glob("*-agent-resume*"))


def test_resume_rejects_head_change_during_workspace_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证快照采集期间 HEAD 不能漂移",
    )
    _git(
        repo,
        "add",
        "src/example.py",
        result.task_card_path.relative_to(repo).as_posix(),
    )
    _git(repo, "commit", "-m", "测试：提交 Handoff WIP")
    original_collect = workspace_check_module.collect_committed_diff
    advanced = False

    def advance_head(*args, **kwargs):
        nonlocal advanced
        committed_diff = original_collect(*args, **kwargs)
        if not advanced:
            repo.joinpath("unexpected.py").write_text(
                "unexpected = True\n",
                encoding="utf-8",
                newline="\n",
            )
            _git(repo, "add", "unexpected.py")
            _git(repo, "commit", "-m", "测试：在快照采集中推进 HEAD")
            advanced = True
        return committed_diff

    monkeypatch.setattr(
        workspace_check_module,
        "collect_committed_diff",
        advance_head,
    )
    next_workspace = tmp_path / "next-workspace-capture-race"
    next_workspace.mkdir()

    with pytest.raises(ValueError, match="Git HEAD 已漂移"):
        SupervisorAgentRuntime(next_workspace).resume_task_card(repo)

    assert not list((next_workspace / "runs").glob("*-agent-resume*"))


def test_resume_rejects_task_card_changed_after_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证 Task Card 内容绑定 Handoff HEAD",
    )
    relative_task = result.task_card_path.relative_to(repo).as_posix()
    _git(repo, "add", "src/example.py", relative_task)
    _git(repo, "commit", "-m", "测试：提交 Handoff WIP")
    original_load = agent_runtime_support_module.load_task_card_with_content

    def advance_task_card(path: Path):
        card, content = original_load(path)
        updated = card.model_copy(
            update={"progress_notes": [*card.progress_notes, "并发更新 Task Card"]}
        )
        path.write_text(
            render_task_card(updated),
            encoding="utf-8",
            newline="\n",
        )
        _git(repo, "add", relative_task)
        _git(repo, "commit", "-m", "测试：读取后更新 Task Card")
        return card, content

    monkeypatch.setattr(
        agent_runtime_support_module,
        "load_task_card_with_content",
        advance_task_card,
    )
    next_workspace = tmp_path / "next-workspace-card-race"
    next_workspace.mkdir()

    with pytest.raises(ValueError, match="Task Card 内容与当前 Handoff 提交不一致"):
        SupervisorAgentRuntime(next_workspace).resume_task_card(repo)

    assert not list((next_workspace / "runs").glob("*-agent-resume*"))


def test_resume_rejects_branch_change_during_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证恢复发布前分支身份不漂移",
    )
    _git(
        repo,
        "add",
        "src/example.py",
        result.task_card_path.relative_to(repo).as_posix(),
    )
    _git(repo, "commit", "-m", "测试：提交 Handoff WIP")
    original_capture = agent_resume_validation_module.capture_review_workspace

    def switch_branch(*args, **kwargs):
        snapshot = original_capture(*args, **kwargs)
        _git(repo, "switch", "-c", "concurrent-resume-branch")
        return snapshot

    monkeypatch.setattr(
        agent_resume_validation_module,
        "capture_review_workspace",
        switch_branch,
    )
    next_workspace = tmp_path / "next-workspace-branch-race"
    next_workspace.mkdir()

    with pytest.raises(ValueError, match="Git 分支已漂移"):
        SupervisorAgentRuntime(next_workspace).resume_task_card(repo)

    assert not list((next_workspace / "runs").glob("*-agent-resume*"))


def test_resume_preserves_both_paths_of_committed_rename(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    source = repo / "src" / "old_name.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8", newline="\n")
    _git(repo, "add", source.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "测试：提交 rename 源文件")
    target = repo / "src" / "new_name.py"
    _git(
        repo,
        "mv",
        source.relative_to(repo).as_posix(),
        target.relative_to(repo).as_posix(),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = AgentPlan(
        task_id="task-rename-handoff",
        user_goal="跨机器恢复已提交 rename",
        success_conditions=["rename 两端都进入范围证据"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="保留 rename 的源路径与目标路径",
                allowed_paths=["src/old_name.py", "src/new_name.py"],
                verification=["检查 rename 后文件存在"],
            )
        ],
    )
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    stopped = SupervisorAgentRecovery(workspace).stop(
        approved.run_dir.name,
        reason="准备提交 rename handoff",
    )
    result = runtime.handoff(
        stopped.run_dir.name,
        reason="把 rename 现场转移到新机器",
    )
    card = load_task_card(result.task_card_path)
    assert card.resume_capsule is not None
    assert card.resume_capsule.changed_files == [
        "src/old_name.py",
        "src/new_name.py",
    ]
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "测试：提交 rename Handoff")
    next_workspace = tmp_path / "next-workspace-rename"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(repo)

    assert restored.state.phase == "ready"
    metadata = json.loads(
        (restored.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    assert metadata["comparison_paths"] == [
        "src/old_name.py",
        "src/new_name.py",
    ]


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


@pytest.mark.parametrize(
    "local_path",
    [
        "/home/alice/private.txt",  # repo-path-policy: allow-test-fixture
        "/Users/alice/private.txt",  # repo-path-policy: allow-test-fixture
        "/private/var/folders/vega/private.txt",
        "/tmp/vega-private.txt",
        "/var/folders/vega/private.txt",
        "/var/tmp/vega-private.txt",
    ],
)
def test_handoff_rejects_posix_local_path_in_task_card(
    tmp_path: Path,
    local_path: str,
) -> None:
    plan = _plan(observed_facts=[f"本机调查文件位于 {local_path}"])
    repo, workspace, run_id = _stopped_run(tmp_path, plan=plan)

    with pytest.raises(TaskCardError, match="本机绝对路径"):
        SupervisorAgentRuntime(workspace).handoff(
            run_id,
            reason="验证公开 Task Card 不携带 POSIX 本机路径",
        )

    assert not list((repo / ".vega" / "tasks").rglob("*.md"))


@pytest.mark.parametrize(
    "portable_text",
    [
        "https://example.com/redirect?next=/tmp/reference",
        "https://example.com/var/folders/reference",
        "<worktree-path>/tmp/reference",
        "<worktree-path>/var/folders/reference",
        "tmp/reference",
    ],
)
def test_task_card_path_check_preserves_urls_placeholders_and_relative_paths(
    portable_text: str,
) -> None:
    assert_portable_task_card_payload({"reference": portable_text})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/redirect?next=file:///home/alice/private.txt",  # repo-path-policy: allow-test-fixture
        "https://example.com/redirect?next=C:/Users/Alice/private.txt",  # repo-path-policy: allow-test-fixture
        "https://example.com/redirect?next=%2Fhome%2Falice%2Fprivate.txt",
        "https://example.com/redirect#file%3A%2F%2F%2Ftmp%2Fprivate.txt",
    ],
)
def test_task_card_path_check_rejects_local_path_inside_url(url: str) -> None:
    with pytest.raises(TaskCardError, match="本机绝对路径"):
        assert_portable_task_card_payload({"reference": url})


@pytest.mark.parametrize(
    "local_uri",
    [
        "file:///home/alice/private.txt",  # repo-path-policy: allow-test-fixture
        "file:///C:/Users/Example/private.txt",  # repo-path-policy: allow-test-fixture
        "file:///var/folders/vega/private.txt",
        "file:///var/tmp/vega-private.txt",
        "file://localhost/home/alice/private.txt",
        "file://localhost/tmp/vega-private.txt",
        "file://server/var/folders/vega/private.txt",
    ],
)
def test_task_card_path_check_rejects_local_file_uri(local_uri: str) -> None:
    with pytest.raises(TaskCardError, match="本机绝对路径"):
        assert_portable_task_card_payload({"reference": local_uri})


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
    ["metadata", "manifest", "summary", "state", "trace", "status"],
)
def test_handoff_artifact_failure_does_not_publish_task_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_writer: str,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    state_path = run_dir / "agent-state.json"
    trace_path = run_dir / "trace.jsonl"
    status_path = run_dir / "status-card.md"
    metadata_path = run_dir / "agent-run.json"
    manifest_path = run_dir / "handoff-manifest.json"
    summary_path = run_dir / "handoff-summary.md"
    original_state_bytes = state_path.read_bytes()
    original_trace_bytes = trace_path.read_bytes()
    original_status_bytes = status_path.read_bytes()
    original_metadata_bytes = metadata_path.read_bytes()
    original_checkpoints = {
        path.name: path.read_bytes()
        for path in (run_dir / "checkpoints").glob("checkpoint-*.json")
    }
    if failed_writer == "metadata":
        original_metadata_writer = agent_handoff_module.write_run_metadata

        def fail_metadata(*args: object, **kwargs: object) -> None:
            original_metadata_writer(*args, **kwargs)
            raise OSError("simulated metadata failure")

        monkeypatch.setattr(agent_handoff_module, "write_run_metadata", fail_metadata)
    elif failed_writer == "manifest":
        original = agent_handoff_module.write_redacted_json

        def fail_manifest(path: Path, payload: object) -> None:
            if path.name == "handoff-manifest.json":
                original(path, payload)
                raise OSError("simulated manifest failure")
            original(path, payload)

        monkeypatch.setattr(agent_handoff_module, "write_redacted_json", fail_manifest)
    elif failed_writer == "summary":
        original_text = agent_handoff_module.write_redacted_text

        def fail_summary(path: Path, text: str) -> None:
            if path.name == "handoff-summary.md":
                original_text(path, text)
                raise OSError("simulated summary failure")
            original_text(path, text)

        monkeypatch.setattr(agent_handoff_module, "write_redacted_text", fail_summary)
    elif failed_writer == "state":
        original_state_writer = agent_handoff_module.save_agent_state

        def fail_state(path: Path, state: object) -> None:
            if getattr(state, "handoff_status", None) != "none":
                original_state_writer(path, state)
                raise OSError("simulated state failure")
            original_state_writer(path, state)

        monkeypatch.setattr(agent_handoff_module, "save_agent_state", fail_state)
    elif failed_writer == "trace":
        original_trace_writer = agent_handoff_module.append_agent_trace

        def fail_trace(*args, **kwargs) -> None:
            if kwargs.get("event") == "agent_handoff_created":
                raise OSError("simulated trace failure")
            original_trace_writer(*args, **kwargs)

        monkeypatch.setattr(agent_handoff_module, "append_agent_trace", fail_trace)
    else:
        original_status_writer = agent_handoff_module.write_status_card

        def fail_status(*args, **kwargs) -> None:
            original_status_writer(*args, **kwargs)
            raise OSError("simulated status failure")

        monkeypatch.setattr(agent_handoff_module, "write_status_card", fail_status)

    with pytest.raises(OSError, match=f"simulated {failed_writer} failure"):
        SupervisorAgentRuntime(workspace).handoff(
            run_id,
            reason="验证失败发布顺序",
        )

    state = load_agent_state(state_path)
    assert state.handoff_status == "none"
    assert state_path.read_bytes() == original_state_bytes
    assert trace_path.read_bytes() == original_trace_bytes
    assert status_path.read_bytes() == original_status_bytes
    assert metadata_path.read_bytes() == original_metadata_bytes
    assert not manifest_path.exists()
    assert not summary_path.exists()
    assert {
        path.name: path.read_bytes()
        for path in (run_dir / "checkpoints").glob("checkpoint-*.json")
    } == original_checkpoints
    assert not list((repo / ".vega" / "tasks").rglob("*.md"))


def test_handoff_treats_observable_trace_commit_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _stopped_run(tmp_path)
    original_trace_writer = agent_handoff_module.append_agent_trace

    def write_then_fail(*args, **kwargs) -> None:
        original_trace_writer(*args, **kwargs)
        raise OSError("simulated trace acknowledgement failure")

    monkeypatch.setattr(
        agent_handoff_module,
        "append_agent_trace",
        write_then_fail,
    )

    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证 Trace 已落盘时不回滚已提交 Handoff",
    )

    assert result.run.state.handoff_status == "handoff_ready"
    assert result.task_card_path.is_file()
    events = read_agent_trace(result.run.run_dir / "trace.jsonl")
    assert events[-1]["event"] == "agent_handoff_created"


def test_handoff_failure_preserves_concurrently_created_task_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    concurrent_content = "concurrent task card\n"

    def fail_after_concurrent_publish(path: Path, card: object) -> None:
        del card
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(concurrent_content, encoding="utf-8")
        raise TaskCardError("simulated concurrent Task Card publication")

    monkeypatch.setattr(
        agent_handoff_module,
        "save_task_card",
        fail_after_concurrent_publish,
    )

    with pytest.raises(TaskCardError, match="simulated concurrent"):
        SupervisorAgentRuntime(workspace).handoff(
            run_id,
            reason="验证失败回滚不删除其他 run 的 Task Card",
        )

    cards = list((repo / ".vega" / "tasks").rglob("*.md"))
    assert len(cards) == 1
    assert cards[0].read_text(encoding="utf-8") == concurrent_content


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
