from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import PrivateAttr

from .brief_runtime import BriefRuntime
from .execution_control import RunnerExecutionContext
from .gate_runtime import evaluate_risk, render_gate_report
from .loop_engine import (
    DEFAULT_LOOP_ENGINE,
    LoopEngineName,
    normalize_loop_engine,
    preflight_persisted_linear_engine,
    require_persisted_linear_engine,
)
from .loop_graph_state import GraphStateValidationError, read_graph_state
from .loop_recovery_replay import (
    guard_recovery_artifacts,
    recovery_artifact_guard,
)
from .loop_steps import (
    CaptureWorkspaceStepRequest,
    FinalizeRunStepRequest,
    HumanDecisionStepRequest,
    HumanDecisionStepResult,
    LoopStepInstruction,
    LoopStepProgram,
    LoopStepProgramDriver,
    LoopStepServices,
    PrepareRunStepRequest,
    ReflectStepRequest,
    ReviewStepRequest,
    RiskStepRequest,
    VerificationStepRequest,
    WorkerEpochStepRequest,
    WorkspaceReconcileStepRequest,
)
from .models import (
    BriefInput,
    GateResult,
    LoopAutomationState,
    LoopIterationState,
    ReviewVerdict,
)
from .project_config import ProjectConfig, load_project_config, project_policy_snapshot
from .prompt_metrics import measure_prompt, write_context_budget_report, write_prompt_metrics
from .redaction import redact_text, redact_value
from .reflect_runtime import ReflectRuntime
from .review_runtime import ReviewRuntime
from .risk_gate_evidence import (
    render_risk_gate_report_binding,
    sha256_text,
    validate_iteration_risk_gate_artifacts,
)
from .run_utils import create_run_dir, resolve_run_dir, run_name
from .runner import Runner, RunnerResult, RunnerStatus, make_runner
from .trace import RecoveryTraceWriter, TraceWriter
from .verification import VerificationRunResult, run_project_verification
from .workspace_check import (
    WorkspaceCheckResult,
    WorkspaceSnapshot,
    run_workspace_check,
    snapshot_workspace,
)

LOOP_ARTIFACTS = [
    "state.json",
    "trace.jsonl",
    "agent-brief.md",
    "project-context.md",
    "loop-plan.md",
    "worker-prompt.md",
    "worker-prompt-metrics.json",
    "worker-prompt-metrics.md",
    "eval.md",
]
FINAL_LOOP_ARTIFACTS = [*LOOP_ARTIFACTS, "final-report.md"]
RISK_GATE_RESULT_ARTIFACT = "risk-gate-result.json"
RISK_GATE_REPORT_ARTIFACT = "risk-gate-report.md"
RUN_TERMINAL_STATE_REVOKED_EVENT = "run_terminal_state_revoked"
RUN_TERMINAL_REVOKED_EVENT = "run_terminal_revoked"


@dataclass(frozen=True)
class LoopRiskGateEvidence:
    result: GateResult | None
    error: str | None
    source_run: str
    result_sha256: str
    report_sha256: str

    @property
    def status(self) -> Literal["success", "failed"]:
        return "failed" if self.error else "success"


class _RecoveryLoopAutomationState(LoopAutomationState):
    """恢复时先静默重建生成器，和 checkpoint 对齐后才允许写权威状态。"""

    _persistence_enabled: bool = PrivateAttr(default=False)

    def enable_persistence(self) -> None:
        self._persistence_enabled = True

    def save(self, path: Path) -> None:
        if self._persistence_enabled:
            super().save(path)


class LoopAutomationRuntime:
    def __init__(
        self,
        workspace: Path,
        worker_runner: Runner | None = None,
        reviewer_runner: Runner | None = None,
        timeout_seconds: int = 900,
        step_services: LoopStepServices | None = None,
        graph_fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.worker_runner = worker_runner
        self.reviewer_runner = reviewer_runner
        self.timeout_seconds = timeout_seconds
        self.step_services = step_services or self._default_step_services()
        self.graph_fault_injector = graph_fault_injector

    def _default_step_services(self) -> LoopStepServices:
        def dispatch_review(request: ReviewStepRequest) -> Path:
            return ReviewRuntime(
                request.workspace,
                runner=self.reviewer_runner,
                timeout_seconds=self.timeout_seconds,
            ).run(
                request.repo_path,
                run_name(request.reflect_run),
                runner_name=request.reviewer_name,
                execution_context=RunnerExecutionContext(
                    execution_dir=_iteration_dir(
                        request.loop_run_dir,
                        request.iteration,
                    )
                    / "executions"
                    / "reviewer",
                    run_id=request.loop_run_dir.name,
                    step="reviewer",
                    iteration=request.iteration,
                ),
                project_config=request.config,
                human_approval_run_dir=request.loop_run_dir,
                human_approval_iteration=request.iteration,
                human_approval_ref=request.human_approval_ref,
            )

        def finalize_run(request: FinalizeRunStepRequest) -> None:
            self._persist_loop_done(
                request.run_dir,
                request.state,
                request.status,
                request.trace,
                current_step=request.current_step,
            )

        return LoopStepServices(
            prepare_run=lambda request: BriefRuntime(request.workspace).run(
                request.brief_input
            ),
            capture_workspace=lambda request: snapshot_workspace(
                request.repo_path
            ),
            execute_worker_epoch=lambda request: request.runner.run(
                request.prompt,
                request.repo_path,
                sandbox=request.sandbox,
                timeout_seconds=request.timeout_seconds,
                execution_context=request.execution_context,
            ),
            reconcile_workspace=lambda request: run_workspace_check(
                request.repo_path,
                request.output_dir,
                baseline=request.baseline,
                allow_existing_tracked_diff=request.allow_existing_tracked_diff,
                require_clean_untracked=request.require_clean_untracked,
            ),
            run_verification=lambda request: run_project_verification(
                request.workspace,
                request.repo_path,
                request.output_dir,
            ),
            run_reflect=lambda request: ReflectRuntime(request.workspace).run(
                request.repo_path,
                source_run=request.source_run,
                test_log=request.test_log,
                note=request.note,
            ),
            evaluate_risk=lambda request: evaluate_risk(
                request.workspace,
                request.repo_path,
                request.source_run,
            ),
            request_human_decision=lambda _request: HumanDecisionStepResult(
                decision="not_provided",
            ),
            dispatch_review=dispatch_review,
            finalize_run=finalize_run,
        )

    def start(
        self,
        brief_input: BriefInput,
        automation_mode: Literal["assist", "auto"],
        worker_name: str = "codex-exec",
        reviewer_name: str = "codex-exec",
        max_iterations: int = 2,
        verify: bool = True,
        engine: LoopEngineName | str = DEFAULT_LOOP_ENGINE,
    ) -> Path:
        engine_name = normalize_loop_engine(engine)
        config = load_project_config(Path(brief_input.repo_path))
        worker_name, reviewer_name = _apply_runner_defaults(
            config,
            worker_name,
            reviewer_name,
        )
        program_executor = self._load_program_executor(
            engine_name,
            automation_mode=automation_mode,
            worker_name=worker_name,
            reviewer_name=reviewer_name,
            verify=verify,
        )
        if engine_name == "langgraph":
            _require_graph_control_isolation(
                self.workspace,
                Path(brief_input.repo_path),
            )
        run_id, run_dir = create_run_dir(
            self.workspace,
            _new_loop_run_id(brief_input.mode),
        )
        trace = TraceWriter(run_dir / "trace.jsonl")
        state = LoopAutomationState(
            run_id=run_id,
            task_mode=brief_input.mode,
            automation_mode=automation_mode,
            engine=engine_name,
            repo_path=brief_input.repo_path,
            input_source=brief_input.source,
            status="running",
            max_iterations=max_iterations,
            project_policy_snapshot=project_policy_snapshot(Path(brief_input.repo_path)),
        )
        state.current_step = "brief"
        state.save(run_dir / "state.json")
        trace.write(
            "loop_started",
            task_mode=brief_input.mode,
            automation_mode=automation_mode,
            engine=engine_name,
            repo_path=brief_input.repo_path,
        )
        _write_project_policy_snapshot(run_dir, state.project_policy_snapshot)

        program = self._start_step_program(
            run_dir,
            state,
            trace,
            brief_input,
            automation_mode,
            worker_name,
            reviewer_name,
            verify,
            config,
        )
        driver = LoopStepProgramDriver(program, self.step_services)
        return program_executor(run_dir, driver)

    def _load_program_executor(
        self,
        engine: LoopEngineName,
        *,
        automation_mode: Literal["assist", "auto"],
        worker_name: str,
        reviewer_name: str,
        verify: bool,
    ) -> Callable[[Path, LoopStepProgramDriver], Path]:
        if engine == "linear":
            return lambda _run_dir, driver: driver.run_linear()
        try:
            from .loop_graph_runtime import execute_langgraph_program
        except ModuleNotFoundError as exc:
            if exc.name == "langgraph" or (
                exc.name is not None and exc.name.startswith("langgraph.")
            ):
                raise ValueError(
                    "langgraph engine 需要可选依赖；"
                    "请在项目隔离环境中安装 `vegaloom[langgraph]`"
                ) from exc
            raise
        return lambda run_dir, driver: execute_langgraph_program(
            run_dir,
            driver,
            automation_mode=automation_mode,
            worker_name=worker_name,
            reviewer_name=reviewer_name,
            verify=verify,
            timeout_seconds=self.timeout_seconds,
            fault_injector=cast(Callable, self.graph_fault_injector),
        )

    def recover_langgraph(
        self,
        run: str,
        reason: str,
        *,
        engine: str | None = None,
    ) -> Path:
        """按 Gate 3 reconciliation 恢复 graph，而不是盲目重放外部节点。"""

        from .loop_graph_recovery import hold_graph_operation_lease

        if not reason.strip():
            raise ValueError("recover 必须提供原因，方便后续追溯。")
        run_dir = resolve_run_dir(self.workspace, run)
        with hold_graph_operation_lease(run_dir, "recover"):
            return self._recover_langgraph_with_lease(
                run_dir,
                reason.strip(),
                engine=engine,
            )

    def _recover_langgraph_with_lease(
        self,
        run_dir: Path,
        reason: str,
        *,
        engine: str | None,
    ) -> Path:
        from .loop_engine import ensure_loop_engine_matches
        from .loop_graph_recovery import read_graph_run_config
        from .loop_graph_runtime import resume_langgraph_program

        state_path = run_dir / "state.json"
        state = _RecoveryLoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
        if state.run_id != run_dir.name:
            raise ValueError(
                "loop state.run_id 与 run 目录身份不一致；"
                "为避免串用 checkpoint，已拒绝 graph recovery。"
            )
        persisted_engine = ensure_loop_engine_matches(state.engine, engine)
        if persisted_engine != "langgraph":
            raise ValueError("只有 langgraph run 可以使用 graph recovery")
        if state.status == "running" and state.current_step == "human_decision":
            raise ValueError(
                "当前 LangGraph run 正在等待 human_decision；"
                "普通 recover 不得替代人工决策消费，"
                "请先写入 decision ledger，再使用 "
                "`vega resume --decision-id <dec-id> --engine langgraph`。"
            )
        run_config = read_graph_run_config(run_dir)
        if self.timeout_seconds != run_config.timeout_seconds:
            raise ValueError(
                "graph run 的 timeout_seconds 已固定为 "
                f"{run_config.timeout_seconds}，当前 Runtime 为 "
                f"{self.timeout_seconds}；为避免 attempt 输入身份漂移，"
                "已拒绝恢复。"
            )
        repo_path = Path(state.repo_path).resolve()
        _require_graph_control_isolation(self.workspace, repo_path)
        config = load_project_config(repo_path)
        brief_input = BriefInput(
            mode=state.task_mode,
            text="",
            source=state.input_source,
            repo_path=str(repo_path),
        )
        recovery_trace = RecoveryTraceWriter(run_dir / "trace.jsonl")
        with guard_recovery_artifacts(run_dir) as artifact_guard:
            program = self._start_step_program(
                run_dir,
                state,
                recovery_trace,
                brief_input,
                run_config.automation_mode,
                run_config.worker_name,
                run_config.reviewer_name,
                run_config.verify,
                config,
            )
            driver = LoopStepProgramDriver(program, self.step_services)

            def enable_business_writes() -> None:
                state.enable_persistence()
                artifact_guard.enable_new_writes()
                recovery_trace.enable()

            return resume_langgraph_program(
                run_dir,
                driver,
                business_state=state,
                request_reason=reason,
                enable_state_persistence=enable_business_writes,
                fault_injector=cast(Callable, self.graph_fault_injector),
            )

    def resume_langgraph_decision(
        self,
        run: str,
        decision_id: str,
        *,
        engine: str | None = None,
    ) -> Path:
        """只用已写入 ledger 的 decision id 恢复 HITL interrupt。"""

        from .loop_graph_recovery import hold_graph_operation_lease

        normalized_decision_id = decision_id.strip()
        if not normalized_decision_id:
            raise ValueError("resume 必须提供 decision id")
        run_dir = resolve_run_dir(self.workspace, run)
        with hold_graph_operation_lease(run_dir, "resume_decision"):
            return self._resume_langgraph_decision_with_lease(
                run_dir,
                normalized_decision_id,
                engine=engine,
            )

    def _resume_langgraph_decision_with_lease(
        self,
        run_dir: Path,
        decision_id: str,
        *,
        engine: str | None,
    ) -> Path:
        from .loop_engine import ensure_loop_engine_matches
        from .loop_graph_recovery import read_graph_run_config
        from .loop_graph_runtime import resume_langgraph_decision

        state = _RecoveryLoopAutomationState.model_validate_json(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
        if state.run_id != run_dir.name:
            raise ValueError(
                "loop state.run_id 与 run 目录身份不一致；"
                "为避免串用 HITL checkpoint，已拒绝 resume。"
            )
        persisted_engine = ensure_loop_engine_matches(state.engine, engine)
        if persisted_engine != "langgraph":
            raise ValueError("只有 langgraph run 可以消费 HITL decision")
        if state.status != "running" or state.current_step != "human_decision":
            raise ValueError(
                "只有停在 human_decision 的 running run 可以 resume"
            )
        run_config = read_graph_run_config(run_dir)
        if self.timeout_seconds != run_config.timeout_seconds:
            raise ValueError(
                "graph run 的 timeout_seconds 已固定为 "
                f"{run_config.timeout_seconds}，当前 Runtime 为 "
                f"{self.timeout_seconds}；已拒绝 resume。"
            )
        repo_path = Path(state.repo_path).resolve()
        _require_graph_control_isolation(self.workspace, repo_path)
        config = load_project_config(repo_path)
        brief_input = BriefInput(
            mode=state.task_mode,
            text="",
            source=state.input_source,
            repo_path=str(repo_path),
        )
        recovery_trace = RecoveryTraceWriter(run_dir / "trace.jsonl")
        with guard_recovery_artifacts(run_dir) as artifact_guard:
            program = self._start_step_program(
                run_dir,
                state,
                recovery_trace,
                brief_input,
                run_config.automation_mode,
                run_config.worker_name,
                run_config.reviewer_name,
                run_config.verify,
                config,
            )
            driver = LoopStepProgramDriver(program, self.step_services)

            def enable_business_writes() -> None:
                state.enable_persistence()
                artifact_guard.enable_new_writes()
                recovery_trace.enable()

            return resume_langgraph_decision(
                run_dir,
                driver,
                business_state=state,
                decision_id=decision_id,
                enable_state_persistence=enable_business_writes,
                fault_injector=cast(Callable, self.graph_fault_injector),
            )

    def _start_step_program(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        trace: TraceWriter,
        brief_input: BriefInput,
        automation_mode: Literal["assist", "auto"],
        worker_name: str,
        reviewer_name: str,
        verify: bool,
        config: ProjectConfig,
    ) -> LoopStepProgram:
        brief_run = cast(
            Path,
            (yield LoopStepInstruction(
                "prepare_run",
                PrepareRunStepRequest(
                    workspace=self.workspace,
                    brief_input=brief_input,
                ),
            )),
        )
        state.brief_run = run_name(brief_run)
        _copy_if_exists(brief_run / "agent-brief.md", run_dir / "agent-brief.md")
        _copy_if_exists(brief_run / "project-context.md", run_dir / "project-context.md")
        trace.write("brief_finished", brief_run=state.brief_run)
        _write_text_artifact(
            run_dir / "loop-plan.md",
            render_loop_plan(brief_input, automation_mode, state.max_iterations),
        )
        worker_prompt, worker_sections = build_worker_prompt(
            brief_input,
            brief_run,
            None,
            1,
        )
        _write_text_artifact(run_dir / "worker-prompt.md", worker_prompt)
        worker_metrics = measure_prompt(
            worker_prompt,
            role="worker",
            max_chars=config.prompt_budget.worker_max_chars,
            sections=worker_sections,
        )
        write_prompt_metrics(
            run_dir,
            "worker-prompt",
            worker_metrics,
            write_text=_write_text_artifact,
        )
        trace.write("worker_prompt_measured", metrics=worker_metrics.model_dump())
        if automation_mode == "assist":
            root_budget_artifact: str | None = None
            if worker_metrics.exceeded:
                root_budget_artifact = write_context_budget_report(
                    run_dir,
                    "worker",
                    worker_metrics,
                )
            state.current_step = "waiting_for_worker"
            state.status = "needs_human"
            state.artifacts = [
                *LOOP_ARTIFACTS,
                *([root_budget_artifact] if root_budget_artifact else []),
            ]
            trace.write("loop_waiting_for_worker", worker_prompt="worker-prompt.md")
            yield self._finalize_instruction(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="waiting_for_worker",
            )
            return run_dir

        return (
            yield from self._auto_step_program(
                run_dir,
                state,
                brief_input,
                worker_name,
                reviewer_name,
                verify,
                config,
                trace,
            )
        )

    def _finalize_instruction(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        status: Literal["success", "failed", "needs_human"],
        trace: TraceWriter,
        *,
        current_step: str = "done",
    ) -> LoopStepInstruction:
        return LoopStepInstruction(
            "finalize_run",
            FinalizeRunStepRequest(
                run_dir=run_dir,
                state=state,
                status=status,
                trace=trace,
                current_step=current_step,
            ),
        )

    def _run_auto_iterations(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        brief_input: BriefInput,
        worker_name: str,
        reviewer_name: str,
        verify: bool,
        config: ProjectConfig,
    ) -> Path:
        return LoopStepProgramDriver(
            self._auto_step_program(
                run_dir,
                state,
                brief_input,
                worker_name,
                reviewer_name,
                verify,
                config,
                TraceWriter(run_dir / "trace.jsonl"),
            ),
            self.step_services,
        ).run_linear()

    def _auto_step_program(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        brief_input: BriefInput,
        worker_name: str,
        reviewer_name: str,
        verify: bool,
        config: ProjectConfig,
        trace: TraceWriter,
    ) -> LoopStepProgram:
        return (
            yield from self._auto_iterations_program(
                run_dir,
                state,
                brief_input,
                worker_name,
                reviewer_name,
                verify,
                config,
                trace,
            )
        )

    def continue_assist(
        self,
        run: str,
        repo_path: Path,
        reviewer_name: str = "codex-exec",
        test_log: Path | None = None,
        note: str | None = None,
        verify: bool = True,
        engine: str | None = None,
    ) -> Path:
        run_dir = resolve_run_dir(self.workspace, run)
        preflight_persisted_linear_engine(run_dir / "state.json", engine)
        state = LoopAutomationState.model_validate_json(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
        if state.run_id != run_dir.name:
            raise ValueError(
                "loop state.run_id 与 run 目录身份不一致；"
                "为避免在错误证据链上继续，已拒绝 continue。"
            )
        require_persisted_linear_engine(state.engine, engine)
        if state.automation_mode not in {"assist", "auto"}:
            raise ValueError("只有 assist/auto loop 可以使用 continue")
        repo = repo_path.resolve()
        expected_repo = Path(state.repo_path).resolve()
        if repo != expected_repo:
            raise ValueError(
                f"loop continue 目标仓库不匹配：run={expected_repo}，传入={repo}"
            )
        if state.status != "needs_human":
            raise ValueError(
                f"只有 needs_human 状态的 loop 可以 continue，当前状态：{state.status}"
            )
        if _project_policy_changed(repo, state.project_policy_snapshot):
            trace = TraceWriter(run_dir / "trace.jsonl")
            iteration_number = len(state.iterations) + 1
            iteration_dir = _iteration_dir(run_dir, iteration_number)
            _write_project_policy_change_report(
                iteration_dir,
                state.project_policy_snapshot,
                project_policy_snapshot(repo),
            )
            state.current_iteration = iteration_number
            state.iterations.append(
                LoopIterationState(
                    iteration=iteration_number,
                    worker_status="skipped",
                )
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "项目策略文件在 loop 启动后发生变化，未继续自动验证和审查。",
            )
            trace.write("project_policy_changed", iteration=iteration_number)
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="project_policy_changed",
            )
            return run_dir
        config = load_project_config(repo)
        _, reviewer_name = _apply_runner_defaults(config, "codex-exec", reviewer_name)
        trace = TraceWriter(run_dir / "trace.jsonl")
        state.status = "running"
        state.current_step = "workspace_check"
        state.save(run_dir / "state.json")
        iteration_number = len(state.iterations) + 1
        iteration_dir = _iteration_dir(run_dir, iteration_number)
        trace.write("assist_continue_started", iteration=iteration_number)
        _write_text_artifact(
            iteration_dir / "worker-output.txt",
            f"{state.automation_mode} continue 模式下，worker 是主会话或人工；Vega 只记录当前工作区 diff。\n",
        )
        workspace_check = self.step_services.reconcile_workspace(
            WorkspaceReconcileStepRequest(
                repo_path=repo,
                output_dir=iteration_dir,
                require_clean_untracked=True,
            )
        )
        trace.write(
            "workspace_check_finished",
            iteration=iteration_number,
            status=workspace_check.status,
            new_untracked_count=workspace_check.new_untracked_count,
            baseline_untracked_changed=workspace_check.baseline_untracked_changed,
        )
        if workspace_check.has_failures:
            state.iterations.append(
                LoopIterationState(
                    iteration=iteration_number,
                    worker_status="skipped",
                    workspace_status="failed",
                    workspace_new_files_count=workspace_check.new_untracked_count,
                )
            )
            state.current_iteration = iteration_number
            if workspace_check.new_untracked_count:
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_untracked_files_fix_prompt(
                        iteration_number + 1,
                        workspace_check.new_untracked_files,
                    ),
                )
                trace.write(
                    "untracked_files_require_human",
                    iteration=iteration_number,
                    count=workspace_check.new_untracked_count,
                    paths=workspace_check.new_untracked_files,
                )
                conclusion = (
                    "当前工作区存在未跟踪文件；reviewer 不读取其内容，"
                    "已在 verification 前转人工确认。"
                )
                current_step = "untracked_files"
            else:
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_workspace_fix_prompt(iteration_number + 1),
                )
                conclusion = "工作区完整性检查失败，未继续 verification、reflect 或 review。"
                current_step = "workspace_check_failed"
            _write_final_report(run_dir, state, None, conclusion)
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step=current_step,
            )
            return run_dir

        auto_test_log = test_log
        if verify and auto_test_log is None:
            state.current_step = "verify"
            state.save(run_dir / "state.json")
            verification = self.step_services.run_verification(
                VerificationStepRequest(
                    workspace=self.workspace,
                    repo_path=repo,
                    output_dir=iteration_dir,
                )
            )
            verification_status = _verification_status(verification.command_count, verification.failed_count)
            verification_failed_count = verification.failed_count
            trace.write(
                "verification_finished",
                iteration=iteration_number,
                commands=verification.command_count,
                failed=verification.failed_count,
                interruption_status=verification.interruption_status,
            )
            auto_test_log = verification.summary_path
            if verification.was_interrupted:
                self._pause_for_verification_interruption(
                    run_dir,
                    state,
                    iteration_dir,
                    iteration_number,
                    verification,
                    trace,
                    worker_status="skipped",
                )
                return run_dir
        else:
            verification_status = "skipped"
            verification_failed_count = 0

        state.current_step = "reflect"
        state.save(run_dir / "state.json")
        reflect_run = self.step_services.run_reflect(
            ReflectStepRequest(
                workspace=self.workspace,
                repo_path=repo,
                source_run=state.brief_run,
                test_log=auto_test_log.resolve() if auto_test_log else None,
                note=note,
            )
        )
        _record_reflect(iteration_dir, reflect_run)
        if not _reflect_run_succeeded(reflect_run):
            state.iterations.append(
                LoopIterationState(
                    iteration=iteration_number,
                    worker_status="skipped",
                    workspace_status=workspace_check.status,
                    workspace_new_files_count=workspace_check.new_untracked_count,
                    verification_status=verification_status,
                    verification_failed_count=verification_failed_count,
                    reflect_run=run_name(reflect_run),
                )
            )
            state.current_iteration = iteration_number
            _write_reflect_failure_report(iteration_dir, reflect_run)
            trace.write(
                "reflect_failed",
                iteration=iteration_number,
                reflect_run=run_name(reflect_run),
                status=_read_reflect_run_status(reflect_run),
            )
            _write_final_report(
                run_dir,
                state,
                None,
                "Reflect 的确定性证据检查失败，未启动隔离 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="reflect_failed",
            )
            return run_dir
        if not _reflect_has_tracked_diff(reflect_run):
            state.iterations.append(
                LoopIterationState(
                    iteration=iteration_number,
                    worker_status="skipped",
                    workspace_status=workspace_check.status,
                    workspace_new_files_count=workspace_check.new_untracked_count,
                    verification_status=verification_status,
                    verification_failed_count=verification_failed_count,
                    reflect_run=run_name(reflect_run),
                )
            )
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_no_diff_fix_prompt(iteration_number + 1),
            )
            trace.write("zero_diff_requires_human", iteration=iteration_number)
            _write_final_report(
                run_dir,
                state,
                None,
                "当前工作区没有可审查的 tracked diff，不能启动隔离 reviewer。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="no_diff",
            )
            return run_dir
        gate_evidence = _evaluate_loop_risk_gate(
            self.step_services.evaluate_risk,
            self.workspace,
            repo,
            reflect_run,
            iteration_dir,
            trace,
            iteration_number,
        )
        gate_result = gate_evidence.result
        if (
            gate_evidence.error
            or gate_result is None
            or gate_result.recommendation == "human-review"
        ):
            state.iterations.append(
                LoopIterationState(
                    iteration=iteration_number,
                    worker_status="skipped",
                    workspace_status=workspace_check.status,
                    workspace_new_files_count=workspace_check.new_untracked_count,
                    verification_status=verification_status,
                    verification_failed_count=verification_failed_count,
                    reflect_run=run_name(reflect_run),
                    **_risk_gate_state_fields(gate_evidence),
                )
            )
            state.current_iteration = iteration_number
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_risk_gate_fix_prompt(
                    iteration_number + 1,
                    None if gate_evidence.error else gate_result,
                ),
            )
            conclusion = (
                "风险门禁评估失败，未启动隔离 reviewer。"
                if gate_evidence.error or gate_result is None
                else "风险门禁要求人工确认，未继续自动隔离审查。"
            )
            current_step = (
                "risk_gate_failed"
                if gate_evidence.error or gate_result is None
                else "risk_gate_needs_human"
            )
            _write_final_report(run_dir, state, None, conclusion)
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step=current_step,
            )
            return run_dir
        state.current_step = "review"
        state.save(run_dir / "state.json")
        review_run = self.step_services.dispatch_review(
            ReviewStepRequest(
                workspace=self.workspace,
                repo_path=repo,
                reflect_run=reflect_run,
                reviewer_name=reviewer_name,
                loop_run_dir=run_dir,
                iteration=iteration_number,
                config=config,
            )
        )
        verdict = _read_verdict(review_run)
        reviewer_status = _read_review_runner_status(review_run)
        review_run_status = _read_review_run_status(review_run)
        _record_review(iteration_dir, review_run)
        iteration = LoopIterationState(
            iteration=iteration_number,
            worker_status="skipped",
            reviewer_status=reviewer_status,
            verification_status=verification_status,
            verification_failed_count=verification_failed_count,
            reflect_run=run_name(reflect_run),
            **_risk_gate_state_fields(gate_evidence),
            review_run=run_name(review_run),
            verdict=verdict.verdict,
            findings_count=len(verdict.findings),
        )
        state.iterations.append(iteration)
        state.current_iteration = iteration_number
        if not _review_run_allows_verdict(review_run_status, verdict):
            trace.write(
                "review_run_not_successful",
                iteration=iteration_number,
                status=review_run_status,
            )
            _write_final_report(
                run_dir,
                state,
                verdict,
                f"Review run 自身状态为 {review_run_status}，不能采用其 verdict。",
            )
            self._save_loop_done(
                run_dir,
                state,
                "needs_human",
                trace,
                current_step="review_run_failed",
            )
            return run_dir
        self._finish_or_prepare_next(run_dir, state, verdict, iteration_dir, trace)
        return run_dir

    def _auto_iterations_program(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        brief_input: BriefInput,
        worker_name: str,
        reviewer_name: str,
        verify: bool,
        config: ProjectConfig,
        trace: TraceWriter,
    ) -> LoopStepProgram:
        repo_path = Path(brief_input.repo_path).resolve()
        previous_verdict: ReviewVerdict | None = None
        worker = self.worker_runner or make_runner(
            worker_name,
            options=config.runner.codex_exec.worker,
        )

        for iteration_number in range(1, state.max_iterations + 1):
            state.current_iteration = iteration_number
            state.current_step = "worker"
            state.save(run_dir / "state.json")
            iteration_dir = _iteration_dir(run_dir, iteration_number)
            prompt, prompt_sections = build_worker_prompt(
                brief_input,
                resolve_run_dir(self.workspace, state.brief_run or ""),
                previous_verdict,
                iteration_number,
            )
            _write_text_artifact(iteration_dir / "worker-prompt.md", prompt)
            prompt_metrics = measure_prompt(
                prompt,
                role="worker",
                max_chars=config.prompt_budget.worker_max_chars,
                sections=prompt_sections,
            )
            write_prompt_metrics(
                iteration_dir,
                "worker-prompt",
                prompt_metrics,
                write_text=_write_text_artifact,
            )
            trace.write(
                "worker_prompt_measured",
                iteration=iteration_number,
                metrics=prompt_metrics.model_dump(),
            )
            if prompt_metrics.exceeded:
                write_context_budget_report(iteration_dir, "worker", prompt_metrics)
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="skipped",
                    )
                )
                _write_final_report(
                    run_dir,
                    state,
                    previous_verdict,
                    "worker prompt 超过上下文预算，未启动外部 runner。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="worker_context_budget",
                )
                return run_dir
            workspace_baseline = cast(
                WorkspaceSnapshot,
                (yield LoopStepInstruction(
                    "capture_workspace",
                    CaptureWorkspaceStepRequest(repo_path=repo_path),
                )),
            )
            if not workspace_baseline.capture_complete or (
                iteration_number == 1 and workspace_baseline.has_tracked_changes
            ):
                workspace_check = cast(
                    WorkspaceCheckResult,
                    (yield LoopStepInstruction(
                        "reconcile_workspace",
                        WorkspaceReconcileStepRequest(
                            repo_path=repo_path,
                            output_dir=iteration_dir,
                            baseline=workspace_baseline,
                            allow_existing_tracked_diff=iteration_number > 1,
                        ),
                    )),
                )
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="skipped",
                        workspace_status="failed",
                        workspace_new_files_count=workspace_check.new_untracked_count,
                    )
                )
                if workspace_baseline.has_tracked_changes:
                    _write_text_artifact(
                        iteration_dir / "fix-prompt.md",
                        render_tracked_baseline_fix_prompt(iteration_number + 1),
                    )
                    conclusion = (
                        "worker 启动前已存在 tracked diff；为避免把历史修改归因于本轮 worker，"
                        "未启动自动执行。"
                    )
                    current_step = "workspace_baseline_dirty"
                else:
                    _write_text_artifact(
                        iteration_dir / "fix-prompt.md",
                        render_workspace_fix_prompt(iteration_number + 1),
                    )
                    conclusion = "worker 启动前无法完整捕获工作区基线，未启动自动执行。"
                    current_step = "workspace_baseline_unavailable"
                trace.write(
                    "workspace_baseline_blocked",
                    iteration=iteration_number,
                    capture_complete=workspace_baseline.capture_complete,
                    tracked_files=len(workspace_baseline.tracked_files),
                )
                _write_final_report(run_dir, state, previous_verdict, conclusion)
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step=current_step,
                )
                return run_dir
            trace.write("worker_started", iteration=iteration_number, runner=worker_name)
            worker_result = cast(
                RunnerResult,
                (yield LoopStepInstruction(
                    "execute_worker_epoch",
                    WorkerEpochStepRequest(
                        runner=worker,
                        prompt=prompt,
                        repo_path=repo_path,
                        sandbox="workspace-write",
                        timeout_seconds=self.timeout_seconds,
                        execution_context=RunnerExecutionContext(
                            execution_dir=iteration_dir / "executions" / "worker",
                            run_id=state.run_id,
                            step="worker",
                            iteration=iteration_number,
                        ),
                    ),
                )),
            )
            _write_text_artifact(
                iteration_dir / "worker-output.txt",
                worker_result.output or worker_result.error or "",
            )
            trace.write("worker_finished", iteration=iteration_number, status=worker_result.status)
            if worker_result.status in {"timed_out", "stopped"}:
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status=worker_result.status,
                    )
                )
                _write_execution_interruption_report(
                    iteration_dir,
                    step="worker",
                    status=worker_result.status,
                    reason=worker_result.error,
                )
                conclusion = (
                    "worker 单次执行超时，已停止后续验证和审查。"
                    if worker_result.status == "timed_out"
                    else "worker 已按 stop request 停止，已停止后续验证和审查。"
                )
                _write_final_report(run_dir, state, None, conclusion)
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step=worker_result.status,
                )
                return run_dir
            if worker_result.status != "success":
                workspace_check = cast(
                    WorkspaceCheckResult,
                    (yield LoopStepInstruction(
                        "reconcile_workspace",
                        WorkspaceReconcileStepRequest(
                            repo_path=repo_path,
                            output_dir=iteration_dir,
                            baseline=workspace_baseline,
                            allow_existing_tracked_diff=iteration_number > 1,
                        ),
                    )),
                )
                trace.write(
                    "workspace_check_finished",
                    iteration=iteration_number,
                    status=workspace_check.status,
                    new_untracked_count=workspace_check.new_untracked_count,
                    baseline_untracked_changed=workspace_check.baseline_untracked_changed,
                )
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="failed",
                        workspace_status=workspace_check.status,
                        workspace_new_files_count=workspace_check.new_untracked_count,
                    )
                )
                _write_runner_error_report(
                    iteration_dir,
                    step="worker",
                    reason=worker_result.error,
                    workspace_status=workspace_check.raw_status,
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker runner 异常退出，可能已留下部分改动；未继续验证和审查。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="worker_error",
                )
                return run_dir

            current_policy = project_policy_snapshot(repo_path)
            if _project_policy_changed(repo_path, state.project_policy_snapshot):
                _write_project_policy_change_report(
                    iteration_dir,
                    state.project_policy_snapshot,
                    current_policy,
                )
                trace.write("project_policy_changed", iteration=iteration_number)
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="success",
                    )
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker 修改了项目策略文件，已停止自动验证和审查。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="project_policy_changed",
                )
                return run_dir

            workspace_check = cast(
                WorkspaceCheckResult,
                (yield LoopStepInstruction(
                    "reconcile_workspace",
                    WorkspaceReconcileStepRequest(
                        repo_path=repo_path,
                        output_dir=iteration_dir,
                        baseline=workspace_baseline,
                        allow_existing_tracked_diff=iteration_number > 1,
                    ),
                )),
            )
            trace.write(
                "workspace_check_finished",
                iteration=iteration_number,
                status=workspace_check.status,
                new_untracked_count=workspace_check.new_untracked_count,
                baseline_untracked_changed=workspace_check.baseline_untracked_changed,
            )
            if workspace_check.has_failures:
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="success",
                        workspace_status="failed",
                        workspace_new_files_count=workspace_check.new_untracked_count,
                    )
                )
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_workspace_fix_prompt(iteration_number + 1),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker 结束后工作区污染检查失败，已停止自动验证和审查。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="workspace_check_failed",
                )
                return run_dir

            if workspace_check.new_untracked_count:
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="success",
                        workspace_status="failed",
                        workspace_new_files_count=workspace_check.new_untracked_count,
                    )
                )
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_untracked_files_fix_prompt(
                        iteration_number + 1,
                        workspace_check.new_untracked_files,
                    ),
                )
                trace.write(
                    "untracked_files_require_human",
                    iteration=iteration_number,
                    count=workspace_check.new_untracked_count,
                    paths=workspace_check.new_untracked_files,
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "worker 新增了未跟踪文件；reviewer 不读取其内容，已转人工确认。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="untracked_files",
                )
                return run_dir

            verification_log: Path | None = None
            verification_result_path: Path | None = None
            verification_summary_path: Path | None = None
            verification_status = "skipped"
            verification_failed_count = 0
            if verify:
                state.current_step = "verify"
                state.save(run_dir / "state.json")
                verification = cast(
                    VerificationRunResult,
                    (yield LoopStepInstruction(
                        "run_verification",
                        VerificationStepRequest(
                            workspace=self.workspace,
                            repo_path=repo_path,
                            output_dir=iteration_dir,
                        ),
                    )),
                )
                verification_log = verification.summary_path
                verification_result_path = verification.result_path
                verification_summary_path = verification.summary_path
                verification_status = _verification_status(verification.command_count, verification.failed_count)
                verification_failed_count = verification.failed_count
                trace.write(
                    "verification_finished",
                    iteration=iteration_number,
                    commands=verification.command_count,
                    failed=verification.failed_count,
                    interruption_status=verification.interruption_status,
                )
                if verification.was_interrupted:
                    current_step = self._prepare_verification_interruption(
                        run_dir,
                        state,
                        iteration_dir,
                        iteration_number,
                        verification,
                        trace,
                        worker_status="success",
                        workspace_status=workspace_check.status,
                        workspace_new_files_count=workspace_check.new_untracked_count,
                    )
                    yield self._finalize_instruction(
                        run_dir,
                        state,
                        "needs_human",
                        trace,
                        current_step=current_step,
                    )
                    return run_dir

            state.current_step = "reflect"
            state.save(run_dir / "state.json")
            reflect_run = cast(
                Path,
                (yield LoopStepInstruction(
                    "run_reflect",
                    ReflectStepRequest(
                        workspace=self.workspace,
                        repo_path=repo_path,
                        source_run=state.brief_run,
                        test_log=verification_log,
                        note=f"auto loop 第 {iteration_number} 轮执行后复盘",
                    ),
                )),
            )
            _record_reflect(iteration_dir, reflect_run)
            if not _reflect_run_succeeded(reflect_run):
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="success",
                        workspace_status=workspace_check.status,
                        workspace_new_files_count=workspace_check.new_untracked_count,
                        verification_status=verification_status,
                        verification_failed_count=verification_failed_count,
                        reflect_run=run_name(reflect_run),
                    )
                )
                _write_reflect_failure_report(iteration_dir, reflect_run)
                trace.write(
                    "reflect_failed",
                    iteration=iteration_number,
                    reflect_run=run_name(reflect_run),
                    status=_read_reflect_run_status(reflect_run),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "Reflect 的确定性证据检查失败，未启动隔离 reviewer。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="reflect_failed",
                )
                return run_dir
            if not _reflect_has_tracked_diff(reflect_run):
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="success",
                        workspace_status=workspace_check.status,
                        workspace_new_files_count=workspace_check.new_untracked_count,
                        verification_status=verification_status,
                        verification_failed_count=verification_failed_count,
                        reflect_run=run_name(reflect_run),
                    )
                )
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_no_diff_fix_prompt(iteration_number + 1),
                )
                trace.write("zero_diff_requires_human", iteration=iteration_number)
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "本轮没有可审查的 tracked diff，不能自动判定成功。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="no_diff",
                )
                return run_dir
            source_run = run_name(reflect_run)
            try:
                gate_result = cast(
                    GateResult,
                    (yield LoopStepInstruction(
                        "evaluate_risk",
                        RiskStepRequest(
                            workspace=self.workspace,
                            repo_path=repo_path,
                            source_run=source_run,
                        ),
                    )),
                )
            except Exception as exc:  # noqa: BLE001 - 风险判断错误必须 fail-closed
                gate_evidence = _record_loop_risk_gate_failure(
                    exc,
                    source_run,
                    iteration_dir,
                    trace,
                    iteration_number,
                )
            else:
                gate_evidence = _record_loop_risk_gate_success(
                    gate_result,
                    source_run,
                    iteration_dir,
                    trace,
                    iteration_number,
                )
            gate_result = gate_evidence.result
            if gate_evidence.error or gate_result is None:
                state.iterations.append(
                    LoopIterationState(
                        iteration=iteration_number,
                        worker_status="success",
                        workspace_status=workspace_check.status,
                        workspace_new_files_count=workspace_check.new_untracked_count,
                        verification_status=verification_status,
                        verification_failed_count=verification_failed_count,
                        reflect_run=run_name(reflect_run),
                        **_risk_gate_state_fields(gate_evidence),
                    )
                )
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_risk_gate_fix_prompt(iteration_number + 1, None),
                )
                _write_final_report(
                    run_dir,
                    state,
                    None,
                    "风险门禁评估失败，未启动隔离 reviewer。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="risk_gate_failed",
                )
                return run_dir
            if gate_result.recommendation == "human-review":
                state.current_step = "human_decision"
                state.save(run_dir / "state.json")
                human_decision = cast(
                    HumanDecisionStepResult,
                    (yield LoopStepInstruction(
                        "request_human_decision",
                        HumanDecisionStepRequest(
                            repo_path=repo_path,
                            iteration=iteration_number,
                            reflect_run=reflect_run,
                            verification_status=verification_status,
                            verification_failed_count=verification_failed_count,
                            verification_result_path=verification_result_path,
                            verification_summary_path=verification_summary_path,
                            risk_result_sha256=gate_evidence.result_sha256,
                            risk_report_sha256=gate_evidence.report_sha256,
                        ),
                    )),
                )
                trace.write(
                    "human_decision_finished",
                    iteration=iteration_number,
                    decision=human_decision.decision,
                    decision_id=human_decision.decision_id,
                )
                if human_decision.decision != "approved":
                    state.iterations.append(
                        LoopIterationState(
                            iteration=iteration_number,
                            worker_status="success",
                            workspace_status=workspace_check.status,
                            workspace_new_files_count=workspace_check.new_untracked_count,
                            verification_status=verification_status,
                            verification_failed_count=verification_failed_count,
                            reflect_run=run_name(reflect_run),
                            **_risk_gate_state_fields(gate_evidence),
                        )
                    )
                    _write_text_artifact(
                        iteration_dir / "fix-prompt.md",
                        render_risk_gate_fix_prompt(
                            iteration_number + 1,
                            gate_result,
                        ),
                    )
                    conclusion = (
                        "人工已拒绝风险门禁批准，未继续自动隔离审查。"
                        if human_decision.decision == "rejected"
                        else "风险门禁要求人工确认，未继续自动隔离审查。"
                    )
                    _write_final_report(
                        run_dir,
                        state,
                        None,
                        conclusion,
                    )
                    yield self._finalize_instruction(
                        run_dir,
                        state,
                        "needs_human",
                        trace,
                        current_step=(
                            "risk_gate_rejected"
                            if human_decision.decision == "rejected"
                            else "risk_gate_needs_human"
                        ),
                    )
                    return run_dir
                human_approval_ref = human_decision.consumption_ref
            else:
                human_approval_ref = None
            state.current_step = "review"
            state.save(run_dir / "state.json")
            review_run = cast(
                Path,
                (yield LoopStepInstruction(
                    "dispatch_review",
                    ReviewStepRequest(
                        workspace=self.workspace,
                        repo_path=repo_path,
                        reflect_run=reflect_run,
                        reviewer_name=reviewer_name,
                        loop_run_dir=run_dir,
                        iteration=iteration_number,
                        config=config,
                        human_approval_ref=human_approval_ref,
                    ),
                )),
            )
            verdict = _read_verdict(review_run)
            reviewer_status = _read_review_runner_status(review_run)
            review_run_status = _read_review_run_status(review_run)
            _record_review(iteration_dir, review_run)
            iteration = LoopIterationState(
                iteration=iteration_number,
                worker_status="success",
                reviewer_status=reviewer_status,
                workspace_status=workspace_check.status,
                workspace_new_files_count=workspace_check.new_untracked_count,
                verification_status=verification_status,
                verification_failed_count=verification_failed_count,
                reflect_run=run_name(reflect_run),
                **_risk_gate_state_fields(gate_evidence),
                review_run=run_name(review_run),
                verdict=verdict.verdict,
                findings_count=len(verdict.findings),
            )
            state.iterations.append(iteration)
            previous_verdict = verdict
            if not _review_run_allows_verdict(review_run_status, verdict):
                trace.write(
                    "review_run_not_successful",
                    iteration=iteration_number,
                    status=review_run_status,
                )
                _write_final_report(
                    run_dir,
                    state,
                    verdict,
                    f"Review run 自身状态为 {review_run_status}，不能采用其 verdict。",
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    "needs_human",
                    trace,
                    current_step="review_run_failed",
                )
                return run_dir
            if verdict.verdict in {"approve", "needs_human"}:
                status, current_step = self._prepare_finish_or_next(
                    run_dir,
                    state,
                    verdict,
                    iteration_dir,
                )
                yield self._finalize_instruction(
                    run_dir,
                    state,
                    status,
                    trace,
                    current_step=current_step,
                )
                return run_dir
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_fix_prompt(verdict, iteration_number + 1),
            )
            trace.write("fix_prompt_written", iteration=iteration_number)

        _write_final_report(run_dir, state, previous_verdict, "达到最大自动迭代轮数，需要人工接管。")
        yield self._finalize_instruction(
            run_dir,
            state,
            "needs_human",
            trace,
        )
        return run_dir

    def _pause_for_verification_interruption(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_number: int,
        verification: VerificationRunResult,
        trace: TraceWriter,
        *,
        worker_status: Literal["skipped", "success"],
        workspace_status: Literal["skipped", "passed", "failed"] = "skipped",
        workspace_new_files_count: int = 0,
    ) -> None:
        current_step = self._prepare_verification_interruption(
            run_dir,
            state,
            iteration_dir,
            iteration_number,
            verification,
            trace,
            worker_status=worker_status,
            workspace_status=workspace_status,
            workspace_new_files_count=workspace_new_files_count,
        )
        self._save_loop_done(
            run_dir,
            state,
            "needs_human",
            trace,
            current_step=current_step,
        )

    def _prepare_verification_interruption(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        iteration_dir: Path,
        iteration_number: int,
        verification: VerificationRunResult,
        trace: TraceWriter,
        *,
        worker_status: Literal["skipped", "success"],
        workspace_status: Literal["skipped", "passed", "failed"] = "skipped",
        workspace_new_files_count: int = 0,
    ) -> str:
        interruption_status = verification.interruption_status
        if interruption_status is None:
            raise ValueError("verification interruption 缺少中断状态")

        _copy_if_exists(verification.summary_path, iteration_dir / "test-summary.md")
        state.current_iteration = iteration_number
        state.iterations.append(
            LoopIterationState(
                iteration=iteration_number,
                worker_status=worker_status,
                workspace_status=workspace_status,
                workspace_new_files_count=workspace_new_files_count,
                verification_status="failed",
                verification_failed_count=verification.failed_count,
            )
        )
        trace.write(
            "verification_interrupted",
            iteration=iteration_number,
            status=interruption_status,
            command=verification.interruption_command,
            reason=verification.interruption_reason,
        )
        _write_verification_interruption_report(iteration_dir, verification)
        conclusion = {
            "timed_out": "verification 命令超时，已停止剩余验证、reflect 和 review。",
            "stopped": "verification 已按 stop request 停止，未继续 reflect 或 review。",
            "termination-unconfirmed": (
                "verification owned process tree 终止未确认，已保留现场并停止 reflect 和 review。"
            ),
        }[interruption_status]
        _write_final_report(run_dir, state, None, conclusion)
        current_step = {
            "timed_out": "timed_out",
            "stopped": "stopped",
            "termination-unconfirmed": "verification_termination_unconfirmed",
        }[interruption_status]
        return current_step

    def _finish_or_prepare_next(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        verdict: ReviewVerdict,
        iteration_dir: Path,
        trace: TraceWriter,
    ) -> None:
        status, current_step = self._prepare_finish_or_next(
            run_dir,
            state,
            verdict,
            iteration_dir,
        )
        self._save_loop_done(
            run_dir,
            state,
            status,
            trace,
            current_step=current_step,
        )

    def _prepare_finish_or_next(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        verdict: ReviewVerdict,
        iteration_dir: Path,
    ) -> tuple[Literal["success", "needs_human"], str]:
        if verdict.verdict == "approve":
            if _latest_verification_failed(state):
                _write_text_artifact(
                    iteration_dir / "fix-prompt.md",
                    render_verification_fix_prompt(state.current_iteration),
                )
                _write_final_report(run_dir, state, verdict, "验证命令失败，不能自动通过。")
                return "needs_human", "done"
            _write_final_report(run_dir, state, verdict, "隔离 reviewer 已通过。")
            return "success", "done"
        if verdict.verdict == "request_changes":
            _write_text_artifact(
                iteration_dir / "fix-prompt.md",
                render_fix_prompt(verdict, state.current_iteration + 1),
            )
            _write_final_report(run_dir, state, verdict, "reviewer 要求修改，已生成 fix-prompt.md。")
            return "needs_human", "done"
        latest = state.iterations[-1] if state.iterations else None
        if latest and latest.reviewer_status == "timed_out":
            conclusion = "reviewer 单次执行超时，需要人工判断或重新审查。"
        elif latest and latest.reviewer_status == "stopped":
            conclusion = "reviewer 已按 stop request 停止，需要人工判断后续动作。"
        elif latest and latest.reviewer_status == "error":
            conclusion = "reviewer runner 异常退出，未产生可信审查结论。"
        else:
            conclusion = "reviewer 需要人工判断。"
        _write_final_report(run_dir, state, verdict, conclusion)
        if latest and latest.reviewer_status == "error":
            interruption_step = "reviewer_error"
        elif latest and latest.reviewer_status in {"timed_out", "stopped"}:
            interruption_step = latest.reviewer_status
        else:
            interruption_step = "done"
        return "needs_human", interruption_step

    def _save_loop_done(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        status: Literal["success", "failed", "needs_human"],
        trace: TraceWriter,
        current_step: str = "done",
    ) -> None:
        self.step_services.finalize_run(
            FinalizeRunStepRequest(
                run_dir=run_dir,
                state=state,
                status=status,
                trace=trace,
                current_step=current_step,
            )
        )

    def _persist_loop_done(
        self,
        run_dir: Path,
        state: LoopAutomationState,
        status: Literal["success", "failed", "needs_human"],
        trace: TraceWriter,
        current_step: str = "done",
    ) -> None:
        required_artifacts = (
            FINAL_LOOP_ARTIFACTS
            if (run_dir / "final-report.md").exists()
            else LOOP_ARTIFACTS
        )
        artifacts = list(dict.fromkeys([*required_artifacts, *state.artifacts]))
        state.current_step = current_step
        state.status = status
        state.artifacts = artifacts
        _finalize_loop_eval(run_dir, state, status, artifacts, trace)


def render_loop_plan(
    brief_input: BriefInput,
    automation_mode: str,
    max_iterations: int,
) -> str:
    return "\n".join(
        [
            "# Loop Plan",
            "",
            f"- 任务类型：`{brief_input.mode}`",
            f"- 自动化模式：`{automation_mode}`",
            f"- 最大迭代轮数：`{max_iterations}`",
            "",
            "## 流程",
            "",
            "1. 生成 agent brief，编译 AGENTS.md 和已接受 memory。",
            "2. 生成 project-context.md，稳定注入项目画像、验证命令、AGENTS.md 和 accepted memory。",
            "3. auto 模式先确认启动前不存在 tracked diff，再启动 worker。",
            "4. worker 结束后检查工作区污染，超过预算则停止并交给人工判断。",
            "5. 自动执行项目画像识别出的最小验证命令。",
            "6. Reflect 收集当前 diff、验证日志和复盘材料；其确定性检查失败时停止。",
            "7. 运行风险/变更预算门禁；需要人工确认时不启动自动 reviewer。",
            "8. 隔离 reviewer 使用只读 runner 审查 review-pack。",
            "9. approve 则生成 final-report；request_changes 则生成 fix-prompt。",
            "",
            "## 禁止动作",
            "",
            "- 不自动 commit / push / release。",
            "- 不自动接受 memory proposal。",
            "- reviewer 只读，不修改目标仓库。",
        ]
    ).rstrip() + "\n"


def build_worker_prompt(
    brief_input: BriefInput,
    brief_run: Path,
    previous_verdict: ReviewVerdict | None,
    iteration: int,
) -> tuple[str, dict[str, str]]:
    brief_text = redact_text(
        brief_run.joinpath("agent-brief.md").read_text(encoding="utf-8", errors="replace")
    )
    project_context = redact_text(_read_optional_text(brief_run / "project-context.md"))
    lines = [
        "# Worker Prompt",
        "",
        f"你是第 {iteration} 轮 worker，请基于 agent brief 完成最小必要修改。",
        "",
        "硬性约束：",
        "- 只修改满足需求所需的文件。",
        "- 不要 git commit、git push、发布或改长期 memory。",
        "- 修改后尽量运行项目画像建议的最小验证命令，并在输出里总结结果。",
        "- 如果需求或环境阻塞，停止并明确说明。",
        "",
        "## 项目上下文",
        "",
        project_context or "- 未找到 project-context.md，请基于仓库现状保守判断项目规范。",
        "",
        "## Agent Brief",
        "",
        brief_text,
    ]
    previous_findings = ""
    if previous_verdict:
        previous_findings = render_fix_prompt(previous_verdict, iteration)
        lines.extend(["", "## 上一轮 Review Findings", "", previous_findings])
    prompt = redact_text("\n".join(lines).rstrip() + "\n")
    return prompt, {
        "project_context": project_context,
        "agent_brief": brief_text,
        "previous_findings": previous_findings,
    }


def render_worker_prompt(
    brief_input: BriefInput,
    brief_run: Path,
    previous_verdict: ReviewVerdict | None,
    iteration: int,
) -> str:
    return build_worker_prompt(brief_input, brief_run, previous_verdict, iteration)[0]


def render_fix_prompt(verdict: ReviewVerdict, next_iteration: int) -> str:
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        f"- reviewer 结论：`{verdict.verdict}`",
        f"- 摘要：{verdict.summary}",
        "",
        "请只修复以下被 reviewer 标出的具体问题，不要扩大范围：",
        "",
    ]
    if verdict.findings:
        for finding in verdict.findings:
            location = f"{finding.file}:{finding.line}" if finding.file else "未指定位置"
            lines.extend(
                [
                    f"- [{finding.severity}] {finding.title}",
                    f"  - 位置：`{location}`",
                    f"  - 证据：{finding.evidence or '未提供'}",
                    f"  - 建议：{finding.recommendation or '未提供'}",
                ]
            )
    else:
        lines.append("- reviewer 未给出具体 finding；请人工判断是否继续。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_verification_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：自动验证命令失败。",
            "",
            "请优先读取本轮 `verification-summary.md` 和 `test-summary.md`，只修复导致验证失败的具体问题。",
            "验证重新通过前，Vega 不会把 reviewer approve 视为可交付状态。",
        ]
    ).rstrip() + "\n"


def render_workspace_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：worker 结束后工作区污染检查失败。",
            "",
            "请先读取本轮 `workspace-check.md`：",
            "- 确认新增未跟踪文件哪些是需求必须产物，哪些是 worker 误生成的临时/噪声文件。",
            "- 手动清理无关文件，或把真实需要新增的文件纳入明确 scope 后再继续。",
            "- Vega 不会自动删除文件，也不会自动 kill 外部进程。",
            "",
            "清理或确认完成后，再运行 `vega loop continue --repo <repo> --run <run>` 继续复盘与审查。",
        ]
    ).rstrip() + "\n"


def render_tracked_baseline_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：启动 auto worker 前已存在 tracked diff。",
            "",
            "Vega 无法可靠区分这些历史修改与本轮 worker 的产物，因此没有启动 worker。",
            "请先人工检查、提交、暂存外部变更到其他分支或恢复无关 diff；确认工作区干净后再重开 auto loop。",
            "如果这些变更本来就是主会话/人工完成的实现，请使用 `vega loop continue` 或独立 reflect/review 流程，",
            "不要把它们归因给新的 auto worker。",
        ]
    ).rstrip() + "\n"


def render_untracked_files_fix_prompt(next_iteration: int, paths: list[str]) -> str:
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        "- 阻塞原因：当前工作区存在未跟踪文件，隔离 reviewer 不会读取其内容。",
        "",
        "## 仅路径清单",
        "",
    ]
    lines.extend(f"- `{path}`" for path in paths)
    lines.extend(
        [
            "",
            "请人工确认这些路径属于本次实现，并将需要审查的实现纳入 tracked diff 后再继续。",
            "Vega 不会读取、删除、暂存或提交这些未跟踪文件。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_risk_gate_fix_prompt(next_iteration: int, result: GateResult | None) -> str:
    lines = [
        "# Fix Prompt",
        "",
        f"- 下一轮：`{next_iteration}`",
        "- 阻塞原因：风险门禁未允许继续自动隔离审查。",
        "",
        "请先阅读本轮 `risk-gate-report.md` 和 `risk-gate-result.json`，再决定是否缩小范围、补测试、",
        "拆分变更或由人工完成审查。Vega 不会自动忽略预算、风险路径或项目要求。",
    ]
    if result:
        lines.extend(
            [
                "",
                "## 门禁结果",
                "",
                f"- 风险：`{result.risk}`",
                f"- 建议：`{result.recommendation}`",
                "- 原因：" + "、".join(reason.code for reason in result.reasons),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_no_diff_fix_prompt(next_iteration: int) -> str:
    return "\n".join(
        [
            "# Fix Prompt",
            "",
            f"- 下一轮：`{next_iteration}`",
            "- 阻塞原因：本轮没有可审查的 tracked diff。",
            "",
            "请确认 worker 是否实际完成了任务；产生明确 diff 后再继续 verification 和隔离 reviewer。",
        ]
    ).rstrip() + "\n"


def run_loop_eval(
    run_dir: Path,
    artifacts: list[str],
    *,
    require_terminal: bool = True,
    status_for_eval: str | None = None,
) -> list[str]:
    results = [
        f"{'PASS' if (run_dir / item).exists() else 'FAIL'}: artifact 存在：{item}"
        for item in artifacts
    ]
    state_path = run_dir / "state.json"
    if not state_path.exists():
        results.append("FAIL: loop state 不存在")
        return results
    try:
        state = LoopAutomationState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - eval 必须把损坏证据转成 FAIL
        results.append(f"FAIL: loop state schema 不合法：{type(exc).__name__}")
        return results
    results.append("PASS: loop state schema 合法")

    missing_declared = sorted(set(artifacts) - set(state.artifacts))
    if missing_declared:
        results.append(
            "FAIL: state.artifacts 未声明必需 artifact：" + ", ".join(missing_declared)
        )
    else:
        results.append("PASS: state.artifacts 与必需 artifact 一致")

    effective_status = status_for_eval or state.status
    if require_terminal:
        results.extend(
            _loop_terminal_eval_results(
                run_dir,
                state.status,
                state.engine,
            )
        )
        if state.engine == "langgraph" and state.status == "success":
            try:
                read_graph_state(run_dir)
            except GraphStateValidationError as exc:
                results.append(
                    "FAIL: LangGraph success 的 Graph State 终态证据不可信："
                    f"{type(exc).__name__}"
                )
            else:
                results.append(
                    "PASS: LangGraph success 的 Graph State 终态证据可信"
                )

    if effective_status == "success" and not state.iterations:
        results.append("FAIL: success loop 至少需要一轮 iteration")
        return results
    if effective_status == "success":
        results.append("PASS: success loop 包含 iteration")

    if not state.iterations:
        return results

    workspace = run_dir.parent.parent
    repo_path = Path(state.repo_path)
    latest_iteration = state.iterations[-1].iteration
    for expected_iteration, iteration in enumerate(state.iterations, start=1):
        if iteration.iteration != expected_iteration:
            results.append(
                "FAIL: iteration 编号不连续："
                f"期望 {expected_iteration:02d}，实际 {iteration.iteration:02d}"
            )
        iteration_dir = run_dir / "iterations" / f"{iteration.iteration:02d}"
        results.extend(
            _loop_iteration_evidence_checks(
                iteration_dir,
                iteration,
                # 前序 Reflect 已被后续 worker 合法改变时，使用终态工作区重算会把
                # 历史证据误判为失效。只对最终 iteration 重算；前序 iteration 仍校验
                # result/report/state/trace 的绑定，终态自动结论仍无法靠同步降级放行。
                workspace=workspace if iteration.iteration == latest_iteration else None,
                repo_path=repo_path if iteration.iteration == latest_iteration else None,
                trace_path=run_dir / "trace.jsonl",
            )
        )
    if state.current_iteration != state.iterations[-1].iteration:
        results.append("FAIL: state.current_iteration 与最后一轮 iteration 不一致")

    if effective_status == "success":
        latest = state.iterations[-1]
        iteration_dir = run_dir / "iterations" / f"{latest.iteration:02d}"
        if latest.verdict != "approve":
            results.append("FAIL: success loop 的最新 verdict 必须为 approve")
        else:
            results.append("PASS: success loop 的最新 verdict 为 approve")
        if latest.reviewer_status != "success":
            results.append("FAIL: success loop 缺少成功的 reviewer 执行")
        else:
            results.append("PASS: success loop reviewer 执行成功")
        if latest.workspace_new_files_count:
            results.append("FAIL: success loop 仍包含新增未跟踪文件")
        if latest.verification_status == "failed" or latest.verification_failed_count:
            results.append("FAIL: success loop 不能包含失败的 verification")
        if "final-report.md" not in state.artifacts:
            results.append("FAIL: success loop 未声明 final-report.md")
        if not (iteration_dir / "diff-summary.md").exists():
            results.append("FAIL: success loop 缺少 diff-summary.md")
        elif "当前没有工作区 diff 文件" in _read_optional_text(
            iteration_dir / "diff-summary.md"
        ):
            results.append("FAIL: success loop 没有可审查的 tracked diff")
    return results


def _loop_trace_checks(
    run_dir: Path,
    engine: str,
) -> tuple[list[str], str | None]:
    trace_path = run_dir / "trace.jsonl"
    if not trace_path.exists():
        return ["FAIL: trace.jsonl 不存在"], None
    try:
        items = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        return [f"FAIL: trace.jsonl 不是合法 JSONL：{exc.msg}"], None
    if not items:
        return ["FAIL: trace.jsonl 为空"], None
    terminal_indices = [
        index
        for index, item in enumerate(items)
        if isinstance(item, dict) and item.get("event") == "run_finished"
    ]
    if not terminal_indices:
        return ["FAIL: trace.jsonl 缺少 run_finished 终态事件"], None
    if len(terminal_indices) != 1:
        return ["FAIL: trace.jsonl 必须且只能包含一个 run_finished 终态事件"], None
    terminal_index = terminal_indices[0]
    status = items[terminal_index].get("status")
    if not isinstance(status, str):
        return ["FAIL: run_finished 终态缺少 status"], None
    if terminal_index == len(items) - 1:
        return ["PASS: trace.jsonl 非空且包含 run_finished 终态事件"], status
    if terminal_index < len(items) - 1 and engine == "langgraph":
        suffix = items[terminal_index + 1 :]
        completion = (
            suffix[-1]
            if isinstance(suffix[-1], dict)
            and suffix[-1].get("event") == RUN_TERMINAL_REVOKED_EVENT
            else None
        )
        evidence_end = -1 if completion is not None else len(suffix)
        evidence_items = suffix[:evidence_end]
        state_revocation = (
            evidence_items[-1]
            if evidence_items
            and isinstance(evidence_items[-1], dict)
            and evidence_items[-1].get("event")
            == RUN_TERMINAL_STATE_REVOKED_EVENT
            else None
        )
        revocation = completion or state_revocation
        diagnostic_events = (
            evidence_items[:-1]
            if state_revocation is not None
            else evidence_items
        )
        if (
            isinstance(revocation, dict)
            and revocation.get("previous_status") == status
            and revocation.get("status") == "needs_human"
        ):
            reason = revocation.get("reason")
            events_consistent = (
                completion is None
                or state_revocation is None
                or (
                    state_revocation.get("reason") == reason
                    and state_revocation.get("previous_status") == status
                    and state_revocation.get("status") == "needs_human"
                    and completion.get("reason") == reason
                    and completion.get("previous_status") == status
                    and completion.get("status") == "needs_human"
                )
            )
            valid_diagnostics = (
                reason == "graph_evidence_failed"
                and not diagnostic_events
            ) or (
                reason == "checkpoint_validation_failed"
                and len(diagnostic_events) == 1
                and isinstance(diagnostic_events[0], dict)
                and diagnostic_events[0].get("event")
                == "graph_checkpoint_validation_failed"
                and diagnostic_events[0].get("original_status") == status
            )
            if events_consistent and valid_diagnostics:
                return [
                    "PASS: run_finished 终态已由 append-only 撤销事实明确撤销"
                ], "needs_human"
    return ["FAIL: run_finished 必须是 trace.jsonl 最后一条事件"], None


def _loop_terminal_eval_results(
    run_dir: Path,
    state_status: str,
    engine: str,
) -> list[str]:
    results, terminal_status = _loop_trace_checks(run_dir, engine)
    if terminal_status and terminal_status != state_status:
        results.append(
            f"FAIL: trace 终态与 state.status 不一致：{terminal_status} != {state_status}"
        )
    elif terminal_status:
        results.append("PASS: trace 终态与 state.status 一致")
    return results


def _loop_iteration_evidence_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    workspace: Path | None = None,
    repo_path: Path | None = None,
    trace_path: Path | None = None,
) -> list[str]:
    results: list[str] = []
    if not iteration_dir.exists():
        return [f"FAIL: iteration artifact 目录不存在：{iteration.iteration:02d}"]

    if iteration.reflect_run:
        reflect_ref = _read_optional_text(iteration_dir / "reflect-run.txt").strip()
        if not reflect_ref:
            results.append("FAIL: iteration 声明 reflect_run 但缺少 reflect-run.txt")
        elif Path(reflect_ref).name != iteration.reflect_run:
            results.append("FAIL: reflect-run.txt 与 iteration.reflect_run 不一致")

    if iteration.verification_failed_count and iteration.verification_status != "failed":
        results.append("FAIL: verification 失败计数与状态不一致")
    if iteration.verification_status in {"passed", "failed"}:
        if not (iteration_dir / "verification-summary.md").exists():
            results.append("FAIL: verification 状态已记录但缺少 verification-summary.md")
        if not (iteration_dir / "test-summary.md").exists():
            results.append("FAIL: verification 状态已记录但缺少 test-summary.md")

    gate_integrity = validate_iteration_risk_gate_artifacts(
        iteration_dir,
        iteration,
        workspace=workspace,
        repo_path=repo_path,
        trace_path=trace_path,
    )
    if gate_integrity.valid and iteration.risk_gate_status != "skipped":
        results.append("PASS: risk gate artifact 与 iteration 一致")
    else:
        results.extend(f"FAIL: {issue}" for issue in gate_integrity.issues)

    if iteration.verdict is None:
        return results
    verdict_path = iteration_dir / "review-verdict.json"
    if not verdict_path.exists():
        results.append("FAIL: iteration 声明 verdict 但缺少 review-verdict.json")
        return results
    try:
        verdict = ReviewVerdict.model_validate_json(
            verdict_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - eval 必须报告损坏 verdict
        results.append(f"FAIL: review-verdict.json 不合法：{type(exc).__name__}")
        return results
    if verdict.verdict != iteration.verdict:
        results.append("FAIL: review-verdict.json 与 iteration.verdict 不一致")
    else:
        results.append("PASS: reviewer verdict artifact 与 iteration 一致")

    context_path = iteration_dir / "review-context.json"
    if not context_path.exists():
        results.append("FAIL: iteration 声明 verdict 但缺少 review-context.json")
        return results
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        results.append(f"FAIL: review-context.json 不合法：{exc.msg}")
        return results
    if iteration.reflect_run and context.get("source_run") != iteration.reflect_run:
        results.append("FAIL: review-context.json 与 iteration.reflect_run 不一致")
    if isinstance(context.get("acceptance_evidence"), dict):
        results.extend(
            _validate_iteration_acceptance_evidence(
                iteration_dir,
                context["acceptance_evidence"],
            )
        )
    return results


def _validate_iteration_acceptance_evidence(
    iteration_dir: Path,
    expected_manifest: dict[str, object],
) -> list[str]:
    markdown_path = iteration_dir / "acceptance-evidence.md"
    json_path = iteration_dir / "acceptance-evidence.json"
    if not markdown_path.is_file() or not json_path.is_file():
        return ["FAIL: review 声明 acceptance evidence 但 iteration 副本不完整"]
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["FAIL: iteration acceptance-evidence.json 不合法"]
    if not isinstance(payload, dict):
        return ["FAIL: iteration acceptance-evidence.json 顶层必须是 object"]
    items = payload.get("items")
    if not isinstance(items, list):
        return ["FAIL: iteration acceptance evidence items 不合法"]
    actual_manifest = {
        **{
            key: value
            for key, value in payload.items()
            if key != "items"
        },
        "items": [
            {
                key: value
                for key, value in item.items()
                if key != "content"
            }
            if isinstance(item, dict)
            else item
            for item in items
        ],
    }
    if actual_manifest != expected_manifest:
        return ["FAIL: iteration acceptance evidence 与 review-context 不一致"]
    used_chars = payload.get("used_chars")
    max_chars = payload.get("max_chars")
    if (
        not isinstance(used_chars, int)
        or not isinstance(max_chars, int)
        or used_chars < 0
        or used_chars > max_chars
    ):
        return ["FAIL: iteration acceptance evidence 预算字段不合法"]
    calculated_chars = 0
    for item in items:
        if not isinstance(item, dict):
            return ["FAIL: iteration acceptance evidence item 不合法"]
        content = item.get("content")
        included_chars = item.get("included_chars")
        included_sha256 = item.get("included_sha256")
        if (
            not isinstance(content, str)
            or not isinstance(included_chars, int)
            or included_chars != len(content)
            or included_sha256 != sha256_text(content)
        ):
            return ["FAIL: iteration acceptance evidence 内容哈希不合法"]
        calculated_chars += included_chars
    if calculated_chars != used_chars:
        return ["FAIL: iteration acceptance evidence used_chars 不一致"]
    return ["PASS: iteration acceptance evidence 与 review-context 一致"]


def render_eval(results: list[str]) -> str:
    return redact_text("# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n")


def _iteration_dir(run_dir: Path, iteration: int) -> Path:
    path = run_dir / "iterations" / f"{iteration:02d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_reflect(iteration_dir: Path, reflect_run: Path) -> None:
    _write_text_artifact(iteration_dir / "reflect-run.txt", str(reflect_run.resolve()) + "\n")
    for name in ["diff-summary.md", "test-summary.md", "project-context.md", "reflection.md"]:
        _copy_if_exists(reflect_run / name, iteration_dir / name)


def _read_reflect_run_status(reflect_run: Path) -> str:
    try:
        state = json.loads(reflect_run.joinpath("state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "failed"
    status = state.get("status")
    return status if isinstance(status, str) else "failed"


def _reflect_run_succeeded(reflect_run: Path) -> bool:
    return _read_reflect_run_status(reflect_run) == "success"


def _write_reflect_failure_report(iteration_dir: Path, reflect_run: Path) -> Path:
    path = iteration_dir / "reflect-failure.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Reflect Failure Report",
                "",
                f"- reflect run：`{run_name(reflect_run)}`",
                f"- 状态：`{_read_reflect_run_status(reflect_run)}`",
                "",
                "## 结论",
                "",
                "- Reflect 的确定性证据检查未通过，当前 patch 不能作为可信 reviewer 输入。",
                "- Vega 未启动隔离 reviewer，也不会把任何 approve 视为成功。",
                "- 请先查看对应 reflect run 的 `eval.md`、`diff-summary.md` 和 `test-summary.md`。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _evaluate_loop_risk_gate(
    evaluate_risk_step: Callable[[RiskStepRequest], GateResult],
    workspace: Path,
    repo_path: Path,
    reflect_run: Path,
    iteration_dir: Path,
    trace: TraceWriter,
    iteration: int,
) -> LoopRiskGateEvidence:
    """在 loop 内复用 Gate 的确定性策略，但不隐式创建独立 gate run。

    日常 auto/continue 需要在 success 前强制执行同一套预算和风险规则；独立
    `vega gate` 仍保留给人工排障和单独观察。这里的本地 artifact 绑定当前
    iteration，避免把高风险结果只留在内存中。
    """
    source_run = run_name(reflect_run)
    try:
        result = evaluate_risk_step(
            RiskStepRequest(
                workspace=workspace,
                repo_path=repo_path,
                source_run=source_run,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 风险判断错误必须 fail-closed
        return _record_loop_risk_gate_failure(
            exc,
            source_run,
            iteration_dir,
            trace,
            iteration,
        )
    return _record_loop_risk_gate_success(
        result,
        source_run,
        iteration_dir,
        trace,
        iteration,
    )


def _record_loop_risk_gate_failure(
    error: Exception,
    source_run: str,
    iteration_dir: Path,
    trace: TraceWriter,
    iteration: int,
) -> LoopRiskGateEvidence:
    result_path = iteration_dir / RISK_GATE_RESULT_ARTIFACT
    report_path = iteration_dir / RISK_GATE_REPORT_ARTIFACT
    diagnostic = redact_text(f"{type(error).__name__}: {error}")[:1000]
    payload = {
        "schema_version": 1,
        "status": "failed",
        "iteration": iteration,
        "source_run": source_run,
        "code": "risk_evaluation_failed",
        "message": "风险门禁评估失败，未生成可信自动放行结论。",
        "diagnostic": diagnostic,
    }
    result_text = json.dumps(
        redact_value(payload),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _write_text_artifact(result_path, result_text)
    result_sha256 = sha256_text(result_text)
    report_body = redact_text(
        "\n".join(
            [
                "# 本轮风险门禁报告",
                "",
                f"- source reflect：`{source_run}`",
                f"- iteration：`{iteration:02d}`",
                "- 状态：`failed`",
                "",
                "## 结论",
                "",
                "- 风险门禁未能完成评估，Vega 不会继续自动隔离审查。",
                f"- 诊断：{diagnostic or '未提供'}",
            ]
        ).rstrip()
        + "\n"
    )
    report_text = (
        report_body.rstrip()
        + "\n\n"
        + render_risk_gate_report_binding(
            status="failed",
            iteration=iteration,
            source_run=source_run,
            result_sha256=result_sha256,
            risk=None,
            recommendation=None,
        )
    )
    _write_text_artifact(report_path, report_text)
    report_sha256 = sha256_text(report_text)
    trace.write(
        "risk_gate_failed",
        iteration=iteration,
        source_run=source_run,
        status="failed",
        result_sha256=result_sha256,
        report_sha256=report_sha256,
        diagnostic=diagnostic,
    )
    return LoopRiskGateEvidence(
        result=None,
        error=diagnostic or "risk_evaluation_failed",
        source_run=source_run,
        result_sha256=result_sha256,
        report_sha256=report_sha256,
    )


def _record_loop_risk_gate_success(
    result: GateResult,
    source_run: str,
    iteration_dir: Path,
    trace: TraceWriter,
    iteration: int,
) -> LoopRiskGateEvidence:
    result_path = iteration_dir / RISK_GATE_RESULT_ARTIFACT
    report_path = iteration_dir / RISK_GATE_REPORT_ARTIFACT
    payload = {
        "schema_version": 1,
        "status": "success",
        "iteration": iteration,
        "source_run": source_run,
        **result.model_dump(mode="json"),
    }
    result_text = json.dumps(
        redact_value(payload),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _write_text_artifact(result_path, result_text)
    result_sha256 = sha256_text(result_text)
    report_body = redact_text(
        render_gate_report(result)
        + "\n"
        + "\n".join(
            [
                "## 本轮关联",
                "",
                f"- source reflect：`{source_run}`",
                f"- iteration：`{iteration:02d}`",
                "- 此结果由 loop 在启动隔离 reviewer 前生成。",
            ]
        )
        + "\n"
    )
    report_text = (
        report_body.rstrip()
        + "\n\n"
        + render_risk_gate_report_binding(
            status="success",
            iteration=iteration,
            source_run=source_run,
            result_sha256=result_sha256,
            risk=result.risk,
            recommendation=result.recommendation,
        )
    )
    _write_text_artifact(report_path, report_text)
    report_sha256 = sha256_text(report_text)
    trace.write(
        "risk_gate_finished",
        iteration=iteration,
        source_run=source_run,
        status="success",
        risk=result.risk,
        recommendation=result.recommendation,
        reasons=[reason.code for reason in result.reasons],
        result_sha256=result_sha256,
        report_sha256=report_sha256,
    )
    return LoopRiskGateEvidence(
        result=result,
        error=None,
        source_run=source_run,
        result_sha256=result_sha256,
        report_sha256=report_sha256,
    )


def _risk_gate_state_fields(evidence: LoopRiskGateEvidence) -> dict[str, object]:
    result = evidence.result
    return {
        "risk_gate_status": evidence.status,
        "risk_gate_source_run": evidence.source_run,
        "risk_gate_risk": result.risk if result else None,
        "risk_gate_recommendation": result.recommendation if result else None,
        "risk_gate_result_sha256": evidence.result_sha256,
        "risk_gate_report_sha256": evidence.report_sha256,
    }


def _record_review(iteration_dir: Path, review_run: Path) -> None:
    for name in [
        "review-pack.md",
        "review-prompt.md",
        "review-checklist.md",
        "review-context.json",
        "acceptance-evidence.md",
        "acceptance-evidence.json",
        "review-prompt-metrics.json",
        "review-prompt-metrics.md",
        "project-context.md",
        "review-verdict.json",
        "review-findings.md",
        "review-runner-output.txt",
        "timeout-report.md",
        "stop-report.md",
        "runner-error-report.md",
        "review-context-budget-report.md",
    ]:
        _copy_if_exists(review_run / name, iteration_dir / name)
    _copy_if_exists(review_run / "state.json", iteration_dir / "review-state.json")
    _copy_if_exists(review_run / "eval.md", iteration_dir / "review-eval.md")


def _read_verdict(review_run: Path) -> ReviewVerdict:
    return ReviewVerdict.model_validate_json(
        review_run.joinpath("review-verdict.json").read_text(encoding="utf-8")
    )


def _read_review_runner_status(review_run: Path) -> RunnerStatus:
    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    status = state.get("runner_status", "skipped")
    if status not in {"skipped", "success", "error", "timed_out", "stopped"}:
        return "error"
    return status


def _read_review_run_status(review_run: Path) -> str:
    state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    status = state.get("status")
    return status if isinstance(status, str) else "failed"


def _review_run_allows_verdict(status: str, verdict: ReviewVerdict) -> bool:
    if status == "success":
        return True
    return status == "needs_human" and verdict.verdict in {
        "request_changes",
        "needs_human",
    }


def _write_execution_interruption_report(
    iteration_dir: Path,
    *,
    step: str,
    status: Literal["timed_out", "stopped"],
    reason: str | None,
    command: str | None = None,
) -> Path:
    filename = "timeout-report.md" if status == "timed_out" else "stop-report.md"
    title = "Timeout Report" if status == "timed_out" else "Stop Report"
    if step == "verification":
        conclusion = (
            "当前 verification 命令已超时；本轮不会继续剩余验证、reflect 或 review。"
            if status == "timed_out"
            else "当前 verification 已按用户请求停止；本轮不会继续剩余验证、reflect 或 review。"
        )
    else:
        conclusion = (
            "当前 attempt 已超时；timeout 不等于任务失败，但本轮不会继续 verification/review。"
            if status == "timed_out"
            else "当前 attempt 已按用户请求停止，本轮不会继续 verification/review。"
        )
    path = iteration_dir / filename
    details = [
        f"# {title}",
        "",
        f"- 步骤：`{step}`",
        f"- 状态：`{status}`",
    ]
    if command:
        details.append(f"- 命令：`{command}`")
    details.extend(
        [
            f"- 原因：{reason or '未提供'}",
            "",
            "## 结论",
            "",
            f"- {conclusion}",
            "- 目标仓库现场和 execution 证据均保留，交由人工检查。",
            "- Vega 未自动回滚、清理、提交、推送或发布。",
        ]
    )
    _write_text_artifact(path, "\n".join(details).rstrip() + "\n")
    return path


def _write_verification_interruption_report(
    iteration_dir: Path,
    verification: VerificationRunResult,
) -> Path:
    status = verification.interruption_status
    if status in {"timed_out", "stopped"}:
        return _write_execution_interruption_report(
            iteration_dir,
            step="verification",
            status=status,
            reason=verification.interruption_reason,
            command=verification.interruption_command,
        )
    if status != "termination-unconfirmed":
        raise ValueError("verification interruption 缺少可识别状态")

    path = iteration_dir / "runner-error-report.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Verification Termination Report",
                "",
                "- 步骤：`verification`",
                "- 状态：`termination-unconfirmed`",
                f"- 命令：`{verification.interruption_command or '未知'}`",
                f"- 原因：{verification.interruption_reason or '未提供'}",
                "",
                "## 结论",
                "",
                "- owned process tree 的终止未被确认，不能把本轮视为普通命令失败。",
                "- 剩余验证命令、reflect 和 reviewer 均未继续执行。",
                "- execution、验证输出和目标仓库现场均已保留，必须人工确认进程与工作区状态。",
                "- Vega 未自动回滚、清理、提交、推送或发布。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _write_runner_error_report(
    iteration_dir: Path,
    *,
    step: str,
    reason: str | None,
    workspace_status: str,
) -> Path:
    path = iteration_dir / "runner-error-report.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Runner Error Report",
                "",
                f"- 步骤：`{step}`",
                f"- 原因：{reason or '未提供'}",
                "",
                "## 当前工作区",
                "",
                "```text",
                workspace_status.strip() or "<clean>",
                "```",
                "",
                "## 结论",
                "",
                "- 外部 runner 异常不等于工作区没有改动，当前现场必须按部分完成处理。",
                "- 本轮未继续 verification/review，不能把现有 diff 视为已完成或已通过。",
                "- Vega 未自动回滚、清理、提交、推送或发布。",
                "",
                "## 建议下一步",
                "",
                "- 先人工检查 `git status`、当前 diff 和 `worker-output.txt`。",
                "- 如部分改动可保留，补齐后运行 `vega loop continue` 进入验证和隔离审查。",
                "- 如改动不可用，人工清理后重开新的 loop。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _write_final_report(
    run_dir: Path,
    state: LoopAutomationState,
    verdict: ReviewVerdict | None,
    conclusion: str,
) -> None:
    latest_iteration = state.iterations[-1] if state.iterations else None
    lines = [
        "# Final Report",
        "",
        f"- 结论：{conclusion}",
        f"- 模式：`{state.automation_mode}`",
        f"- 任务类型：`{state.task_mode}`",
        f"- brief run：`{state.brief_run or '无'}`",
        f"- 迭代轮数：`{len(state.iterations)}`",
        "",
        "## Review Verdict",
        "",
    ]
    if verdict:
        lines.extend([f"- verdict：`{verdict.verdict}`", f"- summary：{verdict.summary}", ""])
        if verdict.findings:
            lines.append("## 剩余 Findings")
            lines.append("")
            for finding in verdict.findings:
                lines.append(f"- [{finding.severity}] {finding.title}：{finding.recommendation}")
        else:
            lines.append("- 未发现阻塞问题。")
    else:
        lines.append("- 未产生有效 reviewer verdict。")
    if latest_iteration and latest_iteration.verification_status == "failed":
        lines.extend(
            [
                "",
                "## 验证门禁",
                "",
                f"- verification：`failed`，失败命令数：{latest_iteration.verification_failed_count}",
                "- 机器验证失败或中断时，不能自动进入 ready_to_commit。",
            ]
        )
    lines.extend(
        [
            "",
            "## 后续",
            "",
            "- 如需提交，请人工检查 diff 后自行 commit。",
            "- 如本次产生跨任务可复用经验，可执行 `vega reflect --lesson \"...\"` 生成候选。",
        ]
    )
    _write_text_artifact(
        run_dir / "final-report.md",
        "\n".join(lines).rstrip() + "\n",
    )


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        _write_text_artifact(
            target,
            source.read_text(encoding="utf-8", errors="replace"),
        )


def _read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _reflect_has_tracked_diff(reflect_run: Path) -> bool:
    return bool(_read_optional_text(reflect_run / "full-diff.patch").strip())


def _write_text_artifact(path: Path, text: str) -> None:
    redacted = redact_text(text)
    guard = recovery_artifact_guard()
    if guard is not None and guard.write_text(path, redacted):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redacted, encoding="utf-8")


def _finalize_loop_eval(
    run_dir: Path,
    state: LoopAutomationState,
    requested_status: Literal["success", "failed", "needs_human"],
    artifacts: list[str],
    trace: TraceWriter,
) -> None:
    _write_text_artifact(run_dir / "eval.md", "# Eval\n\n(pending)\n")
    state.status = "running"
    state.artifacts = artifacts
    state.save(run_dir / "state.json")

    try:
        base_results = run_loop_eval(
            run_dir,
            artifacts,
            require_terminal=False,
            status_for_eval=requested_status,
        )
    except Exception as exc:  # noqa: BLE001 - 终态收口必须 fail-closed
        base_results = [f"FAIL: loop eval 执行异常：{type(exc).__name__}"]
    final_status = _status_after_eval(base_results, requested_status)
    state.eval_results = base_results
    _write_text_artifact(run_dir / "eval.md", render_eval(base_results))
    state.save(run_dir / "state.json")
    trace.write("eval_written", results=base_results)
    if final_status == "needs_human":
        state.status = final_status
        state.save(run_dir / "state.json")
        trace.write("run_paused", status=state.status, current_step=state.current_step)
        return

    trace.write("run_finished", status=final_status)
    terminal_results = _loop_terminal_eval_results(
        run_dir,
        final_status,
        state.engine,
    )
    if any(result.startswith("FAIL:") for result in terminal_results):
        raise RuntimeError("loop 终态 trace 审计失败，state 保持 running 以阻止误判成功")
    state.status = final_status
    state.eval_results = [*base_results, *terminal_results]
    _write_text_artifact(run_dir / "eval.md", render_eval(state.eval_results))
    state.save(run_dir / "state.json")


def _status_after_eval(
    eval_results: list[str],
    requested_status: Literal["success", "failed", "needs_human"],
) -> Literal["success", "failed", "needs_human"]:
    if any(result.startswith("FAIL:") for result in eval_results):
        return "failed"
    return requested_status


def _new_loop_run_id(task_mode: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{task_mode}-loop"


def _verification_status(command_count: int, failed_count: int) -> Literal["skipped", "passed", "failed"]:
    if command_count == 0:
        return "skipped"
    if failed_count:
        return "failed"
    return "passed"


def _latest_verification_failed(state: LoopAutomationState) -> bool:
    if not state.iterations:
        return False
    latest = state.iterations[-1]
    return latest.verification_status == "failed" or latest.verification_failed_count > 0


def _project_policy_changed(
    repo_path: Path,
    initial_snapshot: dict[str, str | None],
) -> bool:
    return project_policy_snapshot(repo_path) != initial_snapshot


def _write_project_policy_snapshot(
    run_dir: Path,
    snapshot: dict[str, str | None],
) -> None:
    run_dir.joinpath("project-policy-snapshot.json").write_text(
        json.dumps(redact_value(snapshot), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_project_policy_change_report(
    iteration_dir: Path,
    initial_snapshot: dict[str, str | None],
    current_snapshot: dict[str, str | None],
) -> Path:
    path = iteration_dir / "project-policy-change-report.md"
    _write_text_artifact(
        path,
        "\n".join(
            [
                "# Project Policy Change Report",
                "",
                "- worker 或人工执行后检测到 `.vega.yaml` / `.vega.yml` 发生变化。",
                "- 为避免执行策略被运行过程改写，本轮未继续自动 verification、reflect 或 reviewer。",
                "- Vega 未自动回滚、删除、提交、推送或发布任何内容。",
                "",
                "## 启动时策略快照",
                "",
                "```json",
                json.dumps(initial_snapshot, ensure_ascii=False, indent=2),
                "```",
                "",
                "## 当前策略快照",
                "",
                "```json",
                json.dumps(current_snapshot, ensure_ascii=False, indent=2),
                "```",
                "",
                "## 建议下一步",
                "",
                "- 人工审查策略文件改动是否属于本次任务。",
                "- 确认后重新创建 loop，或人工完成验证和隔离审查。",
            ]
        ).rstrip()
        + "\n",
    )
    return path


def _apply_runner_defaults(
    config: ProjectConfig,
    worker_name: str,
    reviewer_name: str,
) -> tuple[str, str]:
    if worker_name == "codex-exec" and config.runner.worker:
        worker_name = config.runner.worker
    if reviewer_name == "codex-exec" and config.runner.reviewer:
        reviewer_name = config.runner.reviewer
    return worker_name, reviewer_name


def _require_graph_control_isolation(
    workspace: Path,
    repo_path: Path,
) -> None:
    """Gate 3 第一轮禁止目标仓库观察 Vega 自己的 Graph 控制面。"""

    control_root = (workspace.resolve() / "runs").resolve()
    repo_root = repo_path.resolve()
    if (
        control_root == repo_root
        or control_root in repo_root.parents
        or repo_root in control_root.parents
    ):
        raise ValueError(
            "Gate 3 要求 Graph control root 与目标 Git 仓库互不包含；"
            "请把目标 fixture 放到 workspace/runs 之外的独立仓库。"
        )
