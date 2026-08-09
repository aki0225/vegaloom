from __future__ import annotations

from ..goal_models import GoalCheckpointRecord, GoalContract, GoalState


def render_contract_markdown(contract: GoalContract) -> str:
    lines = [
        "# Goal Contract",
        "",
        f"- 仓库：`{contract.repo_path}`",
        f"- 输入：`{contract.input_source}`",
        f"- scope：`{contract.scope_profile or 'default'}`",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Non-goals",
        "",
    ]
    lines.extend(f"- {item}" for item in contract.non_goals or ["未显式声明。"])
    lines.extend(["", "## Success Conditions", ""])
    lines.extend(f"- {item}" for item in contract.success_conditions or ["未显式声明。"])
    lines.extend(["", "## Raw Goal", "", contract.raw_text.strip()])
    return "\n".join(lines).rstrip() + "\n"


def render_progress(state: GoalState, contract: GoalContract) -> str:
    lines = [
        "# Goal Progress",
        "",
        f"- run：`{state.run_id}`",
        f"- 状态：`{state.status}`",
        f"- 当前步骤：`{state.current_step}`",
        f"- 仓库：`{state.repo_path}`",
        f"- scope：`{state.scope_profile or 'default'}`",
        f"- checkpoint 数：`{state.checkpoint_count}`",
        f"- active child run：`{state.active_child_run or '无'}`",
        f"- 最近 child run：`{state.last_child_run or '无'}`"
        f"（{state.last_child_status or '未知'}）",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Checkpoints",
        "",
    ]
    if state.checkpoint_records:
        for record in state.checkpoint_records:
            refs = (
                ", ".join(
                    f"{item.type}:{item.run}"
                    + ("(eligible)" if item.completion_eligible else "(evidence)")
                    for item in record.refs
                )
                or "无"
            )
            mode = f"，mode=`{record.completion_mode}`" if record.completion_mode else ""
            lines.append(f"- `{record.checkpoint}`：`{record.status}`{mode}，refs：{refs}")
    elif state.checkpoints:
        lines.extend(f"- `{item}`" for item in state.checkpoints)
    else:
        lines.append("- 尚未生成 checkpoint plan。")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Goal P0 只维护状态、checkpoint plan 和经过校验的证据引用。",
            "- Goal P1 实验最多自动执行一个 checkpoint，边界处暂停。",
            "- 不自动修改目标仓库之外的路径，不自动 commit，不写长期 memory。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_checkpoint_plan(
    state: GoalState,
    contract: GoalContract,
    index: int,
    *,
    task_text: str | None = None,
) -> str:
    lines = [
        f"# Checkpoint {index:02d} Plan",
        "",
        f"- goal：`{state.run_id}`",
        f"- 仓库：`{state.repo_path}`",
        f"- scope：`{state.scope_profile or 'default'}`",
        "",
        "## Goal Objective",
        "",
        contract.objective,
    ]
    if task_text and task_text.strip():
        lines.extend(["", "## Checkpoint Task", "", task_text.strip()])
    lines.extend(
        [
            "",
            "## Suggested Execution",
            "",
            "1. 人工确认本 checkpoint 的目标和非目标。",
            "2. 如需自动执行，使用 `vega goal run --max-checkpoints 1`。",
            "3. 自动执行仍必须经过 workspace-check、verification、Gate 和 Reviewer。",
            "4. checkpoint 边界不会自动 commit、push 或进入下一个 checkpoint。",
            "",
            "## Boundary",
            "",
            "- 本文件是本 checkpoint 的唯一任务输入。",
            "- 失败、超时或证据不足时交还人工。",
            "- 不写长期 memory。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_checkpoint_report(
    state: GoalState,
    contract: GoalContract,
    record: GoalCheckpointRecord,
) -> str:
    lines = [
        f"# Checkpoint {record.checkpoint} Report",
        "",
        f"- goal：`{state.run_id}`",
        f"- 仓库：`{state.repo_path}`",
        f"- 状态：`{record.status}`",
        f"- 完成时间：`{record.completed_at or 'unknown'}`",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Evidence Refs",
        "",
    ]
    if record.refs:
        for item in record.refs:
            lines.extend(
                [
                    f"- `{item.type}`：`{item.run}`",
                    f"  - validated：`{item.validated}`",
                    f"  - completion eligible：`{item.completion_eligible}`",
                    f"  - validation：{item.validation_summary or '未提供'}",
                    f"  - note：{item.note or '无'}",
                ]
            )
    else:
        lines.append("- 未挂载证据引用。")
    lines.extend(
        [
            "",
            "## Completion Decision",
            "",
            f"- mode：`{record.completion_mode or 'unknown'}`",
            f"- note：{record.completed_note or '未填写。'}",
            "",
            "## Boundary",
            "",
            "- 本报告只汇总经过校验的证据引用。",
            "- 是否调用 worker/reviewer 以及是否修改目标仓库，以 child run、Goal trace 和 Git 现场为准。",
            "- Vega 未自动 commit、push、回滚，也未写长期 memory。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_stop_report(state: GoalState, contract: GoalContract, reason: str) -> str:
    return "\n".join(
        [
            "# Goal Stop Report",
            "",
            f"- run：`{state.run_id}`",
            f"- 仓库：`{state.repo_path}`",
            f"- 原因：{reason}",
            f"- checkpoint 数：`{state.checkpoint_count}`",
            "",
            "## Objective",
            "",
            contract.objective,
            "",
            "## 结论",
            "",
            "- 已停止继续调度新的 goal step。",
            "- 未自动回滚，未删除文件，未提交代码。",
        ]
    ).rstrip() + "\n"


def render_recovery_report(
    state: GoalState,
    previous_step: str,
    reason: str,
) -> str:
    return "\n".join(
        [
            "# Goal Recovery Report",
            "",
            f"- run：`{state.run_id}`",
            f"- 仓库：`{state.repo_path}`",
            f"- 原步骤：`{previous_step}`",
            f"- 原因：{reason}",
            "",
            "## 结论",
            "",
            "- 已将 goal 从 `running` 标记为 `needs_human`。",
            "- 未恢复外部 worker 上下文，未清理工作区，未继续执行。",
            "- 请人工检查目标仓库和 checkpoint 产物后再决定继续、停止或重开。",
        ]
    ).rstrip() + "\n"


def render_goal_final_report(state: GoalState, contract: GoalContract) -> str:
    lines = [
        "# Goal Final Report",
        "",
        f"- run：`{state.run_id}`",
        f"- 仓库：`{state.repo_path}`",
        f"- 状态：`{state.status}`",
        f"- 完成时间：`{state.completed_at or 'unknown'}`",
        f"- 完成说明：{state.completion_note or '未提供'}",
        "",
        "## Objective",
        "",
        contract.objective,
        "",
        "## Success Conditions",
        "",
    ]
    lines.extend(f"- {item}" for item in contract.success_conditions)
    lines.extend(["", "## Checkpoints", ""])
    for record in state.checkpoint_records:
        lines.append(
            f"- `{record.checkpoint}`：`{record.status}`，"
            f"mode=`{record.completion_mode or 'unknown'}`，refs={len(record.refs)}"
        )
    lines.extend(
        [
            "",
            "## Completion Boundary",
            "",
            "- Goal complete 是人工确认后的状态完成，不会自动 commit、push、release。",
            "- 完成结论来自 checkpoint 证据和人工说明，不代表 Vega 自动理解了全部业务正确性。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_goal_eval(results: list[str]) -> str:
    return "# Goal Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n"
