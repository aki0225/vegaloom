from __future__ import annotations

from pathlib import Path

from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace
from .workspace_inventory import workspace_ignored_path_exclusions


def capture_runtime_workspace(
    workspace: Path,
    repo_path: Path,
    *,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> ReviewWorkspaceSnapshot:
    """捕获运行阶段快照，并排除目标仓库内由 Vega 自己维护的 runs。"""

    return capture_review_workspace(
        repo_path,
        ignored_path_exclusions=workspace_ignored_path_exclusions(
            workspace,
            repo_path,
        ),
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )
