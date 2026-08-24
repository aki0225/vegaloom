from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .git_read import coerce_git_output_bytes, read_git_config_value, run_git_capture
from .redaction import redact_text
from .verification_command_preflight import inspect_verification_commands


PYTEST_COMMAND_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])pytest(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)
RUNS_IGNORE_PROBES = (
    "runs/vega-preflight-run/state.json",
    "runs/vega-preflight-run/trace.jsonl",
    "runs/vega-preflight-run/iterations/01/worker-output.txt",
)


class ProjectConfigIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["error", "warning"]
    message: str
    evidence: str = ""

    @field_validator("message", "evidence")
    @classmethod
    def redact_issue_text(cls, value: str) -> str:
        return redact_text(value)


def validate_repository_preflight(
    repo: Path,
    *,
    source_path: str | None,
    verification_commands: list[str],
) -> list[ProjectConfigIssue]:
    try:
        return _collect_repository_preflight(
            repo,
            source_path=source_path,
            verification_commands=verification_commands,
        )
    except RuntimeError as exc:
        return [
            ProjectConfigIssue(
                code="repository_preflight_failed",
                severity="error",
                message="Git 安全配置校验失败，无法可信地完成项目预检。",
                evidence=str(exc),
            )
        ]


def _collect_repository_preflight(
    repo: Path,
    *,
    source_path: str | None,
    verification_commands: list[str],
) -> list[ProjectConfigIssue]:
    if not _is_git_work_tree(repo):
        return []

    issues: list[ProjectConfigIssue] = []
    if _are_ignored_paths(repo, RUNS_IGNORE_PROBES) is False:
        issues.append(
            ProjectConfigIssue(
                code="vega_runs_not_ignored",
                severity="warning",
                message=(
                    "当前仓库未忽略 Vega 的 runs/ 运行产物；在仓库目录中启动 loop 会产生 "
                    "Git 状态噪声。请在 .gitignore 或 .git/info/exclude 中忽略 runs/。"
                ),
                evidence="runs/",
            )
        )
    if source_path is not None:
        config_name = Path(source_path).name
        if _is_tracked_path(repo, config_name) is False:
            issues.append(
                ProjectConfigIssue(
                    code="project_config_not_tracked",
                    severity="warning",
                    message=(
                        "项目策略文件未被 Git 跟踪；assist 的可信上下文或 reviewer 前置检查"
                        "可能拒绝该现场。请在启动 loop 前把策略纳入明确的准备提交。"
                    ),
                    evidence=config_name,
                )
            )

    missing_src_import = [
        index
        for index, command in enumerate(verification_commands, start=1)
        if PYTEST_COMMAND_PATTERN.search(command)
        and not _command_declares_src_import(command)
    ]
    if missing_src_import and _looks_like_python_src_layout(repo):
        locations = ", ".join(
            f"verification.commands[{index}]" for index in missing_src_import
        )
        issues.append(
            ProjectConfigIssue(
                code="pytest_src_import_path_unspecified",
                severity="warning",
                message=(
                    "检测到 Python src layout，但 pytest 命令未显式声明本地 src 导入路径；"
                    "若当前 checkout 未安装，验证可能误用环境中的其他版本。请在命令中使用 "
                    "`-o pythonpath=src` 等等价方式。"
                ),
                evidence=locations,
            )
        )

    issues.extend(
        ProjectConfigIssue(
            code=issue.code,
            severity="error",
            message=(
                f"verification.commands[{issue.command_index}] {issue.message}"
                f" 建议改为：`{redact_text(issue.suggestion)}`"
            ),
            evidence=issue.evidence,
        )
        for issue in inspect_verification_commands(repo, verification_commands)
    )

    if _is_windows_environment() and _effective_core_autocrlf(repo) is True:
        issues.append(
            ProjectConfigIssue(
                code="windows_autocrlf_enabled",
                severity="warning",
                message=(
                    "当前 Windows Git 配置启用了 core.autocrlf；正式 Pilot 的确定性 diff/Reflect "
                    "证据可能因 CRLF checkout 产生整文件差异。请在干净副本 checkout 前显式选择"
                    "并冻结行尾策略。"
                ),
                evidence="core.autocrlf=true",
            )
        )
    return issues


def _is_git_work_tree(repo: Path) -> bool:
    try:
        result = run_git_capture(
            repo,
            ["git", "rev-parse", "--is-inside-work-tree"],
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and coerce_git_output_bytes(result.stdout).strip().lower() == b"true"
    )


def _is_tracked_path(repo: Path, path: str) -> bool | None:
    try:
        result = run_git_capture(
            repo,
            ["git", "ls-files", "--error-unmatch", "--", path],
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    return False if result.returncode == 1 else None


def _are_ignored_paths(repo: Path, paths: tuple[str, ...]) -> bool | None:
    try:
        result = run_git_capture(
            repo,
            ["git", "check-ignore", "--no-index", "--", *paths],
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in {0, 1}:
        return None
    ignored = {
        line.decode("utf-8", errors="replace")
        for line in coerce_git_output_bytes(result.stdout).splitlines()
        if line
    }
    return all(path in ignored for path in paths)


def _looks_like_python_src_layout(repo: Path) -> bool:
    return (repo / "src").is_dir() and any(
        (repo / name).is_file()
        for name in ("pyproject.toml", "setup.cfg", "setup.py")
    )


def _command_declares_src_import(command: str) -> bool:
    normalized = command.casefold()
    return "pythonpath" in normalized and bool(
        re.search(r"(?<![A-Za-z0-9_])src(?![A-Za-z0-9_])", normalized)
    )


def _is_windows_environment() -> bool:
    return os.name == "nt"


def _effective_core_autocrlf(repo: Path) -> bool | None:
    value = read_git_config_value(repo, "core.autocrlf")
    if value is None:
        return None
    normalized = value.casefold()
    if normalized in {"true", "yes", "on", "1"}:
        return True
    if normalized in {"false", "no", "off", "0", "input"}:
        return False
    return None
