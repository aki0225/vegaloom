from __future__ import annotations

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
    termination_unconfirmed: bool = False

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
    这里不传 bypass sandbox / bypass approval，避免把自动 loop 变成无边界执行器。
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
        resolved = shutil.which(self.executable)
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
            "--cd",
            str(repo_path.resolve()),
            "--sandbox",
            sandbox,
            "--config",
            "notify=[]",
            "--disable",
            "hooks",
            "--disable",
            "memories",
            "--disable",
            "plugins",
        ]
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
        standalone_root = Path.cwd()
        context = execution_context or RunnerExecutionContext(
            execution_root=standalone_root,
            execution_dir=standalone_root
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
            termination_unconfirmed=getattr(result, "termination_unconfirmed", False),
        )


def make_runner(name: str, options: CodexExecOptions | None = None) -> Runner:
    normalized = name.strip().lower()
    if normalized in {"none", "prompt-only"}:
        return NoneRunner()
    if normalized in {"codex-exec", "codex"}:
        return CodexExecRunner(options=options)
    raise ValueError(f"不支持的 runner：{name}")
