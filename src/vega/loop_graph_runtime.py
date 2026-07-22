from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .decision import DecisionStore
from .execution_control import ExecutionLease
from .loop_graph_checkpoint import (
    GraphCheckpointValidationError,
    TrustedCheckpointState,
    capture_checkpoint_data_snapshot,
    capture_checkpoint_store_identity,
    capture_trusted_checkpoint_data_for_resume,
    capture_trusted_checkpoint_state,
    checkpoint_config,
    clear_checkpoint_pending_marker,
    open_sqlite_checkpointer,
    require_checkpoint_file_continuity,
    require_checkpoint_file_layout_continuity,
    require_checkpoint_store_continuity,
    seal_checkpoint_manifest_for_resume,
    validate_checkpoint_manifest,
    write_checkpoint_pending_marker,
    write_checkpoint_manifest,
)
from .loop_graph_decision import (
    DecisionConsumption,
    consume_pending_decision,
    prepare_pending_decision,
    read_pending_decision,
    validate_pending_decision_bindings,
)
from .loop_graph_recovery import (
    GraphFaultPoint,
    GraphRecoveryValidationError,
    ReconciliationResult,
    append_graph_recovery_trace,
    capture_workspace_evidence,
    ensure_graph_recovery_execution_quiescent,
    hold_graph_operation_lease,
    predicted_runner_command,
    read_graph_run_config,
    read_or_create_attempt,
    read_workspace_evidence,
    reconcile_graph_resume,
    render_checkpoint_validation_failure_report,
    render_graph_recovery_report,
    runner_identity,
    workspace_snapshot_from_evidence,
    write_graph_recovery_report,
    write_graph_run_config,
)
from .loop_graph_state import (
    GRAPH_SCHEMA_VERSION,
    VegaGraphState,
    create_graph_state,
    refresh_graph_state,
    validate_graph_state,
    write_graph_state,
)
from .loop_step_result import (
    StepResultOutcome,
    StepResultOutputRef,
    build_step_result,
    read_step_result,
    write_step_result,
)
from .loop_steps import (
    HumanDecisionStepRequest,
    HumanDecisionStepResult,
    LoopStepName,
    LoopStepProgramDriver,
    WorkerEpochStepRequest,
)
from .models import GateResult, LoopAutomationState
from .redaction import (
    redact_text,
    write_redacted_json_atomic,
    write_redacted_text_atomic,
    write_redacted_text_create_once_atomic,
)
from .runner import RunnerResult
from .verification import VerificationRunResult
from .workspace_check import WorkspaceCheckResult, WorkspaceSnapshot

GRAPH_NODE_NAMES: tuple[LoopStepName, ...] = (
    "prepare_run",
    "capture_workspace",
    "execute_worker_epoch",
    "reconcile_workspace",
    "run_verification",
    "run_reflect",
    "evaluate_risk",
    "request_human_decision",
    "dispatch_review",
    "finalize_run",
)
GRAPH_FAILURE_REPORT_ARTIFACT = "graph-failure-report.md"
GRAPH_EVIDENCE_FAILURE_RESULT = (
    "FAIL: LangGraph 终态证据未能完成校验或持久化，"
    "业务 success 已撤销并转入人工检查"
)
GRAPH_CHECKPOINT_FAILURE_RESULT = (
    "FAIL: LangGraph checkpoint 终态证据不可信，"
    "原业务终态已撤销并转入人工检查"
)
GraphFaultInjector = Callable[[GraphFaultPoint], None]


class GraphExecutionInterrupted(RuntimeError):
    """故障注入模拟 Runtime 崩溃；不得按普通 Graph 失败撤销业务现场。"""


def execute_langgraph_program(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    automation_mode: str,
    worker_name: str,
    reviewer_name: str,
    verify: bool,
    timeout_seconds: int,
    fault_injector: GraphFaultInjector | None = None,
) -> Path:
    """用 SQLite checkpoint 驱动同一份 Vega 业务程序。"""

    with hold_graph_operation_lease(run_dir, "execute"):
        if driver.done or driver.current_name != "prepare_run":
            raise RuntimeError("LangGraph 顺序程序必须从 prepare_run 开始")
        write_graph_run_config(
            run_dir,
            automation_mode=cast(Literal["assist", "auto"], automation_mode),
            worker_name=worker_name,
            reviewer_name=reviewer_name,
            verify=verify,
            timeout_seconds=timeout_seconds,
        )
        initial_state = create_graph_state(run_dir)
        return _run_graph(
            run_dir,
            driver,
            initial_state=initial_state,
            fault_injector=fault_injector,
            resume=False,
            reconciliation=None,
            resume_command=None,
        )


def resume_langgraph_program(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    business_state: LoopAutomationState,
    request_reason: str,
    enable_state_persistence: Callable[[], None],
    fault_injector: GraphFaultInjector | None = None,
) -> Path:
    """从 SQLite checkpoint 和权威业务证据恢复 graph run。"""

    read_graph_run_config(run_dir)
    ensure_graph_recovery_execution_quiescent(run_dir)
    if not _checkpoint_is_trusted_or_stop(
        run_dir,
        business_state=business_state,
        request_reason=request_reason,
        enable_state_persistence=enable_state_persistence,
    ):
        return run_dir
    config = checkpoint_config(run_dir.name)
    try:
        snapshot, next_nodes, sealed_checkpoint = (
            _prepare_checkpoint_resume_snapshot(
                run_dir,
                driver,
                config=config,
                fault_injector=fault_injector,
            )
        )
    except GraphCheckpointValidationError as exc:
        _stop_for_checkpoint_validation_failure(
            run_dir,
            business_state=business_state,
            request_reason=request_reason,
            enable_state_persistence=enable_state_persistence,
            error=exc,
        )
        return run_dir
    next_node = next_nodes[0] if len(next_nodes) == 1 else None
    if len(next_nodes) > 1:
        raise GraphRecoveryValidationError(
            "Gate 3 顺序图 checkpoint 不得同时存在多个 next node"
        )
    reconciliation = reconcile_graph_resume(
        run_dir,
        state=business_state,
        next_node=next_node,
    )
    append_graph_recovery_trace(
        run_dir,
        "graph_reconciliation_finished",
        action=reconciliation.action,
        reason=reconciliation.reason,
        next_node=next_node,
    )
    write_graph_recovery_report(
        run_dir,
        render_graph_recovery_report(
            run_dir,
            state=business_state,
            result=reconciliation,
            request_reason=request_reason,
        ),
    )
    if reconciliation.action == "needs_human":
        enable_state_persistence()
        business_state.status = "needs_human"
        business_state.current_step = "graph_recovery_needs_human"
        business_state.artifacts = list(
            dict.fromkeys(
                [
                    *business_state.artifacts,
                    "graph-recovery-report.md",
                    "state.json",
                    "trace.jsonl",
                ]
            )
        )
        business_state.save(run_dir / "state.json")
        return run_dir
    if reconciliation.action == "terminal_recovery":
        try:
            return _recover_terminal_checkpoint(
                run_dir,
                business_state,
                expected_checkpoint_state=sealed_checkpoint,
            )
        except GraphCheckpointValidationError as exc:
            _stop_for_checkpoint_validation_failure(
                run_dir,
                business_state=business_state,
                request_reason=request_reason,
                enable_state_persistence=enable_state_persistence,
                error=exc,
            )
            return run_dir
    if next_node is None:
        raise GraphRecoveryValidationError(
            "非终态业务状态缺少可恢复的 graph next node"
        )
    _align_driver_for_resume(
        run_dir,
        driver,
        next_node=cast(LoopStepName, next_node),
        reconciliation=reconciliation,
    )
    enable_state_persistence()
    checkpoint_state = cast(VegaGraphState, snapshot.values)
    return _run_graph(
        run_dir,
        driver,
        initial_state=checkpoint_state,
        fault_injector=fault_injector,
        resume=True,
        reconciliation=reconciliation,
        resume_command=None,
        expected_checkpoint_state=sealed_checkpoint,
    )


def resume_langgraph_decision(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    business_state: LoopAutomationState,
    decision_id: str,
    enable_state_persistence: Callable[[], None],
    fault_injector: GraphFaultInjector | None = None,
) -> Path:
    """按 ledger decision id 恢复结构化 HITL，不接受自然语言批准。"""

    read_graph_run_config(run_dir)
    ensure_graph_recovery_execution_quiescent(run_dir)
    if not _checkpoint_is_trusted_or_stop(
        run_dir,
        business_state=business_state,
        request_reason=f"消费已记录 decision：{decision_id}",
        enable_state_persistence=enable_state_persistence,
    ):
        return run_dir
    config = checkpoint_config(run_dir.name)
    try:
        snapshot, next_nodes, sealed_checkpoint = (
            _prepare_checkpoint_resume_snapshot(
                run_dir,
                driver,
                config=config,
                fault_injector=fault_injector,
            )
        )
    except GraphCheckpointValidationError as exc:
        _stop_for_checkpoint_validation_failure(
            run_dir,
            business_state=business_state,
            request_reason=f"消费已记录 decision：{decision_id}",
            enable_state_persistence=enable_state_persistence,
            error=exc,
        )
        return run_dir
    retry_interrupted_node = (
        next_nodes == ()
        and len(snapshot.tasks) == 1
        and snapshot.tasks[0].name == "request_human_decision"
        and snapshot.tasks[0].error is not None
        and bool(snapshot.tasks[0].interrupts)
    )
    if (
        next_nodes != ("request_human_decision",)
        and not retry_interrupted_node
    ):
        raise GraphRecoveryValidationError(
            "当前 checkpoint 不是可消费 decision 的 HITL interrupt"
        )
    checkpoint_state = validate_graph_state(
        run_dir,
        cast(VegaGraphState, snapshot.values),
    )
    pending_id = checkpoint_state["pending_human_decision_id"]
    if pending_id is None:
        raise GraphRecoveryValidationError(
            "HITL checkpoint 缺少 pending decision identity"
        )
    pending = read_pending_decision(run_dir, pending_id)
    decision = DecisionStore(run_dir).get(decision_id)
    validate_pending_decision_bindings(
        run_dir,
        pending,
        state=business_state,
        decision=decision,
    )
    _align_driver_for_resume(
        run_dir,
        driver,
        next_node="request_human_decision",
        reconciliation=None,
    )
    enable_state_persistence()
    return _run_graph(
        run_dir,
        driver,
        initial_state=checkpoint_state,
        fault_injector=fault_injector,
        resume=True,
        reconciliation=None,
        resume_command=(
            None
            if retry_interrupted_node
            else Command(resume=decision_id)
        ),
        expected_checkpoint_state=sealed_checkpoint,
    )


def _checkpoint_is_trusted_or_stop(
    run_dir: Path,
    *,
    business_state: LoopAutomationState,
    request_reason: str,
    enable_state_persistence: Callable[[], None],
) -> bool:
    """只在 checkpoint 信任链完整时允许打开 SQLite。"""

    try:
        validate_checkpoint_manifest(run_dir)
    except GraphCheckpointValidationError as exc:
        _stop_for_checkpoint_validation_failure(
            run_dir,
            business_state=business_state,
            request_reason=request_reason,
            enable_state_persistence=enable_state_persistence,
            error=exc,
        )
        return False
    return True


def _prepare_checkpoint_resume_snapshot(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    config: dict[str, dict[str, str]],
    fault_injector: GraphFaultInjector | None,
) -> tuple[Any, tuple[str, ...], TrustedCheckpointState]:
    trusted_before = capture_trusted_checkpoint_state(run_dir)
    trusted_data = capture_trusted_checkpoint_data_for_resume(
        run_dir,
        trusted_before,
    )
    with open_sqlite_checkpointer(
        run_dir,
        require_existing=True,
        expected_trusted_state=trusted_before,
    ) as checkpointer:
        graph = _compile_graph(
            run_dir,
            driver,
            checkpointer,
            fault_injector=fault_injector,
            reconciliation=None,
        )
        opened_data = capture_checkpoint_data_snapshot(run_dir)
        require_checkpoint_file_layout_continuity(
            run_dir,
            expected=trusted_data,
            observed=opened_data,
        )
        opened_store = capture_checkpoint_store_identity(
            run_dir,
            manifest=trusted_before.manifest,
        )
        require_checkpoint_store_continuity(
            run_dir,
            expected=trusted_before.store,
            observed=opened_store,
        )
        snapshot = graph.get_state(config)
        next_nodes = tuple(snapshot.next)
        observed_data = capture_checkpoint_data_snapshot(run_dir)
        require_checkpoint_file_continuity(
            run_dir,
            expected=opened_data,
            observed=observed_data,
        )
        observed_store = capture_checkpoint_store_identity(
            run_dir,
            manifest=trusted_before.manifest,
        )
        require_checkpoint_store_continuity(
            run_dir,
            expected=opened_store,
            observed=observed_store,
        )
    stable_data = capture_checkpoint_data_snapshot(run_dir)
    require_checkpoint_file_continuity(
        run_dir,
        expected=observed_data,
        observed=stable_data,
    )
    stable_store = capture_checkpoint_store_identity(
        run_dir,
        manifest=trusted_before.manifest,
    )
    require_checkpoint_store_continuity(
        run_dir,
        expected=observed_store,
        observed=stable_store,
    )
    sealed_checkpoint = seal_checkpoint_manifest_for_resume(
        run_dir,
        config,
        expected_data_snapshot=stable_data,
    )
    return snapshot, next_nodes, sealed_checkpoint


def _stop_for_checkpoint_validation_failure(
    run_dir: Path,
    *,
    business_state: LoopAutomationState,
    request_reason: str,
    enable_state_persistence: Callable[[], None],
    error: GraphCheckpointValidationError,
) -> None:
    reason = "checkpoint_validation_failed"
    completion = _terminal_revocation_record(
        run_dir,
        reason=reason,
    )
    published_terminal = _published_terminal_record(run_dir)
    original_status = business_state.status
    original_step = business_state.current_step
    previous_status = (
        original_status
        if original_status in {"success", "failed"}
        else _terminal_record_status(completion)
        or _terminal_record_status(published_terminal)
    )
    diagnostic_status = previous_status or original_status
    diagnostic_step = (
        _terminal_record_step(published_terminal) or original_step
    )
    diagnostic_state = business_state.model_copy(
        update={
            "status": diagnostic_status,
            "current_step": diagnostic_step,
        }
    )
    enable_state_persistence()
    business_state.status = "needs_human"
    business_state.current_step = "graph_recovery_needs_human"
    business_state.artifacts = list(
        dict.fromkeys(
            [
                *business_state.artifacts,
                "graph-recovery-report.md",
                "state.json",
                "trace.jsonl",
                "eval.md",
            ]
        )
    )
    # state.json 是权威业务终态，必须先原子撤销，再写任何诊断或补偿证据。
    business_state.save(run_dir / "state.json")
    report = render_checkpoint_validation_failure_report(
        run_dir,
        state=diagnostic_state,
        request_reason=request_reason,
        validation_error=str(error),
    )
    if completion is None:
        write_graph_recovery_report(run_dir, report)
        if not _checkpoint_validation_failure_recorded(
            run_dir,
            original_status=diagnostic_status,
        ):
            append_graph_recovery_trace(
                run_dir,
                "graph_checkpoint_validation_failed",
                reason_code=reason,
                original_status=diagnostic_status,
                original_step=diagnostic_step,
                detail=str(error),
            )
    if previous_status in {"success", "failed"}:
        _revoke_untrusted_terminal(
            run_dir,
            business_state,
            previous_status=previous_status,
            reason=reason,
            current_step="graph_recovery_needs_human",
            error_type=type(error).__name__,
            report_artifact="graph-recovery-report.md",
            failure_result=GRAPH_CHECKPOINT_FAILURE_RESULT,
            invalidate_delivery_reports=True,
        )


def _run_graph(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    initial_state: VegaGraphState,
    fault_injector: GraphFaultInjector | None,
    resume: bool,
    reconciliation: ReconciliationResult | None,
    resume_command: Command | None,
    expected_checkpoint_state: TrustedCheckpointState | None = None,
) -> Path:
    validate_graph_state(
        run_dir,
        initial_state,
        require_task_contract=resume,
    )
    config = checkpoint_config(run_dir.name)
    result: VegaGraphState | None = None
    snapshot_after = None
    error: Exception | None = None
    try:
        with open_sqlite_checkpointer(
            run_dir,
            require_existing=resume,
            expected_trusted_state=expected_checkpoint_state,
        ) as checkpointer:
            graph = _compile_graph(
                run_dir,
                driver,
                checkpointer,
                fault_injector=fault_injector,
                reconciliation=reconciliation,
            )
            invoke_config = {
                **config,
                "recursion_limit": _graph_recursion_limit(run_dir),
            }
            invoked = graph.invoke(
                (
                    resume_command
                    if resume
                    else initial_state
                ),
                config=invoke_config,
            )
            result = cast(VegaGraphState, invoked)
            snapshot_after = graph.get_state(config)
    except Exception as exc:  # noqa: BLE001 - manifest 必须在错误后完成绑定
        error = exc
    finally:
        checkpoint_path = run_dir / "graph" / "checkpoints.sqlite"
        manifest_written = False
        if checkpoint_path.is_file():
            try:
                write_checkpoint_manifest(run_dir, config)
                manifest_written = True
            except Exception as manifest_exc:  # noqa: BLE001 - 必须撤销已发布 success
                if error is not None:
                    manifest_exc.add_note(
                        "LangGraph 执行同时发生异常；"
                        f"原异常类型：{type(error).__name__}"
                    )
                error = manifest_exc
        if manifest_written:
            try:
                clear_checkpoint_pending_marker(run_dir)
            except Exception as marker_exc:  # noqa: BLE001 - pending marker 也是信任边界
                if error is not None:
                    marker_exc.add_note(
                        "LangGraph 执行同时发生异常；"
                        f"原异常类型：{type(error).__name__}"
                    )
                error = marker_exc

    if error is not None:
        if isinstance(error, GraphExecutionInterrupted):
            raise error
        try:
            _quarantine_untrusted_success(run_dir, error)
        except Exception as quarantine_exc:
            raise RuntimeError(
                "LangGraph 终态证据失败，且无法撤销不可信 success"
            ) from quarantine_exc
        raise error

    if snapshot_after is not None and any(
        task.interrupts
        for task in snapshot_after.tasks
    ):
        interrupted_state = validate_graph_state(
            run_dir,
            cast(VegaGraphState, snapshot_after.values),
        )
        write_graph_state(run_dir, interrupted_state)
        return run_dir

    assert result is not None
    if not driver.done:
        raise RuntimeError(
            f"LangGraph 已结束，但 Vega 顺序程序仍停在 {driver.current_name}"
        )
    if driver.result.resolve() != run_dir.resolve():
        raise RuntimeError(
            "LangGraph 返回的 run 与 Vega 顺序程序结果不一致"
        )
    try:
        validated = validate_graph_state(run_dir, result)
        write_graph_state(run_dir, validated)
    except Exception as exc:
        try:
            _quarantine_untrusted_success(run_dir, exc)
        except Exception as quarantine_exc:
            raise RuntimeError(
                "LangGraph 终态证据失败，且无法撤销不可信 success"
            ) from quarantine_exc
        raise
    return driver.result


def _compile_graph(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    checkpointer: object,
    *,
    fault_injector: GraphFaultInjector | None,
    reconciliation: ReconciliationResult | None,
):
    graph_builder = StateGraph(VegaGraphState)
    for node_name in GRAPH_NODE_NAMES:
        graph_builder.add_node(
            node_name,
            _build_graph_node(
                run_dir,
                driver,
                node_name,
                fault_injector=fault_injector,
                reconciliation=reconciliation,
            ),
        )
    graph_builder.add_edge(START, "prepare_run")
    return graph_builder.compile(checkpointer=checkpointer)


def _build_graph_node(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    node_name: LoopStepName,
    *,
    fault_injector: GraphFaultInjector | None,
    reconciliation: ReconciliationResult | None,
) -> Callable[[VegaGraphState], Command]:
    def execute_node(graph_state: VegaGraphState) -> Command:
        validated = validate_graph_state(
            run_dir,
            graph_state,
            require_task_contract=node_name != "prepare_run",
        )
        latest_step_result_id = validated["latest_step_result_id"]
        if _is_reconciled_checkpoint_node(node_name, driver, reconciliation):
            if (
                node_name == "execute_worker_epoch"
                and reconciliation is not None
                and reconciliation.step_result is not None
            ):
                latest_step_result_id = reconciliation.step_result.step_result_id
        elif node_name == "capture_workspace":
            baseline = cast(
                WorkspaceSnapshot,
                driver.execute_current(expected=node_name),
            )
            worker_request = cast(
                WorkerEpochStepRequest,
                driver.current_instruction.request,
            )
            capture_workspace_evidence(
                run_dir,
                worker_request.repo_path,
                iteration=worker_request.execution_context.iteration or 1,
                phase="before-worker",
                baseline=baseline,
            )
        elif node_name == "execute_worker_epoch":
            latest_step_result_id = _execute_worker_node(
                run_dir,
                driver,
                reconciliation=reconciliation,
                fault_injector=fault_injector,
            )
        elif node_name == "request_human_decision":
            _execute_human_decision_node(
                run_dir,
                driver,
                validated,
                fault_injector=fault_injector,
            )
        else:
            driver.execute_current(expected=node_name)
        refreshed = refresh_graph_state(run_dir, validated)
        if latest_step_result_id is not None:
            refreshed["latest_step_result_id"] = latest_step_result_id
        if (
            node_name == "evaluate_risk"
            and not driver.done
            and driver.current_name == "request_human_decision"
        ):
            request = cast(
                HumanDecisionStepRequest,
                driver.current_instruction.request,
            )
            pending = prepare_pending_decision(
                run_dir,
                request,
                latest_step_result_id=latest_step_result_id,
            )
            refreshed["pending_human_decision_id"] = pending.pending_id
        elif node_name == "request_human_decision":
            refreshed["pending_human_decision_id"] = None
        refreshed = validate_graph_state(run_dir, refreshed)
        business_state = LoopAutomationState.model_validate_json(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
        if (
            node_name == "reconcile_workspace"
            and business_state.status == "running"
            and business_state.current_step == "verify"
        ):
            _emit_fault(fault_injector, "after_state_before_checkpoint")
        if (
            node_name == "finalize_run"
            and business_state.status in {"success", "failed", "needs_human"}
        ):
            _emit_fault(
                fault_injector,
                "after_terminal_state_before_checkpoint",
            )
        destination = END if driver.done else driver.current_name
        return Command(update=refreshed, goto=destination)

    execute_node.__name__ = f"vega_{node_name}"
    return execute_node


def _execute_worker_node(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    reconciliation: ReconciliationResult | None,
    fault_injector: GraphFaultInjector | None,
) -> str | None:
    request = cast(
        WorkerEpochStepRequest,
        driver.current_instruction.request,
    )
    iteration = request.execution_context.iteration or 1
    if (
        reconciliation is not None
        and reconciliation.action == "safe_reuse_step_result"
        and reconciliation.step_result is not None
        and reconciliation.step_result.iteration == iteration
    ):
        result = _runner_result_from_step_result(
            run_dir,
            reconciliation.step_result,
        )
        driver.replay_current(result, expected="execute_worker_epoch")
        return reconciliation.step_result.step_result_id

    before = read_workspace_evidence(
        run_dir,
        iteration,
        "before-worker",
    )
    command = predicted_runner_command(
        request.runner,
        request.repo_path,
        request.sandbox,
    )
    identity = runner_identity(request.runner, request.sandbox)
    attempt = read_or_create_attempt(
        run_dir,
        state=LoopAutomationState.model_validate_json(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        ),
        iteration=iteration,
        runner_identity=identity,
        before_workspace=before,
        command=command,
        input_payload={
            "prompt_sha256": hashlib.sha256(
                request.prompt.encode("utf-8")
            ).hexdigest(),
            "sandbox": request.sandbox,
            "timeout_seconds": request.timeout_seconds,
            "runner_identity": identity,
        },
    )
    _emit_fault(fault_injector, "before_external_execution")
    enriched_context = replace(
        request.execution_context,
        engine="langgraph",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        step_id=attempt.step_id,
        attempt_id=attempt.attempt_id,
        idempotency_key=attempt.idempotency_key,
        replay_class=attempt.replay_class,
        runner_identity=attempt.runner_identity,
        base_head=attempt.base_head,
        before_workspace_fingerprint=attempt.before_workspace_fingerprint,
        policy_snapshot_sha256=attempt.policy_snapshot_sha256,
        input_fingerprint=attempt.input_fingerprint,
        command_sha256=attempt.command_sha256,
        fault_injector=(
            (lambda point: _emit_fault(fault_injector, cast(GraphFaultPoint, point)))
            if fault_injector is not None
            else None
        ),
    )
    result = cast(
        RunnerResult,
        driver.execute_current(
            expected="execute_worker_epoch",
            request_override=replace(
                request,
                execution_context=enriched_context,
            ),
        ),
    )
    if result.status == "skipped":
        return None
    execution_path = enriched_context.execution_dir / "execution.json"
    if not execution_path.is_file():
        raise GraphRecoveryValidationError(
            "external_non_replayable worker 缺少 execution.json"
        )
    execution = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    if execution.attempt_id != attempt.attempt_id:
        raise GraphRecoveryValidationError(
            "execution.json 未绑定当前 attempt identity"
        )
    after = capture_workspace_evidence(
        run_dir,
        request.repo_path,
        iteration=iteration,
        phase="after-worker",
    )
    output_path = (
        run_dir
        / "iterations"
        / f"{iteration:02d}"
        / "worker-output.txt"
    )
    workspace_path = (
        run_dir
        / "iterations"
        / f"{iteration:02d}"
        / "workspace-after-worker.json"
    )
    manifest = build_step_result(
        run_dir,
        attempt=attempt,
        after_workspace_fingerprint=after.fingerprint,
        execution_ref=execution_path.relative_to(run_dir).as_posix(),
        output_refs=[
            StepResultOutputRef(
                path=output_path.relative_to(run_dir).as_posix(),
                sha256=_sha256_file(output_path),
            ),
            StepResultOutputRef(
                path=workspace_path.relative_to(run_dir).as_posix(),
                sha256=_sha256_file(workspace_path),
            ),
        ],
        outcome=StepResultOutcome(
            status=result.status,
            summary="worker 外部动作已完成并绑定当前 workspace",
            error=result.error,
        ),
    )
    write_step_result(run_dir, manifest)
    write_checkpoint_pending_marker(
        run_dir,
        step_id=manifest.step_id,
        attempt_id=manifest.attempt_id,
        step_result_id=manifest.step_result_id,
    )
    _emit_fault(fault_injector, "after_step_result_before_state")
    return manifest.step_result_id


def _execute_human_decision_node(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    graph_state: VegaGraphState,
    *,
    fault_injector: GraphFaultInjector | None,
) -> DecisionConsumption:
    pending_id = graph_state["pending_human_decision_id"]
    if pending_id is None:
        raise GraphRecoveryValidationError(
            "request_human_decision 缺少 pending identity"
        )
    pending = read_pending_decision(run_dir, pending_id)
    resumed_decision_id = interrupt(
        {
            "kind": "vega_human_decision",
            "pending_id": pending.pending_id,
            "decision_type": pending.decision_type,
            "allowed_decisions": pending.allowed_decisions,
            "pending_ref": pending.artifact_ref,
        }
    )
    if not isinstance(resumed_decision_id, str) or not resumed_decision_id.strip():
        raise GraphRecoveryValidationError(
            "HITL resume 只接受非空 decision id"
        )
    decision = DecisionStore(run_dir).get(resumed_decision_id)
    business_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    validate_pending_decision_bindings(
        run_dir,
        pending,
        state=business_state,
        decision=decision,
    )
    consumption = consume_pending_decision(
        run_dir,
        pending,
        decision,
    )
    _emit_fault(
        fault_injector,
        "after_decision_consumption_before_state",
    )
    driver.replay_current(
        HumanDecisionStepResult(
            decision=decision.decision,
            decision_id=decision.id,
            consumption_ref=(
                "graph/decision-consumptions/"
                f"{consumption.pending_id}.json"
            ),
        ),
        expected="request_human_decision",
    )
    return consumption


def _align_driver_for_resume(
    run_dir: Path,
    driver: LoopStepProgramDriver,
    *,
    next_node: LoopStepName,
    reconciliation: ReconciliationResult | None,
) -> None:
    business_state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    iteration = max(1, business_state.current_iteration)
    if iteration != 1:
        raise GraphRecoveryValidationError(
            "Gate 3 driver recovery 只支持第一轮，已拒绝错绑历史 Step Result"
        )
    while not driver.done and driver.current_name != next_node:
        current = driver.current_name
        if current == "prepare_run":
            if not business_state.brief_run:
                raise GraphRecoveryValidationError(
                    "恢复 prepare_run 时缺少 brief_run"
                )
            driver.replay_current(
                run_dir.parent / business_state.brief_run,
                expected=current,
            )
            continue
        if current == "capture_workspace":
            evidence = read_workspace_evidence(
                run_dir,
                iteration,
                "before-worker",
            )
            driver.replay_current(
                workspace_snapshot_from_evidence(evidence),
                expected=current,
            )
            continue
        if current == "execute_worker_epoch":
            step_result = (
                reconciliation.step_result
                if reconciliation is not None
                else None
            ) or read_step_result(
                run_dir,
                f"worker-iteration-{iteration:02d}",
            )
            driver.replay_current(
                _runner_result_from_step_result(run_dir, step_result),
                expected=current,
            )
            continue
        if current == "reconcile_workspace":
            driver.replay_current(
                _workspace_check_result_from_artifact(run_dir, iteration),
                expected=current,
            )
            continue
        if current == "run_verification":
            driver.replay_current(
                _verification_result_from_artifact(run_dir, iteration),
                expected=current,
            )
            continue
        if current == "run_reflect":
            driver.replay_current(
                _reflect_run_from_artifact(run_dir, iteration),
                expected=current,
            )
            continue
        if current == "evaluate_risk":
            driver.replay_current(
                _gate_result_from_artifact(run_dir, iteration),
                expected=current,
            )
            continue
        raise GraphRecoveryValidationError(
            f"Gate 3 无法从证据重建到节点 {next_node}，停在 {current}"
        )
    if driver.done or driver.current_name != next_node:
        raise GraphRecoveryValidationError(
            f"恢复 driver 未对齐 checkpoint next node：{next_node}"
        )
    if (
        reconciliation is not None
        and
        next_node == "execute_worker_epoch"
        and reconciliation.action == "safe_reuse_step_result"
        and reconciliation.step_result is not None
    ):
        driver.replay_current(
            _runner_result_from_step_result(
                run_dir,
                reconciliation.step_result,
            ),
            expected=next_node,
        )
    elif (
        reconciliation is not None
        and
        next_node == "reconcile_workspace"
        and reconciliation.action == "safe_resume_from_state"
    ):
        driver.replay_current(
            _workspace_check_result_from_artifact(run_dir, iteration),
            expected=next_node,
        )


def _is_reconciled_checkpoint_node(
    node_name: LoopStepName,
    driver: LoopStepProgramDriver,
    reconciliation: ReconciliationResult | None,
) -> bool:
    if reconciliation is None or driver.done or driver.current_name == node_name:
        return False
    return (
        node_name == "execute_worker_epoch"
        and reconciliation.action == "safe_reuse_step_result"
        and reconciliation.next_node == node_name
    ) or (
        node_name == "reconcile_workspace"
        and reconciliation.action == "safe_resume_from_state"
        and reconciliation.next_node == node_name
    )


def _runner_result_from_step_result(
    run_dir: Path,
    manifest,
) -> RunnerResult:
    output_ref = next(
        (
            item
            for item in manifest.output_refs
            if item.path.endswith("/worker-output.txt")
        ),
        None,
    )
    if output_ref is None:
        raise GraphRecoveryValidationError(
            "worker step result 缺少 worker-output 引用"
        )
    output = run_dir.joinpath(*output_ref.path.split("/")).read_text(
        encoding="utf-8"
    )
    execution = ExecutionLease.model_validate_json(
        run_dir.joinpath(*manifest.execution_ref.split("/")).read_text(
            encoding="utf-8"
        )
    )
    return RunnerResult(
        status=manifest.result.status,
        output=output,
        error=manifest.result.error,
        command=execution.command,
    )


def _workspace_check_result_from_artifact(
    run_dir: Path,
    iteration: int,
) -> WorkspaceCheckResult:
    path = (
        run_dir
        / "iterations"
        / f"{iteration:02d}"
        / "workspace-check.json"
    )
    if not path.is_file():
        raise GraphRecoveryValidationError(
            "state 已推进到 verify，但缺少 workspace-check.json"
        )
    try:
        return WorkspaceCheckResult.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - 恢复证据必须统一 fail-closed
        raise GraphRecoveryValidationError(
            "workspace-check.json 无法作为恢复证据"
        ) from exc


def _verification_result_from_artifact(
    run_dir: Path,
    iteration: int,
) -> VerificationRunResult:
    iteration_dir = run_dir / "iterations" / f"{iteration:02d}"
    result_path = iteration_dir / "verification-result.json"
    summary_path = iteration_dir / "verification-summary.md"
    if not result_path.is_file() or not summary_path.is_file():
        raise GraphRecoveryValidationError(
            "HITL driver 重建缺少 verification artifacts"
        )
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        command_count = payload["command_count"]
        failed_count = payload["failed_count"]
        if (
            type(command_count) is not int
            or command_count < 0
            or type(failed_count) is not int
            or failed_count < 0
        ):
            raise ValueError("verification count 不合法")
        interruption_status = payload.get("interruption_status")
        if interruption_status not in {
            None,
            "timed_out",
            "stopped",
            "termination-unconfirmed",
        }:
            raise ValueError("verification interruption status 不合法")
        return VerificationRunResult(
            summary_path=summary_path,
            result_path=result_path,
            command_count=command_count,
            failed_count=failed_count,
            interruption_status=interruption_status,
            interruption_command=payload.get("interruption_command"),
            interruption_reason=payload.get("interruption_reason"),
        )
    except Exception as exc:  # noqa: BLE001 - 恢复证据必须统一 fail-closed
        raise GraphRecoveryValidationError(
            "verification-result.json 无法作为恢复证据"
        ) from exc


def _reflect_run_from_artifact(
    run_dir: Path,
    iteration: int,
) -> Path:
    path = (
        run_dir
        / "iterations"
        / f"{iteration:02d}"
        / "reflect-run.txt"
    )
    if not path.is_file():
        raise GraphRecoveryValidationError(
            "HITL driver 重建缺少 reflect-run.txt"
        )
    raw = path.read_text(encoding="utf-8").strip()
    stored = Path(raw)
    if not raw or stored.name in {"", ".", ".."}:
        raise GraphRecoveryValidationError("reflect-run.txt 内容不合法")
    candidate = (run_dir.parent / stored.name).resolve()
    if (
        candidate.parent != run_dir.parent.resolve()
        or not candidate.is_dir()
        or candidate != stored.resolve()
    ):
        raise GraphRecoveryValidationError(
            "reflect-run.txt 未绑定当前 runs root 的直接子目录"
        )
    return candidate


def _gate_result_from_artifact(
    run_dir: Path,
    iteration: int,
) -> GateResult:
    path = (
        run_dir
        / "iterations"
        / f"{iteration:02d}"
        / "risk-gate-result.json"
    )
    if not path.is_file():
        raise GraphRecoveryValidationError(
            "HITL driver 重建缺少 risk-gate-result.json"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "success":
            raise ValueError("risk gate 没有可信 success 结果")
        if payload.get("iteration") != iteration:
            raise ValueError("risk gate iteration 不一致")
        return GateResult.model_validate(
            {
                key: value
                for key, value in payload.items()
                if key
                in {
                    "risk",
                    "recommendation",
                    "reasons",
                    "changed_files",
                    "scope_profile",
                }
            }
        )
    except Exception as exc:  # noqa: BLE001 - 恢复证据必须统一 fail-closed
        raise GraphRecoveryValidationError(
            "risk-gate-result.json 无法作为恢复证据"
        ) from exc


def _recover_terminal_checkpoint(
    run_dir: Path,
    business_state: LoopAutomationState,
    *,
    expected_checkpoint_state: TrustedCheckpointState,
) -> Path:
    config = checkpoint_config(run_dir.name)
    trusted_data = capture_trusted_checkpoint_data_for_resume(
        run_dir,
        expected_checkpoint_state,
    )
    with open_sqlite_checkpointer(
        run_dir,
        require_existing=True,
        expected_trusted_state=expected_checkpoint_state,
    ) as checkpointer:
        graph_builder = StateGraph(VegaGraphState)
        graph_builder.add_node("finalize_run", lambda state: state)
        graph_builder.add_edge(START, "finalize_run")
        graph_builder.add_edge("finalize_run", END)
        graph = graph_builder.compile(checkpointer=checkpointer)
        opened_data = capture_checkpoint_data_snapshot(run_dir)
        require_checkpoint_file_layout_continuity(
            run_dir,
            expected=trusted_data,
            observed=opened_data,
        )
        opened_store = capture_checkpoint_store_identity(
            run_dir,
            manifest=expected_checkpoint_state.manifest,
        )
        require_checkpoint_store_continuity(
            run_dir,
            expected=expected_checkpoint_state.store,
            observed=opened_store,
        )
        snapshot = graph.get_state(config)
        observed_data = capture_checkpoint_data_snapshot(run_dir)
        require_checkpoint_file_continuity(
            run_dir,
            expected=opened_data,
            observed=observed_data,
        )
        observed_store = capture_checkpoint_store_identity(
            run_dir,
            manifest=expected_checkpoint_state.manifest,
        )
        require_checkpoint_store_continuity(
            run_dir,
            expected=opened_store,
            observed=observed_store,
        )
        graph_state = refresh_graph_state(
            run_dir,
            cast(VegaGraphState, snapshot.values),
        )
        graph_state = validate_graph_state(run_dir, graph_state)
        graph.update_state(config, graph_state, as_node="finalize_run")
    write_checkpoint_manifest(run_dir, config)
    write_graph_state(run_dir, graph_state)
    append_graph_recovery_trace(
        run_dir,
        "graph_terminal_recovered",
        status=business_state.status,
        current_step=business_state.current_step,
    )
    return run_dir


def _emit_fault(
    fault_injector: GraphFaultInjector | None,
    point: GraphFaultPoint,
) -> None:
    if fault_injector is not None:
        fault_injector(point)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _graph_recursion_limit(run_dir: Path) -> int:
    state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    iterations = max(1, state.max_iterations)
    # 每轮按完整节点集合预留预算，既覆盖 5 轮 CLI 上限，也保留有限的失控保护。
    return 4 + len(GRAPH_NODE_NAMES) * (iterations + 1)


def _quarantine_untrusted_success(
    run_dir: Path,
    error: Exception,
) -> None:
    state_path = run_dir / "state.json"
    state = LoopAutomationState.model_validate_json(
        state_path.read_text(encoding="utf-8")
    )
    if state.engine != "langgraph" or state.status != "success":
        return

    state.status = "needs_human"
    state.current_step = "graph_evidence_failed"
    state.save(state_path)

    report_path = run_dir / GRAPH_FAILURE_REPORT_ARTIFACT
    write_redacted_text_atomic(
        report_path,
        "\n".join(
            [
                "# Graph Evidence Failure",
                "",
                "- LangGraph 已完成业务 finalize，"
                "但最终 Graph State 未能可靠落盘。",
                "- 原 success 已撤销，当前 run 固定为 `needs_human`。",
                f"- 错误类型：`{type(error).__name__}`",
                "- 不得把该 run 作为成功证据；请保留现场并检查 Graph State 写入边界。",
            ]
        )
        + "\n",
    )
    if GRAPH_FAILURE_REPORT_ARTIFACT not in state.artifacts:
        state.artifacts.append(GRAPH_FAILURE_REPORT_ARTIFACT)
    _revoke_untrusted_terminal(
        run_dir,
        state,
        previous_status="success",
        reason="graph_evidence_failed",
        error_type=type(error).__name__,
        current_step="graph_evidence_failed",
        report_artifact=GRAPH_FAILURE_REPORT_ARTIFACT,
        failure_result=GRAPH_EVIDENCE_FAILURE_RESULT,
        invalidate_delivery_reports=False,
    )


def _revoke_untrusted_terminal(
    run_dir: Path,
    state: LoopAutomationState,
    *,
    previous_status: str,
    reason: str,
    current_step: str,
    error_type: str,
    report_artifact: str,
    failure_result: str,
    invalidate_delivery_reports: bool,
) -> None:
    from .loop_runtime import (
        RUN_TERMINAL_REVOKED_EVENT,
        RUN_TERMINAL_STATE_REVOKED_EVENT,
        render_eval,
        run_loop_eval,
    )

    if previous_status not in {"success", "failed"}:
        raise GraphRecoveryValidationError(
            "只有已发布 success/failed 才能追加终态撤销"
        )
    state.status = "needs_human"
    state.current_step = current_step
    state.artifacts = list(
        dict.fromkeys(
            [
                *state.artifacts,
                report_artifact,
                "state.json",
                "trace.jsonl",
                "eval.md",
            ]
        )
    )
    state.save(run_dir / "state.json")
    completion_recorded = _terminal_revocation_already_recorded(
        run_dir,
        reason=reason,
    )
    if (
        not completion_recorded
        and not _terminal_state_revocation_already_recorded(
            run_dir,
            reason=reason,
        )
    ):
        append_graph_recovery_trace(
            run_dir,
            RUN_TERMINAL_STATE_REVOKED_EVENT,
            reason=reason,
            previous_status=previous_status,
            status=state.status,
            current_step=state.current_step,
            error_type=error_type,
            report_artifact=report_artifact,
        )

    def persist_eval() -> None:
        state.eval_results = [
            *run_loop_eval(
                run_dir,
                state.artifacts,
                require_terminal=True,
            ),
            failure_result,
        ]
        write_redacted_text_atomic(
            run_dir / "eval.md",
            render_eval(state.eval_results),
        )
        state.save(run_dir / "state.json")

    # 先让 eval 明确失效；即使后续报告阶段崩溃，也不会保留旧 PASS。
    persist_eval()
    if invalidate_delivery_reports:
        try:
            invalidated_artifacts = (
                _invalidate_checkpoint_delivery_reports(
                    run_dir,
                    previous_status=previous_status,
                    reason=reason,
                )
            )
        except GraphRecoveryValidationError:
            state.artifacts = list(
                dict.fromkeys(
                    [
                        *state.artifacts,
                        *_existing_checkpoint_revocation_artifacts(
                            run_dir
                        ),
                    ]
                )
            )
            persist_eval()
            raise
        state.artifacts = list(
            dict.fromkeys(
                [
                    *state.artifacts,
                    *invalidated_artifacts,
                ]
            )
        )
        persist_eval()
    # 完成事件必须是补偿事务的最后一个必需写入；eval 先依据撤销事实进入
    # fail-closed，再由 trace 的完成事件标记全部阶段已经落盘。
    if not completion_recorded:
        append_graph_recovery_trace(
            run_dir,
            RUN_TERMINAL_REVOKED_EVENT,
            reason=reason,
            previous_status=previous_status,
            status=state.status,
            current_step=state.current_step,
            error_type=error_type,
            report_artifact=report_artifact,
        )


def _read_graph_recovery_trace(run_dir: Path) -> list[dict[str, object]]:
    trace_path = run_dir / "trace.jsonl"
    try:
        raw_items = [
            json.loads(line)
            for line in trace_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _terminal_revocation_record(
    run_dir: Path,
    *,
    reason: str,
) -> dict[str, object] | None:
    for item in reversed(_read_graph_recovery_trace(run_dir)):
        if (
            item.get("event") == "run_terminal_revoked"
            and item.get("reason") == reason
            and item.get("status") == "needs_human"
        ):
            return item
    return None


def _terminal_state_revocation_record(
    run_dir: Path,
    *,
    reason: str,
) -> dict[str, object] | None:
    for item in reversed(_read_graph_recovery_trace(run_dir)):
        if (
            item.get("event") == "run_terminal_state_revoked"
            and item.get("reason") == reason
            and item.get("status") == "needs_human"
        ):
            return item
    return None


def _published_terminal_record(
    run_dir: Path,
) -> dict[str, object] | None:
    for item in reversed(_read_graph_recovery_trace(run_dir)):
        if (
            item.get("event") == "run_finished"
            and item.get("status") in {"success", "failed"}
        ):
            return item
    return None


def _terminal_record_status(
    record: dict[str, object] | None,
) -> str | None:
    if record is None:
        return None
    status = record.get("previous_status", record.get("status"))
    return status if status in {"success", "failed"} else None


def _terminal_record_step(
    record: dict[str, object] | None,
) -> str | None:
    if record is None:
        return None
    current_step = record.get("current_step")
    return current_step if isinstance(current_step, str) else None


def _checkpoint_validation_failure_recorded(
    run_dir: Path,
    *,
    original_status: str,
) -> bool:
    return any(
        item.get("event") == "graph_checkpoint_validation_failed"
        and item.get("reason_code") == "checkpoint_validation_failed"
        and item.get("original_status") == original_status
        for item in _read_graph_recovery_trace(run_dir)
    )


def _terminal_revocation_already_recorded(
    run_dir: Path,
    *,
    reason: str,
) -> bool:
    return _terminal_revocation_record(run_dir, reason=reason) is not None


def _terminal_state_revocation_already_recorded(
    run_dir: Path,
    *,
    reason: str,
) -> bool:
    return (
        _terminal_state_revocation_record(run_dir, reason=reason)
        is not None
    )


def _existing_checkpoint_revocation_artifacts(
    run_dir: Path,
) -> list[str]:
    candidates = (
        "final-report.md",
        "final-report.before-checkpoint-revocation.md",
        "finish-report.md",
        "finish-report.before-checkpoint-revocation.md",
        "finish-summary.json",
        "finish-summary.before-checkpoint-revocation.json",
    )
    return [
        artifact
        for artifact in candidates
        if run_dir.joinpath(artifact).is_file()
    ]


def _invalidate_checkpoint_delivery_reports(
    run_dir: Path,
    *,
    previous_status: str,
    reason: str,
) -> list[str]:
    artifacts: list[str] = []
    final_path = run_dir / "final-report.md"
    final_archive = "final-report.before-checkpoint-revocation.md"
    final_archive_path = run_dir / final_archive
    if _delivery_report_already_invalidated(
        final_path,
        final_archive_path,
        title="Final Report",
        previous_status=previous_status,
        reason=reason,
    ):
        if final_archive_path.is_file():
            artifacts.append(final_archive)
    else:
        previous_final_sha = _archive_text_artifact(
            final_path,
            final_archive_path,
        )
        if previous_final_sha is not None:
            artifacts.append(final_archive)
        write_redacted_text_atomic(
            final_path,
            _render_invalidated_delivery_report(
                title="Final Report",
                previous_status=previous_status,
                reason=reason,
                previous_sha256=previous_final_sha,
            ),
        )
    artifacts.append("final-report.md")

    finish_report = run_dir / "finish-report.md"
    finish_summary = run_dir / "finish-summary.json"
    if finish_report.exists():
        finish_report_archive = (
            "finish-report.before-checkpoint-revocation.md"
        )
        finish_report_archive_path = run_dir / finish_report_archive
        if _delivery_report_already_invalidated(
            finish_report,
            finish_report_archive_path,
            title="Finish Report",
            previous_status=previous_status,
            reason=reason,
        ):
            if finish_report_archive_path.is_file():
                artifacts.append(finish_report_archive)
        else:
            previous_finish_sha = _archive_text_artifact(
                finish_report,
                finish_report_archive_path,
            )
            artifacts.append(finish_report_archive)
            write_redacted_text_atomic(
                finish_report,
                _render_invalidated_delivery_report(
                    title="Finish Report",
                    previous_status=previous_status,
                    reason=reason,
                    previous_sha256=previous_finish_sha,
                ),
            )
        artifacts.append("finish-report.md")
    if finish_summary.exists():
        finish_summary_archive = (
            "finish-summary.before-checkpoint-revocation.json"
        )
        finish_summary_archive_path = run_dir / finish_summary_archive
        payload = _read_json_object(finish_summary)
        if not _finish_summary_already_invalidated(
            payload,
            previous_status=previous_status,
            reason=reason,
        ):
            _archive_text_artifact(
                finish_summary,
                finish_summary_archive_path,
            )
            payload.update(
                {
                    "run_id": run_dir.name,
                    "loop_status": "needs_human",
                    "finish_status": "needs_human",
                    "invalidated": True,
                    "invalidation_reason": reason,
                    "previous_loop_status": previous_status,
                }
            )
            if finish_report.is_file():
                payload["finish_report_ref"] = "finish-report.md"
                payload["finish_report_sha256"] = _sha256_file(
                    finish_report
                )
            write_redacted_json_atomic(finish_summary, payload)
        if finish_summary_archive_path.is_file():
            artifacts.append(finish_summary_archive)
        artifacts.append("finish-summary.json")
    return artifacts


def _delivery_report_already_invalidated(
    report: Path,
    archive: Path,
    *,
    title: str,
    previous_status: str,
    reason: str,
) -> bool:
    if not report.is_file():
        return False
    try:
        content = report.read_text(encoding="utf-8", errors="replace")
        archived_content = (
            archive.read_text(encoding="utf-8", errors="replace")
            if archive.is_file()
            else None
        )
    except OSError as exc:
        raise GraphRecoveryValidationError(
            f"无法读取交付报告失效阶段：{report.name}"
        ) from exc
    previous_sha256 = (
        hashlib.sha256(archived_content.encode("utf-8")).hexdigest()
        if archived_content is not None
        else None
    )
    expected = _render_invalidated_delivery_report(
        title=title,
        previous_status=previous_status,
        reason=reason,
        previous_sha256=previous_sha256,
    )
    return content == expected


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _finish_summary_already_invalidated(
    payload: dict[str, object],
    *,
    previous_status: str,
    reason: str,
) -> bool:
    return (
        payload.get("loop_status") == "needs_human"
        and payload.get("finish_status") == "needs_human"
        and payload.get("invalidated") is True
        and payload.get("invalidation_reason") == reason
        and payload.get("previous_loop_status") == previous_status
    )


def _archive_text_artifact(
    source: Path,
    archive: Path,
) -> str | None:
    if not source.is_file():
        return None
    try:
        content = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GraphRecoveryValidationError(
            f"无法归档旧交付报告：{source.name}"
        ) from exc
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if archive.exists():
        try:
            existing = archive.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise GraphRecoveryValidationError(
                f"无法读取既有交付报告归档：{archive.name}"
            ) from exc
        if existing != content:
            raise GraphRecoveryValidationError(
                f"交付报告归档已存在且内容不同：{archive.name}"
            )
    else:
        try:
            write_redacted_text_create_once_atomic(archive, content)
        except FileExistsError:
            try:
                existing = archive.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                raise GraphRecoveryValidationError(
                    f"无法读取并发创建的交付报告归档：{archive.name}"
                ) from exc
            if existing != content:
                raise GraphRecoveryValidationError(
                    f"交付报告归档已存在且内容不同：{archive.name}"
                )
        except OSError as exc:
            raise GraphRecoveryValidationError(
                f"无法原子归档旧交付报告：{archive.name}"
            ) from exc
    return digest


def _render_invalidated_delivery_report(
    *,
    title: str,
    previous_status: str,
    reason: str,
    previous_sha256: str | None,
) -> str:
    return redact_text(
        "\n".join(
            [
                f"# {title}",
                "",
                "- 当前结论：原终态已撤销，当前 run 必须人工检查。",
                f"- 原业务状态：`{previous_status}`",
                "- 当前业务状态：`needs_human`",
                f"- 撤销原因：`{reason}`",
                (
                    "- 原报告归档 SHA-256："
                    f"`{previous_sha256}`"
                    if previous_sha256 is not None
                    else "- 原报告：不存在"
                ),
                "",
                "## 约束",
                "",
                "- 不得把原 success/failed 或 finish 结论继续作为可信终态证据。",
                "- SQLite、Graph State、execution 和 workspace 原现场均保留，"
                "需要人工核对后再决定是否新建 run。",
            ]
        ).rstrip()
        + "\n"
    )
