from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .prompt_metrics import PromptMetrics
from .review_contract import (
    ReviewFinding,
    ReviewRiskDisclosure,
    ReviewVerdict,
    normalize_review_path,
)
from .redaction import redact_value
from .run_utils import resolve_run_dir

MAX_REVIEW_QUEUE_ITEMS = 8
REVIEW_QUEUE_ARTIFACT = "review-queue.json"
_OID_PATTERN = r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$"


class ReviewQueueItem(BaseModel):
    """一个只读 Reviewer 会话负责的文件集合。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    status: Literal["pending", "running", "completed", "blocked"] = "pending"
    target_files: list[str]
    covered: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    risk_disclosures: list[ReviewRiskDisclosure] = Field(default_factory=list)
    checked_items: list[str] = Field(default_factory=list)
    verdict: Literal["approve", "request_changes", "needs_human"] | None = None
    runner_status: Literal[
        "skipped",
        "success",
        "error",
        "timed_out",
        "stopped",
    ] = "skipped"
    prompt_chars: int = Field(default=0, ge=0)
    diff_chars: int = Field(default=0, ge=0)
    issue: str | None = None
    artifact_dir: str

    @field_validator("target_files", "covered", "remaining")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [normalize_review_path(item) for item in values]
        if any(not item for item in normalized):
            raise ValueError("Review Queue 路径不能为空")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Review Queue 路径不能重复")
        return normalized


class ReviewQueue(BaseModel):
    """超预算审查的有界队列；Git 与 Workspace 指纹仍拥有代码事实。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_run: str
    candidate_sha: str = Field(pattern=_OID_PATTERN)
    workspace_fingerprint: str
    trigger: list[Literal["prompt_budget", "diff_budget"]]
    status: Literal["planned", "running", "completed", "blocked"] = "planned"
    max_items: int = Field(ge=1)
    max_prompt_chars: int = Field(ge=1)
    max_diff_chars: int = Field(ge=1)
    items: list[ReviewQueueItem] = Field(default_factory=list)
    covered: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    verdict: Literal["approve", "request_changes", "needs_human"] | None = None
    issue: str | None = None

    @field_validator("covered", "remaining")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [normalize_review_path(item) for item in values]
        if any(not item for item in normalized):
            raise ValueError("Review Queue 汇总路径不能为空")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Review Queue 汇总路径不能重复")
        return normalized


def review_queue_required(
    inputs: dict[str, object],
    metrics: PromptMetrics,
) -> bool:
    """只有最终 Prompt 或完整 Diff 超过软预算时才启用队列。"""

    truncated = set(inputs.get("truncated_sections") or [])
    return metrics.exceeded or "full_diff" in truncated


def queue_context_summary(queue: ReviewQueue) -> dict[str, object]:
    return {
        "schema_version": queue.schema_version,
        "status": queue.status,
        "candidate_sha": queue.candidate_sha,
        "workspace_fingerprint": queue.workspace_fingerprint,
        "trigger": list(queue.trigger),
        "total_items": len(queue.items),
        "completed_items": sum(
            item.status == "completed" for item in queue.items
        ),
        "covered": list(queue.covered),
        "remaining": list(queue.remaining),
        "findings_count": len(queue.findings),
        "verdict": queue.verdict,
        "issue": queue.issue,
    }


def render_redacted_queue_verdict(verdict: ReviewVerdict) -> str:
    return json.dumps(
        redact_value(verdict.model_dump(mode="json")),
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def review_queue_status_payload(
    run_dir: Path,
    *,
    iteration_number: int | None = None,
) -> dict[str, object]:
    """从当前 run 或指定迭代的队列 Artifact 读取实时进度。"""

    path = _review_queue_artifact_path(
        run_dir,
        iteration_number=iteration_number,
    )
    if path is None:
        return {
            "review_queue_status": "not_used",
            "review_queue_completed": 0,
            "review_queue_total": 0,
        }
    try:
        queue = ReviewQueue.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "review_queue_status": "invalid",
            "review_queue_completed": 0,
            "review_queue_total": 0,
        }
    return {
        "review_queue_status": queue.status,
        "review_queue_completed": sum(
            item.status == "completed" for item in queue.items
        ),
        "review_queue_total": len(queue.items),
    }


def projected_review_queue_status_payload(
    workspace: Path,
    run_dir: Path,
    state: dict[str, Any],
    kind: str,
) -> dict[str, object]:
    """读取当前 run，或父 Agent 最近可信 child 的队列进度。"""

    target_dir, target_state = run_dir, state
    if kind == "agent":
        child_run = state.get("last_child_run")
        if not isinstance(child_run, str):
            return review_queue_status_payload(run_dir)
        try:
            target_dir = resolve_run_dir(workspace, child_run)
        except FileNotFoundError:
            return review_queue_status_payload(run_dir)
        target_state = _read_child_state(target_dir)
        if target_state is None:
            return _invalid_status_payload()
    return review_queue_status_payload(
        target_dir,
        iteration_number=_latest_iteration_number(target_state),
    )


def _review_queue_artifact_path(
    run_dir: Path,
    *,
    iteration_number: int | None,
) -> Path | None:
    direct = run_dir / REVIEW_QUEUE_ARTIFACT
    if direct.is_file():
        return direct
    if iteration_number is None or iteration_number < 1:
        return None
    iteration = (
        run_dir
        / "iterations"
        / f"{iteration_number:02d}"
        / REVIEW_QUEUE_ARTIFACT
    )
    return iteration if iteration.is_file() else None


def _read_child_state(run_dir: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("run_id") != run_dir.name:
        return None
    return payload


def _latest_iteration_number(state: dict[str, Any]) -> int | None:
    candidates: list[int] = []
    iterations = state.get("iterations")
    if isinstance(iterations, list) and iterations:
        latest = iterations[-1]
        if isinstance(latest, dict):
            value = latest.get("iteration")
            if isinstance(value, int) and not isinstance(value, bool):
                candidates.append(value)
    value = state.get("current_iteration")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        candidates.append(value)
    return max(candidates) if candidates else None


def _invalid_status_payload() -> dict[str, object]:
    return {
        "review_queue_status": "invalid",
        "review_queue_completed": 0,
        "review_queue_total": 0,
    }
