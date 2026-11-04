from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from .agent_approval_policy import validate_bounded_approval_freshness
from .agent_change_contract import ChangeContract, ExecutionPlan
from .agent_change_control import change_budget_snapshot
from .agent_change_fix_packet import load_current_fix_packet
from .agent_checkpoint_history import ready_checkpoint_origin
from .agent_change_revision import ChangeRevisionAssessment, assess_change_revision
from .agent_change_revision_runtime import revise_change_run
from .agent_change_run import load_change_run_context, project_agent_plan, validate_change_projection
from .agent_contract import AgentCheckpoint, AgentPlan, AgentState
from .agent_environment_preparation import prepare_change_environment, require_completed_change_environment
from .agent_persistence import load_agent_checkpoint, read_agent_trace
from .agent_run import AgentRun
from .agent_runtime_support import (
    bound_repo, capture_bound_workspace, load_agent_bundle, validate_dispatch_artifacts,
)
from .agent_worker_evidence import require_child_quiescent
from .project_config import load_project_config
from .provider_session import load_provider_sessions
from .risk_review import match_required_reviews
from .scope_path_matching import matching_patterns, scope_paths_are_case_insensitive


_FILE_ARGUMENT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py\Z")
_FLAGS = {"-q", "-qq", "-v", "-vv", "-s", "-x", "--disable-warnings",
          "--strict-markers", "--strict-config"}
_VALUE_FLAGS = {"-k", "-m", "-o", "--override-ini", "--timeout", "--timeout-method",
                "--basetemp", "--maxfail", "--tb"}


def _pytest_arguments(command: str) -> list[str]:
    # 只识别明确的 pytest 调用；未知选项或 shell 语法整条跳过，不猜测读取参数的含义。
    if any(char in command for char in "$`;&|<>\\\r\n'%!^()"):
        return []
    try:
        args = shlex.split(command)
    except ValueError:
        return []
    if args[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"]):
        return args[3:]
    elif args[:1] == ["pytest"]:
        return args[1:]
    return []


def _pytest_files(command: str) -> set[str]:
    args = _pytest_arguments(command)
    paths: set[str] = set()
    index = 0
    while index < len(args):
        arg = args[index]
        flag, separator, value = arg.partition("=")
        if arg in _FLAGS:
            index += 1
            continue
        if flag in _VALUE_FLAGS:
            if separator and value:
                index += 1
                continue
            if not separator and index + 1 < len(args):
                index += 2
                continue
            return set()
        path = arg.split("::", 1)[0]
        if not _FILE_ARGUMENT.fullmatch(path) or any(
            part in {".", "..", ".git"} for part in path.split("/")
        ):
            return set()
        paths.add(path)
        index += 1
    return paths


def _missing_file(repo: Path, relative: str) -> bool:
    path = repo
    for part in relative.split("/"):
        path /= part
        if path.is_symlink():
            return False
    return not path.exists() and path.resolve().is_relative_to(repo.resolve())


def prepare_change_attempt(
    workspace: Path, run: str, *, complete_scope: Callable[[str], AgentRun | None],
    event_reporter: Callable[[str], None] | None,
    progress_reporter: Callable[[str, int], None] | None,
) -> AgentRun | None:
    """先检查已有修订来源，再准备环境并补齐范围；这些动作都在 Worker 派发前。"""
    require_verification_scope_resume(workspace, *load_agent_bundle(workspace, run))

    def report_preparation(step: str, elapsed: int) -> None:
        if event_reporter is not None:
            action = "已开始" if elapsed == 0 else "运行中"
            event_reporter(f"环境准备{action}，已用时 {elapsed} 秒；Worker 尚未启动")
        elif progress_reporter is not None:
            progress_reporter(step, elapsed)

    failure = prepare_change_environment(workspace, run, progress_reporter=report_preparation)
    if failure is None and complete_scope(run) is not None and event_reporter is not None:
        event_reporter("执行前已在原合同与预算内补全验证文件范围，继续当前工作项")
    return failure


def _proposed_plan(
    repo: Path, contract: ChangeContract, execution: ExecutionPlan, work_item_id: str,
) -> ExecutionPlan | None:
    # 读取需求不授予写入权：只考虑合同逐字列出的具体路径，且不把已存在的回归测试纳入写范围。
    projected = project_agent_plan(contract, execution)
    commands = next(item.verification for item in projected.work_items if item.work_item_id == work_item_id)
    referenced = set().union(*(_pytest_files(command) for command in commands))
    case_sensitive = not scope_paths_are_case_insensitive(repo)
    assigned = [path for item in execution.work_items for path in item.likely_files]
    additions = sorted(
        path for path in referenced
        if path in contract.authority_envelope.allowed_paths
        and not matching_patterns(path, assigned, case_sensitive=case_sensitive)
        and not matching_patterns(path, contract.authority_envelope.forbidden_paths,
                                  case_sensitive=case_sensitive)
        and _missing_file(repo, path)
    )
    if not additions:
        return None
    limit = contract.authority_envelope.max_changed_files
    if limit is not None and len(set(assigned) | set(additions)) > limit:
        return None
    risks = match_required_reviews(repo, additions, load_project_config(repo).risk.required_reviews)
    if any(hit.id not in contract.authorized_risk_reviews for hit in risks):
        return None
    proposed = execution.model_copy(deep=True)
    proposed.plan_revision += 1
    target = next(item for item in proposed.work_items if item.work_item_id == work_item_id)
    target.likely_files.extend(additions)
    return ExecutionPlan.model_validate(proposed.model_dump(mode="json"))


def _require_pending_progress(plan: AgentPlan, state: AgentState) -> None:
    found = False
    for item in plan.work_items:
        if item.work_item_id == state.current_work_item:
            found = True
        if item.status != ("pending" if found else "completed"):
            raise ValueError("执行前补齐不能恢复失败项或改变工作项顺序")
    if not found:
        raise ValueError("执行前补齐缺少当前工作项")


def complete_verification_scope_if_eligible(
    workspace: Path, run_dir: Path, state: AgentState, plan: AgentPlan, metadata: dict[str, object],
) -> AgentRun | None:
    """持有 RunMutationLock 时补齐首次执行前可确定的测试文件遗漏。"""
    if state.run_kind == "change" and state.phase == "ready":
        require_verification_scope_resume(workspace, run_dir, state, plan, metadata)
    try:
        if (
            state.run_kind != "change" or state.phase != "ready"
            or state.allowed_actions != ["next", "replan", "human"]
            or state.active_child_run or state.active_operation_id or state.operation_started
            or state.active_candidate_sha or state.active_planning_execution_id
            or state.handoff_status != "none"
        ):
            return None
        context = load_change_run_context(run_dir, state, plan, metadata)
        if context is None or not context.contract.approval_is_current():
            return None
        require_completed_change_environment(run_dir, context.contract)
        _require_pending_progress(plan, state)
        validate_dispatch_artifacts(run_dir, state, plan)
        budget = change_budget_snapshot(run_dir, state, context.contract)
        if budget.worker_attempts_used or any(context.contract.side_effect_policy.model_dump(
            exclude={"schema_version"},
        ).values()):
            return None
        repo = bound_repo(run_dir)
        proposed = _proposed_plan(repo, context.contract, context.execution_plan, state.current_work_item)
        if proposed is None:
            return None
        require_child_quiescent(run_dir)
        if any(h.owner != "vega" or h.lifecycle in {"active", "waiting_user"}
               for h in load_provider_sessions(run_dir).handles.values()):
            return None
        before = capture_bound_workspace(run_dir)
        if (
            before.fingerprint != state.workspace_fingerprint
            or before.head_sha != state.accepted_checkpoint_sha
            or before.staged_diff or before.unstaged_diff or before.untracked_files
            or before.unsafe_index_paths or not before.git_control_complete
        ):
            return None
        validate_bounded_approval_freshness(repo, context.contract, proposed)
        assessment = assess_change_revision(
            repo=repo, changed_files=list(before.changed_files), current_contract=context.contract,
            proposed_contract=context.contract, current_plan=context.execution_plan,
            proposed_plan=proposed, budget=budget,
        )
        if assessment.outcome != "auto_apply":
            return None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    return revise_change_run(
        run_dir, state, plan, metadata, proposed_contract=context.contract,
        proposed_execution_plan=proposed,
        verification_scope_source=f"checkpoints/{state.latest_checkpoint_id}.json",
    )


def require_verification_scope_resume(
    workspace: Path, run_dir: Path, state: AgentState, plan: AgentPlan, metadata: dict[str, object],
) -> None:
    """派发前重算原计划，预检修订不凭 ready 标签取得 Writer。"""
    if state.run_kind != "change" or state.latest_checkpoint_id is None:
        return
    dispatch_state = state
    state, checkpoint = ready_checkpoint_origin(run_dir, state)
    previous = state.execution_plan_revision - 1
    archive_ref = f"execution-plans/execution-plan-revision-{previous:03d}.json"
    events = [event for event in read_agent_trace(run_dir / "trace.jsonl")
              if event.get("event") == "change_execution_plan_auto_applied"
              and event.get("route_reason") == "verification.scope_completed"
              and event.get("work_item") == state.current_work_item
              and archive_ref in event.get("artifact_refs", [])]
    if events and "repair" in state.allowed_actions:
        # 首次派发后由原 Core 失败证据与 Fix Packet 授予返修，不能拿历史预检当新的派发证据。
        validate_dispatch_artifacts(run_dir, dispatch_state, plan)
        load_current_fix_packet(workspace, run_dir, dispatch_state)
        return
    assessments = [(ref, ChangeRevisionAssessment.model_validate_json(_read_ref(run_dir, ref)))
                   for ref in checkpoint.evidence_refs if ref.startswith("revisions/")]
    if not events and not any(item.verification_scope_source for _, item in assessments):
        return
    if len(events) != 1 or len(assessments) != 1:
        raise ValueError("执行前补齐缺少唯一修订来源")
    ref, assessment = assessments[0]
    source_ref = assessment.verification_scope_source
    if source_ref is None or assessment.outcome != "auto_apply":
        raise ValueError("执行前补齐来源字段缺失或无效")
    _require_revision_source(run_dir, state, plan, metadata, checkpoint, ref, source_ref)


def _read_ref(run_dir: Path, ref: str) -> str:
    path = (run_dir / ref).resolve(strict=True)
    if not path.is_relative_to(run_dir.resolve(strict=True)):
        raise ValueError("执行前补齐来源越过 run 边界")
    return path.read_text(encoding="utf-8")


def _require_revision_source(
    run_dir: Path, state: AgentState, plan: AgentPlan, metadata: dict[str, object],
    checkpoint: AgentCheckpoint, assessment_ref: str, source_ref: str,
) -> None:
    context = load_change_run_context(run_dir, state, plan, metadata)
    if context is None:
        raise ValueError("执行前补齐缺少合同投影")
    _read_ref(run_dir, source_ref)
    source = load_agent_checkpoint((run_dir / source_ref).resolve(strict=True))
    previous = context.execution_plan.plan_revision - 1
    old_execution_ref = f"execution-plans/execution-plan-revision-{previous:03d}.json"
    old_plan_ref = f"plans/plan-revision-{previous:03d}.json"
    required = {source_ref, old_execution_ref, old_plan_ref, assessment_ref}
    if (
        source_ref != f"checkpoints/{source.checkpoint_id}.json"
        or source.run_id != state.run_id or source.phase != "ready" or source.status != "safe"
        or source.current_work_item != state.current_work_item
        or source.workspace_fingerprint != state.workspace_fingerprint
        or source.operation_started or source.external_side_effects != "none"
        or source.state_version + 3 != state.state_version
        or not required.issubset(checkpoint.evidence_refs)
    ):
        raise ValueError("执行前补齐来源 Checkpoint 不可信")
    events = [event for event in read_agent_trace(run_dir / "trace.jsonl")
              if event.get("event") == "change_execution_plan_auto_applied"
              and event.get("state_version") == state.state_version]
    if len(events) != 1 or not required.issubset(events[0].get("artifact_refs", [])):
        raise ValueError("执行前补齐缺少对应修订事件")
    old_execution = ExecutionPlan.model_validate_json(_read_ref(run_dir, old_execution_ref))
    old_plan = AgentPlan.model_validate_json(_read_ref(run_dir, old_plan_ref))
    validate_change_projection(context.contract, old_execution, old_plan)
    _require_pending_progress(old_plan, state)
    expected = _proposed_plan(bound_repo(run_dir), context.contract, old_execution, state.current_work_item)
    if expected != context.execution_plan:
        raise ValueError("执行前补齐无法从归档计划重算")
