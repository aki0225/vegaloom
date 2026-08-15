from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vega import agent_recovery as agent_recovery_module
from vega import agent_side_effect_adjudication as agent_adjudication_module
from vega import agent_worker as agent_worker_module
from vega.agent_contract import AgentObservation, AgentPlan, AgentWorkItem
from vega.agent_persistence import (
    load_agent_checkpoint,
    load_agent_state,
    read_agent_trace,
    save_agent_checkpoint,
    save_agent_state,
)
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_recovery_request import AgentRecoveryRequest
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_side_effect_adjudication import (
    SupervisorAgentSideEffectAdjudicator,
)
from vega.agent_worker import SupervisorAgentWorker
from vega.cli_entrypoint import app
from vega.execution_control import ExecutionLease
from vega.models import LoopAutomationState


def test_live_worker_blocks_recovery_and_second_writer(tmp_path: Path) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(run_id, child_run="attempt-01", operation_id="operation-01")
    _write_execution(
        workspace / "runs" / run_id,
        execution_id="operation-01",
        status="running",
    )

    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-02", operation_id="operation-02")
    with pytest.raises(ValueError, match="active execution"):
        SupervisorAgentRecovery(workspace).recover(
            run_id,
            AgentRecoveryRequest(
                reason="模拟会话断开但 Worker 仍存活",
            ),
        )

    state = load_agent_state(workspace / "runs" / run_id / "agent-state.json")
    assert state.active_child_run == "attempt-01"
    assert state.active_operation_id == "operation-01"
    assert state.phase == "acting"
    assert not (repo / "unexpected.txt").exists()


def test_live_worker_observation_keeps_writer_binding(tmp_path: Path) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    bound = worker.bind(
        run_id,
        child_run="attempt-live",
        operation_id="operation-live",
    )

    observed = runtime.observe(
        bound.run_dir.name,
        _observation(
            "obs-live",
            worker_alive=False,
            operation_started=False,
        ),
    )

    assert observed.state.phase == "needs_human"
    assert observed.state.active_child_run == "attempt-live"
    assert observed.state.active_operation_id == "operation-live"
    assert observed.plan.work_items[0].status == "pending"
    saved = json.loads(
        (observed.run_dir / "observations" / "obs-live.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["authority"] == "external_claim"
    assert saved["worker_alive"] is True
    assert saved["operation_started"] is True


def test_observation_without_active_writer_is_rejected(tmp_path: Path) -> None:
    _, workspace, run_id = _approved_run(tmp_path)

    with pytest.raises(ValueError, match="必须绑定当前 active Writer"):
        SupervisorAgentRuntime(workspace).observe(
            run_id,
            _observation("obs-forged"),
        )


def test_concurrent_dispatch_allows_only_one_writer(tmp_path: Path) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)

    def bind(index: int) -> str:
        try:
            result = worker.bind(
                run_id,
                child_run=f"attempt-{index}",
                operation_id=f"operation-{index}",
            )
            return f"started:{result.state.active_child_run}"
        except ValueError as exc:
            return f"blocked:{exc}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(bind, [1, 2]))

    assert sum(item.startswith("started:") for item in outcomes) == 1
    assert sum(item.startswith("blocked:") for item in outcomes) == 1
    state = load_agent_state(workspace / "runs" / run_id / "agent-state.json")
    assert state.active_child_run in {"attempt-1", "attempt-2"}


def test_dispatch_without_execution_evidence_keeps_original_writer(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(run_id, child_run="attempt-old", operation_id="operation-old")
    run_dir = workspace / "runs" / run_id
    (run_dir / "graph-checkpoints.sqlite").unlink(missing_ok=True)

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="dispatch 已提交，但 execution 证据尚未落盘",
            external_side_effects="none",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run == "attempt-old"
    assert recovered.state.active_operation_id == "operation-old"
    assert not (run_dir / "graph-checkpoints.sqlite").exists()
    checkpoint = _latest_checkpoint(run_dir)
    assert checkpoint.status == "blocked"
    assert checkpoint.operation_started is True
    assert "缺少可验证的 execution 记录" in checkpoint.reason
    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-new", operation_id="operation-new")
    events = [item["event"] for item in read_agent_trace(run_dir / "trace.jsonl")]
    assert events[-2:] == [
        "worker_dispatch_committed",
        "agent_recovery_execution_blocked",
    ]


def test_legacy_unconfirmed_operation_keeps_original_writer(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(
        run_id,
        child_run="attempt-legacy",
        operation_id="operation-legacy",
    )
    run_dir = workspace / "runs" / run_id
    state_path = run_dir / "agent-state.json"
    legacy_state = load_agent_state(state_path).model_copy(
        update={"operation_started": False}
    )
    save_agent_state(state_path, legacy_state)

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="旧版两阶段 dispatch 尚未写入启动确认",
            external_side_effects="none",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run == "attempt-legacy"
    assert recovered.state.active_operation_id == "operation-legacy"
    checkpoint = _latest_checkpoint(run_dir)
    assert checkpoint.status == "blocked"
    assert "不能证明 operation 未启动" in checkpoint.reason
    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-new", operation_id="operation-new")


def test_partial_diff_after_worker_loss_requires_human(tmp_path: Path) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(
        run_id,
        child_run="attempt-partial",
        operation_id="operation-partial",
    )
    _write_execution(
        workspace / "runs" / run_id,
        execution_id="operation-partial",
        status="failed",
    )
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("partial = True\n", encoding="utf-8")

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="Worker 在留下 partial diff 后失联",
            external_side_effects="none",
        ),
    )
    checkpoint = _latest_checkpoint(recovered.run_dir)

    assert recovered.state.phase == "needs_human"
    assert recovered.state.allowed_actions == ["human"]
    assert recovered.state.active_child_run is None
    assert checkpoint.failed_attempts == ["attempt-partial"]
    assert checkpoint.operation_started is True
    assert checkpoint.changed_files == ["src/example.py"]
    assert "partial diff" in checkpoint.reason
    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-new", operation_id="operation-new")


def test_mismatched_terminal_execution_keeps_original_writer_binding(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(
        run_id,
        child_run="attempt-current",
        operation_id="operation-current",
    )
    run_dir = workspace / "runs" / run_id
    _write_execution(
        run_dir,
        execution_id="operation-old",
        status="completed",
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="历史 execution 不能核销当前 Writer",
            external_side_effects="none",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run == "attempt-current"
    assert recovered.state.active_operation_id == "operation-current"
    checkpoint = _latest_checkpoint(run_dir)
    assert checkpoint.status == "blocked"
    assert "active operation 身份不一致" in checkpoint.reason
    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-new", operation_id="operation-new")


def test_terminal_execution_requires_replan_before_new_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    original_append_trace = agent_worker_module.append_agent_trace

    def fail_dispatch_trace(*args, **kwargs):
        raise OSError("simulated dispatch trace failure")

    monkeypatch.setattr(
        agent_worker_module,
        "append_agent_trace",
        fail_dispatch_trace,
    )
    with pytest.raises(OSError, match="simulated dispatch trace failure"):
        worker.bind(
            run_id,
            child_run="attempt-failed",
            operation_id="operation-failed",
        )
    monkeypatch.setattr(
        agent_worker_module,
        "append_agent_trace",
        original_append_trace,
    )
    run_dir = workspace / "runs" / run_id
    state = load_agent_state(run_dir / "agent-state.json")
    assert state.phase == "acting"
    assert state.active_operation_id == "operation-failed"
    assert list((run_dir / "operations").glob("*.json"))
    _write_execution(
        run_dir,
        execution_id="operation-failed",
        status="failed",
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="Worker 已失败且未留下变更",
            external_side_effects="none",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run is None
    recovery_observation = json.loads(
        next((run_dir / "observations").glob("recovery-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert "executions/worker/execution.json" in recovery_observation["evidence_refs"]
    assert any(
        item.startswith("operations/")
        for item in recovery_observation["evidence_refs"]
    )
    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-new", operation_id="operation-new")

    draft = AgentPlan(
        task_id=recovered.plan.task_id,
        user_goal=recovered.plan.user_goal,
        success_conditions=list(recovered.plan.success_conditions),
        work_items=[
            item.model_copy(update={"status": "pending"})
            for item in recovered.plan.work_items
        ],
    )
    revised = runtime.update_plan(run_id, draft)
    approved = runtime.approve(revised.run_dir.name)
    with pytest.raises(ValueError, match="operation_id 已在当前 Agent run 使用"):
        worker.bind(
            approved.run_dir.name,
            child_run="attempt-reused",
            operation_id="operation-failed",
        )
    replacement = worker.bind(
        approved.run_dir.name,
        child_run="attempt-new",
        operation_id="operation-new",
    )

    assert replacement.state.phase == "acting"
    assert replacement.state.active_child_run == "attempt-new"


def test_recovery_checkpoint_failure_keeps_original_writer_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(
        run_id,
        child_run="attempt-checkpoint",
        operation_id="operation-checkpoint",
    )
    run_dir = workspace / "runs" / run_id
    _write_execution(
        run_dir,
        execution_id="operation-checkpoint",
        status="failed",
    )
    original_write_checkpoint = agent_recovery_module.write_checkpoint
    original_uuid4 = agent_recovery_module.uuid4

    class FixedUuid:
        hex = "a" * 32

    monkeypatch.setattr(agent_recovery_module, "uuid4", lambda: FixedUuid())

    def fail_checkpoint(*args, **kwargs):
        raise OSError("simulated checkpoint failure")

    monkeypatch.setattr(
        agent_recovery_module,
        "write_checkpoint",
        fail_checkpoint,
    )
    with pytest.raises(OSError, match="simulated checkpoint failure"):
        SupervisorAgentRecovery(workspace).recover(
            run_id,
            AgentRecoveryRequest(
                reason="模拟终态对账后写 Checkpoint 失败",
                external_side_effects="unknown",
            ),
        )

    state = load_agent_state(run_dir / "agent-state.json")
    assert state.phase == "acting"
    assert state.active_child_run == "attempt-checkpoint"
    assert state.active_operation_id == "operation-checkpoint"
    assert state.operation_started is True
    first_observations = sorted((run_dir / "observations").glob("recovery-*.json"))
    assert len(first_observations) == 1
    first_observation = first_observations[0]
    first_payload = first_observation.read_bytes()

    monkeypatch.setattr(
        agent_recovery_module,
        "write_checkpoint",
        original_write_checkpoint,
    )
    with pytest.raises(ValueError, match="Observation ID 已存在"):
        SupervisorAgentRecovery(workspace).recover(
            run_id,
            AgentRecoveryRequest(
                reason="重复恢复不能覆盖第一次机器对账",
                external_side_effects="unknown",
            ),
        )
    assert first_observation.read_bytes() == first_payload
    monkeypatch.setattr(agent_recovery_module, "uuid4", original_uuid4)
    with pytest.raises(ValueError, match="只有已暂停且没有 active Writer"):
        SupervisorAgentRecovery(workspace).resume_local(run_id)

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="Checkpoint 写入恢复后重新执行现场对账",
            external_side_effects="unknown",
        ),
    )
    observations = sorted((run_dir / "observations").glob("recovery-*.json"))

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run is None
    assert len(observations) == 2
    assert first_observation.read_bytes() == first_payload


@pytest.mark.parametrize(
    "domain",
    ["数据库迁移", "支付请求", "部署动作", "外部 API"],
)
def test_unknown_external_side_effect_never_auto_retries(
    tmp_path: Path,
    domain: str,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(
        run_id,
        child_run=f"attempt-{domain}",
        operation_id=f"operation-{domain}",
    )
    _write_execution(
        workspace / "runs" / run_id,
        execution_id=f"operation-{domain}",
        status="failed",
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason=f"{domain}终态未知",
            external_side_effects="unknown",
        ),
    )
    checkpoint = _latest_checkpoint(recovered.run_dir)

    assert recovered.state.phase == "needs_human"
    assert recovered.state.allowed_actions == ["human"]
    assert checkpoint.external_side_effects == "unknown"
    assert "禁止自动重放" in checkpoint.reason


def test_known_external_side_effect_never_auto_retries(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(
        run_id,
        child_run="attempt-known-side-effect",
        operation_id="operation-known-side-effect",
    )
    _write_execution(
        workspace / "runs" / run_id,
        execution_id="operation-known-side-effect",
        status="completed",
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="宿主确认已经产生外部副作用",
            external_side_effects="known",
        ),
    )
    checkpoint = _latest_checkpoint(recovered.run_dir)

    assert recovered.state.phase == "needs_human"
    assert checkpoint.status == "blocked"
    assert checkpoint.external_side_effects == "known"
    assert "必须由人工决定" in checkpoint.reason


def test_corrupt_execution_record_blocks_recovery_without_releasing_writer(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(run_id, child_run="attempt-execution", operation_id="operation-execution")
    run_dir = workspace / "runs" / run_id
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    execution_path.parent.mkdir(parents=True)
    execution_path.write_text("{", encoding="utf-8")

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="execution 记录损坏",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run == "attempt-execution"
    assert recovered.state.active_operation_id == "operation-execution"
    checkpoint = _latest_checkpoint(run_dir)
    assert checkpoint.status == "blocked"
    assert "execution 证据无法安全解析" in checkpoint.reason


def test_truncated_trace_keeps_original_writer_binding(tmp_path: Path) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(run_id, child_run="attempt-trace", operation_id="operation-trace")
    run_dir = workspace / "runs" / run_id
    _write_execution(
        run_dir,
        execution_id="operation-trace",
        status="failed",
    )
    with (run_dir / "trace.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"event":"truncated"')

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="模拟写 Trace 时突然断电",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run == "attempt-trace"
    assert recovered.state.active_operation_id == "operation-trace"
    checkpoint = _latest_checkpoint(recovered.run_dir)
    assert checkpoint.status == "blocked"
    assert "Trace 尾部" in checkpoint.reason


def test_corrupt_state_and_unknown_schema_write_diagnostic_without_overwrite(
    tmp_path: Path,
) -> None:
    for mutation in ("digest", "schema"):
        _, workspace, run_id = _approved_run(tmp_path / mutation)
        worker = SupervisorAgentWorker(workspace)
        worker.bind(run_id, child_run="attempt-bad", operation_id="operation-bad")
        state_path = workspace / "runs" / run_id / "agent-state.json"
        original = json.loads(state_path.read_text(encoding="utf-8"))
        if mutation == "digest":
            original["data"]["phase"] = "ready"
        else:
            original["data"]["schema_version"] = 99
        state_path.write_text(
            json.dumps(original, ensure_ascii=False),
            encoding="utf-8",
        )
        corrupted = state_path.read_bytes()

        with pytest.raises(ValueError, match="Agent run 无法恢复"):
            SupervisorAgentRecovery(workspace).recover(
                run_id,
                AgentRecoveryRequest(
                    reason="模拟状态损坏",
                ),
            )

        assert state_path.read_bytes() == corrupted
        report = json.loads(
            (state_path.parent / "agent-recovery-report.json").read_text(
                encoding="utf-8"
            )
        )
        assert report["status"] == "blocked"
        assert report["state_preserved"] is True
        assert report["workspace"]["captured"] is True


def test_pause_resume_and_stop_preserve_goal_and_workspace(tmp_path: Path) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    recovery = SupervisorAgentRecovery(workspace)
    runtime = SupervisorAgentRuntime(workspace)
    plan_before = (workspace / "runs" / run_id / "agent-plan.json").read_bytes()

    paused = recovery.pause(run_id, reason="准备切换当前会话")
    resumed = recovery.resume_local(paused.run_dir.name)
    stopped = recovery.stop(resumed.run_dir.name, reason="人工决定暂时结束")

    assert paused.state.phase == "needs_human"
    assert resumed.state.phase == "ready"
    assert stopped.state.phase == "stopped"
    assert (repo / "README.md").read_text(encoding="utf-8") == "fixture\n"
    assert (stopped.run_dir / "agent-plan.json").read_bytes() == plan_before
    assert "任务已停止" in runtime.status(stopped.run_dir.name)


def test_stop_inherits_unknown_side_effect_and_remains_blocked(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "unknown")

    stopped = SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="外部副作用仍未完成对账",
    )
    checkpoint = _latest_checkpoint(run_dir)

    assert stopped.state.phase == "needs_human"
    assert stopped.state.allowed_actions == ["human"]
    assert checkpoint.status == "blocked"
    assert checkpoint.pending_actions == ["human"]
    assert checkpoint.external_side_effects == "unknown"
    handoff = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="未知外部副作用仍未解除",
    )
    assert handoff.handoff_status == "handoff_blocked"


def test_pause_inherits_known_side_effect(tmp_path: Path) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "known")

    SupervisorAgentRecovery(workspace).pause(
        run_id,
        reason="暂停并保留已知外部副作用",
    )

    assert _latest_checkpoint(run_dir).external_side_effects == "known"


def test_stop_without_latest_checkpoint_keeps_side_effect_none(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    state_path = run_dir / "agent-state.json"
    state = load_agent_state(state_path)
    save_agent_state(
        state_path,
        state.model_copy(update={"latest_checkpoint_id": None}),
    )

    SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="没有历史 Checkpoint 时停止",
    )

    assert _latest_checkpoint(run_dir).external_side_effects == "none"


def test_unknown_side_effect_adjudication_appends_evidence_and_allows_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "unknown")
    SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="Worker 已停止，等待人工核对外部副作用",
    )
    previous = _latest_checkpoint(run_dir)
    previous_path = run_dir / "checkpoints" / f"{previous.checkpoint_id}.json"
    previous_bytes = previous_path.read_bytes()
    checkpoint_paths = sorted((run_dir / "checkpoints").glob("checkpoint-*.json"))
    assert len(checkpoint_paths) >= 2
    next(path for path in checkpoint_paths if path != previous_path).unlink()
    evidence_path = run_dir / "manual-evidence" / "side-effects-reviewed.md"
    evidence_path.parent.mkdir()
    evidence_path.write_text(
        "已核对执行记录和任务范围；本次只产生仓库内文件修改。\n",
        encoding="utf-8",
    )
    request_path = workspace / "side-effect-adjudication.json"
    fake_secret = "sk-test-side-effect-adjudication-secret"
    request_path.write_text(
        AgentRecoveryRequest(
            reason=(
                "执行记录未包含部署、数据库或外部 API 操作；"
                f"临时核对 token={fake_secret}"
            ),
            external_side_effects="none",
            actor="operator",
            evidence_refs=["manual-evidence/side-effects-reviewed.md"],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)
    invalid_secret = "sk-test-invalid-side-effect-secret"
    invalid_request_path = workspace / "invalid-side-effect-adjudication.json"
    invalid_request_path.write_text(
        json.dumps(
            {
                "reason": "invalid request",
                "external_side_effects": invalid_secret,
            }
        ),
        encoding="utf-8",
    )
    invalid = CliRunner().invoke(
        app,
        [
            "agent",
            "adjudicate-side-effects",
            "--run",
            run_id,
            "--input",
            str(invalid_request_path),
        ],
    )
    assert invalid.exit_code != 0
    assert invalid_secret not in invalid.output

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "adjudicate-side-effects",
            "--run",
            run_id,
            "--input",
            str(request_path),
        ],
    )
    adjudicated = load_agent_state(run_dir / "agent-state.json")
    current = _latest_checkpoint(run_dir)

    assert result.exit_code == 0, result.output
    assert adjudicated.phase == "stopped"
    assert current.status == "safe"
    assert current.external_side_effects == "none"
    assert f"checkpoints/{previous.checkpoint_id}.json" in current.evidence_refs
    assert "manual-evidence/side-effects-reviewed.md" in current.evidence_refs
    assert previous_path.read_bytes() == previous_bytes
    assert load_agent_checkpoint(
        previous_path
    ).external_side_effects == "unknown"
    adjudication = json.loads(
        next((run_dir / "adjudications").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert adjudication["previous_external_side_effects"] == "unknown"
    assert adjudication["resolved_external_side_effects"] == "none"
    assert adjudication["evidence"][1]["path"] == (
        "manual-evidence/side-effects-reviewed.md"
    )
    events = [item["event"] for item in read_agent_trace(run_dir / "trace.jsonl")]
    assert events[-1] == "agent_side_effects_adjudicated"
    run_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*")
        if path.is_file()
    )
    assert fake_secret not in run_text
    handoff = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="人工副作用裁决后准备换机",
    )
    assert handoff.handoff_status == "handoff_ready"
    assert repo.joinpath(handoff.task_card_path.relative_to(repo)).is_file()


def test_known_side_effect_adjudication_remains_blocked(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "unknown")
    SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="等待人工确认已发生的外部动作",
    )
    evidence_path = run_dir / "manual-evidence" / "known-side-effect.md"
    evidence_path.parent.mkdir()
    evidence_path.write_text("已确认存在外部 API 写入。\n", encoding="utf-8")

    adjudicated = SupervisorAgentSideEffectAdjudicator(workspace).adjudicate(
        run_id,
        AgentRecoveryRequest(
            reason="人工确认存在外部 API 写入",
            external_side_effects="known",
            evidence_refs=["manual-evidence/known-side-effect.md"],
        ),
    )

    assert adjudicated.state.phase == "needs_human"
    assert adjudicated.state.allowed_actions == ["human"]
    checkpoint = _latest_checkpoint(run_dir)
    assert checkpoint.status == "blocked"
    assert checkpoint.external_side_effects == "known"
    stopped = SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="已知外部副作用仍未处理",
    )
    assert stopped.state.phase == "needs_human"
    assert stopped.state.allowed_actions == ["human"]
    checkpoint = _latest_checkpoint(run_dir)
    assert checkpoint.status == "blocked"
    assert checkpoint.external_side_effects == "known"
    handoff = SupervisorAgentRuntime(workspace).handoff(
        run_id,
        reason="已知副作用仍需人工处理",
    )
    assert handoff.handoff_status == "handoff_blocked"


def test_side_effect_adjudication_rejects_missing_evidence_or_workspace_drift(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "unknown")
    SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="等待严格人工裁决",
    )
    adjudicator = SupervisorAgentSideEffectAdjudicator(workspace)

    with pytest.raises(ValueError, match="至少需要一个"):
        adjudicator.adjudicate(
            run_id,
            AgentRecoveryRequest(
                reason="不能只凭口头断言解除 unknown",
                external_side_effects="none",
            ),
        )

    evidence_path = run_dir / "manual-evidence" / "review.md"
    evidence_path.parent.mkdir()
    evidence_path.write_text("人工检查记录。\n", encoding="utf-8")
    (repo / "README.md").write_text("workspace drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Workspace 已漂移"):
        adjudicator.adjudicate(
            run_id,
            AgentRecoveryRequest(
                reason="现场变化后不能复用旧证据",
                external_side_effects="none",
                evidence_refs=["manual-evidence/review.md"],
            ),
        )
    assert _latest_checkpoint(run_dir).external_side_effects == "unknown"


def test_side_effect_adjudication_rejects_stale_checkpoint_binding(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "unknown")
    SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="等待人工确认外部副作用",
    )
    checkpoint = _latest_checkpoint(run_dir)
    checkpoint_path = (
        run_dir / "checkpoints" / f"{checkpoint.checkpoint_id}.json"
    )
    save_agent_checkpoint(
        checkpoint_path,
        checkpoint.model_copy(update={"current_work_item": "stale-work-item"}),
    )
    evidence_path = run_dir / "manual-evidence" / "review.md"
    evidence_path.parent.mkdir()
    evidence_path.write_text("人工核对记录。\n", encoding="utf-8")

    with pytest.raises(ValueError, match="绑定不一致"):
        SupervisorAgentSideEffectAdjudicator(workspace).adjudicate(
            run_id,
            AgentRecoveryRequest(
                reason="没有仓库外写入",
                external_side_effects="none",
                evidence_refs=["manual-evidence/review.md"],
            ),
        )

    assert load_agent_state(run_dir / "agent-state.json").phase == "needs_human"


def test_side_effect_adjudication_fails_closed_before_state_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for target in ("append_agent_trace", "write_status_card"):
        _, workspace, run_id = _approved_run(tmp_path / target)
        run_dir = workspace / "runs" / run_id
        _replace_latest_external_side_effects(run_dir, "unknown")
        SupervisorAgentRecovery(workspace).stop(
            run_id,
            reason="等待人工确认外部副作用",
        )
        original_state = (run_dir / "agent-state.json").read_bytes()
        evidence_path = run_dir / "manual-evidence" / "review.md"
        evidence_path.parent.mkdir()
        evidence_path.write_text("人工核对记录。\n", encoding="utf-8")

        def fail_publish(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError(f"simulated {target} failure")

        with monkeypatch.context() as scoped:
            scoped.setattr(agent_adjudication_module, target, fail_publish)
            with pytest.raises(OSError, match="simulated"):
                SupervisorAgentSideEffectAdjudicator(workspace).adjudicate(
                    run_id,
                    AgentRecoveryRequest(
                        reason="没有仓库外写入",
                        external_side_effects="none",
                        evidence_refs=["manual-evidence/review.md"],
                    ),
                )

        assert (run_dir / "agent-state.json").read_bytes() == original_state
        assert _latest_checkpoint(run_dir).external_side_effects == "unknown"


def test_side_effect_adjudication_rejects_linked_artifact_directory(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    run_dir = workspace / "runs" / run_id
    _replace_latest_external_side_effects(run_dir, "unknown")
    SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="等待人工确认外部副作用",
    )
    evidence_path = run_dir / "manual-evidence" / "review.md"
    evidence_path.parent.mkdir()
    evidence_path.write_text("人工核对记录。\n", encoding="utf-8")
    outside = tmp_path / "outside-adjudications"
    outside.mkdir()
    _create_directory_link(run_dir / "adjudications", outside)

    with pytest.raises(OSError, match="链接|junction|reparse"):
        SupervisorAgentSideEffectAdjudicator(workspace).adjudicate(
            run_id,
            AgentRecoveryRequest(
                reason="没有仓库外写入",
                external_side_effects="none",
                evidence_refs=["manual-evidence/review.md"],
            ),
        )

    assert not list(outside.iterdir())
    assert _latest_checkpoint(run_dir).external_side_effects == "unknown"


def test_stop_active_assist_child_targets_only_bound_operation(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    child_run = "assist-child-stop"
    SupervisorAgentWorker(workspace).bind(
        run_id,
        child_run=child_run,
        operation_id="operation-stop",
    )
    child_dir = _write_assist_child(workspace, repo, child_run)
    _write_execution(
        child_dir,
        execution_id="operation-stop",
        status="running",
    )

    result = SupervisorAgentRecovery(workspace).stop(
        run_id,
        reason="人工请求停止当前 Worker",
    )

    request = json.loads(
        (
            child_dir / "executions" / "worker" / "stop-request.json"
        ).read_text(encoding="utf-8")
    )
    assert request["execution_id"] == "operation-stop"
    assert result.state.phase == "acting"
    assert result.state.active_child_run == child_run
    events = [item["event"] for item in read_agent_trace(result.run_dir / "trace.jsonl")]
    assert events[-1] == "agent_stop_requested"


def test_stop_active_assist_child_rejects_execution_identity_mismatch(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    child_run = "assist-child-mismatch"
    SupervisorAgentWorker(workspace).bind(
        run_id,
        child_run=child_run,
        operation_id="operation-expected",
    )
    child_dir = _write_assist_child(workspace, repo, child_run)
    _write_execution(
        child_dir,
        execution_id="operation-other",
        status="running",
    )

    with pytest.raises(ValueError, match="期望 operation 身份不一致"):
        SupervisorAgentRecovery(workspace).stop(
            run_id,
            reason="拒绝误停其他 execution",
        )

    assert not (
        child_dir / "executions" / "worker" / "stop-request.json"
    ).exists()


def test_recover_reads_sibling_assist_child_execution(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    child_run = "assist-child-recover"
    SupervisorAgentWorker(workspace).bind(
        run_id,
        child_run=child_run,
        operation_id="operation-recover",
    )
    child_dir = _write_assist_child(workspace, repo, child_run)
    _write_execution(
        child_dir,
        execution_id="operation-recover",
        status="failed",
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="原 agent run 命令中断后重新对账",
            external_side_effects="none",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run is None
    assert list((recovered.run_dir / "recovery-executions").glob("*.json"))
    observation = json.loads(
        next((recovered.run_dir / "observations").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert any(
        ref.startswith("recovery-executions/")
        for ref in observation["evidence_refs"]
    )


def test_recover_uses_bound_worker_when_child_has_newer_core_execution(
    tmp_path: Path,
) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    child_run = "assist-child-core-finished"
    SupervisorAgentWorker(workspace).bind(
        run_id,
        child_run=child_run,
        operation_id="operation-worker",
    )
    child_dir = _write_assist_child(workspace, repo, child_run)
    _write_execution(
        child_dir,
        execution_id="operation-worker",
        status="completed",
    )
    _write_execution(
        child_dir,
        execution_id="reviewer-execution",
        status="completed",
        step="reviewer",
        heartbeat_offset_seconds=5,
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="Core 已结束但主控制进程在机器 Observation 前中断",
            external_side_effects="none",
        ),
    )

    assert recovered.state.phase == "needs_human"
    assert recovered.state.active_child_run is None
    summary = json.loads(
        next((recovered.run_dir / "recovery-executions").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert summary["execution_id"] == "operation-worker"
    assert summary["execution_artifact"].startswith("executions/worker/")


def test_resume_rejects_safe_checkpoint_with_known_side_effect(
    tmp_path: Path,
) -> None:
    _, workspace, run_id = _approved_run(tmp_path)
    recovery = SupervisorAgentRecovery(workspace)
    paused = recovery.pause(run_id, reason="准备人工核对外部副作用")
    checkpoint = _latest_checkpoint(paused.run_dir)
    payload = checkpoint.model_dump(mode="json")
    payload["external_side_effects"] = "known"

    save_agent_checkpoint(
        paused.run_dir / "checkpoints" / f"{checkpoint.checkpoint_id}.json",
        checkpoint.model_validate(payload),
    )

    with pytest.raises(ValueError, match="不能证明现场可恢复"):
        recovery.resume_local(run_id)


def _approved_run(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    plan = AgentPlan(
        task_id="task-recovery",
        user_goal="验证 Agent 恢复语义",
        success_conditions=["故障注入符合 fail-closed"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="执行当前恢复案例",
                allowed_paths=["src/example.py"],
                verification=["运行恢复测试"],
            )
        ],
    )
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    approved = runtime.approve(run.run_dir.name)
    return repo, workspace, approved.run_dir.name


def _observation(
    observation_id: str,
    *,
    worker_alive: bool = False,
    operation_started: bool = True,
) -> AgentObservation:
    return AgentObservation(
        observation_id=observation_id,
        work_item_id="W1",
        machine_summary="测试 Observation",
        workspace_fingerprint="0" * 64,
        worker_alive=worker_alive,
        operation_started=operation_started,
    )


def _latest_checkpoint(run_dir: Path):
    state = load_agent_state(run_dir / "agent-state.json")
    assert state.latest_checkpoint_id is not None
    return load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )


def _replace_latest_external_side_effects(run_dir: Path, value: str) -> None:
    checkpoint = _latest_checkpoint(run_dir)
    payload = checkpoint.model_dump(mode="json")
    payload["external_side_effects"] = value
    save_agent_checkpoint(
        run_dir / "checkpoints" / f"{checkpoint.checkpoint_id}.json",
        checkpoint.model_validate(payload),
    )


def _create_directory_link(link_path: Path, target_path: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("当前 Windows 环境不能创建 junction")
        return
    link_path.symlink_to(target_path, target_is_directory=True)


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
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


def _write_execution(
    run_dir: Path,
    *,
    execution_id: str,
    status: str,
    step: str = "worker",
    heartbeat_offset_seconds: int = 0,
) -> None:
    now = datetime.now(UTC) + timedelta(seconds=heartbeat_offset_seconds)
    execution_dir = run_dir / "executions" / step
    execution_dir.mkdir(parents=True, exist_ok=True)
    lease = ExecutionLease(
        run_id=run_dir.name,
        execution_id=execution_id,
        step=step,
        owner_pid=os.getpid(),
        command=["fake-worker"],
        started_at=(now - timedelta(seconds=2)).isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(seconds=30)).isoformat(),
        deadline=(now + timedelta(seconds=60)).isoformat(),
        status=status,
        returncode=0 if status == "completed" else 1 if status == "failed" else None,
        finished_at=(
            now.isoformat()
            if status in {"stopped", "timed_out", "completed", "failed"}
            else None
        ),
    )
    (execution_dir / "execution.json").write_text(
        lease.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_assist_child(
    workspace: Path,
    repo: Path,
    child_run: str,
) -> Path:
    child_dir = workspace / "runs" / child_run
    child_dir.mkdir(parents=True)
    LoopAutomationState(
        run_id=child_run,
        status="needs_human",
        task_mode="bug",
        automation_mode="assist",
        repo_path=str(repo),
        input_source="agent-task-brief",
        current_step="waiting_for_worker",
    ).save(child_dir / "state.json")
    return child_dir
