from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.agent_change_contract import (
    ChangeAuthorityEnvelope, ChangeContract, ExecutionPlan, ExecutionWorkItem,
)
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_recovery import SupervisorAgentRecovery
from vega.agent_contract import canonical_digest
from vega.agent_provider_adapter import SupervisorAgentProviderAdapter
from vega.agent_persistence import load_agent_checkpoint, save_agent_checkpoint, save_agent_state
from vega.agent_runtime_support import bound_repo


def _scenario(tmp_path: Path, *, blocker: str = ""):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.name", "Vega Test"),
                 ("config", "user.email", "vega@example.invalid")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    (repo / "src").mkdir()
    (repo / "src/one.py").write_text("value = 0\n", encoding="utf-8")
    (repo / "tests").mkdir()
    target = "tests/test_new.py"
    if blocker == "symlink":
        (repo / "actual-tests").mkdir()
        (repo / "actual-tests/keep.txt").write_text("fixture\n", encoding="utf-8")
        try:
            (repo / "tests/linked").symlink_to("../actual-tests", target_is_directory=True)
        except OSError:
            pytest.skip("当前平台无法创建目录符号链接")
        target = "tests/linked/test_new.py"
    command = f"python -m pytest -q {target}"
    (repo / ".gitignore").write_text("__pycache__/\n.vega/\n", encoding="utf-8")
    config = ("version: 1\nscope:\n  allowed_paths: [src/**, tests/**]\nverification:\n"
              f"  commands: [{json.dumps(command)}]\n")
    if blocker == "risk":
        config += ("risk:\n  required_reviews:\n    - id: sensitive\n"
                   "      label: 敏感路径\n      paths: [tests/test_new.py]\n")
    (repo / ".vega.yaml").write_text(config, encoding="utf-8")
    for args in (("add", "."), ("commit", "-m", "测试：初始化")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = SupervisorAgentRuntime(workspace)
    contract = ChangeContract(
        task_id="verification-scope-safety", goal="实现并验证", acceptance=["固定验证通过"],
        required_verification=[command], authority_envelope=ChangeAuthorityEnvelope(
            allowed_paths=["src/one.py", "tests/**" if blocker == "glob_authority" else target],
            forbidden_paths=["tests/**"] if blocker == "forbidden" else [],
            max_changed_files=1 if blocker == "file_budget" else 3,
            max_auto_replans=1,
        ),
    )
    started = runtime.start_change(repo, contract=contract, execution_plan=ExecutionPlan(
        task_id=contract.task_id, contract_revision=1, work_items=[ExecutionWorkItem(
            work_item_id="WI-01", objective="实现", likely_files=["src/one.py"], verification=[command],
        )],
    ))
    return runtime, runtime.approve(started.run_dir.name)


def _files(run_dir: Path) -> dict[Path, bytes]:
    return {path.relative_to(run_dir): path.read_bytes()
            for path in run_dir.rglob("*") if path.is_file()}


@pytest.mark.parametrize("source_kind", ["repair", "reviewer_timeout"])
def test_repeated_repair_rejects_wrong_latest_failure_and_replayed_packet(tmp_path, source_kind):
    from vega.agent_change_fix_packet import load_current_fix_packet
    from vega.agent_reviewer_timeout_retry import prepare_reviewer_timeout_source
    from vega.execution_control import ExecutionController
    from vega.loop_runtime import LoopAutomationRuntime
    from vega.runner import RunnerResult

    runtime, approved = _scenario(tmp_path)
    workspace = tmp_path / "workspace"

    class Worker:
        calls = 0

        def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
            assert sandbox == "workspace-write"
            self.calls += 1
            controller = ExecutionController(execution_context)
            controller.prepare(["fixture-worker"], 60)
            (repo_path / "tests").mkdir(exist_ok=True)
            expected = 0 if source_kind == "reviewer_timeout" and self.calls == 2 else self.calls
            (repo_path / "tests/test_new.py").write_text(
                f"def test_value():\n    assert 0 == {expected}\n", encoding="utf-8",
            )
            controller.finish("success", reason=None, returncode=0)
            return RunnerResult(status="success", command=["fixture-worker"], output=json.dumps({
                "claimed_status": "completed", "summary": "新增测试", "tests_claimed": [],
                "remaining_questions": [],
            }))

    class Reviewer:
        calls = 0

        def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context):
            assert sandbox == "read-only"
            self.calls += 1
            controller = ExecutionController(execution_context)
            controller.prepare(["fixture-reviewer"], 60)
            if source_kind == "reviewer_timeout" and self.calls == 2:
                controller.finish("timed_out", reason="模拟审查超时", returncode=None)
                return RunnerResult(status="timed_out", command=["fixture-reviewer"], output="")
            controller.finish("success", reason=None, returncode=0)
            return RunnerResult(status="success", command=["fixture-reviewer"], output=json.dumps({
                "verdict": "approve", "summary": "测试仍由确定性验证裁决", "findings": [],
                "reviewed_files": ["tests/test_new.py"], "checked_items": ["scope", "tests"],
            }))

    worker = Worker()
    adapter = SupervisorAgentProviderAdapter(
        workspace, worker_runner=worker,
        loop_runtime=LoopAutomationRuntime(workspace, reviewer_runner=Reviewer()),
    )
    first = adapter._run_once(approved.run_dir.name, timeout_seconds=60)
    first_checkpoint = load_agent_checkpoint(
        first.run_dir / f"checkpoints/{first.state.latest_checkpoint_id}.json",
    )
    second = adapter._run_once(first.run_dir.name, timeout_seconds=60)
    checkpoint_path = second.run_dir / f"checkpoints/{second.state.latest_checkpoint_id}.json"
    checkpoint = load_agent_checkpoint(checkpoint_path)
    def load_source():
        if source_kind == "repair":
            return load_current_fix_packet(workspace, second.run_dir, second.state)
        return prepare_reviewer_timeout_source(workspace, second.run_dir.name)

    source = load_source()
    assert worker.calls == 2
    assert len(checkpoint.failed_attempts) == 2
    if source_kind == "repair":
        assert source.repair_round == 2
        assert checkpoint.failed_attempts[-1] == source.source_child_run
    else:
        assert checkpoint.failed_attempts[-1] == source.child_dir.name
    original = checkpoint_path.read_bytes()
    # 包内每条证据仍有效，旧整组证据也不能冒充本轮最新失败。
    for update in (
        {"failed_attempts": list(reversed(checkpoint.failed_attempts))},
        {"failed_attempts": first_checkpoint.failed_attempts,
         "evidence_refs": first_checkpoint.evidence_refs},
    ):
        save_agent_checkpoint(checkpoint_path, checkpoint.model_copy(update=update))
        before = _files(second.run_dir)
        with pytest.raises(ValueError):
            load_source()
        assert _files(second.run_dir) == before
        checkpoint_path.write_bytes(original)

    trace_path = second.run_dir / "trace.jsonl"
    trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
    latest = next(row for row in reversed(trace) if row["event"] == "worker_dispatch_committed")
    original_operation = latest["operation_id"]
    for newer_retry in (False, True):
        if newer_retry:
            latest["operation_id"] = original_operation
            trace.append({**latest, "event": "verification_retry_committed", "phase": "observing",
                          "operation_id": "newer-retry-operation"})
        else:
            latest["operation_id"] = "different-operation"
        trace_path.write_text("".join(json.dumps(row) + "\n" for row in trace), encoding="utf-8")
        before = _files(second.run_dir)
        with pytest.raises(ValueError):
            load_source()
        assert _files(second.run_dir) == before


@pytest.mark.parametrize("blocker", ["symlink", "forbidden", "glob_authority", "risk", "file_budget"])
def test_scope_completion_requires_exact_safe_authority_for_missing_file(tmp_path, blocker):
    runtime, approved = _scenario(tmp_path, blocker=blocker)
    before = _files(approved.run_dir)
    assert runtime.complete_verification_scope_if_eligible(approved.run_dir.name) is None
    assert _files(approved.run_dir) == before
    assert not (bound_repo(approved.run_dir) / "tests/test_new.py").exists()


@pytest.mark.parametrize("drift", ["approval", "workspace"])
def test_scope_completion_rejects_drift_without_publishing_revision(tmp_path, drift):
    runtime, approved = _scenario(tmp_path)
    if drift == "approval":
        path = approved.run_dir / "change-contract.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["goal"] = "未经批准的新目标"
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        (bound_repo(approved.run_dir) / "src/one.py").write_text("value = 2\n", encoding="utf-8")
    before = _files(approved.run_dir)
    if drift == "approval":
        # Runtime 先验证合同投影；损坏证据不得被吞成一次成功的修订。
        with pytest.raises(ValueError):
            runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    else:
        assert runtime.complete_verification_scope_if_eligible(approved.run_dir.name) is None
    assert _files(approved.run_dir) == before


def test_scope_completion_rechecks_workspace_immediately_before_publication(tmp_path, monkeypatch):
    import vega.agent_change_revision_runtime as revision_module

    runtime, approved = _scenario(tmp_path)
    before = _files(approved.run_dir)
    capture = revision_module.capture_bound_workspace

    def drift(run_dir):
        (bound_repo(run_dir) / "src/one.py").write_text("value = 3\n", encoding="utf-8")
        return capture(run_dir)

    monkeypatch.setattr(revision_module, "capture_bound_workspace", drift)
    with pytest.raises(ValueError):
        runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    assert _files(approved.run_dir) == before


@pytest.mark.parametrize("tamper", ["archive", "assessment_source", "checkpoint_reference"])
def test_adapter_rejects_tampered_scope_revision_archive_before_worker(tmp_path, monkeypatch, tamper):
    runtime, approved = _scenario(tmp_path)
    revised = runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    assert revised is not None
    checkpoint_path = revised.run_dir / f"checkpoints/{revised.state.latest_checkpoint_id}.json"
    checkpoint = load_agent_checkpoint(checkpoint_path)
    if tamper == "archive":
        archive = revised.run_dir / "execution-plans/execution-plan-revision-001.json"
        payload = json.loads(archive.read_text(encoding="utf-8"))
        payload["work_items"][0]["verification"] = ["python -m pytest -q tests/other.py"]
        archive.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "assessment_source":
        refs = [ref for ref in checkpoint.evidence_refs if ref.startswith("revisions/")]
        assert len(refs) == 1
        assessment_path = revised.run_dir / refs[0]
        payload = json.loads(assessment_path.read_text(encoding="utf-8"))
        assert payload.pop("verification_scope_source") is not None
        assessment_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        assert any(ref.startswith("revisions/") for ref in checkpoint.evidence_refs)
        checkpoint.evidence_refs = [ref for ref in checkpoint.evidence_refs if not ref.startswith("revisions/")]
        # 重算合法 envelope checksum，确保拒绝来自缺失绑定，而非 JSON/checksum 损坏。
        save_agent_checkpoint(checkpoint_path, checkpoint)
        assert load_agent_checkpoint(checkpoint_path) == checkpoint

    def never_prepare(*args, **kwargs):
        raise AssertionError("修订来源缺失或被篡改时不得进入环境准备")

    monkeypatch.setattr("vega.agent_verification_scope_replan.prepare_change_environment", never_prepare)

    class NeverWorker:
        def run(self, *args, **kwargs):
            raise AssertionError("修订来源被篡改时不得启动 Worker")

    before = _files(revised.run_dir)
    with pytest.raises(ValueError):
        SupervisorAgentProviderAdapter(
            runtime.workspace, worker_runner=NeverWorker(),
        ).run(revised.run_dir.name, timeout_seconds=60)
    assert _files(revised.run_dir) == before


def test_scope_completion_cannot_be_bypassed_by_repair_label_without_fix_packet(tmp_path, monkeypatch):
    runtime, approved = _scenario(tmp_path)
    revised = runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    assert revised is not None
    state = revised.state.model_copy(deep=True)
    state.allowed_actions = ["repair", "replan", "human"]
    checkpoint_path = revised.run_dir / f"checkpoints/{state.latest_checkpoint_id}.json"
    checkpoint = load_agent_checkpoint(checkpoint_path)
    checkpoint.pending_actions = list(state.allowed_actions)
    assert not any(ref.startswith("fix-packets/") for ref in checkpoint.evidence_refs)
    # 保持批准、safe checkpoint、版本、指纹及合法 checksum；仅伪造 repair 意图。
    save_agent_state(revised.run_dir / "agent-state.json", state)
    save_agent_checkpoint(checkpoint_path, checkpoint)

    def never_prepare(*args, **kwargs):
        raise AssertionError("没有可信 Core FixPacket 时不得准备或派发")

    class NeverWorker:
        def run(self, *args, **kwargs):
            raise AssertionError("repair 标签不能授权 Worker")

    monkeypatch.setattr("vega.agent_verification_scope_replan.prepare_change_environment", never_prepare)
    before = _files(revised.run_dir)
    with pytest.raises(ValueError, match="Fix Packet"):
        SupervisorAgentProviderAdapter(
            runtime.workspace, worker_runner=NeverWorker(),
        ).run(revised.run_dir.name, timeout_seconds=60)
    assert _files(revised.run_dir) == before


def test_resumed_scope_rejects_corrupted_original_checkpoint_before_preparation(tmp_path, monkeypatch):
    runtime, approved = _scenario(tmp_path)
    revised = runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    assert revised is not None
    SupervisorAgentRecovery(runtime.workspace).pause(revised.run_dir.name, reason="模拟补齐后的会话切换")
    resumed = SupervisorAgentRecovery(runtime.workspace).resume_local(revised.run_dir.name)
    assert resumed.state.phase == "ready"
    source_path = approved.run_dir / f"checkpoints/{approved.state.latest_checkpoint_id}.json"
    source = load_agent_checkpoint(source_path)
    source.external_side_effects = "unknown"
    # 合法 checksum 的原始来源损坏，不能因后继 pause/resume checkpoint 为 safe 而忽略。
    save_agent_checkpoint(source_path, source)
    assert load_agent_checkpoint(source_path).external_side_effects == "unknown"

    def never_prepare(*args, **kwargs):
        raise AssertionError("恢复链的原来源不可信时不得准备或派发")

    class NeverWorker:
        def run(self, *args, **kwargs):
            raise AssertionError("恢复不能掩盖原来源的未知副作用")

    monkeypatch.setattr("vega.agent_verification_scope_replan.prepare_change_environment", never_prepare)
    before = _files(resumed.run_dir)
    with pytest.raises(ValueError):
        SupervisorAgentProviderAdapter(
            runtime.workspace, worker_runner=NeverWorker(),
        ).run(resumed.run_dir.name, timeout_seconds=60)
    assert _files(resumed.run_dir) == before


@pytest.mark.parametrize("stage", ["paused", "resumed"])
def test_local_resume_source_field_cannot_be_deleted_with_valid_checksum(tmp_path, monkeypatch, stage):
    runtime, approved = _scenario(tmp_path)
    revised = runtime.complete_verification_scope_if_eligible(approved.run_dir.name)
    assert revised is not None
    recovery = SupervisorAgentRecovery(runtime.workspace)
    current = recovery.pause(revised.run_dir.name, reason="测试来源字段缺失")
    if stage == "resumed":
        current = recovery.resume_local(current.run_dir.name)
    path = current.run_dir / f"checkpoints/{current.state.latest_checkpoint_id}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["data"].pop("resume_source_checkpoint") is not None
    # 删除字段并重算合法 checksum；模拟旧 schema 外观，不能靠默认 None 绕过新链校验。
    envelope["digest"] = canonical_digest(envelope["data"])
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert load_agent_checkpoint(path).resume_source_checkpoint is None

    def never_prepare(*args, **kwargs):
        raise AssertionError("来源字段缺失时不得进入环境准备")

    class NeverWorker:
        def run(self, *args, **kwargs):
            raise AssertionError("来源字段缺失时不得派发")

    monkeypatch.setattr("vega.agent_verification_scope_replan.prepare_change_environment", never_prepare)
    before = _files(current.run_dir)
    with pytest.raises(ValueError, match="来源字段缺失"):
        if stage == "paused":
            SupervisorAgentRecovery(runtime.workspace).resume_local(current.run_dir.name)
        else:
            SupervisorAgentProviderAdapter(
                runtime.workspace, worker_runner=NeverWorker(),
            ).run(current.run_dir.name, timeout_seconds=60)
    assert _files(current.run_dir) == before
