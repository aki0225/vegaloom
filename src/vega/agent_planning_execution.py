from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from .agent_planning import PlanningRequest
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


def execute_planning_turn(
    prepared: PreparedPlanningTurn,
    *,
    timeout_seconds: int,
    progress_reporter: Callable[[str, int], None] | None,
) -> RunnerResult:
    return prepared.runner.run(
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
