from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agent_contract_support import canonical_digest, utc_now
from .redaction import redact_text, redact_value
from .run_lock import RunMutationLock


PROVIDER_SESSIONS_ARTIFACT = "provider-sessions.json"

SessionOwner = Literal["vega", "human"]
SessionLifecycle = Literal[
    "new",
    "idle",
    "active",
    "waiting_user",
    "unavailable",
]
SteerStatus = Literal["queued", "delivered", "rejected"]
InteractionStatus = Literal["pending", "responded", "closed"]
ProviderSandbox = Literal[
    "read-only",
    "workspace-write",
    "danger-full-access",
    "external",
]


class ProviderSessionHandle(BaseModel):
    """Provider Thread 的本机协调记录，不属于成功证据。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    provider: str = "codex"
    role: str
    thread_id: str | None = None
    owner: SessionOwner = "vega"
    lifecycle: SessionLifecycle = "new"
    work_item_id: str | None = None
    contract_revision: int | None = Field(default=None, ge=1)
    plan_revision: int | None = Field(default=None, ge=1)
    sandbox: ProviderSandbox | None = None
    approval_policy: str | None = None
    permissions_verified: bool = False
    last_turn_id: str | None = None
    compaction_pending: bool = False
    turn_count: int = Field(default=0, ge=0)
    compaction_count: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=0)
    last_event: str | None = None
    updated_at: str = Field(default_factory=utc_now)


class PendingSteer(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    steer_id: str
    role_key: str
    instruction: str
    status: SteerStatus = "queued"
    queued_at: str = Field(default_factory=utc_now)
    delivered_turn_id: str | None = None
    result_note: str | None = None


class PendingInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    interaction_id: str
    role_key: str
    rpc_request_id: str
    method: str
    thread_id: str
    turn_id: str | None = None
    summary: str
    status: InteractionStatus = "pending"
    response: dict[str, object] | None = None
    created_at: str = Field(default_factory=utc_now)
    resolved_at: str | None = None


class ProviderSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: int = 1
    run_id: str
    revision: int = Field(default=1, ge=1)
    handles: dict[str, ProviderSessionHandle] = Field(default_factory=dict)
    steers: list[PendingSteer] = Field(default_factory=list)
    interactions: list[PendingInteraction] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


SessionMutation = Callable[[ProviderSessionState], None]


def load_provider_sessions(run_dir: Path) -> ProviderSessionState:
    path = run_dir / PROVIDER_SESSIONS_ARTIFACT
    if not path.exists():
        return ProviderSessionState(run_id=run_dir.name)
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Provider Session 状态无法读取") from exc
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"kind", "data", "digest"}
        or envelope.get("kind") != "provider_sessions"
        or not isinstance(envelope.get("data"), dict)
        or envelope.get("digest") != canonical_digest(envelope["data"])
    ):
        raise ValueError("Provider Session 状态 envelope 不可信")
    try:
        state = ProviderSessionState.model_validate(envelope["data"])
    except ValidationError as exc:
        raise ValueError("Provider Session 状态 schema 无效") from exc
    if state.run_id != run_dir.name:
        raise ValueError("Provider Session 状态绑定了其他 run")
    return state


def save_provider_sessions(run_dir: Path, state: ProviderSessionState) -> None:
    if state.run_id != run_dir.name:
        raise ValueError("Provider Session 状态不能写入其他 run")
    state.updated_at = utc_now()
    data = redact_value(state.model_dump(mode="json"))
    envelope = {
        "kind": "provider_sessions",
        "data": data,
        "digest": canonical_digest(data),
    }
    path = run_dir / PROVIDER_SESSIONS_ARTIFACT
    temp = path.with_name(f".provider-sessions-{uuid4().hex[:12]}")
    temp.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    last_error: OSError | None = None
    for _ in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.02)
    temp.unlink(missing_ok=True)
    assert last_error is not None
    raise last_error


def mutate_provider_sessions(
    run_dir: Path,
    operation: str,
    mutation: SessionMutation,
) -> ProviderSessionState:
    with RunMutationLock.acquire(run_dir, operation):
        state = load_provider_sessions(run_dir)
        mutation(state)
        state.revision += 1
        _trim_history(state)
        save_provider_sessions(run_dir, state)
        return state


def ensure_session_handle(
    state: ProviderSessionState,
    role_key: str,
    *,
    provider: str = "codex",
    work_item_id: str | None,
    contract_revision: int | None,
    plan_revision: int | None,
) -> ProviderSessionHandle:
    handle = state.handles.get(role_key)
    if handle is None:
        handle = ProviderSessionHandle(
            provider=provider,
            role=role_key,
            work_item_id=work_item_id,
            contract_revision=contract_revision,
            plan_revision=plan_revision,
        )
        state.handles[role_key] = handle
        return handle
    provider_changed = handle.provider != provider
    if provider_changed and handle.owner != "vega":
        raise ValueError("人工接管的 Provider Session 不能切换 Provider")
    contract_changed = (
        handle.contract_revision is not None
        and contract_revision is not None
        and handle.contract_revision != contract_revision
    )
    reviewer_plan_changed = (
        role_key.startswith("reviewer:")
        and handle.plan_revision is not None
        and plan_revision is not None
        and handle.plan_revision != plan_revision
    )
    if provider_changed or contract_changed or reviewer_plan_changed:
        reason = (
            "provider_changed"
            if provider_changed
            else (
                "contract_revision_changed"
                if contract_changed
                else "review_plan_revision_changed"
            )
        )
        _reset_session_handle(state, handle, reason)
    handle.provider = provider
    handle.work_item_id = work_item_id
    handle.contract_revision = contract_revision
    handle.plan_revision = plan_revision
    handle.updated_at = utc_now()
    return handle


def _reset_session_handle(
    state: ProviderSessionState,
    handle: ProviderSessionHandle,
    reason: str,
) -> None:
    handle.thread_id = None
    handle.lifecycle = "new"
    handle.last_turn_id = None
    handle.sandbox = None
    handle.approval_policy = None
    handle.permissions_verified = False
    handle.compaction_pending = False
    handle.turn_count = 0
    handle.compaction_count = 0
    handle.total_tokens = None
    handle.cached_input_tokens = None
    handle.context_window = None
    handle.last_event = reason
    note = {
        "provider_changed": "Provider 已变化",
        "contract_revision_changed": "合同 revision 已变化",
        "review_plan_revision_changed": "Reviewer Plan revision 已变化",
    }[reason]
    for steer in state.steers:
        if steer.role_key == handle.role and steer.status == "queued":
            steer.status = "rejected"
            steer.result_note = note
    for interaction in state.interactions:
        if interaction.role_key == handle.role and interaction.status == "pending":
            interaction.status = "closed"
            interaction.resolved_at = utc_now()


def resolve_session_role(run_dir: Path, requested: str) -> str:
    """把 `worker` / `reviewer` 映射到当前本机会话，不猜测其他角色。"""

    state = load_provider_sessions(run_dir)
    if requested in state.handles:
        return requested
    if requested != "reviewer":
        raise ValueError("目标 Provider Session 不存在")
    reviewers = [
        (key, handle)
        for key, handle in state.handles.items()
        if key.startswith("reviewer:")
    ]
    if not reviewers:
        raise ValueError("当前 run 尚未建立 Reviewer Session")
    reviewers.sort(key=lambda item: item[1].updated_at, reverse=True)
    return reviewers[0][0]


def queue_steer(run_dir: Path, role_key: str, instruction: str) -> PendingSteer:
    normalized = redact_text(instruction.strip())
    if not normalized:
        raise ValueError("Steer 指令不能为空")
    if len(normalized.encode("utf-8")) > 8 * 1024:
        raise ValueError("Steer 指令不能超过 8 KiB")
    created = PendingSteer(
        steer_id=f"steer-{uuid4().hex[:12]}",
        role_key=role_key,
        instruction=normalized,
    )

    def mutate(state: ProviderSessionState) -> None:
        handle = state.handles.get(role_key)
        if handle is None or handle.owner != "vega":
            raise ValueError("目标会话不存在或当前由人工接管")
        state.steers.append(created)

    mutate_provider_sessions(run_dir, "agent.steer", mutate)
    return created


def respond_to_interaction(
    run_dir: Path,
    interaction_id: str,
    response: dict[str, object],
    *,
    expected: PendingInteraction | None = None,
    expected_provider: str | None = None,
) -> PendingInteraction:
    selected: PendingInteraction | None = None

    def mutate(state: ProviderSessionState) -> None:
        nonlocal selected
        matches = [
            item
            for item in state.interactions
            if item.interaction_id == interaction_id
        ]
        if len(matches) != 1 or matches[0].status != "pending":
            raise ValueError("待响应请求不存在、已关闭或已处理")
        if expected is not None and _interaction_binding(matches[0]) != (
            _interaction_binding(expected)
        ):
            raise ValueError("待响应请求已变化，拒绝使用旧提示结果")
        handle = state.handles.get(matches[0].role_key)
        if (
            handle is None
            or handle.role != matches[0].role_key
            or (
                expected_provider is not None
                and handle.provider != expected_provider
            )
            or handle.owner != "vega"
            or handle.lifecycle != "waiting_user"
            or not handle.permissions_verified
            or not matches[0].thread_id
            or not matches[0].turn_id
            or handle.thread_id != matches[0].thread_id
            or handle.last_turn_id != matches[0].turn_id
        ):
            raise ValueError("待响应请求不再绑定当前 Provider Turn")
        matches[0].response = redact_value(response)
        matches[0].status = "responded"
        matches[0].resolved_at = utc_now()
        selected = matches[0].model_copy(deep=True)

    mutate_provider_sessions(run_dir, "agent.respond", mutate)
    assert selected is not None
    return selected


def close_pending_interactions(
    run_dir: Path,
    *,
    interaction_id: str | None = None,
) -> int:
    """停止 Provider attempt 后关闭未发送的请求，避免留下可误响应的假 pending。"""

    closed_count = 0

    def mutate(state: ProviderSessionState) -> None:
        nonlocal closed_count
        for interaction in state.interactions:
            if (
                interaction.status != "pending"
                or (
                    interaction_id is not None
                    and interaction.interaction_id != interaction_id
                )
            ):
                continue
            interaction.status = "closed"
            interaction.resolved_at = utc_now()
            closed_count += 1

    mutate_provider_sessions(run_dir, "agent.session", mutate)
    return closed_count


def summarize_provider_interaction(
    method: str,
    params: dict[str, object],
) -> str:
    """只保留请求类型和原因，不把命令、路径或工具参数写入协调状态。"""

    reason = params.get("reason")
    if method == "item/commandExecution/requestApproval":
        detail = _command_approval_summary(params)
    else:
        labels = {
            "item/fileChange/requestApproval": "文件修改",
            "item/permissions/requestApproval": "权限提升",
            "item/tool/requestUserInput": "工具请求用户输入",
            "mcpServer/elicitation/request": (
                f"MCP 请求：{params.get('serverName') or 'unknown'}"
            ),
        }
        detail = labels.get(method, method)
    if isinstance(reason, str) and reason.strip():
        return redact_text(f"{detail}；{reason.strip()}")[:500]
    return redact_text(detail)[:500]


def set_session_owner(
    run_dir: Path,
    role_key: str,
    owner: SessionOwner,
) -> ProviderSessionHandle:
    selected: ProviderSessionHandle | None = None

    def mutate(state: ProviderSessionState) -> None:
        nonlocal selected
        handle = state.handles.get(role_key)
        if handle is None or handle.thread_id is None:
            raise ValueError("目标 Provider Session 尚未建立")
        if handle.lifecycle in {"active", "waiting_user"}:
            raise ValueError("活动 Turn 尚未结束，不能变更会话所有者")
        handle.owner = owner
        handle.updated_at = utc_now()
        selected = handle.model_copy(deep=True)

    operation = "agent.takeover" if owner == "human" else "agent.reclaim"
    mutate_provider_sessions(run_dir, operation, mutate)
    assert selected is not None
    return selected


def session_status_projection(
    run_dir: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """生成状态卡需要的白名单字段；损坏时不影响 Core 状态查询。"""

    try:
        state = load_provider_sessions(run_dir)
    except ValueError:
        return [], "Provider Session 协调状态无法验证；Core 证据不受影响。"
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


def _trim_history(state: ProviderSessionState) -> None:
    # 待发送指令和待响应请求不能为了控制文件大小而被历史裁剪。
    state.steers = _keep_active_and_recent(
        state.steers,
        active=lambda item: item.status == "queued",
    )
    state.interactions = _keep_active_and_recent(
        state.interactions,
        active=lambda item: item.status == "pending",
    )


def _keep_active_and_recent(items: list, *, active: Callable[[object], bool]) -> list:
    historical_indexes = [index for index, item in enumerate(items) if not active(item)]
    keep_historical = set(historical_indexes[-100:])
    return [
        item
        for index, item in enumerate(items)
        if active(item) or index in keep_historical
    ]


def _command_approval_summary(params: dict[str, object]) -> str:
    actions = params.get("commandActions")
    if not isinstance(actions, list) or not actions:
        return "命令执行"
    action_labels = {
        "read": "读取文件",
        "listFiles": "列出文件",
        "search": "搜索文件",
    }
    action_types = [
        item.get("type") if isinstance(item, dict) else None
        for item in actions
    ]
    if any(
        not isinstance(action_type, str) or action_type not in action_labels
        for action_type in action_types
    ):
        return "未分类命令执行（请接管原生会话确认）"
    labels = [
        action_labels[action_type]
        for action_type in action_types[:5]
        if isinstance(action_type, str)
    ]
    return "、".join(dict.fromkeys(labels))


def _interaction_binding(interaction: PendingInteraction) -> tuple[object, ...]:
    return (
        interaction.interaction_id,
        interaction.role_key,
        interaction.rpc_request_id,
        interaction.method,
        interaction.thread_id,
        interaction.turn_id,
        interaction.summary,
        interaction.created_at,
    )
