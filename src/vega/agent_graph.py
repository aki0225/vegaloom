from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Literal, TypedDict

from .agent_contract import AgentDecision, AgentState
from .redaction import redact_text


GraphRoute = Literal["next", "repair", "replan", "human", "finalize"]


class SupervisorGraphState(TypedDict, total=False):
    run_id: str
    phase: str
    route: GraphRoute
    route_reason: str
    interrupt_reason: str


def langgraph_available() -> bool:
    return importlib.util.find_spec("langgraph") is not None


def build_supervisor_graph(*, checkpointer: Any | None = None) -> Any:
    """构建最小控制图；业务事实仍由 Agent State、Workspace 和 Core Artifact 管理。"""

    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:
        raise RuntimeError(
            "当前环境未安装 LangGraph；Agent 合同与运行时仍可使用，"
            "但 Gate 1 图游标和 interrupt 需要安装可选依赖。"
        ) from exc

    graph = StateGraph(SupervisorGraphState)

    def dispatch(state: SupervisorGraphState) -> SupervisorGraphState:
        return state

    def await_human(state: SupervisorGraphState) -> SupervisorGraphState:
        decision = interrupt(
            {
                "run_id": state.get("run_id"),
                "phase": state.get("phase"),
                "reason": state.get("route_reason") or state.get("interrupt_reason"),
                "allowed_actions": ["replan", "human"],
            }
        )
        return {"interrupt_reason": str(decision)}

    def route(state: SupervisorGraphState) -> str:
        return state.get("route", "human")

    graph.add_node("dispatch", dispatch)
    graph.add_node("await_human", await_human)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges(
        "dispatch",
        route,
        {
            "next": END,
            "repair": END,
            "replan": "await_human",
            "human": "await_human",
            "finalize": END,
        },
    )
    graph.add_edge("await_human", END)
    return graph.compile(checkpointer=checkpointer)


def compile_gate1_graph() -> Any:
    """使用内存 checkpoint 完成 Gate 1 smoke；本地 SQLite 属于后续恢复 Gate。"""

    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError as exc:
        raise RuntimeError("LangGraph checkpoint 组件不可用") from exc
    return build_supervisor_graph(checkpointer=InMemorySaver())


def record_supervisor_route(
    run_dir: Path,
    state: AgentState,
    decision: AgentDecision,
) -> bool:
    """持久化图游标并返回是否停在人工 interrupt；不写入业务成功状态。"""

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "Supervisor Agent 需要 LangGraph SQLite checkpoint；"
            "请安装项目的 agent 可选依赖。"
        ) from exc

    database = run_dir / "graph-checkpoints.sqlite"
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        graph = build_supervisor_graph(checkpointer=checkpointer)
        result = graph.invoke(
            {
                "run_id": state.run_id,
                "phase": state.phase,
                "route": decision.selected_action,
                "route_reason": redact_text(decision.reason),
            },
            {"configurable": {"thread_id": state.run_id}},
        )
    return bool(result.get("__interrupt__"))
