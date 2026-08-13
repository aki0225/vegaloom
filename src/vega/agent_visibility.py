from __future__ import annotations

from .agent_contract import AgentStatusCard


_PHASE_LABELS = {
    "planning": "调查与计划",
    "awaiting_approval": "等待批准",
    "ready": "等待执行",
    "acting": "Worker 执行",
    "observing": "现场对账",
    "needs_human": "等待人工",
    "finalizing": "最终裁决",
    "completed": "已完成",
    "stopped": "已停止",
}
_GATE_LABELS = {
    "not_run": "尚未运行",
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻断",
    "stale": "已过期",
}
_CHECKPOINT_LABELS = {
    "safe": "现场可解释",
    "uncertain": "现场不确定",
    "blocked": "现场阻断",
}
_ACTION_LABELS = {
    "next": "进入下一 Work Item",
    "repair": "在原范围内修复",
    "replan": "重新调查并修订计划",
    "human": "交由人工判断",
    "finalize": "进入 Finish",
}


def render_agent_status_card(card: AgentStatusCard) -> str:
    checkpoint = "尚无"
    if card.latest_checkpoint:
        status = (
            _CHECKPOINT_LABELS[card.checkpoint_status]
            if card.checkpoint_status is not None
            else "状态未知"
        )
        checkpoint = f"{card.latest_checkpoint} / {status}"
    changed_files = (
        "无"
        if not card.changed_files
        else f"{len(card.changed_files)} 个（{', '.join(card.changed_files[:5])}"
        f"{' 等' if len(card.changed_files) > 5 else ''}）"
    )
    allowed = (
        "无"
        if not card.allowed_actions
        else "、".join(_ACTION_LABELS[action] for action in card.allowed_actions)
    )
    lines = [
        "# Vega Agent",
        "",
        f"- 运行：`{card.run_id}`",
        f"- 阶段：{_PHASE_LABELS[card.phase]}",
        f"- 任务：{card.task_goal}",
        f"- Work Item：{card.work_item_label}",
        f"- Worker：{card.worker_label}",
        f"- Workspace：{changed_files}；未知文件 {card.unknown_file_count} 个",
        f"- 最近 Checkpoint：{checkpoint}",
        f"- Verification：{_GATE_LABELS[card.verification]}",
        f"- Risk：{_GATE_LABELS[card.risk]}",
        f"- Reviewer：{_GATE_LABELS[card.review]}",
        f"- 允许动作：{allowed}",
        f"- 下一步：{card.next_step}",
    ]
    return "\n".join(lines).rstrip() + "\n"
