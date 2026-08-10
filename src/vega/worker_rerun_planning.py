from __future__ import annotations

from pathlib import Path

from .models import LoopAutomationState, WorkerRerunAuthorization
from .worker_baseline import worker_workspace_fingerprint
from .workspace_check import snapshot_worker_workspace
from .workspace_inventory import WorkspaceSnapshot, workspace_ignored_path_exclusions


def initialize_auto_worker_rerun(
    workspace: Path,
    repo_path: Path,
    state: LoopAutomationState,
    *,
    start_iteration: int,
    rerun_requested: bool,
    expected_workspace_snapshot: WorkspaceSnapshot | None,
    source_interrupted_iteration: int | None = None,
    source_worker_baseline_sha256: str | None = None,
) -> tuple[WorkspaceSnapshot | None, WorkerRerunAuthorization | None]:
    """在进入迭代前复核恢复快照，并构造与 recovery 绑定的重跑授权。"""

    recovered_snapshot: WorkspaceSnapshot | None = None
    authorization: WorkerRerunAuthorization | None = None
    if expected_workspace_snapshot is not None:
        recovered_snapshot = snapshot_worker_workspace(
            repo_path,
            ignored_path_exclusions=workspace_ignored_path_exclusions(
                workspace,
                repo_path,
            ),
        )
        if (
            worker_workspace_fingerprint(recovered_snapshot)
            != worker_workspace_fingerprint(expected_workspace_snapshot)
        ):
            raise ValueError(
                "工作区在确认重跑后、Worker 启动前发生变化；"
                "已拒绝 --rerun-worker，避免覆盖新的人工或外部修改。"
            )
    if rerun_requested:
        if (
            state.last_recovery_id is None
            or source_interrupted_iteration is None
            or source_worker_baseline_sha256 is None
        ):
            raise ValueError("Worker 重跑缺少可验证的恢复授权绑定。")
        authorization = WorkerRerunAuthorization(
            rerun_iteration=start_iteration,
            source_interrupted_iteration=source_interrupted_iteration,
            recovery_id=state.last_recovery_id,
            source_worker_baseline_artifact_version=2,
            source_worker_baseline_sha256=source_worker_baseline_sha256,
        )
    return recovered_snapshot, authorization


def select_worker_workspace_snapshot(
    workspace: Path,
    repo_path: Path,
    recovered_snapshot: WorkspaceSnapshot | None,
    *,
    iteration_number: int,
    start_iteration: int,
) -> tuple[WorkspaceSnapshot, WorkspaceSnapshot | None]:
    """在每轮 Worker 前捕获快照，并在显式重跑首轮复核授权边界。"""

    if recovered_snapshot is not None and iteration_number == start_iteration:
        current = snapshot_worker_workspace(
            repo_path,
            ignored_path_exclusions=workspace_ignored_path_exclusions(
                workspace,
                repo_path,
            ),
        )
        if (
            worker_workspace_fingerprint(current)
            != worker_workspace_fingerprint(recovered_snapshot)
        ):
            raise ValueError(
                "工作区在重跑授权后、Worker 启动前发生变化；"
                "已拒绝 --rerun-worker，避免覆盖新的人工或外部修改。"
            )
        return current, recovered_snapshot
    return (
        snapshot_worker_workspace(
            repo_path,
            ignored_path_exclusions=workspace_ignored_path_exclusions(
                workspace,
                repo_path,
            ),
        ),
        recovered_snapshot,
    )
