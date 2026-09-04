from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .agent_contract import AgentState, canonical_digest
from .agent_status_projection import capture_status_workspace
from .agent_status_artifacts import (
    checkpoint_ref,
    load_bounded_decision,
    parse_checkpoint,
    read_bounded_run_artifact,
    sha256_bytes,
)
from .workspace_snapshot import ReviewWorkspaceSnapshot


_TOKEN_ARTIFACT_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class AgentSnapshotToken:
    state_sha256: str
    plan_sha256: str
    state_version: int
    provider_sha256: str | None
    provider_revision: int | None
    checkpoint_ref: str | None
    checkpoint_sha256: str | None
    decision_refs: tuple[str, ...]
    decision_sha256: tuple[str | None, ...]
    workspace_identity: tuple[str, ...]


def read_agent_snapshot_token(
    run_dir: Path,
    *,
    workspace_capture: tuple[ReviewWorkspaceSnapshot | None, str | None] | None = None,
) -> AgentSnapshotToken:
    state_path = run_dir / "agent-state.json"
    try:
        content = _read_bounded_bytes(state_path)
        plan_content = _read_bounded_bytes(run_dir / "agent-plan.json")
        envelope = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Agent State 快照无法读取。") from exc
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"kind", "data", "digest"}
        or envelope.get("kind") != "agent_state"
        or not isinstance(envelope.get("data"), dict)
        or envelope.get("digest") != canonical_digest(envelope["data"])
    ):
        raise ValueError("Agent State 快照 envelope 无法验证。")
    try:
        state = AgentState.model_validate(envelope["data"])
    except ValidationError as exc:
        raise ValueError("Agent State 快照 schema 无法验证。") from exc
    provider_content = _optional_bounded_bytes(
        run_dir / "provider-sessions.json",
        label="Provider Session",
    )
    checkpoint_ref, checkpoint_sha256, decision_refs, decision_sha256 = (
        _checkpoint_token(run_dir, state)
    )
    live_workspace, workspace_issue = (
        capture_status_workspace(run_dir)
        if workspace_capture is None
        else workspace_capture
    )
    return AgentSnapshotToken(
        state_sha256=hashlib.sha256(content).hexdigest(),
        plan_sha256=hashlib.sha256(plan_content).hexdigest(),
        state_version=state.state_version,
        provider_sha256=(
            hashlib.sha256(provider_content).hexdigest()
            if provider_content is not None
            else None
        ),
        provider_revision=_provider_revision(provider_content),
        checkpoint_ref=checkpoint_ref,
        checkpoint_sha256=checkpoint_sha256,
        decision_refs=decision_refs,
        decision_sha256=decision_sha256,
        workspace_identity=_workspace_identity(
            live_workspace,
            workspace_issue,
        ),
    )


def _checkpoint_token(
    run_dir: Path,
    state: AgentState,
) -> tuple[str | None, str | None, tuple[str, ...], tuple[str | None, ...]]:
    if state.latest_checkpoint_id is None:
        return None, None, (), ()
    try:
        ref = checkpoint_ref(state.latest_checkpoint_id)
        checkpoint_content = read_bounded_run_artifact(
            run_dir,
            ref,
            directory="checkpoints",
        )
        checkpoint = parse_checkpoint(checkpoint_content)
    except ValueError:
        return (
            None,
            None,
            (),
            (),
        )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.checkpoint_id != state.latest_checkpoint_id
        or checkpoint.current_work_item != state.current_work_item
    ):
        return ref, sha256_bytes(checkpoint_content), (), ()
    decision_refs = tuple(
        item
        for item in checkpoint.evidence_refs
        if item.startswith("decisions/")
    )
    decision_hashes = tuple(
        _decision_ref_sha256(run_dir, ref)
        for ref in decision_refs
    )
    return (
        ref,
        sha256_bytes(checkpoint_content),
        decision_refs,
        decision_hashes,
    )


def _decision_ref_sha256(run_dir: Path, ref: str) -> str | None:
    try:
        _, content = load_bounded_decision(run_dir, ref)
    except ValueError:
        return None
    return sha256_bytes(content)


def _optional_bounded_bytes(path: Path, *, label: str) -> bytes | None:
    if not path.exists():
        return None
    try:
        return _read_bounded_bytes(path)
    except OSError as exc:
        raise ValueError(f"{label} 快照无法读取。") from exc


def _read_bounded_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(_TOKEN_ARTIFACT_LIMIT + 1)
    if len(content) > _TOKEN_ARTIFACT_LIMIT:
        raise OSError("快照 Artifact 超出读取上限")
    return content


def _provider_revision(content: bytes | None) -> int | None:
    if content is None:
        return None
    try:
        envelope = json.loads(content.decode("utf-8"))
        data = envelope.get("data") if isinstance(envelope, dict) else None
        revision = data.get("revision") if isinstance(data, dict) else None
        return revision if isinstance(revision, int) else None
    except (UnicodeError, json.JSONDecodeError):
        return None


def _workspace_identity(
    snapshot: ReviewWorkspaceSnapshot | None,
    issue: str | None,
) -> tuple[str, ...]:
    if snapshot is None:
        return ("unavailable", issue or "unknown")
    return (
        snapshot.fingerprint,
        snapshot.head_sha,
        snapshot.status_sha256,
        snapshot.staged_diff_sha256,
        snapshot.unstaged_diff_sha256,
        snapshot.untracked_manifest_sha256,
        snapshot.ignored_manifest_sha256,
        snapshot.index_flags_sha256,
        snapshot.git_control_sha256,
        snapshot.comparison_base_sha or "",
        snapshot.committed_diff_sha256,
    )
