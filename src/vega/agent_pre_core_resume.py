from __future__ import annotations

import json
from pathlib import Path

from .agent_contract import AgentObservation, AgentState
from .agent_operation import child_summary_ref
from .agent_persistence import read_agent_trace
from .agent_run_status import latest_dispatch_binding
from .agent_worker_evidence import hash_evidence_refs, require_child_quiescent
from .run_utils import resolve_run_dir


def require_pre_core_resume(workspace: Path, run_dir: Path, state: AgentState) -> None:
    """仅为显式恢复或有来源证据的范围修订重建未启动 Core 的输入。"""
    trace = read_agent_trace(run_dir / "trace.jsonl")
    if trace and trace[-1].get("event") == "change_execution_plan_auto_applied":
        from .agent_scope_replan import require_scope_replan_resume

        require_scope_replan_resume(workspace, run_dir, state)
    elif not trace or trace[-1].get("event") != "agent_resumed":
        raise ValueError("非 repair 的后续尝试必须来自显式本机恢复")
    require_pre_core_evidence(workspace, run_dir, state)


def require_pre_core_evidence(workspace: Path, run_dir: Path, state: AgentState) -> None:
    """确认最近 Writer 已停止，且该工作项从未进入 Core。"""
    binding = latest_dispatch_binding(run_dir, state)
    if binding is None or state.active_candidate_sha is not None:
        raise ValueError("恢复现场缺少原 Worker 绑定或已有 Candidate")
    child, operation = binding
    child_dir = resolve_run_dir(workspace, child)
    require_child_quiescent(child_dir)
    ref = child_summary_ref(child, operation)
    matches = []
    for path in (run_dir / "observations").glob("*.json"):
        observation = AgentObservation.model_validate_json(path.read_text(encoding="utf-8"))
        # 最近一次 Worker 可能在 repair 中途停止；不能因此丢掉更早的 Core finding。
        if observation.work_item_id == state.current_work_item and any(
            value != "not_run" for value in (observation.verification, observation.risk, observation.review)
        ):
            raise ValueError("当前 Work Item 已有 Core 门禁结果，请使用原门禁修复路径")
        if observation.child_run == child and observation.operation_id == operation:
            matches.append(observation)
    if len(matches) != 1:
        raise ValueError("无法唯一核对恢复来源 Observation")
    observation = matches[0]
    if (
        observation.work_item_id != state.current_work_item
        or observation.authority != "machine_reconcile"
        or any(value != "not_run" for value in (
            observation.verification, observation.risk, observation.review,
        ))
        or ref not in observation.evidence_refs
        or hash_evidence_refs(run_dir, observation.evidence_refs) != observation.evidence_sha256
    ):
        raise ValueError("恢复来源已进入 Core 或证据不一致；请使用原门禁修复路径")
    summary = json.loads((run_dir / ref).read_text(encoding="utf-8"))
    core = summary.get("core") if isinstance(summary, dict) else None
    if not isinstance(core, dict) or core.get("status") != "not_run" or (child_dir / "state.json").exists():
        raise ValueError("已有 Core 结果不能作为未启动 Core 的恢复现场")
