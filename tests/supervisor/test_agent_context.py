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
    assert "## 批准时观察事实（可能已被前序修改改变）" in brief.content
    assert "其余" not in brief.content
    assert "外部副作用声明：none" in brief.content

    current = compile_task_brief(
        plan=plan,
        work_item_id="W1",
        confirmed_facts=["当前代码已完成前序修改"],
    )
    assert "## 当前已确认事实" in current.content
    assert "当前代码已完成前序修改" in current.content
    assert "## 批准时观察事实" not in current.content


def test_task_brief_separates_current_and_later_work_items() -> None:
    draft = AgentPlan(
        task_id="task-context-work-item-boundary",
        user_goal="分两步调整字符串工具",
        success_conditions=["两个字符串行为最终都符合要求"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="先调整 normalize_name",
                allowed_paths=["src/text_tools.py"],
                verification=["python -m pytest tests/test_text_tools.py -q"],
                external_side_effects="none",
            ),
            AgentWorkItem(
                work_item_id="W2",
                objective="再调整 is_blank",
                allowed_paths=["src/text_tools.py"],
                verification=["python -m pytest tests/test_text_tools.py -q"],
                external_side_effects="none",
            ),
        ],
    )
    payload = draft.model_dump(mode="json")
    payload.update(
        {
            "approved": True,
            "approved_at": "2026-08-25T00:00:00+00:00",
            "approved_by": "human",
            "approved_digest": draft.expected_approval_digest(),
        }
    )
    plan = AgentPlan.model_validate(payload)

    brief = compile_task_brief(plan=plan, work_item_id="W1")

    assert "## 当前 Work Item（本轮唯一修改目标）" in brief.content
    assert "- W1: 先调整 normalize_name" in brief.content
    assert "## 其他未完成 Work Item（本轮不处理）" in brief.content
    assert "- W2: 再调整 is_blank" in brief.content
    assert "## 最终合同条件（全部 Work Item 完成后判断）" in brief.content
    assert "即使后续事项位于相同文件，也不要提前实现" in brief.content
