from __future__ import annotations

import re
from dataclasses import dataclass

from .agent_change_contract import ExecutionWorkItem
from .agent_change_run import load_change_run_context
from .agent_contract import canonical_digest
from .agent_run import AgentRun
from .agent_runtime_support import load_agent_bundle
from .redaction import redact_text


_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)"
    r"[^\s`\"'<>|,;，；。!?！？)\]}]+"
)
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![:\w])/(?:home|users|root|tmp|workspace|mnt|opt|private/var|var/(?:folders|tmp))"
    r"(?![\w-])"
    r"(?:/[^/\s`\"'<>|,;，；、。!?！？)\]}]+)*",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ChangeApprovalSnapshot:
    """绑定用户实际看到的 Contract、Execution Plan 与状态版本。"""

    prompt: str
    state_version: int
    contract_digest: str
    execution_plan_revision: int
    execution_plan_digest: str


def build_change_approval_snapshot(current: AgentRun) -> ChangeApprovalSnapshot:
    """生成批准页及其机器绑定，供确认返回后做原子校验。"""

    run_dir, state, plan, metadata = load_agent_bundle(
        current.run_dir.parent.parent,
        current.run_dir.name,
    )
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        raise ValueError("当前 Run 缺少 Change Contract")
    contract = context.contract
    execution_plan = context.execution_plan
    plan_digest = canonical_digest(execution_plan.model_dump(mode="json"))
    envelope = contract.authority_envelope
    lines = [
        "Vega 需要批准 Change Contract",
        "",
        f"Task：{contract.task_id}",
        f"Contract revision：{contract.contract_revision}",
        f"Execution Plan revision：{execution_plan.plan_revision}",
        f"目标：{contract.goal}",
        "验收条件：",
        *_items(contract.acceptance),
        "必须保持：",
        *_items(contract.invariants),
        "不在本次范围：",
        *_items(contract.non_goals),
        "允许范围：",
        *_items(envelope.allowed_paths),
        "禁止范围：",
        *_items(envelope.forbidden_paths),
        f"最多修改文件数：{envelope.max_changed_files or '未单独限制'}",
        "自动循环预算：",
        f"- repair：{envelope.max_repair_rounds}",
        f"- replan：{envelope.max_auto_replans}",
        f"- review：{envelope.max_review_rounds}",
        f"- verification retry：{envelope.max_verification_retries}",
        "副作用授权：",
        *[
            f"- {name}：{'允许' if enabled else '禁止'}"
            for name, enabled in contract.side_effect_policy.model_dump().items()
        ],
        "风险复核：",
        *_items(contract.authorized_risk_reviews),
        "验证命令：",
        *_items(contract.required_verification),
        "已确认事实：",
        *_items(execution_plan.observed_facts),
        "待验证假设：",
        *_items(execution_plan.hypotheses),
        "实现策略：",
        *_items(execution_plan.implementation_strategy),
        "额外检查：",
        *_items(execution_plan.additional_checks),
        "未解决决策：",
        *_items(execution_plan.unresolved_decisions),
        "执行步骤：",
        *_render_work_items(execution_plan.work_items),
        f"Contract digest：{contract.expected_approval_digest()}",
        f"Execution Plan digest：{plan_digest}",
        "",
        "批准当前 Contract 与 Execution Plan？",
    ]
    return ChangeApprovalSnapshot(
        prompt=redact_change_message("\n".join(lines)),
        state_version=state.state_version,
        contract_digest=contract.expected_approval_digest(),
        execution_plan_revision=execution_plan.plan_revision,
        execution_plan_digest=plan_digest,
    )


def render_change_approval_prompt(current: AgentRun) -> str:
    """兼容既有调用方；新批准路径应同时保留机器绑定。"""

    return build_change_approval_snapshot(current).prompt


def _items(values: list[str]) -> list[str]:
    return [f"- {item}" for item in values] or ["- 无"]


def _render_work_items(work_items: list[ExecutionWorkItem]) -> list[str]:
    lines: list[str] = []
    for item in work_items:
        lines.extend(
            [
                f"- {item.work_item_id}",
                f"  目标：{item.objective}",
                f"  依赖：{_inline(item.depends_on)}",
                f"  候选文件：{_inline(item.likely_files)}",
                f"  验证：{_inline(item.verification)}",
                f"  风险说明：{_inline(item.risk_notes)}",
            ]
        )
    return lines


def _inline(values: list[str]) -> str:
    return "；".join(values) if values else "无"


def redact_change_message(value: str) -> str:
    """清理 Change CLI 出站消息中的凭据和本机绝对路径。"""

    safe = redact_text(value)
    safe = _WINDOWS_ABSOLUTE_PATH.sub("<redacted-path>", safe)
    return _POSIX_ABSOLUTE_PATH.sub("<redacted-path>", safe)
