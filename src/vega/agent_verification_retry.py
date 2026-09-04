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
)
from .agent_provider import AgentProvider
from . import agent_provider_preparation as provider_preparation
from .agent_provider_factory import runner_name
from .agent_plan_scope import (
    evaluate_plan_scope,
    plan_scope_failure,
    write_plan_scope_evidence,
)
from .agent_contract import AgentObservation
from .agent_operation import operation_ref, reserve_operation_identity
from .agent_persistence import (
    append_agent_trace,
    save_agent_state,
)
from .agent_repository_guard import acquire_writer_claim, release_writer_claim
from .agent_run import AgentRun
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    capture_bound_workspace,
    save_agent_plan,
    write_status_card,
)
from .agent_verification_retry_archive import archive_retry_source_finish
from .agent_verification_retry_evidence import (
    PreparedVerificationRetry,
    load_optional_child_state,
    load_optional_finish,
    same_tracked_workspace,
    write_retry_child_summary,
)
from .agent_verification_retry_preparation import (
    VerificationRetryReason,
    prepare_verification_retry,
)
from .finish_runtime import FinishRuntime
from .loop_runtime import LoopAutomationRuntime
from .models import LoopAutomationState
from .project_config import load_project_config
from .redaction import redact_text
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .scope_gate import ScopeGateResult

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
    def run(
        self,
        run: str,
        *,
        retry_reason: VerificationRetryReason = "verification_failure",
    ) -> AgentRun:
        prepared, operation_id, bound = self._prepare_and_bind(
            run,
            retry_reason=retry_reason,
        )
        return self._run_core(prepared, operation_id, bound)
    def run_reviewer_timeout_if_eligible(self, run: str) -> AgentRun | None:
        """资格不足时保留原 needs_human，资格通过后才发布恢复 operation。"""

        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "agent.retry-verification"):
            try:
                prepared = prepare_verification_retry(
                    self.workspace,
                    run,
                    loop_runtime=self.loop_runtime,
                    provider=self.provider,
                    persistent_sessions=self.persistent_sessions,
                    retry_reason="reviewer_timeout",
                )
            except ValueError:
                return None
            operation_id = uuid4().hex
            bound = self._bind(prepared, operation_id)
        self._event("Core Reviewer 明确超时；正在自动恢复一次")
        return self._run_core(prepared, operation_id, bound)
    def _prepare_and_bind(
        self,
        run: str,
        *,
        retry_reason: VerificationRetryReason = "verification_failure",
    ) -> tuple[PreparedVerificationRetry, str, AgentRun]:
        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "agent.retry-verification"):
            prepared = prepare_verification_retry(
                self.workspace,
                run,
                loop_runtime=self.loop_runtime,
                provider=self.provider,
                persistent_sessions=self.persistent_sessions,
                retry_reason=retry_reason,
            )
            operation_id = uuid4().hex
            bound = self._bind(prepared, operation_id)
        return prepared, operation_id, bound
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
            details = {
                "source_operation_id": prepared.source_operation_id,
                "source_finish_ref": source_finish_ref,
                "source_finish_sha256": prepared.source_finish_sha256,
                "retry_reason": prepared.retry_reason,
                "reviewer_retry_attempt": prepared.reviewer_retry_attempt,
            }
            if prepared.candidate_sha is not None:
                details["candidate_sha"] = prepared.candidate_sha
            operation_relative = reserve_operation_identity(
                prepared.run_dir,
                state,
                child_run=prepared.child_dir.name,
                operation_id=operation_id,
                operation_kind="verification_retry",
                details=details,
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
            if prepared.retry_reason == "reviewer_timeout":
                save_agent_plan(prepared.run_dir, prepared.plan)
            save_agent_state(prepared.run_dir / "agent-state.json", observing)
            state_committed = True
            append_agent_trace(
                prepared.run_dir / "trace.jsonl",
                event="verification_retry_committed",
                state=observing,
                observation_summary=(
                    "已绑定 Reviewer timeout 自动恢复；"
                    "复用原 child 与 Candidate，不启动新的 Coding Worker"
                    if prepared.retry_reason == "reviewer_timeout"
                    else (
                        "已绑定验证专用恢复；"
                        "复用原 child 与 Diff，不启动新的 Coding Worker"
                    )
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
                next_step=(
                    "正在同一 Candidate 上重跑验证、风险门禁与新的独立 Reviewer"
                    if prepared.retry_reason == "reviewer_timeout"
                    else "正在同一 child 上重跑验证、风险门禁与独立 Reviewer"
                ),
            )
            self._event(
                (
                    "Reviewer timeout 自动恢复已启动："
                    if prepared.retry_reason == "reviewer_timeout"
                    else "验证专用恢复已启动："
                )
                + prepared.child_dir.name
            )
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
            provider_runner = runner_name(
                self.provider,
                persistent_session=self.persistent_sessions,
            )
            self.loop_runtime.continue_assist(
                child_run,
                prepared.repo,
                worker_name=provider_runner,
                reviewer_name=provider_runner,
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
            reviewer_retry_attempt=prepared.reviewer_retry_attempt,
        )
        if observation.all_work_items_completed:
            attempt_number = (
                prepared.child_state.current_iteration
                if prepared.retry_reason == "reviewer_timeout"
                else 2
            )
            observation = provider_preparation.review_final_candidate(
                self.workspace,
                prepared.run_dir,
                observation,
                load_project_config(prepared.repo),
                persistent_session=self.persistent_sessions,
                attempt_number=attempt_number,
                timeout_seconds=900,
                progress_reporter=self.progress_reporter,
                event_reporter=self._event,
                provider=self.provider,
                reviewer_runner=getattr(self.loop_runtime, "reviewer_runner", None),
            )
        self._event("验证恢复后的 Workspace 与 Core Artifact 已完成对账")
        routed = self.runtime.observe_machine(prepared.run_dir.name, observation)
        self._event(f"Supervisor 选择：{decision_label(routed, observation)}")
        routed = self._settle_candidate(prepared, routed, observation)
        if routed.state.phase == "finalizing":
            routed = self.runtime.finalize(routed.run_dir.name)
            self._event("Supervisor 已完成：ready_to_commit")
        return routed
    def _settle_candidate(
        self,
        prepared: PreparedVerificationRetry,
        routed: AgentRun,
        observation: AgentObservation,
    ) -> AgentRun:
        if prepared.candidate_ref is None:
            return routed
        outcome = None
        if observation.work_item_completed and routed.state.phase in {
            "ready",
            "finalizing",
        }:
            outcome = "accept"
        elif (
            routed.state.phase == "ready"
            and "repair" in routed.state.allowed_actions
        ):
            outcome = "repair"
        if outcome is None:
            return routed
        settled = self.runtime.settle_candidate(
            routed.run_dir.name,
            candidate_ref=prepared.candidate_ref,
            outcome=outcome,
        )
        self._event(
            "Reviewer timeout Candidate 已接受"
            if outcome == "accept"
            else "Reviewer timeout Candidate 已还原为待修复 WIP"
        )
        return settled
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
