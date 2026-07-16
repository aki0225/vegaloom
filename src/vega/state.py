from __future__ import annotations

from pathlib import Path

from .models import RunState


class StateStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.state_path = run_dir / "state.json"

    def save(self, state: RunState) -> None:
        state.save(self.state_path)
