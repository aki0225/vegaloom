from __future__ import annotations

from pathlib import Path

from .agent_contract import AgentState
from .agent_planning import PlanningProposal
from .agent_provider import AgentProvider
from .agent_worker_evidence import WorkerClaim
from .claude_code_runner import ClaudeCodeRunner
from .codex_app_server_runner import CodexAppServerRunner
from .loop_runtime import LoopAutomationRuntime
from .project_config import ProjectConfig
from .review_contract import ReviewVerdict
from .runner import CodexExecRunner, Runner


def planning_runner(
    run_dir: Path,
    state: AgentState,
    config: ProjectConfig,
    *,
    provider: AgentProvider,
    persistent_session: bool,
) -> Runner:
    if provider == "claude":
        return ClaudeCodeRunner(
            run_dir,
            "worker",
            work_item_id=state.current_work_item,
            contract_revision=None,
            plan_revision=None,
            output_schema=PlanningProposal.model_json_schema(),
            persistent_session=persistent_session,
            options=config.runner.claude_code.worker,
        )
    if persistent_session:
        return CodexAppServerRunner(
            run_dir,
            "worker",
            work_item_id=state.current_work_item,
            contract_revision=None,
            plan_revision=None,
            output_schema=PlanningProposal.model_json_schema(),
            isolate_session=True,
            options=config.runner.codex_exec.worker,
        )
    return CodexExecRunner(
        options=config.runner.codex_exec.worker,
        output_schema=PlanningProposal.model_json_schema(),
        isolate_mcp=True,
    )


def worker_runner(
    run_dir: Path,
    state: AgentState,
    config: ProjectConfig,
    *,
    provider: AgentProvider,
    persistent_session: bool,
) -> Runner:
    if provider == "claude":
        return ClaudeCodeRunner(
            run_dir,
            "worker",
            work_item_id=state.current_work_item,
            contract_revision=state.contract_revision,
            plan_revision=state.execution_plan_revision,
            output_schema=WorkerClaim.model_json_schema(),
            persistent_session=persistent_session,
            options=config.runner.claude_code.worker,
        )
    if persistent_session:
        return CodexAppServerRunner(
            run_dir,
            "worker",
            work_item_id=state.current_work_item,
            contract_revision=state.contract_revision,
            plan_revision=state.execution_plan_revision,
            output_schema=WorkerClaim.model_json_schema(),
            options=config.runner.codex_exec.worker,
        )
    return CodexExecRunner(
        options=config.runner.codex_exec.worker,
        output_schema=WorkerClaim.model_json_schema(),
        single_writer=True,
    )


def ensure_reviewer_runner(
    loop_runtime: object,
    config: ProjectConfig,
    *,
    agent_run_dir: Path | None,
    state: AgentState | None,
    provider: AgentProvider,
    persistent_session: bool,
) -> None:
    if not isinstance(loop_runtime, LoopAutomationRuntime):
        return
    if loop_runtime.reviewer_runner is not None and not isinstance(
        loop_runtime.reviewer_runner,
        (CodexAppServerRunner, ClaudeCodeRunner),
    ):
        return
    if provider == "claude":
        if agent_run_dir is None or state is None or state.current_work_item is None:
            raise ValueError("Claude Reviewer 缺少 Agent run 或当前 Work Item")
        loop_runtime.reviewer_runner = _claude_reviewer(
            agent_run_dir,
            f"reviewer:{state.current_work_item}",
            state,
            config,
            persistent_session=persistent_session,
        )
        return
    if persistent_session:
        if agent_run_dir is None or state is None or state.current_work_item is None:
            raise ValueError("持久 Reviewer 缺少 Agent run 或当前 Work Item")
        loop_runtime.reviewer_runner = CodexAppServerRunner(
            agent_run_dir,
            f"reviewer:{state.current_work_item}",
            work_item_id=state.current_work_item,
            contract_revision=state.contract_revision,
            plan_revision=state.execution_plan_revision,
            output_schema=ReviewVerdict.model_json_schema(),
            isolate_session=True,
            options=config.runner.codex_exec.reviewer,
        )
    elif loop_runtime.reviewer_runner is None:
        loop_runtime.reviewer_runner = CodexExecRunner(
            options=config.runner.codex_exec.reviewer,
            isolate_mcp=True,
        )


def final_reviewer_runner(
    run_dir: Path,
    state: AgentState,
    candidate_sha: str,
    config: ProjectConfig,
    *,
    provider: AgentProvider,
    persistent_session: bool,
    explicit_runner: Runner | None,
) -> Runner:
    if explicit_runner is not None and not isinstance(
        explicit_runner,
        (CodexAppServerRunner, ClaudeCodeRunner),
    ):
        return explicit_runner
    if provider == "claude":
        return _claude_reviewer(
            run_dir,
            f"reviewer:integration:{candidate_sha[:12]}",
            state,
            config,
            persistent_session=persistent_session,
        )
    if persistent_session:
        return CodexAppServerRunner(
            run_dir,
            f"reviewer:integration:{candidate_sha[:12]}",
            work_item_id=state.current_work_item,
            contract_revision=state.contract_revision,
            plan_revision=state.execution_plan_revision,
            output_schema=ReviewVerdict.model_json_schema(),
            isolate_session=True,
            options=config.runner.codex_exec.reviewer,
        )
    return CodexExecRunner(
        options=config.runner.codex_exec.reviewer,
        output_schema=ReviewVerdict.model_json_schema(),
        isolate_mcp=True,
    )


def runner_name(provider: AgentProvider, *, persistent_session: bool) -> str:
    if provider == "claude":
        return "claude-code" if persistent_session else "claude-code-fresh"
    return "codex-app-server" if persistent_session else "codex-exec"


def _claude_reviewer(
    run_dir: Path,
    role_key: str,
    state: AgentState,
    config: ProjectConfig,
    *,
    persistent_session: bool,
) -> ClaudeCodeRunner:
    return ClaudeCodeRunner(
        run_dir,
        role_key,
        work_item_id=state.current_work_item,
        contract_revision=state.contract_revision,
        plan_revision=state.execution_plan_revision,
        output_schema=ReviewVerdict.model_json_schema(),
        persistent_session=persistent_session,
        options=config.runner.claude_code.reviewer,
    )
