from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .agent_codex_completion import (
    finish_evidence_untrusted as _finish_evidence_untrusted,
    review_status as _review_status,
    risk_status as _risk_status,
    scope_remained_inside_plan as _scope_remained_inside_plan,
    verification_status as _verification_status,
)
from .agent_change_run import ChangeRunContext, current_change_work_item
from .agent_change_control import ChangeFixPacket, render_fix_packet
from .agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentWorkItem,
    validate_v1_execution_binding,
)
from .agent_codex_scope import PlanScopeBaseline
from .agent_operation import child_summary_ref
from .agent_persistence import load_agent_checkpoint
from .agent_run import AgentRun
from .agent_runtime_support import capture_bound_workspace
from .execution_control import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionRecord,
    find_execution_records,
    inspect_execution_for_recovery,
)
from .models import LoopAutomationState
from .redaction import write_redacted_json_once
from .run_utils import resolve_run_dir
from .runner import Runner, RunnerResult
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
    verification_commands: tuple[str, ...]
    external_side_effects: Literal["none", "known", "unknown"]
    plan_scope_baseline: PlanScopeBaseline
    comparison_base_sha: str | None = None
    comparison_paths: tuple[str, ...] = ()
    change_context: ChangeRunContext | None = None


@dataclass(frozen=True)
class ExecutedCodexAttempt:
    prepared: PreparedCodexAttempt
    bound: AgentRun
    child_dir: Path
    operation_id: str
    worker_record: ExecutionRecord
    result: RunnerResult


def evaluate_worker_claim(output: str) -> tuple[WorkerClaim | None, str | None]:
    try:
        claim = WorkerClaim.model_validate_json(output)
    except ValidationError as exc:
        return None, f"Worker 最终 Claim 不符合窄 Schema：{exc.errors()[0]['type']}"
    if claim.claimed_status == "blocked":
        return claim, f"Worker 最终 Claim 明确为 blocked；未启动 Core：{claim.summary}"
    return claim, None


def require_single_executable_work_item(
    plan: AgentPlan,
    state: AgentState,
) -> AgentWorkItem:
    work_item = validate_v1_execution_binding(plan, state.current_work_item)
    commands = [command.strip() for command in work_item.verification]
    if not commands:
        raise ValueError("真实 Adapter Work Item 必须冻结至少一个验证命令")
    if any("\n" in command or "\r" in command for command in commands):
        raise ValueError("真实 Adapter Work Item 的验证命令必须是单行")
    if len(set(commands)) != len(commands):
        raise ValueError("真实 Adapter Work Item 的验证命令不能重复")
    return work_item


def require_executable_work_item(
    plan: AgentPlan,
    state: AgentState,
) -> AgentWorkItem:
    """legacy run 保持单项限制；ChangeRun 只取状态机当前项。"""

    work_item = (
        current_change_work_item(plan, state)
        if state.run_kind == "change"
        else validate_v1_execution_binding(plan, state.current_work_item)
    )
    commands = [command.strip() for command in work_item.verification]
    if not commands:
        raise ValueError("真实 Adapter Work Item 必须冻结至少一个验证命令")
    if any("\n" in command or "\r" in command for command in commands):
        raise ValueError("真实 Adapter Work Item 的验证命令必须是单行")
    if len(set(commands)) != len(commands):
        raise ValueError("真实 Adapter Work Item 的验证命令不能重复")
    return work_item


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


def build_repair_prompt(
    child_dir: Path,
    task_brief: str,
    *,
    fix_packet: ChangeFixPacket | None = None,
) -> str:
    """只把当前 Task Brief 与结构化修复要求交给新 Worker。"""

    if fix_packet is not None:
        return (
            f"{task_brief.rstrip()}\n\n"
            f"{render_fix_packet(fix_packet).strip()}\n"
        )

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
    relative = child_summary_ref(child_dir.name, operation_id)
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
    external_side_effects: Literal["none", "known", "unknown"],
) -> AgentObservation:
    latest = child_state.iterations[-1] if child_state.iterations else None
    finish_status = finish_summary.get("finish_status")
    verification = _verification_status(latest, finish_summary)
    risk = _risk_status(latest)
    review = _review_status(latest)
    work_item_completed = (
        finish_status == "ready_to_commit"
        and not _finish_evidence_untrusted(finish_summary)
    )
    all_work_items_completed = work_item_completed and all(
        item.work_item_id == state.current_work_item
        or item.status in {"completed", "superseded"}
        for item in plan.work_items
    )
    repairable = (
        latest is not None
        and _scope_remained_inside_plan(latest)
        and not _finish_evidence_untrusted(finish_summary)
        and (
            finish_status == "needs_fix"
            or (
                review == "failed"
                and verification == "passed"
                and risk == "passed"
            )
        )
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
        evidence_sha256=hash_evidence_refs(agent_run_dir, evidence_refs),
        authority="machine_reconcile",
        operation_started=True,
        workspace_explained=True,
        external_side_effects=external_side_effects,
        repairable_in_scope=repairable,
        verification=verification,
        risk=risk,
        review=review,
        work_item_completed=work_item_completed,
        all_work_items_completed=all_work_items_completed,
    )


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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_evidence_refs(
    run_dir: Path,
    evidence_refs: list[str],
) -> dict[str, str]:
    root = run_dir.resolve(strict=True)
    result: dict[str, str] = {}
    for ref in evidence_refs:
        path = (root / ref).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Observation 证据引用越过 Agent run 或不是普通文件")
        result[ref] = _sha256_file(path)
    return result
