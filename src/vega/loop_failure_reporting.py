from __future__ import annotations

from pathlib import Path
from typing import Literal

from .redaction import write_redacted_text
from .verification import VerificationRunResult


def write_execution_interruption_report(
    iteration_dir: Path,
    *,
    step: str,
    status: Literal["timed_out", "stopped"],
    reason: str | None,
    command: str | None = None,
) -> Path:
    filename = "timeout-report.md" if status == "timed_out" else "stop-report.md"
    title = "Timeout Report" if status == "timed_out" else "Stop Report"
    if step == "verification":
        conclusion = (
            "当前 verification 命令已超时；本轮不会继续剩余验证、reflect 或 review。"
            if status == "timed_out"
            else "当前 verification 已按用户请求停止；本轮不会继续剩余验证、reflect 或 review。"
        )
    else:
        conclusion = (
            "当前 attempt 已超时；timeout 不等于任务失败，但本轮不会继续 verification/review。"
            if status == "timed_out"
            else "当前 attempt 已按用户请求停止，本轮不会继续 verification/review。"
        )
    path = iteration_dir / filename
    details = [
        f"# {title}",
        "",
        f"- 步骤：`{step}`",
        f"- 状态：`{status}`",
    ]
    if command:
        details.append(f"- 命令：`{command}`")
    details.extend(
        [
            f"- 原因：{reason or '未提供'}",
            "",
            "## 结论",
            "",
            f"- {conclusion}",
            "- 目标仓库现场和 execution 证据均保留，交由人工检查。",
            "- Vega 未自动回滚、清理、提交、推送或发布。",
        ]
    )
    write_redacted_text(path, "\n".join(details).rstrip() + "\n")
    return path


def write_verification_interruption_report(
    iteration_dir: Path,
    verification: VerificationRunResult,
) -> Path:
    status = verification.interruption_status
    if status in {"timed_out", "stopped"}:
        return write_execution_interruption_report(
            iteration_dir,
            step="verification",
            status=status,
            reason=verification.interruption_reason,
            command=verification.interruption_command,
        )
    if status != "termination-unconfirmed":
        raise ValueError("verification interruption 缺少可识别状态")

    path = iteration_dir / "runner-error-report.md"
    write_redacted_text(
        path,
        "\n".join(
            [
                "# Verification Termination Report",
                "",
                "- 步骤：`verification`",
                "- 状态：`termination-unconfirmed`",
                f"- 命令：`{verification.interruption_command or '未知'}`",
                f"- 原因：{verification.interruption_reason or '未提供'}",
                "",
                "## 结论",
                "",
                "- owned process tree 的终止未被确认，不能把本轮视为普通命令失败。",
                "- 剩余验证命令、reflect 和 reviewer 均未继续执行。",
                "- execution、验证输出和目标仓库现场均已保留，必须人工确认进程与工作区状态。",
                "- Vega 未自动回滚、清理、提交、推送或发布。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def write_runner_error_report(
    iteration_dir: Path,
    *,
    step: str,
    reason: str | None,
    workspace_status: str,
    termination_unconfirmed: bool = False,
) -> Path:
    path = iteration_dir / "runner-error-report.md"
    if termination_unconfirmed:
        write_redacted_text(
            path,
            "\n".join(
                [
                    "# Runner Termination Report",
                    "",
                    f"- 步骤：`{step}`",
                    "- 状态：`termination-unconfirmed`",
                    f"- 原因：{reason or '未提供'}",
                    "",
                    "## 结论",
                    "",
                    "- owned process tree 的终止未被确认，不能按普通 runner error 继续处理。",
                    "- runner 输出、工作区检查、verification 和 review 均未继续消费。",
                    "- execution 证据和目标仓库现场已保留，必须人工确认进程与工作区状态。",
                    "- Vega 未自动回滚、清理、提交、推送或发布。",
                ]
            ).rstrip()
            + "\n",
        )
        return path
    write_redacted_text(
        path,
        "\n".join(
            [
                "# Runner Error Report",
                "",
                f"- 步骤：`{step}`",
                f"- 原因：{reason or '未提供'}",
                "",
                "## 当前工作区",
                "",
                "```text",
                workspace_status.strip() or "<clean>",
                "```",
                "",
                "## 结论",
                "",
                "- 外部 runner 异常不等于工作区没有改动，当前现场必须按部分完成处理。",
                "- 本轮未继续 verification/review，不能把现有 diff 视为已完成或已通过。",
                "- Vega 未自动回滚、清理、提交、推送或发布。",
                "",
                "## 建议下一步",
                "",
                "- 先人工检查 `git status`、当前 diff 和 `worker-output.txt`。",
                "- 如部分改动可保留，回到所属 ChangeRun 继续 Repair、验证和隔离审查。",
                "- 如改动不可用，人工清理后创建新的 ChangeRun。",
            ]
        ).rstrip()
        + "\n",
    )
    return path
