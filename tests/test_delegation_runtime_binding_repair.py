from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vega.delegation import PlanContract
from vega.review_runtime import render_review_context
from vega.runner import RunnerResult


RUN_ID = "ma2ar-run"
SECOND_RUN_ID = "ma2ar-run-second"
SLICE_ID = "S-IMPLEMENT"


class RecordingWorker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: Any = None,
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
        _write_execution_artifact(execution_context)
        return RunnerResult(
            status="success",
            output="fake worker completed",
            command=["fake-worker"],
        )


class ControlPlaneTamperWorker(RecordingWorker):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: Any = None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        run_dir = execution_context.execution_dir.parent.parent
        plan_path = run_dir / "delegation" / "delegation-plan.json"
        payload = _read_json(plan_path)
        payload["goal"]["non_goals"] = ["Worker 已篡改控制面。"]
        _write_json(plan_path, payload)
        return result


class StageNewFileWorker(RecordingWorker):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: Any = None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        repo_path.joinpath("NEW.md").write_text(
            "new file\n",
            encoding="utf-8",
            newline="\n",
        )
        _git(repo_path, "add", "NEW.md")
        return result


class BoundProbe:
    def __init__(self, *, status: str = "passed") -> None:
        self.status = status
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Path:
        del args
        self.calls += 1
        artifact_path = Path(kwargs["artifact_path"])
        payload = dict(kwargs["expected_artifact"])
        payload["status"] = self.status
        _write_json(artifact_path, payload)
        return artifact_path


class StatusOnlyProbe:
    def __call__(self, *args: Any, **kwargs: Any) -> Path:
        del args
        artifact_path = Path(kwargs["artifact_path"])
        _write_json(
            artifact_path,
            {
                "schema_version": 1,
                "status": "passed",
            },
        )
        return artifact_path


def test_live_compiled_context_records_bound_attempt_and_prompt(
    tmp_path: Path,
) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    first_run = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    second_run = _prepare_run_sources(tmp_path / "workspace", SECOND_RUN_ID)
    first_source = _context_source(runtime, first_run)
    second_source = _context_source(runtime, second_run)
    compiled = runtime["compile"](
        run_dir=first_run,
        repo_path=repo,
        source=first_source,
    )
    plan = _plan(compiled)
    first_worker = RecordingWorker()
    second_worker = RecordingWorker()

    first_outcome = _bridge(
        runtime,
        run_dir=first_run,
        repo=repo,
        source=first_source,
        worker=first_worker,
    ).run(plan=plan, slice_id=SLICE_ID)
    second_outcome = _bridge(
        runtime,
        run_dir=second_run,
        repo=repo,
        source=second_source,
        worker=second_worker,
    ).run(plan=plan, slice_id=SLICE_ID)

    assert first_outcome.status == "attempt_recorded"
    assert second_outcome.status == "attempt_recorded"
    assert len(first_worker.calls) == 1
    assert len(second_worker.calls) == 1
    assert first_worker.calls[0]["prompt"] == second_worker.calls[0]["prompt"]
    first_attempt = _read_json(first_outcome.attempt_path)
    assert "context_ref" in first_attempt
    assert "prompt_ref" in first_attempt
    first_prompt = first_run / first_attempt["prompt_ref"]["relative_path"]
    second_attempt = _read_json(second_outcome.attempt_path)
    second_prompt = second_run / second_attempt["prompt_ref"]["relative_path"]
    assert first_prompt.read_bytes() == second_prompt.read_bytes()


def test_stale_live_workspace_blocks_before_worker(tmp_path: Path) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    source = _context_source(runtime, run_dir)
    compiled = runtime["compile"](run_dir=run_dir, repo_path=repo, source=source)
    plan = _plan(compiled)
    repo.joinpath("README.md").write_text(
        "# Demo\nstale change\n",
        encoding="utf-8",
        newline="\n",
    )
    worker = RecordingWorker()

    outcome = _bridge(
        runtime,
        run_dir=run_dir,
        repo=repo,
        source=source,
        worker=worker,
    ).run(plan=plan, slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert worker.calls == []
    assert {
        "snapshot_workspace_mismatch",
        "workspace_not_clean_before_worker",
    }.intersection(outcome.issue_codes)


def test_missing_authoritative_task_blocks_before_worker(tmp_path: Path) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    source = _context_source(runtime, run_dir)
    compiled = runtime["compile"](run_dir=run_dir, repo_path=repo, source=source)
    plan = _plan(compiled)
    run_dir.joinpath("tasks", "TASK-MA2AR.json").unlink()
    worker = RecordingWorker()

    outcome = _bridge(
        runtime,
        run_dir=run_dir,
        repo=repo,
        source=source,
        worker=worker,
    ).run(plan=plan, slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert "task_artifact_unreadable" in outcome.issue_codes
    assert worker.calls == []


def test_status_only_probe_cannot_record_attempt(tmp_path: Path) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    source = _context_source(runtime, run_dir)
    compiled = runtime["compile"](run_dir=run_dir, repo_path=repo, source=source)
    worker = RecordingWorker()

    outcome = runtime["bridge"](
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=BoundProbe(),
        verification_runner=StatusOnlyProbe(),
    ).run(plan=_plan(compiled), slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert "verification_probe_failed" in outcome.issue_codes
    assert len(worker.calls) == 1
    assert outcome.attempt_path is None


def test_worker_control_plane_tamper_blocks_attempt(tmp_path: Path) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    source = _context_source(runtime, run_dir)
    compiled = runtime["compile"](run_dir=run_dir, repo_path=repo, source=source)

    outcome = _bridge(
        runtime,
        run_dir=run_dir,
        repo=repo,
        source=source,
        worker=ControlPlaneTamperWorker(),
    ).run(plan=_plan(compiled), slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert "control_artifact_changed:delegation-plan.json" in outcome.issue_codes
    assert outcome.attempt_path is None


def test_staged_new_file_counts_against_budget(tmp_path: Path) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    source = _context_source(runtime, run_dir)
    compiled = runtime["compile"](run_dir=run_dir, repo_path=repo, source=source)

    outcome = _bridge(
        runtime,
        run_dir=run_dir,
        repo=repo,
        source=source,
        worker=StageNewFileWorker(),
    ).run(
        plan=_plan(compiled, write_path="NEW.md", max_new_files=0),
        slice_id=SLICE_ID,
    )

    assert outcome.status == "blocked"
    assert "new_files_exceed_plan_budget" in outcome.issue_codes
    assert outcome.attempt_path is None


def test_attempt_validator_cross_checks_readiness_plan_hash(
    tmp_path: Path,
) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(tmp_path / "workspace", RUN_ID)
    source = _context_source(runtime, run_dir)
    compiled = runtime["compile"](run_dir=run_dir, repo_path=repo, source=source)
    outcome = _bridge(
        runtime,
        run_dir=run_dir,
        repo=repo,
        source=source,
        worker=RecordingWorker(),
    ).run(plan=_plan(compiled), slice_id=SLICE_ID)
    assert outcome.status == "attempt_recorded"
    attempt = _read_json(outcome.attempt_path)
    readiness_path = run_dir / attempt["readiness_ref"]["relative_path"]
    readiness = _read_json(readiness_path)
    readiness["plan_sha256"] = "f" * 64
    _write_json(readiness_path, readiness)
    attempt["readiness_ref"]["sha256"] = _sha256_file(readiness_path)
    _write_json(outcome.attempt_path, attempt)

    validation = runtime["validate_attempt"](
        outcome.attempt_path,
        run_dir=run_dir,
    )

    assert validation.status == "human_required"
    assert "readiness_plan_sha256_mismatch" in validation.issue_codes


def test_partial_delegation_summary_is_all_or_nothing() -> None:
    context = render_review_context(
        _review_inputs(
            summary={
                "plan_id": "PLAN-MA2AR",
            }
        )
    )

    assert context["delegation_summary"] == {}


def test_run_dir_overlapping_repo_blocks_before_worker(tmp_path: Path) -> None:
    runtime = _runtime_api()
    repo = _init_repo(tmp_path / "repo")
    run_dir = _prepare_run_sources(repo, RUN_ID)
    source = _context_source(runtime, run_dir)
    worker = RecordingWorker()

    outcome = _bridge(
        runtime,
        run_dir=run_dir,
        repo=repo,
        source=source,
        worker=worker,
    ).run(plan=_placeholder_plan(), slice_id=SLICE_ID)

    assert outcome.status == "blocked"
    assert "run_dir_overlaps_repo" in outcome.issue_codes
    assert worker.calls == []


def _runtime_api() -> dict[str, Any]:
    from vega.delegation_runtime import (
        DelegationContextSource,
        DelegationRuntimeBridge,
        compile_delegation_context,
        validate_delegation_attempt,
    )

    return {
        "source": DelegationContextSource,
        "bridge": DelegationRuntimeBridge,
        "compile": compile_delegation_context,
        "validate_attempt": validate_delegation_attempt,
    }


def _bridge(
    runtime: dict[str, Any],
    *,
    run_dir: Path,
    repo: Path,
    source: Any,
    worker: Any,
) -> Any:
    return runtime["bridge"](
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        context_source=source,
        scope_gate=BoundProbe(),
        verification_runner=BoundProbe(),
    )


def _context_source(runtime: dict[str, Any], run_dir: Path) -> Any:
    del run_dir
    return runtime["source"].model_validate(
        {
            "schema_version": 1,
            "task_artifact_path": "tasks/TASK-MA2AR.json",
            "delegation_policy_path": "policies/delegation-policy.json",
            "input_artifact_paths": ["inputs/design.json"],
        }
    )


def _plan(
    compiled: Any,
    *,
    write_path: str = "README.md",
    max_new_files: int = 0,
) -> PlanContract:
    context = compiled.validation_context
    return PlanContract.model_validate(
        {
            "schema_version": 1,
            "plan_id": "PLAN-MA2AR",
            "plan_revision": 1,
            "parent_plan_ref": None,
            "change_reason_code": None,
            "change_summary": None,
            "invalidated_slice_ids": [],
            "task_id": context.task_id,
            "task_ref": context.task_ref.model_dump(mode="json"),
            "baseline": context.baseline.model_dump(mode="json"),
            "goal": {
                "acceptance_facts": [
                    {
                        "fact_id": "A-RUNTIME",
                        "statement": "单 Slice 只能消费实时编译并受哈希绑定的事实。",
                    }
                ],
                "non_goals": ["不调用真实 Provider。"],
            },
            "task_dag": [
                {
                    "slice_id": SLICE_ID,
                    "read_paths": ["README.md", "tests/test_example.py"],
                    "allowed_write_paths": [write_path],
                    "dependencies": [],
                    "preconditions": ["当前工作区、task 与策略已经冻结。"],
                    "expected_change": "执行冻结的单 Slice 并形成绑定证据。",
                    "acceptance_refs": ["A-RUNTIME"],
                    "input_artifact_refs": [
                        context.available_artifacts[0].model_dump(mode="json")
                    ],
                    "verification": {
                        "commands": ["python -m pytest tests/test_example.py"],
                        "oracle": {"kind": "all_commands_exit_zero"},
                    },
                    "failure_and_recovery": "任一事实失配时停止并交还人工。",
                }
            ],
            "decisions": {
                "resolved": ["MA-2A-R 只运行一个预冻结 Slice。"],
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
                "max_new_files": max_new_files,
                "context_limit_tokens": 10_000,
                "worker_time_limit_seconds": 60,
                "worker_token_limit": 5_000,
            },
        }
    )


def _placeholder_plan() -> PlanContract:
    return PlanContract.model_validate(
        {
            "schema_version": 1,
            "plan_id": "PLAN-MA2AR",
            "plan_revision": 1,
            "parent_plan_ref": None,
            "change_reason_code": None,
            "change_summary": None,
            "invalidated_slice_ids": [],
            "task_id": "TASK-MA2AR",
            "task_ref": _artifact("tasks/TASK-MA2AR.json", "1" * 64),
            "baseline": {
                "head_sha": "a" * 40,
                "workspace_fingerprint": "b" * 64,
                "project_policy_sha256": "c" * 64,
                "scope_policy_sha256": "d" * 64,
            },
            "goal": {
                "acceptance_facts": [
                    {
                        "fact_id": "A-RUNTIME",
                        "statement": "重叠控制目录必须阻断。",
                    }
                ],
                "non_goals": [],
            },
            "task_dag": [
                {
                    "slice_id": SLICE_ID,
                    "read_paths": ["README.md"],
                    "allowed_write_paths": ["README.md"],
                    "dependencies": [],
                    "preconditions": [],
                    "expected_change": "不应执行。",
                    "acceptance_refs": ["A-RUNTIME"],
                    "input_artifact_refs": [],
                    "verification": {
                        "commands": ["python -m pytest tests/test_example.py"],
                        "oracle": {"kind": "all_commands_exit_zero"},
                    },
                    "failure_and_recovery": "阻断。",
                }
            ],
            "decisions": {"resolved": [], "unresolved": []},
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
    )


def _prepare_run_sources(workspace: Path, run_id: str) -> Path:
    run_dir = workspace / "runs" / run_id
    run_dir.joinpath("tasks").mkdir(parents=True)
    run_dir.joinpath("policies").mkdir()
    run_dir.joinpath("inputs").mkdir()
    _write_json(
        run_dir / "tasks" / "TASK-MA2AR.json",
        {
            "schema_version": 1,
            "task_id": "TASK-MA2AR",
            "summary": "验证单 Slice 事实绑定。",
        },
    )
    _write_json(
        run_dir / "policies" / "delegation-policy.json",
        {
            "schema_version": 1,
            "budget_limits": {
                "max_slices": 1,
                "max_dependency_edges": 0,
                "max_write_paths": 1,
                "max_changed_files": 1,
                "max_diff_lines": 100,
                "max_new_files": 1,
                "max_context_tokens": 10_000,
                "max_worker_time_seconds": 60,
                "max_worker_tokens": 5_000,
            },
        },
    )
    _write_json(
        run_dir / "inputs" / "design.json",
        {
            "schema_version": 1,
            "decision": "只允许一个 Slice。",
        },
    )
    return run_dir


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    repo.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("tests").mkdir()
    repo.joinpath("tests", "test_example.py").write_text(
        "def test_example() -> None:\n    assert True\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "scope:",
                "  allowed_paths:",
                "    - README.md",
                "    - NEW.md",
                "    - tests/test_example.py",
                "verification:",
                "  commands:",
                "    - python -m pytest tests/test_example.py",
                "  max_commands: 1",
                "budget:",
                "  max_changed_files: 1",
                "  max_diff_lines: 100",
                "  max_new_files: 1",
            ]
        )
        + "\n",
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


def _write_execution_artifact(execution_context: Any) -> None:
    execution_context.execution_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    _write_json(
        execution_context.execution_dir / "execution.json",
        {
            "run_id": execution_context.run_id,
            "execution_id": "execution-ma2ar",
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
    )


def _review_inputs(*, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_path": "repo",
        "repo_name": "repo",
        "source_run": "reflect-run",
        "source_run_dir": "runs/reflect-run",
        "source_brief": "执行 MA-2A-R。",
        "reflection": "完成。",
        "diff_summary": "摘要。",
        "test_summary": "测试。",
        "changed_files": ["README.md"],
        "full_diff": "diff --git a/README.md b/README.md",
        "project_context": "上下文。",
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
    }


def _artifact(relative_path: str, sha256: str) -> dict[str, str]:
    return {
        "relative_path": relative_path,
        "sha256": sha256,
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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
