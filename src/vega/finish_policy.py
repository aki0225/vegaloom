from __future__ import annotations

from typing import Literal


FinishStatus = Literal["ready_to_commit", "needs_fix", "needs_human", "incomplete"]


def decide_finish_status(
    loop_status: str,
    latest_verdict: str | None,
    latest_verification_failed: bool = False,
    *,
    verification_passed: bool = False,
    evidence_fresh: bool = True,
    artifact_integrity_valid: bool = True,
) -> FinishStatus:
    if not artifact_integrity_valid:
        return "needs_human"
    if not evidence_fresh:
        return "needs_human"
    if latest_verification_failed:
        return "needs_fix"
    if not verification_passed:
        return "needs_human"
    if loop_status == "success" and latest_verdict == "approve":
        return "ready_to_commit"
    if latest_verdict == "request_changes":
        return "needs_fix"
    if loop_status in {"failed", "needs_human"}:
        return "needs_human"
    return "incomplete"
