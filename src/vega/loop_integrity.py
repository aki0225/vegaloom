from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import BriefState, GateResult, LoopAutomationState, ReviewVerdict
from .prompt_metrics import PromptMetrics
from .run_utils import resolve_run_dir
from .trace import read_trace_items
from .workspace_baseline import (
    ASSIST_INITIALIZATION_ARTIFACTS,
    LEGACY_ASSIST_INITIALIZATION_ARTIFACTS,
    LEGACY_WORKSPACE_BASELINE_UNAVAILABLE,
    WORKSPACE_BASELINE_ARTIFACT,
    append_workspace_baseline_trace_issues,
    read_workspace_baseline,
)


@dataclass(frozen=True)
class LoopArtifactIntegrity:
    valid: bool
    issues: tuple[str, ...]
    review_verdicts: tuple[ReviewVerdict, ...] = ()
    verification_results: tuple[dict[str, Any], ...] = ()
    risk_gate_results: tuple[GateResult, ...] = ()
    reviewed_workspace_fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": list(self.issues),
            "review_verdict_count": len(self.review_verdicts),
            "verification_result_count": len(self.verification_results),
            "risk_gate_result_count": len(self.risk_gate_results),
            "reviewed_workspace_fingerprint": self.reviewed_workspace_fingerprint,
        }


@dataclass(frozen=True)
class BriefInitializationEvidence:
    run_dir: Path
    state: BriefState


def load_brief_initialization_evidence(
    workspace: Path,
    state: LoopAutomationState,
) -> tuple[BriefInitializationEvidence | None, list[str]]:
    if not state.brief_run:
        return None, ["brief_run_missing"]
    try:
        brief_dir = resolve_run_dir(workspace, state.brief_run)
    except (FileNotFoundError, ValueError):
        return None, ["brief_run_unresolvable"]
    try:
        brief_state = BriefState.model_validate_json(
            brief_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError):
        return None, ["brief_state_invalid"]
    return BriefInitializationEvidence(brief_dir, brief_state), []


def brief_initialization_binding_issues(
    evidence: BriefInitializationEvidence,
    loop_run_dir: Path,
    loop_state: LoopAutomationState,
    repo_path: Path,
) -> list[str]:
    issues: list[str] = []
    if evidence.state.run_id != evidence.run_dir.name:
        issues.append("brief_run_id_mismatch")
    try:
        if Path(evidence.state.repo_path).resolve() != repo_path.resolve():
            issues.append("brief_repo_mismatch")
    except OSError:
        issues.append("brief_repo_unresolvable")
    if evidence.state.mode != loop_state.task_mode:
        issues.append("brief_task_mode_mismatch")
    if evidence.state.status != "success":
        issues.append("brief_status_not_success")
    for name in ("agent-brief.md", "project-context.md"):
        issues.extend(
            _copied_brief_artifact_issues(
                evidence.run_dir / name,
                loop_run_dir / name,
                name,
            )
        )
    return issues


def initialization_artifact_issues(
    run_dir: Path,
    expected_artifacts: list[str],
) -> list[str]:
    issues: list[str] = []
    for name in expected_artifacts:
        path = run_dir / name
        try:
            if not path.is_file() or not path.read_bytes():
                issues.append(f"{name}_missing_or_empty")
        except OSError:
            issues.append(f"{name}_missing_or_unreadable")
    return issues


def worker_prompt_metric_issues(run_dir: Path) -> list[str]:
    try:
        prompt = run_dir.joinpath("worker-prompt.md").read_text(encoding="utf-8")
        metrics = PromptMetrics.model_validate_json(
            run_dir.joinpath("worker-prompt-metrics.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError, ValidationError):
        return ["worker_prompt_metrics_invalid"]
    if (
        metrics.role != "worker"
        or metrics.chars != len(prompt)
        or metrics.utf8_bytes != len(prompt.encode("utf-8"))
        or metrics.lines != len(prompt.splitlines())
    ):
        return ["worker_prompt_metrics_mismatch"]
    return []


def read_initialization_trace(
    run_dir: Path,
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    try:
        return read_trace_items(run_dir / "trace.jsonl"), []
    except (OSError, ValueError):
        return None, ["initialization_trace_invalid"]


def initialization_trace_issues(
    trace_items: list[dict[str, Any]] | None,
    state: LoopAutomationState,
    expected_artifacts: list[str],
    *,
    legacy_assist_initialization: bool,
) -> list[str]:
    if trace_items is None:
        return ["initialization_trace_invalid"]
    issues: list[str] = []
    initialized_events = [
        item for item in trace_items if item.get("event") == "loop_initialized"
    ]
    if initialized_events:
        issues.extend(
            _initialized_event_issues(
                initialized_events,
                state,
                expected_artifacts,
            )
        )
    elif state.automation_mode == "assist":
        issues.append("loop_initialization_marker_missing")
    else:
        legacy_markers = [
            item
            for item in trace_items
            if item.get("event") == "worker_prompt_measured"
            and "iteration" not in item
        ]
        if len(legacy_markers) != 1:
            issues.append("loop_initialization_marker_missing")
    append_workspace_baseline_trace_issues(
        trace_items,
        state,
        issues,
        legacy_assist_initialization=legacy_assist_initialization,
    )
    return issues


def expected_initialization_artifacts(
    state: LoopAutomationState,
    legacy_assist_initialization: bool = False,
) -> list[str]:
    if state.automation_mode == "assist" and not legacy_assist_initialization:
        return list(ASSIST_INITIALIZATION_ARTIFACTS)
    return list(LEGACY_ASSIST_INITIALIZATION_ARTIFACTS)


def workspace_baseline_binding_issues(
    run_dir: Path,
    state: LoopAutomationState,
    *,
    require_usable: bool,
    require_initial_head_match: bool,
) -> list[str]:
    if state.automation_mode != "assist":
        return []
    if state.workspace_baseline_artifact_version != 1:
        return ["workspace_baseline_version_missing_or_unsupported"]
    if not state.workspace_baseline_sha256:
        return ["workspace_baseline_binding_missing"]
    try:
        baseline = read_workspace_baseline(
            run_dir / WORKSPACE_BASELINE_ARTIFACT,
            expected_sha256=state.workspace_baseline_sha256,
        )
    except ValueError:
        return ["workspace_baseline_invalid"]
    issues: list[str] = []
    if require_initial_head_match and (
        not state.initial_head_sha or baseline.head_sha != state.initial_head_sha
    ):
        issues.append("workspace_baseline_head_mismatch")
    if require_usable and not baseline.capture_complete:
        issues.append("workspace_baseline_capture_incomplete")
    if require_usable and baseline.has_tracked_changes:
        issues.append("workspace_baseline_tracked_changes_present")
    return issues


def workspace_baseline_initialization_issues(
    run_dir: Path,
    state: LoopAutomationState,
    legacy_assist_initialization: bool,
) -> list[str]:
    if legacy_assist_initialization:
        return [LEGACY_WORKSPACE_BASELINE_UNAVAILABLE]
    return workspace_baseline_binding_issues(
        run_dir,
        state,
        require_usable=True,
        require_initial_head_match=True,
    )


def workspace_baseline_eval_results(
    run_dir: Path,
    state: LoopAutomationState,
) -> list[str]:
    if state.automation_mode != "assist":
        return []
    issues = workspace_baseline_binding_issues(
        run_dir,
        state,
        require_usable=False,
        require_initial_head_match=False,
    )
    if issues:
        return ["FAIL: workspace baseline 未与根状态可靠绑定：" + ", ".join(issues)]
    return ["PASS: workspace baseline artifact 与根状态哈希绑定"]


def trusted_verification_passed(
    state: LoopAutomationState,
    artifact_integrity: LoopArtifactIntegrity,
) -> bool:
    """只接受最新轮次中绑定当前 schema 的完整结构化通过证据。"""

    if not artifact_integrity.valid or not state.iterations:
        return False
    latest = state.iterations[-1]
    if latest.lifecycle != "completed" or latest.verification_status != "passed":
        return False
    if latest.verification_failed_count != 0:
        return False
    if latest.verification_failure_kind is not None:
        return False

    for payload in artifact_integrity.verification_results:
        if payload.get("iteration") != latest.iteration:
            continue
        if not _uses_current_verification_artifact(state, payload):
            return False
        command_count = payload.get("command_count")
        failed_count = payload.get("failed_count")
        selected_command_count = payload.get("selected_command_count")
        skipped_commands = payload.get("skipped_commands")
        workspace_fingerprint = payload.get("workspace_fingerprint")
        commands = payload.get("commands")
        results = payload.get("results")
        return (
            payload.get("failure_kind") is None
            and _is_positive_int(command_count)
            and failed_count == 0
            and selected_command_count == command_count
            and skipped_commands == []
            and payload.get("interruption_status") is None
            and payload.get("interruption_command") is None
            and payload.get("interruption_reason") is None
            and _is_sha256(workspace_fingerprint)
            and workspace_fingerprint
            == artifact_integrity.reviewed_workspace_fingerprint
            and isinstance(commands, list)
            and len(commands) == command_count
            and isinstance(results, list)
            and len(results) == command_count
            and all(
                isinstance(item, dict)
                and item.get("status") == "passed"
                and item.get("interruption_status") is None
                and item.get("interruption_reason") is None
                for item in results
            )
        )
    return False


def latest_verification_failed(
    state: LoopAutomationState,
    artifact_integrity: LoopArtifactIntegrity,
) -> bool:
    """只根据最新轮次的受信结构化结果判断验证失败。"""

    if not artifact_integrity.valid or not state.iterations:
        return False
    latest_iteration = state.iterations[-1].iteration
    for payload in reversed(artifact_integrity.verification_results):
        if payload.get("iteration") == latest_iteration:
            if not _uses_current_verification_artifact(state, payload):
                return False
            if payload.get("failure_kind") is not None:
                return False
            return _is_positive_int(payload.get("failed_count"))
    return False


def validate_verification_failure_kind_schema(
    artifact_version: object,
    failure_kind: object,
    expected_failure_kind: str | None,
    prefix: str,
    issues: list[str],
) -> None:
    if artifact_version == 2 and failure_kind not in {
        None,
        "project_config_invalid",
        "workspace_capture_failed",
    }:
        issues.append(f"{prefix}_verification_failure_kind_invalid")
    if failure_kind != expected_failure_kind:
        issues.append(f"{prefix}_verification_failure_kind_mismatch")


def validate_project_config_failure_payload(
    payload: dict[str, Any],
    failure_kind: object,
    commands: object,
    command_results: object,
    command_count: object,
    failed_count: object,
    selected_command_count: object,
    skipped_commands: object,
    prefix: str,
    issues: list[str],
) -> None:
    if failure_kind != "project_config_invalid":
        return
    config_check = payload.get("config_check")
    if not isinstance(config_check, dict) or config_check.get("status") != "failed":
        issues.append(f"{prefix}_verification_config_check_invalid")
    if commands != [] or command_results != []:
        issues.append(f"{prefix}_verification_config_failure_has_commands")
    if command_count != 0 or failed_count != 0 or selected_command_count != 0:
        issues.append(f"{prefix}_verification_config_failure_count_invalid")
    if skipped_commands != []:
        issues.append(f"{prefix}_verification_config_failure_skipped_commands")
    if any(
        payload.get(field) is not None
        for field in (
            "interruption_status",
            "interruption_command",
            "interruption_reason",
        )
    ):
        issues.append(f"{prefix}_verification_config_failure_interrupted")


def validated_review_workspace_fingerprint(
    child_context: dict[str, Any] | None,
    verdict: str,
    issues: list[str],
    prefix: str,
) -> str:
    if child_context is None or verdict != "approve":
        return ""
    review_fingerprints = [
        child_context.get("source_workspace_fingerprint"),
        child_context.get("current_workspace_fingerprint"),
        child_context.get("reviewer_start_workspace_fingerprint"),
        child_context.get("reviewer_end_workspace_fingerprint"),
    ]
    if not all(_is_sha256(item) for item in review_fingerprints) or len(
        set(review_fingerprints)
    ) != 1:
        issues.append(f"{prefix}_child_review_workspace_fingerprint_invalid")
        return ""
    return str(review_fingerprints[0])


def validate_verification_workspace_fingerprint(
    payload: dict[str, Any],
    prefix: str,
    issues: list[str],
) -> None:
    if payload.get("failure_kind") == "workspace_capture_failed":
        if payload.get("workspace_fingerprint") is not None:
            issues.append(f"{prefix}_verification_workspace_fingerprint_unexpected")
        error_type = payload.get("workspace_capture_error_type")
        if not isinstance(error_type, str) or not error_type:
            issues.append(f"{prefix}_verification_workspace_capture_error_invalid")
        if payload.get("selected_command_count") != payload.get("command_count"):
            issues.append(f"{prefix}_verification_workspace_capture_commands_incomplete")
        if payload.get("skipped_commands") != []:
            issues.append(f"{prefix}_verification_workspace_capture_skipped_commands")
        if any(
            payload.get(field) is not None
            for field in (
                "interruption_status",
                "interruption_command",
                "interruption_reason",
            )
        ):
            issues.append(f"{prefix}_verification_workspace_capture_interrupted")
        return
    if not _is_sha256(payload.get("workspace_fingerprint")):
        issues.append(f"{prefix}_verification_workspace_fingerprint_invalid")
    if payload.get("workspace_capture_error_type") is not None:
        issues.append(f"{prefix}_verification_workspace_capture_error_unexpected")


def _copied_brief_artifact_issues(
    source: Path,
    target: Path,
    name: str,
) -> list[str]:
    try:
        source_bytes = source.read_bytes()
        target_bytes = target.read_bytes()
    except OSError:
        return [f"{name}_missing_or_unreadable"]
    if source_bytes != target_bytes:
        return [f"{name}_source_mismatch"]
    return []


def _initialized_event_issues(
    events: list[dict[str, Any]],
    state: LoopAutomationState,
    expected_artifacts: list[str],
) -> list[str]:
    if len(events) != 1:
        return ["loop_initialized_event_count_invalid"]
    event = events[0]
    issues: list[str] = []
    if event.get("brief_run") != state.brief_run:
        issues.append("loop_initialized_brief_mismatch")
    if event.get("artifacts") != expected_artifacts:
        issues.append("loop_initialized_artifacts_mismatch")
    return issues


def _uses_current_verification_artifact(
    state: LoopAutomationState,
    payload: dict[str, Any],
) -> bool:
    return (
        type(state.verification_artifact_version) is int
        and state.verification_artifact_version == 2
        and type(payload.get("artifact_version")) is int
        and payload["artifact_version"] == 2
    )


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )
