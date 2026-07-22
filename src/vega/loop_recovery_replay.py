from __future__ import annotations

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path


class RecoveryReplayValidationError(ValueError):
    """恢复重放试图创建或改写既有业务 artifact。"""


class RecoveryArtifactGuard:
    """在生成器静默重建期间，只允许按相同内容复用既有 artifact。

    LangGraph checkpoint 只能恢复图游标，Python generator 仍需从头重建。
    重建阶段若直接覆盖业务 artifact，会把“验证旧证据”退化成“重新生成证据”。
    因此在与 checkpoint 对齐前，目标文件必须已经存在且内容完全一致。
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()
        self._replay_only = True

    def enable_new_writes(self) -> None:
        self._replay_only = False

    def write_text(self, path: Path, text: str) -> bool:
        """返回 True 表示已处理；False 表示可以执行正常写入。"""

        if not self._replay_only:
            return False
        candidate = path.absolute()
        if candidate != self.run_dir and self.run_dir not in candidate.parents:
            raise RecoveryReplayValidationError(
                "恢复重放不得向当前 run 目录之外写入 artifact"
            )
        self._assert_no_link_or_reparse(candidate)
        target = candidate.resolve()
        if target != self.run_dir and self.run_dir not in target.parents:
            raise RecoveryReplayValidationError(
                "恢复重放不得向当前 run 目录之外写入 artifact"
            )
        if not target.is_file():
            raise RecoveryReplayValidationError(
                f"恢复重放缺少既有 artifact，拒绝重新生成：{self._relative(target)}"
            )
        try:
            existing = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RecoveryReplayValidationError(
                f"恢复重放无法读取既有 artifact：{self._relative(target)}"
            ) from exc
        if existing != text:
            raise RecoveryReplayValidationError(
                f"恢复重放 artifact 内容不一致，拒绝覆盖：{self._relative(target)}"
            )
        return True

    def _assert_no_link_or_reparse(self, target: Path) -> None:
        try:
            relative = target.relative_to(self.run_dir)
        except ValueError as exc:
            raise RecoveryReplayValidationError(
                "恢复重放 artifact 路径不能越过当前 run 目录"
            ) from exc
        current = self.run_dir
        for part in relative.parts:
            current /= part
            if not os.path.lexists(current):
                continue
            metadata = current.lstat()
            file_attributes = getattr(metadata, "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
                raise RecoveryReplayValidationError(
                    "恢复重放 artifact 路径不能包含链接或 reparse point"
                )

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.run_dir).as_posix()
        except ValueError:
            return str(path)


_ACTIVE_RECOVERY_ARTIFACT_GUARD: ContextVar[RecoveryArtifactGuard | None] = (
    ContextVar("vega_recovery_artifact_guard", default=None)
)


@contextmanager
def guard_recovery_artifacts(
    run_dir: Path,
) -> Generator[RecoveryArtifactGuard, None, None]:
    guard = RecoveryArtifactGuard(run_dir)
    token: Token[RecoveryArtifactGuard | None] = (
        _ACTIVE_RECOVERY_ARTIFACT_GUARD.set(guard)
    )
    try:
        yield guard
    finally:
        _ACTIVE_RECOVERY_ARTIFACT_GUARD.reset(token)


def recovery_artifact_guard() -> RecoveryArtifactGuard | None:
    return _ACTIVE_RECOVERY_ARTIFACT_GUARD.get()
