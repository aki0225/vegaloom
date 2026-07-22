from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vega.execution_control import ExecutionLease
from vega.loop_step_result import (
    AttemptIdentity,
    StepResultOutcome,
    StepResultOutputRef,
    StepResultValidationError,
    build_step_result,
    hash_command,
    read_step_result,
    write_step_result,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_terminal_execution(
    run_dir: Path,
    attempt: AttemptIdentity,
) -> Path:
    execution_path = run_dir / "iterations" / "01" / "executions" / "worker" / "execution.json"
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    lease = ExecutionLease(
        run_id=run_dir.name,
        step="worker",
        iteration=1,
        engine="langgraph",
        graph_schema_version=attempt.graph_schema_version,
        step_id=attempt.step_id,
        attempt_id=attempt.attempt_id,
        idempotency_key=attempt.idempotency_key,
        replay_class=attempt.replay_class,
        runner_identity=attempt.runner_identity,
        base_head=attempt.base_head,
        before_workspace_fingerprint=attempt.before_workspace_fingerprint,
        policy_snapshot_sha256=attempt.policy_snapshot_sha256,
        input_fingerprint=attempt.input_fingerprint,
        command_sha256=attempt.command_sha256,
        owner_pid=999_999,
        command=["gate3-worker"],
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(seconds=10)).isoformat(),
        deadline=(now + timedelta(seconds=60)).isoformat(),
        status="completed",
        returncode=0,
        finished_at=now.isoformat(),
    )
    execution_path.write_text(
        json.dumps(lease.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return execution_path


def _attempt(run_dir: Path) -> AttemptIdentity:
    return AttemptIdentity(
        run_id=run_dir.name,
        engine="langgraph",
        graph_schema_version="checkpoint-v1",
        step_id="worker-iteration-01",
        step_name="worker",
        iteration=1,
        attempt_id="attempt-gate3",
        idempotency_key="sha256:" + "1" * 64,
        replay_class="external_non_replayable",
        runner_identity={"kind": "test-runner"},
        base_head="a" * 40,
        before_workspace_fingerprint="sha256:" + "2" * 64,
        policy_snapshot_sha256="3" * 64,
        command_sha256=hash_command(["gate3-worker"]),
        input_fingerprint="sha256:" + "5" * 64,
        started_at=datetime.now(UTC).isoformat(),
    )


def _build_valid_manifest(run_dir: Path):
    attempt = _attempt(run_dir)
    attempt_path = (
        run_dir
        / "iterations"
        / "01"
        / "executions"
        / "worker"
        / "attempt.json"
    )
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    attempt_path.write_text(
        attempt.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    execution_path = _write_terminal_execution(run_dir, attempt)
    output_path = run_dir / "iterations" / "01" / "worker-output.txt"
    output_path.write_text("worker completed\n", encoding="utf-8", newline="\n")
    workspace_path = run_dir / "iterations" / "01" / "workspace-after-worker.json"
    workspace_path.write_text(
        json.dumps(
            {
                "fingerprint": "sha256:" + "6" * 64,
                "head_sha": "a" * 40,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return build_step_result(
        run_dir,
        attempt=attempt,
        after_workspace_fingerprint="sha256:" + "6" * 64,
        execution_ref=execution_path.relative_to(run_dir).as_posix(),
        output_refs=[
            StepResultOutputRef(
                path=output_path.relative_to(run_dir).as_posix(),
                sha256=_sha256(output_path),
            ),
            StepResultOutputRef(
                path=workspace_path.relative_to(run_dir).as_posix(),
                sha256=_sha256(workspace_path),
            ),
        ],
        outcome=StepResultOutcome(
            status="success",
            summary="worker 产生了可验证的 tracked diff",
        ),
    )


def test_step_result_is_content_addressed_and_can_be_read_back(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir)

    path = write_step_result(run_dir, manifest)
    loaded = read_step_result(run_dir, "worker-iteration-01")

    assert path == run_dir / "step-results" / "worker-iteration-01.json"
    assert loaded == manifest
    assert loaded.step_result_id.startswith("sha256:")
    assert loaded.execution_sha256 == _sha256(
        run_dir / loaded.execution_ref
    )


def test_step_result_refuses_overwrite_with_different_content(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir)
    write_step_result(run_dir, manifest)
    changed = manifest.model_copy(
        update={
            "result": StepResultOutcome(
                status="success",
                summary="不同结果",
            )
        }
    )

    with pytest.raises(StepResultValidationError, match="不得覆盖"):
        write_step_result(run_dir, changed)


def test_step_result_detects_execution_tampering(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir)
    write_step_result(run_dir, manifest)
    execution_path = run_dir / manifest.execution_ref
    execution_path.write_text("{}\n", encoding="utf-8", newline="\n")

    with pytest.raises(StepResultValidationError, match="execution hash"):
        read_step_result(run_dir, manifest.step_id)


def test_step_result_rejects_filename_step_id_mismatch(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir)
    forged_path = run_dir / "step-results" / "forged-alias.json"
    forged_path.parent.mkdir(parents=True)
    forged_path.write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        StepResultValidationError,
        match="文件名与 manifest.step_id",
    ):
        read_step_result(run_dir, "forged-alias")


def test_step_result_rejects_self_consistent_attempt_identity_drift(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir)
    execution_path = run_dir / manifest.execution_ref
    execution = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    drifted_idempotency_key = "sha256:" + "9" * 64
    execution.idempotency_key = drifted_idempotency_key
    execution_path.write_text(
        execution.model_dump_json(indent=2),
        encoding="utf-8",
        newline="\n",
    )
    drifted = manifest.model_copy(
        update={
            "idempotency_key": drifted_idempotency_key,
            "execution_sha256": _sha256(execution_path),
        }
    )

    with pytest.raises(
        StepResultValidationError,
        match="attempt idempotency_key",
    ):
        write_step_result(run_dir, drifted)


def test_step_result_rejects_ref_outside_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir)
    escaped = manifest.model_copy(
        update={
            "output_refs": [
                StepResultOutputRef(
                    path="../outside.txt",
                    sha256="7" * 64,
                )
            ]
        }
    )

    with pytest.raises(StepResultValidationError, match="不能越过 run 目录"):
        write_step_result(run_dir, escaped)


def test_step_result_rejects_sensitive_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _build_valid_manifest(run_dir).model_copy(
        update={
            "result": StepResultOutcome(
                status="success",
                summary="Authorization: Bearer SHOULD_NOT_PERSIST",
            )
        }
    )

    with pytest.raises(StepResultValidationError, match="敏感标记"):
        write_step_result(run_dir, manifest)
