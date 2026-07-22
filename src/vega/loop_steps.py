from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .execution_control import RunnerExecutionContext
from .models import BriefInput, GateResult, LoopAutomationState
from .project_config import ProjectConfig
from .runner import Runner, RunnerResult
from .trace import TraceWriter
from .verification import VerificationRunResult
from .workspace_check import WorkspaceCheckResult, WorkspaceSnapshot


@dataclass(frozen=True)
class PrepareRunStepRequest:
    workspace: Path
    brief_input: BriefInput


@dataclass(frozen=True)
class WorkerEpochStepRequest:
    runner: Runner
    prompt: str
    repo_path: Path
    sandbox: str
    timeout_seconds: int
    execution_context: RunnerExecutionContext


@dataclass(frozen=True)
class CaptureWorkspaceStepRequest:
    repo_path: Path


@dataclass(frozen=True)
class WorkspaceReconcileStepRequest:
    repo_path: Path
    output_dir: Path
    baseline: WorkspaceSnapshot | None = None
    allow_existing_tracked_diff: bool = False
    require_clean_untracked: bool = False


@dataclass(frozen=True)
class VerificationStepRequest:
    workspace: Path
    repo_path: Path
    output_dir: Path


@dataclass(frozen=True)
class ReflectStepRequest:
    workspace: Path
    repo_path: Path
    source_run: str | None
    test_log: Path | None
    note: str | None


@dataclass(frozen=True)
class RiskStepRequest:
    workspace: Path
    repo_path: Path
    source_run: str


@dataclass(frozen=True)
class HumanDecisionStepRequest:
    repo_path: Path
    iteration: int
    reflect_run: Path
    verification_status: Literal["skipped", "passed", "failed"]
    verification_failed_count: int
    verification_result_path: Path | None
    verification_summary_path: Path | None
    risk_result_sha256: str
    risk_report_sha256: str


@dataclass(frozen=True)
class HumanDecisionStepResult:
    decision: Literal["approved", "rejected", "not_provided"]
    decision_id: str | None = None
    consumption_ref: str | None = None


@dataclass(frozen=True)
class ReviewStepRequest:
    workspace: Path
    repo_path: Path
    reflect_run: Path
    reviewer_name: str
    loop_run_dir: Path
    iteration: int
    config: ProjectConfig
    human_approval_ref: str | None = None


@dataclass(frozen=True)
class FinalizeRunStepRequest:
    run_dir: Path
    state: LoopAutomationState
    status: Literal["success", "failed", "needs_human"]
    trace: TraceWriter
    current_step: str = "done"


@dataclass(frozen=True)
class LoopStepServices:
    prepare_run: Callable[[PrepareRunStepRequest], Path]
    capture_workspace: Callable[[CaptureWorkspaceStepRequest], WorkspaceSnapshot]
    execute_worker_epoch: Callable[[WorkerEpochStepRequest], RunnerResult]
    reconcile_workspace: Callable[[WorkspaceReconcileStepRequest], WorkspaceCheckResult]
    run_verification: Callable[[VerificationStepRequest], VerificationRunResult]
    run_reflect: Callable[[ReflectStepRequest], Path]
    evaluate_risk: Callable[[RiskStepRequest], GateResult]
    request_human_decision: Callable[
        [HumanDecisionStepRequest],
        HumanDecisionStepResult,
    ]
    dispatch_review: Callable[[ReviewStepRequest], Path]
    finalize_run: Callable[[FinalizeRunStepRequest], None]


LoopStepName = Literal[
    "prepare_run",
    "capture_workspace",
    "execute_worker_epoch",
    "reconcile_workspace",
    "run_verification",
    "run_reflect",
    "evaluate_risk",
    "request_human_decision",
    "dispatch_review",
    "finalize_run",
]
LoopStepRequest = (
    PrepareRunStepRequest
    | CaptureWorkspaceStepRequest
    | WorkerEpochStepRequest
    | WorkspaceReconcileStepRequest
    | VerificationStepRequest
    | ReflectStepRequest
    | RiskStepRequest
    | HumanDecisionStepRequest
    | ReviewStepRequest
    | FinalizeRunStepRequest
)
LoopStepResult = (
    Path
    | WorkspaceSnapshot
    | RunnerResult
    | WorkspaceCheckResult
    | VerificationRunResult
    | GateResult
    | HumanDecisionStepResult
    | None
)
LoopStepProgram = Generator["LoopStepInstruction", object, Path]


@dataclass(frozen=True)
class LoopStepInstruction:
    name: LoopStepName
    request: LoopStepRequest


class LoopStepProgramDriver:
    """驱动同一份顺序业务程序，具体编排引擎只决定何时执行下一条指令。"""

    def __init__(
        self,
        program: LoopStepProgram,
        services: LoopStepServices,
    ) -> None:
        self._program = program
        self._services = services
        self._current: LoopStepInstruction | None = None
        self._result: Path | None = None
        self._advance(None, initial=True)

    @property
    def done(self) -> bool:
        return self._current is None

    @property
    def current_name(self) -> LoopStepName:
        if self._current is None:
            raise RuntimeError("loop step program 已结束")
        return self._current.name

    @property
    def current_instruction(self) -> LoopStepInstruction:
        if self._current is None:
            raise RuntimeError("loop step program 已结束")
        return self._current

    @property
    def result(self) -> Path:
        if self._result is None:
            raise RuntimeError("loop step program 尚未结束")
        return self._result

    def execute_current(
        self,
        *,
        expected: LoopStepName | None = None,
        request_override: LoopStepRequest | None = None,
    ) -> LoopStepResult:
        instruction = self._current
        if instruction is None:
            raise RuntimeError("loop step program 已结束")
        if expected is not None and instruction.name != expected:
            raise RuntimeError(
                f"loop step 路由不一致：期望 {expected}，实际 {instruction.name}"
            )
        handler = getattr(self._services, instruction.name)
        try:
            request = instruction.request if request_override is None else request_override
            result = handler(request)  # type: ignore[arg-type]
        except Exception as exc:
            self._throw(exc)
            return None
        self._advance(result)
        return cast(LoopStepResult, result)

    def replay_current(
        self,
        result: LoopStepResult,
        *,
        expected: LoopStepName | None = None,
    ) -> None:
        """使用已校验证据推进生成器，不再次调用具有副作用的 handler。"""

        instruction = self._current
        if instruction is None:
            raise RuntimeError("loop step program 已结束")
        if expected is not None and instruction.name != expected:
            raise RuntimeError(
                f"loop step 重放路由不一致：期望 {expected}，实际 {instruction.name}"
            )
        self._advance(result)

    def run_linear(self) -> Path:
        while not self.done:
            self.execute_current()
        return self.result

    def _advance(
        self,
        result: object,
        *,
        initial: bool = False,
    ) -> None:
        try:
            instruction = next(self._program) if initial else self._program.send(result)
        except StopIteration as completed:
            self._current = None
            self._result = completed.value
            return
        self._current = instruction

    def _throw(self, error: Exception) -> None:
        try:
            instruction = self._program.throw(error)
        except StopIteration as completed:
            self._current = None
            self._result = completed.value
            return
        self._current = instruction
