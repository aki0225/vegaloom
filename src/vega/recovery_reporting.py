from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .execution_control import ExecutionRecoveryInspection
from .redaction import redact_text


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


def render_worker_rerun_prestart_recovery_report(
    *,
    run_id: str,
    reason: str,
    rerun_recovery: str,
) -> str:
    restored = rerun_recovery == "prestart_restored"
    lines = [
        "# Recovery Report",
        "",
        f"- run：`{run_id}`",
        f"- 恢复时间：`{datetime.now(UTC).isoformat()}`",
        f"- 请求原因：{reason.strip()}",
        "- 场景：显式 Worker 重跑尚未越过 `worker_started` 边界",
        "",
        "## 结论",
        "",
        (
            "- 已把持久化的 iteration claim 恢复为 `needs_human/recovered`；"
            "没有把尚未启动的 Worker 误记为中断执行。"
            if restored
            else "- 当前仍处于 claim 前的可重入状态，无需创建新的 recovery 记录。"
        ),
        "- 已准备的 baseline 与重跑事务原样保留，未覆盖或删除证据。",
        "- 目标仓库未由本次 recovery 修改。",
        "",
        "## 建议下一步",
        "",
        "- 复核工作区未发生外部变化后，重新执行同一个 `continue --rerun-worker`。",
        "- 如工作区已变化，Vega 会拒绝启动可写 Worker，并交还人工处理。",
    ]
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_iteration_interruption_report(
    *,
    run_id: str,
    iteration: int,
    previous_step: str,
    recovered_at: str,
    inspection: ExecutionRecoveryInspection,
) -> str:
    return redact_text(
        "\n".join(
            [
                "# Iteration Interruption Report",
                "",
                f"- run：`{run_id}`",
                f"- 迭代：`{iteration}`",
                "- 生命周期：`interrupted`",
                f"- 原步骤：`{previous_step}`",
                f"- 冻结时间：`{recovered_at}`",
                f"- execution 判断：{inspection.summary}",
                "",
                "## 结论",
                "",
                "- 本轮 orchestration 未形成可信终态，不能作为 success、verification 或 reviewer 依据。",
                "- 已保留本轮现有 execution、输出和工作区 diff，不自动覆盖、回滚或清理。",
                "- 后续人工确认现场后，`loop continue` 必须使用新的 iteration 编号。",
            ]
        ).rstrip()
        + "\n"
    )
