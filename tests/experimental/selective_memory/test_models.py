from __future__ import annotations

import pytest
from pydantic import ValidationError

from eval.selective_memory.models import MemoryEvent, RunMemoryItem


def test_verified_item_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence_refs"):
        RunMemoryItem.model_validate(
            {
                "id": "fact-001",
                "task_id": "task-001",
                "run_id": "run-001",
                "repo_identity": "repo-001",
                "kind": "confirmed_fact",
                "statement": "已确认事实",
                "status": "active",
                "source_type": "verification",
                "source_ref": "verification-001",
                "authority": "verified",
                "risk": "low",
                "created_seq": 1,
                "updated_seq": 1,
            }
        )


def test_inferred_or_untrusted_item_cannot_be_active(make_add_event) -> None:
    with pytest.raises(ValidationError, match="不能自动进入 active"):
        make_add_event(
            status="active",
            source_type="tool",
            authority="untrusted",
        )


def test_update_cannot_rewrite_statement(make_add_event) -> None:
    add = make_add_event()
    with pytest.raises(ValidationError, match="不能静默重写语义字段"):
        MemoryEvent.model_validate(
            {
                "event_id": "me-000002",
                "seq": 2,
                "task_id": add.task_id,
                "run_id": add.run_id,
                "repo_identity": add.repo_identity,
                "op": "update",
                "memory_id": add.memory_id,
                "patch": {
                    "statement": "被静默改写的内容",
                    "updated_seq": 2,
                },
                "source_type": "verification",
                "source_ref": "verification-002",
            }
        )


def test_confidence_is_not_part_of_experiment_schema(make_add_event) -> None:
    payload = make_add_event().item.model_dump(mode="json")
    payload["confidence"] = 0.99
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunMemoryItem.model_validate(payload)
