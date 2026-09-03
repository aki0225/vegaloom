from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .agent_contract import AgentPlan, AgentState
from .agent_persistence import (
    append_agent_trace,
    save_agent_state,
)
from .agent_planning import (
    PLANNING_CONTEXT_ARTIFACT,
    PLANNING_REQUEST_ARTIFACT,
    PlanningProposal,
    PlanningRequest,
    build_planning_prompt,
    validate_planning_proposal,
)
from .agent_planning_execution import (
    PreparedPlanningTurn,
    execute_planning_turn,
    planning_attempt,
)
from .agent_planning_execution_reservation import reserve_planning_execution
from .agent_planning_publication import (
    planning_publication_committed,
    publish_active_planning_blocked,
    publish_planning_failure,
    publish_planning_proposal,
    publish_planning_stopped,
    reuse_or_publish_planning_proposal,
)
from .agent_planning_recovery import (
    planning_stop_was_requested,
    prepare_planning_state,
    reconcile_planning_exception,
    require_terminal_planning_execution,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    write_status_card,
)
from .agent_provider import AgentProvider
from .agent_provider_factory import planning_runner
from .project_config import load_project_config
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .runner import Runner, RunnerResult


class PlanningProposalRunner:
    """在现有 ChangeRun 中执行一次只读 Planning Turn。"""

    def __init__(
        self,
        workspace: Path,
        *,
        runner: Runner | None = None,
        provider: AgentProvider = "codex",
        persistent_session: bool = True,
        progress_reporter: Callable[[str, int], None] | None = None,
        event_reporter: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.runner = runner
        self.provider = provider
        self.persistent_session = persistent_session
        self.progress_reporter = progress_reporter
        self.event_reporter = event_reporter

    def run(self, run: str, *, timeout_seconds: int = 900) -> AgentRun:
        if not 60 <= timeout_seconds <= 3600:
            raise ValueError("Planning timeout 必须在 60..3600 秒之间")
        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "agent.planning"):
            prepared = self._prepare_locked(
                run_dir,
                timeout_seconds=timeout_seconds,
            )
        if isinstance(prepared, AgentRun):
            return prepared
        try:
            result = execute_planning_turn(
                prepared,
                timeout_seconds=timeout_seconds,
                progress_reporter=self.progress_reporter,
            )
        except Exception as exc:
            with RunMutationLock.acquire(run_dir, "agent.planning"):
                return reconcile_planning_exception(
                    self.workspace,
                    prepared,
                    exc,
                    event_reporter=self.event_reporter,
                )
        with RunMutationLock.acquire(run_dir, "agent.planning"):
            return self._reconcile_locked(prepared, result)

    def _prepare_locked(
        self,
        run_dir: Path,
        *,
        timeout_seconds: int,
    ) -> PreparedPlanningTurn | AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(
            self.workspace,
            run_dir.name,
        )
        prepared_state = prepare_planning_state(
            run_dir,
            state,
            plan,
            event_reporter=self.event_reporter,
        )
        if isinstance(prepared_state, AgentRun):
            return prepared_state
        before = prepared_state
        request = _load_planning_request(run_dir, state)
        repo = bound_repo(run_dir)
        if (
            before.fingerprint != state.workspace_fingerprint
            or before.head_sha != state.accepted_checkpoint_sha
        ):
            return self._fail_human(
                run_dir,
                state,
                plan,
                before,
                "只读调查前 Workspace 已漂移，必须人工核对",
            )
        existing = reuse_or_publish_planning_proposal(
            run_dir,
            repo,
            state,
            plan,
            before,
            request,
            event_reporter=self.event_reporter,
        )
        if existing is not None:
            return existing
        prompt = _planning_prompt(run_dir, request)
        config = load_project_config(
            repo,
            tracked_only=True,
            tracked_revision=request.source_revision,
        )
        if len(prompt) > config.prompt_budget.worker_max_chars:
            return self._fail_retryable(
                run_dir,
                state,
                plan,
                before,
                (
                    f"Planning Prompt 为 {len(prompt)} 字符，超过项目软上限 "
                    f"{config.prompt_budget.worker_max_chars}"
                ),
            )
        attempt = planning_attempt(run_dir)
        execution_id = uuid4().hex
        runner = self.runner or planning_runner(
            run_dir,
            state,
            config,
            provider=self.provider,
            persistent_session=self.persistent_session,
        )
        reservation = reserve_planning_execution(
            run_dir,
            execution_id=execution_id,
            attempt=attempt,
            timeout_seconds=timeout_seconds,
        )
        active_state = update_state(
            state,
            state_version=state.state_version + 1,
            active_planning_execution_id=execution_id,
        )
        try:
            save_agent_state(run_dir / "agent-state.json", active_state)
        except Exception:
            reservation.discard()
            raise
        try:
            self._event(f"开始只读调查：attempt {attempt}")
            append_agent_trace(
                run_dir / "trace.jsonl",
                event="planning_turn_started",
                state=active_state,
                observation_summary=f"只读 Planning attempt {attempt} 已启动",
                artifact_refs=[
                    f"executions/planning/{execution_id}/execution.json"
                ],
            )
            write_status_card(
                run_dir,
                active_state,
                plan,
                next_step="只读调查正在运行；可使用 status/watch 查看进度，或显式 stop",
            )
        except Exception as exc:
            reservation.finish_if_unclaimed(None, error=exc)
            raise
        return PreparedPlanningTurn(
            run_dir=run_dir,
            state_version=active_state.state_version,
            request=request,
            repo=repo,
            before=before,
            prompt=prompt,
            runner=runner,
            attempt=attempt,
            execution_id=execution_id,
            reservation=reservation,
        )

    def _reconcile_locked(
        self,
        prepared: PreparedPlanningTurn,
        result: RunnerResult,
    ) -> AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(
            self.workspace,
            prepared.run_dir.name,
        )
        if (
            state.phase != "planning"
            or state.state_version != prepared.state_version
            or state.active_planning_execution_id != prepared.execution_id
        ):
            self._event("Planning Turn 已结束，但 run 状态已变化；本轮结果未发布")
            return AgentRun(run_dir=run_dir, state=state, plan=plan)
        request = prepared.request
        execution_ref = (
            f"executions/planning/{prepared.execution_id}/execution.json"
        )
        evidence_refs = (
            [execution_ref] if (run_dir / execution_ref).is_file() else []
        )
        try:
            require_terminal_planning_execution(
                run_dir,
                prepared.execution_id,
            )
        except ValueError as exc:
            return publish_active_planning_blocked(
                run_dir,
                state,
                plan,
                prepared.before,
                f"Planning execution 终态无法确认：{exc}",
                evidence_refs=evidence_refs,
                event_reporter=self.event_reporter,
            )
        state = update_state(state, active_planning_execution_id=None)
        after = capture_bound_workspace(run_dir)
        if (
            after.fingerprint != prepared.before.fingerprint
            or after.head_sha != request.source_revision
        ):
            return self._fail_human(
                run_dir,
                state,
                plan,
                after,
                "只读 Planning Turn 改变了 Workspace，已停止自动流程",
                evidence_refs=evidence_refs,
            )
        if result.termination_unconfirmed:
            return self._fail_human(
                run_dir,
                state,
                plan,
                after,
                "Planning 进程终止未确认，不能安全重试",
                evidence_refs=evidence_refs,
            )
        if planning_stop_was_requested(
            run_dir,
            prepared.execution_id,
        ):
            return self._publish_stopped(
                run_dir,
                state,
                plan,
                after,
                result.error or "Planning 已按 stop request 停止",
                evidence_refs=evidence_refs,
            )
        if result.status != "success":
            return self._fail_retryable(
                run_dir,
                state,
                plan,
                after,
                result.error or f"Planning Runner 终态为 {result.status}",
                evidence_refs=evidence_refs,
            )
        try:
            proposal = PlanningProposal.model_validate_json(result.output)
            validate_planning_proposal(
                prepared.repo,
                proposal,
                task_id=request.task_id,
                user_goal=request.user_goal,
                source_revision=request.source_revision,
            )
        except (ValidationError, ValueError) as exc:
            return self._fail_retryable(
                run_dir,
                state,
                plan,
                after,
                f"Planning Proposal 无效：{exc}",
                evidence_refs=evidence_refs,
            )
        try:
            return self._publish_proposal(
                run_dir,
                state,
                plan,
                after,
                proposal,
                evidence_refs=evidence_refs,
            )
        except OSError as exc:
            _, current_state, current_plan, _ = load_agent_bundle(
                self.workspace,
                run_dir.name,
            )
            if planning_publication_committed(run_dir, current_state):
                recovered = reuse_or_publish_planning_proposal(
                    run_dir,
                    prepared.repo,
                    current_state,
                    current_plan,
                    after,
                    prepared.request,
                    event_reporter=self.event_reporter,
                )
                assert recovered is not None
                return recovered
            return self._fail_retryable(
                run_dir,
                current_state,
                current_plan,
                after,
                f"Planning Proposal 发布中断：{type(exc).__name__}",
                evidence_refs=evidence_refs,
            )
    def _publish_proposal(
        self,
        run_dir: Path,
        state: AgentState,
        plan: AgentPlan,
        snapshot,
        proposal: PlanningProposal,
        *,
        evidence_refs: list[str],
    ) -> AgentRun:
        return publish_planning_proposal(
            run_dir,
            state,
            plan,
            snapshot,
            proposal,
            evidence_refs=evidence_refs,
            event_reporter=self.event_reporter,
        )

    def _fail_retryable(
        self,
        run_dir: Path,
        state: AgentState,
        plan: AgentPlan,
        snapshot,
        reason: str,
        *,
        evidence_refs: list[str] | None = None,
    ) -> AgentRun:
        return publish_planning_failure(
            run_dir,
            state,
            plan,
            snapshot,
            reason,
            needs_human=False,
            evidence_refs=evidence_refs or [],
            event_reporter=self.event_reporter,
        )

    def _fail_human(
        self,
        run_dir: Path,
        state: AgentState,
        plan: AgentPlan,
        snapshot,
        reason: str,
        *,
        evidence_refs: list[str] | None = None,
    ) -> AgentRun:
        return publish_planning_failure(
            run_dir,
            state,
            plan,
            snapshot,
            reason,
            needs_human=True,
            evidence_refs=evidence_refs or [],
            event_reporter=self.event_reporter,
        )

    def _publish_stopped(
        self,
        run_dir: Path,
        state: AgentState,
        plan: AgentPlan,
        snapshot,
        reason: str,
        *,
        evidence_refs: list[str],
    ) -> AgentRun:
        return publish_planning_stopped(
            run_dir,
            state,
            plan,
            snapshot,
            reason,
            evidence_refs=evidence_refs,
            event_reporter=self.event_reporter,
        )

    def _event(self, message: str) -> None:
        if self.event_reporter is not None:
            self.event_reporter(message)


def _load_planning_request(
    run_dir: Path,
    state: AgentState,
) -> PlanningRequest:
    try:
        request = PlanningRequest.model_validate_json(
            (run_dir / PLANNING_REQUEST_ARTIFACT).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise ValueError("Planning Request 无法读取") from exc
    if request.task_id != state.task_id:
        raise ValueError("Planning Request 与 Agent State 身份不一致")
    return request


def _planning_prompt(
    run_dir: Path,
    request: PlanningRequest,
) -> str:
    try:
        context = (run_dir / PLANNING_CONTEXT_ARTIFACT).read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise ValueError("Planning 项目上下文无法读取") from exc
    digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
    if digest != request.project_context_sha256:
        raise ValueError("Planning 项目上下文摘要不一致")
    return build_planning_prompt(
        task_id=request.task_id,
        user_goal=request.user_goal,
        source_revision=request.source_revision,
        project_context=context,
    )
