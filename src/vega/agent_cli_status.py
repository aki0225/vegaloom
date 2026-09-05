from __future__ import annotations

from pathlib import Path

from .agent_cli_snapshot import AgentCliSnapshot
from .agent_explain_codes import PublicActionId
from .agent_repository_binding import load_run_metadata
from .run_status import render_run_status_payload


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
_ACTION_TEXT: dict[PublicActionId, str] = {
    "change.start": (
        "在源仓库 `{source_repo}` 目录执行 "
        "`vega change \"<新的变更目标>\"` 创建 ChangeRun。"
    ),
    "diff.inspect": "人工检查 Candidate Diff；以完整状态卡中的 Candidate SHA 为准。",
    "evidence.inspect": "人工检查下方“证据引用”列出的 Artifact。",
    "handoff.create": (
        "在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega handoff --run {run_id} --reason \"<交接原因>\"` 生成交接材料。"
    ),
    "human.review": "人工检查当前状态、证据和风险，再决定是否调整授权边界或停止。",
    "plan.approve": (
        "在 Run Workspace `{run_workspace}` 先核对 "
        "`runs/{run_id}/change-contract.json` 和 `runs/{run_id}/execution-plan.json`；"
        "确认授权范围后执行 `vega approve --run {run_id} --actor human`。"
        "该高级命令立即批准，没有二次询问。"
    ),
    "plan.revise": (
        "准备修订后的合同和计划，再在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega revise --run {run_id} --contract <contract.json> "
        "--execution-plan <execution-plan.json>`。"
    ),
    "provider.respond_decision": (
        "人工确认请求内容后，在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega respond --run {run_id} --interaction {interaction_id} "
        "--decision <accept|decline>`。"
    ),
    "provider.respond_input": (
        "准备不含敏感信息的响应 JSON 后，在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega respond --run {run_id} --interaction {interaction_id} "
        "--input <response.json>`。"
    ),
    "provider.steer": (
        "在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega steer --run {run_id} --role <worker|reviewer> --text \"<补充指令>\"`。"
    ),
    "provider.takeover": (
        "在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega takeover --run {run_id} --role <worker|reviewer>` 接管会话。"
    ),
    "run.continue": (
        "在 Run Workspace `{run_workspace}` 目录执行 `{continue_command}` "
        "继续当前 ChangeRun。"
    ),
    "run.stop": (
        "在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega stop --run {run_id} --reason \"<停止原因>\"` 停止并保留现场。"
    ),
    "status.view": (
        "在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega status --run {run_id}` 查看当前状态。"
    ),
    "status.view_full": (
        "在 Run Workspace `{run_workspace}` 目录执行 "
        "`vega status --run {run_id} --full` 查看完整状态卡。"
    ),
}


def render_status_snapshot(
    snapshot: AgentCliSnapshot,
    *,
    full: bool,
) -> str:
    if snapshot.status.get("kind") != "agent":
        return render_run_status_payload(snapshot.status)
    if full:
        if snapshot.full_status is None:
            raise ValueError("当前 CLI 快照未包含完整状态卡。")
        return snapshot.full_status
    assert snapshot.explanation is not None
    return render_compact_agent_status(snapshot)


def render_compact_agent_status(snapshot: AgentCliSnapshot) -> str:
    status = snapshot.status
    assert snapshot.explanation is not None
    explanation = snapshot.explanation
    phase = str(status.get("agent_phase") or "unknown")
    changed_files = _string_list(status.get("changed_files"))
    # 推荐动作与 explain 同源；next_steps 仍保留为 JSON 兼容字段。
    next_step = _recommended_action_text(snapshot)
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
    delivery = _delivery_lines(status, phase)
    if delivery:
        lines.extend(delivery)
    return "\n".join(lines).rstrip() + "\n"


def _recommended_action_text(snapshot: AgentCliSnapshot) -> str:
    """返回与 explain 相同来源的第一条推荐动作。"""

    explanation = snapshot.explanation
    if explanation is not None and explanation.safe_actions:
        return _action_texts(explanation.safe_actions[:1], snapshot)[0]
    return "暂无可推荐动作，请人工核对状态与证据"


def _delivery_lines(status: dict[str, object], phase: str) -> list[str]:
    """在终态第一屏指出现有报告和 Candidate，不创建新的交付状态。"""

    if phase != "completed":
        return []
    lines: list[str] = []
    delivery = status.get("delivery")
    if isinstance(delivery, dict):
        for key, label in (("worktree_path", "代码目录"), ("branch", "任务分支"), ("base_revision", "累计 Diff 基线")):
            value = delivery.get(key)
            if isinstance(value, str) and value:
                lines.append(f"- {label}：`{value}`")
    candidate = status.get("accepted_checkpoint_sha") or status.get(
        "active_candidate_sha"
    )
    if isinstance(candidate, str) and candidate:
        lines.append(f"- Candidate：`{candidate}`")
    artifacts = status.get("key_artifacts")
    if isinstance(artifacts, list):
        reports = [
            item
            for item in artifacts
            if isinstance(item, str)
            and item.endswith(("agent-final-report.md", "agent-final-report.json"))
        ]
        if reports:
            lines.append(f"- 报告：`{', '.join(reports)}`")
    return lines


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
        "## 下一步",
        "",
        *_bullet_lines(
            _action_texts(explanation.safe_actions, snapshot),
            empty="暂无可执行的下一步。",
        ),
        "",
        "## 证据引用",
        "",
        *_bullet_lines(explanation.evidence_refs, empty="暂无。", code=True),
    ]
    if full:
        if snapshot.full_status is None:
            raise ValueError("当前 CLI 快照未包含完整状态卡。")
        lines.extend(
            [
                "",
                "## 完整状态卡",
                "",
                snapshot.full_status.rstrip(),
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


def _action_texts(
    actions: list[PublicActionId],
    snapshot: AgentCliSnapshot,
) -> list[str]:
    values = _action_values(snapshot)
    return [_ACTION_TEXT[action].format(**values) for action in actions]


def _action_values(snapshot: AgentCliSnapshot) -> dict[str, str]:
    run_workspace = snapshot.target.workspace.resolve()
    source_repo = _source_repo(snapshot)
    interactions = (
        snapshot.status_projection.provider_interactions
        if snapshot.status_projection is not None
        else ()
    )
    continue_command = (
        f"vega change --run {snapshot.target.run_dir.name}"
        if source_repo == run_workspace
        else f"vega run --run {snapshot.target.run_dir.name}"
    )
    return {
        "run_id": snapshot.target.run_dir.name,
        "run_workspace": str(run_workspace),
        "source_repo": str(source_repo or "<target-repo>"),
        "interaction_id": (
            interactions[0].interaction_id if interactions else "<request-id>"
        ),
        "continue_command": continue_command,
    }


def _source_repo(snapshot: AgentCliSnapshot) -> Path | None:
    try:
        metadata = load_run_metadata(snapshot.target.run_dir)
    except ValueError:
        return None
    change_run = metadata.get("change_run")
    source = (
        change_run.get("source_repo_path")
        if isinstance(change_run, dict)
        else None
    )
    return Path(source).resolve() if isinstance(source, str) and source else None


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
