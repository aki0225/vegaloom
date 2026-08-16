from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .redaction import redact_text
from .risk_gate_evidence import collect_gate_diff_check
from .runtime_workspace import capture_runtime_workspace
from .tracked_workspace import collect_committed_diff, normalize_comparison_paths
from .workspace_check import collect_tracked_diff_parts, render_tracked_diff_sections


def safe_comparison_base(value: object) -> str | None:
    if (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(character in "0123456789abcdef" for character in value)
    ):
        return value
    return None


def safe_comparison_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        return ()
    try:
        return normalize_comparison_paths(value)
    except ValueError:
        return ()


def comparison_binding_from_mapping(
    payload: dict[str, Any],
    *,
    base_key: str = "comparison_base_sha",
    paths_key: str = "comparison_paths",
) -> tuple[str | None, tuple[str, ...], list[str]]:
    raw_base = payload.get(base_key)
    base_sha = safe_comparison_base(raw_base)
    issues = (
        ["comparison_base_invalid"]
        if raw_base is not None and base_sha is None
        else []
    )
    raw_paths = payload.get(paths_key, [])
    paths = safe_comparison_paths(raw_paths)
    if not isinstance(raw_paths, list) or not all(
        isinstance(path, str) for path in raw_paths
    ):
        issues.append("comparison_paths_invalid")
    elif raw_paths and not paths:
        issues.append("comparison_paths_invalid")
    if paths and base_sha is None:
        issues.append("comparison_paths_without_base")
    return base_sha, paths, issues


def require_comparison_binding_from_mapping(
    payload: dict[str, Any],
    *,
    base_key: str = "comparison_base_sha",
    paths_key: str = "comparison_paths",
) -> tuple[str | None, tuple[str, ...]]:
    base_sha, paths, issues = comparison_binding_from_mapping(
        payload,
        base_key=base_key,
        paths_key=paths_key,
    )
    if issues:
        raise ValueError("comparison binding 无效：" + ", ".join(issues))
    return base_sha, paths


def comparison_state_issues(
    state: dict[str, Any],
    comparison_base_sha: str | None,
    comparison_paths: tuple[str, ...],
) -> list[str]:
    issues: list[str] = []
    if state.get("comparison_base_sha") != comparison_base_sha:
        issues.append("comparison_base_state_mismatch")
    if state.get("comparison_paths", []) != list(comparison_paths):
        issues.append("comparison_paths_state_mismatch")
    return issues


def scope_gate_comparison_issues(
    actual_base_sha: str | None,
    actual_paths: list[str],
    expected_base_sha: str | None,
    expected_paths: tuple[str, ...],
) -> list[str]:
    issues: list[str] = []
    if actual_base_sha != expected_base_sha:
        issues.append("scope_gate_comparison_base_mismatch")
    if actual_paths != list(expected_paths):
        issues.append("scope_gate_comparison_paths_mismatch")
    return issues


def capture_workspace_fingerprint(
    workspace: Path,
    repo_path: Path,
    *,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
    capture_workspace: Callable[..., Any] = capture_runtime_workspace,
) -> tuple[str | None, str | None]:
    if comparison_base_sha is None and comparison_paths:
        return None, "ValueError"
    try:
        if comparison_base_sha is None and not comparison_paths:
            snapshot = capture_workspace(workspace, repo_path)
        else:
            snapshot = capture_workspace(
                workspace,
                repo_path,
                comparison_base_sha=comparison_base_sha,
                comparison_paths=comparison_paths,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return None, type(exc).__name__
    return snapshot.fingerprint, None


def collect_git_reflection(
    repo_path: Path,
    *,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> dict[str, str]:
    staged, unstaged = collect_tracked_diff_parts(repo_path, ["--check"])
    committed = collect_committed_diff(
        repo_path,
        comparison_base_sha,
        ["--check"],
        comparison_paths=comparison_paths,
    )
    return {
        "check": redact_text(
            render_tracked_diff_sections(
                staged,
                unstaged,
                committed_diff=committed,
                comparison_base_sha=comparison_base_sha,
            )
        )
    }


def collect_gate_comparison_evidence(
    repo_path: Path,
    reflect_state: dict[str, Any],
) -> tuple[str | None, tuple[str, ...], str, str, str]:
    comparison_base_sha, comparison_paths = require_comparison_binding_from_mapping(
        reflect_state,
    )
    diff_check = collect_gate_diff_check(
        repo_path,
        comparison_base_sha=comparison_base_sha,
        comparison_paths=comparison_paths,
    )
    staged_name_status, unstaged_name_status = collect_tracked_diff_parts(
        repo_path,
        ["--name-status"],
    )
    staged_numstat, unstaged_numstat = collect_tracked_diff_parts(
        repo_path,
        ["--numstat"],
    )
    committed_name_status = collect_committed_diff(
        repo_path,
        comparison_base_sha,
        ["--name-status"],
        comparison_paths=comparison_paths,
    )
    committed_numstat = collect_committed_diff(
        repo_path,
        comparison_base_sha,
        ["--numstat"],
        comparison_paths=comparison_paths,
    )
    name_status = render_tracked_diff_sections(
        staged_name_status,
        unstaged_name_status,
        committed_diff=committed_name_status,
        comparison_base_sha=comparison_base_sha,
    )
    numstat = render_tracked_diff_sections(
        staged_numstat,
        unstaged_numstat,
        committed_diff=committed_numstat,
        comparison_base_sha=comparison_base_sha,
    )
    return comparison_base_sha, comparison_paths, diff_check, name_status, numstat
