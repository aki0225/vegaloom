from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .agent_worker_evidence import (
    decision_label,
    hash_evidence_refs,
    load_child_state,
    load_finish_summary,
    observation_from_child,
    require_child_quiescent,
    require_single_executable_work_item,
)
from .agent_provider import AgentProvider
from . import agent_provider_preparation as provider_preparation
from .agent_provider_factory import ensure_reviewer_runner
from .agent_plan_scope import (
    capture_plan_scope_baseline,
    evaluate_plan_scope,
    plan_scope_failure,
    write_plan_scope_evidence,
)
from .agent_contract import AgentObservation
from .agent_change_control import require_change_verification_retry_budget
from .agent_operation import operation_ref, reserve_operation_identity
from .agent_persistence import (
    append_agent_trace,
    load_agent_checkpoint,
    save_agent_state,
)
from .agent_repository_guard import acquire_writer_claim, release_writer_claim
from .agent_run import AgentRun
from .agent_run_status import latest_worker_dispatch_binding
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    validate_dispatch_artifacts,
    write_status_card,
)
from .agent_verification_retry_archive import archive_retry_source_finish
from .agent_verification_retry_evidence import (
    PreparedVerificationRetry,
    capture_verification_retry_baseline,
    load_optional_child_state,
    load_optional_finish,
    load_source_observation,
    matching_source_plans,
    same_tracked_workspace,
    select_source_plan,
    validate_retry_source,
    write_retry_child_summary,
)
from .finish_runtime import FinishRuntime
from .loop_runtime import LoopAutomationRuntime
from .models import LoopAutomationState
from .project_config import load_project_config
from .redaction import redact_text
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .scope_gate import ScopeGateResult
from .verification_command_preflight import require_verification_commands_preflight

class SupervisorAgentVerificationRetry:
    """在同一 Diff 上重跑 Core 门禁，不再次启动 Coding Worker。"""
    def __init__(
        self,
        workspace: Path,
        *,
        loop_runtime: LoopAutomationRuntime | None = None,
        finish_runtime: FinishRuntime | None = None,
        progress_reporter=None,
        event_reporter=None,
        provider: AgentProvider = "codex",
        persistent_sessions: bool = True,
    ) -> None:
        self.workspace = workspace.resolve()
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
    def run(self, run: str) -> AgentRun:
        prepared, operation_id, bound = self._prepare_and_bind(run)
        return self._run_core(prepared, operation_id, bound)
    def _prepare_and_bind(
        self,
        run: str,
    ) -> tuple[PreparedVerificationRetry, str, AgentRun]:
        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "agent.retry-verification"):
            prepared = self._prepare(run)
            operation_id = uuid4().hex
            bound = self._bind(prepared, operation_id)
        return prepared, operation_id, bound
    def _prepare(self, run: str) -> PreparedVerificationRetry:
        run_dir, state, plan, metadata = load_agent_bundle(self.workspace, run)
        if state.phase != "ready" or state.active_child_run or state.active_operation_id:
            raise ValueError("当前 Agent 状态不允许验证专用恢复")
        validate_dispatch_artifacts(run_dir, state, plan)
        require_change_verification_retry_budget(run_dir, state, plan, metadata)
        work_item = require_single_executable_work_item(plan, state)
        if work_item.external_side_effects != "none":
            raise ValueError("验证专用恢复只接受 external_side_effects=none 的 Work Item")
        repo = bound_repo(run_dir)
        require_verification_commands_preflight(repo, work_item.verification)
        before = capture_bound_workspace(run_dir)
        if before.fingerprint != state.workspace_fingerprint:
            raise ValueError("验证恢复前 Workspace 已漂移，必须先重新对账")
        checkpoint = load_agent_checkpoint(
            run_dir / "checkpoints" / f"{state.latest_checkpoint_id}.json"
        )
        if len(checkpoint.failed_attempts) != 1:
            raise ValueError("当前 Checkpoint 没有唯一失败 child，不能执行验证专用恢复")
        child_dir = resolve_run_dir(self.workspace, checkpoint.failed_attempts[0])
        child_state = load_child_state(child_dir, repo)
        require_child_quiescent(child_dir)
        if (
            child_state.status != "needs_human"
            or not child_state.iterations
            or child_state.current_iteration >= child_state.max_iterations
        ):
            raise ValueError("失败 child 没有可追加的 Core iteration")
        worker_binding = latest_worker_dispatch_binding(run_dir, state)
        if worker_binding is None or worker_binding[0] != child_dir.name:
            raise ValueError("无法把失败 child 绑定到原始真实 Worker")
        source_operation_id = worker_binding[1]
        source_observation = load_source_observation(
            run_dir,
            child_dir.name,
            source_operation_id,
        )
        source_plans = matching_source_plans(run_dir, plan)
        source_plan = select_source_plan(
            run_dir,
            state,
            source_plans,
            source_observation,
        )
        source_summary_ref, source_claim, source_finish_sha256 = validate_retry_source(
            run_dir,
            state,
            source_plan,
            source_observation,
            child_dir,
            before,
        )
        core_workspace_baseline = capture_verification_retry_baseline(
            self.workspace,
            repo,
            before,
        )
        comparison_base_sha, comparison_paths = provider_preparation.comparison_binding_from_metadata(
            metadata
        )
        next_iteration = child_state.current_iteration + 1
        plan_scope_baseline = capture_plan_scope_baseline(
            repo,
            plan,
            work_item,
            expected_head_sha=before.head_sha,
            iteration=next_iteration,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
        pre_core_scope = evaluate_plan_scope(
            repo,
            plan_scope_baseline,
            expected_head_sha=before.head_sha,
            iteration=next_iteration,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
        if pre_core_scope.status == "failed":
            raise ValueError(plan_scope_failure(pre_core_scope))
        ensure_reviewer_runner(
            self.loop_runtime,
            load_project_config(repo),
            agent_run_dir=run_dir,
            state=state,
            provider=self.provider,
            persistent_session=self.persistent_sessions,
        )
        return PreparedVerificationRetry(
            run_dir=run_dir,
            state=state,
            plan=plan,
            work_item=work_item,
            repo=repo,
            before=before,
            child_dir=child_dir,
            child_state=child_state,
            source_plan=source_plan,
            source_observation=source_observation,
            source_claim=source_claim,
            source_summary_ref=source_summary_ref,
            source_operation_id=source_operation_id,
            source_finish_sha256=source_finish_sha256,
            core_workspace_baseline=core_workspace_baseline,
            plan_scope_baseline=plan_scope_baseline,
            pre_core_scope=pre_core_scope,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
    def _bind(
        self,
        prepared: PreparedVerificationRetry,
        operation_id: str,
    ) -> AgentRun:
        state = prepared.state
        acquire_writer_claim(
            prepared.repo,
            run_dir=prepared.run_dir,
            task_id=state.task_id,
            child_run=prepared.child_dir.name,
            operation_id=operation_id,
            operation_kind="verification_retry",
        )
        state_committed = False
        try:
            source_finish_ref = archive_retry_source_finish(
                prepared.run_dir,
                prepared.child_dir,
                operation_id,
                prepared.source_finish_sha256,
            )
            operation_relative = reserve_operation_identity(
                prepared.run_dir,
                state,
                child_run=prepared.child_dir.name,
                operation_id=operation_id,
                operation_kind="verification_retry",
                details={
                    "source_operation_id": prepared.source_operation_id,
                    "source_finish_ref": source_finish_ref,
                    "source_finish_sha256": prepared.source_finish_sha256,
                },
            )
            observing = update_state(
                state,
                phase="observing",
                state_version=state.state_version + 1,
                active_child_run=prepared.child_dir.name,
                active_operation_id=operation_id,
                operation_started=True,
                allowed_actions=["human"],
            )
            save_agent_state(prepared.run_dir / "agent-state.json", observing)
            state_committed = True
            append_agent_trace(
                prepared.run_dir / "trace.jsonl",
                event="verification_retry_committed",
                state=observing,
                observation_summary=(
                    "已绑定验证专用恢复；复用原 child 与 Diff，不启动新的 Coding Worker"
                ),
                artifact_refs=[
                    operation_relative,
                    prepared.source_summary_ref,
                    source_finish_ref,
                ],
            )
            write_status_card(
                prepared.run_dir,
                observing,
                prepared.plan,
                next_step="正在同一 child 上重跑验证、风险门禁与独立 Reviewer",
            )
            self._event(f"验证专用恢复已启动：{prepared.child_dir.name}")
            return AgentRun(
                run_dir=prepared.run_dir,
                state=observing,
                plan=prepared.plan,
            )
        except Exception:
            if not state_committed:
                release_writer_claim(
                    prepared.repo,
                    run_id=state.run_id,
                    operation_id=operation_id,
                )
            raise
    def _run_core(
        self,
        prepared: PreparedVerificationRetry,
        operation_id: str,
        bound: AgentRun,
    ) -> AgentRun:
        child_run = prepared.child_dir.name
        try:
            self.loop_runtime.continue_assist(
                child_run,
                prepared.repo,
                worker_name="codex-app-server" if self.persistent_sessions else "codex-exec",
                reviewer_name="codex-app-server" if self.persistent_sessions else "codex-exec",
                verify=True,
                verification_commands=list(prepared.work_item.verification),
                verification_retry_baseline=prepared.core_workspace_baseline,
            )
            self.finish_runtime.run(child_run)
            require_child_quiescent(prepared.child_dir)
            child_state = load_child_state(prepared.child_dir, prepared.repo)
            finish_summary = load_finish_summary(prepared.child_dir, child_run)
            after = capture_bound_workspace(prepared.run_dir)
            if not same_tracked_workspace(prepared.before, after):
                return self._observe_failure(
                    prepared,
                    operation_id,
                    bound,
                    reason="验证专用恢复期间 tracked Workspace 发生变化",
                    child_state=child_state,
                    finish_summary=finish_summary,
                    plan_contradicted=True,
                )
            post_core_scope = evaluate_plan_scope(
                prepared.repo,
                prepared.plan_scope_baseline,
                expected_head_sha=prepared.before.head_sha,
                iteration=child_state.current_iteration,
                comparison_base_sha=prepared.comparison_base_sha,
                comparison_paths=prepared.comparison_paths,
            )
        except Exception as exc:  # noqa: BLE001 - 控制边界必须把异常转为 fail-closed 现场
            require_child_quiescent(prepared.child_dir)
            return self._observe_failure(
                prepared,
                operation_id,
                bound,
                reason=(
                    "验证专用恢复未形成可采用的 Core 终态："
                    f"{redact_text(f'{type(exc).__name__}: {exc}')[:1000]}"
                ),
            )
        if post_core_scope.status == "failed":
            return self._observe_failure(
                prepared,
                operation_id,
                bound,
                reason=plan_scope_failure(post_core_scope),
                child_state=child_state,
                finish_summary=finish_summary,
                post_core_scope=post_core_scope,
                plan_contradicted=True,
            )
        pre_scope_ref = write_plan_scope_evidence(
            prepared.run_dir,
            operation_id,
            prepared.pre_core_scope,
            stage="post-worker",
        )
        post_scope_ref = write_plan_scope_evidence(
            prepared.run_dir,
            operation_id,
            post_core_scope,
            stage="post-core",
        )
        summary_ref = write_retry_child_summary(
            prepared,
            operation_id,
            child_state,
            finish_summary,
        )
        evidence_refs = [
            operation_ref(operation_id),
            pre_scope_ref,
            post_scope_ref,
            summary_ref,
        ]
        observation = observation_from_child(
            prepared.run_dir,
            bound.state,
            prepared.plan,
            prepared.child_dir,
            operation_id,
            prepared.source_claim,
            child_state,
            finish_summary,
            evidence_refs=evidence_refs,
            external_side_effects="none",
        )
        if observation.all_work_items_completed:
            observation = provider_preparation.review_final_candidate(
                self.workspace,
                prepared.run_dir,
                observation,
                load_project_config(prepared.repo),
                persistent_session=self.persistent_sessions,
                attempt_number=2,
                timeout_seconds=900,
                progress_reporter=self.progress_reporter,
                event_reporter=self._event,
                provider=self.provider,
                reviewer_runner=getattr(self.loop_runtime, "reviewer_runner", None),
            )
        self._event("验证恢复后的 Workspace 与 Core Artifact 已完成对账")
        routed = self.runtime.observe_machine(prepared.run_dir.name, observation)
        self._event(f"Supervisor 选择：{decision_label(routed, observation)}")
        if routed.state.phase == "finalizing":
            routed = self.runtime.finalize(routed.run_dir.name)
            self._event("Supervisor 已完成：ready_to_commit")
        return routed
    def _observe_failure(
        self,
        prepared: PreparedVerificationRetry,
        operation_id: str,
        bound: AgentRun,
        *,
        reason: str,
        child_state: LoopAutomationState | None = None,
        finish_summary: dict[str, object] | None = None,
        post_core_scope: ScopeGateResult | None = None,
        plan_contradicted: bool = False,
    ) -> AgentRun:
        child_state = child_state or load_optional_child_state(
            prepared.child_dir,
            prepared.repo,
        )
        finish_summary = finish_summary or load_optional_finish(
            prepared.child_dir,
            prepared.child_dir.name,
        )
        current_iteration = (
            child_state.current_iteration
            if child_state is not None
            else prepared.child_state.current_iteration + 1
        )
        post_core_scope = post_core_scope or evaluate_plan_scope(
            prepared.repo,
            prepared.plan_scope_baseline,
            expected_head_sha=prepared.before.head_sha,
            iteration=current_iteration,
            comparison_base_sha=prepared.comparison_base_sha,
            comparison_paths=prepared.comparison_paths,
        )
        pre_scope_ref = write_plan_scope_evidence(
            prepared.run_dir,
            operation_id,
            prepared.pre_core_scope,
            stage="post-worker",
        )
        post_scope_ref = write_plan_scope_evidence(
            prepared.run_dir,
            operation_id,
            post_core_scope,
            stage="post-core",
        )
        summary_ref = write_retry_child_summary(
            prepared,
            operation_id,
            child_state,
            finish_summary,
            failure_reason=reason,
        )
        evidence_refs = [
            operation_ref(operation_id),
            pre_scope_ref,
            post_scope_ref,
            summary_ref,
        ]
        snapshot = capture_bound_workspace(prepared.run_dir)
        tracked_unchanged = same_tracked_workspace(prepared.before, snapshot)
        if not tracked_unchanged:
            plan_contradicted = True
            reason = f"{reason}；tracked Workspace 已发生变化"
        observation = AgentObservation(
            observation_id=f"observation-{uuid4().hex[:12]}",
            work_item_id=bound.state.current_work_item,
            child_run=prepared.child_dir.name,
            operation_id=operation_id,
            worker_claim=prepared.source_claim.summary,
            machine_summary=reason,
            workspace_fingerprint=snapshot.fingerprint,
            changed_files=list(snapshot.changed_files),
            evidence_refs=evidence_refs,
            evidence_sha256=hash_evidence_refs(prepared.run_dir, evidence_refs),
            authority="machine_reconcile",
            operation_started=True,
            workspace_explained=(
                not snapshot.unsafe_index_paths and snapshot.git_control_complete
            ),
            external_side_effects="none",
            plan_contradicted=plan_contradicted,
            verification="blocked",
            risk="not_run",
            review="not_run",
        )
        routed = self.runtime.observe_machine(prepared.run_dir.name, observation)
        self._event(f"验证专用恢复已停止：{reason}")
        return routed
    def _event(self, message: str) -> None:
        if self.event_reporter is not None:
            self.event_reporter(message)
