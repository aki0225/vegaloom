from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir


ResultT = TypeVar("ResultT")


class _AgentRuntime(Protocol):
    workspace: Path


def agent_mutation(
    operation: str,
) -> Callable[[Callable[..., ResultT]], Callable[..., ResultT]]:
    """串行化同一 Agent run 的跨文件状态变更，防止并发启动两个 Writer。"""

    def decorate(method: Callable[..., ResultT]) -> Callable[..., ResultT]:
        @wraps(method)
        def locked(
            self: _AgentRuntime,
            run: str,
            *args: Any,
            **kwargs: Any,
        ) -> ResultT:
            run_dir = resolve_run_dir(self.workspace, run)
            with RunMutationLock.acquire(run_dir, operation):
                return method(self, run, *args, **kwargs)

        return cast(Callable[..., ResultT], locked)

    return decorate
