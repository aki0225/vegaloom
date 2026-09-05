from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from .project_config import (
    ProjectConfig,
    ProjectConfigIssue,
    load_project_config,
    validate_project_config,
)


ProviderName = Literal["codex", "claude"]


def select_provider(
    config: ProjectConfig,
    requested: ProviderName | None,
) -> ProviderName | None:
    """根据显式选择或项目 runner 选择预检 Provider。"""

    if requested is not None:
        return requested
    providers = _runner_providers(config)
    return next(iter(providers)) if len(providers) == 1 else None


def _runner_providers(config: ProjectConfig) -> set[ProviderName]:
    runners = {
        (config.runner.worker or "codex-exec").strip().lower(),
        (config.runner.reviewer or "codex-exec").strip().lower(),
    }
    names: dict[str, ProviderName] = {
        "claude": "claude", "claude-code": "claude",
        "codex": "codex", "codex-exec": "codex", "codex-app-server": "codex",
    }
    return {names[runner] for runner in runners if runner in names}


def provider_cli_available(provider: ProviderName) -> bool:
    executable = "claude" if provider == "claude" else "codex"
    return bool(shutil.which(executable)) or (
        provider == "claude" and bool(shutil.which("claude.cmd"))
    )


def validate_runtime_dependencies(
    config: ProjectConfig,
    *,
    provider: ProviderName | None = None,
    cli_available: bool | None = None,
) -> list[ProjectConfigIssue]:
    # 旧 Loop 允许混合 runner；Change 调用方传入单一有效 Provider。
    providers = {provider} if provider is not None else _runner_providers(config)
    issues: list[ProjectConfigIssue] = []
    for selected in sorted(providers):
        available = cli_available if len(providers) == 1 else None
        if available is None:
            available = provider_cli_available(selected)
        if available:
            continue
        label = "Claude Code" if selected == "claude" else "Codex"
        issues.append(
            ProjectConfigIssue(
                code=f"{selected}_cli_missing",
                severity="warning",
                message=f"本次检查需要 {label}，但当前 PATH 中未找到 {label} CLI。",
                evidence=f"请先安装 {label} CLI；config check 不会验证登录状态。",
            )
        )
    return issues


def validate_change_startup_config(
    config: ProjectConfig,
) -> list[ProjectConfigIssue]:
    """检查自然语言 Change 的固定配置，不猜测验证命令。"""

    if config.source_path is None:
        return [
            ProjectConfigIssue(
                code="change_project_config_missing",
                severity="error",
                message="自然语言 Change 需要已跟踪的 `.vega.yaml`，其中登记固定验证命令。",
                evidence="请先登记 verification.commands；不会从项目画像猜测验证命令。",
            )
        ]
    if not config.verification.commands:
        return [
            ProjectConfigIssue(
                code="change_verification_commands_missing",
                severity="error",
                message="自然语言 Change 需要在 `.vega.yaml` 中登记至少一条验证命令。",
                evidence="verification.commands 为空；不会由模型或预检自动补全。",
            )
        ]
    return []


def ensure_change_startup_config(repo: Path) -> None:
    """新任务预检只读 HEAD；后续 Compiler 仍校验 Run 冻结的源版本。"""
    config = load_project_config(repo, tracked_only=True, tracked_revision="HEAD")
    issues = validate_project_config(config) + validate_change_startup_config(config)
    errors = [issue.message for issue in issues if issue.severity == "error"]
    if errors:
        raise ValueError("\n".join(errors))
