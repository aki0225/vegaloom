from __future__ import annotations

import json
from pathlib import Path, PurePath

from pydantic import ValidationError

from vega.redaction import append_redacted_jsonl, redact_value

from .models import MemoryEvent


class EventStore:
    """仓库内 append-only MemoryEvent 存储。

    实验层显式接收 workspace 根目录和相对路径，避免调用方把事件写到其他项目或用户目录。
    """

    def __init__(self, workspace_root: Path, relative_path: str | Path) -> None:
        self.workspace_root = workspace_root.resolve()
        candidate = PurePath(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("event store 路径必须位于当前 workspace 内")
        self.path = (self.workspace_root / candidate).resolve()
        if not self.path.is_relative_to(self.workspace_root):
            raise ValueError("event store 路径越过当前 workspace")

    def read(self) -> list[MemoryEvent]:
        if not self.path.exists():
            return []
        events: list[MemoryEvent] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                raise ValueError(f"events.jsonl 第 {line_number} 行为空")
            try:
                payload = json.loads(line)
                events.append(MemoryEvent.model_validate(payload))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ValueError(f"events.jsonl 第 {line_number} 行无效") from exc
        return events

    def append(self, event: MemoryEvent) -> MemoryEvent:
        existing = self.read()
        expected_seq = len(existing) + 1
        if event.seq != expected_seq:
            raise ValueError(
                f"event.seq 必须连续：期望 {expected_seq}，实际 {event.seq}"
            )
        if existing:
            first = existing[0]
            if (
                event.task_id != first.task_id
                or event.run_id != first.run_id
                or event.repo_identity != first.repo_identity
            ):
                raise ValueError("event 与现有日志的 repo/run/task 绑定不一致")
            if any(item.event_id == event.event_id for item in existing):
                raise ValueError("event_id 重复")

        # 先脱敏再重新校验，确保落盘内容仍满足 schema，而不是只校验内存中的原始对象。
        persisted = MemoryEvent.model_validate(
            redact_value(event.model_dump(mode="json"))
        )
        append_redacted_jsonl(self.path, persisted.model_dump(mode="json"))
        return persisted
