from __future__ import annotations

import hashlib
from pathlib import Path

from .agent_change_handoff import (
    prepare_resumed_change_workspace,
    prepare_resumed_planning_workspace,
)
from .agent_change_run import (
    change_run_metadata,
    save_change_run_artifacts,
)
from .agent_contract import AgentState
from .agent_persistence import append_agent_trace, save_agent_state
from .agent_planning import (
    PLANNING_CONTEXT_ARTIFACT,
    PLANNING_PROPOSAL_ARTIFACT,
    PLANNING_REPORT_ARTIFACT,
    PLANNING_REQUEST_ARTIFACT,
    PlanningRequest,
    render_planning_proposal,
    validate_planning_proposal,
    validate_published_planning_proposal,
)
from .agent_repository_binding import require_git_root, write_run_metadata
from .agent_repository_guard import release_task_card_resume_claim
from .agent_resume_validation import (
    require_resume_repository_identity,
    validate_resume_workspace,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    load_agent_bundle,
    save_agent_plan,
    write_checkpoint,
    write_status_card,
    write_task_brief,
)
from .agent_task_card import AgentTaskCard, task_card_content_digest
from .agent_task_card_resume import (
    create_claimed_resume_run,
    load_task_card_with_content,
    resolve_resume_task,
    state_from_task_card,
)
from .project_context import build_project_context
from .redaction import write_redacted_json, write_redacted_text
from .workspace_inventory import prepare_verification_temp_root


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
        planning_workspace = (
            prepare_resumed_planning_workspace(
                workspace,
                repo_root,
                run_id=run_id,
                card=card,
                relative_task=relative_task,
                handoff_revision=snapshot.head_sha,
            )
            if card.planning_run is not None
            else None
        )
        managed_workspace = change_workspace or planning_workspace
        bound_repository = (
            managed_workspace.handle.worktree_path
            if managed_workspace is not None
            else repo_root
        )
        current_snapshot = (
            managed_workspace.snapshot
            if managed_workspace is not None
            else snapshot
        )
        if managed_workspace is not None:
            prepare_verification_temp_root(bound_repository)
        state = state_from_task_card(
            run_id,
            bound_repository,
            card,
            current_snapshot,
            accepted_checkpoint_sha=(
                managed_workspace.accepted_checkpoint_sha
                if managed_workspace is not None
                else None
            ),
        )
        resumed_failed_attempts = list(
            dict.fromkeys(
                [
                    *card.failed_attempts,
                    *(capsule.failed_attempts if capsule else []),
                ]
            )
        )
        comparison_base = (
            managed_workspace.accepted_checkpoint_sha
            if managed_workspace is not None
            else (
                capsule.comparison_base_revision or card.handoff_base_revision
            )
            if capsule is not None and changed_files
            else snapshot.head_sha
        )
        write_run_metadata(
            run_dir,
            bound_repository,
            card.base_revision if managed_workspace is not None else snapshot.head_sha,
            task_card=relative_task,
            task_card_sha256=task_card_sha256,
            comparison_base_revision=comparison_base,
            comparison_paths=[] if managed_workspace is not None else changed_files,
            change_run=(
                change_run_metadata(managed_workspace.handle)
                if managed_workspace is not None
                else None
            ),
        )
        if card.change_run is not None:
            save_change_run_artifacts(
                run_dir,
                card.change_run.contract,
                card.change_run.execution_plan,
            )
        planning_refs = _restore_planning_resume_artifacts(
            workspace,
            run_dir,
            bound_repository,
            card,
        )
        save_agent_plan(run_dir, card.plan)
        checkpoint = write_checkpoint(
            run_dir,
            state,
            current_snapshot,
            reason=(
                "已从 Git 跟踪的 Task Card 恢复 Planning Proposal"
                if card.planning_run is not None
                else "已从 Git 跟踪的 Resume Capsule 建立新本机 run"
            ),
            status="safe" if card.handoff_status == "handoff_ready" else "blocked",
            pending_actions=list(state.allowed_actions),
            evidence_refs=[relative_task, *planning_refs],
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
        if card.planning_run is None:
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
            event=(
                "planning_task_card_resumed"
                if card.planning_run is not None
                else "task_card_resumed"
            ),
            state=state,
            observation_summary=(
                "Planning Proposal 已按固定 source revision 恢复"
                if card.planning_run is not None
                else "旧门禁已作为历史证据，当前现场已重新对账"
            ),
            artifact_refs=[
                relative_task,
                *planning_refs,
                *(["task-brief.md"] if card.planning_run is None else []),
            ],
        )
        write_status_card(
            run_dir,
            state,
            card.plan,
            checkpoint=checkpoint,
            next_step=_resume_next_step(card, state),
        )
        save_agent_state(run_dir / "agent-state.json", state)
        validated_dir, validated_state, _, _ = load_agent_bundle(workspace, run_id)
        if validated_dir != run_dir or validated_state != state:
            raise ValueError("恢复后的 Agent run 自校验结果不一致")
        if card.planning_run is not None:
            request = PlanningRequest.model_validate_json(
                (run_dir / PLANNING_REQUEST_ARTIFACT).read_text(encoding="utf-8")
            )
            validate_published_planning_proposal(
                run_dir,
                bound_repository,
                state,
                card.plan,
                request,
            )
        published = True
        return AgentRun(run_dir=run_dir, state=state, plan=card.plan)
    finally:
        if not published:
            release_task_card_resume_claim(
                repo_root,
                task_card_sha256=task_card_sha256,
                run_id=run_id,
            )


def _restore_planning_resume_artifacts(
    workspace: Path,
    run_dir: Path,
    repo: Path,
    card: AgentTaskCard,
) -> list[str]:
    planning_run = card.planning_run
    if planning_run is None:
        return []
    proposal = planning_run.proposal
    validate_planning_proposal(
        repo,
        proposal,
        task_id=card.task_id,
        user_goal=card.plan.user_goal,
        source_revision=planning_run.source_revision,
    )
    context = build_project_context(
        workspace,
        repo,
        card.plan.user_goal,
        tracked_revision=planning_run.source_revision,
    )
    write_redacted_json(
        run_dir / PLANNING_REQUEST_ARTIFACT,
        PlanningRequest(
            task_id=card.task_id,
            user_goal=card.plan.user_goal,
            source_revision=planning_run.source_revision,
            project_context_sha256=hashlib.sha256(context.encode("utf-8")).hexdigest(),
        ).model_dump(mode="json"),
    )
    write_redacted_text(run_dir / PLANNING_CONTEXT_ARTIFACT, context)
    write_redacted_json(
        run_dir / PLANNING_PROPOSAL_ARTIFACT,
        proposal.model_dump(mode="json"),
    )
    write_redacted_text(
        run_dir / PLANNING_REPORT_ARTIFACT,
        render_planning_proposal(proposal),
    )
    return [
        PLANNING_REQUEST_ARTIFACT,
        PLANNING_CONTEXT_ARTIFACT,
        PLANNING_PROPOSAL_ARTIFACT,
        PLANNING_REPORT_ARTIFACT,
    ]


def _resume_next_step(card: AgentTaskCard, state: AgentState) -> str:
    if card.planning_run is not None:
        if state.phase == "needs_human":
            return "Planning Handoff 仍需人工核对；确认现场后再决定是否重新调查或编译合同"
        return "Planning Proposal 已恢复；等待编译为可批准 Change Contract"
    if card.resume_capsule is not None:
        return card.resume_capsule.next_step
    return "人工确认当前 Work Item 后继续"
