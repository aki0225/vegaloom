from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import vega.parallel_review_artifacts as review_artifacts
from vega.execution_control import ExecutionLease
from vega.loop_step_result import hash_command
from vega.parallel_review import (
    PARALLEL_REVIEW_PROMPT_VERSION,
    ParallelReviewAggregationContext,
    ParallelReviewResult,
    ParallelReviewRoutingContext,
    ReviewEvidenceSnapshot,
    ReviewerRole,
    aggregate_parallel_reviews,
    build_parallel_review_attempt_identity,
    build_parallel_review_plan,
    build_parallel_review_result,
)
from vega.parallel_review_artifacts import (
    ParallelReviewArtifactValidationError,
    build_review_evidence_snapshot_from_artifacts,
    list_parallel_review_result_refs,
    parallel_review_execution_artifact_ref,
    parallel_review_result_pointer_artifact_ref,
    prepare_parallel_review_execution_path,
    read_parallel_review_aggregate,
    read_parallel_review_plan,
    read_parallel_review_result,
    sha256_parallel_review_artifact,
    write_parallel_review_aggregate,
    write_parallel_review_execution,
    write_parallel_review_plan,
    write_parallel_review_process_output,
    write_parallel_review_public_evidence,
    write_parallel_review_result,
    write_parallel_review_role_prompt,
)


def _create_run(tmp_path: Path) -> tuple[Path, ReviewEvidenceSnapshot]:
    run_dir = tmp_path / "runs" / "gate5-run"
    iteration_dir = run_dir / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    artifacts = {
        "project-policy-snapshot.json": "{}\n",
        "iterations/01/verification-result.json": '{"status":"passed"}\n',
        "iterations/01/risk-gate-result.json": '{"risk":"low"}\n',
        "iterations/01/acceptance-evidence.json": '{"items":[]}\n',
    }
    for ref, content in artifacts.items():
        path = run_dir.joinpath(*ref.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    snapshot = build_review_evidence_snapshot_from_artifacts(
        run_dir,
        iteration=1,
        workspace_fingerprint="sha256:" + "1" * 64,
        policy_snapshot_ref="project-policy-snapshot.json",
        verification_result_ref="iterations/01/verification-result.json",
        risk_result_ref="iterations/01/risk-gate-result.json",
        acceptance_evidence_manifest_ref=(
            "iterations/01/acceptance-evidence.json"
        ),
    )
    return run_dir, snapshot


def _plan(
    run_dir: Path,
    snapshot: ReviewEvidenceSnapshot,
    *,
    topology: str = "adaptive",
):
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
            "changed_files": ["src/example.py"],
            "gate_reason_codes": [],
        }
    )
    return build_parallel_review_plan(
        routing,
        topology=topology,  # type: ignore[arg-type]
    )


def _execution(
    run_dir: Path,
    plan,
    *,
    role: ReviewerRole = "correctness_reviewer",
    attempt_id: str | None = None,
    status: str = "completed",
    termination_unconfirmed: bool = False,
) -> tuple[str, ExecutionLease]:
    attempt = None
    if role in plan.required_roles:
        public_evidence = (
            "# Gate 5 artifact test evidence\n\n"
            f"- plan: `{plan.plan_id}`\n"
        )
        _, public_evidence_sha256 = write_parallel_review_public_evidence(
            run_dir,
            plan,
            public_evidence,
        )
        role_prompt = (
            "# Gate 5 artifact test role prompt\n\n"
            f"- prompt_version: `{PARALLEL_REVIEW_PROMPT_VERSION}`\n"
            f"- reviewer_role: `{role}`\n"
        )
        _, role_prompt_sha256 = write_parallel_review_role_prompt(
            run_dir,
            plan,
            reviewer_role=role,
            content=role_prompt,
        )
        attempt = build_parallel_review_attempt_identity(
            plan,
            reviewer_role=role,
            public_evidence_sha256=public_evidence_sha256,
            role_prompt_sha256=role_prompt_sha256,
        )
    selected_attempt_id = (
        attempt.attempt_id
        if attempt is not None
        else (attempt_id or f"attempt-{role}")
    )
    if (
        attempt is not None
        and attempt_id is not None
        and attempt_id != selected_attempt_id
    ):
        raise ValueError("测试 attempt_id 与实际 evidence/prompt identity 不一致")
    execution_ref = parallel_review_execution_artifact_ref(
        plan,
        reviewer_role=role,
        attempt_id=selected_attempt_id,
    )
    _, process_output_sha256, process_output_bytes = (
        write_parallel_review_process_output(
            run_dir,
            execution_ref=execution_ref,
            content=f"{role} artifact fixture output",
        )
    )
    timestamp = "2026-07-17T00:00:00+00:00"
    command = ["vega-fake-reviewer", role]
    lease = ExecutionLease(
        run_id=run_dir.name,
        step="reviewer",
        iteration=1,
        engine="langgraph",
        graph_schema_version="checkpoint-v1",
        step_id=(
            attempt.step_id if attempt is not None else f"review-{role}"
        ),
        attempt_id=selected_attempt_id,
        idempotency_key=(
            attempt.idempotency_key
            if attempt is not None
            else "sha256:" + "7" * 64
        ),
        replay_class="read_only_replayable",
        runner_identity={
            "kind": "deterministic-fake-reviewer",
            "role": role,
            **(
                {
                    "prompt_version": PARALLEL_REVIEW_PROMPT_VERSION,
                    "public_evidence_sha256": (
                        attempt.public_evidence_sha256
                    ),
                    "role_prompt_sha256": attempt.role_prompt_sha256,
                }
                if attempt is not None
                else {}
            ),
        },
        base_head="8" * 40,
        before_workspace_fingerprint="sha256:" + "1" * 64,
        policy_snapshot_sha256="2" * 64,
        input_fingerprint=(
            attempt.input_fingerprint
            if attempt is not None
            else "sha256:" + "3" * 64
        ),
        command_sha256=hash_command(command),
        process_output_sha256=process_output_sha256,
        process_output_bytes=process_output_bytes,
        owner_pid=max(1, os.getpid()),
        command=command,
        started_at=timestamp,
        last_heartbeat=timestamp,
        lease_expires_at=timestamp,
        deadline=timestamp,
        status=status,  # type: ignore[arg-type]
        termination_unconfirmed=termination_unconfirmed,
        returncode=0 if status == "completed" else None,
        finished_at=(
            timestamp
            if status in {"completed", "failed", "timed_out", "stopped"}
            else None
        ),
    )
    return execution_ref, lease


def _result(
    run_dir: Path,
    plan,
    *,
    role: str = "correctness_reviewer",
    status: str = "completed",
    verdict: str = "approve",
    summary: str = "fake reviewer 完成确定性审查。",
) -> ParallelReviewResult:
    execution_status = {
        "completed": "completed",
        "timed_out": "timed_out",
        "stopped": "stopped",
        "provider_error": "failed",
        "parse_error": "failed",
        "active": "running",
        "termination_unconfirmed": "running",
    }[status]
    execution_ref, execution = _execution(
        run_dir,
        plan,
        role=role,
        status=execution_status,
        termination_unconfirmed=status == "termination_unconfirmed",
    )
    execution_path = write_parallel_review_execution(
        run_dir,
        execution_ref=execution_ref,
        execution=execution,
    )
    return build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=run_dir.name,
        iteration=1,
        reviewer_role=role,  # type: ignore[arg-type]
        attempt_id=str(execution.attempt_id),
        evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
        execution_ref=execution_ref,
        execution_sha256=hashlib.sha256(
            execution_path.read_bytes()
        ).hexdigest(),
        status=status,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        summary=summary,
        checked_items=["公共 evidence snapshot"],
    )


def _aggregate_context(run_dir: Path, plan):
    return ParallelReviewAggregationContext(
        run_id=run_dir.name,
        iteration=1,
        evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
        review_plan=plan,
        verification_status="passed",
        verification_failed_count=0,
        risk="low",
    )


def test_snapshot_hashes_real_run_artifacts_and_changes_after_tamper(
    tmp_path: Path,
) -> None:
    run_dir, first = _create_run(tmp_path)
    verification_path = (
        run_dir / "iterations" / "01" / "verification-result.json"
    )

    verification_path.write_text(
        '{"status":"failed"}\n',
        encoding="utf-8",
        newline="\n",
    )
    second = build_review_evidence_snapshot_from_artifacts(
        run_dir,
        iteration=1,
        workspace_fingerprint=first.workspace_fingerprint,
        policy_snapshot_ref="project-policy-snapshot.json",
        verification_result_ref="iterations/01/verification-result.json",
        risk_result_ref="iterations/01/risk-gate-result.json",
        acceptance_evidence_manifest_ref=(
            "iterations/01/acceptance-evidence.json"
        ),
    )

    assert first.verification_result_sha256 != second.verification_result_sha256
    assert first.evidence_snapshot_sha256 != second.evidence_snapshot_sha256


def test_snapshot_rejects_ref_outside_run(tmp_path: Path) -> None:
    run_dir, _ = _create_run(tmp_path)

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="不能越过 run 目录",
    ):
        build_review_evidence_snapshot_from_artifacts(
            run_dir,
            iteration=1,
            workspace_fingerprint="sha256:" + "1" * 64,
            policy_snapshot_ref="../policy.json",
            verification_result_ref="iterations/01/verification-result.json",
            risk_result_ref="iterations/01/risk-gate-result.json",
            acceptance_evidence_manifest_ref=(
                "iterations/01/acceptance-evidence.json"
            ),
        )


def test_plan_artifact_is_content_addressed_append_only_and_idempotent(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)

    first_path = write_parallel_review_plan(run_dir, plan)
    first_hash = hashlib.sha256(first_path.read_bytes()).hexdigest()
    second_path = write_parallel_review_plan(run_dir, plan)

    assert first_path == second_path
    assert read_parallel_review_plan(
        run_dir,
        iteration=1,
        plan_id=plan.plan_id,
    ) == plan
    assert hashlib.sha256(second_path.read_bytes()).hexdigest() == first_hash


def test_plan_artifact_tamper_is_rejected(tmp_path: Path) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    path = write_parallel_review_plan(run_dir, plan)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["role_reasons"]["correctness_reviewer"] = ["policy:tampered"]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="identity",
    ):
        read_parallel_review_plan(
            run_dir,
            iteration=1,
            plan_id=plan.plan_id,
        )


def test_result_ref_is_bound_to_actual_artifact_execution_and_plan(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result = _result(run_dir, plan)

    result_ref = write_parallel_review_result(run_dir, result)
    loaded = read_parallel_review_result(run_dir, result_ref)

    assert loaded == result
    assert result_ref.result_id == result.result_id
    assert result_ref.review_plan_id == plan.plan_id
    assert result_ref.artifact_sha256 == sha256_parallel_review_artifact(
        run_dir,
        result_ref.artifact_ref,
    )


def test_result_artifact_tamper_is_rejected_by_narrow_ref(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result_ref = write_parallel_review_result(
        run_dir,
        _result(run_dir, plan),
    )
    result_path = run_dir.joinpath(*result_ref.artifact_ref.split("/"))
    result_path.write_text(
        result_path.read_text(encoding="utf-8").replace(
            "fake reviewer",
            "tampered reviewer",
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="artifact hash",
    ):
        read_parallel_review_result(run_dir, result_ref)


def test_execution_tamper_invalidates_result_even_when_result_file_is_unchanged(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result = _result(run_dir, plan)
    result_ref = write_parallel_review_result(run_dir, result)
    execution_path = run_dir.joinpath(*result.execution_ref.split("/"))
    payload = json.loads(execution_path.read_text(encoding="utf-8"))
    payload["reason"] = "tampered"
    execution_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="execution hash",
    ):
        read_parallel_review_result(run_dir, result_ref)


def test_result_for_role_outside_plan_is_rejected(tmp_path: Path) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    adaptive_plan = _plan(run_dir, snapshot)
    fixed_plan = _plan(run_dir, snapshot, topology="fixed_three")
    write_parallel_review_plan(run_dir, adaptive_plan)
    execution_ref, execution = _execution(
        run_dir,
        adaptive_plan,
        role="security_design_reviewer",
    )
    execution_path = write_parallel_review_execution(
        run_dir,
        execution_ref=execution_ref,
        execution=execution,
    )
    result = build_parallel_review_result(
        review_plan_id=adaptive_plan.plan_id,
        run_id=run_dir.name,
        iteration=1,
        reviewer_role="security_design_reviewer",
        attempt_id="attempt-security_design_reviewer",
        evidence_snapshot_sha256=adaptive_plan.evidence_snapshot_sha256,
        execution_ref=execution_ref,
        execution_sha256=hashlib.sha256(
            execution_path.read_bytes()
        ).hexdigest(),
        status="completed",
        verdict="approve",
        summary="计划外 reviewer。",
    )

    assert fixed_plan.plan_id != adaptive_plan.plan_id
    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="必需角色",
    ):
        write_parallel_review_result(run_dir, result)


def test_aggregate_artifact_is_rebuildable_and_hash_checked(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result = _result(run_dir, plan)
    result_ref = write_parallel_review_result(run_dir, result)
    loaded_result = read_parallel_review_result(run_dir, result_ref)
    aggregate = aggregate_parallel_reviews(
        _aggregate_context(run_dir, plan),
        [loaded_result],
    )

    path, artifact_sha256 = write_parallel_review_aggregate(
        run_dir,
        aggregate,
    )
    loaded = read_parallel_review_aggregate(
        run_dir,
        iteration=1,
        plan_id=plan.plan_id,
        artifact_sha256=artifact_sha256,
    )

    assert loaded == aggregate
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact_sha256

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"verdict": "approve"',
            '"verdict": "needs_human"',
        ),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="aggregate artifact hash",
    ):
        read_parallel_review_aggregate(
            run_dir,
            iteration=1,
            plan_id=plan.plan_id,
            artifact_sha256=artifact_sha256,
        )


def test_create_once_publish_failure_does_not_leave_partial_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    expected_ref = review_artifacts.parallel_review_plan_artifact_ref(plan)
    expected_path = run_dir.joinpath(*expected_ref.split("/"))
    monkeypatch.setattr(
        review_artifacts.os,
        "link",
        lambda *_: (_ for _ in ()).throw(PermissionError("blocked")),
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="独占发布",
    ):
        write_parallel_review_plan(run_dir, plan)

    assert not expected_path.exists()
    assert list(expected_path.parent.glob(".tmp-*")) == []


def test_result_output_directory_reparse_point_is_rejected(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    iteration_dir = run_dir / "iterations" / "01"
    target = run_dir / "parallel-review-target"
    target.mkdir()
    _create_directory_link_or_skip(
        target,
        iteration_dir / "parallel-reviews",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="reparse point",
    ):
        write_parallel_review_plan(run_dir, plan)


def test_real_execution_attempt_directory_reparse_point_is_rejected(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    execution_ref = parallel_review_execution_artifact_ref(
        plan,
        reviewer_role="correctness_reviewer",
        attempt_id="attempt-reparse-probe",
    )
    execution_path = run_dir.joinpath(*execution_ref.split("/"))
    target = run_dir / "execution-target"
    target.mkdir()
    execution_path.parent.parent.mkdir(parents=True, exist_ok=True)
    _create_directory_link_or_skip(target, execution_path.parent)

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="reparse point",
    ):
        prepare_parallel_review_execution_path(
            run_dir,
            execution_ref=execution_ref,
        )


def test_duplicate_json_fields_are_rejected(tmp_path: Path) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    path = write_parallel_review_plan(run_dir, plan)
    raw = path.read_text(encoding="utf-8")
    path.write_text(
        raw.replace(
            '  "run_id": ',
            '  "run_id": "shadow-run",\n  "run_id": ',
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="重复字段",
    ):
        read_parallel_review_plan(
            run_dir,
            iteration=1,
            plan_id=plan.plan_id,
        )


def test_existing_result_identity_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result = _result(run_dir, plan)
    result_ref = write_parallel_review_result(run_dir, result)
    path = run_dir.joinpath(*result_ref.artifact_ref.split("/"))
    original = path.read_bytes()

    # Pydantic model_copy 可绕过 validator；写入边界必须重新校验并拒绝同 identity 的不同内容。
    tampered = result.model_copy(
        update={"summary": "同 identity 的不同内容"}
    )
    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="已存在且内容不同",
    ):
        write_parallel_review_result(run_dir, tampered)

    assert path.read_bytes() == original


def test_result_ref_mapping_is_revalidated_after_checkpoint_roundtrip(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result_ref = write_parallel_review_result(
        run_dir,
        _result(run_dir, plan),
    )
    checkpoint_payload = deepcopy(result_ref.model_dump(mode="json"))

    assert read_parallel_review_result(
        run_dir,
        checkpoint_payload,
    ).result_id == result_ref.result_id

    checkpoint_payload["artifact_sha256"] = "0" * 64
    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="artifact hash",
    ):
        read_parallel_review_result(run_dir, checkpoint_payload)


def test_result_rejects_noncanonical_execution_ref(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result = _result(run_dir, plan)
    execution_path = run_dir.joinpath(*result.execution_ref.split("/"))
    forged_ref = "forged/execution.json"
    forged_path = run_dir.joinpath(*forged_ref.split("/"))
    forged_path.parent.mkdir(parents=True)
    forged_path.write_bytes(execution_path.read_bytes())
    forged = build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=run_dir.name,
        iteration=1,
        reviewer_role=result.reviewer_role,
        attempt_id=result.attempt_id,
        evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
        execution_ref=forged_ref,
        execution_sha256=hashlib.sha256(
            forged_path.read_bytes()
        ).hexdigest(),
        status="completed",
        verdict="approve",
        summary="伪造 execution 路径。",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="规范化 attempt 路径",
    ):
        write_parallel_review_result(run_dir, forged)


def test_result_rejects_execution_not_bound_to_actual_role_prompt(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result = _result(run_dir, plan)
    execution_path = run_dir.joinpath(*result.execution_ref.split("/"))
    payload = json.loads(execution_path.read_text(encoding="utf-8"))
    payload["runner_identity"]["role_prompt_sha256"] = "0" * 64
    execution_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    forged = build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=run_dir.name,
        iteration=1,
        reviewer_role=result.reviewer_role,
        attempt_id=result.attempt_id,
        evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
        execution_ref=result.execution_ref,
        execution_sha256=hashlib.sha256(
            execution_path.read_bytes()
        ).hexdigest(),
        status="completed",
        verdict="approve",
        summary="伪造 prompt hash。",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="实际 evidence 与角色 prompt",
    ):
        write_parallel_review_result(run_dir, forged)


def test_active_execution_cannot_publish_terminal_result(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    active = _result(
        run_dir,
        plan,
        status="active",
        verdict="needs_human",
    )

    with pytest.raises(
        ParallelReviewArtifactValidationError,
        match="active Reviewer execution",
    ):
        write_parallel_review_result(run_dir, active)


def test_role_result_pointer_ignores_unclaimed_orphan_result_file(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    plan = _plan(run_dir, snapshot)
    write_parallel_review_plan(run_dir, plan)
    result_ref = write_parallel_review_result(
        run_dir,
        _result(run_dir, plan),
    )
    result_path = run_dir.joinpath(*result_ref.artifact_ref.split("/"))
    orphan_path = result_path.with_name("r-unclaimed-orphan.json")
    orphan_path.write_bytes(result_path.read_bytes())
    pointer_ref = parallel_review_result_pointer_artifact_ref(
        plan,
        result_ref.reviewer_role,
    )

    discovered = list_parallel_review_result_refs(run_dir, plan)

    assert discovered == (result_ref,)
    assert run_dir.joinpath(*pointer_ref.split("/")).is_file()


def _create_directory_link_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        if sys.platform != "win32":
            pytest.skip(f"当前平台不能创建目录 symlink：{exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(
                "当前平台不能创建目录 symlink 或 junction："
                f"{exc}; {result.stderr}"
            )
