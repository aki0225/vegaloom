from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path

from .models import ToolResult
from .redaction import assert_not_sensitive_path, is_sensitive_path, redact_text


TEXT_EXTENSIONS = {
    ".cfg",
    ".c",
    ".cc",
    ".conf",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".mdx",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_FILE_CHARS = 6000
MAX_DIRECTORY_FILES = 12
MAX_DIRECTORY_FILE_CHARS = 2500
MAX_DIRECTORY_ENTRIES = 2000
DEFAULT_TARGET_SECTION_NAMES = ["Target files", "Target files to search", "目标文件", "目标文件："]


def parse_target_files(task_text: str, section_names: list[str] | None = None) -> list[str]:
    targets: list[str] = []
    in_targets = False
    names = section_names or DEFAULT_TARGET_SECTION_NAMES

    for line in task_text.splitlines():
        stripped = line.strip()

        if _is_target_section(stripped, names):
            in_targets = True
            continue

        if not in_targets:
            continue
        if not stripped:
            if targets:
                break
            continue
        if targets and stripped.endswith(":") and not _is_list_item(stripped):
            break

        extracted = _extract_target_from_line(stripped)
        if extracted:
            targets.extend(extracted)
        elif targets and not _is_list_item(stripped):
            break

    return _dedupe_preserving_order(targets)


def load_target_context(repo_path: Path, targets: list[str]) -> list[ToolResult]:
    results: list[ToolResult] = []
    for target in targets:
        safe_target = _safe_path_label(target)
        try:
            resolved = resolve_repo_target(repo_path, target)
            if resolved.is_file():
                results.append(_read_file_context(repo_path, resolved))
            elif resolved.is_dir():
                results.append(_read_directory_context(repo_path, resolved))
            else:
                results.append(
                    ToolResult(
                        tool="file.read",
                        status="error",
                        output={"path": safe_target},
                        error=f"目标不存在：{safe_target}",
                    )
                )
        except Exception as exc:
            results.append(
                ToolResult(
                    tool="file.read",
                    status="error",
                    output={"path": safe_target},
                    error=redact_text(str(exc)),
                )
            )
    return results


def resolve_repo_target(repo_path: Path, relative_path: str) -> Path:
    cleaned = relative_path.strip().strip("`").replace("\\", "/")
    assert_not_sensitive_path(cleaned)
    candidate = Path(cleaned)
    if candidate.is_absolute():
        raise ValueError(f"路径越过仓库边界：{relative_path}")

    repo = repo_path.resolve()
    target_path = repo / Path(os.path.normpath(str(candidate)))
    _assert_no_link_or_reparse(repo, target_path)
    target = target_path.resolve()
    if target != repo and repo not in target.parents:
        raise ValueError(f"路径越过仓库边界：{relative_path}")
    assert_not_sensitive_path(target)
    return target


def _is_target_section(line: str, section_names: list[str]) -> bool:
    normalized = _normalize_section_name(line)
    for name in section_names:
        expected = _normalize_section_name(name)
        if normalized == expected:
            return True
    return False


def _normalize_section_name(value: str) -> str:
    return value.strip().strip("#").strip().rstrip(":：").lower()


def _extract_target_from_line(line: str) -> list[str]:
    backticked = re.findall(r"`([^`]+)`", line)
    if backticked:
        return [item.strip() for item in backticked if item.strip()]

    if not _is_list_item(line):
        return []
    value = re.sub(r"^[-*]\s+", "", line)
    value = re.sub(r"^\d+[.)]\s+", "", value)
    value = value.strip()
    return [value] if value else []


def _is_list_item(line: str) -> bool:
    return bool(re.match(r"^([-*]|\d+[.)])\s+", line))


def _read_file_context(repo_path: Path, path: Path) -> ToolResult:
    return ToolResult(
        tool="file.read",
        output={
            "path": _repo_relative(repo_path, path),
            "kind": "file",
            "content": _read_text_excerpt(path, MAX_FILE_CHARS),
        },
    )


def _read_directory_context(repo_path: Path, path: Path) -> ToolResult:
    files = sorted(
        _iter_directory_files(
            repo_path,
            path,
            max_files=MAX_DIRECTORY_FILES,
            max_entries=MAX_DIRECTORY_ENTRIES,
        ),
        key=lambda child: _repo_relative(repo_path, child).casefold(),
    )
    return ToolResult(
        tool="file.read",
        output={
            "path": _repo_relative(repo_path, path),
            "kind": "directory",
            "files": [_repo_relative(repo_path, file_path) for file_path in files],
            "summaries": [
                {
                    "path": _repo_relative(repo_path, file_path),
                    "content": _read_text_excerpt(file_path, MAX_DIRECTORY_FILE_CHARS),
                }
                for file_path in files
            ],
        },
    )


def _is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS


def _is_safe_directory_file(repo_path: Path, path: Path) -> bool:
    if not _is_text_candidate(path) or is_sensitive_path(path):
        return False
    try:
        stat_result = path.lstat()
        if _is_link_or_reparse_stat(stat_result) or not stat.S_ISREG(stat_result.st_mode):
            return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(repo_path.resolve())
        assert_not_sensitive_path(resolved)
    except (OSError, ValueError):
        return False
    return True


def _read_text_excerpt(path: Path, max_chars: int) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        return redact_text(stream.read(max_chars))


def _iter_directory_files(
    repo_path: Path,
    root: Path,
    *,
    max_files: int,
    max_entries: int,
) -> Iterator[Path]:
    pending = [root]
    entries_seen = 0
    files_yielded = 0

    while pending and entries_seen < max_entries and files_yielded < max_files:
        current = pending.pop()
        try:
            if _path_is_link_or_reparse(current):
                continue
            with os.scandir(current) as entries:
                iterator = iter(entries)
                while entries_seen < max_entries and files_yielded < max_files:
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    entries_seen += 1
                    if _entry_is_link_or_reparse(entry):
                        continue

                    child = Path(entry.path)
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(child)
                        elif entry.is_file(follow_symlinks=False) and _is_safe_directory_file(
                            repo_path,
                            child,
                        ):
                            files_yielded += 1
                            yield child
                    except OSError:
                        continue
        except OSError:
            continue


def _assert_no_link_or_reparse(repo_path: Path, target_path: Path) -> None:
    try:
        relative = target_path.relative_to(repo_path)
    except ValueError:
        return

    current = repo_path
    for part in relative.parts:
        current /= part
        try:
            stat_result = current.lstat()
        except OSError:
            return
        if _is_link_or_reparse_stat(stat_result):
            raise ValueError(f"拒绝读取链接或 reparse point：{current}")


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        return _is_link_or_reparse_stat(path.lstat())
    except OSError:
        return False


def _entry_is_link_or_reparse(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_symlink() or _is_link_or_reparse_stat(entry.stat(follow_symlinks=False))
    except OSError:
        return True


def _is_link_or_reparse_stat(stat_result: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(stat_result, "st_file_attributes", 0)
    return stat.S_ISLNK(stat_result.st_mode) or bool(file_attributes & reparse_flag)


def _repo_relative(repo_path: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_path.resolve()).as_posix()


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _safe_path_label(path: str) -> str:
    if is_sensitive_path(path):
        return "[SENSITIVE_PATH]"
    return redact_text(path)
