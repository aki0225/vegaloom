from __future__ import annotations

from vega.agent_context import compile_task_brief
from vega.agent_contract import AgentPlan, AgentWorkItem, approve_plan


def test_task_brief_preserves_all_required_facts() -> None:
    facts = [f"已确认事实 {index:02d}" for index in range(1, 14)]
    plan = approve_plan(
        AgentPlan(
            task_id="task-context-completeness",
            user_goal="验证 Task Brief 不静默丢失事实",
            observed_facts=facts,
            work_items=[
                AgentWorkItem(
                    work_item_id="W1",
                    objective="保留全部已确认事实",
                    allowed_paths=["src/example.py"],
                    verification=["python -m pytest tests/test_example.py -q"],
                    external_side_effects="none",
                )
            ],
        ),
        actor="human",
        approved_at="2026-08-20T00:00:00+00:00",
    )

    brief = compile_task_brief(
        plan=plan,
        work_item_id="W1",
    )

    assert "已确认事实 13" in brief.content
    assert "其余" not in brief.content
    assert "外部副作用声明：none" in brief.content
