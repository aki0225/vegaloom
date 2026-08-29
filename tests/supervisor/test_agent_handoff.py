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
from vega.agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from vega.agent_contract import AgentObservation, AgentPlan, AgentWorkItem
from vega.agent_persistence import (
    load_agent_checkpoint,
    load_agent_state,
    read_agent_trace,
    save_agent_checkpoint,
)
from vega.agent_handoff_safety import assert_portable_task_card_payload
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_recovery_request import AgentRecoveryRequest
from vega.agent_run import AgentRun
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
            "handoff",
            "--run",
            run_id,
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


def test_change_run_handoff_preserves_metadata_and_status(tmp_path: Path) -> None:
    repo, workspace, runtime, approved, managed_repo = _approved_change_run(
        tmp_path,
        task_id="task-change-handoff",
    )
    original_accepted = approved.state.accepted_checkpoint_sha
    original_metadata = json.loads(
        (approved.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    (managed_repo / "README.md").write_text(
        "fixture\nchange run handoff wip\n",
        encoding="utf-8",
        newline="\n",
    )
    stopped = SupervisorAgentRecovery(workspace).stop(
        approved.run_dir.name,
        reason="准备验证 ChangeRun Handoff",
    )
    metadata_path = stopped.run_dir / "agent-run.json"

    handoff = runtime.handoff(
        stopped.run_dir.name,
        reason="验证交接保留 ChangeRun metadata",
    )

    published_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert published_metadata["change_run"] == original_metadata["change_run"]
    assert runtime.status(handoff.run.run_dir.name)
    card = load_task_card(handoff.task_card_path)
    assert card.change_run is not None
    assert card.change_run.accepted_checkpoint_sha == original_accepted
    assert card.resume_capsule is not None
    assert card.resume_capsule.comparison_base_revision == original_accepted
    task_card = handoff.task_card_path.relative_to(managed_repo).as_posix()
    _git(managed_repo, "add", "README.md", task_card)
    _git(managed_repo, "commit", "-m", "测试：提交 ChangeRun Handoff")

    clone = tmp_path / "clone"
    _git(
        tmp_path,
        "-c",
        "core.autocrlf=false",
        "clone",
        "--branch",
        card.branch,
        str(repo),
        str(clone),
    )
    _git(clone, "config", "core.autocrlf", "false")
    next_workspace = tmp_path / "next-workspace"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(clone)
    restored_metadata = json.loads(
        (restored.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    restored_repo = Path(restored_metadata["repo_path"])

    assert restored.state.run_kind == "change"
    assert restored.state.contract_revision == 1
    assert restored.state.execution_plan_revision == 1
    assert restored.state.approved_contract_digest == card.change_run.contract.approved_digest
    assert restored.state.active_candidate_sha is None
    assert restored.state.accepted_checkpoint_sha != original_accepted
    assert _head(restored_repo) == restored.state.accepted_checkpoint_sha
    assert (restored_repo / "README.md").read_text(encoding="utf-8") == (
        "fixture\nchange run handoff wip\n"
    )
    assert _git_output(
        restored_repo,
        "show",
        f"{restored.state.accepted_checkpoint_sha}:README.md",
    ) == "fixture"
    assert _git_output(
        restored_repo,
        "ls-files",
        "--error-unmatch",
        task_card,
    ) == task_card
    assert restored_metadata["change_run"]["run_id"] == restored.run_dir.name
    assert Path(restored_metadata["change_run"]["worktree_path"]) == restored_repo
    assert (
        json.loads(
            (restored.run_dir / "change-contract.json").read_text(encoding="utf-8")
        )["approved_digest"]
        == card.change_run.contract.approved_digest
    )
    assert runtime.status(handoff.run.run_dir.name)
    assert SupervisorAgentRuntime(next_workspace).status(restored.run_dir.name)


def test_handoff_status_keeps_old_gates_visible_as_historical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _stopped_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    snapshot = agent_runtime_support_module.capture_bound_workspace(run_dir)
    observation = AgentObservation(
        observation_id="observation-handoff-history",
        machine_summary="旧门禁已经完成",
        workspace_fingerprint=snapshot.fingerprint,
        changed_files=list(snapshot.changed_files),
        verification="passed",
        risk="blocked",
        review="passed",
    )
    monkeypatch.setattr(
        agent_handoff_module,
        "_latest_observation",
        lambda _: (observation, "2026-08-29T00:00:00+00:00"),
    )

    result = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证旧门禁仍可见",
    )
    status = SupervisorAgentRuntime(workspace).status(result.run.run_dir.name)

    assert "Verification：尚未运行" in status
    assert "Risk：尚未运行" in status
    assert "Reviewer：尚未运行" in status
    assert "历史门禁：Verification=通过、Risk=阻断、Reviewer=通过" in status
    assert "不能作为当前门禁的通过证据" in status


def test_clean_change_run_can_resume_and_handoff_again(tmp_path: Path) -> None:
    repo, workspace, runtime, approved, first_source = _approved_change_run(
        tmp_path,
        task_id="task-change-two-hops",
    )
    first_stopped = SupervisorAgentRecovery(workspace).stop(
        approved.run_dir.name,
        reason="准备第一次跨机器恢复",
    )
    first_handoff = runtime.handoff(
        first_stopped.run_dir.name,
        reason="提交第一次 ChangeRun Handoff",
    )
    first_card = load_task_card(first_handoff.task_card_path)
    first_relative = first_handoff.task_card_path.relative_to(
        first_source
    ).as_posix()
    assert first_handoff.handoff_status == "handoff_ready"
    _git(first_source, "add", first_relative)
    _git(first_source, "commit", "-m", "测试：提交第一次 ChangeRun Handoff")

    second_clone = tmp_path / "second-clone"
    _git(
        tmp_path,
        "-c",
        "core.autocrlf=false",
        "clone",
        "--branch",
        first_card.branch,
        str(repo),
        str(second_clone),
    )
    _git(second_clone, "config", "core.autocrlf", "false")
    second_workspace = tmp_path / "second-workspace"
    second_workspace.mkdir()
    second_runtime = SupervisorAgentRuntime(second_workspace)
    second_run = second_runtime.resume_task_card(second_clone)

    assert second_run.state.phase == "ready"
    assert second_run.state.run_kind == "change"
    agent_runtime_support_module.validate_dispatch_artifacts(
        second_run.run_dir,
        second_run.state,
        second_run.plan,
    )
    second_stopped = SupervisorAgentRecovery(second_workspace).stop(
        second_run.run_dir.name,
        reason="准备第二次跨机器恢复",
    )
    second_handoff = second_runtime.handoff(
        second_stopped.run_dir.name,
        reason="提交第二次 ChangeRun Handoff",
    )
    second_card = load_task_card(second_handoff.task_card_path)
    second_metadata = json.loads(
        (second_run.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    second_source = Path(second_metadata["repo_path"])
    second_relative = second_handoff.task_card_path.relative_to(
        second_source
    ).as_posix()

    assert second_handoff.handoff_status == "handoff_ready"
    assert second_card.handoff_sequence == 2
    assert second_card.previous_task_card == first_relative
    assert second_card.branch != first_card.branch
    _git(second_source, "add", second_relative)
    _git(second_source, "commit", "-m", "测试：提交第二次 ChangeRun Handoff")

    third_clone = tmp_path / "third-clone"
    _git(
        tmp_path,
        "-c",
        "core.autocrlf=false",
        "clone",
        "--branch",
        second_card.branch,
        str(second_clone),
        str(third_clone),
    )
    _git(third_clone, "config", "core.autocrlf", "false")
    third_workspace = tmp_path / "third-workspace"
    third_workspace.mkdir()
    third_run = SupervisorAgentRuntime(third_workspace).resume_task_card(
        third_clone
    )

    assert third_run.state.phase == "ready"
    assert third_run.state.run_kind == "change"
    agent_runtime_support_module.validate_dispatch_artifacts(
        third_run.run_dir,
        third_run.state,
        third_run.plan,
    )


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
    source_run_dir = workspace / "runs" / run_id
    source_state = load_agent_state(source_run_dir / "agent-state.json")
    assert source_state.latest_checkpoint_id is not None
    source_checkpoint_path = (
        source_run_dir
        / "checkpoints"
        / f"{source_state.latest_checkpoint_id}.json"
    )
    source_checkpoint = load_agent_checkpoint(source_checkpoint_path)
    save_agent_checkpoint(
        source_checkpoint_path,
        source_checkpoint.model_copy(
            update={"failed_attempts": ["attempt-before-handoff"]}
        ),
    )
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
    assert restored.state.latest_checkpoint_id is not None
    restored_checkpoint = load_agent_checkpoint(
        restored.run_dir
        / "checkpoints"
        / f"{restored.state.latest_checkpoint_id}.json"
    )
    assert restored_checkpoint.failed_attempts == ["attempt-before-handoff"]
    assert "attempt-before-handoff" in (
        restored.run_dir / "task-brief.md"
    ).read_text(encoding="utf-8")
    assert "重新对账" in (
        next_workspace / "runs" / restored.run_dir.name / "status-card.md"
    ).read_text(encoding="utf-8")
    status_card = (restored.run_dir / "status-card.md").read_text(encoding="utf-8")
    assert "Verification：尚未运行" in status_card
    assert "Risk：尚未运行" in status_card
    assert "Reviewer：尚未运行" in status_card


def test_clean_handoff_resumes_without_treating_task_card_as_wip(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "feature/clean-handoff")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = AgentPlan(
        task_id="task-clean-handoff",
        user_goal="验证没有 WIP 的跨机器交接",
        success_conditions=["新 run 可以从干净现场恢复"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="继续只读调查",
                allowed_paths=["README.md"],
                verification=["git diff --check"],
                external_side_effects="none",
            )
        ],
    )
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    stopped = SupervisorAgentRecovery(workspace).stop(
        approved.run_dir.name,
        reason="准备换机继续只读调查",
    )
    handoff = runtime.handoff(
        stopped.run_dir.name,
        reason="提交只有 Task Card 的交接",
    )
    card = load_task_card(handoff.task_card_path)
    assert card.resume_capsule is not None
    assert card.resume_capsule.changed_files == []
    _git(repo, "add", handoff.task_card_path.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "测试：提交干净 Handoff")
    next_workspace = tmp_path / "next-workspace"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(repo)

    checkpoint = load_agent_checkpoint(
        restored.run_dir
        / "checkpoints"
        / f"{restored.state.latest_checkpoint_id}.json"
    )
    metadata = json.loads(
        (restored.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    assert restored.state.phase == "ready"
    assert checkpoint.changed_files == []
    assert metadata["comparison_base_revision"] == _head(repo)
    assert metadata["comparison_paths"] == []


def test_same_task_card_cannot_resume_twice_in_one_git_repository(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    handoff = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证同一卡只能恢复一次",
    )
    _git(
        repo,
        "add",
        "src/example.py",
        handoff.task_card_path.relative_to(repo).as_posix(),
    )
    _git(repo, "commit", "-m", "测试：提交单次恢复 Task Card")
    first_workspace = tmp_path / "first-workspace"
    second_workspace = tmp_path / "second-workspace"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first = SupervisorAgentRuntime(first_workspace).resume_task_card(repo)

    with pytest.raises(ValueError, match="已经.*恢复 run|已在本机"):
        SupervisorAgentRuntime(second_workspace).resume_task_card(repo)

    assert first.state.phase == "ready"
    assert not list(
        (second_workspace / "runs").glob("*-agent-resume*/agent-state.json")
    )


def test_task_card_supports_two_handoff_hops(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _stopped_run(tmp_path)
    first_handoff = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="第一次换机交接",
    )
    first_relative = first_handoff.task_card_path.relative_to(repo).as_posix()
    _git(repo, "add", "src/example.py", first_relative)
    _git(repo, "commit", "-m", "测试：提交第一次 Handoff")
    second_workspace = tmp_path / "second-workspace"
    second_workspace.mkdir()
    restored = SupervisorAgentRuntime(second_workspace).resume_task_card(repo)
    stopped = SupervisorAgentRecovery(second_workspace).stop(
        restored.run_dir.name,
        reason="准备第二次换机",
    )
    second_handoff = SupervisorAgentRuntime(second_workspace).handoff(
        stopped.run_dir.name,
        reason="第二次换机交接",
    )
    second_card = load_task_card(second_handoff.task_card_path)
    second_relative = second_handoff.task_card_path.relative_to(repo).as_posix()

    assert second_card.handoff_sequence == 2
    assert second_card.previous_task_card == first_relative
    second_metadata = json.loads(
        (second_handoff.run.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    assert (
        second_metadata["comparison_base_revision"]
        == second_card.resume_capsule.comparison_base_revision
    )
    assert second_metadata["comparison_paths"] == ["src/example.py"]
    assert "Workspace 与证据一致：`是`" in SupervisorAgentRuntime(
        second_workspace
    ).status(second_handoff.run.run_dir.name)
    _git(repo, "add", second_relative)
    _git(repo, "commit", "-m", "测试：提交第二次 Handoff")
    third_workspace = tmp_path / "third-workspace"
    third_workspace.mkdir()

    resumed_again = SupervisorAgentRuntime(third_workspace).resume_task_card(repo)

    assert resumed_again.state.phase == "ready"
    metadata = json.loads(
        (resumed_again.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    assert metadata["task_card"] == second_relative
    assert metadata["comparison_paths"] == ["src/example.py"]
    stopped_again = SupervisorAgentRecovery(third_workspace).stop(
        resumed_again.run_dir.name,
        reason="准备第三次换机",
    )

    third_handoff = SupervisorAgentRuntime(third_workspace).handoff(
        stopped_again.run_dir.name,
        reason="第三次换机交接",
    )
    third_card = load_task_card(third_handoff.task_card_path)

    assert third_card.handoff_sequence == 3
    assert third_card.previous_task_card == second_relative


def test_handoff_and_task_brief_preserve_all_confirmed_facts(
    tmp_path: Path,
) -> None:
    facts = [f"事实 {index:02d}" for index in range(1, 14)]
    plan = _plan(observed_facts=facts)
    repo, workspace, run_id = _stopped_run(tmp_path, plan=plan)
    handoff = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="验证长任务事实不会静默丢失",
    )
    card = load_task_card(handoff.task_card_path)
    assert card.resume_capsule is not None
    assert "事实 13" in card.resume_capsule.confirmed_facts
    _git(
        repo,
        "add",
        "src/example.py",
        handoff.task_card_path.relative_to(repo).as_posix(),
    )
    _git(repo, "commit", "-m", "测试：提交完整上下文 Handoff")
    next_workspace = tmp_path / "next-workspace-facts"
    next_workspace.mkdir()

    restored = SupervisorAgentRuntime(next_workspace).resume_task_card(repo)
    task_brief = (restored.run_dir / "task-brief.md").read_text(encoding="utf-8")

    assert "事实 13" in task_brief
    assert "其余" not in task_brief


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


def _approved_change_run(
    tmp_path: Path,
    *,
    task_id: str,
) -> tuple[
    Path,
    Path,
    SupervisorAgentRuntime,
    AgentRun,
    Path,
]:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(
        repo,
        contract=ChangeContract(
            task_id=task_id,
            goal="验证 ChangeRun 跨机器恢复",
            acceptance=["恢复后保留同一批准合同和执行计划"],
            required_verification=["git diff --check"],
            authority_envelope=ChangeAuthorityEnvelope(
                allowed_paths=["README.md"],
                max_changed_files=1,
            ),
        ),
        execution_plan=ExecutionPlan(
            task_id=task_id,
            contract_revision=1,
            work_items=[
                ExecutionWorkItem(
                    work_item_id="WI-01",
                    objective="完成最小文档修改",
                    likely_files=["README.md"],
                    verification=["git diff --check"],
                )
            ],
        ),
    )
    approved = runtime.approve(started.run_dir.name)
    metadata = json.loads(
        (approved.run_dir / "agent-run.json").read_text(encoding="utf-8")
    )
    return repo, workspace, runtime, approved, Path(metadata["repo_path"])


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
                external_side_effects="none",
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


def _git_output(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
    return process.stdout.strip()


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
