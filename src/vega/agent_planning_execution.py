from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from .agent_planning import PlanningRequest
from .agent_planning_execution_reservation import PlanningExecutionReservation
from .agent_persistence import read_agent_trace
from .execution_control import RunnerExecutionContext
from .progress import make_execution_progress_reporter
from .runner import Runner, RunnerResult
from .workspace_snapshot import ReviewWorkspaceSnapshot


class PreparedPlanningTurn(NamedTuple):
    run_dir: Path
    state_version: int
    request: PlanningRequest
    repo: Path
    before: ReviewWorkspaceSnapshot
    prompt: str
    runner: Runner
    attempt: int
    execution_id: str
    reservation: PlanningExecutionReservation


def planning_attempt(run_dir: Path) -> int:
    try:
        trace = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        return 1
    return (
        sum(item.get("event") == "planning_turn_started" for item in trace) + 1
    )


def execute_planning_turn(
    prepared: PreparedPlanningTurn,
    *,
    timeout_seconds: int,
    progress_reporter: Callable[[str, int], None] | None,
) -> RunnerResult:
    try:
        result = prepared.runner.run(
            prepared.prompt,
            prepared.repo,
            sandbox="read-only",
            timeout_seconds=timeout_seconds,
            execution_context=RunnerExecutionContext(
                execution_root=prepared.run_dir,
                execution_dir=(
                    prepared.run_dir
                    / "executions"
                    / "planning"
                    / prepared.execution_id
                ),
                run_id=prepared.run_dir.name,
                step="runner",
                execution_id=prepared.execution_id,
                iteration=prepared.attempt,
                progress_reporter=make_execution_progress_reporter(
                    prepared.run_dir,
                    progress_reporter,
                    iteration=prepared.attempt,
                ),
            ),
        )
    except Exception as exc:
        prepared.reservation.finish_if_unclaimed(None, error=exc)
        raise
    prepared.reservation.finish_if_unclaimed(result)
    return result
