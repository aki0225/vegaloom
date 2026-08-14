from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    GateStatus,
    canonical_digest,
)
from .agent_persistence import load_agent_checkpoint
from .agent_run import AgentRun
from .agent_runtime_support import capture_bound_workspace
from .execution_control import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionRecord,
    find_execution_records,
    inspect_execution_for_recovery,
)
from .models import LoopAutomationState, LoopIterationState
from .project_config import ScopeConfig
from .redaction import write_redacted_json_once
from .run_utils import resolve_run_dir
from .runner import Runner, RunnerResult
from .scope_gate import ScopeGateResult, evaluate_scope_gate
from .workspace_check import ReviewWorkspaceSnapshot


ClaimSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
ClaimListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class WorkerClaim(BaseModel):
    """Worker 的窄自述；所有完成判断仍由 Workspace 与 Core Artifact 重算。"""

    model_config = ConfigDict(extra="forbid")

    claimed_status: Literal["completed", "blocked"]
    summary: ClaimSummary
    tests_claimed: list[ClaimListItem] = Field(max_length=20)
    remaining_questions: list[ClaimListItem] = Field(max_length=20)


@dataclass(frozen=True)
class PreparedCodexAttempt:
    run_dir: Path
    state: AgentState
    plan: AgentPlan
    attempt_number: int
    before: ReviewWorkspaceSnapshot
    repo: Path
    task_brief: str
    runner: Runner


@dataclass(frozen=True)
class ExecutedCodexAttempt:
    prepared: PreparedCodexAttempt
    bound: AgentRun
    child_dir: Path
    operation_id: str
    worker_record: ExecutionRecord
    result: RunnerResult


def require_single_executable_work_item(
    plan: AgentPlan,
    state: AgentState,
) -> None:
    executable = [
        item
        for item in plan.work_items
        if item.status not in {"completed", "superseded"}
    ]
    if (
        len(executable) != 1
        or executable[0].work_item_id != state.current_work_item
    ):
        raise ValueError(
            "Gate 2B 真实 Adapter 当前只接受一个未完成 Work Item；"
            "多 Work Item 的累计 Diff 归因尚未取得可信证据"
        )


def evaluate_plan_scope(
    repo: Path,
    plan: AgentPlan,
    *,
    expected_head_sha: str,
    iteration: int,
) -> ScopeGateResult:
    """把已批准 Plan 的全部路径约束作为 Adapter 额外机器门禁。"""

    allowed_paths = list(
        dict.fromkeys(
            path
            for item in plan.work_items
            if item.status != "superseded"
            for path in item.allowed_paths
        )
    )
    forbidden_paths = list(
        dict.fromkeys(
            path
            for item in plan.work_items
            if item.status != "superseded"
            for path in item.forbidden_paths
        )
    )
    return evaluate_scope_gate(
        repo,
        ScopeConfig(
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
        ),
        iteration=iteration,
        phase="pre_verification",
        expected_head_sha=expected_head_sha,
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


def require_repair_child(
    workspace: Path,
    run_dir: Path,
    state: AgentState,
    repo: Path,
) -> Path:
    """从最近安全 Checkpoint 恢复同一 Work Item 的失败 child。"""

    if "repair" not in state.allowed_actions or not state.latest_checkpoint_id:
        raise ValueError("当前状态没有可复用的 repair child")
    checkpoint = load_agent_checkpoint(
        run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
    )
    if (
        checkpoint.run_id != state.run_id
        or checkpoint.current_work_item != state.current_work_item
        or len(checkpoint.failed_attempts) != 1
    ):
        raise ValueError("最近 Checkpoint 没有唯一失败 child，拒绝猜测 repair 来源")
    child_dir = resolve_run_dir(workspace, checkpoint.failed_attempts[0])
    child_state = load_child_state(child_dir, repo)
    if (
        child_state.status != "needs_human"
        or child_state.current_step == "waiting_for_worker"
        or not child_state.iterations
    ):
        raise ValueError("失败 child 尚未形成可继续的 Core 结果")
    require_child_quiescent(child_dir)
    return child_dir


def build_repair_prompt(child_dir: Path, task_brief: str) -> str:
    """只把当前 Task Brief 与 Core 生成的最新 fix prompt 交给新 Worker。"""

    candidates = sorted(child_dir.glob("iterations/*/fix-prompt.md"))
    if not candidates:
        raise ValueError("失败 child 缺少 fix-prompt.md，不能安全启动 repair Worker")
    try:
        fix_prompt = candidates[-1].read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("无法读取失败 child 的 fix-prompt.md") from exc
    if not fix_prompt.strip():
        raise ValueError("失败 child 的 fix-prompt.md 为空")
    return (
        f"{task_brief.rstrip()}\n\n"
        "## 当前修复要求\n\n"
        f"{fix_prompt.strip()}\n"
    )


def require_waiting_child(child_dir: Path, repo: Path) -> LoopAutomationState:
    state = load_child_state(child_dir, repo)
    if (
        state.automation_mode != "assist"
        or state.status != "needs_human"
        or state.current_step != "waiting_for_worker"
    ):
        raise ValueError(
            "assist child 未形成 waiting_for_worker baseline，拒绝绑定真实 Worker"
        )
    if not (child_dir / "worker-prompt.md").is_file():
        raise ValueError("assist child 缺少 worker-prompt.md")
    return state


def load_child_state(child_dir: Path, repo: Path) -> LoopAutomationState:
    try:
        state = LoopAutomationState.model_validate_json(
            (child_dir / "state.json").read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("无法解析 assist child state") from exc
    if state.run_id != child_dir.name:
        raise ValueError("assist child state 与目录身份不一致")
    if Path(state.repo_path).resolve() != repo.resolve():
        raise ValueError("assist child 绑定了不同目标仓库")
    return state


def load_finish_summary(child_dir: Path, child_run: str) -> dict[str, object]:
    try:
        payload = json.loads(
            (child_dir / "finish-summary.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法解析 child finish-summary.json") from exc
    if not isinstance(payload, dict) or payload.get("run_id") != child_run:
        raise ValueError("child Finish 摘要身份不一致")
    return payload


def require_terminal_worker_execution(
    child_dir: Path,
    operation_id: str,
) -> ExecutionRecord:
    records = [
        record
        for record in find_execution_records(child_dir)
        if record.lease.execution_id == operation_id
    ]
    if len(records) != 1:
        raise ValueError(
            "无法唯一定位与当前 operation 匹配的 Worker execution；"
            "已保留 Writer binding。"
        )
    record = records[0]
    if record.lease.step != "worker":
        raise ValueError("当前 operation 绑定的 execution 不是 Worker")
    if record.lease.status not in TERMINAL_EXECUTION_STATUSES:
        raise ValueError("Worker execution 尚未形成可信终态")
    require_child_quiescent(child_dir)
    return record


def require_child_quiescent(child_dir: Path) -> None:
    inspection = inspect_execution_for_recovery(child_dir)
    if not inspection.can_recover:
        raise ValueError(f"child 仍有活动或未确认进程：{inspection.summary}")


def write_child_summary(
    run_dir: Path,
    state: AgentState,
    child_dir: Path,
    operation_id: str,
    worker_record: ExecutionRecord,
    result: RunnerResult,
    *,
    claim: WorkerClaim | None = None,
    failure_reason: str | None = None,
    child_state: LoopAutomationState | None = None,
    finish_summary: dict[str, object] | None = None,
) -> str:
    relative = (
        "children/"
        f"{canonical_digest({'child': child_dir.name, 'operation_id': operation_id})}.json"
    )
    finish_path = child_dir / "finish-summary.json"
    write_redacted_json_once(
        run_dir / relative,
        {
            "schema_version": 1,
            "authority": "child_binding_summary",
            "agent_run_id": state.run_id,
            "work_item_id": state.current_work_item,
            "child_run": child_dir.name,
            "operation_id": operation_id,
            "worker": {
                "runner_status": result.status,
                "lease_status": worker_record.lease.status,
                "execution_artifact": worker_record.path.relative_to(
                    child_dir
                ).as_posix(),
                "execution_sha256": _sha256_file(worker_record.path),
                "claim": claim.model_dump(mode="json") if claim is not None else None,
                "failure_reason": failure_reason,
            },
            "core": {
                "status": child_state.status if child_state is not None else "not_run",
                "current_step": (
                    child_state.current_step if child_state is not None else "not_run"
                ),
                "finish_status": (
                    finish_summary.get("finish_status")
                    if finish_summary is not None
                    else None
                ),
                "finish_sha256": (
                    _sha256_file(finish_path) if finish_path.is_file() else None
                ),
            },
        },
    )
    return relative


def observation_from_child(
    agent_run_dir: Path,
    state: AgentState,
    plan: AgentPlan,
    child_dir: Path,
    operation_id: str,
    claim: WorkerClaim,
    child_state: LoopAutomationState,
    finish_summary: dict[str, object],
    *,
    evidence_refs: list[str],
) -> AgentObservation:
    latest = child_state.iterations[-1] if child_state.iterations else None
    finish_status = finish_summary.get("finish_status")
    verification = _verification_status(latest, finish_summary)
    risk = _risk_status(latest)
    review = _review_status(latest)
    work_item_completed = finish_status == "ready_to_commit"
    all_work_items_completed = work_item_completed and all(
        item.work_item_id == state.current_work_item
        or item.status in {"completed", "superseded"}
        for item in plan.work_items
    )
    repairable = (
        finish_status == "needs_fix"
        and latest is not None
        and latest.workspace_status == "passed"
        and _scope_remained_inside_plan(latest)
    )
    snapshot = capture_bound_workspace(agent_run_dir)
    return AgentObservation(
        observation_id=f"observation-{uuid4().hex[:12]}",
        work_item_id=state.current_work_item,
        child_run=child_dir.name,
        operation_id=operation_id,
        worker_claim=claim.summary,
        machine_summary=(
            f"child Finish={finish_status}；"
            f"Verification={verification}；Risk={risk}；Reviewer={review}"
        ),
        workspace_fingerprint=snapshot.fingerprint,
        changed_files=list(snapshot.changed_files),
        evidence_refs=evidence_refs,
        authority="machine_reconcile",
        operation_started=True,
        workspace_explained=True,
        external_side_effects="none",
        repairable_in_scope=repairable,
        verification=verification,
        risk=risk,
        review=review,
        work_item_completed=work_item_completed,
        all_work_items_completed=all_work_items_completed,
    )


def operation_ref(operation_id: str) -> str:
    return f"operations/{canonical_digest({'operation_id': operation_id})}.json"


def decision_label(result: AgentRun, observation: AgentObservation) -> str:
    if result.state.phase == "finalizing":
        return "finalize"
    if result.state.phase == "planning":
        return "replan"
    if result.state.phase == "needs_human":
        return "human"
    if result.state.phase == "ready":
        return "next" if observation.work_item_completed else "repair"
    return result.state.phase


def _verification_status(
    latest: LoopIterationState | None,
    finish_summary: dict[str, object],
) -> GateStatus:
    if finish_summary.get("latest_verification_failed") is True:
        return "failed"
    if latest is not None and latest.verification_status == "failed":
        return "failed"
    if finish_summary.get("verification_passed") is True:
        return "passed"
    if _finish_evidence_untrusted(finish_summary):
        return "blocked"
    return "not_run"


def _risk_status(latest: LoopIterationState | None) -> GateStatus:
    if latest is None or latest.risk_gate_status == "skipped":
        return "not_run"
    if latest.risk_gate_status == "failed":
        return "failed"
    if latest.risk_gate_recommendation == "human-review":
        return "blocked"
    return "passed"


def _review_status(latest: LoopIterationState | None) -> GateStatus:
    if latest is None or latest.reviewer_status == "skipped":
        return "not_run"
    if latest.reviewer_status != "success":
        return "blocked"
    if latest.verdict == "approve":
        return "passed"
    if latest.verdict == "request_changes":
        return "failed"
    return "blocked"


def _scope_remained_inside_plan(latest: LoopIterationState) -> bool:
    statuses = (
        latest.scope_gate_status,
        latest.scope_gate_post_verification_status,
        latest.scope_gate_pre_review_status,
    )
    return all(status in {"skipped", "success"} for status in statuses) and not (
        latest.scope_gate_violations
        or latest.scope_gate_post_verification_violations
        or latest.scope_gate_pre_review_violations
    )


def _finish_evidence_untrusted(finish_summary: dict[str, object]) -> bool:
    integrity = finish_summary.get("artifact_integrity")
    freshness = finish_summary.get("evidence_freshness")
    return (
        not isinstance(integrity, dict)
        or integrity.get("valid") is not True
        or not isinstance(freshness, dict)
        or freshness.get("fresh") is not True
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
