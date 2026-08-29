from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from .agent_handoff_safety import TaskCardError
from .agent_task_card import AgentTaskCard, load_task_card


_TERMINAL_TASK_STATUSES = frozenset({"completed", "stopped"})


def discover_handoff_task_cards(
    repo: Path,
    *,
    branch: str | None = None,
) -> list[Path]:
    repo_root = repo.resolve()
    current_branch = branch or _current_branch(repo_root)
    process = subprocess.run(
        ["git", "ls-files", "-z", "--", ":(glob).vega/tasks/**/*.md"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if process.returncode != 0:
        raise TaskCardError("无法读取 Git 跟踪的 Task Card")
    tracked_cards: dict[str, tuple[Path, AgentTaskCard]] = {}
    for raw_path in process.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="strict")
        path = repo_root / PurePosixPath(relative)
        tracked_cards[relative] = (path, load_task_card(path))
    return _active_handoff_paths(tracked_cards, current_branch)


def discover_local_handoff_task_cards(
    repo: Path,
    *,
    branch: str,
) -> list[Path]:
    """发现本地 Task Card 链的最新可恢复节点，包括尚未提交的新卡。"""

    repo_root = repo.resolve()
    task_root = repo_root / ".vega" / "tasks"
    if not task_root.exists():
        return []
    cards: dict[str, tuple[Path, AgentTaskCard]] = {}
    for path in sorted(task_root.rglob("*.md")):
        if path.is_symlink():
            raise TaskCardError("Task Card 目录中不能包含链接文件")
        try:
            card = load_task_card(path)
        except (OSError, ValueError, TaskCardError) as exc:
            relative = path.relative_to(repo_root).as_posix()
            raise TaskCardError(
                f"本地 Task Card 无法验证：{relative}"
            ) from exc
        cards[path.relative_to(repo_root).as_posix()] = (path, card)
    return _active_handoff_paths(cards, branch)


def task_card_chain_paths(
    repo: Path,
    card: AgentTaskCard,
    relative_task: str,
) -> set[str]:
    """返回当前 Task Card 及显式前驱；恢复后的任务分支可以变化。"""

    paths = {relative_task}
    seen = {relative_task}
    current = card
    while current.previous_task_card is not None:
        previous = current.previous_task_card
        if previous in seen:
            raise ValueError("Task Card 交接链存在循环引用")
        seen.add(previous)
        try:
            previous_card = load_task_card(repo / previous)
        except (OSError, ValueError) as exc:
            raise ValueError("Task Card 交接链中的旧卡无法验证") from exc
        if (
            previous_card.task_id != card.task_id
            or previous_card.handoff_sequence >= current.handoff_sequence
        ):
            raise ValueError("Task Card 交接链身份或 sequence 不一致")
        paths.add(previous)
        current = previous_card
    return paths


def next_handoff_sequence(
    repo: Path,
    task_id: str,
    branch: str,
    *,
    previous_task_card: str | None,
) -> int:
    if previous_task_card is not None:
        previous = load_task_card(repo / previous_task_card)
        if previous.task_id != task_id:
            raise TaskCardError("上一张 Task Card 与当前任务不一致")
        return previous.handoff_sequence + 1
    root = repo / ".vega" / "tasks"
    if not root.exists():
        return 1
    highest = 0
    for path in root.rglob("*.md"):
        try:
            card = load_task_card(path)
        except (OSError, ValueError, TaskCardError):
            continue
        if card.task_id == task_id and card.branch == branch:
            highest = max(highest, card.handoff_sequence)
    return highest + 1


def _active_handoff_paths(
    cards: dict[str, tuple[Path, AgentTaskCard]],
    branch: str,
) -> list[Path]:
    branch_cards = {
        relative: (path, card)
        for relative, (path, card) in cards.items()
        if card.branch == branch
    }
    superseded: set[str] = set()
    for _, successor in branch_cards.values():
        previous = successor.previous_task_card
        if previous is None:
            continue
        predecessor_entry = cards.get(previous)
        if predecessor_entry is None:
            raise TaskCardError("Task Card 交接链引用的上一张卡不存在")
        _, predecessor = predecessor_entry
        if (
            predecessor.task_id != successor.task_id
            or predecessor.handoff_sequence >= successor.handoff_sequence
        ):
            raise TaskCardError("Task Card 交接链的任务身份或 sequence 不一致")
        superseded.add(previous)
    return sorted(
        path
        for relative, (path, card) in branch_cards.items()
        if (
            relative not in superseded
            and card.status not in _TERMINAL_TASK_STATUSES
            and card.handoff_status != "none"
        )
    )


def _current_branch(repo: Path) -> str:
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
        raise TaskCardError("当前 HEAD 不是可恢复任务分支")
    return branch
