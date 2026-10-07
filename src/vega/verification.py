from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .comparison_binding import (
    capture_workspace_fingerprint as _capture_workspace_fingerprint,
)
from .execution_control import RunnerExecutionContext, run_owned_process
from .project_config import (
    VERIFICATION_TEMP_ENV,
    VERIFICATION_TEMP_PLACEHOLDER,
    build_verification_shell_command,
    check_project_config,
    current_verification_shell_kind,
    load_project_config,
    render_verification_command,
    render_project_config_check,
)
from .project_profile import build_project_profile
from .redaction import redact_text, redact_value
from .runtime_workspace import capture_runtime_workspace
from .workspace_inventory import create_verification_temp_dir
from .verification_summary import bounded_output, render_verification_summary

MAX_OUTPUT_CHARS = 8000
VerificationInterruptionStatus = Literal[
    "timed_out",
    "stopped",
    "termination-unconfirmed",
]
VerificationFailureKind = Literal[
    "project_config_invalid",
    "workspace_capture_failed",
]


@dataclass
class VerificationRunResult:
    summary_path: Path
    result_path: Path
    command_count: int
    failed_count: int
    failure_kind: VerificationFailureKind | None = None
    interruption_status: VerificationInterruptionStatus | None = None
    interruption_command: str | None = None
    interruption_reason: str | None = None

    @property
    def has_failures(self) -> bool:
        return self.failed_count > 0 or self.failure_kind is not None

    @property
    def was_interrupted(self) -> bool:
        return self.interruption_status is not None


def run_project_verification(
    workspace: Path,
    repo_path: Path,
    output_dir: Path,
    *,
    iteration: int = 1,
    max_commands: int | None = None,
    timeout_seconds: int | None = None,
    progress_reporter: Callable[[str, int], None] | None = None,
    verification_commands: list[str] | None = None,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> VerificationRunResult:
    """按项目画像执行最小验证命令，并把结果写成可交给 reflect/reviewer 的日志。

    普通 loop 的命令来自 project profile 或项目配置；Supervisor 可以传入人工批准
    Work Item 中冻结的显式命令。普通 loop 默认最多执行两个命令。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    config_check = check_project_config(repo_path)
    if config_check.has_errors:
        return _write_verification_config_failure(
            output_dir,
            config_check,
            workspace=workspace,
            repo_path=repo_path,
            iteration=iteration,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )

    profile = build_project_profile(workspace, repo_path)
    config = load_project_config(repo_path)
    command_limit = (
        max_commands
        if max_commands is not None
        else len(verification_commands)
        if verification_commands is not None
        else config.verification.max_commands
    )
    command_timeout = timeout_seconds if timeout_seconds is not None else config.verification.timeout_seconds
    commands = select_verification_commands(
        profile.test_commands,
        profile.lint_commands,
        configured_commands=(
            verification_commands
            if verification_commands is not None
            else config.verification.commands
        ),
        max_commands=command_limit,
    )
    run_id = _find_parent_run_id(output_dir)
    shell_kind = current_verification_shell_kind()
    results: list[dict[str, Any]] = []
    for index, configured_command in enumerate(commands, start=1):
        verification_temp = None
        if VERIFICATION_TEMP_PLACEHOLDER in configured_command:
            verification_temp = create_verification_temp_dir(
                repo_path,
                run_id,
                iteration,
                index,
            )
        executed_command = render_verification_command(
            configured_command,
            shell_kind,
        )
        result = _run_command(
            repo_path,
            configured_command,
            executed_command,
            verification_temp,
            index,
            command_timeout,
            RunnerExecutionContext(
                execution_root=output_dir,
                execution_dir=output_dir / "executions" / f"verification-{index:02d}",
                run_id=run_id,
                step="verification",
                iteration=iteration,
                heartbeat_interval_seconds=0.2,
                lease_timeout_seconds=2.0,
                terminate_grace_seconds=0.25,
                progress_reporter=progress_reporter,
            ),
        )
        results.append(result)
        if result["interruption_status"] is not None:
            break

    completed_commands = commands[: len(results)]
    interruption_result = next(
        (item for item in results if item["interruption_status"] is not None),
        None,
    )
    workspace_fingerprint, workspace_capture_error_type = (
        _capture_workspace_fingerprint(
            workspace,
            repo_path,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
            capture_workspace=capture_runtime_workspace,
        )
    )
    failure_kind: VerificationFailureKind | None = (
        None if workspace_fingerprint is not None else "workspace_capture_failed"
    )
    payload = redact_value({
        "artifact_version": 2,
        "run_id": run_id,
        "iteration": iteration,
        "shell_kind": shell_kind,
        "repo_path": str(repo_path.resolve()),
        "workspace_fingerprint": workspace_fingerprint,
        "workspace_capture_error_type": workspace_capture_error_type,
        "config_path": config.source_path,
        "config_check": config_check.model_dump(),
        "failure_kind": failure_kind,
        "commands": completed_commands,
        "results": results,
        "command_count": len(results),
        "failed_count": sum(1 for item in results if item["status"] != "passed"),
        "selected_command_count": len(commands),
        "skipped_commands": commands[len(results) :],
        "interruption_status": (
            interruption_result["interruption_status"] if interruption_result else None
        ),
        "interruption_command": (
            interruption_result["command"] if interruption_result else None
        ),
        "interruption_reason": (
            interruption_result["interruption_reason"] if interruption_result else None
        ),
    })
    result_path = output_dir / "verification-result.json"
    summary_path = output_dir / "verification-summary.md"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(redact_text(render_verification_summary(payload)), encoding="utf-8")
    return VerificationRunResult(
        summary_path=summary_path,
        result_path=result_path,
        command_count=payload["command_count"],
        failed_count=payload["failed_count"],
        failure_kind=payload["failure_kind"],
        interruption_status=payload["interruption_status"],
        interruption_command=payload["interruption_command"],
        interruption_reason=payload["interruption_reason"],
    )


def select_verification_commands(
    test_commands: list[str],
    lint_commands: list[str],
    *,
    configured_commands: list[str] | None = None,
    max_commands: int = 2,
) -> list[str]:
    commands: list[str] = []
    source = configured_commands if configured_commands is not None else [*test_commands, *lint_commands]
    for command in source:
        normalized = command.strip()
        if normalized and "\n" not in normalized and normalized not in commands:
            commands.append(normalized)
        if len(commands) >= max_commands:
            break
    return commands


def _write_verification_config_failure(
    output_dir: Path,
    config_check: Any,
    *,
    workspace: Path,
    repo_path: Path,
    iteration: int,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> VerificationRunResult:
    text = redact_text(render_project_config_check(config_check))
    run_id = _find_parent_run_id(output_dir)
    shell_kind = current_verification_shell_kind()
    workspace_fingerprint, workspace_capture_error_type = (
        _capture_workspace_fingerprint(
            workspace,
            repo_path,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
            capture_workspace=capture_runtime_workspace,
        )
    )
    failure_kind: VerificationFailureKind = (
        "project_config_invalid"
        if workspace_fingerprint is not None
        else "workspace_capture_failed"
    )
    payload = redact_value({
        "artifact_version": 2,
        "run_id": run_id,
        "iteration": iteration,
        "shell_kind": shell_kind,
        "repo_path": config_check.repo_path,
        "workspace_fingerprint": workspace_fingerprint,
        "workspace_capture_error_type": workspace_capture_error_type,
        "config_path": config_check.source_path,
        "config_check": config_check.model_dump(),
        "failure_kind": failure_kind,
        "commands": [],
        "results": [],
        "command_count": 0,
        "failed_count": 0,
        "selected_command_count": 0,
        "skipped_commands": [],
        "interruption_status": None,
        "interruption_command": None,
        "interruption_reason": None,
    })
    result_path = output_dir / "verification-result.json"
    summary_path = output_dir / "verification-summary.md"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(
        redact_text(
            "\n".join(
                [
                    "# 验证摘要",
                    "",
                    "- `FAIL`：项目验证配置预检失败，未执行任何验证命令。",
                    "",
                    "## 配置预检",
                    "",
                    text.rstrip(),
                ]
            )
            + "\n"
        ),
        encoding="utf-8",
    )
    return VerificationRunResult(
        summary_path=summary_path,
        result_path=result_path,
        command_count=0,
        failed_count=0,
        failure_kind=failure_kind,
    )


def _run_command(
    repo_path: Path,
    configured_command: str,
    executed_command: str,
    verification_temp: Path | None,
    command_index: int,
    timeout_seconds: int,
    execution_context: RunnerExecutionContext,
) -> dict[str, Any]:
    started = time.perf_counter()
    environment = {"PYTHONDONTWRITEBYTECODE": "1"}
    if verification_temp is not None:
        environment[VERIFICATION_TEMP_ENV] = str(verification_temp)
    result = run_owned_process(
        _shell_command(executed_command),
        "",
        repo_path,
        timeout_seconds,
        execution_context,
        environment=environment,
    )
    interruption_status: VerificationInterruptionStatus | None = None
    if result.termination_unconfirmed:
        interruption_status = "termination-unconfirmed"
    elif result.status == "timed_out":
        interruption_status = "timed_out"
    elif result.status == "stopped":
        interruption_status = "stopped"
    status = {
        "success": "passed",
        "error": "failed",
        "timed_out": "timeout",
        "stopped": "failed",
    }[result.status]
    output = _redact_process_output(result.output, result.error)
    if not output and result.status == "timed_out":
        output = redact_text(f"命令超时：{timeout_seconds}s")
    output_log = execution_context.execution_dir / "process-output.txt"
    log_reference = None
    if output_log.is_file() and output_log.resolve().is_relative_to(execution_context.execution_root.resolve()):
        log_reference = output_log.resolve().relative_to(execution_context.execution_root.resolve()).as_posix()
    return {
        "output_log": log_reference,
        "command": redact_text(configured_command),
        "configured_command": redact_text(configured_command),
        "executed_command": redact_text(executed_command),
        "command_index": command_index,
        "verification_temp": (
            _verification_temp_artifact_path(repo_path, verification_temp)
            if verification_temp is not None
            else None
        ),
        "status": status,
        "returncode": result.returncode,
        "duration_seconds": time.perf_counter() - started,
        "output": output,
        "interruption_status": interruption_status,
        "interruption_reason": (
            redact_text(result.error or output) if interruption_status is not None else None
        ),
    }


def _shell_command(command: str) -> list[str] | str:
    # Windows 使用原生命令行以保留 python -c 等嵌套引号；/v:off 禁止延迟展开
    # 在占位符注入后改变 cmd 解析状态。
    return build_verification_shell_command(command)


def _find_parent_run_id(output_dir: Path) -> str:
    resolved = output_dir.resolve()
    for candidate in (resolved, *resolved.parents):
        if candidate.parent.name == "runs":
            return candidate.name
    return resolved.name


def _verification_temp_artifact_path(repo_path: Path, verification_temp: Path) -> str:
    repo = repo_path.resolve()
    resolved = verification_temp.resolve()
    if not resolved.is_relative_to(repo):
        raise ValueError("verification 临时目录无法记录为仓库相对路径")
    return resolved.relative_to(repo).as_posix()


def _redact_process_output(
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> str:
    output = _decode_process_output(stdout) + _decode_process_output(stderr)
    return bounded_output(output, MAX_OUTPUT_CHARS)


def _decode_process_output(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""
