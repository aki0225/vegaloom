from __future__ import annotations

from pathlib import Path
from typing import Any

from .loop_initialization import loop_initialization_issues
from .models import LoopAutomationState
from .trace import read_trace_items
from .workspace_baseline import (
    INITIALIZATION_EVIDENCE_UNAVAILABLE,
    INITIALIZATION_TRACE_UNAVAILABLE,
    LEGACY_WORKSPACE_BASELINE_UNAVAILABLE,
)


def classify_assist_initialization_status(
    workspace: Path,
    run_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    if (
        state.get("status") != "needs_human"
        or state.get("automation_mode") != "assist"
        or state.get("current_step") != "waiting_for_worker"
    ):
        return state
    try:
        loop_state = LoopAutomationState.model_validate(state)
        repo_path = Path(loop_state.repo_path).resolve()
    except (OSError, ValueError):
        return {
            **state,
            "current_step": INITIALIZATION_EVIDENCE_UNAVAILABLE,
        }
    try:
        read_trace_items(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        return {
            **state,
            "current_step": INITIALIZATION_TRACE_UNAVAILABLE,
        }
    issues = loop_initialization_issues(
        workspace,
        run_dir,
        loop_state,
        repo_path,
    )
    if not issues:
        return state
    if issues == [LEGACY_WORKSPACE_BASELINE_UNAVAILABLE]:
        current_step = LEGACY_WORKSPACE_BASELINE_UNAVAILABLE
    else:
        current_step = INITIALIZATION_EVIDENCE_UNAVAILABLE
    return {**state, "current_step": current_step}


def initialization_next_steps(
    run_dir: Path,
    state: dict[str, Any],
) -> list[str] | None:
    if state.get("status") != "needs_human":
        return None
    current_step = state.get("current_step")
    if current_step in {
        "initialization_evidence_unavailable",
        "initialization_trace_unavailable",
    }:
        return [
            "读取 state、trace 和现有报告，确认初始化证据缺失、损坏或不一致。",
            "当前 run 的初始化证据不可验证，不能安全 continue。",
            "保留当前 run 作为证据，并从新的 loop 重新开始任务。",
        ]
    if current_step in {
        "legacy_workspace_baseline_unavailable",
        "workspace_baseline_dirty",
        "workspace_baseline_unavailable",
        "workspace_head_changed",
    }:
        return [
            "读取 state、trace 和现有报告，确认启动基线问题。",
            "当前 run 没有把任务交给 Worker，也不能安全 continue。",
            "清理或稳定目标仓库后，保留本 run 作为证据并重新创建新的 loop。",
        ]
    if current_step == "waiting_for_worker":
        return [
            f"读取 `{run_dir / 'worker-prompt.md'}`，让主会话/人工完成实现。",
            "实现后回到所属 ChangeRun 重新对账并执行门禁。",
        ]
    return None


def recovery_next_steps(
    run_dir: Path,
    state: dict[str, Any],
) -> list[str] | None:
    if state.get("status") != "needs_human":
        return None
    current_step = state.get("current_step")
    if current_step == "recovered_initialization_incomplete":
        return [
            f"读取 `{run_dir / 'recovery-report.md'}`，确认初始化中断位置。",
            "原始 brief 尚未绑定到 loop state，不能安全 continue。",
            "保留当前 run 作为中断证据，并从新的 run 重新开始任务。",
        ]
    if current_step != "recovered":
        return None

    interruption = latest_iteration_file(run_dir, "interruption-report.md")
    steps = [f"读取 `{run_dir / 'recovery-report.md'}`，确认中断原因和现场。"]
    if interruption.is_file():
        steps.append(f"读取 `{interruption}`，确认被冻结的 iteration 与原执行步骤。")
    steps.append("先人工检查目标仓库 `git status`，不要直接覆盖或清理未知文件。")
    latest = _latest_iteration(state)
    if (
        state.get("automation_mode") == "auto"
        and latest.get("lifecycle") == "interrupted"
        and latest.get("interrupted_step") == "worker"
    ):
        current_iteration = state.get("current_iteration")
        max_iterations = state.get("max_iterations")
        if (
            isinstance(current_iteration, int)
            and isinstance(max_iterations, int)
            and current_iteration >= max_iterations
        ):
            steps.extend(
                [
                    "当前已达到自动 Worker 迭代上限，不能再使用 `--rerun-worker`。",
                    "如需继续，请人工完成并验证现场修改，再回到所属 ChangeRun。",
                ]
            )
            return steps
        steps.extend(
            [
                "如果没有新的 tracked 或非 ignored untracked diff，"
                "由所属 ChangeRun 决定是否建立新的 Worker attempt。",
                "如果已有 partial work，不要启动第二个 Writer 覆盖现场；"
                "人工完成并验证后再回到所属 ChangeRun。",
            ]
        )
        return steps
    steps.append("如果工作区已有合理修复，回到所属 ChangeRun 继续对账。")
    return steps


def latest_iteration_file(run_dir: Path, filename: str) -> Path:
    matches = sorted(run_dir.glob(f"iterations/*/{filename}"))
    if not matches:
        return run_dir / filename
    return matches[-1]


def review_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    if state.get("current_step") == "context_budget":
        return [
            f"读取 `{run_dir / 'review-prompt-metrics.md'}`、"
            f"`{run_dir / 'review-context-budget-report.md'}` 和 Review Queue 状态。",
            "当前 reviewer 未启动或只完成部分队列任务；"
            "缩小任务，或人工确认新的 prompt 预算后重跑。",
        ]
    if state.get("current_step") == "evidence_truncated":
        return [
            f"读取 `{run_dir / 'review-context.json'}` 和 "
            f"`{run_dir / 'review-queue.json'}` 确认未覆盖文件。",
            f"读取 `{run_dir / 'review-findings.md'}`；"
            "当前结果不能视为完整 approve。",
            "请缩小任务/diff 后重跑，或由人工检查完整证据。",
        ]
    verdict = state.get("verdict")
    if verdict == "approve":
        return [
            "reviewer 已通过；回到主会话整理交付结论，人工检查后再 commit。"
        ]
    if verdict == "request_changes":
        return [
            f"读取 `{run_dir / 'review-findings.md'}`，"
            "按 findings 修复后重新 reflect + review。"
        ]
    return [
        f"读取 `{run_dir / 'review-runner-output.txt'}` 和 "
        f"`{run_dir / 'review-findings.md'}`，人工判断或重跑 reviewer。"
    ]


def verification_failure_next_steps(
    run_dir: Path,
    iteration: dict[str, Any],
) -> list[str]:
    verification = latest_iteration_file(run_dir, "verification-summary.md")
    if iteration.get("verification_failure_kind") == "project_config_invalid":
        return [
            f"项目配置预检失败，先读取 `{verification}`。",
            "修复目标仓库中的 `.vega.yaml` / `.vega.yml` 后重新运行；"
            "本轮未执行任何验证命令，也未启动 reviewer。",
            "配置恢复前不要把该结果解释为测试失败或代码回归。",
        ]
    if iteration.get("verification_failure_kind") == "workspace_capture_failed":
        return [
            f"工作区指纹采集失败，先读取 `{verification}`。",
            "人工确认目标仓库 Git 状态、ignored/untracked 清单和权限是否可读取；"
            "当前验证结果不能绑定到可信工作区快照。",
            "现场稳定后重新运行本轮任务，不要复用当前 verification artifact。",
        ]
    fix_prompt = latest_iteration_file(run_dir, "fix-prompt.md")
    return [
        f"自动验证失败，先读取 `{verification}`。",
        f"按 `{fix_prompt}` 修复后回到所属 ChangeRun 重新执行门禁。",
    ]


def _latest_iteration(state: dict[str, Any]) -> dict[str, Any]:
    iterations = state.get("iterations")
    if not isinstance(iterations, list) or not iterations:
        return {}
    latest = iterations[-1]
    return latest if isinstance(latest, dict) else {}
