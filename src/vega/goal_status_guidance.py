from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

GoalGuidanceRenderer = Callable[[Path, dict[str, Any]], list[str]]


def _bound_child_run(state: dict[str, Any]) -> str | None:
    records = state.get("checkpoint_records")
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "planned":
            continue
        child_run = record.get("bound_child_run")
        if isinstance(child_run, str) and child_run:
            return child_run
    return None


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
    active_child = state.get("active_child_run") or _bound_child_run(state)
    if isinstance(active_child, str) and active_child:
        return [
            f"Goal P1 正在执行 child loop `{active_child}`；运行 `vega watch --run {active_child} --follow` 查看安全进度。",
            f'需要旁路停止时运行：`vega stop --run {active_child} --reason "..."`。',
            f"child 结束但父 Goal 尚未归档时运行：`vega goal reconcile --run {run_dir.name}`。",
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
    bound_child = _bound_child_run(state)
    if bound_child:
        return [
            f"当前 checkpoint 仍绑定 child `{bound_child}`；先检查 child，再运行 `vega goal reconcile --run {run_dir.name}`。",
            f'如放弃目标，运行：`vega goal stop --run {run_dir.name} --reason "..."`。',
        ]
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
        child_run = _bound_child_run(state) or state.get("last_child_run")
        reconcile_step = (
            f"child 修复并完成后运行：`vega goal reconcile --run {run_dir.name}`。"
            if isinstance(child_run, str) and child_run
            else "确认现场后，可修复后重新创建 checkpoint，或停止当前 goal。"
        )
        return [
            f"读取最新 `checkpoint-blocked.md`、child run 和 `{run_dir / 'progress.md'}`。",
            "先人工检查工作区、验证和 Reviewer 证据，不自动重试或进入下一个 checkpoint。",
            reconcile_step,
        ]
    if current_step == "child_recovery_required":
        child_run = (
            state.get("active_child_run")
            or _bound_child_run(state)
            or state.get("last_child_run")
        )
        return [
            f"child `{child_run or '<child_run>'}` 已失去可确认执行主体；先读取其 recovery guidance。",
            f'先运行：`vega recover --run {child_run or "<child_run>"} --reason "父 Goal 重新核对发现执行中断"`。',
            f"人工确认现场并完成 `vega loop continue` 后，运行：`vega goal reconcile --run {run_dir.name}`。",
        ]
    active_child = state.get("active_child_run") or _bound_child_run(state)
    if isinstance(active_child, str) and active_child:
        return [
            f"父 Goal 已恢复，但仍绑定 child `{active_child}`；不要直接 resume。",
            f"先检查或恢复 child，随后运行：`vega goal reconcile --run {run_dir.name}`。",
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
