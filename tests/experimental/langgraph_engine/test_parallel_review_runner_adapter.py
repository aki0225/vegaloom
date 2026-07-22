from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import vega.parallel_review_runtime as review_runtime
from vega.execution_control import (
    ExecutionLease,
    ExecutionStopLatchedError,
    RunnerExecutionContext,
)
from vega.goal_evidence import (
    _validate_iteration_review,
    validate_goal_evidence,
    validate_review_evidence_freshness,
)
from vega.loop_step_result import hash_command
from vega.models import LoopIterationState, ReviewVerdict
from vega.parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    ParallelReviewAggregate,
    ParallelReviewAggregationContext,
    ParallelReviewPlan,
    ParallelReviewResult,
    ParallelReviewRoutingContext,
    ReviewEvidenceSnapshot,
    aggregate_parallel_reviews,
    build_parallel_review_finding,
    build_parallel_review_plan,
    build_parallel_review_result,
    build_review_evidence_snapshot,
)
from vega.parallel_review_artifacts import (
    ParallelReviewArtifactValidationError,
    parallel_review_public_evidence_artifact_ref,
    parallel_review_result_pointer_artifact_ref,
    parallel_review_role_prompt_artifact_ref,
    read_parallel_review_result,
    write_parallel_review_aggregate,
    write_parallel_review_plan,
    write_parallel_review_result,
)
from vega.parallel_review_runtime import (
    ParallelReviewAttemptActiveError,
    ParallelReviewRuntimeValidationError,
    PreparedParallelReviewEvidence,
    RunnerParallelReviewerExecutor,
    parallel_review_aggregate_to_legacy_verdict,
    prepare_parallel_review_evidence,
    render_parallel_review_role_prompt,
    write_parallel_review_compatibility_run,
)
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import REVIEW_ARTIFACTS, run_review_pack_eval
from vega.runner import RunnerResult
from vega.workspace_check import capture_review_workspace


PUBLIC_EVIDENCE = "# 公共审查证据\n\n- 变更文件：`src/example.py`\n- 验证结果：全部通过\n"
PRIVATE_CANARIES = {
    "correctness_reviewer": "CORRECTNESS_PRIVATE_CANARY_RUNTIME",
    "verification_adequacy_reviewer": ("VERIFICATION_PRIVATE_CANARY_RUNTIME"),
    "security_design_reviewer": "SECURITY_PRIVATE_CANARY_RUNTIME",
}


class RecordingRunner:
    def __init__(
        self,
        *,
        status: str = "success",
        output: str | None = None,
        error: str | None = None,
        termination_unconfirmed: bool = False,
    ) -> None:
        self.status = status
        self.output = output
        self.error = error
        self.termination_unconfirmed = termination_unconfirmed
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        assert execution_context is not None
        assert self.output is not None
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "sandbox": sandbox,
                "timeout_seconds": timeout_seconds,
                "execution_context": execution_context,
            }
        )
        output_path = execution_context.execution_dir / "process-output.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            self.output.rstrip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self._write_execution(execution_context)
        return RunnerResult(
            status=self.status,  # type: ignore[arg-type]
            output=self.output,
            error=self.error,
            command=["fake-parallel-reviewer"],
        )

    def _write_execution(self, context: RunnerExecutionContext) -> None:
        command = ["fake-parallel-reviewer"]
        output_payload = context.execution_dir.joinpath(
            "process-output.txt"
        ).read_bytes()
        execution_status = {
            "success": "completed",
            "error": "failed",
            "timed_out": "timed_out",
            "stopped": "stopped",
        }[self.status]
        if self.termination_unconfirmed:
            execution_status = "running"
        runner_started_path = context.execution_dir / "runner-started.json"
        timestamp = (
            json.loads(runner_started_path.read_text(encoding="utf-8"))[
                "started_at"
            ]
            if runner_started_path.exists()
            else "2026-07-17T00:00:00+00:00"
        )
        terminal = execution_status in {
            "completed",
            "failed",
            "timed_out",
            "stopped",
        }
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
            runner_identity=context.runner_identity or {"kind": "test-runner"},
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
            status=execution_status,  # type: ignore[arg-type]
            termination_unconfirmed=self.termination_unconfirmed,
            reason=self.error,
            returncode=(
                0
                if execution_status == "completed"
                else 1
                if execution_status == "failed"
                else None
            ),
            finished_at=timestamp if terminal else None,
        )
        context.execution_dir.mkdir(parents=True, exist_ok=True)
        context.execution_dir.joinpath("execution.json").write_text(
            lease.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


class RaisingRecordingRunner(RecordingRunner):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, repo_path, sandbox, timeout_seconds, execution_context
        raise RuntimeError("runner crashed before returning execution evidence")


class TerminalExecutionCommitRunner(RecordingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.execution_written = threading.Event()
        self.release_result = threading.Event()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        self.execution_written.set()
        assert self.release_result.wait(timeout=5)
        return result


class TerminalExecutionThenAbortRunner(RecordingRunner):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        raise SystemExit("injected after terminal execution")


class WorkspaceDriftRunner(RecordingRunner):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        readme_path = repo_path / "README.md"
        readme_path.write_text(
            readme_path.read_text(encoding="utf-8") + "workspace drift\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


class StopLatchedRunner(RecordingRunner):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        del prompt, repo_path, sandbox, timeout_seconds, execution_context
        raise ExecutionStopLatchedError(
            "run stop latch 已建立，拒绝启动 reviewer"
        )


class AlternateRecordingRunner(RecordingRunner):
    pass


class NonCooperativeRunner:
    """完全忽略 execution_context，用于验证 executor 自身的原子认领。"""

    def __init__(self) -> None:
        self.calls = 0
        self.output = "{}"
        self.started = threading.Event()
        self.release = threading.Event()
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
        del prompt, repo_path, sandbox, timeout_seconds, execution_context
        with self._lock:
            self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=5)
        return RunnerResult(
            status="success",
            output="{}",
            error=None,
            command=["non-cooperative-runner"],
        )


def _snapshot(
    run_id: str,
    *,
    workspace_fingerprint: str = "sha256:" + "1" * 64,
) -> ReviewEvidenceSnapshot:
    return build_review_evidence_snapshot(
        run_id=run_id,
        iteration=1,
        workspace_fingerprint=workspace_fingerprint,
        policy_snapshot_sha256="2" * 64,
        verification_result_sha256="3" * 64,
        risk_result_sha256="4" * 64,
        acceptance_evidence_manifest_sha256="5" * 64,
    )


def _plan(
    snapshot: ReviewEvidenceSnapshot,
    *,
    topology: str = "adaptive",
) -> ParallelReviewPlan:
    routing = ParallelReviewRoutingContext(
        run_id=snapshot.run_id,
        iteration=snapshot.iteration,
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        verification_status="passed",
        verification_failed_count=0,
        risk="low",
        changed_files=["src/example.py"],
        gate_reason_codes=[],
    )
    return build_parallel_review_plan(
        routing,
        topology=topology,  # type: ignore[arg-type]
    )


def _valid_review_output(
    plan: ParallelReviewPlan,
    role: str = "correctness_reviewer",
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "reviewer_role": role,
            "review_plan_id": plan.plan_id,
            "evidence_snapshot_sha256": plan.evidence_snapshot_sha256,
            "verdict": "approve",
            "summary": "当前角色未发现阻塞问题。",
            "findings": [],
            "checked_items": ["公共 evidence snapshot"],
        },
        ensure_ascii=False,
    )


def _init_repo(repo_path: Path) -> str:
    repo_path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=repo_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "parallel-review@example.invalid"],
        cwd=repo_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Parallel Review Test"],
        cwd=repo_path,
        check=True,
    )
    repo_path.joinpath("README.md").write_text(
        "# Parallel review fixture\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "test: initialize fixture"],
        cwd=repo_path,
        check=True,
    )
    return "sha256:" + capture_review_workspace(repo_path).fingerprint


def _prepared_evidence(
    snapshot: ReviewEvidenceSnapshot,
) -> PreparedParallelReviewEvidence:
    return prepare_parallel_review_evidence(
        snapshot,
        PUBLIC_EVIDENCE,
        forbidden_markers=tuple(PRIVATE_CANARIES.values()),
    )


def _executor(
    tmp_path: Path,
    *,
    run_id: str,
    runner: RecordingRunner,
    private_canary: str | None = None,
) -> tuple[
    Path,
    Path,
    ReviewEvidenceSnapshot,
    ParallelReviewPlan,
    RunnerParallelReviewerExecutor,
]:
    run_dir = tmp_path / "runs" / run_id
    repo_path = tmp_path / "repo"
    run_dir.mkdir(parents=True)
    workspace_fingerprint = _init_repo(repo_path)
    snapshot = _snapshot(
        run_id,
        workspace_fingerprint=workspace_fingerprint,
    )
    plan = _plan(snapshot)
    write_parallel_review_plan(run_dir, plan)
    if runner.output is None:
        runner.output = _valid_review_output(plan)
    executor = RunnerParallelReviewerExecutor(
        reviewer_role="correctness_reviewer",
        repo_path=repo_path,
        runner=runner,
        evidence=_prepared_evidence(snapshot),
        timeout_seconds=37,
        private_canary=private_canary,
    )
    return run_dir, repo_path, snapshot, plan, executor


def test_three_role_prompts_share_public_hash_and_only_own_canary() -> None:
    snapshot = _snapshot("parallel-prompt-run")
    plan = _plan(snapshot, topology="fixed_three")
    evidence = _prepared_evidence(snapshot)
    expected_hash = hashlib.sha256(PUBLIC_EVIDENCE.encode("utf-8")).hexdigest()

    prompts = {
        role: render_parallel_review_role_prompt(
            plan,
            role,
            evidence.public_evidence_sha256,
            private_canary=PRIVATE_CANARIES[role],
        )
        for role in AVAILABLE_REVIEWER_ROLES
    }

    assert isinstance(evidence, PreparedParallelReviewEvidence)
    assert evidence.public_evidence_sha256 == expected_hash
    for role, prompt in prompts.items():
        assert role in prompt
        assert expected_hash in prompt
        assert PRIVATE_CANARIES[role] in prompt
        for other_role, other_canary in PRIVATE_CANARIES.items():
            if other_role != role:
                assert other_canary not in prompt


def test_prepare_public_evidence_rejects_private_marker() -> None:
    snapshot = _snapshot("parallel-evidence-marker-run")

    with pytest.raises(ValueError):
        prepare_parallel_review_evidence(
            snapshot,
            PUBLIC_EVIDENCE + PRIVATE_CANARIES["correctness_reviewer"],
            forbidden_markers=tuple(PRIVATE_CANARIES.values()),
        )


def test_successful_executor_is_read_only_and_writes_trusted_artifacts(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    canary = PRIVATE_CANARIES["correctness_reviewer"]
    run_dir, repo_path, snapshot, plan, executor = _executor(
        tmp_path,
        run_id="parallel-success-run",
        runner=runner,
        private_canary=canary,
    )

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,  # type: ignore[arg-type]
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["repo_path"] == repo_path.resolve()
    assert call["sandbox"] == "read-only"
    assert call["timeout_seconds"] == 37
    assert canary in str(call["prompt"])
    context = call["execution_context"]
    assert isinstance(context, RunnerExecutionContext)
    assert context.engine == "langgraph"
    assert context.step == "reviewer"
    assert context.iteration == plan.iteration  # type: ignore[attr-defined]
    assert context.replay_class == "read_only_replayable"
    assert context.policy_snapshot_sha256 == snapshot.policy_snapshot_sha256

    assert result.status == "completed"
    assert result.verdict == "approve"
    assert result.review_plan_id == plan.plan_id  # type: ignore[attr-defined]
    assert result.evidence_snapshot_sha256 == snapshot.evidence_snapshot_sha256
    execution_path = run_dir.joinpath(*result.execution_ref.split("/"))
    execution = ExecutionLease.model_validate_json(execution_path.read_text(encoding="utf-8"))
    assert execution.status == "completed"
    assert execution.attempt_id == result.attempt_id
    assert execution.replay_class == "read_only_replayable"
    assert result.execution_sha256 == hashlib.sha256(execution_path.read_bytes()).hexdigest()

    evidence_path = run_dir.joinpath(
        *parallel_review_public_evidence_artifact_ref(plan).split("/")  # type: ignore[arg-type]
    )
    prompt_path = run_dir.joinpath(
        *parallel_review_role_prompt_artifact_ref(
            plan,  # type: ignore[arg-type]
            "correctness_reviewer",
        ).split("/")
    )
    assert evidence_path.read_text(encoding="utf-8") == PUBLIC_EVIDENCE
    assert canary in prompt_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    (
        "case_id",
        "runner_status",
        "output",
        "termination_unconfirmed",
        "expected_status",
        "expected_verdict",
    ),
    [
        (
            "valid-success",
            "success",
            None,
            False,
            "completed",
            "approve",
        ),
        (
            "invalid-json",
            "success",
            "not-json",
            False,
            "parse_error",
            "needs_human",
        ),
        (
            "provider-error",
            "error",
            "",
            False,
            "provider_error",
            "needs_human",
        ),
        (
            "timed-out",
            "timed_out",
            "",
            False,
            "timed_out",
            "needs_human",
        ),
        (
            "stopped",
            "stopped",
            "",
            False,
            "stopped",
            "needs_human",
        ),
    ],
)
def test_runner_and_execution_statuses_map_to_parallel_result(
    tmp_path: Path,
    case_id: str,
    runner_status: str,
    output: str | None,
    termination_unconfirmed: bool,
    expected_status: str,
    expected_verdict: str,
) -> None:
    runner = RecordingRunner(
        status=runner_status,
        output=output,
        error=None if runner_status == "success" else f"{case_id} error",
        termination_unconfirmed=termination_unconfirmed,
    )
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id=("pr-status-" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8]),
        runner=runner,
    )

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,  # type: ignore[arg-type]
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)

    assert result.status == expected_status
    assert result.verdict == expected_verdict
    if expected_status != "completed":
        assert result.findings == []


@pytest.mark.parametrize(
    ("runner_status", "expected_summary"),
    [
        ("timed_out", "Reviewer Runner 执行超时"),
        ("stopped", "Reviewer Runner 已按 stop request 停止"),
    ],
)
def test_terminal_timeout_or_stop_status_survives_workspace_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_status: str,
    expected_summary: str,
) -> None:
    runner = WorkspaceDriftRunner(
        status=runner_status,
        output="",
        error=f"{runner_status} evidence",
    )
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id=f"parallel-{runner_status}-workspace-drift",
        runner=runner,
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            review_runtime,
            "write_parallel_review_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("injected result publish failure")
            ),
        )
        with pytest.raises(
            RuntimeError,
            match="injected result publish failure",
        ):
            executor(
                run_dir=run_dir,
                plan=plan,
                reviewer_role="correctness_reviewer",
            )

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)
    execution = ExecutionLease.model_validate_json(
        next(run_dir.rglob("execution.json")).read_text(encoding="utf-8")
    )

    assert result.status == runner_status
    assert execution.status == runner_status
    assert result.verdict == "needs_human"
    assert expected_summary in result.summary
    assert "Reviewer 执行期间工作区发生变化" in result.summary
    assert len(runner.calls) == 1


def test_same_executor_replay_reuses_result_without_second_runner_call(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-replay-run",
        runner=runner,
    )

    first = executor(
        run_dir=run_dir,
        plan=plan,  # type: ignore[arg-type]
        reviewer_role="correctness_reviewer",
    )
    second = executor(
        run_dir=run_dir,
        plan=plan,  # type: ignore[arg-type]
        reviewer_role="correctness_reviewer",
    )

    assert second == first
    assert len(runner.calls) == 1
    assert read_parallel_review_result(run_dir, second).status == "completed"


def test_attempt_claim_prevents_duplicate_non_cooperative_runner_calls(
    tmp_path: Path,
) -> None:
    runner = NonCooperativeRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-atomic-attempt-claim",
        runner=runner,  # type: ignore[arg-type]
    )

    def invoke() -> object:
        return executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke)
        assert runner.started.wait(timeout=5)
        second = pool.submit(invoke)
        with pytest.raises(
            ParallelReviewAttemptActiveError,
            match="已进入 Runner",
        ):
            second.result(timeout=5)
        runner.release.set()
        with pytest.raises(ParallelReviewAttemptActiveError):
            first.result(timeout=5)

    pointer_path = run_dir.joinpath(
        *parallel_review_result_pointer_artifact_ref(
            plan,
            "correctness_reviewer",
        ).split("/")
    )
    assert runner.calls == 1
    assert pointer_path.exists() is False


def test_claim_only_orphan_reuses_same_attempt_before_runner_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecordingRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-claim-only-orphan",
        runner=runner,
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            review_runtime,
            "_write_runner_started_metadata",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("injected before runner marker")
            ),
        )
        with pytest.raises(
            RuntimeError,
            match="injected before runner marker",
        ):
            executor(
                run_dir=run_dir,
                plan=plan,
                reviewer_role="correctness_reviewer",
            )

    assert len(runner.calls) == 0
    assert len(list(run_dir.rglob("attempt-claim.json"))) == 1
    assert list(run_dir.rglob("runner-started.json")) == []
    assert list(run_dir.rglob("execution.json")) == []

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)

    assert result.status == "completed"
    assert result.verdict == "approve"
    assert len(runner.calls) == 1


def test_runner_started_orphan_becomes_needs_human_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecordingRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-runner-started-orphan",
        runner=runner,
    )
    write_started = review_runtime._write_runner_started_metadata

    def write_then_abort(*args, **kwargs) -> None:
        write_started(*args, **kwargs)
        raise SystemExit("injected after runner marker")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            review_runtime,
            "_write_runner_started_metadata",
            write_then_abort,
        )
        with pytest.raises(
            SystemExit,
            match="injected after runner marker",
        ):
            executor(
                run_dir=run_dir,
                plan=plan,
                reviewer_role="correctness_reviewer",
            )

    assert len(runner.calls) == 0
    assert len(list(run_dir.rglob("runner-started.json"))) == 1
    assert list(run_dir.rglob("execution.json")) == []

    monkeypatch.setattr(
        review_runtime,
        "is_process_alive",
        lambda _pid: False,
    )
    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)
    execution = ExecutionLease.model_validate_json(
        next(run_dir.rglob("execution.json")).read_text(encoding="utf-8")
    )

    assert result.status == "termination_unconfirmed"
    assert result.verdict == "needs_human"
    assert execution.status == "failed"
    assert execution.termination_unconfirmed is True
    assert len(runner.calls) == 0


def test_stale_active_execution_becomes_needs_human_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecordingRunner(
        status="error",
        output="",
        error="owned process may still be alive",
        termination_unconfirmed=True,
    )
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-stale-active",
        runner=runner,
    )

    with pytest.raises(ParallelReviewAttemptActiveError):
        executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )

    monkeypatch.setattr(
        review_runtime,
        "is_process_alive",
        lambda _pid: False,
    )
    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)
    execution = ExecutionLease.model_validate_json(
        next(run_dir.rglob("execution.json")).read_text(encoding="utf-8")
    )

    assert result.status == "termination_unconfirmed"
    assert result.verdict == "needs_human"
    assert execution.status == "failed"
    assert execution.termination_unconfirmed is True
    assert len(runner.calls) == 1


def test_runner_exception_without_execution_is_termination_unconfirmed(
    tmp_path: Path,
) -> None:
    runner = RaisingRecordingRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-runner-exception",
        runner=runner,
    )

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)
    execution = ExecutionLease.model_validate_json(
        next(run_dir.rglob("execution.json")).read_text(encoding="utf-8")
    )

    assert result.status == "termination_unconfirmed"
    assert result.verdict == "needs_human"
    assert execution.status == "failed"
    assert execution.termination_unconfirmed is True
    assert "owned process 终止未确认" in result.summary


def test_stop_latched_runner_is_published_as_stopped(
    tmp_path: Path,
) -> None:
    runner = StopLatchedRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-stop-latched-runner",
        runner=runner,
    )

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)
    execution = ExecutionLease.model_validate_json(
        next(run_dir.rglob("execution.json")).read_text(
            encoding="utf-8"
        )
    )

    assert result.status == "stopped"
    assert result.verdict == "needs_human"
    assert execution.status == "stopped"
    assert execution.termination_unconfirmed is False
    assert "stop latch" in result.summary


def test_active_termination_unconfirmed_execution_publishes_no_result(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        status="error",
        output="",
        error="owned process may still be alive",
        termination_unconfirmed=True,
    )
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="pr-active-unconfirmed",
        runner=runner,
    )

    with pytest.raises(ParallelReviewAttemptActiveError):
        executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )

    execution_path = next(run_dir.rglob("execution.json"))
    execution = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    pointer_path = run_dir.joinpath(
        *parallel_review_result_pointer_artifact_ref(
            plan,
            "correctness_reviewer",
        ).split("/")
    )
    assert execution.status == "running"
    assert execution.termination_unconfirmed is True
    assert list(run_dir.rglob("r-*.json")) == []
    assert pointer_path.exists() is False


def test_artifact_validator_rejects_active_termination_unconfirmed_result(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        status="error",
        output="",
        error="owned process may still be alive",
        termination_unconfirmed=True,
    )
    run_dir, _, snapshot, plan, executor = _executor(
        tmp_path,
        run_id="parallel-active-artifact-rejection",
        runner=runner,
    )
    with pytest.raises(ParallelReviewAttemptActiveError):
        executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )
    execution_path = next(run_dir.rglob("execution.json"))
    execution = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    forged = build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=plan.run_id,
        iteration=plan.iteration,
        reviewer_role="correctness_reviewer",
        attempt_id=execution.attempt_id,
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        execution_ref=execution_path.relative_to(run_dir).as_posix(),
        execution_sha256=hashlib.sha256(
            execution_path.read_bytes()
        ).hexdigest(),
        status="termination_unconfirmed",
        verdict="needs_human",
        summary="进程终止尚未确认。",
        findings=[],
        checked_items=["owned process"],
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="active Reviewer execution",
    ):
        write_parallel_review_result(run_dir, forged)

    assert list(run_dir.rglob("r-*.json")) == []


def test_existing_attempt_rejects_runner_identity_drift(
    tmp_path: Path,
) -> None:
    first_runner = RecordingRunner()
    run_dir, repo_path, snapshot, plan, first_executor = _executor(
        tmp_path,
        run_id="parallel-runner-identity-drift",
        runner=first_runner,
    )
    first_executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    second_executor = RunnerParallelReviewerExecutor(
        reviewer_role="correctness_reviewer",
        repo_path=repo_path,
        runner=AlternateRecordingRunner(
            output=_valid_review_output(plan),
        ),
        evidence=_prepared_evidence(snapshot),
        timeout_seconds=37,
    )

    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="execution identity",
    ):
        second_executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )


def test_resume_rejects_tampered_process_output_before_result_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecordingRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-output-tamper",
        runner=runner,
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            review_runtime,
            "write_parallel_review_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("injected result publish failure")
            ),
        )
        with pytest.raises(
            RuntimeError,
            match="injected result publish failure",
        ):
            executor(
                run_dir=run_dir,
                plan=plan,
                reviewer_role="correctness_reviewer",
            )

    execution_path = next(run_dir.rglob("execution.json"))
    output_path = execution_path.with_name("process-output.txt")
    output_path.write_text(
        _valid_review_output(plan).replace(
            '"verdict": "approve"',
            '"verdict": "request_changes"',
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="process output",
    ):
        executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )

    assert len(runner.calls) == 1


def test_concurrent_resume_waits_for_terminal_execution_metadata_commit(
    tmp_path: Path,
) -> None:
    runner = TerminalExecutionCommitRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-terminal-metadata-commit",
        runner=runner,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            executor,
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )
        assert runner.execution_written.wait(timeout=5)
        execution_path = next(run_dir.rglob("execution.json"))
        metadata_path = execution_path.with_name("runner-result.json")
        assert metadata_path.exists() is False
        with pytest.raises(
            ParallelReviewAttemptActiveError,
            match="正在提交 runner-result.json",
        ):
            executor(
                run_dir=run_dir,
                plan=plan,
                reviewer_role="correctness_reviewer",
            )
        pointer_path = run_dir.joinpath(
            *parallel_review_result_pointer_artifact_ref(
                plan,
                "correctness_reviewer",
            ).split("/")
        )
        assert pointer_path.exists() is False
        assert list(run_dir.rglob("r-*.json")) == []
        runner.release_result.set()
        first_ref = first.result(timeout=5)

    resumed_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, resumed_ref)

    assert len(runner.calls) == 1
    assert resumed_ref == first_ref
    assert metadata_path.exists() is True
    assert result.status == "completed"
    assert result.verdict == "approve"


def test_terminal_execution_orphan_becomes_provider_error_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = TerminalExecutionThenAbortRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-terminal-execution-orphan",
        runner=runner,
    )

    with pytest.raises(
        SystemExit,
        match="injected after terminal execution",
    ):
        executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )

    execution_path = next(run_dir.rglob("execution.json"))
    metadata_path = execution_path.with_name("runner-result.json")
    execution = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    assert execution.status == "completed"
    assert metadata_path.exists() is False
    assert len(runner.calls) == 1

    monkeypatch.setattr(
        review_runtime,
        "is_process_alive",
        lambda _pid: False,
    )
    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert result.status == "provider_error"
    assert result.verdict == "needs_human"
    assert "禁止自动重试或恢复 success" in result.summary
    assert metadata["source"] == "terminal_execution_recovery"
    assert metadata["status"] == "error"
    assert len(runner.calls) == 1


def test_forged_runner_success_metadata_cannot_recover_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = TerminalExecutionThenAbortRunner()
    run_dir, _, _, plan, executor = _executor(
        tmp_path,
        run_id="parallel-forged-runner-metadata",
        runner=runner,
    )

    with pytest.raises(SystemExit):
        executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role="correctness_reviewer",
        )

    execution_path = next(run_dir.rglob("execution.json"))
    review_runtime._write_runner_result_metadata(
        execution_path.parent,
        RunnerResult(
            status="success",
            output="",
            error=None,
            command=["forged-runner"],
        ),
    )
    monkeypatch.setattr(
        review_runtime,
        "is_process_alive",
        lambda _pid: False,
    )

    result_ref = executor(
        run_dir=run_dir,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(run_dir, result_ref)

    assert result.status == "provider_error"
    assert result.verdict == "needs_human"
    assert "不得信任" in result.summary
    assert len(runner.calls) == 1


def test_runner_result_metadata_uses_staged_fsync_atomic_create_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_dir = tmp_path / "execution"
    result = RunnerResult(
        status="success",
        output="ignored",
        error=None,
        command=["reviewer", "--read-only"],
    )
    real_fsync = review_runtime.os.fsync
    real_link = review_runtime.os.link
    fsync_calls: list[int] = []
    linked_staged_payloads: list[str] = []

    def recording_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        real_fsync(descriptor)

    def recording_link(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.name.startswith(".rr-")
        assert target_path.exists() is False
        linked_staged_payloads.append(source_path.read_text(encoding="utf-8"))
        real_link(source, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(review_runtime.os, "fsync", recording_fsync)
        scoped.setattr(review_runtime.os, "link", recording_link)
        review_runtime._write_runner_result_metadata(execution_dir, result)

    metadata_path = execution_dir / "runner-result.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    review_runtime._write_runner_result_metadata(execution_dir, result)

    assert fsync_calls
    assert linked_staged_payloads == [metadata_path.read_text(encoding="utf-8")]
    assert metadata["status"] == "success"
    assert metadata["command"] == ["reviewer", "--read-only"]
    assert list(execution_dir.glob(".rr-*")) == []


def test_runner_result_metadata_rejects_truncated_and_conflicting_files(
    tmp_path: Path,
) -> None:
    expected = RunnerResult(
        status="success",
        output="ignored",
        error=None,
        command=["reviewer"],
    )
    truncated_dir = tmp_path / "truncated"
    truncated_dir.mkdir()
    truncated_path = truncated_dir / "runner-result.json"
    truncated_path.write_text('{"status":', encoding="utf-8", newline="\n")

    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="内容冲突",
    ):
        review_runtime._write_runner_result_metadata(truncated_dir, expected)
    assert truncated_path.read_text(encoding="utf-8") == '{"status":'
    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="runner-result.json 不可信",
    ):
        review_runtime._read_runner_result_metadata(truncated_dir)

    conflict_dir = tmp_path / "conflict"
    review_runtime._write_runner_result_metadata(conflict_dir, expected)
    original = (conflict_dir / "runner-result.json").read_bytes()
    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="内容冲突",
    ):
        review_runtime._write_runner_result_metadata(
            conflict_dir,
            RunnerResult(
                status="error",
                output="ignored",
                error="different terminal result",
                command=["reviewer"],
            ),
        )
    assert (conflict_dir / "runner-result.json").read_bytes() == original
    assert list(tmp_path.rglob(".rr-*")) == []


def test_parallel_aggregate_converts_to_legacy_review_verdict() -> None:
    snapshot = _snapshot("parallel-legacy-verdict-run")
    plan = _plan(snapshot)
    finding = build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity="major",
        category="correctness",
        rule_id="wrong-result",
        path="src/example.py",
        location="line:12",
        title="返回值与合同不一致",
        evidence="第 12 行返回了错误状态。",
        recommendation="按公开合同返回 completed。",
    )
    result = build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=snapshot.run_id,
        iteration=snapshot.iteration,
        reviewer_role="correctness_reviewer",
        attempt_id="attempt-correctness",
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        execution_ref="reviews/correctness/execution.json",
        execution_sha256="6" * 64,
        status="completed",
        verdict="request_changes",
        summary="发现一个需要修复的正确性问题。",
        findings=[finding],
        checked_items=["正确性", "公开合同"],
    )
    aggregate = aggregate_parallel_reviews(
        ParallelReviewAggregationContext(
            run_id=snapshot.run_id,
            iteration=snapshot.iteration,
            evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
            review_plan=plan,
            verification_status="passed",
            verification_failed_count=0,
            risk="low",
        ),
        [result],
    )

    legacy = parallel_review_aggregate_to_legacy_verdict(
        aggregate,
        [result],
    )

    assert isinstance(legacy, ReviewVerdict)
    assert legacy.verdict == "request_changes"
    assert legacy.summary
    assert legacy.checked_items == [
        "correctness_reviewer: 正确性",
        "correctness_reviewer: 公开合同",
    ]
    assert len(legacy.findings) == 1
    assert legacy.findings[0].severity == "major"
    assert legacy.findings[0].file == "src/example.py"
    assert legacy.findings[0].line == 12
    assert legacy.findings[0].title == "返回值与合同不一致"
    assert legacy.findings[0].evidence == "第 12 行返回了错误状态。"


def test_legacy_dedup_uses_aggregate_severity_and_deterministic_text() -> None:
    snapshot = _snapshot("parallel-legacy-severity-run")
    plan = _plan(snapshot, topology="fixed_three")
    correctness_finding = build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity="minor",
        category="correctness",
        rule_id="shared-finding",
        path="src/example.py",
        location="line:21",
        title="确定性代表标题",
        evidence="来自 correctness reviewer 的代表文本。",
        recommendation="保留确定性文本选择。",
    )
    security_finding = build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity="blocker",
        category="correctness",
        rule_id="shared-finding",
        path="src/example.py",
        location="line:21",
        title="更高严重度角色的不同标题",
        evidence="来自 security reviewer 的不同文本。",
        recommendation="立即阻断。",
    )
    role_findings = {
        "correctness_reviewer": [correctness_finding],
        "verification_adequacy_reviewer": [],
        "security_design_reviewer": [security_finding],
    }
    results = [
        build_parallel_review_result(
            review_plan_id=plan.plan_id,
            run_id=snapshot.run_id,
            iteration=snapshot.iteration,
            reviewer_role=role,
            attempt_id=f"attempt-{role}",
            evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
            execution_ref=f"reviews/{role}/execution.json",
            execution_sha256=hashlib.sha256(role.encode()).hexdigest(),
            status="completed",
            verdict=(
                "approve"
                if role == "verification_adequacy_reviewer"
                else "request_changes"
            ),
            summary=f"{role} 已完成。",
            findings=role_findings[role],
            checked_items=[role],
        )
        for role in AVAILABLE_REVIEWER_ROLES
    ]
    aggregate = aggregate_parallel_reviews(
        ParallelReviewAggregationContext(
            run_id=snapshot.run_id,
            iteration=snapshot.iteration,
            evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
            review_plan=plan,
            verification_status="passed",
            verification_failed_count=0,
            risk="low",
        ),
        results,
    )

    legacy = parallel_review_aggregate_to_legacy_verdict(
        aggregate,
        results,
    )

    assert aggregate.findings[0].severity == "blocker"
    assert legacy.findings[0].severity == "blocker"
    assert legacy.findings[0].title == "确定性代表标题"
    assert (
        legacy.findings[0].evidence
        == "来自 correctness reviewer 的代表文本。"
    )


def _compatibility_source(
    tmp_path: Path,
    *,
    runner_status: str = "success",
) -> tuple[
    Path,
    Path,
    Path,
    ParallelReviewAggregate,
    tuple[ParallelReviewResult, ...],
]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    repo.joinpath("README.md").write_text(
        "# Parallel review fixture\n\ncompatibility change\n",
        encoding="utf-8",
        newline="\n",
    )
    reflect_run = ReflectRuntime(workspace).run(repo)
    source_artifacts = {
        "project-policy-snapshot.json": (
            '{"reviewer_policy":"read-only","version":1}\n'
        ),
        "iterations/01/verification-result.json": (
            '{"status":"passed","failed_count":0}\n'
        ),
        "iterations/01/risk-gate-result.json": '{"risk":"low"}\n',
        "iterations/01/acceptance-evidence.json": (
            '{"items":["compatibility source"]}\n'
        ),
    }
    for artifact_ref, content in source_artifacts.items():
        artifact_path = reflect_run.joinpath(*artifact_ref.split("/"))
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            content,
            encoding="utf-8",
            newline="\n",
        )
    snapshot = review_runtime.build_review_evidence_snapshot_from_artifacts(
        reflect_run,
        iteration=1,
        workspace_fingerprint=(
            "sha256:" + capture_review_workspace(repo).fingerprint
        ),
        policy_snapshot_ref="project-policy-snapshot.json",
        verification_result_ref="iterations/01/verification-result.json",
        risk_result_ref="iterations/01/risk-gate-result.json",
        acceptance_evidence_manifest_ref=(
            "iterations/01/acceptance-evidence.json"
        ),
    )
    plan = _plan(snapshot)
    write_parallel_review_plan(reflect_run, plan)
    runner = RecordingRunner(
        status=runner_status,
        output=_valid_review_output(plan),
        error=(
            None
            if runner_status == "success"
            else "injected compatibility provider error"
        ),
    )
    executor = RunnerParallelReviewerExecutor(
        reviewer_role="correctness_reviewer",
        repo_path=repo,
        runner=runner,
        evidence=_prepared_evidence(snapshot),
        timeout_seconds=37,
    )
    result_ref = executor(
        run_dir=reflect_run,
        plan=plan,
        reviewer_role="correctness_reviewer",
    )
    result = read_parallel_review_result(reflect_run, result_ref)
    aggregate = aggregate_parallel_reviews(
        ParallelReviewAggregationContext(
            run_id=plan.run_id,
            iteration=plan.iteration,
            evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
            review_plan=plan,
            verification_status="passed",
            verification_failed_count=0,
            risk="low",
        ),
        [result],
    )
    write_parallel_review_aggregate(reflect_run, aggregate)
    return workspace, repo, reflect_run, aggregate, (result,)


def test_compatibility_run_is_consumable_by_legacy_readers(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(tmp_path)
    compatibility_run = workspace / "runs" / "parallel-compat-review"

    write_parallel_review_compatibility_run(
        compatibility_run,
        repo_path=repo,
        source_run=reflect_run.name,
        aggregate=aggregate,
        results=results,
    )
    replayed = write_parallel_review_compatibility_run(
        compatibility_run,
        repo_path=repo,
        source_run=reflect_run.name,
        aggregate=aggregate,
        results=results,
    )

    state = json.loads(
        compatibility_run.joinpath("state.json").read_text(encoding="utf-8")
    )
    eval_results = run_review_pack_eval(
        compatibility_run,
        REVIEW_ARTIFACTS,
    )
    freshness = validate_review_evidence_freshness(
        workspace,
        repo,
        compatibility_run.name,
    )
    iteration_dir = tmp_path / "iteration"
    iteration_dir.mkdir()
    for source_name, local_name in (
        ("state.json", "review-state.json"),
        ("review-context.json", "review-context.json"),
        ("review-verdict.json", "review-verdict.json"),
        ("acceptance-evidence.json", "acceptance-evidence.json"),
    ):
        iteration_dir.joinpath(local_name).write_bytes(
            compatibility_run.joinpath(source_name).read_bytes()
        )
    issues: list[str] = []
    verdicts: list[ReviewVerdict] = []
    _validate_iteration_review(
        workspace,
        repo,
        iteration_dir,
        LoopIterationState(
            iteration=1,
            reviewer_status="success",
            reflect_run=reflect_run.name,
            review_run=compatibility_run.name,
            verdict="approve",
            findings_count=0,
        ),
        issues,
        verdicts,
    )

    assert replayed == compatibility_run.resolve()
    assert state["status"] == "success"
    assert state["runner_status"] == "success"
    assert state["artifacts"] == REVIEW_ARTIFACTS
    assert not any(item.startswith("FAIL:") for item in eval_results)
    assert freshness.fresh is True, freshness.issues
    assert issues == []
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "approve"


def test_compatibility_readers_reject_forged_provider_error_approval(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(
        tmp_path,
        runner_status="error",
    )
    compatibility_run = workspace / "runs" / "parallel-forged-review"
    write_parallel_review_compatibility_run(
        compatibility_run,
        repo_path=repo,
        source_run=reflect_run.name,
        aggregate=aggregate,
        results=results,
    )

    state_path = compatibility_run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "status": "success",
            "current_step": "done",
            "verdict": "approve",
            "runner_status": "success",
        }
    )
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    verdict_path = compatibility_run / "review-verdict.json"
    verdict_path.write_text(
        ReviewVerdict(
            verdict="approve",
            summary="forged approval",
            findings=[],
            checked_items=["forged"],
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    freshness = validate_review_evidence_freshness(
        workspace,
        repo,
        compatibility_run.name,
    )
    eval_results = run_review_pack_eval(
        compatibility_run,
        REVIEW_ARTIFACTS,
    )
    iteration_dir = tmp_path / "forged-iteration"
    iteration_dir.mkdir()
    for source_name, local_name in (
        ("state.json", "review-state.json"),
        ("review-context.json", "review-context.json"),
        ("review-verdict.json", "review-verdict.json"),
        ("acceptance-evidence.json", "acceptance-evidence.json"),
    ):
        iteration_dir.joinpath(local_name).write_bytes(
            compatibility_run.joinpath(source_name).read_bytes()
        )
    issues: list[str] = []
    verdicts: list[ReviewVerdict] = []
    _validate_iteration_review(
        workspace,
        repo,
        iteration_dir,
        LoopIterationState(
            iteration=1,
            reviewer_status="success",
            reflect_run=reflect_run.name,
            review_run=compatibility_run.name,
            verdict="approve",
            findings_count=0,
        ),
        issues,
        verdicts,
    )

    assert freshness.fresh is False
    assert "parallel_review_legacy_verdict_mismatch" in freshness.issues
    assert "parallel_review_state_verdict_mismatch" in freshness.issues
    assert "parallel_review_runner_status_mismatch" in freshness.issues
    assert any(
        item
        == "FAIL: parallel review source evidence 不可信"
        for item in eval_results
    )
    assert (
        "iteration_01_parallel_review_legacy_verdict_mismatch"
        in issues
    )
    assert verdicts == []


def test_compatibility_eval_rejects_invalid_state_without_type_downgrade(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(tmp_path)
    compatibility_run = workspace / "runs" / "parallel-invalid-state"
    write_parallel_review_compatibility_run(
        compatibility_run,
        repo_path=repo,
        source_run=reflect_run.name,
        aggregate=aggregate,
        results=results,
    )
    compatibility_run.joinpath("state.json").write_text(
        '{"runner":',
        encoding="utf-8",
        newline="\n",
    )

    eval_results = run_review_pack_eval(
        compatibility_run,
        REVIEW_ARTIFACTS,
    )

    assert "FAIL: parallel review source evidence 不可信" in eval_results
    assert (
        "FAIL: parallel review source issue：parallel_review_state_invalid"
        in eval_results
    )


def test_compatibility_markers_cannot_be_removed_to_downgrade_review_type(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(
        tmp_path,
        runner_status="error",
    )
    compatibility_run = workspace / "runs" / "parallel-type-downgrade"
    write_parallel_review_compatibility_run(
        compatibility_run,
        repo_path=repo,
        source_run=reflect_run.name,
        aggregate=aggregate,
        results=results,
    )

    state_path = compatibility_run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "runner": "codex-exec",
            "status": "success",
            "current_step": "done",
            "verdict": "approve",
            "runner_status": "success",
        }
    )
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    compatibility_run.joinpath("review-verdict.json").write_text(
        ReviewVerdict(
            verdict="approve",
            summary="forged ordinary review",
            findings=[],
            checked_items=["forged"],
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    context_path = compatibility_run / "review-context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context.pop("parallel_review_source")
    context["acceptance_evidence"]["items"][0][
        "source_kind"
    ] = "ordinary_review_output"
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    acceptance_path = compatibility_run / "acceptance-evidence.json"
    acceptance_path.write_text(
        '{"items":',
        encoding="utf-8",
        newline="\n",
    )

    freshness = validate_review_evidence_freshness(
        workspace,
        repo,
        compatibility_run.name,
    )
    eval_results = run_review_pack_eval(
        compatibility_run,
        REVIEW_ARTIFACTS,
    )
    iteration_dir = tmp_path / "downgraded-iteration"
    iteration_dir.mkdir()
    for source_name, local_name in (
        ("state.json", "review-state.json"),
        ("review-context.json", "review-context.json"),
        ("review-verdict.json", "review-verdict.json"),
        ("acceptance-evidence.json", "acceptance-evidence.json"),
    ):
        iteration_dir.joinpath(local_name).write_bytes(
            compatibility_run.joinpath(source_name).read_bytes()
        )
    issues: list[str] = []
    verdicts: list[ReviewVerdict] = []
    _validate_iteration_review(
        workspace,
        repo,
        iteration_dir,
        LoopIterationState(
            iteration=1,
            reviewer_status="success",
            reflect_run=reflect_run.name,
            review_run=compatibility_run.name,
            verdict="approve",
            findings_count=0,
        ),
        issues,
        verdicts,
    )

    assert freshness.fresh is False
    assert "parallel_review_binding_invalid" in freshness.issues
    assert (
        "FAIL: parallel review source issue：parallel_review_binding_invalid"
        in eval_results
    )
    assert "iteration_01_parallel_review_binding_invalid" in issues
    assert verdicts == []


def test_goal_review_attachment_rejects_compatibility_source_redirect(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(tmp_path)
    compatibility_run = workspace / "runs" / "parallel-goal-binding"
    write_parallel_review_compatibility_run(
        compatibility_run,
        repo_path=repo,
        source_run=reflect_run.name,
        aggregate=aggregate,
        results=results,
    )
    attached = validate_goal_evidence(
        workspace,
        repo,
        compatibility_run.name,
        "review",
        None,
    )
    legacy_attachment = attached.model_copy(
        update={
            "review_source_run": None,
            "review_kind": None,
            "review_binding_sha256": None,
        }
    )
    with pytest.raises(
        ValueError,
        match="历史 Review Goal attachment 缺少外部绑定",
    ):
        validate_goal_evidence(
            workspace,
            repo,
            compatibility_run.name,
            "review",
            None,
            expected_ref=legacy_attachment,
        )
    other_reflect = ReflectRuntime(workspace).run(repo)

    state_path = compatibility_run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "source_run": other_reflect.name,
            "runner": "codex-exec",
            "status": "success",
            "current_step": "done",
            "verdict": "approve",
            "runner_status": "success",
        }
    )
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    context_path = compatibility_run / "review-context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["source_run"] = other_reflect.name
    context["source_run_dir"] = str(other_reflect)
    context["risk_gate"]["source_run"] = other_reflect.name
    context.pop("parallel_review_source")
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    compatibility_run.joinpath("review-verdict.json").write_text(
        ReviewVerdict(
            verdict="approve",
            summary="redirected review source",
            findings=[],
            checked_items=["redirected"],
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ValueError,
        match="source_run 已发生变化",
    ):
        validate_goal_evidence(
            workspace,
            repo,
            compatibility_run.name,
            "review",
            None,
            expected_ref=attached,
        )


def test_compatibility_run_rejects_cross_run_expected_artifacts(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        _,
        aggregate,
        results,
    ) = _compatibility_source(tmp_path)
    other_reflect_run = ReflectRuntime(workspace).run(repo)

    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="不属于指定 source run",
    ):
        write_parallel_review_compatibility_run(
            workspace / "runs" / "parallel-cross-run",
            repo_path=repo,
            source_run=other_reflect_run.name,
            aggregate=aggregate,
            results=results,
        )


def test_compatibility_run_rejects_stale_evidence_snapshot(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(tmp_path)
    reflect_run.joinpath(
        "iterations/01/verification-result.json"
    ).write_text(
        '{"status":"failed","failed_count":1}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="evidence snapshot 已过期",
    ):
        write_parallel_review_compatibility_run(
            workspace / "runs" / "parallel-stale-snapshot",
            repo_path=repo,
            source_run=reflect_run.name,
            aggregate=aggregate,
            results=results,
        )


def test_compatibility_run_rejects_process_output_hash_drift(
    tmp_path: Path,
) -> None:
    (
        workspace,
        repo,
        reflect_run,
        aggregate,
        results,
    ) = _compatibility_source(tmp_path)
    execution_ref = results[0].execution_ref
    process_output_path = reflect_run.joinpath(
        *Path(execution_ref).with_name("process-output.txt").parts
    )
    process_output_path.write_text(
        "tampered\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewRuntimeValidationError,
        match="source artifact 证据链不可信",
    ):
        write_parallel_review_compatibility_run(
            workspace / "runs" / "parallel-hash-drift",
            repo_path=repo,
            source_run=reflect_run.name,
            aggregate=aggregate,
            results=results,
        )
