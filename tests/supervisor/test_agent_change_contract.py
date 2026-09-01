from __future__ import annotations

import pytest
from pydantic import ValidationError

from vega.agent_change_contract import (
    CHANGE_APPROVAL_METADATA_FIELDS,
    ChangeAuthorityEnvelope,
    ChangeContract,
    ChangeSideEffectPolicy,
    ExecutionPlan,
    ExecutionWorkItem,
    approve_change_contract,
    classify_declared_revision,
    validate_execution_plan_against_contract,
)


def test_execution_plan_revision_does_not_invalidate_approved_contract() -> None:
    contract = _approved_contract()
    current = _execution_plan()
    proposed = current.model_copy(
        update={
            "plan_revision": 2,
            "hypotheses": ["问题位于重试入口，而不是幂等键生成器"],
            "implementation_strategy": ["在现有事务边界内复用 payment attempt"],
        }
    )

    assessment = classify_declared_revision(
        current_contract=contract,
        proposed_contract=contract,
        current_plan=current,
        proposed_plan=proposed,
    )

    assert contract.approval_is_current()
    assert assessment.decision == "auto_apply"
    assert "hypotheses" in assessment.changed_fields
    assert "implementation_strategy" in assessment.changed_fields


def test_contract_change_requires_new_human_approval() -> None:
    current_contract = _approved_contract()
    proposed_contract = ChangeContract.model_validate(
        {
            **current_contract.model_dump(
                mode="json",
                exclude=CHANGE_APPROVAL_METADATA_FIELDS,
            ),
            "contract_revision": 2,
            "side_effect_policy": {
                **current_contract.side_effect_policy.model_dump(mode="json"),
                "database_schema_change": True,
            },
        }
    )
    proposed_plan = _execution_plan().model_copy(
        update={"contract_revision": 2, "plan_revision": 2}
    )

    assessment = classify_declared_revision(
        current_contract=current_contract,
        proposed_contract=proposed_contract,
        current_plan=_execution_plan(),
        proposed_plan=proposed_plan,
    )

    assert assessment.decision == "requires_approval"
    assert assessment.changed_fields == [
        "side_effect_policy.database_schema_change"
    ]


def test_contract_change_without_revision_increment_is_rejected() -> None:
    current_contract = _approved_contract()
    proposed_contract = current_contract.model_copy(
        update={
            **{
                field: None
                for field in CHANGE_APPROVAL_METADATA_FIELDS
                if field != "approved"
            },
            "approved": False,
            "non_goals": ["允许调整公共 API"],
        }
    )

    with pytest.raises(ValueError, match="contract_revision 必须递增 1"):
        classify_declared_revision(
            current_contract=current_contract,
            proposed_contract=proposed_contract,
            current_plan=_execution_plan(),
            proposed_plan=_execution_plan(),
        )


def test_execution_plan_candidate_path_must_stay_inside_contract() -> None:
    plan = _execution_plan().model_copy(
        update={
            "work_items": [
                ExecutionWorkItem(
                    work_item_id="WI-01",
                    objective="顺便修改部署脚本",
                    likely_files=["deploy/release.ps1"],
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="越出授权范围"):
        validate_execution_plan_against_contract(_approved_contract(), plan)


def test_forbidden_path_wins_over_allowed_path() -> None:
    contract = _approved_contract().model_copy(
        update={
            "authority_envelope": ChangeAuthorityEnvelope(
                allowed_paths=["src/payments/**"],
                forbidden_paths=["src/payments/generated/**"],
            )
        }
    )
    plan = _execution_plan().model_copy(
        update={
            "work_items": [
                ExecutionWorkItem(
                    work_item_id="WI-01",
                    objective="修改生成代码",
                    likely_files=["src/payments/generated/client.py"],
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="命中禁止范围"):
        validate_execution_plan_against_contract(contract, plan)


def test_execution_plan_dependencies_must_follow_declared_order() -> None:
    with pytest.raises(ValidationError, match="前面已经定义"):
        ExecutionPlan(
            task_id="task-payment-idempotency",
            contract_revision=1,
            work_items=[
                ExecutionWorkItem(
                    work_item_id="WI-02",
                    objective="补回归测试",
                    depends_on=["WI-01"],
                ),
                ExecutionWorkItem(
                    work_item_id="WI-01",
                    objective="修复幂等逻辑",
                ),
            ],
        )


def test_execution_change_requires_plan_revision_increment() -> None:
    current = _execution_plan()
    proposed = current.model_copy(
        update={"hypotheses": ["根因已经更新，但版本号没有变化"]}
    )

    with pytest.raises(ValueError, match="plan_revision 必须递增 1"):
        classify_declared_revision(
            current_contract=_approved_contract(),
            proposed_contract=_approved_contract(),
            current_plan=current,
            proposed_plan=proposed,
        )


def test_contract_approval_digest_detects_frozen_field_change() -> None:
    approved = _approved_contract()
    changed = approved.model_copy(
        update={"acceptance": ["允许重复扣款一次"]}
    )

    assert approved.approval_is_current()
    assert not changed.approval_is_current()


def test_human_approval_records_source_without_policy_binding() -> None:
    approved = _approved_contract()

    assert approved.approval_source == "human"
    assert approved.approval_policy_id is None
    assert approved.approval_policy_digest is None
    assert approved.approval_policy_revision is None


def _approved_contract() -> ChangeContract:
    return approve_change_contract(
        ChangeContract(
            task_id="task-payment-idempotency",
            goal="修复订单偶发重复扣款",
            acceptance=[
                "同一业务支付请求不能重复扣款",
                "第三方超时后重试必须复用同一幂等身份",
            ],
            invariants=["一笔业务订单最多产生一次有效扣款"],
            non_goals=["不重构整个支付架构"],
            side_effect_policy=ChangeSideEffectPolicy(
                payment_or_funds_change=True,
            ),
            required_verification=[
                "重复请求回归测试",
                "现有支付模块测试",
            ],
            authority_envelope=ChangeAuthorityEnvelope(
                allowed_paths=[
                    "src/payments/**",
                    "tests/payments/**",
                ],
                forbidden_paths=["src/payments/generated/**"],
                max_changed_files=10,
                max_repair_rounds=3,
                max_auto_replans=1,
            ),
        ),
        actor="user",
        approved_at="2026-08-25T00:00:00+00:00",
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-payment-idempotency",
        contract_revision=1,
        plan_revision=1,
        observed_facts=["超时重试路径会创建新的支付请求"],
        hypotheses=["重试时重新生成了幂等键"],
        work_items=[
            ExecutionWorkItem(
                work_item_id="WI-01",
                objective="修复幂等身份复用并补回归测试",
                likely_files=[
                    "src/payments/service.py",
                    "tests/payments/test_retry.py",
                ],
                verification=["python -m pytest tests/payments/test_retry.py"],
            )
        ],
        implementation_strategy=["将幂等身份绑定到业务 payment attempt"],
        additional_checks=["git diff --check"],
    )
