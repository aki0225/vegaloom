from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from .agent_codex_evidence import (
    ExecutedCodexAttempt,
    PreparedCodexAttempt,
    WorkerClaim,
    build_repair_prompt,
    decision_label,
    evaluate_plan_scope,
    evaluate_worker_claim,
    load_child_state,
    load_finish_summary,
    observation_from_child,
    operation_ref,
    plan_scope_failure,
    require_child_quiescent,
    require_repair_child,
    require_single_executable_work_item,
    require_terminal_worker_execution,
    require_waiting_child,
    write_child_summary,
    write_plan_scope_evidence,
)
from .agent_codex_preparation import (
    comparison_binding_from_metadata,
    next_attempt_number as _next_attempt_number,
    read_task_brief as _read_task_brief,
    validate_prepared_workspace,
)
from .agent_contract import AgentObservation
from .agent_run import AgentRun
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    validate_dispatch_artifacts,
)
from .agent_worker import SupervisorAgentWorker
from .execution_control import (
    ExecutionRecord,
    RunnerExecutionContext,
)
from .finish_runtime import FinishRuntime
from .loop_runtime import LoopAutomationRuntime
from .models import BriefInput, LoopAutomationState
from .project_config import load_project_config
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .runner import CodexExecRunner, Runner, RunnerResult


class SupervisorAgentCodexAdapter:
    """把一个真实 Codex Worker 接到现有 assist loop 与 Supervisor。"""

    def __init__(
        self,
        workspace: Path,
        *,
        worker_runner: Runner | None = None,
        loop_runtime: LoopAutomationRuntime | None = None,
        finish_runtime: FinishRuntime | None = None,
        progress_reporter: Callable[[str, int], None] | None = None,
        event_reporter: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.worker_runner = worker_runner
        self.loop_runtime = loop_runtime or LoopAutomationRuntime(
            self.workspace,
            progress_reporter=progress_reporter,
        )
        self.finish_runtime = finish_runtime or FinishRuntime(self.workspace)
        self.progress_reporter = progress_reporter
        self.event_reporter = event_reporter
        self.runtime = SupervisorAgentRuntime(self.workspace)
        self.worker = SupervisorAgentWorker(self.workspace)

    def run(self, run: str, *, timeout_seconds: int = 900) -> AgentRun:
        prepared, child_dir, prompt, operation_id, bound = self._prepare_and_bind(
            run,
            timeout_seconds,
        )
        executed = self._execute_worker(
            prepared,
            child_dir,
            prompt,
            timeout_seconds,
            operation_id,
            bound,
        )
        return self._reconcile_attempt(executed)

    def _prepare_and_bind(
        self,
        run: str,
        timeout_seconds: int,
    ) -> tuple[PreparedCodexAttempt, Path, str, str, AgentRun]:
        run_dir = resolve_run_dir(self.workspace, run)
        # 只串行化创建 child 与发布 Writer binding。真实 Worker 启动后立即释放
        # mutation lock，确保另一个 CLI 仍可执行 stop 或 recover。
        with RunMutationLock.acquire(run_dir, "agent.dispatch"):
            prepared = self._prepare_attempt(run, timeout_seconds)
            child_dir, prompt = self._prepare_child(prepared)
            operation_id = uuid4().hex
            bound = self.worker._bind_locked(
                prepared.run_dir.name,
                child_run=child_dir.name,
                operation_id=operation_id,
            )
        return prepared, child_dir, prompt, operation_id, bound

    def _prepare_attempt(
        self,
        run: str,
        timeout_seconds: int,
    ) -> PreparedCodexAttempt:
        if not 60 <= timeout_seconds <= 3600:
            raise ValueError("Worker timeout 必须在 60..3600 秒之间")
        run_dir, state, plan, metadata = load_agent_bundle(self.workspace, run)
        validate_dispatch_artifacts(run_dir, state, plan)
        work_item = require_single_executable_work_item(plan, state)
        attempt_number = _next_attempt_number(run_dir, state)
        before = capture_bound_workspace(run_dir)
        validate_prepared_workspace(
            before,
            expected_fingerprint=state.workspace_fingerprint,
            attempt_number=attempt_number,
        )
        repo = bound_repo(run_dir)
        comparison_base_sha, comparison_paths = comparison_binding_from_metadata(
            metadata
        )
        initial_plan_scope = evaluate_plan_scope(
            repo,
            plan,
            expected_head_sha=before.head_sha,
            iteration=attempt_number,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
        if initial_plan_scope.status == "failed":
            raise ValueError(plan_scope_failure(initial_plan_scope))
        task_brief = _read_task_brief(run_dir)
        config = load_project_config(repo)
        runner = self.worker_runner or CodexExecRunner(
            options=config.runner.codex_exec.worker,
            output_schema=WorkerClaim.model_json_schema(),
            single_writer=True,
        )
        return PreparedCodexAttempt(
            run_dir=run_dir,
            state=state,
            plan=plan,
            attempt_number=attempt_number,
            before=before,
            repo=repo,
            task_brief=task_brief,
            runner=runner,
            verification_commands=tuple(work_item.verification),
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )

    def _prepare_child(
        self,
        prepared: PreparedCodexAttempt,
    ) -> tuple[Path, str]:
        if prepared.attempt_number == 1:
            child_dir = self.loop_runtime.start(
                BriefInput(
                    mode="bug",
                    text=prepared.task_brief,
                    source=f"agent-task-brief:{prepared.state.run_id}",
                    repo_path=str(prepared.repo),
                ),
                "assist",
                worker_name="codex-exec",
                reviewer_name="codex-exec",
                max_iterations=2,
                verify=True,
                comparison_base_sha=prepared.comparison_base_sha,
                comparison_paths=prepared.comparison_paths,
            )
            require_waiting_child(child_dir, prepared.repo)
            prompt = (child_dir / "worker-prompt.md").read_text(encoding="utf-8")
            self._event(f"child baseline 已冻结：{child_dir.name}")
        else:
            child_dir = require_repair_child(
                self.workspace,
                prepared.run_dir,
                prepared.state,
                prepared.repo,
            )
            prompt = build_repair_prompt(child_dir, prepared.task_brief)
            self._event(f"repair child 已恢复：{child_dir.name}")
        return child_dir, prompt

    def _execute_worker(
        self,
        prepared: PreparedCodexAttempt,
        child_dir: Path,
        prompt: str,
        timeout_seconds: int,
        operation_id: str,
        bound: AgentRun,
    ) -> ExecutedCodexAttempt:
        child_run = child_dir.name

        execution_context = RunnerExecutionContext(
            execution_root=child_dir,
            execution_dir=child_dir / "executions" / "worker" / operation_id,
            run_id=child_run,
            step="worker",
            execution_id=operation_id,
            progress_reporter=self.progress_reporter,
        )
        self._event(f"Worker 已启动：{child_run}")
        result = prepared.runner.run(
            prompt,
            prepared.repo,
            sandbox="workspace-write",
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        if result.termination_unconfirmed:
            raise ValueError(
                "Worker owned process tree 终止未确认；已保留 Writer binding，"
                "必须人工核对后执行 recover。"
            )
        worker_record = require_terminal_worker_execution(
            child_dir,
            operation_id,
        )
        self._event(f"Worker 已退出：{result.status}")
        return ExecutedCodexAttempt(
            prepared=prepared,
            bound=bound,
            child_dir=child_dir,
            operation_id=operation_id,
            worker_record=worker_record,
            result=result,
        )

    def _reconcile_attempt(self, executed: ExecutedCodexAttempt) -> AgentRun:
        prepared = executed.prepared
        run_dir = prepared.run_dir
        state = prepared.state
        plan = prepared.plan
        repo = prepared.repo
        before = prepared.before
        attempt_number = prepared.attempt_number
        child_dir = executed.child_dir
        child_run = child_dir.name
        operation_id = executed.operation_id
        worker_record = executed.worker_record
        result = executed.result
        plan_scope = evaluate_plan_scope(
            repo,
            plan,
            expected_head_sha=before.head_sha,
            iteration=attempt_number,
            comparison_base_sha=prepared.comparison_base_sha,
            comparison_paths=prepared.comparison_paths,
        )
        plan_scope_ref = write_plan_scope_evidence(
            run_dir,
            operation_id,
            plan_scope,
            stage="post-worker",
        )
        if plan_scope.status == "failed":
            return self._observe_failure(
                executed.bound,
                child_dir,
                operation_id,
                worker_record,
                result,
                reason=plan_scope_failure(plan_scope),
                external_side_effects=(
                    "none" if result.status == "success" else "unknown"
                ),
                plan_contradicted=True,
                extra_evidence_refs=[plan_scope_ref],
            )
        after_worker = capture_bound_workspace(run_dir)
        if (
            attempt_number > 1
            and result.status == "success"
            and after_worker.fingerprint == before.fingerprint
        ):
            return self._observe_failure(
                executed.bound,
                child_dir,
                operation_id,
                worker_record,
                result,
                reason=(
                    "repair Worker 未产生新的 Workspace 变化；"
                    "不能把上一 attempt 的 Diff 重新记为本次修复证据"
                ),
                external_side_effects="none",
                extra_evidence_refs=[plan_scope_ref],
            )

        if result.status != "success":
            return self._observe_failure(
                executed.bound,
                child_dir,
                operation_id,
                worker_record,
                result,
                reason=result.error or f"Worker 终态为 {result.status}",
                external_side_effects="unknown",
                extra_evidence_refs=[plan_scope_ref],
            )

        claim, failure_reason = evaluate_worker_claim(result.output)
        if failure_reason:
            return self._observe_failure(
                executed.bound, child_dir, operation_id, worker_record, result,
                reason=failure_reason,
                external_side_effects="unknown",
                claim=claim,
                extra_evidence_refs=[plan_scope_ref],
            )

        assert claim is not None
        try:
            self.loop_runtime.continue_assist(
                child_run,
                repo,
                worker_name="codex-exec",
                reviewer_name="codex-exec",
                verify=True,
                verification_commands=list(prepared.verification_commands),
            )
            self.finish_runtime.run(child_run)
            require_child_quiescent(child_dir)
            child_state = load_child_state(child_dir, repo)
            finish_summary = load_finish_summary(child_dir, child_run)
        except (OSError, ValueError, ValidationError) as exc:
            require_child_quiescent(child_dir)
            return self._observe_failure(
                executed.bound,
                child_dir,
                operation_id,
                worker_record,
                result,
                reason=f"现有 Vega Core 未形成可采用终态：{exc}",
                external_side_effects="unknown",
                claim=claim,
                extra_evidence_refs=[plan_scope_ref],
            )

        final_plan_scope = evaluate_plan_scope(
            repo,
            plan,
            expected_head_sha=before.head_sha,
            iteration=attempt_number,
            comparison_base_sha=prepared.comparison_base_sha,
            comparison_paths=prepared.comparison_paths,
        )
        final_plan_scope_ref = write_plan_scope_evidence(
            run_dir,
            operation_id,
            final_plan_scope,
            stage="post-core",
        )
        if final_plan_scope.status == "failed":
            return self._observe_failure(
                executed.bound,
                child_dir,
                operation_id,
                worker_record,
                result,
                reason=(
                    "现有 Core 执行后，"
                    f"{plan_scope_failure(final_plan_scope)}"
                ),
                external_side_effects="none",
                claim=claim,
                plan_contradicted=True,
                extra_evidence_refs=[
                    plan_scope_ref,
                    final_plan_scope_ref,
                ],
                child_state=child_state,
                finish_summary=finish_summary,
            )

        summary_ref = write_child_summary(
            run_dir,
            state,
            child_dir,
            operation_id,
            worker_record,
            result,
            claim=claim,
            child_state=child_state,
            finish_summary=finish_summary,
        )
        observation = observation_from_child(
            run_dir,
            state,
            plan,
            child_dir,
            operation_id,
            claim,
            child_state,
            finish_summary,
            evidence_refs=[
                operation_ref(operation_id),
                plan_scope_ref,
                final_plan_scope_ref,
                summary_ref,
            ],
        )
        self._event("Workspace 与现有 Core Artifact 已完成对账")
        routed = self.runtime.observe_machine(run_dir.name, observation)
        self._event(f"Supervisor 选择：{decision_label(routed, observation)}")
        return routed

    def _observe_failure(
        self,
        bound: AgentRun,
        child_dir: Path,
        operation_id: str,
        worker_record: ExecutionRecord,
        result: RunnerResult,
        *,
        reason: str,
        external_side_effects: Literal["none", "known", "unknown"],
        claim: WorkerClaim | None = None,
        plan_contradicted: bool = False,
        extra_evidence_refs: list[str] | None = None,
        child_state: LoopAutomationState | None = None,
        finish_summary: dict[str, object] | None = None,
    ) -> AgentRun:
        require_child_quiescent(child_dir)
        summary_ref = write_child_summary(
            bound.run_dir,
            bound.state,
            child_dir,
            operation_id,
            worker_record,
            result,
            claim=claim,
            failure_reason=reason,
            child_state=child_state,
            finish_summary=finish_summary,
        )
        snapshot = capture_bound_workspace(bound.run_dir)
        observation = AgentObservation(
            observation_id=f"observation-{uuid4().hex[:12]}",
            work_item_id=bound.state.current_work_item,
            child_run=child_dir.name,
            operation_id=operation_id,
            worker_claim=claim.summary if claim is not None else None,
            machine_summary=reason,
            workspace_fingerprint=snapshot.fingerprint,
            changed_files=list(snapshot.changed_files),
            evidence_refs=[
                operation_ref(operation_id),
                *(extra_evidence_refs or []),
                summary_ref,
            ],
            authority="machine_reconcile",
            operation_started=True,
            workspace_explained=True,
            external_side_effects=external_side_effects,
            plan_contradicted=plan_contradicted,
        )
        routed = self.runtime.observe_machine(bound.run_dir.name, observation)
        self._event(f"Supervisor 选择：{decision_label(routed, observation)}")
        return routed

    def _event(self, message: str) -> None:
        if self.event_reporter is not None:
            self.event_reporter(message)
