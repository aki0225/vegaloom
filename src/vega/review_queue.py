from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .execution_control import RunnerExecutionContext
from .models import RequiredReviewHit
from .prompt_metrics import measure_prompt
from .redaction import redact_text, redact_value
from .review_contract import (
    ReviewFinding,
    ReviewRiskDisclosure,
    ReviewVerdict,
)
from .review_coverage import build_review_file_coverage
from .review_queue_contract import (
    MAX_REVIEW_QUEUE_ITEMS,
    ReviewQueue,
    ReviewQueueItem,
    render_redacted_queue_verdict,
)
from .review_queue_plan import PreparedReviewTask, prepare_review_queue
from .runner import Runner, RunnerResult

RunnerFactory = Callable[[list[RequiredReviewHit]], Runner]
PromptRenderer = Callable[[dict[str, Any]], str]
VerdictParser = Callable[[RunnerResult], ReviewVerdict]
TrustedResult = Callable[[RunnerResult], bool]
ProgressReporter = Callable[[str, int], None]
TraceReporter = Callable[..., None]

@dataclass(frozen=True)
class ReviewQueueOutcome:
    queue: ReviewQueue
    result: RunnerResult
    verdict: ReviewVerdict
    reviewer_started: bool


def run_review_queue(
    run_dir: Path,
    repo_path: Path,
    inputs: dict[str, Any],
    *,
    runner_factory: RunnerFactory,
    render_prompt: PromptRenderer,
    parse_verdict: VerdictParser,
    trusted_result: TrustedResult,
    execution_context: RunnerExecutionContext,
    timeout_seconds: int,
    max_prompt_chars: int,
    max_diff_chars: int,
    progress_reporter: ProgressReporter | None = None,
    trace_reporter: TraceReporter | None = None,
    max_items: int = MAX_REVIEW_QUEUE_ITEMS,
) -> ReviewQueueOutcome:
    """按文件集合顺序运行独立 Reviewer，并持续保存覆盖进度。"""

    prepared, queue = prepare_review_queue(
        repo_path,
        inputs,
        render_prompt=render_prompt,
        max_prompt_chars=max_prompt_chars,
        max_diff_chars=max_diff_chars,
        max_items=max_items,
    )
    _save_queue(run_dir, queue)
    if queue.status == "blocked":
        verdict = _blocked_verdict(queue)
        _report(progress_reporter, "reviewer.review_queue_blocked")
        _trace(
            trace_reporter,
            "review_queue_finished",
            status=queue.status,
            covered=queue.covered,
            remaining=queue.remaining,
            findings=len(queue.findings),
            verdict=verdict.verdict,
        )
        return ReviewQueueOutcome(
            queue=queue,
            result=RunnerResult(
                status="skipped",
                output=verdict.model_dump_json(),
                error=queue.issue,
                command=["review-queue"],
            ),
            verdict=verdict,
            reviewer_started=False,
        )

    queue = queue.model_copy(update={"status": "running"})
    _save_queue(run_dir, queue)
    _report(progress_reporter, "reviewer.review_queue_started")
    _trace(
        trace_reporter,
        "review_queue_started",
        total_items=len(queue.items),
        candidate_sha=queue.candidate_sha,
    )

    results: list[RunnerResult] = []
    verdicts: list[ReviewVerdict] = []
    prepared_by_id = {task.item.item_id: task for task in prepared}
    for index, queue_item in enumerate(queue.items, start=1):
        task = prepared_by_id[queue_item.item_id]
        running_item = queue_item.model_copy(update={"status": "running"})
        queue = _replace_item(queue, running_item)
        _save_queue(run_dir, queue)
        _write_task_input(run_dir, task)
        _report(progress_reporter, "reviewer.review_task_started")
        _trace(
            trace_reporter,
            "review_queue_item_started",
            item_id=queue_item.item_id,
            item_index=index,
            target_files=queue_item.target_files,
        )

        runner = runner_factory(task.required_reviews)
        task_context = replace(
            execution_context,
            execution_dir=(
                execution_context.execution_dir / queue_item.item_id.lower()
            ),
            execution_id=None,
        )
        result = runner.run(
            task.prompt,
            repo_path.resolve(),
            sandbox="read-only",
            timeout_seconds=timeout_seconds,
            execution_context=task_context,
        )
        verdict = parse_verdict(result)
        trusted = trusted_result(result)
        results.append(result)
        verdicts.append(verdict)
        completed_item = _complete_item(
            running_item,
            result,
            verdict,
            trusted=trusted,
        )
        _write_task_result(run_dir, completed_item, result, verdict)
        queue = _refresh_queue_summary(_replace_item(queue, completed_item))
        _save_queue(run_dir, queue)
        event = (
            "reviewer.review_task_completed"
            if completed_item.status == "completed"
            else "reviewer.review_task_blocked"
        )
        _report(progress_reporter, event)
        _trace(
            trace_reporter,
            "review_queue_item_finished",
            item_id=queue_item.item_id,
            item_index=index,
            status=completed_item.status,
            runner_status=result.status,
            verdict=completed_item.verdict,
            covered=completed_item.covered,
            remaining=completed_item.remaining,
            findings=len(completed_item.findings),
        )
        if result.termination_unconfirmed or not trusted:
            break

    queue, verdict = _finish_queue(queue, verdicts)
    _save_queue(run_dir, queue)
    final_event = (
        "reviewer.review_queue_completed"
        if queue.status == "completed"
        else "reviewer.review_queue_blocked"
    )
    _report(progress_reporter, final_event)
    _trace(
        trace_reporter,
        "review_queue_finished",
        status=queue.status,
        covered=queue.covered,
        remaining=queue.remaining,
        findings=len(queue.findings),
        verdict=queue.verdict,
    )
    return ReviewQueueOutcome(
        queue=queue,
        result=_aggregate_runner_result(results, verdict, queue),
        verdict=verdict,
        reviewer_started=bool(results),
    )


def _complete_item(
    item: ReviewQueueItem,
    result: RunnerResult,
    verdict: ReviewVerdict,
    *,
    trusted: bool,
) -> ReviewQueueItem:
    coverage = build_review_file_coverage(
        item.target_files,
        verdict.reviewed_files,
    )
    target_set = set(item.target_files)
    covered = [
        path for path in item.target_files if path in set(verdict.reviewed_files)
    ]
    remaining = [path for path in item.target_files if path not in set(covered)]
    issues: list[str] = []
    if result.termination_unconfirmed:
        issues.append("Reviewer 进程树终止未确认")
    if not trusted:
        issues.append("Reviewer Runner 未形成可信终态")
    if not coverage["complete"]:
        issues.append("Reviewer 未精确覆盖本任务文件")
    if any(path not in target_set for path in verdict.reviewed_files):
        issues.append("Reviewer 声明了任务范围外文件")
    return item.model_copy(
        update={
            "status": "blocked" if issues else "completed",
            "covered": covered if trusted else [],
            "remaining": remaining if trusted else list(item.target_files),
            "findings": list(verdict.findings),
            "risk_disclosures": list(verdict.risk_disclosures),
            "checked_items": list(verdict.checked_items),
            "verdict": verdict.verdict,
            "runner_status": result.status,
            "issue": "；".join(dict.fromkeys(issues)) or None,
        }
    )


def _finish_queue(
    queue: ReviewQueue,
    verdicts: list[ReviewVerdict],
) -> tuple[ReviewQueue, ReviewVerdict]:
    queue = _refresh_queue_summary(queue)
    if any(item.status != "completed" for item in queue.items) or queue.remaining:
        queue = queue.model_copy(
            update={
                "status": "blocked",
                "verdict": "needs_human",
                "issue": queue.issue or "Review Queue 未完成全部文件覆盖",
            }
        )
        return queue, _blocked_verdict(queue)
    verdict = _aggregate_verdict(queue, verdicts)
    return (
        queue.model_copy(
            update={
                "status": "completed",
                "verdict": verdict.verdict,
                "issue": None,
            }
        ),
        verdict,
    )


def _refresh_queue_summary(queue: ReviewQueue) -> ReviewQueue:
    expected = [path for item in queue.items for path in item.target_files]
    covered_set = {path for item in queue.items for path in item.covered}
    return queue.model_copy(
        update={
            "covered": list(
                dict.fromkeys(path for path in expected if path in covered_set)
            ),
            "remaining": list(
                dict.fromkeys(path for path in expected if path not in covered_set)
            ),
            "findings": [
                finding for item in queue.items for finding in item.findings
            ],
        }
    )


def _aggregate_verdict(
    queue: ReviewQueue,
    verdicts: list[ReviewVerdict],
) -> ReviewVerdict:
    verdict_order = {
        "approve": 0,
        "request_changes": 1,
        "needs_human": 2,
    }
    selected = max(
        (verdict.verdict for verdict in verdicts),
        key=verdict_order.__getitem__,
        default="needs_human",
    )
    findings = _unique_models(
        [finding for verdict in verdicts for finding in verdict.findings]
    )
    disclosures = _unique_disclosures(
        [
            disclosure
            for verdict in verdicts
            for disclosure in verdict.risk_disclosures
        ]
    )
    checked_items = list(
        dict.fromkeys(
            item for verdict in verdicts for item in verdict.checked_items
        )
    )
    return ReviewVerdict(
        verdict=selected,
        summary=(
            f"Review Queue 已完成 {len(queue.items)} 个独立审查任务；"
            f"覆盖 {len(queue.covered)} 个变更文件。"
        ),
        findings=findings,
        risk_disclosures=disclosures,
        reviewed_files=list(queue.covered),
        checked_items=checked_items or ["Review Queue 文件覆盖"],
    )


def _blocked_verdict(queue: ReviewQueue) -> ReviewVerdict:
    finding = ReviewFinding(
        severity="major",
        title="Review Queue 未完成",
        evidence=(
            f"covered={','.join(queue.covered) or '<none>'}；"
            f"remaining={','.join(queue.remaining) or '<none>'}；"
            f"issue={queue.issue or 'unknown'}"
        ),
        recommendation="检查未覆盖文件和队列 Artifact，缩小任务或调整软预算后重新审查。",
    )
    return ReviewVerdict(
        verdict="needs_human",
        summary="Review Queue 未完成全部文件覆盖，不能采用部分审查作为通过结论。",
        findings=[*queue.findings, finding],
        reviewed_files=list(queue.covered),
        checked_items=["Review Queue 文件覆盖"],
    )


def _aggregate_runner_result(
    results: list[RunnerResult],
    verdict: ReviewVerdict,
    queue: ReviewQueue,
) -> RunnerResult:
    status = (
        next(
            (result.status for result in results if result.status != "success"),
            "success",
        )
        if results
        else "skipped"
    )
    errors = [result.error for result in results if result.error]
    if queue.issue:
        errors.append(queue.issue)
    return RunnerResult(
        status=status,
        output=verdict.model_dump_json(),
        error="；".join(dict.fromkeys(errors)) or None,
        command=["review-queue"],
        termination_unconfirmed=any(
            result.termination_unconfirmed for result in results
        ),
    )


def _replace_item(
    queue: ReviewQueue,
    replacement: ReviewQueueItem,
) -> ReviewQueue:
    return queue.model_copy(
        update={
            "items": [
                replacement if item.item_id == replacement.item_id else item
                for item in queue.items
            ]
        }
    )


def _write_task_input(run_dir: Path, task: PreparedReviewTask) -> None:
    directory = run_dir / task.item.artifact_dir
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("prompt.md").write_text(
        redact_text(task.prompt),
        encoding="utf-8",
        newline="\n",
    )
    metrics = measure_prompt(
        task.prompt,
        role="reviewer",
        max_chars=task.inputs["reviewer_prompt_max_chars"],
        sections={
            "source_brief": task.inputs["source_brief"],
            "reflection": task.inputs["reflection"],
            "diff_summary": task.inputs["diff_summary"],
            "test_summary": task.inputs["test_summary"],
            "project_context": task.inputs["project_context"],
            "full_diff": task.inputs["full_diff"],
        },
    )
    directory.joinpath("prompt-metrics.json").write_text(
        json.dumps(metrics.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_task_result(
    run_dir: Path,
    item: ReviewQueueItem,
    result: RunnerResult,
    verdict: ReviewVerdict,
) -> None:
    directory = run_dir / item.artifact_dir
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("runner-result.json").write_text(
        json.dumps(
            redact_value(
                {
                    "status": result.status,
                    "error": result.error,
                    "termination_unconfirmed": result.termination_unconfirmed,
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    directory.joinpath("verdict.json").write_text(
        render_redacted_queue_verdict(verdict),
        encoding="utf-8",
        newline="\n",
    )


def _save_queue(run_dir: Path, queue: ReviewQueue) -> None:
    path = run_dir / "review-queue.json"
    temp = path.with_name(f".{path.name}.{uuid4().hex[:12]}.tmp")
    temp.write_text(
        json.dumps(
            redact_value(queue.model_dump(mode="json")),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _unique_models(values: list[ReviewFinding]) -> list[ReviewFinding]:
    result: list[ReviewFinding] = []
    seen: set[str] = set()
    for value in values:
        digest = value.model_dump_json()
        if digest not in seen:
            seen.add(digest)
            result.append(value)
    return result


def _unique_disclosures(
    values: list[ReviewRiskDisclosure],
) -> list[ReviewRiskDisclosure]:
    result: list[ReviewRiskDisclosure] = []
    seen: set[str] = set()
    for value in values:
        if value.risk_id not in seen:
            seen.add(value.risk_id)
            result.append(value)
    return result


def _report(reporter: ProgressReporter | None, event: str) -> None:
    if reporter is None:
        return
    try:
        reporter(event, 0)
    except Exception:  # noqa: BLE001 - 可见性失败不能改变 Review 结果
        return


def _trace(reporter: TraceReporter | None, event: str, **payload: object) -> None:
    if reporter is not None:
        reporter(event, **redact_value(payload))
