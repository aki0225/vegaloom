from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .agent_approval_policy import validate_bounded_approval_freshness
from .agent_change_contract import (
    ChangeContract,
    ExecutionPlan,
    ExecutionWorkItem,
    validate_execution_plan_against_contract,
)
from .agent_contract import (
    AgentPlan,
    AgentState,
    AgentWorkItem,
    canonical_digest,
)
from .agent_git_candidate import CandidateCommit
from .agent_git_worktree import ManagedChangeWorktree
from .redaction import write_redacted_json, write_redacted_json_once


CHANGE_CONTRACT_ARTIFACT = "change-contract.json"
EXECUTION_PLAN_ARTIFACT = "execution-plan.json"


@dataclass(frozen=True)
class ChangeRunContext:
    contract: ChangeContract
    execution_plan: ExecutionPlan
    worktree: ManagedChangeWorktree


def project_agent_plan(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    *,
    current: AgentPlan | None = None,
) -> AgentPlan:
    """把新合同投影到既有 Core 需要的窄 Plan，不赋予第二套成功语义。"""

    validate_execution_plan_against_contract(contract, execution_plan)
    statuses = {
        item.work_item_id: item.status
        for item in current.work_items
    } if current is not None else {}
    work_items = [
        _project_work_item(
            contract,
            execution_plan,
            item,
            status=statuses.get(item.work_item_id, "pending"),
        )
        for item in execution_plan.work_items
    ]
    payload: dict[str, object] = {
        "task_id": contract.task_id,
        "goal_revision": contract.contract_revision,
        "plan_revision": execution_plan.plan_revision,
        "user_goal": contract.goal,
        "non_goals": list(contract.non_goals),
        "success_conditions": [
            *contract.acceptance,
            *[f"保持不变量：{item}" for item in contract.invariants],
        ],
        "observed_facts": list(execution_plan.observed_facts),
        "hypotheses": list(execution_plan.hypotheses),
        "unresolved_decisions": list(execution_plan.unresolved_decisions),
        "work_items": [
            item.model_dump(mode="json")
            for item in work_items
        ],
        "approved": False,
        "approved_at": None,
        "approved_by": None,
        "approved_digest": None,
    }
    projected = AgentPlan.model_validate(payload)
    if not contract.approval_is_current():
        return projected
    approved_payload = projected.model_dump(mode="json")
    approved_payload.update(
        {
            "approved": True,
            "approved_at": contract.approved_at,
            "approved_by": contract.approved_by,
            "approved_digest": canonical_digest(projected.content_for_approval()),
        }
    )
    return AgentPlan.model_validate(approved_payload)


def validate_change_projection(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    plan: AgentPlan,
) -> None:
    expected = project_agent_plan(
        contract,
        execution_plan,
        current=plan,
    )
    if expected.model_dump(mode="json") != plan.model_dump(mode="json"):
        raise ValueError("agent-plan.json 不再是当前 ChangeRun 合同的有效投影")


def current_change_work_item(
    plan: AgentPlan,
    state: AgentState,
) -> AgentWorkItem:
    if state.run_kind != "change" or state.current_work_item is None:
        raise ValueError("当前 run 不是可执行 ChangeRun")
    matches = [
        item
        for item in plan.work_items
        if item.work_item_id == state.current_work_item
    ]
    if len(matches) != 1:
        raise ValueError("ChangeRun 当前 Work Item 与投影 Plan 不一致")
    work_item = matches[0]
    if work_item.status not in {"pending", "active"}:
        raise ValueError("ChangeRun 当前 Work Item 已不是可执行状态")
    completed = {
        item.work_item_id
        for item in plan.work_items
        if item.status in {"completed", "superseded"}
    }
    missing = set(work_item.depends_on) - completed
    if missing:
        raise ValueError(
            f"ChangeRun 当前 Work Item 仍有未完成依赖：{sorted(missing)}"
        )
    return work_item


def save_change_run_artifacts(
    run_dir: Path,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> None:
    validate_execution_plan_against_contract(contract, execution_plan)
    write_redacted_json(
        run_dir / CHANGE_CONTRACT_ARTIFACT,
        contract.model_dump(mode="json"),
    )
    write_redacted_json(
        run_dir / EXECUTION_PLAN_ARTIFACT,
        execution_plan.model_dump(mode="json"),
    )


def task_brief_worker_verification(
    run_dir: Path,
    state: AgentState,
) -> tuple[str, ...] | None:
    """返回 Execution Plan 的局部自检；Core Gate 仍使用投影 Plan 的完整命令。"""

    if state.run_kind != "change":
        return None
    try:
        execution_plan = ExecutionPlan.model_validate_json(
            (run_dir / EXECUTION_PLAN_ARTIFACT).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("ChangeRun Execution Plan 无法用于 Task Brief") from exc
    if (
        execution_plan.task_id != state.task_id
        or execution_plan.plan_revision != state.execution_plan_revision
        or state.current_work_item is None
    ):
        raise ValueError("Task Brief 与当前 Execution Plan revision 不一致")
    matches = [
        item
        for item in execution_plan.work_items
        if item.work_item_id == state.current_work_item
    ]
    if len(matches) != 1:
        raise ValueError("Task Brief 当前 Work Item 不属于 Execution Plan")
    return tuple(matches[0].verification)


def write_candidate_artifact(
    run_dir: Path,
    candidate: CandidateCommit,
) -> str:
    if candidate.operation_id is None:
        raise ValueError("ChangeRun Candidate 缺少 operation_id")
    relative = f"candidates/{candidate.operation_id}.json"
    write_redacted_json_once(
        run_dir / relative,
        candidate.model_dump(mode="json"),
    )
    return relative


def load_candidate_artifact(
    run_dir: Path,
    relative: str,
) -> CandidateCommit:
    path = (run_dir / relative).resolve(strict=True)
    root = run_dir.resolve(strict=True)
    if (
        not path.is_relative_to(root)
        or path.parent != root / "candidates"
        or not path.is_file()
    ):
        raise ValueError("Candidate Artifact 路径无效")
    try:
        return CandidateCommit.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("Candidate Artifact 无法解析") from exc


def load_change_run_context(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
) -> ChangeRunContext | None:
    if state.run_kind == "legacy":
        return None
    if state.contract_revision is None or state.execution_plan_revision is None:
        if state.phase not in {"planning", "needs_human", "stopped"}:
            raise ValueError("ChangeRun 在可执行阶段缺少合同或执行计划")
        return None
    try:
        contract = ChangeContract.model_validate_json(
            (run_dir / CHANGE_CONTRACT_ARTIFACT).read_text(encoding="utf-8")
        )
        execution_plan = ExecutionPlan.model_validate_json(
            (run_dir / EXECUTION_PLAN_ARTIFACT).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("ChangeRun 合同或执行计划无法恢复") from exc
    if (
        contract.task_id != state.task_id
        or contract.contract_revision != state.contract_revision
        or execution_plan.plan_revision != state.execution_plan_revision
        or execution_plan.contract_revision != contract.contract_revision
    ):
        raise ValueError("ChangeRun State 与合同或执行计划 revision 不一致")
    if state.phase not in {"planning", "awaiting_approval"} and (
        not contract.approval_is_current()
        or state.approved_contract_digest != contract.approved_digest
    ):
        raise ValueError("ChangeRun Approved Contract 已过期")
    validate_change_projection(contract, execution_plan, plan)
    worktree = managed_worktree_from_metadata(metadata)
    if worktree.run_id != state.run_id:
        raise ValueError("ChangeRun Worktree 与 State run_id 不一致")
    if state.phase not in {"planning", "awaiting_approval"}:
        validate_bounded_approval_freshness(
            worktree.worktree_path,
            contract,
            execution_plan,
        )
    return ChangeRunContext(
        contract=contract,
        execution_plan=execution_plan,
        worktree=worktree,
    )


def managed_worktree_from_metadata(
    metadata: dict[str, object],
) -> ManagedChangeWorktree:
    raw = metadata.get("change_run")
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("ChangeRun metadata 缺失或版本不受支持")
    required = {
        "run_id",
        "source_repo_path",
        "worktree_path",
        "branch",
        "base_revision",
    }
    if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
        raise ValueError("ChangeRun metadata 字段不完整")
    return ManagedChangeWorktree(
        run_id=str(raw["run_id"]),
        source_repo=Path(str(raw["source_repo_path"])).resolve(),
        worktree_path=Path(str(raw["worktree_path"])).resolve(),
        branch=str(raw["branch"]),
        base_sha=str(raw["base_revision"]),
    )


def change_run_metadata(
    handle: ManagedChangeWorktree,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": handle.run_id,
        "source_repo_path": str(handle.source_repo),
        "worktree_path": str(handle.worktree_path),
        "branch": handle.branch,
        "base_revision": handle.base_sha,
    }


def change_worktree_root(workspace: Path, source_repo: Path) -> Path:
    """选择仓库外的稳定根目录，避免把 Worktree 放进用户当前工作区。"""

    workspace_root = workspace.resolve()
    repo_root = source_repo.resolve()
    direct = workspace_root / ".vega-worktrees"
    if direct != repo_root and not direct.is_relative_to(repo_root):
        return direct
    digest = canonical_digest({"repo": os.path.normcase(str(repo_root))})[:16]
    return workspace_root.parent / ".vega-worktrees" / digest


def _project_work_item(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    item: ExecutionWorkItem,
    *,
    status: str,
) -> AgentWorkItem:
    is_final_work_item = item.work_item_id == execution_plan.work_items[-1].work_item_id
    verification_sources = [*item.verification]
    if is_final_work_item:
        verification_sources.extend(contract.required_verification)
        verification_sources.extend(execution_plan.additional_checks)
    elif not verification_sources:
        # 中间项没有局部验证时保守回退到合同命令，避免无验证推进。
        verification_sources.extend(contract.required_verification)
    verification = list(
        dict.fromkeys(verification_sources)
    )
    external_side_effects = (
        "known"
        if contract.side_effect_policy.external_write_during_validation
        or contract.side_effect_policy.deployment_action
        else "none"
    )
    return AgentWorkItem(
        work_item_id=item.work_item_id,
        objective=item.objective,
        # Contract 约束整个 ChangeRun；当前 Work Item 只应修改自己在
        # Execution Plan 中声明的文件。否则每个 Work Item 都会继承全局并集，
        # 已接受项与后续 Repair 的 WIP 将无法可靠归因。
        allowed_paths=list(item.likely_files),
        forbidden_paths=list(contract.authority_envelope.forbidden_paths),
        verification=verification,
        external_side_effects=external_side_effects,
        risk_notes=list(item.risk_notes),
        depends_on=list(item.depends_on),
        status=status,
    )
