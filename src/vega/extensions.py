from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import MemoryLedgerEntry


class MemoryBackend(Protocol):
    """核心只依赖查询端口，不了解实验 Memory 的持久化实现。"""

    def search(
        self,
        query: str = "",
        accepted_only: bool = True,
        repo: str | None = None,
        repo_unscoped_only: bool = False,
        tags: list[str] | None = None,
        path: str | None = None,
    ) -> list[MemoryLedgerEntry]: ...


MemoryBackendFactory = Callable[[Path], MemoryBackend]
_memory_backend_factory: MemoryBackendFactory | None = None


def register_memory_backend(factory: MemoryBackendFactory) -> None:
    """由 CLI 组合根或显式实验调用安装 Memory 后端。"""

    global _memory_backend_factory
    _memory_backend_factory = factory


def search_memory(
    workspace: Path,
    *,
    query: str = "",
    accepted_only: bool = True,
    repo: str | None = None,
    repo_unscoped_only: bool = False,
    tags: list[str] | None = None,
    path: str | None = None,
) -> list[MemoryLedgerEntry]:
    if _memory_backend_factory is None:
        return []
    return _memory_backend_factory(workspace).search(
        query=query,
        accepted_only=accepted_only,
        repo=repo,
        repo_unscoped_only=repo_unscoped_only,
        tags=tags,
        path=path,
    )
