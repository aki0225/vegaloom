from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vega.execution_control import ExecutionController, RunnerExecutionContext  # noqa: E402
from vega.parallel_review import (  # noqa: E402
    AVAILABLE_REVIEWER_ROLES,
    ParallelReviewAggregationContext,
    ParallelReviewResult,
    ParallelReviewRoutingContext,
    ReviewTopology,
    ReviewerRole,
    aggregate_parallel_reviews,
    build_parallel_review_plan,
)
from vega.parallel_review_artifacts import (  # noqa: E402
    build_review_evidence_snapshot_from_artifacts,
    read_parallel_review_result,
)
from vega.parallel_review_graph import execute_parallel_review_graph  # noqa: E402
from vega.parallel_review_runtime import (  # noqa: E402
    build_runner_parallel_review_executors,
    prepare_parallel_review_evidence,
)
from vega.project_config import (  # noqa: E402
    CodexExecOptions,
    CodexProviderDescriptor,
    codex_provider_descriptor_sha256,
)
from vega.redaction import (  # noqa: E402
    redact_text,
    write_redacted_json_atomic,
    write_redacted_text_atomic,
)
from vega.reviewer_topology_eval import (  # noqa: E402
    ProviderSessionBudget,
    ReviewerTopologyCaseScore,
    ReviewerTopologyEvaluationDataset,
    ReviewerTopologyGroundTruthDataset,
    ReviewerTopologyPublicCase,
    ReviewerTopologyPublicDataset,
    ReviewerTopologySummary,
    load_ground_truth,
    load_public_dataset,
    score_reviewer_topology_case,
    summarize_reviewer_topology,
)
from vega.runner import CodexExecRunner, Runner, RunnerResult  # noqa: E402
from vega.workspace_check import (  # noqa: E402
    ReviewWorkspaceSnapshot,
    capture_review_workspace,
)


RunnerMode = Literal["fake", "real"]
TOPOLOGIES: tuple[ReviewTopology, ...] = (
    "single",
    "fixed_three",
    "adaptive",
)
EVAL_SCHEMA_VERSION = 1
DEFAULT_DATASET = PROJECT_ROOT / "eval" / "gate-5.5" / "cases.json"
DEFAULT_GROUND_TRUTH = (
    PROJECT_ROOT / "eval" / "gate-5.5" / "ground-truth.json"
)
DEFAULT_FIXTURE_ROOT = (
    PROJECT_ROOT / ".tmp" / "langgraph-fixtures" / "gate-5.5"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / ".local-validation" / "gate-5.5"
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs"
FROZEN_BRANCH = "experiment/langgraph-comparison"
FROZEN_EXECUTION_TAG = "gate-5.5-pre-run-v1"
FROZEN_MODEL = "sandbox-model"
FROZEN_REASONING = "high"
FROZEN_PROVIDER = "sandboxproxy"
FROZEN_PROVIDER_BASE_URL = "http://127.0.0.1:18080/v1"
FROZEN_PROVIDER_WIRE_API = "responses"
FROZEN_PROVIDER_REQUIRES_AUTH = True
FROZEN_PROVIDER_SUPPORTS_WEBSOCKETS = False
FROZEN_AUTH_MODE = "chatgpt"
FROZEN_CODEX_VERSION = "0.144.5"
FROZEN_WINDOWS_SANDBOX_OVERRIDE = "elevated"
FROZEN_REVIEW_TIMEOUT_SECONDS = 900
FROZEN_PREFLIGHT_TIMEOUT_SECONDS = 180
FROZEN_PROVIDER_SESSION_LIMIT = 90
FROZEN_DATASET_SHA256 = (
    "50d2fac3f04260b6f9bbb13831fd2fbd2b9db39d064d98d5e1f4719d3b042bb1"
)
FROZEN_GROUND_TRUTH_SHA256 = (
    "2c5839d7c770a3a4e58f918a2c2fdfc5f548b7249dea2256df2281bf3e7a782b"
)
GROUND_TRUTH_FORBIDDEN_MARKER = "VEGA_GATE_5_5_GROUND_TRUTH_PRIVATE"
_TOKEN_COUNT_PATTERN = re.compile(
    r"(?im)^tokens used\s*$\s*^([0-9][0-9,]*)\s*$"
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
_ROLE_PATTERN = re.compile(r"Reviewer role：`([^`]+)`")
_PLAN_PATTERN = re.compile(r"ReviewPlan：`([^`]+)`")
_SNAPSHOT_PATTERN = re.compile(r"Evidence snapshot：`([^`]+)`")
_FROZEN_RULE_IDS = (
    "correctness.off_by_one",
    "correctness.expiry_boundary",
    "correctness.unicode_separator",
    "verification.mocked_subject",
    "verification.missing_side_effect_assertion",
    "verification.missing_boundary_case",
    "security.path_traversal",
    "security.command_injection",
    "design.non_atomic_persistence",
)
_FROZEN_EVALUATOR_CASE_IDS = (
    "clean_project_key_normalization",
    "clean_endpoint_default_port",
    "clean_stable_metric_summary",
    "correctness_pagination_page_size",
    "correctness_expiry_exact_boundary",
    "correctness_unicode_tag_separator",
    "verification_webhook_mocked_persistence",
    "verification_receipt_side_effects",
    "verification_discount_threshold_boundary",
    "security_report_output_path",
    "security_git_revision_command",
    "design_atomic_settings_persistence",
)
@dataclass(frozen=True)
class FixtureRecord:
    case_id: str
    neutral_case_id: str
    repo_path: Path
    head_sha: str
    workspace: ReviewWorkspaceSnapshot
    verification_command: str
    verification_output: str
    verification_elapsed_seconds: float


@dataclass(frozen=True)
class ProviderCallRecord:
    session_number: int
    run_id: str
    reviewer_role: str
    attempt_id: str
    status: str
    elapsed_seconds: float
    tokens_used: int | None
    prompt_chars: int | None


@dataclass
class TopologyExecutionRecord:
    case_id: str
    topology: ReviewTopology
    replicate: int
    comparison_kind: str | None
    review_plan_id: str
    evidence_snapshot_sha256: str
    public_evidence_sha256: str
    required_roles: list[str]
    aggregate_verdict: str
    aggregate_reasons: list[str]
    aggregate_sha256: str
    result_ids: list[str]
    attempt_ids: list[str]
    result_statuses: dict[str, str]
    elapsed_seconds: float
    provider_sessions: int
    tokens_used: int | None
    score: ReviewerTopologyCaseScore | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "case_id": self.case_id,
            "topology": self.topology,
            "replicate": self.replicate,
            "comparison_kind": self.comparison_kind,
            "review_plan_id": self.review_plan_id,
            "evidence_snapshot_sha256": self.evidence_snapshot_sha256,
            "public_evidence_sha256": self.public_evidence_sha256,
            "required_roles": self.required_roles,
            "aggregate_verdict": self.aggregate_verdict,
            "aggregate_reasons": self.aggregate_reasons,
            "aggregate_sha256": self.aggregate_sha256,
            "result_ids": self.result_ids,
            "attempt_ids": self.attempt_ids,
            "result_statuses": self.result_statuses,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "provider_sessions": self.provider_sessions,
            "tokens_used": self.tokens_used,
        }
        if self.score is not None:
            payload["score"] = self.score.model_dump(mode="json")
        return payload


@dataclass
class _ExecutionBundle:
    record: TopologyExecutionRecord
    results: list[ParallelReviewResult]
    aggregate: object


@dataclass(frozen=True)
class RepeatTrigger:
    case_id: str
    case_index: int
    comparison_kind: Literal[
        "adaptive-vs-single",
        "fixed-three-vs-single",
        "fixed-three-vs-adaptive",
    ]
    topologies: tuple[ReviewTopology, ReviewTopology]
    finding_ids: tuple[str, ...]
    reason: str


@dataclass
class RepeatOutcome:
    trigger: RepeatTrigger
    completed: bool
    unresolved_reason: str | None
    candidate_reproduced_finding_ids: list[str] = field(default_factory=list)
    comparison_missed_finding_ids: list[str] = field(default_factory=list)
    verdict_flip: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "case_id": self.trigger.case_id,
            "comparison_kind": self.trigger.comparison_kind,
            "topologies": list(self.trigger.topologies),
            "finding_ids": list(self.trigger.finding_ids),
            "reason": self.trigger.reason,
            "completed": self.completed,
            "unresolved_reason": self.unresolved_reason,
            "candidate_reproduced_finding_ids": (
                self.candidate_reproduced_finding_ids
            ),
            "comparison_missed_finding_ids": (
                self.comparison_missed_finding_ids
            ),
            "verdict_flip": self.verdict_flip,
        }


@dataclass
class EvaluationRun:
    session: str
    runner_mode: RunnerMode
    output_dir: Path
    fixture_session_root: Path
    run_root: Path
    public_dataset: ReviewerTopologyPublicDataset
    dataset_sha256: str
    ground_truth_sha256: str
    budget: ProviderSessionBudget
    call_records: list[ProviderCallRecord] = field(default_factory=list)
    topology_records: list[TopologyExecutionRecord] = field(default_factory=list)
    repeat_outcomes: list[RepeatOutcome] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class EvaluationContractViolation(RuntimeError):
    """评测合同或隔离边界失败，必须分类为 fail。"""


class BudgetedRunner:
    """在真实 provider 启动前原子预留预算，并记录脱敏调用指标。"""

    def __init__(
        self,
        inner: Runner,
        *,
        budget: ProviderSessionBudget,
        records: list[ProviderCallRecord],
        lock: threading.Lock,
    ) -> None:
        self.inner = inner
        self.budget = budget
        self.records = records
        self.lock = lock

    def execution_identity(self, sandbox: str) -> dict[str, str]:
        identity = getattr(self.inner, "execution_identity", None)
        if callable(identity):
            return dict(identity(sandbox))
        return {"kind": type(self.inner).__name__, "sandbox": sandbox}

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        session_number = self.budget.reserve_session()
        started = time.monotonic()
        role = ""
        run_id = ""
        attempt_id = ""
        if execution_context is not None:
            run_id = execution_context.run_id
            attempt_id = execution_context.attempt_id or ""
            if execution_context.runner_identity is not None:
                role = execution_context.runner_identity.get("role", "")
        try:
            result = self.inner.run(
                prompt,
                repo_path,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
                execution_context=execution_context,
            )
        except Exception:
            elapsed = time.monotonic() - started
            with self.lock:
                self.records.append(
                    ProviderCallRecord(
                        session_number=session_number,
                        run_id=run_id,
                        reviewer_role=role,
                        attempt_id=attempt_id,
                        status="raised",
                        elapsed_seconds=round(elapsed, 3),
                        tokens_used=None,
                        prompt_chars=len(prompt),
                    )
                )
            raise
        elapsed = time.monotonic() - started
        record = ProviderCallRecord(
            session_number=session_number,
            run_id=run_id,
            reviewer_role=role,
            attempt_id=attempt_id,
            status=result.status,
            elapsed_seconds=round(elapsed, 3),
            tokens_used=_parse_tokens_used(result.output),
            prompt_chars=len(prompt),
        )
        with self.lock:
            self.records.append(record)
        return result


class PromptGuardRunner:
    """在外部 Runner 前检查最终 prompt 和 fixture 中性身份。"""

    def __init__(
        self,
        inner: Runner,
        *,
        neutral_case_id: str,
        forbidden_markers: tuple[str, ...],
    ) -> None:
        self.inner = inner
        self.neutral_case_id = neutral_case_id
        self.forbidden_markers = tuple(
            marker for marker in forbidden_markers if marker
        )

    def execution_identity(self, sandbox: str) -> dict[str, str]:
        identity = getattr(self.inner, "execution_identity", None)
        if callable(identity):
            return dict(identity(sandbox))
        return {"kind": type(self.inner).__name__, "sandbox": sandbox}

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        git_log = _git_output(repo_path, "log", "-5", "--format=%B")
        environment = "\n".join(
            f"{key}={value}" for key, value in sorted(os.environ.items())
        )
        surfaces = {
            "final_prompt": prompt,
            "repo_path": str(repo_path.resolve()),
            "git_log": git_log,
            "environment": environment,
        }
        for label, content in surfaces.items():
            lowered = content.lower()
            leaked = next(
                (
                    marker
                    for marker in self.forbidden_markers
                    if marker.lower() in lowered
                ),
                None,
            )
            if leaked is not None:
                raise EvaluationContractViolation(
                    f"{label} 包含 evaluator 私有标记，禁止启动 provider"
                )
        if self.neutral_case_id not in git_log:
            raise EvaluationContractViolation(
                "fixture Git 历史未绑定中性 case id"
            )
        return self.inner.run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )


class DeterministicFakeReviewer:
    """只验证 harness 和 artifact 链，不模拟真实 finding 质量。"""

    def execution_identity(self, sandbox: str) -> dict[str, str]:
        return {
            "kind": type(self).__name__,
            "sandbox": sandbox,
            "model": "deterministic-fake",
            "reasoning_effort": "none",
            "ephemeral": "true",
        }

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        if execution_context is None:
            raise RuntimeError("fake reviewer 缺少 execution context")
        role = _required_match(_ROLE_PATTERN, prompt, "reviewer role")
        plan_id = _required_match(_PLAN_PATTERN, prompt, "review plan")
        snapshot_sha256 = _required_match(
            _SNAPSHOT_PATTERN,
            prompt,
            "evidence snapshot",
        )
        command = ["gate-5.5-fake-reviewer", role]
        controller = ExecutionController(execution_context)
        controller.prepare(command, timeout_seconds)
        output = json.dumps(
            {
                "schema_version": 1,
                "reviewer_role": role,
                "review_plan_id": plan_id,
                "evidence_snapshot_sha256": snapshot_sha256,
                "verdict": "approve",
                "summary": "确定性 fake reviewer 未输出 finding。",
                "findings": [],
                "checked_items": ["harness artifact contract"],
            },
            ensure_ascii=False,
        )
        output_path = (
            execution_context.execution_dir / "process-output.txt"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            output.rstrip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output=output,
            command=command,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="运行 Gate 5.5 Reviewer topology 收益评测。",
    )
    parser.add_argument(
        "--runner",
        choices=["fake", "real"],
        default="fake",
    )
    parser.add_argument(
        "--session",
        default=datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    parser.add_argument("--model", default=FROZEN_MODEL)
    parser.add_argument("--reviewer-reasoning", default=FROZEN_REASONING)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=FROZEN_REVIEW_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--preflight-timeout-seconds",
        type=int,
        default=FROZEN_PREFLIGHT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--max-provider-sessions",
        type=int,
        default=FROZEN_PROVIDER_SESSION_LIMIT,
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=DEFAULT_GROUND_TRUTH,
    )
    parser.add_argument(
        "--ground-truth-sha256",
        help="真实评测必填；必须等于预注册 ground truth 文件 SHA-256。",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=DEFAULT_FIXTURE_ROOT,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=DEFAULT_RUN_ROOT,
    )
    parser.add_argument(
        "--expected-provider",
        default=FROZEN_PROVIDER,
    )
    parser.add_argument(
        "--expected-auth-mode",
        choices=["api_key", "chatgpt"],
        default=FROZEN_AUTH_MODE,
    )
    parser.add_argument(
        "--expected-codex-version",
        default=FROZEN_CODEX_VERSION,
    )
    parser.add_argument(
        "--provider-base-url",
        default=FROZEN_PROVIDER_BASE_URL,
    )
    parser.add_argument(
        "--provider-wire-api",
        choices=["responses"],
        default=FROZEN_PROVIDER_WIRE_API,
    )
    parser.add_argument(
        "--provider-requires-openai-auth",
        choices=["true", "false"],
        default=str(FROZEN_PROVIDER_REQUIRES_AUTH).lower(),
    )
    parser.add_argument(
        "--provider-supports-websockets",
        choices=["true", "false"],
        default=str(FROZEN_PROVIDER_SUPPORTS_WEBSOCKETS).lower(),
    )
    parser.add_argument(
        "--windows-sandbox-session-override",
        choices=["elevated"],
        default=FROZEN_WINDOWS_SANDBOX_OVERRIDE,
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="只允许 fake dry-run 使用；真实评测始终要求 clean worktree。",
    )
    args = parser.parse_args(argv)

    runner_mode: RunnerMode = args.runner
    if runner_mode == "real" and args.allow_dirty:
        parser.error("真实评测禁止 --allow-dirty")
    if runner_mode == "real" and not args.ground_truth_sha256:
        parser.error("真实评测必须提供 --ground-truth-sha256")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds 必须大于 0")
    if args.preflight_timeout_seconds <= 0:
        parser.error("--preflight-timeout-seconds 必须大于 0")
    if args.max_provider_sessions < 1:
        parser.error("--max-provider-sessions 必须大于 0")
    if not _IDENTIFIER_PATTERN.fullmatch(args.session):
        parser.error("--session 只能包含小写字母、数字、点、下划线和连字符")

    try:
        dataset_path = _require_project_file(args.dataset, "--dataset")
        ground_truth_path = _require_project_file(
            args.ground_truth,
            "--ground-truth",
        )
        fixture_root = _require_project_directory_root(
            args.fixture_root,
            "--fixture-root",
        )
        output_root = _require_project_directory_root(
            args.output_root,
            "--output-root",
        )
        run_root = _require_project_directory_root(
            args.run_root,
            "--run-root",
        )
        provider = CodexProviderDescriptor(
            name=args.expected_provider,
            base_url=args.provider_base_url,
            wire_api=args.provider_wire_api,
            requires_openai_auth=_parse_bool(
                args.provider_requires_openai_auth
            ),
            supports_websockets=_parse_bool(
                args.provider_supports_websockets
            ),
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        if not args.allow_dirty:
            _require_clean_project()
        if runner_mode == "real":
            _require_frozen_real_arguments(args)
            _require_execution_baseline()
    except RuntimeError as exc:
        parser.error(str(exc))

    try:
        exit_code = run_evaluation(
            runner_mode=runner_mode,
            session=args.session,
            model=args.model,
            reviewer_reasoning=args.reviewer_reasoning,
            timeout_seconds=args.timeout_seconds,
            preflight_timeout_seconds=args.preflight_timeout_seconds,
            max_provider_sessions=args.max_provider_sessions,
            dataset_path=dataset_path,
            ground_truth_path=ground_truth_path,
            ground_truth_sha256=args.ground_truth_sha256,
            fixture_root=fixture_root,
            output_root=output_root,
            run_root=run_root,
            expected_provider=args.expected_provider,
            expected_auth_mode=args.expected_auth_mode,
            expected_codex_version=args.expected_codex_version,
            provider=provider,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
        )
    except Exception as exc:  # noqa: BLE001 - CLI 必须固化明确失败
        print(
            "Gate 5.5 执行失败："
            f"{type(exc).__name__}: {redact_text(str(exc))}",
            file=sys.stderr,
        )
        return 1
    return exit_code


def run_evaluation(
    *,
    runner_mode: RunnerMode,
    session: str,
    model: str,
    reviewer_reasoning: str,
    timeout_seconds: int,
    preflight_timeout_seconds: int,
    max_provider_sessions: int,
    dataset_path: Path,
    ground_truth_path: Path,
    ground_truth_sha256: str | None,
    fixture_root: Path,
    output_root: Path,
    run_root: Path,
    expected_provider: str,
    expected_auth_mode: Literal["api_key", "chatgpt"],
    expected_codex_version: str,
    provider: CodexProviderDescriptor,
    windows_sandbox_session_override: Literal["elevated"],
) -> int:
    if runner_mode == "real":
        _require_clean_project()
        _require_execution_baseline()
        _require_frozen_runtime_contract(
            model=model,
            reviewer_reasoning=reviewer_reasoning,
            timeout_seconds=timeout_seconds,
            preflight_timeout_seconds=preflight_timeout_seconds,
            max_provider_sessions=max_provider_sessions,
            expected_provider=expected_provider,
            expected_auth_mode=expected_auth_mode,
            expected_codex_version=expected_codex_version,
            provider=provider,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
        )
    started = time.monotonic()
    output_dir = _new_session_root(output_root, session)
    fixture_session_root = _new_session_root(fixture_root, session)
    try:
        public_dataset = load_public_dataset(dataset_path)
        dataset_sha256 = _sha256_file(dataset_path)
        if dataset_sha256 != FROZEN_DATASET_SHA256:
            raise ValueError(
                "public dataset SHA-256 与预注册 commitment 不一致"
            )
        expected_ground_truth_sha256 = _normalize_sha256(
            ground_truth_sha256 or _sha256_file(ground_truth_path)
        )
        if expected_ground_truth_sha256 != FROZEN_GROUND_TRUTH_SHA256:
            raise ValueError(
                "ground truth SHA-256 与预注册 commitment 不一致"
            )
        readiness = _validate_readiness(public_dataset)
        if readiness != {
            "single": 12,
            "fixed_three": 36,
            "adaptive": 24,
        }:
            raise ValueError(
                f"Reviewer session readiness 不一致：{readiness}"
            )
    except Exception as exc:  # noqa: BLE001 - readiness 必须留下证据
        _persist_early_blocked_summary(
            output_dir,
            session=session,
            runner_mode=runner_mode,
            model=model,
            expected_provider=expected_provider,
            error=exc,
            elapsed_seconds=time.monotonic() - started,
        )
        return 1
    budget = ProviderSessionBudget(max_provider_sessions)
    evaluation = EvaluationRun(
        session=session,
        runner_mode=runner_mode,
        output_dir=output_dir,
        fixture_session_root=fixture_session_root,
        run_root=run_root,
        public_dataset=public_dataset,
        dataset_sha256=dataset_sha256,
        ground_truth_sha256=expected_ground_truth_sha256,
        budget=budget,
    )

    preflight: dict[str, object]
    if runner_mode == "real":
        try:
            preflight = _run_real_preflight(
                evaluation,
                model=model,
                reviewer_reasoning=reviewer_reasoning,
                expected_provider=expected_provider,
                expected_auth_mode=expected_auth_mode,
                expected_codex_version=expected_codex_version,
                provider=provider,
                windows_sandbox_session_override=(
                    windows_sandbox_session_override
                ),
                timeout_seconds=preflight_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - preflight 必须收敛
            if not any(
                item.reviewer_role == "provider-preflight"
                for item in evaluation.call_records
            ):
                evaluation.call_records.append(
                    ProviderCallRecord(
                        session_number=evaluation.budget.used_sessions,
                        run_id="provider-preflight",
                        reviewer_role="provider-preflight",
                        attempt_id="provider-preflight",
                        status="raised",
                        elapsed_seconds=0.0,
                        tokens_used=None,
                        prompt_chars=None,
                    )
                )
            evaluation.failures.append(
                f"{type(exc).__name__}: {redact_text(str(exc))}"
            )
            preflight = {
                "conclusion": "blocked",
                "reason": "provider preflight 异常退出",
                "diagnostics": list(evaluation.failures),
            }
        if preflight.get("conclusion") != "preflight-passed":
            summary = _build_summary(
                evaluation,
                model=model,
                reviewer_reasoning=reviewer_reasoning,
                expected_provider=expected_provider,
                expected_auth_mode=expected_auth_mode,
                expected_codex_version=expected_codex_version,
                provider=provider,
                preflight=preflight,
                topology_summaries={},
                decision="blocked",
                elapsed_seconds=time.monotonic() - started,
            )
            _persist_summary(evaluation.output_dir, summary)
            print("Gate 5.5：blocked（provider preflight 未通过）")
            print(f"证据：{evaluation.output_dir}")
            return 1
    else:
        preflight = {
            "conclusion": "not-required-fake",
            "provider_sessions": 0,
        }

    if runner_mode == "real":
        options = CodexExecOptions(
            ignore_user_config=True,
            provider=provider,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            model=model,
            reasoning_effort=reviewer_reasoning,
            ephemeral=True,
        )
        base_runner: Runner = CodexExecRunner(options=options)
    else:
        base_runner = DeterministicFakeReviewer()

    call_lock = threading.Lock()
    reviewer_runner: Runner
    if runner_mode == "real":
        reviewer_runner = BudgetedRunner(
            base_runner,
            budget=budget,
            records=evaluation.call_records,
            lock=call_lock,
        )
    else:
        reviewer_runner = base_runner

    try:
        topology_summaries, decision = _execute_case_matrix(
            evaluation,
            runner_mode=runner_mode,
            public_dataset=public_dataset,
            ground_truth_path=ground_truth_path,
            dataset_sha256=dataset_sha256,
            ground_truth_sha256=expected_ground_truth_sha256,
            reviewer_runner=reviewer_runner,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - 必须持久化明确终态
        evaluation.failures.append(
            f"{type(exc).__name__}: {redact_text(str(exc))}"
        )
        topology_summaries = {}
        decision = (
            "fail"
            if isinstance(exc, EvaluationContractViolation)
            else "blocked"
        )
    summary = _build_summary(
        evaluation,
        model=model,
        reviewer_reasoning=reviewer_reasoning,
        expected_provider=expected_provider,
        expected_auth_mode=expected_auth_mode,
        expected_codex_version=expected_codex_version,
        provider=provider,
        preflight=preflight,
        topology_summaries=topology_summaries,
        decision=decision,
        elapsed_seconds=time.monotonic() - started,
    )
    _persist_summary(evaluation.output_dir, summary)
    print(f"Gate 5.5：{decision}")
    print(f"证据：{evaluation.output_dir}")
    print(
        "Provider sessions："
        f"{budget.used_sessions}/{budget.max_sessions}"
    )
    return 0 if decision not in {"blocked", "fail"} else 1


def _execute_case_matrix(
    evaluation: EvaluationRun,
    *,
    runner_mode: RunnerMode,
    public_dataset: ReviewerTopologyPublicDataset,
    ground_truth_path: Path,
    dataset_sha256: str,
    ground_truth_sha256: str,
    reviewer_runner: Runner,
    timeout_seconds: int,
) -> tuple[dict[str, ReviewerTopologySummary], str]:
    bundles: dict[tuple[str, ReviewTopology], _ExecutionBundle] = {}
    fixtures_by_case: dict[str, FixtureRecord] = {}
    neutral_ids: dict[str, str] = {}
    for index, public_case in enumerate(public_dataset.cases):
        neutral_case_id = f"case-{index + 1:02d}"
        fixture = _prepare_fixture(
            evaluation.fixture_session_root,
            public_case,
            neutral_case_id=neutral_case_id,
        )
        fixtures_by_case[public_case.case_id] = fixture
        neutral_ids[public_case.case_id] = neutral_case_id
        case_run_dir = _new_case_run_root(
            evaluation.run_root,
            session=evaluation.session,
            neutral_case_id=neutral_case_id,
        )
        evidence, routing = _prepare_case_evidence(
            case_run_dir,
            public_case=public_case,
            fixture=fixture,
            dataset_id=public_dataset.dataset_id,
            dataset_sha256=dataset_sha256,
            ground_truth_sha256=ground_truth_sha256,
            neutral_case_id=neutral_case_id,
        )
        for topology in _topology_order(index):
            before_sessions = evaluation.budget.used_sessions
            bundle = _execute_topology(
                run_dir=case_run_dir,
                repo_path=fixture.repo_path,
                public_case=public_case,
                topology=topology,
                routing=routing,
                evidence=evidence,
                runner=reviewer_runner,
                timeout_seconds=timeout_seconds,
                ground_truth_sha256=ground_truth_sha256,
                neutral_case_id=neutral_case_id,
            )
            bundle.record.provider_sessions = (
                evaluation.budget.used_sessions - before_sessions
            )
            bundle.record.tokens_used = _tokens_for_attempts(
                evaluation.call_records,
                [result.attempt_id for result in bundle.results],
            )
            evaluation.topology_records.append(bundle.record)
            bundles[(public_case.case_id, topology)] = bundle

    ground_truth = load_ground_truth(
        ground_truth_path,
        expected_sha256=ground_truth_sha256,
    )
    topology_summaries = _score_results(
        evaluation,
        public_dataset=public_dataset,
        ground_truth=ground_truth,
        bundles=bundles,
    )
    if runner_mode == "real":
        triggers = _build_repeat_triggers(
            public_dataset,
            bundles=bundles,
        )
        _execute_repeat_triggers(
            evaluation,
            triggers=triggers,
            public_dataset=public_dataset,
            ground_truth=ground_truth,
            fixtures_by_case=fixtures_by_case,
            neutral_ids=neutral_ids,
            dataset_sha256=dataset_sha256,
            reviewer_runner=reviewer_runner,
            timeout_seconds=timeout_seconds,
            bundles=bundles,
        )
    topology_costs = _build_topology_costs(
        evaluation.topology_records,
        evaluation.call_records,
        topology_summaries,
    )
    decision = _decide_topology(
        topology_summaries,
        repeat_outcomes=evaluation.repeat_outcomes,
        topology_costs=topology_costs,
    )
    if runner_mode == "fake":
        decision = "dry-run-passed"
    return topology_summaries, decision


def _run_real_preflight(
    evaluation: EvaluationRun,
    *,
    model: str,
    reviewer_reasoning: str,
    expected_provider: str,
    expected_auth_mode: Literal["api_key", "chatgpt"],
    expected_codex_version: str,
    provider: CodexProviderDescriptor,
    windows_sandbox_session_override: Literal["elevated"],
    timeout_seconds: int,
) -> dict[str, object]:
    session_number = evaluation.budget.reserve_session()
    preflight_fixture_root = (
        evaluation.fixture_session_root / "provider-preflight-fixtures"
    )
    preflight_output_root = evaluation.output_dir / "provider-preflight"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "langgraph_core_dogfood.py"),
        "--runner",
        "real",
        "--preflight-only",
        "--session",
        "provider",
        "--model",
        model,
        "--worker-reasoning",
        reviewer_reasoning,
        "--reviewer-reasoning",
        reviewer_reasoning,
        "--ignore-user-config",
        "--windows-sandbox-session-override",
        windows_sandbox_session_override,
        "--expected-provider",
        expected_provider,
        "--expected-auth-mode",
        expected_auth_mode,
        "--provider-base-url",
        provider.base_url,
        "--provider-wire-api",
        provider.wire_api,
        "--provider-requires-openai-auth",
        str(provider.requires_openai_auth).lower(),
        "--provider-supports-websockets",
        str(provider.supports_websockets).lower(),
        "--expected-codex-version",
        expected_codex_version,
        "--preflight-timeout-seconds",
        str(timeout_seconds),
        "--fixture-root",
        str(preflight_fixture_root),
        "--output-root",
        str(preflight_output_root),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            check=False,
            timeout=timeout_seconds + 60,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        evaluation.call_records.append(
            ProviderCallRecord(
                session_number=session_number,
                run_id="provider-preflight",
                reviewer_role="provider-preflight",
                attempt_id="provider-preflight",
                status="timed_out",
                elapsed_seconds=round(elapsed, 3),
                tokens_used=None,
                prompt_chars=None,
            )
        )
        return {
            "conclusion": "blocked",
            "reason": "provider preflight 超时",
            "diagnostics": ["preflight subprocess timed out"],
        }
    elapsed = time.monotonic() - started
    summary_path = (
        preflight_output_root / "provider" / "summary.json"
    )
    if not summary_path.is_file():
        evaluation.call_records.append(
            ProviderCallRecord(
                session_number=session_number,
                run_id="provider-preflight",
                reviewer_role="provider-preflight",
                attempt_id="provider-preflight",
                status="error",
                elapsed_seconds=round(elapsed, 3),
                tokens_used=None,
                prompt_chars=None,
            )
        )
        return {
            "conclusion": "blocked",
            "returncode": completed.returncode,
            "diagnostics": _safe_process_diagnostics(completed),
            "reason": "provider preflight 未生成 summary.json",
        }
    payload = _load_json(summary_path)
    if not isinstance(payload, dict):
        raise ValueError("provider preflight summary 必须是对象")
    payload = dict(payload)
    payload["returncode"] = completed.returncode
    payload["diagnostics"] = _safe_process_diagnostics(completed)
    observed = payload.get("preflight")
    if not isinstance(observed, dict):
        evaluation.call_records.append(
            ProviderCallRecord(
                session_number=session_number,
                run_id="provider-preflight",
                reviewer_role="provider-preflight",
                attempt_id="provider-preflight",
                status="unknown",
                elapsed_seconds=round(elapsed, 3),
                tokens_used=None,
                prompt_chars=None,
            )
        )
        payload["conclusion"] = "blocked"
        payload["reason"] = "provider preflight 缺少结构化身份"
        return payload
    preflight_output = (
        preflight_output_root
        / "provider"
        / "preflight"
        / "execution"
        / "process-output.txt"
    )
    tokens_used = (
        _parse_tokens_used(
            preflight_output.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )
        if preflight_output.is_file()
        else None
    )
    evaluation.call_records.append(
        ProviderCallRecord(
            session_number=session_number,
            run_id="provider-preflight",
            reviewer_role="provider-preflight",
            attempt_id="provider-preflight",
            status=str(observed.get("runner_status") or "unknown"),
            elapsed_seconds=round(elapsed, 3),
            tokens_used=tokens_used,
            prompt_chars=None,
        )
    )
    checks = {
        "expected_provider": expected_provider,
        "observed_provider": expected_provider,
        "observed_model": model,
        "observed_codex_version": expected_codex_version,
        "observed_auth_mode": expected_auth_mode,
        "auth_mode_valid": True,
        "command_shape_valid": True,
        "repo_clean": True,
        "execution_valid": True,
    }
    mismatches = [
        f"{key}={observed.get(key)!r}, expected={expected!r}"
        for key, expected in checks.items()
        if observed.get(key) != expected
    ]
    if completed.returncode != 0 or mismatches:
        payload["conclusion"] = "blocked"
        payload["reason"] = (
            "provider preflight identity 不一致："
            + "; ".join(mismatches)
        )
    return payload


def _prepare_fixture(
    fixture_session_root: Path,
    public_case: ReviewerTopologyPublicCase,
    *,
    neutral_case_id: str,
) -> FixtureRecord:
    case_root = fixture_session_root / neutral_case_id
    repo_path = case_root / "repo"
    if case_root.exists():
        raise FileExistsError(f"fixture case 已存在：{public_case.case_id}")
    repo_path.mkdir(parents=True)
    before_files = dict(public_case.before_files)
    before_files.setdefault(
        ".gitignore",
        "__pycache__/\n*.py[cod]\n.pytest_cache/\n",
    )
    before_files.setdefault("src/__init__.py", "")
    before_files.setdefault("tests/__init__.py", "")
    for relative, content in before_files.items():
        _write_fixture_file(repo_path, relative, content)
    _run_git(repo_path, "init", "-b", "main")
    _run_git(repo_path, "config", "user.name", "Vega Gate 5.5")
    _run_git(
        repo_path,
        "config",
        "user.email",
        "gate-5.5@vega.invalid",
    )
    _run_git(repo_path, "add", "--all")
    _run_git(
        repo_path,
        "commit",
        "-m",
        f"fixture baseline {neutral_case_id}",
    )
    head_sha = _git_output(repo_path, "rev-parse", "HEAD")

    for relative, content in public_case.after_files.items():
        _write_fixture_file(repo_path, relative, content)
    verification = dict(public_case.verification)
    command = verification.get("command")
    expected_exit_code = verification.get("expected_exit_code")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"{public_case.case_id} verification.command 不合法")
    if not isinstance(expected_exit_code, int):
        raise ValueError(
            f"{public_case.case_id} verification.expected_exit_code 不合法"
        )
    started = time.monotonic()
    completed = subprocess.run(
        _verification_argv(command),
        cwd=repo_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
        timeout=60,
    )
    elapsed = time.monotonic() - started
    output = redact_text(
        (completed.stdout or "") + (completed.stderr or "")
    )
    if completed.returncode != expected_exit_code:
        raise RuntimeError(
            f"{public_case.case_id} verification 失败："
            f"returncode={completed.returncode}\n{output[-4000:]}"
        )
    workspace = capture_review_workspace(repo_path)
    routing = dict(public_case.routing_facts)
    declared_changed_files = routing.get("changed_files")
    if (
        not isinstance(declared_changed_files, list)
        or list(workspace.changed_files) != declared_changed_files
    ):
        raise ValueError(
            f"{public_case.case_id} routing changed_files 与实际 diff 不一致："
            f"declared={declared_changed_files!r}, "
            f"actual={list(workspace.changed_files)!r}"
        )
    return FixtureRecord(
        case_id=public_case.case_id,
        neutral_case_id=neutral_case_id,
        repo_path=repo_path,
        head_sha=head_sha,
        workspace=workspace,
        verification_command=command,
        verification_output=output,
        verification_elapsed_seconds=round(elapsed, 3),
    )


def _prepare_case_evidence(
    run_dir: Path,
    *,
    public_case: ReviewerTopologyPublicCase,
    fixture: FixtureRecord,
    dataset_id: str,
    dataset_sha256: str,
    ground_truth_sha256: str,
    neutral_case_id: str,
):
    evidence_dir = run_dir / "evidence"
    policy_ref = "evidence/project-policy-snapshot.json"
    verification_ref = "evidence/verification-result.json"
    risk_ref = "evidence/risk-result.json"
    acceptance_ref = "evidence/acceptance-evidence-manifest.json"
    routing_payload = dict(public_case.routing_facts)
    verification_status = routing_payload.get("verification_status")
    verification_failed_count = routing_payload.get(
        "verification_failed_count"
    )
    risk = routing_payload.get("risk")
    changed_files = routing_payload.get("changed_files")
    gate_reason_codes = routing_payload.get("gate_reason_codes")
    policy_payload = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "case_id": neutral_case_id,
        "reviewer_topologies": list(TOPOLOGIES),
        "read_only": True,
        "memory": "off",
        "automatic_retries": 0,
    }
    verification_payload = {
        "schema_version": 1,
        "status": verification_status,
        "failed_count": verification_failed_count,
        "command": fixture.verification_command,
        "returncode": 0,
        "elapsed_seconds": fixture.verification_elapsed_seconds,
        "output_sha256": hashlib.sha256(
            fixture.verification_output.encode("utf-8")
        ).hexdigest(),
    }
    risk_payload = {
        "schema_version": 1,
        "risk": risk,
        "gate_reason_codes": gate_reason_codes,
        "changed_files": changed_files,
    }
    acceptance_payload = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "ground_truth_commitment_sha256": ground_truth_sha256,
        "case_id": neutral_case_id,
        "task": public_case.task,
        "acceptance": list(public_case.acceptance),
        "fixture_head": fixture.head_sha,
        "workspace_diff_sha256": fixture.workspace.full_diff_sha256,
    }
    write_redacted_json_atomic(evidence_dir / "project-policy-snapshot.json", policy_payload)
    write_redacted_json_atomic(evidence_dir / "verification-result.json", verification_payload)
    write_redacted_json_atomic(evidence_dir / "risk-result.json", risk_payload)
    write_redacted_json_atomic(
        evidence_dir / "acceptance-evidence-manifest.json",
        acceptance_payload,
    )
    snapshot = build_review_evidence_snapshot_from_artifacts(
        run_dir,
        iteration=1,
        workspace_fingerprint=f"sha256:{fixture.workspace.fingerprint}",
        policy_snapshot_ref=policy_ref,
        verification_result_ref=verification_ref,
        risk_result_ref=risk_ref,
        acceptance_evidence_manifest_ref=acceptance_ref,
    )
    routing = ParallelReviewRoutingContext.model_validate(
        {
            "run_id": run_dir.name,
            "iteration": 1,
            "evidence_snapshot_sha256": snapshot.evidence_snapshot_sha256,
            "verification_status": verification_status,
            "verification_failed_count": verification_failed_count,
            "risk": risk,
            "changed_files": changed_files,
            "gate_reason_codes": gate_reason_codes,
        }
    )
    public_evidence = _render_public_evidence(
        public_case,
        fixture=fixture,
        dataset_id=dataset_id,
        dataset_sha256=dataset_sha256,
        ground_truth_sha256=ground_truth_sha256,
        neutral_case_id=neutral_case_id,
    )
    evidence = prepare_parallel_review_evidence(
        snapshot,
        public_evidence,
        forbidden_markers=[
            GROUND_TRUTH_FORBIDDEN_MARKER,
            public_case.case_id,
            *_FROZEN_RULE_IDS,
        ],
    )
    return evidence, routing


def _execute_topology(
    *,
    run_dir: Path,
    repo_path: Path,
    public_case: ReviewerTopologyPublicCase,
    topology: ReviewTopology,
    routing: ParallelReviewRoutingContext,
    evidence,
    runner: Runner,
    timeout_seconds: int,
    ground_truth_sha256: str,
    neutral_case_id: str,
    replicate: int = 0,
    comparison_kind: str | None = None,
) -> _ExecutionBundle:
    plan = build_parallel_review_plan(routing, topology=topology)
    budget = getattr(runner, "budget", None)
    if (
        budget is not None
        and budget.remaining_sessions < len(plan.required_roles)
    ):
        raise RuntimeError(
            "剩余 provider session 预算不足以启动完整 topology"
        )
    all_canaries = {
        (
            candidate_replicate,
            candidate_topology,
            role,
        ): (
            f"GATE55-{neutral_case_id}-r{candidate_replicate}-"
            f"{candidate_topology}-{role}-"
            f"{ground_truth_sha256[:12]}"
        )
        for candidate_replicate in (0, 1)
        for candidate_topology in TOPOLOGIES
        for role in AVAILABLE_REVIEWER_ROLES
    }
    canaries: dict[ReviewerRole, str] = {
        role: all_canaries[(replicate, topology, role)]
        for role in AVAILABLE_REVIEWER_ROLES
    }
    guarded_runners: dict[ReviewerRole, Runner] = {
        role: PromptGuardRunner(
            runner,
            neutral_case_id=neutral_case_id,
            forbidden_markers=(
                public_case.case_id,
                *_FROZEN_RULE_IDS,
                *(
                    marker
                    for identity, marker in all_canaries.items()
                    if identity != (replicate, topology, role)
                ),
            ),
        )
        for role in AVAILABLE_REVIEWER_ROLES
    }
    executors = build_runner_parallel_review_executors(
        repo_path=repo_path,
        runners=guarded_runners,
        evidence=evidence,
        timeout_seconds=timeout_seconds,
        private_canaries=canaries,
    )
    context = ParallelReviewAggregationContext(
        run_id=run_dir.name,
        iteration=1,
        evidence_snapshot_sha256=evidence.snapshot.evidence_snapshot_sha256,
        review_plan=plan,
        verification_status=routing.verification_status,
        verification_failed_count=routing.verification_failed_count,
        risk=routing.risk,
        # Gate 5.5 只评 Reviewer 信息增益，不执行高风险业务动作。
        human_approval_valid=True,
    )
    started = time.monotonic()
    graph_run = execute_parallel_review_graph(
        run_dir,
        context=context,
        executors=executors,
    )
    elapsed = time.monotonic() - started
    results = [
        read_parallel_review_result(run_dir, result_ref)
        for result_ref in graph_run.result_refs
    ]
    if len(results) != len(plan.required_roles):
        raise RuntimeError("topology 未形成完整 Reviewer result 集合")
    non_completed = {
        result.reviewer_role: result.status
        for result in results
        if result.status != "completed"
    }
    if non_completed:
        raise RuntimeError(
            f"topology Reviewer 未形成可信 completed 终态：{non_completed}"
        )
    rebuilt = aggregate_parallel_reviews(context, results)
    if rebuilt != graph_run.aggregate:
        raise EvaluationContractViolation(
            "Reviewer aggregate 无法从结构化 results 确定性重建"
        )
    _validate_canary_isolation(
        results,
        graph_run.aggregate,
        topology=topology,
        replicate=replicate,
        all_canaries=all_canaries,
    )
    _audit_process_output_isolation(run_dir, results)
    after_workspace = capture_review_workspace(repo_path)
    if (
        f"sha256:{after_workspace.fingerprint}"
        != evidence.snapshot.workspace_fingerprint
    ):
        raise EvaluationContractViolation(
            "Reviewer topology 执行后 fixture workspace 发生变化"
        )
    record = TopologyExecutionRecord(
        case_id=public_case.case_id,
        topology=topology,
        replicate=replicate,
        comparison_kind=comparison_kind,
        review_plan_id=plan.plan_id,
        evidence_snapshot_sha256=(
            evidence.snapshot.evidence_snapshot_sha256
        ),
        public_evidence_sha256=evidence.public_evidence_sha256,
        required_roles=list(plan.required_roles),
        aggregate_verdict=graph_run.aggregate.verdict,
        aggregate_reasons=list(graph_run.aggregate.reasons),
        aggregate_sha256=graph_run.aggregate.aggregate_sha256,
        result_ids=[result.result_id for result in results],
        attempt_ids=[result.attempt_id for result in results],
        result_statuses={
            result.reviewer_role: result.status for result in results
        },
        elapsed_seconds=elapsed,
        provider_sessions=0,
        tokens_used=None,
    )
    return _ExecutionBundle(
        record=record,
        results=results,
        aggregate=graph_run.aggregate,
    )


def _score_results(
    evaluation: EvaluationRun,
    *,
    public_dataset: ReviewerTopologyPublicDataset,
    ground_truth: ReviewerTopologyGroundTruthDataset,
    bundles: dict[tuple[str, ReviewTopology], _ExecutionBundle],
) -> dict[str, ReviewerTopologySummary]:
    if public_dataset.dataset_id != ground_truth.dataset_id:
        raise ValueError("public dataset 与 ground truth dataset_id 不一致")
    ReviewerTopologyEvaluationDataset(
        public_dataset=public_dataset,
        ground_truth=ground_truth,
    )
    truth_by_id = {case.case_id: case for case in ground_truth.cases}
    public_by_id = {case.case_id: case for case in public_dataset.cases}
    if set(truth_by_id) != set(public_by_id):
        raise ValueError("ground truth 未精确覆盖全部 public case")

    scores_by_topology: dict[
        ReviewTopology,
        list[ReviewerTopologyCaseScore],
    ] = {topology: [] for topology in TOPOLOGIES}
    single_scores: dict[str, ReviewerTopologyCaseScore] = {}
    for public_case in public_dataset.cases:
        single_bundle = bundles[(public_case.case_id, "single")]
        truth = truth_by_id[public_case.case_id]
        score = score_reviewer_topology_case(
            public_case=public_case,
            ground_truth=truth,
            topology="single",
            results=single_bundle.results,
            aggregate=single_bundle.aggregate,
        )
        single_bundle.record.score = score
        single_scores[public_case.case_id] = score
        scores_by_topology["single"].append(score)

    for topology in ("fixed_three", "adaptive"):
        for case_id, public_case in public_by_id.items():
            bundle = bundles[(case_id, topology)]
            truth = truth_by_id[case_id]
            score = score_reviewer_topology_case(
                public_case=public_case,
                ground_truth=truth,
                topology=topology,
                results=bundle.results,
                aggregate=bundle.aggregate,
                single_true_positive_finding_ids=(
                    single_scores[case_id].true_positive_finding_ids
                ),
            )
            bundle.record.score = score
            scores_by_topology[topology].append(score)

    summaries = {
        topology: summarize_reviewer_topology(
            scores,
            single_scores=(
                None if topology == "single" else single_scores
            ),
        )
        for topology, scores in scores_by_topology.items()
    }
    return summaries


def _build_repeat_triggers(
    public_dataset: ReviewerTopologyPublicDataset,
    *,
    bundles: dict[tuple[str, ReviewTopology], _ExecutionBundle],
) -> list[RepeatTrigger]:
    collected: dict[
        tuple[str, str],
        dict[str, object],
    ] = {}
    order = {
        "adaptive-vs-single": 0,
        "fixed-three-vs-single": 1,
        "fixed-three-vs-adaptive": 2,
    }

    def add_trigger(
        *,
        case_id: str,
        case_index: int,
        comparison_kind: str,
        topologies: tuple[ReviewTopology, ReviewTopology],
        finding_ids: set[str],
        reason: str,
    ) -> None:
        key = (case_id, comparison_kind)
        existing = collected.setdefault(
            key,
            {
                "case_index": case_index,
                "topologies": topologies,
                "finding_ids": set(),
                "reasons": set(),
            },
        )
        existing["finding_ids"].update(finding_ids)  # type: ignore[union-attr]
        existing["reasons"].add(reason)  # type: ignore[union-attr]

    for case_index, public_case in enumerate(public_dataset.cases):
        case_id = public_case.case_id
        scores = {
            topology: bundles[(case_id, topology)].record.score
            for topology in TOPOLOGIES
        }
        if any(score is None for score in scores.values()):
            raise RuntimeError("初始 topology score 尚未完成")
        single = scores["single"]
        adaptive = scores["adaptive"]
        fixed = scores["fixed_three"]
        assert single is not None
        assert adaptive is not None
        assert fixed is not None
        single_major = set(
            single.true_positive_blocker_major_finding_ids
        )
        adaptive_major = set(
            adaptive.true_positive_blocker_major_finding_ids
        )
        fixed_major = set(
            fixed.true_positive_blocker_major_finding_ids
        )
        adaptive_unique = adaptive_major - single_major
        if adaptive_unique:
            add_trigger(
                case_id=case_id,
                case_index=case_index,
                comparison_kind="adaptive-vs-single",
                topologies=("adaptive", "single"),
                finding_ids=adaptive_unique,
                reason="adaptive unique true blocker/major",
            )
        fixed_unique_single = fixed_major - single_major
        if fixed_unique_single:
            add_trigger(
                case_id=case_id,
                case_index=case_index,
                comparison_kind="fixed-three-vs-single",
                topologies=("fixed_three", "single"),
                finding_ids=fixed_unique_single,
                reason="fixed_three unique true blocker/major",
            )
        fixed_unique_adaptive = fixed_major - adaptive_major
        if fixed_unique_adaptive:
            add_trigger(
                case_id=case_id,
                case_index=case_index,
                comparison_kind="fixed-three-vs-adaptive",
                topologies=("fixed_three", "adaptive"),
                finding_ids=fixed_unique_adaptive,
                reason="fixed_three/adaptive decision-relevant hit difference",
            )
        if public_case.case_kind == "clean":
            if (
                adaptive.clean_has_false_blocker
                or adaptive.clean_false_major_count
                > single.clean_false_major_count
            ):
                add_trigger(
                    case_id=case_id,
                    case_index=case_index,
                    comparison_kind="adaptive-vs-single",
                    topologies=("adaptive", "single"),
                    finding_ids={"clean-false-major"},
                    reason="adaptive clean false blocker/major",
                )
            if (
                fixed.clean_has_false_blocker
                or fixed.clean_false_major_count
                > adaptive.clean_false_major_count
            ):
                add_trigger(
                    case_id=case_id,
                    case_index=case_index,
                    comparison_kind="fixed-three-vs-adaptive",
                    topologies=("fixed_three", "adaptive"),
                    finding_ids={"clean-false-major"},
                    reason="fixed_three clean false blocker/major",
                )

    triggers = [
        RepeatTrigger(
            case_id=case_id,
            case_index=int(payload["case_index"]),
            comparison_kind=comparison_kind,  # type: ignore[arg-type]
            topologies=payload["topologies"],  # type: ignore[arg-type]
            finding_ids=tuple(sorted(payload["finding_ids"])),  # type: ignore[arg-type]
            reason="; ".join(sorted(payload["reasons"])),  # type: ignore[arg-type]
        )
        for (case_id, comparison_kind), payload in collected.items()
    ]
    return sorted(
        triggers,
        key=lambda item: (
            item.case_index,
            order[item.comparison_kind],
            item.finding_ids,
        ),
    )


def _execute_repeat_triggers(
    evaluation: EvaluationRun,
    *,
    triggers: list[RepeatTrigger],
    public_dataset: ReviewerTopologyPublicDataset,
    ground_truth: ReviewerTopologyGroundTruthDataset,
    fixtures_by_case: dict[str, FixtureRecord],
    neutral_ids: dict[str, str],
    dataset_sha256: str,
    reviewer_runner: Runner,
    timeout_seconds: int,
    bundles: dict[tuple[str, ReviewTopology], _ExecutionBundle],
) -> None:
    public_by_id = {case.case_id: case for case in public_dataset.cases}
    truth_by_id = {case.case_id: case for case in ground_truth.cases}
    repeat_contexts: dict[str, tuple[Path, object, ParallelReviewRoutingContext]] = {}
    repeat_bundles: dict[
        tuple[str, ReviewTopology],
        _ExecutionBundle,
    ] = {}
    stopped_for_budget = False
    for trigger in triggers:
        if stopped_for_budget:
            evaluation.repeat_outcomes.append(
                RepeatOutcome(
                    trigger=trigger,
                    completed=False,
                    unresolved_reason=(
                        "前一个 comparison bundle 已因预算不足停止复跑"
                    ),
                )
            )
            continue
        public_case = public_by_id[trigger.case_id]
        missing_topologies = [
            topology
            for topology in trigger.topologies
            if (trigger.case_id, topology) not in repeat_bundles
        ]
        required_sessions = sum(
            _reviewer_count(public_case, topology)
            for topology in missing_topologies
        )
        if evaluation.budget.remaining_sessions < required_sessions:
            stopped_for_budget = True
            evaluation.repeat_outcomes.append(
                RepeatOutcome(
                    trigger=trigger,
                    completed=False,
                    unresolved_reason=(
                        "下一个完整 comparison bundle 超过剩余预算"
                    ),
                )
            )
            continue
        if trigger.case_id not in repeat_contexts:
            neutral_case_id = neutral_ids[trigger.case_id]
            repeat_run_dir = _new_repeat_run_root(
                evaluation.run_root,
                session=evaluation.session,
                neutral_case_id=neutral_case_id,
            )
            evidence, routing = _prepare_case_evidence(
                repeat_run_dir,
                public_case=public_case,
                fixture=fixtures_by_case[trigger.case_id],
                dataset_id=public_dataset.dataset_id,
                dataset_sha256=dataset_sha256,
                ground_truth_sha256=evaluation.ground_truth_sha256,
                neutral_case_id=neutral_case_id,
            )
            repeat_contexts[trigger.case_id] = (
                repeat_run_dir,
                evidence,
                routing,
            )
        repeat_run_dir, evidence, routing = repeat_contexts[trigger.case_id]
        for topology in missing_topologies:
            before_sessions = evaluation.budget.used_sessions
            bundle = _execute_topology(
                run_dir=repeat_run_dir,
                repo_path=fixtures_by_case[trigger.case_id].repo_path,
                public_case=public_case,
                topology=topology,
                routing=routing,
                evidence=evidence,
                runner=reviewer_runner,
                timeout_seconds=timeout_seconds,
                ground_truth_sha256=evaluation.ground_truth_sha256,
                neutral_case_id=neutral_ids[trigger.case_id],
                replicate=1,
                comparison_kind=trigger.comparison_kind,
            )
            bundle.record.provider_sessions = (
                evaluation.budget.used_sessions - before_sessions
            )
            bundle.record.tokens_used = _tokens_for_attempts(
                evaluation.call_records,
                [result.attempt_id for result in bundle.results],
            )
            evaluation.topology_records.append(bundle.record)
            repeat_bundles[(trigger.case_id, topology)] = bundle

        truth = truth_by_id[trigger.case_id]
        scored: dict[ReviewTopology, ReviewerTopologyCaseScore] = {}
        for topology in trigger.topologies:
            bundle = repeat_bundles[(trigger.case_id, topology)]
            score = score_reviewer_topology_case(
                public_case=public_case,
                ground_truth=truth,
                topology=topology,
                results=bundle.results,
                aggregate=bundle.aggregate,
            )
            bundle.record.score = score
            scored[topology] = score

        candidate_topology, comparison_topology = trigger.topologies
        candidate = scored[candidate_topology]
        comparison = scored[comparison_topology]
        initial_candidate = bundles[
            (trigger.case_id, candidate_topology)
        ].record.score
        initial_comparison = bundles[
            (trigger.case_id, comparison_topology)
        ].record.score
        assert initial_candidate is not None
        assert initial_comparison is not None
        if trigger.finding_ids == ("clean-false-major",):
            reproduced = (
                ["clean-false-major"]
                if (
                    candidate.clean_has_false_blocker
                    or candidate.clean_has_false_major
                )
                else []
            )
            comparison_missed = (
                ["clean-false-major"]
                if not (
                    comparison.clean_has_false_blocker
                    or comparison.clean_has_false_major
                )
                else []
            )
        else:
            candidate_major = set(
                candidate.true_positive_blocker_major_finding_ids
            )
            comparison_major = set(
                comparison.true_positive_blocker_major_finding_ids
            )
            reproduced = sorted(
                set(trigger.finding_ids).intersection(candidate_major)
            )
            comparison_missed = sorted(
                set(trigger.finding_ids) - comparison_major
            )
        evaluation.repeat_outcomes.append(
            RepeatOutcome(
                trigger=trigger,
                completed=True,
                unresolved_reason=None,
                candidate_reproduced_finding_ids=reproduced,
                comparison_missed_finding_ids=comparison_missed,
                verdict_flip=(
                    candidate.actual_verdict
                    != initial_candidate.actual_verdict
                    or comparison.actual_verdict
                    != initial_comparison.actual_verdict
                ),
            )
        )


def _reviewer_count(
    public_case: ReviewerTopologyPublicCase,
    topology: ReviewTopology,
) -> int:
    context = ParallelReviewRoutingContext.model_validate(
        {
            "run_id": "gate55-repeat-readiness",
            "iteration": 1,
            "evidence_snapshot_sha256": "0" * 64,
            **dict(public_case.routing_facts),
        }
    )
    return len(build_parallel_review_plan(context, topology=topology).required_roles)


def _validate_canary_isolation(
    results: list[ParallelReviewResult],
    aggregate: object,
    *,
    topology: ReviewTopology,
    replicate: int,
    all_canaries: dict[
        tuple[int, ReviewTopology, ReviewerRole],
        str,
    ],
) -> None:
    for result in results:
        serialized = result.model_dump_json()
        own_canary = all_canaries[
            (replicate, topology, result.reviewer_role)
        ]
        forbidden = [
            marker
            for marker in all_canaries.values()
            if marker != own_canary
        ]
        if any(marker in serialized for marker in forbidden):
            raise EvaluationContractViolation(
                "Reviewer result 包含其他角色、topology 或 replicate canary"
            )
    aggregate_json = aggregate.model_dump_json()  # type: ignore[attr-defined]
    if any(marker in aggregate_json for marker in all_canaries.values()):
        raise EvaluationContractViolation(
            "Reviewer private canary 泄漏进入 aggregate"
        )


def _audit_process_output_isolation(
    run_dir: Path,
    results: list[ParallelReviewResult],
) -> None:
    forbidden_markers = (
        "ground-truth.json",
        "eval/gate-5.5",
        "gate-5.5-pre-registration.md",
        *_FROZEN_EVALUATOR_CASE_IDS,
    )
    for result in results:
        output_ref = PurePosixPath(result.execution_ref).with_name(
            "process-output.txt"
        )
        output_path = run_dir.joinpath(*output_ref.parts)
        text = output_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).lower().replace("\\", "/")
        leaked = next(
            marker
            for marker in forbidden_markers
            if marker.lower() in text
        ) if any(
            marker.lower() in text for marker in forbidden_markers
        ) else None
        if leaked is not None:
            raise EvaluationContractViolation(
                "Reviewer process output 显示访问 evaluator 私有路径或身份"
            )


def _decide_topology(
    summaries: dict[str, ReviewerTopologySummary],
    *,
    repeat_outcomes: list[RepeatOutcome],
    topology_costs: dict[str, object],
) -> str:
    single = summaries["single"]
    adaptive = summaries["adaptive"]
    fixed = summaries["fixed_three"]
    if any(
        not isinstance(topology_costs.get(topology), dict)
        or not topology_costs[topology].get("complete")  # type: ignore[union-attr]
        for topology in TOPOLOGIES
    ):
        return "blocked"
    if any(
        not outcome.completed or outcome.verdict_flip
        for outcome in repeat_outcomes
    ):
        return "no-stable-winner"
    if (
        adaptive.unique_true_positive_blocker_major_count == 0
        and fixed.unique_true_positive_blocker_major_count == 0
    ):
        return "single-wins"
    adaptive_candidate = (
        adaptive.blocker_major_recall >= single.blocker_major_recall
        and adaptive.finding_recall >= single.finding_recall
        and adaptive.finding_precision >= single.finding_precision
        and adaptive.verdict_accuracy >= single.verdict_accuracy
        and adaptive.unique_true_positive_blocker_major_count >= 1
        and adaptive.clean_false_blocker_case_count == 0
        and adaptive.clean_false_major_case_count
        <= single.clean_false_major_case_count
    )
    fixed_initial_advantage = any(
        outcome.trigger.comparison_kind == "fixed-three-vs-adaptive"
        for outcome in repeat_outcomes
    )
    fixed_candidate = (
        fixed_initial_advantage
        and fixed.blocker_major_recall >= adaptive.blocker_major_recall
        and fixed.finding_recall >= adaptive.finding_recall
        and fixed.finding_precision >= adaptive.finding_precision
        and fixed.verdict_accuracy >= adaptive.verdict_accuracy
        and fixed.clean_false_blocker_case_count == 0
        and fixed.clean_false_major_case_count
        <= adaptive.clean_false_major_case_count
    )
    adaptive_reproduced = any(
        outcome.trigger.comparison_kind == "adaptive-vs-single"
        and outcome.completed
        and bool(
            set(outcome.trigger.finding_ids)
            & set(outcome.candidate_reproduced_finding_ids)
            & set(outcome.comparison_missed_finding_ids)
        )
        for outcome in repeat_outcomes
    )
    fixed_reproduced = any(
        outcome.trigger.comparison_kind == "fixed-three-vs-adaptive"
        and outcome.completed
        and bool(
            set(outcome.trigger.finding_ids)
            & set(outcome.candidate_reproduced_finding_ids)
            & set(outcome.comparison_missed_finding_ids)
        )
        for outcome in repeat_outcomes
    )
    if fixed_candidate:
        return "no-stable-winner" if fixed_reproduced else "single-wins"
    adaptive_cost = topology_costs["adaptive"]
    fixed_cost = topology_costs["fixed_three"]
    assert isinstance(adaptive_cost, dict)
    assert isinstance(fixed_cost, dict)
    adaptive_cheaper = (
        adaptive_cost["provider_sessions"] < fixed_cost["provider_sessions"]
        and adaptive_cost["total_tokens"] < fixed_cost["total_tokens"]
    )
    if adaptive_candidate and adaptive_reproduced and adaptive_cheaper:
        return "adaptive-wins"
    return "single-wins"


def _build_summary(
    evaluation: EvaluationRun,
    *,
    model: str,
    reviewer_reasoning: str,
    expected_provider: str,
    expected_auth_mode: str,
    expected_codex_version: str,
    provider: CodexProviderDescriptor,
    preflight: dict[str, object],
    topology_summaries: dict[str, ReviewerTopologySummary],
    decision: str,
    elapsed_seconds: float,
) -> dict[str, object]:
    branch = _git_output(PROJECT_ROOT, "branch", "--show-current")
    head = _git_output(PROJECT_ROOT, "rev-parse", "HEAD")
    call_records = sorted(
        evaluation.call_records,
        key=lambda item: item.session_number,
    )
    total_tokens = sum(
        item.tokens_used or 0 for item in call_records
    )
    token_observation_complete = bool(call_records) and all(
        item.tokens_used is not None for item in call_records
    )
    fixed_three_quality_advantage = any(
        outcome.trigger.comparison_kind == "fixed-three-vs-adaptive"
        and outcome.completed
        and not outcome.verdict_flip
        and bool(
            set(outcome.trigger.finding_ids)
            & set(outcome.candidate_reproduced_finding_ids)
            & set(outcome.comparison_missed_finding_ids)
        )
        for outcome in evaluation.repeat_outcomes
    )
    initial_reviewer_sessions = sum(
        len(record.required_roles)
        for record in evaluation.topology_records
        if record.replicate == 0
    )
    repeat_reviewer_sessions = sum(
        len(record.required_roles)
        for record in evaluation.topology_records
        if record.replicate == 1
    )
    payload: dict[str, object] = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "session": evaluation.session,
        "date": _current_local_date(),
        "timezone": "Asia/Shanghai",
        "runner_mode": evaluation.runner_mode,
        "decision": decision,
        "default_topology": "single",
        "decision_state": {
            "experiment_conclusion": decision,
            "default_topology": "single",
            "fixed_three_quality_advantage_observed": (
                fixed_three_quality_advantage
            ),
            "owner_cost_confirmation_required": (
                fixed_three_quality_advantage
                and decision == "no-stable-winner"
            ),
        },
        "branch": branch,
        "head": head,
        "dataset_id": evaluation.public_dataset.dataset_id,
        "dataset_sha256": evaluation.dataset_sha256,
        "ground_truth_sha256": evaluation.ground_truth_sha256,
        "case_count": len(evaluation.public_dataset.cases),
        "topologies": list(TOPOLOGIES),
        "model": model,
        "reviewer_reasoning": reviewer_reasoning,
        "sandbox": "read-only",
        "ephemeral": True,
        "memory": "off",
        "automatic_retries": 0,
        "expected_provider": expected_provider,
        "expected_auth_mode": expected_auth_mode,
        "expected_codex_version": expected_codex_version,
        "provider_descriptor": provider.model_dump(mode="json", exclude_none=True),
        "provider_descriptor_sha256": (
            codex_provider_descriptor_sha256(provider)
        ),
        "preflight": preflight,
        "provider_session_budget": {
            "used": evaluation.budget.used_sessions,
            "max": evaluation.budget.max_sessions,
            "remaining": evaluation.budget.remaining_sessions,
            "initial_reviewer_sessions": initial_reviewer_sessions,
            "repeat_reviewer_sessions": repeat_reviewer_sessions,
        },
        "provider_calls": [
            {
                "session_number": item.session_number,
                "run_id": item.run_id,
                "reviewer_role": item.reviewer_role,
                "attempt_id": item.attempt_id,
                "status": item.status,
                "elapsed_seconds": item.elapsed_seconds,
                "tokens_used": item.tokens_used,
                "prompt_chars": item.prompt_chars,
            }
            for item in call_records
        ],
        "total_observed_tokens": (
            total_tokens if call_records else None
        ),
        "token_observation_complete": token_observation_complete,
        "safety_metrics": {
            "reviewer_context_leak": 0 if not evaluation.failures else None,
            "workspace_writes_by_reviewer": (
                0 if not evaluation.failures else None
            ),
            "ground_truth_prompt_leak": (
                0 if not evaluation.failures else None
            ),
            "cross_topology_output_leak": (
                0 if not evaluation.failures else None
            ),
            "cross_replicate_output_leak": (
                0 if not evaluation.failures else None
            ),
            "aggregate_reconstruction_mismatch": (
                0 if not evaluation.failures else None
            ),
        },
        "topology_summaries": {
            topology: summary.model_dump(mode="json")
            for topology, summary in topology_summaries.items()
        },
        "category_summaries": _build_category_summaries(
            evaluation.topology_records,
            evaluation.public_dataset,
        ),
        "topology_costs": _build_topology_costs(
            evaluation.topology_records,
            call_records,
            topology_summaries,
        ),
        "repeat_costs": _build_repeat_costs(
            evaluation.topology_records,
            call_records,
        ),
        "cases": [
            record.to_payload()
            for record in evaluation.topology_records
        ],
        "repeat_outcomes": [
            outcome.to_payload()
            for outcome in evaluation.repeat_outcomes
        ],
        "failures": list(evaluation.failures),
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    return payload


def _persist_summary(
    output_dir: Path,
    summary: dict[str, object],
) -> None:
    write_redacted_json_atomic(output_dir / "summary.json", summary)
    write_redacted_text_atomic(
        output_dir / "REPORT.md",
        _render_report(summary),
    )


def _persist_early_blocked_summary(
    output_dir: Path,
    *,
    session: str,
    runner_mode: RunnerMode,
    model: str,
    expected_provider: str,
    error: Exception,
    elapsed_seconds: float,
) -> None:
    payload = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "session": session,
        "date": _current_local_date(),
        "timezone": "Asia/Shanghai",
        "runner_mode": runner_mode,
        "decision": "blocked",
        "default_topology": "single",
        "decision_state": {
            "experiment_conclusion": "blocked",
            "default_topology": "single",
            "fixed_three_quality_advantage_observed": False,
            "owner_cost_confirmation_required": False,
        },
        "branch": _git_output(PROJECT_ROOT, "branch", "--show-current"),
        "head": _git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "dataset_id": None,
        "model": model,
        "expected_provider": expected_provider,
        "expected_auth_mode": FROZEN_AUTH_MODE,
        "dataset_sha256": None,
        "ground_truth_sha256": None,
        "provider_session_budget": {
            "used": 0,
            "max": FROZEN_PROVIDER_SESSION_LIMIT,
            "remaining": FROZEN_PROVIDER_SESSION_LIMIT,
            "initial_reviewer_sessions": 0,
            "repeat_reviewer_sessions": 0,
        },
        "provider_calls": [],
        "total_observed_tokens": None,
        "token_observation_complete": False,
        "topology_summaries": {},
        "category_summaries": {},
        "topology_costs": {},
        "repeat_costs": {
            "provider_sessions": 0,
            "tokens_complete": False,
            "total_tokens": None,
            "total_prompt_chars": 0,
            "wall_clock_sum_seconds": 0.0,
            "topology_block_count": 0,
        },
        "cases": [],
        "repeat_outcomes": [],
        "safety_metrics": {
            "reviewer_context_leak": None,
            "workspace_writes_by_reviewer": None,
            "ground_truth_prompt_leak": None,
            "cross_topology_output_leak": None,
            "cross_replicate_output_leak": None,
            "aggregate_reconstruction_mismatch": None,
        },
        "failures": [
            f"{type(error).__name__}: {redact_text(str(error))}"
        ],
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    _persist_summary(output_dir, payload)


def _build_topology_costs(
    topology_records: list[TopologyExecutionRecord],
    call_records: list[ProviderCallRecord],
    summaries: dict[str, ReviewerTopologySummary],
) -> dict[str, object]:
    costs: dict[str, object] = {}
    expected_sessions = {
        "single": 12,
        "adaptive": 24,
        "fixed_three": 36,
    }
    for topology in TOPOLOGIES:
        records = [
            item
            for item in topology_records
            if item.topology == topology and item.replicate == 0
        ]
        attempt_ids = {
            attempt_id
            for record in records
            for attempt_id in record.attempt_ids
        }
        calls = [
            item
            for item in call_records
            if item.attempt_id in attempt_ids
        ]
        call_count_complete = len(calls) == expected_sessions[topology]
        block_count_complete = len(records) == 12
        token_complete = call_count_complete and all(
            item.tokens_used is not None for item in calls
        )
        total_tokens = (
            sum(item.tokens_used or 0 for item in calls)
            if token_complete
            else None
        )
        call_latencies = [item.elapsed_seconds for item in calls]
        prompt_chars = sum(item.prompt_chars or 0 for item in calls)
        block_latencies = [item.elapsed_seconds for item in records]
        latency_complete = (
            call_count_complete
            and block_count_complete
            and all(value > 0 for value in call_latencies)
            and all(value > 0 for value in block_latencies)
        )
        summary = summaries.get(topology)
        true_positive_count = (
            summary.true_positive_count if summary is not None else 0
        )
        unique_major_count = (
            summary.unique_true_positive_blocker_major_count
            if summary is not None
            else 0
        )
        costs[topology] = {
            "provider_sessions": len(calls),
            "expected_provider_sessions": expected_sessions[topology],
            "call_count_complete": call_count_complete,
            "topology_block_count": len(records),
            "block_count_complete": block_count_complete,
            "tokens_complete": token_complete,
            "latency_complete": latency_complete,
            "complete": (
                call_count_complete
                and block_count_complete
                and token_complete
                and latency_complete
            ),
            "total_tokens": total_tokens,
            "total_prompt_chars": prompt_chars,
            "call_latency_p50_seconds": _percentile(
                call_latencies,
                0.50,
            ),
            "call_latency_p95_seconds": _percentile(
                call_latencies,
                0.95,
            ),
            "block_latency_p50_seconds": _percentile(
                block_latencies,
                0.50,
            ),
            "block_latency_p95_seconds": _percentile(
                block_latencies,
                0.95,
            ),
            "tokens_per_true_positive": (
                total_tokens / true_positive_count
                if total_tokens is not None and true_positive_count
                else None
            ),
            "tokens_per_unique_true_blocker_major": (
                total_tokens / unique_major_count
                if total_tokens is not None and unique_major_count
                else None
            ),
        }
    single = costs["single"]
    fixed = costs["fixed_three"]
    assert isinstance(single, dict)
    assert isinstance(fixed, dict)
    for topology, payload in costs.items():
        assert isinstance(payload, dict)
        payload["session_multiplier_vs_single"] = _safe_metric_ratio(
            payload["provider_sessions"],
            single["provider_sessions"],
        )
        payload["token_multiplier_vs_single"] = _safe_metric_ratio(
            payload["total_tokens"],
            single["total_tokens"],
        )
        payload["block_p50_multiplier_vs_single"] = _safe_metric_ratio(
            payload["block_latency_p50_seconds"],
            single["block_latency_p50_seconds"],
        )
        payload["session_multiplier_vs_fixed_three"] = _safe_metric_ratio(
            payload["provider_sessions"],
            fixed["provider_sessions"],
        )
        payload["token_multiplier_vs_fixed_three"] = _safe_metric_ratio(
            payload["total_tokens"],
            fixed["total_tokens"],
        )
    return costs


def _build_repeat_costs(
    topology_records: list[TopologyExecutionRecord],
    call_records: list[ProviderCallRecord],
) -> dict[str, object]:
    records = [
        item for item in topology_records if item.replicate == 1
    ]
    attempt_ids = {
        attempt_id
        for record in records
        for attempt_id in record.attempt_ids
    }
    calls = [
        item for item in call_records if item.attempt_id in attempt_ids
    ]
    token_complete = all(
        item.tokens_used is not None for item in calls
    )
    return {
        "provider_sessions": len(calls),
        "tokens_complete": token_complete,
        "total_tokens": (
            sum(item.tokens_used or 0 for item in calls)
            if token_complete
            else None
        ),
        "total_prompt_chars": sum(
            item.prompt_chars or 0 for item in calls
        ),
        "wall_clock_sum_seconds": round(
            sum(record.elapsed_seconds for record in records),
            3,
        ),
        "topology_block_count": len(records),
    }


def _build_category_summaries(
    topology_records: list[TopologyExecutionRecord],
    public_dataset: ReviewerTopologyPublicDataset,
) -> dict[str, object]:
    category_by_case = {
        case.case_id: case.category for case in public_dataset.cases
    }
    payload: dict[str, object] = {}
    for topology in TOPOLOGIES:
        topology_payload: dict[str, object] = {}
        for category in (
            "clean",
            "correctness",
            "verification_adequacy",
            "security_design",
        ):
            scores = [
                record.score
                for record in topology_records
                if (
                    record.replicate == 0
                    and record.topology == topology
                    and category_by_case.get(record.case_id) == category
                    and record.score is not None
                )
            ]
            expected = sum(
                score.expected_finding_count for score in scores
            )
            predicted = sum(
                score.predicted_finding_count for score in scores
            )
            true_positive = sum(
                score.true_positive_count for score in scores
            )
            topology_payload[category] = {
                "case_count": len(scores),
                "expected_findings": expected,
                "predicted_findings": predicted,
                "true_positives": true_positive,
                "precision": (
                    true_positive / predicted
                    if predicted
                    else 1.0 if expected == 0 else 0.0
                ),
                "recall": (
                    true_positive / expected if expected else 1.0
                ),
                "verdict_accuracy": (
                    sum(score.verdict_correct for score in scores)
                    / len(scores)
                    if scores
                    else None
                ),
            }
        payload[topology] = topology_payload
    return payload


def _render_report(summary: dict[str, object]) -> str:
    lines = [
        "# Gate 5.5 Reviewer Topology 评测报告",
        "",
        f"- session：`{summary['session']}`",
        f"- 日期：`{summary['date']}`",
        f"- runner：`{summary['runner_mode']}`",
        f"- decision：`{summary['decision']}`",
        f"- default topology：`{summary['default_topology']}`",
        f"- branch / HEAD：`{summary['branch']}` / `{summary['head']}`",
        f"- dataset：`{summary['dataset_id']}`",
        f"- provider / model：`{summary['expected_provider']}` / `{summary['model']}`",
        f"- auth：`{summary['expected_auth_mode']}`",
        "",
        "## 调用预算",
        "",
    ]
    budget = summary["provider_session_budget"]
    if not isinstance(budget, dict):
        raise TypeError("provider_session_budget 必须是对象")
    lines.extend(
        [
            f"- used / max：`{budget['used']} / {budget['max']}`",
            (
                "- initial reviewer sessions："
                f"`{budget['initial_reviewer_sessions']}`"
            ),
            (
                "- repeat reviewer sessions："
                f"`{budget['repeat_reviewer_sessions']}`"
            ),
            f"- observed tokens：`{summary['total_observed_tokens']}`",
            "",
            "## Topology 指标",
            "",
        ]
    )
    topology_summaries = summary["topology_summaries"]
    if isinstance(topology_summaries, dict) and topology_summaries:
        topology_costs = summary.get("topology_costs")
        for topology in TOPOLOGIES:
            item = topology_summaries.get(topology)
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"### {topology}",
                    "",
                    f"- precision：`{item['finding_precision']:.4f}`",
                    f"- recall：`{item['finding_recall']:.4f}`",
                    (
                        "- exact / alias TP："
                        f"`{item['exact_true_positive_count']} / "
                        f"{item['alias_true_positive_count']}`"
                    ),
                    (
                        "- severity range accuracy："
                        f"`{item['severity_range_accuracy']:.4f}`"
                    ),
                    (
                        "- blocker / major recall："
                        f"`{item['blocker_major_recall']:.4f}`"
                    ),
                    (
                        "- clean false blocker cases："
                        f"`{item['clean_false_blocker_case_count']}`"
                    ),
                    (
                        "- clean false major cases / findings："
                        f"`{item['clean_false_major_case_count']} / "
                        f"{item['clean_false_major_count']}`"
                    ),
                    (
                        "- unique true blocker / major："
                        f"`{item['unique_true_positive_blocker_major_count']}`"
                    ),
                    (
                        "- raw / unique / duplicate findings："
                        f"`{item['raw_finding_count']} / "
                        f"{item['unique_raw_finding_count']} / "
                        f"{item['duplicate_finding_count']}`"
                    ),
                    f"- duplicate ratio：`{item['duplicate_ratio']:.4f}`",
                    f"- verdict accuracy：`{item['verdict_accuracy']:.4f}`",
                ]
            )
            if isinstance(topology_costs, dict):
                cost = topology_costs.get(topology)
                if isinstance(cost, dict):
                    lines.extend(
                        [
                            (
                                "- sessions / total tokens："
                                f"`{cost['provider_sessions']} / "
                                f"{cost['total_tokens']}`"
                            ),
                            (
                                "- block latency p50 / p95："
                                f"`{cost['block_latency_p50_seconds']} / "
                                f"{cost['block_latency_p95_seconds']}`"
                            ),
                            (
                                "- token multiplier vs single："
                                f"`{cost['token_multiplier_vs_single']}`"
                            ),
                            (
                                "- tokens per TP / unique blocker-major："
                                f"`{cost['tokens_per_true_positive']} / "
                                f"{cost['tokens_per_unique_true_blocker_major']}`"
                            ),
                        ]
                    )
            lines.append("")
    else:
        lines.extend(["- preflight 阶段停止，未运行业务 Case。", ""])
    repeat_costs = summary.get("repeat_costs")
    repeat_outcomes = summary.get("repeat_outcomes")
    if isinstance(repeat_costs, dict):
        lines.extend(
            [
                "## Replicate 成本",
                "",
                (
                    "- sessions / total tokens："
                    f"`{repeat_costs['provider_sessions']} / "
                    f"{repeat_costs['total_tokens']}`"
                ),
                (
                    "- topology blocks / wall-clock sum："
                    f"`{repeat_costs['topology_block_count']} / "
                    f"{repeat_costs['wall_clock_sum_seconds']}`"
                ),
                "",
            ]
        )
    if isinstance(repeat_outcomes, list) and repeat_outcomes:
        lines.extend(["## Replicate 结论", ""])
        for outcome in repeat_outcomes:
            if not isinstance(outcome, dict):
                continue
            lines.append(
                "- "
                f"`{outcome['case_id']}` / "
                f"`{outcome['comparison_kind']}` / "
                f"completed=`{outcome['completed']}` / "
                f"flip=`{outcome['verdict_flip']}`"
            )
        lines.append("")
    category_summaries = summary.get("category_summaries")
    if isinstance(category_summaries, dict):
        lines.extend(["## 分类指标", ""])
        for topology in TOPOLOGIES:
            categories = category_summaries.get(topology)
            if not isinstance(categories, dict):
                continue
            for category, metrics in categories.items():
                if not isinstance(metrics, dict):
                    continue
                lines.append(
                    "- "
                    f"`{topology}/{category}`："
                    f"precision=`{metrics['precision']}`，"
                    f"recall=`{metrics['recall']}`，"
                    f"verdict_accuracy=`{metrics['verdict_accuracy']}`"
                )
        lines.append("")
    lines.extend(
        [
            "## 结论",
            "",
            f"```text\n{summary['decision']}\n```",
        ]
    )
    decision_state = summary.get("decision_state")
    if isinstance(decision_state, dict):
        lines.extend(
            [
                "",
                (
                    "- fixed_three quality advantage observed："
                    f"`{decision_state['fixed_three_quality_advantage_observed']}`"
                ),
                (
                    "- owner cost confirmation required："
                    f"`{decision_state['owner_cost_confirmation_required']}`"
                ),
            ]
        )
    lines.extend(
        [
            "",
            "本报告只使用结构化 reviewer artifacts 和延迟加载的 ground truth 计算。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_public_evidence(
    public_case: ReviewerTopologyPublicCase,
    *,
    fixture: FixtureRecord,
    dataset_id: str,
    dataset_sha256: str,
    ground_truth_sha256: str,
    neutral_case_id: str,
) -> str:
    acceptance = "\n".join(
        f"- {item}" for item in public_case.acceptance
    )
    return (
        "# Gate 5.5 Reviewer Topology 公共 Evidence\n\n"
        f"- dataset：`{dataset_id}`\n"
        f"- dataset SHA-256：`{dataset_sha256}`\n"
        f"- ground truth commitment：`{ground_truth_sha256}`\n"
        f"- case：`{neutral_case_id}`\n"
        f"- fixture HEAD：`{fixture.head_sha}`\n"
        f"- workspace fingerprint：`sha256:{fixture.workspace.fingerprint}`\n"
        f"- diff SHA-256：`{fixture.workspace.full_diff_sha256}`\n\n"
        "## 任务\n\n"
        f"{public_case.task}\n\n"
        "## 验收条件\n\n"
        f"{acceptance}\n\n"
        "## Finding 输出约束\n\n"
        "category 和 rule_id 必须由当前证据独立推导，不得猜测 evaluator "
        "分类或隐藏答案。位置请使用稳定结构化标识，例如 "
        "`function:expression`；"
        "无法稳定定位时可使用 `line:<n>`。\n\n"
        "## 变更文件\n\n"
        + "\n".join(f"- `{item}`" for item in fixture.workspace.changed_files)
        + "\n\n"
        "## Git Diff\n\n"
        "```diff\n"
        f"{fixture.workspace.full_diff.rstrip()}\n"
        "```\n\n"
        "## Verification\n\n"
        f"- command：`{fixture.verification_command}`\n"
        "- status：`passed`\n\n"
        "```text\n"
        f"{fixture.verification_output.rstrip()[-12000:]}\n"
        "```\n"
    )


def _topology_order(case_index: int) -> tuple[ReviewTopology, ...]:
    orders: tuple[tuple[ReviewTopology, ...], ...] = (
        ("single", "adaptive", "fixed_three"),
        ("adaptive", "fixed_three", "single"),
        ("fixed_three", "single", "adaptive"),
    )
    return orders[case_index % len(orders)]


def _validate_readiness(
    dataset: ReviewerTopologyPublicDataset,
) -> dict[str, int]:
    if len(dataset.cases) != 12:
        raise ValueError("Gate 5.5 public dataset 必须精确包含 12 个 case")
    category_counts = {
        category: sum(
            case.category == category for case in dataset.cases
        )
        for category in (
            "clean",
            "correctness",
            "verification_adequacy",
            "security_design",
        )
    }
    if any(count != 3 for count in category_counts.values()):
        raise ValueError(
            f"Gate 5.5 四类 case 必须各 3 个：{category_counts}"
        )
    if tuple(case.case_id for case in dataset.cases) != (
        _FROZEN_EVALUATOR_CASE_IDS
    ):
        raise ValueError("Gate 5.5 evaluator case identity/order 已漂移")
    counts = {topology: 0 for topology in TOPOLOGIES}
    for index, case in enumerate(dataset.cases):
        routing = dict(case.routing_facts)
        context = ParallelReviewRoutingContext.model_validate(
            {
                "run_id": f"gate55-readiness-case-{index + 1:02d}",
                "iteration": 1,
                "evidence_snapshot_sha256": "0" * 64,
                **routing,
            }
        )
        for topology in TOPOLOGIES:
            plan = build_parallel_review_plan(
                context,
                topology=topology,
            )
            counts[topology] += len(plan.required_roles)
    return counts


def _tokens_for_attempts(
    records: list[ProviderCallRecord],
    attempt_ids: list[str],
) -> int | None:
    expected = set(attempt_ids)
    relevant = [
        item
        for item in records
        if item.attempt_id in expected
    ]
    if (
        len(relevant) != len(expected)
        or any(item.tokens_used is None for item in relevant)
    ):
        return None
    return sum(item.tokens_used or 0 for item in relevant)


def _parse_tokens_used(output: str) -> int | None:
    matches = _TOKEN_COUNT_PATTERN.findall(output)
    if not matches:
        return None
    return int(matches[-1].replace(",", ""))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    result = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(result, 3)


def _safe_metric_ratio(
    numerator: object,
    denominator: object,
) -> float | None:
    if (
        not isinstance(numerator, (int, float))
        or isinstance(numerator, bool)
        or not isinstance(denominator, (int, float))
        or isinstance(denominator, bool)
        or denominator == 0
    ):
        return None
    return round(numerator / denominator, 4)


def _required_match(
    pattern: re.Pattern[str],
    text: str,
    label: str,
) -> str:
    matched = pattern.search(text)
    if matched is None:
        raise ValueError(f"prompt 缺少 {label}")
    return matched.group(1)


def _verification_argv(command: str) -> list[str]:
    normalized = command.strip()
    if normalized != "python -m pytest -q":
        raise ValueError(f"不允许的 fixture verification command：{command}")
    return [sys.executable, "-m", "pytest", "-q"]


def _write_fixture_file(
    repo_path: Path,
    relative: str,
    content: str,
) -> None:
    normalized = _normalize_relative_path(relative)
    path = repo_path.joinpath(*PurePosixPath(normalized).parts)
    resolved_root = repo_path.resolve()
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
        raise ValueError("fixture 文件不能越过 repo")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        content.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
    )


def _normalize_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("fixture 文件路径必须是 POSIX 相对路径")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or ":" in path.parts[0]
    ):
        raise ValueError("fixture 文件路径不能越界")
    return path.as_posix()


def _new_session_root(root: Path, name: str) -> Path:
    candidate = root / name
    if candidate.exists():
        raise FileExistsError(f"session 已存在：{candidate}")
    candidate.mkdir(parents=True)
    return candidate


def _new_case_run_root(
    run_root: Path,
    *,
    session: str,
    neutral_case_id: str,
) -> Path:
    name = f"gate55-{session}-{neutral_case_id}"
    if len(name) > 180:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        name = f"gate55-{session[:80]}-{digest}"
    return _new_session_root(run_root, name)


def _new_repeat_run_root(
    run_root: Path,
    *,
    session: str,
    neutral_case_id: str,
) -> Path:
    name = f"gate55-{session}-repeat-01-{neutral_case_id}"
    return _new_session_root(run_root, name)


def _require_project_file(path: Path, option: str) -> Path:
    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{option} 必须是项目内已存在文件")
    return resolved


def _require_project_directory_root(path: Path, option: str) -> Path:
    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{option} 必须位于项目内")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _require_clean_project() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=True,
    ).stdout.strip()
    if status:
        raise RuntimeError(
            "真实 Gate 5.5 必须从 clean worktree 运行；"
            f"当前状态：{redact_text(status)}"
        )


def _require_frozen_real_arguments(args: argparse.Namespace) -> None:
    provider = CodexProviderDescriptor(
        name=args.expected_provider,
        base_url=args.provider_base_url,
        wire_api=args.provider_wire_api,
        requires_openai_auth=_parse_bool(
            args.provider_requires_openai_auth
        ),
        supports_websockets=_parse_bool(
            args.provider_supports_websockets
        ),
    )
    _require_frozen_runtime_contract(
        model=args.model,
        reviewer_reasoning=args.reviewer_reasoning,
        timeout_seconds=args.timeout_seconds,
        preflight_timeout_seconds=args.preflight_timeout_seconds,
        max_provider_sessions=args.max_provider_sessions,
        expected_provider=args.expected_provider,
        expected_auth_mode=args.expected_auth_mode,
        expected_codex_version=args.expected_codex_version,
        provider=provider,
        windows_sandbox_session_override=(
            args.windows_sandbox_session_override
        ),
    )


def _require_frozen_runtime_contract(
    *,
    model: str,
    reviewer_reasoning: str,
    timeout_seconds: int,
    preflight_timeout_seconds: int,
    max_provider_sessions: int,
    expected_provider: str,
    expected_auth_mode: str,
    expected_codex_version: str,
    provider: CodexProviderDescriptor,
    windows_sandbox_session_override: str,
) -> None:
    observed = {
        "model": model,
        "reviewer_reasoning": reviewer_reasoning,
        "timeout_seconds": timeout_seconds,
        "preflight_timeout_seconds": preflight_timeout_seconds,
        "max_provider_sessions": max_provider_sessions,
        "expected_provider": expected_provider,
        "expected_auth_mode": expected_auth_mode,
        "expected_codex_version": expected_codex_version,
        "provider_base_url": provider.base_url,
        "provider_wire_api": provider.wire_api,
        "provider_requires_openai_auth": (
            provider.requires_openai_auth
        ),
        "provider_supports_websockets": (
            provider.supports_websockets
        ),
        "windows_sandbox_session_override": (
            windows_sandbox_session_override
        ),
    }
    expected = {
        "model": FROZEN_MODEL,
        "reviewer_reasoning": FROZEN_REASONING,
        "timeout_seconds": FROZEN_REVIEW_TIMEOUT_SECONDS,
        "preflight_timeout_seconds": FROZEN_PREFLIGHT_TIMEOUT_SECONDS,
        "max_provider_sessions": FROZEN_PROVIDER_SESSION_LIMIT,
        "expected_provider": FROZEN_PROVIDER,
        "expected_auth_mode": FROZEN_AUTH_MODE,
        "expected_codex_version": FROZEN_CODEX_VERSION,
        "provider_base_url": FROZEN_PROVIDER_BASE_URL,
        "provider_wire_api": FROZEN_PROVIDER_WIRE_API,
        "provider_requires_openai_auth": FROZEN_PROVIDER_REQUIRES_AUTH,
        "provider_supports_websockets": (
            FROZEN_PROVIDER_SUPPORTS_WEBSOCKETS
        ),
        "windows_sandbox_session_override": (
            FROZEN_WINDOWS_SANDBOX_OVERRIDE
        ),
    }
    mismatches = [
        f"{key}={observed[key]!r}, frozen={value!r}"
        for key, value in expected.items()
        if observed[key] != value
    ]
    if mismatches:
        raise RuntimeError(
            "真实 Gate 5.5 执行身份不得由 CLI 改写："
            + "; ".join(mismatches)
        )


def _require_execution_baseline() -> None:
    branch = _git_output(PROJECT_ROOT, "branch", "--show-current")
    if branch != FROZEN_BRANCH:
        raise RuntimeError(
            f"真实 Gate 5.5 必须在冻结分支运行：{FROZEN_BRANCH}"
        )
    head = _git_output(PROJECT_ROOT, "rev-parse", "HEAD")
    try:
        tagged = _git_output(
            PROJECT_ROOT,
            "rev-parse",
            f"refs/tags/{FROZEN_EXECUTION_TAG}^{{commit}}",
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"缺少冻结 execution baseline tag：{FROZEN_EXECUTION_TAG}"
        ) from exc
    if head != tagged:
        raise RuntimeError(
            "当前 HEAD 与冻结 execution baseline tag 不一致："
            f"HEAD={head}, tagged={tagged}"
        )


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("布尔参数只能是 true 或 false")


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("SHA-256 必须是 64 位小写十六进制")
    return normalized


def _current_local_date() -> str:
    return datetime.now().date().isoformat()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_process_diagnostics(
    completed: subprocess.CompletedProcess[str],
) -> list[str]:
    combined = redact_text(
        (completed.stdout or "") + "\n" + (completed.stderr or "")
    )
    return [
        line[:500]
        for line in combined.splitlines()
        if (
            line.startswith("Gate 4.5")
            or line.startswith("证据：")
            or line.startswith("- preflight:")
            or line.startswith("ERROR:")
            or line.startswith("warning:")
        )
    ][-20:]


def _run_git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Git 命令失败："
            f"git {' '.join(args)}\n"
            f"{redact_text(completed.stderr or completed.stdout)}"
        )


def _git_output(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Git 读取失败："
            f"git {' '.join(args)}\n"
            f"{redact_text(completed.stderr or completed.stdout)}"
        )
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
