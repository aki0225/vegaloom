from __future__ import annotations

import json
from copy import deepcopy
from itertools import permutations

import pytest
from pydantic import ValidationError

from vega.parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    ParallelReviewAggregationContext,
    ParallelReviewFinding,
    ParallelReviewPlan,
    ParallelReviewResult,
    ParallelReviewRoutingContext,
    ReviewEvidenceSnapshot,
    aggregate_parallel_reviews,
    build_parallel_review_finding,
    build_parallel_review_plan,
    build_parallel_review_result,
    build_parallel_review_result_ref,
    build_review_evidence_snapshot,
    merge_parallel_review_result_refs,
    merge_parallel_review_results,
)


def _snapshot(*, suffix: str = "1") -> ReviewEvidenceSnapshot:
    return build_review_evidence_snapshot(
        run_id="gate5-run",
        iteration=1,
        workspace_fingerprint="sha256:" + suffix * 64,
        policy_snapshot_sha256="2" * 64,
        verification_result_sha256="3" * 64,
        risk_result_sha256="4" * 64,
        acceptance_evidence_manifest_sha256="5" * 64,
    )


def _routing_context(
    snapshot: ReviewEvidenceSnapshot,
    **updates: object,
) -> ParallelReviewRoutingContext:
    payload: dict[str, object] = {
        "run_id": snapshot.run_id,
        "iteration": snapshot.iteration,
        "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
        "verification_status": "passed",
        "verification_failed_count": 0,
        "risk": "low",
        "changed_files": ["src/example.py"],
        "gate_reason_codes": [],
    }
    payload.update(updates)
    return ParallelReviewRoutingContext.model_validate(payload)


def _plan(
    snapshot: ReviewEvidenceSnapshot,
    *,
    topology: str = "fixed_three",
    **routing_updates: object,
) -> ParallelReviewPlan:
    return build_parallel_review_plan(
        _routing_context(snapshot, **routing_updates),
        topology=topology,  # type: ignore[arg-type]
    )


def _context(
    snapshot: ReviewEvidenceSnapshot,
    *,
    plan: ParallelReviewPlan | None = None,
    **updates: object,
) -> ParallelReviewAggregationContext:
    payload: dict[str, object] = {
        "run_id": snapshot.run_id,
        "iteration": snapshot.iteration,
        "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
        "review_plan": plan or _plan(snapshot),
        "verification_status": "passed",
        "verification_failed_count": 0,
        "risk": "low",
        "human_approval_valid": False,
        "evidence_fresh": True,
        "evidence_truncated": False,
        "evidence_hash_valid": True,
    }
    payload.update(updates)
    return ParallelReviewAggregationContext.model_validate(payload)


def _result(
    snapshot: ReviewEvidenceSnapshot,
    role: str,
    *,
    plan: ParallelReviewPlan | None = None,
    status: str = "completed",
    verdict: str = "approve",
    findings: list[ParallelReviewFinding] | None = None,
    suffix: str | None = None,
    summary: str = "reviewer 完成结构化审查。",
) -> ParallelReviewResult:
    selected_plan = plan or _plan(snapshot)
    role_suffix = suffix or str(AVAILABLE_REVIEWER_ROLES.index(role) + 6)
    return build_parallel_review_result(
        review_plan_id=selected_plan.plan_id,
        run_id=snapshot.run_id,
        iteration=snapshot.iteration,
        reviewer_role=role,  # type: ignore[arg-type]
        attempt_id=f"attempt-{role}",
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        execution_ref=f"reviews/{role}/execution.json",
        execution_sha256=role_suffix * 64,
        status=status,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        summary=summary,
        findings=findings or [],
        checked_items=["公共 evidence snapshot"],
    )


def _approve_results(
    snapshot: ReviewEvidenceSnapshot,
    *,
    plan: ParallelReviewPlan | None = None,
) -> list[ParallelReviewResult]:
    selected_plan = plan or _plan(snapshot)
    return [
        _result(snapshot, role, plan=selected_plan)
        for role in selected_plan.required_roles
    ]


def test_evidence_snapshot_rejects_inconsistent_content_identity() -> None:
    snapshot = _snapshot()

    assert len(snapshot.evidence_snapshot_sha256) == 64

    payload = snapshot.model_dump(mode="json")
    payload["verification_result_sha256"] = "a" * 64
    with pytest.raises(ValidationError, match="证据身份不一致"):
        ReviewEvidenceSnapshot.model_validate(payload)


def test_adaptive_plan_uses_single_reviewer_for_low_risk_change() -> None:
    snapshot = _snapshot()

    plan = _plan(snapshot, topology="adaptive")

    assert plan.topology == "adaptive"
    assert plan.required_roles == ["correctness_reviewer"]
    assert plan.role_reasons == {
        "correctness_reviewer": ["policy:baseline-correctness"]
    }
    assert plan.max_parallelism == 1


def test_adaptive_plan_adds_verification_reviewer_for_test_scope() -> None:
    snapshot = _snapshot()

    plan = _plan(
        snapshot,
        topology="adaptive",
        changed_files=["src/example.py", "tests/test_example.py"],
    )

    assert plan.required_roles == [
        "correctness_reviewer",
        "verification_adequacy_reviewer",
    ]
    assert plan.role_reasons["verification_adequacy_reviewer"] == [
        "path:test-scope"
    ]


def test_adaptive_plan_adds_risk_reviewer_from_gate_reason() -> None:
    snapshot = _snapshot()

    plan = _plan(
        snapshot,
        topology="adaptive",
        risk="medium",
        changed_files=["config/deploy.yaml"],
        gate_reason_codes=["medium_risk_paths"],
    )

    assert plan.required_roles == [
        "correctness_reviewer",
        "security_design_reviewer",
    ]
    assert plan.role_reasons["security_design_reviewer"] == [
        "gate:medium_risk_paths"
    ]


def test_adaptive_plan_fails_closed_to_all_roles_for_high_risk() -> None:
    snapshot = _snapshot()

    plan = _plan(
        snapshot,
        topology="adaptive",
        risk="high",
        changed_files=["src/auth/service.py"],
        gate_reason_codes=["high_risk_paths"],
    )

    assert plan.required_roles == list(AVAILABLE_REVIEWER_ROLES)
    assert plan.role_reasons["verification_adequacy_reviewer"] == [
        "risk:high-cross-check"
    ]
    assert plan.role_reasons["security_design_reviewer"] == [
        "gate:high_risk_paths",
        "risk:high-fail-closed",
    ]


def test_adaptive_plan_does_not_multiply_reviewers_for_deterministic_blocker() -> None:
    snapshot = _snapshot()

    plan = _plan(
        snapshot,
        topology="adaptive",
        risk="high",
        changed_files=["src/example.py"],
        gate_reason_codes=["diff_check_failed"],
    )

    assert plan.required_roles == ["correctness_reviewer"]


def test_adaptive_plan_does_not_multiply_reviewers_for_failed_verification() -> None:
    snapshot = _snapshot()

    plan = _plan(
        snapshot,
        topology="adaptive",
        verification_status="failed",
        verification_failed_count=1,
        risk="high",
        changed_files=["src/auth/service.py"],
        gate_reason_codes=["high_risk_paths"],
    )

    assert plan.required_roles == ["correctness_reviewer"]


def test_single_and_fixed_three_are_explicit_experiment_topologies() -> None:
    snapshot = _snapshot()
    routing = {
        "risk": "high",
        "changed_files": ["src/auth/service.py"],
        "gate_reason_codes": ["high_risk_paths"],
    }

    single = _plan(snapshot, topology="single", **routing)
    fixed_three = _plan(snapshot, topology="fixed_three", **routing)

    assert single.required_roles == ["correctness_reviewer"]
    assert fixed_three.required_roles == list(AVAILABLE_REVIEWER_ROLES)
    assert fixed_three.role_reasons["verification_adequacy_reviewer"] == [
        "topology:fixed-three-reference"
    ]


def test_review_plan_is_content_addressed_and_rejects_tampering() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="adaptive")
    payload = plan.model_dump(mode="json")
    payload["max_parallelism"] = 2

    with pytest.raises(ValidationError, match="max_parallelism"):
        ParallelReviewPlan.model_validate(payload)

    payload = plan.model_dump(mode="json")
    payload["role_reasons"]["correctness_reviewer"] = ["policy:other"]
    with pytest.raises(ValidationError, match="plan_id"):
        ParallelReviewPlan.model_validate(payload)


def test_review_plan_rejects_parallelism_above_required_roles() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError, match="max_parallelism"):
        build_parallel_review_plan(
            _routing_context(snapshot),
            topology="adaptive",
            max_parallelism=2,
        )


def test_finding_identity_normalizes_path_location_and_rule() -> None:
    snapshot = _snapshot()

    finding = build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity="major",
        category=" Correctness ",
        rule_id="Unicode Separator",
        path=".\\src\\slugify.py",
        location=" L10 - L12 ",
        title="Unicode 分隔符被删除",
    )

    assert finding.category == "correctness"
    assert finding.rule_id == "unicode-separator"
    assert finding.normalized_path == "src/slugify.py"
    assert finding.normalized_location == "l10-l12"
    assert finding.finding_id.startswith("finding-")


def test_finding_identity_binds_evidence_snapshot() -> None:
    first = _snapshot(suffix="1")
    second = _snapshot(suffix="a")
    arguments = {
        "severity": "major",
        "category": "correctness",
        "rule_id": "unicode-separator",
        "path": "src/slugify.py",
        "location": "l10-l12",
        "title": "同一 finding",
    }

    first_finding = build_parallel_review_finding(
        evidence_snapshot_sha256=first.evidence_snapshot_sha256,
        **arguments,
    )
    second_finding = build_parallel_review_finding(
        evidence_snapshot_sha256=second.evidence_snapshot_sha256,
        **arguments,
    )

    assert first_finding.finding_id != second_finding.finding_id


def test_finding_rejects_absolute_or_traversal_path() -> None:
    snapshot = _snapshot()

    for path in ("../secret.txt", "C:/private/auth.json", "/etc/passwd"):
        with pytest.raises(ValueError, match="仓库相对路径"):
            build_parallel_review_finding(
                evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
                severity="major",
                category="security",
                rule_id="path-boundary",
                path=path,
                location="global",
                title="越界路径",
            )


def test_result_rejects_finding_from_another_snapshot() -> None:
    current = _snapshot(suffix="1")
    stale = _snapshot(suffix="a")
    stale_finding = build_parallel_review_finding(
        evidence_snapshot_sha256=stale.evidence_snapshot_sha256,
        severity="major",
        category="correctness",
        rule_id="stale-finding",
        path="src/example.py",
        location="l1",
        title="旧证据 finding",
    )

    with pytest.raises(ValidationError, match="当前 evidence snapshot"):
        _result(
            current,
            "correctness_reviewer",
            findings=[stale_finding],
        )


def test_non_completed_result_cannot_claim_approve_or_findings() -> None:
    snapshot = _snapshot()
    finding = build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity="major",
        category="correctness",
        rule_id="untrusted-output",
        path="src/example.py",
        location="l1",
        title="未知终态输出",
    )

    with pytest.raises(ValidationError, match="只能输出 needs_human"):
        _result(
            snapshot,
            "correctness_reviewer",
            status="timed_out",
            verdict="approve",
        )
    with pytest.raises(ValidationError, match="findings 不可信"):
        _result(
            snapshot,
            "correctness_reviewer",
            status="provider_error",
            verdict="needs_human",
            findings=[finding],
        )


def test_reviewer_free_text_is_redacted_before_result_serialization() -> None:
    snapshot = _snapshot()
    finding = build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity="minor",
        category="security",
        rule_id="redaction-boundary",
        path="src/example.py",
        location="l1",
        title='api_key="phase1-secret-value"',
        evidence="Authorization: Bearer reviewer-secret-token",
    )
    result = _result(
        snapshot,
        "security_design_reviewer",
        summary='password="reviewer-password-value"',
        findings=[finding],
    )
    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    assert "phase1-secret-value" not in serialized
    assert "reviewer-secret-token" not in serialized
    assert "reviewer-password-value" not in serialized
    assert "[REDACTED]" in serialized


def test_result_reducer_is_idempotent_sorted_and_conflict_detecting() -> None:
    snapshot = _snapshot()
    first = _result(snapshot, "correctness_reviewer")
    second = _result(snapshot, "security_design_reviewer")

    merged = merge_parallel_review_results(
        {second.result_id: second},
        {first.result_id: first},
    )
    assert list(merged) == sorted([first.result_id, second.result_id])
    assert merge_parallel_review_results(merged, deepcopy(merged)) == merged

    conflict = first.model_copy(update={"summary": "同 identity 的不同内容"})
    with pytest.raises(ValueError, match="identity 冲突"):
        merge_parallel_review_results(
            {first.result_id: first},
            {first.result_id: conflict},
        )


def test_result_reducer_rejects_map_key_mismatch() -> None:
    snapshot = _snapshot()
    result = _result(snapshot, "correctness_reviewer")

    with pytest.raises(ValueError, match="map key"):
        merge_parallel_review_results({}, {"wrong-id": result})


def test_result_reducer_accepts_checkpoint_deserialized_dict() -> None:
    snapshot = _snapshot()
    result = _result(snapshot, "correctness_reviewer")
    raw_result = result.model_dump(mode="json")

    merged = merge_parallel_review_results(
        {},
        {result.result_id: raw_result},
    )

    assert merged == {result.result_id: result}


def test_result_ref_is_thin_and_does_not_propagate_private_text() -> None:
    snapshot = _snapshot()
    result = _result(
        snapshot,
        "correctness_reviewer",
        summary="CORRECTNESS_PRIVATE_CANARY_123",
    )

    result_ref = build_parallel_review_result_ref(
        result,
        artifact_ref="iterations/01/parallel-reviews/correctness/result.json",
        artifact_sha256="a" * 64,
    )
    serialized = json.dumps(result_ref.model_dump(mode="json"), ensure_ascii=False)

    assert result_ref.result_id == result.result_id
    assert result_ref.review_plan_id == result.review_plan_id
    assert "summary" not in serialized
    assert "findings" not in serialized
    assert "checked_items" not in serialized
    assert "PRIVATE_CANARY" not in serialized


def test_result_ref_reducer_is_idempotent_and_conflict_detecting() -> None:
    snapshot = _snapshot()
    result = _result(snapshot, "correctness_reviewer")
    result_ref = build_parallel_review_result_ref(
        result,
        artifact_ref="iterations/01/parallel-reviews/correctness/result.json",
        artifact_sha256="a" * 64,
    )
    current = {result_ref.result_id: result_ref}

    assert merge_parallel_review_result_refs(current, deepcopy(current)) == current

    conflict = result_ref.model_copy(update={"artifact_sha256": "b" * 64})
    with pytest.raises(ValueError, match="identity 冲突"):
        merge_parallel_review_result_refs(
            current,
            {conflict.result_id: conflict},
        )


def test_result_ref_reducer_rejects_map_key_mismatch() -> None:
    snapshot = _snapshot()
    result_ref = build_parallel_review_result_ref(
        _result(snapshot, "correctness_reviewer"),
        artifact_ref="iterations/01/parallel-reviews/correctness/result.json",
        artifact_sha256="a" * 64,
    )

    with pytest.raises(ValueError, match="map key"):
        merge_parallel_review_result_refs(
            {"wrong-id": result_ref},
            {},
        )


def test_result_ref_reducer_accepts_checkpoint_deserialized_dict() -> None:
    snapshot = _snapshot()
    result_ref = build_parallel_review_result_ref(
        _result(snapshot, "correctness_reviewer"),
        artifact_ref="iterations/01/parallel-reviews/correctness/result.json",
        artifact_sha256="a" * 64,
    )
    raw_ref = result_ref.model_dump(mode="json")

    merged = merge_parallel_review_result_refs(
        {},
        {result_ref.result_id: raw_ref},
    )

    assert merged == {result_ref.result_id: result_ref}

    conflicting_raw_ref = dict(raw_ref)
    conflicting_raw_ref["artifact_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="identity 冲突"):
        merge_parallel_review_result_refs(
            {result_ref.result_id: raw_ref},
            {result_ref.result_id: conflicting_raw_ref},
        )


def test_aggregator_is_deterministic_for_every_completion_order() -> None:
    snapshot = _snapshot()
    context = _context(snapshot)
    results = _approve_results(snapshot)

    aggregates = [
        aggregate_parallel_reviews(context, order)
        for order in permutations(results)
    ]

    assert {item.aggregate_sha256 for item in aggregates} == {
        aggregates[0].aggregate_sha256
    }
    assert {
        json.dumps(item.model_dump(mode="json"), sort_keys=True)
        for item in aggregates
    } == {
        json.dumps(aggregates[0].model_dump(mode="json"), sort_keys=True)
    }
    assert aggregates[0].verdict == "approve"


def test_aggregator_accepts_single_reviewer_plan() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="adaptive")
    results = _approve_results(snapshot, plan=plan)

    aggregate = aggregate_parallel_reviews(
        _context(snapshot, plan=plan),
        results,
    )

    assert plan.required_roles == ["correctness_reviewer"]
    assert aggregate.review_plan_id == plan.plan_id
    assert aggregate.verdict == "approve"
    assert aggregate.reviewer_result_ids == {
        "correctness_reviewer": results[0].result_id
    }


def test_aggregator_is_deterministic_for_two_role_plan() -> None:
    snapshot = _snapshot()
    plan = _plan(
        snapshot,
        topology="adaptive",
        changed_files=["src/example.py", "tests/test_example.py"],
    )
    results = _approve_results(snapshot, plan=plan)

    aggregates = [
        aggregate_parallel_reviews(
            _context(snapshot, plan=plan),
            order,
        )
        for order in permutations(results)
    ]

    assert plan.required_roles == [
        "correctness_reviewer",
        "verification_adequacy_reviewer",
    ]
    assert {item.aggregate_sha256 for item in aggregates} == {
        aggregates[0].aggregate_sha256
    }


def test_aggregator_rejects_unexpected_reviewer_outside_plan() -> None:
    snapshot = _snapshot()
    adaptive_plan = _plan(snapshot, topology="adaptive")
    fixed_plan = _plan(snapshot, topology="fixed_three")
    expected = _result(
        snapshot,
        "correctness_reviewer",
        plan=adaptive_plan,
    )
    unexpected = _result(
        snapshot,
        "security_design_reviewer",
        plan=adaptive_plan,
    )

    aggregate = aggregate_parallel_reviews(
        _context(snapshot, plan=adaptive_plan),
        [expected, unexpected],
    )

    assert fixed_plan.plan_id != adaptive_plan.plan_id
    assert aggregate.verdict == "needs_human"
    assert "reviewer_result_set_incomplete" in aggregate.reasons
    assert "reviewer_result_identity_mismatch" in aggregate.reasons


def test_aggregator_rejects_result_bound_to_another_plan() -> None:
    snapshot = _snapshot()
    adaptive_plan = _plan(snapshot, topology="adaptive")
    single_plan = _plan(snapshot, topology="single")
    assert adaptive_plan.plan_id != single_plan.plan_id

    result = _result(
        snapshot,
        "correctness_reviewer",
        plan=single_plan,
    )
    aggregate = aggregate_parallel_reviews(
        _context(snapshot, plan=adaptive_plan),
        [result],
    )

    assert aggregate.verdict == "needs_human"
    assert aggregate.reviewer_result_ids == {}
    assert "reviewer_result_identity_mismatch" in aggregate.reasons


def test_aggregator_deduplicates_findings_without_propagating_private_text() -> None:
    snapshot = _snapshot()
    shared = {
        "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
        "category": "correctness",
        "rule_id": "unicode-separator",
        "path": "src/slugify.py",
        "location": "l10-l12",
    }
    correctness = build_parallel_review_finding(
        **shared,
        severity="minor",
        title="CORRECTNESS_PRIVATE_CANARY_123",
        evidence="CORRECTNESS_PRIVATE_CANARY_123",
    )
    verification = build_parallel_review_finding(
        **shared,
        severity="major",
        title="VERIFICATION_PRIVATE_CANARY_456",
        evidence="VERIFICATION_PRIVATE_CANARY_456",
    )
    results = [
        _result(
            snapshot,
            "correctness_reviewer",
            findings=[correctness],
            verdict="request_changes",
        ),
        _result(
            snapshot,
            "verification_adequacy_reviewer",
            findings=[verification],
            verdict="request_changes",
        ),
        _result(snapshot, "security_design_reviewer"),
    ]

    aggregate = aggregate_parallel_reviews(_context(snapshot), results)
    serialized = json.dumps(aggregate.model_dump(mode="json"), ensure_ascii=False)

    assert aggregate.verdict == "request_changes"
    assert aggregate.reasons == [
        "major_findings",
        "reviewer_requested_changes",
    ]
    assert len(aggregate.findings) == 1
    assert aggregate.findings[0].severity == "major"
    assert aggregate.findings[0].reviewer_roles == [
        "correctness_reviewer",
        "verification_adequacy_reviewer",
    ]
    assert "PRIVATE_CANARY" not in serialized


def test_verification_failure_cannot_be_overridden_by_three_approvals() -> None:
    snapshot = _snapshot()

    aggregate = aggregate_parallel_reviews(
        _context(
            snapshot,
            verification_status="failed",
            verification_failed_count=1,
        ),
        _approve_results(snapshot),
    )

    assert aggregate.verdict == "request_changes"
    assert aggregate.reasons == ["verification_failed"]


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"verification_status": "timed_out"}, "verification_unresolved"),
        ({"evidence_fresh": False}, "evidence_stale"),
        ({"evidence_truncated": True}, "evidence_truncated"),
        ({"evidence_hash_valid": False}, "evidence_hash_mismatch"),
    ],
)
def test_unresolved_or_untrusted_evidence_never_approves(
    updates: dict[str, object],
    reason: str,
) -> None:
    snapshot = _snapshot()

    aggregate = aggregate_parallel_reviews(
        _context(snapshot, **updates),
        _approve_results(snapshot),
    )

    assert aggregate.verdict == "needs_human"
    assert reason in aggregate.reasons


def test_missing_or_duplicate_required_reviewer_never_approves() -> None:
    snapshot = _snapshot()
    results = _approve_results(snapshot)

    missing = aggregate_parallel_reviews(_context(snapshot), results[:2])
    duplicate = aggregate_parallel_reviews(
        _context(snapshot),
        [*results, _result(snapshot, "correctness_reviewer", suffix="9")],
    )

    assert missing.verdict == "needs_human"
    assert duplicate.verdict == "needs_human"
    assert "reviewer_result_set_incomplete" in missing.reasons
    assert "reviewer_result_set_incomplete" in duplicate.reasons


def test_timeout_or_provider_error_never_approves() -> None:
    snapshot = _snapshot()
    for status in ("timed_out", "provider_error", "termination_unconfirmed"):
        results = _approve_results(snapshot)
        results[0] = _result(
            snapshot,
            "correctness_reviewer",
            status=status,
            verdict="needs_human",
        )

        aggregate = aggregate_parallel_reviews(_context(snapshot), results)

        assert aggregate.verdict == "needs_human"
        assert "reviewer_execution_unresolved" in aggregate.reasons


def test_stale_reviewer_result_is_not_merged() -> None:
    current = _snapshot(suffix="1")
    stale = _snapshot(suffix="a")
    results = _approve_results(current)
    results[0] = _result(stale, "correctness_reviewer")

    aggregate = aggregate_parallel_reviews(_context(current), results)

    assert aggregate.verdict == "needs_human"
    assert "reviewer_result_identity_mismatch" in aggregate.reasons
    assert "reviewer_result_set_incomplete" in aggregate.reasons
    assert "correctness_reviewer" not in aggregate.reviewer_result_ids


def test_high_risk_requires_valid_human_approval() -> None:
    snapshot = _snapshot()
    results = _approve_results(snapshot)

    without_approval = aggregate_parallel_reviews(
        _context(snapshot, risk="high", human_approval_valid=False),
        results,
    )
    with_approval = aggregate_parallel_reviews(
        _context(snapshot, risk="high", human_approval_valid=True),
        results,
    )

    assert without_approval.verdict == "needs_human"
    assert without_approval.reasons == ["high_risk_without_approval"]
    assert with_approval.verdict == "approve"


def test_reviewer_request_changes_is_preserved_without_major_finding() -> None:
    snapshot = _snapshot()
    results = _approve_results(snapshot)
    results[0] = _result(
        snapshot,
        "correctness_reviewer",
        verdict="request_changes",
    )

    aggregate = aggregate_parallel_reviews(_context(snapshot), results)

    assert aggregate.verdict == "request_changes"
    assert aggregate.reasons == ["reviewer_requested_changes"]
