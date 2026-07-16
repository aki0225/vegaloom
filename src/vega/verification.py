from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .execution_control import RunnerExecutionContext, run_owned_process
from .project_config import check_project_config, load_project_config, render_project_config_check
from .project_profile import build_project_profile
from .redaction import redact_text, redact_value

MAX_OUTPUT_CHARS = 8000
VerificationInterruptionStatus = Literal[
    "timed_out",
    "stopped",
    "termination-unconfirmed",
]


@dataclass
class VerificationRunResult:
    summary_path: Path
    result_path: Path
    command_count: int
    failed_count: int
    interruption_status: VerificationInterruptionStatus | None = None
    interruption_command: str | None = None
    interruption_reason: str | None = None

    @property
    def has_failures(self) -> bool:
        return self.failed_count > 0

    @property
    def was_interrupted(self) -> bool:
        return self.interruption_status is not None


def run_project_verification(
    workspace: Path,
    repo_path: Path,
    output_dir: Path,
    *,
    max_commands: int | None = None,
    timeout_seconds: int | None = None,
) -> VerificationRunResult:
    """按项目画像执行最小验证命令，并把结果写成可交给 reflect/reviewer 的日志。

    命令只来自 Vega 自己识别出的 project profile，不接收任意外部字符串；
    默认最多执行两个命令，避免轻量 loop 变成长时间 CI。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    config_check = check_project_config(repo_path)
    if config_check.has_errors:
        return _write_verification_config_failure(output_dir, config_check)

    profile = build_project_profile(workspace, repo_path)
    config = load_project_config(repo_path)
    command_limit = max_commands if max_commands is not None else config.verification.max_commands
    command_timeout = timeout_seconds if timeout_seconds is not None else config.verification.timeout_seconds
    commands = select_verification_commands(
        profile.test_commands,
        profile.lint_commands,
        configured_commands=config.verification.commands,
        max_commands=command_limit,
    )
    run_id = _find_parent_run_id(output_dir)
    results: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        result = _run_command(
            repo_path,
            command,
            command_timeout,
            RunnerExecutionContext(
                execution_dir=output_dir / "executions" / f"verification-{index:02d}",
                run_id=run_id,
                step="verification",
                iteration=index,
                heartbeat_interval_seconds=0.2,
                lease_timeout_seconds=2.0,
                terminate_grace_seconds=0.25,
            ),
        )
        results.append(result)
        if result["interruption_status"] is not None:
            break

    executed_commands = commands[: len(results)]
    interruption_result = next(
        (item for item in results if item["interruption_status"] is not None),
        None,
    )
    payload = redact_value({
        "repo_path": str(repo_path.resolve()),
        "config_path": config.source_path,
        "config_check": config_check.model_dump(),
        "commands": executed_commands,
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
) -> VerificationRunResult:
    text = redact_text(render_project_config_check(config_check))
    payload = redact_value({
        "repo_path": config_check.repo_path,
        "config_path": config_check.source_path,
        "config_check": config_check.model_dump(),
        "commands": ["<vega config check>"],
        "results": [
            {
                "command": "<vega config check>",
                "status": "failed",
                "returncode": None,
                "duration_seconds": 0.0,
                "output": text,
                "interruption_status": None,
                "interruption_reason": None,
            }
        ],
        "command_count": 1,
        "failed_count": 1,
        "selected_command_count": 1,
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
        command_count=1,
        failed_count=1,
    )


def render_verification_summary(payload: dict[str, Any]) -> str:
    lines = ["# 验证摘要", "", f"- 仓库：`{payload['repo_path']}`", ""]
    if payload.get("config_path"):
        lines.append(f"- 项目策略：`{payload['config_path']}`")
        lines.append("")
    if not payload["commands"]:
        lines.extend(
            [
                "- 未识别自动验证命令；请人工补充最小验证结果。",
                "",
                "## 结果",
                "",
                "- `SKIP`：project profile 未给出 test/lint 命令。",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            f"- 命令数：{payload['command_count']}",
            f"- 失败数：{payload['failed_count']}",
            "",
            "## 命令结果",
            "",
        ]
    )
    if payload.get("interruption_status"):
        lines.extend(
            [
                f"- 中断状态：`{payload['interruption_status']}`",
                f"- 未执行命令数：{len(payload.get('skipped_commands', []))}",
                "",
            ]
        )
    for index, item in enumerate(payload["results"], start=1):
        interruption_status = item.get("interruption_status")
        badge = {
            "timed_out": "TIMEOUT",
            "stopped": "STOPPED",
            "termination-unconfirmed": "TERMINATION-UNCONFIRMED",
        }.get(interruption_status, "PASS" if item["status"] == "passed" else "FAIL")
        lines.extend(
            [
                f"### {index}. `{item['command']}`",
                "",
                f"- 结果：`{badge}`",
                f"- 退出码：`{item['returncode']}`",
                f"- 耗时：`{item['duration_seconds']:.2f}s`",
                *(
                    [f"- 中断类型：`{interruption_status}`"]
                    if interruption_status is not None
                    else []
                ),
                "",
                "```text",
                item["output"].strip() or "<empty>",
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _run_command(
    repo_path: Path,
    command: str,
    timeout_seconds: int,
    execution_context: RunnerExecutionContext,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = run_owned_process(
        _shell_command(command),
        "",
        repo_path,
        timeout_seconds,
        execution_context,
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
    return {
        "command": redact_text(command),
        "status": status,
        "returncode": result.returncode,
        "duration_seconds": time.perf_counter() - started,
        "output": output,
        "interruption_status": interruption_status,
        "interruption_reason": (
            redact_text(result.error or output) if interruption_status is not None else None
        ),
    }


def _shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def _find_parent_run_id(output_dir: Path) -> str:
    resolved = output_dir.resolve()
    for candidate in (resolved, *resolved.parents):
        if candidate.parent.name == "runs":
            return candidate.name
    return resolved.name


def _redact_process_output(
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> str:
    output = _decode_process_output(stdout) + _decode_process_output(stderr)
    return redact_text(output)[:MAX_OUTPUT_CHARS]


def _decode_process_output(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""
