from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

PROGRESS_INTERVAL_SECONDS = 25.0
ExecutionProgressReporter = Callable[[str, int], None]


@dataclass
class ExecutionProgressTicker:
    """同步发出轻量反馈；reporter 必须保持非阻塞。"""

    step: str
    reporter: ExecutionProgressReporter | None
    started_at: float = field(default_factory=time.monotonic)
    next_report_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.next_report_at = self.started_at + PROGRESS_INTERVAL_SECONDS

    def started(self) -> None:
        self._emit(0)

    def tick(self, now: float) -> None:
        if self.reporter is None or now < self.next_report_at:
            return
        self.next_report_at = now + PROGRESS_INTERVAL_SECONDS
        self._emit(int(now - self.started_at))

    def _emit(self, elapsed_seconds: int) -> None:
        reporter = self.reporter
        if reporter is None:
            return
        try:
            reporter(self.step, elapsed_seconds)
        except Exception:  # noqa: BLE001 - 临时进度输出失败不能改变执行结果
            self.reporter = None


def render_owned_child_pid_line(is_active: bool, child_pid: int | None) -> str:
    label = (
        "owned child PID"
        if is_active
        else "历史 owned child PID（仅供审计，不表示当前存活）"
    )
    missing_value = "尚未启动" if is_active else "未记录"
    return f"- {label}：`{child_pid if child_pid is not None else missing_value}`"
