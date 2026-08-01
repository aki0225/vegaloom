from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import LoopAutomationState
from .prompt_metrics import PromptMetrics
from .trace import TraceWriter
from .workspace_check import (
    run_workspace_check,
    snapshot_workspace,
)
from .workspace_inventory import (
    WorkspaceSnapshot,
    prepare_verification_temp_root,
    workspace_ignored_path_exclusions,
)

WORKSPACE_BASELINE_ARTIFACT = "workspace-baseline.json"
LEGACY_WORKSPACE_BASELINE_UNAVAILABLE = (
    "legacy_workspace_baseline_unavailable"
)
INITIALIZATION_TRACE_UNAVAILABLE = "initialization_trace_unavailable"
INITIALIZATION_EVIDENCE_UNAVAILABLE = "initialization_evidence_unavailable"
LOOP_PRE_WORKER_ARTIFACTS = (
    "agent-brief.md",
    "project-context.md",
    "project-policy-snapshot.json",
    "loop-plan.md",
)
WORKER_PROMPT_ARTIFACTS = (
    "worker-prompt.md",
    "worker-prompt-metrics.json",
    "worker-prompt-metrics.md",
)
LEGACY_ASSIST_INITIALIZATION_ARTIFACTS = (
    *LOOP_PRE_WORKER_ARTIFACTS,
    *WORKER_PROMPT_ARTIFACTS,
)
ASSIST_INITIALIZATION_ARTIFACTS = (
    *LOOP_PRE_WORKER_ARTIFACTS,
    WORKSPACE_BASELINE_ARTIFACT,
    *WORKER_PROMPT_ARTIFACTS,
)
ASSIST_BASELINE_BLOCKED_ARTIFACTS = (
    "state.json",
    "trace.jsonl",
    *LOOP_PRE_WORKER_ARTIFACTS,
    WORKSPACE_BASELINE_ARTIFACT,
    "workspace-check.json",
    "workspace-check.md",
    "final-report.md",
    "eval.md",
)
ASSIST_BASELINE_BLOCKED_STEPS = frozenset(
    {
        "workspace_baseline_dirty",
        "workspace_baseline_unavailable",
        "workspace_head_changed",
    }
)


@dataclass(frozen=True)
class WorkspaceBaselineBlock:
    current_step: str
    conclusion: str


class WorkspaceBaselineArtifact(BaseModel):
    """可跨进程恢复的 Worker 启动前工作区基线。"""

    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    head_sha: str
    tracked_files: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    ignored_path_exclusions: list[str] = Field(default_factory=list)
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    ignored_manifest_complete: bool
    ignored_content_complete: bool
    git_control_sha256: str
    git_control_complete: bool
    capture_complete: bool


def write_workspace_baseline(path: Path, snapshot: WorkspaceSnapshot) -> str:
    """封存 workspace baseline，并返回供 state 与 trace 绑定的内容哈希。"""

    artifact = WorkspaceBaselineArtifact(
        head_sha=snapshot.head_sha,
        tracked_files=sorted(snapshot.tracked_files),
        untracked_files=sorted(snapshot.untracked_files),
        ignored_path_exclusions=sorted(snapshot.ignored_path_exclusions),
        untracked_manifest_sha256=snapshot.untracked_manifest_sha256,
        ignored_manifest_sha256=snapshot.ignored_manifest_sha256,
        ignored_manifest_complete=snapshot.ignored_manifest_complete,
        ignored_content_complete=snapshot.ignored_content_complete,
        git_control_sha256=snapshot.git_control_sha256,
        git_control_complete=snapshot.git_control_complete,
        capture_complete=snapshot.capture_complete,
    )
    raw = (
        json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def read_workspace_baseline(
    path: Path,
    *,
    expected_sha256: str,
) -> WorkspaceSnapshot:
    """读取并验证基线；证据缺失、被改写或路径越界时 fail-closed。"""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("workspace baseline 缺失或不可读") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("workspace baseline 内容哈希与 loop state 不一致")
    try:
        artifact = WorkspaceBaselineArtifact.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError("workspace baseline 内容不合法") from exc
    _validate_baseline_paths(artifact.tracked_files, "tracked")
    _validate_baseline_paths(artifact.untracked_files, "untracked")
    _validate_baseline_paths(
        artifact.ignored_path_exclusions,
        "ignored exclusion",
    )
    return WorkspaceSnapshot(
        raw_status="",
        tracked_files=frozenset(artifact.tracked_files),
        untracked_files=frozenset(artifact.untracked_files),
        ignored_path_exclusions=frozenset(artifact.ignored_path_exclusions),
        head_sha=artifact.head_sha,
        untracked_manifest_sha256=artifact.untracked_manifest_sha256,
        ignored_manifest_sha256=artifact.ignored_manifest_sha256,
        ignored_manifest_complete=artifact.ignored_manifest_complete,
        ignored_content_complete=artifact.ignored_content_complete,
        git_control_sha256=artifact.git_control_sha256,
        git_control_complete=artifact.git_control_complete,
        capture_complete=artifact.capture_complete,
    )


def capture_assist_workspace_baseline(
    workspace: Path,
    repo_path: Path,
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
) -> WorkspaceBaselineBlock | None:
    """在外部 Worker 接手前封存可跨进程验证的 assist 基线。"""

    state.current_step = "workspace_baseline"
    state.save(run_dir / "state.json")
    prepare_verification_temp_root(repo_path)
    baseline = snapshot_workspace(
        repo_path,
        ignored_path_exclusions=workspace_ignored_path_exclusions(
            workspace,
            repo_path,
        ),
    )
    state.workspace_baseline_artifact_version = 1
    state.workspace_baseline_sha256 = write_workspace_baseline(
        run_dir / WORKSPACE_BASELINE_ARTIFACT,
        baseline,
    )
    state.artifacts = [WORKSPACE_BASELINE_ARTIFACT]
    state.save(run_dir / "state.json")
    trace.write(
        "workspace_baseline_captured",
        artifact=WORKSPACE_BASELINE_ARTIFACT,
        artifact_version=state.workspace_baseline_artifact_version,
        sha256=state.workspace_baseline_sha256,
        head_sha=baseline.head_sha,
        tracked_files=len(baseline.tracked_files),
        untracked_files=len(baseline.untracked_files),
        ignored_manifest_complete=baseline.ignored_manifest_complete,
        git_control_complete=baseline.git_control_complete,
        capture_complete=baseline.capture_complete,
    )
    head_changed = bool(
        state.initial_head_sha
        and baseline.head_sha
        and baseline.head_sha != state.initial_head_sha
    )
    if baseline.capture_complete and not baseline.has_tracked_changes and not head_changed:
        return None

    run_workspace_check(
        repo_path,
        run_dir,
        baseline=baseline,
        expected_head_sha=state.initial_head_sha,
    )
    block = _workspace_baseline_block(baseline, head_changed)
    state.current_step = block.current_step
    trace.write(
        "workspace_baseline_blocked",
        reason=block.current_step,
        capture_complete=baseline.capture_complete,
        tracked_files=len(baseline.tracked_files),
        baseline_head_changed=head_changed,
    )
    return block


def require_assist_workspace_baseline_continuable(
    state: LoopAutomationState,
) -> None:
    if state.current_step not in ASSIST_BASELINE_BLOCKED_STEPS:
        return
    raise ValueError(
        "loop 的启动基线不可用，不能继续归因当前 diff；"
        "请清理工作区后重新启动新的 loop。"
    )


def load_assist_workspace_baseline(
    run_dir: Path,
    state: LoopAutomationState,
) -> WorkspaceSnapshot | None:
    if state.automation_mode != "assist":
        return None
    if not state.workspace_baseline_sha256:
        raise ValueError("loop 缺少已绑定的 workspace baseline，已拒绝 continue。")
    return read_workspace_baseline(
        run_dir / WORKSPACE_BASELINE_ARTIFACT,
        expected_sha256=state.workspace_baseline_sha256,
    )


def _workspace_baseline_block(
    baseline: WorkspaceSnapshot,
    head_changed: bool,
) -> WorkspaceBaselineBlock:
    if not baseline.capture_complete:
        return WorkspaceBaselineBlock(
            current_step="workspace_baseline_unavailable",
            conclusion=(
                "无法完整封存 Worker 启动前 workspace baseline，"
                "未把任务交给外部 Worker。"
            ),
        )
    if head_changed:
        return WorkspaceBaselineBlock(
            current_step="workspace_head_changed",
            conclusion=(
                "生成计划后 Git HEAD 已发生变化，"
                "为避免在错误提交上执行，未把任务交给外部 Worker。"
            ),
        )
    return WorkspaceBaselineBlock(
        current_step="workspace_baseline_dirty",
        conclusion=(
            "封存基线时已存在 tracked diff，"
            "无法把后续修改安全归因于本轮外部 Worker。"
        ),
    )


def is_legacy_assist_initialization_unavailable(
    run_dir: Path,
    state: LoopAutomationState,
    trace_items: list[dict[str, Any]] | None,
) -> bool:
    """识别 baseline 协议引入前已完整初始化的 assist run。"""

    if trace_items is None or not _has_legacy_assist_protocol_markers(
        run_dir,
        state,
        trace_items,
    ):
        return False
    for name in LEGACY_ASSIST_INITIALIZATION_ARTIFACTS:
        path = run_dir / name
        try:
            if not path.is_file() or not path.read_bytes():
                return False
        except OSError:
            return False
    if not _legacy_worker_prompt_metrics_match(run_dir):
        return False
    return True


def append_workspace_baseline_trace_issues(
    trace_items: list[dict[str, Any]],
    state: LoopAutomationState,
    issues: list[str],
    *,
    legacy_assist_initialization: bool,
) -> None:
    if (
        state.automation_mode != "assist"
        or legacy_assist_initialization
    ):
        return
    baseline_indices = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "workspace_baseline_captured"
    ]
    if len(baseline_indices) != 1:
        issues.append("workspace_baseline_trace_event_count_invalid")
        return

    baseline_index = baseline_indices[0]
    baseline_event = trace_items[baseline_index]
    if baseline_event.get("artifact") != WORKSPACE_BASELINE_ARTIFACT:
        issues.append("workspace_baseline_trace_artifact_mismatch")
    if (
        baseline_event.get("artifact_version")
        != state.workspace_baseline_artifact_version
    ):
        issues.append("workspace_baseline_trace_version_mismatch")
    if baseline_event.get("sha256") != state.workspace_baseline_sha256:
        issues.append("workspace_baseline_trace_hash_mismatch")
    if baseline_event.get("head_sha") != state.initial_head_sha:
        issues.append("workspace_baseline_trace_head_mismatch")

    worker_prompt_indices = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "worker_prompt_measured"
        and "iteration" not in item
    ]
    initialized_indices = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "loop_initialized"
    ]
    if (
        len(worker_prompt_indices) != 1
        or len(initialized_indices) != 1
        or not (
            baseline_index
            < worker_prompt_indices[0]
            < initialized_indices[0]
        )
    ):
        issues.append("workspace_baseline_trace_order_invalid")


def recovered_initialization_step(initialization_issues: list[str]) -> str:
    if initialization_issues == [LEGACY_WORKSPACE_BASELINE_UNAVAILABLE]:
        return LEGACY_WORKSPACE_BASELINE_UNAVAILABLE
    if initialization_issues:
        return "recovered_initialization_incomplete"
    return "recovered"


def _has_legacy_assist_protocol_markers(
    run_dir: Path,
    state: LoopAutomationState,
    trace_items: list[dict[str, Any]],
) -> bool:
    if (
        state.automation_mode != "assist"
        or state.workspace_baseline_artifact_version is not None
        or state.workspace_baseline_sha256 is not None
        or run_dir.joinpath(WORKSPACE_BASELINE_ARTIFACT).exists()
        or WORKSPACE_BASELINE_ARTIFACT in state.artifacts
        or any(
            item.get("event") == "workspace_baseline_captured"
            for item in trace_items
        )
    ):
        return False
    worker_prompt_indices = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "worker_prompt_measured"
        and "iteration" not in item
    ]
    initialized_indices = [
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "loop_initialized"
    ]
    if len(worker_prompt_indices) != 1 or len(initialized_indices) != 1:
        return False
    initialized = trace_items[initialized_indices[0]]
    return (
        initialized.get("brief_run") == state.brief_run
        and initialized.get("artifacts")
        == list(LEGACY_ASSIST_INITIALIZATION_ARTIFACTS)
        and worker_prompt_indices[0] < initialized_indices[0]
    )


def _legacy_worker_prompt_metrics_match(run_dir: Path) -> bool:
    try:
        prompt = run_dir.joinpath("worker-prompt.md").read_text(encoding="utf-8")
        metrics = PromptMetrics.model_validate_json(
            run_dir.joinpath("worker-prompt-metrics.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return False
    return (
        metrics.role == "worker"
        and metrics.chars == len(prompt)
        and metrics.utf8_bytes == len(prompt.encode("utf-8"))
        and metrics.lines == len(prompt.splitlines())
    )


def _validate_baseline_paths(paths: list[str], label: str) -> None:
    if paths != sorted(set(paths)):
        raise ValueError(f"workspace baseline 的 {label} 路径未规范化")
    for value in paths:
        normalized = value.replace("\\", "/")
        posix_candidate = PurePosixPath(normalized)
        windows_candidate = PureWindowsPath(value)
        if (
            not value
            or "\x00" in value
            or normalized == "."
            or posix_candidate.is_absolute()
            or windows_candidate.drive
            or windows_candidate.root
            or windows_candidate.anchor
            or ".." in posix_candidate.parts
        ):
            raise ValueError("workspace baseline 包含越过仓库边界的路径")
