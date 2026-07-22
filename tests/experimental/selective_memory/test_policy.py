from __future__ import annotations

from eval.selective_memory.candidates import build_candidates
from eval.selective_memory.models import (
    InterventionCandidate,
    PlannedAction,
)
from eval.selective_memory.policy import decide_reminder
from eval.selective_memory.projector import replay_events

EVIDENCE_SHA = "a" * 64


def test_failed_attempt_reminds_and_dedupes(make_add_event) -> None:
    snapshot = replay_events(
        [make_add_event()],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )
    action = PlannedAction(
        checkpoint_id="cp-001",
        action="upgrade_dependency",
        summary="再次升级依赖",
        context={
            "action": "upgrade_dependency",
            "dependency_version": "1.x",
        },
    )
    candidates = build_candidates(snapshot, [], action)

    first = decide_reminder(action, candidates, snapshot)
    second = decide_reminder(
        action.model_copy(update={"checkpoint_id": "cp-002"}),
        candidates,
        snapshot,
        [first],
    )
    resumed = decide_reminder(
        action.model_copy(
            update={"checkpoint_id": "cp-003", "session_resumed": True}
        ),
        candidates,
        snapshot,
        [first, second],
    )

    assert first.decision == "remind"
    assert first.suppressed_by_dedupe is False
    assert second.suppressed_by_dedupe is True
    assert resumed.suppressed_by_dedupe is False


def test_pending_approval_blocks_without_dedupe() -> None:
    action = PlannedAction(
        checkpoint_id="cp-001",
        action="deploy",
        summary="部署到生产环境",
    )
    pending = InterventionCandidate(
        candidate_id="canonical:approval:deploy",
        source_layer="canonical_state",
        source_ref="decisions.jsonl#latest",
        kind="pending_approval",
        statement="部署尚未获得批准",
        authority="authoritative",
        risk="high",
    )
    from eval.selective_memory.models import MemorySnapshot

    snapshot = MemorySnapshot(
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        source_event_count=0,
        source_events_sha256="0" * 64,
    )

    first = decide_reminder(action, [pending], snapshot)
    second = decide_reminder(
        action.model_copy(update={"checkpoint_id": "cp-002"}),
        [pending],
        snapshot,
        [first],
    )

    assert first.decision == "block"
    assert second.decision == "block"
    assert second.suppressed_by_dedupe is False


def test_candidate_state_change_creates_new_dedupe_key(make_add_event) -> None:
    snapshot = replay_events(
        [make_add_event()],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )
    action = PlannedAction(
        checkpoint_id="cp-001",
        action="upgrade_dependency",
        summary="再次升级依赖",
        context={
            "action": "upgrade_dependency",
            "dependency_version": "1.x",
        },
    )
    candidates = build_candidates(snapshot, [], action)
    first = decide_reminder(action, candidates, snapshot)
    changed_candidates = [
        item.model_copy(update={"risk": "high", "source_ref": "verification-002"})
        for item in candidates
    ]

    changed = decide_reminder(
        action.model_copy(update={"checkpoint_id": "cp-002"}),
        changed_candidates,
        snapshot,
        [first],
    )

    assert changed.suppressed_by_dedupe is False
    assert changed.dedupe_key != first.dedupe_key


def test_high_risk_unknown_applicability_escalates(make_add_event) -> None:
    snapshot = replay_events(
        [
            make_add_event(
                risk="high",
                applicability={
                    "action": "upgrade_dependency",
                    "dependency_version": "1.x",
                    "api_version": "v2",
                },
            )
        ],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )
    action = PlannedAction(
        checkpoint_id="cp-unknown",
        action="upgrade_dependency",
        summary="缺少 API 版本信息",
        context={
            "action": "upgrade_dependency",
            "dependency_version": "1.x",
        },
    )
    candidates = build_candidates(snapshot, [], action)

    decision = decide_reminder(action, candidates, snapshot)

    assert decision.decision == "escalate"
    assert decision.reason_code == "applicability_unknown"


def test_unscoped_failed_attempt_does_not_remind_unrelated_action(
    make_add_event,
) -> None:
    snapshot = replay_events(
        [
            make_add_event(
                risk="medium",
                applicability={},
            )
        ],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )
    action = PlannedAction(
        checkpoint_id="cp-unrelated",
        action="write_documentation",
        summary="改写文档",
        context={"action": "write_documentation"},
    )
    candidates = build_candidates(snapshot, [], action)

    decision = decide_reminder(action, candidates, snapshot)

    assert decision.decision == "allow"
    assert decision.reason_code == "none"


def test_unscoped_high_risk_failed_attempt_escalates(make_add_event) -> None:
    snapshot = replay_events(
        [
            make_add_event(
                risk="high",
                applicability={},
            )
        ],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )
    action = PlannedAction(
        checkpoint_id="cp-unscoped-high",
        action="worker_attempt",
        summary="准备再次执行 worker",
        context={"action": "worker_attempt"},
    )
    candidates = build_candidates(snapshot, [], action)

    decision = decide_reminder(action, candidates, snapshot)

    assert decision.decision == "escalate"
    assert decision.reason_code == "applicability_unknown"
