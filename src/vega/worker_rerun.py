from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import LoopAutomationState, WorkerRerunAuthorization
from .trace import TraceWriter, read_trace_items
from .workspace_baseline import read_workspace_baseline, write_workspace_baseline
from .workspace_inventory import WorkspaceSnapshot

WORKER_BASELINE_ARTIFACT = "worker-baseline.json"


def worker_baseline_relative_path(iteration: int) -> str:
    if iteration < 1:
        raise ValueError("worker baseline iteration 必须从 1 开始")
    return f"iterations/{iteration:02d}/{WORKER_BASELINE_ARTIFACT}"


def capture_auto_worker_workspace_baseline(
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
    *,
    iteration: int,
    snapshot: WorkspaceSnapshot,
) -> str:
    """在 auto Worker 启动前持久化当前轮次的完整工作区基线。"""

    if not snapshot.capture_complete or not snapshot.tracked_diff_complete:
        raise ValueError("auto Worker 启动前工作区基线不完整")
    relative_path = worker_baseline_relative_path(iteration)
    digest = write_workspace_baseline(run_dir / relative_path, snapshot)
    state.worker_baseline_artifact_version = 1
    state.worker_baseline_iteration = iteration
    state.worker_baseline_sha256 = digest
    state.artifacts = list(dict.fromkeys([*state.artifacts, relative_path]))
    state.save(run_dir / "state.json")
    trace.write(
        "worker_baseline_captured",
        iteration=iteration,
        artifact=relative_path,
        artifact_version=state.worker_baseline_artifact_version,
        sha256=digest,
        head_sha=snapshot.head_sha,
        tracked_files=len(snapshot.tracked_files),
        untracked_files=len(snapshot.untracked_files),
        tracked_diff_complete=snapshot.tracked_diff_complete,
        ignored_manifest_complete=snapshot.ignored_manifest_complete,
        git_control_complete=snapshot.git_control_complete,
        capture_complete=snapshot.capture_complete,
    )
    return digest


def load_bound_auto_worker_baseline(
    run_dir: Path,
    state: LoopAutomationState,
    *,
    iteration: int,
) -> WorkspaceSnapshot:
    """读取最新中断 Worker 启动前、已绑定到根状态的工作区基线。"""

    if (
        state.worker_baseline_artifact_version != 1
        or state.worker_baseline_iteration != iteration
        or not state.worker_baseline_sha256
    ):
        raise ValueError("loop 缺少当前中断 Worker 的已绑定 workspace baseline")
    return read_workspace_baseline(
        run_dir / worker_baseline_relative_path(iteration),
        expected_sha256=state.worker_baseline_sha256,
    )


def worker_rerun_binding_issues(
    run_dir: Path,
    state: LoopAutomationState,
    trace_items: list[dict[str, Any]] | None = None,
) -> list[str]:
    """校验显式 Worker 重跑的 state、baseline 与 trace 三方绑定。"""

    if state.automation_mode != "auto":
        return (
            ["worker_rerun_authorization_unexpected"]
            if state.worker_rerun_authorizations
            else []
        )
    try:
        items = (
            trace_items
            if trace_items is not None
            else read_trace_items(run_dir / "trace.jsonl")
        )
    except (OSError, ValueError):
        return ["worker_rerun_trace_unreadable"]
    requested = [
        item for item in items if item.get("event") == "auto_worker_rerun_requested"
    ]
    authorizations = state.worker_rerun_authorizations
    if len(requested) != len(authorizations):
        return ["worker_rerun_authorization_trace_count_mismatch"]
    if not requested:
        return []

    recovery_ids = {
        str(item.get("recovery_id"))
        for item in items
        if item.get("event") == "loop_recovered"
        and isinstance(item.get("recovery_id"), str)
    }
    issues: list[str] = []
    seen_recovery_ids: set[str] = set()
    for authorization in authorizations:
        issues.extend(
            _authorization_issues(
                run_dir,
                state,
                items,
                requested,
                authorization,
                recovery_ids,
                seen_recovery_ids,
            )
        )
        seen_recovery_ids.add(authorization.recovery_id)
    return issues


def worker_rerun_eval_results(
    run_dir: Path,
    state: LoopAutomationState,
) -> list[str]:
    """把显式 Worker 重跑授权缺失转成可见的 eval FAIL。"""

    if state.automation_mode != "auto" and not state.worker_rerun_authorizations:
        return []
    issues = worker_rerun_binding_issues(run_dir, state)
    if issues:
        return ["FAIL: Worker 重跑授权证据无效：" + ", ".join(issues)]
    if state.worker_rerun_authorizations:
        return ["PASS: Worker 重跑授权与 baseline、trace 已绑定"]
    return []


def _authorization_issues(
    run_dir: Path,
    state: LoopAutomationState,
    trace_items: list[dict[str, Any]],
    requested: list[dict[str, Any]],
    authorization: WorkerRerunAuthorization,
    recovery_ids: set[str],
    seen_recovery_ids: set[str],
) -> list[str]:
    prefix = f"worker_rerun_{authorization.rerun_iteration:02d}"
    issues: list[str] = []
    if authorization.recovery_id in seen_recovery_ids:
        issues.append(f"{prefix}_duplicate_recovery_id")
    if authorization.recovery_id not in recovery_ids:
        issues.append(f"{prefix}_recovery_event_missing")
    if authorization.source_interrupted_iteration >= authorization.rerun_iteration:
        issues.append(f"{prefix}_iteration_order_invalid")
    issues.extend(_iteration_issues(state, authorization, prefix))
    issues.extend(_source_baseline_issues(run_dir, authorization, prefix))
    if not _has_source_baseline_event(trace_items, authorization):
        issues.append(f"{prefix}_source_baseline_trace_invalid")
    if not _has_rerun_request(requested, authorization):
        issues.append(f"{prefix}_trace_binding_invalid")
    return issues


def _iteration_issues(
    state: LoopAutomationState,
    authorization: WorkerRerunAuthorization,
    prefix: str,
) -> list[str]:
    source = [
        item
        for item in state.iterations
        if item.iteration == authorization.source_interrupted_iteration
    ]
    issues: list[str] = []
    if (
        len(source) != 1
        or source[0].lifecycle != "interrupted"
        or source[0].interrupted_step != "worker"
    ):
        issues.append(f"{prefix}_source_iteration_invalid")
    if not any(
        item.iteration == authorization.rerun_iteration for item in state.iterations
    ):
        issues.append(f"{prefix}_rerun_iteration_missing")
    return issues


def _source_baseline_issues(
    run_dir: Path,
    authorization: WorkerRerunAuthorization,
    prefix: str,
) -> list[str]:
    try:
        baseline = read_workspace_baseline(
            run_dir
            / worker_baseline_relative_path(
                authorization.source_interrupted_iteration
            ),
            expected_sha256=authorization.source_worker_baseline_sha256,
        )
    except ValueError:
        return [f"{prefix}_source_baseline_invalid"]
    issues: list[str] = []
    if not baseline.capture_complete:
        issues.append(f"{prefix}_source_baseline_incomplete")
    if not baseline.tracked_diff_complete:
        issues.append(f"{prefix}_source_tracked_diff_incomplete")
    return issues


def _has_source_baseline_event(
    trace_items: list[dict[str, Any]],
    authorization: WorkerRerunAuthorization,
) -> bool:
    expected_path = worker_baseline_relative_path(
        authorization.source_interrupted_iteration
    )
    matches = [
        item
        for item in trace_items
        if item.get("event") == "worker_baseline_captured"
        and item.get("iteration") == authorization.source_interrupted_iteration
        and item.get("artifact") == expected_path
        and item.get("artifact_version") == 1
        and item.get("sha256") == authorization.source_worker_baseline_sha256
    ]
    return len(matches) == 1


def _has_rerun_request(
    requested: list[dict[str, Any]],
    authorization: WorkerRerunAuthorization,
) -> bool:
    matches = [
        item
        for item in requested
        if item.get("rerun_iteration") == authorization.rerun_iteration
        and item.get("source_interrupted_iteration")
        == authorization.source_interrupted_iteration
        and item.get("recovery_id") == authorization.recovery_id
        and item.get("source_worker_baseline_sha256")
        == authorization.source_worker_baseline_sha256
    ]
    return len(matches) == 1
