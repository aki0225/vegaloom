from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from .models import MemoryLedgerEntry, MemoryProposal
from .redaction import redact_value


def make_memory_proposal_id(source_run_id: str, title: str, content: str) -> str:
    digest = sha256(f"{source_run_id}\n{title}\n{content}".encode("utf-8")).hexdigest()[:12]
    return f"mp-{digest}"


class MemoryProposalStore:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "memory-proposals.jsonl"

    def append(self, proposal: MemoryProposal) -> None:
        payload = redact_value(proposal.model_dump(mode="json"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def list(self) -> list[MemoryProposal]:
        if not self.path.exists():
            return []
        proposals: list[MemoryProposal] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                proposals.append(MemoryProposal.model_validate_json(line))
        return proposals

    def get(self, proposal_id: str) -> MemoryProposal | None:
        for proposal in self.list():
            if proposal.id == proposal_id:
                return proposal
        return None


class MemoryLedgerStore:
    def __init__(self, workspace: Path) -> None:
        self.path = workspace / "memory" / "ledger.jsonl"

    def list_entries(self) -> list[MemoryLedgerEntry]:
        if not self.path.exists():
            return []
        entries: list[MemoryLedgerEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(MemoryLedgerEntry.model_validate_json(line))
        return entries

    def has_decision(self, proposal_id: str) -> bool:
        return any(entry.proposal_id == proposal_id for entry in self.list_entries())

    def append_decision(
        self,
        proposal: MemoryProposal,
        status: str,
        reason: str | None = None,
    ) -> MemoryLedgerEntry:
        if status not in {"accepted", "rejected"}:
            raise ValueError(f"不支持的 memory 决策：{status}")
        entry = MemoryLedgerEntry(
            proposal_id=proposal.id,
            source_run_id=proposal.source_run_id,
            type=proposal.type,
            title=proposal.title,
            content=proposal.content,
            confidence=proposal.confidence,
            sensitivity=proposal.sensitivity,
            tags=proposal.tags,
            status=status,  # type: ignore[arg-type]
            reason=reason,
            repo=proposal.repo,
            paths=proposal.paths,
        )
        safe_entry = MemoryLedgerEntry.model_validate(
            redact_value(entry.model_dump(mode="json"))
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe_entry.model_dump(), ensure_ascii=False) + "\n")
        return safe_entry

    def search(
        self,
        query: str = "",
        accepted_only: bool = True,
        repo: str | None = None,
        repo_unscoped_only: bool = False,
        tags: list[str] | None = None,
        path: str | None = None,
    ) -> list[MemoryLedgerEntry]:
        if repo is not None and repo_unscoped_only:
            raise ValueError("repo 与 repo_unscoped_only 不能同时使用")
        needle = query.lower().strip()
        tag_set = {tag.lower() for tag in tags or []}
        results: list[MemoryLedgerEntry] = []
        for entry in self.list_entries():
            if accepted_only and entry.status != "accepted":
                continue
            if repo is not None and not _entry_matches_repo(entry, repo):
                continue
            if repo_unscoped_only and entry.repo is not None:
                continue
            if tag_set and not tag_set.intersection({tag.lower() for tag in entry.tags}):
                continue
            if path and not _entry_matches_path(entry, path):
                continue
            if needle and needle not in _entry_search_text(entry):
                continue
            results.append(entry)
        return results


def _entry_matches_repo(entry: MemoryLedgerEntry, repo: str) -> bool:
    if entry.repo is None:
        return False
    return entry.repo.strip().casefold() == repo.strip().casefold()


def _entry_matches_path(entry: MemoryLedgerEntry, path: str) -> bool:
    needle = path.replace("\\", "/").lower()
    for item in entry.paths:
        if needle in item.replace("\\", "/").lower():
            return True
    return needle in _entry_search_text(entry)


def _entry_search_text(entry: MemoryLedgerEntry) -> str:
    return "\n".join(
        [
            entry.title,
            entry.content,
            entry.repo or "",
            " ".join(entry.tags),
            " ".join(entry.paths),
        ]
    ).lower()
