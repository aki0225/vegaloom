from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentObservation,
    canonical_digest,
)


_ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_STATUS_ARTIFACT_LIMIT = 4 * 1024 * 1024


def checkpoint_ref(checkpoint_id: str) -> str:
    if _ARTIFACT_ID_PATTERN.fullmatch(checkpoint_id) is None:
        raise ValueError("Checkpoint ID 不是 canonical Artifact ID")
    return f"checkpoints/{checkpoint_id}.json"


def load_bounded_checkpoint(
    run_dir: Path,
    checkpoint_id: str,
) -> tuple[AgentCheckpoint, bytes]:
    ref = checkpoint_ref(checkpoint_id)
    content = read_bounded_run_artifact(
        run_dir,
        ref,
        directory="checkpoints",
    )
    return parse_checkpoint(content), content


def parse_checkpoint(content: bytes) -> AgentCheckpoint:
    try:
        envelope = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Checkpoint 无法解析") from exc
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"kind", "data", "digest"}
        or envelope.get("kind") != "agent_checkpoint"
        or not isinstance(envelope.get("data"), dict)
        or envelope.get("digest") != canonical_digest(envelope["data"])
    ):
        raise ValueError("Checkpoint envelope 无法验证")
    try:
        return AgentCheckpoint.model_validate(envelope["data"])
    except ValidationError as exc:
        raise ValueError("Checkpoint schema 无法验证") from exc


def load_bounded_decision(
    run_dir: Path,
    ref: str,
) -> tuple[AgentDecision, bytes]:
    content = read_bounded_run_artifact(
        run_dir,
        ref,
        directory="decisions",
    )
    try:
        return AgentDecision.model_validate_json(content), content
    except (UnicodeError, ValidationError) as exc:
        raise ValueError("Decision 无法验证") from exc


def load_bounded_observation(
    run_dir: Path,
    ref: str,
) -> tuple[AgentObservation, bytes]:
    content = read_bounded_run_artifact(
        run_dir,
        ref,
        directory="observations",
    )
    try:
        return AgentObservation.model_validate_json(content), content
    except (UnicodeError, ValidationError) as exc:
        raise ValueError("Observation 无法验证") from exc


def read_bounded_run_artifact(
    run_dir: Path,
    ref: str,
    *,
    directory: str,
) -> bytes:
    path = resolve_canonical_run_artifact(
        run_dir,
        ref,
        directory=directory,
    )
    with path.open("rb") as stream:
        content = stream.read(_STATUS_ARTIFACT_LIMIT + 1)
    if len(content) > _STATUS_ARTIFACT_LIMIT:
        raise ValueError("状态 Artifact 超出读取上限")
    return content


def resolve_canonical_run_artifact(
    run_dir: Path,
    ref: str,
    *,
    directory: str,
) -> Path:
    candidate = PurePosixPath(ref)
    if (
        candidate.is_absolute()
        or len(candidate.parts) != 2
        or candidate.parts[0] != directory
        or candidate.parts[1] in {"", ".", ".."}
        or candidate.as_posix() != ref
    ):
        raise ValueError(f"{directory} 引用不是 canonical 相对路径")
    try:
        root = run_dir.resolve(strict=True)
        allowed_root = (root / directory).resolve(strict=True)
        path = (root / candidate).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{directory} Artifact 不存在或无法读取") from exc
    if (
        not allowed_root.is_relative_to(root)
        or allowed_root.parent != root
        or path.parent != allowed_root
        or not path.is_relative_to(allowed_root)
    ):
        raise ValueError(f"{directory} 引用越过允许目录")
    return path


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
