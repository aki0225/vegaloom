from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..goal_models import GoalCheckpointRecord
from ..progress import RunProgressLog
from ..redaction import write_redacted_json, write_redacted_text
from ..trace import TraceWriter
from .goal_reporting import render_checkpoint_report

if TYPE_CHECKING:
    from .goal_runtime import GoalRuntime


def checkpoint_done_without_lock(
    runtime: GoalRuntime,
    run: str,
    checkpoint: str,
    note: str | None = None,
    *,
    allow_manual_evidence: bool = False,
) -> Path:
    from .goal_runtime import (
        _active_checkpoint,
        _dedupe,
        _ensure_action_allowed,
        _find_checkpoint_record,
        _normalize_checkpoint,
        _revalidate_checkpoint_refs,
        _save_goal_state,
        _write_progress,
    )

    run_dir, state, contract = runtime._load(run)
    _ensure_action_allowed(state, "checkpoint_done")
    checkpoint_id = _normalize_checkpoint(checkpoint)
    record = _find_checkpoint_record(state, checkpoint_id)
    if record is None:
        raise ValueError(f"checkpoint 不存在：{checkpoint_id}")
    if record.status != "planned":
        raise ValueError(f"checkpoint {checkpoint_id} 已经完成，不能重复 checkpoint-done。")
    active = _active_checkpoint(state)
    if active is None or active.checkpoint != checkpoint_id:
        raise ValueError(f"只能完成当前 active checkpoint：{active.checkpoint if active else '无'}")
    if not record.refs:
        raise ValueError("checkpoint 没有挂载任何证据，不能标记完成。")
    refreshed_refs, refresh_errors = _revalidate_checkpoint_refs(
        runtime.workspace,
        Path(state.repo_path),
        record,
        state.scope_profile,
    )
    if refresh_errors:
        raise ValueError(
            "checkpoint 证据重新校验失败：" + "；".join(refresh_errors)
        )
    record.refs = refreshed_refs
    completion_mode = _resolve_completion_mode(
        record,
        allow_manual_evidence=allow_manual_evidence,
        note=note,
    )

    checkpoint_dir = run_dir / "checkpoints" / checkpoint_id
    report_rel = f"checkpoints/{checkpoint_id}/checkpoint-report.md"
    record.status = "done"
    record.report_path = report_rel
    record.completed_note = note.strip() if note and note.strip() else None
    record.completed_at = datetime.now(UTC).isoformat()
    record.completion_mode = completion_mode
    write_redacted_text(
        checkpoint_dir / "checkpoint-report.md",
        render_checkpoint_report(state, contract, record),
    )
    write_redacted_json(
        checkpoint_dir / "checkpoint-evidence.json",
        record.model_dump(mode="json"),
    )
    state.status = "checkpoint_done"
    state.current_step = "checkpoint_done"
    state.artifacts = _dedupe(
        [
            *state.artifacts,
            f"checkpoints/{checkpoint_id}/checkpoint-evidence.json",
            report_rel,
        ]
    )
    _write_progress(run_dir, state, contract)
    _save_goal_state(run_dir, state)
    TraceWriter(run_dir / "goal-trace.jsonl").write(
        "goal_checkpoint_done",
        checkpoint=checkpoint_id,
        ref_count=len(record.refs),
        completion_mode=completion_mode,
    )
    RunProgressLog(run_dir).append(
        "goal.checkpoint_done",
        checkpoint=checkpoint_id,
    )
    return run_dir


def _resolve_completion_mode(
    record: GoalCheckpointRecord,
    *,
    allow_manual_evidence: bool,
    note: str | None,
) -> str:
    eligible_refs = [
        item
        for item in record.refs
        if item.validated and item.completion_eligible
    ]
    bound_child_eligible = (
        record.bound_child_run is None
        or any(
            item.type == "loop"
            and item.run == record.bound_child_run
            and item.validated
            and item.completion_eligible
            for item in record.refs
        )
    )
    if eligible_refs and bound_child_eligible:
        return "validated"
    if not allow_manual_evidence:
        if record.bound_child_run and not bound_child_eligible:
            raise ValueError(
                "checkpoint 已绑定自动 child，但该 child 的 loop 证据尚不具备完成资格。"
            )
        raise ValueError(
            "checkpoint 缺少可完成证据；请挂载成功的 loop/approved review/"
            "ready_to_commit finish，或显式使用 --allow-manual-evidence。"
        )
    manual_refs = [
        item
        for item in record.refs
        if item.validated and item.type == "manual"
    ]
    if not manual_refs:
        raise ValueError("--allow-manual-evidence 需要至少一个已校验的 manual 证据文件。")
    if not note or not note.strip():
        raise ValueError("manual evidence override 必须提供 --note，说明人工完成依据。")
    return "manual_override"
