from __future__ import annotations

import json
import os
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from .agent_contract import AgentCheckpoint, AgentState, canonical_digest
from .redaction import redact_value
from .trace import TraceWriter, read_trace_items


class AgentArtifactError(ValueError):
    pass


ModelT = TypeVar("ModelT", bound=BaseModel)


def save_agent_state(path: Path, state: AgentState) -> None:
    _save_envelope(path, "agent_state", state)


def load_agent_state(path: Path) -> AgentState:
    return _load_envelope(path, "agent_state", AgentState)


def save_agent_checkpoint(path: Path, checkpoint: AgentCheckpoint) -> None:
    _save_envelope(path, "agent_checkpoint", checkpoint)


def load_agent_checkpoint(path: Path) -> AgentCheckpoint:
    return _load_envelope(path, "agent_checkpoint", AgentCheckpoint)


def append_agent_trace(
    trace_path: Path,
    *,
    event: str,
    state: AgentState,
    observation_summary: str | None = None,
    route_reason: str | None = None,
    artifact_refs: list[str] | None = None,
) -> None:
    TraceWriter(trace_path).write(
        event,
        run_id=state.run_id,
        task_id=state.task_id,
        phase=state.phase,
        state_version=state.state_version,
        work_item=state.current_work_item,
        child_run=state.active_child_run,
        operation_id=state.active_operation_id,
        workspace_fingerprint=state.workspace_fingerprint,
        observation_summary=observation_summary,
        route_reason=route_reason,
        artifact_refs=artifact_refs or [],
    )


def read_agent_trace(trace_path: Path) -> list[dict[str, object]]:
    return read_trace_items(trace_path)


def append_agent_trace_commit(
    trace_path: Path,
    *,
    event: str,
    state: AgentState,
    observation_summary: str | None = None,
    route_reason: str | None = None,
    artifact_refs: list[str] | None = None,
    writer: Callable[..., None] = append_agent_trace,
) -> None:
    refs = artifact_refs or []
    try:
        writer(
            trace_path,
            event=event,
            state=state,
            observation_summary=observation_summary,
            route_reason=route_reason,
            artifact_refs=refs,
        )
    except Exception:
        if not _trace_commit_exists(
            trace_path,
            event=event,
            state=state,
            artifact_refs=refs,
        ):
            raise


def read_optional_artifact(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def restore_optional_artifact(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.write_bytes(content)


def artifact_names(directory: Path, pattern: str) -> set[str]:
    return {path.name for path in directory.glob(pattern)}


def remove_new_artifacts(
    directory: Path,
    pattern: str,
    previous_names: set[str],
) -> None:
    for path in directory.glob(pattern):
        if path.name not in previous_names:
            path.unlink(missing_ok=True)


def remove_artifact_if_published(path: Path, published: bool) -> None:
    if published:
        path.unlink(missing_ok=True)


def _trace_commit_exists(
    trace_path: Path,
    *,
    event: str,
    state: AgentState,
    artifact_refs: list[str],
) -> bool:
    try:
        item: dict[str, Any] = read_trace_items(trace_path)[-1]
    except (IndexError, OSError, ValueError):
        return False
    return (
        item.get("event") == event
        and item.get("run_id") == state.run_id
        and item.get("state_version") == state.state_version
        and item.get("artifact_refs") == artifact_refs
    )


def _save_envelope(path: Path, kind: str, model: BaseModel) -> None:
    data = redact_value(model.model_dump(mode="json"))
    envelope = {
        "kind": kind,
        "data": data,
        "digest": canonical_digest(data),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".agent-{uuid4().hex[:16]}")
    temp_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    last_error: OSError | None = None
    for _ in range(10):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.02)
    if temp_path.exists():
        temp_path.unlink()
    assert last_error is not None
    raise last_error


def _load_envelope(
    path: Path,
    expected_kind: str,
    model_type: type[ModelT],
) -> ModelT:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AgentArtifactError(f"Agent Artifact 不存在：{path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentArtifactError(f"Agent Artifact 无法解析：{path.name}") from exc
    if not isinstance(raw, dict):
        raise AgentArtifactError(f"Agent Artifact 必须是 JSON object：{path.name}")
    if set(raw) != {"kind", "data", "digest"}:
        raise AgentArtifactError(f"Agent Artifact envelope 字段不完整：{path.name}")
    if raw["kind"] != expected_kind or not isinstance(raw["data"], dict):
        raise AgentArtifactError(f"Agent Artifact 类型不匹配：{path.name}")
    if raw["digest"] != canonical_digest(raw["data"]):
        raise AgentArtifactError(f"Agent Artifact digest 不一致：{path.name}")
    try:
        return model_type.model_validate(raw["data"])
    except ValidationError as exc:
        raise AgentArtifactError(f"Agent Artifact schema 校验失败：{path.name}") from exc
