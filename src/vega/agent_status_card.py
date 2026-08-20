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
from .agent_run_status import read_live_child_stage, trusted_worker_label
from .agent_status_evidence import build_supervisor_evidence
from .agent_visibility import render_agent_status_card
from .redaction import redact_text, write_redacted_text


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

    checkpoint = _load_status_checkpoint(run_dir, state)
    observation = _load_status_observation(run_dir, state, checkpoint)
    card = _build_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
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
    return AgentStatusCard(
        run_id=state.run_id,
        task_id=state.task_id,
        phase=state.phase,
        task_goal=plan.user_goal,
        work_item_label=(
            f"{state.current_work_item} / {len(plan.work_items)}"
            if state.current_work_item
            else "尚未选择"
        ),
        worker_label=worker_label,
        live_child_stage=read_live_child_stage(run_dir, state),
        changed_files=(
            observation.changed_files
            if observation is not None
            else checkpoint.changed_files
            if checkpoint is not None
            else []
        ),
        unknown_file_count=observation.unknown_file_count if observation else 0,
        latest_checkpoint=state.latest_checkpoint_id,
        checkpoint_status=checkpoint.status if checkpoint else None,
        verification=observation.verification if observation else "not_run",
        risk=observation.risk if observation else "not_run",
        review=observation.review if observation else "not_run",
        terminal_status=state.terminal_status,
        allowed_actions=list(state.allowed_actions),
        next_step=next_step or default_next_step(state.phase, current_index),
        plan_risk_notes=list(current_item.risk_notes) if current_item else [],
        supervisor_evidence=build_supervisor_evidence(
            run_dir,
            state,
            observation,
            plan,
        ),
    )


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
