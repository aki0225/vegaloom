from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .execution_control import ExecutionLease
from .loop_graph_state import merge_review_results_by_identity
from .loop_step_result import hash_command
from .parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    PARALLEL_REVIEW_PROMPT_VERSION,
    ParallelReviewAggregate,
    ParallelReviewAggregationContext,
    ParallelReviewAttemptIdentity,
    ParallelReviewFinding,
    ParallelReviewPlan,
    ParallelReviewResultRef,
    ReviewerRole,
    ReviewExecutionStatus,
    ReviewVerdictValue,
    aggregate_parallel_reviews,
    build_parallel_review_attempt_identity,
    build_parallel_review_result,
)
from .parallel_review_artifacts import (
    parallel_review_execution_artifact_ref,
    parallel_review_aggregate_artifact_ref,
    list_parallel_review_result_refs,
    read_parallel_review_aggregate,
    read_parallel_review_result,
    write_parallel_review_aggregate,
    write_parallel_review_execution,
    write_parallel_review_plan,
    write_parallel_review_process_output,
    write_parallel_review_public_evidence,
    write_parallel_review_result,
    write_parallel_review_role_prompt,
)


class ParallelReviewGraphValidationError(ValueError):
    pass


class ParallelReviewAggregateArtifactRef(TypedDict):
    review_plan_id: str
    aggregate_sha256: str
    artifact_ref: str
    artifact_sha256: str


class ParallelReviewGraphState(TypedDict):
    review_plan: dict[str, object]
    review_results: Annotated[
        dict[str, dict[str, object]],
        merge_review_results_by_identity,
    ]
    aggregate_ref: ParallelReviewAggregateArtifactRef | None


class _ParallelReviewerTask(TypedDict):
    review_plan: dict[str, object]
    reviewer_role: ReviewerRole


class ParallelReviewerExecutor(Protocol):
    def __call__(
        self,
        *,
        run_dir: Path,
        plan: ParallelReviewPlan,
        reviewer_role: ReviewerRole,
    ) -> ParallelReviewResultRef: ...


@dataclass(frozen=True)
class ParallelReviewGraphRun:
    plan: ParallelReviewPlan
    result_refs: tuple[ParallelReviewResultRef, ...]
    aggregate: ParallelReviewAggregate
    aggregate_ref: ParallelReviewAggregateArtifactRef
    graph_state: ParallelReviewGraphState


@dataclass(frozen=True)
class DeterministicFakeReviewer:
    """Gate 5 只读 fake executor；用于验证 fan-out，不代表真实模型质量。"""

    reviewer_role: ReviewerRole
    private_canary: str
    status: ReviewExecutionStatus = "completed"
    verdict: ReviewVerdictValue = "approve"
    findings: tuple[ParallelReviewFinding, ...] = ()
    checked_items: tuple[str, ...] = ("公共 evidence snapshot",)
    delay_seconds: float = 0.0
    on_started: Callable[[ReviewerRole], None] | None = None
    wait_until_released: Callable[[ReviewerRole], None] | None = None
    on_completed: Callable[[ReviewerRole], None] | None = None

    def __post_init__(self) -> None:
        if self.delay_seconds < 0:
            raise ValueError("fake reviewer delay_seconds 不能小于 0")
        if self.status != "completed":
            if self.verdict != "needs_human":
                raise ValueError(
                    "非 completed fake reviewer 只能输出 needs_human"
                )
            if self.findings:
                raise ValueError(
                    "非 completed fake reviewer 不能携带 findings"
                )

    def __call__(
        self,
        *,
        run_dir: Path,
        plan: ParallelReviewPlan,
        reviewer_role: ReviewerRole,
    ) -> ParallelReviewResultRef:
        if reviewer_role != self.reviewer_role:
            raise ParallelReviewGraphValidationError(
                "fake reviewer executor 与调度角色不一致"
            )
        if reviewer_role not in plan.required_roles:
            raise ParallelReviewGraphValidationError(
                "fake reviewer 不能执行 ReviewPlan 之外的角色"
            )
        if self.on_started is not None:
            self.on_started(reviewer_role)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.wait_until_released is not None:
            self.wait_until_released(reviewer_role)

        public_evidence = (
            "# Gate 5 deterministic fake evidence\n\n"
            f"- review_plan_id: `{plan.plan_id}`\n"
            f"- evidence_snapshot_sha256: "
            f"`{plan.evidence_snapshot_sha256}`\n"
        )
        _, public_evidence_sha256 = write_parallel_review_public_evidence(
            run_dir,
            plan,
            public_evidence,
        )
        role_prompt = (
            "# Gate 5 deterministic fake role prompt\n\n"
            f"- prompt_version: `{PARALLEL_REVIEW_PROMPT_VERSION}`\n"
            f"- reviewer_role: `{reviewer_role}`\n"
            f"- private_canary: `{self.private_canary}`\n"
        )
        _, role_prompt_sha256 = write_parallel_review_role_prompt(
            run_dir,
            plan,
            reviewer_role=reviewer_role,
            content=role_prompt,
        )
        attempt = build_parallel_review_attempt_identity(
            plan,
            reviewer_role=reviewer_role,
            public_evidence_sha256=public_evidence_sha256,
            role_prompt_sha256=role_prompt_sha256,
        )
        execution_ref = parallel_review_execution_artifact_ref(
            plan,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
        )
        _, process_output_sha256, process_output_bytes = (
            write_parallel_review_process_output(
                run_dir,
                execution_ref=execution_ref,
                content=(
                    f"{reviewer_role} deterministic fake output；"
                    f"private_canary={self.private_canary}"
                ),
            )
        )
        execution = _build_fake_execution(
            plan,
            reviewer_role=reviewer_role,
            attempt=attempt,
            result_status=self.status,
            process_output_sha256=process_output_sha256,
            process_output_bytes=process_output_bytes,
        )
        execution_path = write_parallel_review_execution(
            run_dir,
            execution_ref=execution_ref,
            execution=execution,
        )
        result = build_parallel_review_result(
            review_plan_id=plan.plan_id,
            run_id=plan.run_id,
            iteration=plan.iteration,
            reviewer_role=reviewer_role,
            attempt_id=attempt.attempt_id,
            evidence_snapshot_sha256=plan.evidence_snapshot_sha256,
            execution_ref=execution_ref,
            execution_sha256=hashlib.sha256(
                execution_path.read_bytes()
            ).hexdigest(),
            status=self.status,
            verdict=self.verdict,
            summary=(
                f"{reviewer_role} deterministic fake result；"
                f"private_canary={self.private_canary}"
            ),
            findings=self.findings,
            checked_items=self.checked_items,
        )
        result_ref = write_parallel_review_result(run_dir, result)
        if self.on_completed is not None:
            self.on_completed(reviewer_role)
        return result_ref


def execute_parallel_review_graph(
    run_dir: Path,
    *,
    context: ParallelReviewAggregationContext,
    executors: Mapping[ReviewerRole, ParallelReviewerExecutor],
    checkpointer: object | None = None,
) -> ParallelReviewGraphRun:
    """按 ReviewPlan 动态 fan-out，并只在 Graph State 中合并窄 result ref。"""

    plan = _validated_plan(context.review_plan)
    _validate_context(plan, context)
    existing_result_refs = list_parallel_review_result_refs(run_dir, plan)
    existing_roles = {
        result_ref.reviewer_role for result_ref in existing_result_refs
    }
    missing_roles = [
        role
        for role in plan.required_roles
        if role not in existing_roles and role not in executors
    ]
    unsupported_roles = [
        role for role in executors if role not in AVAILABLE_REVIEWER_ROLES
    ]
    if missing_roles:
        raise ParallelReviewGraphValidationError(
            f"缺少必需 reviewer executor：{missing_roles}"
        )
    if unsupported_roles:
        raise ParallelReviewGraphValidationError(
            f"存在不受支持 reviewer executor：{unsupported_roles}"
        )
    write_parallel_review_plan(run_dir, plan)

    if (
        checkpointer is None
        and existing_roles == set(plan.required_roles)
    ):
        aggregate, aggregate_ref = _aggregate_result_refs(
            run_dir,
            context,
            existing_result_refs,
        )
        graph_state: ParallelReviewGraphState = {
            "review_plan": plan.model_dump(mode="json"),
            "review_results": {
                result_ref.result_id: result_ref.model_dump(mode="json")
                for result_ref in existing_result_refs
            },
            "aggregate_ref": aggregate_ref,
        }
        return ParallelReviewGraphRun(
            plan=plan,
            result_refs=existing_result_refs,
            aggregate=aggregate,
            aggregate_ref=aggregate_ref,
            graph_state=graph_state,
        )

    graph = _compile_parallel_review_graph(
        run_dir,
        context=context,
        executors=executors,
        checkpointer=checkpointer,
    )
    config: dict[str, object] = {
        "configurable": {
            "thread_id": f"{plan.run_id}:{plan.plan_id}",
        },
        "max_concurrency": plan.max_parallelism,
    }
    invocation_input: ParallelReviewGraphState | None
    if checkpointer is not None:
        try:
            checkpoint_snapshot = graph.get_state(config)
        except Exception as exc:
            raise ParallelReviewGraphValidationError(
                "无法读取 parallel review checkpoint"
            ) from exc
        if checkpoint_snapshot.values:
            _validate_checkpoint_resume_state(
                run_dir,
                plan,
                checkpoint_snapshot.values,
                checkpoint_tasks=checkpoint_snapshot.tasks,
                existing_result_refs=existing_result_refs,
            )
            invocation_input = None
        else:
            invocation_input = _initial_graph_state(
                plan,
                existing_result_refs,
            )
    else:
        invocation_input = _initial_graph_state(
            plan,
            existing_result_refs,
        )
    invoked = cast(
        ParallelReviewGraphState,
        graph.invoke(invocation_input, config=config),
    )
    result_refs = _validated_result_refs(invoked["review_results"])
    aggregate_ref = invoked["aggregate_ref"]
    if aggregate_ref is None:
        raise ParallelReviewGraphValidationError(
            "parallel review graph 未生成 aggregate ref"
        )
    aggregate = read_parallel_review_aggregate(
        run_dir,
        iteration=plan.iteration,
        plan_id=plan.plan_id,
        artifact_sha256=aggregate_ref["artifact_sha256"],
    )
    if (
        aggregate.aggregate_sha256 != aggregate_ref["aggregate_sha256"]
        or aggregate_ref["review_plan_id"] != plan.plan_id
    ):
        raise ParallelReviewGraphValidationError(
            "parallel review aggregate ref 与实际 artifact 不一致"
        )
    return ParallelReviewGraphRun(
        plan=plan,
        result_refs=result_refs,
        aggregate=aggregate,
        aggregate_ref=aggregate_ref,
        graph_state=invoked,
    )


def _initial_graph_state(
    plan: ParallelReviewPlan,
    result_refs: tuple[ParallelReviewResultRef, ...],
) -> ParallelReviewGraphState:
    return {
        "review_plan": plan.model_dump(mode="json"),
        "review_results": {
            result_ref.result_id: result_ref.model_dump(mode="json")
            for result_ref in result_refs
        },
        "aggregate_ref": None,
    }


def _validate_checkpoint_resume_state(
    run_dir: Path,
    plan: ParallelReviewPlan,
    checkpoint_state: object,
    *,
    checkpoint_tasks: object,
    existing_result_refs: tuple[ParallelReviewResultRef, ...],
) -> None:
    if not isinstance(checkpoint_state, Mapping):
        raise ParallelReviewGraphValidationError(
            "parallel review checkpoint state 不是对象"
        )
    checkpoint_plan = _validated_plan(checkpoint_state.get("review_plan"))
    if checkpoint_plan != plan:
        raise ParallelReviewGraphValidationError(
            "parallel review checkpoint 的 ReviewPlan 与当前计划不一致"
        )
    raw_review_results = checkpoint_state.get("review_results")
    if not isinstance(raw_review_results, Mapping):
        raise ParallelReviewGraphValidationError(
            "parallel review checkpoint result refs 不是对象"
        )
    checkpoint_result_refs_by_id = {
        result_ref.result_id: result_ref
        for result_ref in _validated_result_refs(raw_review_results)
    }
    if not isinstance(checkpoint_tasks, (list, tuple)):
        raise ParallelReviewGraphValidationError(
            "parallel review checkpoint tasks 不是序列"
        )
    for task in checkpoint_tasks:
        task_result = getattr(task, "result", None)
        if task_result is None:
            continue
        if not isinstance(task_result, Mapping):
            raise ParallelReviewGraphValidationError(
                "parallel review checkpoint task result 不是对象"
            )
        pending_review_results = task_result.get("review_results")
        if pending_review_results is None:
            continue
        if not isinstance(pending_review_results, Mapping):
            raise ParallelReviewGraphValidationError(
                "parallel review checkpoint pending result refs 不是对象"
            )
        for result_ref in _validated_result_refs(
            pending_review_results
        ):
            existing = checkpoint_result_refs_by_id.get(
                result_ref.result_id
            )
            if existing is not None and existing != result_ref:
                raise ParallelReviewGraphValidationError(
                    "parallel review checkpoint result ref 发生冲突"
                )
            checkpoint_result_refs_by_id[result_ref.result_id] = result_ref
    artifact_refs_by_id = {
        result_ref.result_id: result_ref
        for result_ref in existing_result_refs
    }
    for result_ref in checkpoint_result_refs_by_id.values():
        artifact_ref = artifact_refs_by_id.get(result_ref.result_id)
        if artifact_ref != result_ref:
            raise ParallelReviewGraphValidationError(
                "parallel review checkpoint result ref "
                "与当前 artifact pointer 不一致"
            )
        read_parallel_review_result(run_dir, result_ref)
    aggregate_ref = checkpoint_state.get("aggregate_ref")
    if aggregate_ref is not None:
        if not isinstance(aggregate_ref, Mapping):
            raise ParallelReviewGraphValidationError(
                "parallel review checkpoint aggregate ref 不是对象"
            )
        if aggregate_ref.get("review_plan_id") != plan.plan_id:
            raise ParallelReviewGraphValidationError(
                "parallel review checkpoint aggregate ref 与当前计划不一致"
            )


def _compile_parallel_review_graph(
    run_dir: Path,
    *,
    context: ParallelReviewAggregationContext,
    executors: Mapping[ReviewerRole, ParallelReviewerExecutor],
    checkpointer: object | None,
):
    builder = StateGraph(ParallelReviewGraphState)

    def dispatch_reviewers(
        state: ParallelReviewGraphState,
    ) -> dict[str, object]:
        _validated_plan(state["review_plan"])
        return {}

    def route_reviewers(
        state: ParallelReviewGraphState,
    ) -> list[Send]:
        plan = _validated_plan(state["review_plan"])
        completed_roles = {
            result_ref.reviewer_role
            for result_ref in _validated_result_refs(
                state["review_results"]
            )
        }
        return [
            Send(
                "execute_reviewer",
                {
                    "review_plan": plan.model_dump(mode="json"),
                    "reviewer_role": role,
                },
            )
            for role in plan.required_roles
            if role not in completed_roles
        ]

    def execute_reviewer(
        task: _ParallelReviewerTask,
    ) -> dict[str, object]:
        plan = _validated_plan(task["review_plan"])
        role = task["reviewer_role"]
        if role not in plan.required_roles:
            raise ParallelReviewGraphValidationError(
                "LangGraph Send 注入了计划外 reviewer role"
            )
        executor = executors[role]
        result_ref = executor(
            run_dir=run_dir,
            plan=plan,
            reviewer_role=role,
        )
        result = read_parallel_review_result(run_dir, result_ref)
        if (
            result.review_plan_id != plan.plan_id
            or result.reviewer_role != role
            or result.evidence_snapshot_sha256
            != plan.evidence_snapshot_sha256
        ):
            raise ParallelReviewGraphValidationError(
                "reviewer executor 返回了错误 plan、role 或 snapshot"
            )
        return {
            "review_results": {
                result_ref.result_id: result_ref.model_dump(mode="json")
            }
        }

    def aggregate_results(
        state: ParallelReviewGraphState,
    ) -> dict[str, object]:
        result_refs = _validated_result_refs(state["review_results"])
        _, aggregate_ref = _aggregate_result_refs(
            run_dir,
            context,
            result_refs,
        )
        return {"aggregate_ref": aggregate_ref}

    builder.add_node("dispatch_reviewers", dispatch_reviewers)
    builder.add_node("execute_reviewer", execute_reviewer)
    builder.add_node("aggregate_results", aggregate_results)
    builder.add_edge(START, "dispatch_reviewers")
    builder.add_conditional_edges(
        "dispatch_reviewers",
        route_reviewers,
    )
    builder.add_edge("execute_reviewer", "aggregate_results")
    builder.add_edge("aggregate_results", END)
    return builder.compile(checkpointer=checkpointer)


def _aggregate_result_refs(
    run_dir: Path,
    context: ParallelReviewAggregationContext,
    result_refs: tuple[ParallelReviewResultRef, ...],
) -> tuple[
    ParallelReviewAggregate,
    ParallelReviewAggregateArtifactRef,
]:
    plan = _validated_plan(context.review_plan)
    results = [
        read_parallel_review_result(run_dir, result_ref)
        for result_ref in result_refs
    ]
    aggregate = aggregate_parallel_reviews(context, results)
    path, artifact_sha256 = write_parallel_review_aggregate(
        run_dir,
        aggregate,
    )
    expected_ref = parallel_review_aggregate_artifact_ref(aggregate)
    actual_ref = path.relative_to(run_dir).as_posix()
    if actual_ref != expected_ref:
        raise ParallelReviewGraphValidationError(
            "aggregate artifact 未位于规范化路径"
        )
    aggregate_ref: ParallelReviewAggregateArtifactRef = {
        "review_plan_id": plan.plan_id,
        "aggregate_sha256": aggregate.aggregate_sha256,
        "artifact_ref": actual_ref,
        "artifact_sha256": artifact_sha256,
    }
    return aggregate, aggregate_ref


def _validated_plan(
    plan: ParallelReviewPlan | Mapping[str, object],
) -> ParallelReviewPlan:
    try:
        return ParallelReviewPlan.model_validate(
            (
                plan.model_dump(mode="json")
                if isinstance(plan, ParallelReviewPlan)
                else dict(plan)
            )
        )
    except Exception as exc:
        raise ParallelReviewGraphValidationError(
            "parallel review graph 的 ReviewPlan 不可信"
        ) from exc


def _validate_context(
    plan: ParallelReviewPlan,
    context: ParallelReviewAggregationContext,
) -> None:
    try:
        validated = ParallelReviewAggregationContext.model_validate(
            context.model_dump(mode="json")
        )
    except Exception as exc:
        raise ParallelReviewGraphValidationError(
            "parallel review aggregation context 不可信"
        ) from exc
    if validated.review_plan != plan:
        raise ParallelReviewGraphValidationError(
            "aggregation context 与 ReviewPlan 不一致"
        )


def _validated_result_refs(
    raw_refs: Mapping[str, Mapping[str, object]],
) -> tuple[ParallelReviewResultRef, ...]:
    refs: list[ParallelReviewResultRef] = []
    for identity, raw_ref in raw_refs.items():
        try:
            result_ref = ParallelReviewResultRef.model_validate(dict(raw_ref))
        except Exception as exc:
            raise ParallelReviewGraphValidationError(
                "parallel review graph result ref 不可信"
            ) from exc
        if identity != result_ref.result_id:
            raise ParallelReviewGraphValidationError(
                "parallel review graph result ref map key 不一致"
            )
        refs.append(result_ref)
    return tuple(
        sorted(
            refs,
            key=lambda item: (
                AVAILABLE_REVIEWER_ROLES.index(item.reviewer_role),
                item.result_id,
            ),
        )
    )


def _build_fake_execution(
    plan: ParallelReviewPlan,
    *,
    reviewer_role: ReviewerRole,
    attempt: ParallelReviewAttemptIdentity,
    result_status: ReviewExecutionStatus,
    process_output_sha256: str,
    process_output_bytes: int,
) -> ExecutionLease:
    command = ["vega-fake-reviewer", reviewer_role]
    timestamp = "2026-07-17T00:00:00+00:00"
    status, termination_unconfirmed, returncode, finished_at = (
        _fake_execution_terminal_fields(result_status)
    )
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
            "kind": "deterministic-fake-reviewer",
            "role": reviewer_role,
            "prompt_version": PARALLEL_REVIEW_PROMPT_VERSION,
            "public_evidence_sha256": attempt.public_evidence_sha256,
            "role_prompt_sha256": attempt.role_prompt_sha256,
        },
        input_fingerprint=attempt.input_fingerprint,
        command_sha256=hash_command(command),
        process_output_sha256=process_output_sha256,
        process_output_bytes=process_output_bytes,
        owner_pid=1,
        command=command,
        started_at=timestamp,
        last_heartbeat=timestamp,
        lease_expires_at=timestamp,
        deadline=timestamp,
        status=status,
        termination_unconfirmed=termination_unconfirmed,
        returncode=returncode,
        finished_at=finished_at,
    )


def _fake_execution_terminal_fields(
    result_status: ReviewExecutionStatus,
) -> tuple[str, bool, int | None, str | None]:
    timestamp = "2026-07-17T00:00:00+00:00"
    if result_status == "completed":
        return "completed", False, 0, timestamp
    if result_status == "timed_out":
        return "timed_out", False, None, timestamp
    if result_status == "stopped":
        return "stopped", False, None, timestamp
    if result_status in {"provider_error", "parse_error"}:
        return "failed", False, 1, timestamp
    if result_status == "termination_unconfirmed":
        return "running", True, None, None
    return "running", False, None, None
