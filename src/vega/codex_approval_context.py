from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .agent_contract_support import utc_now
from .codex_app_server_rpc import CODEX_SUPPORTED_SERVER_REQUESTS
from .execution_paths import ExecutionPathGuard
from .provider_session import (
    PendingInteraction, load_provider_sessions, mutate_provider_sessions, summarize_provider_interaction,
)

_COMMAND = "item/commandExecution/requestApproval"
_FILE = "item/fileChange/requestApproval"
_MAX_CONTEXT_BYTES = 64 * 1024
_BINDING = ("interaction_id", "role_key", "rpc_request_id", "thread_id", "turn_id", "method")


def _context_path(run_dir: Path, reference: str) -> tuple[ExecutionPathGuard, Path]:
    if re.fullmatch(r"[a-f0-9]{32}-request-[a-f0-9]{12}\.json", reference) is None:
        raise ValueError("临时授权上下文引用无效")
    directory = run_dir / ".provider-approvals"
    return ExecutionPathGuard(run_dir, directory), directory / reference


def read_approval_context(run_dir: Path, interaction: PendingInteraction) -> dict:
    if not interaction.context_ref or not interaction.context_digest:
        raise ValueError("缺少完整临时授权上下文")
    guard, path = _context_path(run_dir, interaction.context_ref)
    guard.validate_artifact(path)
    with path.open("rb") as stream:
        raw = stream.read(_MAX_CONTEXT_BYTES + 1)
    if len(raw) > _MAX_CONTEXT_BYTES or hashlib.sha256(raw).hexdigest() != interaction.context_digest:
        raise ValueError("临时授权上下文已变化")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("binding") != {
        key: getattr(interaction, key) for key in _BINDING
    }:
        raise ValueError("临时授权上下文不属于当前请求")
    return payload


def cleanup_approval_contexts(run_dir: Path, prefix: str) -> None:
    if re.fullmatch(r"[a-f0-9]{32}", prefix) is None:
        raise ValueError("临时授权上下文前缀无效")
    directory = run_dir / ".provider-approvals"
    if not os.path.lexists(directory):
        return
    guard = ExecutionPathGuard(run_dir, directory)
    guard.validate_artifact(directory / f"{prefix}-request-000000000000.json")
    for path in directory.glob(f"{prefix}-request-*.json"):
        guard.validate_artifact(path)
        path.unlink()


def _approval_details(method: str, params: dict, changes: object, cwd: str) -> dict | None:
    common = {"threadId", "turnId", "itemId", "reason", "startedAtMs"}
    if any(not isinstance(params.get(key), str) or not params[key] for key in ("threadId", "turnId", "itemId")):
        return None
    if method == _COMMAND:
        allowed = common | {"approvalId", "command", "cwd", "commandActions", "kind"}
        if params.get("kind", "command") != "command":
            return None
        if any(not isinstance(params.get(key), str) or not params[key] for key in ("command", "cwd")):
            return None
        details = {"command": params["command"], "cwd": params["cwd"]}
    elif method == _FILE:
        allowed = common
        if not _complete_changes(changes):
            return None
        details = {"changes": changes, "cwd": cwd}
    else:
        return None
    # 未支持的网络、额外权限、策略增量或 grantRoot，即使带有友好标签也不能代答。
    unsupported = {"networkApprovalContext", "additionalPermissions", "environmentId",
                   "proposedExecpolicyAmendment", "proposedNetworkPolicyAmendments", "grantRoot"}
    if any(key not in allowed and (key not in unsupported or value is not None)
           for key, value in params.items()):
        return None
    details["itemId"] = params["itemId"]
    details["reason"] = params.get("reason")
    return details


def _complete_changes(changes: object) -> bool:
    if not isinstance(changes, list) or not changes:
        return False
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "kind", "diff"}:
            return False
        if any(not isinstance(change[key], str) or not change[key] for key in ("path", "diff")):
            return False
        kind = change["kind"]
        if not isinstance(kind, dict) or kind.get("type") not in {"add", "delete", "update"}:
            return False
        if set(kind) - {"type", "move_path"}:
            return False
        if kind.get("move_path") is not None and not isinstance(kind["move_path"], str):
            return False
    return True


class AppServerApprovals:
    """仅在本次 helper 存活期间关联原生请求、文件通知与临时知情上下文。"""

    def __init__(self, run_dir: Path, role: str, cwd: str, prefix: str | None) -> None:
        self.run_dir, self.role, self.cwd = run_dir, role, cwd
        self.prefix = prefix or uuid4().hex
        self.pending: dict[str, PendingInteraction] = {}
        self.file_items: dict[tuple[str, str, str], object] = {}

    def observe_item(self, params: dict) -> None:
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") != "fileChange":
            return
        key = (params.get("threadId"), params.get("turnId"), item.get("id"))
        if not all(isinstance(value, str) and value for value in key):
            return
        changes = item.get("changes")
        if len(json.dumps(changes).encode("utf-8")) > _MAX_CONTEXT_BYTES:
            changes = None
        if key in self.file_items and self.file_items[key] != changes:
            for pending in self.pending.values():
                if pending.context_ref and pending.method == _FILE:
                    payload = read_approval_context(self.run_dir, pending)
                    if (pending.thread_id, pending.turn_id, payload["itemId"]) == key:
                        self._remove_context(pending)
        self.file_items[key] = changes
        if len(self.file_items) > 32:
            self.file_items.pop(next(iter(self.file_items)))

    def record(self, message: dict, thread_id: str | None, turn_id: str | None) -> None:
        rpc_id = json.dumps(message["id"], ensure_ascii=False, separators=(",", ":"))
        method = str(message["method"])
        if method not in CODEX_SUPPORTED_SERVER_REQUESTS or rpc_id in self.pending:
            raise RuntimeError("App Server 请求类型不受支持或请求 ID 重复")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        interaction = PendingInteraction(
            interaction_id=f"request-{uuid4().hex[:12]}", role_key=self.role,
            rpc_request_id=rpc_id, method=method,
            thread_id=str(params.get("threadId") or thread_id or ""),
            turn_id=str(params["turnId"]) if "turnId" in params else turn_id,
            # 审批 reason 也可能复述原始命令；完整原文只进入临时知情展示。
            summary=summarize_provider_interaction(method, {} if method in {_COMMAND, _FILE} else params),
        )
        key = (params.get("threadId"), params.get("turnId"), params.get("itemId"))
        changes = self.file_items.get(key) if all(isinstance(value, str) for value in key) else None
        details = _approval_details(method, params, changes, self.cwd)
        if details is not None and interaction.thread_id == thread_id and interaction.turn_id == turn_id:
            self._publish_context(interaction, details)

        def mutation(state) -> None:
            state.interactions.append(interaction)
            handle = state.handles[self.role]
            handle.lifecycle, handle.last_event = "waiting_user", "waiting_user"
            handle.updated_at = utc_now()

        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        self.pending[rpc_id] = interaction

    def _publish_context(self, interaction: PendingInteraction, details: dict) -> None:
        details["binding"] = {key: getattr(interaction, key) for key in _BINDING}
        raw = json.dumps(details, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(raw) > _MAX_CONTEXT_BYTES:
            return
        reference = f"{self.prefix}-{interaction.interaction_id}.json"
        guard, path = _context_path(self.run_dir, reference)
        guard.prepare()
        guard.validate_artifact(path)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(raw)
        interaction.context_ref = reference
        interaction.context_digest = hashlib.sha256(raw).hexdigest()

    def resolve(self, params: dict) -> None:
        rpc_id = json.dumps(params.get("requestId"), ensure_ascii=False, separators=(",", ":"))
        expected = self.pending.get(rpc_id)
        if expected is None or params.get("threadId") != expected.thread_id:
            return

        def mutation(state) -> None:
            for current in state.interactions:
                if current.interaction_id == expected.interaction_id:
                    current.status, current.resolved_at = "closed", utc_now()
            handle = state.handles.get(self.role)
            if handle is not None and handle.owner == "vega" and (
                handle.thread_id == expected.thread_id and handle.last_turn_id == expected.turn_id
                and handle.lifecycle == "waiting_user"
            ):
                handle.lifecycle, handle.last_event = "active", "native_request_resolved"
                handle.updated_at = utc_now()
            self._remove_context(expected)
            self.pending.pop(rpc_id)

        mutate_provider_sessions(self.run_dir, "agent.session", mutation)

    def send_responses(self, respond: Callable[[str | int, dict], None]) -> bool:
        sent = False
        if not self.pending:
            return sent
        ids = {item.interaction_id for item in self.pending.values()}
        if not any(item.interaction_id in ids and item.status in {"responded", "closed"}
                   for item in load_provider_sessions(self.run_dir).interactions):
            return sent

        def mutation(state) -> None:
            nonlocal sent
            by_id = {item.interaction_id: item for item in state.interactions}
            for rpc_id, expected in list(self.pending.items()):
                current = by_id.get(expected.interaction_id)
                if current is None or current.status == "closed":
                    self._remove_context(expected)
                    self.pending.pop(rpc_id)
                    continue
                if current.status != "responded" or current.response is None:
                    continue
                self._require_response_binding(state.handles.get(self.role), current, expected)
                if expected.context_ref:
                    read_approval_context(self.run_dir, expected)
                respond(json.loads(rpc_id), current.response)
                self.pending.pop(rpc_id)
                current.status, current.resolved_at = "closed", utc_now()
                handle = state.handles[self.role]
                handle.lifecycle, handle.last_event = "active", "user_response_sent"
                handle.updated_at = utc_now()
                self._remove_context(expected)
                sent = True

        mutate_provider_sessions(self.run_dir, "agent.session", mutation)
        return sent

    def _require_response_binding(self, handle, current: PendingInteraction, expected: PendingInteraction) -> None:
        if any(getattr(current, key) != getattr(expected, key) for key in (*_BINDING, "context_ref", "context_digest")):
            raise ValueError("审批响应请求绑定已变化")
        if handle is None or (
            handle.provider != "codex" or handle.owner != "vega" or handle.role != self.role
            or not handle.permissions_verified or handle.lifecycle != "waiting_user"
            or handle.thread_id != current.thread_id or handle.last_turn_id != current.turn_id
        ):
            raise ValueError("审批响应不再属于当前活动 owner/Thread/Turn")

    def _remove_context(self, interaction: PendingInteraction) -> None:
        if interaction.context_ref:
            guard, path = _context_path(self.run_dir, interaction.context_ref)
            guard.validate_artifact(path)
            path.unlink(missing_ok=True)

    def close(self) -> None:
        cleanup_approval_contexts(self.run_dir, self.prefix)
