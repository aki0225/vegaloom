from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .approval_policy_config import bounded_approval_policy_digest
from .agent_change_contract import ChangeContract, ExecutionPlan
from .project_config import (
    ProjectConfig,
    load_project_config,
    validate_project_config,
)
from .risk_review import match_required_reviews
from .scope_path_matching import (
    path_matches_pattern,
    scope_paths_are_case_insensitive,
)


@dataclass(frozen=True)
class BoundedApprovalDecision:
    eligible: bool
    policy_id: str | None
    policy_digest: str | None
    policy_revision: str
    contract_digest: str
    reasons: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.eligible:
            return f"策略 `{self.policy_id}` 满足 bounded 自动批准条件"
        return "；".join(self.reasons)


def evaluate_bounded_approval(
    repo: Path,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    config: ProjectConfig,
    *,
    policy_revision: str,
) -> BoundedApprovalDecision:
    """只用冻结合同和仓库策略判断是否允许自动批准。"""

    policy = config.approval.bounded
    reasons = _configuration_reasons(config)
    contract_files, file_reasons = _candidate_file_reasons(
        contract,
        execution_plan,
    )
    reasons.extend(file_reasons)
    if policy.enabled and policy.allowed_paths:
        reasons.extend(
            _scope_reasons(
                repo,
                contract_files,
                policy_patterns=policy.allowed_paths,
                config=config,
                contract=contract,
            )
        )
    reasons.extend(_contract_reasons(contract, execution_plan))
    reasons.extend(_risk_reasons(repo, contract_files, config))
    reasons.extend(_verification_reasons(contract, execution_plan, config))
    reasons.extend(_budget_reasons(contract, execution_plan, config))

    policy_digest = (
        bounded_approval_policy_digest(config)
        if config.source_path is not None
        else None
    )
    return BoundedApprovalDecision(
        eligible=not reasons,
        policy_id=policy.policy_id,
        policy_digest=policy_digest,
        policy_revision=policy_revision,
        contract_digest=contract.expected_approval_digest(),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _configuration_reasons(config: ProjectConfig) -> list[str]:
    reasons: list[str] = []
    config_errors = [
        issue.message
        for issue in validate_project_config(config)
        if issue.severity == "error"
    ]
    if config_errors:
        reasons.append("项目配置无效：" + config_errors[0])
    if config.source_path is None:
        reasons.append("固定 revision 缺少 `.vega.yaml`")
    if not config.approval.bounded.enabled:
        reasons.append("仓库未启用 `approval.bounded`")
    return reasons


def _candidate_file_reasons(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    contract_files = list(contract.authority_envelope.allowed_paths)
    if any(_has_wildcard(path) for path in contract_files):
        reasons.append("bounded 批准只接受具体文件，Contract 仍包含 glob")

    work_item_files: list[str] = []
    for item in execution_plan.work_items:
        if not item.likely_files:
            reasons.append(f"{item.work_item_id} 没有明确候选文件")
            continue
        wildcard_files = [path for path in item.likely_files if _has_wildcard(path)]
        if wildcard_files:
            reasons.append(
                f"{item.work_item_id} 的候选文件仍包含 glob："
                + "、".join(wildcard_files)
            )
        work_item_files.extend(item.likely_files)
    if set(work_item_files) != set(contract_files):
        reasons.append("Work Item 候选文件与 Contract 精确允许文件不一致")
    return contract_files, reasons


def _contract_reasons(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> list[str]:
    reasons: list[str] = []
    if execution_plan.unresolved_decisions:
        reasons.append("Execution Plan 仍有未解决决策")
    enabled_side_effects = [
        name
        for name, enabled in contract.side_effect_policy.model_dump(
            mode="json",
            exclude={"schema_version"},
        ).items()
        if enabled
    ]
    if enabled_side_effects:
        reasons.append(
            "Contract 声明了 bounded 模式不允许的副作用："
            + "、".join(enabled_side_effects)
        )

    if contract.authorized_risk_reviews:
        reasons.append(
            "Contract 声明了必须人工检查的风险领域："
            + "、".join(contract.authorized_risk_reviews)
        )
    return reasons


def validate_bounded_approval_freshness(
    repo: Path,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> None:
    """每次恢复可执行上下文时重新验证 bounded 策略。"""

    if contract.approval_source != "bounded":
        return
    if contract.approval_policy_revision is None:
        raise ValueError("bounded 批准缺少策略 revision")
    try:
        config = load_project_config(repo)
    except (OSError, ValueError) as exc:
        raise ValueError("bounded 批准绑定的项目策略无法读取") from exc
    decision = evaluate_bounded_approval(
        repo,
        contract,
        execution_plan,
        config,
        policy_revision=contract.approval_policy_revision,
    )
    if (
        not decision.eligible
        or decision.policy_id != contract.approval_policy_id
        or decision.policy_digest != contract.approval_policy_digest
        or decision.contract_digest != contract.approved_digest
    ):
        raise ValueError(
            "bounded 批准已过期："
            + (
                decision.summary
                if not decision.eligible
                else "策略或 Contract 摘要与批准记录不一致"
            )
        )


def _scope_reasons(
    repo: Path,
    files: list[str],
    *,
    policy_patterns: list[str],
    config: ProjectConfig,
    contract: ChangeContract,
) -> list[str]:
    case_sensitive = not scope_paths_are_case_insensitive(repo)
    reasons: list[str] = []
    forbidden = [
        *config.scope.forbidden_paths,
        *contract.authority_envelope.forbidden_paths,
    ]
    for path in files:
        if any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in forbidden
        ):
            reasons.append(f"文件命中禁止范围：{path}")
            continue
        if not any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in policy_patterns
        ):
            reasons.append(f"文件越出 bounded 策略范围：{path}")
        if config.scope.allowed_paths and not any(
            path_matches_pattern(
                path,
                pattern,
                case_sensitive=case_sensitive,
            )
            for pattern in config.scope.allowed_paths
        ):
            reasons.append(f"文件越出项目 scope：{path}")
    return reasons


def _risk_reasons(
    repo: Path,
    files: list[str],
    config: ProjectConfig,
) -> list[str]:
    if not files:
        return []
    required = match_required_reviews(
        repo,
        files,
        config.risk.required_reviews,
    )
    if required:
        return [
            "候选文件命中 risk.required_reviews："
            + "、".join(sorted(item.id for item in required))
        ]
    case_sensitive = not scope_paths_are_case_insensitive(repo)
    for label, patterns in (
        ("risk.high_paths", config.risk.high_paths),
        ("risk.medium_paths", config.risk.medium_paths),
        ("risk.require_human_review", config.risk.require_human_review),
    ):
        matched = [
            path
            for path in files
            if any(
                path_matches_pattern(
                    path,
                    pattern,
                    case_sensitive=case_sensitive,
                )
                for pattern in patterns
            )
        ]
        if matched:
            return [f"候选文件命中 {label}：" + "、".join(matched)]
    return []


def _verification_reasons(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    config: ProjectConfig,
) -> list[str]:
    registered = {
        command.strip()
        for command in config.verification.commands or []
        if command.strip()
    }
    if not registered:
        return ["`.vega.yaml` 没有登记验证命令"]
    proposed = [
        *contract.required_verification,
        *[
            command
            for item in execution_plan.work_items
            for command in item.verification
        ],
        *execution_plan.additional_checks,
    ]
    unknown = sorted(
        {
            command.strip()
            for command in proposed
            if command.strip() not in registered
        }
    )
    reasons = (
        ["存在未登记验证命令：" + "；".join(unknown)]
        if unknown
        else []
    )
    for index, item in enumerate(execution_plan.work_items):
        commands = list(item.verification)
        if index == len(execution_plan.work_items) - 1:
            commands.extend(contract.required_verification)
            commands.extend(execution_plan.additional_checks)
        elif not commands:
            commands.extend(contract.required_verification)
        if len(dict.fromkeys(commands)) > config.verification.max_commands:
            reasons.append(
                f"{item.work_item_id} 的验证命令超过项目 max_commands"
            )
    return reasons


def _budget_reasons(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    config: ProjectConfig,
) -> list[str]:
    policy = config.approval.bounded
    if not policy.enabled:
        return []
    assert policy.max_changed_files is not None
    assert policy.max_work_items is not None
    assert policy.max_repair_rounds is not None
    assert policy.max_auto_replans is not None
    envelope = contract.authority_envelope
    reasons: list[str] = []
    if envelope.max_changed_files is None:
        reasons.append("Contract 没有明确 max_changed_files")
    elif envelope.max_changed_files > policy.max_changed_files:
        reasons.append("Contract 文件预算超过 bounded 策略")
    if len(envelope.allowed_paths) > policy.max_changed_files:
        reasons.append("Contract 精确允许文件数超过 bounded 策略")
    if (
        config.budget.max_changed_files is not None
        and len(envelope.allowed_paths) > config.budget.max_changed_files
    ):
        reasons.append("Contract 精确允许文件数超过项目预算")
    if len(execution_plan.work_items) > policy.max_work_items:
        reasons.append("Work Item 数量超过 bounded 策略")
    if envelope.max_repair_rounds > policy.max_repair_rounds:
        reasons.append("Repair 预算超过 bounded 策略")
    if envelope.max_auto_replans > policy.max_auto_replans:
        reasons.append("Replan 预算超过 bounded 策略")
    return reasons


def _has_wildcard(path: str) -> bool:
    return any(character in path for character in ("*", "?", "["))
