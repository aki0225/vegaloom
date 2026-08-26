from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_change_revision_runtime import revise_change_run
from .agent_contract import AgentPlan, AgentState
from .agent_mutation import agent_mutation
from .agent_run import AgentRun


class _ChangeRuntimeHost(Protocol):
    workspace: Path

    def _load_run(
        self,
        run: str,
    ) -> tuple[Path, AgentState, AgentPlan, dict[str, object]]: ...


class ChangeRevisionRuntimeMixin:
    """把 ChangeRun revision 生命周期留在独立职责模块。"""

    workspace: Path

    @agent_mutation("agent.replan")
    def revise_change(
        self: _ChangeRuntimeHost,
        run: str,
        *,
        proposed_contract: ChangeContract,
        proposed_execution_plan: ExecutionPlan,
    ) -> AgentRun:
        run_dir, state, plan, metadata = self._load_run(run)
        return revise_change_run(
            run_dir,
            state,
            plan,
            metadata,
            proposed_contract=proposed_contract,
            proposed_execution_plan=proposed_execution_plan,
        )
