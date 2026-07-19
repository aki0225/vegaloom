from __future__ import annotations

from pathlib import Path
from typing import Literal

from .models import DecisionEntry
from .redaction import append_redacted_jsonl, redact_value
from .run_lock import RunMutationLock
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
        with RunMutationLock.acquire(self.run_dir, "decision.append"):
            return self._append_locked(
                decision_type=decision_type,
                decision=decision,
                reason=reason,
                actor=actor,
                references=references,
            )

    def _append_locked(
        self,
        *,
        decision_type: str,
        decision: Literal["approved", "rejected"],
        reason: str,
        actor: str,
        references: list[str] | None,
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
