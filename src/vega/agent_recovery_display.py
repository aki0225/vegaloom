"""恢复入口的只读展示查询；必要条件只用于排除，不授予执行资格。"""

from pathlib import Path

from .acceptance_submission import acceptance_explanation
from .agent_contract import AgentDecision, AgentObservation, AgentState
from .agent_core_recheck import core_recheck_available


def acceptance_for_display(run_dir: Path, state: AgentState, observation: AgentObservation | None):
    # 原补验准入要求风险已通过；缺少来源仍交给完整核验。
    if observation is not None and observation.risk != "passed":
        return None
    return acceptance_explanation(run_dir, state)


def core_recheck_for_display(run_dir: Path, decision: AgentDecision | None) -> bool:
    # 原重算只接纳 evidence.core_untrusted；未分类历史记录仍完整核验。
    if decision is not None and decision.reason_code not in (None, "evidence.core_untrusted"):
        return False
    return core_recheck_available(run_dir.parent.parent, run_dir.name)
