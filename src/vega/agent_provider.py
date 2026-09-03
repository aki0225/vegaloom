from __future__ import annotations

from pathlib import Path
from typing import Literal

from .provider_session import load_provider_sessions


AgentProvider = Literal["codex", "claude"]

_SESSION_PROVIDER_TO_AGENT: dict[str, AgentProvider] = {
    "codex": "codex",
    "codex-app-server": "codex",
    "codex-exec": "codex",
    "claude": "claude",
    "claude-code": "claude",
}


def normalize_agent_provider(value: str) -> AgentProvider:
    normalized = value.strip().lower()
    if normalized in {"codex", "codex-exec", "codex-app-server"}:
        return "codex"
    if normalized in {"claude", "claude-code"}:
        return "claude"
    raise ValueError(f"不支持的 Coding Agent Provider：{value}")


def resolve_run_provider(
    run_dir: Path,
    requested: str | None,
) -> AgentProvider:
    """固定一条 ChangeRun 使用的 Provider，避免恢复时静默切换会话。"""

    selected = (
        normalize_agent_provider(requested)
        if requested is not None
        else None
    )
    state = load_provider_sessions(run_dir)
    observed: set[AgentProvider] = set()
    for handle in state.handles.values():
        provider = _SESSION_PROVIDER_TO_AGENT.get(handle.provider)
        if provider is None:
            raise ValueError(
                f"Provider Session 使用了未知 Provider：{handle.provider}"
            )
        observed.add(provider)
    if len(observed) > 1:
        raise ValueError("同一 ChangeRun 出现多个 Provider，必须人工核对")
    existing = next(iter(observed), None)
    if selected is not None and existing is not None and selected != existing:
        raise ValueError(
            f"当前 ChangeRun 已绑定 {existing}，不能切换为 {selected}；"
            "请创建新的 ChangeRun"
        )
    return selected or existing or "codex"


def provider_resume_command(provider: str, thread_id: str) -> str:
    selected = normalize_agent_provider(provider)
    if selected == "claude":
        return f"claude --resume {thread_id}"
    return f"codex resume {thread_id}"
