from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from .agent_contract import AgentState, canonical_digest
from .redaction import write_redacted_json_once


AgentOperationKind = Literal["worker", "verification_retry"]

_IDENTITY_FIELDS = frozenset(
    {
        "schema_version",
        "authority",
        "operation_kind",
        "run_id",
        "state_version",
        "work_item_id",
        "child_run",
        "operation_id",
    }
)


def operation_ref(operation_id: str) -> str:
    """返回 operation Artifact 的唯一 canonical 引用。"""

    return f"operations/{canonical_digest({'operation_id': operation_id})}.json"


def child_summary_ref(child_run: str, operation_id: str) -> str:
    """返回 child 与 operation 联合绑定摘要的唯一 canonical 引用。"""

    digest = canonical_digest(
        {
            "child": child_run,
            "operation_id": operation_id,
        }
    )
    return f"children/{digest}.json"


def reserve_operation_identity(
    run_dir: Path,
    state: AgentState,
    *,
    child_run: str,
    operation_id: str,
    operation_kind: AgentOperationKind = "worker",
    details: Mapping[str, object] | None = None,
) -> str:
    """一次性写入 operation 身份，附加字段不得覆盖绑定事实。"""

    if operation_kind not in {"worker", "verification_retry"}:
        raise ValueError("operation_kind 不受支持")
    extra = dict(details or {})
    conflicts = sorted(_IDENTITY_FIELDS.intersection(extra))
    if conflicts:
        raise ValueError(
            "operation 附加字段不得覆盖身份字段："
            + "、".join(conflicts)
        )
    relative = operation_ref(operation_id)
    payload: dict[str, object] = {
        "schema_version": 1,
        "authority": "agent_operation",
        "operation_kind": operation_kind,
        "run_id": state.run_id,
        "state_version": state.state_version,
        "work_item_id": state.current_work_item,
        "child_run": child_run,
        "operation_id": operation_id,
    }
    payload.update(extra)
    try:
        write_redacted_json_once(run_dir / relative, payload)
    except FileExistsError as exc:
        raise ValueError(
            "operation_id 已在当前 Agent run 使用，禁止复用旧执行身份"
        ) from exc
    return relative


def bound_operation_kind(
    run_dir: Path,
    state: AgentState,
) -> AgentOperationKind:
    """读取当前 active operation 的不可变类型；旧 Artifact 视为 Worker。"""

    if not state.active_operation_id or not state.active_child_run:
        raise ValueError("当前 Agent State 缺少 active operation 绑定")
    path = run_dir / operation_ref(state.active_operation_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("active operation Artifact 缺失或无法解析") from exc
    if not isinstance(payload, dict):
        raise ValueError("active operation Artifact 不是 JSON object")
    operation_kind = payload.get("operation_kind", "worker")
    if (
        payload.get("run_id") != state.run_id
        or payload.get("work_item_id") != state.current_work_item
        or payload.get("child_run") != state.active_child_run
        or payload.get("operation_id") != state.active_operation_id
        or operation_kind not in {"worker", "verification_retry"}
    ):
        raise ValueError("active operation Artifact 身份或类型不一致")
    return operation_kind
