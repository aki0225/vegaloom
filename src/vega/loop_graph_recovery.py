from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .execution_control import (
    ACTIVE_EXECUTION_STATUSES,
    TERMINAL_EXECUTION_STATUSES,
    ExecutionLease,
    ExecutionRecord,
    find_execution_records,
    is_process_alive,
)
from .loop_graph_state import GRAPH_SCHEMA_VERSION
from .loop_step_result import (
    AttemptIdentity,
    StepResultManifest,
    StepResultValidationError,
    hash_command,
    read_step_result,
)
from .models import LoopAutomationState
from .redaction import redact_text, redact_value
from .workspace_check import WorkspaceSnapshot, capture_review_workspace

GRAPH_RUN_CONFIG_REF = "graph/run-config.json"
GRAPH_RUN_CONFIG_SCHEMA_VERSION = 1
WORKSPACE_EVIDENCE_SCHEMA_VERSION = 1
ATTEMPT_MANIFEST_SCHEMA_VERSION = 1
GRAPH_OPERATION_LEASE_SCHEMA_VERSION = 1
CONTROL_ARTIFACT_MAX_BYTES = 128 * 1024
GRAPH_RECOVERY_TRACE_MAX_BYTES = 32 * 1024 * 1024
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PREFIXED_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
LEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

GraphFaultPoint = Literal[
    "before_external_execution",
    "after_external_effect_before_terminal_execution",
    "after_step_result_before_state",
    "after_state_before_checkpoint",
    "after_terminal_state_before_checkpoint",
    "after_decision_consumption_before_state",
]
ReconciliationAction = Literal[
    "safe_execute",
    "safe_reuse_step_result",
    "safe_resume_from_state",
    "terminal_recovery",
    "needs_human",
]
GraphOperation = Literal["execute", "recover", "resume_decision"]


class GraphRecoveryValidationError(ValueError):
    pass


class GraphRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = GRAPH_RUN_CONFIG_SCHEMA_VERSION
    run_id: str
    automation_mode: Literal["assist", "auto"]
    worker_name: str
    reviewer_name: str
    verify: bool
    timeout_seconds: int = Field(ge=1)


class GraphOperationLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = GRAPH_OPERATION_LEASE_SCHEMA_VERSION
    run_id: str
    lease_id: str
    operation: GraphOperation
    owner_pid: int = Field(ge=1)
    acquired_at: str
    status: Literal["active", "released"] = "active"
    supersedes_lease_id: str | None = None
    released_at: str | None = None


class WorkspaceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = WORKSPACE_EVIDENCE_SCHEMA_VERSION
    run_id: str
    iteration: int
    phase: Literal["before-worker", "after-worker"]
    fingerprint: str
    head_sha: str
    status_sha256: str
    full_diff_sha256: str
    staged_diff_sha256: str
    unstaged_diff_sha256: str
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    baseline_raw_status: str | None = None
    baseline_tracked_files: list[str] = Field(default_factory=list)
    baseline_untracked_files: list[str] = Field(default_factory=list)
    baseline_untracked_manifest_sha256: str | None = None
    baseline_capture_complete: bool | None = None
    captured_at: str


@dataclass(frozen=True)
class ReconciliationResult:
    action: ReconciliationAction
    reason: str
    next_node: str | None
    attempt: AttemptIdentity | None = None
    step_result: StepResultManifest | None = None


@contextmanager
def hold_graph_operation_lease(
    run_dir: Path,
    operation: GraphOperation,
) -> Iterator[GraphOperationLease]:
    """串行化首次图执行、恢复和 HITL resume，阻止同一 run 并发推进。"""

    lock_path, metadata_path = _graph_operation_lease_paths(run_dir)
    lock_file = _acquire_graph_operation_lock(run_dir, lock_path)
    operation_failed = False
    try:
        previous = _read_graph_operation_lease(
            run_dir,
            metadata_path,
            allow_missing=True,
        )
        lease = GraphOperationLease(
            run_id=run_dir.name,
            lease_id=uuid4().hex,
            operation=operation,
            owner_pid=os.getpid(),
            acquired_at=datetime.now(UTC).isoformat(),
            supersedes_lease_id=(
                previous.lease_id
                if previous is not None and previous.status == "active"
                else None
            ),
        )
        _write_graph_operation_lease(
            run_dir,
            metadata_path,
            lease,
        )
        yield lease
    except BaseException:
        operation_failed = True
        raise
    finally:
        try:
            if "lease" in locals():
                _release_graph_operation_lease(
                    run_dir,
                    metadata_path,
                    lease,
                )
        except GraphRecoveryValidationError:
            if not operation_failed:
                raise
        finally:
            _release_graph_operation_lock(lock_file)


def write_graph_run_config(
    run_dir: Path,
    *,
    automation_mode: Literal["assist", "auto"],
    worker_name: str,
    reviewer_name: str,
    verify: bool,
    timeout_seconds: int,
) -> GraphRunConfig:
    config = GraphRunConfig(
        run_id=run_dir.name,
        automation_mode=automation_mode,
        worker_name=worker_name,
        reviewer_name=reviewer_name,
        verify=verify,
        timeout_seconds=timeout_seconds,
    )
    path = _resolve_run_ref(run_dir, GRAPH_RUN_CONFIG_REF, "graph_run_config")
    if path.exists():
        existing = read_graph_run_config(run_dir)
        if existing != config:
            raise GraphRecoveryValidationError(
                "graph run config 已固定，不能在恢复时修改"
            )
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    _write_json_atomic(path, config.model_dump(mode="json"))
    return config


def read_graph_run_config(run_dir: Path) -> GraphRunConfig:
    path = _resolve_run_ref(run_dir, GRAPH_RUN_CONFIG_REF, "graph_run_config")
    payload = _read_json(path, "graph run config")
    try:
        config = GraphRunConfig.model_validate(payload)
    except ValidationError as exc:
        raise GraphRecoveryValidationError("graph run config schema 不合法") from exc
    if config.schema_version != GRAPH_RUN_CONFIG_SCHEMA_VERSION:
        raise GraphRecoveryValidationError("graph run config schema 不受支持")
    if config.run_id != run_dir.name:
        raise GraphRecoveryValidationError("graph run config run_id 不一致")
    if not config.worker_name.strip() or not config.reviewer_name.strip():
        raise GraphRecoveryValidationError("graph run config runner name 不能为空")
    return config


def capture_workspace_evidence(
    run_dir: Path,
    repo_path: Path,
    *,
    iteration: int,
    phase: Literal["before-worker", "after-worker"],
    baseline: WorkspaceSnapshot | None = None,
) -> WorkspaceEvidence:
    snapshot = capture_review_workspace(repo_path)
    evidence = WorkspaceEvidence(
        run_id=run_dir.name,
        iteration=iteration,
        phase=phase,
        fingerprint=f"sha256:{snapshot.fingerprint}",
        head_sha=snapshot.head_sha,
        status_sha256=snapshot.status_sha256,
        full_diff_sha256=snapshot.full_diff_sha256,
        staged_diff_sha256=snapshot.staged_diff_sha256,
        unstaged_diff_sha256=snapshot.unstaged_diff_sha256,
        untracked_manifest_sha256=snapshot.untracked_manifest_sha256,
        ignored_manifest_sha256=snapshot.ignored_manifest_sha256,
        baseline_raw_status=baseline.raw_status if baseline is not None else None,
        baseline_tracked_files=(
            sorted(baseline.tracked_files) if baseline is not None else []
        ),
        baseline_untracked_files=(
            sorted(baseline.untracked_files) if baseline is not None else []
        ),
        baseline_untracked_manifest_sha256=(
            baseline.untracked_manifest_sha256 if baseline is not None else None
        ),
        baseline_capture_complete=(
            baseline.capture_complete if baseline is not None else None
        ),
        captured_at=datetime.now(UTC).isoformat(),
    )
    path = _workspace_evidence_path(run_dir, iteration, phase)
    if path.exists():
        existing = read_workspace_evidence(run_dir, iteration, phase)
        if (
            existing.fingerprint == evidence.fingerprint
            and existing.head_sha == evidence.head_sha
        ):
            return existing
        raise GraphRecoveryValidationError(
            f"workspace evidence 已存在且现场不同，不得覆盖：{phase}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    _write_json_atomic(path, evidence.model_dump(mode="json"))
    return evidence


def read_workspace_evidence(
    run_dir: Path,
    iteration: int,
    phase: Literal["before-worker", "after-worker"],
) -> WorkspaceEvidence:
    path = _workspace_evidence_path(run_dir, iteration, phase)
    payload = _read_json(path, "workspace evidence")
    try:
        evidence = WorkspaceEvidence.model_validate(payload)
    except ValidationError as exc:
        raise GraphRecoveryValidationError("workspace evidence schema 不合法") from exc
    if evidence.run_id != run_dir.name:
        raise GraphRecoveryValidationError("workspace evidence run_id 不一致")
    if evidence.iteration != iteration or evidence.phase != phase:
        raise GraphRecoveryValidationError("workspace evidence identity 不一致")
    _require_prefixed_sha256(evidence.fingerprint, "workspace fingerprint")
    for field in (
        "status_sha256",
        "full_diff_sha256",
        "staged_diff_sha256",
        "unstaged_diff_sha256",
        "untracked_manifest_sha256",
        "ignored_manifest_sha256",
    ):
        _require_hex_sha256(getattr(evidence, field), field)
    return evidence


def workspace_snapshot_from_evidence(
    evidence: WorkspaceEvidence,
) -> WorkspaceSnapshot:
    if evidence.phase != "before-worker":
        raise GraphRecoveryValidationError("只有 before-worker evidence 可恢复 baseline")
    if (
        evidence.baseline_raw_status is None
        or evidence.baseline_untracked_manifest_sha256 is None
        or evidence.baseline_capture_complete is None
    ):
        raise GraphRecoveryValidationError("before-worker evidence 缺少 baseline")
    return WorkspaceSnapshot(
        raw_status=evidence.baseline_raw_status,
        tracked_files=frozenset(evidence.baseline_tracked_files),
        untracked_files=frozenset(evidence.baseline_untracked_files),
        untracked_manifest_sha256=evidence.baseline_untracked_manifest_sha256,
        capture_complete=evidence.baseline_capture_complete,
    )


def current_workspace_fingerprint(repo_path: Path) -> str:
    return f"sha256:{capture_review_workspace(repo_path).fingerprint}"


def ensure_graph_recovery_execution_quiescent(run_dir: Path) -> None:
    """持有 run 级图锁后，拒绝仍可能产生外部副作用的已知执行主体。"""

    try:
        records = find_execution_records(run_dir)
    except ValueError as exc:
        raise GraphRecoveryValidationError(
            "execution 证据不可信，已拒绝 Graph recovery"
        ) from exc
    for record in records:
        lease = record.lease
        if lease.termination_unconfirmed:
            raise GraphRecoveryValidationError(
                "owned process tree 终止未确认，已拒绝 Graph recovery"
            )
        if lease.child_pid is not None and is_process_alive(lease.child_pid):
            raise GraphRecoveryValidationError(
                "execution child PID 仍存活，已拒绝 Graph recovery"
            )
        if (
            is_process_alive(lease.owner_pid)
            and (
                lease.status in ACTIVE_EXECUTION_STATUSES
                or not _terminal_execution_commit_is_trusted(
                    run_dir,
                    record,
                )
            )
        ):
            raise GraphRecoveryValidationError(
                "execution owner PID 仍存活，已拒绝 Graph recovery"
            )


def _terminal_execution_commit_is_trusted(
    run_dir: Path,
    record: ExecutionRecord,
) -> bool:
    lease = record.lease
    if lease.status not in TERMINAL_EXECUTION_STATUSES:
        return False
    if lease.step != "worker" or lease.step_id is None:
        return False
    try:
        step_result = read_step_result(run_dir, lease.step_id)
    except (OSError, StepResultValidationError, ValueError):
        return False
    try:
        execution_ref = record.path.relative_to(run_dir).as_posix()
    except ValueError:
        return False
    return (
        step_result.execution_ref == execution_ref
        and step_result.execution_sha256 == _sha256_file(record.path)
    )


def read_or_create_attempt(
    run_dir: Path,
    *,
    state: LoopAutomationState,
    iteration: int,
    runner_identity: dict[str, str],
    before_workspace: WorkspaceEvidence,
    command: list[str],
    input_payload: dict[str, object],
) -> AttemptIdentity:
    path = _attempt_manifest_path(run_dir, iteration)
    command_sha256 = hash_command(command)
    policy_sha256 = _sha256_file(run_dir / "project-policy-snapshot.json")
    input_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            input_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    idempotency_key = "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "run_id": state.run_id,
                "step_id": f"worker-iteration-{iteration:02d}",
                "iteration": iteration,
                "base_head": before_workspace.head_sha,
                "before_workspace_fingerprint": before_workspace.fingerprint,
                "policy_snapshot_sha256": policy_sha256,
                "command_sha256": command_sha256,
                "input_fingerprint": input_fingerprint,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    expected = {
        "schema_version": ATTEMPT_MANIFEST_SCHEMA_VERSION,
        "run_id": state.run_id,
        "engine": "langgraph",
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "step_id": f"worker-iteration-{iteration:02d}",
        "step_name": "worker",
        "iteration": iteration,
        "idempotency_key": idempotency_key,
        "replay_class": "external_non_replayable",
        "runner_identity": runner_identity,
        "base_head": before_workspace.head_sha,
        "before_workspace_fingerprint": before_workspace.fingerprint,
        "policy_snapshot_sha256": policy_sha256,
        "command_sha256": command_sha256,
        "input_fingerprint": input_fingerprint,
    }
    if path.exists():
        attempt = _read_attempt(path)
        actual = attempt.model_dump(mode="json")
        for key, value in expected.items():
            if actual.get(key) != value:
                raise GraphRecoveryValidationError(
                    f"attempt manifest 输入身份发生变化：{key}"
                )
        return attempt
    attempt = AttemptIdentity(
        **expected,
        attempt_id=f"attempt-{uuid4().hex}",
        started_at=datetime.now(UTC).isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    _write_json_atomic(path, attempt.model_dump(mode="json"))
    return attempt


def read_attempt(run_dir: Path, iteration: int) -> AttemptIdentity:
    return _read_attempt(_attempt_manifest_path(run_dir, iteration))


def reconcile_graph_resume(
    run_dir: Path,
    *,
    state: LoopAutomationState,
    next_node: str | None,
) -> ReconciliationResult:
    if state.run_id != run_dir.name:
        raise GraphRecoveryValidationError("state.run_id 与 run 目录不一致")
    if state.engine != "langgraph":
        raise GraphRecoveryValidationError("只有 langgraph run 可以图恢复")
    if state.status in {"success", "failed", "needs_human"}:
        return ReconciliationResult(
            action="terminal_recovery",
            reason="权威 state.json 已进入终态，只允许补齐 graph checkpoint",
            next_node=next_node,
        )
    if state.status != "running":
        return ReconciliationResult(
            action="needs_human",
            reason=f"业务状态 {state.status} 不能自动恢复",
            next_node=next_node,
        )
    iteration = max(1, state.current_iteration)
    if iteration != 1:
        return ReconciliationResult(
            action="needs_human",
            reason=(
                "Gate 3 自动恢复当前只注册第一轮 worker；"
                f"检测到 iteration={iteration}，为避免错绑旧 Step Result 已停止"
            ),
            next_node=next_node,
        )
    before = read_workspace_evidence(run_dir, iteration, "before-worker")
    current_fingerprint = current_workspace_fingerprint(Path(state.repo_path))
    execution_path = (
        run_dir
        / "iterations"
        / f"{iteration:02d}"
        / "executions"
        / "worker"
        / "execution.json"
    )
    attempt = (
        read_attempt(run_dir, iteration)
        if _attempt_manifest_path(run_dir, iteration).is_file()
        else None
    )

    if next_node == "execute_worker_epoch":
        if not execution_path.is_file():
            if current_fingerprint == before.fingerprint:
                return ReconciliationResult(
                    action="safe_execute",
                    reason="没有 execution，且 workspace 与 worker 输入基线一致",
                    next_node=next_node,
                    attempt=attempt,
                )
            return ReconciliationResult(
                action="needs_human",
                reason="没有 execution，但 workspace 已偏离 worker 输入基线",
                next_node=next_node,
                attempt=attempt,
            )
        execution = _read_execution(execution_path)
        if execution.termination_unconfirmed:
            return ReconciliationResult(
                action="needs_human",
                reason="execution 标记 termination_unconfirmed",
                next_node=next_node,
                attempt=attempt,
            )
        if execution.status in ACTIVE_EXECUTION_STATUSES:
            return ReconciliationResult(
                action="needs_human",
                reason=(
                    "worker execution 已被认领但尚无可信终态；"
                    "即使 child PID 未落盘也不能证明外部动作从未启动，"
                    "禁止重复启动或执行"
                ),
                next_node=next_node,
                attempt=attempt,
            )
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            try:
                step_result = read_step_result(
                    run_dir,
                    f"worker-iteration-{iteration:02d}",
                )
            except StepResultValidationError as exc:
                return ReconciliationResult(
                    action="needs_human",
                    reason=f"terminal execution 缺少可信 step result：{exc}",
                    next_node=next_node,
                    attempt=attempt,
                )
            if current_fingerprint != step_result.after_workspace_fingerprint:
                return ReconciliationResult(
                    action="needs_human",
                    reason="当前 workspace 与 step result 绑定现场不一致",
                    next_node=next_node,
                    attempt=attempt,
                    step_result=step_result,
                )
            return ReconciliationResult(
                action="safe_reuse_step_result",
                reason="terminal execution、step result 与 workspace 一致",
                next_node=next_node,
                attempt=attempt,
                step_result=step_result,
            )

    if next_node == "reconcile_workspace" and state.current_step == "verify":
        try:
            step_result = read_step_result(
                run_dir,
                f"worker-iteration-{iteration:02d}",
            )
        except StepResultValidationError as exc:
            return ReconciliationResult(
                action="needs_human",
                reason=f"state 已推进但 worker step result 不可信：{exc}",
                next_node=next_node,
                attempt=attempt,
            )
        if current_fingerprint != step_result.after_workspace_fingerprint:
            return ReconciliationResult(
                action="needs_human",
                reason="state 已推进，但 workspace 与 worker step result 不一致",
                next_node=next_node,
                attempt=attempt,
                step_result=step_result,
            )
        return ReconciliationResult(
            action="safe_resume_from_state",
            reason="state.json 已进入 verify，checkpoint 不得把业务状态回退到 worker",
            next_node=next_node,
            attempt=attempt,
            step_result=step_result,
        )

    return ReconciliationResult(
        action="needs_human",
        reason=f"Gate 3 尚未注册该恢复节点：{next_node or '<terminal>'}",
        next_node=next_node,
        attempt=attempt,
    )


def render_graph_recovery_report(
    run_dir: Path,
    *,
    state: LoopAutomationState,
    result: ReconciliationResult,
    request_reason: str,
) -> str:
    return redact_text(
        "\n".join(
            [
                "# LangGraph Recovery Report",
                "",
                f"- run：`{run_dir.name}`",
                f"- 请求原因：{request_reason}",
                f"- 原业务状态：`{state.status}`",
                f"- 原业务步骤：`{state.current_step}`",
                f"- checkpoint next：`{result.next_node or '<terminal>'}`",
                f"- reconciliation：`{result.action}`",
                f"- 判定：{result.reason}",
                "",
                "## 约束",
                "",
                "- `state.json` 仍是当前 run 的权威业务状态。",
                "- execution 终态未知时不得重复启动 worker。",
                "- workspace、step result 或 policy 无法对齐时必须安全停止。",
            ]
        ).rstrip()
        + "\n"
    )


def render_checkpoint_validation_failure_report(
    run_dir: Path,
    *,
    state: LoopAutomationState,
    request_reason: str,
    validation_error: str,
) -> str:
    """渲染 checkpoint 信任链失败后的安全停止报告。"""

    return redact_text(
        "\n".join(
            [
                "# LangGraph Recovery Report",
                "",
                f"- run：`{run_dir.name}`",
                f"- 请求原因：{request_reason}",
                f"- 原业务状态：`{state.status}`",
                f"- 原业务步骤：`{state.current_step}`",
                "- checkpoint 状态：未通过可信校验，"
                "未调用恢复用可写 SQLite checkpointer",
                "- reconciliation：`needs_human`",
                "- reason code：`checkpoint_validation_failed`",
                f"- 校验错误：{validation_error}",
                "",
                "## 约束",
                "",
                "- `state.json` 仍是当前 run 的权威业务状态。",
                "- 不得根据未封存的 SQLite 主库或事务侧文件推断 graph 游标。",
                "- 不得删除、移动或回放未绑定的 journal、WAL、SHM 或 master journal。",
                "- 不得删除、移动或回放 checkpoint pending marker；"
                "它表示 Graph 提交可能未完成。",
                "- 不得重复启动 worker、reviewer 或其他外部 provider 动作。",
                "- 保留 `graph/` 原现场，由人工核对 execution、Step Result 和 workspace。",
            ]
        ).rstrip()
        + "\n"
    )


def write_graph_recovery_report(
    run_dir: Path,
    content: str,
) -> None:
    path = _resolve_run_ref(
        run_dir,
        "graph-recovery-report.md",
        "graph_recovery_report",
    )
    _write_bytes_atomic(
        run_dir,
        path,
        redact_text(content).encode("utf-8"),
    )


def append_graph_recovery_trace(
    run_dir: Path,
    event: str,
    **payload: object,
) -> None:
    path = _resolve_run_ref(
        run_dir,
        "trace.jsonl",
        "graph_recovery_trace",
    )
    try:
        existing = path.read_bytes() if path.exists() else b""
    except OSError as exc:
        raise GraphRecoveryValidationError(
            "无法读取 Graph recovery trace"
        ) from exc
    item = redact_value(
        {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **payload,
        }
    )
    line = (
        json.dumps(item, ensure_ascii=False).encode("utf-8")
        + b"\n"
    )
    separator = b"\n" if existing and not existing.endswith(b"\n") else b""
    content = existing + separator + line
    if len(content) > GRAPH_RECOVERY_TRACE_MAX_BYTES:
        raise GraphRecoveryValidationError(
            "Graph recovery trace 超过大小限制"
        )
    _write_bytes_atomic(run_dir, path, content)


def predicted_runner_command(
    runner: object,
    repo_path: Path,
    sandbox: str,
) -> list[str]:
    builder = getattr(runner, "build_command", None)
    if not callable(builder):
        raise GraphRecoveryValidationError(
            "Gate 3 外部 worker 必须提供 build_command，才能在启动前冻结 command identity"
        )
    command = builder(repo_path, sandbox)
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) for item in command)
    ):
        raise GraphRecoveryValidationError("runner build_command 返回值不合法")
    return command


def runner_identity(
    runner: object,
    sandbox: str,
) -> dict[str, str]:
    identity_builder = getattr(runner, "execution_identity", None)
    if callable(identity_builder):
        identity = identity_builder(sandbox)
        if (
            not isinstance(identity, dict)
            or not identity
            or any(
                not isinstance(key, str)
                or not key
                or not isinstance(value, str)
                or not value
                for key, value in identity.items()
            )
        ):
            raise GraphRecoveryValidationError(
                "runner execution_identity 返回值不合法"
            )
        return identity

    identity = {"kind": type(runner).__name__}
    options = getattr(runner, "options", None)
    for field in ("model", "reasoning_effort", "profile"):
        value = getattr(options, field, None) if options is not None else None
        if isinstance(value, str) and value:
            identity[field] = value
    return identity


def _workspace_evidence_path(
    run_dir: Path,
    iteration: int,
    phase: Literal["before-worker", "after-worker"],
) -> Path:
    filename = (
        "workspace-before-worker.json"
        if phase == "before-worker"
        else "workspace-after-worker.json"
    )
    return _resolve_run_ref(
        run_dir,
        f"iterations/{iteration:02d}/{filename}",
        "workspace_evidence",
    )


def _attempt_manifest_path(run_dir: Path, iteration: int) -> Path:
    return _resolve_run_ref(
        run_dir,
        f"iterations/{iteration:02d}/executions/worker/attempt.json",
        "attempt_manifest",
    )


def _read_attempt(path: Path) -> AttemptIdentity:
    payload = _read_json(path, "attempt manifest")
    try:
        attempt = AttemptIdentity.model_validate(payload)
    except ValidationError as exc:
        raise GraphRecoveryValidationError("attempt manifest schema 不合法") from exc
    if attempt.schema_version != ATTEMPT_MANIFEST_SCHEMA_VERSION:
        raise GraphRecoveryValidationError("attempt manifest schema 不受支持")
    return attempt


def _read_execution(path: Path) -> ExecutionLease:
    payload = _read_json(path, "execution.json")
    try:
        return ExecutionLease.model_validate(payload)
    except ValidationError as exc:
        raise GraphRecoveryValidationError("execution.json schema 不合法") from exc


def _graph_operation_lease_paths(run_dir: Path) -> tuple[Path, Path]:
    resolved_run = run_dir.resolve()
    workspace = resolved_run.parent.parent
    key = hashlib.sha256(
        str(resolved_run).casefold().encode("utf-8")
    ).hexdigest()
    lease_root = (
        workspace
        / ".tmp"
        / "vega"
        / "graph-operation-leases"
    )
    lock_path = lease_root / f"{key}.lock"
    metadata_path = lease_root / f"{key}.json"
    for path in (lock_path, metadata_path):
        _assert_no_link_or_reparse(workspace, path)
        resolved = path.resolve()
        if resolved != workspace and workspace not in resolved.parents:
            raise GraphRecoveryValidationError(
                "Graph operation lease 不能越过 workspace"
            )
    return lock_path.resolve(), metadata_path.resolve()


def _acquire_graph_operation_lock(
    run_dir: Path,
    lock_path: Path,
) -> BinaryIO:
    workspace = run_dir.resolve().parent.parent
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(workspace, lock_path)
    lock_file = lock_path.open("a+b")
    os.set_inheritable(lock_file.fileno(), False)
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
    except OSError as exc:
        lock_file.close()
        raise GraphRecoveryValidationError(
            "当前 run 已有活跃 Graph 操作，已拒绝并发执行。"
        ) from exc
    return lock_file


def _release_graph_operation_lock(lock_file: BinaryIO) -> None:
    try:
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _read_graph_operation_lease(
    run_dir: Path,
    metadata_path: Path,
    *,
    allow_missing: bool = False,
) -> GraphOperationLease | None:
    workspace = run_dir.resolve().parent.parent
    _assert_no_link_or_reparse(workspace, metadata_path)
    if allow_missing and not metadata_path.exists():
        return None
    payload = _read_json(metadata_path, "Graph operation lease")
    try:
        lease = GraphOperationLease.model_validate(payload)
    except ValidationError as exc:
        raise GraphRecoveryValidationError(
            "Graph operation lease schema 不合法"
        ) from exc
    if lease.schema_version != GRAPH_OPERATION_LEASE_SCHEMA_VERSION:
        raise GraphRecoveryValidationError(
            "Graph operation lease schema 不受支持"
        )
    if lease.run_id != run_dir.name:
        raise GraphRecoveryValidationError(
            "Graph operation lease run_id 不一致"
        )
    if not LEASE_ID_PATTERN.fullmatch(lease.lease_id):
        raise GraphRecoveryValidationError(
            "Graph operation lease id 不合法"
        )
    try:
        datetime.fromisoformat(lease.acquired_at)
    except ValueError as exc:
        raise GraphRecoveryValidationError(
            "Graph operation lease acquired_at 不合法"
        ) from exc
    if lease.supersedes_lease_id is not None and not LEASE_ID_PATTERN.fullmatch(
        lease.supersedes_lease_id
    ):
        raise GraphRecoveryValidationError(
            "Graph operation lease supersedes id 不合法"
        )
    if lease.status == "active" and lease.released_at is not None:
        raise GraphRecoveryValidationError(
            "active Graph operation lease 不得包含 released_at"
        )
    if lease.status == "released":
        if lease.released_at is None:
            raise GraphRecoveryValidationError(
                "released Graph operation lease 缺少 released_at"
            )
        try:
            datetime.fromisoformat(lease.released_at)
        except ValueError as exc:
            raise GraphRecoveryValidationError(
                "Graph operation lease released_at 不合法"
            ) from exc
    return lease


def _write_graph_operation_lease(
    run_dir: Path,
    metadata_path: Path,
    lease: GraphOperationLease,
) -> None:
    workspace = run_dir.resolve().parent.parent
    _assert_no_link_or_reparse(workspace, metadata_path)
    _write_json_atomic(
        metadata_path,
        lease.model_dump(mode="json"),
    )


def _release_graph_operation_lease(
    run_dir: Path,
    metadata_path: Path,
    lease: GraphOperationLease,
) -> None:
    if not metadata_path.exists():
        raise GraphRecoveryValidationError(
            "Graph operation lease 在释放前已丢失"
        )
    existing = _read_graph_operation_lease(
        run_dir,
        metadata_path,
    )
    assert existing is not None
    if (
        existing.lease_id != lease.lease_id
        or existing.owner_pid != lease.owner_pid
    ):
        raise GraphRecoveryValidationError(
            "Graph operation lease 所有权发生变化，拒绝删除"
        )
    if existing.status != "active":
        raise GraphRecoveryValidationError(
            "Graph operation lease 在释放前已不是 active"
        )
    _write_graph_operation_lease(
        run_dir,
        metadata_path,
        existing.model_copy(
            update={
                "status": "released",
                "released_at": datetime.now(UTC).isoformat(),
            }
        ),
    )


def _read_json(path: Path, label: str) -> object:
    if not path.is_file():
        raise GraphRecoveryValidationError(f"{label} 不存在")
    try:
        if path.stat().st_size > CONTROL_ARTIFACT_MAX_BYTES:
            raise GraphRecoveryValidationError(f"{label} 超过大小限制")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
        )
    except GraphRecoveryValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GraphRecoveryValidationError(f"{label} 无法解析") from exc


def _resolve_run_ref(run_dir: Path, ref: str, field: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise GraphRecoveryValidationError(
            f"{field} 必须是非空 POSIX 相对路径"
        )
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or ":" in pure.parts[0]
        or "." in pure.parts
        or ".." in pure.parts
    ):
        raise GraphRecoveryValidationError(f"{field} 不能越过 run 目录")
    candidate = run_dir.joinpath(*pure.parts)
    _assert_no_link_or_reparse(run_dir, candidate)
    resolved = candidate.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise GraphRecoveryValidationError(f"{field} 不能越过 run 目录")
    return resolved


def _assert_no_link_or_reparse(run_dir: Path, target: Path) -> None:
    try:
        relative = target.relative_to(run_dir)
    except ValueError as exc:
        raise GraphRecoveryValidationError(
            "Graph recovery 路径不能越过 run 目录"
        ) from exc
    current = run_dir
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            continue
        metadata = current.lstat()
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
            raise GraphRecoveryValidationError(
                "Graph recovery 路径不能包含链接或 reparse point"
            )


def _write_json_atomic(path: Path, payload: object) -> None:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if len(content.encode("utf-8")) > CONTROL_ARTIFACT_MAX_BYTES:
        raise GraphRecoveryValidationError("Graph control artifact 超过大小限制")
    temp_path = path.with_name(f".tmp-{uuid4().hex[:10]}")
    last_error: OSError | None = None
    try:
        temp_path.write_text(content, encoding="utf-8", newline="\n")
        for _ in range(10):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.02)
        assert last_error is not None
        raise last_error
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_bytes_atomic(
    run_dir: Path,
    path: Path,
    content: bytes,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, path)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    _assert_no_link_or_reparse(run_dir, temp_path)
    last_error: OSError | None = None
    try:
        temp_path.write_bytes(content)
        for _ in range(10):
            try:
                _assert_no_link_or_reparse(run_dir, path)
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.02)
        assert last_error is not None
        raise last_error
    except OSError as exc:
        raise GraphRecoveryValidationError(
            f"Graph recovery artifact 无法原子写入：{path.name}"
        ) from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GraphRecoveryValidationError(f"无法读取文件：{path.name}") from exc


def _require_hex_sha256(value: str, field: str) -> None:
    if not HEX_SHA256_PATTERN.fullmatch(value):
        raise GraphRecoveryValidationError(f"{field} 不是合法 SHA-256")


def _require_prefixed_sha256(value: str, field: str) -> None:
    if not PREFIXED_SHA256_PATTERN.fullmatch(value):
        raise GraphRecoveryValidationError(
            f"{field} 不是合法 sha256 identity"
        )


def _reject_duplicate_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise GraphRecoveryValidationError(f"JSON 包含重复字段：{key}")
        payload[key] = value
    return payload
