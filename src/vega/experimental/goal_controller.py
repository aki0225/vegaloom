from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..loop_runtime import LoopAutomationRuntime
from ..models import BriefInput
from ..progress import RunProgressLog
from ..redaction import write_redacted_text
from ..trace import TraceWriter
from ..goal_models import GoalContract, GoalState

if TYPE_CHECKING:
    from .goal_runtime import GoalRuntime


def run_one_checkpoint(
    runtime: GoalRuntime,
    run: str,
    *,
    worker_name: str,
    reviewer_name: str,
    max_iterations: int,
    verify: bool,
    max_checkpoints: int,
) -> Path:
    """执行一个明确 checkpoint，并在证据边界停止。"""

    from .goal_runtime import (
        _active_checkpoint,
        _ensure_action_allowed,
        _save_goal_state,
        _write_progress,
    )

    if max_checkpoints != 1:
        raise ValueError(
            "当前 Goal P1 实验只允许 --max-checkpoints 1；"
            "多 checkpoint 自动串联尚未证明安全。"
        )
    run_dir, state, contract = runtime._load(run)
    _ensure_action_allowed(state, "run_one")
    active = _active_checkpoint(state)
    if active is None:
        raise ValueError(
            "必须先运行 `vega goal step --text \"...\"` 创建明确的 active checkpoint。"
        )
    if not active.task_text or not active.task_text.strip():
        raise ValueError(
            f"checkpoint {active.checkpoint} 缺少明确任务；"
            "请重新创建 checkpoint 并提供 --text 或 --input。"
        )

    progress = RunProgressLog(run_dir)
    state.status = "running"
    state.current_step = "checkpoint_dispatching"
    state.active_child_run = None
    state.last_child_status = "starting"
    _write_progress(run_dir, state, contract)
    _save_goal_state(run_dir, state)
    TraceWriter(run_dir / "goal-trace.jsonl").write(
        "goal_checkpoint_dispatching",
        checkpoint=active.checkpoint,
        max_checkpoints=max_checkpoints,
    )
    progress.append("goal.checkpoint_dispatching", checkpoint=active.checkpoint)

    def on_child_created(child_dir: Path) -> None:
        state.active_child_run = child_dir.name
        state.last_child_run = child_dir.name
        state.last_child_status = "running"
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            "goal_child_run_created",
            checkpoint=active.checkpoint,
            child_run=child_dir.name,
        )
        progress.append(
            "goal.child_run_created",
            checkpoint=active.checkpoint,
            child_run=child_dir.name,
        )

    try:
        child_dir = LoopAutomationRuntime(
            runtime.workspace,
            progress_reporter=runtime.progress_reporter,
        ).start(
            BriefInput(
                mode="feature",
                text=active.task_text,
                source=active.task_source or active.plan_path,
                repo_path=state.repo_path,
            ),
            automation_mode="auto",
            worker_name=worker_name,
            reviewer_name=reviewer_name,
            max_iterations=max_iterations,
            verify=verify,
            on_run_created=on_child_created,
        )
    except Exception as exc:  # noqa: BLE001 - 先保留 Goal 现场，再由 CLI 报错
        _mark_checkpoint_blocked(
            run_dir,
            state,
            contract,
            active.checkpoint,
            f"child loop 启动或执行异常：{type(exc).__name__}",
            progress,
        )
        raise

    try:
        child_state = _read_child_state(child_dir)
        state.active_child_run = None
        state.last_child_run = child_dir.name
        state.last_child_status = str(child_state.get("status") or "unknown")
        _write_progress(run_dir, state, contract)
        _save_goal_state(run_dir, state)
        TraceWriter(run_dir / "goal-trace.jsonl").write(
            "goal_child_run_finished",
            checkpoint=active.checkpoint,
            child_run=child_dir.name,
            status=state.last_child_status,
        )
        progress.append(
            "goal.child_run_finished",
            checkpoint=active.checkpoint,
            child_run=child_dir.name,
        )
        runtime._attach_without_lock(
            run,
            checkpoint=active.checkpoint,
            child_run=child_dir.name,
            evidence_type="loop",
            note="Goal P1 自动 checkpoint 产生的 child loop 证据。",
        )
        runtime._checkpoint_done_without_lock(
            run,
            active.checkpoint,
            note="单 checkpoint 自动执行完成，已通过证据资格校验。",
        )
    except (FileNotFoundError, ValueError) as exc:
        try:
            run_dir, state, contract = runtime._load(run)
        except (FileNotFoundError, ValueError):
            pass
        _mark_checkpoint_blocked(
            run_dir,
            state,
            contract,
            active.checkpoint,
            f"child loop 状态或证据未达到 checkpoint 完成资格：{type(exc).__name__}",
            RunProgressLog(run_dir),
        )
    return run_dir


def _read_child_state(child_dir: Path) -> dict[str, object]:
    try:
        payload = json.loads((child_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("child loop state 无法读取或解析。") from exc
    if not isinstance(payload, dict):
        raise ValueError("child loop state 顶层必须是 JSON object。")
    return payload


def _mark_checkpoint_blocked(
    run_dir: Path,
    state: GoalState,
    contract: GoalContract,
    checkpoint: str,
    reason: str,
    progress: RunProgressLog,
) -> None:
    from .goal_runtime import _dedupe, _save_goal_state, _write_progress

    state.status = "needs_human"
    state.current_step = "checkpoint_blocked"
    state.active_child_run = None
    if state.last_child_status in {None, "starting", "running"}:
        state.last_child_status = "error"
    report_rel = f"checkpoints/{checkpoint}/checkpoint-blocked.md"
    write_redacted_text(
        run_dir / report_rel,
        "\n".join(
            [
                f"# Checkpoint {checkpoint} Blocked",
                "",
                f"- goal：`{state.run_id}`",
                f"- 状态：`{state.status}`",
                f"- 原因：{reason}",
                "",
                "## Objective",
                "",
                contract.objective,
                "",
                "## 下一步",
                "",
                "- 人工读取 child run、workspace 和已有证据。",
                "- 不自动重试，不自动回滚，不自动进入下一 checkpoint。",
                "- 确认现场后再决定修复、重新创建 checkpoint 或停止 Goal。",
            ]
        ).rstrip()
        + "\n",
    )
    state.artifacts = _dedupe([*state.artifacts, report_rel])
    _write_progress(run_dir, state, contract)
    _save_goal_state(run_dir, state)
    TraceWriter(run_dir / "goal-trace.jsonl").write(
        "goal_checkpoint_blocked",
        checkpoint=checkpoint,
        reason=reason,
    )
    progress.append("goal.checkpoint_blocked", checkpoint=checkpoint)
