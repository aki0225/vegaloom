from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .agent_codex_evidence import (
    ExecutedCodexAttempt,
    PreparedCodexAttempt,
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
from .agent_change_run import load_change_run_context
from .agent_change_core import (
    initialize_change_core_child,
    reserve_change_core_child,
)
from .agent_git_candidate import CandidateCommit
from .agent_codex_reconcile import reconcile_codex_attempt
from .agent_operation import operation_ref
from .agent_codex_scope import (
    capture_plan_scope_baseline,
)
from .agent_codex_preparation import (
    ensure_isolated_reviewer,
    next_attempt_context as _next_attempt_context,
    prepare_dispatch_binding,
    read_task_brief as _read_task_brief,
    validate_prepared_workspace,
)
from .agent_contract import AgentObservation
from .agent_graph import require_agent_runtime_dependencies
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
        require_agent_runtime_dependencies()
        result = self._run_once(run, timeout_seconds=timeout_seconds)
        advances = 0
        while (
            getattr(getattr(result, "state", None), "run_kind", None) == "change"
            and result.state.phase == "ready"
            and "next" in result.state.allowed_actions
        ):
            advances += 1
            if advances > 8:
                raise ValueError("ChangeRun 自动推进超过 Work Item 上限")
            result = self._run_once(
                result.run_dir.name,
                timeout_seconds=timeout_seconds,
            )
        return result

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
        work_item = require_executable_work_item(plan, state)
        change_context = load_change_run_context(
            run_dir,
            state,
            plan,
            metadata,
        )
        attempt_number, requires_clean_workspace = _next_attempt_context(run_dir, state)
        if change_context is not None:
            requires_clean_workspace = attempt_number == 1
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
        self._ensure_isolated_reviewer(config)
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
            external_side_effects=work_item.external_side_effects,
            plan_scope_baseline=plan_scope_baseline,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
            change_context=change_context,
        )

    def _ensure_isolated_reviewer(self, config: ProjectConfig) -> None:
        ensure_isolated_reviewer(self.loop_runtime, config)

    def _prepare_child(
        self,
        prepared: PreparedCodexAttempt,
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
                prompt = build_repair_prompt(previous_child, prepared.task_brief)
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
        return reconcile_codex_attempt(self, executed)

    def _initialize_change_core(
        self,
        prepared: PreparedCodexAttempt,
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
