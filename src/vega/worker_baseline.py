from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import LoopAutomationState
from .trace import TraceWriter, read_trace_items
from .workspace_inventory import WorkspaceSnapshot

WORKER_BASELINE_ARTIFACT = "worker-baseline.json"
WORKER_BASELINE_ARTIFACT_VERSION = 2


class WorkerWorkspaceBaselineArtifact(BaseModel):
    """只保存机器比较所需摘要，避免把目标仓库路径名写入 run artifact。"""

    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[2] = WORKER_BASELINE_ARTIFACT_VERSION
    head_sha: str
    tracked_paths_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tracked_paths_count: int = Field(ge=0)
    untracked_paths_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    untracked_paths_count: int = Field(ge=0)
    ignored_exclusions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ignored_exclusions_count: int = Field(ge=0)
    tracked_diff_sha256: str
    tracked_diff_complete: bool
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    ignored_manifest_complete: bool
    ignored_content_complete: bool
    ignored_descendants_manifest_sha256: str
    ignored_descendants_complete: bool
    git_control_sha256: str
    git_control_complete: bool
    index_flags_sha256: str
    unsafe_index_paths_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unsafe_index_paths_count: int = Field(ge=0)
    capture_complete: bool


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
    """在 auto Worker 启动前持久化当前轮次的机读工作区基线。"""

    if not snapshot.capture_complete or not snapshot.tracked_diff_complete:
        raise ValueError("auto Worker 启动前工作区基线不完整")
    relative_path = worker_baseline_relative_path(iteration)
    digest = write_worker_workspace_baseline(run_dir / relative_path, snapshot)
    bind_auto_worker_workspace_baseline(
        state,
        iteration=iteration,
        digest=digest,
    )
    state.save(run_dir / "state.json")
    trace.write(
        "worker_baseline_captured",
        **_worker_baseline_trace_payload(
            iteration=iteration,
            snapshot=snapshot,
            digest=digest,
        ),
    )
    return digest


def write_worker_workspace_baseline(
    path: Path,
    snapshot: WorkspaceSnapshot,
) -> str:
    raw = _worker_workspace_baseline_bytes(snapshot)
    _write_bytes_atomically(path, raw, prefix=".b.")
    return hashlib.sha256(raw).hexdigest()


def prepare_auto_worker_workspace_baseline(
    run_dir: Path,
    trace: TraceWriter,
    *,
    iteration: int,
    snapshot: WorkspaceSnapshot,
) -> str:
    """在 claim 前幂等准备 baseline，不提前修改根状态。"""

    if not snapshot.capture_complete or not snapshot.tracked_diff_complete:
        raise ValueError("auto Worker 启动前工作区基线不完整")
    relative_path = worker_baseline_relative_path(iteration)
    path = run_dir / relative_path
    raw = _worker_workspace_baseline_bytes(snapshot)
    digest = hashlib.sha256(raw).hexdigest()
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise ValueError("已准备的 Worker baseline 无法读取") from exc
        if existing != raw:
            raise ValueError("已准备的 Worker baseline 与当前工作区不一致")
    else:
        _write_bytes_atomically(path, raw, prefix=".b.")
    _ensure_worker_baseline_trace(
        run_dir,
        trace,
        iteration=iteration,
        snapshot=snapshot,
        digest=digest,
    )
    return digest


def bind_auto_worker_workspace_baseline(
    state: LoopAutomationState,
    *,
    iteration: int,
    digest: str,
) -> None:
    """把已准备的 baseline 与 iteration claim 放进同一次 state 保存。"""

    relative_path = worker_baseline_relative_path(iteration)
    state.worker_baseline_artifact_version = WORKER_BASELINE_ARTIFACT_VERSION
    state.worker_baseline_iteration = iteration
    state.worker_baseline_sha256 = digest
    state.artifacts = list(dict.fromkeys([*state.artifacts, relative_path]))


def read_worker_workspace_baseline(
    path: Path,
    *,
    expected_sha256: str,
) -> WorkerWorkspaceBaselineArtifact:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("worker workspace baseline 缺失或不可读") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("worker workspace baseline 内容哈希与 loop state 不一致")
    try:
        return WorkerWorkspaceBaselineArtifact.model_validate_json(raw)
    except (ValueError, ValidationError) as exc:
        raise ValueError("worker workspace baseline 内容不合法") from exc


def worker_workspace_baseline_artifact(
    snapshot: WorkspaceSnapshot,
) -> WorkerWorkspaceBaselineArtifact:
    return WorkerWorkspaceBaselineArtifact(
        head_sha=snapshot.head_sha,
        tracked_paths_sha256=_stable_path_set_sha256(snapshot.tracked_files),
        tracked_paths_count=len(snapshot.tracked_files),
        untracked_paths_sha256=_stable_path_set_sha256(snapshot.untracked_files),
        untracked_paths_count=len(snapshot.untracked_files),
        ignored_exclusions_sha256=_stable_path_set_sha256(
            snapshot.ignored_path_exclusions
        ),
        ignored_exclusions_count=len(snapshot.ignored_path_exclusions),
        tracked_diff_sha256=snapshot.tracked_diff_sha256,
        tracked_diff_complete=snapshot.tracked_diff_complete,
        untracked_manifest_sha256=snapshot.untracked_manifest_sha256,
        ignored_manifest_sha256=snapshot.ignored_manifest_sha256,
        ignored_manifest_complete=snapshot.ignored_manifest_complete,
        ignored_content_complete=snapshot.ignored_content_complete,
        ignored_descendants_manifest_sha256=(
            snapshot.ignored_descendants_manifest_sha256
        ),
        ignored_descendants_complete=snapshot.ignored_descendants_complete,
        git_control_sha256=snapshot.git_control_sha256,
        git_control_complete=snapshot.git_control_complete,
        index_flags_sha256=snapshot.index_flags_sha256,
        unsafe_index_paths_sha256=_stable_path_set_sha256(
            snapshot.unsafe_index_paths
        ),
        unsafe_index_paths_count=len(snapshot.unsafe_index_paths),
        capture_complete=snapshot.capture_complete,
    )


def worker_workspace_fingerprint(snapshot: WorkspaceSnapshot) -> str:
    return worker_workspace_baseline_fingerprint(
        worker_workspace_baseline_artifact(snapshot)
    )


def worker_workspace_baseline_fingerprint(
    artifact: WorkerWorkspaceBaselineArtifact,
) -> str:
    payload = json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def worker_workspace_matches_baseline(
    snapshot: WorkspaceSnapshot,
    baseline: WorkerWorkspaceBaselineArtifact,
) -> bool:
    return worker_workspace_baseline_artifact(snapshot) == baseline


def worker_workspace_rerun_ready(
    baseline: WorkerWorkspaceBaselineArtifact,
) -> bool:
    return (
        baseline.capture_complete
        and baseline.tracked_diff_complete
        and baseline.ignored_descendants_complete
        and baseline.unsafe_index_paths_count == 0
    )


def worker_workspace_snapshot_rerun_ready(
    snapshot: WorkspaceSnapshot,
) -> bool:
    return (
        snapshot.capture_complete
        and snapshot.tracked_diff_complete
        and snapshot.ignored_descendants_complete
        and not snapshot.unsafe_index_paths
    )


def load_bound_auto_worker_baseline(
    run_dir: Path,
    state: LoopAutomationState,
    *,
    iteration: int,
) -> WorkerWorkspaceBaselineArtifact:
    """读取并验证最新中断 Worker 的基线 artifact 与来源 trace。"""

    if (
        state.worker_baseline_artifact_version
        != WORKER_BASELINE_ARTIFACT_VERSION
        or state.worker_baseline_iteration != iteration
        or not state.worker_baseline_sha256
    ):
        raise ValueError("loop 缺少当前中断 Worker 的 v2 workspace baseline")
    _require_source_baseline_trace(
        run_dir,
        iteration=iteration,
        artifact_version=WORKER_BASELINE_ARTIFACT_VERSION,
        sha256=state.worker_baseline_sha256,
    )
    return read_worker_workspace_baseline(
        run_dir / worker_baseline_relative_path(iteration),
        expected_sha256=state.worker_baseline_sha256,
    )


def _require_source_baseline_trace(
    run_dir: Path,
    *,
    iteration: int,
    artifact_version: int,
    sha256: str,
) -> None:
    try:
        items = read_trace_items(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise ValueError("来源 Worker baseline trace 无法验证") from exc
    baseline_events = [
        item
        for item in items
        if item.get("event") == "worker_baseline_captured"
        and item.get("iteration") == iteration
    ]
    expected_path = worker_baseline_relative_path(iteration)
    matches = [
        item
        for item in baseline_events
        if item.get("artifact") == expected_path
        and item.get("artifact_version") == artifact_version
        and item.get("sha256") == sha256
    ]
    if len(baseline_events) != 1 or len(matches) != 1:
        raise ValueError("来源 Worker baseline trace 缺失、重复或绑定不一致")


def _worker_workspace_baseline_bytes(snapshot: WorkspaceSnapshot) -> bytes:
    artifact = worker_workspace_baseline_artifact(snapshot)
    return (
        json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_bytes_atomically(path: Path, raw: bytes, *, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{prefix}{uuid4().hex[:16]}")
    try:
        temp_path.write_bytes(raw)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _worker_baseline_trace_payload(
    *,
    iteration: int,
    snapshot: WorkspaceSnapshot,
    digest: str,
) -> dict[str, object]:
    return {
        "iteration": iteration,
        "artifact": worker_baseline_relative_path(iteration),
        "artifact_version": WORKER_BASELINE_ARTIFACT_VERSION,
        "sha256": digest,
        "head_sha": snapshot.head_sha,
        "tracked_files": len(snapshot.tracked_files),
        "untracked_files": len(snapshot.untracked_files),
        "tracked_diff_complete": snapshot.tracked_diff_complete,
        "ignored_manifest_complete": snapshot.ignored_manifest_complete,
        "ignored_descendants_complete": snapshot.ignored_descendants_complete,
        "git_control_complete": snapshot.git_control_complete,
        "unsafe_index_paths": len(snapshot.unsafe_index_paths),
        "capture_complete": snapshot.capture_complete,
    }


def _ensure_worker_baseline_trace(
    run_dir: Path,
    trace: TraceWriter,
    *,
    iteration: int,
    snapshot: WorkspaceSnapshot,
    digest: str,
) -> None:
    try:
        items = read_trace_items(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise ValueError("Worker baseline trace 无法验证") from exc
    matches = [
        item
        for item in items
        if item.get("event") == "worker_baseline_captured"
        and item.get("iteration") == iteration
    ]
    expected = _worker_baseline_trace_payload(
        iteration=iteration,
        snapshot=snapshot,
        digest=digest,
    )
    if matches:
        if len(matches) != 1 or any(
            matches[0].get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Worker baseline trace 已存在冲突记录")
        return
    trace.write("worker_baseline_captured", **expected)


def _stable_path_set_sha256(values) -> str:
    payload = json.dumps(
        sorted(dict.fromkeys(values)),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
