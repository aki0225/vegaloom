from __future__ import annotations

import json
import re
import subprocess

import pytest

import vega.agent_change_driver as driver_module
from vega.agent_change_contract import ChangeAuthorityEnvelope, ChangeContract, ExecutionPlan, ExecutionWorkItem
from vega.agent_change_driver import AgentChangeDriver
from vega.agent_persistence import read_agent_trace
from vega.agent_provider_adapter import SupervisorAgentProviderAdapter
from vega.agent_provider_preparation import next_attempt_context
from vega.agent_pre_core_resume import require_pre_core_resume
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_runtime_support import bound_repo
from vega.execution_control import ExecutionController
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.runner import RunnerResult


class _Worker:
    def __init__(self, replan_rounds=1):
        self.calls = 0
        self.replan_rounds = replan_rounds

    def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
        assert sandbox == "workspace-write"
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-worker"], 60)
        self.calls += 1
        # 修订后的重入可以采用已有 WIP，但仍必须经过新 Candidate 和全部 Core 门禁。
        target = "extra" if self.replan_rounds == 1 else f"extra{min(self.calls, self.replan_rounds)}"
        (repo_path / f"src/{target}.py").write_text("value = 1\n", encoding="utf-8")
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(status="success", command=["fake-worker"], output=json.dumps({
            "claimed_status": "completed", "summary": "完成实现",
            "tests_claimed": [], "remaining_questions": [],
        }))


class _Reviewer:
    def __init__(self):
        self.calls = 0

    def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
        assert sandbox == "read-only"
        self.calls += 1
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-reviewer"], 60)
        context = execution_context.execution_root / "review-context.json"
        files = (json.loads(context.read_text(encoding="utf-8"))["changed_files"] if context.is_file()
                 else sorted(set(re.findall(r"^\+\+\+ b/(.+)$", prompt, flags=re.MULTILINE))))
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(status="success", command=["fake-reviewer"], output=json.dumps({
            "verdict": "approve", "summary": "未发现阻断问题", "findings": [],
            "reviewed_files": files, "checked_items": ["scope", "tests"],
        }))


def _scenario(tmp_path, *, blocker="", replan_rounds=1):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.name", "Vega Test"),
                 ("config", "user.email", "vega@example.invalid")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    (repo / "src").mkdir()
    (repo / "src/one.py").write_text("value = 0\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n.vega/\n", encoding="utf-8")
    config = ("version: 1\nscope:\n  allowed_paths: [src/**]\nverification:\n"
              "  commands: [python -m compileall -q src]\n  max_commands: 2\n")
    if blocker == "risk":
        config += ("risk:\n  required_reviews:\n    - id: sensitive\n"
                   "      label: 敏感路径\n      paths: [src/extra.py]\n")
    (repo / ".vega.yaml").write_text(config, encoding="utf-8")
    for args in (("add", "."), ("commit", "-m", "测试：初始化")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    workspace = tmp_path / "w"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = ChangeContract(
        task_id="scope-replan", goal="完成修改", acceptance=["实现正确"],
        required_verification=["python -m compileall -q src"],
        authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/one.py"] if blocker == "contract" else ["src/**"],
            max_auto_replans=0 if blocker == "replan_budget" else replan_rounds,
            max_repair_rounds=0 if blocker == "worker_budget" else max(2, replan_rounds),
        ),
    )
    items = [ExecutionWorkItem(work_item_id="WI-01", objective="修改目标", likely_files=["src/one.py"])]
    if blocker == "other_item":
        items.append(ExecutionWorkItem(work_item_id="WI-02", objective="第二项",
                                       likely_files=["src/extra.py"], depends_on=["WI-01"]))
    started = runtime.start_change(repo, contract=contract, execution_plan=ExecutionPlan(
        task_id=contract.task_id, contract_revision=1, work_items=items,
    ))
    approved = runtime.approve(started.run_dir.name)
    worker, reviewer = _Worker(replan_rounds), _Reviewer()
    adapter = SupervisorAgentProviderAdapter(
        workspace, worker_runner=worker,
        loop_runtime=LoopAutomationRuntime(workspace, reviewer_runner=reviewer),
        finish_runtime=FinishRuntime(workspace),
    )
    return workspace, runtime, approved, adapter, worker, reviewer


@pytest.mark.parametrize("replan_rounds", [1, 6])
def test_change_automatically_replans_scope_and_completes(tmp_path, monkeypatch, replan_rounds):
    workspace, _, approved, adapter, worker, reviewer = _scenario(tmp_path, replan_rounds=replan_rounds)
    contract_before = (approved.run_dir / "change-contract.json").read_bytes()
    monkeypatch.setattr(driver_module, "ensure_change_provider_ready", lambda provider: None)
    monkeypatch.setattr("vega.agent_change_execution.SupervisorAgentProviderAdapter",
                        lambda workspace, **kwargs: adapter)
    result = AgentChangeDriver(workspace, tmp_path / "repo", provider="claude", timeout_seconds=60).change(
        run=approved.run_dir.name,
    )
    assert result.outcome == "completed"
    assert result.run.state.terminal_status == "ready_to_commit"
    assert worker.calls == replan_rounds + 1 and reviewer.calls == 2
    assert (approved.run_dir / "change-contract.json").read_bytes() == contract_before
    assert len(result.run.plan.work_items[0].allowed_paths) == replan_rounds + 1
    trace = read_agent_trace(approved.run_dir / "trace.jsonl")
    assert sum(item["event"] == "change_execution_plan_auto_applied" for item in trace) == replan_rounds
    assert sum(item["event"] == "worker_dispatch_committed" for item in trace) == replan_rounds + 1
    assert not (tmp_path / "repo/src/extra.py").exists()


@pytest.mark.parametrize("blocker", ["contract", "other_item", "risk", "replan_budget", "worker_budget",
                                     "workspace", "scope_evidence", "approval", "core",
                                     "superseded_before", "superseded_after"])
def test_scope_replan_rejects_untrusted_or_unauthorized_sources(tmp_path, blocker):
    workspace, runtime, approved, adapter, worker, reviewer = _scenario(tmp_path, blocker=blocker)
    failed = adapter.run(approved.run_dir.name, timeout_seconds=60)
    assert failed.state.phase == "planning"
    if blocker == "workspace":
        (bound_repo(failed.run_dir) / "src/extra.py").write_text("value = 2\n", encoding="utf-8")
    if blocker == "scope_evidence":
        next((failed.run_dir / "plan-scope").glob("*.json")).write_text("{}", encoding="utf-8")
    if blocker == "approval":
        path = failed.run_dir / "change-contract.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["goal"] = "批准后改变的目标"
        path.write_text(json.dumps(payload), encoding="utf-8")
    if blocker == "core":
        child = next((workspace / "runs").glob("*-bug-loop"))
        (child / "state.json").write_text("{}", encoding="utf-8")
    if blocker.startswith("superseded_"):
        from vega.agent_change_run import project_agent_plan
        from vega.agent_runtime_support import save_agent_plan

        path = failed.run_dir / "execution-plan.json"
        execution = ExecutionPlan.model_validate_json(path.read_text(encoding="utf-8"))
        extra = ExecutionWorkItem(work_item_id="WI-old", objective="旧项", likely_files=["src/old.py"])
        execution.work_items.insert(0 if blocker.endswith("before") else 1, extra)
        path.write_text(execution.model_dump_json(), encoding="utf-8")
        contract = ChangeContract.model_validate_json((failed.run_dir / "change-contract.json").read_text())
        save_agent_plan(failed.run_dir, project_agent_plan(
            contract, execution, work_item_statuses={"WI-01": "superseded", "WI-old": "superseded"},
        ))
    before = {path.relative_to(failed.run_dir): path.read_bytes()
              for path in failed.run_dir.rglob("*") if path.is_file()}
    if blocker == "approval":
        with pytest.raises(ValueError, match="合同的有效投影"):
            runtime.replan_scope_if_eligible(failed.run_dir.name)
    else:
        assert runtime.replan_scope_if_eligible(failed.run_dir.name) is None
    after = {path.relative_to(failed.run_dir): path.read_bytes()
             for path in failed.run_dir.rglob("*") if path.is_file()}
    assert after == before
    assert worker.calls == 1 and reviewer.calls == 0


def test_scope_replan_resumes_once_without_resetting_worker_budget(tmp_path):
    from vega.run_lock import RunMutationBusyError, RunMutationLock

    workspace, runtime, approved, adapter, _, _ = _scenario(tmp_path)
    failed = adapter.run(approved.run_dir.name, timeout_seconds=60)
    with RunMutationLock.acquire(failed.run_dir, "agent.replan"):
        with pytest.raises(RunMutationBusyError):
            runtime.replan_scope_if_eligible(failed.run_dir.name)
    revised = runtime.replan_scope_if_eligible(failed.run_dir.name)
    assert revised is not None and revised.state.phase == "ready"
    assert runtime.replan_scope_if_eligible(revised.run_dir.name) is None
    assert next_attempt_context(revised.run_dir, revised.state, max_repair_rounds=2) == (2, False)
    require_pre_core_resume(workspace, revised.run_dir, revised.state)
    # 单独伪造或破坏原范围证据不能借 auto_apply 事件继续 Writer。
    next((revised.run_dir / "plan-scope").glob("*.json")).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="来源|证据"):
        require_pre_core_resume(workspace, revised.run_dir, revised.state)


def test_scope_replan_rechecks_workspace_before_publication(tmp_path, monkeypatch):
    import vega.agent_change_revision_runtime as revision_module

    _, runtime, approved, adapter, _, _ = _scenario(tmp_path)
    failed = adapter.run(approved.run_dir.name, timeout_seconds=60)
    before = {path.relative_to(failed.run_dir): path.read_bytes()
              for path in failed.run_dir.rglob("*") if path.is_file()}
    capture = revision_module.capture_bound_workspace

    def drift(run_dir):
        (bound_repo(run_dir) / "src/extra.py").write_text("value = 2\n", encoding="utf-8")
        return capture(run_dir)

    monkeypatch.setattr(revision_module, "capture_bound_workspace", drift)
    with pytest.raises(ValueError, match="发布前 Workspace 已漂移"):
        runtime.replan_scope_if_eligible(failed.run_dir.name)
    assert {path.relative_to(failed.run_dir): path.read_bytes()
            for path in failed.run_dir.rglob("*") if path.is_file()} == before


def test_change_stops_when_phase_does_not_advance(tmp_path, monkeypatch):
    workspace, _, approved, _, _, _ = _scenario(tmp_path)
    driver = AgentChangeDriver(workspace, tmp_path / "repo", provider="claude", timeout_seconds=60)
    monkeypatch.setattr(driver, "_advance_phase", lambda current, provider: current)
    result = driver.change(run=approved.run_dir.name)
    assert result.outcome == "attention_required" and result.reason_code == "workflow.no_progress"
