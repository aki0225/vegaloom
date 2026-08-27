from __future__ import annotations

from pathlib import Path
from typing import Any


def agent_next_steps(run_dir: Path, state: dict[str, Any]) -> list[str]:
    phase = state.get("agent_phase")
    if phase in {"planning", "awaiting_approval"}:
        if state.get("agent_run_kind") == "change":
            return [
                f"读取 `{run_dir / 'change-contract.json'}` 与 "
                f"`{run_dir / 'execution-plan.json'}`，核对授权边界和实施步骤。",
                f"需要修改时运行：`vega agent replan --run {run_dir.name} "
                "--contract <change-contract.json> "
                "--execution-plan <execution-plan.json>`。",
                f"仅在人工明确批准后运行：`vega agent approve --run {run_dir.name}`。",
            ]
        return [
            f"读取 `{run_dir / 'agent-plan.json'}`；这是旧 Task Card 或本机 run 的兼容状态。",
            "需要改变目标或范围时，生成 Change Contract 与 Execution Plan，并创建新的 ChangeRun。",
            "不要修改旧 Plan 来伪造新的人工授权。",
        ]
    if phase == "ready":
        return [
            f"执行当前批准 Work Item：`vega agent run --run {run_dir.name}`。",
            f"另一个终端可运行：`vega watch --run {run_dir.name} --follow`。",
        ]
    if phase in {"acting", "observing"}:
        return [
            f"当前 Agent 仍在执行或对账；运行：`vega watch --run {run_dir.name} --follow`。",
            f"如需人工停止：`vega agent stop --run {run_dir.name} --reason \"...\"`。",
        ]
    if phase == "finalizing":
        return [
            f"Core Finish 已完成但 Supervisor 终态尚未发布；"
            f"运行：`vega agent finalize --run {run_dir.name}`。",
        ]
    if phase == "completed":
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和 child Core Finish 证据。",
            _completed_git_step(state),
        ]
    if phase == "needs_human":
        return [
            f"读取 `{run_dir / 'status-card.md'}`、最新 Checkpoint 与 Trace，确认阻断原因。",
            "根据现场选择 replan、resume-local、recover、handoff 或停止；"
            "不要在证据不明时启动第二 Writer。",
        ]
    if phase == "stopped":
        return [
            f"读取 `{run_dir / 'status-card.md'}` 和最新 Checkpoint，确认保留的 Workspace。",
            "当前本机 run 已停止，不能使用 resume-local；"
            "如需继续，请人工创建 Handoff 或新的 Agent run。",
        ]
    return [f"读取 `{run_dir / 'agent-state.json'}`，人工确认 Agent 状态。"]


def agent_artifact_names(state: dict[str, Any]) -> list[str]:
    names = [
        "agent-state.json",
        "agent-plan.json",
        "status-card.md",
        "task-brief.md",
        "task-brief-manifest.json",
        "trace.jsonl",
    ]
    checkpoint_id = state.get("latest_checkpoint_id")
    if isinstance(checkpoint_id, str):
        names.append(f"checkpoints/{checkpoint_id}.json")
    if state.get("agent_run_kind") == "change":
        names.extend(["change-contract.json", "execution-plan.json"])
    return names


def _completed_git_step(state: dict[str, Any]) -> str:
    if state.get("agent_run_kind") == "change":
        return "人工检查 Accepted Checkpoint 的累计 Diff，再决定是否 push、创建 PR 或合并。"
    return "人工检查全部 Diff 与验证结果后，再决定 commit 与 push。"
