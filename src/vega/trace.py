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
