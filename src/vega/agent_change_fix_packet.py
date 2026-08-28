from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationError

from .agent_contract import (
    AgentDecision,
    AgentObservation,
    AgentState,
    NonEmptyText,
    StrictAgentModel,
    utc_now,
)
from .agent_persistence import load_agent_checkpoint
from .redaction import write_redacted_json_once
from .review_contract import ReviewFinding
from .run_utils import resolve_run_dir

if TYPE_CHECKING:
    from .agent_change_control import ChangeBudgetSnapshot


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
    findings = _observation_findings(run_dir, observation, finish)
    integration_refs = _integration_review_refs(observation)
    required_actions = _fix_required_actions(findings, decision.reason)
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
        required_actions=required_actions,
        source_artifacts=[
            f"observations/{observation.observation_id}.json",
            f"decisions/{decision.decision_id}.json",
            child_ref,
            *integration_refs,
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
    workspace: Path,
    run_dir: Path,
    state: AgentState,
) -> ChangeFixPacket:
    if state.latest_checkpoint_id is None or state.current_work_item is None:
        raise ValueError("当前 repair 缺少 Checkpoint 或 Work Item")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (
        state.phase != "ready"
        or "repair" not in state.allowed_actions
        or checkpoint.run_id != state.run_id
        or checkpoint.state_version + 1 != state.state_version
        or checkpoint.phase != "ready"
        or checkpoint.status != "safe"
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.active_child_run is not None
        or checkpoint.operation_started
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.pending_actions != state.allowed_actions
    ):
        raise ValueError("当前 repair State 与 Checkpoint 不一致")
    packet_refs = [
        ref for ref in checkpoint.evidence_refs if ref.startswith("fix-packets/")
    ]
    observation_refs = [
        ref for ref in checkpoint.evidence_refs if ref.startswith("observations/")
    ]
    decision_refs = [
        ref for ref in checkpoint.evidence_refs if ref.startswith("decisions/")
    ]
    if len(packet_refs) != 1:
        raise ValueError("当前 repair Checkpoint 没有唯一 Fix Packet")
    if len(observation_refs) != 1 or len(decision_refs) != 1:
        raise ValueError("当前 repair Checkpoint 没有唯一来源证据")
    packet_ref = packet_refs[0]
    observation_ref = observation_refs[0]
    decision_ref = decision_refs[0]
    try:
        packet = ChangeFixPacket.model_validate_json(
            (run_dir / packet_ref).read_text(encoding="utf-8")
        )
        observation = AgentObservation.model_validate_json(
            (run_dir / observation_ref).read_text(encoding="utf-8")
        )
        decision = AgentDecision.model_validate_json(
            (run_dir / decision_ref).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("当前 Fix Packet 或来源证据无法解析") from exc
    if (
        packet_ref != f"fix-packets/{packet.packet_id}.json"
        or observation_ref
        != f"observations/{observation.observation_id}.json"
        or decision_ref != f"decisions/{decision.decision_id}.json"
        or decision.observation_id != observation.observation_id
        or decision.selected_action != "repair"
        or decision.source != "deterministic"
        or decision.allowed_actions != checkpoint.pending_actions
        or observation.authority != "machine_reconcile"
        or observation.work_item_id != state.current_work_item
        or observation.child_run is None
        or observation.operation_id is None
        or observation.worker_alive
        or observation.work_item_completed
        or not observation.repairable_in_scope
        or checkpoint.failed_attempts != [observation.child_run]
    ):
        raise ValueError("当前 repair 来源证据与 Checkpoint 不一致")
    child_ref, finish, finish_sha256 = _load_bound_finish(
        workspace,
        run_dir,
        observation,
    )
    findings = _observation_findings(run_dir, observation, finish)
    integration_refs = _integration_review_refs(observation)
    required_actions = _fix_required_actions(findings, decision.reason)
    if (
        packet.run_id != state.run_id
        or packet.work_item_id != state.current_work_item
        or packet.contract_revision != state.contract_revision
        or packet.execution_plan_revision != state.execution_plan_revision
        or packet.source_observation_id != observation.observation_id
        or packet.source_decision_id != decision.decision_id
        or packet.source_child_run != observation.child_run
        or packet.source_operation_id != observation.operation_id
        or packet.reason != decision.reason
        or packet.machine_summary != observation.machine_summary
        or packet.verification != observation.verification
        or packet.risk != observation.risk
        or packet.review != observation.review
        or packet.changed_files != observation.changed_files
        or packet.findings != findings
        or packet.required_actions != required_actions
        or packet.source_artifacts
        != [observation_ref, decision_ref, child_ref, *integration_refs]
        or packet.source_finish_sha256 != finish_sha256
    ):
        raise ValueError("当前 Fix Packet 与来源证据不一致")
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
            lines.append(f"- [{finding.severity}] {location} {finding.title}")
    lines.extend(
        [
            "",
            "只处理当前 Fix Packet 和已批准合同内的问题。"
            "如果需要改变合同字段或风险边界，停止写入并请求 Replan。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


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


def _observation_findings(
    run_dir: Path,
    observation: AgentObservation,
    finish: dict[str, object],
) -> list[ReviewFinding]:
    findings = _finish_findings(finish)
    for ref in _integration_review_refs(observation):
        path = run_dir / ref
        expected = observation.evidence_sha256.get(ref)
        try:
            content = path.read_bytes()
            payload = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("最终集成 Reviewer Artifact 无法读取") from exc
        if (
            expected is None
            or hashlib.sha256(content).hexdigest() != expected
            or not isinstance(payload, dict)
            or payload.get("run_id") != run_dir.name
            or payload.get("status") != "request_changes"
        ):
            raise ValueError("最终集成 Reviewer Artifact 与 Observation 不一致")
        batches = payload.get("batches")
        if not isinstance(batches, list):
            raise ValueError("最终集成 Reviewer Artifact 缺少批次")
        for batch in batches:
            verdict = batch.get("verdict") if isinstance(batch, dict) else None
            values = verdict.get("findings") if isinstance(verdict, dict) else None
            if not isinstance(values, list):
                continue
            for value in values:
                try:
                    findings.append(ReviewFinding.model_validate(value))
                except ValidationError as exc:
                    raise ValueError("最终集成 Reviewer finding 无法解析") from exc
    return findings


def _integration_review_refs(observation: AgentObservation) -> list[str]:
    refs = [
        ref
        for ref in observation.evidence_refs
        if ref.startswith("integration-reviews/")
    ]
    if len(refs) > 1:
        raise ValueError("Observation 绑定了多个最终集成 Reviewer Artifact")
    return refs


def _fix_required_actions(
    findings: list[ReviewFinding],
    fallback: str,
) -> list[str]:
    actions = [
        item.recommendation.strip() or item.title.strip()
        for item in findings
        if item.recommendation.strip() or item.title.strip()
    ]
    return list(dict.fromkeys(actions or [fallback]))
