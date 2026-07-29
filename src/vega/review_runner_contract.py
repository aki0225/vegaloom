from __future__ import annotations

from .runner import RunnerResult


def review_result_is_trusted(result: RunnerResult) -> bool:
    """只有无错误完成的 Reviewer Runner 输出才能进入正式结论。"""

    return (
        result.status == "success"
        and result.error is None
        and result.termination_unconfirmed is False
    )


def review_result_diagnostic(result: RunnerResult) -> str:
    details = [f"status={result.status}"]
    if result.error is not None:
        details.append("error_present=true")
    if result.termination_unconfirmed is not False:
        details.append("termination_unconfirmed_valid=false")
    return ", ".join(details)


def untrusted_review_current_step(result: RunnerResult) -> str:
    if result.status in {"success", "error"}:
        return "runner_error"
    return result.status
