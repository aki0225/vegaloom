from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_contract import AgentPlan, AgentState


@dataclass(frozen=True)
class AgentRun:
    run_dir: Path
    state: AgentState
    plan: AgentPlan
