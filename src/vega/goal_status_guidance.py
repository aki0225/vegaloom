from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

GoalGuidanceRenderer = Callable[[Path, dict[str, Any]], list[str]]


def goal_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    renderer = _STATUS_RENDERERS.get(str(state.get("status") or ""))
    if renderer is None:
        return [f"读取 `{run_dir / 'goal-state.json'}` 确认当前 goal 状态。"]
    return renderer(run_dir, state)


def _created_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    return [
        f"人工审查 `{run_dir / 'goal-contract.md'}`。",
        f"确认后运行：`vega goal step --run {run_dir.name}` 生成第一个 checkpoint plan。",
    ]


def _running_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    active_child = state.get("active_child_run")
    if isinstance(active_child, str) and active_child:
        return [
            f"Goal P1 正在执行 child loop `{active_child}`；运行 `vega watch --run {active_child} --follow` 查看安全进度。",
            f'需要旁路停止时运行：`vega stop --run {active_child} --reason "..."`。',
            "child loop 结束前不要重复执行 `vega goal run`。",
        ]
    return [
        f"读取 `{run_dir / 'progress.md'}` 和最新 checkpoint plan。",
        f"完成人工执行后运行：`vega goal attach --run {run_dir.name} --checkpoint <n> --ref <child_run> --type <type>`。",
        f"证据挂载完成后运行：`vega goal checkpoint-done --run {run_dir.name} --checkpoint <n>`。",
    ]


def _checkpoint_done_next_steps(
    run_dir: Path,
    state: dict[str, Any],
) -> list[str]:
    return [
        f"读取 `{run_dir / 'progress.md'}` 和最新 `checkpoint-report.md`。",
        f"如继续推进，运行：`vega goal step --run {run_dir.name}` 生成下一个 checkpoint plan。",
        f'如 success conditions 已满足，运行：`vega goal complete --run {run_dir.name} --note "..."`。',
        f'如放弃目标，运行：`vega goal stop --run {run_dir.name} --reason "..."`。',
    ]


def _paused_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    return [
        f"如确认继续，运行：`vega goal resume --run {run_dir.name}`。",
        f'如方向变化，运行：`vega goal stop --run {run_dir.name} --reason "..."`。',
    ]


def _needs_human_next_steps(
    run_dir: Path,
    state: dict[str, Any],
) -> list[str]:
    current_step = state.get("current_step")
    if current_step == "completion_eval_failed":
        return [
            f"读取 `{run_dir / 'goal-eval.md'}` 和 `{run_dir / 'goal-final-report.md'}`。",
            "修复缺失产物或不可信 checkpoint 证据后，再重新执行 goal complete。",
        ]
    if current_step == "checkpoint_blocked":
        return [
            f"读取最新 `checkpoint-blocked.md`、child run 和 `{run_dir / 'progress.md'}`。",
            "先人工检查工作区、验证和 Reviewer 证据，不自动重试或进入下一个 checkpoint。",
            "确认现场后，可修复后重新创建 checkpoint，或停止当前 goal。",
        ]
    return [
        f"读取 `{run_dir / 'recovery-report.md'}` 和 `{run_dir / 'progress.md'}`。",
        "人工检查目标仓库和 checkpoint 产物，再决定 resume、stop 或重开 goal。",
    ]


def _stopped_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    return [
        f"读取 `{run_dir / 'stop-report.md'}` 了解停止原因。",
        "该 goal 不再调度新的 checkpoint；如目标仍有效，建议重开新的 goal。",
    ]


def _success_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    return [
        f"读取 `{run_dir / 'goal-final-report.md'}` 和 `{run_dir / 'goal-eval.md'}`。",
        "Goal 已完成；如需提交代码，仍应人工检查关联 child run 的 diff 和 finish 报告。",
    ]


_STATUS_RENDERERS: dict[str, GoalGuidanceRenderer] = {
    "created": _created_next_steps,
    "running": _running_next_steps,
    "checkpoint_done": _checkpoint_done_next_steps,
    "paused": _paused_next_steps,
    "needs_human": _needs_human_next_steps,
    "stopped": _stopped_next_steps,
    "success": _success_next_steps,
}
