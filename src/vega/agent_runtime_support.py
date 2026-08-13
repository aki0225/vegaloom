from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .agent_context import (
    DEFAULT_TASK_BRIEF_MAX_BYTES,
    TaskBrief,
    compile_task_brief,
    task_brief_manifest,
)
from .agent_contract import (
    AgentCheckpoint,
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentStatusCard,
)
from .agent_persistence import (
    AgentArtifactError,
    load_agent_checkpoint,
    load_agent_state,
    save_agent_checkpoint,
)
from .agent_task_card import (
    AgentTaskCard,
    compute_handoff_workspace_digest,
    discover_handoff_task_cards,
)
from .agent_visibility import render_agent_status_card
from .redaction import write_redacted_json, write_redacted_text
from .repository_identity import repository_scope
from .run_utils import resolve_run_dir
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


def require_git_root(repo: Path) -> Path:
    root = repo.resolve(strict=True)
    process = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0 or Path(process.stdout.strip()).resolve() != root:
        raise ValueError("目标目录必须是 Git 仓库根目录")
    return root


def current_branch(repo: Path) -> str:
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
        raise ValueError("当前 HEAD 不是任务分支")
    return branch


def resolve_resume_task(repo: Path, task_path: Path | None) -> tuple[Path, str]:
    if task_path is None:
        matches = discover_handoff_task_cards(repo)
        if not matches:
            raise ValueError("当前分支没有可恢复的 Task Card")
        if len(matches) > 1:
            choices = ", ".join(path.relative_to(repo).as_posix() for path in matches)
            raise ValueError(f"当前分支有多个可恢复 Task Card，请使用 --task 选择：{choices}")
        task_path = matches[0]
    resolved_task = task_path.resolve(strict=True)
    try:
        relative_task = resolved_task.relative_to(repo).as_posix()
    except ValueError as exc:
        raise ValueError("Task Card 必须位于目标仓库内") from exc
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative_task],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if tracked.returncode != 0:
        raise ValueError("跨机器恢复只接受 Git 已跟踪的 Task Card")
    return resolved_task, relative_task


def capture_bound_workspace(run_dir: Path) -> ReviewWorkspaceSnapshot:
    metadata = json.loads((run_dir / "agent-run.json").read_text(encoding="utf-8"))
    repo = Path(metadata["repo_path"]).resolve(strict=True)
    return capture_review_workspace(repo)


def load_agent_bundle(
    workspace: Path,
    run: str,
) -> tuple[Path, AgentState, AgentPlan, dict[str, str]]:
    """读取 Agent 的三个权威本机 Artifact，并统一执行身份校验。"""

    run_dir = resolve_run_dir(workspace, run)
    try:
        state = load_agent_state(run_dir / "agent-state.json")
        plan = AgentPlan.model_validate_json(
            (run_dir / "agent-plan.json").read_text(encoding="utf-8")
        )
        metadata = json.loads((run_dir / "agent-run.json").read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError, AgentArtifactError) as exc:
        raise ValueError(f"Agent run 无法恢复：{run_dir.name}") from exc
    if state.run_id != run_dir.name or plan.task_id != state.task_id:
        raise ValueError("Agent run 身份绑定不一致")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("repo_path"), str):
        raise ValueError("Agent run 缺少 repo binding")
    return run_dir, state, plan, metadata


def save_agent_plan(run_dir: Path, plan: AgentPlan) -> None:
    write_redacted_json(run_dir / "agent-plan.json", plan.model_dump(mode="json"))


def bound_repo(run_dir: Path) -> Path:
    metadata = json.loads((run_dir / "agent-run.json").read_text(encoding="utf-8"))
    return Path(metadata["repo_path"]).resolve(strict=True)


def write_run_metadata(
    run_dir: Path,
    repo: Path,
    base_revision: str,
    *,
    task_card: str | None = None,
) -> None:
    write_redacted_json(
        run_dir / "agent-run.json",
        {
            "schema_version": 1,
            "run_id": run_dir.name,
            "repo_path": str(repo),
            "base_revision": base_revision,
            "task_card": task_card,
        },
    )


def write_checkpoint(
    run_dir: Path,
    state: AgentState,
    snapshot: ReviewWorkspaceSnapshot,
    *,
    reason: str,
    status: str,
    pending_actions: list[str],
    evidence_refs: list[str] | None = None,
    completed_attempts: list[str] | None = None,
    failed_attempts: list[str] | None = None,
    operation_started: bool | None = None,
    external_side_effects: Literal["none", "known", "unknown"] | None = None,
) -> AgentCheckpoint:
    checkpoints = sorted((run_dir / "checkpoints").glob("checkpoint-*.json"))
    checkpoint_id = f"checkpoint-{len(checkpoints) + 1:03d}"
    checkpoint = AgentCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=state.run_id,
        state_version=state.state_version,
        reason=reason,
        status=status,
        phase=state.phase,
        current_work_item=state.current_work_item,
        active_child_run=state.active_child_run,
        operation_started=(
            state.operation_started if operation_started is None else operation_started
        ),
        external_side_effects=external_side_effects or "none",
        workspace_fingerprint=snapshot.fingerprint,
        changed_files=list(snapshot.changed_files),
        completed_attempts=completed_attempts or [],
        failed_attempts=failed_attempts or [],
        pending_actions=pending_actions,
        evidence_refs=evidence_refs or [],
    )
    save_agent_checkpoint(
        run_dir / "checkpoints" / f"{checkpoint_id}.json",
        checkpoint,
    )
    return checkpoint


def write_task_brief(
    run_dir: Path,
    plan: AgentPlan,
    state: AgentState,
    checkpoint: AgentCheckpoint,
    *,
    confirmed_facts: list[str] | None = None,
    failed_attempts: list[str] | None = None,
) -> TaskBrief:
    if not state.current_work_item:
        raise ValueError("当前 run 没有可编译的 Work Item")
    brief = compile_task_brief(
        plan=plan,
        work_item_id=state.current_work_item,
        checkpoint=checkpoint,
        confirmed_facts=confirmed_facts or (),
        failed_attempts=failed_attempts or (),
        max_bytes=DEFAULT_TASK_BRIEF_MAX_BYTES,
    )
    write_redacted_text(run_dir / "task-brief.md", brief.content)
    write_redacted_json(
        run_dir / "task-brief-manifest.json",
        task_brief_manifest(brief),
    )
    return brief


def write_status_card(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    observation: AgentObservation | None = None,
    checkpoint: AgentCheckpoint | None = None,
    next_step: str | None = None,
) -> None:
    if checkpoint is None and state.latest_checkpoint_id:
        checkpoint = load_agent_checkpoint(
            run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
        )
    current_index = next(
        (
            index
            for index, item in enumerate(plan.work_items, start=1)
            if item.work_item_id == state.current_work_item
        ),
        0,
    )
    card = AgentStatusCard(
        run_id=state.run_id,
        task_id=state.task_id,
        phase=state.phase,
        task_goal=plan.user_goal,
        work_item_label=(
            f"{state.current_work_item} / {len(plan.work_items)}"
            if state.current_work_item
            else "尚未选择"
        ),
        worker_label=state.active_child_run or "未启动",
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
        allowed_actions=list(state.allowed_actions),
        next_step=next_step or default_next_step(state.phase, current_index),
    )
    write_redacted_text(run_dir / "status-card.md", render_agent_status_card(card))


def default_next_step(phase: str, current_index: int) -> str:
    if phase == "awaiting_approval":
        return "人工审查当前 Plan revision"
    if phase == "ready":
        return f"准备执行第 {max(1, current_index)} 个 Work Item"
    if phase == "needs_human":
        return "查看最近 Observation 与 Checkpoint 后选择人工动作"
    if phase == "finalizing":
        return "调用现有 Vega Finish，Agent Graph 不能自行宣称成功"
    return "查看结构化状态与允许动作"


def validate_resume_workspace(
    repo: Path,
    card: AgentTaskCard,
) -> ReviewWorkspaceSnapshot:
    if card.handoff_status == "none":
        raise ValueError("Task Card 没有可恢复交接")
    if card.branch != current_branch(repo):
        raise ValueError("Task Card 分支与当前分支不一致")
    snapshot = capture_review_workspace(repo)
    if snapshot.changed_files:
        raise ValueError("恢复前 Workspace 必须没有额外 Diff")
    if card.resume_capsule is None:
        raise ValueError("Task Card 缺少 Resume Capsule")
    expected_changed = set(card.resume_capsule.changed_files)
    observed_changed = set(snapshot.changed_files)
    if not observed_changed.issubset(expected_changed):
        unexpected = ", ".join(sorted(observed_changed - expected_changed))
        raise ValueError(f"恢复前存在交接未登记的 Workspace 变化：{unexpected}")
    current_digest = compute_handoff_workspace_digest(
        repo,
        card.resume_capsule.changed_files,
    )
    if card.handoff_workspace_digest != current_digest:
        raise ValueError(
            "当前 WIP 内容与交接摘要不一致；"
            "旧验证已降为历史，但现场仍必须先人工对账"
        )
    return snapshot


def state_from_task_card(
    run_id: str,
    repo: Path,
    card: AgentTaskCard,
    snapshot: ReviewWorkspaceSnapshot,
) -> AgentState:
    allowed_actions = (
        ["human"]
        if card.handoff_status == "handoff_blocked"
        else list(card.resume_capsule.allowed_actions)
        if card.resume_capsule
        else ["human"]
    )
    return AgentState(
        run_id=run_id,
        task_id=card.task_id,
        repository_id=repository_scope(repo),
        phase="needs_human" if card.handoff_status == "handoff_blocked" else "ready",
        goal_revision=card.plan.goal_revision,
        plan_revision=card.plan.plan_revision,
        approved_plan_digest=card.plan.approved_digest,
        current_work_item=card.current_work_item,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=allowed_actions,
        handoff_status=card.handoff_status,
    )
