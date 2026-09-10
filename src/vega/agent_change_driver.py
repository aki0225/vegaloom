from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from .agent_change_execution import ProviderOperationBoundary, ensure_change_provider_ready
from .agent_change_execution import run_provider_operation
from .agent_change_task_card import TaskCardSelection, confirm_task_card_selection
from .agent_change_task_card import select_unique_task_card
from .agent_change_presentation import (
    ChangeDriverResult,
    build_change_approval_snapshot,
    redact_change_message,
)
from .agent_approval_runtime import ApprovalSnapshotChangedError
from .agent_cli_interaction import InteractionPumpUpdate
from .agent_planning import PLANNING_PROPOSAL_ARTIFACT
from .agent_planning_runtime import PlanningProposalRunner
from .agent_provider import AgentProvider, resolve_run_provider
from .agent_provider_adapter import SupervisorAgentProviderAdapter
from .agent_repository_change_lock import (
    AgentRepositoryGuardBusyError,
    RepositoryChangeLock,
)
from .agent_run import AgentRun
from .agent_explain import AgentExplanation
from .agent_cli_snapshot import AgentCliRun, build_agent_cli_snapshot
from .agent_verification_retry import SupervisorAgentVerificationRetry
from .agent_verification_retry_preparation import verification_retry_requested
from .agent_run_selection import (
    ChangeRunSelectionError,
    select_repository_change_run,
)
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_support import load_agent_bundle
from .project_config_provider import ensure_change_startup_config


ApprovalMode = Literal["human", "bounded"]
ConfirmCallback = Callable[[str], bool]
EventReporter = Callable[[str], None]
InteractionReporter = Callable[[InteractionPumpUpdate], None]
ProgressReporter = Callable[[str, int], None]

class AgentChangeDriver:
    """把已有 ChangeRun 阶段串成日常主路径，不接管 Core 裁决。"""

    def __init__(
        self,
        workspace: Path,
        repo: Path,
        *,
        provider: AgentProvider | None = None,
        approval: ApprovalMode = "human",
        timeout_seconds: int = 900,
        interactive: bool = False,
        json_output: bool = False,
        fresh_session: bool = False,
        confirm: ConfirmCallback | None = None,
        event_reporter: EventReporter | None = None,
        interaction_reporter: InteractionReporter | None = None,
        progress_reporter: ProgressReporter | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.repo = repo.resolve()
        self.requested_provider = provider
        self.approval = approval
        self.timeout_seconds = timeout_seconds
        self.interactive = interactive and not json_output
        self.json_output = json_output
        self.persistent_sessions = not fresh_session
        self.confirm = confirm
        self.event_reporter = event_reporter
        self.interaction_reporter = interaction_reporter
        self.progress_reporter = progress_reporter
        self.runtime = SupervisorAgentRuntime(self.workspace)
        if not 60 <= timeout_seconds <= 3600:
            raise ValueError("--timeout 必须在 60..3600 秒之间")

    def change(
        self, *, text: str | None = None, run: str | None = None, task: Path | None = None
    ) -> ChangeDriverResult:
        """创建或继续当前仓库的唯一 ChangeRun。"""

        normalized_text = text.strip() if text is not None else None
        if text is not None and not normalized_text:
            raise ValueError("TEXT 不能为空")
        selected_modes = sum(value is not None for value in (normalized_text, run, task))
        if selected_modes > 1:
            raise ValueError("TEXT、--run 与 --task 必须且只能选择一种输入方式")
        try:
            if normalized_text is not None:
                return self._start_new(normalized_text)
            if run is not None:
                return self._drive(self._explicit_run(run))
            if task is not None:
                return self._resume_explicit_task(task)
            return self._continue_default()
        except AgentRepositoryGuardBusyError as exc:
            return self._attention(
                None,
                "change.repository_busy",
                str(exc),
                ("status", "explain", "change"),
            )

    def _start_new(self, text: str) -> ChangeDriverResult:
        with RepositoryChangeLock.acquire(self.repo):
            active = self._implicit_active_run()
            if isinstance(active, ChangeDriverResult):
                return active
            if active is not None:
                return self._attention(
                    active,
                    "change.active_run_exists",
                    (
                        f"当前仓库已有未完成 ChangeRun：{active.run_dir.name}；"
                        "使用不带 TEXT 的 `vega change` 继续，"
                        "或使用高级 `vega start` 显式创建并行任务。"
                    ),
                )
            ensure_change_startup_config(self.repo)
            started = self.runtime.start_planning(self.repo, goal=text)
        self._event(f"Planning ChangeRun 已创建：{started.run_dir.name}")
        return self._drive(started)

    def _resume_explicit_task(self, task: Path) -> ChangeDriverResult:
        task_path = task if task.is_absolute() else self.repo / task
        with RepositoryChangeLock.acquire(self.repo):
            active = self._implicit_active_run()
            if isinstance(active, ChangeDriverResult):
                return active
            if active is not None:
                return self._attention(
                    active,
                    "change.active_run_exists",
                    "当前仓库已有未完成 ChangeRun，拒绝恢复第二个 Writer。",
                )
            restored = self.runtime.resume_task_card(self.repo, task_path)
        self._event(f"已从 Task Card 恢复：{restored.run_dir.name}")
        return self._drive(restored)

    def _continue_default(self) -> ChangeDriverResult:
        active = self._implicit_active_run()
        if isinstance(active, ChangeDriverResult):
            return active
        if active is not None:
            return self._drive(active)
        return self._resume_implicit_task_card()

    def _drive(self, current: AgentRun) -> ChangeDriverResult:
        current.state.require_current_execution()
        for _ in range(12):
            selected_provider = resolve_run_provider(
                current.run_dir,
                self.requested_provider,
            )
            advanced = self._advance_phase(current, selected_provider)
            if isinstance(advanced, ChangeDriverResult):
                return advanced
            current = advanced
        raise ValueError("ChangeRun 主路径超过允许的阶段推进次数")

    def _advance_phase(
        self, current: AgentRun, provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        if current.state.phase in {"acting", "observing", "stopped"}:
            stopped = current.state.phase == "stopped"
            return self._attention(
                current, "workflow.stopped" if stopped else "workflow.execution_already_active",
                "ChangeRun 已停止，现场保持不变。" if stopped else
                "当前 ChangeRun 已绑定活动 Writer，拒绝启动第二个 Writer。",
            )
        handlers = {
            "completed": self._completed,
            "finalizing": lambda run, _: self.runtime.finalize(run.run_dir.name),
            "planning": self._run_planning,
            "awaiting_approval": self._approve,
            "ready": self._run_ready,
            "needs_human": self._needs_human,
        }
        try:
            handler = handlers[current.state.phase]
        except KeyError as exc:
            raise ValueError(
                f"ChangeRun 阶段无法由日常入口处理：{current.state.phase}"
            ) from exc
        return handler(current, provider)

    def _completed(
        self, current: AgentRun, _provider: AgentProvider
    ) -> ChangeDriverResult:
        explanation = self._explanation(current)
        completed = explanation.phase == "completed" and explanation.outcome == "completed"
        return ChangeDriverResult(
            run=current,
            outcome="completed" if completed else "attention_required",
            reason_code="workflow.completed" if completed else explanation.reason_code,
            message=redact_change_message(explanation.reason),
            safe_actions=tuple(explanation.safe_actions),
        )

    def _run_planning(
        self, current: AgentRun, provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        if current.state.contract_revision is not None:
            # 已编译合同的 replan 必须经过 revision 裁决，不能重用初始调查绕过批准和预算。
            return self._attention(
                current,
                "workflow.replan_required",
                "当前合同需要重新规划；请查看证据后使用 revise 提交修订，"
                "由现有合同和预算门禁裁决。保留当前 Candidate 和批准记录。",
            )
        if current.state.active_planning_execution_id is not None:
            return self._reconcile_planning(current, provider)
        if not (current.run_dir / PLANNING_PROPOSAL_ARTIFACT).is_file():
            ensure_change_provider_ready(provider)
        executed = run_provider_operation(
            self.workspace,
            current,
            provider,
            lambda: PlanningProposalRunner(
                self.workspace,
                provider=provider,
                persistent_session=self.persistent_sessions,
                progress_reporter=self.progress_reporter,
                event_reporter=self.event_reporter,
            ).run(
                current.run_dir.name,
                timeout_seconds=self.timeout_seconds,
            ),
            interaction_reporter=self.interaction_reporter,
            event_reporter=self.event_reporter,
        )
        if isinstance(executed, ProviderOperationBoundary):
            return self._interaction_boundary(executed)
        if (
            executed.state.phase == "planning"
            and (executed.run_dir / PLANNING_PROPOSAL_ARTIFACT).is_file()
        ):
            executed = self.runtime.compile_planning(executed.run_dir.name)
            self._event("Contract Compiler 已生成未批准合同")
        if executed.state.phase == "planning":
            return self._attention(
                executed,
                "planning.incomplete",
                "只读调查没有形成可编译的 Planning Proposal。",
            )
        return executed

    def _approve(
        self, current: AgentRun, _provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        if self.approval == "bounded":
            approved = self.runtime.approve_bounded(current.run_dir.name)
            if approved.state.phase != "ready":
                return self._attention(
                    approved,
                    "approval.bounded_rejected",
                    "bounded 策略未放行，Contract 仍等待人工批准。",
                )
            self._event("bounded 策略已批准当前 Contract")
            return approved
        confirm = self.confirm if self.interactive else None
        snapshot = build_change_approval_snapshot(current) if confirm is not None else None
        if snapshot is None or confirm is None or not confirm(snapshot.prompt):
            return self._attention(
                current,
                "approval.contract_required" if snapshot is None else "approval.declined",
                "当前 Contract 等待人工批准；JSON 或非交互终端不会读取 stdin。"
                if snapshot is None else "当前 Contract 未获批准，Worker 未启动。",
            )
        try:
            approved = self.runtime.approve_if_current(
                current.run_dir.name,
                expected_state_version=snapshot.state_version,
                expected_contract_digest=snapshot.contract_digest,
                expected_execution_plan_revision=snapshot.execution_plan_revision,
                expected_execution_plan_digest=snapshot.execution_plan_digest,
                actor="human:vega-change",
            )
        except ApprovalSnapshotChangedError as exc:
            return self._attention(
                exc.current,
                "approval.snapshot_changed",
                "确认期间 Contract、Execution Plan 或 Run 状态已变化；请重新查看并批准。",
            )
        self._event("当前 Contract 已由人工批准")
        return approved

    def _run_ready(
        self, current: AgentRun, provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        if not {"next", "repair"}.intersection(current.state.allowed_actions):
            return self._attention(
                current,
                "workflow.no_automatic_action",
                "当前 ready 状态没有可自动执行的 next 或 repair 动作。",
            )
        if verification_retry_requested(self.workspace, current.run_dir.name):
            # 判定只选择现有引擎；完整门禁失败必须向外报告，不能偷偷改派 Worker。
            return self._verification_retry(provider).run(current.run_dir.name)
        ensure_change_provider_ready(provider)
        executed = run_provider_operation(
            self.workspace,
            current,
            provider,
            lambda: SupervisorAgentProviderAdapter(
                self.workspace,
                provider=provider,
                persistent_sessions=self.persistent_sessions,
                progress_reporter=self.progress_reporter,
                event_reporter=self.event_reporter,
            ).run(
                current.run_dir.name,
                timeout_seconds=self.timeout_seconds,
            ),
            interaction_reporter=self.interaction_reporter,
            event_reporter=self.event_reporter,
        )
        if isinstance(executed, ProviderOperationBoundary):
            return self._interaction_boundary(executed)
        return executed

    def _interaction_boundary(
        self, boundary: ProviderOperationBoundary
    ) -> ChangeDriverResult:
        message = boundary.update.message or "Provider 请求需要人工处理。"
        message = (
            f"{message} 当前 attempt 已中断；请使用 status、explain、recover "
            "或 takeover 对账，确认后创建新 attempt。"
        )
        if boundary.stop_unconfirmed:
            message = (
                f"{message} 停止请求已发送，但 15 秒内未取得执行终态；"
                "保留现场并按 recover 流程对账。"
            )
        return self._attention(
            boundary.run,
            boundary.update.reason_code or "provider.interaction_required",
            message,
        )

    def _needs_human(
        self, current: AgentRun, provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        if current.state.contract_revision is None and current.state.active_planning_execution_id:
            return self._reconcile_planning(current, provider)
        rechecked = self._verification_retry(provider).recheck_core_if_eligible(
            current.run_dir.name,
        )
        if rechecked is not None:
            if rechecked.state.phase in {"ready", "finalizing", "completed"}:
                return rechecked
            current = rechecked
        return self._attention(
            current,
            "workflow.needs_human",
            "ChangeRun 已到人工边界；请查看原因和安全下一步。",
        )

    def _resume_implicit_task_card(self) -> ChangeDriverResult:
        selection = select_unique_task_card(self.repo)
        if not selection.selected:
            return self._task_card_attention(selection)
        assert selection.task is not None
        assert selection.relative_path is not None
        if (
            not self.interactive
            or self.confirm is None
            or not self.confirm(
                f"从 Task Card 恢复 `{selection.relative_path}`？"
            )
        ):
            return self._attention(
                None,
                "handoff.confirmation_required",
                (
                    f"检测到可恢复 Task Card：{selection.relative_path}；"
                    "确认后再创建本机 ChangeRun。"
                ),
                (f"change --task {selection.relative_path}",),
            )
        with RepositoryChangeLock.acquire(self.repo):
            active = self._implicit_active_run()
            if isinstance(active, ChangeDriverResult):
                return active
            if active is not None:
                return self._attention(
                    active,
                    "change.active_run_exists",
                    "当前仓库已有未完成 ChangeRun，拒绝恢复第二个 Writer。",
                )
            current = confirm_task_card_selection(self.repo, selection)
            if not current.selected:
                return self._task_card_attention(current)
            assert current.task is not None
            restored = self.runtime.resume_task_card(self.repo, current.task)
        self._event(f"已从 Task Card 恢复：{restored.run_dir.name}")
        return self._drive(restored)

    def _task_card_attention(
        self,
        selection: TaskCardSelection,
    ) -> ChangeDriverResult:
        assert selection.reason_code is not None
        assert selection.message is not None
        return self._attention(
            None,
            selection.reason_code,
            selection.message,
            selection.safe_actions,
        )

    def _explicit_run(self, run: str) -> AgentRun:
        run_dir, state, plan, metadata = load_agent_bundle(self.workspace, run)
        if state.run_kind != "change":
            raise ValueError("change 只接受 ChangeRun")
        change_metadata = metadata.get("change_run")
        source = change_metadata.get("source_repo_path") if isinstance(change_metadata, dict) else None
        if not isinstance(source, str) or Path(source).resolve() != self.repo:
            raise ValueError("指定 Run 不属于当前仓库的可验证 ChangeRun")
        return AgentRun(run_dir=run_dir, state=state, plan=plan)

    def _implicit_active_run(self) -> AgentRun | ChangeDriverResult | None:
        try:
            selected = select_repository_change_run(self.repo)
        except ChangeRunSelectionError as exc:
            if exc.candidates:
                choices = "、".join(
                    item.run_dir.name for item in exc.candidates
                )
                return self._attention(
                    None,
                    "change.multiple_active_runs",
                    (
                        "当前仓库存在多个未完成 ChangeRun，拒绝自动选择："
                        f"{choices}"
                    ),
                    ("change --run <run-id>", "status --run <run-id>"),
                )
            raise
        if selected is None or not selected.is_active:
            return None
        run_dir, state, plan, _ = load_agent_bundle(
            self.workspace,
            selected.run_dir.name,
        )
        return AgentRun(run_dir=run_dir, state=state, plan=plan)

    def _attention(
        self,
        run: AgentRun | None,
        reason_code: str,
        message: str,
        safe_actions: tuple[str, ...] = (),
    ) -> ChangeDriverResult:
        return ChangeDriverResult(
            run=run,
            outcome="attention_required",
            reason_code=reason_code,
            message=redact_change_message(message),
            safe_actions=tuple(self._explanation(run).safe_actions) if run is not None else safe_actions,
        )

    def _reconcile_planning(self, current: AgentRun, provider: AgentProvider) -> ChangeDriverResult:
        # 已绑定的调查只交回原 Runner 对账；本次调用不得顺势开启第二个 Planner。
        reconciled = PlanningProposalRunner(
            self.workspace, provider=provider,
            persistent_session=self.persistent_sessions,
            progress_reporter=self.progress_reporter, event_reporter=self.event_reporter,
        ).run(current.run_dir.name, timeout_seconds=self.timeout_seconds)
        return self._attention(
            reconciled, "planning.reconciled", "已核对当前调查执行；请查看状态后继续。",
        )

    def _verification_retry(self, provider: AgentProvider) -> SupervisorAgentVerificationRetry:
        return SupervisorAgentVerificationRetry(
            self.workspace, provider=provider,
            persistent_sessions=self.persistent_sessions,
            progress_reporter=self.progress_reporter,
            event_reporter=self.event_reporter,
        )

    def _explanation(self, current: AgentRun) -> AgentExplanation:
        # 与 status、explain 复用同一证据快照，不能把阶段名称再推导成另一套建议。
        snapshot = build_agent_cli_snapshot(AgentCliRun(
            workspace=current.run_dir.parent.parent,
            run_dir=current.run_dir,
            selection_source="explicit",
        ))
        assert snapshot.explanation is not None
        if snapshot.status_projection is None or snapshot.status_projection.state != current.state:
            raise ValueError("ChangeRun 在结果展示期间已变化；请重新查看 status")
        return snapshot.explanation

    def _event(self, message: str) -> None:
        if self.event_reporter is not None:
            self.event_reporter(redact_change_message(message))
