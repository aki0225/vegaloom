from __future__ import annotations

from .project_config_preflight import ProjectConfigIssue
from .verification_shell import (
    VERIFICATION_TEMP_PLACEHOLDER, find_unknown_verification_placeholders,
    unsafe_windows_verification_syntax, verification_temp_placeholder_has_unsafe_context,
)


def inspect_verification_commands(commands: list[str], shell_kind: str) -> list[ProjectConfigIssue]:
    issues: list[ProjectConfigIssue] = []
    for index, command in enumerate(commands, start=1):
        stripped = command.strip()
        location = f"verification.commands[{index}]"
        if not stripped:
            issues.append(
                ProjectConfigIssue(
                    code="empty_verification_command",
                    severity="error",
                    message=f"{location} 为空，无法作为自动验证命令执行。",
                )
            )
            continue
        if "\n" in command or "\r" in command:
            issues.append(
                ProjectConfigIssue(
                    code="multiline_verification_command",
                    severity="error",
                    message=f"{location} 包含换行；请把复杂验证封装成脚本，再在这里调用脚本。",
                    evidence=stripped[:300],
                )
            )
        if stripped.endswith("\\") or stripped.endswith("`"):
            issues.append(
                ProjectConfigIssue(
                    code="truncated_verification_command",
                    severity="error",
                    message=f"{location} 看起来以 shell 续行符结尾，疑似命令被截断。",
                    evidence=stripped[:300],
                )
            )
        unknown_placeholders = find_unknown_verification_placeholders(command)
        if unknown_placeholders:
            issues.append(
                ProjectConfigIssue(
                    code="unknown_verification_placeholder",
                    severity="error",
                    message=(
                        f"{location} 包含不受支持的 Vega 占位符；"
                        f"只允许精确字面量 {VERIFICATION_TEMP_PLACEHOLDER}。"
                    ),
                    evidence=", ".join(unknown_placeholders),
                )
            )
        if (
            VERIFICATION_TEMP_PLACEHOLDER in command
            and verification_temp_placeholder_has_unsafe_context(command, shell_kind)
        ):
            issues.append(
                ProjectConfigIssue(
                    code="unsafe_verification_temp_placeholder_context",
                    severity="error",
                    message=(
                        f"{location} 中的 {VERIFICATION_TEMP_PLACEHOLDER} 必须作为未加引号的"
                        "独立路径 token；Runtime 会负责安全引用。"
                    ),
                    evidence=VERIFICATION_TEMP_PLACEHOLDER,
                )
            )
        if shell_kind == "cmd":
            unsafe_windows_syntax = unsafe_windows_verification_syntax(command)
            if unsafe_windows_syntax:
                issues.append(
                    ProjectConfigIssue(
                        code="unsafe_windows_verification_syntax",
                        severity="error",
                        message=(
                            f"{location} 包含 cmd.exe 不安全或不兼容的 shell 语法；"
                            "请改用双引号或仓库内脚本，且不要使用单管道。"
                        ),
                        evidence=", ".join(unsafe_windows_syntax),
                    )
                )
        if len(stripped) > 500:
            issues.append(
                ProjectConfigIssue(
                    code="long_verification_command",
                    severity="warning",
                    message=f"{location} 过长，建议封装为仓库内脚本，减少 YAML 转义错误。",
                    evidence=f"{len(stripped)} chars",
                )
            )
    return issues
