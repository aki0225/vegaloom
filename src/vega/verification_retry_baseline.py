from __future__ import annotations

from pathlib import Path

from .models import LoopAutomationState
from .workspace_baseline import load_assist_workspace_baseline
from .workspace_check import snapshot_workspace
from .workspace_inventory import WorkspaceSnapshot


def validate_verification_retry_request(
    baseline: WorkspaceSnapshot | None,
    *,
    rerun_worker: bool,
    test_log: Path | None,
    note: str | None,
    verify: bool,
    verification_commands: list[str] | None,
) -> None:
    if baseline is None:
        return
    if (
        rerun_worker
        or test_log is not None
        or note is not None
        or not verify
        or verification_commands is None
    ):
        raise ValueError("验证恢复基线只允许用于不重跑 Worker 的显式验证命令。")


def select_assist_workspace_baseline(
    run_dir: Path,
    state: LoopAutomationState,
    repo: Path,
    retry_baseline: WorkspaceSnapshot | None,
) -> WorkspaceSnapshot | None:
    if retry_baseline is None:
        return load_assist_workspace_baseline(run_dir, state)
    if state.automation_mode != "assist":
        raise ValueError("验证恢复基线只适用于 assist loop。")
    _require_current_retry_baseline(
        retry_baseline,
        repo=repo,
        expected_head_sha=state.initial_head_sha,
    )
    return retry_baseline


def _require_current_retry_baseline(
    baseline: WorkspaceSnapshot,
    *,
    repo: Path,
    expected_head_sha: str,
) -> None:
    """验证专用恢复只能采用 Supervisor 已对账的当前现场。"""

    current = snapshot_workspace(
        repo,
        ignored_path_exclusions=baseline.ignored_path_exclusions,
    )
    if (
        not baseline.capture_complete
        or not baseline.tracked_diff_complete
        or baseline.head_sha != expected_head_sha
        or baseline.untracked_files
        or baseline.unsafe_index_paths
        or not current.capture_complete
        or not current.tracked_diff_complete
        or current.head_sha != baseline.head_sha
        or current.tracked_files != baseline.tracked_files
        or current.tracked_diff_sha256 != baseline.tracked_diff_sha256
        or current.untracked_files != baseline.untracked_files
        or current.untracked_manifest_sha256 != baseline.untracked_manifest_sha256
        or current.ignored_manifest_sha256 != baseline.ignored_manifest_sha256
        or current.ignored_manifest_complete != baseline.ignored_manifest_complete
        or current.ignored_content_complete != baseline.ignored_content_complete
        or current.git_control_sha256 != baseline.git_control_sha256
        or current.git_control_complete != baseline.git_control_complete
        or current.index_flags_sha256 != baseline.index_flags_sha256
        or current.unsafe_index_paths != baseline.unsafe_index_paths
    ):
        raise ValueError("验证恢复基线不完整或不再对应原 Git 现场。")
