from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .codex_workspace import filter_codex_runtime_ignored_paths
from .git_inventory import (
    build_git_control_manifest,
    read_core_ignorecase as _read_core_ignorecase,
    read_head_sha as _read_head_sha,
    read_ignored_paths,
)
from .git_read import run_git_bytes as _run_git_bytes
from .project_config import load_project_config
from .redaction import redact_text, redact_value
from .workspace_inventory import (
    ContentManifestBudget,
    CurrentWorkspaceInventory,
    WorkspaceSnapshot,
    build_content_manifest,
    safe_git_status as _safe_git_status,
    safe_path_for_report as _safe_path_for_report,
    untracked_paths as _untracked_paths,
)
from .workspace_status import (
    parse_porcelain_v1_paths as _parse_porcelain_v1_paths,
    parse_porcelain_v2_status as _parse_porcelain_v2_status,
)


MAX_IGNORED_METADATA_FILES = 4096
MAX_IGNORED_CONTENT_FILES = 256
MAX_IGNORED_FILE_BYTES = 1024 * 1024
MAX_IGNORED_CONTENT_BYTES = 8 * 1024 * 1024
MAX_UNTRACKED_CONTENT_FILES = MAX_IGNORED_CONTENT_FILES
MAX_UNTRACKED_FILE_BYTES = MAX_IGNORED_FILE_BYTES
MAX_UNTRACKED_CONTENT_BYTES = MAX_IGNORED_CONTENT_BYTES
MAX_GIT_CONTROL_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ReviewWorkspaceSnapshot:
    fingerprint: str
    head_sha: str
    status_sha256: str
    staged_diff_sha256: str
    unstaged_diff_sha256: str
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    index_flags_sha256: str
    full_diff: str
    staged_diff: str
    unstaged_diff: str
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    unsafe_index_paths: tuple[str, ...] = ()
    untracked_content_complete: bool = False
    ignored_manifest_complete: bool = False
    ignored_content_complete: bool = False
    git_control_sha256: str = ""
    git_control_complete: bool = False

    @property
    def ignored_coverage_level(self) -> str:
        return ignored_coverage_level(
            self.ignored_manifest_complete,
            self.ignored_content_complete,
        )


@dataclass(frozen=True)
class TrackedScopeSnapshot:
    """scope gate 读取到的一致 HEAD、tracked status 与双 diff 路径快照。"""

    head_sha: str
    status_sha256: str
    index_flags_sha256: str
    staged_files: tuple[str, ...]
    unstaged_files: tuple[str, ...]
    untracked_files: tuple[str, ...] = ()
    unsafe_index_paths: tuple[str, ...] = ()

    @property
    def changed_paths_sha256(self) -> str:
        payload = json.dumps(
            {
                "staged": self.staged_files,
                "unstaged": self.unstaged_files,
                "untracked": self.untracked_files,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _sha256(payload)


class WorkspaceCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed", "skipped"]
    repo_path: str
    max_new_files: int | None = None
    new_untracked_count: int = 0
    new_untracked_files: list[str] = Field(default_factory=list)
    baseline_tracked_changes_present: bool = False
    baseline_tracked_files: list[str] = Field(default_factory=list)
    baseline_untracked_changed: bool = False
    baseline_ignored_changed: bool = False
    baseline_ignored_manifest_complete: bool = False
    current_ignored_manifest_complete: bool = False
    baseline_ignored_content_complete: bool = False
    current_ignored_content_complete: bool = False
    git_control_changed: bool = False
    git_control_complete: bool = False
    baseline_head_sha: str | None = None
    current_head_sha: str | None = None
    baseline_head_changed: bool = False
    reasons: list[str] = Field(default_factory=list)
    raw_status: str = ""

    @property
    def has_failures(self) -> bool:
        return self.status == "failed"


@dataclass
class _WorkspaceAssessment:
    status: Literal["passed", "failed", "skipped"]
    reasons: list[str]
    baseline_untracked_changed: bool = False
    baseline_ignored_changed: bool = False
    git_control_changed: bool = False


def snapshot_workspace(
    repo_path: Path,
    *,
    ignored_path_exclusions: frozenset[str] = frozenset(),
) -> WorkspaceSnapshot:
    repo = repo_path.resolve()
    try:
        tracked_snapshot = capture_tracked_scope_snapshot(
            repo,
            include_untracked=True,
        )
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return WorkspaceSnapshot(
            raw_status=f"<git status failed before worker: {exc}>",
            tracked_files=frozenset(),
            untracked_files=frozenset(),
            ignored_path_exclusions=ignored_path_exclusions,
            capture_complete=False,
        )
    tracked_files = [
        *tracked_snapshot.staged_files,
        *tracked_snapshot.unstaged_files,
    ]
    untracked_files = list(tracked_snapshot.untracked_files)
    try:
        ignored_files, ignored_capture_complete = _ignored_paths(
            repo,
            ignored_path_exclusions,
        )
        (
            ignored_manifest_sha256,
            ignored_metadata_complete,
            ignored_content_complete,
        ) = _ignored_manifest(
            repo,
            ignored_files,
        )
        ignored_manifest_complete = ignored_capture_complete and ignored_metadata_complete
        git_control_sha256, git_control_complete = _git_control_manifest(repo)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return WorkspaceSnapshot(
            raw_status=f"<workspace control snapshot failed before worker: {exc}>",
            tracked_files=frozenset(dict.fromkeys(tracked_files)),
            untracked_files=frozenset(untracked_files),
            ignored_path_exclusions=ignored_path_exclusions,
            head_sha=tracked_snapshot.head_sha,
            untracked_manifest_sha256=_untracked_manifest_hash(repo, untracked_files),
            capture_complete=False,
        )
    return WorkspaceSnapshot(
        raw_status="",
        tracked_files=frozenset(dict.fromkeys(tracked_files)),
        untracked_files=frozenset(untracked_files),
        ignored_path_exclusions=ignored_path_exclusions,
        head_sha=tracked_snapshot.head_sha,
        untracked_manifest_sha256=_untracked_manifest_hash(repo, untracked_files),
        ignored_manifest_sha256=ignored_manifest_sha256,
        ignored_manifest_complete=ignored_manifest_complete,
        ignored_content_complete=ignored_content_complete,
        git_control_sha256=git_control_sha256,
        git_control_complete=git_control_complete,
        capture_complete=(
            ignored_manifest_complete and git_control_complete
        ),
    )


def capture_review_workspace(
    repo_path: Path, *, ignored_path_exclusions: frozenset[str] = frozenset()
) -> ReviewWorkspaceSnapshot:
    """捕获 reviewer 使用的确定性工作区快照，不修改 Git index。"""
    repo = repo_path.resolve()
    head_sha = _run_git_bytes(repo, ["git", "rev-parse", "HEAD"]).decode(
        "utf-8",
        errors="replace",
    ).strip()
    status = _run_git_bytes(
        repo,
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    staged_diff, unstaged_diff = collect_tracked_diff_parts(
        repo,
        ["--binary", "--full-index"],
    )
    tracked_files, untracked_files = _parse_porcelain_v1_paths(status)
    ignored_files, ignored_capture_complete = _ignored_paths(repo, ignored_path_exclusions)
    index_flags = _run_git_bytes(repo, ["git", "ls-files", "-v", "-z"])
    unsafe_index_paths = _unsafe_index_paths(index_flags)
    # 未跟踪文件只参与工作区指纹，不把其内容带入 reflect/reviewer 输入。
    # ignored 普通小文件使用有界内容指纹；敏感文件只记录增强元数据，绝不读取内容。
    full_diff = render_tracked_diff_sections(staged_diff, unstaged_diff)
    untracked_manifest_sha256, untracked_content_complete = _untracked_manifest(
        repo,
        untracked_files,
    )
    (
        ignored_manifest_sha256,
        ignored_metadata_complete,
        ignored_content_complete,
    ) = _ignored_manifest(
        repo,
        ignored_files,
    )
    ignored_manifest_complete = ignored_capture_complete and ignored_metadata_complete
    git_control_sha256, git_control_complete = _git_control_manifest(repo)
    status_sha256 = _sha256(status)
    full_diff_sha256 = _sha256(full_diff.encode("utf-8"))
    staged_diff_sha256 = _sha256(staged_diff.encode("utf-8"))
    unstaged_diff_sha256 = _sha256(unstaged_diff.encode("utf-8"))
    fingerprint_payload = "\n".join(
        [
            f"head={head_sha}",
            f"status={status_sha256}",
            f"full_diff={full_diff_sha256}",
            f"staged_diff={staged_diff_sha256}",
            f"unstaged_diff={unstaged_diff_sha256}",
            f"untracked={untracked_manifest_sha256}",
            f"untracked_content_complete={untracked_content_complete}",
            f"ignored={ignored_manifest_sha256}",
            f"ignored_manifest_complete={ignored_manifest_complete}",
            f"ignored_content_complete={ignored_content_complete}",
            f"git_control={git_control_sha256}",
            f"git_control_complete={git_control_complete}",
            f"index_flags={_sha256(index_flags)}",
        ]
    ).encode("utf-8")
    return ReviewWorkspaceSnapshot(
        fingerprint=_sha256(fingerprint_payload),
        head_sha=head_sha,
        status_sha256=status_sha256,
        staged_diff_sha256=staged_diff_sha256,
        unstaged_diff_sha256=unstaged_diff_sha256,
        untracked_manifest_sha256=untracked_manifest_sha256,
        ignored_manifest_sha256=ignored_manifest_sha256,
        index_flags_sha256=_sha256(index_flags),
        full_diff=full_diff,
        staged_diff=staged_diff,
        unstaged_diff=unstaged_diff,
        changed_files=tuple(dict.fromkeys([*tracked_files, *untracked_files])),
        untracked_files=tuple(untracked_files),
        unsafe_index_paths=tuple(unsafe_index_paths),
        untracked_content_complete=untracked_content_complete,
        ignored_manifest_complete=ignored_manifest_complete,
        ignored_content_complete=ignored_content_complete,
        git_control_sha256=git_control_sha256,
        git_control_complete=git_control_complete,
    )


def run_workspace_check(
    repo_path: Path,
    output_dir: Path,
    *,
    baseline: WorkspaceSnapshot | None = None,
    expected_head_sha: str | None = None,
    require_clean_untracked: bool = False,
    allow_existing_tracked_diff: bool = False,
) -> WorkspaceCheckResult:
    """检查 worker 是否制造了明显工作区污染，并写入可复盘 artifact。

    这里不自动清理任何文件，只在新增未跟踪文件超过项目预算时停止后续自动流程。
    这样可以防止短生命周期 worker 失控生成大量临时文件，同时保留现场供人工判断。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result = WorkspaceCheckResult.model_validate(
        redact_value(
            evaluate_workspace(
                repo_path,
                baseline=baseline,
                expected_head_sha=expected_head_sha,
                require_clean_untracked=require_clean_untracked,
                allow_existing_tracked_diff=allow_existing_tracked_diff,
            ).model_dump(mode="json")
        )
    )
    output_dir.joinpath("workspace-check.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_dir.joinpath("workspace-check.md").write_text(
        redact_text(render_workspace_check(result)),
        encoding="utf-8",
    )
    return result


def evaluate_workspace(
    repo_path: Path,
    *,
    baseline: WorkspaceSnapshot | None = None,
    expected_head_sha: str | None = None,
    require_clean_untracked: bool = False,
    allow_existing_tracked_diff: bool = False,
) -> WorkspaceCheckResult:
    repo = repo_path.resolve()
    try:
        current = _capture_current_workspace_inventory(
            repo,
            baseline.ignored_path_exclusions if baseline else frozenset(),
        )
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return WorkspaceCheckResult(
            status="failed",
            repo_path=str(repo),
            reasons=[f"无法读取 Git 工作区或控制文件：{exc}"],
        )

    try:
        config = load_project_config(repo)
        max_new_files = config.budget.max_new_files
    except Exception as exc:  # noqa: BLE001 - 配置错误应进入可读报告，不在这里抛散
        return WorkspaceCheckResult(
            status="failed",
            repo_path=str(repo),
            reasons=[f"无法读取 .vega.yaml 预算配置：{exc}"],
            raw_status=_safe_git_status(current.raw_status),
        )

    current_untracked = _untracked_paths(current.raw_status)
    previous_untracked = baseline.untracked_files if baseline else frozenset()
    new_untracked = sorted(set(current_untracked) - set(previous_untracked))
    baseline_tracked_files = sorted(baseline.tracked_files) if baseline else []
    current_baseline_manifest_sha256 = _untracked_manifest_hash(
        repo,
        sorted(previous_untracked),
    )
    baseline_tracked_changes_present = bool(baseline and baseline.has_tracked_changes)
    baseline_head_sha = expected_head_sha or (
        baseline.head_sha if baseline and baseline.head_sha else None
    )
    baseline_head_changed = bool(
        baseline_head_sha and baseline_head_sha != current.head_sha
    )
    assessment = _WorkspaceAssessment(status="passed", reasons=[])
    _assess_baseline_integrity(
        assessment,
        baseline=baseline,
        baseline_head_changed=baseline_head_changed,
        current_baseline_manifest_sha256=current_baseline_manifest_sha256,
        allow_existing_tracked_diff=allow_existing_tracked_diff,
    )
    _assess_workspace_controls(
        assessment,
        baseline=baseline,
        current=current,
    )
    _assess_untracked_budget(
        assessment,
        current_untracked=current_untracked,
        new_untracked=new_untracked,
        max_new_files=max_new_files,
        require_clean_untracked=require_clean_untracked,
    )

    return WorkspaceCheckResult(
        status=assessment.status,
        repo_path=str(repo),
        max_new_files=max_new_files,
        new_untracked_count=len(new_untracked),
        new_untracked_files=[_safe_path_for_report(path) for path in new_untracked],
        baseline_tracked_changes_present=baseline_tracked_changes_present,
        baseline_tracked_files=[
            _safe_path_for_report(path) for path in baseline_tracked_files
        ],
        baseline_untracked_changed=assessment.baseline_untracked_changed,
        baseline_ignored_changed=assessment.baseline_ignored_changed,
        baseline_ignored_manifest_complete=bool(
            baseline and baseline.ignored_manifest_complete
        ),
        current_ignored_manifest_complete=current.ignored_manifest_complete,
        baseline_ignored_content_complete=bool(
            baseline and baseline.ignored_content_complete
        ),
        current_ignored_content_complete=current.ignored_content_complete,
        git_control_changed=assessment.git_control_changed,
        git_control_complete=current.git_control_complete,
        baseline_head_sha=baseline_head_sha,
        current_head_sha=current.head_sha,
        baseline_head_changed=baseline_head_changed,
        reasons=assessment.reasons,
        raw_status=_safe_git_status(current.raw_status),
    )


def _capture_current_workspace_inventory(
    repo: Path,
    ignored_path_exclusions: frozenset[str],
) -> CurrentWorkspaceInventory:
    head_sha = read_head_sha(repo)
    raw_status = _git_status(repo)
    ignored_files, ignored_capture_complete = _ignored_paths(
        repo,
        ignored_path_exclusions,
    )
    (
        ignored_manifest_sha256,
        ignored_metadata_complete,
        ignored_content_complete,
    ) = _ignored_manifest(
        repo,
        ignored_files,
    )
    ignored_manifest_complete = (
        ignored_capture_complete and ignored_metadata_complete
    )
    git_control_sha256, git_control_complete = _git_control_manifest(repo)
    return CurrentWorkspaceInventory(
        head_sha=head_sha,
        raw_status=raw_status,
        ignored_manifest_sha256=ignored_manifest_sha256,
        ignored_manifest_complete=ignored_manifest_complete,
        ignored_content_complete=ignored_content_complete,
        git_control_sha256=git_control_sha256,
        git_control_complete=git_control_complete,
    )


def _assess_baseline_integrity(
    assessment: _WorkspaceAssessment,
    *,
    baseline: WorkspaceSnapshot | None,
    baseline_head_changed: bool,
    current_baseline_manifest_sha256: str,
    allow_existing_tracked_diff: bool,
) -> None:
    if baseline and not baseline.capture_complete:
        assessment.status = "failed"
        assessment.reasons.append("worker 启动前未能完整捕获工作区基线。")
    elif baseline_head_changed:
        assessment.status = "failed"
        assessment.reasons.append(
            "worker 执行期间 Git HEAD 发生变化；自动流程禁止 worker commit、checkout 或 rebase。"
        )
    elif baseline and baseline.has_tracked_changes:
        if allow_existing_tracked_diff:
            assessment.reasons.append(
                "worker 启动前保留上一轮 auto 已产生的 tracked diff，"
                "将其作为本轮工作区基线继续迭代。"
            )
        else:
            assessment.status = "failed"
            assessment.reasons.append(
                "worker 启动前已存在 tracked diff；loop 无法将其安全归因于本轮 worker。"
            )
    if (
        baseline
        and baseline.untracked_manifest_sha256 != current_baseline_manifest_sha256
    ):
        assessment.baseline_untracked_changed = True
        assessment.status = "failed"
        assessment.reasons.append("worker 修改或删除了启动前已存在的未跟踪文件。")


def _assess_workspace_controls(
    assessment: _WorkspaceAssessment,
    *,
    baseline: WorkspaceSnapshot | None,
    current: CurrentWorkspaceInventory,
) -> None:
    if baseline is None:
        return
    if not baseline.git_control_complete or not current.git_control_complete:
        assessment.status = "failed"
        assessment.reasons.append("无法完整读取 Git 控制文件，不能信任后续状态与 diff。")
    elif baseline.git_control_sha256 != current.git_control_sha256:
        assessment.git_control_changed = True
        assessment.status = "failed"
        assessment.reasons.append(
            "worker 修改了 Git 控制文件；已拒绝使用可能被改写的忽略或 diff 语义。"
        )
    if not (
        baseline.ignored_manifest_complete
        and current.ignored_manifest_complete
    ):
        assessment.status = "failed"
        assessment.reasons.append(
            "无法完整构建 ignored 清单，不能信任其变更比较。"
        )
    elif baseline.ignored_manifest_sha256 != current.ignored_manifest_sha256:
        assessment.baseline_ignored_changed = True
        assessment.status = "failed"
        assessment.reasons.append("worker 新增、修改或删除了 ignored 路径。")
    if (
        baseline.ignored_manifest_complete
        and current.ignored_manifest_complete
        and not (
            baseline.ignored_content_complete
            and current.ignored_content_complete
        )
    ):
        assessment.reasons.append(
            "ignored 路径根与元数据清单完整，但敏感路径、预算外文件或折叠目录内部内容未读取；"
            "自动决策依赖稳定元数据比较，不将其表述为恶意本地写者的完整文件系统证明。"
        )


def _assess_untracked_budget(
    assessment: _WorkspaceAssessment,
    *,
    current_untracked: list[str],
    new_untracked: list[str],
    max_new_files: int | None,
    require_clean_untracked: bool,
) -> None:
    if require_clean_untracked and current_untracked:
        assessment.status = "failed"
        assessment.reasons.append("当前工作区存在未跟踪文件，隔离 reviewer 无法审查其内容。")
        return
    if max_new_files is None:
        if assessment.status != "failed":
            assessment.status = "skipped"
        assessment.reasons.append("未配置 budget.max_new_files，跳过新增未跟踪文件数量门禁。")
        return
    if len(new_untracked) > max_new_files:
        assessment.status = "failed"
        assessment.reasons.append(
            f"新增未跟踪文件数量超过预算：{len(new_untracked)} > {max_new_files}。"
        )
        return
    assessment.reasons.append(
        f"新增未跟踪文件数量在预算内：{len(new_untracked)} <= {max_new_files}。"
    )


def render_workspace_check(result: WorkspaceCheckResult) -> str:
    lines = [
        "# Workspace Check",
        "",
        f"- 仓库：`{result.repo_path}`",
        f"- 状态：`{result.status}`",
        f"- 新增未跟踪文件：`{result.new_untracked_count}`",
        f"- 启动前已有 tracked diff：`{str(result.baseline_tracked_changes_present).lower()}`",
        f"- 启动前未跟踪文件发生变化：`{str(result.baseline_untracked_changed).lower()}`",
        f"- ignored 路径发生变化：`{str(result.baseline_ignored_changed).lower()}`",
        f"- ignored 基线清单完整：`{str(result.baseline_ignored_manifest_complete).lower()}`",
        f"- ignored 当前清单完整：`{str(result.current_ignored_manifest_complete).lower()}`",
        f"- ignored 基线内容完整：`{str(result.baseline_ignored_content_complete).lower()}`",
        f"- ignored 当前内容完整：`{str(result.current_ignored_content_complete).lower()}`",
        f"- Git 控制文件发生变化：`{str(result.git_control_changed).lower()}`",
        f"- Git 控制文件完整：`{str(result.git_control_complete).lower()}`",
        f"- 执行期间 HEAD 发生变化：`{str(result.baseline_head_changed).lower()}`",
        f"- 预算上限：`{result.max_new_files if result.max_new_files is not None else '未配置'}`",
        "",
        "## 结论",
        "",
    ]
    lines.extend(f"- {reason}" for reason in result.reasons)
    lines.extend(["", "## 启动前 tracked diff", ""])
    if result.baseline_tracked_files:
        lines.extend(f"- `{path}`" for path in result.baseline_tracked_files[:50])
        if len(result.baseline_tracked_files) > 50:
            lines.append(f"- ... 另有 {len(result.baseline_tracked_files) - 50} 个文件")
    else:
        lines.append("- 无")
    lines.extend(["", "## 新增未跟踪文件", ""])
    if result.new_untracked_files:
        lines.extend(f"- `{path}`" for path in result.new_untracked_files[:50])
        if len(result.new_untracked_files) > 50:
            lines.append(f"- ... 另有 {len(result.new_untracked_files) - 50} 个文件")
    else:
        lines.append("- 无")
    lines.extend(["", "## Git Status", "", "```text", result.raw_status.strip() or "<clean>", "```"])
    return "\n".join(lines).rstrip() + "\n"


def _git_status(repo_path: Path) -> str:
    return _run_git_bytes(
        repo_path,
        ["git", "status", "--short", "--untracked-files=all"],
    ).decode("utf-8", errors="replace")


def collect_tracked_diff_parts(
    repo_path: Path,
    options: list[str],
) -> tuple[str, str]:
    """分别读取 staged（index 对 HEAD）和 unstaged（工作区对 index）差异。

    不能用 ``git diff HEAD`` 代替两者并集：当同一文件同时是 ``MM`` 状态时，
    它只给出 HEAD 到最终工作区的净差异，可能把 index 中尚未审查的内容抵消掉。
    """
    allowed_returncodes = (0, 1, 2) if "--check" in options else (0,)
    staged = _run_git_bytes(
        repo_path,
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--cached",
            *options,
            "HEAD",
            "--",
        ],
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace")
    unstaged = _run_git_bytes(
        repo_path,
        ["git", "diff", "--no-ext-diff", "--no-textconv", *options, "--"],
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace")
    return _normalize_newlines(staged), _normalize_newlines(unstaged)


def render_tracked_diff_sections(staged_diff: str, unstaged_diff: str) -> str:
    """生成可审查的双事实流 patch，明确区分 index 与工作区差异。"""
    sections: list[str] = []
    if staged_diff.strip():
        sections.append(
            "# --- Vega staged diff: index vs HEAD ---\n"
            + staged_diff.rstrip()
        )
    if unstaged_diff.strip():
        sections.append(
            "# --- Vega unstaged diff: working tree vs index ---\n"
            + unstaged_diff.rstrip()
        )
    return "\n\n".join(sections).rstrip() + ("\n" if sections else "")


def capture_tracked_scope_snapshot(
    repo_path: Path,
    *,
    include_untracked: bool = False,
) -> TrackedScopeSnapshot:
    """在两次 HEAD/status 读取之间采集 staged 与 unstaged 路径。

    porcelain v2 的单次输出同时包含 HEAD、index 与工作区状态；前后两次字节完全一致才
    采信。这样既避免 staged/unstaged 独立进程之间的混合时刻，也只需前后各读取一次
    status 与 index flags，共 4 个只读 Git 进程，仍符合轻量 runtime 的约束。
    """
    repo = repo_path.resolve()
    command = [
        "git",
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        f"--untracked-files={'all' if include_untracked else 'no'}",
    ]
    index_flags_before = _run_git_bytes(repo, ["git", "ls-files", "-v", "-z"])
    status_before = _run_git_bytes(repo, command)
    status_after = _run_git_bytes(repo, command)
    index_flags_after = _run_git_bytes(repo, ["git", "ls-files", "-v", "-z"])
    if status_before != status_after or index_flags_before != index_flags_after:
        raise RuntimeError("scope gate 采集期间 HEAD、tracked status 或 index 标记发生变化")
    head_sha, staged, unstaged, untracked = _parse_porcelain_v2_status(
        status_after
    )
    return TrackedScopeSnapshot(
        head_sha=head_sha,
        status_sha256=_sha256(status_after),
        index_flags_sha256=_sha256(index_flags_after),
        staged_files=tuple(staged),
        unstaged_files=tuple(unstaged),
        untracked_files=tuple(untracked),
        unsafe_index_paths=tuple(_unsafe_index_paths(index_flags_after)),
    )


def _unsafe_index_paths(payload: bytes) -> list[str]:
    """找出会让 Git 工作区视图忽略真实文件变化的 index 标记。"""
    paths: list[str] = []
    for item in payload.split(b"\0"):
        if not item:
            continue
        record = item.decode("utf-8", errors="replace")
        if len(record) < 3 or record[1] != " ":
            raise RuntimeError("git ls-files -v 输出格式不完整")
        tag = record[0]
        if tag == "S" or tag.islower():
            paths.append(record[2:])
    return list(dict.fromkeys(paths))


def read_head_sha(repo_path: Path) -> str:
    """读取当前提交身份；没有可解析 HEAD 时由调用方 fail-closed。"""
    return _read_head_sha(repo_path, run_git_bytes=_run_git_bytes)


def read_core_ignorecase(repo_path: Path) -> bool | None:
    """只读取仓库本地的 core.ignorecase；未配置时交由文件系统语义决定。"""
    return _read_core_ignorecase(repo_path, run_git_bytes=_run_git_bytes)


def _untracked_manifest_hash(repo_path: Path, paths: list[str]) -> str:
    return _untracked_manifest(repo_path, paths)[0]


def _ignored_paths(
    repo_path: Path,
    exclusions: frozenset[str] = frozenset(),
) -> tuple[list[str], bool]:
    paths, complete = read_ignored_paths(repo_path)
    return filter_codex_runtime_ignored_paths(repo_path, paths, exclusions), complete


def _untracked_manifest(
    repo_path: Path,
    paths: list[str],
) -> tuple[str, bool]:
    result = build_content_manifest(
        repo_path,
        paths,
        version="untracked-v3",
        budget=ContentManifestBudget(
            max_content_files=MAX_UNTRACKED_CONTENT_FILES,
            max_file_bytes=MAX_UNTRACKED_FILE_BYTES,
            max_content_bytes=MAX_UNTRACKED_CONTENT_BYTES,
        ),
    )
    return result.sha256, result.content_complete


def _ignored_manifest(
    repo_path: Path,
    paths: list[str],
) -> tuple[str, bool, bool]:
    result = build_content_manifest(
        repo_path,
        paths,
        version="ignored-v5",
        budget=ContentManifestBudget(
            max_content_files=MAX_IGNORED_CONTENT_FILES,
            max_file_bytes=MAX_IGNORED_FILE_BYTES,
            max_content_bytes=MAX_IGNORED_CONTENT_BYTES,
            max_metadata_files=MAX_IGNORED_METADATA_FILES,
        ),
    )
    return (
        result.sha256,
        result.metadata_complete,
        result.content_complete,
    )


def ignored_coverage_level(
    manifest_complete: object,
    content_complete: object,
) -> str:
    if manifest_complete is not True:
        return "incomplete"
    if content_complete is True:
        return "full_content"
    return "metadata_bounded"


def _git_control_manifest(repo_path: Path) -> tuple[str, bool]:
    return build_git_control_manifest(
        repo_path,
        run_git_bytes=_run_git_bytes,
        max_file_bytes=MAX_GIT_CONTROL_FILE_BYTES,
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
