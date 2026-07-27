from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import GateResult, LoopAutomationState, ReviewVerdict


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
