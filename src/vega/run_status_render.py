from __future__ import annotations

from typing import Any

from .agent_run_status import agent_status_lines
from .execution_control import ACTIVE_EXECUTION_STATUSES
from .execution_feedback import render_owned_child_pid_line


def render_run_status_payload(payload: dict[str, Any]) -> str:
    lines = [
        "# Run Status",
        "",
        f"- run：`{payload['run_id']}`",
        f"- 类型：`{payload['kind']}`",
        f"- 状态：`{payload['status']}`",
        f"- 当前步骤：`{payload['current_step']}`",
    ]
    if payload.get("repo_path"):
        lines.append(f"- 仓库：`{payload['repo_path']}`")
    if payload.get("risk"):
        lines.append(f"- 风险等级：`{payload['risk']}`")
    if payload.get("recommendation"):
        lines.append(f"- 建议：`{payload['recommendation']}`")
    if payload.get("review_queue_status") not in {None, "not_used"}:
        lines.append(
            "- Review Queue："
            f"`{payload['review_queue_status']}` / "
            f"`{payload.get('review_queue_completed') or 0}`"
            f"/`{payload.get('review_queue_total') or 0}`"
        )
    lines.extend(agent_status_lines(payload))
    if payload.get("decision_count"):
        lines.append(f"- 人工决策：`{payload['decision_count']}` 条")
    execution = payload.get("execution")
    if execution:
        execution_is_current = bool(execution.get("termination_unconfirmed")) or (
            payload["status"] == "running"
            and execution["status"] in ACTIVE_EXECUTION_STATUSES
        )
        lines.extend(
            [
                f"- execution：`{execution['status']}` / `{execution['step']}`",
                render_owned_child_pid_line(
                    execution_is_current,
                    execution["child_pid"],
                ),
                f"- 最后心跳：`{execution['last_heartbeat']}`",
            ]
        )
        if execution.get("termination_unconfirmed"):
            lines.append("- owned process tree：`终止未确认`")
    lines.extend(["", "## 下一步", ""])
    lines.extend(f"- {item}" for item in payload["next_steps"])
    lines.extend(["", "## 关键产物", ""])
    artifacts = payload["key_artifacts"]
    if artifacts:
        lines.extend(f"- `{item}`" for item in artifacts)
    else:
        lines.append("- 未识别关键产物。")
    return "\n".join(lines).rstrip() + "\n"
