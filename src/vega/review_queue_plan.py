from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .git_read import run_git_bytes
from .models import GateResult, RequiredReviewHit
from .review_contract import normalize_review_path
from .review_queue_contract import ReviewQueue, ReviewQueueItem
from .risk_review_runtime import required_reviews_from_inputs
from .tracked_workspace import render_tracked_diff_sections


@dataclass(frozen=True)
class PreparedReviewTask:
    item: ReviewQueueItem
    inputs: dict[str, Any]
    prompt: str
    required_reviews: list[RequiredReviewHit]


@dataclass(frozen=True)
class _DiffUnit:
    files: tuple[str, ...]
    diff: str


PromptRenderer = Callable[[dict[str, Any]], str]


def prepare_review_queue(
    repo_path: Path,
    inputs: dict[str, Any],
    *,
    render_prompt: PromptRenderer,
    max_prompt_chars: int,
    max_diff_chars: int,
    max_items: int,
) -> tuple[list[PreparedReviewTask], ReviewQueue]:
    changed_files = [
        normalize_review_path(item) for item in inputs["changed_files"]
    ]
    trigger: list[Literal["prompt_budget", "diff_budget"]] = []
    if inputs.get("_review_prompt_exceeded"):
        trigger.append("prompt_budget")
    if "full_diff" in (inputs.get("truncated_sections") or []):
        trigger.append("diff_budget")
    queue = ReviewQueue(
        source_run=str(inputs["source_run"]),
        candidate_sha=str(inputs["_current_head_sha"]),
        workspace_fingerprint=str(inputs["current_workspace_fingerprint"]),
        trigger=trigger,
        max_items=max_items,
        max_prompt_chars=max_prompt_chars,
        max_diff_chars=max_diff_chars,
        remaining=changed_files,
    )
    unsupported_sections = [
        item
        for item in inputs.get("truncated_sections") or []
        if item != "full_diff"
    ]
    if unsupported_sections:
        return [], queue.model_copy(
            update={
                "status": "blocked",
                "issue": (
                    "Review Queue 只能拆分完整 Diff；以下输入仍被截断："
                    + "、".join(unsupported_sections)
                ),
            }
        )
    try:
        units = _build_diff_units(repo_path, inputs, changed_files)
    except (OSError, RuntimeError, ValueError) as exc:
        return [], queue.model_copy(
            update={
                "status": "blocked",
                "issue": (
                    "无法为 Review Queue 读取逐文件 Diff："
                    f"{type(exc).__name__}"
                ),
            }
        )
    prepared, issue = _pack_tasks(
        inputs,
        units,
        render_prompt=render_prompt,
        max_prompt_chars=max_prompt_chars,
        max_diff_chars=max_diff_chars,
        max_items=max_items,
    )
    if issue is not None:
        return prepared, queue.model_copy(
            update={
                "status": "blocked",
                "items": [task.item for task in prepared],
                "issue": issue,
            }
        )
    return prepared, queue.model_copy(
        update={"items": [task.item for task in prepared]}
    )


def _build_diff_units(
    repo_path: Path,
    inputs: dict[str, Any],
    changed_files: list[str],
) -> list[_DiffUnit]:
    groups = _risk_bound_file_groups(
        changed_files,
        required_reviews_from_inputs(inputs),
    )
    units: list[_DiffUnit] = []
    by_diff: dict[str, int] = {}
    for group in groups:
        diff = _collect_target_diff(repo_path, inputs, group)
        if not diff.strip():
            raise ValueError("变更文件没有可绑定的 tracked Diff")
        existing_index = by_diff.get(diff)
        if existing_index is None:
            by_diff[diff] = len(units)
            units.append(_DiffUnit(files=tuple(group), diff=diff))
            continue
        existing = units[existing_index]
        merged = _ordered_paths(
            changed_files,
            [*existing.files, *group],
        )
        units[existing_index] = _DiffUnit(files=tuple(merged), diff=diff)
    return units


def _risk_bound_file_groups(
    changed_files: list[str],
    required_reviews: list[RequiredReviewHit],
) -> list[list[str]]:
    groups = [
        set(path for path in item.matched_files if path in changed_files)
        for item in required_reviews
    ]
    groups = [group for group in groups if group]
    merged: list[set[str]] = []
    for group in groups:
        overlaps = [existing for existing in merged if existing & group]
        if not overlaps:
            merged.append(set(group))
            continue
        combined = set(group)
        for existing in overlaps:
            combined.update(existing)
            merged.remove(existing)
        merged.append(combined)
    covered = set().union(*merged) if merged else set()
    merged.extend({path} for path in changed_files if path not in covered)
    return [_ordered_paths(changed_files, group) for group in merged]


def _pack_tasks(
    inputs: dict[str, Any],
    units: list[_DiffUnit],
    *,
    render_prompt: PromptRenderer,
    max_prompt_chars: int,
    max_diff_chars: int,
    max_items: int,
) -> tuple[list[PreparedReviewTask], str | None]:
    prepared: list[PreparedReviewTask] = []
    current: list[_DiffUnit] = []
    for unit in units:
        candidate = [*current, unit]
        task = _prepare_task(
            inputs,
            candidate,
            len(prepared) + 1,
            render_prompt=render_prompt,
        )
        if _task_within_budget(task, max_prompt_chars, max_diff_chars):
            current = candidate
            continue
        if current:
            prepared.append(
                _prepare_task(
                    inputs,
                    current,
                    len(prepared) + 1,
                    render_prompt=render_prompt,
                )
            )
            current = [unit]
            task = _prepare_task(
                inputs,
                current,
                len(prepared) + 1,
                render_prompt=render_prompt,
            )
        if not _task_within_budget(task, max_prompt_chars, max_diff_chars):
            blocked = task.item.model_copy(
                update={
                    "status": "blocked",
                    "issue": "单个不可拆分文件组仍超过 Reviewer 软预算",
                }
            )
            prepared.append(
                PreparedReviewTask(
                    item=blocked,
                    inputs=task.inputs,
                    prompt=task.prompt,
                    required_reviews=task.required_reviews,
                )
            )
            return prepared, blocked.issue
    if current:
        prepared.append(
            _prepare_task(
                inputs,
                current,
                len(prepared) + 1,
                render_prompt=render_prompt,
            )
        )
    if len(prepared) > max_items:
        return prepared, (
            f"Review Queue 需要 {len(prepared)} 个任务，超过固定上限 {max_items}"
        )
    return prepared, None


def _prepare_task(
    inputs: dict[str, Any],
    units: list[_DiffUnit],
    index: int,
    *,
    render_prompt: PromptRenderer,
) -> PreparedReviewTask:
    target_files = _ordered_paths(
        inputs["changed_files"],
        [path for unit in units for path in unit.files],
    )
    full_diff = "\n\n".join(unit.diff.rstrip() for unit in units).rstrip() + "\n"
    task_inputs = dict(inputs)
    task_inputs["changed_files"] = target_files
    task_inputs["full_diff"] = full_diff
    task_inputs["truncated_sections"] = [
        item
        for item in inputs.get("truncated_sections") or []
        if item != "full_diff"
    ]
    task_inputs["risk_gate"] = _filtered_risk_gate(inputs, target_files)
    task_inputs["review_queue"] = {
        "item_id": f"RQ-{index:02d}",
        "all_changed_files": list(inputs["changed_files"]),
        "target_files": target_files,
        "candidate_sha": inputs["_current_head_sha"],
    }
    prompt = render_prompt(task_inputs)
    item = ReviewQueueItem(
        item_id=f"RQ-{index:02d}",
        target_files=target_files,
        remaining=target_files,
        prompt_chars=len(prompt),
        diff_chars=len(full_diff),
        artifact_dir=f"review-queue/rq-{index:02d}",
    )
    return PreparedReviewTask(
        item=item,
        inputs=task_inputs,
        prompt=prompt,
        required_reviews=required_reviews_from_inputs(task_inputs),
    )


def _filtered_risk_gate(
    inputs: dict[str, Any],
    target_files: list[str],
) -> object:
    risk_gate = inputs.get("risk_gate")
    if not isinstance(risk_gate, dict) or risk_gate.get("status") != "success":
        return risk_gate
    try:
        result = GateResult.model_validate(risk_gate.get("result"))
    except ValueError:
        return risk_gate
    target_set = set(target_files)
    required = [
        item
        for item in result.required_reviews
        if set(item.matched_files).issubset(target_set)
    ]
    recommendation = (
        "human-review"
        if required
        else "isolated-review"
        if result.recommendation != "self-check"
        else "self-check"
    )
    filtered = result.model_copy(
        update={
            "changed_files": target_files,
            "required_reviews": required,
            "recommendation": recommendation,
        }
    )
    return {
        "status": "success",
        "source_run": risk_gate.get("source_run"),
        "result": filtered.model_dump(mode="json"),
    }


def _collect_target_diff(
    repo_path: Path,
    inputs: dict[str, Any],
    paths: list[str],
) -> str:
    repo = repo_path.resolve()
    head_sha = str(inputs["_current_head_sha"])
    comparison_base = inputs.get("comparison_base_sha")
    committed = ""
    if isinstance(comparison_base, str):
        committed = _git_diff(repo, [comparison_base, head_sha], paths)
    staged = _git_diff(repo, ["--cached", head_sha], paths)
    unstaged = _git_diff(repo, [], paths)
    return render_tracked_diff_sections(
        staged,
        unstaged,
        committed_diff=committed,
        comparison_base_sha=(
            comparison_base if isinstance(comparison_base, str) else None
        ),
    )


def _git_diff(repo: Path, revision_args: list[str], paths: list[str]) -> str:
    output = run_git_bytes(
        repo,
        [
            "git",
            "-c",
            "core.autocrlf=input",
            "--literal-pathspecs",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=3",
            *revision_args,
            "--",
            *paths,
        ],
    ).decode("utf-8", errors="replace")
    return output.replace("\r\n", "\n").replace("\r", "\n")


def _task_within_budget(
    task: PreparedReviewTask,
    max_prompt_chars: int,
    max_diff_chars: int,
) -> bool:
    return (
        task.item.prompt_chars <= max_prompt_chars
        and task.item.diff_chars <= max_diff_chars
    )


def _ordered_paths(reference: list[str], values: object) -> list[str]:
    value_set = {str(item) for item in values}
    return [path for path in reference if path in value_set]
