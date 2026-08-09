from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .redaction import redact_value


def _save_goal_model(path: Path, model: BaseModel) -> None:
    payload = redact_value(model.model_dump(mode="json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".m.{uuid4().hex[:16]}")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    last_error: OSError | None = None
    for _ in range(10):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.02)
    if temp_path.exists():
        temp_path.unlink()
    assert last_error is not None
    raise last_error


class GoalContract(BaseModel):
    objective: str
    repo_path: str
    input_source: str
    raw_text: str
    scope_profile: str | None = None
    non_goals: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


GoalCheckpointEvidenceType = Literal["loop", "reflect", "gate", "review", "finish", "manual"]


class GoalCheckpointRef(BaseModel):
    run: str
    type: GoalCheckpointEvidenceType
    note: str | None = None
    kind: str | None = None
    status: str | None = None
    repo_path: str | None = None
    validated: bool = False
    completion_eligible: bool = False
    validation_summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    attached_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class GoalCheckpointRecord(BaseModel):
    checkpoint: str
    status: Literal["planned", "done"] = "planned"
    plan_path: str
    task_text: str | None = None
    task_source: str | None = None
    bound_child_run: str | None = None
    runner_timeout_seconds: int | None = Field(default=None, ge=60, le=3600)
    report_path: str | None = None
    refs: list[GoalCheckpointRef] = Field(default_factory=list)
    completed_note: str | None = None
    completed_at: str | None = None
    completion_mode: Literal["validated", "manual_override"] | None = None


class GoalState(BaseModel):
    run_id: str
    status: Literal[
        "created",
        "running",
        "checkpoint_done",
        "paused",
        "stopped",
        "blocked",
        "timeout",
        "stale",
        "needs_human",
        "success",
        "failed",
    ] = "created"
    repo_path: str
    input_source: str
    scope_profile: str | None = None
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    checkpoint_count: int = 0
    checkpoints: list[str] = Field(default_factory=list)
    checkpoint_records: list[GoalCheckpointRecord] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    pause_reason: str | None = None
    paused_from_status: Literal["created", "running", "checkpoint_done"] | None = None
    stop_reason: str | None = None
    recover_reason: str | None = None
    active_child_run: str | None = None
    last_child_run: str | None = None
    last_child_status: str | None = None
    last_reconciled_at: str | None = None
    completion_note: str | None = None
    completed_at: str | None = None
    eval_results: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_goal_model(path, self)
