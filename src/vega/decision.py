from __future__ import annotations

from pathlib import Path
from typing import Literal

from .models import DecisionEntry
from .redaction import append_redacted_jsonl, redact_value
from .run_utils import resolve_run_dir

DECISION_TYPES = {"gate", "review", "finish", "memory", "custom"}


class DecisionStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.path = run_dir / "decisions.jsonl"

    def append(
        self,
        *,
        decision_type: str,
        decision: Literal["approved", "rejected"],
        reason: str,
        actor: str = "human",
        references: list[str] | None = None,
    ) -> DecisionEntry:
        normalized_type = decision_type.strip().lower()
        if normalized_type not in DECISION_TYPES:
            raise ValueError(f"不支持的 decision type：{decision_type}")
        if not reason.strip():
            raise ValueError("decision reason 不能为空")
        entry = DecisionEntry(
            run_id=self.run_dir.name,
            type=normalized_type,  # type: ignore[arg-type]
            decision=decision,
            reason=reason.strip(),
            actor=actor.strip() or "human",
            references=references or [],
        )
        safe_entry = DecisionEntry.model_validate(
            redact_value(entry.model_dump(mode="json"))
        )
        append_redacted_jsonl(self.path, safe_entry.model_dump(mode="json"))
        return safe_entry

    def list(self, decision_type: str | None = None) -> list[DecisionEntry]:
        if not self.path.exists():
            return []
        entries: list[DecisionEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = DecisionEntry.model_validate_json(line)
                entries.append(
                    DecisionEntry.model_validate(
                        redact_value(entry.model_dump(mode="json"))
                    )
                )
        if decision_type:
            normalized = decision_type.strip().lower()
            entries = [entry for entry in entries if entry.type == normalized]
        return entries

    def get(self, decision_id: str) -> DecisionEntry:
        normalized_id = decision_id.strip()
        if not normalized_id:
            raise ValueError("decision id 不能为空")
        matches = [
            entry
            for entry in self.list()
            if entry.id == normalized_id
        ]
        if not matches:
            raise ValueError(f"decision ledger 不存在：{normalized_id}")
        if len(matches) != 1:
            raise ValueError(
                f"decision ledger 包含重复 identity：{normalized_id}"
            )
        return matches[0]


def append_run_decision(
    workspace: Path,
    run: str,
    *,
    decision_type: str,
    decision: Literal["approved", "rejected"],
    reason: str,
    actor: str = "human",
    references: list[str] | None = None,
) -> DecisionEntry:
    run_dir = resolve_run_dir(workspace, run)
    return DecisionStore(run_dir).append(
        decision_type=decision_type,
        decision=decision,
        reason=reason,
        actor=actor,
        references=references,
    )


def list_run_decisions(
    workspace: Path,
    run: str,
    decision_type: str | None = None,
) -> list[DecisionEntry]:
    run_dir = resolve_run_dir(workspace, run)
    return DecisionStore(run_dir).list(decision_type=decision_type)
