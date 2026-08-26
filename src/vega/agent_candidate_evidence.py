from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .agent_contract import AgentCheckpoint, AgentObservation, AgentState
from .agent_git_candidate import CandidateCommit


@dataclass(frozen=True)
class CandidateScopeExpectation:
    changed_files: list[str]
    post_worker_head: str | None
    post_core_head: str | None


def candidate_scope_expectation(
    run_dir: Path,
    state: AgentState,
    observation: AgentObservation,
) -> tuple[CandidateScopeExpectation, str | None]:
    """从 Observation 绑定的 Candidate 生成 scope 证据预期。"""

    refs = [ref for ref in observation.evidence_refs if ref.startswith("candidates/")]
    if not refs:
        return (
            CandidateScopeExpectation(
                changed_files=list(observation.changed_files),
                post_worker_head=None,
                post_core_head=None,
            ),
            None,
        )
    if len(refs) != 1:
        return _empty_expectation(), "Observation 包含多个 Candidate Artifact"
    candidate, issue = _load_bound_candidate(
        run_dir,
        state,
        observation,
        refs[0],
    )
    if issue is not None or candidate is None:
        return _empty_expectation(), issue
    return (
        CandidateScopeExpectation(
            changed_files=list(candidate.changed_files),
            post_worker_head=candidate.parent_sha,
            post_core_head=candidate.candidate_sha,
        ),
        None,
    )


def matches_accepted_candidate_transition(
    run_dir: Path,
    state: AgentState,
    checkpoint: AgentCheckpoint,
    observation: AgentObservation,
) -> bool:
    """验证上一 Work Item 的 Observation 是否正好产生当前 Accepted Checkpoint。"""

    if (
        state.run_kind != "change"
        or checkpoint.phase != "ready"
        or checkpoint.pending_actions != ["next", "replan", "human"]
        or not observation.work_item_completed
        or observation.all_work_items_completed
    ):
        return False
    candidate_scope, issue = candidate_scope_expectation(
        run_dir,
        state,
        observation,
    )
    return bool(
        issue is None
        and candidate_scope.post_core_head is not None
        and candidate_scope.post_core_head == state.accepted_checkpoint_sha
    )


def _load_bound_candidate(
    run_dir: Path,
    state: AgentState,
    observation: AgentObservation,
    ref: str,
) -> tuple[CandidateCommit | None, str | None]:
    try:
        path = _safe_ref_path(run_dir, ref)
        payload_bytes = path.read_bytes()
        candidate = CandidateCommit.model_validate_json(payload_bytes)
    except (OSError, ValidationError, ValueError):
        return None, "Candidate Artifact 缺失、损坏或 schema 不可验证"
    expected_sha256 = observation.evidence_sha256.get(ref)
    if (
        expected_sha256 is None
        or hashlib.sha256(payload_bytes).hexdigest() != expected_sha256
        or candidate.run_id != state.run_id
        or candidate.operation_id != observation.operation_id
        or candidate.work_item_id != observation.work_item_id
    ):
        return None, "Candidate Artifact 与 Observation 绑定不一致"
    return candidate, None


def _safe_ref_path(run_dir: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    if (
        candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError("Candidate Artifact 必须使用安全的 run 相对路径")
    path = (run_dir / candidate).resolve(strict=False)
    root = run_dir.resolve(strict=True)
    if not path.is_relative_to(root) or path.parent != root / "candidates":
        raise ValueError("Candidate Artifact 引用越过 Agent run 目录")
    return path


def _empty_expectation() -> CandidateScopeExpectation:
    return CandidateScopeExpectation(
        changed_files=[],
        post_worker_head=None,
        post_core_head=None,
    )
