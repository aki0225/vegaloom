from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .agent_run_status import agent_progress_items
from .progress import RunProgressLog


class ProgressCursor:
    """按安全事件及其出现次数跟踪快照，不依赖合并列表的位置。"""

    def __init__(self) -> None:
        self._seen: Counter[str] = Counter()

    def advance(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        occurrences: Counter[str] = Counter()
        added: list[dict[str, Any]] = []
        for item in items:
            # child_run 已在安全投影中标识来源；计数保留内容完全相同的追加事件。
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            occurrences[key] += 1
            if occurrences[key] > self._seen[key]:
                added.append(item)
        # child 暂时离开当前快照后仍保留历史，防止切回时重播。
        self._seen |= occurrences
        return added


def progress_items_for_run(
    workspace: Path,
    run_dir: Path,
    status_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    if status_payload["kind"] == "agent":
        return agent_progress_items(workspace, run_dir, status_payload)
    return RunProgressLog(run_dir).read()
