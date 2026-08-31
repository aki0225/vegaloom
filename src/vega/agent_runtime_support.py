from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .agent_checkpoint_history import inherited_failed_attempts
from .agent_context import (
    DEFAULT_TASK_BRIEF_MAX_BYTES,
    TaskBrief,
    compile_task_brief,
    task_brief_manifest,
)
from .agent_change_handoff import prepare_resumed_change_workspace
from .agent_change_run import (
    change_run_metadata,
    load_change_run_context,
    save_change_run_artifacts,
    task_brief_worker_verification,
)
from .agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentObservation,
    AgentPlan,
    AgentState,
    ObservationAuthority,
    canonical_digest,
)
from .agent_persistence import (
    AgentArtifactError,
    append_agent_trace,
    load_agent_checkpoint,
    load_agent_state,
    save_agent_state,
    save_agent_checkpoint,
)
from .agent_resume_validation import (
    current_branch as current_branch,
    require_resume_repository_identity,
    validate_resume_workspace,
)
from .agent_repository_binding import (
    bound_repo as _bound_repo,
    capture_bound_workspace as _capture_bound_workspace,
    load_run_metadata as _load_run_metadata,
    require_git_root,
    validate_run_repository_binding,
    write_run_metadata,
)
from .agent_repository_guard import (
    prepare_terminal_writer_claim_release,
    release_task_card_resume_claim,
    release_terminal_writer_claim,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_status_card import write_status_card as _write_status_card
from .agent_task_card import task_card_content_digest
from .agent_task_card_resume import (
    create_claimed_resume_run,
    load_task_card_with_content,
    resolve_resume_task,
    state_from_task_card,
)
from .redaction import write_redacted_json, write_redacted_text
from .run_utils import resolve_run_dir
from .workspace_inventory import prepare_verification_temp_root
from .workspace_snapshot import ReviewWorkspaceSnapshot


def capture_bound_workspace(run_dir: Path) -> ReviewWorkspaceSnapshot:
    return _capture_bound_workspace(run_dir)


def load_agent_bundle(
    workspace: Path,
    run: str,
) -> tuple[Path, AgentState, AgentPlan, dict[str, object]]:
    """读取 Agent 的三个权威本机 Artifact，并统一执行身份校验。"""

    run_dir = resolve_run_dir(workspace, run)
    try:
        state = load_agent_state(run_dir / "agent-state.json")
        plan = AgentPlan.model_validate_json(
            (run_dir / "agent-plan.json").read_text(encoding="utf-8")
        )
        metadata = _load_run_metadata(run_dir)
    except (OSError, ValidationError, json.JSONDecodeError, AgentArtifactError) as exc:
        raise ValueError(f"Agent run 无法恢复：{run_dir.name}") from exc
    if state.run_id != run_dir.name or plan.task_id != state.task_id:
        raise ValueError("Agent run 身份绑定不一致")
    if (
        state.run_kind == "legacy" or state.contract_revision is not None
    ) and state.phase not in {"planning", "awaiting_approval"}:
        if (
            state.goal_revision != plan.goal_revision
            or state.plan_revision != plan.plan_revision
            or state.approved_plan_digest != plan.approved_digest
            or not plan.approval_is_current()
        ):
            raise ValueError("Agent State 与当前批准 Plan 不一致")
    validate_run_repository_binding(run_dir, state, metadata)
    load_change_run_context(run_dir, state, plan, metadata)
    return run_dir, state, plan, metadata


def save_agent_plan(run_dir: Path, plan: AgentPlan) -> None:
    write_redacted_json(run_dir / "agent-plan.json", plan.model_dump(mode="json"))


def bound_repo(run_dir: Path) -> Path:
    return _bound_repo(run_dir)


def resume_agent_task_card(
    workspace: Path,
    repo: Path,
    task_path: Path | None = None,
) -> AgentRun:
    repo_root = require_git_root(repo)
    resolved_task, relative_task = resolve_resume_task(repo_root, task_path)
    card, task_card_content = load_task_card_with_content(resolved_task)
    task_card_sha256 = task_card_content_digest(task_card_content)
    # 新机器不会携带空的运行目录；先重建 Vega 自己的固定根路径，再冻结新现场。
    prepare_verification_temp_root(repo_root)
    snapshot = validate_resume_workspace(
        repo_root,
        card,
        relative_task=relative_task,
        task_card_content=task_card_content,
    )
    require_resume_repository_identity(
        repo_root,
        expected_head_sha=snapshot.head_sha,
        expected_branch=card.branch,
    )
    run_id, run_dir = create_claimed_resume_run(
        workspace,
        repo_root,
        task_card_sha256=task_card_sha256,
        task_card=relative_task,
    )
    published = False
    try:
        capsule = card.resume_capsule
        changed_files = list(capsule.changed_files) if capsule else []
        change_workspace = (
            prepare_resumed_change_workspace(
                workspace,
                repo_root,
                run_id=run_id,
                card=card,
                relative_task=relative_task,
                handoff_revision=snapshot.head_sha,
            )
            if card.change_run is not None
            else None
        )
        bound_repository = (
            change_workspace.handle.worktree_path
            if change_workspace is not None
            else repo_root
        )
        current_snapshot = (
            change_workspace.snapshot
            if change_workspace is not None
            else snapshot
        )
        if change_workspace is not None:
            prepare_verification_temp_root(bound_repository)
        state = state_from_task_card(
            run_id,
            bound_repository,
            card,
            current_snapshot,
            accepted_checkpoint_sha=(
                change_workspace.accepted_checkpoint_sha
                if change_workspace is not None
                else None
            ),
        )
        resumed_failed_attempts = list(dict.fromkeys(
            [*card.failed_attempts, *(capsule.failed_attempts if capsule else [])]
        ))
        comparison_base = (
            change_workspace.accepted_checkpoint_sha
            if change_workspace is not None
            else (
                capsule.comparison_base_revision
                or card.handoff_base_revision
            )
            if capsule is not None and changed_files
            else snapshot.head_sha
        )
        write_run_metadata(
            run_dir,
            bound_repository,
            (
                card.base_revision
                if change_workspace is not None
                else snapshot.head_sha
            ),
            task_card=relative_task,
            task_card_sha256=task_card_sha256,
            comparison_base_revision=comparison_base,
            comparison_paths=[] if change_workspace is not None else changed_files,
            change_run=(
                change_run_metadata(change_workspace.handle)
                if change_workspace is not None
                else None
            ),
        )
        if card.change_run is not None:
            save_change_run_artifacts(
                run_dir,
                card.change_run.contract,
                card.change_run.execution_plan,
            )
        save_agent_plan(run_dir, card.plan)
        checkpoint = write_checkpoint(
            run_dir,
            state,
            current_snapshot,
            reason="已从 Git 跟踪的 Resume Capsule 建立新本机 run",
            status="safe" if card.handoff_status == "handoff_ready" else "blocked",
            pending_actions=list(state.allowed_actions),
            evidence_refs=[relative_task],
            failed_attempts=resumed_failed_attempts,
            external_side_effects=capsule.external_side_effects if capsule else "unknown",
        )
        state = update_state(
            state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=state.state_version + 1,
        )
        confirmed_facts = list(
            dict.fromkeys(
                [
                    *card.plan.observed_facts,
                    *(capsule.confirmed_facts if capsule else []),
                ]
            )
        )
        write_task_brief(
            run_dir,
            card.plan,
            state,
            checkpoint,
            confirmed_facts=confirmed_facts,
            failed_attempts=resumed_failed_attempts,
            artifact_refs=[relative_task],
        )
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="task_card_resumed",
            state=state,
            observation_summary="旧门禁已作为历史证据，当前现场已重新对账",
            artifact_refs=[relative_task, "task-brief.md"],
        )
        write_status_card(
            run_dir,
            state,
            card.plan,
            checkpoint=checkpoint,
            next_step=(
                capsule.next_step
                if capsule is not None
                else "人工确认当前 Work Item 后继续"
            ),
        )
        save_agent_state(run_dir / "agent-state.json", state)
        validated_dir, validated_state, _, _ = load_agent_bundle(
            workspace,
            run_id,
        )
        if validated_dir != run_dir or validated_state != state:
            raise ValueError("恢复后的 Agent run 自校验结果不一致")
        published = True
        return AgentRun(run_dir=run_dir, state=state, plan=card.plan)
    finally:
        if not published:
            release_task_card_resume_claim(
                repo_root,
                task_card_sha256=task_card_sha256,
                run_id=run_id,
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
    allow_work_item_advance: bool = False,
) -> AgentCheckpoint:
    checkpoint_numbers = [
        int(path.stem.removeprefix("checkpoint-"))
        for path in (run_dir / "checkpoints").glob("checkpoint-*.json")
        if path.stem.removeprefix("checkpoint-").isdigit()
    ]
    checkpoint_id = f"checkpoint-{max(checkpoint_numbers, default=0) + 1:03d}"
    previous_failed_attempts = inherited_failed_attempts(
        run_dir,
        state,
        allow_work_item_advance=allow_work_item_advance,
    )
    new_failed_attempts = failed_attempts or []
    cumulative_failed_attempts = [*dict.fromkeys(previous_failed_attempts + new_failed_attempts)]
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
        failed_attempts=cumulative_failed_attempts,
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
    artifact_refs: list[str] | None = None,
) -> TaskBrief:
    if not state.current_work_item:
        raise ValueError("当前 run 没有可编译的 Work Item")
    brief = compile_task_brief(
        plan=plan,
        work_item_id=state.current_work_item,
        checkpoint=checkpoint,
        confirmed_facts=confirmed_facts or (),
        failed_attempts=failed_attempts or (),
        artifact_refs=artifact_refs or (),
        worker_verification=task_brief_worker_verification(
            run_dir,
            state,
        ),
        max_bytes=DEFAULT_TASK_BRIEF_MAX_BYTES,
    )
    write_redacted_text(run_dir / "task-brief.md", brief.content)
    write_redacted_json(
        run_dir / "task-brief-manifest.json",
        task_brief_manifest(
            brief,
            plan=plan,
            state=state,
            checkpoint=checkpoint,
        ),
    )
    return brief


def validate_dispatch_artifacts(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
) -> None:
    """dispatch 前复核批准、Checkpoint 与 Task Brief 属于同一现场。"""

    if (
        not plan.approval_is_current()
        or state.goal_revision != plan.goal_revision
        or state.plan_revision != plan.plan_revision
        or state.approved_plan_digest != plan.approved_digest
    ):
        raise ValueError("当前 Plan 批准已过期或与 Agent State 不一致")
    if state.latest_checkpoint_id is None:
        raise ValueError("当前 ready State 缺少可验证 Checkpoint")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.status != "safe"
        or checkpoint.phase != "ready"
        or checkpoint.current_work_item != state.current_work_item
        or checkpoint.workspace_fingerprint != state.workspace_fingerprint
        or checkpoint.operation_started
        or checkpoint.external_side_effects != "none"
        or checkpoint.state_version + 1 != state.state_version
        or not {"next", "repair"}.intersection(checkpoint.pending_actions)
    ):
        raise ValueError("当前 ready State 没有匹配的 safe Checkpoint")
    try:
        brief = (run_dir / "task-brief.md").read_text(encoding="utf-8")
        manifest = json.loads(
            (run_dir / "task-brief-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("当前 ready State 缺少可验证 Task Brief") from exc
    expected = {
        "schema_version": 2,
        "utf8_bytes": len(brief.encode("utf-8")),
        "sha256": canonical_digest({"content": brief}),
        "goal_revision": plan.goal_revision,
        "plan_revision": plan.plan_revision,
        "approved_plan_digest": plan.approved_digest,
        "current_work_item": state.current_work_item,
        "checkpoint_id": checkpoint.checkpoint_id,
        "workspace_fingerprint": checkpoint.workspace_fingerprint,
    }
    if not isinstance(manifest, dict) or any(
        manifest.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("Task Brief 与当前 Plan、Checkpoint 或内容摘要不一致")


def write_status_card(
    run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    *,
    observation: AgentObservation | None = None,
    checkpoint: AgentCheckpoint | None = None,
    next_step: str | None = None,
) -> None:
    _write_status_card(
        run_dir, state, plan,
        observation=observation, checkpoint=checkpoint, next_step=next_step,
    )


def publish_observation_transition(
    run_dir: Path,
    previous_state: AgentState,
    state: AgentState,
    plan: AgentPlan,
    observation: AgentObservation,
    decision: AgentDecision,
    checkpoint: AgentCheckpoint,
    authority: ObservationAuthority,
    *,
    next_step: str,
) -> None:
    """按 claim、Plan、State、Trace、状态卡的安全顺序发布对账结果。"""

    repo = bound_repo(run_dir)
    prepare_terminal_writer_claim_release(
        repo, previous_state, state, observation, authority
    )
    save_agent_plan(run_dir, plan)
    save_agent_state(run_dir / "agent-state.json", state)
    append_agent_trace(
        run_dir / "trace.jsonl",
        event=f"supervisor_{decision.selected_action}",
        state=state,
        observation_summary=observation.machine_summary,
        route_reason=decision.reason,
        artifact_refs=[
            f"observations/{observation.observation_id}.json",
            f"decisions/{decision.decision_id}.json",
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ],
    )
    write_status_card(
        run_dir,
        state,
        plan,
        observation=observation,
        checkpoint=checkpoint,
        next_step=next_step,
    )
    release_terminal_writer_claim(
        repo, previous_state, state, observation, authority
    )
