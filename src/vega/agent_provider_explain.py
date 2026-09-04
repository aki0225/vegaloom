from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar, cast

from .agent_contract import AgentState
from .provider_session import (
    PROVIDER_SESSIONS_ARTIFACT,
    PendingInteraction,
    ProviderSessionState,
    load_provider_sessions,
)


class _ExplanationView(Protocol):
    unknowns: list[str]
    evidence_refs: list[str]

    def model_copy(self, *, update: dict[str, object]) -> object: ...


ExplanationT = TypeVar("ExplanationT", bound=_ExplanationView)


def provider_interaction_projection(
    run_dir: Path,
    state: AgentState,
    *,
    provider_sessions: ProviderSessionState | None = None,
    provider_issue: str | None = None,
) -> tuple[list[PendingInteraction], list[str]]:
    """只把绑定当前活动 Turn 的请求投影成待授权事项。"""

    if provider_sessions is not None:
        sessions = provider_sessions
    elif provider_issue is not None:
        return [], [provider_issue]
    else:
        try:
            sessions = load_provider_sessions(run_dir)
        except ValueError:
            return [], ["Provider Session 协调状态无法验证，已忽略其运行提示。"]
    pending = sorted(
        (
            item
            for item in sessions.interactions
            if item.status == "pending"
        ),
        key=lambda item: item.created_at,
    )
    if not pending:
        return [], []
    if not _phase_is_active(state):
        return [], [
            f"发现 {len(pending)} 个陈旧 Provider 请求；"
            f"Core 阶段 {state.phase} 不接受其覆盖。"
        ]

    valid: list[PendingInteraction] = []
    warnings: list[str] = []
    for interaction in pending:
        issue = _binding_issue(sessions, state, interaction)
        if issue is None:
            valid.append(interaction)
        else:
            warnings.append(
                f"Provider 请求 {interaction.interaction_id} "
                f"未通过当前 Turn 绑定：{issue}"
            )
    return valid, warnings


def with_provider_warnings(
    explanation: ExplanationT,
    warnings: list[str],
) -> ExplanationT:
    """把协调层问题作为未知项附加，不改变可信 Core 结论。"""

    if not warnings:
        return explanation
    return cast(
        ExplanationT,
        explanation.model_copy(
            update={
                "unknowns": list(
                    dict.fromkeys(
                        [
                            *explanation.unknowns,
                            *(
                                f"Provider 协调告警：{warning}"
                                for warning in warnings
                            ),
                        ]
                    )
                ),
                "evidence_refs": list(
                    dict.fromkeys(
                        [
                            *explanation.evidence_refs,
                            PROVIDER_SESSIONS_ARTIFACT,
                        ]
                    )
                ),
            }
        ),
    )


def _phase_is_active(state: AgentState) -> bool:
    if state.phase == "planning":
        return state.active_planning_execution_id is not None
    return bool(
        state.phase in {"acting", "observing"}
        and state.operation_started
        and state.active_operation_id
        and state.active_child_run
    )


def _binding_issue(
    sessions: ProviderSessionState,
    state: AgentState,
    interaction: PendingInteraction,
) -> str | None:
    handle = sessions.handles.get(interaction.role_key)
    if handle is None:
        return "缺少对应 Session handle"
    if handle.role != interaction.role_key or handle.provider != "codex":
        return "Provider 或角色不一致"
    if handle.owner != "vega" or handle.lifecycle != "waiting_user":
        return "Session 所有者或生命周期不一致"
    if not handle.permissions_verified:
        return "Session 权限尚未验证"
    if (
        not interaction.thread_id
        or not interaction.turn_id
        or handle.thread_id != interaction.thread_id
        or handle.last_turn_id != interaction.turn_id
    ):
        return "Thread 或 Turn 不一致"
    if state.phase != "planning" and handle.work_item_id != state.current_work_item:
        return "Session 未绑定当前 Work Item"
    return None
