from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.selective_memory.models import MemoryEvent
from eval.selective_memory.projector import load_or_rebuild_snapshot, replay_events

EVIDENCE_SHA = "a" * 64


def test_event_replay_is_deterministic(make_add_event) -> None:
    events = [make_add_event()]
    kwargs = {
        "task_id": "task-001",
        "run_id": "run-001",
        "repo_identity": "repo-001",
        "evidence_hashes": {
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    }

    first = replay_events(events, **kwargs)
    second = replay_events(events, **kwargs)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.source_event_count == 1
    assert len(first.source_events_sha256) == 64
    assert [item.id for item in first.active_items] == ["failure-001"]


def test_stale_evidence_removes_verified_item_from_active(make_add_event) -> None:
    snapshot = replay_events(
        [make_add_event()],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": "b" * 64,
        },
    )

    assert snapshot.active_items == []
    assert snapshot.invalidated_items[0].invalidation_reason.startswith("evidence_stale:")


def test_snapshot_missing_or_mismatch_is_rebuilt(
    tmp_path: Path,
    make_add_event,
) -> None:
    path = tmp_path / "snapshot.json"
    events = [make_add_event()]
    kwargs = {
        "task_id": "task-001",
        "run_id": "run-001",
        "repo_identity": "repo-001",
        "evidence_hashes": {
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    }

    first, rebuilt, reason = load_or_rebuild_snapshot(path, events, **kwargs)
    assert rebuilt is True
    assert reason == "snapshot_missing"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_events_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    second, rebuilt, reason = load_or_rebuild_snapshot(path, events, **kwargs)

    assert rebuilt is True
    assert reason == "snapshot_mismatch"
    assert second == first


def test_replay_rejects_sequence_gap(make_add_event) -> None:
    event = make_add_event().model_copy(update={"seq": 2})
    with pytest.raises(ValueError, match="seq 不连续"):
        replay_events(
            [event],
            task_id="task-001",
            run_id="run-001",
            repo_identity="repo-001",
            evidence_hashes={},
        )


def test_replay_rejects_duplicate_event_id(make_add_event) -> None:
    first = make_add_event()
    second = make_add_event(seq=2, memory_id="failure-002").model_copy(
        update={"event_id": first.event_id}
    )

    with pytest.raises(ValueError, match="event_id 重复"):
        replay_events(
            [first, second],
            task_id="task-001",
            run_id="run-001",
            repo_identity="repo-001",
            evidence_hashes={
                "iterations/01/verification-result.json": EVIDENCE_SHA,
            },
        )


def test_replay_detects_conflicting_active_items(make_add_event) -> None:
    first = make_add_event(statement="必须保持接口 A")
    second_payload = make_add_event(
        seq=2,
        memory_id="constraint-002",
        statement="禁止保持接口 A",
        kind="constraint_interpretation",
        applicability={"target": "api"},
    ).model_dump(mode="json")
    first_payload = first.model_dump(mode="json")
    first_payload["item"]["kind"] = "constraint_interpretation"
    first_payload["item"]["applicability"] = {"target": "api"}
    first_payload["item"]["conflict_group"] = "api-contract"
    second_payload["item"]["conflict_group"] = "api-contract"
    first = MemoryEvent.model_validate(first_payload)
    second = MemoryEvent.model_validate(second_payload)

    snapshot = replay_events(
        [first, second],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )

    assert len(snapshot.conflicts) == 1
    assert snapshot.conflicts[0].item_ids == ["constraint-002", "failure-001"]


def test_different_statements_are_not_automatically_conflicts(make_add_event) -> None:
    first = make_add_event(
        statement="接口 A 已经通过验证",
        kind="confirmed_fact",
        applicability={"target": "api"},
    )
    second = make_add_event(
        seq=2,
        memory_id="fact-002",
        statement="接口 B 已经通过验证",
        kind="confirmed_fact",
        applicability={"target": "api"},
    )

    snapshot = replay_events(
        [first, second],
        task_id="task-001",
        run_id="run-001",
        repo_identity="repo-001",
        evidence_hashes={
            "iterations/01/verification-result.json": EVIDENCE_SHA,
        },
    )

    assert snapshot.conflicts == []


def test_low_authority_event_cannot_mutate_verified_item(make_add_event) -> None:
    update = MemoryEvent.model_validate(
        {
            "event_id": "me-000002",
            "seq": 2,
            "task_id": "task-001",
            "run_id": "run-001",
            "repo_identity": "repo-001",
            "op": "update",
            "memory_id": "failure-001",
            "patch": {
                "risk": "low",
                "updated_seq": 2,
            },
            "source_type": "tool",
            "source_ref": "tool-002",
        }
    )

    with pytest.raises(ValueError, match="不能修改 verified"):
        replay_events(
            [make_add_event(), update],
            task_id="task-001",
            run_id="run-001",
            repo_identity="repo-001",
            evidence_hashes={
                "iterations/01/verification-result.json": EVIDENCE_SHA,
            },
        )
