from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .execution_control import (
    ExecutionController,
    ExecutionLease,
    RunnerExecutionContext,
)
from .runner import RunnerResult


@dataclass(frozen=True)
class PlanningExecutionReservation:
    """在释放 run lock 前登记 Planning execution，封住启动并发窗口。"""

    context: RunnerExecutionContext
    started_at: str

    @property
    def path(self) -> Path:
        return self.context.execution_dir / "execution.json"

    def discard(self) -> None:
        """仅在 Agent State 尚未绑定 reservation 时撤销本次登记。"""

        lease = self._load_owned_placeholder()
        if lease is None:
            return
        self.path.unlink(missing_ok=True)
        try:
            self.context.execution_dir.rmdir()
        except OSError:
            pass

    def finish_if_unclaimed(
        self,
        result: RunnerResult | None,
        *,
        error: Exception | None = None,
    ) -> None:
        """自定义 Runner 未建立 owned process 时，把 reservation 写成真实终态。"""

        lease = self._load_owned_placeholder()
        if lease is None:
            return
        controller = ExecutionController(self.context)
        controller.lease = lease
        stop_request = controller.read_stop_request()
        if stop_request is not None:
            controller.mark_stop_requested(stop_request)
            controller.finish(
                "stopped",
                reason=f"Planning 已按 stop request 停止：{stop_request.reason}",
                returncode=None,
            )
            return
        if error is not None:
            controller.finish(
                "error",
                reason=f"Planning Runner 异常：{type(error).__name__}: {error}",
                returncode=None,
            )
            return
        assert result is not None
        status = result.status if result.status != "skipped" else "error"
        controller.finish(
            status,
            reason=(
                result.error
                if result.status != "skipped"
                else result.error or "Planning Runner 未执行"
            ),
            returncode=None,
        )

    def _load_owned_placeholder(self) -> ExecutionLease | None:
        try:
            lease = ExecutionLease.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        if (
            lease.run_id != self.context.run_id
            or lease.execution_id != self.context.execution_id
            or lease.started_at != self.started_at
            or lease.step != self.context.step
            or lease.iteration != self.context.iteration
            or lease.status != "starting"
            or lease.child_pid is not None
            or lease.command
        ):
            return None
        return lease


def reserve_planning_execution(
    run_dir: Path,
    *,
    execution_id: str,
    attempt: int,
    timeout_seconds: int,
) -> PlanningExecutionReservation:
    context = RunnerExecutionContext(
        execution_root=run_dir,
        execution_dir=run_dir / "executions" / "planning" / execution_id,
        run_id=run_dir.name,
        step="runner",
        execution_id=execution_id,
        iteration=attempt,
    )
    lease = ExecutionController(context).prepare([], timeout_seconds)
    return PlanningExecutionReservation(
        context=context,
        started_at=lease.started_at,
    )
