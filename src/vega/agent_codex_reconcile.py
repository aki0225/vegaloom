from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from .agent_codex_evidence import (
    ExecutedCodexAttempt,
    WorkerClaim,
    decision_label,
    evaluate_worker_claim,
    load_child_state,
    load_finish_summary,
    observation_from_child,
    require_child_quiescent,
    write_child_summary,
)
from .agent_codex_scope import (
    evaluate_plan_scope,
    plan_scope_failure,
    write_plan_scope_evidence,
)
from .agent_git_candidate import (
    CandidateCommit,
    freeze_candidate_commit,
    validate_candidate_binding,
)
from .agent_operation import operation_ref
from .agent_run import AgentRun
from .agent_runtime_support import capture_bound_workspace
from .models import LoopAutomationState

if TYPE_CHECKING:
    from .agent_codex_adapter import SupervisorAgentCodexAdapter


def reconcile_codex_attempt(
    adapter: SupervisorAgentCodexAdapter,
    executed: ExecutedCodexAttempt,
) -> AgentRun:
    return _AttemptReconciler(adapter, executed).run()


class _AttemptReconciler:
    def __init__(
        self,
        adapter: SupervisorAgentCodexAdapter,
        executed: ExecutedCodexAttempt,
    ) -> None:
        self.adapter = adapter
        self.executed = executed
        self.prepared = executed.prepared
        self.bound = executed.bound
        self.claim: WorkerClaim | None = None
        self.candidate: CandidateCommit | None = None
        self.candidate_ref: str | None = None
        self.plan_scope_ref: str | None = None
        self.final_plan_scope_ref: str | None = None
        self.child_state: LoopAutomationState | None = None
        self.finish_summary: dict[str, object] | None = None

    def run(self) -> AgentRun:
        failure = self._validate_worker_result()
        if failure is not None:
            return failure
        failure = self._freeze_candidate()
        if failure is not None:
            return failure
        failure = self._run_core()
        if failure is not None:
            return failure
        failure = self._validate_core_result()
        if failure is not None:
            return failure
        return self._publish_observation()

    def _validate_worker_result(self) -> AgentRun | None:
        result = self.executed.result
        scope = evaluate_plan_scope(
            self.prepared.repo,
            self.prepared.plan_scope_baseline,
            expected_head_sha=self.prepared.before.head_sha,
            iteration=self.prepared.attempt_number,
            comparison_base_sha=self.prepared.comparison_base_sha,
            comparison_paths=self.prepared.comparison_paths,
        )
        self.plan_scope_ref = write_plan_scope_evidence(
            self.prepared.run_dir,
            self.executed.operation_id,
            scope,
            stage="post-worker",
        )
        if scope.status == "failed":
            side_effects = (
                self.prepared.external_side_effects
                if result.status == "success"
                else "unknown"
            )
            return self._failure(
                plan_scope_failure(scope),
                external_side_effects=side_effects,
                plan_contradicted=True,
            )
        after_worker = capture_bound_workspace(self.prepared.run_dir)
        if (
            self.prepared.attempt_number > 1
            and result.status == "success"
            and after_worker.fingerprint == self.prepared.before.fingerprint
        ):
            return self._failure(
                "repair Worker 未产生新的 Workspace 变化；"
                "不能把上一 attempt 的 Diff 重新记为本次修复证据",
                external_side_effects=self.prepared.external_side_effects,
            )
        if result.status != "success":
            return self._failure(
                result.error or f"Worker 终态为 {result.status}",
                external_side_effects="unknown",
            )
        self.claim, failure_reason = evaluate_worker_claim(result.output)
        if failure_reason is not None:
            return self._failure(
                failure_reason,
                external_side_effects="unknown",
            )
        return None

    def _freeze_candidate(self) -> AgentRun | None:
        context = self.prepared.change_context
        if context is None:
            return None
        assert self.claim is not None
        try:
            self.candidate = freeze_candidate_commit(
                context.worktree,
                expected_parent_sha=self.prepared.before.head_sha,
                contract=context.contract,
                execution_plan=context.execution_plan,
                work_item_id=self.prepared.state.current_work_item or "",
                operation_id=self.executed.operation_id,
            )
            self.bound, self.candidate_ref = self.adapter.runtime.bind_candidate(
                self.prepared.run_dir.name,
                candidate=self.candidate,
            )
            self.adapter._initialize_change_core(
                self.prepared,
                self.executed.child_dir,
                self.candidate,
            )
        except (OSError, ValueError) as exc:
            return self._failure(
                f"ChangeRun Candidate 无法进入 Core：{exc}",
                external_side_effects=self.prepared.external_side_effects,
            )
        return None

    def _run_core(self) -> AgentRun | None:
        assert self.claim is not None
        child_dir = self.executed.child_dir
        try:
            self.adapter.loop_runtime.continue_assist(
                child_dir.name,
                self.prepared.repo,
                worker_name="codex-exec",
                reviewer_name="codex-exec",
                verify=True,
                verification_commands=list(self.prepared.verification_commands),
            )
            self.adapter.finish_runtime.run(child_dir.name)
            require_child_quiescent(child_dir)
            self.child_state = load_child_state(child_dir, self.prepared.repo)
            self.finish_summary = load_finish_summary(child_dir, child_dir.name)
        except (OSError, ValueError, ValidationError) as exc:
            require_child_quiescent(child_dir)
            return self._failure(
                f"现有 Vega Core 未形成可采用终态：{exc}",
                external_side_effects=self.prepared.external_side_effects,
            )
        return None

    def _validate_core_result(self) -> AgentRun | None:
        assert self.claim is not None
        assert self.child_state is not None
        assert self.finish_summary is not None
        expected_head = (
            self.candidate.candidate_sha
            if self.candidate is not None
            else self.prepared.before.head_sha
        )
        scope = evaluate_plan_scope(
            self.prepared.repo,
            self.prepared.plan_scope_baseline,
            expected_head_sha=expected_head,
            iteration=self.prepared.attempt_number,
            comparison_base_sha=self.prepared.comparison_base_sha,
            comparison_paths=self.prepared.comparison_paths,
        )
        self.final_plan_scope_ref = write_plan_scope_evidence(
            self.prepared.run_dir,
            self.executed.operation_id,
            scope,
            stage="post-core",
        )
        if scope.status == "failed":
            return self._failure(
                f"现有 Core 执行后，{plan_scope_failure(scope)}",
                external_side_effects=self.prepared.external_side_effects,
                plan_contradicted=True,
            )
        return self._validate_candidate_after_core()

    def _validate_candidate_after_core(self) -> AgentRun | None:
        if self.candidate is None:
            return None
        context = self.prepared.change_context
        assert context is not None
        try:
            validate_candidate_binding(
                context.worktree,
                candidate=self.candidate,
                contract=context.contract,
                execution_plan=context.execution_plan,
            )
        except ValueError as exc:
            return self._failure(
                f"Core 执行后 Candidate 已漂移：{exc}",
                external_side_effects=self.prepared.external_side_effects,
            )
        return None

    def _publish_observation(self) -> AgentRun:
        assert self.claim is not None
        assert self.child_state is not None
        assert self.finish_summary is not None
        refs = [
            operation_ref(self.executed.operation_id),
            *self._scope_and_candidate_refs(),
        ]
        summary_ref = write_child_summary(
            self.prepared.run_dir,
            self.prepared.state,
            self.executed.child_dir,
            self.executed.operation_id,
            self.executed.worker_record,
            self.executed.result,
            claim=self.claim,
            child_state=self.child_state,
            finish_summary=self.finish_summary,
        )
        observation = observation_from_child(
            self.prepared.run_dir,
            self.prepared.state,
            self.prepared.plan,
            self.executed.child_dir,
            self.executed.operation_id,
            self.claim,
            self.child_state,
            self.finish_summary,
            evidence_refs=[*refs, summary_ref],
            external_side_effects=self.prepared.external_side_effects,
        )
        self.adapter._event("Workspace 与现有 Core Artifact 已完成对账")
        routed = self.adapter.runtime.observe_machine(
            self.prepared.run_dir.name,
            observation,
        )
        self.adapter._event(
            f"Supervisor 选择：{decision_label(routed, observation)}"
        )
        routed = self._settle_candidate(routed, observation.work_item_completed)
        if routed.state.phase == "finalizing":
            self.adapter._event("正在采用可信 Core Finish 终态")
            routed = self.adapter.runtime.finalize(routed.run_dir.name)
            self.adapter._event("Supervisor 已完成：ready_to_commit")
        return routed

    def _settle_candidate(
        self,
        routed: AgentRun,
        work_item_completed: bool,
    ) -> AgentRun:
        if self.candidate_ref is None:
            return routed
        outcome = None
        if work_item_completed and routed.state.phase in {"ready", "finalizing"}:
            outcome = "accept"
        elif routed.state.phase == "ready" and "repair" in routed.state.allowed_actions:
            outcome = "repair"
        if outcome is None:
            return routed
        routed = self.adapter.runtime.settle_candidate(
            routed.run_dir.name,
            candidate_ref=self.candidate_ref,
            outcome=outcome,
        )
        if outcome == "repair":
            self.adapter._event("失败 Candidate 已还原为待修复 WIP")
        else:
            assert self.candidate is not None
            self.adapter._event(
                f"Accepted Checkpoint：{self.candidate.candidate_sha[:12]}"
            )
        return routed

    def _failure(
        self,
        reason: str,
        *,
        external_side_effects: str,
        plan_contradicted: bool = False,
    ) -> AgentRun:
        return self.adapter._observe_failure(
            self.bound,
            self.executed.child_dir,
            self.executed.operation_id,
            self.executed.worker_record,
            self.executed.result,
            reason=reason,
            external_side_effects=external_side_effects,
            claim=self.claim,
            plan_contradicted=plan_contradicted,
            extra_evidence_refs=self._scope_and_candidate_refs(),
            child_state=self.child_state,
            finish_summary=self.finish_summary,
        )

    def _scope_and_candidate_refs(self) -> list[str]:
        return [
            ref
            for ref in (
                self.plan_scope_ref,
                self.final_plan_scope_ref,
                self.candidate_ref,
            )
            if ref is not None
        ]
