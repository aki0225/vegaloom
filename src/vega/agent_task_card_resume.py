from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .agent_contract import AgentState
from .agent_handoff_safety import TaskCardError
from .agent_repository_guard import acquire_task_card_resume_claim
from .agent_task_card import (
    AgentTaskCard,
    discover_handoff_task_cards,
    parse_task_card,
)
from .repository_identity import repository_scope
from .run_utils import create_run_dir
from .workspace_snapshot import ReviewWorkspaceSnapshot


def create_claimed_resume_run(
    workspace: Path,
    repo: Path,
    *,
    task_card_sha256: str,
    task_card: str,
) -> tuple[str, Path]:
    """创建并独占恢复 run；Claim 失败时只撤销尚未写入证据的空目录。"""

    run_id, run_dir = create_run_dir(
        workspace,
        (
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid4().hex[:12]}-agent-resume"
        ),
    )
    try:
        acquire_task_card_resume_claim(
            repo,
            task_card_sha256=task_card_sha256,
            run_dir=run_dir,
            task_card=task_card,
        )
    except BaseException:
        try:
            run_dir.rmdir()
        except OSError:
            pass
        raise
    return run_id, run_dir


def resolve_resume_task(
    repo: Path,
    task_path: Path | None,
) -> tuple[Path, str]:
    if task_path is None:
        matches = discover_handoff_task_cards(repo)
        if not matches:
            raise ValueError("当前分支没有可恢复的 Task Card")
        if len(matches) > 1:
            choices = ", ".join(
                path.relative_to(repo).as_posix()
                for path in matches
            )
            raise ValueError(
                f"当前分支有多个可恢复 Task Card，请使用 --task 选择：{choices}"
            )
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


def load_task_card_with_content(
    path: Path,
) -> tuple[AgentTaskCard, str]:
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        raise TaskCardError(f"无法读取 Task Card：{path.name}") from exc
    return parse_task_card(content), content


def state_from_task_card(
    run_id: str,
    repo: Path,
    card: AgentTaskCard,
    snapshot: ReviewWorkspaceSnapshot,
    *,
    accepted_checkpoint_sha: str | None = None,
) -> AgentState:
    requires_human = (
        card.handoff_status == "handoff_blocked"
        or card.status == "needs_human"
    )
    allowed_actions = (
        ["human"]
        if requires_human
        else list(card.resume_capsule.allowed_actions)
        if card.resume_capsule
        else ["human"]
    )
    return AgentState(
        run_id=run_id,
        task_id=card.task_id,
        repository_id=repository_scope(repo),
        run_kind="change" if card.change_run is not None else "legacy",
        phase="needs_human" if requires_human else "ready",
        goal_revision=card.plan.goal_revision,
        plan_revision=card.plan.plan_revision,
        approved_plan_digest=card.plan.approved_digest,
        contract_revision=(
            card.change_run.contract.contract_revision
            if card.change_run is not None
            else None
        ),
        approved_contract_digest=(
            card.change_run.contract.approved_digest
            if card.change_run is not None
            else None
        ),
        execution_plan_revision=(
            card.change_run.execution_plan.plan_revision
            if card.change_run is not None
            else None
        ),
        accepted_checkpoint_sha=accepted_checkpoint_sha,
        current_work_item=card.current_work_item,
        workspace_fingerprint=snapshot.fingerprint,
        allowed_actions=allowed_actions,
        # 新 run 不继承旧 Handoff 发布态，避免后续失败被误判为已经完成交接。
        handoff_status="none",
    )
