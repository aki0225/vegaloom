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
    full_diff: str
    staged_diff: str
    unstaged_diff: str
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    untracked_content_complete: bool = False


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
    reasons: list[str] = Field(default_factory=list)
    raw_status: str = ""

    @property
    def has_failures(self) -> bool:
        return self.status == "failed"


def snapshot_workspace(repo_path: Path) -> WorkspaceSnapshot:
    repo = repo_path.resolve()
    try:
        status = _git_status(repo)
        tracked_files = _tracked_changed_files(repo)
    except RuntimeError as exc:
        return WorkspaceSnapshot(
            raw_status=f"<git status failed before worker: {exc}>",
            tracked_files=frozenset(),
            untracked_files=frozenset(),
            capture_complete=False,
        )
    untracked_files = _untracked_paths(status)
    return WorkspaceSnapshot(
        raw_status=_safe_git_status(status),
        tracked_files=frozenset(tracked_files),
        untracked_files=frozenset(untracked_files),
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
    tracked_files = _tracked_changed_files(repo)
    untracked_files = _decode_nul_paths(
        _run_git_bytes(repo, ["git", "ls-files", "--others", "--exclude-standard", "-z"])
    )
    ignored_files = _decode_nul_paths(
        _run_git_bytes(
            repo,
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        )
    )
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
        full_diff=full_diff,
        staged_diff=staged_diff,
        unstaged_diff=unstaged_diff,
        changed_files=tuple(dict.fromkeys([*tracked_files, *untracked_files])),
        untracked_files=tuple(untracked_files),
        untracked_content_complete=untracked_content_complete,
    )


def run_workspace_check(
    repo_path: Path,
    output_dir: Path,
    *,
    baseline: WorkspaceSnapshot | None = None,
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
    require_clean_untracked: bool = False,
    allow_existing_tracked_diff: bool = False,
) -> WorkspaceCheckResult:
    repo = repo_path.resolve()
    try:
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
    reasons: list[str] = []
    status: Literal["passed", "failed", "skipped"] = "passed"
    if baseline and not baseline.capture_complete:
        status = "failed"
        reasons.append("worker 启动前未能完整捕获工作区基线。")
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
    if result.returncode not in allowed_returncodes:
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        output = stdout + format_git_error(repo_path, stderr)
        raise RuntimeError(output.strip())
    if result.returncode:
        return (result.stdout or b"") + (result.stderr or b"")
    return result.stdout or b""


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


def _tracked_changed_files(repo_path: Path) -> list[str]:
    staged = _decode_nul_paths(
        _run_git_bytes(
            repo_path,
            ["git", "diff", "--cached", "--name-only", "-z", "HEAD", "--"],
        )
    )
    unstaged = _decode_nul_paths(
        _run_git_bytes(
            repo_path,
            ["git", "diff", "--name-only", "-z", "--"],
        )
    )
    return list(dict.fromkeys([*staged, *unstaged]))


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
