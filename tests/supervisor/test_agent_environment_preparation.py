from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vega.agent_change_contract import ChangeAuthorityEnvelope, ChangeContract, ExecutionPlan, ExecutionWorkItem
from vega.agent_environment_preparation import prepare_change_environment
from vega.agent_change_presentation import build_change_approval_snapshot
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_persistence import load_agent_state, read_agent_trace
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_runtime_support import (
    bound_repo, capture_bound_workspace, load_agent_bundle, validate_dispatch_artifacts,
)
from vega.execution_control import find_execution_records
from vega.project_config import ProjectConfig, validate_project_config
from vega.run_status import run_status_payload


@pytest.mark.parametrize("outcome", ["success", "failed", "tracked_mutation"])
def test_preparation_is_owned_once_and_never_verification_success(tmp_path: Path, outcome: str) -> None:
    workspace, run_dir = _approved_run(tmp_path, outcome=outcome)
    before = load_agent_state(run_dir / "agent-state.json")
    result = prepare_change_environment(workspace, run_dir.name)
    current = load_agent_state(run_dir / "agent-state.json")
    leases = find_execution_records(run_dir)
    assert len(leases) == 1
    assert leases[0].lease.step == "environment_prepare"
    assert current.active_operation_id is None
    assert current.active_child_run is None
    trace = read_agent_trace(run_dir / "trace.jsonl")
    assert sum(item["event"] == "environment_prepare_started" for item in trace) == 1
    assert not any(item["event"] == "worker_dispatch_committed" for item in trace)
    assert current.active_candidate_sha is None
    if outcome == "success":
        assert result is None
        assert current.phase == "ready"
        assert current.workspace_fingerprint != before.workspace_fingerprint
        assert current.workspace_fingerprint == capture_bound_workspace(run_dir).fingerprint
        _, _, plan, _ = load_agent_bundle(workspace, run_dir.name)
        validate_dispatch_artifacts(run_dir, current, plan)
        assert prepare_change_environment(workspace, run_dir.name) is None
        assert len(find_execution_records(run_dir)) == 1
    else:
        assert result is not None
        assert current.phase == "needs_human"
        with pytest.raises(ValueError, match="禁止自动重放"):
            prepare_change_environment(workspace, run_dir.name)
        assert len(find_execution_records(run_dir)) == 1


def test_preparation_rejects_unapproved_exact_command(tmp_path: Path) -> None:
    workspace, run_dir = _approved_run(tmp_path, authorized=False)
    with pytest.raises(ValueError, match="不完全一致"):
        prepare_change_environment(workspace, run_dir.name)
    assert find_execution_records(run_dir) == []
    assert not (bound_repo(run_dir) / ".prepared").exists()


def test_preparation_started_without_terminal_is_not_replayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, run_dir = _approved_run(tmp_path)
    calls = []

    def interrupted(*_args, **_kwargs):
        calls.append(True)
        raise RuntimeError("测试：控制器中断")

    monkeypatch.setattr("vega.agent_environment_preparation.run_owned_process", interrupted)
    with pytest.raises(RuntimeError, match="控制器中断"):
        prepare_change_environment(workspace, run_dir.name)
    state = load_agent_state(run_dir / "agent-state.json")
    assert state.operation_started
    assert state.active_operation_id is not None
    with pytest.raises(ValueError, match="禁止自动重放"):
        prepare_change_environment(workspace, run_dir.name)
    assert calls == [True]


def test_preparation_can_be_observed_and_stopped_without_worker(tmp_path: Path) -> None:
    workspace, run_dir = _approved_run(tmp_path, outcome="slow")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(prepare_change_environment, workspace, run_dir.name)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            records = find_execution_records(run_dir)
            if records and records[-1].lease.child_pid:
                break
            if future.done():
                future.result()
                pytest.fail("准备进程未进入可观察运行态")
            time.sleep(0.05)
        else:
            pytest.fail("准备进程没有按时启动")
        payload = run_status_payload(workspace, run_dir.name)
        assert payload["active_operation_kind"] == "environment_prepare"
        assert payload.get("last_child_run") is None
        stopped = SupervisorAgentRecovery(workspace).stop(run_dir.name, reason="测试：停止环境准备")
        assert stopped.state.active_operation_id is not None
        result = future.result(timeout=15)
    assert result is not None and result.state.phase == "needs_human"
    assert find_execution_records(run_dir)[0].lease.status == "stopped"


@pytest.mark.parametrize("commands,max_commands", [([""], 2), (["echo ok", "echo later"], 1), (["echo ok\necho bad"], 2)])
def test_preparation_config_reuses_validation_and_budget(commands: list[str], max_commands: int) -> None:
    config = ProjectConfig.model_validate({"verification": {"prepare_commands": commands, "max_commands": max_commands}})
    assert any(issue.severity == "error" for issue in validate_project_config(config))


def _approved_run(tmp_path: Path, *, outcome: str = "success", authorized: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Vega Test")
    _git(repo, "config", "user.email", "vega@example.invalid")
    (repo / ".gitignore").write_text(".prepared/\n", encoding="utf-8")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    script = "from pathlib import Path\nPath('.prepared').mkdir(exist_ok=True)\nPath('.prepared/cache.txt').write_text('ready')\n"
    if outcome == "failed":
        script += "raise SystemExit(7)\n"
    elif outcome == "tracked_mutation":
        script += "Path('sample.py').write_text('value = 2')\n"
    elif outcome == "slow":
        script += "import time\ntime.sleep(25)\n"
    (repo / "prepare.py").write_text(script, encoding="utf-8")
    (repo / ".vega.yaml").write_text(json.dumps({
        "verification": {"commands": ["python -m compileall -q sample.py"],
                         "prepare_commands": ["python prepare.py"], "timeout_seconds": 30},
    }), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "测试：初始化准备命令")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = ChangeContract(
        task_id="prepare-test", goal="修改示例", acceptance=["示例正确"],
        required_verification=["python -m compileall -q sample.py"],
        prepare_commands=["python prepare.py"] if authorized else [],
        authority_envelope=ChangeAuthorityEnvelope(allowed_paths=["sample.py"]),
    )
    plan = ExecutionPlan(task_id=contract.task_id, contract_revision=1, work_items=[
        ExecutionWorkItem(work_item_id="WI-01", objective="修改示例", likely_files=["sample.py"]),
    ])
    runtime = SupervisorAgentRuntime(workspace)
    started = runtime.start_change(repo, contract=contract, execution_plan=plan)
    if authorized:
        approval = build_change_approval_snapshot(started)
        assert "控制器环境准备" in approval.prompt and "python prepare.py" in approval.prompt
        assert approval.contract_digest != contract.model_copy(update={"prepare_commands": []}).expected_approval_digest()
    approved = runtime.approve(started.run_dir.name, actor="user")
    return workspace, approved.run_dir


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
