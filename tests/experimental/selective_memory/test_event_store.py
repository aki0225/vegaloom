from __future__ import annotations

from pathlib import Path

import pytest

from eval.selective_memory.event_store import EventStore


def test_event_store_appends_redacted_events(tmp_path: Path, make_add_event) -> None:
    store = EventStore(tmp_path, "events/run-001.jsonl")
    fake_secret = "sk-" + "EXAMPLEVALUE1234567890"
    event = make_add_event(statement=f"工具输出包含 {fake_secret}")

    persisted = store.append(event)

    raw = store.path.read_text(encoding="utf-8")
    assert fake_secret not in raw
    assert "[REDACTED]" in raw
    assert store.read() == [persisted]


def test_event_store_rejects_gap_and_binding_mismatch(
    tmp_path: Path,
    make_add_event,
) -> None:
    store = EventStore(tmp_path, "events/run-001.jsonl")
    store.append(make_add_event())

    with pytest.raises(ValueError, match="必须连续"):
        store.append(make_add_event(seq=3, memory_id="failure-003"))

    mismatched = make_add_event(seq=2, memory_id="failure-002")
    mismatched = mismatched.model_copy(update={"run_id": "another-run"})
    with pytest.raises(ValueError, match="绑定不一致"):
        store.append(mismatched)


def test_event_store_rejects_existing_duplicate_event_id(
    tmp_path: Path,
    make_add_event,
) -> None:
    store = EventStore(tmp_path, "events/run-001.jsonl")
    store.append(make_add_event())
    duplicate = make_add_event(seq=2, memory_id="failure-002").model_copy(
        update={"event_id": "me-000001"}
    )

    with pytest.raises(ValueError, match="event_id 重复"):
        store.append(duplicate)


@pytest.mark.parametrize("relative_path", ["../outside.jsonl", "/outside.jsonl"])
def test_event_store_rejects_workspace_escape(
    tmp_path: Path,
    relative_path: str,
) -> None:
    with pytest.raises(ValueError, match="workspace"):
        EventStore(tmp_path, relative_path)
