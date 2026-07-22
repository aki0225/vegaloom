from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .execution_control import (
    ACTIVE_EXECUTION_STATUSES,
    ExecutionAlreadyExistsError,
    ExecutionLease,
    ExecutionStopLatchedError,
    RunnerExecutionContext,
    hold_run_control_file_lock,
    is_process_alive,
)
from .loop_step_result import hash_command
from .models import GateResult, ReviewFinding, ReviewState, ReviewVerdict
from .parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    PARALLEL_REVIEW_PROMPT_VERSION,
    ParallelReviewAggregate,
    ParallelReviewArtifactDigest,
    ParallelReviewAttemptIdentity,
    ParallelReviewCompatibilityBinding,
    ParallelReviewFinding,
    ParallelReviewPlan,
    ParallelReviewResult,
    ParallelReviewResultRef,
    ReviewEvidenceSnapshot,
    ReviewerRole,
    ReviewExecutionStatus,
    build_parallel_review_attempt_identity,
    build_parallel_review_finding,
    build_parallel_review_result,
)
from .parallel_review_artifacts import (
    ParallelReviewArtifactValidationError,
    build_review_evidence_snapshot_from_artifacts,
    claim_parallel_review_attempt,
    list_parallel_review_result_refs,
    parallel_review_aggregate_artifact_ref,
    parallel_review_execution_artifact_ref,
    parallel_review_plan_artifact_ref,
    parallel_review_result_pointer_artifact_ref,
    prepare_parallel_review_execution_path,
    read_parallel_review_aggregate,
    read_parallel_review_plan,
    read_parallel_review_process_output,
    read_parallel_review_result,
    reconcile_stale_parallel_review_execution,
    sha256_parallel_review_artifact,
    write_parallel_review_execution,
    write_parallel_review_process_output,
    write_parallel_review_public_evidence,
    write_parallel_review_result,
    write_parallel_review_role_prompt,
)
from .prompt_metrics import measure_prompt, render_prompt_metrics
from .redaction import redact_text
from .review_runtime import (
    REVIEW_ARTIFACTS,
    render_eval,
    render_review_findings,
    run_review_pack_eval,
)
from .runner import Runner, RunnerResult, RunnerStatus
from .workspace_check import ReviewWorkspaceSnapshot, capture_review_workspace


_RUNNER_RESULT_FILENAME = "runner-result.json"
_RUNNER_STARTED_FILENAME = "runner-started.json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ROLE_FOCUS = {
    "correctness_reviewer": (
        "检查需求是否真实满足、行为回归、边界条件、错误处理和兼容性。"
        "不要重新裁定已经由机器验证确定的通过或失败事实。"
    ),
    "verification_adequacy_reviewer": (
        "检查测试是否覆盖关键行为、失败路径、回归风险和验收条件。"
        "重点发现缺失或产生虚假信心的验证，不重复执行确定性路由。"
    ),
    "security_design_reviewer": (
        "检查权限、敏感信息、路径边界、并发恢复、外部副作用和架构约束。"
        "只报告能够由当前 evidence 支撑的安全或设计问题。"
    ),
}


class ParallelReviewRuntimeValidationError(ValueError):
    pass


class ParallelReviewAttemptActiveError(ParallelReviewRuntimeValidationError):
    pass


@dataclass(frozen=True)
class _TrustedCompatibilitySource:
    plan: ParallelReviewPlan
    aggregate: ParallelReviewAggregate
    results: tuple[ParallelReviewResult, ...]
    plan_artifact_ref: str
    plan_artifact_sha256: str
    aggregate_artifact_ref: str
    aggregate_artifact_sha256: str
    result_refs: tuple[ParallelReviewResultRef, ...]
    result_pointer_artifacts: tuple[tuple[str, str], ...]


class _ParallelReviewerFindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["blocker", "major", "minor", "suggestion"]
    category: str
    rule_id: str
    path: str
    location: str
    title: str
    evidence: str = ""
    recommendation: str = ""


class _ParallelReviewerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    reviewer_role: ReviewerRole
    review_plan_id: str
    evidence_snapshot_sha256: str
    verdict: Literal["approve", "request_changes", "needs_human"]
    summary: str
    findings: list[_ParallelReviewerFindingPayload] = Field(
        default_factory=list,
        max_length=200,
    )
    checked_items: list[str] = Field(default_factory=list, max_length=100)


class _RunnerResultMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RunnerStatus
    error: str | None = None
    command: list[str] | None = None
    source: Literal["runner", "terminal_execution_recovery"] = "runner"
    execution_sha256: str | None = None
    process_output_sha256: str | None = None
    runner_started_sha256: str | None = None


class _RunnerStartedMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    iteration: int
    review_plan_id: str
    reviewer_role: ReviewerRole
    attempt_id: str
    input_fingerprint: str
    runner_identity: dict[str, str]
    base_head: str
    before_workspace_fingerprint: str
    policy_snapshot_sha256: str
    owner_pid: int = Field(ge=1)
    started_at: str


@dataclass(frozen=True)
class PreparedParallelReviewEvidence:
    snapshot: ReviewEvidenceSnapshot
    public_evidence: str
    public_evidence_sha256: str
    max_prompt_chars: int = 60_000

    def __post_init__(self) -> None:
        if not self.public_evidence.strip():
            raise ValueError("公共 review evidence 不能为空")
        if not _SHA256_PATTERN.fullmatch(self.public_evidence_sha256):
            raise ValueError("public_evidence_sha256 必须是 64 位小写十六进制")
        if self.max_prompt_chars <= 0:
            raise ValueError("max_prompt_chars 必须大于 0")


def prepare_parallel_review_evidence(
    snapshot: ReviewEvidenceSnapshot,
    public_evidence: str,
    *,
    forbidden_markers: Iterable[str] = (),
    max_prompt_chars: int = 60_000,
) -> PreparedParallelReviewEvidence:
    validated_snapshot = ReviewEvidenceSnapshot.model_validate(
        snapshot.model_dump(mode="json")
    )
    normalized = redact_text(public_evidence).replace("\r\n", "\n").replace(
        "\r",
        "\n",
    )
    normalized = normalized.rstrip() + "\n"
    if not normalized.strip():
        raise ParallelReviewRuntimeValidationError(
            "公共 review evidence 不能为空"
        )
    for marker in forbidden_markers:
        if marker and marker in normalized:
            raise ParallelReviewRuntimeValidationError(
                "公共 review evidence 包含禁止进入 Reviewer 的私有标记"
            )
    return PreparedParallelReviewEvidence(
        snapshot=validated_snapshot,
        public_evidence=normalized,
        public_evidence_sha256=_sha256_text(normalized),
        max_prompt_chars=max_prompt_chars,
    )


def render_parallel_review_role_prompt(
    plan: ParallelReviewPlan,
    reviewer_role: ReviewerRole,
    public_evidence_sha256: str,
    *,
    private_canary: str | None = None,
) -> str:
    validated_plan = ParallelReviewPlan.model_validate(
        plan.model_dump(mode="json")
    )
    if reviewer_role not in validated_plan.required_roles:
        raise ParallelReviewRuntimeValidationError(
            "不能为 ReviewPlan 之外的角色生成 prompt"
        )
    public_sha256 = _normalize_sha256(
        public_evidence_sha256,
        "public_evidence_sha256",
    )
    schema = {
        "schema_version": 1,
        "reviewer_role": reviewer_role,
        "review_plan_id": validated_plan.plan_id,
        "evidence_snapshot_sha256": (
            validated_plan.evidence_snapshot_sha256
        ),
        "verdict": "approve | request_changes | needs_human",
        "summary": "本角色结论",
        "findings": [
            {
                "severity": "blocker | major | minor | suggestion",
                "category": "稳定的小写分类",
                "rule_id": "稳定的小写规则标识",
                "path": "仓库相对路径",
                "location": "line:123 或结构化位置",
                "title": "问题标题",
                "evidence": "可复核证据",
                "recommendation": "最小修改建议",
            }
        ],
        "checked_items": ["已检查事项"],
    }
    lines = [
        "# Gate 5 角色化隔离审查",
        "",
        f"- Prompt 版本：`{PARALLEL_REVIEW_PROMPT_VERSION}`",
        f"- Reviewer role：`{reviewer_role}`",
        f"- ReviewPlan：`{validated_plan.plan_id}`",
        f"- Run / iteration：`{validated_plan.run_id}` / `{validated_plan.iteration}`",
        (
            "- Evidence snapshot："
            f"`{validated_plan.evidence_snapshot_sha256}`"
        ),
        f"- 公共 evidence SHA-256：`{public_sha256}`",
        "",
        "## 本角色职责",
        "",
        _ROLE_FOCUS[reviewer_role],
        "",
        "## 硬性边界",
        "",
        "- 只读审查，不修改文件、提交、推送或发布。",
        "- 只使用随后提供的公共 evidence package 和本角色说明。",
        "- 不读取、推测或引用其他 reviewer 的 prompt、输出或私有标记。",
        "- 证据不足时返回 needs_human，不得强行 approve。",
        "- 最终只输出一个 JSON object，不要使用 Markdown 代码块。",
        "- finding 必须提供稳定 category、rule_id、仓库相对路径和结构化位置。",
    ]
    if private_canary:
        lines.extend(
            [
                "",
                "## 本角色私有隔离标记",
                "",
                private_canary,
                "",
                "该标记只属于当前角色上下文，不得传播到其他 reviewer。",
            ]
        )
    lines.extend(
        [
            "",
            "## 输出 Schema",
            "",
            "```json",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "```",
        ]
    )
    return redact_text("\n".join(lines).rstrip() + "\n")


@dataclass(frozen=True)
class RunnerParallelReviewerExecutor:
    reviewer_role: ReviewerRole
    repo_path: Path
    runner: Runner
    evidence: PreparedParallelReviewEvidence
    timeout_seconds: int = 900
    private_canary: str | None = None

    def __post_init__(self) -> None:
        if self.reviewer_role not in AVAILABLE_REVIEWER_ROLES:
            raise ValueError("reviewer_role 不受支持")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")

    def __call__(
        self,
        *,
        run_dir: Path,
        plan: ParallelReviewPlan,
        reviewer_role: ReviewerRole,
    ) -> ParallelReviewResultRef:
        validated_plan = _validate_executor_identity(
            run_dir,
            plan,
            reviewer_role=reviewer_role,
            expected_role=self.reviewer_role,
            evidence=self.evidence,
        )
        _, public_evidence_sha256 = write_parallel_review_public_evidence(
            run_dir,
            validated_plan,
            self.evidence.public_evidence,
        )
        if public_evidence_sha256 != self.evidence.public_evidence_sha256:
            raise ParallelReviewRuntimeValidationError(
                "公共 evidence artifact hash 与准备阶段不一致"
            )
        role_prompt = render_parallel_review_role_prompt(
            validated_plan,
            reviewer_role,
            public_evidence_sha256,
            private_canary=self.private_canary,
        )
        _, role_prompt_sha256 = write_parallel_review_role_prompt(
            run_dir,
            validated_plan,
            reviewer_role=reviewer_role,
            content=role_prompt,
        )
        runner_input = _render_runner_input(
            role_prompt,
            self.evidence.public_evidence,
        )
        attempt = build_parallel_review_attempt_identity(
            validated_plan,
            reviewer_role=reviewer_role,
            public_evidence_sha256=public_evidence_sha256,
            role_prompt_sha256=role_prompt_sha256,
        )
        execution_ref = parallel_review_execution_artifact_ref(
            validated_plan,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
        )
        execution_path = prepare_parallel_review_execution_path(
            run_dir,
            execution_ref=execution_ref,
        )
        attempt_lock_path = execution_path.parent / ".attempt.lock"
        with hold_run_control_file_lock(run_dir, attempt_lock_path):
            for published_ref in list_parallel_review_result_refs(
                run_dir,
                validated_plan,
            ):
                if published_ref.reviewer_role != reviewer_role:
                    continue
                if published_ref.attempt_id != attempt.attempt_id:
                    raise ParallelReviewRuntimeValidationError(
                        "已发布 Reviewer result 不属于当前确定性 attempt"
                    )
                published_execution = _read_execution(execution_path)
                _validate_existing_execution(
                    published_execution,
                    validated_plan,
                    reviewer_role=reviewer_role,
                    attempt=attempt,
                    expected_runner_identity=_runner_identity(
                        self.runner,
                        reviewer_role,
                        public_evidence_sha256=(
                            attempt.public_evidence_sha256
                        ),
                        role_prompt_sha256=attempt.role_prompt_sha256,
                    ),
                    expected_policy_snapshot_sha256=(
                        self.evidence.snapshot.policy_snapshot_sha256
                    ),
                    expected_workspace_fingerprint=(
                        self.evidence.snapshot.workspace_fingerprint
                    ),
                )
                return published_ref
            if execution_path.exists():
                return self._resume_existing_attempt(
                    run_dir,
                    validated_plan,
                    reviewer_role=reviewer_role,
                    execution_ref=execution_ref,
                    execution_path=execution_path,
                    attempt=attempt,
                )

            claim_path = execution_path.with_name("attempt-claim.json")
            claim_existed = claim_path.exists()
            claim_parallel_review_attempt(
                run_dir,
                validated_plan,
                reviewer_role=reviewer_role,
                attempt=attempt,
            )
            runner_started = _read_runner_started_metadata(
                execution_path.parent,
                plan=validated_plan,
                reviewer_role=reviewer_role,
                attempt=attempt,
                expected_runner_identity=_runner_identity(
                    self.runner,
                    reviewer_role,
                    public_evidence_sha256=public_evidence_sha256,
                    role_prompt_sha256=role_prompt_sha256,
                ),
                expected_policy_snapshot_sha256=(
                    self.evidence.snapshot.policy_snapshot_sha256
                ),
                expected_workspace_fingerprint=(
                    self.evidence.snapshot.workspace_fingerprint
                ),
            )
            if runner_started is not None:
                if not claim_existed:
                    raise ParallelReviewRuntimeValidationError(
                        "runner-started.json 缺少先行 attempt claim"
                    )
                if is_process_alive(runner_started.owner_pid):
                    raise ParallelReviewAttemptActiveError(
                        "同一 reviewer attempt 已进入 Runner，"
                        "owner PID 仍存活"
                    )
                return self._publish_unknown_started_attempt(
                    run_dir,
                    validated_plan,
                    reviewer_role=reviewer_role,
                    execution_ref=execution_ref,
                    execution_path=execution_path,
                    attempt=attempt,
                    runner_started=runner_started,
                )

            current_workspace = _capture_workspace(self.repo_path)
            expected_workspace_fingerprint = (
                self.evidence.snapshot.workspace_fingerprint
            )
            actual_workspace_fingerprint = _workspace_fingerprint(
                current_workspace
            )
            if (
                actual_workspace_fingerprint
                != expected_workspace_fingerprint
            ):
                raise ParallelReviewRuntimeValidationError(
                    "当前工作区与 ReviewPlan evidence snapshot 不一致"
                )

            if len(runner_input) > self.evidence.max_prompt_chars:
                runner_result = RunnerResult(
                    status="error",
                    output="",
                    error=(
                        "角色 reviewer prompt 超过上下文预算，"
                        "未启动外部 Runner。"
                    ),
                    command=[],
                )
                _, process_output_sha256, process_output_bytes = (
                    write_parallel_review_process_output(
                        run_dir,
                        execution_ref=execution_ref,
                        content=runner_result.output,
                    )
                )
                execution = _build_synthetic_execution(
                    validated_plan,
                    reviewer_role=reviewer_role,
                    attempt=attempt,
                    runner=self.runner,
                    evidence=self.evidence,
                    runner_result=runner_result,
                    before_workspace=current_workspace,
                    status="failed",
                    process_output_sha256=process_output_sha256,
                    process_output_bytes=process_output_bytes,
                )
                write_parallel_review_execution(
                    run_dir,
                    execution_ref=execution_ref,
                    execution=execution,
                )
                _write_runner_result_metadata(
                    execution_path.parent,
                    runner_result,
                )
                return _write_result_from_execution(
                    run_dir,
                    validated_plan,
                    reviewer_role=reviewer_role,
                    attempt_id=attempt.attempt_id,
                    execution_ref=execution_ref,
                    execution_path=execution_path,
                    execution=execution,
                    runner_result=runner_result,
                    workspace_issue=(
                        "角色 reviewer prompt 超过上下文预算"
                    ),
                )

            _write_runner_started_metadata(
                execution_path.parent,
                _RunnerStartedMetadata(
                    run_id=validated_plan.run_id,
                    iteration=validated_plan.iteration,
                    review_plan_id=validated_plan.plan_id,
                    reviewer_role=reviewer_role,
                    attempt_id=attempt.attempt_id,
                    input_fingerprint=attempt.input_fingerprint,
                    runner_identity=_runner_identity(
                        self.runner,
                        reviewer_role,
                        public_evidence_sha256=public_evidence_sha256,
                        role_prompt_sha256=role_prompt_sha256,
                    ),
                    base_head=current_workspace.head_sha,
                    before_workspace_fingerprint=(
                        actual_workspace_fingerprint
                    ),
                    policy_snapshot_sha256=(
                        self.evidence.snapshot.policy_snapshot_sha256
                    ),
                    owner_pid=max(1, os.getpid()),
                    started_at=datetime.now(UTC).isoformat(),
                ),
            )

        context = RunnerExecutionContext(
            execution_dir=execution_path.parent,
            run_id=validated_plan.run_id,
            step="reviewer",
            iteration=validated_plan.iteration,
            engine="langgraph",
            graph_schema_version="checkpoint-v1",
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            idempotency_key=attempt.idempotency_key,
            replay_class="read_only_replayable",
            runner_identity=_runner_identity(
                self.runner,
                reviewer_role,
                public_evidence_sha256=public_evidence_sha256,
                role_prompt_sha256=role_prompt_sha256,
            ),
            base_head=current_workspace.head_sha,
            before_workspace_fingerprint=actual_workspace_fingerprint,
            policy_snapshot_sha256=(
                self.evidence.snapshot.policy_snapshot_sha256
            ),
            input_fingerprint=attempt.input_fingerprint,
            exclusive_create=True,
        )
        runner_raised = False
        try:
            runner_result = self.runner.run(
                runner_input,
                self.repo_path.resolve(),
                sandbox="read-only",
                timeout_seconds=self.timeout_seconds,
                execution_context=context,
            )
        except ExecutionAlreadyExistsError:
            with hold_run_control_file_lock(
                run_dir,
                attempt_lock_path,
            ):
                return self._resume_existing_attempt(
                    run_dir,
                    validated_plan,
                    reviewer_role=reviewer_role,
                    execution_ref=execution_ref,
                    execution_path=execution_path,
                    attempt=attempt,
                )
        except ExecutionStopLatchedError as exc:
            runner_result = RunnerResult(
                status="stopped",
                output="",
                error=redact_text(str(exc))[:500],
                command=[],
            )
        except Exception as exc:
            runner_raised = True
            runner_result = RunnerResult(
                status="error",
                output="",
                error=(
                    "角色 reviewer Runner 抛出异常："
                    f"{type(exc).__name__}: {redact_text(str(exc))[:500]}"
                ),
                command=[],
            )
        _write_runner_result_metadata(execution_path.parent, runner_result)

        if execution_path.exists():
            execution = _read_execution(execution_path)
        else:
            _, process_output_sha256, process_output_bytes = (
                write_parallel_review_process_output(
                    run_dir,
                    execution_ref=execution_ref,
                    content=runner_result.output,
                )
            )
            execution = _build_missing_execution_evidence(
                validated_plan,
                reviewer_role=reviewer_role,
                attempt=attempt,
                runner=self.runner,
                evidence=self.evidence,
                runner_result=runner_result,
                before_workspace=current_workspace,
                termination_unknown=runner_raised,
                process_output_sha256=process_output_sha256,
                process_output_bytes=process_output_bytes,
            )
            write_parallel_review_execution(
                run_dir,
                execution_ref=execution_ref,
                execution=execution,
            )

        workspace_issue = _post_review_workspace_issue(
            self.repo_path,
            expected_workspace_fingerprint,
        )
        return _write_result_from_execution(
            run_dir,
            validated_plan,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
            execution_ref=execution_ref,
            execution_path=execution_path,
            execution=execution,
            runner_result=runner_result,
            workspace_issue=workspace_issue,
        )

    def _resume_existing_attempt(
        self,
        run_dir: Path,
        plan: ParallelReviewPlan,
        *,
        reviewer_role: ReviewerRole,
        execution_ref: str,
        execution_path: Path,
        attempt: ParallelReviewAttemptIdentity,
    ) -> ParallelReviewResultRef:
        execution = _read_execution(execution_path)
        _validate_existing_execution(
            execution,
            plan,
            reviewer_role=reviewer_role,
            attempt=attempt,
            expected_runner_identity=_runner_identity(
                self.runner,
                reviewer_role,
                public_evidence_sha256=attempt.public_evidence_sha256,
                role_prompt_sha256=attempt.role_prompt_sha256,
            ),
            expected_policy_snapshot_sha256=(
                self.evidence.snapshot.policy_snapshot_sha256
            ),
            expected_workspace_fingerprint=(
                self.evidence.snapshot.workspace_fingerprint
            ),
        )
        if execution.status in ACTIVE_EXECUTION_STATUSES:
            if _execution_has_live_owned_pid(execution):
                raise ParallelReviewAttemptActiveError(
                    "同一 reviewer attempt 仍处于 active 状态，"
                    "owned/child PID 仍存活"
                )
            return self._publish_stale_active_attempt(
                run_dir,
                plan,
                reviewer_role=reviewer_role,
                execution_ref=execution_ref,
                execution_path=execution_path,
                attempt=attempt,
                execution=execution,
            )
        runner_result = _read_or_recover_terminal_runner_result(
            run_dir,
            plan,
            reviewer_role=reviewer_role,
            execution_ref=execution_ref,
            execution_path=execution_path,
            execution=execution,
            attempt=attempt,
            runner=self.runner,
            evidence=self.evidence,
        )
        workspace_issue = _post_review_workspace_issue(
            self.repo_path,
            self.evidence.snapshot.workspace_fingerprint,
        )
        return _write_result_from_execution(
            run_dir,
            plan,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
            execution_ref=execution_ref,
            execution_path=execution_path,
            execution=execution,
            runner_result=runner_result,
            workspace_issue=workspace_issue,
        )

    def _publish_unknown_started_attempt(
        self,
        run_dir: Path,
        plan: ParallelReviewPlan,
        *,
        reviewer_role: ReviewerRole,
        execution_ref: str,
        execution_path: Path,
        attempt: ParallelReviewAttemptIdentity,
        runner_started: _RunnerStartedMetadata,
    ) -> ParallelReviewResultRef:
        reason = (
            "Reviewer runner-started marker 的 owner PID 已消失，"
            "provider 调用终态未知，禁止自动重试。"
        )
        runner_result = _ensure_unknown_runner_result_metadata(
            execution_path.parent,
            reason=reason,
            runner=self.runner,
        )
        _, process_output_sha256, process_output_bytes = (
            write_parallel_review_process_output(
                run_dir,
                execution_ref=execution_ref,
                content=runner_result.output,
            )
        )
        execution = _build_synthetic_execution(
            plan,
            reviewer_role=reviewer_role,
            attempt=attempt,
            runner=self.runner,
            evidence=self.evidence,
            runner_result=runner_result,
            before_workspace=None,
            status="failed",
            termination_unconfirmed=True,
            process_output_sha256=process_output_sha256,
            process_output_bytes=process_output_bytes,
            owner_pid=runner_started.owner_pid,
            started_at=runner_started.started_at,
            base_head=runner_started.base_head,
            before_workspace_fingerprint=(
                runner_started.before_workspace_fingerprint
            ),
        )
        write_parallel_review_execution(
            run_dir,
            execution_ref=execution_ref,
            execution=execution,
        )
        workspace_issue = _post_review_workspace_issue(
            self.repo_path,
            self.evidence.snapshot.workspace_fingerprint,
        )
        return _write_result_from_execution(
            run_dir,
            plan,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
            execution_ref=execution_ref,
            execution_path=execution_path,
            execution=execution,
            runner_result=runner_result,
            workspace_issue=workspace_issue,
        )

    def _publish_stale_active_attempt(
        self,
        run_dir: Path,
        plan: ParallelReviewPlan,
        *,
        reviewer_role: ReviewerRole,
        execution_ref: str,
        execution_path: Path,
        attempt: ParallelReviewAttemptIdentity,
        execution: ExecutionLease,
    ) -> ParallelReviewResultRef:
        reason = (
            "Reviewer active execution 的 owner/child PID 已消失，"
            "provider 调用终态未知，禁止自动重试。"
        )
        process_output_sha256, process_output_bytes = (
            _seal_stale_process_output(
                run_dir,
                execution_ref=execution_ref,
                execution_path=execution_path,
            )
        )
        timestamp = datetime.now(UTC).isoformat()
        recovered_payload = execution.model_dump(mode="json")
        recovered_payload.update(
            {
                "status": "failed",
                "termination_unconfirmed": True,
                "reason": reason,
                "returncode": execution.returncode,
                "process_output_sha256": process_output_sha256,
                "process_output_bytes": process_output_bytes,
                "last_heartbeat": timestamp,
                "lease_expires_at": timestamp,
                "deadline": timestamp,
                "finished_at": timestamp,
            }
        )
        recovered_execution = ExecutionLease.model_validate(
            recovered_payload
        )
        reconcile_stale_parallel_review_execution(
            run_dir,
            execution_ref=execution_ref,
            expected_execution=execution,
            recovered_execution=recovered_execution,
        )
        runner_result = _ensure_unknown_runner_result_metadata(
            execution_path.parent,
            reason=reason,
            runner=self.runner,
        )
        workspace_issue = _post_review_workspace_issue(
            self.repo_path,
            self.evidence.snapshot.workspace_fingerprint,
        )
        return _write_result_from_execution(
            run_dir,
            plan,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
            execution_ref=execution_ref,
            execution_path=execution_path,
            execution=recovered_execution,
            runner_result=runner_result,
            workspace_issue=workspace_issue,
        )


def build_runner_parallel_review_executors(
    *,
    repo_path: Path,
    runners: Mapping[ReviewerRole, Runner],
    evidence: PreparedParallelReviewEvidence,
    timeout_seconds: int = 900,
    private_canaries: Mapping[ReviewerRole, str] | None = None,
) -> dict[ReviewerRole, RunnerParallelReviewerExecutor]:
    missing = [
        role for role in AVAILABLE_REVIEWER_ROLES if role not in runners
    ]
    if missing:
        raise ParallelReviewRuntimeValidationError(
            f"缺少 reviewer Runner：{missing}"
        )
    canaries = private_canaries or {}
    return {
        role: RunnerParallelReviewerExecutor(
            reviewer_role=role,
            repo_path=repo_path,
            runner=runners[role],
            evidence=evidence,
            timeout_seconds=timeout_seconds,
            private_canary=canaries.get(role),
        )
        for role in AVAILABLE_REVIEWER_ROLES
    }


def parallel_review_aggregate_to_legacy_verdict(
    aggregate: ParallelReviewAggregate,
    results: Iterable[ParallelReviewResult],
) -> ReviewVerdict:
    validated_aggregate = ParallelReviewAggregate.model_validate(
        aggregate.model_dump(mode="json")
    )
    validated_results = [
        ParallelReviewResult.model_validate(result.model_dump(mode="json"))
        for result in results
    ]
    by_id = {result.result_id: result for result in validated_results}
    expected_ids = set(validated_aggregate.reviewer_result_ids.values())
    if not expected_ids.issubset(by_id):
        raise ParallelReviewRuntimeValidationError(
            "aggregate 引用的 Reviewer result 不完整"
        )

    full_findings: dict[str, list[tuple[ReviewerRole, ParallelReviewFinding]]] = {}
    for role in AVAILABLE_REVIEWER_ROLES:
        result_id = validated_aggregate.reviewer_result_ids.get(role)
        if result_id is None:
            continue
        for finding in by_id[result_id].findings:
            full_findings.setdefault(finding.finding_id, []).append(
                (role, finding)
            )

    legacy_findings: list[ReviewFinding] = []
    for aggregated in validated_aggregate.findings:
        candidates = full_findings.get(aggregated.finding_id, [])
        if not candidates:
            raise ParallelReviewRuntimeValidationError(
                "aggregate finding 缺少可复核的完整 Reviewer finding"
            )
        _, representative = min(
            candidates,
            key=lambda item: AVAILABLE_REVIEWER_ROLES.index(item[0]),
        )
        legacy_findings.append(
            ReviewFinding(
                severity=aggregated.severity,
                file=representative.normalized_path,
                line=_legacy_line_number(
                    representative.normalized_location
                ),
                title=representative.title,
                evidence=representative.evidence,
                recommendation=representative.recommendation,
            )
        )

    reasons = ", ".join(validated_aggregate.reasons) or "none"
    checked_items = [
        (
            f"{role}: {item}"
            if item
            else role
        )
        for role in AVAILABLE_REVIEWER_ROLES
        for result_id in [
            validated_aggregate.reviewer_result_ids.get(role)
        ]
        if result_id is not None
        for item in (by_id[result_id].checked_items or ["已完成角色审查"])
    ]
    return ReviewVerdict(
        verdict=validated_aggregate.verdict,
        summary=(
            "该 verdict 由 Gate 5 并行 Reviewer aggregate 确定性生成；"
            f"reasons={reasons}。"
        ),
        findings=legacy_findings,
        checked_items=checked_items,
    )


def write_parallel_review_compatibility_run(
    output_dir: Path,
    *,
    repo_path: Path,
    source_run: str,
    aggregate: ParallelReviewAggregate,
    results: Iterable[ParallelReviewResult],
) -> Path:
    """写出旧 Loop 可读取的单一 aggregate-derived Review run 视图。"""

    from .gate_runtime import evaluate_risk
    from .goal_evidence import validate_reflect_evidence_freshness
    from .run_utils import resolve_run_dir

    resolved_output_dir = output_dir.resolve()
    runs_dir = resolved_output_dir.parent
    workspace = runs_dir.parent
    if runs_dir.name != "runs":
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 必须写入 workspace/runs/<run_id>"
        )
    resolved_repo = repo_path.resolve()
    source_dir = resolve_run_dir(workspace, source_run)
    source_run = source_dir.name
    source_freshness = validate_reflect_evidence_freshness(
        workspace,
        resolved_repo,
        source_run,
    )
    if not source_freshness.fresh:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 Reflect 源证据不新鲜："
            + ", ".join(source_freshness.issues)
        )
    source_state = _read_compatibility_source_json(
        source_dir / "state.json",
        "Reflect state",
    )
    source_evidence = _read_compatibility_source_json(
        source_dir / "review-evidence.json",
        "Reflect review evidence",
    )
    source_created_at = source_state.get("created_at")
    if not isinstance(source_created_at, str) or not source_created_at.strip():
        raise ParallelReviewRuntimeValidationError(
            "Reflect state 缺少稳定 created_at"
        )
    current_workspace = _capture_workspace(resolved_repo)
    if current_workspace.fingerprint != (
        source_freshness.trusted_workspace_fingerprint
    ):
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 写入前工作区已偏离 Reflect 源快照"
        )
    trusted_source = _load_trusted_compatibility_source(
        source_dir,
        trusted_workspace_fingerprint=(
            source_freshness.trusted_workspace_fingerprint
        ),
        expected_aggregate=aggregate,
        expected_results=results,
    )
    risk_result = evaluate_risk(
        workspace,
        resolved_repo,
        source_run,
    )
    validated_aggregate = trusted_source.aggregate
    validated_results = trusted_source.results
    verdict = parallel_review_aggregate_to_legacy_verdict(
        validated_aggregate,
        validated_results,
    )
    compatibility_binding = _build_compatibility_binding(
        source_run,
        trusted_source,
    )
    runner_status = _legacy_runner_status(validated_results)
    risk_requires_human = risk_result.recommendation == "human-review"
    state_status = (
        "success"
        if (
            runner_status == "success"
            and verdict.verdict == "approve"
            and not risk_requires_human
        )
        else "needs_human"
    )
    current_step = (
        (
            "risk_gate_needs_human"
            if risk_requires_human
            else "done"
        )
        if runner_status == "success"
        else (
            runner_status
            if runner_status in {"timed_out", "stopped"}
            else "runner_error"
        )
    )
    aggregate_content = json.dumps(
        validated_aggregate.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    aggregate_content_sha256 = _sha256_text(aggregate_content)
    acceptance_evidence = {
        "schema_version": 1,
        "max_chars": max(1, len(aggregate_content)),
        "used_chars": len(aggregate_content),
        "items": [
            {
                "source_path": "parallel-review-aggregate.json",
                "source_kind": "parallel_review_aggregate",
                "source_chars": len(aggregate_content),
                "source_sha256": aggregate_content_sha256,
                "included_chars": len(aggregate_content),
                "included_sha256": aggregate_content_sha256,
                "truncated": False,
                "content": aggregate_content,
            }
        ],
        "omitted_paths": [],
    }
    acceptance_manifest = {
        **{
            key: value
            for key, value in acceptance_evidence.items()
            if key != "items"
        },
        "items": [
            {
                key: value
                for key, value in item.items()
                if key != "content"
            }
            for item in acceptance_evidence["items"]
        ],
    }
    review_context = {
        "repo_path": str(resolved_repo),
        "repo_name": resolved_repo.name,
        "source_run": source_run,
        "source_run_dir": str(source_dir),
        "changed_files": list(source_state.get("changed_files") or []),
        "agents_files": [],
        "memory_hit_count": 0,
        "contains_worker_chat": False,
        "acceptance_evidence": acceptance_manifest,
        "truncated_sections": [],
        "evidence_consistent": True,
        "evidence_issues": [],
        "evidence_diagnostics": [],
        "source_snapshot_id": source_freshness.snapshot_id,
        "source_workspace_fingerprint": (
            source_freshness.trusted_workspace_fingerprint
        ),
        "current_workspace_fingerprint": current_workspace.fingerprint,
        "source_untracked_content_complete": bool(
            source_evidence.get("untracked_content_complete")
        ),
        "current_untracked_content_complete": (
            current_workspace.untracked_content_complete
        ),
        "reviewer_start_workspace_fingerprint": current_workspace.fingerprint,
        "reviewer_end_workspace_fingerprint": current_workspace.fingerprint,
        "workspace_changed_during_review": False,
        "review_execution_issues": [],
        "risk_gate": {
            "source_run": source_run,
            "status": "success",
            "result": risk_result.model_dump(mode="json"),
        },
        "human_approval": None,
        "parallel_review_source": compatibility_binding.model_dump(
            mode="json"
        ),
    }
    review_prompt = (
        "# Parallel Review 兼容 Prompt\n\n"
        "- 该兼容视图由可信 aggregate 确定性生成。\n"
        "- 不包含 worker 的完整聊天记录，也不复制 Reviewer 私有输出。\n"
        f"- aggregate：`{validated_aggregate.aggregate_sha256}`\n"
    )
    prompt_metrics = measure_prompt(
        review_prompt,
        role="reviewer",
        max_chars=max(1, len(review_prompt)),
        sections={"aggregate_provenance": aggregate_content},
    )
    eval_results = _compatibility_eval_results(
        risk_requires_human=risk_requires_human,
    )
    state = ReviewState(
        run_id=resolved_output_dir.name,
        status=state_status,
        repo_path=str(resolved_repo),
        source_run=source_run,
        runner="parallel-review",
        current_step=current_step,
        created_at=source_created_at,
        changed_files=list(source_state.get("changed_files") or []),
        verdict=verdict.verdict,
        runner_status=runner_status,
        artifacts=list(REVIEW_ARTIFACTS),
        eval_results=eval_results,
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    _write_create_once(
        resolved_output_dir / "review-verdict.json",
        verdict.model_dump_json(indent=2) + "\n",
    )
    _write_create_once(
        resolved_output_dir / "review-findings.md",
        render_review_findings(verdict),
    )
    _write_create_once(
        resolved_output_dir / "review-runner-output.txt",
        _render_aggregate_runner_output(
            validated_aggregate,
            validated_results,
        ),
    )
    _write_create_once(
        resolved_output_dir / "review-pack.md",
        (
            "# Parallel Review Compatibility Pack\n\n"
            + _render_aggregate_runner_output(
                validated_aggregate,
                validated_results,
            )
            + "\n"
            + render_review_findings(verdict)
        ),
    )
    _write_create_once(
        resolved_output_dir / "review-prompt.md",
        review_prompt,
    )
    _write_create_once(
        resolved_output_dir / "review-checklist.md",
        (
            "# Review Checklist\n\n"
            "- [x] aggregate identity 已绑定。\n"
            "- [x] Reviewer execution 结果已聚合。\n"
            "- [x] 旧 Loop 证据链已生成。\n"
        ),
    )
    _write_create_once(
        resolved_output_dir / "review-context.json",
        json.dumps(
            review_context,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _write_create_once(
        resolved_output_dir / "project-context.md",
        (
            "# Project Context\n\n"
            f"- 仓库：`{resolved_repo.name}`\n"
            f"- Reflect source：`{source_run}`\n"
            f"- Aggregate：`{validated_aggregate.aggregate_sha256}`\n"
        ),
    )
    _write_create_once(
        resolved_output_dir / "acceptance-evidence.md",
        (
            "# Acceptance Evidence\n\n"
            "- 来源：可信 ParallelReviewAggregate。\n"
            f"- SHA-256：`{aggregate_content_sha256}`\n"
        ),
    )
    _write_create_once(
        resolved_output_dir / "acceptance-evidence.json",
        json.dumps(
            acceptance_evidence,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _write_create_once(
        resolved_output_dir / "review-prompt-metrics.json",
        prompt_metrics.model_dump_json(indent=2) + "\n",
    )
    _write_create_once(
        resolved_output_dir / "review-prompt-metrics.md",
        render_prompt_metrics(prompt_metrics),
    )
    trace_lines = [
        {
            "event": "review_started",
            "run_id": state.run_id,
            "source_run": source_run,
            "runner": "parallel-review",
        },
        {
            "event": "run_finished",
            "run_id": state.run_id,
            "status": state.status,
            "verdict": verdict.verdict,
        },
    ]
    _write_create_once(
        resolved_output_dir / "trace.jsonl",
        "\n".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in trace_lines
        )
        + "\n",
    )
    _write_create_once(
        resolved_output_dir / "eval.md",
        render_eval(eval_results),
    )
    _write_create_once(
        resolved_output_dir / "state.json",
        state.model_dump_json(indent=2) + "\n",
    )
    actual_eval_results = run_review_pack_eval(
        resolved_output_dir,
        REVIEW_ARTIFACTS,
    )
    if actual_eval_results != eval_results:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 未通过旧 eval reader"
        )
    return resolved_output_dir


def _compatibility_eval_results(
    *,
    risk_requires_human: bool,
) -> list[str]:
    results = [
        f"PASS: artifact 存在：{artifact}"
        for artifact in REVIEW_ARTIFACTS
    ]
    results.extend(
        [
            "PASS: review prompt 标记不包含 worker 聊天",
            "PASS: review prompt 未超过上下文预算",
            "PASS: acceptance evidence 来源、哈希、截断和字符预算可校验",
            "PASS: review evidence 未截断",
            "PASS: review 证据与当前工作区属于同一快照",
            "PASS: parallel review source evidence 可回溯",
            (
                "FAIL: review 风险门禁要求人工审查"
                if risk_requires_human
                else "PASS: review 风险门禁允许隔离审查"
            ),
        ]
    )
    return results


def _read_compatibility_source_json(
    path: Path,
    label: str,
) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ParallelReviewRuntimeValidationError(
            f"{label} 无法解析"
        ) from exc
    if not isinstance(payload, dict):
        raise ParallelReviewRuntimeValidationError(
            f"{label} 必须是 JSON object"
        )
    return payload


def _load_optional_compatibility_object(
    path: Path,
) -> tuple[dict[str, object] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None, "invalid"
    if not isinstance(payload, dict):
        return None, "invalid"
    return payload, None


def validate_parallel_review_compatibility_source(
    review_dir: Path,
    *,
    expected_source_run: str | None = None,
) -> tuple[str, ...] | None:
    """重放 Compatibility run 的 Gate 5 来源；普通 Review run 返回 None。"""

    resolved_review_dir = review_dir.resolve()
    state_payload, state_issue = _load_optional_compatibility_object(
        resolved_review_dir / "state.json"
    )
    context, context_issue = _load_optional_compatibility_object(
        resolved_review_dir / "review-context.json"
    )
    if not _is_parallel_review_compatibility_payload(
        resolved_review_dir,
        state_payload or {},
        context or {},
        expected_source_run=expected_source_run,
    ):
        return None
    if state_issue is not None:
        return ("parallel_review_state_invalid",)
    if context_issue is not None:
        return ("parallel_review_context_invalid",)
    assert state_payload is not None
    assert context is not None

    issues: list[str] = []
    try:
        state = ReviewState.model_validate(state_payload)
    except ValidationError:
        return ("parallel_review_state_invalid",)
    try:
        verdict_payload = _read_compatibility_source_json(
            resolved_review_dir / "review-verdict.json",
            "Compatibility Review verdict",
        )
        recorded_verdict = ReviewVerdict.model_validate(verdict_payload)
    except (ParallelReviewRuntimeValidationError, ValidationError):
        return ("parallel_review_legacy_verdict_invalid",)
    try:
        binding = ParallelReviewCompatibilityBinding.model_validate(
            context.get("parallel_review_source")
        )
    except ValidationError:
        return ("parallel_review_binding_invalid",)

    if state.runner != "parallel-review":
        issues.append("parallel_review_runner_identity_mismatch")
    if state.run_id != resolved_review_dir.name:
        issues.append("parallel_review_run_id_mismatch")
    if (
        state.source_run != binding.source_run
        or context.get("source_run") != binding.source_run
    ):
        issues.append("parallel_review_source_run_mismatch")

    runs_dir = resolved_review_dir.parent
    if runs_dir.name != "runs":
        return (*issues, "parallel_review_layout_invalid")
    workspace = runs_dir.parent
    try:
        from .run_utils import resolve_run_dir

        source_dir = resolve_run_dir(workspace, binding.source_run)
    except (FileNotFoundError, ValueError):
        return (*issues, "parallel_review_source_unavailable")
    if source_dir.name != binding.source_run:
        issues.append("parallel_review_source_run_mismatch")
    declared_source_dir = context.get("source_run_dir")
    if (
        not isinstance(declared_source_dir, str)
        or _normalized_path(declared_source_dir)
        != _normalized_path(source_dir)
    ):
        issues.append("parallel_review_source_dir_mismatch")

    context_repo_path = context.get("repo_path")
    if (
        not isinstance(context_repo_path, str)
        or _normalized_path(context_repo_path)
        != _normalized_path(state.repo_path)
    ):
        issues.append("parallel_review_repo_mismatch")
    try:
        source_state = _read_compatibility_source_json(
            source_dir / "state.json",
            "Compatibility Reflect state",
        )
    except ParallelReviewRuntimeValidationError:
        return (*issues, "parallel_review_source_state_invalid")
    source_repo_path = source_state.get("repo_path")
    if (
        not isinstance(source_repo_path, str)
        or _normalized_path(source_repo_path)
        != _normalized_path(state.repo_path)
    ):
        issues.append("parallel_review_source_repo_mismatch")

    trusted_workspace_fingerprint = context.get(
        "source_workspace_fingerprint"
    )
    if not _is_sha256(trusted_workspace_fingerprint):
        return (*issues, "parallel_review_workspace_fingerprint_invalid")

    try:
        aggregate = read_parallel_review_aggregate(
            source_dir,
            iteration=binding.iteration,
            plan_id=binding.review_plan_id,
            artifact_sha256=binding.aggregate_artifact.artifact_sha256,
        )
        if (
            parallel_review_aggregate_artifact_ref(aggregate)
            != binding.aggregate_artifact.artifact_ref
        ):
            issues.append("parallel_review_aggregate_ref_mismatch")
        disk_results = tuple(
            read_parallel_review_result(source_dir, result_ref)
            for result_ref in binding.result_artifacts
        )
        trusted_source = _load_trusted_compatibility_source(
            source_dir,
            trusted_workspace_fingerprint=trusted_workspace_fingerprint,
            expected_aggregate=aggregate,
            expected_results=disk_results,
        )
    except (
        OSError,
        ParallelReviewArtifactValidationError,
        ParallelReviewRuntimeValidationError,
        ValidationError,
        ValueError,
    ):
        return (*issues, "parallel_review_source_artifact_invalid")

    expected_binding = _build_compatibility_binding(
        source_dir.name,
        trusted_source,
    )
    if binding != expected_binding:
        issues.append("parallel_review_binding_mismatch")

    derived_verdict = parallel_review_aggregate_to_legacy_verdict(
        trusted_source.aggregate,
        trusted_source.results,
    )
    derived_runner_status = _legacy_runner_status(
        trusted_source.results
    )
    if recorded_verdict != derived_verdict:
        issues.append("parallel_review_legacy_verdict_mismatch")
    if state.verdict != derived_verdict.verdict:
        issues.append("parallel_review_state_verdict_mismatch")
    if state.runner_status != derived_runner_status:
        issues.append("parallel_review_runner_status_mismatch")

    risk_gate = context.get("risk_gate")
    try:
        if (
            not isinstance(risk_gate, dict)
            or risk_gate.get("source_run") != binding.source_run
            or risk_gate.get("status") != "success"
        ):
            raise ValueError("risk gate identity invalid")
        risk_result = GateResult.model_validate(risk_gate.get("result"))
    except (ValidationError, ValueError):
        return (*issues, "parallel_review_risk_gate_invalid")
    risk_requires_human = (
        risk_result.recommendation == "human-review"
    )
    expected_state_status = (
        "success"
        if (
            derived_runner_status == "success"
            and derived_verdict.verdict == "approve"
            and not risk_requires_human
        )
        else "needs_human"
    )
    expected_current_step = (
        (
            "risk_gate_needs_human"
            if risk_requires_human
            else "done"
        )
        if derived_runner_status == "success"
        else (
            derived_runner_status
            if derived_runner_status in {"timed_out", "stopped"}
            else "runner_error"
        )
    )
    if state.status != expected_state_status:
        issues.append("parallel_review_state_status_mismatch")
    if state.current_step != expected_current_step:
        issues.append("parallel_review_current_step_mismatch")
    return tuple(dict.fromkeys(issues))


def _is_parallel_review_compatibility_payload(
    review_dir: Path,
    state: Mapping[str, object],
    context: Mapping[str, object],
    *,
    expected_source_run: str | None,
) -> bool:
    if (
        state.get("runner") == "parallel-review"
        or "parallel_review_source" in context
        or any(
            str(key).startswith("parallel_review_")
            for key in context
        )
    ):
        return True
    try:
        acceptance = _read_compatibility_source_json(
            review_dir / "acceptance-evidence.json",
            "Compatibility acceptance evidence",
        )
    except ParallelReviewRuntimeValidationError:
        acceptance = {}
    items = acceptance.get("items")
    if isinstance(items, list) and any(
        isinstance(item, dict)
        and item.get("source_kind") == "parallel_review_aggregate"
        for item in items
    ):
        return True
    source_runs = {
        source_run
        for source_run in (
            expected_source_run,
            state.get("source_run"),
            context.get("source_run"),
        )
        if isinstance(source_run, str) and source_run.strip()
    }
    return any(
        _source_run_contains_parallel_review_aggregate(
            review_dir,
            source_run,
        )
        for source_run in source_runs
    )


def _source_run_contains_parallel_review_aggregate(
    review_dir: Path,
    source_run: str,
) -> bool:
    runs_dir = review_dir.parent
    if runs_dir.name != "runs":
        return False
    try:
        from .run_utils import resolve_run_dir

        source_dir = resolve_run_dir(runs_dir.parent, source_run)
    except (FileNotFoundError, ValueError):
        return False
    return any(
        path.is_file() and not path.is_symlink()
        for path in source_dir.glob(
            "iterations/*/parallel-reviews/p-*/aggregate.json"
        )
    )


def _build_compatibility_binding(
    source_run: str,
    trusted_source: _TrustedCompatibilitySource,
) -> ParallelReviewCompatibilityBinding:
    return ParallelReviewCompatibilityBinding(
        source_run=source_run,
        iteration=trusted_source.plan.iteration,
        review_plan_id=trusted_source.plan.plan_id,
        evidence_snapshot_sha256=(
            trusted_source.plan.evidence_snapshot_sha256
        ),
        plan_artifact=ParallelReviewArtifactDigest(
            artifact_ref=trusted_source.plan_artifact_ref,
            artifact_sha256=trusted_source.plan_artifact_sha256,
        ),
        result_artifacts=list(trusted_source.result_refs),
        result_pointer_artifacts=[
            ParallelReviewArtifactDigest(
                artifact_ref=artifact_ref,
                artifact_sha256=artifact_sha256,
            )
            for artifact_ref, artifact_sha256 in (
                trusted_source.result_pointer_artifacts
            )
        ],
        aggregate_artifact=ParallelReviewArtifactDigest(
            artifact_ref=trusted_source.aggregate_artifact_ref,
            artifact_sha256=trusted_source.aggregate_artifact_sha256,
        ),
        aggregate_sha256=trusted_source.aggregate.aggregate_sha256,
    )


def _load_trusted_compatibility_source(
    source_dir: Path,
    *,
    trusted_workspace_fingerprint: str,
    expected_aggregate: ParallelReviewAggregate,
    expected_results: Iterable[ParallelReviewResult],
) -> _TrustedCompatibilitySource:
    """从 source run 的磁盘工件封存兼容视图的唯一权威输入。"""

    try:
        expected_aggregate = ParallelReviewAggregate.model_validate(
            expected_aggregate.model_dump(mode="json")
        )
        expected_results = tuple(
            ParallelReviewResult.model_validate(
                result.model_dump(mode="json")
            )
            for result in expected_results
        )
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 expected aggregate/results 不合法"
        ) from exc
    if expected_aggregate.run_id != source_dir.name:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 aggregate 不属于指定 source run"
        )
    expected_results_by_id = {
        result.result_id: result for result in expected_results
    }
    if len(expected_results_by_id) != len(expected_results):
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 expected results 存在重复 result_id"
        )

    try:
        plan = read_parallel_review_plan(
            source_dir,
            iteration=expected_aggregate.iteration,
            plan_id=expected_aggregate.review_plan_id,
        )
        snapshot = build_review_evidence_snapshot_from_artifacts(
            source_dir,
            iteration=plan.iteration,
            workspace_fingerprint=(
                f"sha256:{trusted_workspace_fingerprint}"
            ),
            policy_snapshot_ref="project-policy-snapshot.json",
            verification_result_ref=(
                f"iterations/{plan.iteration:02d}/"
                "verification-result.json"
            ),
            risk_result_ref=(
                f"iterations/{plan.iteration:02d}/"
                "risk-gate-result.json"
            ),
            acceptance_evidence_manifest_ref=(
                f"iterations/{plan.iteration:02d}/"
                "acceptance-evidence.json"
            ),
        )
        if snapshot.evidence_snapshot_sha256 != plan.evidence_snapshot_sha256:
            raise ParallelReviewRuntimeValidationError(
                "ReviewPlan 绑定的 evidence snapshot 已过期"
            )

        pointer_artifacts = tuple(
            (
                pointer_ref,
                sha256_parallel_review_artifact(
                    source_dir,
                    pointer_ref,
                ),
            )
            for role in plan.required_roles
            for pointer_ref in [
                parallel_review_result_pointer_artifact_ref(plan, role)
            ]
        )
        result_refs = list_parallel_review_result_refs(source_dir, plan)
        disk_results = tuple(
            read_parallel_review_result(source_dir, result_ref)
            for result_ref in result_refs
        )
        aggregate = read_parallel_review_aggregate(
            source_dir,
            iteration=plan.iteration,
            plan_id=plan.plan_id,
        )
        plan_artifact_ref = parallel_review_plan_artifact_ref(plan)
        plan_artifact_sha256 = sha256_parallel_review_artifact(
            source_dir,
            plan_artifact_ref,
        )
        aggregate_artifact_ref = parallel_review_aggregate_artifact_ref(
            aggregate
        )
        aggregate_artifact_sha256 = sha256_parallel_review_artifact(
            source_dir,
            aggregate_artifact_ref,
        )
        aggregate = read_parallel_review_aggregate(
            source_dir,
            iteration=plan.iteration,
            plan_id=plan.plan_id,
            artifact_sha256=aggregate_artifact_sha256,
        )
    except ParallelReviewRuntimeValidationError:
        raise
    except (ParallelReviewArtifactValidationError, OSError) as exc:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 source artifact 证据链不可信"
        ) from exc

    disk_results_by_id = {
        result.result_id: result for result in disk_results
    }
    disk_result_ids_by_role = {
        result.reviewer_role: result.result_id
        for result in disk_results
    }
    if (
        len(result_refs) != len(plan.required_roles)
        or set(disk_result_ids_by_role) != set(plan.required_roles)
    ):
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 缺少 ReviewPlan 必需的磁盘 result pointer"
        )
    if aggregate != expected_aggregate:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 expected aggregate 与磁盘 artifact 不一致"
        )
    if disk_results_by_id != expected_results_by_id:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 expected results 与磁盘 artifact 不一致"
        )
    if (
        aggregate.observed_result_ids
        != sorted(disk_results_by_id)
        or aggregate.reviewer_result_ids != disk_result_ids_by_role
    ):
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 aggregate 未精确绑定磁盘 result 集合"
        )

    try:
        confirmed_plan = read_parallel_review_plan(
            source_dir,
            iteration=plan.iteration,
            plan_id=plan.plan_id,
        )
        confirmed_snapshot = build_review_evidence_snapshot_from_artifacts(
            source_dir,
            iteration=plan.iteration,
            workspace_fingerprint=(
                f"sha256:{trusted_workspace_fingerprint}"
            ),
            policy_snapshot_ref="project-policy-snapshot.json",
            verification_result_ref=(
                f"iterations/{plan.iteration:02d}/"
                "verification-result.json"
            ),
            risk_result_ref=(
                f"iterations/{plan.iteration:02d}/"
                "risk-gate-result.json"
            ),
            acceptance_evidence_manifest_ref=(
                f"iterations/{plan.iteration:02d}/"
                "acceptance-evidence.json"
            ),
        )
        confirmed_result_refs = list_parallel_review_result_refs(
            source_dir,
            plan,
        )
        confirmed_results = tuple(
            read_parallel_review_result(source_dir, result_ref)
            for result_ref in confirmed_result_refs
        )
        confirmed_aggregate = read_parallel_review_aggregate(
            source_dir,
            iteration=plan.iteration,
            plan_id=plan.plan_id,
            artifact_sha256=aggregate_artifact_sha256,
        )
    except ParallelReviewArtifactValidationError as exc:
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 source artifact 在封存期间发生漂移"
        ) from exc
    if (
        confirmed_plan != plan
        or confirmed_snapshot != snapshot
        or confirmed_result_refs != result_refs
        or confirmed_results != disk_results
        or confirmed_aggregate != aggregate
    ):
        raise ParallelReviewRuntimeValidationError(
            "兼容 Review run 的 source artifact 在封存期间发生漂移"
        )

    return _TrustedCompatibilitySource(
        plan=plan,
        aggregate=aggregate,
        results=disk_results,
        plan_artifact_ref=plan_artifact_ref,
        plan_artifact_sha256=plan_artifact_sha256,
        aggregate_artifact_ref=aggregate_artifact_ref,
        aggregate_artifact_sha256=aggregate_artifact_sha256,
        result_refs=result_refs,
        result_pointer_artifacts=pointer_artifacts,
    )


def _validate_executor_identity(
    run_dir: Path,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    expected_role: ReviewerRole,
    evidence: PreparedParallelReviewEvidence,
) -> ParallelReviewPlan:
    validated = ParallelReviewPlan.model_validate(plan.model_dump(mode="json"))
    if run_dir.name != validated.run_id:
        raise ParallelReviewRuntimeValidationError(
            "ReviewPlan run_id 与 run 目录不一致"
        )
    if reviewer_role != expected_role:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer executor 与调度角色不一致"
        )
    if reviewer_role not in validated.required_roles:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer executor 不能执行 ReviewPlan 之外的角色"
        )
    snapshot = evidence.snapshot
    if (
        snapshot.run_id != validated.run_id
        or snapshot.iteration != validated.iteration
        or snapshot.evidence_snapshot_sha256
        != validated.evidence_snapshot_sha256
    ):
        raise ParallelReviewRuntimeValidationError(
            "公共 evidence snapshot 与 ReviewPlan identity 不一致"
        )
    return validated


def _render_runner_input(role_prompt: str, public_evidence: str) -> str:
    return (
        role_prompt.rstrip()
        + "\n\n---\n\n# 公共 Evidence Package\n\n"
        + public_evidence.rstrip()
        + "\n"
    )


def _runner_identity(
    runner: Runner,
    reviewer_role: ReviewerRole,
    *,
    public_evidence_sha256: str,
    role_prompt_sha256: str,
) -> dict[str, str]:
    identity = {
        "kind": type(runner).__name__,
        "role": reviewer_role,
        "prompt_version": PARALLEL_REVIEW_PROMPT_VERSION,
        "public_evidence_sha256": public_evidence_sha256,
        "role_prompt_sha256": role_prompt_sha256,
    }
    execution_identity = getattr(runner, "execution_identity", None)
    if callable(execution_identity):
        try:
            provided = execution_identity("read-only")
        except Exception:
            provided = None
        if isinstance(provided, Mapping):
            for key, value in provided.items():
                identity[str(key)] = redact_text(str(value))
    identity["role"] = reviewer_role
    return identity


def _capture_workspace(repo_path: Path) -> ReviewWorkspaceSnapshot:
    try:
        return capture_review_workspace(repo_path.resolve())
    except Exception as exc:
        raise ParallelReviewRuntimeValidationError(
            "无法捕获 Reviewer 工作区快照："
            f"{type(exc).__name__}"
        ) from exc


def _workspace_fingerprint(snapshot: ReviewWorkspaceSnapshot) -> str:
    return f"sha256:{snapshot.fingerprint}"


def _post_review_workspace_issue(
    repo_path: Path,
    expected_fingerprint: str,
) -> str | None:
    try:
        observed = _workspace_fingerprint(
            capture_review_workspace(repo_path.resolve())
        )
    except Exception as exc:
        return f"Reviewer 执行后无法捕获工作区快照：{type(exc).__name__}"
    if observed != expected_fingerprint:
        return "Reviewer 执行期间工作区发生变化"
    return None


def _build_missing_execution_evidence(
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
    runner: Runner,
    evidence: PreparedParallelReviewEvidence,
    runner_result: RunnerResult,
    before_workspace: ReviewWorkspaceSnapshot,
    termination_unknown: bool,
    process_output_sha256: str,
    process_output_bytes: int,
) -> ExecutionLease:
    if runner_result.status == "stopped":
        status: Literal["failed", "running", "stopped"] = "stopped"
        termination_unconfirmed = False
    elif termination_unknown:
        status = "failed"
        termination_unconfirmed = True
    elif runner_result.status in {"error", "skipped"}:
        status = "failed"
        termination_unconfirmed = False
    else:
        status = "running"
        termination_unconfirmed = True
    return _build_synthetic_execution(
        plan,
        reviewer_role=reviewer_role,
        attempt=attempt,
        runner=runner,
        evidence=evidence,
        runner_result=runner_result,
        before_workspace=before_workspace,
        status=status,
        termination_unconfirmed=termination_unconfirmed,
        process_output_sha256=process_output_sha256,
        process_output_bytes=process_output_bytes,
    )


def _build_synthetic_execution(
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
    runner: Runner,
    evidence: PreparedParallelReviewEvidence,
    runner_result: RunnerResult,
    before_workspace: ReviewWorkspaceSnapshot | None,
    status: Literal["failed", "running", "stopped"],
    process_output_sha256: str,
    process_output_bytes: int,
    termination_unconfirmed: bool = False,
    owner_pid: int | None = None,
    started_at: str | None = None,
    base_head: str | None = None,
    before_workspace_fingerprint: str | None = None,
) -> ExecutionLease:
    timestamp = datetime.now(UTC).isoformat()
    resolved_base_head = (
        before_workspace.head_sha
        if before_workspace is not None
        else base_head
    )
    resolved_workspace_fingerprint = (
        _workspace_fingerprint(before_workspace)
        if before_workspace is not None
        else before_workspace_fingerprint
    )
    if (
        not resolved_base_head
        or not resolved_workspace_fingerprint
    ):
        raise ParallelReviewRuntimeValidationError(
            "synthetic execution 缺少启动时 workspace identity"
        )
    command = runner_result.command or [type(runner).__name__]
    return ExecutionLease(
        run_id=plan.run_id,
        step="reviewer",
        iteration=plan.iteration,
        engine="langgraph",
        graph_schema_version="checkpoint-v1",
        step_id=attempt.step_id,
        attempt_id=attempt.attempt_id,
        idempotency_key=attempt.idempotency_key,
        replay_class="read_only_replayable",
        runner_identity={
            **_runner_identity(
                runner,
                reviewer_role,
                public_evidence_sha256=attempt.public_evidence_sha256,
                role_prompt_sha256=attempt.role_prompt_sha256,
            ),
            "synthetic_execution": "true",
        },
        base_head=resolved_base_head,
        before_workspace_fingerprint=resolved_workspace_fingerprint,
        policy_snapshot_sha256=(
            evidence.snapshot.policy_snapshot_sha256
        ),
        input_fingerprint=attempt.input_fingerprint,
        command_sha256=hash_command(command),
        process_output_sha256=process_output_sha256,
        process_output_bytes=process_output_bytes,
        owner_pid=owner_pid or max(1, os.getpid()),
        child_pid=None,
        termination_unconfirmed=termination_unconfirmed,
        command=command,
        started_at=started_at or timestamp,
        last_heartbeat=timestamp,
        lease_expires_at=timestamp,
        deadline=timestamp,
        status=status,
        reason=runner_result.error,
        returncode=1 if status == "failed" else None,
        finished_at=(
            timestamp
            if status in {"failed", "stopped"}
            else None
        ),
    )


def _read_execution(path: Path) -> ExecutionLease:
    try:
        return ExecutionLease.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as exc:
        raise ParallelReviewRuntimeValidationError(
            "真实 Reviewer execution.json 不可信"
        ) from exc


def _validate_existing_execution(
    execution: ExecutionLease,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
    expected_runner_identity: Mapping[str, str],
    expected_policy_snapshot_sha256: str,
    expected_workspace_fingerprint: str,
) -> None:
    actual_runner_identity = dict(execution.runner_identity)
    synthetic_execution = actual_runner_identity.pop(
        "synthetic_execution",
        None,
    )
    if (
        execution.run_id != plan.run_id
        or execution.iteration != plan.iteration
        or execution.step != "reviewer"
        or execution.engine != "langgraph"
        or execution.graph_schema_version != "checkpoint-v1"
        or execution.step_id != attempt.step_id
        or execution.attempt_id != attempt.attempt_id
        or execution.idempotency_key != attempt.idempotency_key
        or execution.input_fingerprint != attempt.input_fingerprint
        or execution.replay_class != "read_only_replayable"
        or actual_runner_identity != dict(expected_runner_identity)
        or synthetic_execution not in {None, "true"}
        or execution.before_workspace_fingerprint
        != expected_workspace_fingerprint
        or execution.policy_snapshot_sha256
        != expected_policy_snapshot_sha256
        or execution.command_sha256 != hash_command(execution.command)
    ):
        raise ParallelReviewRuntimeValidationError(
            "已有 Reviewer execution identity 与当前 attempt 不一致"
        )


def _write_runner_started_metadata(
    execution_dir: Path,
    metadata: _RunnerStartedMetadata,
) -> None:
    content = redact_text(
        metadata.model_dump_json(indent=2) + "\n"
    ).replace("\r\n", "\n").replace("\r", "\n")
    _write_runner_result_create_once(
        execution_dir / _RUNNER_STARTED_FILENAME,
        content.rstrip() + "\n",
        label=_RUNNER_STARTED_FILENAME,
        temp_prefix=".rs-",
    )


def _read_runner_started_metadata(
    execution_dir: Path,
    *,
    plan: ParallelReviewPlan,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
    expected_runner_identity: Mapping[str, str],
    expected_policy_snapshot_sha256: str,
    expected_workspace_fingerprint: str,
) -> _RunnerStartedMetadata | None:
    metadata_path = execution_dir / _RUNNER_STARTED_FILENAME
    if not metadata_path.exists():
        return None
    try:
        metadata = _RunnerStartedMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        started_at = datetime.fromisoformat(metadata.started_at)
    except (OSError, ValidationError, ValueError) as exc:
        raise ParallelReviewRuntimeValidationError(
            "runner-started.json 不可信"
        ) from exc
    if started_at.tzinfo is None:
        raise ParallelReviewRuntimeValidationError(
            "runner-started.json 的 started_at 缺少时区"
        )
    if (
        metadata.run_id != plan.run_id
        or metadata.iteration != plan.iteration
        or metadata.review_plan_id != plan.plan_id
        or metadata.reviewer_role != reviewer_role
        or metadata.attempt_id != attempt.attempt_id
        or metadata.input_fingerprint != attempt.input_fingerprint
        or metadata.runner_identity != dict(expected_runner_identity)
        or metadata.policy_snapshot_sha256
        != expected_policy_snapshot_sha256
        or metadata.before_workspace_fingerprint
        != expected_workspace_fingerprint
        or not metadata.base_head.strip()
    ):
        raise ParallelReviewRuntimeValidationError(
            "runner-started.json identity 与当前 attempt 不一致"
        )
    return metadata


def _write_runner_result_metadata(
    execution_dir: Path,
    result: RunnerResult,
    *,
    source: Literal["runner", "terminal_execution_recovery"] = "runner",
    execution_sha256: str | None = None,
    process_output_sha256: str | None = None,
    runner_started_sha256: str | None = None,
) -> None:
    metadata = _RunnerResultMetadata(
        status=result.status,
        error=result.error,
        command=result.command,
        source=source,
        execution_sha256=execution_sha256,
        process_output_sha256=process_output_sha256,
        runner_started_sha256=runner_started_sha256,
    )
    content = redact_text(
        metadata.model_dump_json(indent=2) + "\n"
    ).replace("\r\n", "\n").replace("\r", "\n")
    _write_runner_result_create_once(
        execution_dir / _RUNNER_RESULT_FILENAME,
        content.rstrip() + "\n",
        label=_RUNNER_RESULT_FILENAME,
        temp_prefix=".rr-",
    )


def _write_runner_result_create_once(
    path: Path,
    content: str,
    *,
    label: str,
    temp_prefix: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{temp_prefix}{uuid4().hex[:10]}")
    try:
        try:
            with temp_path.open(
                "x",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ParallelReviewRuntimeValidationError(
                f"无法 staged 写入 {label}"
            ) from exc
        try:
            os.link(temp_path, path)
        except FileExistsError:
            try:
                existing = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ParallelReviewRuntimeValidationError(
                    f"已有 {label} 无法读取"
                ) from exc
            if existing != content:
                raise ParallelReviewRuntimeValidationError(
                    f"已有 {label} 内容冲突，不得覆盖"
                )
        except OSError as exc:
            raise ParallelReviewRuntimeValidationError(
                f"文件系统不支持 {label} 原子 create-once"
            ) from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ParallelReviewRuntimeValidationError(
                f"无法清理 {label} staged 文件"
            ) from exc


def _read_runner_result_metadata(
    execution_dir: Path,
) -> RunnerResult:
    metadata = _read_runner_result_metadata_record(execution_dir)
    if metadata is None:
        raise ParallelReviewAttemptActiveError(
            "Reviewer terminal execution 正在提交 runner-result.json，"
            "禁止发布终态 result"
        )
    return RunnerResult(
        status=metadata.status,
        output="",
        error=metadata.error,
        command=metadata.command,
    )


def _read_runner_result_metadata_record(
    execution_dir: Path,
) -> _RunnerResultMetadata | None:
    metadata_path = execution_dir / _RUNNER_RESULT_FILENAME
    if not metadata_path.exists():
        return None
    try:
        metadata = _RunnerResultMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError, ValueError) as exc:
        raise ParallelReviewRuntimeValidationError(
            "runner-result.json 不可信"
        ) from exc
    if metadata.source == "runner":
        if any(
            value is not None
            for value in (
                metadata.execution_sha256,
                metadata.process_output_sha256,
                metadata.runner_started_sha256,
            )
        ):
            raise ParallelReviewRuntimeValidationError(
                "runner-result.json 的 runner metadata 含有恢复字段"
            )
    elif (
        metadata.status != "error"
        or not _is_sha256(metadata.execution_sha256)
        or not _is_sha256(metadata.process_output_sha256)
        or (
            metadata.runner_started_sha256 is not None
            and not _is_sha256(metadata.runner_started_sha256)
        )
    ):
        raise ParallelReviewRuntimeValidationError(
            "runner-result.json 的 terminal recovery metadata 不可信"
        )
    return metadata


def _read_or_recover_terminal_runner_result(
    run_dir: Path,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    execution_ref: str,
    execution_path: Path,
    execution: ExecutionLease,
    attempt: ParallelReviewAttemptIdentity,
    runner: Runner,
    evidence: PreparedParallelReviewEvidence,
) -> RunnerResult:
    metadata = _read_runner_result_metadata_record(execution_path.parent)

    expected_runner_identity = _runner_identity(
        runner,
        reviewer_role,
        public_evidence_sha256=attempt.public_evidence_sha256,
        role_prompt_sha256=attempt.role_prompt_sha256,
    )
    _validate_existing_attempt_claim(
        execution_path,
        plan,
        reviewer_role=reviewer_role,
        attempt=attempt,
    )
    runner_started = _read_runner_started_metadata(
        execution_path.parent,
        plan=plan,
        reviewer_role=reviewer_role,
        attempt=attempt,
        expected_runner_identity=expected_runner_identity,
        expected_policy_snapshot_sha256=(
            evidence.snapshot.policy_snapshot_sha256
        ),
        expected_workspace_fingerprint=(
            evidence.snapshot.workspace_fingerprint
        ),
    )
    synthetic_execution = (
        execution.runner_identity.get("synthetic_execution") == "true"
    )
    if runner_started is None and not synthetic_execution:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer terminal execution 缺少可信 runner-started.json"
        )
    if runner_started is not None:
        _validate_runner_started_execution_binding(
            runner_started,
            execution,
            expected_runner_identity=expected_runner_identity,
        )

    try:
        read_parallel_review_process_output(
            run_dir,
            execution_ref=execution_ref,
            process_output_sha256=execution.process_output_sha256,
            process_output_bytes=execution.process_output_bytes,
        )
    except ParallelReviewArtifactValidationError as exc:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer terminal execution 的 process output 不可信"
        ) from exc

    execution_sha256 = _sha256_bytes(execution_path.read_bytes())
    process_output_sha256 = execution.process_output_sha256
    if not _is_sha256(process_output_sha256):
        raise ParallelReviewRuntimeValidationError(
            "Reviewer terminal execution 缺少可信 process output hash"
        )
    runner_started_path = execution_path.with_name(_RUNNER_STARTED_FILENAME)
    runner_started_sha256 = (
        _sha256_bytes(runner_started_path.read_bytes())
        if runner_started is not None
        else None
    )
    _require_terminal_recovery_artifacts_unchanged(
        run_dir,
        execution_ref=execution_ref,
        execution_path=execution_path,
        expected_execution=execution,
        expected_execution_sha256=execution_sha256,
        expected_runner_started_sha256=runner_started_sha256,
    )

    owned_pids = {
        execution.owner_pid,
        *(
            [execution.child_pid]
            if execution.child_pid is not None
            else []
        ),
        *(
            [runner_started.owner_pid]
            if runner_started is not None
            else []
        ),
    }
    if metadata is not None:
        if metadata.source == "runner":
            if metadata.status == "success":
                return RunnerResult(
                    status="error",
                    output="",
                    error=(
                        "Reviewer Runner 声称 success，但原调用未发布 "
                        "result 且 owner/child 已消失；跨进程恢复不得信任"
                        "未绑定的 success metadata。"
                    ),
                    command=metadata.command,
                )
            return RunnerResult(
                status=metadata.status,
                output="",
                error=metadata.error,
                command=metadata.command,
            )
        if (
            metadata.execution_sha256 != execution_sha256
            or metadata.process_output_sha256 != process_output_sha256
            or metadata.runner_started_sha256 != runner_started_sha256
        ):
            raise ParallelReviewRuntimeValidationError(
                "runner-result.json 的 terminal recovery binding 已漂移"
            )
        return RunnerResult(
            status="error",
            output="",
            error=metadata.error,
            command=metadata.command,
        )

    if any(is_process_alive(pid) for pid in owned_pids):
        raise ParallelReviewAttemptActiveError(
            "Reviewer terminal execution 正在提交 runner-result.json，"
            "owner/child PID 仍存活"
        )

    _require_terminal_recovery_artifacts_unchanged(
        run_dir,
        execution_ref=execution_ref,
        execution_path=execution_path,
        expected_execution=execution,
        expected_execution_sha256=execution_sha256,
        expected_runner_started_sha256=runner_started_sha256,
    )
    recovered = RunnerResult(
        status="error",
        output="",
        error=(
            "Reviewer terminal execution 已发布，但 runner-result.json "
            "提交未完成；禁止自动重试或恢复 success。"
        ),
        command=list(execution.command),
    )
    _write_runner_result_metadata(
        execution_path.parent,
        recovered,
        source="terminal_execution_recovery",
        execution_sha256=execution_sha256,
        process_output_sha256=process_output_sha256,
        runner_started_sha256=runner_started_sha256,
    )
    committed = _read_runner_result_metadata_record(execution_path.parent)
    if (
        committed is None
        or committed.source != "terminal_execution_recovery"
        or committed.execution_sha256 != execution_sha256
        or committed.process_output_sha256 != process_output_sha256
        or committed.runner_started_sha256 != runner_started_sha256
    ):
        raise ParallelReviewRuntimeValidationError(
            "Reviewer terminal recovery metadata 提交后复核失败"
        )
    return recovered


def _validate_existing_attempt_claim(
    execution_path: Path,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
) -> None:
    claim_path = execution_path.with_name("attempt-claim.json")
    if (
        not claim_path.is_file()
        or claim_path.is_symlink()
    ):
        raise ParallelReviewRuntimeValidationError(
            "Reviewer terminal execution 缺少可信 attempt claim"
        )
    try:
        payload = json.loads(claim_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer attempt claim 不可信"
        ) from exc
    expected = {
        "schema_version": 1,
        "run_id": plan.run_id,
        "iteration": plan.iteration,
        "review_plan_id": plan.plan_id,
        "reviewer_role": reviewer_role,
        "attempt_id": attempt.attempt_id,
        "step_id": attempt.step_id,
        "idempotency_key": attempt.idempotency_key,
        "input_fingerprint": attempt.input_fingerprint,
        "public_evidence_sha256": attempt.public_evidence_sha256,
        "role_prompt_sha256": attempt.role_prompt_sha256,
    }
    if payload != expected:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer attempt claim identity 不一致"
        )


def _validate_runner_started_execution_binding(
    runner_started: _RunnerStartedMetadata,
    execution: ExecutionLease,
    *,
    expected_runner_identity: Mapping[str, str],
) -> None:
    actual_runner_identity = dict(execution.runner_identity)
    actual_runner_identity.pop("synthetic_execution", None)
    try:
        marker_started_at = datetime.fromisoformat(
            runner_started.started_at
        )
        execution_started_at = datetime.fromisoformat(execution.started_at)
    except ValueError as exc:
        raise ParallelReviewRuntimeValidationError(
            "runner-started.json 与 execution 的时间戳不可信"
        ) from exc
    if (
        execution_started_at.tzinfo is None
        or marker_started_at > execution_started_at
        or runner_started.owner_pid != execution.owner_pid
        or runner_started.base_head != execution.base_head
        or runner_started.before_workspace_fingerprint
        != execution.before_workspace_fingerprint
        or runner_started.policy_snapshot_sha256
        != execution.policy_snapshot_sha256
        or actual_runner_identity != dict(expected_runner_identity)
    ):
        raise ParallelReviewRuntimeValidationError(
            "runner-started.json 与 terminal execution identity 不一致"
        )


def _require_terminal_recovery_artifacts_unchanged(
    run_dir: Path,
    *,
    execution_ref: str,
    execution_path: Path,
    expected_execution: ExecutionLease,
    expected_execution_sha256: str,
    expected_runner_started_sha256: str | None,
) -> None:
    if (
        _sha256_bytes(execution_path.read_bytes())
        != expected_execution_sha256
        or _read_execution(execution_path) != expected_execution
    ):
        raise ParallelReviewRuntimeValidationError(
            "Reviewer terminal execution 在恢复提交前发生变化"
        )
    try:
        read_parallel_review_process_output(
            run_dir,
            execution_ref=execution_ref,
            process_output_sha256=expected_execution.process_output_sha256,
            process_output_bytes=expected_execution.process_output_bytes,
        )
    except ParallelReviewArtifactValidationError as exc:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer process output 在恢复提交前发生变化"
        ) from exc
    runner_started_path = execution_path.with_name(_RUNNER_STARTED_FILENAME)
    if expected_runner_started_sha256 is None:
        if runner_started_path.exists():
            raise ParallelReviewRuntimeValidationError(
                "runner-started.json 在恢复提交前意外出现"
            )
    elif (
        not runner_started_path.is_file()
        or runner_started_path.is_symlink()
        or _sha256_bytes(runner_started_path.read_bytes())
        != expected_runner_started_sha256
    ):
        raise ParallelReviewRuntimeValidationError(
            "runner-started.json 在恢复提交前发生变化"
        )


def _ensure_unknown_runner_result_metadata(
    execution_dir: Path,
    *,
    reason: str,
    runner: Runner,
) -> RunnerResult:
    metadata_path = execution_dir / _RUNNER_RESULT_FILENAME
    if not metadata_path.exists():
        _write_runner_result_metadata(
            execution_dir,
            RunnerResult(
                status="error",
                output="",
                error=reason,
                command=[type(runner).__name__],
            ),
        )
    return _read_runner_result_metadata(execution_dir)


def _execution_has_live_owned_pid(execution: ExecutionLease) -> bool:
    return is_process_alive(execution.owner_pid) or (
        execution.child_pid is not None
        and is_process_alive(execution.child_pid)
    )


def _seal_stale_process_output(
    run_dir: Path,
    *,
    execution_ref: str,
    execution_path: Path,
) -> tuple[str, int]:
    output_path = execution_path.with_name("process-output.txt")
    if not output_path.exists():
        _, output_sha256, output_bytes = (
            write_parallel_review_process_output(
                run_dir,
                execution_ref=execution_ref,
                content="",
            )
        )
        return output_sha256, output_bytes
    try:
        payload = output_path.read_bytes()
    except OSError as exc:
        raise ParallelReviewRuntimeValidationError(
            "stale Reviewer process output 无法读取"
        ) from exc
    output_sha256 = _sha256_bytes(payload)
    output_bytes = len(payload)
    try:
        read_parallel_review_process_output(
            run_dir,
            execution_ref=execution_ref,
            process_output_sha256=output_sha256,
            process_output_bytes=output_bytes,
        )
    except ParallelReviewArtifactValidationError as exc:
        raise ParallelReviewRuntimeValidationError(
            "stale Reviewer process output 不可信"
        ) from exc
    return output_sha256, output_bytes


def _write_result_from_execution(
    run_dir: Path,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt_id: str,
    execution_ref: str,
    execution_path: Path,
    execution: ExecutionLease,
    runner_result: RunnerResult,
    workspace_issue: str | None,
) -> ParallelReviewResultRef:
    if not execution_path.exists():
        raise ParallelReviewRuntimeValidationError(
            "Reviewer execution artifact 尚未发布"
        )
    if execution.status in ACTIVE_EXECUTION_STATUSES:
        raise ParallelReviewAttemptActiveError(
            "Reviewer execution 仍处于 active 状态，禁止发布终态 result"
        )
    try:
        trusted_output = read_parallel_review_process_output(
            run_dir,
            execution_ref=execution_ref,
            process_output_sha256=execution.process_output_sha256,
            process_output_bytes=execution.process_output_bytes,
        )
    except Exception as exc:
        raise ParallelReviewRuntimeValidationError(
            "Reviewer process output 与 terminal execution 不一致"
        ) from exc
    runner_result = RunnerResult(
        status=runner_result.status,
        output=trusted_output,
        error=runner_result.error,
        command=runner_result.command,
    )
    execution_sha256 = _sha256_bytes(execution_path.read_bytes())
    status = _review_execution_status(
        execution,
        runner_result,
        workspace_issue=workspace_issue,
    )
    if status == "active":
        raise ParallelReviewAttemptActiveError(
            "Reviewer execution 仍处于 active 状态，禁止发布终态 result"
        )

    if status == "completed":
        try:
            parsed = _parse_parallel_reviewer_output(
                runner_result.output,
                plan,
                reviewer_role=reviewer_role,
            )
            findings = tuple(
                build_parallel_review_finding(
                    evidence_snapshot_sha256=(
                        plan.evidence_snapshot_sha256
                    ),
                    severity=finding.severity,
                    category=finding.category,
                    rule_id=finding.rule_id,
                    path=finding.path,
                    location=finding.location,
                    title=finding.title,
                    evidence=finding.evidence,
                    recommendation=finding.recommendation,
                )
                for finding in parsed.findings
            )
        except (
            ParallelReviewRuntimeValidationError,
            ValidationError,
            ValueError,
            TypeError,
        ):
            status = "parse_error"
        else:
            result = build_parallel_review_result(
                review_plan_id=plan.plan_id,
                run_id=plan.run_id,
                iteration=plan.iteration,
                reviewer_role=reviewer_role,
                attempt_id=attempt_id,
                evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
                execution_ref=execution_ref,
                execution_sha256=execution_sha256,
                status="completed",
                verdict=parsed.verdict,
                summary=parsed.summary,
                findings=findings,
                checked_items=parsed.checked_items,
            )
            return write_parallel_review_result(run_dir, result)

    summary = _failure_summary(
        status,
        runner_result,
        workspace_issue=workspace_issue,
    )
    result = build_parallel_review_result(
        review_plan_id=plan.plan_id,
        run_id=plan.run_id,
        iteration=plan.iteration,
        reviewer_role=reviewer_role,
        attempt_id=attempt_id,
        evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
        execution_ref=execution_ref,
        execution_sha256=execution_sha256,
        status=status,
        verdict="needs_human",
        summary=summary,
        findings=(),
        checked_items=(
            "公共 evidence snapshot",
            f"角色：{reviewer_role}",
        ),
    )
    return write_parallel_review_result(run_dir, result)


def _review_execution_status(
    execution: ExecutionLease,
    runner_result: RunnerResult,
    *,
    workspace_issue: str | None,
) -> ReviewExecutionStatus:
    if execution.status in ACTIVE_EXECUTION_STATUSES:
        return "active"
    if execution.termination_unconfirmed:
        return "termination_unconfirmed"
    if execution.status == "timed_out":
        return "timed_out"
    if execution.status == "stopped":
        return "stopped"
    if workspace_issue is not None:
        return "provider_error"
    if runner_result.status == "timed_out":
        return "timed_out"
    if runner_result.status == "stopped":
        return "stopped"
    if runner_result.status in {"error", "skipped"}:
        return "provider_error"
    if execution.status == "failed":
        return "provider_error"
    if execution.status != "completed":
        return "termination_unconfirmed"
    return "completed"


def _parse_parallel_reviewer_output(
    output: str,
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
) -> _ParallelReviewerOutput:
    for candidate in _iter_json_object_candidates(output):
        try:
            parsed = _ParallelReviewerOutput.model_validate_json(candidate)
        except (ValidationError, ValueError, TypeError):
            continue
        if (
            parsed.reviewer_role != reviewer_role
            or parsed.review_plan_id != plan.plan_id
            or parsed.evidence_snapshot_sha256
            != plan.evidence_snapshot_sha256
        ):
            continue
        if not parsed.summary.strip():
            continue
        return parsed
    raise ParallelReviewRuntimeValidationError(
        "Reviewer 输出无法解析为角色化 result JSON"
    )


def _iter_json_object_candidates(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    if not stripped:
        return ()
    candidates: list[str] = []
    for start, char in enumerate(stripped):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(stripped)):
            current = stripped[end]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(stripped[start : end + 1])
                    break
    return tuple(candidates)


def _failure_summary(
    status: ReviewExecutionStatus,
    runner_result: RunnerResult,
    *,
    workspace_issue: str | None,
) -> str:
    labels = {
        "provider_error": "Reviewer Runner 已确认失败",
        "parse_error": "Reviewer 输出无法解析为可信结构化结果",
        "timed_out": "Reviewer Runner 执行超时",
        "stopped": "Reviewer Runner 已按 stop request 停止",
        "termination_unconfirmed": "Reviewer owned process 终止未确认",
        "active": "Reviewer execution 仍在运行",
        "completed": "Reviewer 已完成",
    }
    details = []
    if runner_result.error:
        details.append(runner_result.error)
    if workspace_issue:
        details.append(workspace_issue)
    summary = labels[status]
    if details:
        summary += "：" + "；".join(details)
    return redact_text(summary)[:2000]


def _legacy_runner_status(
    results: Iterable[ParallelReviewResult],
) -> RunnerStatus:
    statuses = {result.status for result in results}
    if statuses <= {"completed"}:
        return "success"
    if statuses & {
        "provider_error",
        "parse_error",
        "active",
        "termination_unconfirmed",
    }:
        return "error"
    if "timed_out" in statuses:
        return "timed_out"
    if "stopped" in statuses:
        return "stopped"
    return "error"


def _render_aggregate_runner_output(
    aggregate: ParallelReviewAggregate,
    results: Iterable[ParallelReviewResult],
) -> str:
    lines = [
        "# Parallel Reviewer Aggregate",
        "",
        f"- verdict: `{aggregate.verdict}`",
        (
            "- reasons: "
            + (
                ", ".join(f"`{reason}`" for reason in aggregate.reasons)
                if aggregate.reasons
                else "`none`"
            )
        ),
        "",
        "## Reviewer 状态",
        "",
    ]
    lines.extend(
        f"- `{result.reviewer_role}`: `{result.status}` / `{result.verdict}`"
        for result in sorted(
            results,
            key=lambda item: AVAILABLE_REVIEWER_ROLES.index(
                item.reviewer_role
            ),
        )
    )
    lines.extend(
        [
            "",
            "该兼容视图不复制各 Reviewer 的私有 process output。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _legacy_line_number(location: str) -> int:
    match = re.search(r"(?:line[:#-]?|^)(\d+)", location)
    return int(match.group(1)) if match else 0


def _write_create_once(path: Path, content: str) -> None:
    normalized = redact_text(content).replace("\r\n", "\n").replace(
        "\r",
        "\n",
    )
    normalized = normalized.rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
    except FileExistsError:
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ParallelReviewRuntimeValidationError(
                f"已有 artifact 无法读取：{path.name}"
            ) from exc
        if existing != normalized:
            raise ParallelReviewRuntimeValidationError(
                f"已有 artifact 内容不同，不得覆盖：{path.name}"
            )


def _normalize_sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ParallelReviewRuntimeValidationError(
            f"{field_name} 必须是 64 位小写十六进制"
        )
    return normalized


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_PATTERN.fullmatch(value))


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve()))
