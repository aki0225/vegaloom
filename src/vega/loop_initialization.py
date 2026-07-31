from __future__ import annotations

from pathlib import Path

from .loop_integrity import (
    brief_initialization_binding_issues,
    expected_initialization_artifacts,
    initialization_artifact_issues,
    initialization_trace_issues,
    load_brief_initialization_evidence,
    read_initialization_trace,
    worker_prompt_metric_issues,
    workspace_baseline_initialization_issues,
)
from .models import LoopAutomationState
from .risk_gate_evidence import project_policy_snapshot_eval_results
from .workspace_baseline import is_legacy_assist_initialization_unavailable


def loop_initialization_issues(
    workspace: Path,
    run_dir: Path,
    state: LoopAutomationState,
    repo_path: Path,
) -> list[str]:
    """返回 loop 初始化证据问题，供 recovery、continue 与 status 共用。"""

    evidence, issues = load_brief_initialization_evidence(workspace, state)
    if evidence is None:
        return issues
    issues.extend(
        brief_initialization_binding_issues(
            evidence,
            run_dir,
            state,
            repo_path,
        )
    )
    trace_items, trace_read_issues = read_initialization_trace(run_dir)
    legacy_assist_initialization = (
        is_legacy_assist_initialization_unavailable(
            run_dir,
            state,
            trace_items,
        )
    )
    expected_artifacts = expected_initialization_artifacts(
        state,
        legacy_assist_initialization,
    )
    issues.extend(initialization_artifact_issues(run_dir, expected_artifacts))
    issues.extend(worker_prompt_metric_issues(run_dir))
    if any(
        result.startswith("FAIL:")
        for result in project_policy_snapshot_eval_results(run_dir, state)
    ):
        issues.append("project_policy_snapshot_invalid")
    issues.extend(
        workspace_baseline_initialization_issues(
            run_dir,
            state,
            legacy_assist_initialization,
        )
    )
    if trace_items is None:
        issues.extend(trace_read_issues)
    else:
        issues.extend(
            initialization_trace_issues(
                trace_items,
                state,
                expected_artifacts,
                legacy_assist_initialization=legacy_assist_initialization,
            )
        )
    return list(dict.fromkeys(issues))
