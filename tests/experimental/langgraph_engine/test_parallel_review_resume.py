from __future__ import annotations

import hashlib
import inspect
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from vega.execution_control import (
    ExecutionLease,
    RunnerExecutionContext,
    request_stop_for_active_executions,
    run_owned_process,
)
from vega.loop_step_result import hash_command
from vega.models import LoopAutomationState
from vega.parallel_review import (
    ParallelReviewAggregationContext,
    ParallelReviewRoutingContext,
    ReviewEvidenceSnapshot,
    ReviewerRole,
    build_parallel_review_plan,
)
from vega.parallel_review_artifacts import (
    build_review_evidence_snapshot_from_artifacts,
    list_parallel_review_result_refs,
    parallel_review_public_evidence_artifact_ref,
    parallel_review_result_pointer_artifact_ref,
    parallel_review_role_prompt_artifact_ref,
    read_parallel_review_result,
    write_parallel_review_plan,
)
from vega.parallel_review_runtime import (
    ParallelReviewAttemptActiveError,
    ParallelReviewRuntimeValidationError,
    PreparedParallelReviewEvidence,
    RunnerParallelReviewerExecutor,
    prepare_parallel_review_evidence,
)
from vega.runner import RunnerResult
from vega.workspace_check import capture_review_workspace


LANGGRAPH_AVAILABLE = importlib.util.find_spec("langgraph") is not None
LANGGRAPH_SQLITE_AVAILABLE = (
    LANGGRAPH_AVAILABLE
    and importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None
)
requires_langgraph = pytest.mark.skipif(
    not LANGGRAPH_SQLITE_AVAILABLE,
    reason="需要安装项目 langgraph 与 SQLite checkpoint 可选依赖",
)
if LANGGRAPH_SQLITE_AVAILABLE:
    from langgraph.checkpoint.sqlite import SqliteSaver

    from vega.parallel_review_graph import (
        DeterministicFakeReviewer,
        ParallelReviewGraphValidationError,
        ParallelReviewerExecutor,
        execute_parallel_review_graph,
    )


PRIVATE_CANARIES: dict[ReviewerRole, str] = {
    "correctness_reviewer": "CORRECTNESS_PROCESS_CANARY_GATE5",
    "verification_adequacy_reviewer": "VERIFICATION_PROCESS_CANARY_GATE5",
    "security_design_reviewer": "SECURITY_PROCESS_CANARY_GATE5",
}
PUBLIC_EVIDENCE = (
    "# Gate 5 公共审查证据\n\n"
    "- verification: passed\n"
    "- risk: low\n"
    "- changed files: src/example.py, tests/test_example.py\n"
)


def _create_run(
    tmp_path: Path,
    *,
    run_id: str,
) -> tuple[Path, Path, ReviewEvidenceSnapshot]:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo_path.joinpath("example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "init"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "gate5@example.invalid"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Gate 5 Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", "example.py"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )

    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        engine="langgraph",
        repo_path=str(repo_path),
        input_source="test",
        status="running",
        current_step="review",
        current_iteration=1,
    ).save(run_dir / "state.json")
    artifacts = {
        "loop-plan.md": "# Gate 5 resume fixture\n",
        "project-policy-snapshot.json": (
            '{"reviewer_policy":"read-only","version":1}\n'
        ),
        "iterations/01/verification-result.json": (
            '{"status":"passed","failed_count":0}\n'
        ),
        "iterations/01/risk-gate-result.json": '{"risk":"low"}\n',
        "iterations/01/acceptance-evidence.json": (
            '{"items":["pytest passed"]}\n'
        ),
    }
    for ref, content in artifacts.items():
        path = run_dir.joinpath(*ref.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    snapshot = build_review_evidence_snapshot_from_artifacts(
        run_dir,
        iteration=1,
        workspace_fingerprint=(
            "sha256:" + capture_review_workspace(repo_path).fingerprint
        ),
        policy_snapshot_ref="project-policy-snapshot.json",
        verification_result_ref="iterations/01/verification-result.json",
        risk_result_ref="iterations/01/risk-gate-result.json",
        acceptance_evidence_manifest_ref=(
            "iterations/01/acceptance-evidence.json"
        ),
    )
    return run_dir, repo_path, snapshot


def _context(
    run_dir: Path,
    snapshot: ReviewEvidenceSnapshot,
    *,
    topology: str,
) -> ParallelReviewAggregationContext:
    routing = ParallelReviewRoutingContext.model_validate(
        {
            "run_id": run_dir.name,
            "iteration": 1,
            "evidence_snapshot_sha256": (
                snapshot.evidence_snapshot_sha256
            ),
            "verification_status": "passed",
            "verification_failed_count": 0,
            "risk": "low",
            "changed_files": [
                "src/example.py",
                "tests/test_example.py",
            ],
            "gate_reason_codes": [],
        }
    )
    plan = build_parallel_review_plan(
        routing,
        topology=topology,  # type: ignore[arg-type]
    )
    return ParallelReviewAggregationContext(
        run_id=run_dir.name,
        iteration=1,
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        review_plan=plan,
        verification_status="passed",
        verification_failed_count=0,
        risk="low",
        human_approval_valid=True,
    )


def _prepare_evidence(
    snapshot: ReviewEvidenceSnapshot,
) -> PreparedParallelReviewEvidence:
    prepared = prepare_parallel_review_evidence(
        snapshot,
        PUBLIC_EVIDENCE,
        forbidden_markers=tuple(PRIVATE_CANARIES.values()),
    )
    assert isinstance(prepared, PreparedParallelReviewEvidence)
    return prepared


def _runner_executor(
    *,
    runner: object,
    prepared: PreparedParallelReviewEvidence,
    repo_path: Path,
    reviewer_role: ReviewerRole,
    timeout_seconds: int = 10,
) -> RunnerParallelReviewerExecutor:
    executor = _invoke_supported(
        RunnerParallelReviewerExecutor,
        {
            "runner": runner,
            "prepared": prepared,
            "prepared_evidence": prepared,
            "evidence": prepared,
            "repo_path": repo_path,
            "reviewer_role": reviewer_role,
            "role": reviewer_role,
            "timeout_seconds": timeout_seconds,
            "private_canary": PRIVATE_CANARIES[reviewer_role],
        },
    )
    assert isinstance(executor, RunnerParallelReviewerExecutor)
    return executor


def _invoke_supported(
    target: Callable[..., Any],
    candidates: Mapping[str, object],
) -> Any:
    signature = inspect.signature(target)
    positional: list[object] = []
    keyword: dict[str, object] = {}
    missing: list[str] = []
    for name, parameter in signature.parameters.items():
        if name in {"self", "cls"}:
            continue
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if name not in candidates:
            if parameter.default is inspect.Parameter.empty:
                missing.append(name)
            continue
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            positional.append(candidates[name])
        else:
            keyword[name] = candidates[name]
    assert not missing, (
        f"{target.__name__} 出现测试未识别的必需参数：{missing}；"
        "请把 Gate 5 公共接口参数加入测试 fixture。"
    )
    return target(*positional, **keyword)


class _CountingJsonRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[ReviewerRole, RunnerExecutionContext]] = []
        self._lock = threading.Lock()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        assert sandbox == "read-only"
        assert repo_path.is_dir()
        assert prompt.strip()
        assert execution_context is not None
        role = _execution_role(execution_context)
        with self._lock:
            self.calls.append((role, execution_context))
        output = _bound_review_output(prompt, role)
        _write_success_execution(
            execution_context,
            command=["counting-json-runner", role],
            output=output,
        )
        return RunnerResult(
            status="success",
            output=output,
            command=["counting-json-runner", role],
        )


class _LocalPythonReviewRunner:
    def __init__(self) -> None:
        self.prompts: dict[ReviewerRole, str] = {}
        self._lock = threading.Lock()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        assert sandbox == "read-only"
        assert execution_context is not None
        role = _execution_role(execution_context)
        canary = PRIVATE_CANARIES[role]
        with self._lock:
            self.prompts[role] = prompt
        payload = json.loads(_bound_review_output(prompt, role, canary))
        script = (
            "import json, sys, time; "
            "prompt = sys.stdin.read(); "
            "assert prompt.strip(); "
            "time.sleep(0.25); "
            f"print(json.dumps({payload!r}), flush=True)"
        )
        command = [sys.executable, "-u", "-c", script]
        result = run_owned_process(
            command,
            prompt,
            repo_path,
            timeout_seconds,
            execution_context,
        )
        return RunnerResult(
            status=result.status,
            output=result.output,
            error=result.error,
            command=command,
        )


class _ExclusiveClaimProbeRunner:
    def __init__(self, marker_path: Path) -> None:
        self.marker_path = marker_path

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        assert sandbox == "read-only"
        assert execution_context is not None
        role = _execution_role(execution_context)
        payload = json.loads(_bound_review_output(prompt, role))
        script = (
            "from pathlib import Path; import json, sys, time; "
            "prompt = sys.stdin.read(); assert prompt.strip(); "
            f"marker = Path({str(self.marker_path)!r}); "
            "marker.parent.mkdir(parents=True, exist_ok=True); "
            "handle = marker.open('a', encoding='utf-8'); "
            "handle.write('started\\n'); handle.close(); "
            "time.sleep(0.75); "
            f"print(json.dumps({payload!r}), flush=True)"
        )
        command = [sys.executable, "-u", "-c", script]
        result = run_owned_process(
            command,
            prompt,
            repo_path,
            timeout_seconds,
            execution_context,
        )
        return RunnerResult(
            status=result.status,
            output=result.output,
            error=result.error,
            command=command,
        )


def _execution_role(context: RunnerExecutionContext) -> ReviewerRole:
    identity = context.runner_identity or {}
    role = identity.get("role")
    assert role in PRIVATE_CANARIES
    return cast(ReviewerRole, role)


def _bound_review_output(
    prompt: str,
    role: ReviewerRole,
    canary: str | None = None,
) -> str:
    plan_match = re.search(r"ReviewPlan：`([^`]+)`", prompt)
    snapshot_match = re.search(r"Evidence snapshot：`([^`]+)`", prompt)
    assert plan_match is not None
    assert snapshot_match is not None
    suffix = f" {canary}" if canary is not None else ""
    return json.dumps(
        {
            "schema_version": 1,
            "reviewer_role": role,
            "review_plan_id": plan_match.group(1),
            "evidence_snapshot_sha256": snapshot_match.group(1),
            "verdict": "approve",
            "summary": f"{role} completed{suffix}",
            "findings": [],
            "checked_items": ["public evidence", role],
        },
        ensure_ascii=False,
    )


def _write_success_execution(
    context: RunnerExecutionContext,
    *,
    command: list[str],
    output: str,
) -> None:
    timestamp = "2026-07-17T00:00:00+00:00"
    context.execution_dir.mkdir(parents=True, exist_ok=True)
    output_path = context.execution_dir / "process-output.txt"
    output_path.write_text(
        output.rstrip() + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output_payload = output_path.read_bytes()
    lease = ExecutionLease(
        run_id=context.run_id,
        step=context.step,
        iteration=context.iteration,
        engine=context.engine,
        graph_schema_version=context.graph_schema_version,
        step_id=context.step_id,
        attempt_id=context.attempt_id,
        idempotency_key=context.idempotency_key,
        replay_class=context.replay_class,
        runner_identity=context.runner_identity or {},
        base_head=context.base_head,
        before_workspace_fingerprint=context.before_workspace_fingerprint,
        policy_snapshot_sha256=context.policy_snapshot_sha256,
        input_fingerprint=context.input_fingerprint,
        command_sha256=hash_command(command),
        process_output_sha256=hashlib.sha256(
            output_payload
        ).hexdigest(),
        process_output_bytes=len(output_payload),
        owner_pid=max(1, os.getpid()),
        command=command,
        started_at=timestamp,
        last_heartbeat=timestamp,
        lease_expires_at=timestamp,
        deadline=timestamp,
        status="completed",
        returncode=0,
        finished_at=timestamp,
    )
    context.execution_dir.joinpath("execution.json").write_text(
        lease.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class _NotifyAfterSuccess:
    def __init__(
        self,
        delegate: ParallelReviewerExecutor,
        completed: threading.Event,
    ) -> None:
        self.delegate = delegate
        self.completed = completed

    def __call__(self, **kwargs: Any):
        result = self.delegate(**kwargs)
        self.completed.set()
        return result


class _RaiseAfterPeerCompleted:
    def __init__(self, peer_completed: threading.Event) -> None:
        self.peer_completed = peer_completed
        self.calls = 0

    def __call__(self, **_: Any):
        self.calls += 1
        assert self.peer_completed.wait(timeout=5)
        raise RuntimeError("injected reviewer branch failure")


class _FailIfCalled:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    def __call__(self, **_: Any):
        self.calls += 1
        raise AssertionError(f"{self.label} 已有 result，不得重复启动 runner")


@pytest.mark.requires_langgraph
@requires_langgraph
def test_partial_adaptive_fanout_resume_reuses_completed_result(
    tmp_path: Path,
) -> None:
    run_dir, repo_path, snapshot = _create_run(
        tmp_path,
        run_id="gate5-partial-resume",
    )
    context = _context(
        run_dir,
        snapshot,
        topology="adaptive",
    )
    assert context.review_plan.required_roles == [
        "correctness_reviewer",
        "verification_adequacy_reviewer",
    ]
    prepared = _prepare_evidence(snapshot)
    first_runner = _CountingJsonRunner()
    correctness_completed = threading.Event()
    failing_branch = _RaiseAfterPeerCompleted(correctness_completed)
    correctness_executor = _runner_executor(
        runner=first_runner,
        prepared=prepared,
        repo_path=repo_path,
        reviewer_role="correctness_reviewer",
    )
    checkpoint_path = tmp_path / "parallel-review-checkpoints.sqlite"
    config = {
        "configurable": {
            "thread_id": (
                f"{context.review_plan.run_id}:{context.review_plan.plan_id}"
            )
        }
    }
    first_connection = sqlite3.connect(
        str(checkpoint_path),
        check_same_thread=False,
    )
    try:
        first_checkpointer = SqliteSaver(first_connection)
        first_checkpointer.setup()
        with pytest.raises(
            RuntimeError,
            match="injected reviewer branch failure",
        ):
            execute_parallel_review_graph(
                run_dir,
                context=context,
                executors={
                    "correctness_reviewer": _NotifyAfterSuccess(
                        correctness_executor,
                        correctness_completed,
                    ),
                    "verification_adequacy_reviewer": failing_branch,
                },
                checkpointer=first_checkpointer,
            )
        assert list(first_checkpointer.list(config))
    finally:
        first_connection.close()

    assert len(first_runner.calls) == 1
    assert first_runner.calls[0][0] == "correctness_reviewer"
    assert failing_branch.calls == 1

    duplicate_guard = _FailIfCalled("correctness reviewer")
    resumed_runner = _CountingJsonRunner()
    resumed_connection = sqlite3.connect(
        str(checkpoint_path),
        check_same_thread=False,
    )
    try:
        resumed_checkpointer = SqliteSaver(resumed_connection)
        resumed_checkpointer.setup()
        resumed = execute_parallel_review_graph(
            run_dir,
            context=context,
            executors={
                "correctness_reviewer": duplicate_guard,
                "verification_adequacy_reviewer": _runner_executor(
                    runner=resumed_runner,
                    prepared=prepared,
                    repo_path=repo_path,
                    reviewer_role="verification_adequacy_reviewer",
                ),
            },
            checkpointer=resumed_checkpointer,
        )
        assert list(resumed_checkpointer.list(config))
    finally:
        resumed_connection.close()

    assert duplicate_guard.calls == 0
    assert [item[0] for item in resumed_runner.calls] == [
        "verification_adequacy_reviewer"
    ]
    assert {
        result_ref.reviewer_role for result_ref in resumed.result_refs
    } == set(context.review_plan.required_roles)
    assert resumed.aggregate.verdict == "approve"


@pytest.mark.requires_langgraph
@requires_langgraph
def test_checkpoint_resume_rejects_missing_completed_result_artifact(
    tmp_path: Path,
) -> None:
    run_dir, _, snapshot = _create_run(
        tmp_path,
        run_id="gate5-checkpoint-artifact-conflict",
    )
    context = _context(
        run_dir,
        snapshot,
        topology="adaptive",
    )
    correctness_completed = threading.Event()
    checkpoint_path = tmp_path / "parallel-review-conflict.sqlite"
    first_connection = sqlite3.connect(
        str(checkpoint_path),
        check_same_thread=False,
    )
    try:
        first_checkpointer = SqliteSaver(first_connection)
        first_checkpointer.setup()
        with pytest.raises(RuntimeError, match="injected reviewer branch failure"):
            execute_parallel_review_graph(
                run_dir,
                context=context,
                executors={
                    "correctness_reviewer": _NotifyAfterSuccess(
                        DeterministicFakeReviewer(
                            reviewer_role="correctness_reviewer",
                            private_canary="checkpoint-completed",
                        ),
                        correctness_completed,
                    ),
                    "verification_adequacy_reviewer": _RaiseAfterPeerCompleted(
                        correctness_completed
                    ),
                },
                checkpointer=first_checkpointer,
            )
    finally:
        first_connection.close()

    pointer_ref = parallel_review_result_pointer_artifact_ref(
        context.review_plan,
        reviewer_role="correctness_reviewer",
    )
    pointer_path = run_dir.joinpath(*pointer_ref.split("/"))
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    run_dir.joinpath(*pointer["artifact_ref"].split("/")).unlink()
    pointer_path.unlink()

    correctness_guard = _FailIfCalled("correctness reviewer")
    verification_guard = _FailIfCalled("verification reviewer")
    resumed_connection = sqlite3.connect(
        str(checkpoint_path),
        check_same_thread=False,
    )
    try:
        resumed_checkpointer = SqliteSaver(resumed_connection)
        resumed_checkpointer.setup()
        with pytest.raises(
            ParallelReviewGraphValidationError,
            match="checkpoint result ref.*artifact pointer",
        ):
            execute_parallel_review_graph(
                run_dir,
                context=context,
                executors={
                    "correctness_reviewer": correctness_guard,
                    "verification_adequacy_reviewer": verification_guard,
                },
                checkpointer=resumed_checkpointer,
            )
    finally:
        resumed_connection.close()

    assert correctness_guard.calls == 0
    assert verification_guard.calls == 0


@pytest.mark.requires_langgraph
@requires_langgraph
def test_published_provider_error_result_is_not_automatically_retried(
    tmp_path: Path,
) -> None:
    terminal_status = "provider_error"
    run_dir, _, snapshot = _create_run(
        tmp_path,
        run_id=f"gate5-no-retry-{terminal_status}",
    )
    context = _context(
        run_dir,
        snapshot,
        topology="adaptive",
    )
    first = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors={
            "correctness_reviewer": DeterministicFakeReviewer(
                reviewer_role="correctness_reviewer",
                private_canary="completed-result",
            ),
            "verification_adequacy_reviewer": DeterministicFakeReviewer(
                reviewer_role="verification_adequacy_reviewer",
                private_canary=f"{terminal_status}-result",
                status=cast(Any, terminal_status),
                verdict="needs_human",
            ),
        },
    )
    guards = {
        role: _FailIfCalled(role)
        for role in context.review_plan.required_roles
    }

    resumed = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=cast(
            Mapping[ReviewerRole, ParallelReviewerExecutor],
            guards,
        ),
    )

    assert all(guard.calls == 0 for guard in guards.values())
    assert resumed.result_refs == first.result_refs
    assert resumed.aggregate == first.aggregate
    assert resumed.aggregate.verdict == "needs_human"
    assert "reviewer_execution_unresolved" in resumed.aggregate.reasons


@pytest.mark.requires_langgraph
@requires_langgraph
def test_runner_reviewers_use_isolated_owned_process_artifacts(
    tmp_path: Path,
) -> None:
    run_dir, repo_path, snapshot = _create_run(
        tmp_path,
        run_id="gate5-real-process-isolation",
    )
    context = _context(
        run_dir,
        snapshot,
        topology="fixed_three",
    )
    prepared = _prepare_evidence(snapshot)
    worker_private_canary = "WORKER_PRIVATE_CANARY_GATE5"
    run_dir.joinpath("worker-private-output.txt").write_text(
        worker_private_canary + "\n",
        encoding="utf-8",
        newline="\n",
    )
    runners = {
        role: _LocalPythonReviewRunner()
        for role in context.review_plan.required_roles
    }
    executors = {
        role: _runner_executor(
            runner=runners[role],
            prepared=prepared,
            repo_path=repo_path,
            reviewer_role=role,
        )
        for role in context.review_plan.required_roles
    }

    run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=executors,
    )

    attempt_ids: set[str] = set()
    child_pids: set[int] = set()
    role_artifacts: dict[ReviewerRole, tuple[str, str, str, str]] = {}
    for result_ref in run.result_refs:
        role = result_ref.reviewer_role
        result = read_parallel_review_result(run_dir, result_ref)
        execution_path = run_dir.joinpath(
            *result.execution_ref.split("/")
        )
        output_path = execution_path.with_name("process-output.txt")
        result_path = run_dir.joinpath(*result_ref.artifact_ref.split("/"))
        prompt_path = run_dir.joinpath(
            *parallel_review_role_prompt_artifact_ref(
                context.review_plan,
                role,
            ).split("/")
        )
        execution_text = execution_path.read_text(encoding="utf-8")
        output_text = output_path.read_text(encoding="utf-8")
        result_text = result_path.read_text(encoding="utf-8")
        prompt_text = prompt_path.read_text(encoding="utf-8")
        lease = ExecutionLease.model_validate_json(execution_text)

        assert lease.runner_identity["role"] == role
        assert lease.attempt_id == result.attempt_id
        assert lease.child_pid is not None
        assert lease.status == "completed"
        assert PRIVATE_CANARIES[role] in output_text
        assert PRIVATE_CANARIES[role] in result_text
        attempt_ids.add(result.attempt_id)
        child_pids.add(lease.child_pid)
        role_artifacts[role] = (
            execution_text,
            output_text,
            result_text,
            prompt_text,
        )

    for role, runner in runners.items():
        assert set(runner.prompts) == {role}
    assert len(
        {
            next(iter(runner.prompts.values()))
            for runner in runners.values()
        }
    ) == 3
    assert len(attempt_ids) == 3
    assert len(child_pids) == 3
    for role, serialized_artifacts in role_artifacts.items():
        assert all(
            worker_private_canary not in content
            for content in serialized_artifacts
        )
        for other_role, other_canary in PRIVATE_CANARIES.items():
            if other_role == role:
                continue
            assert all(
                other_canary not in content
                for content in serialized_artifacts
            )

    public_evidence_path = run_dir.joinpath(
        *parallel_review_public_evidence_artifact_ref(
            context.review_plan
        ).split("/")
    )
    public_evidence_text = public_evidence_path.read_text(encoding="utf-8")
    aggregate_path = run_dir.joinpath(
        *run.aggregate_ref["artifact_ref"].split("/")
    )
    aggregate_text = aggregate_path.read_text(encoding="utf-8")
    graph_state_text = json.dumps(
        run.graph_state,
        ensure_ascii=False,
        sort_keys=True,
    )
    for canary in (*PRIVATE_CANARIES.values(), worker_private_canary):
        assert canary not in public_evidence_text
        assert canary not in aggregate_text
        assert canary not in graph_state_text


def test_same_role_concurrent_attempt_starts_only_one_owned_process(
    tmp_path: Path,
) -> None:
    run_dir, repo_path, snapshot = _create_run(
        tmp_path,
        run_id="gate5-exclusive-attempt",
    )
    context = _context(
        run_dir,
        snapshot,
        topology="single",
    )
    write_parallel_review_plan(run_dir, context.review_plan)
    marker_path = run_dir / "owned-process-starts.txt"
    runner = _ExclusiveClaimProbeRunner(marker_path)
    executor = _runner_executor(
        runner=runner,
        prepared=_prepare_evidence(snapshot),
        repo_path=repo_path,
        reviewer_role="correctness_reviewer",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                executor,
                run_dir=run_dir,
                plan=context.review_plan,
                reviewer_role="correctness_reviewer",
            )
            for _ in range(2)
        ]
        result_refs = []
        errors = []
        for future in futures:
            try:
                result_refs.append(future.result(timeout=10))
            except Exception as exc:
                errors.append(exc)

    assert len(result_refs) == 1
    assert len(errors) == 1
    assert isinstance(
        errors[0],
        (
            ParallelReviewAttemptActiveError,
            ParallelReviewRuntimeValidationError,
        ),
    )
    assert (
        "仍处于 active" in str(errors[0])
        or "已被认领" in str(errors[0])
        or "已进入 Runner" in str(errors[0])
    )
    assert marker_path.read_text(encoding="utf-8").splitlines() == [
        "started"
    ]

    replayed = executor(
        run_dir=run_dir,
        plan=context.review_plan,
        reviewer_role="correctness_reviewer",
    )

    assert replayed == result_refs[0]
    assert marker_path.read_text(encoding="utf-8").splitlines() == [
        "started"
    ]
    assert list_parallel_review_result_refs(
        run_dir,
        context.review_plan,
    ) == (replayed,)


def test_stop_broadcast_reaches_every_active_reviewer_execution(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "gate5-stop-broadcast"
    repo_path = tmp_path / "repo"
    run_dir.mkdir(parents=True)
    repo_path.mkdir()
    contexts = {
        role: RunnerExecutionContext(
            execution_dir=(
                run_dir
                / "iterations"
                / "01"
                / "parallel-reviews"
                / role
                / "attempt-live"
            ),
            run_id=run_dir.name,
            step="reviewer",
            iteration=1,
            engine="langgraph",
            graph_schema_version="checkpoint-v1",
            step_id=f"review-{role}-iteration-01",
            attempt_id=f"attempt-{role}",
            idempotency_key="sha256:" + role.encode().hex().ljust(64, "0"),
            replay_class="read_only_replayable",
            runner_identity={"kind": "local-python", "role": role},
            input_fingerprint="sha256:" + "2" * 64,
            heartbeat_interval_seconds=0.05,
            lease_timeout_seconds=0.5,
            terminate_grace_seconds=0.2,
        )
        for role in (
            "correctness_reviewer",
            "verification_adequacy_reviewer",
        )
    }
    command = [
        sys.executable,
        "-u",
        "-c",
        "import time; print('reviewer-ready', flush=True); time.sleep(30)",
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            role: pool.submit(
                run_owned_process,
                command,
                "",
                repo_path,
                20,
                context,
            )
            for role, context in contexts.items()
        }
        _wait_until(
            lambda: all(
                _execution_status(context.execution_dir) == "running"
                for context in contexts.values()
            ),
            message="两个 reviewer execution 未同时进入 running",
        )

        request_stop_for_active_executions(
            run_dir,
            "Gate 5 broadcast stop",
        )

        _wait_until(
            lambda: all(
                context.execution_dir.joinpath(
                    "stop-request.json"
                ).is_file()
                for context in contexts.values()
            ),
            message="广播 stop 未写入全部 reviewer execution",
        )
        results = {
            role: future.result(timeout=10)
            for role, future in futures.items()
        }

    assert set(results) == set(contexts)
    assert all(result.status == "stopped" for result in results.values())
    for context in contexts.values():
        lease = ExecutionLease.model_validate_json(
            context.execution_dir.joinpath("execution.json").read_text(
                encoding="utf-8"
            )
        )
        stop_request = json.loads(
            context.execution_dir.joinpath("stop-request.json").read_text(
                encoding="utf-8"
            )
        )
        assert lease.status == "stopped"
        assert stop_request["reason"] == "Gate 5 broadcast stop"


def _execution_status(execution_dir: Path) -> str | None:
    path = execution_dir / "execution.json"
    if not path.is_file():
        return None
    try:
        return ExecutionLease.model_validate_json(
            path.read_text(encoding="utf-8")
        ).status
    except (OSError, ValueError):
        return None


def _wait_until(
    predicate: Callable[[], bool],
    *,
    message: str,
    timeout_seconds: float = 5,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)
