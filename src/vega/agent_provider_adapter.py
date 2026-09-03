from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .agent_worker_evidence import (
    ExecutedWorkerAttempt,
    PreparedWorkerAttempt,
    WorkerClaim,
    build_repair_prompt,
    decision_label,
    hash_evidence_refs,
    require_child_quiescent,
    require_executable_work_item,
    require_repair_child,
    require_terminal_worker_execution,
    require_waiting_child,
    write_child_summary,
)
from .agent_candidate_pipeline import CandidatePipeline
from .agent_change_control import require_change_review_budget
from .agent_change_fix_packet import load_current_fix_packet
from .agent_change_run import load_change_run_context
from .agent_change_core import (
    initialize_change_core_child,
    reserve_change_core_child,
)
from .agent_git_candidate import CandidateCommit
from .agent_operation import operation_ref
from .agent_plan_scope import (
    capture_plan_scope_baseline,
)
from .agent_provider import AgentProvider
from .agent_provider_factory import (
    ensure_reviewer_runner,
    runner_name,
    worker_runner,
)
from .agent_provider_preparation import (
    next_attempt_context as _next_attempt_context,
    prepare_dispatch_binding,
    read_task_brief as _read_task_brief,
    review_final_candidate,
    validate_prepared_workspace,
)
from .agent_contract import AgentObservation, AgentState
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
from .project_config import ProjectConfig, load_project_config
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .runner import Runner, RunnerResult


class SupervisorAgentProviderAdapter:
    """把真实 Coding Agent Provider 接到同一条 Candidate Pipeline。"""

    def __init__(
        self,
        workspace: Path,
        *,
        worker_runner: Runner | None = None,
        loop_runtime: LoopAutomationRuntime | None = None,
        finish_runtime: FinishRuntime | None = None,
        progress_reporter: Callable[[str, int], None] | None = None,
        event_reporter: Callable[[str], None] | None = None,
        provider: AgentProvider = "codex",
        persistent_sessions: bool = True,
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
        self.provider = provider
        self.persistent_sessions = persistent_sessions
        self.runtime = SupervisorAgentRuntime(self.workspace)
        self.worker = SupervisorAgentWorker(self.workspace)

    def run(self, run: str, *, timeout_seconds: int = 900) -> AgentRun:
        result = self._run_once(run, timeout_seconds=timeout_seconds)
        steps = 1
        while (
            getattr(getattr(result, "state", None), "run_kind", None) == "change"
            and result.state.phase == "ready"
            and {"next", "repair"}.intersection(result.state.allowed_actions)
        ):
            max_steps = self._change_run_step_limit(result.run_dir.name)
            if steps >= max_steps:
                raise ValueError("ChangeRun 自动推进超过合同允许的总 attempt 上限")
            steps += 1
            result = self._run_once(
                result.run_dir.name,
                timeout_seconds=timeout_seconds,
            )
        return result

    def _change_run_step_limit(self, run: str) -> int:
        run_dir, state, plan, metadata = load_agent_bundle(self.workspace, run)
        context = load_change_run_context(run_dir, state, plan, metadata)
        if context is None:
            return 1
        return len(context.execution_plan.work_items) * (
            context.contract.authority_envelope.max_repair_rounds + 1
        )

    def _run_once(
        self,
        run: str,
        *,
        timeout_seconds: int,
    ) -> AgentRun:
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
    ) -> tuple[PreparedWorkerAttempt, Path, str, str, AgentRun]:
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
    ) -> PreparedWorkerAttempt:
        if not 60 <= timeout_seconds <= 3600:
            raise ValueError("Worker timeout 必须在 60..3600 秒之间")
        run_dir, state, plan, metadata = load_agent_bundle(self.workspace, run)
        validate_dispatch_artifacts(run_dir, state, plan)
        work_item = require_executable_work_item(plan, state)
        change_context = load_change_run_context(
            run_dir,
            state,
            plan,
            metadata,
        )
        if change_context is not None:
            require_change_review_budget(
                run_dir,
                state,
                change_context.contract,
            )
        attempt_number, requires_clean_workspace = _next_attempt_context(
            run_dir,
            state,
            max_repair_rounds=(
                change_context.contract.authority_envelope.max_repair_rounds
                if change_context is not None
                else 1
            ),
        )
        before = capture_bound_workspace(run_dir)
        validate_prepared_workspace(
            before,
            expected_fingerprint=state.workspace_fingerprint,
            requires_clean_workspace=requires_clean_workspace,
        )
        repo = bound_repo(run_dir)
        if change_context is None:
            comparison_base_sha, comparison_paths = prepare_dispatch_binding(
                metadata, repo, work_item
            )
        else:
            if before.head_sha != state.accepted_checkpoint_sha:
                raise ValueError("ChangeRun 当前 HEAD 不是 Accepted Checkpoint")
            comparison_base_sha = state.accepted_checkpoint_sha
            comparison_paths = ()
        plan_scope_baseline = capture_plan_scope_baseline(
            repo,
            plan,
            work_item,
            expected_head_sha=before.head_sha,
            iteration=attempt_number,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
        task_brief = _read_task_brief(run_dir)
        config = load_project_config(repo)
        self._ensure_reviewer_for_attempt(run_dir, state, config)
        runner = self.worker_runner or worker_runner(
            run_dir,
            state,
            config,
            provider=self.provider,
            persistent_session=self.persistent_sessions,
        )
        worker_name = runner_name(
            self.provider,
            persistent_session=self.persistent_sessions,
        )
        return PreparedWorkerAttempt(
            run_dir=run_dir,
            state=state,
            plan=plan,
            attempt_number=attempt_number,
            before=before,
            repo=repo,
            task_brief=task_brief,
            runner=runner,
            verification_commands=tuple(work_item.verification),
            external_side_effects=work_item.external_side_effects,
            plan_scope_baseline=plan_scope_baseline,
            worker_name=worker_name,
            reviewer_name=worker_name,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
            change_context=change_context,
            timeout_seconds=timeout_seconds,
        )

    def _ensure_reviewer_for_attempt(
        self,
        run_dir: Path,
        state: AgentState,
        config: ProjectConfig,
    ) -> None:
        ensure_reviewer_runner(
            self.loop_runtime,
            config,
            agent_run_dir=run_dir,
            state=state,
            provider=self.provider,
            persistent_session=self.persistent_sessions,
        )

    def _prepare_child(
        self,
        prepared: PreparedWorkerAttempt,
    ) -> tuple[Path, str]:
        if prepared.change_context is not None:
            if prepared.attempt_number == 1:
                prompt = prepared.task_brief
            else:
                previous_child = require_repair_child(
                    self.workspace,
                    prepared.run_dir,
                    prepared.state,
                    prepared.repo,
                )
                fix_packet = load_current_fix_packet(
                    self.workspace,
                    prepared.run_dir,
                    prepared.state,
                )
                prompt = build_repair_prompt(
                    previous_child,
                    prepared.task_brief,
                    fix_packet=fix_packet,
                )
            child_dir = reserve_change_core_child(self.loop_runtime)
            self._event(f"ChangeRun child 已预留：{child_dir.name}")
            return child_dir, prompt
        if prepared.attempt_number == 1:
            child_dir = self.loop_runtime.start(
                BriefInput(
                    mode="bug",
                    text=prepared.task_brief,
                    source=f"agent-task-brief:{prepared.state.run_id}",
                    repo_path=str(prepared.repo),
                ),
                "assist",
                worker_name=prepared.worker_name,
                reviewer_name=prepared.reviewer_name,
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
        prepared: PreparedWorkerAttempt,
        child_dir: Path,
        prompt: str,
        timeout_seconds: int,
        operation_id: str,
        bound: AgentRun,
    ) -> ExecutedWorkerAttempt:
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
        return ExecutedWorkerAttempt(
            prepared=prepared,
            bound=bound,
            child_dir=child_dir,
            operation_id=operation_id,
            worker_record=worker_record,
            result=result,
        )

    def _reconcile_attempt(self, executed: ExecutedWorkerAttempt) -> AgentRun:
        result = CandidatePipeline(
            runtime=self.runtime,
            loop_runtime=self.loop_runtime,
            finish_runtime=self.finish_runtime,
            initialize_core=self._initialize_change_core,
            observe_failure=self._observe_failure,
            event_reporter=self._event,
            final_observation_reviewer=lambda observation: review_final_candidate(
                self.workspace,
                executed.prepared.run_dir,
                observation,
                load_project_config(executed.prepared.repo),
                persistent_session=self.persistent_sessions,
                attempt_number=executed.prepared.attempt_number,
                timeout_seconds=executed.prepared.timeout_seconds,
                progress_reporter=self.progress_reporter,
                event_reporter=self._event,
                provider=self.provider,
                reviewer_runner=getattr(
                    self.loop_runtime,
                    "reviewer_runner",
                    None,
                ),
            ),
        ).reconcile(executed)
        return result

    def _initialize_change_core(
        self,
        prepared: PreparedWorkerAttempt,
        child_dir: Path,
        candidate: CandidateCommit,
    ) -> None:
        initialized = initialize_change_core_child(
            self.loop_runtime,
            child_dir.name,
            BriefInput(
                mode="bug",
                text=prepared.task_brief,
                source=f"agent-task-brief:{prepared.state.run_id}",
                repo_path=str(prepared.repo),
            ),
            comparison_base_sha=candidate.parent_sha,
            comparison_paths=tuple(candidate.changed_files),
        )
        if initialized.resolve() != child_dir.resolve():
            raise ValueError("预留 child 初始化到了不同目录")
        require_waiting_child(child_dir, prepared.repo)
        self._event(f"Candidate Core baseline 已冻结：{child_dir.name}")

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
        evidence_refs = [operation_ref(operation_id), *(extra_evidence_refs or []), summary_ref]
        observation = AgentObservation(
            observation_id=f"observation-{uuid4().hex[:12]}",
            work_item_id=bound.state.current_work_item,
            child_run=child_dir.name,
            operation_id=operation_id,
            worker_claim=claim.summary if claim is not None else None,
            machine_summary=reason,
            workspace_fingerprint=snapshot.fingerprint,
            changed_files=list(snapshot.changed_files),
            evidence_refs=evidence_refs,
            evidence_sha256=hash_evidence_refs(bound.run_dir, evidence_refs),
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
