from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import Field, field_validator, model_validator

from .agent_change_contract import (
    ChangeContract,
    ExecutionPlan,
    validate_execution_plan_against_contract,
)
from .agent_contract import (
    AgentAction,
    AgentPlan,
    GitOidText,
    NonEmptyText,
    RelativePathText,
    Sha256Text,
    StrictAgentModel,
    canonical_digest,
)
from .agent_handoff_digest import WorkspaceDigestKind
from .agent_handoff_safety import TaskCardError, assert_portable_task_card_payload
from .agent_planning import PlanningProposal
from .redaction import redact_value


_MACHINE_STATE_START = "<!-- vega-task-card-state:v1"
_MACHINE_STATE_END = "-->"
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TERMINAL_TASK_STATUSES = frozenset({"completed", "stopped"})


class HistoricalGateEvidence(StrictAgentModel):
    gate: Literal["verification", "risk", "review"]
    status: Literal["passed", "failed", "blocked", "stale", "not_run"]
    source_revision: NonEmptyText
    recorded_at: NonEmptyText
    artifact_refs: list[RelativePathText] = Field(default_factory=list)
    historical: bool = True

    @field_validator("artifact_refs")
    @classmethod
    def validate_refs(cls, values: list[str]) -> list[str]:
        return _normalize_relative_paths(values)

    @field_validator("historical")
    @classmethod
    def require_historical(cls, value: bool) -> bool:
        if not value:
            raise ValueError("跨机器交接中的旧门禁只能记录为 historical")
        return value


class ChangeRunResume(StrictAgentModel):
    """跨机器恢复 ChangeRun 所需的最小批准合同，不携带本机路径。"""

    contract: ChangeContract
    execution_plan: ExecutionPlan
    accepted_checkpoint_sha: GitOidText
    historical_candidate_sha: GitOidText | None = None

    @model_validator(mode="after")
    def validate_change_run(self) -> ChangeRunResume:
        validate_execution_plan_against_contract(
            self.contract,
            self.execution_plan,
        )
        if not self.contract.approval_is_current():
            raise ValueError("ChangeRun Resume 必须携带当前已批准 Contract")
        if self.historical_candidate_sha == self.accepted_checkpoint_sha:
            raise ValueError("历史 Candidate 不能与 Accepted Checkpoint 相同")
        return self


class PlanningRunResume(StrictAgentModel):
    """跨机器恢复未编译 Planning ChangeRun 所需的只读提案。"""

    source_revision: GitOidText
    proposal: PlanningProposal

    @model_validator(mode="after")
    def validate_planning_run(self) -> PlanningRunResume:
        if self.proposal.source_revision != self.source_revision:
            raise ValueError("Planning Resume 与 Proposal 的 source revision 不一致")
        return self


class ResumeCapsule(StrictAgentModel):
    current_work_item: NonEmptyText
    stopped_at: NonEmptyText
    confirmed_facts: list[NonEmptyText] = Field(default_factory=list)
    unresolved_hypotheses: list[NonEmptyText] = Field(default_factory=list)
    failed_attempts: list[NonEmptyText] = Field(default_factory=list)
    restrictions: list[NonEmptyText] = Field(default_factory=list)
    risk_notes: list[NonEmptyText] = Field(default_factory=list)
    human_checks: list[NonEmptyText] = Field(default_factory=list)
    changed_files: list[RelativePathText] = Field(default_factory=list)
    comparison_base_revision: NonEmptyText | None = None
    workspace_digest_kind: WorkspaceDigestKind = "workspace-bytes-v1"
    workspace_digest: Sha256Text
    gate_evidence: list[HistoricalGateEvidence] = Field(default_factory=list)
    external_side_effects: Literal["none", "known", "unknown"] = "none"
    writer_stopped: bool
    workspace_explained: bool
    allowed_actions: list[AgentAction] = Field(min_length=1)
    next_step: NonEmptyText
    recommended_command: NonEmptyText | None = None

    @field_validator("changed_files")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return _normalize_relative_paths(values)

    @field_validator("comparison_base_revision")
    @classmethod
    def validate_comparison_revision(cls, value: str | None) -> str | None:
        if value is not None and not _REVISION_PATTERN.fullmatch(value):
            raise ValueError("Resume Capsule comparison revision 必须是完整 Git OID")
        return value

    @model_validator(mode="after")
    def validate_actions(self) -> ResumeCapsule:
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("Resume Capsule 的 allowed_actions 不能重复")
        return self


class AgentTaskCard(StrictAgentModel):
    kind: Literal["VegaTask"] = "VegaTask"
    task_id: NonEmptyText
    status: Literal[
        "planning",
        "awaiting_approval",
        "ready",
        "active",
        "paused",
        "needs_human",
        "completed",
        "stopped",
    ] = "planning"
    branch: NonEmptyText
    base_revision: NonEmptyText
    previous_task_card: RelativePathText | None = None
    plan: AgentPlan
    current_work_item: NonEmptyText | None = None
    handoff_sequence: int = Field(default=0, ge=0)
    handoff_status: Literal["none", "handoff_ready", "handoff_blocked"] = "none"
    handoff_base_revision: NonEmptyText | None = None
    handoff_workspace_digest: Sha256Text | None = None
    last_handoff_checkpoint: NonEmptyText | None = None
    progress_notes: list[NonEmptyText] = Field(default_factory=list)
    failed_attempts: list[NonEmptyText] = Field(default_factory=list)
    risk_notes: list[NonEmptyText] = Field(default_factory=list)
    verification_notes: list[NonEmptyText] = Field(default_factory=list)
    resume_capsule: ResumeCapsule | None = None
    change_run: ChangeRunResume | None = None
    planning_run: PlanningRunResume | None = None

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        branch = value.strip()
        if (
            branch in {".", ".."}
            or branch.startswith(("/", "-"))
            or branch.endswith(("/", "."))
            or ".." in branch
            or "@{" in branch
            or re.search(r"[\x00-\x20~^:?*\\\[]", branch)
        ):
            raise ValueError(f"分支名不合法：{value}")
        return branch

    @field_validator("base_revision", "handoff_base_revision")
    @classmethod
    def validate_revision(cls, value: str | None) -> str | None:
        if value is not None and not _REVISION_PATTERN.fullmatch(value):
            raise ValueError("Git revision 必须是 40 或 64 位小写十六进制摘要")
        return value

    @field_validator("previous_task_card")
    @classmethod
    def validate_previous_task_card(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _normalize_relative_paths([value])[0]
        if not normalized.startswith(".vega/tasks/") or not normalized.endswith(".md"):
            raise ValueError("previous_task_card 必须指向 .vega/tasks 下的 Markdown")
        return normalized

    @model_validator(mode="after")
    def validate_bindings(self) -> AgentTaskCard:
        if self.plan.task_id != self.task_id:
            raise ValueError("Task Card 与 Plan 的 task_id 不一致")
        if self.plan.approved and not self.plan.approval_is_current():
            raise ValueError("Task Card 中的 Plan 批准摘要已过期")
        if self.current_work_item is not None and self.current_work_item not in {
            item.work_item_id for item in self.plan.work_items
        }:
            raise ValueError("current_work_item 不属于当前 Plan")
        if self.change_run is not None and self.planning_run is not None:
            raise ValueError("Task Card 不能同时携带 Planning 与已编译 ChangeRun Resume")
        if self.change_run is not None:
            _validate_task_card_change_run(self)
        if self.planning_run is not None:
            _validate_task_card_planning_run(self)

        if self.handoff_status == "none":
            _validate_empty_handoff(self)
            return self

        _validate_active_handoff(self)
        return self


def _validate_empty_handoff(card: AgentTaskCard) -> None:
    handoff_values = (
        card.handoff_base_revision,
        card.handoff_workspace_digest,
        card.last_handoff_checkpoint,
        card.resume_capsule,
        card.change_run,
        card.planning_run,
    )
    if card.handoff_sequence != 0 or any(value is not None for value in handoff_values):
        raise ValueError("无交接状态不能包含 Resume Capsule 或交接绑定")


def _validate_active_handoff(card: AgentTaskCard) -> None:
    handoff_values = (
        card.handoff_base_revision,
        card.handoff_workspace_digest,
        card.last_handoff_checkpoint,
        card.resume_capsule,
    )
    if card.status in _TERMINAL_TASK_STATUSES:
        raise ValueError("终态 Task Card 不能保留可恢复 Handoff")
    if card.handoff_sequence < 1 or any(value is None for value in handoff_values):
        raise ValueError("交接状态必须包含 sequence、revision、digest、checkpoint 和 Resume Capsule")
    assert card.resume_capsule is not None
    if card.current_work_item != card.resume_capsule.current_work_item:
        raise ValueError("Task Card 与 Resume Capsule 的 current_work_item 不一致")
    if card.handoff_workspace_digest != card.resume_capsule.workspace_digest:
        raise ValueError("Task Card 与 Resume Capsule 的 Workspace digest 不一致")
    if card.handoff_status == "handoff_ready" and (
        not card.resume_capsule.writer_stopped
        or not card.resume_capsule.workspace_explained
        or card.resume_capsule.external_side_effects != "none"
    ):
        raise ValueError("handoff_ready 必须证明 Writer 已停止、现场可解释且无外部副作用")


def render_task_card(card: AgentTaskCard) -> str:
    from .agent_task_card_render import render_task_card_body

    safe_payload = redact_value(card.model_dump(mode="json"))
    assert_portable_task_card_payload(safe_payload)
    safe_card = AgentTaskCard.model_validate(safe_payload)
    payload = safe_card.model_dump(mode="json")
    payload_digest = canonical_digest(payload)
    front_matter = {
        "kind": safe_card.kind,
        "schema_version": safe_card.schema_version,
        "task_id": safe_card.task_id,
        "status": safe_card.status,
        "branch": safe_card.branch,
        "base_revision": safe_card.base_revision,
        "previous_task_card": safe_card.previous_task_card,
        "goal_revision": safe_card.plan.goal_revision,
        "plan_revision": safe_card.plan.plan_revision,
        "approved_plan_digest": safe_card.plan.approved_digest,
        "current_work_item": safe_card.current_work_item,
        "handoff_sequence": safe_card.handoff_sequence,
        "handoff_status": safe_card.handoff_status,
        "handoff_base_revision": safe_card.handoff_base_revision,
        "handoff_workspace_digest": safe_card.handoff_workspace_digest,
        "last_handoff_checkpoint": safe_card.last_handoff_checkpoint,
        "payload_digest": payload_digest,
    }
    yaml_text = yaml.safe_dump(
        front_matter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    body = render_task_card_body(safe_card)
    machine_state = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        f"---\n{yaml_text}\n---\n\n"
        f"# {safe_card.task_id}\n\n"
        f"{body}\n\n"
        f"{_MACHINE_STATE_START}\n{machine_state}\n{_MACHINE_STATE_END}\n"
    )


def parse_task_card(content: str) -> AgentTaskCard:
    front_matter, payload = _parse_parts(content)
    if front_matter.get("payload_digest") != canonical_digest(payload):
        raise TaskCardError("Task Card payload digest 不一致")
    assert_portable_task_card_payload(payload)
    try:
        card = AgentTaskCard.model_validate(payload)
    except ValueError as exc:
        raise TaskCardError(f"Task Card schema 校验失败：{exc}") from exc
    expected = {
        "kind": card.kind,
        "schema_version": card.schema_version,
        "task_id": card.task_id,
        "status": card.status,
        "branch": card.branch,
        "base_revision": card.base_revision,
        "previous_task_card": card.previous_task_card,
        "goal_revision": card.plan.goal_revision,
        "plan_revision": card.plan.plan_revision,
        "approved_plan_digest": card.plan.approved_digest,
        "current_work_item": card.current_work_item,
        "handoff_sequence": card.handoff_sequence,
        "handoff_status": card.handoff_status,
        "handoff_base_revision": card.handoff_base_revision,
        "handoff_workspace_digest": card.handoff_workspace_digest,
        "last_handoff_checkpoint": card.last_handoff_checkpoint,
    }
    for key, value in expected.items():
        if front_matter.get(key) != value:
            raise TaskCardError(f"Task Card front matter 与机器状态不一致：{key}")
    return card


def save_task_card(path: Path, card: AgentTaskCard) -> None:
    if path.suffix.lower() != ".md":
        raise TaskCardError("Task Card 必须使用 .md 扩展名")
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        raise TaskCardError(f"目标 Task Card 已存在，拒绝覆盖：{path.name}")
    temp_path = path.with_name(f".task-{uuid4().hex[:16]}")
    temp_path.write_text(render_task_card(card), encoding="utf-8", newline="\n")
    try:
        for attempt in range(10):
            try:
                os.link(temp_path, path)
                break
            except FileExistsError as exc:
                raise TaskCardError(
                    f"目标 Task Card 已存在，拒绝覆盖：{path.name}"
                ) from exc
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        else:  # pragma: no cover - 循环只会 break 或抛出
            raise OSError("无法独占发布 Task Card")
    finally:
        temp_path.unlink(missing_ok=True)


def load_task_card(path: Path) -> AgentTaskCard:
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise TaskCardError(f"无法读取 Task Card：{path.name}") from exc
    return parse_task_card(content)


def discover_handoff_task_cards(repo: Path, *, branch: str | None = None) -> list[Path]:
    from .agent_task_card_discovery import discover_handoff_task_cards as discover

    return discover(repo, branch=branch)


def discover_local_handoff_task_cards(
    repo: Path,
    *,
    branch: str,
) -> list[Path]:
    from .agent_task_card_discovery import (
        discover_local_handoff_task_cards as discover,
    )

    return discover(repo, branch=branch)


def task_card_content_digest(content: str) -> str:
    canonical_content = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()


def _validate_task_card_change_run(card: AgentTaskCard) -> None:
    from .agent_change_run import validate_change_projection

    assert card.change_run is not None
    change_run = card.change_run
    validate_change_projection(
        change_run.contract,
        change_run.execution_plan,
        card.plan,
    )
    comparison_base = (
        card.resume_capsule.comparison_base_revision
        if card.resume_capsule is not None
        else None
    )
    if comparison_base != change_run.accepted_checkpoint_sha:
        raise ValueError("ChangeRun Resume 的比较基线必须是 Accepted Checkpoint")


def _validate_task_card_planning_run(card: AgentTaskCard) -> None:
    assert card.planning_run is not None
    proposal = card.planning_run.proposal
    if card.status != "planning":
        raise ValueError("Planning Task Card 必须保持 planning 状态")
    if card.current_work_item != "WI-PLANNING":
        raise ValueError("Planning Task Card 必须绑定 WI-PLANNING")
    if card.plan.approved or card.plan.approval_is_current():
        raise ValueError("Planning Task Card 不能伪造已批准 Plan")
    if proposal.task_id != card.task_id or proposal.user_goal != card.plan.user_goal:
        raise ValueError("Planning Task Card 与 Proposal 的任务身份不一致")
    if card.base_revision != proposal.source_revision:
        raise ValueError("Planning Task Card 基线必须等于 Proposal source revision")
    if (
        card.plan.observed_facts
        != [fact.statement for fact in proposal.observed_facts]
        or card.plan.hypotheses != proposal.hypotheses
        or card.plan.unresolved_decisions
        != [
            *proposal.unresolved_questions,
            "Planning Proposal 尚未经过 Contract Compiler",
        ]
    ):
        raise ValueError("Planning Task Card 的 Plan 投影与 Proposal 不一致")


def _parse_parts(content: str) -> tuple[dict[str, object], dict[str, object]]:
    if not content.startswith("---\n"):
        raise TaskCardError("Task Card 缺少 YAML front matter")
    try:
        yaml_end = content.index("\n---\n", 4)
        machine_start = content.index(_MACHINE_STATE_START, yaml_end)
        machine_payload_start = content.index("\n", machine_start) + 1
        machine_end = content.index(f"\n{_MACHINE_STATE_END}", machine_payload_start)
        front_matter = yaml.safe_load(content[4:yaml_end])
        payload = json.loads(content[machine_payload_start:machine_end])
    except (ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise TaskCardError("Task Card 结构无法解析") from exc
    if not isinstance(front_matter, dict) or not isinstance(payload, dict):
        raise TaskCardError("Task Card front matter 和机器状态必须是 object")
    return front_matter, payload


def _normalize_relative_paths(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip().replace("\\", "/")
        path = PurePosixPath(candidate)
        if (
            not candidate
            or path.is_absolute()
            or candidate.startswith("//")
            or (len(candidate) >= 2 and candidate[1] == ":")
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"路径必须是仓库相对路径：{value}")
        normalized.append(path.as_posix())
    if len(set(normalized)) != len(normalized):
        raise ValueError("仓库相对路径不能重复")
    return normalized
