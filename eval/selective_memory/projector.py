from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from vega.redaction import write_redacted_json

from .models import (
    MemoryConflict,
    MemoryEvent,
    MemorySnapshot,
    RunMemoryItem,
    canonical_json_sha256,
)


def replay_events(
    events: list[MemoryEvent],
    *,
    task_id: str,
    run_id: str,
    repo_identity: str,
    evidence_hashes: Mapping[str, str],
) -> MemorySnapshot:
    """从事件完整重建 snapshot；任何序列或身份异常都直接 fail-closed。"""
    items: dict[str, RunMemoryItem] = {}
    event_ids: set[str] = set()
    for expected_seq, event in enumerate(events, start=1):
        if event.seq != expected_seq:
            raise ValueError(
                f"MemoryEvent seq 不连续：期望 {expected_seq}，实际 {event.seq}"
            )
        if (
            event.task_id != task_id
            or event.run_id != run_id
            or event.repo_identity != repo_identity
        ):
            raise ValueError("MemoryEvent 与 replay 的 repo/run/task 绑定不一致")
        if event.event_id in event_ids:
            raise ValueError(f"MemoryEvent event_id 重复：{event.event_id}")
        event_ids.add(event.event_id)

        if event.op == "add":
            if event.memory_id in items:
                raise ValueError(f"重复 add memory_id：{event.memory_id}")
            assert event.item is not None
            items[event.memory_id] = event.item
            continue

        current = items.get(event.memory_id)
        if current is None:
            raise ValueError(f"{event.op} 引用了不存在的 memory_id：{event.memory_id}")
        event.assert_can_mutate(current)
        payload = current.model_dump(mode="json")
        payload.update(event.patch)
        items[event.memory_id] = RunMemoryItem.model_validate(payload)

    active: list[RunMemoryItem] = []
    candidates: list[RunMemoryItem] = []
    invalidated: list[RunMemoryItem] = []
    for item in items.values():
        if item.status == "active":
            stale_refs = [
                ref.artifact
                for ref in item.evidence_refs
                if evidence_hashes.get(ref.artifact) != ref.sha256
            ]
            if stale_refs:
                # 证据过期是 snapshot 的派生判断，不回写事件，也不伪造新的权威事实。
                invalidated.append(
                    RunMemoryItem.model_validate(
                        {
                            **item.model_dump(mode="json"),
                            "status": "invalidated",
                            "invalidation_reason": "evidence_stale:"
                            + ",".join(sorted(stale_refs)),
                        }
                    )
                )
            else:
                active.append(item)
        elif item.status == "candidate":
            candidates.append(item)
        else:
            invalidated.append(item)

    active.sort(key=lambda item: item.id)
    candidates.sort(key=lambda item: item.id)
    invalidated.sort(key=lambda item: item.id)
    return MemorySnapshot(
        task_id=task_id,
        run_id=run_id,
        repo_identity=repo_identity,
        source_event_count=len(events),
        source_events_sha256=canonical_json_sha256(
            [event.model_dump(mode="json") for event in events]
        ),
        active_items=active,
        candidate_items=candidates,
        invalidated_items=invalidated,
        conflicts=_find_conflicts(active),
    )


def load_or_rebuild_snapshot(
    path: Path,
    events: list[MemoryEvent],
    *,
    task_id: str,
    run_id: str,
    repo_identity: str,
    evidence_hashes: Mapping[str, str],
) -> tuple[MemorySnapshot, bool, str]:
    """读取派生 snapshot；缺失、损坏或哈希不一致时从事件重建。"""
    expected = replay_events(
        events,
        task_id=task_id,
        run_id=run_id,
        repo_identity=repo_identity,
        evidence_hashes=evidence_hashes,
    )
    if path.exists():
        try:
            current = MemorySnapshot.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, ValidationError):
            reason = "snapshot_invalid"
        else:
            if current.model_dump(mode="json") == expected.model_dump(mode="json"):
                return current, False, "snapshot_reused"
            reason = "snapshot_mismatch"
    else:
        reason = "snapshot_missing"

    write_redacted_json(path, expected.model_dump(mode="json"))
    return expected, True, reason


def _find_conflicts(active_items: list[RunMemoryItem]) -> list[MemoryConflict]:
    groups: dict[str, list[RunMemoryItem]] = defaultdict(list)
    for item in active_items:
        if item.conflict_group:
            groups[item.conflict_group].append(item)

    conflicts: list[MemoryConflict] = []
    for conflict_group, items in sorted(groups.items()):
        if len(items) < 2:
            continue
        conflicts.append(
            MemoryConflict(
                conflict_key=conflict_group,
                item_ids=sorted(item.id for item in items),
                kind=items[0].kind,
                applicability=items[0].applicability,
            )
        )
    return conflicts
