from __future__ import annotations

from pathlib import Path

from .models import LoopAutomationState, WorkerRerunAuthorization
from .trace import TraceWriter
from .worker_baseline import (
    bind_auto_worker_workspace_baseline,
    capture_auto_worker_workspace_baseline,
)
from .worker_rerun_transaction import (
    commit_worker_rerun_authorization,
    complete_worker_rerun_start,
    prepare_worker_rerun_baseline,
)
from .worker_rerun_planning import select_worker_workspace_snapshot
from .workspace_inventory import WorkspaceSnapshot


class WorkerStartBoundaryChanged(ValueError):
    """最终 Worker 启动边界的工作区已偏离授权快照。"""


def begin_auto_worker_iteration(
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
    *,
    iteration: int,
    start_iteration: int,
    rerun_authorization: WorkerRerunAuthorization | None,
    recovered_workspace_baseline: WorkspaceSnapshot | None,
) -> tuple[WorkerRerunAuthorization | None, WorkerRerunAuthorization | None]:
    """开始一轮 Worker；显式重跑在 claim 前只提交授权事务。"""

    authorization = (
        rerun_authorization if iteration == start_iteration else None
    )
    if authorization is not None:
        if recovered_workspace_baseline is None:
            raise ValueError("Worker 重跑缺少授权时工作区快照")
        commit_worker_rerun_authorization(
            run_dir,
            state,
            trace,
            authorization=authorization,
            expected_workspace_snapshot=recovered_workspace_baseline,
        )
    else:
        state.status = "running"
        state.current_iteration = iteration
        state.current_step = "worker"
        state.save(run_dir / "state.json")
    return authorization, None


def prepare_auto_worker_start(
    workspace: Path,
    repo_path: Path,
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
    *,
    iteration: int,
    start_iteration: int,
    worker_name: str,
    workspace_baseline: WorkspaceSnapshot,
    recovered_workspace_baseline: WorkspaceSnapshot | None,
    rerun_authorization: WorkerRerunAuthorization | None,
) -> None:
    """紧邻 worker.run() 准备 baseline、claim 和启动证据。"""

    final_baseline = workspace_baseline
    if rerun_authorization is not None:
        try:
            final_baseline, _ = select_worker_workspace_snapshot(
                workspace,
                repo_path,
                recovered_workspace_baseline,
                iteration_number=iteration,
                start_iteration=start_iteration,
            )
        except ValueError as exc:
            raise WorkerStartBoundaryChanged from exc
        digest = prepare_worker_rerun_baseline(
            run_dir,
            trace,
            authorization=rerun_authorization,
            snapshot=final_baseline,
        )
        state.status = "running"
        state.current_iteration = iteration
        state.current_step = "worker"
        bind_auto_worker_workspace_baseline(
            state,
            iteration=iteration,
            digest=digest,
        )
        state.save(run_dir / "state.json")
    else:
        capture_auto_worker_workspace_baseline(
            run_dir,
            state,
            trace,
            iteration=iteration,
            snapshot=final_baseline,
        )
    trace.write("worker_started", iteration=iteration, runner=worker_name)
    if rerun_authorization is not None:
        complete_worker_rerun_start(
            run_dir,
            state,
            authorization=rerun_authorization,
        )
