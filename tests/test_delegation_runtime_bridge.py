from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vega.assurance import build_assurance_context, evaluate_assurance_payload
from vega.delegation import (
    DelegationReadinessResult,
    DelegationValidationContext,
    PlanContract,
    evaluate_delegation_payload,
)
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput
from vega.review_runtime import render_review_context, render_review_pack
from vega.runner import RunnerResult


TASK_HASH = "1" * 64
INPUT_HASH = "2" * 64
RUN_ID = "ma2a-run"
SLICE_ID = "S-IMPLEMENT"


class RecordingWorker:
    def __init__(self, *, status: str = "success") -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "sandbox": sandbox,
                "timeout_seconds": timeout_seconds,
                "execution_context": execution_context,
            }
        )
        if execution_context is not None:
            _write_execution_artifact(execution_context.execution_dir)
        return RunnerResult(
            status=self.status,
            output="fake worker completed",
            error=None if self.status == "success" else "fake worker failed",
            command=["fake-worker"],
        )


class TrackedFileWorker(RecordingWorker):
    def __init__(self, relative_path: str) -> None:
        super().__init__()
        self.relative_path = relative_path

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        target = repo_path / self.relative_path
        target.write_text(
            target.read_text(encoding="utf-8") + "worker change\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


class RecordingReviewer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del repo_path, sandbox, timeout_seconds, execution_context
        self.calls.append(prompt)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "review complete",
                    "findings": [],
                    "checked_items": ["scope", "verification"],
                }
            ),
            command=["fake-reviewer"],
        )


class ArtifactProbe:
    def __init__(self, *, status: str = "passed") -> None:
        self.status = status
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Path:
        del args
        self.calls += 1
        artifact_path = Path(kwargs["artifact_path"])
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(kwargs.get("expected_artifact", {}))
        payload.update(
            {
                "schema_version": 1,
                "status": self.status,
            }
        )
        artifact_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return artifact_path


class WorkspaceMutatingProbe(ArtifactProbe):
    def __call__(self, *args: Any, **kwargs: Any) -> Path:
        repo_path = Path(kwargs["repo_path"])
        readme = repo_path / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "probe mutation\n",
            encoding="utf-8",
            newline="\n",
        )
        return super().__call__(*args, **kwargs)


@pytest.mark.parametrize(
    ("readiness_status", "worker_tier"),
    [
        ("human_required", "budget"),
        ("premium_required", "budget"),
    ],
)
def test_non_eligible_or_tier_mismatched_readiness_never_starts_worker(
    tmp_path: Path,
    readiness_status: str,
    worker_tier: str,
) -> None:
    from vega.delegation_runtime import DelegationRuntimeBridge

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir, readiness_status)
    worker = RecordingWorker()
    bridge = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier=worker_tier,
        context_source=source,
        scope_gate=ArtifactProbe(),
        verification_runner=ArtifactProbe(),
    )

    outcome = bridge.run(
        plan=plan,
        slice_id=SLICE_ID,
    )

    assert outcome.status == "blocked"
    assert outcome.readiness_status == readiness_status
    assert worker.calls == []
    assert not run_dir.joinpath("executions", "worker", "execution.json").exists()


@pytest.mark.parametrize("case", ["stale_snapshot", "missing_task_artifact"])
def test_stale_context_or_missing_authoritative_task_blocks_before_worker(
    tmp_path: Path,
    case: str,
) -> None:
    from vega.delegation_runtime import DelegationRuntimeBridge

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir)
    worker = RecordingWorker()
    if case == "stale_snapshot":
        payload = plan.model_dump(mode="json")
        payload["baseline"]["workspace_fingerprint"] = "f" * 64
        plan = PlanContract.model_validate(payload)
        expected_issue = "snapshot_workspace_mismatch"
    else:
        run_dir.joinpath("tasks", "TASK-MA2A.json").unlink()
        expected_issue = "task_artifact_unreadable"
    bridge = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=ArtifactProbe(),
        verification_runner=ArtifactProbe(),
    )

    outcome = bridge.run(
        plan=plan,
        slice_id=SLICE_ID,
    )

    assert outcome.status == "blocked"
    assert expected_issue in outcome.issue_codes
    assert worker.calls == []


def test_budget_eligible_starts_exactly_one_injected_worker(
    tmp_path: Path,
) -> None:
    from vega.delegation_runtime import DelegationRuntimeBridge

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir)
    worker = RecordingWorker()
    scope_gate = ArtifactProbe()
    verification = ArtifactProbe()
    bridge = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=scope_gate,
        verification_runner=verification,
    )

    outcome = bridge.run(
        plan=plan,
        slice_id=SLICE_ID,
    )

    assert outcome.status == "attempt_recorded"
    assert outcome.readiness_status == "budget_eligible"
    assert len(worker.calls) == 1
    assert scope_gate.calls == 1
    assert verification.calls == 1
    assert worker.calls[0]["sandbox"] == "workspace-write"
    assert worker.calls[0]["execution_context"].run_id == RUN_ID


def test_workspace_change_after_control_freeze_blocks_before_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vega.delegation_runtime as runtime

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir)
    worker = RecordingWorker()
    original_capture = runtime._capture_control_artifact_hashes
    changed = False

    def capture_then_change_workspace(paths: tuple[Path, ...]) -> dict[Path, str]:
        nonlocal changed
        result = original_capture(paths)
        if not changed:
            changed = True
            readme = repo / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "concurrent change\n",
                encoding="utf-8",
                newline="\n",
            )
        return result

    monkeypatch.setattr(
        runtime,
        "_capture_control_artifact_hashes",
        capture_then_change_workspace,
    )
    outcome = runtime.DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=ArtifactProbe(),
        verification_runner=ArtifactProbe(),
    ).run(plan=plan, slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert "workspace_changed_before_worker" in outcome.issue_codes
    assert worker.calls == []


def test_run_policy_cannot_relax_project_budget_before_worker(
    tmp_path: Path,
) -> None:
    from vega.delegation_runtime import DelegationRuntimeBridge

    repo = _init_repo(
        tmp_path / "repo",
        budget_max_changed_files=1,
    )
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(
        repo,
        run_dir,
        route_max_changed_files=10,
        plan_max_changed_files=2,
    )
    worker = RecordingWorker()

    outcome = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=ArtifactProbe(),
        verification_runner=ArtifactProbe(),
    ).run(plan=plan, slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert outcome.readiness_status == "premium_required"
    assert "changed_files_exceed_budget_limit" in outcome.issue_codes
    assert worker.calls == []


@pytest.mark.parametrize(
    ("mutating_stage", "expected_issue"),
    [
        ("scope", "workspace_changed_during_scope_probe"),
        ("verification", "workspace_changed_during_verification_probe"),
    ],
)
def test_probe_workspace_mutation_cannot_record_attempt(
    tmp_path: Path,
    mutating_stage: str,
    expected_issue: str,
) -> None:
    from vega.delegation_runtime import DelegationRuntimeBridge

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir)
    worker = RecordingWorker()
    scope_gate = (
        WorkspaceMutatingProbe() if mutating_stage == "scope" else ArtifactProbe()
    )
    verification = (
        WorkspaceMutatingProbe()
        if mutating_stage == "verification"
        else ArtifactProbe()
    )

    outcome = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=scope_gate,
        verification_runner=verification,
    ).run(plan=plan, slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert expected_issue in outcome.issue_codes
    assert len(worker.calls) == 1
    assert outcome.attempt_path is None


def test_plan_and_readiness_artifacts_must_stay_in_run_owned_directory(
    tmp_path: Path,
) -> None:
    from vega.delegation_runtime import DelegationRuntimeBridge

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir)
    outside = tmp_path / "outside-artifacts"
    worker = RecordingWorker()

    with pytest.raises(ValueError, match="run-owned"):
        DelegationRuntimeBridge(
            run_dir=run_dir,
            repo_path=repo,
            artifact_dir=outside,
            worker_runner=worker,
            worker_tier="budget",
            context_source=source,
            scope_gate=ArtifactProbe(),
            verification_runner=ArtifactProbe(),
        ).run(
            plan=plan,
            slice_id=SLICE_ID,
        )

    assert worker.calls == []
    assert not outside.exists()


def test_attempt_hashes_every_authoritative_artifact_and_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    from vega.delegation_runtime import (
        DelegationRuntimeBridge,
        validate_delegation_attempt,
    )

    repo = _init_repo(tmp_path / "repo")
    run_dir = tmp_path / "workspace" / "runs" / RUN_ID
    source, plan = _runtime_contract(repo, run_dir)
    bridge = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=RecordingWorker(),
        worker_tier="budget",
        context_source=source,
        scope_gate=ArtifactProbe(),
        verification_runner=ArtifactProbe(),
    )
    outcome = bridge.run(
        plan=plan,
        slice_id=SLICE_ID,
    )
    assert outcome.status == "attempt_recorded"
    attempt = _read_json(outcome.attempt_path)

    reference_fields = {
        "plan_ref",
        "context_ref",
        "readiness_ref",
        "prompt_ref",
        "execution_ref",
        "snapshot_before_ref",
        "snapshot_after_ref",
        "scope_ref",
        "verification_ref",
    }
    assert reference_fields.issubset(attempt)
    for field in reference_fields:
        reference = attempt[field]
        artifact_path = run_dir / reference["relative_path"]
        assert reference["sha256"] == _sha256_file(artifact_path)

    execution_path = run_dir / attempt["execution_ref"]["relative_path"]
    execution_path.write_text(
        execution_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    validation = validate_delegation_attempt(
        outcome.attempt_path,
        run_dir=run_dir,
    )

    assert validation.status == "human_required"
    assert "execution_sha256_mismatch" in validation.issue_codes


def test_out_of_scope_diff_cannot_become_success(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = _init_repo(
        tmp_path / "repo",
        scope_allowed_paths=["README.md"],
        verification_command="python -c \"print('verification passed')\"",
    )
    worker = TrackedFileWorker("OUTSIDE.md")
    reviewer = RecordingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)
    state = _read_json(run_dir / "state.json")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "scope_gate_failed"
    assert reviewer.calls == []


def test_verification_failure_cannot_become_success(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = _init_repo(
        tmp_path / "repo",
        scope_allowed_paths=["README.md"],
        verification_command='python -c "raise SystemExit(1)"',
    )
    reviewer = RecordingReviewer()

    run_dir = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedFileWorker("README.md"),
        reviewer_runner=reviewer,
    ).start(_brief(repo), "auto", max_iterations=1, verify=True)
    state = _read_json(run_dir / "state.json")

    assert state["iterations"][0]["verification_status"] == "failed"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "verification_failed"


def test_review_input_exposes_controlled_delegation_summary_without_worker_chat() -> None:
    marker = "PRIVATE_WORKER_CHAT_MUST_NOT_APPEAR"
    summary = {
        "plan_id": "PLAN-MA2A",
        "slice_id": SLICE_ID,
        "readiness_status": "budget_eligible",
        "attempt_sha256": "a" * 64,
        "execution_sha256": "b" * 64,
    }
    inputs = _review_inputs(summary=summary, worker_chat=marker)

    context = render_review_context(inputs)
    pack = render_review_pack(inputs)

    assert context["delegation_summary"] == summary
    assert context["contains_worker_chat"] is False
    assert summary["attempt_sha256"] in pack
    assert marker not in json.dumps(context, ensure_ascii=False)
    assert marker not in pack


def test_assurance_cannot_treat_readiness_as_verification_evidence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    iteration_dir = workspace / "runs" / RUN_ID / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    readiness_path = iteration_dir / "delegation-readiness.json"
    readiness_path.write_text(
        _readiness("budget_eligible").model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    snapshot = _assurance_snapshot()
    evidence = _assurance_evidence(
        relative_path="iterations/01/delegation-readiness.json",
        artifact_hash=_sha256_file(readiness_path),
        snapshot=snapshot,
    )
    claims = [_assurance_claim()]
    threats = [_assurance_threat()]
    context = build_assurance_context(
        run_id=RUN_ID,
        iteration=1,
        snapshot=snapshot,
        claims=claims,
        threats=threats,
        evidence_contracts=[evidence],
    )

    result = evaluate_assurance_payload(
        {
            "schema_version": 1,
            "run_id": RUN_ID,
            "iteration": 1,
            "snapshot": snapshot,
            "verification_conclusion": "verified",
            "claims": claims,
            "threats": threats,
            "evidence": [evidence],
        },
        workspace=workspace,
        expected=context,
    )

    assert result.status == "insufficient"
    assert result.merge_evidence_sufficient is False
    assert result.verification_conclusion == "unknown"
    assert "assurance_verification_conclusion_mismatch:unknown" in result.issues


def _runtime_contract(
    repo: Path,
    run_dir: Path,
    readiness_status: str = "budget_eligible",
    *,
    route_max_changed_files: int = 1,
    plan_max_changed_files: int = 1,
) -> tuple[Any, PlanContract]:
    from vega.delegation_runtime import (
        DelegationContextSource,
        compile_delegation_context,
    )

    _prepare_runtime_sources(
        run_dir,
        route_max_changed_files=route_max_changed_files,
    )
    source = DelegationContextSource.model_validate(
        {
            "schema_version": 1,
            "task_artifact_path": "tasks/TASK-MA2A.json",
            "delegation_policy_path": "policies/delegation-policy.json",
            "input_artifact_paths": ["inputs/design.json"],
        }
    )
    compiled = compile_delegation_context(
        run_dir=run_dir,
        repo_path=repo,
        source=source,
    )
    payload = _plan_payload()
    context = compiled.validation_context
    payload["task_id"] = context.task_id
    payload["task_ref"] = context.task_ref.model_dump(mode="json")
    payload["baseline"] = context.baseline.model_dump(mode="json")
    payload["task_dag"][0]["input_artifact_refs"] = [
        context.available_artifacts[0].model_dump(mode="json")
    ]
    payload["budget"]["max_changed_files"] = plan_max_changed_files
    if readiness_status == "human_required":
        payload["risk"]["human_required"] = True
    elif readiness_status == "premium_required":
        payload["risk"]["premium_worker_required"] = True
    return source, PlanContract.model_validate(payload)


def _readiness(status: str) -> DelegationReadinessResult:
    payload = _plan_payload()
    if status == "human_required":
        payload["risk"]["human_required"] = True
    elif status == "premium_required":
        payload["risk"]["premium_worker_required"] = True
    result = evaluate_delegation_payload(payload, expected=_delegation_context())
    assert result.status == status
    return result


def _plan_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plan_id": "PLAN-MA2A",
        "plan_revision": 1,
        "parent_plan_ref": None,
        "change_reason_code": None,
        "change_summary": None,
        "invalidated_slice_ids": [],
        "task_id": "TASK-MA2A",
        "task_ref": _artifact("tasks/TASK-MA2A.md", TASK_HASH),
        "baseline": _delegation_snapshot(),
        "goal": {
            "acceptance_facts": [
                {
                    "fact_id": "A-RUNTIME",
                    "statement": "只有通过 readiness 的单 slice 才能启动 worker。",
                }
            ],
            "non_goals": ["不改变现有成功语义。"],
        },
        "task_dag": [
            {
                "slice_id": SLICE_ID,
                "read_paths": ["README.md", "tests/test_example.py"],
                "allowed_write_paths": ["README.md"],
                "dependencies": [],
                "preconditions": ["当前计划和策略快照已经冻结。"],
                "expected_change": "完成单 slice 修改并保留可验证证据链。",
                "acceptance_refs": ["A-RUNTIME"],
                "input_artifact_refs": [_artifact("inputs/design.json", INPUT_HASH)],
                "verification": {
                    "commands": ["python -m pytest tests/test_example.py"],
                    "oracle": {"kind": "all_commands_exit_zero"},
                },
                "failure_and_recovery": "任一证据失配时停止并交还人工。",
            }
        ],
        "decisions": {
            "resolved": ["MA-2A 只运行一个预冻结 slice。"],
            "unresolved": [],
        },
        "risk": {
            "threat_refs": [],
            "human_required": False,
            "premium_worker_required": False,
        },
        "budget": {
            "max_changed_files": 1,
            "max_diff_lines": 100,
            "max_new_files": 0,
            "context_limit_tokens": 10_000,
            "worker_time_limit_seconds": 60,
            "worker_token_limit": 5_000,
        },
    }


def _delegation_context(
    *,
    workspace_fingerprint: str = "b" * 64,
) -> DelegationValidationContext:
    snapshot = _delegation_snapshot()
    snapshot["workspace_fingerprint"] = workspace_fingerprint
    return DelegationValidationContext.model_validate(
        {
            "schema_version": 1,
            "task_id": "TASK-MA2A",
            "task_ref": _artifact("tasks/TASK-MA2A.md", TASK_HASH),
            "baseline": snapshot,
            "allowed_read_paths": ["README.md", "tests/test_example.py"],
            "allowed_write_paths": ["README.md"],
            "allowed_verification_commands": ["python -m pytest tests/test_example.py"],
            "available_artifacts": [_artifact("inputs/design.json", INPUT_HASH)],
            "budget_limits": {
                "max_slices": 1,
                "max_dependency_edges": 0,
                "max_write_paths": 1,
                "max_changed_files": 1,
                "max_diff_lines": 100,
                "max_new_files": 0,
                "max_context_tokens": 10_000,
                "max_worker_time_seconds": 60,
                "max_worker_tokens": 5_000,
            },
        }
    )


def _delegation_snapshot() -> dict[str, str]:
    return {
        "head_sha": "a" * 40,
        "workspace_fingerprint": "b" * 64,
        "project_policy_sha256": "c" * 64,
        "scope_policy_sha256": "d" * 64,
    }


def _artifact(relative_path: str, sha256: str) -> dict[str, str]:
    return {
        "relative_path": relative_path,
        "sha256": sha256,
    }


def _prepare_runtime_sources(
    run_dir: Path,
    *,
    route_max_changed_files: int = 1,
) -> None:
    run_dir.joinpath("tasks").mkdir(parents=True)
    run_dir.joinpath("policies").mkdir()
    run_dir.joinpath("inputs").mkdir()
    run_dir.joinpath("tasks", "TASK-MA2A.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "TASK-MA2A",
                "summary": "执行 MA-2A 单 Slice。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    run_dir.joinpath("policies", "delegation-policy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "budget_limits": {
                    "max_slices": 1,
                    "max_dependency_edges": 0,
                    "max_write_paths": 1,
                    "max_changed_files": route_max_changed_files,
                    "max_diff_lines": 100,
                    "max_new_files": 0,
                    "max_context_tokens": 10_000,
                    "max_worker_time_seconds": 60,
                    "max_worker_tokens": 5_000,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    run_dir.joinpath("inputs", "design.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "decision": "只运行一个 Slice。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _init_repo(
    repo: Path,
    *,
    scope_allowed_paths: list[str] | None = None,
    verification_command: str | None = None,
    budget_max_changed_files: int | None = None,
) -> Path:
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- 修改后运行测试。\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("OUTSIDE.md").write_text(
        "# Outside\n",
        encoding="utf-8",
        newline="\n",
    )
    effective_scope = scope_allowed_paths or ["README.md", "tests/test_example.py"]
    effective_verification = (
        verification_command
        if verification_command is not None
        else "python -m pytest tests/test_example.py"
    )
    config_lines = [
        "version: 1",
        "scope:",
        "  allowed_paths:",
        *[f"    - {path}" for path in effective_scope],
        "verification:",
        "  commands:",
        f"    - {effective_verification}",
        "  max_commands: 1",
    ]
    if budget_max_changed_files is not None:
        config_lines.extend(
            [
                "budget:",
                f"  max_changed_files: {budget_max_changed_files}",
            ]
        )
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(config_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".")
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return repo


def _brief(repo: Path) -> BriefInput:
    return BriefInput(
        mode="feature",
        text="执行 MA-2A 单 slice。",
        source="ma2a-red-test",
        repo_path=str(repo),
    )


def _write_execution_artifact(execution_dir: Path) -> None:
    execution_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    execution_dir.joinpath("execution.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "execution_id": "execution-ma2a",
                "step": "worker",
                "iteration": 1,
                "owner_pid": os.getpid(),
                "child_pid": None,
                "termination_unconfirmed": False,
                "command": ["fake-worker"],
                "started_at": now,
                "last_heartbeat": now,
                "lease_expires_at": now,
                "deadline": now,
                "status": "completed",
                "reason": None,
                "returncode": 0,
                "finished_at": now,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _review_inputs(
    *,
    summary: dict[str, Any],
    worker_chat: str,
) -> dict[str, Any]:
    return {
        "repo_path": "repo",
        "repo_name": "repo",
        "source_run": "reflect-run",
        "source_run_dir": "runs/reflect-run",
        "source_brief": "实现 MA-2A runtime bridge。",
        "reflection": "worker 已完成单 slice。",
        "diff_summary": "仅修改允许路径。",
        "test_summary": "验证通过。",
        "changed_files": ["README.md"],
        "full_diff": "diff --git a/README.md b/README.md",
        "project_context": "项目上下文。",
        "truncated_sections": [],
        "evidence_issues": [],
        "evidence_diagnostics": [],
        "evidence_consistent": True,
        "source_snapshot_id": "snapshot-1",
        "source_workspace_fingerprint": "a" * 64,
        "current_workspace_fingerprint": "a" * 64,
        "current_index_flags_sha256": "b" * 64,
        "current_unsafe_index_paths": [],
        "source_untracked_content_complete": True,
        "current_untracked_content_complete": True,
        "reviewer_start_workspace_fingerprint": "",
        "reviewer_end_workspace_fingerprint": "",
        "workspace_changed_during_review": False,
        "review_execution_issues": [],
        "reviewer_prompt_max_chars": 100_000,
        "memory_hit_count": 0,
        "agents_files": ["AGENTS.md"],
        "risk_gate": None,
        "delegation_summary": summary,
        "worker_chat": worker_chat,
    }


def _assurance_snapshot() -> dict[str, str]:
    return {
        "head_sha": "a" * 40,
        "staged_diff_sha256": "0" * 64,
        "unstaged_diff_sha256": "1" * 64,
        "review_snapshot_id": "2" * 64,
        "project_policy_snapshot_sha256": "3" * 64,
        "scope_policy_sha256": "4" * 64,
    }


def _assurance_claim() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "C-MA2A",
        "statement": "只有结构化 verification 才能证明执行成功。",
        "status": "accepted",
        "source": {
            "kind": "project_contract",
            "reference": "policy://success-semantics",
        },
    }


def _assurance_threat() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "T-MA2A",
        "category": "evidence_substitution",
        "source": {
            "kind": "deterministic_detector",
            "reference": "detector://delegation-evidence/v1",
        },
        "status": "active",
        "trigger": "readiness 被误当作 verification evidence。",
        "affected_assets": ["success_status"],
        "claim_refs": ["C-MA2A"],
        "invariant": "readiness 只能授权启动，不能证明执行结果。",
        "failure_mode": "readiness_substitutes_verification",
        "impact": "high",
        "exposure": "medium",
        "blast_radius": "per_run",
        "reversibility": "medium",
        "detectability": "immediate",
        "uncertainty": "low",
        "trigger_evidence": ["policy://success-semantics"],
        "required_evidence": ["delegation_readiness"],
        "evidence_refs": ["E-MA2A"],
        "residual_risks": [],
        "human_required": False,
    }


def _assurance_evidence(
    *,
    relative_path: str,
    artifact_hash: str,
    snapshot: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "E-MA2A",
        "kind": "delegation_readiness",
        "producer": {
            "runner": "vega",
            "version": "ma2a",
        },
        "command": "evaluate delegation readiness",
        "environment": {
            "mode": "deterministic",
        },
        "run_id": RUN_ID,
        "iteration": 1,
        "snapshot": snapshot,
        "input": {
            "plan_id": "PLAN-MA2A",
        },
        "oracle": {
            "statement": "readiness 仅证明允许启动 worker。",
        },
        "result": {
            "status": "passed",
            "exit_code": 0,
            "duration_seconds": 0.01,
        },
        "covers": ["T-MA2A"],
        "artifacts": [
            {
                "artifact_type": "verification_result",
                "run_id": RUN_ID,
                "relative_path": relative_path,
                "sha256": artifact_hash,
            }
        ],
        "limitations": ["该 artifact 不包含 verification 执行结果。"],
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
