from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vega.agent_contract import AgentObservation, AgentPlan, AgentWorkItem
from vega.agent_persistence import (
    load_agent_checkpoint,
    load_agent_state,
    read_agent_trace,
    save_agent_checkpoint,
)
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_recovery_request import AgentRecoveryRequest
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_worker import SupervisorAgentWorker


def test_live_worker_blocks_recovery_and_second_writer(tmp_path: Path) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    worker.bind(run_id, child_run="attempt-01", operation_id="operation-01")

    with pytest.raises(ValueError, match="当前状态不允许启动 Worker"):
        worker.bind(run_id, child_run="attempt-02", operation_id="operation-02")
    with pytest.raises(ValueError, match="宿主仍报告 Worker 存活"):
        SupervisorAgentRecovery(workspace).recover(
            run_id,
            AgentRecoveryRequest(
                reason="模拟会话断开但 Worker 仍存活",
                worker_alive=True,
                operation_started=False,
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
        operation_started=True,
    )

    observed = runtime.observe(
        bound.run_dir.name,
        _observation(
            "obs-live",
            worker_alive=True,
            operation_started=True,
        ),
    )

    assert observed.state.phase == "needs_human"
    assert observed.state.active_child_run == "attempt-live"
    assert observed.state.active_operation_id == "operation-live"
    assert observed.plan.work_items[0].status == "pending"


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


def test_operation_not_started_and_clean_workspace_allows_new_child(
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
            reason="控制进程在真正启动 Worker 前退出",
            worker_alive=False,
            operation_started=False,
        ),
    )
    replacement = worker.bind(
        run_id,
        child_run="attempt-new",
        operation_id="operation-new",
    )

    assert recovered.state.phase == "ready"
    assert recovered.state.active_child_run is None
    assert replacement.state.active_child_run == "attempt-new"
    assert not (run_dir / "graph-checkpoints.sqlite").exists()
    events = [item["event"] for item in read_agent_trace(run_dir / "trace.jsonl")]
    assert events[-2:] == ["agent_recovered", "worker_reserved"]


def test_partial_diff_after_worker_loss_requires_human(tmp_path: Path) -> None:
    repo, workspace, run_id = _approved_run(tmp_path)
    worker = SupervisorAgentWorker(workspace)
    bound = worker.bind(run_id, child_run="attempt-partial", operation_id="operation-partial")
    worker.confirm_started(
        bound.run_dir.name,
        child_run="attempt-partial",
        operation_id="operation-partial",
    )
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("partial = True\n", encoding="utf-8")

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="Worker 在留下 partial diff 后失联",
            worker_alive=False,
            operation_started=True,
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
    bound = worker.bind(
        run_id,
        child_run=f"attempt-{domain}",
        operation_id=f"operation-{domain}",
    )
    worker.confirm_started(
        bound.run_dir.name,
        child_run=f"attempt-{domain}",
        operation_id=f"operation-{domain}",
    )

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason=f"{domain}终态未知",
            worker_alive=False,
            operation_started=True,
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

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="宿主确认已经产生外部副作用",
            worker_alive=False,
            operation_started=False,
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
            worker_alive=False,
            operation_started=False,
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
    with (run_dir / "trace.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"event":"truncated"')

    recovered = SupervisorAgentRecovery(workspace).recover(
        run_id,
        AgentRecoveryRequest(
            reason="模拟写 Trace 时突然断电",
            worker_alive=False,
            operation_started=False,
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
                    worker_alive=False,
                    operation_started=False,
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


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
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
