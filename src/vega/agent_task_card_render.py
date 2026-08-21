from __future__ import annotations

from .agent_task_card import AgentTaskCard


_WORK_ITEM_STATUS = {
    "pending": "待处理",
    "active": "进行中",
    "completed": "已完成",
    "blocked": "受阻",
}
_HANDOFF_STATUS = {
    "none": "尚未交接",
    "handoff_ready": "可交接",
    "handoff_blocked": "交接受阻",
}
_SIDE_EFFECT_STATUS = {
    "none": "无",
    "known": "已知",
    "unknown": "未知",
}


def render_task_card_body(card: AgentTaskCard) -> str:
    sections = [
        ("目标与非目标", _goal_lines(card)),
        ("成功条件", card.plan.success_conditions),
        ("已确认事实与假设", _fact_lines(card)),
        ("已批准计划", _plan_lines(card)),
        ("进度与失败尝试", _progress_lines(card)),
        ("风险与验证", _risk_lines(card)),
        ("最近交接", _handoff_lines(card)),
        ("下一步", _next_step_lines(card)),
    ]
    output: list[str] = []
    for title, values in sections:
        output.extend([f"## {title}", ""])
        output.extend(f"- {value}" for value in (values or ["无"]))
        output.append("")
    return "\n".join(output).rstrip()


def _goal_lines(card: AgentTaskCard) -> list[str]:
    return [
        f"目标：{card.plan.user_goal}",
        *(f"非目标：{value}" for value in card.plan.non_goals),
    ]


def _fact_lines(card: AgentTaskCard) -> list[str]:
    return [
        *(f"事实：{value}" for value in card.plan.observed_facts),
        *(f"假设：{value}" for value in card.plan.hypotheses),
    ]


def _plan_lines(card: AgentTaskCard) -> list[str]:
    approval = (
        f"已批准，digest={card.plan.approved_digest}"
        if card.plan.approval_is_current()
        else "尚未批准"
    )
    return [
        f"计划版本：{card.plan.plan_revision}，{approval}",
        *(
            f"{item.work_item_id} [{_WORK_ITEM_STATUS[item.status]}]：{item.objective}"
            for item in card.plan.work_items
        ),
    ]


def _progress_lines(card: AgentTaskCard) -> list[str]:
    return [
        *(f"进度：{value}" for value in card.progress_notes),
        *(f"失败尝试：{value}" for value in card.failed_attempts),
    ]


def _risk_lines(card: AgentTaskCard) -> list[str]:
    return [
        *(f"风险：{value}" for value in card.risk_notes),
        *(f"验证：{value}" for value in card.verification_notes),
    ]


def _handoff_lines(card: AgentTaskCard) -> list[str]:
    if card.resume_capsule is None:
        return ["尚无跨机器交接"]
    capsule = card.resume_capsule
    return [
        f"状态：{_HANDOFF_STATUS[card.handoff_status]}",
        f"停止位置：{capsule.stopped_at}",
        f"Workspace 摘要：{capsule.workspace_digest}",
        f"变更文件：{', '.join(capsule.changed_files) or '无'}",
        f"比较基线：{capsule.comparison_base_revision or '旧版交接基线'}",
        f"外部副作用：{_SIDE_EFFECT_STATUS[capsule.external_side_effects]}",
    ]


def _next_step_lines(card: AgentTaskCard) -> list[str]:
    if card.resume_capsule is None:
        return ["等待 Plan 批准或继续当前 Work Item"]
    lines = [card.resume_capsule.next_step]
    if card.resume_capsule.recommended_command:
        lines.append(f"建议命令：`{card.resume_capsule.recommended_command}`")
    return lines
