from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope, ChangeContract, ExecutionPlan, ExecutionWorkItem,
)
from vega.agent_change_control import change_budget_snapshot
from vega.agent_change_fix_packet import load_current_fix_packet
from vega.agent_change_run import project_agent_plan
from vega.agent_persistence import load_agent_checkpoint, read_agent_trace
from vega.agent_provider_preparation import next_attempt_context
from vega.agent_provider_adapter import SupervisorAgentProviderAdapter
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_runtime_support import load_agent_bundle, save_agent_plan
from vega.run_lock import RunMutationBusyError, RunMutationLock
from vega.execution_control import ExecutionController
from vega.loop_runtime import LoopAutomationRuntime
from vega.runner import RunnerResult


def _scenario(tmp_path: Path, *, command: str | None = None, blocker: str = "", with_prepare: bool = False):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.name", "Vega Test"),
                 ("config", "user.email", "vega@example.invalid")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    (repo / "src").mkdir()
    (repo / "src/one.py").write_text("value = 0\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests/test_existing.py").write_text("def test_existing(): pass\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n.vega/\n.prepared/\n", encoding="utf-8")
    prepare_commands = ["python prepare.py"] if with_prepare else []
    if with_prepare:
        (repo / "prepare.py").write_text(
            "from pathlib import Path\n"
            "Path('.prepared').mkdir(exist_ok=True)\n"
            "with Path('.prepared/runs.txt').open('a') as stream:\n"
            "    stream.write('prepared\\n')\n", encoding="utf-8",
        )
    command = command or "python -m pytest -q tests/test_new.py::test_value tests/test_existing.py --timeout=60"
    second_command = "python -m pytest -q tests/test_second.py"
    commands = [command, second_command] if blocker == "multi" else [command]
    (repo / ".vega.yaml").write_text(
        "version: 1\nscope:\n  allowed_paths: [src/**, tests/**]\nverification:\n"
        f"  commands: {json.dumps(commands)}\n  max_commands: 3\n"
        f"  prepare_commands: {json.dumps(prepare_commands)}\n  timeout_seconds: 30\n", encoding="utf-8",
    )
    for args in (("add", "."), ("commit", "-m", "测试：初始化")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = ChangeContract(
        task_id="verification-scope", goal="实现功能并增加回归测试", acceptance=["固定验证通过"],
        required_verification=[command], prepare_commands=prepare_commands,
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/one.py", "tests/test_new.py", "tests/test_existing.py", "src/two.py",
                           "tests/test_second.py"],
            max_auto_replans=0 if blocker == "budget" else 2 if blocker == "multi" else 1,
            max_repair_rounds=2 if blocker == "two_repairs" else 3,
        ),
    )
    items = [ExecutionWorkItem(work_item_id="WI-01", objective="实现功能",
                               likely_files=["src/one.py"], verification=[command])]
    if blocker in {"other_item", "superseded"}:
        items.append(ExecutionWorkItem(
            work_item_id="WI-02", objective="后续项", depends_on=["WI-01"],
            likely_files=["tests/test_new.py" if blocker == "other_item" else "src/two.py"],
        ))
    if blocker == "multi":
        items.append(ExecutionWorkItem(
            work_item_id="WI-02", objective="补充第二项验证", depends_on=["WI-01"],
            likely_files=["src/two.py"], verification=[second_command],
        ))
    started = runtime.start_change(repo, contract=contract, execution_plan=ExecutionPlan(
        task_id=contract.task_id, contract_revision=1, work_items=items,
    ))
    return workspace, runtime, runtime.approve(started.run_dir.name)


def _files(run_dir: Path) -> dict[Path, bytes]:
    return {path.relative_to(run_dir): path.read_bytes()
            for path in run_dir.rglob("*") if path.is_file()}


@pytest.mark.parametrize("command", [
    "pytest -q tests/test_new.py tests/test_existing.py",
    "python -m pytest -q tests/test_new.py::test_value tests/test_existing.py --timeout=60",
    "python3 -m pytest tests/test_new.py -k value --timeout 60",
])
def test_scope_completion_is_idempotent_and_preserves_first_attempt(tmp_path, command):
    workspace, runtime, approved = _scenario(tmp_path, command=command)
    contract_path = approved.run_dir / "change-contract.json"
    contract_before = contract_path.read_bytes()
    execution_before = (approved.run_dir / "execution-plan.json").read_bytes()
    source = f"checkpoints/{approved.state.latest_checkpoint_id}.json"
    source_before = (approved.run_dir / source).read_bytes()
    with RunMutationLock.acquire(approved.run_dir, "agent.replan"):
        with pytest.raises(RunMutationBusyError):
            runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    revised = runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    assert revised is not None and revised.state.phase == "ready"
    assert revised.state.current_work_item == "WI-01"
    execution = ExecutionPlan.model_validate_json((revised.run_dir / "execution-plan.json").read_text())
    expected = ExecutionPlan.model_validate_json(execution_before)
    expected.plan_revision += 1
    expected.work_items[0].likely_files.append("tests/test_new.py")
    assert execution == expected
    assert contract_path.read_bytes() == contract_before
    assert (revised.run_dir / source).read_bytes() == source_before
    archive = revised.run_dir / "execution-plans/execution-plan-revision-001.json"
    assert ExecutionPlan.model_validate_json(archive.read_text()) == ExecutionPlan.model_validate_json(execution_before)
    checkpoint = load_agent_checkpoint(revised.run_dir / f"checkpoints/{revised.state.latest_checkpoint_id}.json")
    assert source in checkpoint.evidence_refs
    assert "plans/plan-revision-001.json" in checkpoint.evidence_refs
    # 新 Runtime 从磁盘恢复；自动修订不能伪造一次 Worker，也不能重置 attempt epoch。
    restarted = SupervisorAgentRuntime(workspace)
    _, state, _, _ = load_agent_bundle(workspace, revised.run_dir.name)
    assert next_attempt_context(revised.run_dir, state) == (1, True)
    contract = ChangeContract.model_validate_json(contract_before)
    budget = change_budget_snapshot(revised.run_dir, state, contract)
    assert budget.auto_replans_used == 1 and budget.worker_attempts_used == 0
    before = _files(revised.run_dir)
    assert restarted.complete_verification_scope_if_eligible(revised.run_dir.name) is None
    assert _files(revised.run_dir) == before
    trace = read_agent_trace(revised.run_dir / "trace.jsonl")
    assert sum(row["event"] == "change_execution_plan_auto_applied" for row in trace) == 1
    assert not any(row["event"] == "worker_dispatch_committed" for row in trace)


@pytest.mark.parametrize("blocker", ["budget", "other_item", "superseded"])
def test_scope_completion_does_not_consume_budget_or_reactivate_other_items(tmp_path, blocker):
    _, runtime, approved = _scenario(tmp_path, blocker=blocker)
    if blocker == "superseded":
        contract = ChangeContract.model_validate_json((approved.run_dir / "change-contract.json").read_text())
        execution = ExecutionPlan.model_validate_json((approved.run_dir / "execution-plan.json").read_text())
        save_agent_plan(approved.run_dir, project_agent_plan(
            contract, execution, work_item_statuses={"WI-01": "pending", "WI-02": "superseded"},
        ))
    before = _files(approved.run_dir)
    assert runtime.complete_verification_scope_if_eligible(approved.run_dir.name) is None
    assert _files(approved.run_dir) == before


@pytest.mark.parametrize("command", [
    "python -m pytest $TEST_FILE", "pytest tests/test_*.py",
    "pytest tests/test_new.py && echo done", "pytest --unknown tests/test_new.py",
    "pytest -k %FILTER% tests/test_new.py", "pytest 'tests/test_new.py'",
])
def test_scope_completion_does_not_guess_ambiguous_verification(tmp_path, command):
    _, runtime, approved = _scenario(tmp_path, command=command)
    before = _files(approved.run_dir)
    assert runtime.complete_verification_scope_if_eligible(approved.run_dir.name) is None
    assert _files(approved.run_dir) == before


@pytest.mark.parametrize("with_prepare,worker_mode", [
    (False, "multi"), (True, "multi"), (False, "verification_repair"),
    (True, "verification_repair"),
    pytest.param(False, "pause_before_dispatch", id="scope-pause-resume-first-dispatch"),
    pytest.param(False, "pause_before_repair", id="core-failure-pause-resume-repair"),
    pytest.param(False, "two_repairs", id="two-core-failures-two-repairs"),
])
def test_adapter_completes_scope_for_each_work_item_before_dispatch(tmp_path, monkeypatch, with_prepare, worker_mode):
    workspace, runtime, approved = _scenario(
        tmp_path, command="python -m pytest -q tests/test_new.py",
        blocker=worker_mode if worker_mode in {"multi", "two_repairs"} else "", with_prepare=with_prepare,
    )
    if with_prepare:
        before = _files(approved.run_dir)
        assert runtime.complete_verification_scope_if_eligible(approved.run_dir.name) is None
        assert _files(approved.run_dir) == before

    # 每次发布后以公开 Runtime 再入，实际重算来源归档；不得重复修订或派发。
    complete = SupervisorAgentRuntime.complete_verification_scope_if_eligible
    recomputed = []
    repair_packets = []
    resumed_points = []
    needs_repair = worker_mode in {"verification_repair", "pause_before_repair", "two_repairs"}
    failure_count = 2 if worker_mode == "two_repairs" else int(needs_repair)
    contract_before = (approved.run_dir / "change-contract.json").read_bytes()

    def complete_and_reenter(self, run):
        run_dir, state, _, _ = load_agent_bundle(workspace, run)
        if "repair" in state.allowed_actions:
            packet = load_current_fix_packet(workspace, run_dir, state)
            assert packet.verification == "failed"
            assert packet.work_item_id == "WI-01" and packet.execution_plan_revision == 2
            if worker_mode == "two_repairs":
                dispatches = [row for row in read_agent_trace(run_dir / "trace.jsonl")
                              if row["event"] == "worker_dispatch_committed" and row.get("work_item") == "WI-01"]
                assert packet.source_child_run == dispatches[-1]["child_run"]
                assert packet.source_operation_id == dispatches[-1]["operation_id"]
                checkpoint = load_agent_checkpoint(run_dir / f"checkpoints/{state.latest_checkpoint_id}.json")
                assert checkpoint.failed_attempts == [row["child_run"] for row in dispatches]
                assert len(checkpoint.failed_attempts) == len(repair_packets) + 1
                assert packet.repair_round == len(repair_packets) + 1
            if worker_mode == "pause_before_repair":
                SupervisorAgentRecovery(workspace).pause(run, reason="在可信 Core 返修边界切换会话")
                resumed = SupervisorAgentRecovery(workspace).resume_local(run)
                assert "repair" in resumed.state.allowed_actions
                assert "next" not in resumed.state.allowed_actions
                # 必须仍是原 Core 失败形成的同一个修复包，不重新授权或生成替代失败事实。
                assert load_current_fix_packet(workspace, run_dir, resumed.state) == packet
                assert next_attempt_context(run_dir, resumed.state)[0] == 2
                resumed_points.append("repair")
            repair_packets.append(packet)
        revised = complete(self, run)
        if revised is not None:
            before = _files(revised.run_dir)
            assert complete(SupervisorAgentRuntime(workspace), run) is None
            assert _files(revised.run_dir) == before
            recomputed.append(revised.state.current_work_item)
            if worker_mode == "pause_before_dispatch":
                # 两轮连续暂停恢复必须沿链回到同一原 ready，不重置预算或扩大范围。
                for cycle in range(2):
                    SupervisorAgentRecovery(workspace).pause(run, reason=f"首次派发前第 {cycle + 1} 次切换会话")
                    revised = SupervisorAgentRecovery(workspace).resume_local(run)
                    assert next_attempt_context(run_dir, revised.state) == (1, True)
                    before = _files(run_dir)
                    assert complete(SupervisorAgentRuntime(workspace), run) is None
                    assert _files(run_dir) == before
                resumed_points.append("first_dispatch")
        return revised

    monkeypatch.setattr(SupervisorAgentRuntime, "complete_verification_scope_if_eligible", complete_and_reenter)

    class Worker:
        calls = 0

        def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
            assert sandbox == "workspace-write"
            self.calls += 1
            _, state, plan, _ = load_agent_bundle(workspace, approved.run_dir.name)
            target = "tests/test_new.py" if state.current_work_item == "WI-01" else "tests/test_second.py"
            current = next(item for item in plan.work_items if item.work_item_id == state.current_work_item)
            assert target in current.allowed_paths
            if with_prepare:
                assert (repo_path / ".prepared/runs.txt").read_text() == "prepared\n"
            controller = ExecutionController(execution_context)
            controller.prepare(["fixture-worker"], 60)
            # 由真实 pytest 产生失败和 Core FixPacket；不伪造 Observation/Decision。
            # 第二次失败也产生真实的新 Diff，避免无改动返修被其他门禁提前截住。
            expected = (999 if self.calls == 1 else 9999) if self.calls <= failure_count else 2
            (repo_path / target).write_text(
                f"def test_value():\n    assert 1 + 1 == {expected}\n", encoding="utf-8",
            )
            controller.finish("success", reason=None, returncode=0)
            return RunnerResult(status="success", command=["fixture-worker"], output=json.dumps({
                "claimed_status": "completed", "summary": "增加验证", "tests_claimed": [],
                "remaining_questions": [],
            }))

    class Reviewer:
        def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
            assert sandbox == "read-only"
            controller = ExecutionController(execution_context)
            controller.prepare(["fixture-reviewer"], 60)
            context = execution_context.execution_root / "review-context.json"
            files = (json.loads(context.read_text())["changed_files"] if context.is_file()
                     else sorted(set(re.findall(r"^\+\+\+ b/(.+)$", prompt, flags=re.MULTILINE))))
            controller.finish("success", reason=None, returncode=0)
            return RunnerResult(status="success", command=["fixture-reviewer"], output=json.dumps({
                "verdict": "approve", "summary": "未发现阻断", "findings": [],
                "reviewed_files": files, "checked_items": ["scope", "tests"],
            }))

    worker = Worker()
    result = SupervisorAgentProviderAdapter(
        workspace, worker_runner=worker,
        loop_runtime=LoopAutomationRuntime(workspace, reviewer_runner=Reviewer()),
    ).run(approved.run_dir.name, timeout_seconds=60)
    assert worker.calls == (3 if worker_mode == "two_repairs" else
                            1 if worker_mode == "pause_before_dispatch" else 2)
    item_ids = ("WI-01", "WI-02") if worker_mode == "multi" else ("WI-01",)
    assert recomputed == list(item_ids)
    assert len(repair_packets) == failure_count
    assert resumed_points == ({"pause_before_dispatch": ["first_dispatch"],
                               "pause_before_repair": ["repair"]}.get(worker_mode, []))
    assert (result.run_dir / "change-contract.json").read_bytes() == contract_before
    assert all(item.status == "completed" for item in result.plan.work_items)
    trace = read_agent_trace(result.run_dir / "trace.jsonl")
    for item_id in item_ids:
        applied = [i for i, row in enumerate(trace)
                   if row["event"] == "change_execution_plan_auto_applied" and row.get("work_item") == item_id]
        dispatched = [i for i, row in enumerate(trace)
                      if row["event"] == "worker_dispatch_committed" and row.get("work_item") == item_id]
        assert len(applied) == 1 and applied[0] < dispatched[0]
        assert len(dispatched) == failure_count + 1
        assert trace[applied[0]]["route_reason"] == "verification.scope_completed"
        if with_prepare:
            prepared = [i for i, row in enumerate(trace) if row["event"] == "environment_prepare_completed"]
            assert len(prepared) == 1 and prepared[0] < applied[0]
    if worker_mode == "pause_before_dispatch":
        assert sum(row["event"] == "agent_paused" for row in trace) == 2
        assert sum(row["event"] == "agent_resumed" for row in trace) == 2
        contract = ChangeContract.model_validate_json(contract_before)
        budget = change_budget_snapshot(result.run_dir, result.state, contract)
        assert budget.worker_attempts_used == 1 and budget.auto_replans_used == 1
        assert budget.repair_rounds_used == 0
    if needs_repair:
        contract = ChangeContract.model_validate_json((result.run_dir / "change-contract.json").read_text())
        budget = change_budget_snapshot(result.run_dir, result.state, contract)
        assert budget.worker_attempts_used == failure_count + 1 and budget.auto_replans_used == 1
        assert budget.repair_rounds_used == failure_count
        assert any(row["event"] == "supervisor_repair" for row in trace)
        if worker_mode == "two_repairs":
            assert contract.authority_envelope.max_repair_rounds == 2
            assert len({packet.source_child_run for packet in repair_packets}) == 2
            checkpoint = load_agent_checkpoint(result.run_dir / f"checkpoints/{result.state.latest_checkpoint_id}.json")
            assert checkpoint.failed_attempts == [packet.source_child_run for packet in repair_packets]
