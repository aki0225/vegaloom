from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver

from vega.loop_graph_state import (
    create_graph_state,
    serialize_graph_state,
    validate_graph_state,
)
from vega.models import LoopAutomationState
from vega.parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    ParallelReviewAggregationContext,
    ParallelReviewRoutingContext,
    ReviewEvidenceSnapshot,
    ReviewerRole,
    build_parallel_review_plan,
)
from vega.parallel_review_artifacts import (
    build_review_evidence_snapshot_from_artifacts,
    read_parallel_review_result,
)
from vega.parallel_review_graph import (
    DeterministicFakeReviewer,
    ParallelReviewGraphValidationError,
    execute_parallel_review_graph,
)


PRIVATE_CANARIES = {
    "correctness_reviewer": "CORRECTNESS_PRIVATE_CANARY_GATE5",
    "verification_adequacy_reviewer": (
        "VERIFICATION_PRIVATE_CANARY_GATE5"
    ),
    "security_design_reviewer": "SECURITY_PRIVATE_CANARY_GATE5",
}


def _create_run(tmp_path: Path) -> tuple[Path, ReviewEvidenceSnapshot]:
    run_dir = tmp_path / "runs" / "gate5-fanout-run"
    run_dir.mkdir(parents=True)
    LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        engine="langgraph",
        repo_path=str(tmp_path / "repo"),
        input_source="test",
        status="running",
        current_step="review",
        current_iteration=1,
    ).save(run_dir / "state.json")
    artifacts = {
        "loop-plan.md": "# Gate 5 fixture\n",
        "project-policy-snapshot.json": "{}\n",
        "iterations/01/verification-result.json": (
            '{"status":"passed","failed_count":0}\n'
        ),
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


def _context(
    run_dir: Path,
    snapshot: ReviewEvidenceSnapshot,
    *,
    topology: str,
    changed_files: list[str] | None = None,
    risk: str = "low",
    gate_reason_codes: list[str] | None = None,
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
            "risk": risk,
            "changed_files": changed_files or ["src/example.py"],
            "gate_reason_codes": gate_reason_codes or [],
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
        risk=risk,  # type: ignore[arg-type]
        human_approval_valid=risk != "high",
    )


def _executors(
    *,
    delays: dict[str, float] | None = None,
    completion_order: list[str] | None = None,
    overrides: dict[str, dict[str, object]] | None = None,
):
    selected_delays = delays or {}
    selected_overrides = overrides or {}
    result: dict[ReviewerRole, DeterministicFakeReviewer] = {}
    for role in AVAILABLE_REVIEWER_ROLES:
        role_updates = selected_overrides.get(role, {})
        result[role] = DeterministicFakeReviewer(
            reviewer_role=role,
            private_canary=PRIVATE_CANARIES[role],
            delay_seconds=selected_delays.get(role, 0.0),
            on_completed=(
                (
                    lambda completed_role, target=completion_order: (
                        target.append(completed_role)
                    )
                )
                if completion_order is not None
                else None
            ),
            **role_updates,  # type: ignore[arg-type]
        )
    return result


@pytest.mark.requires_langgraph
def test_adaptive_single_reviewer_fanout_persists_one_result(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    context = _context(
        run_dir,
        snapshot,
        topology="adaptive",
    )

    run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=_executors(),
    )

    assert run.plan.required_roles == ["correctness_reviewer"]
    assert len(run.result_refs) == 1
    assert run.aggregate.verdict == "approve"
    assert run.aggregate.reviewer_result_ids == {
        "correctness_reviewer": run.result_refs[0].result_id
    }


@pytest.mark.requires_langgraph
def test_adaptive_two_role_fanout_follows_review_plan(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    context = _context(
        run_dir,
        snapshot,
        topology="adaptive",
        changed_files=["src/example.py", "tests/test_example.py"],
    )

    run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=_executors(),
    )

    assert run.plan.required_roles == [
        "correctness_reviewer",
        "verification_adequacy_reviewer",
    ]
    assert {
        result_ref.reviewer_role for result_ref in run.result_refs
    } == set(run.plan.required_roles)
    assert run.aggregate.verdict == "approve"


@pytest.mark.requires_langgraph
def test_fixed_three_completion_order_does_not_change_aggregate(
    tmp_path: Path,
) -> None:
    first_run_dir, first_snapshot = _create_run(tmp_path)
    first_context = _context(
        first_run_dir,
        first_snapshot,
        topology="fixed_three",
    )
    first_order = [
        "security_design_reviewer",
        "verification_adequacy_reviewer",
        "correctness_reviewer",
    ]
    first = _execute_with_controlled_completion_order(
        first_run_dir,
        first_context,
        first_order,
    )
    second_run_dir, second_snapshot = _create_run(tmp_path / "b")
    second_context = _context(
        second_run_dir,
        second_snapshot,
        topology="fixed_three",
    )
    second_order = [
        "correctness_reviewer",
        "verification_adequacy_reviewer",
        "security_design_reviewer",
    ]
    second = _execute_with_controlled_completion_order(
        second_run_dir,
        second_context,
        second_order,
    )

    assert first.aggregate == second.aggregate
    assert first.aggregate_ref == second.aggregate_ref


@pytest.mark.requires_langgraph
def test_fake_reviewer_failure_cannot_produce_approve(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    context = _context(
        run_dir,
        snapshot,
        topology="fixed_three",
    )
    run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=_executors(
            overrides={
                "verification_adequacy_reviewer": {
                    "status": "timed_out",
                    "verdict": "needs_human",
                }
            }
        ),
    )

    assert run.aggregate.verdict == "needs_human"
    assert "reviewer_execution_unresolved" in run.aggregate.reasons


@pytest.mark.requires_langgraph
def test_missing_required_executor_fails_before_fanout(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    context = _context(
        run_dir,
        snapshot,
        topology="fixed_three",
    )
    executors = _executors()
    executors.pop("security_design_reviewer")

    with pytest.raises(
        ParallelReviewGraphValidationError,
        match="缺少必需",
    ):
        execute_parallel_review_graph(
            run_dir,
            context=context,
            executors=executors,
        )

    assert not (
        run_dir / "iterations" / "01" / "parallel-reviews"
    ).exists()


@pytest.mark.requires_langgraph
def test_reviewer_private_canaries_stay_in_own_result_artifact(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    context = _context(
        run_dir,
        snapshot,
        topology="fixed_three",
    )
    checkpointer = InMemorySaver()
    run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=_executors(),
        checkpointer=checkpointer,
    )

    for result_ref in run.result_refs:
        own_canary = PRIVATE_CANARIES[result_ref.reviewer_role]
        result_path = run_dir.joinpath(*result_ref.artifact_ref.split("/"))
        result_text = result_path.read_text(encoding="utf-8")
        assert own_canary in result_text
        for role, other_canary in PRIVATE_CANARIES.items():
            if role != result_ref.reviewer_role:
                assert other_canary not in result_text

    serialized_state = json.dumps(
        run.graph_state,
        ensure_ascii=False,
        sort_keys=True,
    )
    aggregate_path = run_dir.joinpath(
        *run.aggregate_ref["artifact_ref"].split("/")
    )
    aggregate_text = aggregate_path.read_text(encoding="utf-8")
    checkpoint_payload = json.dumps(
        [
            item.checkpoint
            for item in checkpointer.list(
                {
                    "configurable": {
                        "thread_id": (
                            f"{run.plan.run_id}:{run.plan.plan_id}"
                        )
                    }
                }
            )
        ],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    for canary in PRIVATE_CANARIES.values():
        assert canary not in serialized_state
        assert canary not in aggregate_text
        assert canary not in checkpoint_payload


@pytest.mark.requires_langgraph
def test_gate5_result_refs_are_valid_graph_state_v2_inputs(
    tmp_path: Path,
) -> None:
    run_dir, snapshot = _create_run(tmp_path)
    context = _context(
        run_dir,
        snapshot,
        topology="fixed_three",
    )
    run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=_executors(),
    )
    graph_state = create_graph_state(run_dir)
    graph_state["review_results"] = {
        result_ref.result_id: result_ref.model_dump(mode="json")
        for result_ref in run.result_refs
    }

    validated = validate_graph_state(run_dir, graph_state)
    serialized = serialize_graph_state(validated)

    assert len(validated["review_results"]) == 3
    for result_ref in run.result_refs:
        loaded = read_parallel_review_result(run_dir, result_ref)
        assert loaded.result_id == result_ref.result_id
    for canary in PRIVATE_CANARIES.values():
        assert canary not in serialized


def _execute_with_controlled_completion_order(
    run_dir: Path,
    context: ParallelReviewAggregationContext,
    expected_order: list[str],
):
    started = {
        role: threading.Event() for role in AVAILABLE_REVIEWER_ROLES
    }
    release = {
        role: threading.Event() for role in AVAILABLE_REVIEWER_ROLES
    }
    completed = {
        role: threading.Event() for role in AVAILABLE_REVIEWER_ROLES
    }
    observed_order: list[str] = []

    executors: dict[ReviewerRole, DeterministicFakeReviewer] = {}
    for role in AVAILABLE_REVIEWER_ROLES:
        executors[role] = DeterministicFakeReviewer(
            reviewer_role=role,
            private_canary=PRIVATE_CANARIES[role],
            on_started=lambda started_role, events=started: events[
                started_role
            ].set(),
            wait_until_released=lambda waiting_role, events=release: events[
                waiting_role
            ].wait(timeout=5),
            on_completed=(
                lambda completed_role, order=observed_order, events=completed: (
                    order.append(completed_role),
                    events[completed_role].set(),
                )
            ),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            execute_parallel_review_graph,
            run_dir,
            context=context,
            executors=executors,
        )
        for event in started.values():
            assert event.wait(timeout=10), "reviewer 未进入并发 fan-out"
        for role in expected_order:
            release[role].set()
            if not completed[role].wait(timeout=10):
                if future.done():
                    future.result()
                raise AssertionError("reviewer 未按控制顺序完成")
        run = future.result(timeout=20)

    assert observed_order == expected_order
    return run
