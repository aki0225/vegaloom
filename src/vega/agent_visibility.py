from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentStatusCard,
)
from .agent_repository_binding import bound_repo, load_run_metadata
from .redaction import write_redacted_json, write_redacted_text
from .run_utils import resolve_run_dir

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
_SUPERVISOR_EVIDENCE_LABELS = {
    "passed": "通过",
    "failed": "未通过",
    "stale": "已过期",
    "unverified": "未验证",
}
_CHECKPOINT_LABELS = {
    "safe": "现场可解释",
    "uncertain": "现场不确定",
    "blocked": "现场阻断",
}
_ACTION_LABELS = {
    "next": "进入下一 Work Item",
    "repair": "在原范围内修复",
    "replan": "重新调查并修订计划",
    "human": "交由人工判断",
    "finalize": "进入 Finish",
}

def render_agent_status_card(card: AgentStatusCard) -> str:
    checkpoint = "尚无"
    if card.latest_checkpoint:
        status = (
            _CHECKPOINT_LABELS[card.checkpoint_status]
            if card.checkpoint_status is not None
            else "状态未知"
        )
        checkpoint = f"{card.latest_checkpoint} / {status}"
    changed_files = (
        "无"
        if not card.changed_files
        else f"{len(card.changed_files)} 个（{', '.join(card.changed_files[:5])}"
        f"{' 等' if len(card.changed_files) > 5 else ''}）"
    )
    allowed = (
        "无"
        if not card.allowed_actions
        else "、".join(_ACTION_LABELS[action] for action in card.allowed_actions)
    )
    lines = [
        "# Vega Agent",
        "",
        *(
            [
                f"> **证据告警**：{card.integrity_warning}",
                "",
            ]
            if card.integrity_warning is not None
            else []
        ),
        f"- 运行：`{card.run_id}`",
        f"- 阶段：{_PHASE_LABELS[card.phase]}",
        f"- 任务：{card.task_goal}",
        f"- Work Item：{card.work_item_label}",
        f"- Worker：{card.worker_label}",
        *(
            [f"- Core 子流程：`{card.live_child_stage}`"]
            if card.live_child_stage is not None
            else []
        ),
        f"- Workspace：{changed_files}；未知文件 {card.unknown_file_count} 个",
        f"- 最近 Checkpoint：{checkpoint}",
        f"- Verification：{_GATE_LABELS[card.verification]}",
        f"- Risk：{_GATE_LABELS[card.risk]}",
        f"- Reviewer：{_GATE_LABELS[card.review]}",
        *(
            [f"- 历史：{card.history_note}"]
            if card.history_note is not None
            else []
        ),
        f"- 证据健康：`{card.evidence_health}`",
        *(
            [
                "- Workspace 与证据一致："
                f"`{'是' if card.workspace_current else '否'}`"
            ]
            if card.workspace_current is not None
            else []
        ),
        f"- 建议提交：`{'是' if card.commit_recommended else '否'}`",
        f"- 允许动作：{allowed}",
    ]
    if card.terminal_status is not None:
        lines.append(f"- Finish：`{card.terminal_status}`")
    lines.append(f"- 下一步：{card.next_step}")
    if card.provider_session_warning is not None:
        lines.extend(["", f"> 会话状态：{card.provider_session_warning}"])
    lines.extend(_render_provider_sessions(card))
    if card.plan_risk_notes:
        lines.extend(
            [
                "",
                "## 计划风险提示",
                "- 以下内容来自当前 Work Item 的批准 Plan，仅供人工关注，不改变 Risk Gate 结果。",
                *[f"- {note}" for note in card.plan_risk_notes],
            ]
        )
    if card.supervisor_evidence:
        lines.extend(
            [
                "",
                "## Supervisor 证据",
                *[
                    f"- {item.label}：{_SUPERVISOR_EVIDENCE_LABELS[item.status]}；"
                    f"{item.detail}"
                    for item in card.supervisor_evidence
                ],
            ]
        )
    return "\n".join(lines).rstrip() + "\n"

def _render_provider_sessions(card: AgentStatusCard) -> list[str]:
    if not card.provider_sessions:
        return []
    lines = ["", "## Provider Sessions"]
    for session in card.provider_sessions:
        usage = ""
        if session.total_tokens is not None:
            usage = f"；tokens={session.total_tokens}"
            if session.cached_input_tokens is not None:
                usage += f"，cache={session.cached_input_tokens}"
            if session.context_window is not None:
                usage += f"，window={session.context_window}"
        lines.append(
            f"- `{session.role}`：{session.lifecycle}；owner={session.owner}；"
            f"turns={session.turn_count}；compactions={session.compaction_count}；"
            f"thread=`{session.thread_id or '尚未建立'}`；"
            f"待响应={session.pending_interactions}；"
            f"待发送 steer={session.queued_steers}{usage}"
        )
    return lines

def write_agent_final_report(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    observation: AgentObservation,
) -> None:
    """只组合已验证 Artifact 与 Git 事实，不调用模型生成交付摘要。"""

    if state.accepted_checkpoint_sha is None:
        raise ValueError("completed ChangeRun 缺少 Accepted Checkpoint")
    metadata = load_run_metadata(run_dir)
    change_run = metadata.get("change_run")
    base_sha = (
        change_run.get("base_revision")
        if isinstance(change_run, dict)
        else metadata.get("base_revision")
    )
    if not isinstance(base_sha, str) or not base_sha:
        raise ValueError("Agent Final Report 缺少基线 revision")
    changed_files, diff_stat = _git_change_summary(
        bound_repo(run_dir),
        base_sha,
        state.accepted_checkpoint_sha,
    )
    child_finish = _load_final_child_finish(workspace, observation)
    first_screen = child_finish.get("first_screen")
    first_screen = first_screen if isinstance(first_screen, dict) else {}
    actual_changes = _mapping(first_screen.get("actual_changes"))
    core_gates = _mapping(first_screen.get("gates"))
    verification = _mapping(first_screen.get("verification"))
    review = _mapping(first_screen.get("review"))
    integration_review = _load_integration_review(
        run_dir,
        observation,
        state.accepted_checkpoint_sha,
    )
    payload = {
        "schema_version": 1,
        "run_id": state.run_id,
        "task_id": state.task_id,
        "finish_status": state.terminal_status,
        "contract_revision": state.contract_revision,
        "execution_plan_revision": state.execution_plan_revision,
        "candidate": {
            "base_sha": base_sha,
            "accepted_sha": state.accepted_checkpoint_sha,
            "changed_files": changed_files,
            "changed_file_count": len(changed_files),
            "diff_stat": diff_stat,
        },
        "work_items": [
            {
                "work_item_id": item.work_item_id,
                "objective": item.objective,
                "status": item.status,
            }
            for item in plan.work_items
        ],
        "worker_claim": observation.worker_claim,
        "machine_summary": observation.machine_summary,
        "supervisor_gates": {
            "verification": observation.verification,
            "risk": observation.risk,
            "review": observation.review,
            "external_side_effects": observation.external_side_effects,
        },
        "core_finish": {
            "gates": core_gates,
            "verification": verification,
            "risk": {
                "gate": core_gates.get("risk"),
                "required_reviews": actual_changes.get("required_reviews", []),
                "high_risk_findings": actual_changes.get(
                    "high_risk_findings",
                    [],
                ),
            },
            "review": review,
            "evidence_limits": first_screen.get("evidence_limits", []),
        },
        "integration_review": integration_review,
        "review_priority_files": _review_priority_files(
            review,
            integration_review,
        ),
        "human_next_step": (
            "检查重要 Diff、Reviewer finding 与未证明风险，"
            "再决定是否 push、创建 PR 或合并。"
        ),
    }
    write_redacted_json(run_dir / "agent-final-report.json", payload)
    write_redacted_text(
        run_dir / "agent-final-report.md",
        _render_final_report(payload),
    )


def _git_change_summary(
    repo: Path,
    base_sha: str,
    accepted_sha: str,
) -> tuple[list[str], str]:
    comparison = f"{base_sha}..{accepted_sha}"
    names = subprocess.run(
        ["git", "diff", "--name-only", "--no-ext-diff", "--no-textconv", comparison, "--"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stat = subprocess.run(
        ["git", "diff", "--stat", "--no-ext-diff", "--no-textconv", comparison, "--"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if names.returncode != 0 or stat.returncode != 0:
        raise ValueError("无法从 Accepted Checkpoint 生成累计 Git 摘要")
    return [
        line.strip().replace("\\", "/")
        for line in names.stdout.splitlines()
        if line.strip()
    ], stat.stdout.strip()


def _load_final_child_finish(
    workspace: Path,
    observation: AgentObservation,
) -> dict[str, object]:
    if observation.child_run is None:
        raise ValueError("最终 Observation 缺少 child run")
    try:
        payload = json.loads(
            (
                resolve_run_dir(workspace, observation.child_run)
                / "finish-summary.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取最终 child Finish") from exc
    if not isinstance(payload, dict):
        raise ValueError("最终 child Finish 必须是 JSON object")
    return payload


def _load_integration_review(
    run_dir: Path,
    observation: AgentObservation,
    accepted_sha: str,
) -> dict[str, object] | None:
    refs = [
        ref
        for ref in observation.evidence_refs
        if ref.startswith("integration-reviews/")
    ]
    if not refs:
        return None
    if len(refs) != 1:
        raise ValueError("最终 Observation 绑定了多个集成审查结果")
    try:
        payload = json.loads((run_dir / refs[0]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取最终集成审查结果") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("candidate_sha") != accepted_sha
        or payload.get("status") != "approve"
    ):
        raise ValueError("最终集成审查没有绑定 Accepted Checkpoint")
    return payload


def _review_priority_files(
    review: dict[str, object],
    integration_review: dict[str, object] | None,
) -> list[str]:
    values: list[str] = []
    for path in review.get("priority_files", []) or []:
        if isinstance(path, str):
            values.append(path)
    for finding in review.get("findings", []) or []:
        if isinstance(finding, dict) and isinstance(finding.get("file"), str):
            values.append(str(finding["file"]))
    if integration_review is not None:
        for batch in integration_review.get("batches", []) or []:
            if not isinstance(batch, dict):
                continue
            verdict = _mapping(batch.get("verdict"))
            for finding in verdict.get("findings", []) or []:
                if isinstance(finding, dict) and isinstance(
                    finding.get("file"),
                    str,
                ):
                    values.append(str(finding["file"]))
    return list(dict.fromkeys(path for path in values if path))


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _render_final_report(payload: dict[str, object]) -> str:
    candidate = payload["candidate"]
    assert isinstance(candidate, dict)
    gates = payload["supervisor_gates"]
    assert isinstance(gates, dict)
    files = candidate["changed_files"]
    assert isinstance(files, list)
    core = payload["core_finish"]
    assert isinstance(core, dict)
    verification = _mapping(core.get("verification"))
    risk = _mapping(core.get("risk"))
    review = _mapping(core.get("review"))
    priority_files = payload.get("review_priority_files") or []
    lines = [
        "# Vega Agent 最终报告",
        "",
        f"- Run：`{payload['run_id']}`",
        f"- Finish：`{payload['finish_status']}`",
        f"- Candidate：`{str(candidate['accepted_sha'])[:12]}`",
        f"- 变更文件：{candidate['changed_file_count']} 个",
        f"- Verification：`{gates['verification']}`",
        f"- Risk：`{gates['risk']}`",
        f"- Reviewer：`{gates['review']}`",
        "",
        "## Reviewer 建议优先查看",
        *(
            [f"- `{path}`" for path in priority_files]
            if priority_files
            else ["- Reviewer 没有标记重点文件；请按完整变更清单检查。"]
        ),
        "",
        "## 变更文件",
        *([f"- `{path}`" for path in files] or ["- 无"]),
        "",
        "## Git 统计",
        "```text",
        str(candidate["diff_stat"] or "无"),
        "```",
        "",
        "## Worker Claim",
        str(payload["worker_claim"] or "未提供"),
        "",
        "## 验证",
        *_render_verification_summary(verification),
        "",
        "## 风险",
        *_render_risk_summary(risk),
        "",
        "## Reviewer",
        *_render_review_summary(review, payload.get("integration_review")),
        "",
        "## 证据边界",
        *[
            f"- {item}"
            for item in core.get("evidence_limits", []) or ["未记录"]
        ],
        "",
        "## 人工下一步",
        str(payload["human_next_step"]),
    ]
    return "\n".join(lines).rstrip() + "\n"

def _render_verification_summary(verification: dict[str, object]) -> list[str]:
    checks = verification.get("checks") or []
    lines = [
        f"- 最新受信通过：`{bool(verification.get('trusted_passed'))}`",
        f"- 最新失败：`{bool(verification.get('latest_failed'))}`",
    ]
    if not checks:
        return [*lines, "- 未记录可展示的验证命令；请检查 child Finish Artifact。"]
    for item in checks:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- `{item.get('status', 'unknown')}`："
            f"`{item.get('command', '<unknown>')}`；"
            f"exit={item.get('returncode')}"
        )
    return lines
def _render_risk_summary(risk: dict[str, object]) -> list[str]:
    gate = _mapping(risk.get("gate"))
    lines = [
        f"- Gate：`{gate.get('status', 'unknown')}`；"
        f"level=`{gate.get('level') or '未记录'}`"
    ]
    required = risk.get("required_reviews") or []
    if not required:
        return [*lines, "- 没有命中已记录的必审风险领域。"]
    for item in required:
        if isinstance(item, dict):
            lines.append(
                f"- `{item.get('id', 'unknown')}`："
                + "、".join(
                    f"`{path}`" for path in item.get("matched_files", []) or []
                )
            )
    return lines
def _render_review_summary(
    review: dict[str, object], integration_review: object
) -> list[str]:
    lines = [f"- Work Item Reviewer：`{review.get('verdict', 'unknown')}`"]
    for finding in review.get("findings", []) or []:
        if isinstance(finding, dict):
            lines.append(
                f"- `{finding.get('severity', 'unknown')}` "
                f"{_finding_location(finding)}：{finding.get('title', '未命名 finding')}"
            )
    if not isinstance(integration_review, dict):
        return [*lines, "- 本次条件未触发最终集成 Reviewer。"]
    lines.append(f"- 最终集成 Reviewer：`{integration_review.get('status')}`")
    for batch in integration_review.get("batches", []) or []:
        if not isinstance(batch, dict):
            continue
        verdict = _mapping(batch.get("verdict"))
        for finding in verdict.get("findings", []) or []:
            if isinstance(finding, dict):
                lines.append(
                    f"- 集成 `{finding.get('severity', 'unknown')}` "
                    f"{_finding_location(finding)}："
                    f"{finding.get('title', '未命名 finding')}"
                )
    return lines

def _finding_location(finding: dict[str, object]) -> str:
    path = str(finding.get("file") or "<unknown>")
    line = finding.get("line")
    if isinstance(line, int) and line > 0:
        return f"`{path}:{line}`"
    return f"`{path}`（Reviewer 未提供行号）"
