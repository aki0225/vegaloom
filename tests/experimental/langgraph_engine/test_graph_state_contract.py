from __future__ import annotations

import subprocess
import sys
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import vega.loop_graph_state as loop_graph_state
from vega.execution_control import ExecutionLease
from vega.loop_step_result import hash_command
from vega.loop_graph_state import (
    GRAPH_ENGINE_LEGACY_VERSION,
    GRAPH_STATE_LEGACY_SCHEMA_VERSION,
    GRAPH_STATE_KEYS,
    GRAPH_STATE_MAX_BYTES,
    GraphStateValidationError,
    create_graph_state,
    merge_review_results_by_identity,
    refresh_graph_state,
    serialize_graph_state,
    validate_graph_state,
    write_graph_state,
)
from vega.models import LoopAutomationState
from vega.parallel_review import (
    PARALLEL_REVIEW_PROMPT_VERSION,
    ParallelReviewResultRef,
    ParallelReviewRoutingContext,
    build_parallel_review_attempt_identity,
    build_parallel_review_plan,
    build_parallel_review_result,
    build_review_evidence_snapshot,
)
from vega.parallel_review_artifacts import (
    parallel_review_execution_artifact_ref,
    write_parallel_review_execution,
    write_parallel_review_plan,
    write_parallel_review_process_output,
    write_parallel_review_public_evidence,
    write_parallel_review_result,
    write_parallel_review_role_prompt,
)


def _create_graph_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "gate2-graph-run"
    run_dir.mkdir(parents=True)
    LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        engine="langgraph",
        repo_path=str(tmp_path / "repo"),
        input_source="test",
        status="running",
        current_step="brief",
    ).save(run_dir / "state.json")
    run_dir.joinpath("project-policy-snapshot.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    run_dir.joinpath("loop-plan.md").write_text(
        "PROMPT_CANARY_DO_NOT_SERIALIZE\n",
        encoding="utf-8",
    )
    run_dir.joinpath("worker-output.txt").write_text(
        "WORKER_OUTPUT_CANARY_DO_NOT_SERIALIZE\n",
        encoding="utf-8",
    )
    return run_dir


def _write_trusted_pending_decision(run_dir: Path) -> str:
    identity = {
        "run_id": run_dir.name,
        "iteration": 1,
        "decision_type": "gate",
        "allowed_decisions": ["approved", "rejected"],
        "workspace_fingerprint": "sha256:" + "1" * 64,
        "policy_snapshot_sha256": "2" * 64,
        "policy_fingerprint": "3" * 64,
        "latest_step_result_id": "step-result-fixture",
        "verification_status": "passed",
        "verification_failed_count": 0,
        "evidence_refs": [],
        "reflect_run_id": "reflect-fixture",
        "reflect_state_sha256": "4" * 64,
        "reflect_diff_sha256": "5" * 64,
    }
    binding_sha256 = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    pending_id = f"pending-{binding_sha256[:24]}"
    pending_dir = run_dir / "graph" / "pending-decisions"
    pending_dir.mkdir(parents=True)
    pending_dir.joinpath(f"{pending_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pending_id": pending_id,
                **identity,
                "binding_sha256": binding_sha256,
                "created_at": "2026-07-16T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return pending_id


def _write_trusted_review_result_ref(
    run_dir: Path,
) -> ParallelReviewResultRef:
    snapshot = build_review_evidence_snapshot(
        run_id=run_dir.name,
        iteration=1,
        workspace_fingerprint="sha256:" + "1" * 64,
        policy_snapshot_sha256="2" * 64,
        verification_result_sha256="3" * 64,
        risk_result_sha256="4" * 64,
        acceptance_evidence_manifest_sha256="5" * 64,
    )
    plan = build_parallel_review_plan(
        ParallelReviewRoutingContext(
            run_id=run_dir.name,
            iteration=1,
            evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
            verification_status="passed",
            verification_failed_count=0,
            risk="low",
            changed_files=["src/example.py"],
            gate_reason_codes=[],
        ),
        topology="adaptive",
    )
    write_parallel_review_plan(run_dir, plan)
    _, public_evidence_sha256 = write_parallel_review_public_evidence(
        run_dir,
        plan,
        "# Graph State trusted public evidence\n",
    )
    _, role_prompt_sha256 = write_parallel_review_role_prompt(
        run_dir,
        plan,
        reviewer_role="correctness_reviewer",
        content=(
            "# Graph State trusted role prompt\n\n"
            f"- prompt_version: `{PARALLEL_REVIEW_PROMPT_VERSION}`\n"
        ),
    )
    attempt = build_parallel_review_attempt_identity(
        plan,
        reviewer_role="correctness_reviewer",
        public_evidence_sha256=public_evidence_sha256,
        role_prompt_sha256=role_prompt_sha256,
    )
    execution_ref = parallel_review_execution_artifact_ref(
        plan,
        reviewer_role="correctness_reviewer",
        attempt_id=attempt.attempt_id,
    )
    _, process_output_sha256, process_output_bytes = (
        write_parallel_review_process_output(
            run_dir,
            execution_ref=execution_ref,
            content="Graph State trusted process output",
        )
    )
    timestamp = "2026-07-17T00:00:00+00:00"
    command = ["vega-fake-reviewer", "correctness_reviewer"]
    execution_path = write_parallel_review_execution(
        run_dir,
        execution_ref=execution_ref,
        execution=ExecutionLease(
            run_id=run_dir.name,
            step="reviewer",
            iteration=1,
            engine="langgraph",
            graph_schema_version="checkpoint-v1",
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            idempotency_key=attempt.idempotency_key,
            replay_class="read_only_replayable",
            runner_identity={
                "kind": "deterministic-fake-reviewer",
                "role": "correctness_reviewer",
                "prompt_version": PARALLEL_REVIEW_PROMPT_VERSION,
                "public_evidence_sha256": (
                    attempt.public_evidence_sha256
                ),
                "role_prompt_sha256": attempt.role_prompt_sha256,
            },
            base_head="7" * 40,
            before_workspace_fingerprint="sha256:" + "1" * 64,
            policy_snapshot_sha256=snapshot.policy_snapshot_sha256,
            input_fingerprint=attempt.input_fingerprint,
            command_sha256=hash_command(command),
            process_output_sha256=process_output_sha256,
            process_output_bytes=process_output_bytes,
            owner_pid=1,
            command=command,
            started_at=timestamp,
            last_heartbeat=timestamp,
            lease_expires_at=timestamp,
            deadline=timestamp,
            status="completed",
            returncode=0,
            finished_at=timestamp,
        ),
    )
    result = build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=run_dir.name,
        iteration=1,
        reviewer_role="correctness_reviewer",
        attempt_id=attempt.attempt_id,
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        execution_ref=execution_ref,
        execution_sha256=hashlib.sha256(
            execution_path.read_bytes()
        ).hexdigest(),
        status="completed",
        verdict="approve",
        summary="PRIVATE_REVIEWER_CANARY_DO_NOT_SERIALIZE",
        checked_items=["公共 evidence snapshot"],
    )
    return write_parallel_review_result(run_dir, result)


def test_graph_state_only_contains_frozen_thin_reference_schema(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)

    graph_state = validate_graph_state(run_dir, create_graph_state(run_dir))
    serialized = serialize_graph_state(graph_state)

    assert set(graph_state) == GRAPH_STATE_KEYS
    assert len(serialized.encode("utf-8")) < GRAPH_STATE_MAX_BYTES
    for forbidden_business_field in (
        '"status"',
        '"risk"',
        '"recommendation"',
        '"verification_status"',
        '"verdict"',
        '"prompt"',
        '"diff"',
        '"stdout"',
        '"stderr"',
        '"pid"',
        '"heartbeat"',
        '"returncode"',
    ):
        assert forbidden_business_field not in serialized
    assert "PROMPT_CANARY_DO_NOT_SERIALIZE" not in serialized
    assert "WORKER_OUTPUT_CANARY_DO_NOT_SERIALIZE" not in serialized
    assert str(run_dir.resolve()) not in serialized


def test_graph_state_rejects_extra_business_mirror_fields(tmp_path: Path) -> None:
    run_dir = _create_graph_run(tmp_path)
    payload = dict(create_graph_state(run_dir))
    payload["status"] = "success"

    with pytest.raises(GraphStateValidationError, match="字段不匹配"):
        validate_graph_state(run_dir, payload)


@pytest.mark.parametrize(
    ("field", "alternate_ref"),
    [
        ("state_ref", "state-copy.json"),
        ("task_contract_ref", "loop-plan-copy.md"),
        ("policy_snapshot_ref", "policy-copy.json"),
    ],
)
def test_graph_state_rejects_noncanonical_authority_refs(
    tmp_path: Path,
    field: str,
    alternate_ref: str,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    source_name = {
        "state_ref": "state.json",
        "task_contract_ref": "loop-plan.md",
        "policy_snapshot_ref": "project-policy-snapshot.json",
    }[field]
    run_dir.joinpath(alternate_ref).write_bytes(
        run_dir.joinpath(source_name).read_bytes()
    )
    payload = dict(create_graph_state(run_dir))
    payload[field] = alternate_ref

    with pytest.raises(GraphStateValidationError, match="必须固定"):
        validate_graph_state(run_dir, payload)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("state_ref", "../state.json", "必须固定"),
        ("task_contract_ref", "C:/private/prompt.md", "必须固定"),
        ("terminal_ref", "../eval.md", "不能越过"),
        ("policy_snapshot_sha256", "0" * 64, "hash 不匹配"),
        ("latest_step_result_id", "step-result-1", "step result"),
        ("pending_human_decision_id", "decision-1", "pending decision 不可信"),
    ],
)
def test_graph_state_rejects_invalid_or_future_gate_fields(
    tmp_path: Path,
    field: str,
    value: str,
    expected: str,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    payload = dict(create_graph_state(run_dir))
    payload[field] = value

    with pytest.raises(GraphStateValidationError, match=expected):
        validate_graph_state(run_dir, payload)


def test_graph_state_accepts_trusted_pending_decision_identity(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    pending_id = _write_trusted_pending_decision(run_dir)
    payload = dict(create_graph_state(run_dir))
    payload["pending_human_decision_id"] = pending_id

    validated = validate_graph_state(run_dir, payload)

    assert validated["pending_human_decision_id"] == pending_id


def test_graph_state_terminal_ref_must_match_authoritative_state(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    graph_state = create_graph_state(run_dir)
    business_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    business_state.status = "needs_human"
    business_state.current_step = "risk_gate_needs_human"
    business_state.save(run_dir / "state.json")
    run_dir.joinpath("eval.md").write_text("# Eval\n", encoding="utf-8")

    with pytest.raises(GraphStateValidationError, match="terminal_ref"):
        validate_graph_state(run_dir, graph_state)

    refreshed = refresh_graph_state(run_dir, graph_state)
    validated = validate_graph_state(run_dir, refreshed)
    assert validated["terminal_ref"] == "eval.md"


def test_graph_state_rejects_noncanonical_terminal_ref(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    business_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    business_state.status = "needs_human"
    business_state.current_step = "risk_gate_needs_human"
    business_state.save(run_dir / "state.json")
    run_dir.joinpath("eval.md").write_text("# Eval\n", encoding="utf-8")
    run_dir.joinpath("terminal-copy.md").write_text(
        "# Eval\n",
        encoding="utf-8",
    )
    payload = refresh_graph_state(run_dir, create_graph_state(run_dir))
    payload["terminal_ref"] = "terminal-copy.md"

    with pytest.raises(GraphStateValidationError, match="权威业务终态"):
        validate_graph_state(run_dir, payload)


def test_write_graph_state_uses_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    graph_dir = run_dir / "graph"
    graph_dir.mkdir()
    graph_path = graph_dir / "graph-state.json"
    graph_path.write_text('{"sentinel": true}\n', encoding="utf-8")
    monkeypatch.setattr(loop_graph_state.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        loop_graph_state.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(PermissionError("locked")),
    )

    with pytest.raises(PermissionError, match="locked"):
        write_graph_state(run_dir, create_graph_state(run_dir))

    assert graph_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert list(graph_dir.glob(".tmp-*")) == []


def test_write_graph_state_rejects_reparse_output_directory(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    target = run_dir / "graph-target"
    target.mkdir()
    _create_directory_link_or_skip(target, run_dir / "graph")

    with pytest.raises(GraphStateValidationError, match="reparse point"):
        write_graph_state(run_dir, create_graph_state(run_dir))


def test_graph_state_v2_accepts_trusted_parallel_review_ref_and_stays_thin(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    result_ref = _write_trusted_review_result_ref(run_dir)
    payload = create_graph_state(run_dir)
    payload["review_results"] = {
        result_ref.result_id: result_ref.model_dump(mode="json")
    }

    validated = validate_graph_state(run_dir, payload)
    serialized = serialize_graph_state(validated)

    assert validated["review_results"] == {
        result_ref.result_id: result_ref.model_dump(mode="json")
    }
    assert result_ref.review_plan_id in serialized
    assert result_ref.artifact_sha256 in serialized
    assert "summary" not in serialized
    assert "findings" not in serialized
    assert "PRIVATE_REVIEWER_CANARY" not in serialized


def test_graph_state_v2_rejects_tampered_review_result_artifact(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    result_ref = _write_trusted_review_result_ref(run_dir)
    result_path = run_dir.joinpath(*result_ref.artifact_ref.split("/"))
    result_path.write_text(
        result_path.read_text(encoding="utf-8").replace(
            "PRIVATE_REVIEWER_CANARY",
            "TAMPERED_REVIEWER_CANARY",
        ),
        encoding="utf-8",
        newline="\n",
    )
    payload = create_graph_state(run_dir)
    payload["review_results"] = {
        result_ref.result_id: result_ref.model_dump(mode="json")
    }

    with pytest.raises(
        GraphStateValidationError,
        match="Reviewer result 不可信",
    ):
        validate_graph_state(run_dir, payload)


def test_graph_state_v1_does_not_silently_accept_gate5_review_refs(
    tmp_path: Path,
) -> None:
    run_dir = _create_graph_run(tmp_path)
    result_ref = _write_trusted_review_result_ref(run_dir)
    payload = create_graph_state(run_dir)
    payload["schema_version"] = GRAPH_STATE_LEGACY_SCHEMA_VERSION
    payload["engine_version"] = GRAPH_ENGINE_LEGACY_VERSION
    payload["review_results"] = {
        result_ref.result_id: result_ref.model_dump(mode="json")
    }

    with pytest.raises(
        GraphStateValidationError,
        match="v1 不允许",
    ):
        validate_graph_state(run_dir, payload)


def test_review_result_reducer_is_idempotent_and_conflict_detecting() -> None:
    result_id = "review-result-" + "a" * 64
    result = {
        "schema_version": 2,
        "result_id": result_id,
        "review_plan_id": "review-plan-" + "b" * 64,
        "reviewer_role": "correctness_reviewer",
        "evidence_snapshot_sha256": "a" * 64,
        "attempt_id": "attempt-1",
        "artifact_ref": "reviews/correctness.json",
        "artifact_sha256": "c" * 64,
    }
    current = {result_id: result}

    assert merge_review_results_by_identity(current, deepcopy(current)) == current

    conflict = deepcopy(current)
    conflict[result_id]["artifact_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="identity 冲突"):
        merge_review_results_by_identity(current, conflict)


def test_review_result_reducer_rejects_legacy_thin_ref() -> None:
    legacy = {
        "reviewer_role": "correctness",
        "evidence_snapshot_sha256": "a" * 64,
        "attempt_id": "attempt-1",
        "artifact_ref": "reviews/correctness.json",
    }

    with pytest.raises(ValueError):
        merge_review_results_by_identity(
            {"legacy-result": legacy},  # type: ignore[dict-item]
            {},
        )


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
