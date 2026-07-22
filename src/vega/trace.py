from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .redaction import redact_value


class TraceWriter:
    def __init__(self, trace_path: Path) -> None:
        self.trace_path = trace_path

    def write(self, event: str, **payload: Any) -> None:
        item = redact_value({
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **payload,
        })
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


class RecoveryTraceWriter(TraceWriter):
    """生成器恢复对齐期间静默，完成对齐后才继续记录业务事件。"""

    def __init__(self, trace_path: Path) -> None:
        super().__init__(trace_path)
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def write(self, event: str, **payload: Any) -> None:
        if self._enabled:
            super().write(event, **payload)
