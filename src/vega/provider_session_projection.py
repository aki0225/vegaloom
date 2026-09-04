from __future__ import annotations

from pathlib import Path

from .provider_session import ProviderSessionState, load_provider_sessions


def session_status_projection(
    run_dir: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """生成状态卡需要的白名单字段；损坏时不影响 Core 状态查询。"""

    try:
        state = load_provider_sessions(run_dir)
    except ValueError:
        return [], "Provider Session 协调状态无法验证；Core 证据不受影响。"
    return session_status_projection_from_state(state)


def session_status_projection_from_state(
    state: ProviderSessionState,
) -> tuple[list[dict[str, object]], str | None]:
    """从已读取状态生成展示摘要，避免同一快照重复读取文件。"""

    rows: list[dict[str, object]] = []
    for key, handle in sorted(state.handles.items()):
        rows.append(
            {
                "role": key,
                "provider": handle.provider,
                "owner": handle.owner,
                "lifecycle": handle.lifecycle,
                "thread_id": handle.thread_id,
                "work_item_id": handle.work_item_id,
                "sandbox": handle.sandbox,
                "approval_policy": handle.approval_policy,
                "permissions_verified": handle.permissions_verified,
                "turn_count": handle.turn_count,
                "compaction_count": handle.compaction_count,
                "last_event": handle.last_event,
                "total_tokens": handle.total_tokens,
                "cached_input_tokens": handle.cached_input_tokens,
                "context_window": handle.context_window,
                "queued_steers": sum(
                    item.role_key == key and item.status == "queued"
                    for item in state.steers
                ),
                "pending_interactions": sum(
                    item.role_key == key and item.status == "pending"
                    for item in state.interactions
                ),
            }
        )
    return rows, None
