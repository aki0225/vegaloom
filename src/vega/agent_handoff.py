from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import agent_handoff_digest
from .agent_contract import (
    AgentCheckpoint,
)
from .agent_change_handoff import build_change_handoff_details
from .comparison_binding import require_comparison_binding_from_mapping
from .agent_handoff_safety import (
    collect_handoff_issues,
    require_plain_task_card_tree,
)
from .agent_handoff_support import (
    compact,
    current_work_item,
    ensure_no_existing_handoff,
    latest_observation,
    metadata_revision,
    prepare_task_card_parent,
    task_card_path,
    task_card_status,
    validate_handoff_bindings,
    validate_handoff_state,
)
from .agent_handoff_rendering import git_checklist, render_handoff_summary
from .agent_persistence import (
    append_agent_trace,
    append_agent_trace_commit,
    load_agent_checkpoint,
    read_optional_artifact,
    remove_artifact_if_published,
    restore_optional_artifact,
    save_agent_state,
)
from .agent_planning_handoff import load_planning_handoff_context
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    current_branch,
    load_agent_bundle,
    write_checkpoint,
    write_run_metadata,
    write_status_card,
)
from .agent_task_card import (
    AgentTaskCard,
    ResumeCapsule,
    render_task_card,
    save_task_card,
    task_card_content_digest,
)
from .agent_task_card_discovery import next_handoff_sequence
from .agent_status_history import historical_gate_evidence
from .redaction import sensitive_path_reason, write_redacted_json, write_redacted_text


@dataclass(frozen=True)
class HandoffResult:
    run: AgentRun
    checkpoint: AgentCheckpoint
    task_card_path: Path
    task_card_digest: str
    handoff_status: str


def create_handoff(
    workspace: Path,
    run: str,
    *,
    reason: str,
) -> HandoffResult:
    """把已经停止调度的 Agent run 生成可人工提交的 Task Card。"""

    if not reason.strip():
        raise ValueError("handoff 必须提供 reason")

    run_dir, state, plan, metadata = load_agent_bundle(workspace, run)
    validate_handoff_bindings(state)
    repo = bound_repo(run_dir)
    planning = load_planning_handoff_context(
        run_dir,
        repo,
        state,
        plan,
    )
    validate_handoff_state(state, plan, planning=planning.enabled)

    work_item = current_work_item(plan, state)
    planning_resume = planning.resume
    branch = current_branch(repo)
    source_task_card = metadata.get("task_card")
    previous_task_card = source_task_card if isinstance(source_task_card, str) else None
    ensure_no_existing_handoff(
        repo,
        state.task_id,
        branch,
        allowed_existing=previous_task_card,
    )
    card_path = task_card_path(repo, state.task_id)
    snapshot = capture_bound_workspace(run_dir)
    comparison_base_revision, comparison_paths = require_comparison_binding_from_mapping(
        metadata,
        base_key="comparison_base_revision",
    )
    change_handoff = build_change_handoff_details(
        workspace,
        run_dir,
        repo,
        state,
        plan,
        metadata,
        snapshot,
        legacy_comparison_base_revision=(
            comparison_base_revision
            or metadata_revision(metadata, snapshot.head_sha)
        ),
    )
    handoff_snapshot = change_handoff.snapshot
    latest = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    issues = collect_handoff_issues(state, latest, snapshot)
    sensitive_paths = [
        path
        for path in handoff_snapshot.changed_files
        if sensitive_path_reason(path) is not None
    ]
    if sensitive_paths:
        raise ValueError(
            "交接现场包含敏感路径，不能写入 Git Task Card："
            + ", ".join(sensitive_paths)
        )

    handoff_status = "handoff_ready" if not issues else "handoff_blocked"
    checkpoint_state = update_state(
        state,
        state_version=state.state_version + 1,
        workspace_fingerprint=snapshot.fingerprint,
        handoff_status=handoff_status,
        allowed_actions=["human"],
    )
    checkpoint = write_checkpoint(
        run_dir,
        checkpoint_state,
        snapshot,
        reason=f"准备 Handoff：{reason.strip()}",
        status="safe" if handoff_status == "handoff_ready" else "blocked",
        pending_actions=(
            []
            if handoff_status == "handoff_ready" and checkpoint_state.phase == "stopped"
            else ["human"]
        ),
        operation_started=False,
        external_side_effects=latest.external_side_effects,
    )

    observation, observation_time = latest_observation(run_dir)
    workspace_digest = agent_handoff_digest.compute_handoff_workspace_digest(
        repo, list(handoff_snapshot.changed_files),
        digest_kind=agent_handoff_digest.PORTABLE_WORKSPACE_DIGEST_KIND,
    )
    gate_evidence = historical_gate_evidence(
        observation,
        source_revision=snapshot.head_sha,
        recorded_at=observation_time,
    )
    comparison_base_revision = change_handoff.comparison_base_revision
    capsule = ResumeCapsule(
        current_work_item=state.current_work_item,
        stopped_at=f"{reason.strip()}；当前阶段：{state.phase}",
        confirmed_facts=compact(
            [
                *plan.observed_facts,
                f"交接基线 HEAD：{snapshot.head_sha}",
            ]
        ),
        unresolved_hypotheses=compact(
            [
                *plan.hypotheses,
                *(f"未决：{value}" for value in plan.unresolved_decisions),
            ]
        ),
        failed_attempts=compact(list(latest.failed_attempts)),
        restrictions=compact(
            [
                *plan.non_goals,
                *planning.non_goals,
                *(f"禁止路径：{value}" for value in work_item.forbidden_paths),
            ]
        ),
        risk_notes=compact(
            [
                *work_item.risk_notes,
                *planning.risk_notes,
                f"外部副作用状态：{latest.external_side_effects}",
                "ignored 文件不会进入 Task Card 或 Git 提交；新机器必须按项目说明重建",
                *issues,
            ]
        ),
        human_checks=compact(
            [
                "把 WIP 文件与 Task Card 一起提交前，执行 git diff --cached --check",
                "旧 Verification、Risk、Reviewer 结果在新机器上只能作为 historical 证据",
                "确认当前任务不依赖未提交的 ignored 文件或本机私密配置",
                *planning.verification,
                *work_item.verification,
            ]
        ),
        changed_files=list(handoff_snapshot.changed_files),
        comparison_base_revision=comparison_base_revision,
        workspace_digest_kind=agent_handoff_digest.PORTABLE_WORKSPACE_DIGEST_KIND,
        workspace_digest=workspace_digest,
        gate_evidence=gate_evidence,
        external_side_effects=latest.external_side_effects,
        writer_stopped=state.active_child_run is None,
        workspace_explained=not issues,
        allowed_actions=(
            ["human"]
            if handoff_status == "handoff_blocked"
            else ["replan", "human"]
            if planning_resume is not None
            else ["repair", "human"]
            if handoff_snapshot.changed_files
            else ["next", "human"]
        ),
        next_step=(
            "先人工核对 Task Card、WIP、进程和外部副作用；当前 Handoff blocked，不能自动启动 Worker"
            if handoff_status == "handoff_blocked"
            else (
                "新机器拉取包含 Task Card 的任务分支后，运行 vega resume --repo .，"
                "重新对账后再人工确认当前 Work Item"
            )
        ),
        recommended_command="vega resume --repo .",
    )
    card = AgentTaskCard(
        task_id=state.task_id,
        status=task_card_status(
            state,
            handoff_status,
            planning=planning_resume is not None,
        ),
        branch=branch,
        base_revision=metadata_revision(metadata, snapshot.head_sha),
        previous_task_card=previous_task_card,
        plan=plan,
        current_work_item=state.current_work_item,
        handoff_sequence=next_handoff_sequence(
            repo,
            state.task_id,
            branch,
            previous_task_card=previous_task_card,
        ),
        handoff_status=handoff_status,
        handoff_base_revision=snapshot.head_sha,
        handoff_workspace_digest=workspace_digest,
        last_handoff_checkpoint=checkpoint.checkpoint_id,
        progress_notes=compact(
            [
                latest.reason,
                *( [observation.machine_summary] if observation else []),
            ]
        ),
        failed_attempts=list(latest.failed_attempts),
        risk_notes=list(capsule.risk_notes),
        verification_notes=[
            f"{evidence.gate}: {evidence.status}（historical）"
            for evidence in gate_evidence
        ],
        resume_capsule=capsule,
        change_run=change_handoff.resume,
        planning_run=planning_resume,
    )

    card_content = render_task_card(card)
    card_digest = task_card_content_digest(card_content)
    relative_card = card_path.relative_to(repo).as_posix()

    published_state = update_state(
        checkpoint_state,
        latest_checkpoint_id=checkpoint.checkpoint_id,
        state_version=checkpoint_state.state_version + 1,
    )
    metadata_path = run_dir / "agent-run.json"
    manifest_path = run_dir / "handoff-manifest.json"
    summary_path = run_dir / "handoff-summary.md"
    state_path = run_dir / "agent-state.json"
    trace_path = run_dir / "trace.jsonl"
    status_path = run_dir / "status-card.md"
    checkpoint_path = (
        run_dir / "checkpoints" / f"{checkpoint.checkpoint_id}.json"
    )
    previous_metadata = metadata_path.read_bytes()
    previous_manifest = read_optional_artifact(manifest_path)
    previous_summary = read_optional_artifact(summary_path)
    previous_state = state_path.read_bytes()
    previous_status = status_path.read_bytes() if status_path.exists() else None
    card_published = False
    try:
        write_run_metadata(
            run_dir,
            repo,
            metadata_revision(metadata, snapshot.head_sha),
            task_card=relative_card,
            task_card_sha256=card_digest,
            comparison_base_revision=comparison_base_revision,
            comparison_paths=list(comparison_paths),
            change_run=(
                metadata["change_run"]
                if isinstance(metadata.get("change_run"), dict)
                else None
            ),
        )
        write_redacted_json(
            manifest_path,
            {
                "schema_version": 1,
                "run_id": state.run_id,
                "task_id": state.task_id,
                "task_card": relative_card,
                "task_card_sha256": card_digest,
                "handoff_status": handoff_status,
                "checkpoint_id": checkpoint.checkpoint_id,
                "handoff_base_revision": snapshot.head_sha,
                "handoff_workspace_digest": workspace_digest,
                "changed_files": list(handoff_snapshot.changed_files),
                "pending_git_actions": git_checklist(
                    relative_card,
                    handoff_snapshot.changed_files,
                    branch,
                ),
            },
        )
        write_redacted_text(
            summary_path,
            render_handoff_summary(
                card=card,
                card_path=relative_card,
                card_digest=card_digest,
                branch=branch,
                changed_files=handoff_snapshot.changed_files,
                issues=issues,
            ),
        )
        prepare_task_card_parent(repo, card_path)
        require_plain_task_card_tree(repo, card_path.parent)
        save_task_card(card_path, card)
        card_published = True
        post_handoff_snapshot = capture_bound_workspace(run_dir)
        published_state = update_state(
            published_state,
            state_version=published_state.state_version + 1,
            workspace_fingerprint=post_handoff_snapshot.fingerprint,
        )
        save_agent_state(state_path, published_state)
        write_status_card(
            run_dir,
            published_state,
            plan,
            checkpoint=checkpoint,
            next_step=capsule.next_step,
        )
        # Trace 是本次发布的最后提交点，避免后续失败留下无条件成功叙事。
        trace_artifacts = [
            relative_card,
            "handoff-manifest.json",
            "handoff-summary.md",
            f"checkpoints/{checkpoint.checkpoint_id}.json",
        ]
        append_agent_trace_commit(
            trace_path,
            event="agent_handoff_created",
            state=published_state,
            observation_summary=f"已生成 {relative_card}",
            route_reason=reason.strip(),
            artifact_refs=trace_artifacts,
            writer=append_agent_trace,
        )
    except Exception:
        restore_optional_artifact(state_path, previous_state)
        restore_optional_artifact(status_path, previous_status)
        restore_optional_artifact(metadata_path, previous_metadata)
        restore_optional_artifact(manifest_path, previous_manifest)
        restore_optional_artifact(summary_path, previous_summary)
        remove_artifact_if_published(card_path, card_published)
        checkpoint_path.unlink(missing_ok=True)
        raise
    return HandoffResult(
        run=AgentRun(run_dir=run_dir, state=published_state, plan=plan),
        checkpoint=checkpoint,
        task_card_path=card_path,
        task_card_digest=card_digest,
        handoff_status=handoff_status,
    )
