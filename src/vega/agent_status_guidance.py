from __future__ import annotations

from pathlib import Path
from typing import Any


def agent_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    phase = state.get("agent_phase")
    pre_contract_steps = _pre_contract_change_next_steps(
        run_dir,
        state,
        phase,
    )
    if pre_contract_steps is not None:
        return pre_contract_steps
    if phase in {"planning", "awaiting_approval"}:
        if state.get("agent_run_kind") == "change":
            return [
                f"读取 `{run_dir / 'change-contract.json'}` 与 "
                f"`{run_dir / 'execution-plan.json'}`，核对授权边界和实施步骤。",
                f"需要修改时运行：`vega revise --run {run_dir.name} "
                "--contract <change-contract.json> "
                "--execution-plan <execution-plan.json>`。",
                f"仅在人工明确批准后运行：`vega approve --run {run_dir.name}`。",
            ]
        return [
            f"读取 `{run_dir / 'agent-plan.json'}`；这是旧 Task Card 或本机 run 的兼容状态。",
            "需要改变目标或范围时，生成 Change Contract 与 Execution Plan，并创建新的 ChangeRun。",
            "不要修改旧 Plan 来伪造新的人工授权。",
        ]
    if phase == "ready":
        return [
            f"执行当前批准 Work Item：`vega run --run {run_dir.name}`。",
            f"另一个终端可运行：`vega watch --run {run_dir.name} --follow`。",
        ]
    if phase in {"acting", "observing"}:
        return [
            f"当前 Agent 仍在执行或对账；运行：`vega watch --run {run_dir.name} --follow`。",
            f"如需人工停止：`vega stop --run {run_dir.name} --reason \"...\"`。",
        ]
    if phase == "finalizing":
        return [
            "Core Finish 已完成但终态尚未发布；重新运行同一 ChangeRun，"
            f"或检查 `{run_dir / 'trace.jsonl'}` 后人工处理。",
        ]
    if phase == "completed":
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和 child Core Finish 证据。",
            _completed_git_step(state),
        ]
    if phase == "needs_human":
        return [
            f"读取 `{run_dir / 'status-card.md'}`、最新 Checkpoint 与 Trace，确认阻断原因。",
            "根据现场选择 revise、resume、handoff 或停止；"
            "不要在证据不明时启动第二 Writer。",
        ]
    if phase == "stopped":
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和最新 Checkpoint，确认保留的 Workspace。",
            "当前 run 已停止；如需继续，请生成 Handoff，"
            "或从 Git Task Card 创建新的本机 run。",
        ]
    return [f"读取 `{run_dir / 'agent-state.json'}`，人工确认 Agent 状态。"]


def _pre_contract_change_next_steps(
    run_dir: Path,
    state: dict[str, Any],
    phase: object,
) -> list[str] | None:
    if not _is_pre_contract_change(state):
        return None
    if phase in {"planning", "awaiting_approval"}:
        return _pre_contract_planning_next_steps(run_dir)
    if phase == "needs_human":
        if state.get("active_planning_execution_id"):
            return [
                f"读取 `{run_dir / 'status-card.md'}` 与当前 Planning execution，"
                "先确认受管进程已经退出。",
                f"进程退出后重新运行：`vega run --run {run_dir.name}`，"
                "只做终态对账，不启动第二个 Planner。",
                f"进程仍在运行时可请求停止：`vega stop --run {run_dir.name} "
                "--reason \"...\"`。",
            ]
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和最新 Checkpoint，确认 Planning 阻断原因。",
            "未编译的 Planning ChangeRun 不能使用 resume 伪造 ready；"
            "按阻断原因重新调查、生成 Handoff 或新建 Planning run。",
        ]
    if phase == "stopped" and not (run_dir / "planning-proposal.json").is_file():
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和最新 Checkpoint，确认 Planning 已停止。",
            "当前 run 没有完整 Proposal，不能生成跨机 Handoff；"
            "如需继续，请创建新的 Planning ChangeRun。",
        ]
    return None


def _pre_contract_planning_next_steps(
    run_dir: Path,
) -> list[str]:
    if (run_dir / "planning-proposal.json").is_file():
        return [
            f"读取 `{run_dir / 'planning-proposal.md'}`，核对事实、假设、范围和未决问题。",
            "当前 Proposal 尚未编译为 Change Contract，不能批准或启动 Worker。",
            f"需要换机时运行：`vega handoff --run {run_dir.name} --reason \"...\"`。",
        ]
    return [
        f"运行只读调查：`vega run --run {run_dir.name}`。",
        f"另一个终端可运行：`vega watch --run {run_dir.name} --follow`。",
        f"如需停止：`vega stop --run {run_dir.name} --reason \"...\"`。",
    ]


def _is_pre_contract_change(state: dict[str, Any]) -> bool:
    persisted = state.get("persisted_agent_state")
    return (
        state.get("agent_run_kind") == "change"
        and isinstance(persisted, dict)
        and persisted.get("contract_revision") is None
    )


def agent_artifact_names(state: dict[str, Any]) -> list[str]:
    names = [
        "agent-state.json",
        "agent-plan.json",
        "status-card.md",
        "task-brief.md",
        "task-brief-manifest.json",
        "trace.jsonl",
        "provider-sessions.json",
    ]
    checkpoint_id = state.get("latest_checkpoint_id")
    if isinstance(checkpoint_id, str):
        names.append(f"checkpoints/{checkpoint_id}.json")
    if state.get("agent_run_kind") == "change":
        persisted = state.get("persisted_agent_state")
        if (
            isinstance(persisted, dict)
            and persisted.get("contract_revision") is None
        ):
            names.extend(
                [
                    "planning-request.json",
                    "project-context.md",
                    "planning-proposal.json",
                    "planning-proposal.md",
                ]
            )
        else:
            names.extend(
                [
                    "change-contract.json",
                    "execution-plan.json",
                    "plan-card.md",
                    "agent-final-report.json",
                    "agent-final-report.md",
                ]
            )
    return names


def _completed_git_step(state: dict[str, Any]) -> str:
    if state.get("agent_run_kind") == "change":
        return "人工检查 Accepted Checkpoint 的累计 Diff，再决定是否 push、创建 PR 或合并。"
    return "人工检查全部 Diff 与验证结果后，再决定 commit 与 push。"
