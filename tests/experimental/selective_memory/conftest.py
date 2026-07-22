from __future__ import annotations

from typing import Any

import pytest

from eval.selective_memory.models import MemoryEvent

EVIDENCE_SHA = "a" * 64


@pytest.fixture
def make_add_event():
    def factory(
        *,
        seq: int = 1,
        memory_id: str = "failure-001",
        status: str = "active",
        source_type: str = "verification",
        authority: str = "verified",
        statement: str = "方案 A 在当前条件下已经验证失败。",
        applicability: dict[str, str] | None = None,
        evidence_sha: str = EVIDENCE_SHA,
        kind: str = "failed_attempt",
        risk: str = "medium",
        conflict_group: str | None = None,
    ) -> MemoryEvent:
        evidence_refs: list[dict[str, Any]] = []
        if authority == "verified":
            evidence_refs.append(
                {
                    "artifact": "iterations/01/verification-result.json",
                    "sha256": evidence_sha,
                }
            )
        return MemoryEvent.model_validate(
            {
                "event_id": f"me-{seq:06d}",
                "seq": seq,
                "task_id": "task-001",
                "run_id": "run-001",
                "repo_identity": "repo-001",
                "op": "add",
                "memory_id": memory_id,
                "item": {
                    "id": memory_id,
                    "task_id": "task-001",
                    "run_id": "run-001",
                    "repo_identity": "repo-001",
                    "kind": kind,
                    "statement": statement,
                    "status": status,
                    "source_type": source_type,
                    "source_ref": f"source-{seq}",
                    "evidence_refs": evidence_refs,
                    "authority": authority,
                    "risk": risk,
                    "applicability": (
                        applicability
                        if applicability is not None
                        else {
                            "action": "upgrade_dependency",
                            "dependency_version": "1.x",
                        }
                    ),
                    "created_seq": seq,
                    "updated_seq": seq,
                    "conflict_group": conflict_group,
                },
                "source_type": source_type,
                "source_ref": f"source-{seq}",
                "created_at": f"2026-07-13T00:00:{seq:02d}Z",
            }
        )

    return factory
