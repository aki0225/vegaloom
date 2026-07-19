from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .project_config import load_project_config
from .redaction import is_sensitive_path, redact_text, redact_value, sensitive_path_reason
from .tools.git_tools import format_git_error


MAX_IGNORED_METADATA_FILES = 4096
MAX_IGNORED_CONTENT_FILES = 256
MAX_IGNORED_FILE_BYTES = 1024 * 1024
MAX_IGNORED_CONTENT_BYTES = 8 * 1024 * 1024
MAX_UNTRACKED_CONTENT_FILES = MAX_IGNORED_CONTENT_FILES
MAX_UNTRACKED_FILE_BYTES = MAX_IGNORED_FILE_BYTES
MAX_UNTRACKED_CONTENT_BYTES = MAX_IGNORED_CONTENT_BYTES


@dataclass(frozen=True)
class WorkspaceSnapshot:
    raw_status: str
    tracked_files: frozenset[str]
    untracked_files: frozenset[str]
    head_sha: str = ""
    untracked_manifest_sha256: str = ""
    capture_complete: bool = True

    @property
    def has_tracked_changes(self) -> bool:
        """启动前已有 tracked diff 时，auto worker 无法安全归因本轮成果。"""
        return bool(self.tracked_files)


@dataclass(frozen=True)
class ReviewWorkspaceSnapshot:
    fingerprint: str
    head_sha: str
    status_sha256: str
    full_diff_sha256: str
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
    baseline_head_sha: str | None = None
    current_head_sha: str | None = None
    baseline_head_changed: bool = False
    reasons: list[str] = Field(default_factory=list)
    raw_status: str = ""

    @property
    def has_failures(self) -> bool:
        return self.status == "failed"


def snapshot_workspace(repo_path: Path) -> WorkspaceSnapshot:
    repo = repo_path.resolve()
    try:
        tracked_snapshot = capture_tracked_scope_snapshot(
            repo,
            include_untracked=True,
        )
    except RuntimeError as exc:
        return WorkspaceSnapshot(
            raw_status=f"<git status failed before worker: {exc}>",
            tracked_files=frozenset(),
            untracked_files=frozenset(),
            capture_complete=False,
        )
    tracked_files = [
        *tracked_snapshot.staged_files,
        *tracked_snapshot.unstaged_files,
    ]
    untracked_files = list(tracked_snapshot.untracked_files)
    return WorkspaceSnapshot(
        raw_status="",
        tracked_files=frozenset(dict.fromkeys(tracked_files)),
        untracked_files=frozenset(untracked_files),
        head_sha=tracked_snapshot.head_sha,
        untracked_manifest_sha256=_untracked_manifest_hash(repo, untracked_files),
    )


def capture_review_workspace(repo_path: Path) -> ReviewWorkspaceSnapshot:
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
    ignored_files = _decode_nul_paths(
        _run_git_bytes(
            repo,
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        )
    )
    index_flags = _run_git_bytes(repo, ["git", "ls-files", "-v", "-z"])
    unsafe_index_paths = _unsafe_index_paths(index_flags)
    # 未跟踪文件只参与工作区指纹，不把其内容带入 reflect/reviewer 输入。
    # ignored 普通小文件使用有界内容指纹；敏感文件只记录增强元数据，绝不读取内容。
    full_diff = render_tracked_diff_sections(staged_diff, unstaged_diff)
    untracked_manifest_sha256, untracked_content_complete = _untracked_manifest(
        repo,
        untracked_files,
    )
    ignored_manifest_sha256 = _ignored_manifest_hash(repo, ignored_files)
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
            f"index_flags={_sha256(index_flags)}",
        ]
    ).encode("utf-8")
    return ReviewWorkspaceSnapshot(
        fingerprint=_sha256(fingerprint_payload),
        head_sha=head_sha,
        status_sha256=status_sha256,
        full_diff_sha256=full_diff_sha256,
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
        current_head_sha = read_head_sha(repo)
        raw_status = _git_status(repo)
    except RuntimeError as exc:
        return WorkspaceCheckResult(
            status="failed",
            repo_path=str(repo),
            reasons=[f"无法读取 git status：{exc}"],
        )

    try:
        config = load_project_config(repo)
        max_new_files = config.budget.max_new_files
    except Exception as exc:  # noqa: BLE001 - 配置错误应进入可读报告，不在这里抛散
        return WorkspaceCheckResult(
            status="failed",
            repo_path=str(repo),
            reasons=[f"无法读取 .vega.yaml 预算配置：{exc}"],
            raw_status=_safe_git_status(raw_status),
        )

    current_untracked = _untracked_paths(raw_status)
    previous_untracked = baseline.untracked_files if baseline else frozenset()
    new_untracked = sorted(set(current_untracked) - set(previous_untracked))
    baseline_tracked_files = sorted(baseline.tracked_files) if baseline else []
    current_baseline_manifest_sha256 = _untracked_manifest_hash(
        repo,
        sorted(previous_untracked),
    )
    baseline_tracked_changes_present = bool(baseline and baseline.has_tracked_changes)
    baseline_untracked_changed = False
    baseline_head_sha = expected_head_sha or (
        baseline.head_sha if baseline and baseline.head_sha else None
    )
    baseline_head_changed = bool(
        baseline_head_sha and baseline_head_sha != current_head_sha
    )
    reasons: list[str] = []
    status: Literal["passed", "failed", "skipped"] = "passed"
    if baseline and not baseline.capture_complete:
        status = "failed"
        reasons.append("worker 启动前未能完整捕获工作区基线。")
    elif baseline_head_changed:
        status = "failed"
        reasons.append(
            "worker 执行期间 Git HEAD 发生变化；自动流程禁止 worker commit、checkout 或 rebase。"
        )
    elif baseline and baseline.has_tracked_changes:
        if allow_existing_tracked_diff:
            reasons.append(
                "worker 启动前保留上一轮 auto 已产生的 tracked diff，"
                "将其作为本轮工作区基线继续迭代。"
            )
        else:
            status = "failed"
            reasons.append(
                "worker 启动前已存在 tracked diff；auto 无法将其安全归因于本轮 worker。"
            )
    elif (
        baseline
        and baseline.untracked_manifest_sha256 != current_baseline_manifest_sha256
    ):
        baseline_untracked_changed = True
        status = "failed"
        reasons.append("worker 修改或删除了启动前已存在的未跟踪文件。")

    if require_clean_untracked and current_untracked:
        status = "failed"
        reasons.append("当前工作区存在未跟踪文件，隔离 reviewer 无法审查其内容。")
    elif max_new_files is None:
        if status != "failed":
            status = "skipped"
        reasons.append("未配置 budget.max_new_files，跳过新增未跟踪文件数量门禁。")
    elif len(new_untracked) > max_new_files:
        status = "failed"
        reasons.append(
            f"新增未跟踪文件数量超过预算：{len(new_untracked)} > {max_new_files}。"
        )
    else:
        reasons.append(f"新增未跟踪文件数量在预算内：{len(new_untracked)} <= {max_new_files}。")

    return WorkspaceCheckResult(
        status=status,
        repo_path=str(repo),
        max_new_files=max_new_files,
        new_untracked_count=len(new_untracked),
        new_untracked_files=[_safe_path_for_report(path) for path in new_untracked],
        baseline_tracked_changes_present=baseline_tracked_changes_present,
        baseline_tracked_files=[
            _safe_path_for_report(path) for path in baseline_tracked_files
        ],
        baseline_untracked_changed=baseline_untracked_changed,
        baseline_head_sha=baseline_head_sha,
        current_head_sha=current_head_sha,
        baseline_head_changed=baseline_head_changed,
        reasons=reasons,
        raw_status=_safe_git_status(raw_status),
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
    result = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=repo_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=False,
    )
    stderr = format_git_error(repo_path, result.stderr or "")
    output = (result.stdout or "") + stderr
    if result.returncode != 0:
        raise RuntimeError(output.strip() or f"git status 退出码 {result.returncode}")
    return output


def _untracked_paths(status_text: str) -> list[str]:
    paths: list[str] = []
    for line in status_text.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip()
        if path:
            paths.append(path)
    return paths


def _safe_git_status(status_text: str) -> str:
    safe_lines: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 4:
            safe_lines.append(redact_text(line))
            continue
        prefix = line[:3]
        path_text = line[3:].strip()
        if not path_text:
            safe_lines.append(redact_text(line))
            continue
        safe_lines.append(f"{prefix}{_safe_status_path_expression(path_text)}")
    return "\n".join(safe_lines)


def _safe_status_path_expression(path_text: str) -> str:
    if " -> " in path_text:
        return " -> ".join(
            _safe_path_for_report(part.strip())
            for part in path_text.split(" -> ")
        )
    return _safe_path_for_report(path_text)


def _safe_path_for_report(path: str) -> str:
    reason = sensitive_path_reason(path)
    if reason:
        return f"<sensitive-path:{reason}>"
    return redact_text(path)


def _run_git_bytes(
    repo_path: Path,
    command: list[str],
    *,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    result = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        timeout=30,
        check=False,
    )
    stdout = _coerce_git_output_bytes(result.stdout)
    stderr = _coerce_git_output_bytes(result.stderr)
    if result.returncode not in allowed_returncodes:
        output = stdout.decode("utf-8", errors="replace") + format_git_error(
            repo_path,
            stderr.decode("utf-8", errors="replace"),
        )
        raise RuntimeError(output.strip())
    if result.returncode:
        return stdout + stderr
    return stdout


def _coerce_git_output_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return value or b""


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
        ["git", "diff", "--cached", *options, "HEAD", "--"],
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace")
    unstaged = _run_git_bytes(
        repo_path,
        ["git", "diff", *options, "--"],
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


def _parse_porcelain_v1_paths(payload: bytes) -> tuple[list[str], list[str]]:
    """从已采集的 porcelain v1 状态中提取 tracked 与 untracked 路径。

    reviewer 快照已经需要读取这份稳定机器格式，无需再启动三次 Git 进程分别枚举
    staged、unstaged 和 untracked 路径。rename 的 NUL 格式先给目标路径、再给源路径；
    两者都属于实际变更范围。copy 只记录新增目标路径，源路径内容没有发生变化。
    """
    tokens = [item for item in payload.split(b"\0") if item]
    tracked: list[str] = []
    untracked: list[str] = []
    index = 0
    while index < len(tokens):
        raw_record = tokens[index]
        index += 1
        if len(raw_record) < 4 or raw_record[2:3] != b" ":
            raise RuntimeError("porcelain v1 路径记录不完整")
        xy = raw_record[:2].decode("ascii", errors="replace")
        path = raw_record[3:].decode("utf-8", errors="replace")
        if xy == "??":
            untracked.append(path)
            continue
        if xy == "!!":
            continue

        change_kind = next(
            (status for status in xy if status in {"R", "C"}),
            None,
        )
        if change_kind is not None:
            if index >= len(tokens):
                raise RuntimeError("porcelain v1 rename/copy 记录缺少源路径")
            original_path = tokens[index].decode("utf-8", errors="replace")
            index += 1
            if change_kind == "R":
                tracked.extend([original_path, path])
            else:
                tracked.append(path)
            continue
        tracked.append(path)
    return (
        list(dict.fromkeys(tracked)),
        list(dict.fromkeys(untracked)),
    )


def _parse_porcelain_v2_status(
    payload: bytes,
) -> tuple[str, list[str], list[str], list[str]]:
    """解析 `git status --porcelain=v2 -z --branch` 的稳定机器格式。"""
    tokens = [item for item in payload.split(b"\0") if item]
    head_sha = ""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    index = 0
    while index < len(tokens):
        record = tokens[index].decode("utf-8", errors="replace")
        index += 1
        if record.startswith("# branch.oid "):
            head_sha = record.removeprefix("# branch.oid ").strip()
            continue
        if record.startswith("# "):
            continue
        if record.startswith("1 "):
            parts = record.split(" ", 8)
            if len(parts) != 9 or len(parts[1]) != 2:
                raise RuntimeError("porcelain v2 普通路径记录不完整")
            _append_status_paths(staged, unstaged, parts[1], parts[8])
            continue
        if record.startswith("2 "):
            parts = record.split(" ", 9)
            if len(parts) != 10 or len(parts[1]) != 2 or index >= len(tokens):
                raise RuntimeError("porcelain v2 rename/copy 记录不完整")
            original_path = tokens[index].decode("utf-8", errors="replace")
            index += 1
            change_kind = parts[8][:1]
            _append_status_paths(
                staged,
                unstaged,
                parts[1],
                parts[9],
                original_path=original_path,
                change_kind=change_kind,
            )
            continue
        if record.startswith("u "):
            parts = record.split(" ", 10)
            if len(parts) != 11:
                raise RuntimeError("porcelain v2 unmerged 记录不完整")
            staged.append(parts[10])
            unstaged.append(parts[10])
            continue
        if record.startswith("? "):
            untracked.append(record[2:])
            continue
        if record.startswith("! "):
            continue
        raise RuntimeError("porcelain v2 包含未知记录类型")
    if not head_sha or head_sha == "(initial)":
        raise RuntimeError("git HEAD 不可用")
    return (
        head_sha,
        list(dict.fromkeys(staged)),
        list(dict.fromkeys(unstaged)),
        list(dict.fromkeys(untracked)),
    )


def _append_status_paths(
    staged: list[str],
    unstaged: list[str],
    xy: str,
    path: str,
    *,
    original_path: str | None = None,
    change_kind: str | None = None,
) -> None:
    staged_status, unstaged_status = xy
    if staged_status != ".":
        _append_changed_path(staged, staged_status, path, original_path, change_kind)
    if unstaged_status != ".":
        _append_changed_path(
            unstaged,
            unstaged_status,
            path,
            original_path,
            change_kind,
        )


def _append_changed_path(
    target: list[str],
    status: str,
    path: str,
    original_path: str | None,
    change_kind: str | None,
) -> None:
    effective_kind = status if status in {"R", "C"} else change_kind
    if effective_kind == "R" and original_path is not None:
        target.extend([original_path, path])
    else:
        # Copy 的源路径没有发生修改；普通增删改也只记录当前路径。
        target.append(path)


def read_head_sha(repo_path: Path) -> str:
    """读取当前提交身份；没有可解析 HEAD 时由调用方 fail-closed。"""
    head_sha = _run_git_bytes(
        repo_path.resolve(),
        ["git", "rev-parse", "--verify", "HEAD"],
    ).decode("utf-8", errors="replace").strip()
    if not head_sha:
        raise RuntimeError("git HEAD 为空")
    return head_sha


def _decode_nul_paths(payload: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="replace")
        for item in payload.split(b"\0")
        if item
    ]


def _untracked_manifest_hash(repo_path: Path, paths: list[str]) -> str:
    return _untracked_manifest(repo_path, paths)[0]


def _untracked_manifest(
    repo_path: Path,
    paths: list[str],
) -> tuple[str, bool]:
    unique_paths = sorted(dict.fromkeys(paths))
    manifest = hashlib.sha256()
    manifest.update(f"untracked-v2:{len(unique_paths)}".encode("ascii"))
    manifest.update(b"\0")
    content_files = 0
    content_bytes = 0
    content_complete = True
    for relative_path in unique_paths:
        path = repo_path / relative_path
        manifest.update(relative_path.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
        try:
            stat_result = path.lstat()
        except OSError:
            manifest.update(b"<unreadable>")
            content_complete = False
        else:
            manifest.update(_stat_metadata(stat_result))
            if stat.S_ISLNK(stat_result.st_mode):
                try:
                    manifest.update(str(path.readlink()).encode("utf-8", errors="replace"))
                except OSError:
                    manifest.update(b"<unreadable-link>")
                    content_complete = False
            elif is_sensitive_path(relative_path):
                manifest.update(b"<sensitive-content-not-read>")
                content_complete = False
            elif stat.S_ISREG(stat_result.st_mode):
                remaining_bytes = MAX_UNTRACKED_CONTENT_BYTES - content_bytes
                if content_files >= MAX_UNTRACKED_CONTENT_FILES:
                    manifest.update(b"<content-file-budget-exceeded>")
                    content_complete = False
                elif stat_result.st_size > MAX_UNTRACKED_FILE_BYTES:
                    manifest.update(b"<content-file-too-large>")
                    content_complete = False
                elif stat_result.st_size > remaining_bytes:
                    manifest.update(b"<content-byte-budget-exceeded>")
                    content_complete = False
                else:
                    content_files += 1
                    content_bytes += stat_result.st_size
                    content_hash = _bounded_file_hash(path, stat_result.st_size)
                    try:
                        final_stat = path.lstat()
                    except OSError:
                        content_hash = b"<content-changed-during-read>"
                    else:
                        if _stat_metadata(final_stat) != _stat_metadata(stat_result):
                            content_hash = b"<content-changed-during-read>"
                    manifest.update(content_hash)
                    if not _is_complete_content_hash(content_hash):
                        content_complete = False
        manifest.update(b"\0")
    return manifest.hexdigest(), content_complete


def _ignored_manifest_hash(repo_path: Path, paths: list[str]) -> str:
    unique_paths = sorted(dict.fromkeys(paths))
    manifest = hashlib.sha256()
    manifest.update(f"ignored-v2:{len(unique_paths)}".encode("ascii"))
    manifest.update(b"\0")
    content_files = 0
    content_bytes = 0
    for index, relative_path in enumerate(unique_paths):
        path = repo_path / relative_path
        manifest.update(relative_path.encode("utf-8", errors="replace"))
        manifest.update(b"\0")
        if index >= MAX_IGNORED_METADATA_FILES:
            manifest.update(b"<metadata-budget-exceeded>")
            manifest.update(b"\0")
            continue
        try:
            stat_result = path.lstat()
        except OSError:
            manifest.update(b"<unreadable>")
        else:
            manifest.update(_stat_metadata(stat_result))
            if path.is_symlink():
                try:
                    manifest.update(str(path.readlink()).encode("utf-8", errors="replace"))
                except OSError:
                    manifest.update(b"<unreadable-link>")
            elif is_sensitive_path(relative_path):
                manifest.update(b"<sensitive-content-not-read>")
            elif stat.S_ISREG(stat_result.st_mode):
                remaining_bytes = MAX_IGNORED_CONTENT_BYTES - content_bytes
                if content_files >= MAX_IGNORED_CONTENT_FILES:
                    manifest.update(b"<content-file-budget-exceeded>")
                elif stat_result.st_size > MAX_IGNORED_FILE_BYTES:
                    manifest.update(b"<content-file-too-large>")
                elif stat_result.st_size > remaining_bytes:
                    manifest.update(b"<content-byte-budget-exceeded>")
                else:
                    manifest.update(_bounded_file_hash(path, stat_result.st_size))
                    content_files += 1
                    content_bytes += stat_result.st_size
        manifest.update(b"\0")
    return manifest.hexdigest()


def _stat_metadata(stat_result) -> bytes:
    return (
        f"{stat_result.st_mode}:{stat_result.st_size}:{stat_result.st_mtime_ns}:"
        f"{stat_result.st_ctime_ns}:{stat_result.st_dev}:{stat_result.st_ino}"
    ).encode("ascii")


def _bounded_file_hash(path: Path, expected_size: int) -> bytes:
    file_hash = hashlib.sha256()
    read_bytes = 0
    try:
        with path.open("rb") as stream:
            while read_bytes <= expected_size:
                chunk = stream.read(min(1024 * 1024, expected_size - read_bytes + 1))
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > expected_size:
                    return b"<content-changed-during-read>"
                file_hash.update(chunk)
    except OSError:
        return b"<content-unreadable>"
    if read_bytes != expected_size:
        return b"<content-changed-during-read>"
    return b"<content-sha256:" + file_hash.hexdigest().encode("ascii") + b">"


def _is_complete_content_hash(content_hash: bytes) -> bool:
    return content_hash.startswith(b"<content-sha256:")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
