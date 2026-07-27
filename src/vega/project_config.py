from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .git_read import coerce_git_output_bytes, run_git_capture
from .redaction import redact_text
from .repository_identity import resolve_git_revision


CONFIG_FILENAMES = [".vega.yaml", ".vega.yml"]
CODEX_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
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


def project_policy_snapshot(repo_path: Path) -> dict[str, str | None]:
    """返回项目策略文件的最小可比较快照，不落盘策略原文。"""
    repo = repo_path.resolve()
    for name in CONFIG_FILENAMES:
        path = repo / name
        if path.is_file():
            return {
                "path": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {"path": None, "sha256": None}


def scope_policy_sha256(scope: ScopeConfig) -> str:
    """计算 scope 规则的规范化摘要，供 run 根状态与各阶段证据绑定。"""
    payload = json.dumps(
        scope.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class VerificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[str] | None = None
    max_commands: int = Field(default=2, ge=0, le=10)
    timeout_seconds: int = Field(default=180, ge=1, le=3600)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_paths: list[str] = Field(default_factory=list)
    medium_paths: list[str] = Field(default_factory=list)
    require_human_review: list[str] = Field(default_factory=list)


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_changed_files: int | None = Field(default=None, ge=0)
    max_diff_lines: int | None = Field(default=None, ge=0)
    max_new_files: int | None = Field(default=None, ge=0)
    max_file_bytes: int = Field(default=200_000, ge=1)
    forbid_new_dependencies: bool = False
    forbid_large_generated_files: bool = False


class ScopeConfig(BaseModel):
    """声明本轮 tracked diff 的精确路径范围。

    `allowed_paths` 非空时，所有变更必须命中其中至少一条规则；`forbidden_paths`
    始终优先，用于把测试、CI、依赖或项目策略等路径从小任务中排除。这里仅保存
    机器可判定的相对 POSIX glob，不把自然语言约束误当成 runtime 强制边界。
    """

    model_config = ConfigDict(extra="forbid")

    allowed_paths: list[str] = Field(default_factory=list, max_length=128)
    forbidden_paths: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_scope_patterns(
        cls,
        values: list[str],
        info: ValidationInfo,
    ) -> list[str]:
        return [_validate_scope_pattern(value, info.field_name) for value in values]


def _validate_scope_pattern(value: str, field_name: str) -> str:
    """拒绝会把仓库相对 glob 解释成外部路径或含糊路径的配置值。"""
    if value != value.strip():
        raise ValueError(f"{field_name} 中的路径规则不能包含首尾空白")
    if not value:
        raise ValueError(f"{field_name} 中的路径规则不能为空")
    if len(value) > 512:
        raise ValueError(f"{field_name} 中的路径规则长度不能超过 512")
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 中的路径规则不能包含换行或 NUL")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(f"{field_name} 中的路径规则不能包含控制字符或双向格式字符")
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{field_name} 只能使用仓库相对路径，不能使用绝对路径或盘符")
    if "\\" in value:
        raise ValueError(f"{field_name} 必须使用 POSIX 分隔符 '/'，不能使用反斜杠")
    if ":" in value:
        raise ValueError(f"{field_name} 不能包含 ':'，避免 Windows 路径歧义")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(
            f"{field_name} 不能包含空路径段、'.' 或 '..'，请使用明确的仓库相对 glob"
        )
    if segments.count("**") > 16:
        raise ValueError(f"{field_name} 中的 '**' 不能超过 16 个，避免规则匹配失控")
    if redact_text(value) != value:
        raise ValueError(
            f"{field_name} 中的路径规则会触发脱敏，无法作为稳定的机器判定身份"
        )
    return value


class PromptBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_max_chars: int = Field(default=40_000, ge=1_000, le=1_000_000)
    reviewer_max_chars: int = Field(default=60_000, ge=1_000, le=1_000_000)
    reviewer_diff_max_chars: int = Field(default=30_000, ge=1_000, le=500_000)


class CodexExecOptions(BaseModel):
    """允许项目按角色覆盖的 Codex exec 参数。

    这里只开放模型、推理强度、profile 和临时会话，不接受任意 CLI 参数。这样既能让
    worker/reviewer 使用不同成本策略，也不会把 sandbox bypass 等危险开关暴露给 YAML。
    """

    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    model: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    ephemeral: bool = False

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str | None) -> str | None:
        normalized = _normalize_codex_cli_value(value, "profile")
        if normalized is not None and not CODEX_PROFILE_PATTERN.fullmatch(normalized):
            raise ValueError("profile 只能包含字母、数字、点、下划线和连字符")
        return normalized

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None, info: ValidationInfo) -> str | None:
        return _normalize_codex_cli_value(value, info.field_name)


def _normalize_codex_cli_value(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if len(normalized) > 200:
        raise ValueError(f"{field_name} 长度不能超过 200")
    if normalized.startswith("-"):
        raise ValueError(f"{field_name} 不能以 '-' 开头")
    if any(character in normalized for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 不能包含换行或 NUL")
    return normalized


class CodexExecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: CodexExecOptions = Field(default_factory=CodexExecOptions)
    reviewer: CodexExecOptions = Field(default_factory=CodexExecOptions)


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str | None = None
    reviewer: str | None = None
    codex_exec: CodexExecConfig = Field(default_factory=CodexExecConfig)


class ProjectMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_tags: list[str] = Field(default_factory=list)


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


class ProjectConfigCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_path: str
    source_path: str | None = None
    status: Literal["passed", "failed"] = "passed"
    issues: list[ProjectConfigIssue] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)

    @field_validator("repo_path", "source_path")
    @classmethod
    def redact_path_text(cls, value: str | None) -> str | None:
        return redact_text(value) if value is not None else None

    @field_validator("verification_commands")
    @classmethod
    def redact_verification_commands(cls, value: list[str]) -> list[str]:
        return [redact_text(command) for command in value]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    budget_profiles: dict[str, BudgetConfig] = Field(default_factory=dict)
    prompt_budget: PromptBudgetConfig = Field(default_factory=PromptBudgetConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    memory: ProjectMemoryConfig = Field(default_factory=ProjectMemoryConfig)
    source_path: str | None = None


def load_project_config(
    repo_path: Path,
    *,
    tracked_only: bool = False,
    tracked_revision: str | None = None,
) -> ProjectConfig:
    repo = repo_path.resolve()
    if tracked_only:
        revision = resolve_git_revision(repo, tracked_revision or "HEAD")
        if revision is None:
            return ProjectConfig()
        for name in CONFIG_FILENAMES:
            content = _read_tracked_config(repo, revision, name)
            if content is None:
                continue
            data = yaml.safe_load(content) or {}
            config = ProjectConfig.model_validate(data)
            config.source_path = str(repo / name)
            return config
        return ProjectConfig()

    for name in CONFIG_FILENAMES:
        path = repo / name
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            config = ProjectConfig.model_validate(data)
            config.source_path = str(path)
            return config
    return ProjectConfig()


def _read_tracked_config(repo: Path, revision: str, name: str) -> str | None:
    result = run_git_capture(
        repo,
        ["git", "show", f"{revision}:{name}"],
    )
    if result.returncode != 0:
        return None
    return coerce_git_output_bytes(result.stdout).decode(
        "utf-8",
        errors="replace",
    )


def check_project_config(repo_path: Path) -> ProjectConfigCheckResult:
    """只读预检 `.vega.yaml`，把配置问题变成可展示的结构化结果。

    这个函数不执行验证命令，只检查 runtime 能否安全理解配置；真实执行仍由
    verification runtime 控制，避免 config check 本身变成隐式 CI。
    """
    repo = repo_path.resolve()
    try:
        config = load_project_config(repo)
    except Exception as exc:  # noqa: BLE001 - 这里要把 YAML/Pydantic 错误统一转为用户可读问题
        issue = ProjectConfigIssue(
            code="invalid_project_config",
            severity="error",
            message="`.vega.yaml` 解析或 schema 校验失败，runtime 无法安全使用该配置。",
            evidence=str(exc)[:1000],
        )
        return ProjectConfigCheckResult(
            repo_path=str(repo),
            source_path=_find_config_path(repo),
            status="failed",
            issues=[issue],
        )

    issues = validate_project_config(config)
    issues.extend(_validate_runtime_dependencies(config))
    return ProjectConfigCheckResult(
        repo_path=str(repo),
        source_path=config.source_path,
        status="failed" if any(issue.severity == "error" for issue in issues) else "passed",
        issues=issues,
        verification_commands=config.verification.commands or [],
    )


def validate_project_config(config: ProjectConfig) -> list[ProjectConfigIssue]:
    issues: list[ProjectConfigIssue] = []
    if config.version != 1:
        issues.append(
            ProjectConfigIssue(
                code="unsupported_config_version",
                severity="error",
                message="当前 runtime 只支持 `.vega.yaml` version: 1。",
                evidence=str(config.version),
            )
        )
    if config.source_path is None:
        issues.append(
            ProjectConfigIssue(
                code="missing_project_config",
                severity="warning",
                message="未发现 `.vega.yaml`，将退回到项目画像自动识别策略。",
            )
        )
    issues.extend(validate_verification_commands(config.verification.commands or []))
    issues.extend(_validate_runner_name("runner.worker", config.runner.worker))
    issues.extend(_validate_runner_name("runner.reviewer", config.runner.reviewer))
    for name in config.budget_profiles:
        if not name.strip():
            issues.append(
                ProjectConfigIssue(
                    code="empty_scope_profile_name",
                    severity="error",
                    message="budget_profiles 中存在空 profile 名称。",
                )
            )
    return issues


def validate_verification_commands(commands: list[str]) -> list[ProjectConfigIssue]:
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
            and verification_temp_placeholder_has_unsafe_context(command)
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


def _validate_runtime_dependencies(config: ProjectConfig) -> list[ProjectConfigIssue]:
    runners = {
        (config.runner.worker or "codex-exec").strip().lower(),
        (config.runner.reviewer or "codex-exec").strip().lower(),
    }
    if not runners.intersection({"codex", "codex-exec"}):
        return []
    if shutil.which("codex"):
        return []
    return [
        ProjectConfigIssue(
            code="codex_cli_missing",
            severity="warning",
            message="配置会使用 codex-exec，但当前 PATH 中未找到 Codex CLI。",
            evidence="请先安装并登录 Codex CLI，或显式选择 none/prompt-only runner。",
        )
    ]


def render_project_config_check(result: ProjectConfigCheckResult) -> str:
    lines = [
        "# Vega Config Check",
        "",
        f"- 仓库：`{redact_text(result.repo_path)}`",
        f"- 配置文件：`{redact_text(result.source_path or '未发现')}`",
        f"- 状态：`{result.status}`",
        "",
        "## 问题",
        "",
    ]
    if result.issues:
        for issue in result.issues:
            lines.extend(
                [
                    f"- [{issue.severity.upper()}] `{issue.code}`：{redact_text(issue.message)}",
                    f"  - 证据：{redact_text(issue.evidence or '无')}",
                ]
            )
    else:
        lines.append("- 未发现配置问题。")

    lines.extend(["", "## 显式验证命令", ""])
    if result.verification_commands:
        lines.extend(f"- `{redact_text(command)}`" for command in result.verification_commands)
    else:
        lines.append("- 未配置，运行时将使用 project profile 自动识别。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def budget_for_scope(config: ProjectConfig, scope: str | None = None) -> BudgetConfig:
    if not scope:
        return config.budget
    normalized = scope.strip()
    if not normalized or normalized == "default":
        return config.budget
    if normalized not in config.budget_profiles:
        available = ", ".join(sorted(config.budget_profiles)) or "无"
        raise ValueError(f"未知 scope profile：{scope}；可用 profiles：{available}")
    return config.budget_profiles[normalized]


def _find_config_path(repo_path: Path) -> str | None:
    for name in CONFIG_FILENAMES:
        path = repo_path / name
        if path.is_file():
            return str(path)
    return None


def _validate_runner_name(field_name: str, value: str | None) -> list[ProjectConfigIssue]:
    if value is None:
        return []
    normalized = value.strip().lower()
    if normalized in {"codex-exec", "codex", "none", "prompt-only"}:
        return []
    return [
        ProjectConfigIssue(
            code="unknown_runner",
            severity="error",
            message=f"{field_name} 使用了当前 runtime 不支持的 runner。",
            evidence=value,
        )
    ]


def render_project_config_summary(config: ProjectConfig) -> str:
    lines = ["# Vega 项目策略", ""]
    if config.source_path:
        lines.append(f"- 配置文件：`{config.source_path}`")
    else:
        lines.append("- 配置文件：未发现 `.vega.yaml`，使用自动识别策略。")
    lines.extend(
        [
            f"- 验证命令上限：`{config.verification.max_commands}`",
            f"- 验证超时：`{config.verification.timeout_seconds}s`",
            f"- 默认 worker：`{config.runner.worker or 'codex-exec'}`",
            f"- 默认 reviewer：`{config.runner.reviewer or 'codex-exec'}`",
            "",
            "## Codex Exec 角色策略",
            "",
            *_render_codex_exec_options("worker", config.runner.codex_exec.worker),
            *_render_codex_exec_options("reviewer", config.runner.codex_exec.reviewer),
            "",
            "## Prompt 预算",
            "",
            f"- worker 最大字符数：`{config.prompt_budget.worker_max_chars}`",
            f"- reviewer 最大字符数：`{config.prompt_budget.reviewer_max_chars}`",
            f"- reviewer diff 最大字符数：`{config.prompt_budget.reviewer_diff_max_chars}`",
            "",
            "## 变更预算",
            "",
            f"- 最大变更文件数：`{config.budget.max_changed_files if config.budget.max_changed_files is not None else '未限制'}`",
            f"- 最大 diff 行数：`{config.budget.max_diff_lines if config.budget.max_diff_lines is not None else '未限制'}`",
            f"- 最大新增文件数：`{config.budget.max_new_files if config.budget.max_new_files is not None else '未限制'}`",
            f"- 禁止新增依赖：`{config.budget.forbid_new_dependencies}`",
            f"- 禁止大体量生成/新增文件：`{config.budget.forbid_large_generated_files}`",
            "",
            "## 精确路径范围",
            "",
            "- allowed_paths："
            + (
                "、".join(f"`{item}`" for item in config.scope.allowed_paths)
                if config.scope.allowed_paths
                else "未配置"
            ),
            "- forbidden_paths："
            + (
                "、".join(f"`{item}`" for item in config.scope.forbidden_paths)
                if config.scope.forbidden_paths
                else "未配置"
            ),
            "",
            "## 显式验证命令",
            "",
        ]
    )
    if config.verification.commands:
        lines.extend(f"- `{command}`" for command in config.verification.commands)
    else:
        lines.append("- 未配置，使用 project profile 自动识别。")
    lines.extend(["", "## Scope Profiles", ""])
    if config.budget_profiles:
        for name, budget in config.budget_profiles.items():
            lines.append(
                f"- `{name}`：files={budget.max_changed_files or '未限制'}，"
                f"diff={budget.max_diff_lines or '未限制'}，new={budget.max_new_files or '未限制'}"
            )
    else:
        lines.append("- 未配置 scope profile。")
    lines.extend(["", "## 风险策略", ""])
    if config.risk.high_paths:
        lines.append("- 高风险路径：" + "、".join(f"`{item}`" for item in config.risk.high_paths))
    if config.risk.medium_paths:
        lines.append("- 中风险路径：" + "、".join(f"`{item}`" for item in config.risk.medium_paths))
    if config.risk.require_human_review:
        lines.append("- 必须人工确认：" + "、".join(f"`{item}`" for item in config.risk.require_human_review))
    if not (config.risk.high_paths or config.risk.medium_paths or config.risk.require_human_review):
        lines.append("- 未配置项目级风险策略。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def _render_codex_exec_options(role: str, options: CodexExecOptions) -> list[str]:
    return [
        f"- `{role}.profile`：`{options.profile or '继承用户配置'}`",
        f"- `{role}.model`：`{options.model or '继承用户配置'}`",
        f"- `{role}.reasoning_effort`：`{options.reasoning_effort or '继承用户配置'}`",
        f"- `{role}.ephemeral`：`{options.ephemeral}`",
    ]
