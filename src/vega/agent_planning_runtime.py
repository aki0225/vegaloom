from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .agent_contract import AgentPlan, AgentState
from .agent_persistence import (
    append_agent_trace,
    read_agent_trace,
    save_agent_state,
)
from .agent_planning import (
    PLANNING_CONTEXT_ARTIFACT,
    PLANNING_PROPOSAL_ARTIFACT,
    PLANNING_REPORT_ARTIFACT,
    PLANNING_REQUEST_ARTIFACT,
    PlanningProposal,
    PlanningRequest,
    build_planning_prompt,
    render_planning_proposal,
    validate_planning_proposal,
    validate_published_planning_proposal,
)
from .agent_planning_execution import (
    PreparedPlanningTurn,
    execute_planning_turn,
)
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo,
    capture_bound_workspace,
    load_agent_bundle,
    save_agent_plan,
    write_checkpoint,
    write_status_card,
)
from .codex_app_server_runner import CodexAppServerRunner
from .project_config import load_project_config
from .redaction import (
    redact_text,
    write_redacted_json_once,
    write_redacted_text,
)
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .runner import CodexExecRunner, Runner, RunnerResult


class PlanningProposalRunner:
    """在现有 ChangeRun 中执行一次只读 Planning Turn。"""

    def __init__(
        self,
        workspace: Path,
        *,
        runner: Runner | None = None,
        persistent_session: bool = True,
        progress_reporter: Callable[[str, int], None] | None = None,
        event_reporter: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.runner = runner
        self.persistent_session = persistent_session
        self.progress_reporter = progress_reporter
        self.event_reporter = event_reporter

    def run(self, run: str, *, timeout_seconds: int = 900) -> AgentRun:
        if not 60 <= timeout_seconds <= 3600:
            raise ValueError("Planning timeout 必须在 60..3600 秒之间")
        run_dir = self._resolve_run_dir(run)
        with RunMutationLock.acquire(run_dir, "agent.planning"):
            prepared = self._prepare_locked(run_dir)
        if isinstance(prepared, AgentRun):
            return prepared
        result = execute_planning_turn(
            prepared,
            timeout_seconds=timeout_seconds,
            progress_reporter=self.progress_reporter,
        )
        with RunMutationLock.acquire(run_dir, "agent.planning"):
            return self._reconcile_locked(prepared, result)

    def _prepare_locked(
        self,
        run_dir: Path,
    ) -> PreparedPlanningTurn | AgentRun:
        run_dir, state, plan, _ = load_agent_bundle(
            self.workspace,
            run_dir.name,
        )
        if state.phase != "planning" or state.run_kind != "change":
            raise ValueError("当前 ChangeRun 不在 Planning 阶段")
        if state.active_child_run or state.active_candidate_sha:
            raise ValueError("Planning 阶段不能绑定 Worker 或 Candidate")
        request = _load_planning_request(run_dir, state)
        repo = bound_repo(run_dir)
        before = capture_bound_workspace(run_dir)
        if (
            before.fingerprint != state.workspace_fingerprint
            or before.head_sha != request.source_revision
        ):
            return self._fail_human(
                run_dir,
                state,
                plan,
                before,
                "只读调查前 Workspace 已漂移，必须人工核对",
            )
        if (run_dir / PLANNING_PROPOSAL_ARTIFACT).exists():
            try:
                validate_published_planning_proposal(
                    run_dir, repo, state, plan, request
                )
            except ValueError as exc:
                return self._fail_human(
                    run_dir, state, plan, before, str(exc)
                )
            return AgentRun(run_dir=run_dir, state=state, plan=plan)
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
        attempt = _planning_attempt(run_dir)
        execution_id = uuid4().hex
        runner = self.runner or _default_planning_runner(
            run_dir,
            state,
            config.runner.codex_exec.worker,
            persistent_session=self.persistent_session,
        )
        self._event(f"开始只读调查：attempt {attempt}")
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="planning_turn_started",
            state=state,
            observation_summary=f"只读 Planning attempt {attempt} 已启动",
        )
        return PreparedPlanningTurn(
            run_dir=run_dir,
            state_version=state.state_version,
            request=request,
            repo=repo,
            before=before,
            prompt=prompt,
            runner=runner,
            attempt=attempt,
            execution_id=execution_id,
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
        if state.phase != "planning" or state.state_version != prepared.state_version:
            self._event("Planning Turn 已结束，但 run 状态已变化；本轮结果未发布")
            return AgentRun(run_dir=run_dir, state=state, plan=plan)
        after = capture_bound_workspace(run_dir)
        request = prepared.request
        execution_ref = (
            f"executions/planning/{prepared.execution_id}/execution.json"
        )
        evidence_refs = (
            [execution_ref] if (run_dir / execution_ref).is_file() else []
        )
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
        return self._publish_proposal(
            run_dir,
            state,
            plan,
            after,
            proposal,
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
        write_redacted_json_once(
            run_dir / PLANNING_PROPOSAL_ARTIFACT,
            proposal.model_dump(mode="json"),
        )
        write_redacted_text(
            run_dir / PLANNING_REPORT_ARTIFACT,
            render_planning_proposal(proposal),
        )
        projected = plan.model_copy(
            update={
                "observed_facts": [
                    fact.statement for fact in proposal.observed_facts
                ],
                "hypotheses": list(proposal.hypotheses),
                "unresolved_decisions": [
                    *proposal.unresolved_questions,
                    "Planning Proposal 尚未经过 Contract Compiler",
                ],
            }
        )
        next_state = update_state(
            state,
            state_version=state.state_version + 1,
            workspace_fingerprint=snapshot.fingerprint,
        )
        checkpoint = write_checkpoint(
            run_dir,
            next_state,
            snapshot,
            reason="Planning Proposal 已生成，等待编译为可批准合同",
            status="safe",
            pending_actions=["replan", "human"],
            evidence_refs=[
                *evidence_refs,
                PLANNING_PROPOSAL_ARTIFACT,
                PLANNING_REPORT_ARTIFACT,
            ],
            operation_started=False,
            external_side_effects="none",
        )
        next_state = update_state(
            next_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        save_agent_plan(run_dir, projected)
        save_agent_state(run_dir / "agent-state.json", next_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="planning_proposal_created",
            state=next_state,
            observation_summary=(
                f"{len(proposal.observed_facts)} 条事实，"
                f"{len(proposal.hypotheses)} 条假设，"
                f"{len(proposal.unresolved_questions)} 个未决问题"
            ),
            artifact_refs=[
                PLANNING_PROPOSAL_ARTIFACT,
                PLANNING_REPORT_ARTIFACT,
                f"checkpoints/{checkpoint.checkpoint_id}.json",
                *evidence_refs,
            ],
        )
        write_status_card(
            run_dir,
            next_state,
            projected,
            checkpoint=checkpoint,
            next_step=checkpoint.reason,
        )
        self._event("Planning Proposal 已生成")
        return AgentRun(run_dir=run_dir, state=next_state, plan=projected)

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
        return self._publish_failure(
            run_dir,
            state,
            plan,
            snapshot,
            reason,
            needs_human=False,
            evidence_refs=evidence_refs or [],
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
        return self._publish_failure(
            run_dir,
            state,
            plan,
            snapshot,
            reason,
            needs_human=True,
            evidence_refs=evidence_refs or [],
        )

    def _publish_failure(
        self,
        run_dir: Path,
        state: AgentState,
        plan: AgentPlan,
        snapshot,
        reason: str,
        *,
        needs_human: bool,
        evidence_refs: list[str],
    ) -> AgentRun:
        safe_reason = redact_text(reason.strip())[:2000]
        next_state = update_state(
            state,
            phase="needs_human" if needs_human else "planning",
            state_version=state.state_version + 1,
            workspace_fingerprint=snapshot.fingerprint,
            allowed_actions=["human"] if needs_human else ["replan", "human"],
        )
        checkpoint = write_checkpoint(
            run_dir,
            next_state,
            snapshot,
            reason=safe_reason,
            status="blocked" if needs_human else "safe",
            pending_actions=["human"] if needs_human else ["replan", "human"],
            evidence_refs=evidence_refs,
            operation_started=False,
            external_side_effects="unknown" if needs_human else "none",
        )
        next_state = update_state(
            next_state,
            latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", next_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="planning_blocked" if needs_human else "planning_retry_required",
            state=next_state,
            route_reason=safe_reason,
            artifact_refs=[
                f"checkpoints/{checkpoint.checkpoint_id}.json",
                *evidence_refs,
            ],
        )
        write_status_card(
            run_dir,
            next_state,
            plan,
            checkpoint=checkpoint,
            next_step=safe_reason,
        )
        self._event(safe_reason)
        return AgentRun(run_dir=run_dir, state=next_state, plan=plan)

    def _resolve_run_dir(self, run: str) -> Path:
        return resolve_run_dir(self.workspace, run)

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


def _default_planning_runner(
    run_dir: Path,
    state: AgentState,
    options,
    *,
    persistent_session: bool,
) -> Runner:
    if persistent_session:
        return CodexAppServerRunner(
            run_dir,
            "worker",
            work_item_id=state.current_work_item,
            contract_revision=None,
            plan_revision=None,
            output_schema=PlanningProposal.model_json_schema(),
            isolate_session=True,
            options=options,
        )
    return CodexExecRunner(
        options=options,
        output_schema=PlanningProposal.model_json_schema(),
        isolate_mcp=True,
    )


def _planning_attempt(run_dir: Path) -> int:
    try:
        trace = read_agent_trace(run_dir / "trace.jsonl")
    except (OSError, ValueError):
        return 1
    return (
        sum(item.get("event") == "planning_turn_started" for item in trace) + 1
    )
