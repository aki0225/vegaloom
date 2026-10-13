from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ReviewCheck:
    """本次检查的事实与报告文字分离，不引入新的持久化格式。"""

    status: Literal["pass", "fail", "warn"]
    message: str

    def __str__(self) -> str:
        return self.message


def review_checks_failed(checks: Iterable[ReviewCheck]) -> bool:
    return any(check.status == "fail" for check in checks)
