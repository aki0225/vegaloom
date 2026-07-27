from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path


def matching_patterns(
    path: str,
    patterns: list[str],
    *,
    case_sensitive: bool = True,
) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if path_matches_pattern(path, pattern, case_sensitive=case_sensitive)
    ]


def path_matches_pattern(
    path: str,
    pattern: str,
    *,
    case_sensitive: bool = True,
) -> bool:
    """以 segment 为边界匹配 POSIX glob，`**` 仅表示零到多个完整目录段。"""
    path_segments = tuple(path.split("/"))
    pattern_segments = tuple(pattern.split("/"))
    if not case_sensitive:
        path_segments = tuple(segment.casefold() for segment in path_segments)
        pattern_segments = tuple(segment.casefold() for segment in pattern_segments)

    @lru_cache(maxsize=None)
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_segments):
            return path_index == len(path_segments)
        current_pattern = pattern_segments[pattern_index]
        if current_pattern == "**":
            if pattern_index == len(pattern_segments) - 1:
                return True
            return any(
                matches(next_path_index, pattern_index + 1)
                for next_path_index in range(path_index, len(path_segments) + 1)
            )
        if path_index == len(path_segments):
            return False
        if not fnmatchcase(path_segments[path_index], current_pattern):
            return False
        return matches(path_index + 1, pattern_index + 1)

    return matches(0, 0)


def scope_paths_are_case_insensitive(repo_path: Path) -> bool:
    return filesystem_is_case_insensitive(repo_path)


def filesystem_is_case_insensitive(repo_path: Path) -> bool:
    """只读探测目标路径本身，避免宿主默认规则误判挂载卷的大小写语义。"""
    resolved = repo_path.resolve(strict=True)
    current = resolved
    while current != current.parent:
        alternate_name = _swap_first_ascii_letter_case(current.name)
        if alternate_name != current.name:
            alternate = current.with_name(alternate_name)
            try:
                return alternate.exists() and current.samefile(alternate)
            except OSError as exc:
                raise RuntimeError("无法探测目标文件系统的大小写语义") from exc
        current = current.parent
    raise RuntimeError("目标路径没有可用于大小写探测的名称")


def _swap_first_ascii_letter_case(value: str) -> str:
    for index, character in enumerate(value):
        if "a" <= character <= "z":
            return value[:index] + character.upper() + value[index + 1 :]
        if "A" <= character <= "Z":
            return value[:index] + character.lower() + value[index + 1 :]
    return value
