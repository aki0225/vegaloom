from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_change_run import (
    ChangeRunContext,
    change_worktree_root,
    load_change_run_context,
)
from .agent_contract import AgentPlan, AgentState
from .agent_git_worktree import (
    ManagedChangeWorktree,
    create_resume_checkpoint,
    prepare_managed_worktree,
    restore_handoff_wip,
)
from .agent_handoff_digest import compute_handoff_workspace_digest
from .agent_task_card import AgentTaskCard, ChangeRunResume
from .agent_task_card_discovery import task_card_chain_paths
from .workspace_check import capture_review_workspace
from .workspace_inventory import workspace_ignored_path_exclusions
from .workspace_snapshot import ReviewWorkspaceSnapshot


@dataclass(frozen=True)
class ChangeHandoffDetails:
    snapshot: ReviewWorkspaceSnapshot
    comparison_base_revision: str
    resume: ChangeRunResume | None


@dataclass(frozen=True)
class ResumedChangeWorkspace:
    handle: ManagedChangeWorktree
    snapshot: ReviewWorkspaceSnapshot
    accepted_checkpoint_sha: str


def build_change_handoff_details(
    workspace: Path,
    run_dir: Path,
    repo: Path,
    state: AgentState,
    plan: AgentPlan,
    metadata: dict[str, object],
    current: ReviewWorkspaceSnapshot,
    *,
    legacy_comparison_base_revision: str,
) -> ChangeHandoffDetails:
    """把本机 ChangeRun 压缩成可提交的合同与 WIP 边界。"""

    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return ChangeHandoffDetails(
            snapshot=current,
            comparison_base_revision=legacy_comparison_base_revision,
            resume=None,
        )
    accepted_checkpoint = state.accepted_checkpoint_sha
    if accepted_checkpoint is None:
        raise ValueError("ChangeRun Handoff 缺少 Accepted Checkpoint")
    expected_head = state.active_candidate_sha or accepted_checkpoint
    if current.head_sha != expected_head:
        raise ValueError("ChangeRun Handoff 的 Git HEAD 与当前 Candidate 绑定不一致")
    snapshot = capture_review_workspace(
        repo,
        ignored_path_exclusions=workspace_ignored_path_exclusions(
            workspace,
            repo,
        ),
        comparison_base_sha=accepted_checkpoint,
    )
    if snapshot.head_sha != expected_head:
        raise ValueError("ChangeRun Handoff 采集期间 Git HEAD 已漂移")
    return ChangeHandoffDetails(
        snapshot=snapshot,
        comparison_base_revision=accepted_checkpoint,
        resume=_change_run_resume(context, state),
    )


def prepare_resumed_change_workspace(
    workspace: Path,
    source_repo: Path,
    *,
    run_id: str,
    card: AgentTaskCard,
    relative_task: str,
    handoff_revision: str,
) -> ResumedChangeWorkspace:
    """在新隔离 Worktree 中保留 Task Card 历史，并把代码恢复为待验证 WIP。"""

    change_run = card.change_run
    capsule = card.resume_capsule
    if change_run is None or capsule is None:
        raise ValueError("Task Card 缺少 ChangeRun Resume Capsule")
    handle = prepare_managed_worktree(
        source_repo,
        workspace_root=change_worktree_root(workspace, source_repo),
        run_id=run_id,
        base_revision=change_run.accepted_checkpoint_sha,
    )
    task_card_chain_paths(source_repo, card, relative_task)
    resumed_checkpoint = create_resume_checkpoint(
        handle,
        source_revision=handoff_revision,
        task_card_path=relative_task,
    )
    restore_handoff_wip(
        handle,
        handoff_revision=handoff_revision,
        restored_checkpoint_sha=resumed_checkpoint,
        changed_files=list(capsule.changed_files),
    )
    snapshot = capture_review_workspace(
        handle.worktree_path,
        comparison_base_sha=resumed_checkpoint,
    )
    if set(snapshot.changed_files) != set(capsule.changed_files):
        raise ValueError("恢复后的 ChangeRun WIP 文件与 Resume Capsule 不一致")
    # 新卡已在源 Handoff revision 上绑定 Git Blob；恢复只需验证签出的路径集合。
    # 旧卡没有 revision 级摘要，仍按恢复后的原始字节重新核对。
    if capsule.workspace_digest_kind == "workspace-bytes-v1" and (
        compute_handoff_workspace_digest(
            handle.worktree_path,
            list(capsule.changed_files),
            digest_kind=capsule.workspace_digest_kind,
        )
        != capsule.workspace_digest
    ):
        raise ValueError("恢复后的 ChangeRun WIP 内容与 Resume Capsule 不一致")
    return ResumedChangeWorkspace(
        handle=handle,
        snapshot=snapshot,
        accepted_checkpoint_sha=resumed_checkpoint,
    )


def _change_run_resume(
    context: ChangeRunContext,
    state: AgentState,
) -> ChangeRunResume:
    assert state.accepted_checkpoint_sha is not None
    return ChangeRunResume(
        contract=context.contract,
        execution_plan=context.execution_plan,
        accepted_checkpoint_sha=state.accepted_checkpoint_sha,
        historical_candidate_sha=state.active_candidate_sha,
    )
