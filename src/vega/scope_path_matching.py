from __future__ import annotations

import re
import unicodedata
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path, PurePosixPath

from .redaction import redact_text


def validate_scope_pattern(value: str, field_name: str) -> str:
    """拒绝会把仓库相对 glob 解释成外部路径或含糊路径的配置值。"""
    if value != value.strip():
        raise ValueError(f"{field_name} 中的路径规则不能包含首尾空白")
    if not value:
        raise ValueError(f"{field_name} 中的路径规则不能为空")
    if len(value) > 512:
        raise ValueError(f"{field_name} 中的路径规则长度不能超过 512")
    _validate_pattern_characters(value, field_name)
    _validate_relative_posix_pattern(value, field_name)
    _validate_pattern_segments(value, field_name)
    if redact_text(value) != value:
        raise ValueError(
            f"{field_name} 中的路径规则会触发脱敏，无法作为稳定的机器判定身份"
        )
    return value


def _validate_pattern_characters(value: str, field_name: str) -> None:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 中的路径规则不能包含换行或 NUL")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(f"{field_name} 中的路径规则不能包含控制字符或双向格式字符")


def _validate_relative_posix_pattern(value: str, field_name: str) -> None:
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{field_name} 只能使用仓库相对路径，不能使用绝对路径或盘符")
    if "\\" in value:
        raise ValueError(f"{field_name} 必须使用 POSIX 分隔符 '/'，不能使用反斜杠")
    if ":" in value:
        raise ValueError(f"{field_name} 不能包含 ':'，避免 Windows 路径歧义")


def _validate_pattern_segments(value: str, field_name: str) -> None:
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(
            f"{field_name} 不能包含空路径段、'.' 或 '..'，请使用明确的仓库相对 glob"
        )
    if segments.count("**") > 16:
        raise ValueError(f"{field_name} 中的 '**' 不能超过 16 个，避免规则匹配失控")


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


def path_pattern_intersects_prefix(
    pattern: str,
    prefix: str,
    *,
    case_sensitive: bool = True,
) -> bool:
    """判断 glob 是否可能匹配给定目录前缀中的路径。"""

    pattern_segments = tuple(pattern.split("/"))
    prefix_segments = tuple(prefix.split("/")) if prefix else ()
    if not case_sensitive:
        pattern_segments = tuple(segment.casefold() for segment in pattern_segments)
        prefix_segments = tuple(segment.casefold() for segment in prefix_segments)

    @lru_cache(maxsize=None)
    def intersects(prefix_index: int, pattern_index: int) -> bool:
        if prefix_index == len(prefix_segments):
            return True
        if pattern_index == len(pattern_segments):
            return False
        current_pattern = pattern_segments[pattern_index]
        if current_pattern == "**":
            return intersects(prefix_index, pattern_index + 1) or intersects(
                prefix_index + 1,
                pattern_index,
            )
        return fnmatchcase(prefix_segments[prefix_index], current_pattern) and intersects(
            prefix_index + 1,
            pattern_index + 1,
        )

    return intersects(0, 0)


def select_scoped_rule_paths(
    rule_paths: list[str],
    path_patterns: list[str],
    *,
    root_rule_path: str,
) -> list[str]:
    """按目标路径模式选择根规则及可能适用的目录规则。"""

    selected = {root_rule_path} if root_rule_path in rule_paths else set()
    for pattern in path_patterns:
        for rule_path in rule_paths:
            parent = PurePosixPath(rule_path).parent
            prefix = "" if parent == PurePosixPath(".") else parent.as_posix()
            if path_pattern_intersects_prefix(pattern, prefix):
                selected.add(rule_path)
    return sorted(
        selected,
        key=lambda item: (len(PurePosixPath(item).parts), item.casefold()),
    )


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
