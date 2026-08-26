from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from .agent_change_contract import ChangeContract
from .agent_change_run import load_change_run_context
from .agent_contract import (
    AgentDecision,
    AgentObservation,
    AgentPlan,
    AgentState,
    NonEmptyText,
    StrictAgentModel,
    utc_now,
)
from .agent_persistence import (
    load_agent_checkpoint,
    read_agent_trace,
)
from .redaction import write_redacted_json_once
from .review_contract import ReviewFinding
from .run_utils import resolve_run_dir


class ChangeBudgetSnapshot(StrictAgentModel):
    """当前 Work Item 的确定性预算计数。"""

    run_id: NonEmptyText
    work_item_id: NonEmptyText
    worker_attempts_used: int = Field(ge=0)
    repair_rounds_used: int = Field(ge=0)
    auto_replans_used: int = Field(ge=0)
    review_rounds_used: int = Field(ge=0)
    verification_retries_used: int = Field(ge=0)
    max_repair_rounds: int = Field(ge=0)
    max_auto_replans: int = Field(ge=0)
    max_review_rounds: int = Field(ge=1)
    max_verification_retries: int = Field(ge=0)


class ChangeFixPacket(StrictAgentModel):
    """把结构化失败事实交给下一 Writer，不复制 Reviewer 会话。"""

    schema_version: Literal[1] = 1
    packet_id: NonEmptyText
    run_id: NonEmptyText
    work_item_id: NonEmptyText
    contract_revision: int = Field(ge=1)
    execution_plan_revision: int = Field(ge=1)
    source_observation_id: NonEmptyText
    source_decision_id: NonEmptyText
    source_child_run: NonEmptyText
    source_operation_id: NonEmptyText
    reason: NonEmptyText
    machine_summary: NonEmptyText
    verification: NonEmptyText
    risk: NonEmptyText
    review: NonEmptyText
    changed_files: list[NonEmptyText] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    required_actions: list[NonEmptyText] = Field(min_length=1)
    source_artifacts: list[NonEmptyText] = Field(min_length=1)
    source_finish_sha256: NonEmptyText
    repair_round: int = Field(ge=1)
    remaining_repair_rounds: int = Field(ge=0)
    created_at: str = Field(default_factory=utc_now)


@dataclass(frozen=True)
class PreparedChangeDecision:
    decision: AgentDecision
    evidence_refs: tuple[str, ...] = ()
    fix_packet_ref: str | None = None

    @property
    def task_brief_refs(self) -> list[str] | None:
        return [self.fix_packet_ref] if self.fix_packet_ref is not None else None


def prepare_change_decision(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    observation: AgentObservation,
    decision: AgentDecision,
) -> PreparedChangeDecision:
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return PreparedChangeDecision(decision=decision)
    budget = change_budget_snapshot(run_dir, state, context.contract)
    guarded = guard_change_decision_budget(decision, budget)
    fix_packet_ref: str | None = None
    if guarded.selected_action == "repair":
        try:
            fix_packet_ref = write_change_fix_packet(
                workspace,
                run_dir,
                state,
                observation,
                guarded,
                budget,
            )
        except ValueError as exc:
            guarded = guarded.model_copy(
                update={
                    "allowed_actions": ["human"],
                    "selected_action": "human",
                    "reason": (
                        "无法生成可信 Fix Packet，自动 Repair 已停止："
                        f"{exc}"
                    ),
                }
            )
    budget_ref = write_change_budget_artifact(
        run_dir,
        guarded,
        budget,
    )
    refs = [budget_ref]
    if fix_packet_ref is not None:
        refs.append(fix_packet_ref)
    return PreparedChangeDecision(
        decision=guarded,
        evidence_refs=tuple(refs),
        fix_packet_ref=fix_packet_ref,
    )


def change_budget_snapshot(
    run_dir: Path,
    state: AgentState,
    contract: ChangeContract,
) -> ChangeBudgetSnapshot:
    if state.current_work_item is None:
        raise ValueError("ChangeRun 缺少当前 Work Item，无法计算预算")
    trace = read_agent_trace(run_dir / "trace.jsonl")
    attempts = sum(
        item.get("event") == "worker_dispatch_committed"
        and item.get("work_item") == state.current_work_item
        for item in trace
    )
    auto_replans = sum(
        item.get("event") == "change_execution_plan_auto_applied"
        for item in trace
    )
    verification_retries = sum(
        item.get("event") == "verification_retry_committed"
        and item.get("work_item") == state.current_work_item
        for item in trace
    )
    review_rounds = _review_rounds_for_work_item(
        run_dir,
        state.current_work_item,
        trace,
    )
    envelope = contract.authority_envelope
    return ChangeBudgetSnapshot(
        run_id=state.run_id,
        work_item_id=state.current_work_item,
        worker_attempts_used=attempts,
        repair_rounds_used=max(0, attempts - 1),
        auto_replans_used=auto_replans,
        review_rounds_used=review_rounds,
        verification_retries_used=verification_retries,
        max_repair_rounds=envelope.max_repair_rounds,
        max_auto_replans=envelope.max_auto_replans,
        max_review_rounds=envelope.max_review_rounds,
        max_verification_retries=envelope.max_verification_retries,
    )


def require_change_verification_retry_budget(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
) -> ChangeBudgetSnapshot | None:
    """ChangeRun 重跑门禁前先消费人工批准的验证重试预算。"""

    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return None
    budget = change_budget_snapshot(run_dir, state, context.contract)
    if budget.verification_retries_used >= budget.max_verification_retries:
        raise ValueError(
            "当前 Work Item 的验证重试预算已用完："
            f"{budget.verification_retries_used}/"
            f"{budget.max_verification_retries}"
        )
    return budget


def guard_change_decision_budget(
    decision: AgentDecision,
    budget: ChangeBudgetSnapshot,
) -> AgentDecision:
    """在发布 repair 前确认下一轮仍有 Worker 与 Review 预算。"""

    if decision.selected_action != "repair":
        return decision
    exhausted: list[str] = []
    if budget.repair_rounds_used >= budget.max_repair_rounds:
        exhausted.append(
            f"Repair {budget.repair_rounds_used}/{budget.max_repair_rounds}"
        )
    if budget.review_rounds_used >= budget.max_review_rounds:
        exhausted.append(
            f"Review {budget.review_rounds_used}/{budget.max_review_rounds}"
        )
    if not exhausted:
        return decision
    return decision.model_copy(
        update={
            "allowed_actions": ["human"],
            "selected_action": "human",
            "reason": (
                "当前 Work Item 已达到自动停止条件："
                + "，".join(exhausted)
                + "；需要人工决定修改合同、调整计划或停止任务"
            ),
        }
    )


def write_change_budget_artifact(
    run_dir: Path,
    decision: AgentDecision,
    budget: ChangeBudgetSnapshot,
) -> str:
    relative = f"budgets/{decision.decision_id}.json"
    write_redacted_json_once(
        run_dir / relative,
        budget.model_dump(mode="json"),
    )
    return relative


def write_change_fix_packet(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    observation: AgentObservation,
    decision: AgentDecision,
    budget: ChangeBudgetSnapshot,
) -> str:
    if (
        decision.selected_action != "repair"
        or observation.child_run is None
        or observation.operation_id is None
        or state.contract_revision is None
        or state.execution_plan_revision is None
    ):
        raise ValueError("只有绑定完整 ChangeRun 证据的 repair 可以生成 Fix Packet")
    child_ref, finish, finish_sha256 = _load_bound_finish(
        workspace,
        run_dir,
        observation,
    )
    findings = _finish_findings(finish)
    required_actions = [
        item.recommendation.strip() or item.title.strip()
        for item in findings
        if item.recommendation.strip() or item.title.strip()
    ]
    if not required_actions:
        required_actions = [decision.reason]
    repair_round = budget.repair_rounds_used + 1
    packet = ChangeFixPacket(
        packet_id=f"fix-{decision.decision_id}",
        run_id=state.run_id,
        work_item_id=state.current_work_item or "",
        contract_revision=state.contract_revision,
        execution_plan_revision=state.execution_plan_revision,
        source_observation_id=observation.observation_id,
        source_decision_id=decision.decision_id,
        source_child_run=observation.child_run,
        source_operation_id=observation.operation_id,
        reason=decision.reason,
        machine_summary=observation.machine_summary,
        verification=observation.verification,
        risk=observation.risk,
        review=observation.review,
        changed_files=list(observation.changed_files),
        findings=findings,
        required_actions=list(dict.fromkeys(required_actions)),
        source_artifacts=[
            f"observations/{observation.observation_id}.json",
            f"decisions/{decision.decision_id}.json",
            child_ref,
        ],
        source_finish_sha256=finish_sha256,
        repair_round=repair_round,
        remaining_repair_rounds=max(
            0,
            budget.max_repair_rounds - repair_round,
        ),
    )
    relative = f"fix-packets/{packet.packet_id}.json"
    write_redacted_json_once(
        run_dir / relative,
        packet.model_dump(mode="json"),
    )
    return relative


def load_current_fix_packet(
    run_dir: Path,
    state: AgentState,
) -> ChangeFixPacket:
    if state.latest_checkpoint_id is None or state.current_work_item is None:
        raise ValueError("当前 repair 缺少 Checkpoint 或 Work Item")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    refs = [
        ref for ref in checkpoint.evidence_refs if ref.startswith("fix-packets/")
    ]
    if len(refs) != 1:
        raise ValueError("当前 repair Checkpoint 没有唯一 Fix Packet")
    try:
        packet = ChangeFixPacket.model_validate_json(
            (run_dir / refs[0]).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("当前 Fix Packet 无法解析") from exc
    if (
        packet.run_id != state.run_id
        or packet.work_item_id != state.current_work_item
        or packet.contract_revision != state.contract_revision
        or packet.execution_plan_revision != state.execution_plan_revision
        or packet.source_child_run not in checkpoint.failed_attempts
    ):
        raise ValueError("当前 Fix Packet 与 repair Checkpoint 不一致")
    return packet


def render_fix_packet(packet: ChangeFixPacket) -> str:
    lines = [
        "## 当前 Fix Packet",
        "",
        f"- Work Item：`{packet.work_item_id}`",
        f"- Repair 轮次：`{packet.repair_round}`",
        f"- 原因：{packet.reason}",
        (
            "- 门禁："
            f"Verification=`{packet.verification}`，"
            f"Risk=`{packet.risk}`，Review=`{packet.review}`"
        ),
        "",
        "### 必须处理",
        "",
        *[f"- {item}" for item in packet.required_actions],
    ]
    if packet.findings:
        lines.extend(["", "### Reviewer Findings", ""])
        for finding in packet.findings:
            location = (
                f"`{finding.file}:{finding.line}`"
                if finding.file
                else "未绑定代码位置"
            )
            lines.append(
                f"- [{finding.severity}] {location} {finding.title}"
            )
    lines.extend(
        [
            "",
            "只处理当前 Fix Packet 和已批准合同内的问题。"
            "如果需要改变合同字段或风险边界，停止写入并请求 Replan。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _review_rounds_for_work_item(
    run_dir: Path,
    work_item_id: str,
    trace: list[dict[str, object]],
) -> int:
    refs = list(
        dict.fromkeys(
            ref
            for item in trace
            if item.get("work_item") == work_item_id
            for ref in item.get("artifact_refs", [])
            if isinstance(ref, str) and ref.startswith("observations/")
        )
    )
    total = 0
    observations_root = (run_dir / "observations").resolve()
    for ref in refs:
        path = (run_dir / ref).resolve()
        if not path.is_relative_to(observations_root):
            raise ValueError("Review 预算引用越出 observations 目录")
        try:
            observation = AgentObservation.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise ValueError("Review 预算引用的 Observation 无法验证") from exc
        if (
            path.name != f"{observation.observation_id}.json"
            or observation.work_item_id != work_item_id
        ):
            raise ValueError("Review 预算引用的 Observation 身份不一致")
        if observation.review != "not_run":
            total += 1
    return total


def _load_bound_finish(
    workspace: Path,
    run_dir: Path,
    observation: AgentObservation,
) -> tuple[str, dict[str, object], str]:
    child_refs = [
        ref for ref in observation.evidence_refs if ref.startswith("children/")
    ]
    if len(child_refs) != 1:
        raise ValueError("repair Observation 没有唯一 child 摘要")
    child_ref = child_refs[0]
    child_path = run_dir / child_ref
    expected_child_sha = observation.evidence_sha256.get(child_ref)
    try:
        child_bytes = child_path.read_bytes()
        child_summary = json.loads(child_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 repair child 摘要") from exc
    if (
        expected_child_sha is None
        or hashlib.sha256(child_bytes).hexdigest() != expected_child_sha
        or not isinstance(child_summary, dict)
        or child_summary.get("child_run") != observation.child_run
        or child_summary.get("operation_id") != observation.operation_id
    ):
        raise ValueError("repair child 摘要与 Observation 不一致")
    core = child_summary.get("core")
    finish_sha256 = core.get("finish_sha256") if isinstance(core, dict) else None
    if not isinstance(finish_sha256, str):
        raise ValueError("repair child 摘要没有绑定 Finish")
    finish_path = (
        resolve_run_dir(workspace, observation.child_run or "")
        / "finish-summary.json"
    )
    try:
        finish_bytes = finish_path.read_bytes()
        finish = json.loads(finish_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 repair child Finish") from exc
    if (
        hashlib.sha256(finish_bytes).hexdigest() != finish_sha256
        or not isinstance(finish, dict)
        or finish.get("run_id") != observation.child_run
    ):
        raise ValueError("repair child Finish 已变化或身份不一致")
    return child_ref, finish, finish_sha256


def _finish_findings(finish: dict[str, object]) -> list[ReviewFinding]:
    first_screen = finish.get("first_screen")
    review = (
        first_screen.get("review")
        if isinstance(first_screen, dict)
        else None
    )
    values = review.get("findings") if isinstance(review, dict) else None
    if not isinstance(values, list):
        return []
    findings: list[ReviewFinding] = []
    for value in values:
        try:
            findings.append(ReviewFinding.model_validate(value))
        except ValidationError as exc:
            raise ValueError("Finish 中的 Reviewer finding 无法解析") from exc
    return findings
