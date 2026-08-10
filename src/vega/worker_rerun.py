from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import LoopAutomationState, WorkerRerunAuthorization
from .trace import read_trace_items
from .worker_baseline import (
    WORKER_BASELINE_ARTIFACT_VERSION,
    read_worker_workspace_baseline,
    worker_baseline_relative_path,
)
from .worker_rerun_transaction import worker_rerun_transaction_pending


def worker_rerun_binding_issues(
    run_dir: Path,
    state: LoopAutomationState,
    trace_items: list[dict[str, Any]] | None = None,
) -> list[str]:
    """校验显式 Worker 重跑的 state、baseline、trace 与 iteration 因果链。"""

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
    issues: list[str] = []
    if worker_rerun_transaction_pending(run_dir):
        issues.append("worker_rerun_transaction_pending")
    if len(requested) != len(authorizations):
        issues.append("worker_rerun_authorization_trace_count_mismatch")

    required_pairs = _required_rerun_pairs(state, items)
    authorization_pairs = [
        (item.source_interrupted_iteration, item.rerun_iteration)
        for item in authorizations
    ]
    request_pairs = [
        (
            item.get("source_interrupted_iteration"),
            item.get("rerun_iteration"),
        )
        for item in requested
    ]
    for source_iteration, rerun_iteration in required_pairs:
        if authorization_pairs.count((source_iteration, rerun_iteration)) != 1:
            issues.append(
                f"worker_rerun_{rerun_iteration:02d}_authorization_missing"
            )
        if request_pairs.count((source_iteration, rerun_iteration)) != 1:
            issues.append(
                f"worker_rerun_{rerun_iteration:02d}_request_trace_missing"
            )

    seen_recovery_ids: set[str] = set()
    for authorization in authorizations:
        issues.extend(
            _authorization_issues(
                run_dir,
                state,
                items,
                requested,
                authorization,
                seen_recovery_ids,
            )
        )
        seen_recovery_ids.add(authorization.recovery_id)
    return list(dict.fromkeys(issues))


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
    seen_recovery_ids: set[str],
) -> list[str]:
    prefix = f"worker_rerun_{authorization.rerun_iteration:02d}"
    issues: list[str] = []
    if authorization.recovery_id in seen_recovery_ids:
        issues.append(f"{prefix}_duplicate_recovery_id")
    recovery_matches = [
        item
        for item in trace_items
        if item.get("event") == "loop_recovered"
        and item.get("recovery_id") == authorization.recovery_id
    ]
    if len(recovery_matches) != 1:
        issues.append(f"{prefix}_recovery_event_invalid")
    if authorization.source_interrupted_iteration >= authorization.rerun_iteration:
        issues.append(f"{prefix}_iteration_order_invalid")
    issues.extend(_iteration_issues(state, authorization, prefix))
    issues.extend(_source_baseline_issues(run_dir, authorization, prefix))
    if not _has_source_baseline_event(trace_items, authorization):
        issues.append(f"{prefix}_source_baseline_trace_invalid")
    if not _has_rerun_request(requested, authorization):
        issues.append(f"{prefix}_trace_binding_invalid")
    issues.extend(_causal_order_issues(trace_items, authorization, prefix))
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
    if (
        authorization.source_worker_baseline_artifact_version
        != WORKER_BASELINE_ARTIFACT_VERSION
    ):
        return [f"{prefix}_source_baseline_version_unsupported"]
    try:
        baseline = read_worker_workspace_baseline(
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
    if not baseline.ignored_descendants_complete:
        issues.append(f"{prefix}_source_ignored_descendants_incomplete")
    if baseline.unsafe_index_paths_count:
        issues.append(f"{prefix}_source_unsafe_index_paths")
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
        and item.get("artifact_version")
        == authorization.source_worker_baseline_artifact_version
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
        and item.get("source_worker_baseline_artifact_version")
        == authorization.source_worker_baseline_artifact_version
        and item.get("source_worker_baseline_sha256")
        == authorization.source_worker_baseline_sha256
    ]
    return len(matches) == 1


def _causal_order_issues(
    trace_items: list[dict[str, Any]],
    authorization: WorkerRerunAuthorization,
    prefix: str,
) -> list[str]:
    event_groups = [
        [
            index
            for index, item in enumerate(trace_items)
            if item.get("event") == "worker_baseline_captured"
            and item.get("iteration") == authorization.source_interrupted_iteration
            and item.get("sha256") == authorization.source_worker_baseline_sha256
        ],
        [
            index
            for index, item in enumerate(trace_items)
            if item.get("event") == "loop_iteration_interrupted"
            and item.get("iteration") == authorization.source_interrupted_iteration
            and item.get("recovery_id") == authorization.recovery_id
        ],
        [
            index
            for index, item in enumerate(trace_items)
            if item.get("event") == "loop_recovered"
            and item.get("recovery_id") == authorization.recovery_id
        ],
        [
            index
            for index, item in enumerate(trace_items)
            if item.get("event") == "auto_worker_rerun_requested"
            and item.get("rerun_iteration") == authorization.rerun_iteration
            and item.get("recovery_id") == authorization.recovery_id
        ],
    ]
    cancelled = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "auto_worker_rerun_cancelled"
        and item.get("rerun_iteration") == authorization.rerun_iteration
        and item.get("recovery_id") == authorization.recovery_id
    ]
    rerun_baseline = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "worker_baseline_captured"
        and item.get("iteration") == authorization.rerun_iteration
    ]
    worker_started = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "worker_started"
        and item.get("iteration") == authorization.rerun_iteration
    ]
    if cancelled:
        if len(cancelled) != 1 or rerun_baseline or worker_started:
            return [f"{prefix}_causal_event_count_invalid"]
        event_groups.append(cancelled)
    else:
        event_groups.extend([rerun_baseline, worker_started])
    if any(len(group) != 1 for group in event_groups):
        return [f"{prefix}_causal_event_count_invalid"]
    indices = [group[0] for group in event_groups]
    if indices != sorted(indices) or len(set(indices)) != len(indices):
        return [f"{prefix}_causal_order_invalid"]
    return []


def _required_rerun_pairs(
    state: LoopAutomationState,
    trace_items: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    worker_started_iterations = {
        item.get("iteration")
        for item in trace_items
        if item.get("event") == "worker_started"
        and type(item.get("iteration")) is int
    }
    required: list[tuple[int, int]] = []
    for source, rerun in zip(state.iterations, state.iterations[1:]):
        if (
            source.lifecycle == "interrupted"
            and source.interrupted_step == "worker"
            and (
                rerun.worker_status != "skipped"
                or rerun.iteration in worker_started_iterations
            )
        ):
            required.append((source.iteration, rerun.iteration))
    return required
