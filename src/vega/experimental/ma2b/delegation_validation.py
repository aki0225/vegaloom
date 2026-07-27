from __future__ import annotations

import re
import unicodedata
from typing import Any

from ...redaction import redact_text, sensitive_path_reason


_PATH_GLOB_CHARACTERS = frozenset("*?[]{}")
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(
        r"(?<![A-Za-z0-9._\\-])(?:\\\\){1,2}"
        r"[A-Za-z0-9._$-]+\\+[A-Za-z0-9._$-]+"
    ),
    re.compile(
        r"(?<![A-Za-z0-9:/])/"
        r"(?![A-Za-z?](?:[\s\"'`<>]|$))"
    ),
)


def contains_cycle(dependencies: dict[str, list[str]]) -> bool:
    """使用深度优先遍历识别任务依赖环。"""

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(slice_id: str) -> bool:
        if slice_id in visiting:
            return True
        if slice_id in visited:
            return False
        visiting.add(slice_id)
        for dependency in dependencies[slice_id]:
            if visit(dependency):
                return True
        visiting.remove(slice_id)
        visited.add(slice_id)
        return False

    return any(visit(slice_id) for slice_id in dependencies)


def validate_repo_relative_path(value: str, field_name: str) -> str:
    if value != value.strip():
        raise ValueError(f"{field_name} 不能包含首尾空白")
    if not value or len(value) > 512:
        raise ValueError(f"{field_name} 必须是长度不超过 512 的非空路径")
    reject_unsafe_text(value, field_name)
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{field_name} 只能使用仓库相对路径")
    if "\\" in value or ":" in value:
        raise ValueError(f"{field_name} 必须使用无盘符的 POSIX 相对路径")
    if any(character in value for character in _PATH_GLOB_CHARACTERS):
        raise ValueError(f"{field_name} 必须是精确路径，不能使用 glob")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"{field_name} 不能包含空路径段、'.' 或 '..'")
    if segments[0] == ".git":
        raise ValueError(f"{field_name} 不能指向 Git 控制目录")
    sensitive_reason = sensitive_path_reason(value)
    if sensitive_reason:
        raise ValueError(f"{field_name} 不能指向敏感路径（{sensitive_reason}）")
    if redact_text(value) != value:
        raise ValueError(f"{field_name} 会触发脱敏，不能作为稳定 artifact 身份")
    return value


def validate_command(value: str) -> str:
    if value != value.strip():
        raise ValueError("verification command 不能包含首尾空白")
    reject_unsafe_text(value, "verification command")
    if any(pattern.search(value) for pattern in _LOCAL_PATH_PATTERNS):
        raise ValueError("verification command 不能包含本机绝对路径")
    if redact_text(value) != value:
        raise ValueError("verification command 会触发脱敏，不能进入公开合同")
    return value


def reject_unsafe_text(value: str, field_name: str) -> None:
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 不能包含换行或 NUL")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(f"{field_name} 不能包含控制字符或双向格式字符")


def require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)
