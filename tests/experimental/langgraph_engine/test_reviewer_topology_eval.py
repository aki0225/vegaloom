from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from vega.parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    ParallelReviewAggregationContext,
    ParallelReviewFinding,
    ParallelReviewPlan,
    ParallelReviewResult,
    ReviewEvidenceSnapshot,
    aggregate_parallel_reviews,
    build_parallel_review_finding,
    build_parallel_review_plan,
    build_parallel_review_result,
    build_review_evidence_snapshot,
)
from vega.reviewer_topology_eval import (
    ProviderSessionBudget,
    ProviderSessionBudgetExceeded,
    ReviewerTopologyEvaluationDataset,
    ReviewerTopologyGroundTruthCase,
    ReviewerTopologyGroundTruthDataset,
    ReviewerTopologyGroundTruthFinding,
    ReviewerTopologyPublicCase,
    ReviewerTopologyPublicDataset,
    load_ground_truth,
    load_public_dataset,
    score_case,
    score_reviewer_topology_case,
    summarize_topology_scores,
    summarize_reviewer_topology,
)


def _snapshot() -> ReviewEvidenceSnapshot:
    return build_review_evidence_snapshot(
        run_id="gate55-run",
        iteration=1,
        workspace_fingerprint="sha256:" + "1" * 64,
        policy_snapshot_sha256="2" * 64,
        verification_result_sha256="3" * 64,
        risk_result_sha256="4" * 64,
        acceptance_evidence_manifest_sha256="5" * 64,
    )


def _plan(
    snapshot: ReviewEvidenceSnapshot,
    topology: str = "fixed_three",
) -> ParallelReviewPlan:
    return build_parallel_review_plan(
        {
            "run_id": snapshot.run_id,
            "iteration": snapshot.iteration,
            "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
            "verification_status": "passed",
            "verification_failed_count": 0,
            "risk": "low",
            "changed_files": ["src/example.py"],
            "gate_reason_codes": [],
        },
        topology=topology,  # type: ignore[arg-type]
    )


def _result(
    snapshot: ReviewEvidenceSnapshot,
    plan: ParallelReviewPlan,
    role: str,
    *,
    findings: list[ParallelReviewFinding] | None = None,
    verdict: str = "approve",
) -> ParallelReviewResult:
    suffix = str(AVAILABLE_REVIEWER_ROLES.index(role) + 6)
    return build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=snapshot.run_id,
        iteration=snapshot.iteration,
        reviewer_role=role,  # type: ignore[arg-type]
        attempt_id=f"attempt-{role}",
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        execution_ref=f"review/{role}/execution.json",
        execution_sha256=suffix * 64,
        status="completed",
        verdict=verdict,  # type: ignore[arg-type]
        summary="完成纯内存评测 fixture。",
        findings=findings or [],
        checked_items=["公共 evidence"],
    )


def _aggregate(
    snapshot: ReviewEvidenceSnapshot,
    plan: ParallelReviewPlan,
    results: list[ParallelReviewResult],
):
    context = ParallelReviewAggregationContext.model_validate(
        {
            "run_id": snapshot.run_id,
            "iteration": snapshot.iteration,
            "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
            "review_plan": plan,
            "verification_status": "passed",
            "verification_failed_count": 0,
            "risk": "low",
        }
    )
    return aggregate_parallel_reviews(context, results)


def _public_case(
    snapshot: ReviewEvidenceSnapshot,
    *,
    case_id: str = "case-correctness",
    case_kind: str = "correctness",
    workspace_fixture_sha256: str = "a" * 64,
) -> ReviewerTopologyPublicCase:
    return ReviewerTopologyPublicCase.model_validate(
        {
            "case_id": case_id,
            "case_kind": case_kind,
            "workspace_fixture_sha256": workspace_fixture_sha256,
            "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
        }
    )


def _truth_finding(
    *,
    finding_id: str = "gt-null-boundary",
) -> ReviewerTopologyGroundTruthFinding:
    return ReviewerTopologyGroundTruthFinding.model_validate(
        {
            "finding_id": finding_id,
            "category": "correctness",
            "rule_id": "null-boundary",
            "path": "src/example.py",
            "location": "l10-l12",
            "severity_range": ["major", "blocker"],
            "category_aliases": ["logic"],
            "rule_id_aliases": ["none-boundary"],
            "allowed_alternative_locations": ["function:parse"],
        }
    )


def _truth_case(
    snapshot: ReviewEvidenceSnapshot,
    *,
    case_id: str = "case-correctness",
    findings: list[ReviewerTopologyGroundTruthFinding] | None = None,
    expected_verdict: str = "request_changes",
    workspace_fixture_sha256: str = "a" * 64,
) -> ReviewerTopologyGroundTruthCase:
    expected_findings = (
        findings if findings is not None else [_truth_finding()]
    )
    return ReviewerTopologyGroundTruthCase.model_validate(
        {
            "case_id": case_id,
            "workspace_fixture_sha256": workspace_fixture_sha256,
            "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
            "expected_verdict": expected_verdict,
            "expected_findings": expected_findings,
            "forbidden_false_blocker_conditions": (
                ["any blocker finding", "any major finding"]
                if expected_verdict == "approve" and not expected_findings
                else []
            ),
        }
    )


def _finding(
    snapshot: ReviewEvidenceSnapshot,
    *,
    category: str = "correctness",
    rule_id: str = "null-boundary",
    location: str = "l10-l12",
    severity: str = "major",
) -> ParallelReviewFinding:
    return build_parallel_review_finding(
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        severity=severity,  # type: ignore[arg-type]
        category=category,
        rule_id=rule_id,
        path="src/example.py",
        location=location,
        title="边界条件未处理",
    )


def test_public_and_ground_truth_dataset_validate_binding() -> None:
    snapshot = _snapshot()
    public = ReviewerTopologyPublicDataset(
        cases=[
            _public_case(snapshot),
            _public_case(
                snapshot,
                case_id="case-clean",
                case_kind="clean",
                workspace_fixture_sha256="b" * 64,
            ),
        ]
    )
    truth = ReviewerTopologyGroundTruthDataset(
        cases=[
            _truth_case(snapshot),
            _truth_case(
                snapshot,
                case_id="case-clean",
                findings=[],
                expected_verdict="approve",
                workspace_fixture_sha256="b" * 64,
            ),
        ]
    )

    dataset = ReviewerTopologyEvaluationDataset(
        public_dataset=public,
        ground_truth=truth,
    )

    assert [case.case_id for case in dataset.ground_truth.cases] == [
        "case-correctness",
        "case-clean",
    ]

    tampered = truth.model_dump(mode="json")
    tampered["cases"][0]["evidence_snapshot_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="hash 绑定不一致"):
        ReviewerTopologyEvaluationDataset(
            public_dataset=public,
            ground_truth=ReviewerTopologyGroundTruthDataset.model_validate(tampered),
        )


def test_dataset_rejects_ground_truth_missing_clean_case() -> None:
    snapshot = _snapshot()
    public = ReviewerTopologyPublicDataset(
        cases=[
            _public_case(snapshot),
            _public_case(
                snapshot,
                case_id="case-clean",
                case_kind="clean",
                workspace_fixture_sha256="b" * 64,
            ),
        ]
    )

    with pytest.raises(ValidationError, match="全部 public case"):
        ReviewerTopologyEvaluationDataset(
            public_dataset=public,
            ground_truth=ReviewerTopologyGroundTruthDataset(cases=[_truth_case(snapshot)]),
        )


def test_dataset_rejects_implicit_ground_truth_commitment_fields() -> None:
    snapshot = _snapshot()
    implicit_truth = ReviewerTopologyGroundTruthCase(
        case_id="case-correctness",
        workspace_fixture_sha256="a" * 64,
        evidence_snapshot_sha256=snapshot.evidence_snapshot_sha256,
        expected_findings=[_truth_finding()],
    )

    with pytest.raises(ValidationError, match="必须显式声明"):
        ReviewerTopologyEvaluationDataset(
            public_dataset=ReviewerTopologyPublicDataset(cases=[_public_case(snapshot)]),
            ground_truth=ReviewerTopologyGroundTruthDataset(cases=[implicit_truth]),
        )


def test_harness_loaders_validate_json_and_ground_truth_sha256(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    public_path = tmp_path / "public.json"
    truth_path = tmp_path / "ground-truth.json"
    public_path.write_text(
        json.dumps({"cases": [_public_case(snapshot).model_dump(mode="json")]}),
        encoding="utf-8",
    )
    truth_bytes = json.dumps(
        {"cases": [_truth_case(snapshot).model_dump(mode="json")]},
        ensure_ascii=False,
    ).encode()
    truth_path.write_bytes(truth_bytes)

    public = load_public_dataset(public_path)
    truth = load_ground_truth(
        truth_path,
        expected_sha256="sha256:" + hashlib.sha256(truth_bytes).hexdigest(),
    )

    assert public.cases[0].case_id == truth.cases[0].case_id
    with pytest.raises(ValueError, match="预注册值不一致"):
        load_ground_truth(truth_path, expected_sha256="f" * 64)


def test_dataset_rejects_clean_case_with_hidden_expected_finding() -> None:
    snapshot = _snapshot()
    public = ReviewerTopologyPublicDataset(
        cases=[
            _public_case(
                snapshot,
                case_id="case-clean",
                case_kind="clean",
            )
        ]
    )

    with pytest.raises(ValidationError, match="clean case"):
        ReviewerTopologyEvaluationDataset(
            public_dataset=public,
            ground_truth=ReviewerTopologyGroundTruthDataset(
                cases=[
                    _truth_case(
                        snapshot,
                        case_id="case-clean",
                        expected_verdict="approve",
                    )
                ]
            ),
        )


@pytest.mark.parametrize(
    ("canonical_field", "canonical_value", "aliases_field", "aliases"),
    [
        (
            "normalized_path",
            "src/other.py",
            "path_aliases",
            ["src/example.py"],
        ),
        ("category", "logic", "category_aliases", ["correctness"]),
        ("rule_id", "none-boundary", "rule_aliases", ["null-boundary"]),
        (
            "normalized_location",
            "function:serialize",
            "location_aliases",
            ["l10-l12"],
        ),
    ],
)
def test_ground_truth_rejects_ambiguous_canonical_alias_intersections(
    canonical_field: str,
    canonical_value: str,
    aliases_field: str,
    aliases: list[str],
) -> None:
    first = _truth_finding(finding_id="gt-first")
    second_payload = first.model_dump(mode="json")
    second_payload["finding_id"] = "gt-second"
    second_payload[canonical_field] = canonical_value
    second_payload[aliases_field] = aliases
    second = ReviewerTopologyGroundTruthFinding.model_validate(second_payload)

    with pytest.raises(ValidationError, match="歧义匹配"):
        ReviewerTopologyGroundTruthCase(
            case_id="case-ambiguous",
            workspace_fixture_sha256="a" * 64,
            evidence_snapshot_sha256="b" * 64,
            expected_verdict="request_changes",
            expected_findings=[first, second],
        )


def test_case_score_prioritizes_exact_core_identity_over_alias_candidate() -> None:
    snapshot = _snapshot()
    alias_candidate = _truth_finding(finding_id="gt-alias-candidate")
    exact_payload = alias_candidate.model_dump(mode="json")
    exact_payload.update(
        {
            "finding_id": "gt-exact-candidate",
            "category": "logic",
            "category_aliases": [],
            "rule_id": "none-boundary",
            "rule_aliases": [],
            "normalized_location": "function:serialize",
            "location_aliases": [],
        }
    )
    exact_candidate = ReviewerTopologyGroundTruthFinding.model_validate(exact_payload)
    truth = _truth_case(
        snapshot,
        findings=[alias_candidate, exact_candidate],
    )
    plan = _plan(snapshot, topology="single")
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[
                _finding(
                    snapshot,
                    category="logic",
                    rule_id="none-boundary",
                    location="line:999",
                )
            ],
            verdict="request_changes",
        )
    ]

    score = score_case(
        "case-correctness",
        "single",
        _aggregate(snapshot, plan, results),
        results,
        truth,
    )

    assert score.true_positive_finding_ids == ["gt-exact-candidate"]
    assert score.false_negative_finding_ids == ["gt-alias-candidate"]
    assert score.false_positive_count == 0


@pytest.mark.parametrize(
    ("expected_severity", "predicted_severity"),
    [("major", "blocker"), ("blocker", "major")],
)
def test_case_score_keeps_major_and_blocker_severity_aliases_interchangeable(
    expected_severity: str,
    predicted_severity: str,
) -> None:
    snapshot = _snapshot()
    truth_payload = _truth_finding().model_dump(mode="json")
    truth_payload.update(
        {
            "severity_min": expected_severity,
            "severity_max": expected_severity,
            "severity_aliases": ["major", "blocker"],
        }
    )
    truth = _truth_case(
        snapshot,
        findings=[ReviewerTopologyGroundTruthFinding.model_validate(truth_payload)],
    )
    plan = _plan(snapshot, topology="single")
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[_finding(snapshot, severity=predicted_severity)],
            verdict="request_changes",
        )
    ]

    score = score_case(
        "case-correctness",
        "single",
        _aggregate(snapshot, plan, results),
        results,
        truth,
    )

    assert score.true_positive_finding_ids == ["gt-null-boundary"]
    assert score.blocker_major_recall == 1.0


def test_case_score_matches_rule_category_and_location_aliases() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot)
    alias_finding = _finding(
        snapshot,
        category="logic",
        rule_id="none-boundary",
        location="function:parse",
        severity="blocker",
    )
    results = [
        _result(
            snapshot,
            plan,
            role,
            findings=[alias_finding] if role == "correctness_reviewer" else [],
            verdict=("request_changes" if role == "correctness_reviewer" else "approve"),
        )
        for role in plan.required_roles
    ]
    aggregate = _aggregate(snapshot, plan, results)

    score = score_reviewer_topology_case(
        public_case=_public_case(snapshot),
        ground_truth=_truth_case(snapshot),
        topology="fixed_three",
        results=results,
        aggregate=aggregate,
    )

    assert score.finding_precision == 1.0
    assert score.finding_recall == 1.0
    assert score.blocker_major_recall == 1.0
    assert score.true_positive_finding_ids == ["gt-null-boundary"]
    assert score.verdict_correct is True


def test_single_truth_does_not_require_location_text_to_match() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="single")
    line_location = _finding(snapshot, location="line:117")
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[line_location],
            verdict="request_changes",
        )
    ]

    score = score_case(
        "case-correctness",
        "single",
        _aggregate(snapshot, plan, results),
        results,
        _truth_case(snapshot),
    )

    assert score.true_positive_finding_ids == ["gt-null-boundary"]
    assert score.false_positive_count == 0
    assert score.finding_recall == 1.0


def test_location_disambiguates_multiple_truths_with_same_core_identity() -> None:
    snapshot = _snapshot()
    first = _truth_finding(finding_id="gt-first")
    second_payload = first.model_dump(mode="json")
    second_payload["finding_id"] = "gt-second"
    second_payload["normalized_location"] = "function:serialize"
    second_payload["location_aliases"] = ["line:117"]
    second = ReviewerTopologyGroundTruthFinding.model_validate(second_payload)
    truth = _truth_case(snapshot, findings=[first, second])
    plan = _plan(snapshot, topology="single")
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[_finding(snapshot, location="line:117")],
            verdict="request_changes",
        )
    ]

    score = score_case(
        "case-correctness",
        "single",
        _aggregate(snapshot, plan, results),
        results,
        truth,
    )

    assert score.true_positive_finding_ids == ["gt-second"]
    assert score.false_negative_finding_ids == ["gt-first"]


def test_case_score_counts_duplicate_alias_outputs_and_false_positive() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot)
    canonical = _finding(snapshot)
    alias = _finding(
        snapshot,
        category="logic",
        rule_id="none-boundary",
        location="function:parse",
    )
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[canonical],
            verdict="request_changes",
        ),
        _result(
            snapshot,
            plan,
            "verification_adequacy_reviewer",
            findings=[alias],
            verdict="request_changes",
        ),
        _result(snapshot, plan, "security_design_reviewer"),
    ]
    aggregate = _aggregate(snapshot, plan, results)

    score = score_reviewer_topology_case(
        public_case=_public_case(snapshot),
        ground_truth=_truth_case(snapshot),
        topology="fixed_three",
        results=results,
        aggregate=aggregate,
    )

    assert score.true_positive_count == 1
    assert score.false_positive_count == 1
    assert score.finding_precision == 0.5
    assert score.raw_finding_count == 2
    assert score.unique_raw_finding_count == 1
    assert score.duplicate_finding_count == 1
    assert score.duplicate_ratio == 0.5


def test_case_score_uses_aggregate_for_clean_false_blocker_and_major() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot)
    blocker = _finding(snapshot, severity="blocker")
    major = _finding(
        snapshot,
        category="security",
        rule_id="unsafe-side-effect",
        location="l30",
        severity="major",
    )
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[blocker],
            verdict="request_changes",
        ),
        _result(
            snapshot,
            plan,
            "verification_adequacy_reviewer",
            findings=[major],
            verdict="request_changes",
        ),
        _result(snapshot, plan, "security_design_reviewer"),
    ]
    aggregate = _aggregate(snapshot, plan, results)
    public = _public_case(snapshot, case_id="case-clean", case_kind="clean")
    truth = _truth_case(
        snapshot,
        case_id="case-clean",
        findings=[],
        expected_verdict="approve",
    )

    score = score_reviewer_topology_case(
        public_case=public,
        ground_truth=truth,
        topology="fixed_three",
        results=results,
        aggregate=aggregate,
    )

    assert score.clean_false_blocker_count == 1
    assert score.clean_false_major_count == 1
    assert score.clean_has_false_blocker is True
    assert score.clean_has_false_major is True
    assert score.verdict_correct is False


def test_case_score_retains_unique_true_positive_ids_relative_to_single() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="single")
    finding = _finding(snapshot)
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[finding],
            verdict="request_changes",
        )
    ]

    score = score_reviewer_topology_case(
        public_case=_public_case(snapshot),
        ground_truth=_truth_case(snapshot),
        topology="single",
        results=results,
        aggregate=_aggregate(snapshot, plan, results),
        single_true_positive_finding_ids=[],
    )
    baseline_score = score_reviewer_topology_case(
        public_case=_public_case(snapshot),
        ground_truth=_truth_case(snapshot),
        topology="single",
        results=results,
        aggregate=_aggregate(snapshot, plan, results),
        single_true_positive_finding_ids=["gt-null-boundary"],
    )

    assert score.unique_true_positive_finding_ids == ["gt-null-boundary"]
    assert score.unique_true_positive_blocker_major_finding_ids == ["gt-null-boundary"]
    assert baseline_score.unique_true_positive_finding_ids == []


def test_harness_score_and_summary_entrypoints() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="single")
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[_finding(snapshot)],
            verdict="request_changes",
        )
    ]
    aggregate = _aggregate(snapshot, plan, results)

    score = score_case(
        "case-correctness",
        "single",
        aggregate,
        results,
        _truth_case(snapshot),
    )
    summary = summarize_topology_scores([score])

    assert score.true_positive_count == 1
    assert summary.topology == "single"
    assert summary.verdict_accuracy == 1.0


def test_harness_score_synthesizes_clean_truth_when_manifest_has_no_case() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="single")
    results = [_result(snapshot, plan, "correctness_reviewer")]

    score = score_case(
        "clean-case",
        "single",
        _aggregate(snapshot, plan, results),
        results,
        None,
    )

    assert score.expected_finding_count == 0
    assert score.expected_verdict == "approve"
    assert score.finding_precision == 1.0
    assert score.finding_recall == 1.0
    assert score.verdict_correct is True


def test_topology_summary_uses_micro_averages_and_single_baseline() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="single")
    finding = _finding(snapshot)
    results = [
        _result(
            snapshot,
            plan,
            "correctness_reviewer",
            findings=[finding],
            verdict="request_changes",
        )
    ]
    hit = score_reviewer_topology_case(
        public_case=_public_case(snapshot),
        ground_truth=_truth_case(snapshot),
        topology="single",
        results=results,
        aggregate=_aggregate(snapshot, plan, results),
    )
    clean_plan = _plan(snapshot, topology="single")
    clean_results = [_result(snapshot, clean_plan, "correctness_reviewer")]
    clean = score_reviewer_topology_case(
        public_case=_public_case(
            snapshot,
            case_id="case-clean",
            case_kind="clean",
        ),
        ground_truth=_truth_case(
            snapshot,
            case_id="case-clean",
            findings=[],
            expected_verdict="approve",
        ),
        topology="single",
        results=clean_results,
        aggregate=_aggregate(snapshot, clean_plan, clean_results),
    )

    summary = summarize_reviewer_topology(
        [hit, clean],
        single_scores={
            "case-correctness": hit.model_copy(update={"true_positive_finding_ids": []}),
            "case-clean": clean,
        },
    )

    assert summary.case_count == 2
    assert summary.finding_precision == 1.0
    assert summary.finding_recall == 1.0
    assert summary.blocker_major_recall == 1.0
    assert summary.verdict_accuracy == 1.0
    assert summary.unique_true_positive_keys == ["case-correctness:gt-null-boundary"]


def test_case_score_rejects_results_not_bound_to_aggregate() -> None:
    snapshot = _snapshot()
    plan = _plan(snapshot, topology="single")
    results = [_result(snapshot, plan, "correctness_reviewer")]
    aggregate = _aggregate(snapshot, plan, results)

    with pytest.raises(ValueError, match="observed_result_ids"):
        score_reviewer_topology_case(
            public_case=_public_case(snapshot),
            ground_truth=_truth_case(snapshot),
            topology="single",
            results=[],
            aggregate=aggregate,
        )


def test_provider_session_budget_fails_closed_before_session_91() -> None:
    budget = ProviderSessionBudget()

    def reserve_once(_: int) -> int | None:
        try:
            return budget.reserve_session()
        except ProviderSessionBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=24) as executor:
        reservations = list(executor.map(reserve_once, range(120)))

    granted = sorted(item for item in reservations if item is not None)
    assert granted == list(range(1, 91))
    assert sum(item is None for item in reservations) == 30
    assert budget.used_sessions == 90
    assert budget.remaining_sessions == 0
    with pytest.raises(ProviderSessionBudgetExceeded, match="启动前"):
        budget.reserve_provider_session()


def test_provider_session_budget_reserves_batch_atomically() -> None:
    budget = ProviderSessionBudget(max_sessions=5)

    assert budget.reserve(count=3) == 3
    with pytest.raises(ValueError, match="正整数"):
        budget.reserve(count=True)
    with pytest.raises(ProviderSessionBudgetExceeded):
        budget.reserve(count=3)
    assert budget.used_sessions == 3
    assert budget.reserve(count=2) == 5
