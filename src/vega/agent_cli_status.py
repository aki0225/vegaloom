from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .agent_explain import AgentExplanation, build_agent_explanation
from .agent_run_selection import (
    ACTIVE_CHANGE_PHASES,
    ChangeRunSelectionError,
    select_repository_change_run,
)
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_support import load_agent_bundle
from .run_status import render_run_status, run_status_payload
from .run_utils import resolve_run_dir


RunSelectionSource = Literal[
    "explicit",
    "repository_active",
    "repository_recent_terminal",
]

_PHASE_LABELS = {
    "planning": "调查与计划",
    "awaiting_approval": "等待批准",
    "ready": "等待执行",
    "acting": "Worker 执行",
    "observing": "现场对账",
    "needs_human": "等待人工",
    "finalizing": "最终裁决",
    "completed": "已完成",
    "stopped": "已停止",
}
_GATE_LABELS = {
    "not_run": "尚未运行",
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻断",
    "stale": "已过期",
}
_OUTCOME_LABELS = {
    "in_progress": "进行中",
    "ready": "可以继续",
    "attention_required": "需要处理",
    "completed": "已完成",
    "stopped": "已停止",
    "unknown": "无法确认",
}


@dataclass(frozen=True)
class AgentCliRun:
    workspace: Path
    run_dir: Path
    selection_source: RunSelectionSource


@dataclass(frozen=True)
class AgentCliSnapshot:
    target: AgentCliRun
    status: dict[str, object]
    explanation: AgentExplanation | None


def resolve_agent_cli_run(
    location: Path,
    run: str | None,
) -> AgentCliRun:
    """解析显式 Run，或按当前 Git 仓库选择唯一 ChangeRun。"""

    if run is not None:
        run_dir = resolve_run_dir(location, run)
        return AgentCliRun(
            workspace=run_dir.parent.parent,
            run_dir=run_dir,
            selection_source="explicit",
        )

    selected = select_repository_change_run(location)
    if selected is None:
        raise ChangeRunSelectionError("当前仓库没有可读取的 ChangeRun。")
    return AgentCliRun(
        workspace=selected.run_dir.parent.parent,
        run_dir=selected.run_dir,
        selection_source=(
            "repository_active"
            if selected.is_active
            else "repository_recent_terminal"
        ),
    )


def build_agent_cli_snapshot(target: AgentCliRun) -> AgentCliSnapshot:
    """构建一次只读状态快照；展示层不能借查询改写运行状态。"""

    payload = run_status_payload(target.workspace, target.run_dir.name)
    explanation: AgentExplanation | None = None
    if payload.get("kind") == "agent":
        run_dir, state, plan, _ = load_agent_bundle(
            target.workspace,
            target.run_dir.name,
        )
        explanation = build_agent_explanation(run_dir, state, plan)
    payload["selected_run"] = selected_run_payload(target, payload)
    payload["explanation"] = (
        explanation.model_dump(mode="json")
        if explanation is not None
        else None
    )
    return AgentCliSnapshot(
        target=target,
        status=payload,
        explanation=explanation,
    )


def selected_run_payload(
    target: AgentCliRun,
    status: dict[str, object],
) -> dict[str, object]:
    recorded_phase = status.get("recorded_agent_phase")
    return {
        "run_id": target.run_dir.name,
        "selection_source": target.selection_source,
        "active": (
            recorded_phase in ACTIVE_CHANGE_PHASES
            if isinstance(recorded_phase, str)
            else None
        ),
    }


def render_status_snapshot(
    snapshot: AgentCliSnapshot,
    *,
    full: bool,
) -> str:
    if snapshot.status.get("kind") != "agent":
        return render_run_status(
            snapshot.target.workspace,
            snapshot.target.run_dir.name,
        )
    if full:
        return SupervisorAgentRuntime(snapshot.target.workspace).status(
            snapshot.target.run_dir.name
        )
    assert snapshot.explanation is not None
    return render_compact_agent_status(snapshot.status, snapshot.explanation)


def render_compact_agent_status(
    status: dict[str, object],
    explanation: AgentExplanation,
) -> str:
    phase = str(status.get("agent_phase") or "unknown")
    changed_files = _string_list(status.get("changed_files"))
    next_steps = _string_list(status.get("next_steps"))
    next_step = (
        next_steps[0]
        if next_steps
        else _action_list(explanation.safe_actions)
    )
    lines = [
        "# Vega Status",
        "",
        f"- 运行：`{status.get('run_id')}`",
        f"- 阶段：{_PHASE_LABELS.get(phase, phase)}",
        f"- Work Item：`{status.get('current_work_item') or '未记录'}`",
        f"- 执行会话：{_provider_attempt(status)}",
        f"- 修改文件：{_changed_files(changed_files)}",
        f"- Verification：{_gate(status.get('verification'))}",
        f"- Risk：{_gate(status.get('risk'))}",
        f"- Reviewer：{_gate(status.get('review'))}",
        f"- 原因：{explanation.reason}",
        f"- 下一步：{next_step}",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_agent_explanation(
    snapshot: AgentCliSnapshot,
    *,
    full: bool,
) -> str:
    explanation = snapshot.explanation
    if explanation is None:
        raise ValueError("explain 只支持 ChangeRun。")
    category = explanation.block_category or "无"
    lines = [
        "# Vega Explain",
        "",
        f"- 运行：`{explanation.run_id}`",
        f"- 阶段：{_PHASE_LABELS.get(explanation.phase, explanation.phase)}",
        f"- 结果：{_OUTCOME_LABELS[explanation.outcome]}",
        f"- 分类：`{category}`",
        f"- 原因代码：`{explanation.reason_code}`",
        f"- 决定者：{explanation.actor}",
        "",
        "## 为什么停在这里",
        "",
        explanation.reason,
        "",
        "## 已确认",
        "",
        *_bullet_lines(explanation.facts, empty="暂无可确认事实。"),
        "",
        "## 尚未确认",
        "",
        *_bullet_lines(explanation.unknowns, empty="无。"),
        "",
        "## 安全动作",
        "",
        *_bullet_lines(explanation.safe_actions, empty="暂无自动动作。"),
        "",
        "## 证据引用",
        "",
        *_bullet_lines(explanation.evidence_refs, empty="暂无。", code=True),
    ]
    if full:
        lines.extend(
            [
                "",
                "## 完整状态卡",
                "",
                render_status_snapshot(snapshot, full=True).rstrip(),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def explanation_json_payload(
    snapshot: AgentCliSnapshot,
    *,
    full: bool,
) -> dict[str, object]:
    if snapshot.explanation is None:
        raise ValueError("explain 只支持 ChangeRun。")
    payload: dict[str, object] = {
        "selected_run": snapshot.status["selected_run"],
        "explanation": snapshot.explanation.model_dump(mode="json"),
    }
    if full:
        payload["status"] = snapshot.status
    return payload


def _provider_attempt(status: dict[str, object]) -> str:
    attempt = (
        status.get("active_child_run")
        or status.get("active_planning_execution_id")
        or status.get("last_child_run")
    )
    sessions = status.get("provider_sessions")
    active_sessions: list[str] = []
    if isinstance(sessions, list):
        for item in sessions:
            if not isinstance(item, dict):
                continue
            lifecycle = item.get("lifecycle")
            role = item.get("role")
            provider = item.get("provider")
            if (
                lifecycle in {"active", "waiting_user"}
                and isinstance(role, str)
                and isinstance(provider, str)
            ):
                active_sessions.append(f"{provider}/{role} {lifecycle}")
    parts = [f"`{attempt}`" if isinstance(attempt, str) else "尚未启动"]
    live_child_stage = status.get("live_child_stage")
    if isinstance(live_child_stage, str):
        parts.append(f"Core `{live_child_stage}`")
    if active_sessions:
        parts.append("；".join(active_sessions))
    return "；".join(parts)


def _changed_files(changed_files: list[str]) -> str:
    if not changed_files:
        return "无"
    visible = "、".join(f"`{item}`" for item in changed_files[:5])
    suffix = f" 等，共 {len(changed_files)} 个" if len(changed_files) > 5 else ""
    return visible + suffix


def _gate(value: object) -> str:
    normalized = str(value or "not_run")
    return _GATE_LABELS.get(normalized, normalized)


def _action_list(actions: list[str]) -> str:
    return "、".join(actions) if actions else "等待人工检查当前状态"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _bullet_lines(
    values: list[str],
    *,
    empty: str,
    code: bool = False,
) -> list[str]:
    if not values:
        return [f"- {empty}"]
    if code:
        return [f"- `{item}`" for item in values]
    return [f"- {item}" for item in values]
