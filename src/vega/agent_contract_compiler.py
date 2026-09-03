from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_change_contract import (
    ChangeAuthorityEnvelope,
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
)
from .agent_planning import (
    PLANNING_REPORT_ARTIFACT,
    PlanningProposal,
)
from .project_config import (
    ProjectConfig,
    validate_project_config,
)
from .risk_review import match_required_reviews
from .scope_path_matching import (
    path_matches_pattern,
    scope_paths_are_case_insensitive,
)


PLAN_CARD_ARTIFACT = "plan-card.md"


@dataclass(frozen=True)
class CompiledPlanningContract:
    contract: ChangeContract
    execution_plan: ExecutionPlan
    risk_hits: tuple[tuple[str, str, tuple[str, ...]], ...]


def compile_planning_proposal(
    repo: Path,
    proposal: PlanningProposal,
    config: ProjectConfig,
) -> CompiledPlanningContract:
    """只使用 Proposal 和仓库配置生成现有 Change Contract 模型。"""

    if proposal.proposal_revision != 1:
        raise ValueError("proposal_revision：初始 Planning Proposal 必须为 1")
    _require_valid_config(config)
    registered_commands = _registered_verification_commands(config)
    verification = _compile_verification(proposal, registered_commands, config)
    likely_files = _likely_files(proposal)
    _validate_scope(repo, likely_files, proposal, config)
    risk_hits = _compile_risk_hits(repo, config, likely_files)
    envelope = _compile_authority_envelope(proposal, config, likely_files)
    contract_proposal = proposal.contract_proposal
    contract = ChangeContract(
        task_id=proposal.task_id,
        contract_revision=proposal.proposal_revision,
        goal=contract_proposal.goal,
        acceptance=list(contract_proposal.acceptance),
        invariants=list(contract_proposal.invariants),
        non_goals=list(contract_proposal.non_goals),
        authorized_risk_reviews=[
            risk_id for risk_id, _label, _files in risk_hits
        ],
        side_effect_policy=contract_proposal.side_effect_policy,
        required_verification=verification,
        authority_envelope=envelope,
    )
    execution_plan = ExecutionPlan(
        task_id=proposal.task_id,
        contract_revision=contract.contract_revision,
        plan_revision=proposal.proposal_revision,
        observed_facts=[
            fact.statement for fact in proposal.observed_facts
        ],
        hypotheses=list(proposal.hypotheses),
        work_items=[
            ExecutionWorkItem.model_validate(item.model_dump(mode="json"))
            for item in proposal.execution_plan.work_items
        ],
        implementation_strategy=list(
            proposal.execution_plan.implementation_strategy
        ),
        additional_checks=list(
            proposal.execution_plan.additional_check_suggestions
        ),
        unresolved_decisions=list(proposal.unresolved_questions),
    )
    return CompiledPlanningContract(
        contract=contract,
        execution_plan=execution_plan,
        risk_hits=risk_hits,
    )


def render_plan_card(
    proposal: PlanningProposal,
    compiled: CompiledPlanningContract,
) -> str:
    contract = compiled.contract
    plan = compiled.execution_plan
    lines = [
        "# 变更计划（编译快照）",
        "",
        f"- 任务：`{contract.task_id}`",
        f"- 源版本：`{proposal.source_revision}`",
        "- 生成时状态：`待人工批准`",
        "- 当前状态以 `status-card.md` 和 `change-contract.json` 为准。",
        "",
        "## 目标",
        "",
        f"- 原始要求：{proposal.user_goal}",
        f"- 建议合同目标：{contract.goal}",
        "",
        "## 验收条件",
        "",
        *[f"- {item}" for item in contract.acceptance],
        "",
        "## 必须保持",
        "",
        *[f"- {item}" for item in contract.invariants or ["无"]],
        "",
        "## 不做的事",
        "",
        *[f"- {item}" for item in contract.non_goals or ["无"]],
        "",
        "## 已确认事实",
        "",
        *[f"- {item}" for item in plan.observed_facts],
        f"- 来源详见 `{PLANNING_REPORT_ARTIFACT}`。",
        "",
        "## 根因假设",
        "",
        *[f"- {item}" for item in plan.hypotheses or ["无"]],
        "",
        "## 未决问题",
        "",
        *[f"- {item}" for item in plan.unresolved_decisions or ["无"]],
        "",
        "## 修改边界",
        "",
        "- 允许文件：",
        *[f"  - `{path}`" for path in contract.authority_envelope.allowed_paths],
        "- 禁止规则：",
        *(
            [
                f"  - `{path}`"
                for path in contract.authority_envelope.forbidden_paths
            ]
            if contract.authority_envelope.forbidden_paths
            else ["  - 无"]
        ),
        (
            "- 文件上限："
            f"`{contract.authority_envelope.max_changed_files or '未限制'}`"
        ),
        (
            "- 自动预算："
            f"repair={contract.authority_envelope.max_repair_rounds}，"
            f"replan={contract.authority_envelope.max_auto_replans}，"
            f"review={contract.authority_envelope.max_review_rounds}，"
            f"verification_retry="
            f"{contract.authority_envelope.max_verification_retries}"
        ),
        "",
        "## 合同验证",
        "",
        *[f"- `{command}`" for command in contract.required_verification],
        "",
        "## 额外检查",
        "",
        *[f"- `{command}`" for command in plan.additional_checks or ["无"]],
        "",
        "## 副作用授权",
        "",
        *_render_side_effect_policy(contract),
        "",
        "## 风险审查",
        "",
        (
            "- 合同声明："
            + (
                "、".join(
                    f"`{risk_id}`"
                    for risk_id in contract.authorized_risk_reviews
                )
                if contract.authorized_risk_reviews
                else "无"
            )
        ),
    ]
    if compiled.risk_hits:
        for risk_id, label, files in compiled.risk_hits:
            lines.append(
                f"- `{risk_id}` / {label}："
                + "、".join(f"`{path}`" for path in files)
            )
    else:
        lines.append("- 未命中 `.vega.yaml` 的 required_reviews。")
    if proposal.contract_proposal.authorized_risk_reviews:
        lines.extend(
            [
                "- Planner 风险提示（仅供人工阅读，不作为机器风险 ID）：",
                *[
                    f"  - {value}"
                    for value in proposal.contract_proposal.authorized_risk_reviews
                ],
            ]
        )
    lines.extend(["", "## 工作项", ""])
    for item in plan.work_items:
        lines.append(f"- `{item.work_item_id}`：{item.objective}")
        if item.depends_on:
            lines.append(
                "  - 依赖："
                + "、".join(f"`{work_item_id}`" for work_item_id in item.depends_on)
            )
        if item.likely_files:
            lines.append(
                "  - 候选文件："
                + "、".join(f"`{path}`" for path in item.likely_files)
            )
        if item.verification:
            lines.append(
                "  - 局部验证："
                + "；".join(f"`{command}`" for command in item.verification)
            )
        if item.risk_notes:
            lines.append("  - 风险：" + "；".join(item.risk_notes))
    lines.extend(
        [
            "",
            "> Contract Compiler 只完成确定性投影和边界检查。"
            "人工批准前不会启动 Worker。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_side_effect_policy(
    contract: ChangeContract,
) -> list[str]:
    labels = {
        "database_schema_change": "数据库结构变更",
        "public_api_change": "公共 API 变更",
        "new_dependency": "新增依赖",
        "deployment_action": "部署操作",
        "external_write_during_validation": "验证期间外部写入",
        "payment_or_funds_change": "支付或资金变更",
        "permission_change": "权限变更",
        "data_deletion": "数据删除",
    }
    policy = contract.side_effect_policy.model_dump(mode="json")
    authorized = [
        label
        for field, label in labels.items()
        if policy[field]
    ]
    if not authorized:
        return ["- 未授权上述高影响副作用。"]
    return ["- 已授权：" + "、".join(authorized)]


def _require_valid_config(config: ProjectConfig) -> None:
    errors = [
        issue
        for issue in validate_project_config(config)
        if issue.severity == "error"
    ]
    if errors:
        issue = errors[0]
        raise ValueError(f"project_config.{issue.code}：{issue.message}")
    if config.source_path is None:
        raise ValueError(
            "verification：固定 source revision 缺少 `.vega.yaml`；"
            "请先登记验证命令，或改用人工准备的显式 Contract"
        )


def _registered_verification_commands(config: ProjectConfig) -> dict[str, str]:
    commands = config.verification.commands or []
    registered: dict[str, str] = {}
    for command in commands:
        normalized = command.strip()
        if normalized in registered:
            raise ValueError("verification：`.vega.yaml` 包含重复验证命令")
        registered[normalized] = normalized
    if not registered:
        raise ValueError("verification：`.vega.yaml` 没有登记验证命令")
    return registered


def _compile_verification(
    proposal: PlanningProposal,
    registered: dict[str, str],
    config: ProjectConfig,
) -> list[str]:
    proposed = [
        *proposal.contract_proposal.verification_suggestions,
        *[
            command
            for item in proposal.execution_plan.work_items
            for command in item.verification
        ],
        *proposal.execution_plan.additional_check_suggestions,
    ]
    unknown = sorted(
        {
            command.strip()
            for command in proposed
            if command.strip() not in registered
        }
    )
    if unknown:
        raise ValueError(
            "verification：Proposal 包含未在 `.vega.yaml` 登记的命令："
            + "；".join(unknown)
        )
    required = list(
        dict.fromkeys(
            registered[command.strip()]
            for command in proposal.contract_proposal.verification_suggestions
        )
    )
    _validate_verification_budget(proposal, required, config)
    return required


def _validate_verification_budget(
    proposal: PlanningProposal,
    required: list[str],
    config: ProjectConfig,
) -> None:
    """按每次真实 Gate 会执行的命令数检查预算，而不是只看合同列表。"""

    items = proposal.execution_plan.work_items
    additional = proposal.execution_plan.additional_check_suggestions
    for index, item in enumerate(items):
        commands = [*item.verification]
        if index == len(items) - 1:
            commands.extend(required)
            commands.extend(additional)
        elif not commands:
            commands.extend(required)
        command_count = len(dict.fromkeys(commands))
        if command_count > config.verification.max_commands:
            raise ValueError(
                "verification："
                f"{item.work_item_id} 将执行 {command_count} 条命令，"
                "超过 `.vega.yaml` 的 max_commands"
            )


def _likely_files(proposal: PlanningProposal) -> list[str]:
    files = list(
        dict.fromkeys(
            path
            for item in proposal.execution_plan.work_items
            for path in item.likely_files
        )
    )
    if not files:
        raise ValueError("execution_plan.work_items.likely_files：至少需要一个候选文件")
    wildcard_paths = [
        path
        for path in files
        if any(character in path for character in ("*", "?", "["))
    ]
    if wildcard_paths:
        raise ValueError(
            "execution_plan.work_items.likely_files：必须列出具体文件，不能使用 glob："
            + "、".join(wildcard_paths)
        )
    return files


def _validate_scope(
    repo: Path,
    files: list[str],
    proposal: PlanningProposal,
    config: ProjectConfig,
) -> None:
    case_sensitive = not scope_paths_are_case_insensitive(repo)
    proposed = proposal.contract_proposal.authority_envelope
    for path in files:
        if any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in [
                *proposed.forbidden_paths,
                *config.scope.forbidden_paths,
            ]
        ):
            raise ValueError(f"scope：候选文件命中禁止范围：{path}")
        if not any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in proposed.allowed_paths
        ):
            raise ValueError(f"scope：候选文件越出 Proposal 建议范围：{path}")
        if config.scope.allowed_paths and not any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in config.scope.allowed_paths
        ):
            raise ValueError(f"scope：候选文件越出项目允许范围：{path}")


def _compile_risk_hits(
    repo: Path,
    config: ProjectConfig,
    files: list[str],
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """风险 ID 只由仓库策略和候选文件确定，不接受 Planner 自由命名。"""

    hits = match_required_reviews(repo, files, config.risk.required_reviews)
    return tuple(
        (hit.id, hit.label, tuple(hit.matched_files))
        for hit in hits
    )


def _compile_authority_envelope(
    proposal: PlanningProposal,
    config: ProjectConfig,
    files: list[str],
) -> ChangeAuthorityEnvelope:
    proposed = proposal.contract_proposal.authority_envelope
    configured_limit = config.budget.max_changed_files
    limits = [
        value
        for value in (proposed.max_changed_files, configured_limit)
        if value is not None
    ]
    max_changed_files = min(limits) if limits else len(files)
    if len(files) > max_changed_files:
        raise ValueError(
            "authority_envelope.max_changed_files："
            f"{len(files)} 个候选文件超过上限 {max_changed_files}"
        )
    if (
        config.budget.forbid_new_dependencies
        and proposal.contract_proposal.side_effect_policy.new_dependency
    ):
        raise ValueError(
            "side_effect_policy.new_dependency：项目策略禁止新增依赖"
        )
    return ChangeAuthorityEnvelope(
        allowed_paths=list(files),
        forbidden_paths=list(
            dict.fromkeys(
                [
                    *proposed.forbidden_paths,
                    *config.scope.forbidden_paths,
                ]
            )
        ),
        max_changed_files=max_changed_files,
        max_repair_rounds=proposed.max_repair_rounds,
        max_auto_replans=proposed.max_auto_replans,
        max_review_rounds=proposed.max_review_rounds,
        max_verification_retries=proposed.max_verification_retries,
    )
