from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .execution_control import (
    ACTIVE_EXECUTION_STATUSES,
    ExecutionRecord,
    find_execution_records,
)


def latest_execution_payload(
    run_dir: Path,
    run_status: object,
) -> dict[str, Any] | None:
    records = find_execution_records(run_dir)
    if not records:
        return None
    unconfirmed = [
        record for record in records if record.lease.termination_unconfirmed
    ]
    preferred = [
        record
        for record in records
        if (record.lease.status in ACTIVE_EXECUTION_STATUSES)
        == (run_status == "running")
    ]
    record = max(
        unconfirmed or preferred or records,
        key=_execution_heartbeat_utc,
    )
    lease = record.lease
    return {
        "status": lease.status,
        "step": lease.step,
        "iteration": lease.iteration,
        "owner_pid": lease.owner_pid,
        "child_pid": lease.child_pid,
        "termination_unconfirmed": lease.termination_unconfirmed,
        "last_heartbeat": lease.last_heartbeat,
        "deadline": lease.deadline,
        "path": str(record.path.resolve()),
    }


def _execution_heartbeat_utc(record: ExecutionRecord) -> datetime:
    try:
        parsed = datetime.fromisoformat(record.lease.last_heartbeat)
    except ValueError as exc:
        raise ValueError(
            f"execution 记录 `{record.path}` 的 last_heartbeat 不是有效 ISO 时间。"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
