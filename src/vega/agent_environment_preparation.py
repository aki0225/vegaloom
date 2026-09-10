from __future__ import annotations

import hashlib
import json
from pathlib import Path
from dataclasses import dataclass

from .agent_change_contract import ChangeContract
from .agent_change_run import ChangeRunContext, load_change_run_context
from .agent_contract import AgentState
from .agent_operation import operation_ref, reserve_operation_identity
from .agent_persistence import append_agent_trace, read_agent_trace, save_agent_state
from .agent_repository_guard import acquire_writer_claim, release_writer_claim
from .agent_run import AgentRun
from .agent_runtime_logic import update_state
from .agent_runtime_support import (
    bound_repo, capture_bound_workspace, load_agent_bundle,
    validate_dispatch_artifacts, write_checkpoint, write_status_card, write_task_brief,
)
from .execution_control import (
    ExecutionLease, RunnerExecutionContext, run_owned_process,
)
from .project_config import (
    build_verification_shell_command, load_project_config, project_policy_snapshot,
    validate_project_config, ProjectConfig,
)
from .redaction import redact_text, write_redacted_json_once
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .workspace_snapshot import ReviewWorkspaceSnapshot


@dataclass(frozen=True)
class PreparedEnvironment:
    """同一次准备的冻结输入；只在控制器调用栈内传递，不新增持久状态。"""

    run_dir: Path
    repo: Path
    config: ProjectConfig
    contract: ChangeContract
    state: AgentState
    before: ReviewWorkspaceSnapshot
    policy: dict[str, str | None]
    operation_id: str
    reservation_ref: str
    result_ref: str


def prepare_change_environment(workspace: Path, run: str) -> AgentRun | None:
    """只执行批准的准备命令；一次预算先落盘，进程运行不占用 mutation lock。"""
    run_dir = resolve_run_dir(workspace, run)
    with RunMutationLock.acquire(run_dir, "agent.dispatch"):
        prepared = _bind_preparation(workspace, run_dir)
    if prepared is None:
        return None
    evidence, succeeded, reason = _execute_preparation(prepared)
    return _publish_preparation(workspace, prepared, evidence, succeeded, reason)


def _bind_preparation(workspace: Path, run_dir: Path) -> PreparedEnvironment | None:
    _, state, plan, metadata = load_agent_bundle(workspace, run_dir.name)
    state.require_current_execution()
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        return None
    contract = context.contract
    repo = bound_repo(run_dir)
    config = load_project_config(repo)
    commands = contract.prepare_commands
    if not _requires_preparation(context, repo, config):
        return None
    assert contract.approved_digest is not None
    operation_id = hashlib.sha256(
        f"prepare:{state.run_id}:{contract.approved_digest}".encode()
    ).hexdigest()[:32]
    result_ref = f"environment-preparations/{contract.approved_digest}.json"
    reservation = run_dir / operation_ref(operation_id)
    trace = read_agent_trace(run_dir / "trace.jsonl")
    started = any(item.get("event") == "environment_prepare_started"
                  and item.get("operation_id") == operation_id for item in trace)
    if not reservation.exists() and (started or (run_dir / result_ref).exists()):
        raise ValueError("环境准备已有开始证据但预算身份缺失；禁止自动重放")
    if reservation.exists():
        _require_completed_preparation(run_dir, result_ref, operation_id, contract.approved_digest, commands)
        return None
    if (
        state.phase != "ready" or not {"next", "repair"}.intersection(state.allowed_actions)
        or state.active_child_run or state.active_operation_id
        or state.active_planning_execution_id is not None
    ):
        raise ValueError("当前状态不能准备环境，禁止启动第二 Writer")
    validate_dispatch_artifacts(run_dir, state, plan)
    before = capture_bound_workspace(run_dir)
    if before.fingerprint != state.workspace_fingerprint:
        raise ValueError("环境准备前 Workspace 已漂移，拒绝执行")
    policy = project_policy_snapshot(repo)
    acquire_writer_claim(
        repo, run_dir=run_dir, task_id=state.task_id, child_run=run_dir.name,
        operation_id=operation_id, operation_kind="environment_prepare",
    )
    # reservation 即一次预算。其后任意崩溃都不能由缺少终态推断可重跑。
    ref = reserve_operation_identity(
        run_dir, state, child_run=run_dir.name, operation_id=operation_id,
        operation_kind="environment_prepare",
        details={"approved_digest": contract.approved_digest, "commands": commands,
                 "policy_snapshot": policy,
                 "timeout_seconds": config.verification.timeout_seconds},
    )
    acting = update_state(
        state, phase="acting", state_version=state.state_version + 1,
        active_child_run=run_dir.name, active_operation_id=operation_id,
        operation_started=True, allowed_actions=["human"],
    )
    save_agent_state(run_dir / "agent-state.json", acting)
    append_agent_trace(
        run_dir / "trace.jsonl", event="environment_prepare_started", state=acting,
        observation_summary="控制器已消耗本批准版本的一次环境准备预算；不启动 Worker",
        artifact_refs=[ref],
    )
    write_status_card(run_dir, acting, plan, next_step="控制器正在准备环境，可使用 stop 停止")
    return PreparedEnvironment(
        run_dir, repo, config, contract, state, before, policy, operation_id, ref, result_ref,
    )


def _requires_preparation(context: ChangeRunContext, repo: Path, config: ProjectConfig) -> bool:
    contract = context.contract
    commands = contract.prepare_commands
    if commands != config.verification.prepare_commands:
        raise ValueError("环境准备命令与已批准 Contract 不完全一致，拒绝执行")
    if not commands:
        return False
    if not contract.approval_is_current():
        raise ValueError("环境准备必须有当前有效的 Contract 批准")
    if contract.approval_source == "bounded":
        raise ValueError("环境准备命令必须人工明确批准，不能沿用验证命令的有界批准权限")
    frozen_config = load_project_config(
        repo, tracked_only=True, tracked_revision=context.worktree.base_sha,
    )
    if frozen_config.verification != config.verification:
        raise ValueError("环境准备策略与固定源版本不一致，拒绝执行")
    errors = [issue for issue in validate_project_config(config) if issue.severity == "error"]
    if errors:
        raise ValueError("环境准备配置无效：" + errors[0].message)
    # 准备阶段没有验证临时目录；不得把未绑定占位符偷偷展开成任意路径。
    if any("{{vega_" in command for command in commands):
        raise ValueError("环境准备命令不支持验证阶段占位符")
    return True


def _execute_preparation(prepared: PreparedEnvironment) -> tuple[dict[str, str], bool, str]:
    run_dir, repo, config = prepared.run_dir, prepared.repo, prepared.config
    operation_id, commands = prepared.operation_id, prepared.contract.prepare_commands
    evidence: dict[str, str] = {}
    succeeded = True
    reason = "环境准备成功；尚未运行 Worker 或合同验证"
    for index, command in enumerate(commands):
        execution_dir = run_dir / "executions" / "environment-prepare" / operation_id / f"{index:02d}"
        result = run_owned_process(
            build_verification_shell_command(command), "", repo,
            config.verification.timeout_seconds,
            RunnerExecutionContext(
                execution_root=run_dir, execution_dir=execution_dir, run_id=run_dir.name,
                step="environment_prepare", execution_id=operation_id, iteration=index + 1,
            ),
        )
        lease_path = execution_dir / "execution.json"
        lease = ExecutionLease.model_validate_json(lease_path.read_text(encoding="utf-8"))
        if (
            result.termination_unconfirmed or lease.termination_unconfirmed
            or lease.status not in {"completed", "failed", "stopped", "timed_out"}
            or lease.execution_id != operation_id or lease.run_id != run_dir.name
            or lease.step != "environment_prepare"
        ):
            raise ValueError("环境准备终态未知，保留 Writer 与一次预算，禁止自动重放")
        evidence[lease_path.relative_to(run_dir).as_posix()] = hashlib.sha256(lease_path.read_bytes()).hexdigest()
        if result.status != "success" or result.returncode != 0 or lease.status != "completed":
            succeeded = False
            reason = "环境准备失败或停止；未启动 Worker，不会自动重试"
            break

    return evidence, succeeded, reason


def _publish_preparation(
    workspace: Path, prepared: PreparedEnvironment, evidence: dict[str, str],
    succeeded: bool, reason: str,
) -> AgentRun | None:
    run_dir, repo = prepared.run_dir, prepared.repo
    contract, state, before = prepared.contract, prepared.state, prepared.before
    policy, operation_id = prepared.policy, prepared.operation_id
    ref, result_ref = prepared.reservation_ref, prepared.result_ref
    with RunMutationLock.acquire(run_dir, "agent.observe"):
        _, current, current_plan, _ = load_agent_bundle(workspace, run_dir.name)
        if current.active_operation_id != operation_id or current.active_child_run != run_dir.name:
            raise ValueError("环境准备 Writer 绑定已改变，禁止发布结果")
        after = capture_bound_workspace(run_dir)
        # 仅接受显式准备造成的 ignored 环境变化；业务文件和 Git 控制面不能被安装脚本改写。
        stable_fields = (
            "head_sha", "staged_diff_sha256", "unstaged_diff_sha256",
            "untracked_manifest_sha256", "index_flags_sha256", "git_control_sha256",
        )
        if any(getattr(before, name) != getattr(after, name) for name in stable_fields) or policy != project_policy_snapshot(repo):
            succeeded = False
            reason = "环境准备修改了业务文件、Git 控制面或项目策略；必须人工核对"
        if not after.git_control_complete or not after.untracked_content_complete:
            succeeded = False
            reason = "环境准备后 Workspace 证据不完整；必须人工核对"
        payload = {
            "operation_id": operation_id, "approved_digest": contract.approved_digest,
            "status": "completed" if succeeded else "failed", "reason": reason,
            "before_fingerprint": before.fingerprint, "after_fingerprint": after.fingerprint,
            "execution_sha256": evidence,
        }
        write_redacted_json_once(run_dir / result_ref, payload)
        actions = list(state.allowed_actions) if succeeded else ["human"]
        next_state = update_state(
            current, phase="ready" if succeeded else "needs_human",
            state_version=current.state_version + 1,
            workspace_fingerprint=after.fingerprint if succeeded else current.workspace_fingerprint,
            active_child_run=None, active_operation_id=None, operation_started=False,
            allowed_actions=actions,
        )
        checkpoint = write_checkpoint(
            run_dir, next_state, after, reason=reason,
            status="safe" if succeeded else "blocked", pending_actions=actions,
            evidence_refs=[ref, result_ref, *evidence],
            # 已批准准备已终态且环境基线已重建，不留下待裁决的外部副作用；
            # 命令及变化仍保存在 operation/结果证据，不借此宣称合同验证通过。
            external_side_effects="none" if succeeded else "known",
        )
        next_state = update_state(
            next_state, latest_checkpoint_id=checkpoint.checkpoint_id,
            state_version=next_state.state_version + 1,
        )
        save_agent_state(run_dir / "agent-state.json", next_state)
        append_agent_trace(
            run_dir / "trace.jsonl",
            event="environment_prepare_completed" if succeeded else "environment_prepare_failed",
            state=next_state, observation_summary=reason, artifact_refs=[ref, result_ref],
        )
        if succeeded:
            write_task_brief(run_dir, current_plan, next_state, checkpoint)
        write_status_card(run_dir, next_state, current_plan, checkpoint=checkpoint, next_step=reason)
        release_writer_claim(repo, run_id=next_state.run_id, operation_id=operation_id)
        return None if succeeded else AgentRun(run_dir=run_dir, state=next_state, plan=current_plan)


def _require_completed_preparation(
    run_dir: Path, result_ref: str, operation_id: str, approved_digest: str, commands: list[str],
) -> None:
    try:
        reservation = json.loads((run_dir / operation_ref(operation_id)).read_text(encoding="utf-8"))
        if (
            reservation["operation_kind"] != "environment_prepare"
            or reservation["approved_digest"] != approved_digest
            or reservation["commands"] != commands or reservation["operation_id"] != operation_id
            or reservation["run_id"] != run_dir.name or reservation["child_run"] != run_dir.name
        ):
            raise ValueError("准备预算身份不一致")
        payload = json.loads((run_dir / result_ref).read_text(encoding="utf-8"))
        expected_refs = {
            f"executions/environment-prepare/{operation_id}/{index:02d}/execution.json"
            for index in range(len(commands))
        }
        if (
            payload["operation_id"] != operation_id or payload["approved_digest"] != approved_digest
            or payload["status"] != "completed" or set(payload["execution_sha256"]) != expected_refs
        ):
            raise ValueError("准备结果不成功或身份不一致")
        for ref, digest in payload["execution_sha256"].items():
            if not (run_dir / ref).resolve().is_relative_to(run_dir.resolve()):
                raise ValueError("准备执行引用越界")
            raw = (run_dir / ref).read_bytes()
            lease = ExecutionLease.model_validate_json(raw)
            command = build_verification_shell_command(commands[int(Path(ref).parent.name)])
            command_parts = [command] if isinstance(command, str) else command
            if (
                hashlib.sha256(raw).hexdigest() != digest or lease.status != "completed"
                or lease.returncode != 0 or lease.termination_unconfirmed
                or lease.execution_id != operation_id or lease.run_id != run_dir.name
                or lease.step != "environment_prepare"
                or lease.command != [redact_text(part) for part in command_parts]
            ):
                raise ValueError("准备执行证据不一致")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("本批准版本已开始环境准备，但成功终态不可验证；禁止自动重放") from exc
