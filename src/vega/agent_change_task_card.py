from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_task_card import discover_handoff_task_cards


@dataclass(frozen=True)
class TaskCardSelection:
    """Task Card 发现结果；只描述候选或人工边界，不改变运行状态。"""

    task: Path | None
    relative_path: str | None
    reason_code: str | None = None
    message: str | None = None
    safe_actions: tuple[str, ...] = ()

    @property
    def selected(self) -> bool:
        return self.task is not None


def select_unique_task_card(repo: Path) -> TaskCardSelection:
    """只在当前分支恰好存在一张可恢复 Task Card 时返回候选。"""

    cards = discover_handoff_task_cards(repo)
    if not cards:
        return TaskCardSelection(
            task=None,
            relative_path=None,
            reason_code="change.no_active_run",
            message="当前仓库没有未完成 ChangeRun，也没有可恢复的 Task Card。",
            safe_actions=("change <TEXT>", "start"),
        )
    if len(cards) > 1:
        choices = "、".join(path.relative_to(repo).as_posix() for path in cards)
        return TaskCardSelection(
            task=None,
            relative_path=None,
            reason_code="handoff.multiple_task_cards",
            message=f"当前分支有多个可恢复 Task Card，拒绝猜测：{choices}",
            safe_actions=("change --task <path>",),
        )
    task = cards[0]
    return TaskCardSelection(
        task=task,
        relative_path=task.relative_to(repo).as_posix(),
    )


def confirm_task_card_selection(
    repo: Path,
    expected: TaskCardSelection,
) -> TaskCardSelection:
    """进入仓库锁后重新发现，避免确认期间候选被替换。"""

    current = select_unique_task_card(repo)
    if not current.selected:
        return current
    assert current.task is not None
    assert current.relative_path is not None
    assert expected.task is not None
    if current.task.resolve() == expected.task.resolve():
        return current
    return TaskCardSelection(
        task=None,
        relative_path=None,
        reason_code="handoff.confirmation_required",
        message=(
            f"可恢复 Task Card 已变化：{current.relative_path}；"
            "请重新确认后再创建本机 ChangeRun。"
        ),
        safe_actions=(f"change --task {current.relative_path}",),
    )
