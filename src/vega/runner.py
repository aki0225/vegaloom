from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from .execution_control import RunnerExecutionContext, run_owned_process
from .project_config import CodexExecOptions
from .redaction import redact_text


RunnerStatus = Literal["success", "error", "timed_out", "stopped", "skipped"]


@dataclass
class RunnerResult:
    status: RunnerStatus
    output: str
    error: str | None = None
    command: list[str] | None = None

    def __post_init__(self) -> None:
        self.output = redact_text(self.output)
        self.error = redact_text(self.error) if self.error is not None else None
        if self.command is not None:
            self.command = [redact_text(item) for item in self.command]


class Runner(Protocol):
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        ...


class NoneRunner:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        return RunnerResult(
            status="skipped",
            output="",
            error="runner=none，仅生成 prompt，不调用外部 AI。",
            command=[],
        )


class CodexExecRunner:
    """通过 codex exec 启动短生命周期隔离会话。

    reviewer 默认使用 read-only sandbox；worker 只在 auto 模式中使用 workspace-write。
    自动化默认不继承用户级 Codex 配置，避免个人 MCP、Hook 或 Memory 扩大 Worker
    进程树与上下文；这里只开放经过验证的角色参数，也不传 bypass sandbox /
    bypass approval。
    """

    def __init__(
        self,
        executable: str = "codex",
        options: CodexExecOptions | None = None,
    ) -> None:
        self.executable = executable
        self.options = options or CodexExecOptions()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        resolved = _resolve_codex_executable(self.executable)
        if not resolved:
            return RunnerResult(
                status="error",
                output="",
                error=f"未找到 {self.executable}，无法启动 codex exec。",
                command=[self.executable, "exec"],
            )

        command = [
            resolved,
            "exec",
        ]
        if not self.options.inherit_user_config:
            command.append("--ignore-user-config")
        command.extend(
            [
                "--cd",
                str(repo_path.resolve()),
                "--sandbox",
                sandbox,
            ]
        )
        if self.options.profile:
            command.extend(["--profile", self.options.profile])
        if self.options.model:
            command.extend(["--model", self.options.model])
        if self.options.reasoning_effort:
            command.extend(
                [
                    "--config",
                    f'model_reasoning_effort="{self.options.reasoning_effort}"',
                ]
            )
        if self.options.ephemeral:
            command.append("--ephemeral")
        command.append("-")
        prompt = redact_text(prompt)
        context = execution_context or RunnerExecutionContext(
            execution_dir=Path.cwd()
            / "runs"
            / "_standalone-executions"
            / f"codex-{uuid4().hex[:12]}",
            run_id="standalone-runner",
            step="codex-exec",
        )
        result = run_owned_process(
            command,
            prompt,
            repo_path,
            timeout_seconds,
            context,
        )
        return RunnerResult(
            status=result.status,
            output=result.output,
            error=result.error,
            command=command,
        )


def make_runner(name: str, options: CodexExecOptions | None = None) -> Runner:
    normalized = name.strip().lower()
    if normalized in {"none", "prompt-only"}:
        return NoneRunner()
    if normalized in {"codex-exec", "codex"}:
        return CodexExecRunner(options=options)
    raise ValueError(f"不支持的 runner：{name}")


def _resolve_codex_executable(executable: str) -> str | None:
    """解析 Codex CLI；Windows npm 安装优先绕过 cmd 包装层。

    `codex.cmd` 仍作为兼容兜底，但标准 npm 安装会附带原生 `codex.exe`。直接运行
    原生二进制可以减少一层 `cmd.exe -> codex.cmd` 进程包装，让 owned process tree
    更容易被 Vega 精确终止和确认。
    """

    resolved = shutil.which(executable)
    if resolved is None or not _is_windows_platform():
        return resolved
    resolved_path = Path(resolved)
    if resolved_path.suffix.casefold() == ".exe":
        return str(resolved_path)
    native = _find_windows_native_codex(resolved_path)
    return str(native) if native is not None else resolved


def _find_windows_native_codex(wrapper: Path) -> Path | None:
    package_scope = (
        wrapper.parent
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
    )
    if not package_scope.is_dir():
        return None
    machine = platform.machine().casefold()
    preferred_suffix = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    package_dirs = sorted(
        package_scope.glob("codex-win32-*"),
        key=lambda path: (
            not path.name.casefold().endswith(preferred_suffix),
            path.name.casefold(),
        ),
    )
    for package_dir in package_dirs:
        for candidate in sorted(package_dir.glob("vendor/*/bin/codex.exe")):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _is_windows_platform() -> bool:
    return os.name == "nt"
