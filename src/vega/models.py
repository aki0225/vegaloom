from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .redaction import redact_value


def _save_model(path: Path, model: BaseModel) -> None:
    payload = redact_value(model.model_dump(mode="json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _model_temp_path(path)
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    last_error: OSError | None = None
    for _ in range(10):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.02)
    if temp_path.exists():
        temp_path.unlink()
    assert last_error is not None
    raise last_error


def _model_temp_path(path: Path) -> Path:
    # 64 位随机后缀足以隔离同目录写入，同时控制深层 Windows 路径长度。
    return path.with_name(f".m.{uuid4().hex[:16]}")


class ToolResult(BaseModel):
    tool: str
    status: Literal["ok", "error"] = "ok"
    output: Any = None
    error: str | None = None


class MemoryProposal(BaseModel):
    id: str = Field(default_factory=lambda: f"mp-{uuid4().hex[:12]}")
    type: str
    title: str
    content: str
    source_run_id: str
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    sensitivity: Literal["public", "internal", "sensitive"] = "internal"
    tags: list[str] = Field(default_factory=list)
    status: Literal["pending", "accepted", "rejected"] = "pending"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    repo: str | None = None
    paths: list[str] = Field(default_factory=list)


class MemoryLedgerEntry(BaseModel):
    proposal_id: str
    source_run_id: str
    type: str
    title: str
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Literal["public", "internal", "sensitive"]
    tags: list[str] = Field(default_factory=list)
    status: Literal["accepted", "rejected"]
    decided_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason: str | None = None
    repo: str | None = None
    paths: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    run_id: str
    loop_name: str
    status: Literal["created", "running", "success", "failed"] = "created"
    repo_path: str
    task_file: str
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    findings: list[str] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)
    review_results: list[str] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class ToolPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class BudgetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = 8
    max_tool_calls: int = 10
    max_minutes: int = 10


class InspectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_section_names: list[str] = Field(
        default_factory=lambda: ["Target files", "Target files to search", "目标文件"]
    )
    search_queries: list[str] = Field(default_factory=list)
    git_checks: list[str] = Field(default_factory=lambda: ["git.status"])


class ReportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = "工程变更报告"
    required_sections: list[str] = Field(
        default_factory=lambda: ["摘要", "上下文", "证据", "发现", "风险", "建议修改", "验证", "记忆提案"]
    )


class ReviewSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    checks: list[str] = Field(
        default_factory=lambda: [
            "report_answers_task",
            "no_unsupported_file_changes",
            "no_forbidden_actions",
            "tool_policy_respected",
        ]
    )
    forbidden_phrases: list[str] = Field(
        default_factory=lambda: [
            "git commit",
            "git push",
            "自动提交",
            "自动发布",
            "自动应用补丁",
            "已经修改文件",
            "已修改文件",
            "applied patch",
            "committed changes",
            "pushed changes",
        ]
    )


class EvalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_checks: list[str] = Field(
        default_factory=lambda: [
            "state.json",
            "trace.jsonl",
            "plan.md",
            "report.md",
            "review.md",
            "eval.md",
        ]
    )
    trace_events: list[str] = Field(
        default_factory=lambda: [
            "task_loaded",
            "plan_written",
            "tool_call",
            "report_written",
            "review_written",
        ]
    )
    require_report_sections: bool = True
    require_tool_policy: bool = True
    require_no_automatic_memory_write: bool = True
    require_review_pass: bool = True


class LoopSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    input: dict[str, str] = Field(default_factory=dict)
    tools: ToolPolicySpec = Field(default_factory=ToolPolicySpec)
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    inspect: InspectSpec = Field(default_factory=InspectSpec)
    report: ReportSpec = Field(default_factory=ReportSpec)
    review: ReviewSpec = Field(default_factory=ReviewSpec)
    eval: EvalSpec = Field(default_factory=EvalSpec)


class AgentsInstruction(BaseModel):
    path: str
    scope: str
    content: str


class MemoryHit(BaseModel):
    proposal_id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    repo: str | None = None
    paths: list[str] = Field(default_factory=list)


class ProjectKnowledge(BaseModel):
    repo_name: str
    repo_path: str
    agents_instructions: list[AgentsInstruction] = Field(default_factory=list)
    memory_hits: list[MemoryHit] = Field(default_factory=list)
    missing_agents_md: bool = False


class BriefInput(BaseModel):
    mode: Literal["bug", "feature"]
    text: str
    source: str
    repo_path: str


class BriefState(BaseModel):
    run_id: str
    mode: Literal["bug", "feature"]
    status: Literal["created", "running", "success", "failed"] = "created"
    repo_path: str
    input_source: str
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    agents_files: list[str] = Field(default_factory=list)
    memory_hits: list[MemoryHit] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class ProjectProfile(BaseModel):
    repo_name: str
    repo_path: str
    tech_stack: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    lint_commands: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    key_directories: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    agents_files: list[str] = Field(default_factory=list)
    memory_hit_count: int = 0


class ProfileState(BaseModel):
    run_id: str
    status: Literal["created", "running", "success", "failed"] = "created"
    repo_path: str
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class ReflectState(BaseModel):
    run_id: str
    status: Literal["created", "running", "success", "failed"] = "created"
    repo_path: str
    source_run: str | None = None
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    changed_files: list[str] = Field(default_factory=list)
    workspace_fingerprint: str = ""
    review_snapshot_id: str = ""
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class ReviewFinding(BaseModel):
    severity: Literal["blocker", "major", "minor", "suggestion"] = "minor"
    file: str = ""
    line: int = Field(default=0, ge=0)
    title: str
    evidence: str = ""
    recommendation: str = ""


class ReviewVerdict(BaseModel):
    verdict: Literal["approve", "request_changes", "needs_human"] = "needs_human"
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    checked_items: list[str] = Field(default_factory=list)


class ReviewState(BaseModel):
    run_id: str
    status: Literal["created", "running", "success", "failed", "needs_human"] = "created"
    repo_path: str
    source_run: str
    runner: str = "none"
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    changed_files: list[str] = Field(default_factory=list)
    verdict: Literal["approve", "request_changes", "needs_human"] | None = None
    runner_status: Literal[
        "skipped",
        "success",
        "error",
        "timed_out",
        "stopped",
    ] = "skipped"
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class ScopeGateViolation(BaseModel):
    """精确路径范围门禁命中的单条越界事实。"""

    model_config = ConfigDict(extra="forbid")

    code: Literal["forbidden_path", "outside_allowed_paths", "unsafe_changed_path"]
    path: str
    matched_patterns: list[str] = Field(default_factory=list)


class LoopIterationState(BaseModel):
    iteration: int
    lifecycle: Literal["completed", "interrupted"] = "completed"
    interrupted_step: str | None = None
    interrupted_at: str | None = None
    worker_status: Literal["skipped", "success", "failed", "timed_out", "stopped"] = "skipped"
    reviewer_status: Literal[
        "skipped",
        "success",
        "error",
        "timed_out",
        "stopped",
    ] = "skipped"
    workspace_status: Literal["skipped", "passed", "failed"] = "skipped"
    workspace_new_files_count: int = 0
    scope_gate_status: Literal["skipped", "success", "failed"] = "skipped"
    scope_gate_changed_files: list[str] = Field(default_factory=list)
    scope_gate_violations: list[ScopeGateViolation] = Field(default_factory=list)
    scope_gate_result_sha256: str | None = None
    scope_gate_report_sha256: str | None = None
    scope_gate_post_verification_status: Literal["skipped", "success", "failed"] = "skipped"
    scope_gate_post_verification_changed_files: list[str] = Field(default_factory=list)
    scope_gate_post_verification_violations: list[ScopeGateViolation] = Field(
        default_factory=list
    )
    scope_gate_post_verification_result_sha256: str | None = None
    scope_gate_post_verification_report_sha256: str | None = None
    scope_gate_pre_review_status: Literal["skipped", "success", "failed"] = "skipped"
    scope_gate_pre_review_changed_files: list[str] = Field(default_factory=list)
    scope_gate_pre_review_violations: list[ScopeGateViolation] = Field(default_factory=list)
    scope_gate_pre_review_result_sha256: str | None = None
    scope_gate_pre_review_report_sha256: str | None = None
    verification_status: Literal["skipped", "passed", "failed"] = "skipped"
    verification_failed_count: int = 0
    reflect_run: str | None = None
    risk_gate_status: Literal["skipped", "success", "failed"] = "skipped"
    risk_gate_source_run: str | None = None
    risk_gate_risk: Literal["low", "medium", "high"] | None = None
    risk_gate_recommendation: (
        Literal["self-check", "isolated-review", "human-review"] | None
    ) = None
    risk_gate_result_sha256: str | None = None
    risk_gate_report_sha256: str | None = None
    review_run: str | None = None
    verdict: Literal["approve", "request_changes", "needs_human"] | None = None
    findings_count: int = 0


class SupersededTerminalRecord(BaseModel):
    """记录 recovery 明确作废的旧终态，并与根状态绑定。"""

    model_config = ConfigDict(extra="forbid")

    terminal_event_index: int = Field(ge=0)
    terminal_status: Literal["success", "failed"]
    recovery_id: str = Field(min_length=1)


class LoopAutomationState(BaseModel):
    run_id: str
    status: Literal["created", "running", "success", "failed", "needs_human"] = "created"
    task_mode: Literal["bug", "feature"]
    automation_mode: Literal["assist", "auto"]
    repo_path: str
    input_source: str
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    brief_run: str | None = None
    initial_head_sha: str | None = None
    project_policy_snapshot: dict[str, str | None] = Field(default_factory=dict)
    project_policy_snapshot_sha256: str | None = None
    scope_gate_required: bool = False
    scope_policy_sha256: str | None = None
    verification_artifact_version: Literal[2] | None = None
    current_iteration: int = 0
    max_iterations: int = 2
    iterations: list[LoopIterationState] = Field(default_factory=list)
    last_recovery_id: str | None = None
    superseded_terminal_events: list[SupersededTerminalRecord] = Field(
        default_factory=list
    )
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class GoalContract(BaseModel):
    objective: str
    repo_path: str
    input_source: str
    raw_text: str
    scope_profile: str | None = None
    non_goals: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


GoalCheckpointEvidenceType = Literal["loop", "reflect", "gate", "review", "finish", "manual"]


class GoalCheckpointRef(BaseModel):
    run: str
    type: GoalCheckpointEvidenceType
    note: str | None = None
    kind: str | None = None
    status: str | None = None
    repo_path: str | None = None
    validated: bool = False
    completion_eligible: bool = False
    validation_summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    attached_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class GoalCheckpointRecord(BaseModel):
    checkpoint: str
    status: Literal["planned", "done"] = "planned"
    plan_path: str
    report_path: str | None = None
    refs: list[GoalCheckpointRef] = Field(default_factory=list)
    completed_note: str | None = None
    completed_at: str | None = None
    completion_mode: Literal["validated", "manual_override"] | None = None


class GoalState(BaseModel):
    run_id: str
    status: Literal[
        "created",
        "running",
        "checkpoint_done",
        "paused",
        "stopped",
        "blocked",
        "timeout",
        "stale",
        "needs_human",
        "success",
        "failed",
    ] = "created"
    repo_path: str
    input_source: str
    scope_profile: str | None = None
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    checkpoint_count: int = 0
    checkpoints: list[str] = Field(default_factory=list)
    checkpoint_records: list[GoalCheckpointRecord] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    pause_reason: str | None = None
    paused_from_status: Literal["created", "running", "checkpoint_done"] | None = None
    stop_reason: str | None = None
    recover_reason: str | None = None
    completion_note: str | None = None
    completed_at: str | None = None
    eval_results: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class GateReason(BaseModel):
    code: str
    severity: Literal["low", "medium", "high"]
    message: str
    evidence: str = ""


class GateResult(BaseModel):
    risk: Literal["low", "medium", "high"]
    recommendation: Literal["self-check", "isolated-review", "human-review"]
    reasons: list[GateReason] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    scope_profile: str | None = None


class GateState(BaseModel):
    run_id: str
    status: Literal["created", "running", "success", "failed"] = "created"
    repo_path: str
    source_run: str
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    risk: Literal["low", "medium", "high"] | None = None
    recommendation: Literal["self-check", "isolated-review", "human-review"] | None = None
    scope_profile: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)


class DecisionEntry(BaseModel):
    id: str = Field(default_factory=lambda: f"dec-{uuid4().hex[:12]}")
    run_id: str
    type: Literal["gate", "review", "finish", "memory", "custom"]
    decision: Literal["approved", "rejected"]
    reason: str
    actor: str = "human"
    references: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ChangePlanState(BaseModel):
    run_id: str
    status: Literal["created", "running", "success", "failed"] = "created"
    repo_path: str
    input_source: str
    scope_profile: str | None = None
    current_step: str = "created"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    artifacts: list[str] = Field(default_factory=list)
    eval_results: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        _save_model(path, self)
