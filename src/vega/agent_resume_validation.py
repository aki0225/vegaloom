from __future__ import annotations

import subprocess
from pathlib import Path

from .agent_handoff_safety import validate_handoff_history
from .agent_task_card import AgentTaskCard, compute_handoff_workspace_digest
from .tracked_workspace import collect_comparison_changed_paths
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


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


def validate_resume_workspace(
    repo: Path,
    card: AgentTaskCard,
    *,
    relative_task: str,
) -> ReviewWorkspaceSnapshot:
    if card.handoff_status == "none":
        raise ValueError("Task Card 没有可恢复交接")
    if card.branch != current_branch(repo):
        raise ValueError("Task Card 分支与当前分支不一致")
    if card.resume_capsule is None:
        raise ValueError("Task Card 缺少 Resume Capsule")
    handoff_head_sha = validate_handoff_history(repo, card, relative_task)
    snapshot = capture_review_workspace(
        repo,
        comparison_base_sha=card.handoff_base_revision,
        comparison_paths=tuple(card.resume_capsule.changed_files),
    )
    if snapshot.head_sha != handoff_head_sha:
        raise ValueError("恢复校验期间 Git HEAD 已漂移")
    if (
        snapshot.staged_diff.strip()
        or snapshot.unstaged_diff.strip()
        or snapshot.untracked_files
    ):
        raise ValueError("恢复前 Workspace 必须没有额外 Diff")
    if snapshot.unsafe_index_paths:
        raise ValueError("恢复前 Git index 包含不安全标记")
    if not snapshot.git_control_complete:
        raise ValueError("恢复前 Git control manifest 不完整")

    _validate_committed_paths(
        repo,
        card,
        relative_task,
        handoff_head_sha=handoff_head_sha,
    )

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


def _validate_committed_paths(
    repo: Path,
    card: AgentTaskCard,
    relative_task: str,
    *,
    handoff_head_sha: str,
) -> None:
    assert card.resume_capsule is not None
    committed_paths = set(
        collect_comparison_changed_paths(
            repo,
            card.handoff_base_revision,
            comparison_head_sha=handoff_head_sha,
        )
    )
    expected_paths = {*card.resume_capsule.changed_files, relative_task}
    if committed_paths == expected_paths:
        return
    details = []
    missing = sorted(expected_paths - committed_paths)
    unexpected = sorted(committed_paths - expected_paths)
    if missing:
        details.append("缺少：" + "、".join(missing))
    if unexpected:
        details.append("未登记：" + "、".join(unexpected))
    raise ValueError(
        "Handoff 提交必须只包含 Resume Capsule 文件与当前 Task Card；"
        + "；".join(details)
    )
