from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from .models import MemoryProposal
from .redaction import redact_value


def make_memory_proposal_id(source_run_id: str, title: str, content: str) -> str:
    digest = sha256(f"{source_run_id}\n{title}\n{content}".encode("utf-8")).hexdigest()[:12]
    return f"mp-{digest}"


class MemoryProposalStore:
    """读写单次 run 的可选 proposal，不负责长期 Memory ledger。"""

    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "memory-proposals.jsonl"

    def append(self, proposal: MemoryProposal) -> None:
        payload = redact_value(proposal.model_dump(mode="json"))
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

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
