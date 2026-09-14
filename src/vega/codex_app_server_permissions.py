from __future__ import annotations

from pathlib import Path

from .agent_persistence import load_agent_state
from .provider_session import (
    ProviderSandbox, load_provider_sessions, mutate_provider_sessions,
)

WORKER_PERMISSION_MODES = {
    "ask": ("workspace-write", "on-request", "user"),
    "auto-review": ("workspace-write", "on-request", "auto_review"),
    "full-access": ("danger-full-access", "never", "user"),
}


def prepare_change_permissions(
    run_dir: Path, mode: str | None, provider: str, persistent: bool,
) -> None:
    """首次模型调用前要求选择；非持久会话只保留原有固定兼容行为。"""
    if provider != "codex" or not persistent:
        if mode is not None:
            raise ValueError("显式 Worker 权限仅支持持久 Codex 会话；此 runner 无法核验新模式")
        require_worker_runner_permissions(run_dir, provider, persistent)
        return
    if mode is None:
        worker_permissions(run_dir)
    else:
        bind_worker_permissions(run_dir, mode)


def require_worker_runner_permissions(run_dir: Path, provider: str, persistent: bool) -> None:
    if provider == "codex" and persistent:
        worker_permissions(run_dir)
    elif load_provider_sessions(run_dir).worker_permission_mode is not None:
        raise ValueError("此 Run 已绑定显式 Codex 权限，不能改用无法核验的 runner")


def permissions_for_role(
    run_dir: Path, role: str, sandbox: str, isolated: bool,
) -> tuple[str, str, str]:
    if sandbox == "read-only":
        return "read-only", "never", "user"
    if role != "worker" or isolated:
        raise ValueError("Planning 和 Reviewer 只能使用只读权限")
    return worker_permissions(run_dir)


def bind_worker_permissions(run_dir: Path, mode: str | None) -> None:
    """只接受调用者显式选择；既有 Run 的权限不在运行中或恢复时偷偷更换。"""
    def mutation(sessions) -> None:
        state = load_agent_state(run_dir / "agent-state.json")
        state.require_current_execution()
        if state.active_operation_id or state.active_planning_execution_id:
            raise ValueError("当前 Run 已有活动执行，不能选择 Worker 权限")
        if any(h.owner != "vega" or h.lifecycle in {"active", "waiting_user"}
               for h in sessions.handles.values()):
            raise ValueError("Provider Session 已有活动执行或由人工接管")
        existing = sessions.worker_permission_mode
        if mode is not None:
            if mode not in WORKER_PERMISSION_MODES:
                raise ValueError("不支持的 Worker 执行权限")
            if existing is not None and existing != mode:
                raise ValueError("Worker 权限已绑定 Run，不能更换；请显式创建新任务")
            sessions.worker_permission_mode = mode
            sessions.worker_permission_source = "explicit"
        if sessions.worker_permission_mode is None or sessions.worker_permission_source != "explicit":
            raise ValueError(_missing_permissions_message(run_dir))

    mutate_provider_sessions(run_dir, "agent.session", mutation)


def worker_permissions(run_dir: Path) -> tuple[str, str, str]:
    sessions = load_provider_sessions(run_dir)
    if sessions.worker_permission_source != "explicit" or sessions.worker_permission_mode is None:
        raise ValueError(_missing_permissions_message(run_dir))
    return WORKER_PERMISSION_MODES[sessions.worker_permission_mode]


def _missing_permissions_message(run_dir: Path) -> str:
    return (
        "缺少可信 Worker 权限来源，本次尚未调用模型。请显式选择后继续同一 Run："
        f"vega change --run {run_dir.name} --worker-permissions ask；"
        "也可明确选择 auto-review 或 full-access，不会自动继承宿主权限。"
    )


_SANDBOX_TYPES: dict[str, ProviderSandbox] = {
    "readOnly": "read-only",
    "workspaceWrite": "workspace-write",
    "dangerFullAccess": "danger-full-access",
    "externalSandbox": "external",
}


def require_thread_permissions(
    result: dict[str, object],
    *,
    requested_sandbox: str,
    requested_approval: str = "never",
    requested_reviewer: str = "user",
    requested_cwd: str | None = None,
) -> tuple[ProviderSandbox, str]:
    """核对 App Server 实际生效权限，避免只相信请求参数。"""

    sandbox = result.get("sandbox")
    sandbox_type = sandbox.get("type") if isinstance(sandbox, dict) else None
    observed_sandbox = _SANDBOX_TYPES.get(str(sandbox_type))
    approval_policy = _approval_policy_name(result.get("approvalPolicy"))
    if observed_sandbox != requested_sandbox:
        raise RuntimeError(
            "App Server 实际 sandbox 与请求不一致："
            f"requested={requested_sandbox}，observed={observed_sandbox or 'unknown'}"
        )
    if approval_policy != requested_approval:
        raise RuntimeError(
            "App Server 实际 approvalPolicy 与请求不一致："
            f"requested={requested_approval}，observed={approval_policy}"
        )
    if result.get("approvalsReviewer") != requested_reviewer:
        raise RuntimeError("App Server 实际 approvalsReviewer 缺失或与请求不一致")
    if requested_cwd is not None and result.get("cwd") != requested_cwd:
        raise RuntimeError("App Server 实际 cwd 缺失或与请求不一致")
    if observed_sandbox == "workspace-write":
        expected = {
            "type": "workspaceWrite", "writableRoots": [], "networkAccess": False,
            "excludeTmpdirEnvVar": True, "excludeSlashTmp": True,
        }
        if sandbox != expected:
            raise RuntimeError("App Server 实际写入范围、网络或临时目录权限与请求不一致")
    elif observed_sandbox == "read-only" and sandbox != {"type": "readOnly", "networkAccess": False}:
        raise RuntimeError("App Server 只读权限范围缺失或不一致")
    return observed_sandbox, approval_policy


def _approval_policy_name(value: object) -> str:
    if isinstance(value, str) and value in {"untrusted", "on-request", "never"}:
        return value
    if isinstance(value, dict) and isinstance(value.get("granular"), dict):
        return "granular"
    return "unknown"
