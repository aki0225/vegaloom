from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vega.agent_context import TaskBriefBudgetExceeded, compile_task_brief
from vega.agent_contract import (
    AgentCheckpoint,
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
    approve_plan,
)
from vega.agent_persistence import (
    AgentArtifactError,
    append_agent_trace,
    load_agent_state,
    read_agent_trace,
    save_agent_state,
)
from vega.agent_routing import decide_next_action, transition_state


FINGERPRINT = "1" * 64


def test_unknown_schema_and_absolute_path_fail_closed() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        AgentWorkItem(
            schema_version=2,
            work_item_id="W1",
            objective="修改代码",
        )

    with pytest.raises(ValidationError, match="仓库相对路径"):
        AgentWorkItem(
            work_item_id="W1",
            objective="修改代码",
            allowed_paths=["C:/Users/example/project.py"],  # repo-path-policy: allow-test-fixture
        )


@pytest.mark.parametrize(
    "observation_id",
    [
        "../agent-state",
        "obs/child",
        r"C:\temp\claim",  # repo-path-policy: allow-test-fixture
        ".",
        "obs.json",
    ],
)
def test_observation_id_rejects_path_like_values(observation_id: str) -> None:
    with pytest.raises(ValidationError, match="observation_id"):
        AgentObservation(
            observation_id=observation_id,
            machine_summary="拒绝路径型 ID",
            workspace_fingerprint=FINGERPRINT,
        )


def test_plan_approval_digest_becomes_stale_after_plan_change() -> None:
    approved = _approved_plan()

    changed = approved.model_copy(deep=True)
    changed.work_items[0].objective = "修改另一处代码"

    assert approved.approval_is_current()
    assert not changed.approval_is_current()


@pytest.mark.parametrize(
    ("observation", "expected_action"),
    [
        (
            AgentObservation(
                observation_id="obs-next",
                work_item_id="W1",
                child_run="attempt-01",
                operation_id="operation-01",
                machine_summary="当前现场可解释",
                workspace_fingerprint=FINGERPRINT,
                authority="fake_worker",
                work_item_completed=True,
                verification="passed",
                risk="passed",
                review="passed",
            ),
            "next",
        ),
        (
            AgentObservation(
                observation_id="obs-next-evidence-missing",
                work_item_id="W1",
                child_run="attempt-01",
                operation_id="operation-01",
                machine_summary="当前项声称完成，但门禁证据缺失",
                workspace_fingerprint=FINGERPRINT,
                authority="fake_worker",
                work_item_completed=True,
            ),
            "human",
        ),
        (
            AgentObservation(
                observation_id="obs-repair",
                work_item_id="W1",
                child_run="attempt-01",
                operation_id="operation-01",
                machine_summary="测试失败且可在范围内修复",
                workspace_fingerprint=FINGERPRINT,
                authority="fake_worker",
                verification="failed",
                repairable_in_scope=True,
            ),
            "repair",
        ),
        (
            AgentObservation(
                observation_id="obs-human",
                work_item_id="W1",
                child_run="attempt-01",
                operation_id="operation-01",
                machine_summary="外部副作用无法确认",
                workspace_fingerprint=FINGERPRINT,
                authority="fake_worker",
                external_side_effects="unknown",
            ),
            "human",
        ),
        (
            AgentObservation(
                observation_id="obs-finalize",
                work_item_id="W1",
                child_run="attempt-01",
                operation_id="operation-01",
                machine_summary="全部工作和门禁完成",
                workspace_fingerprint=FINGERPRINT,
                authority="fake_worker",
                verification="passed",
                risk="passed",
                review="passed",
                work_item_completed=True,
                all_work_items_completed=True,
            ),
            "finalize",
        ),
    ],
)
def test_different_observations_produce_different_decisions(
    observation: AgentObservation,
    expected_action: str,
) -> None:
    decision = decide_next_action(_approved_plan(), observation)

    assert decision.selected_action == expected_action
    assert decision.reason


def test_finalize_is_rejected_when_verification_failed() -> None:
    plan = _approved_plan()
    observation = AgentObservation(
        observation_id="obs-failed",
        work_item_id="W1",
        child_run="attempt-01",
        operation_id="operation-01",
        machine_summary="实现结束但验证失败",
        workspace_fingerprint=FINGERPRINT,
        authority="fake_worker",
        verification="failed",
        risk="passed",
        review="passed",
        work_item_completed=True,
        all_work_items_completed=True,
    )
    routed = decide_next_action(plan, observation)
    forced_finalize = routed.model_copy(
        update={
            "allowed_actions": ["finalize"],
            "selected_action": "finalize",
            "source": "supervisor",
        }
    )
    state = AgentState(
        run_id="agent-1",
        task_id=plan.task_id,
        repository_id="repo-1",
        phase="observing",
        approved_plan_digest=plan.approved_digest,
        workspace_fingerprint=FINGERPRINT,
    )

    with pytest.raises(ValueError, match="确定性规则拒绝"):
        transition_state(state, plan, observation, forced_finalize)


def test_external_observation_cannot_promote_claim_to_progress() -> None:
    observation = AgentObservation(
        observation_id="obs-forged",
        work_item_id="W1",
        machine_summary="外部调用者声称全部完成",
        workspace_fingerprint=FINGERPRINT,
        verification="passed",
        risk="passed",
        review="passed",
        work_item_completed=True,
        all_work_items_completed=True,
    )

    decision = decide_next_action(_approved_plan(), observation)

    assert decision.selected_action == "human"
    assert "只作为 Claim" in decision.reason


def test_finalize_claim_cannot_skip_pending_work_item() -> None:
    plan = AgentPlan(
        task_id="task-two-items",
        user_goal="完成两项工作",
        work_items=[
            AgentWorkItem(work_item_id="W1", objective="第一项"),
            AgentWorkItem(work_item_id="W2", objective="第二项"),
        ],
    )
    plan = approve_plan(plan, actor="user")
    observation = AgentObservation(
        observation_id="obs-skip",
        work_item_id="W1",
        child_run="attempt-01",
        operation_id="operation-01",
        machine_summary="错误声称全部完成",
        workspace_fingerprint=FINGERPRINT,
        authority="fake_worker",
        work_item_completed=True,
        all_work_items_completed=True,
        verification="passed",
        risk="passed",
        review="passed",
    )

    decision = decide_next_action(plan, observation)

    assert decision.selected_action == "human"
    assert "仍有未完成 Work Item" in decision.reason


def test_task_brief_has_no_lower_bound_and_redacts_secret() -> None:
    brief = compile_task_brief(
        plan=_approved_plan(),
        work_item_id="W1",
        confirmed_facts=["Authorization: Bearer abcdefghijklmnop"],
        max_bytes=4096,
    )

    assert 0 < brief.utf8_bytes < 4096
    assert "abcdefghijklmnop" not in brief.content
    assert "[REDACTED]" in brief.content


def test_task_brief_required_content_is_never_silently_truncated() -> None:
    plan = _approved_plan()
    plan.success_conditions = ["必须保留的成功条件" * 100]
    plan = approve_plan(
        plan.model_copy(
            update={
                "approved": False,
                "approved_at": None,
                "approved_by": None,
                "approved_digest": None,
            }
        ),
        actor="user",
    )

    with pytest.raises(TaskBriefBudgetExceeded, match="不能静默截断"):
        compile_task_brief(
            plan=plan,
            work_item_id="W1",
            max_bytes=512,
        )


def test_state_envelope_detects_tampering_and_trace_is_append_only(tmp_path: Path) -> None:
    state = AgentState(
        run_id="agent-1",
        task_id="task-1",
        repository_id="repo-1",
        phase="ready",
    )
    state_path = tmp_path / "agent-state.json"
    trace_path = tmp_path / "trace.jsonl"
    save_agent_state(state_path, state)
    append_agent_trace(
        trace_path,
        event="plan_approved",
        state=state,
        route_reason="用户批准计划",
    )
    append_agent_trace(
        trace_path,
        event="checkpoint_written",
        state=state,
        observation_summary="现场可解释",
    )

    assert load_agent_state(state_path) == state
    assert [item["event"] for item in read_agent_trace(trace_path)] == [
        "plan_approved",
        "checkpoint_written",
    ]

    envelope = json.loads(state_path.read_text(encoding="utf-8"))
    envelope["data"]["phase"] = "completed"
    state_path.write_text(
        json.dumps(envelope, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(AgentArtifactError, match="digest 不一致"):
        load_agent_state(state_path)


def test_checkpoint_rejects_non_relative_evidence_ref() -> None:
    with pytest.raises(ValidationError, match="仓库相对路径"):
        AgentCheckpoint(
            checkpoint_id="cp-1",
            run_id="agent-1",
            state_version=1,
            reason="交接",
            status="safe",
            phase="ready",
            workspace_fingerprint=FINGERPRINT,
            pending_actions=["next"],
            evidence_refs=["../outside.json"],
        )


def _approved_plan() -> AgentPlan:
    return approve_plan(
        AgentPlan(
            task_id="task-1",
            user_goal="修复确定的问题",
            success_conditions=["验证通过"],
            observed_facts=["已定位目标模块"],
            work_items=[
                AgentWorkItem(
                    work_item_id="W1",
                    objective="完成最小修改",
                    allowed_paths=["src/vega/example.py"],
                    forbidden_paths=["eval/real-world-runs.md"],
                    verification=["python -m pytest tests/test_example.py"],
                )
            ],
        ),
        actor="user",
        approved_at="2026-08-13T00:00:00+00:00",
    )
