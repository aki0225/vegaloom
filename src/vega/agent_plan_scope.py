from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .agent_contract import AgentPlan, AgentWorkItem, canonical_digest
from .content_manifest import ContentManifestBudget, build_content_manifest
from .git_read import run_git_bytes
from .models import ScopeGateViolation
from .project_config import ScopeConfig
from .redaction import redact_text, write_redacted_json_once
from .scope_gate import ScopeGateResult, evaluate_scope_gate
from .scope_path_matching import matching_patterns, scope_paths_are_case_insensitive
from .tracked_workspace import capture_tracked_scope_snapshot


_HISTORICAL_PATH_BUDGET = ContentManifestBudget(
    max_content_files=1,
    max_file_bytes=8 * 1024 * 1024,
    max_content_bytes=8 * 1024 * 1024,
    max_metadata_files=1,
)


@dataclass(frozen=True)
class PlanScopeBaseline:
    """冻结当前 Work Item 以及本轮不得再次改动的历史 WIP。"""

    work_item_id: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    immutable_path_states: tuple[tuple[str, str], ...]


def capture_plan_scope_baseline(
    repo: Path,
    plan: AgentPlan,
    work_item: AgentWorkItem,
    *,
    expected_head_sha: str,
    iteration: int,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> PlanScopeBaseline:
    """允许保留既有历史 WIP，但把它冻结为当前 attempt 的只读基线。"""

    historical_allowed_paths = list(
        dict.fromkeys(
            path
            for item in plan.work_items
            for path in item.allowed_paths
        )
    )
    plan_forbidden_paths = list(
        dict.fromkeys(
            path
            for item in plan.work_items
            for path in item.forbidden_paths
        )
    )
    approved_scope = _evaluate_scope(
        repo,
        allowed_paths=historical_allowed_paths,
        forbidden_paths=plan_forbidden_paths,
        expected_head_sha=expected_head_sha,
        iteration=iteration,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )
    if approved_scope.status == "failed":
        raise ValueError(plan_scope_failure(approved_scope))

    try:
        snapshot = capture_tracked_scope_snapshot(
            repo,
            include_untracked=True,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "无法冻结 dispatch 前历史 WIP 路径："
            f"{redact_text(f'{type(exc).__name__}: {exc}')[:1000]}"
        ) from exc
    if snapshot.head_sha != expected_head_sha:
        raise ValueError("冻结历史 Work Item 路径时 Git HEAD 已漂移")
    changed_paths = tuple(
        dict.fromkeys(
            (
                *snapshot.committed_files,
                *snapshot.staged_files,
                *snapshot.unstaged_files,
                *snapshot.untracked_files,
            )
        )
    )
    immutable_paths = _historical_wip_paths(
        repo,
        plan,
        work_item,
        changed_paths,
    )
    immutable_states = _capture_scope_path_states(
        repo,
        immutable_paths,
        expected_head_sha=expected_head_sha,
    )
    return PlanScopeBaseline(
        work_item_id=work_item.work_item_id,
        allowed_paths=tuple(work_item.allowed_paths),
        forbidden_paths=tuple(plan_forbidden_paths),
        immutable_path_states=tuple(immutable_states.items()),
    )


def evaluate_plan_scope(
    repo: Path,
    baseline: PlanScopeBaseline,
    *,
    expected_head_sha: str,
    iteration: int,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> ScopeGateResult:
    """只允许当前 Work Item 产生新变化，并核对历史 WIP 未被再次改动。"""

    result = _evaluate_scope(
        repo,
        allowed_paths=baseline.allowed_paths,
        forbidden_paths=baseline.forbidden_paths,
        expected_head_sha=expected_head_sha,
        iteration=iteration,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )
    if result.failure_code or not baseline.immutable_path_states:
        return result

    try:
        current_states = _capture_scope_path_states(
            repo,
            tuple(path for path, _ in baseline.immutable_path_states),
            expected_head_sha=expected_head_sha,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return result.model_copy(
            update={
                "status": "failed",
                "failure_code": "scope_evaluation_failed",
                "diagnostic": redact_text(
                    f"历史 Work Item 路径无法重新核对：{type(exc).__name__}: {exc}"
                )[:1000],
            }
        )

    frozen_states = dict(baseline.immutable_path_states)
    violations = [
        violation
        for violation in result.violations
        if not (
            violation.code == "outside_allowed_paths"
            and frozen_states.get(violation.path) == current_states.get(violation.path)
        )
    ]
    violated_paths = {violation.path for violation in violations}
    for path, digest in baseline.immutable_path_states:
        if current_states.get(path) != digest and path not in violated_paths:
            violations.append(
                ScopeGateViolation(
                    code="outside_allowed_paths",
                    path=path,
                )
            )
    return result.model_copy(
        update={
            "status": "failed" if violations else "success",
            "violations": violations,
        }
    )


def write_plan_scope_evidence(
    run_dir: Path,
    operation_id: str,
    result: ScopeGateResult,
    *,
    stage: Literal["post-worker", "post-core"],
) -> str:
    relative = (
        f"plan-scope/{stage}-"
        f"{canonical_digest({'operation_id': operation_id, 'stage': stage})}.json"
    )
    write_redacted_json_once(run_dir / relative, result.model_dump(mode="json"))
    return relative


def plan_scope_failure(result: ScopeGateResult) -> str:
    if result.violations:
        changed = "、".join(
            f"{violation.path}（{violation.code}）"
            for violation in result.violations[:5]
        )
        suffix = " 等" if len(result.violations) > 5 else ""
        return f"批准 Plan 范围门禁未通过：{changed}{suffix}"
    detail = result.diagnostic or result.failure_code or "无法确认变更范围"
    return f"批准 Plan 范围门禁未通过：{detail}"


def _evaluate_scope(
    repo: Path,
    *,
    allowed_paths: list[str] | tuple[str, ...],
    forbidden_paths: list[str] | tuple[str, ...],
    expected_head_sha: str,
    iteration: int,
    comparison_base_sha: str | None,
    comparison_paths: tuple[str, ...],
) -> ScopeGateResult:
    return evaluate_scope_gate(
        repo,
        ScopeConfig(
            allowed_paths=list(allowed_paths),
            forbidden_paths=list(forbidden_paths),
        ),
        iteration=iteration,
        phase="pre_verification",
        expected_head_sha=expected_head_sha,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )


def _capture_scope_path_states(
    repo: Path,
    paths: tuple[str, ...],
    *,
    expected_head_sha: str,
) -> dict[str, str]:
    if not paths:
        return {}
    head_before = _read_head(repo)
    if head_before != expected_head_sha:
        raise ValueError("冻结历史 Work Item 路径时 Git HEAD 已漂移")

    states = {
        path: _capture_scope_path_state(repo, path, expected_head_sha)
        for path in sorted(dict.fromkeys(paths))
    }
    if _read_head(repo) != head_before:
        raise RuntimeError("历史 Work Item 路径采集期间 Git HEAD 发生变化")
    return states


def _historical_wip_paths(
    repo: Path,
    plan: AgentPlan,
    work_item: AgentWorkItem,
    changed_paths: tuple[str, ...],
) -> tuple[str, ...]:
    historical_patterns = [
        pattern
        for item in plan.work_items
        if item.status in {"completed", "superseded"}
        for pattern in item.allowed_paths
    ]
    if not historical_patterns:
        return ()
    try:
        case_sensitive = not scope_paths_are_case_insensitive(repo)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "无法判断历史 Work Item 路径的大小写语义："
            f"{redact_text(f'{type(exc).__name__}: {exc}')[:1000]}"
        ) from exc

    immutable: list[str] = []
    ambiguous: list[str] = []
    for path in changed_paths:
        historical_matches = matching_patterns(
            path,
            historical_patterns,
            case_sensitive=case_sensitive,
        )
        if not historical_matches:
            continue
        current_matches = matching_patterns(
            path,
            work_item.allowed_paths,
            case_sensitive=case_sensitive,
        )
        if current_matches:
            ambiguous.append(path)
        else:
            immutable.append(path)
    if ambiguous:
        raise ValueError(
            "dispatch 前 WIP 同时命中历史与当前 Work Item，无法可靠归因；"
            "请重新制定并批准 Plan："
            + "、".join(ambiguous[:5])
            + (" 等" if len(ambiguous) > 5 else "")
        )
    return tuple(immutable)


def _capture_scope_path_state(repo: Path, path: str, head_sha: str) -> str:
    status = run_git_bytes(
        repo,
        [
            "git",
            "--literal-pathspecs",
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
            "--",
            path,
        ],
    )
    index = run_git_bytes(
        repo,
        ["git", "--literal-pathspecs", "ls-files", "--stage", "-z", "--", path],
    )
    diff_prefix = [
        "git",
        "-c",
        "core.autocrlf=input",
        "--literal-pathspecs",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
    ]
    staged = run_git_bytes(repo, [*diff_prefix, "--cached", head_sha, "--", path])
    unstaged = run_git_bytes(repo, [*diff_prefix, "--", path])

    manifest_digest = ""
    if any(record.startswith(b"? ") for record in status.split(b"\0")):
        manifest = build_content_manifest(
            repo,
            [path],
            version="agent-plan-scope-v1",
            budget=_HISTORICAL_PATH_BUDGET,
        )
        if not manifest.metadata_complete or not manifest.content_complete:
            raise ValueError(
                f"历史 Work Item 路径无法完整冻结，拒绝自动派发：{path}"
            )
        manifest_digest = manifest.sha256

    payload = b"\0".join(
        [
            status,
            index,
            staged,
            unstaged,
            manifest_digest.encode("ascii"),
        ]
    )
    return hashlib.sha256(payload).hexdigest()


def _read_head(repo: Path) -> str:
    return run_git_bytes(
        repo,
        ["git", "rev-parse", "--verify", "HEAD"],
    ).decode("utf-8", errors="replace").strip()
