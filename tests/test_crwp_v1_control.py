from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega.execution_control import RunnerExecutionContext
from vega.models import LoopAutomationState
from vega.runner import RunnerResult


TEST_REGISTRATION_HEAD = "a" * 40


def _runtime_freeze_stub() -> dict[str, object]:
    return {
        "head": TEST_REGISTRATION_HEAD,
        "manifest": {"sha256": "c" * 64},
        "registration_head": TEST_REGISTRATION_HEAD,
        "runtime_commit": "b" * 40,
    }


def _load_control_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "pilot"
        / "crwp-v1"
        / "run_crwp_case.py"
    )
    spec = importlib.util.spec_from_file_location("crwp_v1_control", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.repos: list[Path] = []
        self.sandboxes: list[str] = []

    def run(self, prompt, repo_path, *, sandbox, timeout_seconds, execution_context=None):
        del timeout_seconds, execution_context
        self.calls += 1
        self.prompts.append(prompt)
        self.repos.append(repo_path)
        self.sandboxes.append(sandbox)
        return RunnerResult(status="success", output="READY", command=["fake"])


class _FakeWorkerStart:
    def __init__(self) -> None:
        self.created_run_id = "crwp-test-run"
        self.run_ids: list[str] = []

    def assert_bound(self, run_id: str) -> None:
        if run_id != self.created_run_id:
            raise ValueError("run_id 不一致")

    def arm(self, run_id: str) -> None:
        self.assert_bound(run_id)
        self.run_ids.append(run_id)


def _context(tmp_path: Path, role: str = "worker") -> RunnerExecutionContext:
    return RunnerExecutionContext(
        execution_dir=(
            tmp_path
            / "runs"
            / "crwp-test-run"
            / "iterations"
            / "01"
            / "executions"
            / role
        ),
        run_id="crwp-test-run",
        step=role,
        iteration=1,
    )


def _write_launch_fixture(
    module,
    evidence_dir: Path,
    nonce: str,
    *,
    child_pid: int,
    lease_expires_at: datetime,
) -> None:
    execution_path = (
        evidence_dir
        / "supervisor-run"
        / "executions"
        / "controller"
        / "execution.json"
    )
    execution_path.parent.mkdir(parents=True)
    (evidence_dir / "launch-attestation.json").write_text(
        json.dumps(
            {
                "case_id": "CRWP-V1-01",
                "expected_owner_pid": os.getppid(),
                "nonce_sha256": module._sha256_bytes(nonce.encode("utf-8")),
                "registration_head": TEST_REGISTRATION_HEAD,
                "runtime_commit": "b" * 40,
                "runtime_manifest_sha256": "c" * 64,
                "supervisor_execution": (
                    "supervisor-run/executions/controller/execution.json"
                ),
                "supervisor_run_id": "supervisor-run",
            }
        ),
        encoding="utf-8",
    )
    now = datetime.now(UTC)
    lease = module.ExecutionLease(
        run_id="supervisor-run",
        execution_id="synthetic-execution",
        step="crwp-case-controller",
        owner_pid=os.getppid(),
        child_pid=child_pid,
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=lease_expires_at.isoformat(),
        deadline=(now + timedelta(minutes=40)).isoformat(),
        status="running",
    )
    execution_path.write_text(
        lease.model_dump_json(),
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _build_runtime_freeze_fixture(
    module,
    repo: Path,
    monkeypatch,
    *,
    extra_registration_file: bool = False,
    wrong_controller_sha: bool = False,
) -> tuple[str, str]:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "CRWP Test")
    _git(repo, "config", "user.email", "crwp@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")
    control_paths = {
        "controller": "controller.py",
        "control_tests": "tests/test_control.py",
    }
    manifest_path = Path("runtime-freeze.json")
    monkeypatch.setattr(module, "CONTROL_FILE_PATHS", control_paths)
    monkeypatch.setattr(module, "CONTROL_MANIFEST_PATH", manifest_path)

    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "runtime.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (repo / "controller.py").write_text(
        "print('controller')\n",
        encoding="utf-8",
        newline="\n",
    )
    (repo / "tests" / "test_control.py").write_text(
        "def test_control():\n    assert True\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "冻结运行时")
    runtime_commit = _git(repo, "rev-parse", "HEAD")

    entries: dict[str, dict[str, object]] = {}
    for name, relative_path in control_paths.items():
        content = (repo / relative_path).read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        if name == "controller" and wrong_controller_sha:
            sha256 = "0" * 64
        entries[name] = {
            "blob_oid": _git(
                repo,
                "rev-parse",
                f"{runtime_commit}:{relative_path}",
            ),
            "path": relative_path,
            "sha256": sha256,
            "size": len(content),
        }
    manifest = {
        "control_files": entries,
        "pilot_id": "CRWP-V1",
        "runtime_commit": runtime_commit,
        "runtime_src_tree": _git(
            repo,
            "rev-parse",
            f"{runtime_commit}:src",
        ),
        "runtime_tree": _git(
            repo,
            "rev-parse",
            f"{runtime_commit}^{{tree}}",
        ),
        "schema_version": 1,
    }
    (repo / manifest_path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if extra_registration_file:
        (repo / "unexpected.txt").write_text(
            "unexpected\n",
            encoding="utf-8",
            newline="\n",
        )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "登记运行时")
    return runtime_commit, _git(repo, "rev-parse", "HEAD")


def _build_success_evidence_fixture(
    module,
    workspace: Path,
    *,
    state_status: str = "success",
) -> tuple[SimpleNamespace, str, dict[str, object]]:
    repo = (workspace / "target").resolve()
    repo.mkdir(parents=True)
    evidence_dir = workspace / "evidence"
    evidence_dir.mkdir()
    manifest_path = workspace / module.CONTROL_MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        '{"pilot_id":"CRWP-V1"}\n',
        encoding="utf-8",
        newline="\n",
    )
    run_id = "crwp-success-run"
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_id,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(repo),
        input_source="task.md",
        status=state_status,
        current_step="done",
    )
    state.save(run_dir / "state.json")
    (run_dir / "finish-summary.json").write_text(
        json.dumps(
            {
                "artifact_integrity": {"valid": True},
                "evidence_freshness": {"fresh": True},
                "finish_status": "ready_to_commit",
                "latest_verdict": {"verdict": "approve"},
                "latest_verification_failed": False,
                "loop_status": "success",
                "repo_path": str(repo),
                "run_id": run_id,
                "verification_passed": True,
            }
        ),
        encoding="utf-8",
    )
    launch = {
        "case_id": "CRWP-V1-01",
        "execution_id": "synthetic-execution",
        "registration_head": TEST_REGISTRATION_HEAD,
        "runtime_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
    }
    (evidence_dir / "launch-accepted.json").write_text(
        json.dumps(launch),
        encoding="utf-8",
    )
    fingerprint = "d" * 64
    (evidence_dir / "workspace-preflight.json").write_text(
        json.dumps({"fingerprint": fingerprint}),
        encoding="utf-8",
    )
    (evidence_dir / "input-attestation.json").write_text(
        json.dumps(
            {
                "case_id": "CRWP-V1-01",
                "launch": launch,
                "workspace_fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )
    control_summary: dict[str, object] = {
        "case_id": "CRWP-V1-01",
        "error": None,
        "finish_completed": True,
        "finish_status": "ready_to_commit",
        "frozen_inputs_rechecked": True,
        "registered_preflight_rechecked": True,
        "registration_head": TEST_REGISTRATION_HEAD,
        "run_id": run_id,
        "runtime_rechecked": True,
        "workspace_fingerprint": fingerprint,
    }
    args = SimpleNamespace(
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=repo,
    )
    return args, run_id, control_summary


def _stub_recomputed_success(module, monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "validate_loop_evidence_snapshot",
        lambda workspace, repo, run_dir, state: SimpleNamespace(
            artifact_integrity=SimpleNamespace(
                review_verdicts=[SimpleNamespace(verdict="approve")],
                valid=True,
            ),
            evidence_freshness=SimpleNamespace(fresh=True),
        ),
    )
    monkeypatch.setattr(
        module,
        "trusted_verification_passed",
        lambda state, integrity: True,
    )
    monkeypatch.setattr(
        module,
        "latest_verification_failed",
        lambda state, integrity: False,
    )


def test_prompt_audit_blocks_before_external_runner(tmp_path: Path) -> None:
    module = _load_control_module()
    inner = _FakeRunner()
    worker_start = _FakeWorkerStart()
    runner = module.PromptAuditRunner(
        role="worker",
        inner=inner,
        evidence_dir=tmp_path,
        blocked_terms=["forbidden-value"],
        worker_start=worker_start,
        expected_repo=tmp_path,
        expected_workspace=tmp_path,
    )

    result = runner.run(
        "任务包含 forbidden-value",
        tmp_path,
        sandbox="workspace-write",
        timeout_seconds=10,
        execution_context=_context(tmp_path),
    )

    assert result.status == "error"
    assert inner.calls == 0
    assert worker_start.run_ids == []
    audit = json.loads(
        (tmp_path / "prompt-audits" / "worker-iteration-01-precall.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["allowed_to_invoke"] is False
    assert audit["matched_term_count"] == 1
    assert len(audit["matched_term_sha256"]) == 1


def test_prompt_audit_hashes_exact_utf8_and_delegates(tmp_path: Path) -> None:
    module = _load_control_module()
    inner = _FakeRunner()
    worker_start = _FakeWorkerStart()
    runner = module.PromptAuditRunner(
        role="worker",
        inner=inner,
        evidence_dir=tmp_path,
        blocked_terms=[],
        worker_start=worker_start,
        expected_repo=tmp_path,
        expected_workspace=tmp_path,
    )
    prompt = "第一行\n第二行\n"

    result = runner.run(
        prompt,
        tmp_path,
        sandbox="workspace-write",
        timeout_seconds=10,
        execution_context=_context(tmp_path),
    )

    assert result.status == "success"
    assert inner.calls == 1
    assert inner.prompts == [prompt]
    assert inner.repos == [tmp_path]
    assert inner.sandboxes == ["workspace-write"]
    assert worker_start.run_ids == ["crwp-test-run"]
    audit = json.loads(
        (tmp_path / "prompt-audits" / "worker-iteration-01-precall.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["prompt_utf8_bytes"] == len(prompt.encode("utf-8"))
    assert audit["prompt_sha256"] == hashlib.sha256(
        prompt.encode("utf-8")
    ).hexdigest()
    assert audit["prompt_lines"] == 2
    assert audit["allowed_to_invoke"] is True


def test_worker_start_recorder_is_idempotent_for_same_run(tmp_path: Path) -> None:
    module = _load_control_module()
    recorder = module.WorkerStartRecorder(tmp_path / "evidence")

    recorder.record_created("crwp-test-run")
    recorder.arm("crwp-test-run")
    recorder.arm("crwp-test-run")

    created = json.loads(
        (tmp_path / "evidence" / "run-created.json").read_text(
            encoding="utf-8"
        )
    )
    payload = json.loads(
        (tmp_path / "evidence" / "wall-clock-start.json").read_text(
            encoding="utf-8"
        )
    )
    assert created["run_id"] == "crwp-test-run"
    assert payload["run_id"] == "crwp-test-run"
    assert payload["deadline_seconds"] == 1800


def test_prompt_audit_rejects_reviewer_writable_sandbox(tmp_path: Path) -> None:
    module = _load_control_module()
    inner = _FakeRunner()
    worker_start = _FakeWorkerStart()
    runner = module.PromptAuditRunner(
        role="reviewer",
        inner=inner,
        evidence_dir=tmp_path,
        blocked_terms=[],
        worker_start=worker_start,
        expected_repo=tmp_path,
        expected_workspace=tmp_path,
    )
    context = _context(tmp_path, "reviewer")

    result = runner.run(
        "只读评审",
        tmp_path,
        sandbox="workspace-write",
        timeout_seconds=10,
        execution_context=context,
    )

    assert result.status == "error"
    assert inner.calls == 0
    audit = json.loads(
        (
            tmp_path
            / "prompt-audits"
            / "reviewer-iteration-01-precall.json"
        ).read_text(encoding="utf-8")
    )
    assert audit["allowed_to_invoke"] is False
    assert audit["role_boundary_errors"] == [
        "角色 sandbox 与冻结边界不一致"
    ]


def test_prompt_audit_allows_bound_read_only_reviewer(tmp_path: Path) -> None:
    module = _load_control_module()
    inner = _FakeRunner()
    worker_start = _FakeWorkerStart()
    runner = module.PromptAuditRunner(
        role="reviewer",
        inner=inner,
        evidence_dir=tmp_path,
        blocked_terms=[],
        worker_start=worker_start,
        expected_repo=tmp_path,
        expected_workspace=tmp_path,
    )
    context = _context(tmp_path, "reviewer")

    result = runner.run(
        "只读评审",
        tmp_path,
        sandbox="read-only",
        timeout_seconds=10,
        execution_context=context,
    )

    assert result.status == "success"
    assert inner.calls == 1
    assert inner.sandboxes == ["read-only"]
    audit = json.loads(
        (
            tmp_path
            / "prompt-audits"
            / "reviewer-iteration-01-precall.json"
        ).read_text(encoding="utf-8")
    )
    assert audit["allowed_to_invoke"] is True
    assert audit["role_boundary_errors"] == []


def test_prompt_audit_rejects_run_id_different_from_root_binding(
    tmp_path: Path,
) -> None:
    module = _load_control_module()
    inner = _FakeRunner()
    worker_start = _FakeWorkerStart()
    runner = module.PromptAuditRunner(
        role="reviewer",
        inner=inner,
        evidence_dir=tmp_path,
        blocked_terms=[],
        worker_start=worker_start,
        expected_repo=tmp_path,
        expected_workspace=tmp_path,
    )
    context = RunnerExecutionContext(
        execution_dir=(
            tmp_path
            / "runs"
            / "other-run"
            / "iterations"
            / "01"
            / "executions"
            / "reviewer"
        ),
        run_id="other-run",
        step="reviewer",
        iteration=1,
    )

    result = runner.run(
        "修复任务",
        tmp_path,
        sandbox="read-only",
        timeout_seconds=10,
        execution_context=context,
    )

    assert result.status == "error"
    assert inner.calls == 0
    assert worker_start.run_ids == []


def test_prompt_audit_rejects_execution_dir_outside_bound_iteration(
    tmp_path: Path,
) -> None:
    module = _load_control_module()
    inner = _FakeRunner()
    runner = module.PromptAuditRunner(
        role="worker",
        inner=inner,
        evidence_dir=tmp_path,
        blocked_terms=[],
        worker_start=_FakeWorkerStart(),
        expected_repo=tmp_path,
        expected_workspace=tmp_path,
    )
    context = RunnerExecutionContext(
        execution_dir=tmp_path / "unbound-execution",
        run_id="crwp-test-run",
        step="worker",
        iteration=1,
    )

    result = runner.run(
        "修复任务",
        tmp_path,
        sandbox="workspace-write",
        timeout_seconds=10,
        execution_context=context,
    )

    assert result.status == "error"
    assert inner.calls == 0
    audit = json.loads(
        (tmp_path / "prompt-audits" / "worker-iteration-01-precall.json").read_text(
            encoding="utf-8"
        )
    )
    assert "execution_dir 与冻结 run/iteration/role 边界不一致" in audit[
        "role_boundary_errors"
    ]


def test_loop_creation_records_exact_run_before_worker(tmp_path: Path) -> None:
    module = _load_control_module()
    recorder = module.WorkerStartRecorder(tmp_path / "evidence")
    (tmp_path / "runs" / "crwp-created").mkdir(parents=True)

    with module._record_loop_creation(recorder):
        run_id, run_dir = module.loop_runtime_module.create_run_dir(
            tmp_path,
            "crwp-created",
        )

    assert run_id == "crwp-created-02"
    assert run_dir.is_dir()
    payload = json.loads(
        (tmp_path / "evidence" / "run-created.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["run_id"] == run_id
    assert recorder.created_run_id == run_id


def test_execute_child_without_supervisor_nonce_fails_before_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    monkeypatch.delenv(module.LAUNCH_NONCE_ENV, raising=False)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    args = SimpleNamespace(
        blocked_terms=None,
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=tmp_path / "target",
        task=tmp_path / "task.md",
    )

    with pytest.raises(ValueError, match="缺少 supervisor 一次性启动凭据"):
        module._execute_child(args, tmp_path)

    assert not (tmp_path / "runs").exists()
    assert not (evidence_dir / "control-summary.json").exists()


def test_child_launch_accepts_matching_live_supervisor_lease(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    nonce = "synthetic-supervisor-nonce"
    now = datetime.now(UTC)
    _write_launch_fixture(
        module,
        evidence_dir,
        nonce,
        child_pid=os.getpid(),
        lease_expires_at=now + timedelta(seconds=30),
    )
    monkeypatch.setenv(module.LAUNCH_NONCE_ENV, nonce)
    args = SimpleNamespace(
        case_id="CRWP-V1-01",
        registration_head=TEST_REGISTRATION_HEAD,
    )

    accepted = module._validate_child_launch(args, evidence_dir)

    assert accepted["child_pid"] == os.getpid()
    assert accepted["owner_pid"] == os.getppid()
    assert accepted["execution_id"] == "synthetic-execution"
    assert module.LAUNCH_NONCE_ENV not in os.environ


@pytest.mark.parametrize(
    ("child_pid", "expires_delta", "expected_message"),
    [
        (os.getpid(), -1, "lease 已过期"),
        (os.getpid() + 100_000, 30, "无法确认当前 child"),
    ],
)
def test_child_launch_rejects_invalid_supervisor_lease(
    tmp_path: Path,
    monkeypatch,
    child_pid: int,
    expires_delta: int,
    expected_message: str,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    nonce = "synthetic-supervisor-nonce"
    _write_launch_fixture(
        module,
        evidence_dir,
        nonce,
        child_pid=child_pid,
        lease_expires_at=datetime.now(UTC)
        + timedelta(seconds=expires_delta),
    )
    monkeypatch.setenv(module.LAUNCH_NONCE_ENV, nonce)
    monkeypatch.setattr(module, "CHILD_LEASE_WAIT_SECONDS", 0.05)
    args = SimpleNamespace(
        case_id="CRWP-V1-01",
        registration_head=TEST_REGISTRATION_HEAD,
    )

    with pytest.raises(ValueError, match=expected_message):
        module._validate_child_launch(args, evidence_dir)

    assert not (evidence_dir / "launch-accepted.json").exists()


def test_formal_evidence_must_stay_outside_target_repo(tmp_path: Path) -> None:
    module = _load_control_module()
    repo = tmp_path / ".tmp" / "target"
    repo.mkdir(parents=True)
    outside_root = repo / "node_modules" / "formal-run"

    with pytest.raises(ValueError, match="必须位于"):
        module._assert_formal_path_boundaries(
            repo,
            outside_root,
            tmp_path,
        )

    overlapping_repo = (
        tmp_path
        / ".local-validation"
        / "crwp-v1"
        / "formal-runs"
        / "target"
    )
    overlapping_repo.mkdir(parents=True)
    with pytest.raises(ValueError, match="不得位于目标仓库内"):
        module._assert_formal_path_boundaries(
            overlapping_repo,
            overlapping_repo / "ignored" / "run",
            tmp_path,
        )

    formal_root = (
        tmp_path / ".local-validation" / "crwp-v1" / "formal-runs"
    )
    with pytest.raises(ValueError, match="独立的单次运行子目录"):
        module._assert_formal_path_boundaries(
            repo,
            formal_root,
            tmp_path,
        )

    valid_evidence = (
        formal_root / "case-01"
    )
    module._assert_formal_path_boundaries(repo, valid_evidence, tmp_path)

    repo_inside_evidence = valid_evidence / "target"
    with pytest.raises(ValueError, match="目标仓库不得位于正式证据目录内"):
        module._assert_formal_path_boundaries(
            repo_inside_evidence,
            valid_evidence,
            tmp_path,
        )


def test_runtime_freeze_accepts_exact_two_commit_registration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    _, registration_head = _build_runtime_freeze_fixture(
        module,
        tmp_path / "runtime",
        monkeypatch,
    )

    result = module._assert_runtime_ready(
        tmp_path / "runtime",
        registration_head,
    )

    assert result["head"] == registration_head
    assert result["registration_head"] == registration_head
    assert set(result["control_files"]) == {"controller", "control_tests"}


@pytest.mark.parametrize(
    ("extra_registration_file", "wrong_controller_sha", "expected_message"),
    [
        (True, False, "只能新增 Runtime 冻结 manifest"),
        (False, True, "SHA-256 或大小"),
    ],
)
def test_runtime_freeze_rejects_registration_or_manifest_drift(
    tmp_path: Path,
    monkeypatch,
    extra_registration_file: bool,
    wrong_controller_sha: bool,
    expected_message: str,
) -> None:
    module = _load_control_module()
    _, registration_head = _build_runtime_freeze_fixture(
        module,
        tmp_path / "runtime",
        monkeypatch,
        extra_registration_file=extra_registration_file,
        wrong_controller_sha=wrong_controller_sha,
    )

    with pytest.raises(ValueError, match=expected_message):
        module._assert_runtime_ready(
            tmp_path / "runtime",
            registration_head,
        )


def test_runtime_freeze_rejects_non_registration_head(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    runtime_commit, _ = _build_runtime_freeze_fixture(
        module,
        tmp_path / "runtime",
        monkeypatch,
    )

    with pytest.raises(ValueError, match="精确 checkout 到登记提交"):
        module._assert_runtime_ready(
            tmp_path / "runtime",
            runtime_commit,
        )


def test_success_evidence_accepts_consistent_real_terminal_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    _stub_recomputed_success(module, monkeypatch)
    args, run_id, control_summary = _build_success_evidence_fixture(
        module,
        tmp_path,
    )

    errors = module._validate_success_evidence(
        tmp_path,
        args.evidence_dir,
        args,
        run_id,
        control_summary,
    )

    assert errors == []


@pytest.mark.parametrize("state_status", ["failed", "needs_human"])
def test_success_evidence_rejects_non_success_real_state(
    tmp_path: Path,
    monkeypatch,
    state_status: str,
) -> None:
    module = _load_control_module()
    _stub_recomputed_success(module, monkeypatch)
    args, run_id, control_summary = _build_success_evidence_fixture(
        module,
        tmp_path,
        state_status=state_status,
    )

    errors = module._validate_success_evidence(
        tmp_path,
        args.evidence_dir,
        args,
        run_id,
        control_summary,
    )

    assert f"根 state 不是 success：{state_status}" in errors


def test_success_evidence_rejects_missing_launch_acceptance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    _stub_recomputed_success(module, monkeypatch)
    args, run_id, control_summary = _build_success_evidence_fixture(
        module,
        tmp_path,
    )
    (args.evidence_dir / "launch-accepted.json").unlink()

    errors = module._validate_success_evidence(
        tmp_path,
        args.evidence_dir,
        args,
        run_id,
        control_summary,
    )

    assert "成功 child 缺少可解析的 launch-accepted.json" in errors


@pytest.mark.parametrize(
    ("relative_path", "expected_message"),
    [
        ("launch-accepted.json", "launch-accepted.json 顶层必须是对象"),
        ("workspace-preflight.json", "workspace-preflight.json 顶层必须是对象"),
        ("input-attestation.json", "input-attestation.json 顶层必须是对象"),
        (
            "runs/crwp-success-run/finish-summary.json",
            "真实 finish-summary.json 顶层必须是对象",
        ),
    ],
)
def test_success_evidence_rejects_json_null_artifacts(
    tmp_path: Path,
    monkeypatch,
    relative_path: str,
    expected_message: str,
) -> None:
    module = _load_control_module()
    _stub_recomputed_success(module, monkeypatch)
    args, run_id, control_summary = _build_success_evidence_fixture(
        module,
        tmp_path,
    )
    artifact_path = (
        tmp_path / relative_path
        if relative_path.startswith("runs/")
        else args.evidence_dir / relative_path
    )
    artifact_path.write_text(
        "null\n",
        encoding="utf-8",
    )

    errors = module._validate_success_evidence(
        tmp_path,
        args.evidence_dir,
        args,
        run_id,
        control_summary,
    )

    assert expected_message in errors


def test_success_evidence_rejects_surface_green_when_recompute_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    args, run_id, control_summary = _build_success_evidence_fixture(
        module,
        tmp_path,
    )
    monkeypatch.setattr(
        module,
        "validate_loop_evidence_snapshot",
        lambda workspace, repo, run_dir, state: SimpleNamespace(
            artifact_integrity=SimpleNamespace(
                review_verdicts=[SimpleNamespace(verdict="request_changes")],
                valid=False,
            ),
            evidence_freshness=SimpleNamespace(fresh=False),
        ),
    )
    monkeypatch.setattr(
        module,
        "trusted_verification_passed",
        lambda state, integrity: False,
    )
    monkeypatch.setattr(
        module,
        "latest_verification_failed",
        lambda state, integrity: True,
    )

    errors = module._validate_success_evidence(
        tmp_path,
        args.evidence_dir,
        args,
        run_id,
        control_summary,
    )

    assert "独立重算的 Finish 未形成 ready_to_commit" in errors
    assert "独立重算未确认最新确定性验证通过" in errors
    assert "独立重算未确认 artifact integrity" in errors
    assert "独立重算未确认 evidence freshness" in errors
    assert "独立重算未确认 Reviewer approve" in errors


def test_success_evidence_turns_recompute_exception_into_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    args, run_id, control_summary = _build_success_evidence_fixture(
        module,
        tmp_path,
    )

    def raise_recompute_error(workspace, repo, run_dir, state):
        del workspace, repo, run_dir, state
        raise ValueError("synthetic recompute failure")

    monkeypatch.setattr(
        module,
        "validate_loop_evidence_snapshot",
        raise_recompute_error,
    )

    errors = module._validate_success_evidence(
        tmp_path,
        args.evidence_dir,
        args,
        run_id,
        control_summary,
    )

    assert errors == [
        "无法独立重算 Finish 可信门禁：synthetic recompute failure"
    ]


def test_supervisor_stops_owned_controller_when_deadline_has_no_active_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    start_path = evidence_dir / "wall-clock-start.json"
    child_code = textwrap.dedent(
        f"""
        import json
        import time
        from pathlib import Path

        path = Path({str(start_path)!r})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({{
            "deadline_seconds": 0.2,
            "run_id": "synthetic-run",
            "worker_started_monotonic": time.monotonic(),
        }}), encoding="utf-8")
        time.sleep(30)
        """
    )
    monkeypatch.setattr(
        module,
        "_child_command",
        lambda args: [sys.executable, "-c", child_code],
    )
    monkeypatch.setattr(module, "TOTAL_WALL_CLOCK_SECONDS", 0.2)
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(module, "SUPERVISOR_PROCESS_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(
        module,
        "_assert_runtime_ready",
        lambda workspace, registration_head: _runtime_freeze_stub(),
    )
    monkeypatch.setattr(
        module,
        "_invoke_stop_cli",
        lambda workspace, run_id: {
            "error": "synthetic no active execution",
            "returncode": 1,
            "stderr": "",
            "stdout": "",
        },
    )
    args = SimpleNamespace(
        blocked_terms=None,
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=tmp_path,
        task=tmp_path / "task.md",
    )

    returncode = module._run_supervisor(args, tmp_path)

    assert returncode == 1
    summary = json.loads(
        (evidence_dir / "supervisor-summary.json").read_text(encoding="utf-8")
    )
    assert summary["deadline_reached"] is True
    assert summary["controller_termination_unconfirmed"] is False
    assert summary["supervisor_stop"]["requested"] is True


def test_supervisor_postcheck_rejects_deadline_race(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    child_code = textwrap.dedent(
        f"""
        import json
        import time
        from pathlib import Path

        root = Path({str(evidence_dir)!r})
        root.mkdir(parents=True, exist_ok=True)
        run_id = "synthetic-deadline-run"
        started = time.monotonic() - 1
        (root / "run-created.json").write_text(json.dumps({{
            "run_created_monotonic": started,
            "run_id": run_id,
        }}), encoding="utf-8")
        (root / "wall-clock-start.json").write_text(json.dumps({{
            "deadline_seconds": 0.2,
            "run_id": run_id,
            "worker_started_monotonic": started,
        }}), encoding="utf-8")
        (root / "control-summary.json").write_text(json.dumps({{
            "finish_completed": True,
            "finish_status": "ready_to_commit",
            "run_id": run_id,
        }}), encoding="utf-8")
        """
    )
    monkeypatch.setattr(
        module,
        "_child_command",
        lambda args: [sys.executable, "-c", child_code],
    )
    class DormantMonitorThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            return None

        def join(self, timeout=None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(module.threading, "Thread", DormantMonitorThread)
    monkeypatch.setattr(module, "TOTAL_WALL_CLOCK_SECONDS", 0.2)
    monkeypatch.setattr(module, "SUPERVISOR_PROCESS_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(
        module,
        "_assert_runtime_ready",
        lambda workspace, registration_head: _runtime_freeze_stub(),
    )
    monkeypatch.setattr(
        module,
        "_recover_interrupted_run",
        lambda workspace, run_id, reason: {
            "attempted": False,
            "reason": "synthetic terminal",
            "status": "not_needed",
        },
    )
    args = SimpleNamespace(
        blocked_terms=None,
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=tmp_path,
        task=tmp_path / "task.md",
    )

    returncode = module._run_supervisor(args, tmp_path)

    assert returncode == 1
    summary = json.loads(
        (evidence_dir / "supervisor-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["child_returncode"] == 0
    assert summary["deadline_reached"] is True
    assert summary["worker_elapsed_seconds"] >= 1
    deadline = json.loads(
        (evidence_dir / "wall-clock-deadline.json").read_text(
            encoding="utf-8"
        )
    )
    assert deadline["run_id"] == "synthetic-deadline-run"
    assert deadline["source"] == "postcheck"


def test_supervisor_monitor_error_stops_owned_controller(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    start_path = evidence_dir / "wall-clock-start.json"
    child_code = textwrap.dedent(
        f"""
        import json
        import time
        from pathlib import Path

        path = Path({str(start_path)!r})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({{
            "deadline_seconds": 1800,
            "run_id": "",
            "worker_started_monotonic": time.monotonic(),
        }}), encoding="utf-8")
        time.sleep(30)
        """
    )
    monkeypatch.setattr(
        module,
        "_child_command",
        lambda args: [sys.executable, "-c", child_code],
    )
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(module, "SUPERVISOR_PROCESS_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(
        module,
        "_assert_runtime_ready",
        lambda workspace, registration_head: _runtime_freeze_stub(),
    )
    args = SimpleNamespace(
        blocked_terms=None,
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=tmp_path,
        task=tmp_path / "task.md",
    )

    returncode = module._run_supervisor(args, tmp_path)

    assert returncode == 1
    summary = json.loads(
        (evidence_dir / "supervisor-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert "缺少非空 run_id" in summary["monitor_error"]
    assert summary["supervisor_stop"]["requested"] is True
    assert summary["controller_termination_unconfirmed"] is False


def test_supervisor_rejects_child_success_when_independent_gate_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    child_code = textwrap.dedent(
        f"""
        import json
        import time
        from pathlib import Path

        root = Path({str(evidence_dir)!r})
        root.mkdir(parents=True, exist_ok=True)
        run_id = "synthetic-success-run"
        now = time.monotonic()
        (root / "run-created.json").write_text(json.dumps({{
            "run_created_monotonic": now,
            "run_id": run_id,
        }}), encoding="utf-8")
        (root / "wall-clock-start.json").write_text(json.dumps({{
            "deadline_seconds": 1800,
            "run_id": run_id,
            "worker_started_monotonic": now,
        }}), encoding="utf-8")
        (root / "control-summary.json").write_text(json.dumps({{
            "run_id": run_id,
        }}), encoding="utf-8")
        """
    )

    class DormantMonitorThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            return None

        def join(self, timeout=None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(
        module,
        "_child_command",
        lambda args: [sys.executable, "-c", child_code],
    )
    monkeypatch.setattr(module.threading, "Thread", DormantMonitorThread)
    monkeypatch.setattr(
        module,
        "_assert_runtime_ready",
        lambda workspace, registration_head: _runtime_freeze_stub(),
    )
    monkeypatch.setattr(
        module,
        "_validate_success_evidence",
        lambda workspace, evidence_dir, args, run_id, control_summary: [
            "独立重算未确认最新确定性验证通过"
        ],
    )
    monkeypatch.setattr(
        module,
        "_recover_interrupted_run",
        lambda workspace, run_id, reason: {
            "attempted": False,
            "reason": "synthetic terminal",
            "status": "not_needed",
        },
    )
    args = SimpleNamespace(
        blocked_terms=None,
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=tmp_path,
        task=tmp_path / "task.md",
    )

    returncode = module._run_supervisor(args, tmp_path)

    assert returncode == 1
    summary = json.loads(
        (evidence_dir / "supervisor-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        "独立重算未确认最新确定性验证通过"
        in summary["postcheck_errors"]
    )


def test_supervisor_records_summary_when_owned_controller_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    evidence_dir = tmp_path / "evidence"
    monkeypatch.setattr(
        module,
        "_assert_runtime_ready",
        lambda workspace, registration_head: _runtime_freeze_stub(),
    )

    def raise_controller_error(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("synthetic controller failure")

    monkeypatch.setattr(
        module,
        "run_owned_process",
        raise_controller_error,
    )
    args = SimpleNamespace(
        blocked_terms=None,
        case_id="CRWP-V1-01",
        evidence_dir=evidence_dir,
        registration_head=TEST_REGISTRATION_HEAD,
        repo=tmp_path,
        task=tmp_path / "task.md",
    )

    returncode = module._run_supervisor(args, tmp_path)

    assert returncode == 1
    summary = json.loads(
        (evidence_dir / "supervisor-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["controller_error"] == "synthetic controller failure"
    assert (
        "owned controller 调用异常：synthetic controller failure"
        in summary["postcheck_errors"]
    )


@pytest.mark.parametrize(
    ("reader_name", "filename", "expected_message"),
    [
        (
            "_read_run_created",
            "run-created.json",
            "run-created.json 顶层必须是对象",
        ),
        (
            "_read_worker_start",
            "wall-clock-start.json",
            "wall-clock-start.json 顶层必须是对象",
        ),
    ],
)
def test_supervisor_artifact_readers_reject_non_object_json(
    tmp_path: Path,
    reader_name: str,
    filename: str,
    expected_message: str,
) -> None:
    module = _load_control_module()
    path = tmp_path / filename
    path.write_text("null\n", encoding="utf-8")

    with pytest.raises(ValueError, match=expected_message):
        getattr(module, reader_name)(path, strict=True)


def test_recovery_uses_exact_running_run_and_confirms_needs_human(
    tmp_path: Path,
) -> None:
    module = _load_control_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "exact-run"
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(repo),
        input_source="task.md",
        status="running",
        current_step="worker",
    )
    state.save(run_dir / "state.json")

    result = module._recover_interrupted_run(
        tmp_path,
        run_dir.name,
        "模拟控制器中断",
    )

    recovered = json.loads(
        (run_dir / "state.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "recovered"
    assert result["run_id"] == run_dir.name
    assert recovered["run_id"] == run_dir.name
    assert recovered["status"] == "needs_human"


def test_recovery_rejects_untrusted_run_id_without_path_escape(
    tmp_path: Path,
) -> None:
    module = _load_control_module()
    (tmp_path / "runs").mkdir()

    result = module._recover_interrupted_run(
        tmp_path,
        "../outside",
        "模拟路径逃逸",
    )

    assert result["attempted"] is False
    assert result["status"] == "failed"


def test_recovery_rejects_non_object_state_json(tmp_path: Path) -> None:
    module = _load_control_module()
    run_dir = tmp_path / "runs" / "bad-state"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("null\n", encoding="utf-8")

    result = module._recover_interrupted_run(
        tmp_path,
        run_dir.name,
        "模拟非法 state",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "state.json 顶层必须是对象"


def test_recovery_turns_permission_error_into_failed_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = tmp_path / "runs" / "permission-error"
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        repo_path=str(repo),
        input_source="task.md",
        status="running",
        current_step="worker",
    )
    state.save(run_dir / "state.json")

    def raise_permission_error(self, run_id, reason):
        del self, run_id, reason
        raise PermissionError("synthetic recovery denied")

    monkeypatch.setattr(
        module.RecoveryRuntime,
        "recover_loop",
        raise_permission_error,
    )

    result = module._recover_interrupted_run(
        tmp_path,
        run_dir.name,
        "模拟权限错误",
    )

    assert result["status"] == "failed"
    assert "synthetic recovery denied" in result["error"]


def test_supervisor_stop_request_handles_permission_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_control_module()

    def raise_permission_error(supervisor_root, reason):
        del supervisor_root, reason
        raise PermissionError("synthetic denied")

    monkeypatch.setattr(
        module,
        "request_stop_for_run",
        raise_permission_error,
    )

    result = module._request_supervisor_stop(
        tmp_path / "supervisor-run",
        "模拟停止",
    )

    assert result["requested"] is False
    assert "synthetic denied" in result["error"]


def test_native_driver_helper_only_stubs_unused_drivers(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("当前环境没有 Node.js，无法验证 native driver helper")

    helper = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "pilot"
        / "crwp-v1"
        / "ignore-native-drivers.cjs"
    )
    probe = tmp_path / "probe.cjs"
    probe.write_text(
        textwrap.dedent(
            """
            const Module = require('node:module');
            const originalLoad = Module._load;
            const forwarded = [];
            Module._load = function fakeUpstream(request) {
              if (request === 'node:module') {
                return Module;
              }
              forwarded.push(request);
              return { forwarded: request };
            };
            originalLoad.call(Module, process.argv[2], module, false);

            const requests = [
              'ibm_db',
              'odbc',
              'sqlite3',
              '@sequelize/sqlite3',
              'odbc-extra',
            ];
            const results = Object.fromEntries(
              requests.map(request => [
                request,
                Module._load(request, module, false),
              ]),
            );
            process.stdout.write(JSON.stringify({ forwarded, results }));
            """
        ).lstrip(),
        encoding="utf-8",
        newline="\n",
    )

    completed = subprocess.run(
        [node, str(probe), str(helper)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "forwarded": ["sqlite3", "@sequelize/sqlite3", "odbc-extra"],
        "results": {
            "ibm_db": {},
            "odbc": {},
            "sqlite3": {"forwarded": "sqlite3"},
            "@sequelize/sqlite3": {"forwarded": "@sequelize/sqlite3"},
            "odbc-extra": {"forwarded": "odbc-extra"},
        },
    }
