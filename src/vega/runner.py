from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import ValidationError

from .execution_control import RunnerExecutionContext, run_owned_process
from .project_config import (
    CodexExecOptions,
    CodexProviderDescriptor,
    codex_provider_descriptor_sha256,
)
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
        try:
            options = _validate_codex_exec_config_source(self.options)
            command = self.build_command(repo_path, sandbox)
        except FileNotFoundError:
            return RunnerResult(
                status="error",
                output="",
                error=f"未找到 {self.executable}，无法启动 codex exec。",
                command=[self.executable, "exec"],
            )

        prompt = redact_text(prompt)
        context = execution_context or RunnerExecutionContext(
            execution_dir=Path.cwd()
            / "runs"
            / "_standalone-executions"
            / f"codex-{uuid4().hex[:12]}",
            run_id="standalone-runner",
            step="codex-exec",
        )
        if context.runner_identity is None:
            context = replace(
                context,
                runner_identity=self.execution_identity(sandbox),
            )
        result = run_owned_process(
            command,
            prompt,
            repo_path,
            timeout_seconds,
            context,
        )
        result_status = result.status
        result_error = result.error
        if (
            options.windows_sandbox_session_override is not None
            and result.status == "success"
        ):
            observed_sandbox = _parse_codex_live_header_sandbox(result.output)
            if (
                observed_sandbox is None
                or not observed_sandbox.startswith(sandbox)
            ):
                result_status = "error"
                result_error = (
                    "Codex live header sandbox 与请求不一致："
                    f"expected={sandbox!r}, observed={observed_sandbox!r}"
                )
        return RunnerResult(
            status=result_status,
            output=result.output,
            error=result_error,
            command=command,
        )

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        """在启动前构造稳定命令，供 Gate 3 attempt identity 绑定。"""

        options = _validate_codex_exec_config_source(self.options)
        resolved = shutil.which(self.executable)
        if not resolved:
            raise FileNotFoundError(self.executable)
        return build_codex_exec_command(
            resolved,
            options,
            repo_path,
            sandbox,
        )

    def execution_identity(self, sandbox: str) -> dict[str, str]:
        """返回可写入 execution/attempt 证据的统一 Runner 身份。"""

        return build_codex_exec_identity(self.options, sandbox)


def build_codex_exec_command(
    executable: str,
    options: CodexExecOptions,
    repo_path: Path,
    sandbox: str,
) -> list[str]:
    """使用已解析的可执行文件构造 allowlist argv，避免实验 harness 复制命令逻辑。"""

    options = _validate_codex_exec_config_source(options)
    command = [
        executable,
        "exec",
        "--cd",
        str(repo_path.resolve()),
        "--sandbox",
        sandbox,
    ]
    if options.profile:
        command.extend(["--profile", options.profile])
    if options.ignore_user_config:
        command.append("--ignore-user-config")
    if options.windows_sandbox_session_override:
        command.extend(
            [
                "--config",
                (
                    "windows.sandbox="
                    f'"{options.windows_sandbox_session_override}"'
                ),
            ]
        )
    if options.disable_multi_agent:
        command.extend(["--disable", "multi_agent"])
    if options.provider is not None:
        command.extend(_provider_descriptor_argv(options.provider))
    if options.model:
        command.extend(["--model", options.model])
    if options.reasoning_effort:
        command.extend(
            [
                "--config",
                f'model_reasoning_effort="{options.reasoning_effort}"',
            ]
        )
    if options.ephemeral:
        command.append("--ephemeral")
    command.append("-")
    return command


def build_codex_exec_identity(
    options: CodexExecOptions,
    sandbox: str,
) -> dict[str, str]:
    """构造不含凭证、但足以区分 Codex 配置来源的稳定身份。"""

    options = _validate_codex_exec_config_source(options)
    config_mode = (
        "isolated_provider"
        if options.provider is not None
        else "ignore_user_config"
        if options.ignore_user_config
        else "profile"
        if options.profile is not None
        else "default"
    )
    identity = {
        "kind": "CodexExecRunner",
        "runner": "codex-exec",
        "config_mode": config_mode,
        "ignore_user_config": str(options.ignore_user_config).lower(),
        "ephemeral": str(options.ephemeral).lower(),
        "sandbox": sandbox,
    }
    if options.profile is not None:
        identity["profile"] = options.profile
    if options.windows_sandbox_session_override is not None:
        identity["windows_sandbox_session_override"] = (
            options.windows_sandbox_session_override
        )
    if options.disable_multi_agent:
        identity["multi_agent"] = "disabled"
    if options.provider is not None:
        identity.update(
            {
                "provider": options.provider.name,
                "provider_base_url": options.provider.base_url,
                "provider_wire_api": options.provider.wire_api,
                "provider_requires_openai_auth": str(
                    options.provider.requires_openai_auth
                ).lower(),
                "provider_supports_websockets": str(
                    options.provider.supports_websockets
                ).lower(),
                "provider_descriptor_sha256": (
                    codex_provider_descriptor_sha256(options.provider)
                ),
            }
        )
    if options.model is not None:
        identity["model"] = options.model
    if options.reasoning_effort is not None:
        identity["reasoning_effort"] = options.reasoning_effort
    return {
        redact_text(key): redact_text(value)
        for key, value in identity.items()
    }


def _validate_codex_exec_config_source(
    options: CodexExecOptions,
) -> CodexExecOptions:
    if type(options.ignore_user_config) is not bool:
        raise ValueError("ignore_user_config 必须是布尔值")
    if type(options.ephemeral) is not bool:
        raise ValueError("ephemeral 必须是布尔值")
    if type(options.disable_multi_agent) is not bool:
        raise ValueError("disable_multi_agent 必须是布尔值")
    if options.profile is not None and options.ignore_user_config:
        raise ValueError(
            "profile 与 ignore_user_config=True 不能同时配置"
        )
    if (
        options.windows_sandbox_session_override is not None
        and options.windows_sandbox_session_override != "elevated"
    ):
        raise ValueError(
            "windows_sandbox_session_override 当前只允许 elevated"
        )
    if (
        options.windows_sandbox_session_override is not None
        and not options.ignore_user_config
    ):
        raise ValueError(
            "windows_sandbox_session_override 仅可与 "
            "ignore_user_config=True 配合使用"
        )
    payload = {
        field_name: getattr(options, field_name)
        for field_name in CodexExecOptions.model_fields
    }
    if options.provider is not None:
        if not isinstance(options.provider, CodexProviderDescriptor):
            raise ValueError("provider 必须是 CodexProviderDescriptor")
        try:
            validated_provider = CodexProviderDescriptor.model_validate(
                options.provider.model_dump(mode="python"),
                strict=True,
            )
        except ValidationError as exc:
            raise ValueError(
                "CodexProviderDescriptor 未通过严格重验证"
            ) from exc
        if validated_provider != options.provider:
            raise ValueError(
                "CodexProviderDescriptor 包含未规范化的变异值"
            )
        payload["provider"] = validated_provider
    try:
        validated = CodexExecOptions.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise ValueError("CodexExecOptions 未通过严格重验证") from exc
    if any(
        getattr(validated, field_name) != payload[field_name]
        for field_name in CodexExecOptions.model_fields
    ):
        raise ValueError("CodexExecOptions 包含未规范化的变异值")
    return validated


def _provider_descriptor_argv(
    descriptor: CodexProviderDescriptor,
) -> list[str]:
    """把已校验 provider descriptor 转成固定顺序的 Codex TOML override。"""

    prefix = f"model_providers.{descriptor.name}"
    arguments = [
        "--config",
        f"model_provider={_toml_string(descriptor.name)}",
        "--config",
        f"{prefix}.name={_toml_string(descriptor.name)}",
        "--config",
        f"{prefix}.base_url={_toml_string(descriptor.base_url)}",
        "--config",
        f"{prefix}.wire_api={_toml_string(descriptor.wire_api)}",
        "--config",
        (
            f"{prefix}.requires_openai_auth="
            f"{str(descriptor.requires_openai_auth).lower()}"
        ),
        "--config",
        (
            f"{prefix}.supports_websockets="
            f"{str(descriptor.supports_websockets).lower()}"
        ),
    ]
    if descriptor.request_max_retries is not None:
        arguments.extend(
            [
                "--config",
                f"{prefix}.request_max_retries={descriptor.request_max_retries}",
            ]
        )
    if descriptor.stream_max_retries is not None:
        arguments.extend(
            [
                "--config",
                f"{prefix}.stream_max_retries={descriptor.stream_max_retries}",
            ]
        )
    return arguments


def _toml_string(value: str) -> str:
    """当前 allowlist 字符串可安全用 JSON basic string 兼容 TOML override。"""

    return json.dumps(value, ensure_ascii=False)


def _parse_codex_live_header_sandbox(output: str) -> str | None:
    """只从 Codex live header 读取实际 sandbox，不解析模型正文。"""

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.lower() == "user":
            break
        if ":" not in line:
            continue
        key, value = (item.strip() for item in line.split(":", 1))
        if key.lower() == "sandbox" and value:
            return value
    return None


def make_runner(name: str, options: CodexExecOptions | None = None) -> Runner:
    normalized = name.strip().lower()
    if normalized in {"none", "prompt-only"}:
        return NoneRunner()
    if normalized in {"codex-exec", "codex"}:
        return CodexExecRunner(options=options)
    raise ValueError(f"不支持的 runner：{name}")
