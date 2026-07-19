from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .redaction import redact_value


RUN_TERMINAL_SUPERSEDED_EVENT = "run_terminal_superseded"


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


def read_trace_items(trace_path: Path) -> list[dict[str, Any]]:
    """读取严格 JSONL trace，供终态审计和 recovery 共享同一解析语义。"""

    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"trace.jsonl 第 {line_number} 行不是合法 JSON：{exc.msg}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"trace.jsonl 第 {line_number} 行不是 JSON object")
        items.append(item)
    return items


def active_run_finished_indices(
    items: list[dict[str, Any]],
    *,
    expected_superseded: list[dict[str, Any]] | None = None,
) -> tuple[list[int], list[str]]:
    """返回未被 recovery 明确作废的 run_finished 事件索引。

    Trace 保持 append-only。若 owner 在写入 run_finished 后、保存终态 state 前崩溃，
    recovery 只能追加 superseded 证据，不能删除或重写旧终态事件。superseded 事件必须
    同时绑定后续 loop_recovered 事件和根状态记录，不能只靠一条可追加 trace 自证。
    """

    superseded: set[int] = set()
    observed: dict[int, tuple[str, str]] = {}
    issues: list[str] = []
    for index, item in enumerate(items):
        if item.get("event") != RUN_TERMINAL_SUPERSEDED_EVENT:
            continue
        target = item.get("terminal_event_index")
        if type(target) is not int or target < 0 or target >= index:
            issues.append("run_terminal_superseded_target_invalid")
            continue
        target_event = items[target]
        if target_event.get("event") != "run_finished":
            issues.append("run_terminal_superseded_target_not_terminal")
            continue
        if target in superseded:
            issues.append("run_terminal_superseded_duplicate")
            continue
        if item.get("terminal_status") != target_event.get("status"):
            issues.append("run_terminal_superseded_status_mismatch")
            continue
        terminal_status = item.get("terminal_status")
        if terminal_status not in {"success", "failed"}:
            issues.append("run_terminal_superseded_status_invalid")
            continue
        recovery_id = item.get("recovery_id")
        if not isinstance(recovery_id, str) or not recovery_id.strip():
            issues.append("run_terminal_superseded_recovery_id_invalid")
            continue
        if not isinstance(item.get("ts"), str) or not item["ts"].strip():
            issues.append("run_terminal_superseded_timestamp_invalid")
            continue

        recovery_events = [
            candidate
            for candidate_index, candidate in enumerate(items)
            if candidate_index > index
            and candidate.get("event") == "loop_recovered"
            and candidate.get("recovery_id") == recovery_id
        ]
        if not recovery_events:
            issues.append("run_terminal_superseded_recovery_missing")
            continue
        if len(recovery_events) != 1:
            issues.append("run_terminal_superseded_recovery_duplicate")
            continue
        recovery_event = recovery_events[0]
        if recovery_event.get("superseded_terminal_event") != target:
            issues.append("run_terminal_superseded_recovery_mismatch")
            continue
        if not isinstance(recovery_event.get("ts"), str) or not recovery_event["ts"].strip():
            issues.append("run_terminal_superseded_recovery_timestamp_invalid")
            continue
        superseded.add(target)
        observed[target] = (terminal_status, recovery_id)

    if expected_superseded is not None:
        expected: dict[int, tuple[str, str]] = {}
        for record in expected_superseded:
            target = record.get("terminal_event_index")
            status = record.get("terminal_status")
            recovery_id = record.get("recovery_id")
            if (
                type(target) is not int
                or target < 0
                or status not in {"success", "failed"}
                or not isinstance(recovery_id, str)
                or not recovery_id.strip()
            ):
                issues.append("run_terminal_superseded_state_record_invalid")
                continue
            if target in expected:
                issues.append("run_terminal_superseded_state_record_duplicate")
                continue
            expected[target] = (status, recovery_id)

        for target, binding in observed.items():
            if target not in expected:
                issues.append("run_terminal_superseded_state_binding_missing")
            elif expected[target] != binding:
                issues.append("run_terminal_superseded_state_binding_mismatch")
        for target in expected:
            if target not in observed:
                issues.append("run_terminal_superseded_trace_binding_missing")

    terminal_indices = [
        index for index, item in enumerate(items) if item.get("event") == "run_finished"
    ]
    active = [index for index in terminal_indices if index not in superseded]
    return active, list(dict.fromkeys(issues))
