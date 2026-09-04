from __future__ import annotations

from .agent_change_run import load_change_run_context
from .agent_run import AgentRun
from .agent_runtime_support import load_agent_bundle


def render_change_approval_prompt(current: AgentRun) -> str:
    """完整展示人工授权字段，不把批准页压缩成模型摘要。"""

    run_dir, state, plan, metadata = load_agent_bundle(
        current.run_dir.parent.parent,
        current.run_dir.name,
    )
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        raise ValueError("当前 Run 缺少 Change Contract")
    contract = context.contract
    execution_plan = context.execution_plan
    side_effects = [
        key
        for key, enabled in contract.side_effect_policy.model_dump().items()
        if enabled
    ]
    lines = [
        "Vega 需要批准 Change Contract",
        "",
        f"目标：{contract.goal}",
        "验收条件：",
        *[f"- {item}" for item in contract.acceptance],
        "允许范围：",
        *[f"- {item}" for item in contract.authority_envelope.allowed_paths],
        "禁止范围：",
        *(
            [f"- {item}" for item in contract.authority_envelope.forbidden_paths]
            or ["- 无"]
        ),
        "验证命令：",
        *[f"- {item}" for item in contract.required_verification],
        "风险与副作用：",
        *(
            [f"- 风险复核：{item}" for item in contract.authorized_risk_reviews]
            + [f"- 已授权副作用：{item}" for item in side_effects]
            or ["- 未声明高风险或外部副作用"]
        ),
        "执行步骤：",
        *[
            f"- {item.work_item_id}：{item.objective}"
            for item in execution_plan.work_items
        ],
        (
            "预算："
            f"最多 {contract.authority_envelope.max_repair_rounds} 轮 repair，"
            f"{contract.authority_envelope.max_auto_replans} 次自动 replan，"
            f"{contract.authority_envelope.max_review_rounds} 轮 review"
        ),
        f"Contract digest：{contract.expected_approval_digest()}",
        "",
        "批准当前 Contract 与 Execution Plan？",
    ]
    return "\n".join(lines)
