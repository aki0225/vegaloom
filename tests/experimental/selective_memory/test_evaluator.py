from __future__ import annotations

import json
from pathlib import Path

from eval.selective_memory import evaluator
from eval.selective_memory.evaluator import (
    evaluate_cases,
    load_cases,
    load_golden,
)
from eval.selective_memory.models import ReminderDecision

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = PROJECT_ROOT / "eval" / "selective_memory"


def test_full_offline_evaluation_is_reproducible(tmp_path: Path) -> None:
    cases = load_cases(EXPERIMENT_ROOT / "cases")
    labels = load_golden(EXPERIMENT_ROOT / "golden")

    first = evaluate_cases(cases, labels, tmp_path / "first")
    second = evaluate_cases(cases, labels, tmp_path / "second")

    stable_keys = [
        "evidence_status",
        "case_count",
        "checkpoint_count",
        "labeled_checkpoint_count",
        "mode_evaluation_count",
        "d_injected_characters_vs_c",
        "safety_metrics",
        "quality_metrics",
        "sample_evidence",
        "phase2_gates",
        "decision",
        "llm_request_count",
        "real_task_success_claimed",
    ]
    for key in stable_keys:
        assert first[key] == second[key]
    for mode in ("A", "B", "C", "D"):
        for metric, value in first["mode_metrics"][mode].items():
            if metric == "selector_policy_ns":
                continue
            assert value == second["mode_metrics"][mode][metric]

    assert first["case_count"] == 10
    assert first["checkpoint_count"] == 150
    assert first["labeled_checkpoint_count"] == 150
    assert first["mode_evaluation_count"] == 600
    assert first["evidence_status"] == "full-offline-evidence"
    assert first["sample_evidence"]["status"] == "sufficient-offline-samples"
    assert first["safety_metrics"]["candidate_parity"] is True
    assert first["safety_metrics"]["memory_contamination_count"] == 0
    assert first["llm_request_count"] == 0
    assert first["real_task_success_claimed"] is False
    assert first["decision"] == "candidate-for-shadow"


def test_dataset_has_ten_cases_and_fifteen_checkpoints_each() -> None:
    cases = load_cases(EXPERIMENT_ROOT / "cases")
    labels = load_golden(EXPERIMENT_ROOT / "golden")

    assert len(cases) == 10
    assert {len(case.checkpoints) for case in cases} == {15}
    assert len(labels) == 150
    assert sum(
        label.expected_decision != "allow" for label in labels.values()
    ) == 33
    assert sum(label.is_high_risk for label in labels.values()) == 26
    assert sum(
        len(label.expected_candidate_ids) for label in labels.values()
    ) == 40


def test_d_cost_is_lower_than_top_k_and_reports_raw_counts(tmp_path: Path) -> None:
    metrics = evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        tmp_path / "run",
    )

    c = metrics["mode_metrics"]["C"]
    d = metrics["mode_metrics"]["D"]
    ratio = metrics["d_injected_characters_vs_c"]

    assert c["injected_characters"] > 0
    assert d["injected_characters"] < c["injected_characters"]
    assert ratio["numerator"] == d["injected_characters"]
    assert ratio["denominator"] == c["injected_characters"]
    assert ratio["defined"] is True
    assert ratio["value"] <= 0.7
    assert (tmp_path / "run" / "metrics.json").exists()
    assert (tmp_path / "run" / "raw-results.json").exists()
    assert (tmp_path / "run" / "EVAL-REPORT.md").exists()
    assert (tmp_path / "run" / "PHASE-2-DECISION.md").exists()


def test_c_and_d_use_identical_candidate_ids(tmp_path: Path) -> None:
    output = tmp_path / "run"
    evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        output,
    )

    records = json.loads((output / "raw-results.json").read_text(encoding="utf-8"))
    checkpoints = {item["checkpoint_id"] for item in records}
    for checkpoint_id in checkpoints:
        c = next(
            item
            for item in records
            if item["checkpoint_id"] == checkpoint_id and item["mode"] == "C"
        )
        d = next(
            item
            for item in records
            if item["checkpoint_id"] == checkpoint_id and item["mode"] == "D"
        )
        assert c["candidate_ids"] == d["candidate_ids"]


def test_canonical_gate_applies_to_every_mode(tmp_path: Path) -> None:
    output = tmp_path / "run"
    evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        output,
    )

    records = json.loads((output / "raw-results.json").read_text(encoding="utf-8"))
    approval = [
        item
        for item in records
        if item["checkpoint_id"] == "pending-approval-cp-05"
    ]

    assert {item["mode"] for item in approval} == {"A", "B", "C", "D"}
    assert {
        (item["decision"]["decision"], item["decision"]["reason_code"])
        for item in approval
    } == {("block", "pending_approval_conflict")}


def test_full_metrics_report_raw_counts_and_negative_samples(
    tmp_path: Path,
) -> None:
    metrics = evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        tmp_path / "run",
    )

    d = metrics["mode_metrics"]["D"]
    assert d["relevant_item_recall"]["numerator"] == 40
    assert d["relevant_item_recall"]["denominator"] == 40
    assert d["decision_recall"]["numerator"] == 33
    assert d["decision_recall"]["denominator"] == 33
    assert d["decision_precision"]["numerator"] == 33
    assert d["decision_precision"]["denominator"] == 33
    assert d["overblocking_rate"]["denominator"] == 117
    assert d["overblocking_rate"]["numerator"] == 0
    assert metrics["quality_metrics"]["false_positive_count"] == 0
    assert metrics["quality_metrics"]["false_negative_count"] == 0


def test_only_d_reports_selective_decision_metrics(tmp_path: Path) -> None:
    metrics = evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        tmp_path / "run",
    )

    for mode in ("A", "B", "C"):
        assert metrics["mode_metrics"][mode]["decision_precision"]["defined"] is False
        assert metrics["mode_metrics"][mode]["decision_recall"]["defined"] is False
    assert metrics["mode_metrics"]["D"]["decision_precision"]["defined"] is True
    assert metrics["mode_metrics"]["D"]["decision_recall"]["defined"] is True


def test_evaluator_rejects_policy_that_silently_allows_everything(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def always_allow(action, candidates, snapshot, recent_decisions=None, **kwargs):
        return ReminderDecision(
            checkpoint_id=action.checkpoint_id,
            decision="allow",
            reason_code="none",
            risk="low",
        )

    monkeypatch.setattr(evaluator, "decide_reminder", always_allow)
    metrics = evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        tmp_path / "run",
    )

    assert metrics["decision"] == "reject"
    assert metrics["mode_metrics"]["D"]["false_negative_count"] == 33
    assert metrics["phase2_gates"]["decision_recall_at_least_80_percent"] is False


def test_evaluator_rejects_weakened_high_risk_decisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original = evaluator.decide_reminder

    def weaken_block(action, candidates, snapshot, recent_decisions=None, **kwargs):
        decision = original(
            action,
            candidates,
            snapshot,
            recent_decisions,
            **kwargs,
        )
        if decision.decision in {"block", "escalate"}:
            return ReminderDecision(
                checkpoint_id=decision.checkpoint_id,
                decision="remind",
                reason_code="repeats_failed_attempt",
                risk=decision.risk,
                candidate_ids=decision.candidate_ids,
                reminder="weakened intervention",
            )
        return decision

    monkeypatch.setattr(evaluator, "decide_reminder", weaken_block)
    metrics = evaluate_cases(
        load_cases(EXPERIMENT_ROOT / "cases"),
        load_golden(EXPERIMENT_ROOT / "golden"),
        tmp_path / "run",
    )

    assert metrics["decision"] == "reject"
    assert (
        metrics["phase2_gates"][
            "high_risk_exact_decision_recall_at_least_90_percent"
        ]
        is False
    )


def test_unlabeled_checkpoint_is_excluded_from_quality_denominators(
    tmp_path: Path,
) -> None:
    cases = load_cases(EXPERIMENT_ROOT / "cases")
    labels = load_golden(EXPERIMENT_ROOT / "golden")
    labels.pop("requirement-change-cp-01")

    metrics = evaluate_cases(cases, labels, tmp_path / "run")

    assert metrics["checkpoint_count"] == 150
    assert metrics["labeled_checkpoint_count"] == 149
    assert metrics["mode_metrics"]["D"]["decision_accuracy"]["denominator"] == 149
