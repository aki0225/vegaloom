from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from .agent_change_cli import (
    agent_adjudicate,
    agent_recover,
    agent_replan,
    agent_retry,
)
from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_codex_adapter import SupervisorAgentCodexAdapter
from .agent_recovery import SupervisorAgentRecovery
from .agent_runtime import SupervisorAgentRuntime
from .agent_runtime_support import (
    capture_bound_workspace,
    load_agent_bundle,
)
from .cli_support import (
    ensure_runner_ready,
    report_execution_progress,
    require_repo_directory,
)
from .provider_session import (
    load_provider_sessions,
    queue_steer,
    resolve_session_role,
    respond_to_interaction,
    set_session_owner,
)
from .redaction import redact_value
from .run_utils import resolve_run_dir


def register_agent_commands(app: typer.Typer) -> None:
    """把 Agent 命令直接注册到顶层，不保留 `vega agent` 包装。"""

    app.command("start")(agent_start)
    app.command("approve")(agent_approve)
    app.command("run")(agent_run)
    app.command("retry")(agent_retry)
    app.command("recover")(agent_recover)
    app.command("adjudicate")(agent_adjudicate)
    app.command("revise")(agent_replan)
    app.command("pause")(agent_pause)
    app.command("stop")(agent_stop)
    app.command("resume")(agent_resume)
    app.command("handoff")(agent_handoff)
    app.command("steer")(agent_steer)
    app.command("respond")(agent_respond)
    app.command("takeover")(agent_takeover)
    app.command("reclaim")(agent_reclaim)
    app.command("capabilities")(agent_capabilities)


def agent_start(
    repo: Path = typer.Option(..., "--repo", help="目标 Git 仓库根目录。"),
    contract_path: Path = typer.Option(
        ...,
        "--contract",
        help="Change Contract JSON。",
    ),
    execution_plan_path: Path = typer.Option(
        ...,
        "--execution-plan",
        help="Execution Plan JSON。",
    ),
) -> None:
    """创建 ChangeRun 与隔离 Worktree，等待人工批准。"""

    repo = require_repo_directory(repo)
    try:
        result = _runtime().start_change(
            repo,
            contract=_load_change_contract(contract_path),
            execution_plan=_load_execution_plan(execution_plan_path),
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"ChangeRun 已创建：{result.run_dir.name}")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


def agent_approve(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    actor: str = typer.Option("human", "--actor", help="批准人标识。"),
) -> None:
    """批准当前 Contract 与 Execution Plan revision。"""

    try:
        result = _runtime().approve(run, actor=actor)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Change Contract 已批准。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


def agent_run(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    timeout_seconds: int = typer.Option(
        900,
        "--timeout",
        min=60,
        max=3600,
        help="单次 Worker 或 Reviewer 外部进程超时秒数。",
    ),
    fresh_session: bool = typer.Option(
        False,
        "--fresh-session",
        help="显式改用短生命周期 codex exec；默认复用 App Server Thread。",
    ),
) -> None:
    """执行当前 Work Item，并完成验证、风险检查和独立审查。"""

    try:
        _, state, _, _ = load_agent_bundle(Path.cwd(), run)
        if state.phase == "finalizing":
            result = _runtime().finalize(run)
        else:
            ensure_runner_ready("codex-exec", "worker")
            result = _adapter(persistent_sessions=not fresh_session).run(
                run,
                timeout_seconds=timeout_seconds,
            )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


def agent_pause(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    reason: str = typer.Option(..., "--reason", help="暂停原因。"),
) -> None:
    """在没有活动 Writer 时暂停调度并保留现场。"""

    try:
        result = _recovery().pause(run, reason=reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Agent 已暂停。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


def agent_stop(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    reason: str = typer.Option(..., "--reason", help="停止原因。"),
) -> None:
    """停止自动调度；不回滚代码或删除 Artifact。"""

    try:
        result = _recovery().stop(run, reason=reason)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if result.state.phase == "stopped":
        typer.echo("Agent 已停止，现场未回滚。")
    elif result.state.active_child_run:
        typer.echo("停止请求已发送，等待当前 Worker 返回终态。")
    else:
        typer.echo("停止请求未取得安全终态，现场已交还人工。")
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


def agent_resume(
    run: str | None = typer.Option(
        None,
        "--run",
        help="从本机 safe Checkpoint 恢复。",
    ),
    repo: Path | None = typer.Option(
        None,
        "--repo",
        help="从当前分支的 Task Card 跨会话或换机恢复。",
    ),
    task: Path | None = typer.Option(None, "--task", help="显式 Task Card 路径。"),
) -> None:
    """从本机 Checkpoint 或 Git Task Card 恢复 ChangeRun。"""

    if (run is None) == (repo is None):
        raise typer.BadParameter("--run 与 --repo 必须且只能提供一个")
    try:
        if run is not None:
            result = _recovery().resume_local(run)
            message = "Agent 已从本机 Checkpoint 恢复。"
        else:
            assert repo is not None
            result = _runtime().resume_task_card(
                require_repo_directory(repo),
                task,
            )
            message = f"Agent 已从 Task Card 恢复：{result.run_dir.name}"
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(message)
    typer.echo("")
    typer.echo(_runtime().status(result.run_dir.name))


def agent_handoff(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    reason: str = typer.Option(..., "--reason", help="交接原因。"),
) -> None:
    """生成 Git 可跟踪的 Task Card 与本机 Resume Capsule。"""

    try:
        result = _runtime().handoff(run, reason=reason)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        f"Handoff 已生成：{result.run.run_dir.name}；"
        f"Task Card：{result.task_card_path}"
    )
    typer.echo("")
    typer.echo(_runtime().status(result.run.run_dir.name))


def agent_steer(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    role: str = typer.Option("worker", "--role", help="worker 或 reviewer。"),
    text: str | None = typer.Option(None, "--text", help="补充指令。"),
    input_path: Path | None = typer.Option(
        None,
        "--input",
        help="UTF-8 补充指令文件。",
    ),
) -> None:
    """把补充指令排到当前 Turn 的安全事件边界。"""

    run_dir = _agent_run_dir(run)
    try:
        role_key = resolve_session_role(run_dir, role)
        steer = queue_steer(
            run_dir,
            role_key,
            _load_text_choice(text, input_path),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Steer 已排队：{steer.steer_id}；目标={role_key}")


def agent_respond(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    interaction: str = typer.Option(..., "--interaction", help="待响应请求 ID。"),
    decision: str | None = typer.Option(
        None,
        "--decision",
        help="accept、accept-session、decline 或 cancel。",
    ),
    input_path: Path | None = typer.Option(
        None,
        "--input",
        help="权限、用户输入或 MCP elicitation 的响应 JSON。",
    ),
) -> None:
    """响应 App Server 的审批或用户输入请求。"""

    run_dir = _agent_run_dir(run)
    try:
        state = load_provider_sessions(run_dir)
        matches = [
            item
            for item in state.interactions
            if item.interaction_id == interaction and item.status == "pending"
        ]
        if len(matches) != 1:
            raise ValueError("待响应请求不存在或已处理")
        response = _interaction_response(
            matches[0].method,
            decision=decision,
            input_path=input_path,
        )
        result = respond_to_interaction(run_dir, interaction, response)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"响应已记录：{result.interaction_id}")


def agent_takeover(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    role: str = typer.Option("worker", "--role", help="worker 或 reviewer。"),
    reason: str = typer.Option("人工接管 Provider Session", "--reason"),
) -> None:
    """停止活动执行后，把 Provider Thread 所有权交给人工。"""

    run_dir = _agent_run_dir(run)
    interrupted = False
    try:
        role_key = resolve_session_role(run_dir, role)
        handle = load_provider_sessions(run_dir).handles[role_key]
        if handle.lifecycle in {"active", "waiting_user"}:
            interrupted = True
            _recovery().stop(run, reason=reason)
            _wait_for_session_idle(run_dir, role_key)
        handle = set_session_owner(run_dir, role_key, "human")
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"会话已交给人工：{role_key}")
    typer.echo(f"可使用 `codex resume {handle.thread_id}` 继续。")
    if interrupted:
        typer.echo(
            "当前 attempt 已中断并保留 Writer binding；人工处理后先完成 recover 或 handoff，"
            "不能直接 reclaim。"
        )


def agent_reclaim(
    run: str = typer.Option(..., "--run", help="ChangeRun ID 或 runs/<run-id>。"),
    role: str = typer.Option("worker", "--role", help="worker 或 reviewer。"),
) -> None:
    """在 Workspace 未漂移时，把 Provider Thread 交还 Vega。"""

    run_dir = _agent_run_dir(run)
    try:
        _, state, _, _ = load_agent_bundle(Path.cwd(), run)
        if state.active_child_run:
            raise ValueError(
                "当前仍绑定已中断的 operation；先执行 recover 完成现场对账，"
                "不能直接 reclaim"
            )
        snapshot = capture_bound_workspace(run_dir)
        if snapshot.fingerprint != state.workspace_fingerprint:
            raise ValueError(
                "人工接管期间 Workspace 已变化；先完成现场对账，不能直接 reclaim"
            )
        role_key = resolve_session_role(run_dir, role)
        handle = set_session_owner(run_dir, role_key, "vega")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"会话已交还 Vega：{role_key}；thread={handle.thread_id}")


def agent_capabilities() -> None:
    """显示当前公开 Agent 合同。"""

    typer.echo(
        json.dumps(
            {
                "schema_version": 2,
                "control_plane": "deterministic-state-machine",
                "provider": "codex-app-server",
                "fresh_session_fallback": "explicit-only",
                "persistent_worker_thread": True,
                "reviewer_isolation": "per-work-item",
                "interactive_steer": True,
                "interactive_response": True,
                "human_takeover": True,
                "git_task_card_resume": True,
                "finish_owned_by_core": True,
                "automatic_commit_push_release": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _runtime() -> SupervisorAgentRuntime:
    return SupervisorAgentRuntime(Path.cwd())


def _recovery() -> SupervisorAgentRecovery:
    return SupervisorAgentRecovery(Path.cwd())


def _adapter(*, persistent_sessions: bool) -> SupervisorAgentCodexAdapter:
    return SupervisorAgentCodexAdapter(
        Path.cwd(),
        persistent_sessions=persistent_sessions,
        progress_reporter=report_execution_progress,
        event_reporter=lambda message: typer.echo(f"[vega] {message}", err=True),
    )


def _agent_run_dir(run: str) -> Path:
    try:
        return resolve_run_dir(Path.cwd(), run)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _load_text_choice(text: str | None, path: Path | None) -> str:
    if (text is None) == (path is None):
        raise typer.BadParameter("--text 与 --input 必须且只能提供一个")
    if text is not None:
        return text
    assert path is not None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"无法读取输入文件：{path.name}") from exc


def _interaction_response(
    method: str,
    *,
    decision: str | None,
    input_path: Path | None,
) -> dict[str, object]:
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return _approval_response(decision, input_path)
    supported_payload_methods = {
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
    }
    if method not in supported_payload_methods:
        raise ValueError("当前 App Server 请求类型不受支持")
    if decision is not None or input_path is None:
        raise ValueError("权限、用户输入或 MCP 请求必须通过 --input 提供响应 JSON")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("响应 JSON 必须是 object")
    if redact_value(payload) != payload:
        raise ValueError("响应包含敏感信息；请接管原生会话处理，不得写入 Vega Artifact")
    if method == "item/permissions/requestApproval" and not isinstance(
        payload.get("permissions"),
        dict,
    ):
        raise ValueError("权限响应必须包含 permissions object")
    if method == "item/tool/requestUserInput" and not isinstance(
        payload.get("answers"),
        dict,
    ):
        raise ValueError("工具输入响应必须包含 answers object")
    if method == "mcpServer/elicitation/request" and payload.get("action") not in {
        "accept",
        "decline",
        "cancel",
    }:
        raise ValueError("MCP elicitation action 无效")
    return payload


def _approval_response(
    decision: str | None,
    input_path: Path | None,
) -> dict[str, object]:
    if input_path is not None or decision is None:
        raise ValueError("审批请求只接受 --decision")
    mapped = {
        "accept": "accept",
        "accept-session": "acceptForSession",
        "decline": "decline",
        "cancel": "cancel",
    }.get(decision)
    if mapped is None:
        raise ValueError("审批 decision 无效")
    return {"decision": mapped}


def _wait_for_session_idle(
    run_dir: Path,
    role_key: str,
    *,
    timeout_seconds: float = 10,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        handle = load_provider_sessions(run_dir).handles[role_key]
        if handle.lifecycle not in {"active", "waiting_user"}:
            return
        time.sleep(0.1)
    raise ValueError("活动 Turn 尚未确认停止，拒绝变更会话所有者")


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
