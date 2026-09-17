from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

import typer

from .agent_change_driver import AgentChangeDriver, ChangeDriverResult
from .agent_change_presentation import redact_change_message
from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_cli_interaction import InteractionPumpUpdate, TerminalApprovalPrompt
from .agent_cli_snapshot import AgentCliRun, build_agent_cli_snapshot, resolve_agent_cli_run
from .agent_cli_status import render_compact_agent_status
from .agent_runtime_support import load_agent_bundle
from .agent_recovery import SupervisorAgentRecovery
from .agent_recovery_request import AgentRecoveryRequest
from .agent_run_selection import resolve_repository_root
from .agent_runtime import SupervisorAgentRuntime
from .agent_side_effect_adjudication import SupervisorAgentSideEffectAdjudicator
from .cli_support import report_execution_progress
from .redaction import redact_text


def agent_change(
    text: str | None = typer.Argument(
        None,
        help="自然语言变更目标；省略时继续当前仓库唯一未完成 ChangeRun。",
    ),
    run: str | None = typer.Option(
        None,
        "--run",
        help="显式继续指定 ChangeRun。",
    ),
    task: Path | None = typer.Option(
        None,
        "--task",
        help="从指定 Git Task Card 恢复。",
    ),
    acceptance_file: Path | None = typer.Option(
        None, "--acceptance-file", help="显式 --run：提交 workspace 内相对 JSON，仅 acceptance_missing 原 Candidate 再审，不启动 Worker。",
    ),
    provider: Literal["codex", "claude"] | None = typer.Option(
        None,
        "--provider",
        help="Coding Agent Provider；已有 Run 默认沿用原 Provider。",
    ),
    approval: Literal["human", "bounded"] = typer.Option(
        "human",
        "--approval",
        help="human 在当前终端确认；bounded 还要求仓库策略显式放行。",
    ),
    worker_permissions: Literal["ask", "auto-review", "full-access"] | None = typer.Option(
        None, "--worker-permissions",
        help="显式选择并绑定 Codex Worker 执行权限；不是任务批准，也不自动继承宿主。",
    ),
    timeout_seconds: int = typer.Option(
        900,
        "--timeout",
        min=60,
        max=3600,
        help="单次 Planning、Worker 或 Reviewer 外部进程超时秒数；不控制项目 verification.timeout_seconds（每条验证及准备命令，默认 180s）。",
    ),
    fresh_session: bool = typer.Option(
        False, "--fresh-session", help="显式使用短生命周期 Provider 会话。",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="只输出一个稳定 JSON object，不读取 stdin。",
    ),
) -> None:
    """创建或继续一个日常代码变更，直到完成或遇到授权边界。"""

    target = None
    driver = None
    try:
        workspace = Path.cwd()
        if run is not None:
            target = resolve_agent_cli_run(workspace, run)
            _, state, _, metadata = load_agent_bundle(target.workspace, target.run_dir.name)
            if state.run_kind != "change":
                raise ValueError("change 只接受 ChangeRun")
            change_metadata = metadata.get("change_run")
            if not isinstance(change_metadata, dict):
                raise ValueError("ChangeRun 缺少源仓库绑定")
            source = change_metadata.get("source_repo_path")
            if not isinstance(source, str) or not source:
                raise ValueError("ChangeRun 缺少源仓库路径")
            repo = resolve_repository_root(Path(source))
            workspace = target.workspace
            run = target.run_dir.name
        else:
            repo = resolve_repository_root(workspace)
            workspace = repo
        interactive = not json_output and _stream_is_tty(sys.stdin)
        driver = AgentChangeDriver(
            workspace,
            repo,
            provider=provider,
            approval=approval,
            timeout_seconds=timeout_seconds,
            interactive=interactive,
            json_output=json_output,
            fresh_session=fresh_session,
            worker_permissions=worker_permissions,
            confirm=_confirm if interactive else None,
            event_reporter=(
                None
                if json_output
                else lambda message: typer.echo(
                    f"[vega] {message}",
                    err=True,
                )
            ),
            interaction_reporter=(
                None if json_output else
                TerminalApprovalPrompt(_render_interaction_update)
                if interactive and _stream_is_tty(sys.stderr) else _render_interaction_update
            ),
            progress_reporter=(
                None if json_output else report_execution_progress
            ),
        )
        result = driver.change(
            text=text, run=run, task=task,
            **({"acceptance_file": acceptance_file} if acceptance_file is not None else {}),
        )
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        if driver is not None and driver.selected_run is not None:
            current = driver.selected_run
            target = AgentCliRun(current.run_dir.parent.parent, current.run_dir, "explicit")
        error = _change_error_payload(target, exc)
        _render_change_error(target, error, json_output=json_output)
        raise typer.Exit(code=1) from exc

    _render_change_result(workspace, result, json_output=json_output)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


def _render_change_error(
    target: AgentCliRun | None, error: dict[str, object], *, json_output: bool,
) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                error,
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"错误：{error['message']}", err=True)
        if "current_state" in error:
            typer.echo(f"当前状态：{error['current_state']['message']}", err=True)
        if target is not None:
            typer.echo(f"运行：{target.run_dir.name}；下一步：vega explain --run {target.run_dir.name}", err=True)


def _change_error_payload(target: AgentCliRun | None, exc: Exception) -> dict[str, object]:
    """只投影已解析 Run 的当前拒绝，不把异常写回状态或猜测其他 Run。"""
    payload: dict[str, object] = {
        "schema_version": 1, "run_id": target.run_dir.name if target else None,
        "phase": None, "outcome": "error", "reason_code": "change.request_failed",
        "message": redact_change_message(str(exc)),
        "safe_actions": ["status.view", "human.review"] if target else [],
    }
    if target is not None:
        try:
            explanation = build_agent_cli_snapshot(target).explanation
            if explanation is not None:
                payload["current_state"] = {
                    "phase": explanation.phase, "reason_code": explanation.reason_code,
                    "message": redact_change_message(explanation.reason),
                    "safe_actions": explanation.safe_actions,
                }
                payload["phase"] = explanation.phase
        except (OSError, RuntimeError, ValueError):
            pass  # 状态证据无法读取时保留原拒绝，不给出继续执行的建议。
    return payload


def agent_replan(
    run: str = typer.Option(..., "--run", help="ChangeRun run_id 或 runs/<run_id>。"),
    contract_path: Path = typer.Option(
        ...,
        "--contract",
        help="提议的 Change Contract JSON。",
    ),
    execution_plan_path: Path = typer.Option(
        ...,
        "--execution-plan",
        help="提议的 Execution Plan JSON。",
    ),
    request_approval: bool = typer.Option(
        False, "--request-approval", help="将合同内 Plan 修订提交人工批准，不自动采用。",
    ),
) -> None:
    """按合同字段、真实 Diff 和风险路径裁决 ChangeRun revision。"""

    try:
        result = SupervisorAgentRuntime(Path.cwd()).revise_change(
            run,
            proposed_contract=_load_change_contract(contract_path),
            proposed_execution_plan=_load_execution_plan(execution_plan_path),
            request_approval=request_approval,
        )
    except (OSError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if result.state.phase == "awaiting_approval":
        typer.echo("Revision 已写入，等待人工批准。")
    elif result.state.phase == "needs_human":
        typer.echo("Revision 触及合同、风险或预算边界，已停止自动执行。")
    else:
        typer.echo("Execution Plan revision 已在原合同内采用。")
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def agent_recover(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    input_path: Path = typer.Option(
        ...,
        "--input",
        help="结构化 Recovery Request JSON。",
    ),
) -> None:
    """Worker 失去可信终态后，对账进程、Workspace 和副作用。"""

    try:
        request = AgentRecoveryRequest.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = SupervisorAgentRecovery(Path.cwd()).recover(run, request)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Worker 现场已重新对账。")
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def agent_adjudicate(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    input_path: Path = typer.Option(
        ...,
        "--input",
        help="结构化 Recovery Request JSON。",
    ),
) -> None:
    """记录人工核对后的外部副作用结论。"""

    try:
        request = AgentRecoveryRequest.model_validate_json(
            input_path.read_text(encoding="utf-8")
        )
        result = SupervisorAgentSideEffectAdjudicator(Path.cwd()).adjudicate(
            run,
            request,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(redact_text(str(exc))) from exc
    message = (
        "外部副作用已确认不存在，可以准备 Handoff。"
        if result.state.phase == "stopped"
        else "外部副作用已确认为 known，任务继续等待人工处理。"
    )
    typer.echo(message)
    typer.echo("")
    typer.echo(SupervisorAgentRuntime(Path.cwd()).status(result.run_dir.name))


def _load_change_contract(path: Path) -> ChangeContract:
    try:
        return ChangeContract.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"无法读取 Change Contract：{path.name}") from exc


def _load_execution_plan(path: Path) -> ExecutionPlan:
    try:
        return ExecutionPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise typer.BadParameter(f"无法读取 Execution Plan：{path.name}") from exc


def _render_change_result(
    workspace: Path,
    result: ChangeDriverResult,
    *,
    json_output: bool,
) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                result.as_payload(),
                ensure_ascii=False,
            )
        )
        return
    typer.echo(result.message)
    if result.run is None:
        return
    typer.echo("")
    snapshot = build_agent_cli_snapshot(AgentCliRun(
        workspace=workspace, run_dir=result.run.run_dir, selection_source="explicit",
    ))
    typer.echo(render_compact_agent_status(snapshot))


def _confirm(prompt: str) -> bool:
    return typer.confirm(prompt, default=False)


def _render_interaction_update(update: InteractionPumpUpdate) -> None:
    if update.message is not None:
        typer.echo(f"[vega] {update.message}", err=True)


def _stream_is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
