from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .agent_change_execution import (
    ProviderOperationBoundary,
    ensure_change_provider_ready,
    run_provider_operation,
)
from .agent_change_presentation import (
    build_change_approval_snapshot,
    redact_change_message,
)
from .agent_approval_runtime import ApprovalSnapshotChangedError
from .agent_cli_interaction import InteractionPumpUpdate
from .agent_planning import PLANNING_PROPOSAL_ARTIFACT
from .agent_planning_runtime import PlanningProposalRunner
from .agent_provider import AgentProvider, resolve_run_provider
from .agent_provider_adapter import SupervisorAgentProviderAdapter
from .agent_run import AgentRun
from .agent_run_selection import (
    ChangeRunSelectionError,
    select_named_repository_change_run,
    select_repository_change_run,
)
from .agent_repository_guard import (
    AgentRepositoryGuardBusyError,
    RepositoryChangeLock,
)
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_support import load_agent_bundle
from .agent_task_card import discover_handoff_task_cards


ChangeOutcome = Literal["completed", "attention_required"]
ApprovalMode = Literal["human", "bounded"]
ConfirmCallback = Callable[[str], bool]
EventReporter = Callable[[str], None]
InteractionReporter = Callable[[InteractionPumpUpdate], None]
ProgressReporter = Callable[[str, int], None]


@dataclass(frozen=True)
class ChangeDriverResult:
    """`vega change` 的稳定结果；不创造新的运行状态或成功语义。"""

    run: AgentRun | None
    outcome: ChangeOutcome
    reason_code: str
    message: str
    safe_actions: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome == "completed" else 2

    def as_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "run_id": self.run.run_dir.name if self.run is not None else None,
            "phase": self.run.state.phase if self.run is not None else None,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "message": self.message,
            "safe_actions": list(self.safe_actions),
        }


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
                    ("change", "status", "explain"),
                )
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
                    ("change", "status", "explain"),
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
        handlers = {
            "completed": self._completed,
            "finalizing": self._finalize,
            "planning": self._run_planning,
            "awaiting_approval": self._approve_phase,
            "ready": self._run_ready,
            "acting": self._active_execution,
            "observing": self._active_execution,
            "needs_human": self._needs_human,
            "stopped": self._stopped,
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
        return ChangeDriverResult(
            run=current,
            outcome="completed",
            reason_code="workflow.completed",
            message="ChangeRun 已完成，最终交付结论来自现有 Core Finish。",
            safe_actions=("status", "explain"),
        )

    def _finalize(self, current: AgentRun, _provider: AgentProvider) -> AgentRun:
        result = self.runtime.finalize(current.run_dir.name)
        self._event("可信 Core Finish 已发布")
        return result

    def _run_planning(
        self, current: AgentRun, provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        if current.state.active_planning_execution_id is not None:
            return self._attention(
                current,
                "planning.already_running",
                "当前 Planning Turn 仍在运行，拒绝启动第二个 Provider Turn。",
                ("status", "stop"),
            )
        if not (current.run_dir / PLANNING_PROPOSAL_ARTIFACT).is_file():
            ensure_change_provider_ready(provider)
        executed = run_provider_operation(
            self.workspace,
            current,
            provider,
            lambda: PlanningProposalRunner(
                self.workspace,
                provider=provider,
                persistent_session=True,
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
                ("change", "status", "stop"),
            )
        return executed

    def _approve_phase(
        self, current: AgentRun, _provider: AgentProvider
    ) -> AgentRun | ChangeDriverResult:
        return self._approve(current)

    def _approve(self, current: AgentRun) -> AgentRun | ChangeDriverResult:
        if self.approval == "bounded":
            approved = self.runtime.approve_bounded(current.run_dir.name)
            if approved.state.phase != "ready":
                return self._attention(
                    approved,
                    "approval.bounded_rejected",
                    "bounded 策略未放行，Contract 仍等待人工批准。",
                    ("approve", "revise", "status", "explain"),
                )
            self._event("bounded 策略已批准当前 Contract")
            return approved
        if not self.interactive or self.confirm is None:
            return self._attention(
                current,
                "approval.contract_required",
                "当前 Contract 等待人工批准；JSON 或非交互终端不会读取 stdin。",
                ("approve", "revise", "stop"),
            )
        snapshot = build_change_approval_snapshot(current)
        if not self.confirm(snapshot.prompt):
            return self._attention(
                current,
                "approval.declined",
                "当前 Contract 未获批准，Worker 未启动。",
                ("approve", "revise", "stop"),
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
                ("change", "status", "explain"),
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
                ("status", "explain"),
            )
        ensure_change_provider_ready(provider)
        executed = run_provider_operation(
            self.workspace,
            current,
            provider,
            lambda: SupervisorAgentProviderAdapter(
                self.workspace,
                provider=provider,
                persistent_sessions=True,
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
        message = (
            boundary.update.message
            or "Provider 请求需要人工处理。"
        )
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
            ("status", "explain", "recover", "takeover", "change <TEXT>"),
        )

    def _active_execution(
        self, current: AgentRun, _provider: AgentProvider
    ) -> ChangeDriverResult:
        return self._attention(
            current,
            "workflow.execution_already_active",
            "当前 ChangeRun 已绑定活动 Writer，拒绝启动第二个 Writer。",
            ("status", "stop", "recover"),
        )

    def _needs_human(
        self, current: AgentRun, _provider: AgentProvider
    ) -> ChangeDriverResult:
        return self._attention(
            current,
            "workflow.needs_human",
            "ChangeRun 已到人工边界；请查看原因和安全下一步。",
            ("status", "explain"),
        )

    def _stopped(
        self, current: AgentRun, _provider: AgentProvider
    ) -> ChangeDriverResult:
        return self._attention(
            current,
            "workflow.stopped",
            "ChangeRun 已停止，现场保持不变。",
            ("status", "explain", "resume"),
        )

    def _resume_implicit_task_card(self) -> ChangeDriverResult:
        cards = discover_handoff_task_cards(self.repo)
        if not cards:
            return self._attention(
                None,
                "change.no_active_run",
                "当前仓库没有未完成 ChangeRun，也没有可恢复的 Task Card。",
                ("change <TEXT>", "start"),
            )
        if len(cards) > 1:
            choices = "、".join(
                path.relative_to(self.repo).as_posix() for path in cards
            )
            return self._attention(
                None,
                "handoff.multiple_task_cards",
                f"当前分支有多个可恢复 Task Card，拒绝猜测：{choices}",
                ("change --task <path>",),
            )
        task = cards[0]
        relative = task.relative_to(self.repo).as_posix()
        if (
            not self.interactive
            or self.confirm is None
            or not self.confirm(f"从 Task Card 恢复 `{relative}`？")
        ):
            return self._attention(
                None,
                "handoff.confirmation_required",
                f"检测到可恢复 Task Card：{relative}；确认后再创建本机 ChangeRun。",
                (f"change --task {relative}",),
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
                    ("change", "status", "explain"),
                )
            current_cards = discover_handoff_task_cards(self.repo)
            if not current_cards:
                return self._attention(
                    None,
                    "change.no_active_run",
                    "当前仓库没有未完成 ChangeRun，也没有可恢复的 Task Card。",
                    ("change <TEXT>", "start"),
                )
            if len(current_cards) > 1:
                choices = "、".join(
                    path.relative_to(self.repo).as_posix()
                    for path in current_cards
                )
                return self._attention(
                    None,
                    "handoff.multiple_task_cards",
                    f"当前分支有多个可恢复 Task Card，拒绝猜测：{choices}",
                    ("change --task <path>",),
                )
            current_task = current_cards[0]
            if current_task.resolve() != task.resolve():
                current_relative = current_task.relative_to(self.repo).as_posix()
                return self._attention(
                    None,
                    "handoff.confirmation_required",
                    (
                        f"可恢复 Task Card 已变化：{current_relative}；"
                        "请重新确认后再创建本机 ChangeRun。"
                    ),
                    (f"change --task {current_relative}",),
                )
            restored = self.runtime.resume_task_card(self.repo, current_task)
        self._event(f"已从 Task Card 恢复：{restored.run_dir.name}")
        return self._drive(restored)

    def _explicit_run(self, run: str) -> AgentRun:
        selected = select_named_repository_change_run(self.repo, run)
        run_dir, state, plan, _ = load_agent_bundle(
            self.workspace,
            selected.run_dir.name,
        )
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
        safe_actions: tuple[str, ...],
    ) -> ChangeDriverResult:
        return ChangeDriverResult(
            run=run,
            outcome="attention_required",
            reason_code=reason_code,
            message=redact_change_message(message),
            safe_actions=safe_actions,
        )

    def _event(self, message: str) -> None:
        if self.event_reporter is not None:
            self.event_reporter(redact_change_message(message))
