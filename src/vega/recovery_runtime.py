from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .execution_control import ExecutionRecoveryInspection, inspect_execution_for_recovery
from .models import LoopAutomationState
from .redaction import redact_text, write_redacted_text
from .run_utils import resolve_run_dir
from .trace import TraceWriter


class RecoveryRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def recover_loop(self, run: str, reason: str) -> Path:
        """把中断在 running 的 loop 标记为 needs_human，并保留恢复说明。

        recover 只修复 Vega 自己的状态机，不清理工作区、不杀进程、不自动继续。
        这是为了处理 worker 超时、外部命令中断或 CLI 被关闭后留下的半完成 run。
        """
        run_dir = resolve_run_dir(self.workspace, run)
        state_path = run_dir / "state.json"
        try:
            state = LoopAutomationState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            diagnostic = render_corrupt_state_recovery_report(run_dir, reason, exc)
            write_redacted_text(run_dir / "recovery-report.md", diagnostic)
            TraceWriter(run_dir / "trace.jsonl").write(
                "loop_recovery_blocked",
                reason=reason,
                error_type=type(exc).__name__,
                recovery_report="recovery-report.md",
            )
            raise ValueError(
                "state.json 无法解析，已保留现场并生成 recovery-report.md；"
                "Vega 不会猜测或覆盖损坏状态。"
            ) from exc
        if state.run_id != run_dir.name:
            raise ValueError(
                "loop state.run_id 与 run 目录身份不一致；"
                "为避免在错误证据链上 recovery，已拒绝接管。"
            )
        if state.status != "running":
            raise ValueError(f"只能 recover status=running 的 loop，当前状态：{state.status}")
        if not reason.strip():
            raise ValueError("recover 必须提供原因，方便后续追溯。")
        inspection = inspect_execution_for_recovery(run_dir)
        if not inspection.can_recover:
            raise ValueError(inspection.summary)

        previous_step = state.current_step
        previous_iteration = state.current_iteration
        report = render_recovery_report(
            run_id=state.run_id,
            repo_path=state.repo_path,
            previous_step=previous_step,
            previous_iteration=previous_iteration,
            reason=reason,
            inspection=inspection,
        )
        write_redacted_text(run_dir / "recovery-report.md", report)
        state.status = "needs_human"
        state.current_step = "recovered"
        state.artifacts = _dedupe([*state.artifacts, "recovery-report.md", "state.json", "trace.jsonl"])
        state.save(state_path)
        TraceWriter(run_dir / "trace.jsonl").write(
            "loop_recovered",
            previous_step=previous_step,
            previous_iteration=previous_iteration,
            reason=reason,
        )
        return run_dir


def render_corrupt_state_recovery_report(
    run_dir: Path,
    reason: str,
    error: Exception,
) -> str:
    lines = [
        "# Recovery Report",
        "",
        f"- run：`{run_dir.name}`",
        f"- 恢复时间：`{datetime.now(UTC).isoformat()}`",
        f"- 请求原因：{reason.strip() or '未提供'}",
        f"- 状态错误类型：`{type(error).__name__}`",
        "",
        "## 结论",
        "",
        "- `state.json` 无法通过 schema 校验，自动 recovery 已停止。",
        "- Vega 不会根据不完整 JSON 猜测当前步骤，也不会覆盖损坏状态。",
        "- 新状态写入使用原子替换；该诊断主要处理旧 run、外部篡改或存储损坏。",
        "",
        "## 建议下一步",
        "",
        "- 检查 `trace.jsonl`、各 iteration artifact 和 execution.json，确认最后可信步骤。",
        "- 保留当前 run 作为证据；如需继续任务，建议创建新 run 并人工引用旧产物。",
    ]
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_recovery_report(
    *,
    run_id: str,
    repo_path: str,
    previous_step: str,
    previous_iteration: int,
    reason: str,
    inspection: ExecutionRecoveryInspection,
) -> str:
    recovered_at = datetime.now(UTC).isoformat()
    lines = [
        "# Recovery Report",
        "",
        f"- run：`{run_id}`",
        f"- 仓库：`{repo_path}`",
        f"- 恢复时间：`{recovered_at}`",
        f"- 原步骤：`{previous_step}`",
        f"- 原迭代：`{previous_iteration}`",
        f"- 原因：{reason}",
        f"- execution 判断：{inspection.summary}",
    ]
    if inspection.record is not None:
        lease = inspection.record.lease
        lines.extend(
            [
                f"- execution 状态：`{lease.status}`",
                f"- owner PID：`{lease.owner_pid}`",
                f"- child PID：`{lease.child_pid or '无'}`",
                f"- 最后心跳：`{lease.last_heartbeat}`",
                f"- lease 到期：`{lease.lease_expires_at}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 已将 loop 状态从 `running` 标记为 `needs_human`。",
            "- recover 只接管缺失、终态、超时、lease 过期或 PID 消失的 execution。",
            "- 未自动清理工作区，未终止进程，未继续执行 worker/reviewer。",
            "- 请先检查目标仓库 `git status`、execution.json、worker-output.txt 和相关日志。",
            "",
            "## 建议下一步",
            "",
            "- 如果工作区已有合理修复：运行 `vega loop continue --repo <repo> --run <run>`。",
            "- 如果工作区被污染：人工清理后再继续，或放弃该 run 重新开始。",
        ]
    )
    return redact_text("\n".join(lines).rstrip() + "\n")


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
