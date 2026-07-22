from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from vega.redaction import redact_value

from .candidates import (
    build_always_on_candidates,
    build_candidates,
    render_candidate_context,
    select_top_k,
)
from .models import (
    GoldenLabel,
    MemorySnapshot,
    OfflineCase,
    ReminderDecision,
)
from .policy import decide_reminder
from .projector import load_or_rebuild_snapshot, replay_events

Mode = Literal["A", "B", "C", "D"]
MODES: tuple[Mode, ...] = ("A", "B", "C", "D")
CANONICAL_REASON_CODES = frozenset(
    {
        "pending_approval_conflict",
        "superseded_goal",
        "violates_constraint",
    }
)


def load_cases(case_dir: Path) -> list[OfflineCase]:
    cases: list[OfflineCase] = []
    for path in sorted(case_dir.glob("*.json")):
        try:
            cases.append(
                OfflineCase.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"离线 case 无效：{path.name}") from exc
    if not cases:
        raise ValueError("没有找到离线 case")
    return cases


def load_golden(golden_dir: Path) -> dict[str, GoldenLabel]:
    labels: dict[str, GoldenLabel] = {}
    for path in sorted(golden_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                label = GoldenLabel.model_validate(entry)
                if label.checkpoint_id in labels:
                    raise ValueError(f"golden checkpoint 重复：{label.checkpoint_id}")
                labels[label.checkpoint_id] = label
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"Golden label 无效：{path.name}") from exc
    if not labels:
        raise ValueError("没有找到 Golden label")
    return labels


def evaluate_cases(
    cases: list[OfflineCase],
    labels: dict[str, GoldenLabel],
    output_dir: Path,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    parity_checks: list[bool] = []
    replay_checks: list[bool] = []
    rebuild_checks: list[bool] = []
    resume_decision_checks: list[bool] = []
    recent_d_decisions: dict[str, list[ReminderDecision]] = {}

    for case in cases:
        case_output = output_dir / "cases" / case.case_id
        case_output.mkdir(parents=True, exist_ok=True)
        recent = recent_d_decisions.setdefault(case.case_id, [])
        for checkpoint in case.checkpoints:
            events = case.events[: checkpoint.event_seq]
            snapshot = replay_events(
                events,
                task_id=case.task_id,
                run_id=case.run_id,
                repo_identity=case.repo_identity,
                evidence_hashes=case.evidence_hashes,
            )
            replay_again = replay_events(
                events,
                task_id=case.task_id,
                run_id=case.run_id,
                repo_identity=case.repo_identity,
                evidence_hashes=case.evidence_hashes,
            )
            replay_checks.append(snapshot == replay_again)

            rebuilt_snapshot: MemorySnapshot | None = None
            if checkpoint.rebuild_snapshot:
                snapshot_path = (
                    case_output / f"{checkpoint.checkpoint_id}-snapshot.json"
                )
                if snapshot_path.exists():
                    snapshot_path.unlink()
                rebuilt_snapshot, did_rebuild, _ = load_or_rebuild_snapshot(
                    snapshot_path,
                    events,
                    task_id=case.task_id,
                    run_id=case.run_id,
                    repo_identity=case.repo_identity,
                    evidence_hashes=case.evidence_hashes,
                )
                rebuild_checks.append(did_rebuild and rebuilt_snapshot == snapshot)

            always_started = time.perf_counter_ns()
            b_candidates = build_always_on_candidates(
                snapshot,
                checkpoint.canonical_candidates,
            )
            always_selector_ns = time.perf_counter_ns() - always_started

            selector_started = time.perf_counter_ns()
            candidates = build_candidates(
                snapshot,
                checkpoint.canonical_candidates,
                checkpoint.planned_action,
            )
            c_candidates = select_top_k(candidates, top_k)
            d_candidates = select_top_k(candidates, top_k)
            selector_ns = time.perf_counter_ns() - selector_started
            parity_checks.append(
                [item.model_dump(mode="json") for item in c_candidates]
                == [item.model_dump(mode="json") for item in d_candidates]
            )

            canonical_candidates = [
                item
                for item in candidates
                if item.source_layer == "canonical_state"
            ]
            canonical_started = time.perf_counter_ns()
            raw_canonical_decision = decide_reminder(
                checkpoint.planned_action,
                canonical_candidates,
                _snapshot_without_memory(snapshot),
                [],
                apply_dedupe=False,
            )
            canonical_policy_ns = time.perf_counter_ns() - canonical_started
            canonical_decision = _canonicalize_decision(
                raw_canonical_decision,
                checkpoint.planned_action.checkpoint_id,
            )

            d_started = time.perf_counter_ns()
            selective_decision = decide_reminder(
                checkpoint.planned_action,
                d_candidates,
                snapshot,
                recent,
            )
            d_policy_ns = time.perf_counter_ns() - d_started

            if rebuilt_snapshot is not None:
                rebuilt_candidates = select_top_k(
                    build_candidates(
                        rebuilt_snapshot,
                        checkpoint.canonical_candidates,
                        checkpoint.planned_action,
                    ),
                    top_k,
                )
                rebuilt_decision = decide_reminder(
                    checkpoint.planned_action,
                    rebuilt_candidates,
                    rebuilt_snapshot,
                    list(recent),
                )
                resume_decision_checks.append(
                    [
                        item.model_dump(mode="json")
                        for item in rebuilt_candidates
                    ]
                    == [item.model_dump(mode="json") for item in d_candidates]
                    and rebuilt_decision == selective_decision
                )

            reference_decision = (
                canonical_decision
                if canonical_decision.decision != "allow"
                else selective_decision
            )
            contexts = {
                "A": "",
                "B": render_candidate_context(b_candidates),
                "C": render_candidate_context(c_candidates),
                "D": (
                    selective_decision.reminder
                    + ("\n" if selective_decision.reminder else "")
                    if canonical_decision.decision == "allow"
                    else ""
                ),
            }
            mode_candidates = {
                "A": [],
                "B": b_candidates,
                "C": c_candidates,
                "D": d_candidates,
            }
            stale_candidate_ids = sorted(
                f"memory:{item.id}"
                for item in snapshot.invalidated_items
                if (item.invalidation_reason or "").startswith("evidence_stale:")
            )
            full_candidate_ids = {item.candidate_id for item in candidates}
            conflict_candidate_groups = [
                [f"memory:{item_id}" for item_id in conflict.item_ids]
                for conflict in snapshot.conflicts
                if all(
                    f"memory:{item_id}" in full_candidate_ids
                    for item_id in conflict.item_ids
                )
            ]

            for mode in MODES:
                if canonical_decision.decision != "allow":
                    decision = canonical_decision
                elif mode == "D":
                    decision = selective_decision
                else:
                    decision = canonical_decision
                elapsed_ns = {
                    "A": canonical_policy_ns,
                    "B": always_selector_ns + canonical_policy_ns,
                    "C": selector_ns + canonical_policy_ns,
                    "D": selector_ns + canonical_policy_ns + d_policy_ns,
                }[mode]
                context = contexts[mode]
                selected = mode_candidates[mode]
                records.append(
                    {
                        "case_id": case.case_id,
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "mode": mode,
                        "candidate_ids": [
                            item.candidate_id for item in selected
                        ],
                        "unknown_applicability_candidate_ids": [
                            item.candidate_id
                            for item in selected
                            if item.applicability_status == "unknown"
                        ],
                        "stale_candidate_ids": stale_candidate_ids,
                        "conflict_candidate_groups": conflict_candidate_groups,
                        "decision": decision.model_dump(mode="json"),
                        "injected_characters": len(context),
                        "injected_bytes": len(context.encode("utf-8")),
                        "injected_lines": len(context.splitlines()),
                        "selector_policy_ns": elapsed_ns,
                    }
                )
            recent.append(reference_decision)

    expected_checkpoints = {
        checkpoint.checkpoint_id
        for case in cases
        for checkpoint in case.checkpoints
    }
    unexpected_labels = sorted(set(labels) - expected_checkpoints)
    if unexpected_labels:
        raise ValueError(
            "Golden label 引用了不存在的 checkpoint："
            f"{unexpected_labels}"
        )

    metrics = _build_metrics(
        cases,
        labels,
        records,
        parity_checks,
        replay_checks,
        rebuild_checks,
        resume_decision_checks,
    )
    _write_json(output_dir / "raw-results.json", records)
    _write_json(output_dir / "metrics.json", metrics)
    _write_text(output_dir / "EVAL-REPORT.md", render_eval_report(metrics))
    _write_text(
        output_dir / "PHASE-2-DECISION.md",
        render_phase2_decision(metrics),
    )
    return redact_value(metrics)


def _build_metrics(
    cases: list[OfflineCase],
    labels: dict[str, GoldenLabel],
    records: list[dict[str, Any]],
    parity_checks: list[bool],
    replay_checks: list[bool],
    rebuild_checks: list[bool],
    resume_decision_checks: list[bool],
) -> dict[str, Any]:
    by_mode: dict[str, list[dict[str, Any]]] = {
        mode: [item for item in records if item["mode"] == mode]
        for mode in MODES
    }
    intervention_labels = {
        checkpoint_id: label
        for checkpoint_id, label in labels.items()
        if label.expected_decision != "allow"
    }
    allow_labels = {
        checkpoint_id: label
        for checkpoint_id, label in labels.items()
        if label.expected_decision == "allow"
    }
    high_risk = {
        checkpoint_id
        for checkpoint_id, label in intervention_labels.items()
        if label.is_high_risk
    }
    expected_item_pairs = {
        (checkpoint_id, candidate_id)
        for checkpoint_id, label in labels.items()
        for candidate_id in label.expected_candidate_ids
    }
    canonical_gate_labels = {
        checkpoint_id: label
        for checkpoint_id, label in labels.items()
        if label.expected_reason_code in CANONICAL_REASON_CODES
    }

    mode_metrics: dict[str, Any] = {}
    for mode, items in by_mode.items():
        labeled_items = [
            item for item in items if item["checkpoint_id"] in labels
        ]
        predicted_item_pairs = {
            (item["checkpoint_id"], candidate_id)
            for item in labeled_items
            for candidate_id in item["candidate_ids"]
        }
        candidate_true_positive = len(predicted_item_pairs & expected_item_pairs)
        shared_gate_correct = sum(
            _decision_matches_label(
                item["decision"],
                canonical_gate_labels[item["checkpoint_id"]],
            )
            for item in labeled_items
            if item["checkpoint_id"] in canonical_gate_labels
        )
        metrics: dict[str, Any] = {
            "checkpoint_count": len(items),
            "labeled_checkpoint_count": len(labeled_items),
            "candidate_count": sum(len(item["candidate_ids"]) for item in items),
            "injected_characters": sum(
                item["injected_characters"] for item in items
            ),
            "injected_bytes": sum(item["injected_bytes"] for item in items),
            "injected_lines": sum(item["injected_lines"] for item in items),
            "relevant_item_recall": (
                _ratio(candidate_true_positive, len(expected_item_pairs))
                if mode in {"B", "C", "D"}
                else _undefined_ratio()
            ),
            "shared_canonical_gate_accuracy": _ratio(
                shared_gate_correct,
                len(canonical_gate_labels),
            ),
            "selector_policy_ns": sum(
                item["selector_policy_ns"] for item in items
            ),
        }
        if mode != "D":
            metrics.update(
                {
                    "decision_accuracy": _undefined_ratio(),
                    "decision_precision": _undefined_ratio(),
                    "decision_recall": _undefined_ratio(),
                    "high_risk_recall": _undefined_ratio(),
                    "high_risk_exact_decision_recall": _undefined_ratio(),
                    "decision_confusion": {},
                    "overblocking_rate": _undefined_ratio(),
                    "false_positive_count": 0,
                    "false_negative_count": 0,
                }
            )
        mode_metrics[mode] = metrics

    d_items = {
        item["checkpoint_id"]: item
        for item in by_mode["D"]
        if item["checkpoint_id"] in labels
    }
    predicted = {
        checkpoint_id
        for checkpoint_id, item in d_items.items()
        if item["decision"]["decision"] != "allow"
    }
    interventions = set(intervention_labels)
    allows = set(allow_labels)
    true_positive = predicted & interventions
    false_positive = predicted & allows
    false_negative = interventions - predicted
    exact_correct = {
        checkpoint_id
        for checkpoint_id, item in d_items.items()
        if _decision_matches_label(
            item["decision"],
            labels[checkpoint_id],
        )
    }
    high_risk_exact = exact_correct & high_risk
    decision_confusion: dict[str, dict[str, int]] = {}
    for checkpoint_id, item in d_items.items():
        expected = labels[checkpoint_id].expected_decision
        observed = item["decision"]["decision"]
        decision_confusion.setdefault(expected, {})
        decision_confusion[expected][observed] = (
            decision_confusion[expected].get(observed, 0) + 1
        )
    mode_metrics["D"].update(
        {
            "decision_accuracy": _ratio(len(exact_correct), len(labels)),
            "decision_precision": _ratio(len(true_positive), len(predicted)),
            "decision_recall": _ratio(
                len(true_positive),
                len(interventions),
            ),
            "high_risk_recall": _ratio(
                len(predicted & high_risk),
                len(high_risk),
            ),
            "high_risk_exact_decision_recall": _ratio(
                len(high_risk_exact),
                len(high_risk),
            ),
            "decision_confusion": decision_confusion,
            "overblocking_rate": _ratio(
                len(false_positive),
                len(allows),
            ),
            "false_positive_count": len(false_positive),
            "false_negative_count": len(false_negative),
        }
    )

    reminder_labels = {
        checkpoint_id: label
        for checkpoint_id, label in labels.items()
        if label.expected_decision == "remind"
    }
    dedupe_correct = sum(
        d_items[checkpoint_id]["decision"]["suppressed_by_dedupe"]
        == label.expected_suppressed
        for checkpoint_id, label in reminder_labels.items()
    )
    conflict_labels = {
        checkpoint_id: label
        for checkpoint_id, label in labels.items()
        if label.expected_reason_code == "conflicting_candidates"
    }
    conflict_correct = sum(
        _decision_matches_label(
            d_items[checkpoint_id]["decision"],
            label,
        )
        for checkpoint_id, label in conflict_labels.items()
    )
    stale_reference_count = sum(
        bool(
            set(item["decision"]["candidate_ids"])
            & set(item["stale_candidate_ids"])
        )
        for item in d_items.values()
        if item["decision"]["decision"] != "allow"
    )
    stale_item_observation_count = sum(
        len(item["stale_candidate_ids"])
        for item in by_mode["D"]
    )
    unknown_applicability_observation_count = sum(
        len(item["unknown_applicability_candidate_ids"])
        for item in by_mode["D"]
    )
    conflict_bundle_total = sum(
        len(item["conflict_candidate_groups"])
        for item in by_mode["D"]
    )
    conflict_bundle_selected = sum(
        all(candidate_id in item["candidate_ids"] for candidate_id in group)
        for item in by_mode["D"]
        for group in item["conflict_candidate_groups"]
    )

    c_chars = mode_metrics["C"]["injected_characters"]
    d_chars = mode_metrics["D"]["injected_characters"]
    d_vs_c = _ratio(d_chars, c_chars)
    sample_requirements = {
        "intervention_samples": _sample_requirement(
            len(interventions),
            minimum=20,
        ),
        "high_risk_samples": _sample_requirement(
            len(high_risk),
            minimum=10,
        ),
        "relevant_candidate_samples": _sample_requirement(
            len(expected_item_pairs),
            minimum=20,
        ),
        "stale_rate_intervention_samples": _sample_requirement(
            len(predicted),
            minimum=20,
        ),
    }
    samples_sufficient = all(
        item["sufficient"] for item in sample_requirements.values()
    )
    sample_evidence = {
        "status": (
            "sufficient-offline-samples"
            if samples_sufficient
            else "insufficient-evidence"
        ),
        "requirements": sample_requirements,
        "reason": (
            "完整 Phase 2 离线样本量门槛已满足。"
            if samples_sufficient
            else "至少一项完整 Phase 2 样本量门槛未满足。"
        ),
    }

    safety = {
        "event_replay_determinism": all(replay_checks),
        "snapshot_rebuild_success": all(rebuild_checks) if rebuild_checks else False,
        "resume_decision_consistency": (
            all(resume_decision_checks) if resume_decision_checks else False
        ),
        "candidate_parity": all(parity_checks),
        "memory_contamination_count": _memory_contamination_count(cases),
        "silent_conflict_merge_count": _silent_conflict_merge_count(cases),
        "stale_item_observation_count": stale_item_observation_count,
        "unknown_applicability_observation_count": (
            unknown_applicability_observation_count
        ),
        "top_k_conflict_bundle_coverage": _ratio(
            conflict_bundle_selected,
            conflict_bundle_total,
        ),
    }
    quality = {
        "dedupe_suppression_accuracy": _ratio(
            dedupe_correct,
            len(reminder_labels),
        ),
        "conflict_escalation_accuracy": _ratio(
            conflict_correct,
            len(conflict_labels),
        ),
        "stale_reference_count": stale_reference_count,
        "stale_reference_rate": _ratio(
            stale_reference_count,
            len(predicted),
        ),
        "false_positive_count": len(false_positive),
        "false_negative_count": len(false_negative),
    }

    hard_safety_checks = {
        "event_replay_determinism": safety["event_replay_determinism"],
        "snapshot_rebuild_success": safety["snapshot_rebuild_success"],
        "resume_decision_consistency": safety["resume_decision_consistency"],
        "candidate_parity": safety["candidate_parity"],
        "memory_contamination_zero": safety["memory_contamination_count"] == 0,
        "silent_conflict_merge_zero": safety["silent_conflict_merge_count"] == 0,
        "top_k_conflict_bundle_complete": _ratio_at_least(
            safety["top_k_conflict_bundle_coverage"],
            1.0,
        ),
    }
    phase2_gates = {
        **hard_safety_checks,
        "sample_size_sufficient": samples_sufficient,
        "d_vs_c_characters_at_most_70_percent": _ratio_at_most(
            d_vs_c,
            0.7,
        ),
        "decision_precision_at_least_80_percent": _ratio_at_least(
            mode_metrics["D"]["decision_precision"],
            0.8,
        ),
        "decision_recall_at_least_80_percent": _ratio_at_least(
            mode_metrics["D"]["decision_recall"],
            0.8,
        ),
        "exact_decision_accuracy_at_least_90_percent": _ratio_at_least(
            mode_metrics["D"]["decision_accuracy"],
            0.9,
        ),
        "high_risk_recall_at_least_90_percent": _ratio_at_least(
            mode_metrics["D"]["high_risk_recall"],
            0.9,
        ),
        "high_risk_exact_decision_recall_at_least_90_percent": (
            _ratio_at_least(
                mode_metrics["D"]["high_risk_exact_decision_recall"],
                0.9,
            )
        ),
        "stale_reference_rate_below_5_percent": _ratio_below(
            quality["stale_reference_rate"],
            0.05,
        ),
        "relevant_item_recall_at_least_90_percent": _ratio_at_least(
            mode_metrics["D"]["relevant_item_recall"],
            0.9,
        ),
    }
    hard_safety_passed = all(hard_safety_checks.values())
    if not hard_safety_passed:
        decision = "reject"
    elif not samples_sufficient:
        decision = "continue-offline"
    elif all(phase2_gates.values()):
        decision = "candidate-for-shadow"
    else:
        decision = "reject"

    return {
        "schema_version": 1,
        "evidence_status": (
            "full-offline-evidence"
            if len(cases) >= 10 and samples_sufficient
            else "partial-evidence"
        ),
        "case_count": len(cases),
        "checkpoint_count": sum(len(case.checkpoints) for case in cases),
        "labeled_checkpoint_count": len(labels),
        "mode_evaluation_count": len(records),
        "mode_metrics": mode_metrics,
        "d_injected_characters_vs_c": d_vs_c,
        "safety_metrics": safety,
        "quality_metrics": quality,
        "sample_evidence": sample_evidence,
        "phase2_gates": phase2_gates,
        "decision": decision,
        "llm_request_count": 0,
        "real_task_success_claimed": False,
    }


def _decision_matches_label(
    decision: dict[str, Any],
    label: GoldenLabel,
) -> bool:
    return (
        decision["decision"] == label.expected_decision
        and decision["reason_code"] == label.expected_reason_code
        and sorted(decision["candidate_ids"]) == sorted(label.expected_candidate_ids)
        and decision["suppressed_by_dedupe"] == label.expected_suppressed
    )


def _snapshot_without_memory(snapshot: MemorySnapshot) -> MemorySnapshot:
    return snapshot.model_copy(
        update={
            "active_items": [],
            "candidate_items": [],
            "invalidated_items": [],
            "conflicts": [],
        }
    )


def _canonicalize_decision(
    decision: ReminderDecision,
    checkpoint_id: str,
) -> ReminderDecision:
    """Canonical State 门禁属于四组共享安全边界，不计入额外 Memory 注入。"""
    if decision.decision == "allow":
        return decision
    return decision.model_copy(
        update={
            "checkpoint_id": checkpoint_id,
            "reminder": "",
            "dedupe_key": "",
            "suppressed_by_dedupe": False,
        }
    )


def _memory_contamination_count(cases: list[OfflineCase]) -> int:
    count = 0
    for case in cases:
        for checkpoint in case.checkpoints:
            snapshot = replay_events(
                case.events[: checkpoint.event_seq],
                task_id=case.task_id,
                run_id=case.run_id,
                repo_identity=case.repo_identity,
                evidence_hashes=case.evidence_hashes,
            )
            count += sum(
                item.authority != "verified"
                for item in snapshot.active_items
            )
    return count


def _silent_conflict_merge_count(cases: list[OfflineCase]) -> int:
    count = 0
    for case in cases:
        for checkpoint in case.checkpoints:
            snapshot = replay_events(
                case.events[: checkpoint.event_seq],
                task_id=case.task_id,
                run_id=case.run_id,
                repo_identity=case.repo_identity,
                evidence_hashes=case.evidence_hashes,
            )
            candidates = build_candidates(
                snapshot,
                checkpoint.canonical_candidates,
                checkpoint.planned_action,
            )
            decision = decide_reminder(
                checkpoint.planned_action,
                candidates,
                snapshot,
                [],
                apply_dedupe=False,
            )
            applicable_conflicts = [
                conflict
                for conflict in snapshot.conflicts
                if all(
                    f"memory:{item_id}"
                    in {item.candidate_id for item in candidates}
                    for item_id in conflict.item_ids
                )
            ]
            if (
                applicable_conflicts
                and decision.reason_code != "conflicting_candidates"
            ):
                count += len(applicable_conflicts)
    return count


def _sample_requirement(actual: int, *, minimum: int) -> dict[str, Any]:
    return {
        "actual": actual,
        "minimum": minimum,
        "sufficient": actual >= minimum,
    }


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "defined": False,
            "value": None,
            "display": "insufficient-evidence",
        }
    value = numerator / denominator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "defined": True,
        "value": value,
        "display": f"{value:.1%}",
    }


def _undefined_ratio() -> dict[str, Any]:
    return {
        "numerator": 0,
        "denominator": 0,
        "defined": False,
        "value": None,
        "display": "not-applicable",
    }


def _ratio_at_least(value: dict[str, Any], threshold: float) -> bool:
    return bool(
        value["defined"]
        and value["value"] is not None
        and value["value"] >= threshold
    )


def _ratio_at_most(value: dict[str, Any], threshold: float) -> bool:
    return bool(
        value["defined"]
        and value["value"] is not None
        and value["value"] <= threshold
    )


def _ratio_below(value: dict[str, Any], threshold: float) -> bool:
    return bool(
        value["defined"]
        and value["value"] is not None
        and value["value"] < threshold
    )


def render_eval_report(metrics: dict[str, Any]) -> str:
    mode_lines = []
    for mode in MODES:
        item = metrics["mode_metrics"][mode]
        mode_lines.append(
            f"| {mode} | {item['candidate_count']} | "
            f"{item['injected_characters']} | {item['injected_bytes']} | "
            f"{item['relevant_item_recall']['display']} | "
            f"{item['shared_canonical_gate_accuracy']['display']} | "
            f"{item['decision_precision']['display']} | "
            f"{item['decision_recall']['display']} |"
        )
    safety = metrics["safety_metrics"]
    quality = metrics["quality_metrics"]
    d = metrics["mode_metrics"]["D"]
    sample_lines = [
        (
            f"- {name}：`{item['actual']}/{item['minimum']}`，"
            f"sufficient=`{str(item['sufficient']).lower()}`"
        )
        for name, item in metrics["sample_evidence"]["requirements"].items()
    ]
    failed_gates = [
        name
        for name, passed in metrics["phase2_gates"].items()
        if not passed
    ]
    return "\n".join(
        [
            "# Selective Memory Reminder 完整离线评估",
            "",
            f"- 证据状态：`{metrics['evidence_status']}`",
            f"- Case：`{metrics['case_count']}`",
            f"- Checkpoint：`{metrics['checkpoint_count']}`",
            f"- 已标注 Checkpoint：`{metrics['labeled_checkpoint_count']}`",
            f"- A/B/C/D 评估次数：`{metrics['mode_evaluation_count']}`",
            "- 真实 LLM 调用：`0`",
            "- 真实任务成功率声明：`否`",
            "",
            "## 原始规模与成本",
            "",
            "| 模式 | 候选数 | 注入字符 | 注入字节 | Relevant Item Recall | Canonical Gate Accuracy | Decision Precision | Decision Recall |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            *mode_lines,
            "",
            "说明：A/B/C 没有 Selective Decision，因此其 Decision 指标不适用；",
            "四组仍共享 Canonical State 门禁。",
            "",
            "## D 组决策质量",
            "",
            f"- Decision Accuracy：`{d['decision_accuracy']['display']}`",
            f"- Decision Precision：`{d['decision_precision']['display']}`",
            f"- Decision Recall：`{d['decision_recall']['display']}`",
            f"- High-risk Recall：`{d['high_risk_recall']['display']}`",
            f"- High-risk Exact Decision Recall："
            f"`{d['high_risk_exact_decision_recall']['display']}`",
            f"- Overblocking Rate：`{d['overblocking_rate']['display']}`",
            f"- False Positive / False Negative：`{quality['false_positive_count']} / {quality['false_negative_count']}`",
            f"- Dedupe Suppression Accuracy：`{quality['dedupe_suppression_accuracy']['display']}`",
            f"- Conflict Escalation Accuracy：`{quality['conflict_escalation_accuracy']['display']}`",
            f"- Stale Reference Count / Rate：`{quality['stale_reference_count']} / {quality['stale_reference_rate']['display']}`",
            "",
            "## 安全与一致性",
            "",
            f"- Event Replay Determinism：`{str(safety['event_replay_determinism']).lower()}`",
            f"- Snapshot Rebuild Success：`{str(safety['snapshot_rebuild_success']).lower()}`",
            f"- Resume Decision Consistency：`{str(safety['resume_decision_consistency']).lower()}`",
            f"- Candidate Parity：`{str(safety['candidate_parity']).lower()}`",
            f"- Top-K Conflict Bundle Coverage：`{safety['top_k_conflict_bundle_coverage']['display']}`",
            f"- Memory Contamination Count：`{safety['memory_contamination_count']}`",
            f"- Silent Conflict Merge Count：`{safety['silent_conflict_merge_count']}`",
            f"- Stale Item Observation Count：`{safety['stale_item_observation_count']}`",
            f"- Applicability Unknown Observation Count：`{safety['unknown_applicability_observation_count']}`",
            "",
            "## 样本门槛",
            "",
            f"- 状态：`{metrics['sample_evidence']['status']}`",
            *sample_lines,
            "",
            "## Phase 2 门禁",
            "",
            f"- 决策：`{metrics['decision']}`",
            f"- 未通过门禁：`{', '.join(failed_gates) if failed_gates else '无'}`",
            f"- D/C 注入字符比例：`{metrics['d_injected_characters_vs_c']['display']}`",
            "",
            "本报告只证明确定性离线机制和合成场景表现，不证明真实编码任务成功率提升。",
            "",
        ]
    )


def render_phase2_decision(metrics: dict[str, Any]) -> str:
    decision = metrics["decision"]
    ratio = metrics["d_injected_characters_vs_c"]
    failed_gates = [
        name
        for name, passed in metrics["phase2_gates"].items()
        if not passed
    ]
    if decision == "candidate-for-shadow":
        interpretation = (
            "完整离线门槛已通过，只能说明具备进入只观察 Shadow 评估的候选资格；"
            "仍未证明真实任务收益。"
        )
    elif decision == "continue-offline":
        interpretation = "样本或离线证据仍不足，应继续离线验证，不得进入 Shadow。"
    else:
        interpretation = "至少一项安全或完整离线质量门槛失败，当前方案不能进入 Shadow。"
    return "\n".join(
        [
            "# Phase 2 停止决策",
            "",
            f"## `{decision}`",
            "",
            f"- D/C 注入字符比例：`{ratio['display']}`",
            f"- Candidate Parity：`{str(metrics['safety_metrics']['candidate_parity']).lower()}`",
            f"- 样本状态：`{metrics['sample_evidence']['status']}`",
            f"- 未通过门禁：`{', '.join(failed_gates) if failed_gates else '无'}`",
            "",
            interpretation,
            "",
            "本轮严格停止在完整 Phase 2：不自动进入 Shadow，不修改 worker/reviewer prompt，",
            "不接入 `src/vega` runtime，不写 accepted memory，也不声称真实成功率提升。",
            "",
        ]
    )


def _write_json(path: Path, value: Any) -> None:
    """实验报告统一写 LF，避免 Windows CRLF 被 `git diff --check` 误判为空白。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_value(value)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        str(redact_value(text)),
        encoding="utf-8",
        newline="\n",
    )
