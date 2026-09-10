from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega.agent_change_contract import ChangeAuthorityEnvelope, ChangeContract, ExecutionPlan, ExecutionWorkItem
from vega.agent_change_driver import AgentChangeDriver
from vega.agent_change_control import change_budget_snapshot
from vega.agent_core_recheck import core_recheck_available, prepare_core_recheck, require_rechecked_finish
from vega.agent_persistence import load_agent_state, save_agent_state
from vega.agent_provider_adapter import SupervisorAgentProviderAdapter
from vega.agent_repository_binding import bound_repo
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_verification_retry import SupervisorAgentVerificationRetry
from vega.agent_verification_retry_archive import retry_source_finish_ref
from vega.agent_verification_retry_preparation import prepare_verification_retry
from vega.execution_control import ExecutionController
from vega.finish_runtime import FinishRuntime
from vega.loop_runtime import LoopAutomationRuntime
from vega.runner import RunnerResult


class _Runner:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.worker_calls = 0
        self.review_calls = 0

    def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
        del prompt, timeout_seconds
        controller = ExecutionController(execution_context)
        controller.prepare(["fake-runner"], 60)
        if sandbox == "workspace-write":
            self.worker_calls += 1
            (repo_path / "src/one.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
            payload = {"claimed_status": "completed", "summary": "修改完成",
                       "tests_claimed": [], "remaining_questions": []}
        else:
            self.review_calls += 1
            payload = {
                "verdict": self.verdict, "summary": "审查已有修改",
                "reviewed_files": ["src/one.py"], "checked_items": ["需求"],
                "findings": ([] if self.verdict == "approve" else [{
                    "severity": "major", "file": "src/one.py", "line": 1,
                    "title": "需要修复模块行为", "evidence": "当前值不满足要求",
                    "recommendation": "修复当前模块",
                }]),
            }
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(status="success", output=json.dumps(payload), command=["fake-runner"])


class _StaleFinish(FinishRuntime):
    """模拟原 Finish 当时的完整性失败，不改变底层 Core Artifact。"""

    def run(self, run: str) -> Path:
        child = super().run(run)
        path = child / "finish-summary.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["evidence_freshness"]["fresh"] = False
        path.write_text(json.dumps(payload), encoding="utf-8")
        return child


def _blocked_change(tmp_path: Path, verdict: str = "approve"):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src/one.py").write_text("value = 0\n", encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(".vega/\n__pycache__/\n", encoding="utf-8", newline="\n")
    (repo / ".vega.yaml").write_text(
        "version: 1\nscope:\n  allowed_paths: [src/**]\n"
        "verification:\n  commands: [python -m compileall -q src]\n",
        encoding="utf-8", newline="\n",
    )
    for args in (["init", "-b", "main"], ["config", "user.name", "测试"],
                 ["config", "user.email", "test@example.invalid"],
                 ["add", "."], ["commit", "-m", "初始化测试"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    workspace = tmp_path / "w"
    workspace.mkdir()
    contract = ChangeContract(
        task_id="task-recheck", goal="修改模块", acceptance=["行为正确"],
        required_verification=["python -m compileall -q src"],
        authority_envelope=ChangeAuthorityEnvelope(allowed_paths=["src/**"], max_review_rounds=4),
    )
    plan = ExecutionPlan(task_id=contract.task_id, contract_revision=1, work_items=[
        ExecutionWorkItem(work_item_id="WI-01", objective="更新模块", likely_files=["src/one.py"],
                          verification=["python -m compileall -q src"]),
    ])
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(repo, contract=contract, execution_plan=plan)
    approved = runtime.approve(started.run_dir.name, actor="user")
    runner = _Runner(verdict)
    result = SupervisorAgentProviderAdapter(
        workspace, worker_runner=runner,
        loop_runtime=LoopAutomationRuntime(workspace, reviewer_runner=runner),
        finish_runtime=_StaleFinish(workspace),
    ).run(approved.run_dir.name, timeout_seconds=60)
    assert result.state.phase == "needs_human"
    return workspace, result, runner, contract


@pytest.mark.parametrize("verdict", ["approve", "request_changes"])
def test_recheck_reuses_core_without_worker_review_or_verification(
    tmp_path: Path, verdict: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, blocked, runner, contract = _blocked_change(tmp_path, verdict)
    prepared = prepare_core_recheck(workspace, blocked.run_dir.name)
    old_finish = (prepared.child_dir / "finish-summary.json").read_bytes()
    old_observations = {path: path.read_bytes() for path in (blocked.run_dir / "observations").glob("*.json")}
    old_iteration = prepared.child_state.current_iteration
    before_budget = change_budget_snapshot(blocked.run_dir, blocked.state, contract)
    assert core_recheck_available(workspace, blocked.run_dir.name)
    retry = SupervisorAgentVerificationRetry(workspace)
    retry.loop_runtime.continue_assist = lambda *args, **kwargs: pytest.fail("重算不得运行验证或模型")

    if verdict == "approve":
        # 覆盖公开推进接缝：引擎已完成时，本次 change 必须返回完成，而非要求再执行一次。
        driver = AgentChangeDriver(workspace, tmp_path / "repo")
        monkeypatch.setattr(driver, "_verification_retry", lambda _: retry)
        driven = driver.change(run=blocked.run_dir.name)
        assert driven.outcome == "completed" and driven.exit_code == 0
        assert driven.reason_code == "workflow.completed"
        result = driven.run
    else:
        result = retry.recheck_core_if_eligible(blocked.run_dir.name)

    assert result is not None
    assert result.state.phase == ("completed" if verdict == "approve" else "ready")
    if verdict != "approve":
        assert "repair" in result.state.allowed_actions
    assert runner.worker_calls == runner.review_calls == 1
    assert len(list((result.run_dir / "observations").glob("*.json"))) == len(old_observations) + 1
    assert all(path.read_bytes() == content for path, content in old_observations.items())
    archives = list((result.run_dir / "verification-retries").glob("*/source-finish-summary.json"))
    assert len(archives) == 1 and archives[0].read_bytes() == old_finish
    child_state = json.loads((prepared.child_dir / "state.json").read_text(encoding="utf-8"))
    assert child_state["current_iteration"] == old_iteration
    after_budget = change_budget_snapshot(result.run_dir, result.state, contract)
    assert after_budget.verification_retries_used == before_budget.verification_retries_used
    assert after_budget.review_rounds_used == before_budget.review_rounds_used
    assert retry.recheck_core_if_eligible(blocked.run_dir.name) is None


@pytest.mark.parametrize("damage", ["drift", "repeated", "untrusted", "old_protocol"])
def test_recheck_rejects_unsafe_or_repeated_source_without_writes(tmp_path: Path, damage: str) -> None:
    workspace, blocked, _, _ = _blocked_change(tmp_path)
    prepared = prepare_core_recheck(workspace, blocked.run_dir.name)
    if damage == "drift":
        (bound_repo(blocked.run_dir) / "src/one.py").write_text("value = 99\n", encoding="utf-8")
    elif damage == "repeated":
        (blocked.run_dir / "operations" / "prior-recheck.json").write_text(json.dumps({
            "retry_reason": "core_evidence_recheck", "candidate_sha": prepared.candidate_sha,
        }), encoding="utf-8")
    elif damage == "old_protocol":
        path = blocked.run_dir / "agent-state.json"
        state = load_agent_state(path)
        save_agent_state(path, state.model_copy(update={"execution_protocol": 1}))
    else:
        review_run = prepared.child_state.iterations[-1].review_run
        (workspace / "runs" / review_run / "review-verdict.json").write_text("{}", encoding="utf-8")
    state_before = (blocked.run_dir / "agent-state.json").read_bytes()
    operations = sorted((blocked.run_dir / "operations").glob("*.json"))
    assert not core_recheck_available(workspace, blocked.run_dir.name)
    assert SupervisorAgentVerificationRetry(workspace).recheck_core_if_eligible(blocked.run_dir.name) is None
    assert (blocked.run_dir / "agent-state.json").read_bytes() == state_before
    assert sorted((blocked.run_dir / "operations").glob("*.json")) == operations


def test_recheck_adoption_rejects_new_core_artifacts(tmp_path: Path) -> None:
    original = {"loop_status": "success", "iterations": [{"review_run": "review-original"}],
                "review_verdicts": [{"verdict": "approve"}],
                "verification_results": [{"failed_count": 0}], "risk_gate_results": []}
    content = json.dumps(original).encode("utf-8")
    path = tmp_path / retry_source_finish_ref("operation-recheck")
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    prepared = SimpleNamespace(run_dir=tmp_path, source_finish_sha256=hashlib.sha256(content).hexdigest())
    require_rechecked_finish(prepared, "operation-recheck", original)
    for field in ("iterations", "review_verdicts", "verification_results", "risk_gate_results"):
        replaced = {**original, field: ["另一次同 Candidate 的执行结果"]}
        with pytest.raises(ValueError, match="iteration 或证据内容不一致"):
            require_rechecked_finish(prepared, "operation-recheck", replaced)


def test_verification_retry_does_not_accept_readonly_recheck_reason(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="专用入口"):
        prepare_verification_retry(
            tmp_path, "missing-run", loop_runtime=None, provider="codex",
            persistent_sessions=False, retry_reason="core_evidence_recheck",
        )
