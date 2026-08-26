from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from .agent_contract import (
    AgentCheckpoint,
    AgentPlan,
    AgentState,
    canonical_digest,
)
from .redaction import redact_text


DEFAULT_TASK_BRIEF_MAX_BYTES = 32 * 1024


class TaskBriefBudgetExceeded(ValueError):
    pass


@dataclass(frozen=True)
class TaskBrief:
    content: str
    utf8_bytes: int
    sha256: str
    artifact_refs: tuple[str, ...]


def compile_task_brief(
    *,
    plan: AgentPlan,
    work_item_id: str,
    checkpoint: AgentCheckpoint | None = None,
    confirmed_facts: Sequence[str] = (),
    failed_attempts: Sequence[str] = (),
    gate_summary: Mapping[str, str] | None = None,
    artifact_refs: Sequence[str] = (),
    max_bytes: int = DEFAULT_TASK_BRIEF_MAX_BYTES,
) -> TaskBrief:
    if max_bytes < 1:
        raise ValueError("Task Brief 上限必须大于 0")
    if not plan.approval_is_current():
        raise ValueError("只有当前已批准 Plan 可以编译 Task Brief")

    work_item = next(
        (item for item in plan.work_items if item.work_item_id == work_item_id),
        None,
    )
    if work_item is None:
        raise ValueError(f"Plan 中不存在 Work Item：{work_item_id}")
    other_pending_items = [
        f"{item.work_item_id}: {item.objective}"
        for item in plan.work_items
        if item.work_item_id != work_item_id
        and item.status not in {"completed", "superseded"}
    ]

    normalized_refs = tuple(_normalize_artifact_ref(value) for value in artifact_refs)
    if checkpoint is not None:
        normalized_refs = tuple(
            dict.fromkeys([*checkpoint.evidence_refs, *normalized_refs])
        )

    sections = [
        ("任务目标", [plan.user_goal]),
        ("非目标", plan.non_goals or ["无"]),
        (
            "批准信息",
            [
                f"Goal revision: {plan.goal_revision}",
                f"Plan revision: {plan.plan_revision}",
                f"Approved digest: {plan.approved_digest}",
            ],
        ),
        (
            "当前 Work Item（本轮唯一修改目标）",
            [f"{work_item.work_item_id}: {work_item.objective}"],
        ),
        (
            "其他未完成 Work Item（本轮不处理）",
            other_pending_items or ["无"],
        ),
        ("允许范围", work_item.allowed_paths or ["未声明"]),
        ("禁止范围", work_item.forbidden_paths or ["未声明"]),
        (
            "最终合同条件（全部 Work Item 完成后判断）",
            plan.success_conditions or ["未声明"],
        ),
        ("验证要求", work_item.verification or ["未声明"]),
        (
            "风险提示",
            [
                f"外部副作用声明：{work_item.external_side_effects}",
                *(work_item.risk_notes or ["无"]),
            ],
        ),
        ("已确认事实", _complete_lines(confirmed_facts or plan.observed_facts)),
        ("失败尝试", _complete_lines(failed_attempts)),
        ("门禁状态", _render_gate_summary(gate_summary)),
        ("当前现场", _render_checkpoint(checkpoint)),
        ("证据引用", list(normalized_refs) or ["无"]),
        (
            "下一动作",
            [
                "只处理当前 Work Item；即使后续事项位于相同文件，也不要提前实现。"
                "遇到范围变化、未知副作用或证据冲突时停止并请求人工。"
            ],
        ),
    ]
    content = redact_text(_render_markdown(sections))
    size = len(content.encode("utf-8"))
    if size > max_bytes:
        raise TaskBriefBudgetExceeded(
            f"Task Brief 必需内容为 {size} UTF-8 字节，超过 {max_bytes} 字节软上限；"
            "请拆分 Work Item 或人工缩小上下文，不能静默截断。"
        )
    return TaskBrief(
        content=content,
        utf8_bytes=size,
        sha256=canonical_digest({"content": content}),
        artifact_refs=normalized_refs,
    )


def task_brief_manifest(
    brief: TaskBrief,
    *,
    plan: AgentPlan,
    state: AgentState,
    checkpoint: AgentCheckpoint,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "utf8_bytes": brief.utf8_bytes,
        "sha256": brief.sha256,
        "artifact_refs": list(brief.artifact_refs),
        "goal_revision": plan.goal_revision,
        "plan_revision": plan.plan_revision,
        "approved_plan_digest": plan.approved_digest,
        "current_work_item": state.current_work_item,
        "checkpoint_id": checkpoint.checkpoint_id,
        "workspace_fingerprint": checkpoint.workspace_fingerprint,
    }


def _normalize_artifact_ref(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized.startswith("//")
        or any(part in {"", ".", ".."} for part in path.parts)
        or (len(normalized) >= 2 and normalized[1] == ":")
    ):
        raise ValueError(f"Artifact 引用必须是仓库相对路径：{value}")
    return path.as_posix()


def _complete_lines(values: Sequence[str]) -> list[str]:
    normalized = list(
        dict.fromkeys(value.strip() for value in values if value.strip())
    )
    if not normalized:
        return ["无"]
    return normalized


def _render_gate_summary(gate_summary: Mapping[str, str] | None) -> list[str]:
    if not gate_summary:
        return ["尚无当前门禁结果"]
    return [f"{key}: {value}" for key, value in sorted(gate_summary.items())]


def _render_checkpoint(checkpoint: AgentCheckpoint | None) -> list[str]:
    if checkpoint is None:
        return ["尚无 Checkpoint"]
    return [
        f"Checkpoint: {checkpoint.checkpoint_id}",
        f"现场状态: {checkpoint.status}",
        f"外部副作用: {checkpoint.external_side_effects}",
        f"Workspace fingerprint: {checkpoint.workspace_fingerprint}",
        f"changed files: {json.dumps(checkpoint.changed_files, ensure_ascii=False)}",
    ]


def _render_markdown(sections: Sequence[tuple[str, Sequence[str]]]) -> str:
    lines = ["# Vega Task Brief", ""]
    for title, values in sections:
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {value}" for value in values)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
