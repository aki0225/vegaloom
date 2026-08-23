from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .agent_contract import (
    AgentCheckpoint,
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentStatusCard,
)
from .agent_persistence import load_agent_checkpoint
from .agent_repository_binding import capture_bound_workspace
from .agent_run_status import read_live_child_stage, trusted_worker_label
from .agent_status_evidence import build_supervisor_evidence
from .agent_visibility import render_agent_status_card
from .redaction import redact_text, write_redacted_text
from .workspace_snapshot import ReviewWorkspaceSnapshot


_SNAPSHOT_NOTICE = (
    "> 这是生成时快照；实时状态请使用 `vega agent status --run <run-id>` 查看。\n\n"
)


def write_status_card(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    observation: AgentObservation | None = None,
    checkpoint: AgentCheckpoint | None = None,
    next_step: str | None = None,
) -> None:
    """生成主会话状态卡，不改变 Agent 的权威状态。"""

    checkpoint = checkpoint or _load_status_checkpoint(run_dir, state)
    card = _build_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
        next_step=next_step,
    )
    write_redacted_text(
        run_dir / "status-card.md",
        _SNAPSHOT_NOTICE + render_agent_status_card(card),
    )


def read_status_card(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
) -> str:
    """按当前 Artifact 重新渲染状态卡，避免重复旧的通过结论。"""

    checkpoint, checkpoint_issue = _load_status_checkpoint_for_display(
        run_dir,
        state,
    )
    observation, observation_issue = _load_status_observation_for_display(
        run_dir,
        state,
        checkpoint,
    )
    live_workspace, workspace_issue = _capture_live_workspace(run_dir)
    card = _build_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
        live_workspace=live_workspace,
        workspace_checked=True,
        workspace_issue=workspace_issue,
        checkpoint_issue=checkpoint_issue,
        observation_issue=observation_issue,
        next_step=(
            checkpoint.reason
            if checkpoint is not None and state.phase in {"ready", "needs_human"}
            else None
        ),
    )
    return redact_text(render_agent_status_card(card))


def _build_status_card(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    observation: AgentObservation | None,
    checkpoint: AgentCheckpoint | None,
    next_step: str | None,
    live_workspace: ReviewWorkspaceSnapshot | None = None,
    workspace_checked: bool = False,
    workspace_issue: str | None = None,
    checkpoint_issue: str | None = None,
    observation_issue: str | None = None,
) -> AgentStatusCard:
    current_index = next(
        (
            index
            for index, item in enumerate(plan.work_items, start=1)
            if item.work_item_id == state.current_work_item
        ),
        0,
    )
    current_item = next(
        (
            item
            for item in plan.work_items
            if item.work_item_id == state.current_work_item
        ),
        None,
    )
    worker_label = trusted_worker_label(
        run_dir,
        state,
        observation=observation,
        checkpoint_status=checkpoint.status if checkpoint else None,
    )
    supervisor_evidence = build_supervisor_evidence(
        run_dir,
        state,
        observation,
        plan,
    )
    expected_workspace_fingerprint = (
        state.workspace_fingerprint
        if state.handoff_status != "none"
        else observation.workspace_fingerprint
        if observation is not None
        else checkpoint.workspace_fingerprint
        if checkpoint is not None
        else state.workspace_fingerprint
    )
    workspace_current = (
        None
        if not workspace_checked
        else bool(
            live_workspace is not None
            and expected_workspace_fingerprint is not None
            and live_workspace.fingerprint == expected_workspace_fingerprint
        )
    )
    evidence_issue = checkpoint_issue or observation_issue
    evidence_health = _evidence_health(
        supervisor_evidence,
        observation,
        workspace_current=workspace_current,
        evidence_issue=evidence_issue,
    )
    commit_recommended = bool(
        state.phase == "completed"
        and state.terminal_status == "ready_to_commit"
        and checkpoint is not None
        and checkpoint.status == "safe"
        and checkpoint.external_side_effects == "none"
        and observation is not None
        and observation.verification == "passed"
        and observation.risk == "passed"
        and observation.review == "passed"
        and observation.external_side_effects == "none"
        and workspace_current is not False
        and evidence_health == "passed"
    )
    terminal_evidence_invalid = (
        state.phase == "completed"
        and state.terminal_status == "ready_to_commit"
        and not commit_recommended
    )
    workspace_blocks_automatic_action = (
        state.phase in {"ready", "finalizing", "completed"}
        and (workspace_issue is not None or workspace_current is False)
    )
    projection_requires_human = (
        evidence_issue is not None
        or terminal_evidence_invalid
        or workspace_blocks_automatic_action
    )
    effective_phase = "needs_human" if projection_requires_human else state.phase
    effective_next_step = (
        _terminal_integrity_next_step(
            workspace_current,
            workspace_issue,
            evidence_issue,
        )
        if projection_requires_human
        else next_step or default_next_step(state.phase, current_index)
    )
    integrity_warning = _integrity_warning(
        terminal_evidence_invalid=terminal_evidence_invalid,
        workspace_current=workspace_current,
        workspace_issue=workspace_issue,
        evidence_issue=evidence_issue,
    )
    return AgentStatusCard(
        run_id=state.run_id,
        task_id=state.task_id,
        phase=effective_phase,
        task_goal=plan.user_goal,
        work_item_label=(
            f"{state.current_work_item} / {len(plan.work_items)}"
            if state.current_work_item
            else "尚未选择"
        ),
        worker_label=worker_label,
        live_child_stage=read_live_child_stage(run_dir, state),
        changed_files=(
            list(live_workspace.changed_files)
            if workspace_checked and live_workspace is not None
            else observation.changed_files
            if observation is not None
            else checkpoint.changed_files
            if checkpoint is not None
            else []
        ),
        unknown_file_count=(
            len(live_workspace.untracked_files)
            if workspace_checked and live_workspace is not None
            else observation.unknown_file_count
            if observation
            else 0
        ),
        latest_checkpoint=state.latest_checkpoint_id,
        checkpoint_status=checkpoint.status if checkpoint else None,
        verification=observation.verification if observation else "not_run",
        risk=observation.risk if observation else "not_run",
        review=observation.review if observation else "not_run",
        terminal_status=(
            None if projection_requires_human else state.terminal_status
        ),
        allowed_actions=(
            ["human"] if projection_requires_human else list(state.allowed_actions)
        ),
        next_step=effective_next_step,
        evidence_health=evidence_health,
        workspace_current=workspace_current,
        commit_recommended=commit_recommended,
        integrity_warning=integrity_warning,
        plan_risk_notes=list(current_item.risk_notes) if current_item else [],
        supervisor_evidence=supervisor_evidence,
    )


def build_agent_status_payload(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
) -> dict[str, object]:
    """生成文本与 JSON 共用的经验证状态投影。"""

    checkpoint, checkpoint_issue = _load_status_checkpoint_for_display(
        run_dir,
        state,
    )
    observation, observation_issue = _load_status_observation_for_display(
        run_dir,
        state,
        checkpoint,
    )
    live_workspace, workspace_issue = _capture_live_workspace(run_dir)
    card = _build_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
        live_workspace=live_workspace,
        workspace_checked=True,
        workspace_issue=workspace_issue,
        checkpoint_issue=checkpoint_issue,
        observation_issue=observation_issue,
        next_step=(
            checkpoint.reason
            if checkpoint is not None and state.phase in {"ready", "needs_human"}
            else None
        ),
    )
    payload = card.model_dump(mode="json")
    payload.update(
        {
            "recorded_phase": state.phase,
            "recorded_terminal_status": state.terminal_status,
            "effective_phase": card.phase,
            "effective_terminal_status": card.terminal_status,
        }
    )
    return payload


def _evidence_health(
    evidence: list,
    observation: AgentObservation | None,
    *,
    workspace_current: bool | None,
    evidence_issue: str | None = None,
) -> str:
    if evidence_issue is not None:
        return "unverified"
    if workspace_current is False:
        return "stale"
    if observation is None:
        return "not_applicable"
    statuses = [item.status for item in evidence]
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    for status in ("failed", "stale", "unverified"):
        if status in statuses:
            return status
    return "unverified"


def _capture_live_workspace(
    run_dir: Path,
) -> tuple[ReviewWorkspaceSnapshot | None, str | None]:
    try:
        return capture_bound_workspace(run_dir), None
    except (OSError, RuntimeError, ValueError):
        return None, "当前 Workspace 无法重新采集或绑定无法验证"


def _terminal_integrity_next_step(
    workspace_current: bool | None,
    workspace_issue: str | None,
    evidence_issue: str | None = None,
) -> str:
    if evidence_issue is not None:
        return f"{evidence_issue.rstrip('。')}；不要继续自动执行，先检查对应 Artifact"
    if workspace_issue is not None:
        return "当前 Workspace 无法重新采集；不要提交，先检查仓库绑定和 Git 状态"
    if workspace_current is False:
        return "当前 Workspace 已偏离最终证据；不要提交，先重新对账并执行验证"
    return "持久化终态的当前证据无法重新验证；不要提交，先检查损坏或过期的 Artifact"


def _integrity_warning(
    *,
    terminal_evidence_invalid: bool,
    workspace_current: bool | None,
    workspace_issue: str | None,
    evidence_issue: str | None = None,
) -> str | None:
    if evidence_issue is not None:
        return evidence_issue
    if workspace_issue is not None:
        return workspace_issue
    if workspace_current is False:
        return "当前 Workspace 与最近 Observation 或 Checkpoint 不一致。"
    if terminal_evidence_invalid:
        return "持久化 State 记录过 ready_to_commit，但当前证据已失败、过期或无法验证。"
    return None


def _load_status_checkpoint(
    run_dir: Path,
    state: AgentState,
) -> AgentCheckpoint | None:
    if state.latest_checkpoint_id is None:
        return None
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.checkpoint_id != state.latest_checkpoint_id
        or checkpoint.current_work_item != state.current_work_item
    ):
        raise ValueError("最新 Checkpoint 与 Agent State 不一致，拒绝展示状态卡")
    return checkpoint


def _load_status_checkpoint_for_display(
    run_dir: Path,
    state: AgentState,
) -> tuple[AgentCheckpoint | None, str | None]:
    """读取展示用 Checkpoint；损坏证据不应让状态查询崩溃。"""

    if state.latest_checkpoint_id is None:
        if state.phase in {
            "ready",
            "acting",
            "observing",
            "needs_human",
            "finalizing",
            "completed",
            "stopped",
        }:
            return None, "最近 Checkpoint 缺失。"
        return None, None
    try:
        return _load_status_checkpoint(run_dir, state), None
    except ValueError:
        return None, "最近 Checkpoint 缺失、损坏或与 Agent State 绑定不一致。"


def _load_status_observation(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> AgentObservation | None:
    if checkpoint is None:
        return None
    refs = [
        ref
        for ref in checkpoint.evidence_refs
        if ref.startswith("observations/")
    ]
    if not refs:
        return None
    if len(refs) != 1:
        raise ValueError("最新 Checkpoint 无法唯一定位 Observation")
    try:
        root = run_dir.resolve(strict=True)
        path = (root / refs[0]).resolve(strict=True)
    except OSError as exc:
        raise ValueError("最新 Observation 不存在或无法读取，拒绝展示状态卡") from exc
    if not path.is_relative_to(root):
        raise ValueError("Observation 引用越过 Agent run 目录")
    try:
        observation = AgentObservation.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise ValueError("最新 Observation 无法验证，拒绝展示状态卡") from exc
    if (
        path.name != f"{observation.observation_id}.json"
        or observation.work_item_id != state.current_work_item
        or observation.workspace_fingerprint != checkpoint.workspace_fingerprint
    ):
        raise ValueError("最新 Observation 与 Checkpoint 不一致，拒绝展示状态卡")
    return observation


def _load_status_observation_for_display(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint | None,
) -> tuple[AgentObservation | None, str | None]:
    """读取展示用 Observation；损坏证据只降级展示，不改变权威 State。"""

    try:
        return _load_status_observation(run_dir, state, checkpoint), None
    except ValueError:
        return None, "最近 Observation 缺失、损坏或与 Checkpoint 绑定不一致。"


def default_next_step(phase: str, current_index: int) -> str:
    if phase == "awaiting_approval":
        return "人工审查当前 Plan revision"
    if phase == "ready":
        return f"准备执行第 {max(1, current_index)} 个 Work Item"
    if phase == "needs_human":
        return "查看最近 Observation 与 Checkpoint 后选择人工动作"
    if phase == "finalizing":
        return "调用现有 Vega Finish，Agent Graph 不能自行宣称成功"
    if phase == "completed":
        return "读取 Core Finish 结论并完成人工提交前检查；Vega 不自动执行 Git 操作"
    if phase == "stopped":
        return (
            "任务已停止；代码、Goal、Plan 和现场均保留。当前 run 不能使用 "
            "resume-local；如需继续，请人工创建 Handoff 或新的 Agent run"
        )
    return "查看结构化状态与允许动作"
