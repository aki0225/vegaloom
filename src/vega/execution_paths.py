from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ExecutionPathGuard:
    """把 execution artifact 约束在调用方提供的可信根目录内。"""

    trusted_root: Path
    execution_dir: Path

    def prepare(self) -> Path:
        return self._validate_directory(create=True)

    def validate_artifact(self, path: Path) -> None:
        execution_dir = self._validate_directory(create=False)
        artifact_path = _absolute_path(path)
        if artifact_path.parent != execution_dir:
            raise OSError("execution artifact 必须直接位于可信 execution 目录内")
        if not os.path.lexists(artifact_path):
            return
        metadata = artifact_path.lstat()
        if _is_link_or_reparse_stat(metadata):
            raise OSError("execution artifact 不能是符号链接、junction 或 reparse point")
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("execution artifact 必须是普通文件")

    def persist_output(
        self,
        output_file: object,
        output_path: Path,
        redactor: Callable[[str], str],
    ) -> str:
        self.validate_artifact(output_path)
        output_file.flush()
        output_file.seek(0)
        raw_output = output_file.read().decode("utf-8", errors="replace")
        output = redactor(raw_output).replace("\r\n", "\n").replace("\r", "\n")
        output_path.write_text(output, encoding="utf-8", newline="\n")
        return output

    def _validate_directory(self, *, create: bool) -> Path:
        root = _absolute_path(self.trusted_root)
        destination = _absolute_path(self.execution_dir)
        try:
            relative = destination.relative_to(root)
        except ValueError as exc:
            raise OSError("execution 目录逃出可信根路径") from exc
        if not relative.parts:
            raise OSError("execution 目录不能与可信根路径相同")

        _require_plain_directory(root, "可信 execution 根路径")
        current = root
        for part in relative.parts:
            current = current / part
            if not os.path.lexists(current):
                if not create:
                    raise OSError("execution 目录不存在")
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise OSError("无法创建 execution 目录") from exc
            _require_plain_directory(current, "execution 路径")

        if not current.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise OSError("execution 目录经链接或 reparse point 逃出可信根路径")
        return current


def _require_plain_directory(path: Path, label: str) -> None:
    if not os.path.lexists(path):
        raise OSError(f"{label}不存在")
    metadata = path.lstat()
    if _is_link_or_reparse_stat(metadata):
        raise OSError(f"{label}不能包含符号链接、junction 或 reparse point")
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"{label}必须是目录")


def _is_link_or_reparse_stat(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))
