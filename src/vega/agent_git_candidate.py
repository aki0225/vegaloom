from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from .agent_change_contract import (
    ChangeContract,
    ExecutionPlan,
    validate_execution_plan_against_contract,
)
from .agent_contract import NonEmptyText, Sha256Text, StrictAgentModel
from .agent_git_worktree import (
    GitCandidateError,
    ManagedChangeWorktree,
    validate_managed_branch_name,
)
from .scope_path_matching import (
    path_matches_pattern,
    scope_paths_are_case_insensitive,
)
from .tracked_workspace import (
    capture_tracked_scope_snapshot,
    collect_comparison_changed_paths,
    normalize_comparison_paths,
)
from .git_read import run_git_capture


GitOidText = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]


class CandidateCommit(StrictAgentModel):
    run_id: NonEmptyText
    work_item_id: NonEmptyText
    operation_id: NonEmptyText | None = None
    branch: NonEmptyText
    candidate_ref: NonEmptyText
    parent_sha: GitOidText
    candidate_sha: GitOidText
    contract_revision: int = Field(ge=1)
    approved_contract_digest: Sha256Text
    execution_plan_revision: int = Field(ge=1)
    changed_files: list[NonEmptyText] = Field(min_length=1)
    created_at: str

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        validate_managed_branch_name(value)
        return value

    @field_validator("changed_files")
    @classmethod
    def validate_changed_files(cls, values: list[str]) -> list[str]:
        try:
            normalized = list(normalize_comparison_paths(values))
        except ValueError as exc:
            raise ValueError("Candidate changed_files 包含不安全路径") from exc
        if len(set(normalized)) != len(normalized):
            raise ValueError("Candidate changed_files 不能重复")
        return normalized


def freeze_candidate_commit(
    handle: ManagedChangeWorktree,
    *,
    expected_parent_sha: str,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    work_item_id: str,
    operation_id: str | None = None,
) -> CandidateCommit:
    """在范围检查后由 Vega 控制器统一创建本地 Candidate Commit。"""

    _require_current_contract_and_item(
        contract,
        execution_plan,
        work_item_id,
    )
    handle.require_state(expected_parent_sha)
    changed_files = _collect_worker_changes(
        handle,
        contract=contract,
    )
    _stage_candidate(
        handle,
        expected_parent_sha=expected_parent_sha,
        changed_files=changed_files,
    )
    candidate_sha = _commit_candidate(
        handle,
        work_item_id=work_item_id,
    )
    candidate_ref = _preserve_candidate_ref(
        handle,
        work_item_id=work_item_id,
        candidate_sha=candidate_sha,
    )
    actual_files = _require_candidate_diff(
        handle,
        parent_sha=expected_parent_sha,
        candidate_sha=candidate_sha,
        expected_files=changed_files,
    )
    _require_clean_candidate_workspace(handle, candidate_sha)

    assert contract.approved_digest is not None
    candidate = CandidateCommit(
        run_id=handle.run_id,
        work_item_id=work_item_id,
        operation_id=operation_id,
        branch=handle.branch,
        candidate_ref=candidate_ref,
        parent_sha=expected_parent_sha,
        candidate_sha=candidate_sha,
        contract_revision=contract.contract_revision,
        approved_contract_digest=contract.approved_digest,
        execution_plan_revision=execution_plan.plan_revision,
        changed_files=actual_files,
        created_at=datetime.now(UTC).isoformat(),
    )
    validate_candidate_binding(
        handle,
        candidate=candidate,
        contract=contract,
        execution_plan=execution_plan,
    )
    return candidate


def validate_candidate_binding(
    handle: ManagedChangeWorktree,
    *,
    candidate: CandidateCommit,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> None:
    """重新确认 Candidate SHA、合同、计划、分支、Diff 和当前 Workspace。"""

    _require_candidate_metadata(
        handle,
        candidate=candidate,
        contract=contract,
        execution_plan=execution_plan,
    )
    handle.require_state(candidate.candidate_sha)
    parents = handle.git_text(
        ["git", "rev-list", "--parents", "-n", "1", candidate.candidate_sha]
    ).split()
    if parents != [candidate.candidate_sha, candidate.parent_sha]:
        raise GitCandidateError("Candidate 不是绑定 parent 的单父提交")

    actual_files = collect_comparison_changed_paths(
        handle.worktree_path,
        candidate.parent_sha,
        comparison_head_sha=candidate.candidate_sha,
    )
    if actual_files != candidate.changed_files:
        raise GitCandidateError("Candidate changed_files 与真实 Git Diff 不一致")
    _validate_candidate_scope(handle, actual_files, contract)
    _require_clean_candidate_workspace(handle, candidate.candidate_sha)


def restore_candidate_for_repair(
    handle: ManagedChangeWorktree,
    *,
    candidate: CandidateCommit,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> None:
    """把失败 Candidate 还原为 parent 上的可解释 WIP，供新的 Worker 接手。"""

    validate_candidate_binding(
        handle,
        candidate=candidate,
        contract=contract,
        execution_plan=execution_plan,
    )
    handle.run_write(
        ["git", "reset", "--mixed", candidate.parent_sha],
        "恢复失败 Candidate 的 WIP",
    )
    handle.require_state(candidate.parent_sha)
    snapshot = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    changed_files = list(
        normalize_comparison_paths(
            [
                *snapshot.staged_files,
                *snapshot.unstaged_files,
                *snapshot.untracked_files,
            ]
        )
    )
    if (
        snapshot.staged_files
        or snapshot.unsafe_index_paths
        or set(changed_files) != set(candidate.changed_files)
        or not _worktree_matches_candidate(handle, candidate.candidate_sha)
    ):
        raise GitCandidateError("失败 Candidate 无法安全恢复为同一份 WIP")


def _require_current_contract_and_item(
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
    work_item_id: str,
) -> None:
    if not contract.approval_is_current() or contract.approved_digest is None:
        raise GitCandidateError("Approved Contract 缺失或批准摘要已过期")
    validate_execution_plan_against_contract(contract, execution_plan)
    if work_item_id not in {
        item.work_item_id for item in execution_plan.work_items
    }:
        raise GitCandidateError("当前 Work Item 不属于 Execution Plan")


def _collect_worker_changes(
    handle: ManagedChangeWorktree,
    *,
    contract: ChangeContract,
) -> list[str]:
    if handle.git_bytes(["git", "ls-files", "-u", "-z"]):
        raise GitCandidateError("Workspace 存在未解决合并冲突")
    snapshot = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    if snapshot.unsafe_index_paths:
        raise GitCandidateError(
            "Workspace 存在 assume-unchanged 或 skip-worktree 标记"
        )
    changed_files = list(
        normalize_comparison_paths(
            [
                *snapshot.staged_files,
                *snapshot.unstaged_files,
                *snapshot.untracked_files,
            ]
        )
    )
    changed_files = list(dict.fromkeys(changed_files))
    if not changed_files:
        raise GitCandidateError("Worker 没有产生可冻结的代码变化")
    _validate_candidate_scope(handle, changed_files, contract)
    return changed_files


def _stage_candidate(
    handle: ManagedChangeWorktree,
    *,
    expected_parent_sha: str,
    changed_files: list[str],
) -> None:
    handle.run_write(
        [
            "git",
            "--literal-pathspecs",
            "add",
            "-A",
            "--",
            *changed_files,
        ],
        "暂存 Candidate",
    )
    staged = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    if staged.head_sha != expected_parent_sha:
        raise GitCandidateError("暂存 Candidate 时 HEAD 发生变化")
    if staged.unstaged_files or staged.untracked_files:
        raise GitCandidateError("暂存 Candidate 期间 Workspace 继续变化")
    if staged.unsafe_index_paths:
        raise GitCandidateError("暂存 Candidate 后出现不安全 Git index 标记")
    if set(staged.staged_files) != set(changed_files):
        raise GitCandidateError("暂存 Candidate 的路径集合与范围检查不一致")


def _commit_candidate(
    handle: ManagedChangeWorktree,
    *,
    work_item_id: str,
) -> str:
    handle.run_write(
        [
            "git",
            "-c",
            "user.name=Vega Checkpoint",
            "-c",
            "user.email=vega-checkpoint@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={handle.empty_hooks_dir()}",
            "commit",
            "--no-gpg-sign",
            "--no-verify",
            "-m",
            f"检查点：{_safe_label(work_item_id)} 候选变更",
        ],
        "创建 Candidate Commit",
    )
    return handle.git_text(["git", "rev-parse", "--verify", "HEAD"])


def _require_candidate_diff(
    handle: ManagedChangeWorktree,
    *,
    parent_sha: str,
    candidate_sha: str,
    expected_files: list[str],
) -> list[str]:
    actual_files = collect_comparison_changed_paths(
        handle.worktree_path,
        parent_sha,
        comparison_head_sha=candidate_sha,
    )
    if set(actual_files) != set(expected_files):
        raise GitCandidateError("Candidate Commit 的真实路径与冻结范围不一致")
    return actual_files


def _require_candidate_metadata(
    handle: ManagedChangeWorktree,
    *,
    candidate: CandidateCommit,
    contract: ChangeContract,
    execution_plan: ExecutionPlan,
) -> None:
    if (
        candidate.run_id != handle.run_id
        or candidate.branch != handle.branch
        or candidate.contract_revision != contract.contract_revision
        or candidate.execution_plan_revision != execution_plan.plan_revision
        or candidate.approved_contract_digest != contract.approved_digest
        or not contract.approval_is_current()
    ):
        raise GitCandidateError("Candidate 与当前 ChangeRun 合同或计划不一致")
    validate_execution_plan_against_contract(contract, execution_plan)
    resolved_ref = handle.git_text(
        ["git", "rev-parse", "--verify", candidate.candidate_ref]
    )
    if resolved_ref != candidate.candidate_sha:
        raise GitCandidateError("Candidate ref 已漂移或丢失")


def _require_clean_candidate_workspace(
    handle: ManagedChangeWorktree,
    expected_head: str,
) -> None:
    snapshot = capture_tracked_scope_snapshot(
        handle.worktree_path,
        include_untracked=True,
    )
    if (
        snapshot.head_sha != expected_head
        or snapshot.staged_files
        or snapshot.unstaged_files
        or snapshot.untracked_files
        or snapshot.unsafe_index_paths
    ):
        raise GitCandidateError("Candidate Workspace 已漂移，旧验证与 Review 必须失效")


def _validate_candidate_scope(
    handle: ManagedChangeWorktree,
    changed_files: list[str],
    contract: ChangeContract,
) -> None:
    envelope = contract.authority_envelope
    if (
        envelope.max_changed_files is not None
        and len(changed_files) > envelope.max_changed_files
    ):
        raise GitCandidateError(
            "Candidate 变更文件数超过 Approved Contract 授权预算"
        )
    case_sensitive = not scope_paths_are_case_insensitive(handle.worktree_path)
    violations = [
        path
        for path in changed_files
        if _path_violates_envelope(
            path,
            allowed_paths=envelope.allowed_paths,
            forbidden_paths=envelope.forbidden_paths,
            case_sensitive=case_sensitive,
        )
    ]
    if violations:
        raise GitCandidateError(
            "Candidate 触及 Approved Contract 之外的路径："
            + "、".join(sorted(violations))
        )


def _path_violates_envelope(
    path: str,
    *,
    allowed_paths: list[str],
    forbidden_paths: list[str],
    case_sensitive: bool,
) -> bool:
    forbidden = any(
        path_matches_pattern(path, pattern, case_sensitive=case_sensitive)
        for pattern in forbidden_paths
    )
    allowed = any(
        path_matches_pattern(path, pattern, case_sensitive=case_sensitive)
        for pattern in allowed_paths
    )
    return forbidden or not allowed


def _safe_label(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 80:
        raise GitCandidateError("Work Item 标识不能用于 Checkpoint Commit")
    return normalized


def _preserve_candidate_ref(
    handle: ManagedChangeWorktree,
    *,
    work_item_id: str,
    candidate_sha: str,
) -> str:
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", work_item_id).strip(".-")
    if not label:
        label = "work-item"
    candidate_ref = (
        f"refs/vega/candidates/{handle.run_id}/"
        f"{label}/{candidate_sha[:16]}"
    )
    handle.run_write(
        ["git", "update-ref", candidate_ref, candidate_sha],
        "保存 Candidate ref",
    )
    return candidate_ref


def _worktree_matches_candidate(
    handle: ManagedChangeWorktree,
    candidate_sha: str,
) -> bool:
    result = run_git_capture(
        handle.worktree_path,
        [
            "git",
            "diff",
            "--quiet",
            "--no-ext-diff",
            candidate_sha,
            "--",
        ],
    )
    if result.returncode not in {0, 1}:
        raise GitCandidateError("无法比较恢复后的 WIP 与 Candidate")
    return result.returncode == 0
