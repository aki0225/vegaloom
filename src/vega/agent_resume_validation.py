from __future__ import annotations

import subprocess
from pathlib import Path

from .agent_handoff_digest import compute_handoff_workspace_digest
from .agent_handoff_safety import (
    validate_handoff_history,
)
from .agent_task_card import (
    AgentTaskCard,
    ResumeCapsule,
    task_card_content_digest,
)
from .agent_task_card_discovery import task_card_chain_paths
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
    task_card_content: str,
) -> ReviewWorkspaceSnapshot:
    expected_branch = current_branch(repo)
    capsule = _require_resume_capsule(card, expected_branch)
    handoff_head_sha = validate_handoff_history(repo, card, relative_task)
    _validate_change_run_history(repo, card, handoff_head_sha)
    _validate_task_card_binding(
        repo,
        relative_task,
        handoff_head_sha=handoff_head_sha,
        task_card_content=task_card_content,
    )
    snapshot = _capture_resume_snapshot(
        repo,
        card,
        capsule,
        handoff_head_sha=handoff_head_sha,
    )
    _validate_resume_snapshot(snapshot)
    _validate_committed_paths(
        repo,
        card,
        relative_task,
        handoff_head_sha=handoff_head_sha,
    )
    _validate_resume_content(repo, card, capsule, snapshot)
    require_resume_repository_identity(
        repo,
        expected_head_sha=handoff_head_sha,
        expected_branch=expected_branch,
    )
    return snapshot


def _require_resume_capsule(
    card: AgentTaskCard,
    expected_branch: str,
) -> ResumeCapsule:
    if card.handoff_status == "none":
        raise ValueError("Task Card 没有可恢复交接")
    if card.branch != expected_branch:
        raise ValueError("Task Card 分支与当前分支不一致")
    if card.resume_capsule is None:
        raise ValueError("Task Card 缺少 Resume Capsule")
    return card.resume_capsule


def _capture_resume_snapshot(
    repo: Path,
    card: AgentTaskCard,
    capsule: ResumeCapsule,
    *,
    handoff_head_sha: str,
) -> ReviewWorkspaceSnapshot:
    comparison_base = (
        (capsule.comparison_base_revision or card.handoff_base_revision)
        if capsule.changed_files
        else handoff_head_sha
    )
    try:
        snapshot = capture_review_workspace(
            repo,
            comparison_base_sha=comparison_base,
            comparison_paths=tuple(capsule.changed_files),
        )
    except RuntimeError as exc:
        if "Git HEAD" in str(exc):
            raise ValueError("恢复校验期间 Git HEAD 已漂移") from exc
        raise
    if snapshot.head_sha != handoff_head_sha:
        raise ValueError("恢复校验期间 Git HEAD 已漂移")
    return snapshot


def _validate_resume_snapshot(snapshot: ReviewWorkspaceSnapshot) -> None:
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


def _validate_resume_content(
    repo: Path,
    card: AgentTaskCard,
    capsule: ResumeCapsule,
    snapshot: ReviewWorkspaceSnapshot,
) -> None:
    expected_changed = set(capsule.changed_files)
    observed_changed = set(snapshot.changed_files)
    if not observed_changed.issubset(expected_changed):
        unexpected = ", ".join(sorted(observed_changed - expected_changed))
        raise ValueError(f"恢复前存在交接未登记的 Workspace 变化：{unexpected}")
    current_digest = compute_handoff_workspace_digest(
        repo,
        capsule.changed_files,
        digest_kind=capsule.workspace_digest_kind,
        revision=(
            snapshot.head_sha
            if capsule.workspace_digest_kind == "git-blob-v1"
            else None
        ),
    )
    if card.handoff_workspace_digest != current_digest:
        raise ValueError(
            "当前 WIP 内容与交接摘要不一致；"
            "旧验证已降为历史，但现场仍必须先人工对账"
        )


def require_resume_repository_identity(
    repo: Path,
    *,
    expected_head_sha: str,
    expected_branch: str,
) -> None:
    if current_branch(repo) != expected_branch:
        raise ValueError("恢复校验期间 Git 分支已漂移")
    process = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0 or process.stdout.strip() != expected_head_sha:
        raise ValueError("恢复校验期间 Git HEAD 已漂移")


def _validate_task_card_binding(
    repo: Path,
    relative_task: str,
    *,
    handoff_head_sha: str,
    task_card_content: str,
) -> None:
    tree = subprocess.run(
        [
            "git",
            "ls-tree",
            "-z",
            "--full-tree",
            handoff_head_sha,
            "--",
            relative_task,
        ],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    records = [record for record in tree.stdout.split(b"\0") if record]
    if tree.returncode != 0 or len(records) != 1 or b"\t" not in records[0]:
        raise ValueError("无法读取 Handoff 提交中的 Task Card")
    metadata, raw_path = records[0].split(b"\t", 1)
    fields = metadata.split()
    if (
        len(fields) != 3
        or fields[1] != b"blob"
        or raw_path.decode("utf-8", errors="strict") != relative_task
    ):
        raise ValueError("Handoff 提交中的 Task Card 不是预期普通文件")
    blob = subprocess.run(
        ["git", "cat-file", "blob", fields[2].decode("ascii")],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if blob.returncode != 0:
        raise ValueError("无法读取 Handoff 提交中的 Task Card 内容")
    try:
        committed_content = blob.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Handoff 提交中的 Task Card 不是 UTF-8 文本") from exc
    if task_card_content_digest(task_card_content) != task_card_content_digest(
        committed_content
    ):
        raise ValueError("Task Card 内容与当前 Handoff 提交不一致")


def _validate_committed_paths(
    repo: Path,
    card: AgentTaskCard,
    relative_task: str,
    *,
    handoff_head_sha: str,
) -> None:
    assert card.resume_capsule is not None
    comparison_base = (
        card.resume_capsule.comparison_base_revision
        or card.handoff_base_revision
    )
    assert comparison_base is not None
    committed_paths = set(
        collect_comparison_changed_paths(
            repo,
            comparison_base,
            comparison_head_sha=handoff_head_sha,
        )
    )
    chain_paths = task_card_chain_paths(repo, card, relative_task)
    expected_paths = {
        *card.resume_capsule.changed_files,
        relative_task,
    }
    if card.change_run is None:
        expected_paths.update(chain_paths & committed_paths)
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


def _validate_change_run_history(
    repo: Path,
    card: AgentTaskCard,
    handoff_head_sha: str,
) -> None:
    if card.change_run is None:
        return
    revisions = [
        ("Accepted Checkpoint", card.change_run.accepted_checkpoint_sha),
    ]
    if card.change_run.historical_candidate_sha is not None:
        revisions.append(
            ("历史 Candidate", card.change_run.historical_candidate_sha)
        )
    for label, revision in revisions:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, handoff_head_sha],
            cwd=repo,
            check=False,
            capture_output=True,
        )
        if ancestor.returncode != 0:
            raise ValueError(f"{label} 不属于当前仓库的 Handoff 历史")
