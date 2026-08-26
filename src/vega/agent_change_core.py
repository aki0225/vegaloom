from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .loop_project_policy import apply_runner_defaults, load_stable_start_policy
from .models import BriefInput
from .run_lock import RunMutationLock
from .run_utils import create_run_dir, resolve_run_dir
from .tracked_workspace import normalize_comparison_paths, validate_comparison_base

if TYPE_CHECKING:
    from .loop_runtime import LoopAutomationRuntime


def reserve_change_core_child(runtime: LoopAutomationRuntime) -> Path:
    """为先执行 Worker、后冻结 Candidate 的 ChangeRun 预留 child 身份。"""

    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-bug-loop"
    _, run_dir = create_run_dir(runtime.workspace, run_id)
    return run_dir


def initialize_change_core_child(
    runtime: LoopAutomationRuntime,
    run: str,
    brief_input: BriefInput,
    *,
    comparison_base_sha: str,
    comparison_paths: tuple[str, ...],
) -> Path:
    """在 Candidate Commit 已冻结后初始化预留的 assist Core child。"""

    run_dir = resolve_run_dir(runtime.workspace, run)
    unexpected = {
        path.name
        for path in run_dir.iterdir()
        if path.name != "executions"
    }
    if unexpected:
        raise ValueError(
            f"预留 child 已包含非 execution Artifact：{sorted(unexpected)}"
        )
    repo_path = Path(brief_input.repo_path).resolve()
    config, policy_snapshot, initial_head_sha = load_stable_start_policy(repo_path)
    resolved_base = validate_comparison_base(
        repo_path,
        comparison_base_sha,
        head_sha=initial_head_sha,
    )
    normalized_paths = normalize_comparison_paths(comparison_paths)
    worker_name, reviewer_name = apply_runner_defaults(
        config,
        "codex-exec",
        "codex-exec",
    )
    with RunMutationLock.acquire(run_dir, "loop.start"):
        return runtime._start_locked(
            brief_input,
            "assist",
            worker_name,
            reviewer_name,
            2,
            True,
            run_dir.name,
            run_dir,
            config,
            policy_snapshot,
            initial_head_sha,
            resolved_base,
            normalized_paths,
        )
