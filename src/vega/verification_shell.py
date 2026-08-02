from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Literal


VERIFICATION_TEMP_PLACEHOLDER = "{{vega_verification_temp}}"
VERIFICATION_TEMP_ENV = "VEGA_VERIFICATION_TEMP"
VERIFICATION_TEMP_ROOT = Path(".tmp") / "vega-verification"
VEGA_VERIFICATION_PLACEHOLDER_PATTERN = re.compile(r"\{\{vega_[^{}\r\n]*\}\}")
VerificationShellKind = Literal["cmd", "posix-sh"]


def find_unknown_verification_placeholders(command: str) -> list[str]:
    return sorted(
        {
            placeholder
            for placeholder in VEGA_VERIFICATION_PLACEHOLDER_PATTERN.findall(command)
            if placeholder != VERIFICATION_TEMP_PLACEHOLDER
        }
    )


def verification_temp_placeholder_has_unsafe_context(
    command: str,
    shell_kind: VerificationShellKind | None = None,
) -> bool:
    """占位符必须作为未加引号的独立路径 token，避免路径进入 shell 源码。"""

    selected_shell = shell_kind or current_verification_shell_kind()
    search_from = 0
    while True:
        index = command.find(VERIFICATION_TEMP_PLACEHOLDER, search_from)
        if index < 0:
            return False
        end = index + len(VERIFICATION_TEMP_PLACEHOLDER)
        if _posix_quote_before(command, index) is not None:
            return True
        if _cmd_quote_before(command, index):
            return True
        if selected_shell == "cmd" and _cmd_dynamic_expansion_before(command, index):
            return True
        if index > 0 and not (
            command[index - 1].isspace() or command[index - 1] == "="
        ):
            return True
        if end < len(command) and not (
            command[end].isspace() or command[end] in {"/", "\\"}
        ):
            return True
        search_from = end


def current_verification_shell_kind() -> VerificationShellKind:
    return "cmd" if os.name == "nt" else "posix-sh"


def unsafe_windows_verification_syntax(command: str) -> list[str]:
    issues: list[str] = []
    outside_single_quotes = 0
    quoted = False
    position = 0
    while position < len(command):
        character = command[position]
        if not quoted and character == "^":
            position += 2
            continue
        if character == '"':
            quoted = not quoted
            position += 1
            continue
        if quoted:
            position += 1
            continue
        if character == "'":
            outside_single_quotes += 1
            position += 1
            continue
        if character == "|":
            end = position + 1
            while end < len(command) and command[end] == "|":
                end += 1
            if end - position != 2 and "single_pipe" not in issues:
                issues.append("single_pipe")
            position = end
            continue
        position += 1
    if outside_single_quotes >= 2:
        issues.append("posix_single_quote_group")
    return issues


def render_verification_command(
    command: str,
    shell_kind: VerificationShellKind | None = None,
) -> str:
    unknown_placeholders = find_unknown_verification_placeholders(command)
    if unknown_placeholders:
        raise ValueError(
            "verification 命令包含不受支持的 Vega 占位符："
            + ", ".join(unknown_placeholders)
        )
    if VERIFICATION_TEMP_PLACEHOLDER not in command:
        return command
    selected_shell = shell_kind or current_verification_shell_kind()
    if verification_temp_placeholder_has_unsafe_context(command, selected_shell):
        raise ValueError("verification 临时目录占位符必须作为未加引号的独立路径 token")
    variable_reference = (
        f"%{VERIFICATION_TEMP_ENV}%"
        if selected_shell == "cmd"
        else f"${{{VERIFICATION_TEMP_ENV}}}"
    )
    return command.replace(
        VERIFICATION_TEMP_PLACEHOLDER,
        f'"{variable_reference}"',
    )


def build_verification_shell_command(
    command: str,
    shell_kind: VerificationShellKind | None = None,
) -> list[str] | str:
    selected_shell = shell_kind or current_verification_shell_kind()
    if selected_shell == "cmd":
        prefix = subprocess.list2cmdline(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/v:off",
                "/s",
                "/c",
            ]
        )
        return f'{prefix} "{command}"'
    return ["/bin/sh", "-c", command]


def _posix_quote_before(command: str, index: int) -> str | None:
    quote: str | None = None
    position = 0
    while position < index:
        character = command[position]
        if quote == "'":
            if character == "'":
                quote = None
            position += 1
            continue
        if character == "\\":
            position += 2
            continue
        if quote == '"':
            if character == '"':
                quote = None
            position += 1
            continue
        if character in {"'", '"'}:
            quote = character
        position += 1
    return quote


def _cmd_quote_before(command: str, index: int) -> bool:
    """保守按 cmd.exe 语义跟踪双引号；反斜杠不能转义双引号。"""

    quoted = False
    position = 0
    while position < index:
        character = command[position]
        if not quoted and character == "^":
            position += 2
            continue
        if character == '"':
            quoted = not quoted
        position += 1
    return quoted


def _cmd_dynamic_expansion_before(command: str, index: int) -> bool:
    """拒绝占位符前可在运行时改变引号状态的 cmd 百分号展开。"""

    quoted = False
    position = 0
    while position < index:
        character = command[position]
        if not quoted and character == "^":
            position += 2
            continue
        if character == "%":
            return True
        if character == '"':
            quoted = not quoted
        position += 1
    return False
