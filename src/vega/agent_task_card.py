from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import Field, field_validator, model_validator

from .agent_contract import (
    AgentAction,
    AgentPlan,
    NonEmptyText,
    RelativePathText,
    Sha256Text,
    StrictAgentModel,
    canonical_digest,
)
from .agent_handoff_safety import TaskCardError, assert_portable_task_card_payload
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
        or card.resume_capsule.external_side_effects == "unknown"
    ):
        raise ValueError("handoff_ready 必须证明 Writer 已停止、现场可解释且副作用已知")


def render_task_card(card: AgentTaskCard) -> str:
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
    sections = [
        ("Goal and Non-goals", _goal_lines(safe_card)),
        ("Success Conditions", safe_card.plan.success_conditions),
        ("Observed Facts and Hypotheses", _fact_lines(safe_card)),
        ("Approved Plan", _plan_lines(safe_card)),
        ("Progress and Failed Attempts", _progress_lines(safe_card)),
        ("Risks and Verification", _risk_lines(safe_card)),
        ("Last Handoff", _handoff_lines(safe_card)),
        ("Next Step", _next_step_lines(safe_card)),
    ]
    body = _render_sections(sections)
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
        return parse_task_card(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise TaskCardError(f"无法读取 Task Card：{path.name}") from exc


def discover_handoff_task_cards(repo: Path, *, branch: str | None = None) -> list[Path]:
    repo_root = repo.resolve()
    current_branch = branch or _current_branch(repo_root)
    process = subprocess.run(
        ["git", "ls-files", "-z", "--", ":(glob).vega/tasks/**/*.md"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if process.returncode != 0:
        raise TaskCardError("无法读取 Git 跟踪的 Task Card")
    matches: list[Path] = []
    for raw_path in process.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="strict")
        path = repo_root / PurePosixPath(relative)
        card = load_task_card(path)
        if (
            card.branch == current_branch
            and card.status not in _TERMINAL_TASK_STATUSES
            and card.handoff_status != "none"
        ):
            matches.append(path)
    return sorted(matches)


def task_card_content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_handoff_workspace_digest(
    repo: Path,
    changed_files: list[str],
) -> str:
    """绑定准备交接的 WIP 文件内容；Task Card 自身不参与，避免自引用摘要。"""

    root = repo.resolve(strict=True)
    entries: list[dict[str, object]] = []
    for relative in _normalize_relative_paths(changed_files):
        path = (root / PurePosixPath(relative)).resolve(strict=False)
        if not path.is_relative_to(root):
            raise TaskCardError(f"交接文件越过仓库边界：{relative}")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            entries.append({"path": relative, "kind": "missing"})
            continue
        if path.is_symlink():
            entries.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "target": os.readlink(path),
                }
            )
            continue
        if not path.is_file():
            raise TaskCardError(f"交接文件不是普通文件：{relative}")
        entries.append(
            {
                "path": relative,
                "kind": "file",
                "size": metadata.st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return canonical_digest({"changed_files": entries})


def _current_branch(repo: Path) -> str:
    process = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    branch = process.stdout.strip()
    if process.returncode != 0 or not branch:
        raise TaskCardError("当前 HEAD 不是可恢复任务分支")
    return branch


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


def _render_sections(sections: list[tuple[str, list[str]]]) -> str:
    output: list[str] = []
    for title, values in sections:
        output.extend([f"## {title}", ""])
        output.extend(f"- {value}" for value in (values or ["无"]))
        output.append("")
    return "\n".join(output).rstrip()


def _goal_lines(card: AgentTaskCard) -> list[str]:
    return [
        f"Goal: {card.plan.user_goal}",
        *(f"Non-goal: {value}" for value in card.plan.non_goals),
    ]


def _fact_lines(card: AgentTaskCard) -> list[str]:
    return [
        *(f"Fact: {value}" for value in card.plan.observed_facts),
        *(f"Hypothesis: {value}" for value in card.plan.hypotheses),
    ]


def _plan_lines(card: AgentTaskCard) -> list[str]:
    approval = (
        f"已批准，digest={card.plan.approved_digest}"
        if card.plan.approval_is_current()
        else "尚未批准"
    )
    return [
        f"Plan revision: {card.plan.plan_revision}，{approval}",
        *(
            f"{item.work_item_id} [{item.status}]: {item.objective}"
            for item in card.plan.work_items
        ),
    ]


def _progress_lines(card: AgentTaskCard) -> list[str]:
    return [
        *(f"Progress: {value}" for value in card.progress_notes),
        *(f"Failed attempt: {value}" for value in card.failed_attempts),
    ]


def _risk_lines(card: AgentTaskCard) -> list[str]:
    return [
        *(f"Risk: {value}" for value in card.risk_notes),
        *(f"Verification: {value}" for value in card.verification_notes),
    ]


def _handoff_lines(card: AgentTaskCard) -> list[str]:
    if card.resume_capsule is None:
        return ["尚无跨机器交接"]
    capsule = card.resume_capsule
    return [
        f"Status: {card.handoff_status}",
        f"Stopped at: {capsule.stopped_at}",
        f"Workspace digest: {capsule.workspace_digest}",
        f"Changed files: {', '.join(capsule.changed_files) or '无'}",
        f"External side effects: {capsule.external_side_effects}",
    ]


def _next_step_lines(card: AgentTaskCard) -> list[str]:
    if card.resume_capsule is None:
        return ["等待 Plan 批准或继续当前 Work Item"]
    lines = [card.resume_capsule.next_step]
    if card.resume_capsule.recommended_command:
        lines.append(f"建议命令：`{card.resume_capsule.recommended_command}`")
    return lines
