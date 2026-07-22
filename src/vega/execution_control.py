from __future__ import annotations

import errno
import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


ExecutionStatus = Literal[
    "starting",
    "running",
    "stop_requested",
    "stopped",
    "timed_out",
    "completed",
    "failed",
]
ExecutionReplayClass = Literal[
    "pure_replayable",
    "read_only_replayable",
    "external_non_replayable",
]
ACTIVE_EXECUTION_STATUSES = {"starting", "running", "stop_requested"}
TERMINAL_EXECUTION_STATUSES = {"stopped", "timed_out", "completed", "failed"}
_REDACTION_UNAVAILABLE_OUTPUT = "[REDACTION_UNAVAILABLE]"


class ExecutionLease(BaseModel):
    """Vega 对单个外部进程的所有权和存活记录。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    step: str
    iteration: int | None = None
    engine: Literal["linear", "langgraph"] | None = None
    graph_schema_version: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    idempotency_key: str | None = None
    replay_class: ExecutionReplayClass | None = None
    runner_identity: dict[str, str] = Field(default_factory=dict)
    base_head: str | None = None
    before_workspace_fingerprint: str | None = None
    policy_snapshot_sha256: str | None = None
    input_fingerprint: str | None = None
    command_sha256: str | None = None
    process_output_sha256: str | None = None
    process_output_bytes: int = Field(default=0, ge=0)
    owner_pid: int = Field(ge=1)
    child_pid: int | None = Field(default=None, ge=1)
    termination_unconfirmed: bool = False
    command: list[str] = Field(default_factory=list)
    started_at: str
    last_heartbeat: str
    lease_expires_at: str
    deadline: str
    status: ExecutionStatus = "starting"
    reason: str | None = None
    returncode: int | None = None
    finished_at: str | None = None


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    requested_at: str
    requester_pid: int = Field(ge=1)


class RunStopLatch(StopRequest):
    """同一 run 首次广播 stop 的不可覆盖事实。"""

    run_id: str
    latch_id: str


class StopLatchAuditEntry(BaseModel):
    """stop latch 的追加式审计事件。"""

    model_config = ConfigDict(extra="forbid")

    event: Literal[
        "broadcast_started",
        "execution_stop_written",
        "execution_stop_write_failed",
        "execution_rejected_before_start",
        "broadcast_completed",
        "broadcast_failed",
    ]
    run_id: str
    latch_id: str
    recorded_at: str
    execution_ref: str | None = None
    detail: str | None = None


class ExecutionAlreadyExistsError(RuntimeError):
    pass


class ExecutionStopLatchedError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerExecutionContext:
    """Runner 的窄执行上下文，不携带业务 prompt 或 worker 聊天。"""

    execution_dir: Path
    run_id: str
    step: str
    iteration: int | None = None
    engine: Literal["linear", "langgraph"] | None = None
    graph_schema_version: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    idempotency_key: str | None = None
    replay_class: ExecutionReplayClass | None = None
    runner_identity: dict[str, str] | None = None
    base_head: str | None = None
    before_workspace_fingerprint: str | None = None
    policy_snapshot_sha256: str | None = None
    input_fingerprint: str | None = None
    command_sha256: str | None = None
    exclusive_create: bool = False
    git_config_global: Path | None = None
    fault_injector: Callable[[str], None] | None = None
    heartbeat_interval_seconds: float = 1.0
    lease_timeout_seconds: float = 10.0
    terminate_grace_seconds: float = 3.0

    def __post_init__(self) -> None:
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds 必须大于 0")
        if self.lease_timeout_seconds <= self.heartbeat_interval_seconds:
            raise ValueError("lease_timeout_seconds 必须大于 heartbeat_interval_seconds")
        if self.terminate_grace_seconds < 0:
            raise ValueError("terminate_grace_seconds 不能小于 0")


@dataclass
class OwnedProcessResult:
    status: Literal["success", "error", "timed_out", "stopped"]
    output: str
    error: str | None
    returncode: int | None
    termination_unconfirmed: bool = False


@dataclass(frozen=True)
class ProcessTerminationResult:
    succeeded: bool
    detail: str


@dataclass(frozen=True)
class ExecutionRecord:
    path: Path
    lease: ExecutionLease


@dataclass(frozen=True)
class ExecutionRecoveryInspection:
    can_recover: bool
    summary: str
    record: ExecutionRecord | None = None


class ExecutionController:
    """只管理当前 Runner 明确创建并记录 PID 的进程。"""

    def __init__(self, context: RunnerExecutionContext) -> None:
        self.context = context
        self.run_dir = _run_dir_for_execution(
            context.execution_dir,
            context.run_id,
        )
        self.execution_path = context.execution_dir / "execution.json"
        self.stop_request_path = context.execution_dir / "stop-request.json"
        self.stop_latch_path = self.run_dir / "stop-latch.json"
        self.output_path = context.execution_dir / "process-output.txt"
        self.lease: ExecutionLease | None = None

    def prepare(self, command: list[str], timeout_seconds: int) -> ExecutionLease:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _assert_run_control_path(self.run_dir, self.run_dir)
        _assert_run_control_path(self.run_dir, self.context.execution_dir)
        latched_request = self._read_run_stop_latch()
        if latched_request is not None:
            self._record_latched_start_rejection(
                detail="execution 在 prepare 前检测到 run stop latch，未创建 execution。",
            )
            raise ExecutionStopLatchedError(
                "当前 run 已广播 stop，拒绝启动新的 execution。"
            )
        if self.context.fault_injector is not None:
            self.context.fault_injector(
                "after_stop_latch_precheck_before_execution_create"
            )
        self.context.execution_dir.mkdir(parents=True, exist_ok=True)
        _assert_run_control_path(self.run_dir, self.context.execution_dir)
        now = _now()
        command_sha256 = _command_sha256(command)
        if (
            self.context.command_sha256 is not None
            and self.context.command_sha256 != command_sha256
        ):
            raise ValueError("runner command 与预注册 attempt identity 不一致")
        self.lease = ExecutionLease(
            run_id=self.context.run_id,
            step=self.context.step,
            iteration=self.context.iteration,
            engine=self.context.engine,
            graph_schema_version=self.context.graph_schema_version,
            step_id=self.context.step_id,
            attempt_id=self.context.attempt_id,
            idempotency_key=self.context.idempotency_key,
            replay_class=self.context.replay_class,
            runner_identity=_redact_runner_identity(
                self.context.runner_identity or {}
            ),
            base_head=self.context.base_head,
            before_workspace_fingerprint=self.context.before_workspace_fingerprint,
            policy_snapshot_sha256=self.context.policy_snapshot_sha256,
            input_fingerprint=self.context.input_fingerprint,
            command_sha256=command_sha256,
            owner_pid=os.getpid(),
            command=[_redact_process_output(item) for item in command],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(seconds=self.context.lease_timeout_seconds)).isoformat(),
            deadline=(now + timedelta(seconds=timeout_seconds)).isoformat(),
            status="starting",
        )
        if self.context.exclusive_create:
            _write_model_create_once(self.execution_path, self.lease)
        else:
            _write_model_atomic(self.execution_path, self.lease)
        latched_request = self._read_run_stop_latch()
        if latched_request is not None:
            self._reject_latched_execution(latched_request)
            raise ExecutionStopLatchedError(
                "当前 run 已广播 stop，拒绝启动新的 execution。"
            )
        if self.context.fault_injector is not None:
            self.context.fault_injector(
                "after_stop_latch_final_prepare_check_before_start_lock"
            )
        return self.lease

    def child_started(self, child_pid: int) -> None:
        lease = self._require_lease()
        lease.child_pid = child_pid
        lease.status = "running"
        self.heartbeat()

    def heartbeat(self) -> None:
        lease = self._require_lease()
        now = _now()
        lease.last_heartbeat = now.isoformat()
        lease.lease_expires_at = (
            now + timedelta(seconds=self.context.lease_timeout_seconds)
        ).isoformat()
        _write_model_atomic(self.execution_path, lease)

    def read_stop_request(self) -> StopRequest | None:
        if self.stop_request_path.exists():
            try:
                return StopRequest.model_validate_json(
                    self.stop_request_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                # stop request 可能正由另一个 CLI 原子替换；run latch 仍可作为兜底。
                pass
        return self._read_run_stop_latch()

    def mark_stop_requested(self, request: StopRequest) -> None:
        lease = self._require_lease()
        lease.status = "stop_requested"
        lease.reason = _redact_optional_text(request.reason)
        _write_model_atomic(self.execution_path, lease)

    def finish(
        self,
        status: Literal["success", "error", "timed_out", "stopped"],
        *,
        reason: str | None,
        returncode: int | None,
    ) -> None:
        lease = self._require_lease()
        lease.status = {
            "success": "completed",
            "error": "failed",
            "timed_out": "timed_out",
            "stopped": "stopped",
        }[status]
        lease.reason = _redact_optional_text(reason)
        lease.returncode = returncode
        _bind_process_output(lease, self.output_path)
        lease.finished_at = _now().isoformat()
        lease.last_heartbeat = lease.finished_at
        _write_model_atomic(self.execution_path, lease)

    def record_termination_failure(
        self,
        *,
        reason: str,
        returncode: int | None,
    ) -> None:
        """保留 active lease，避免未确认终止被误写为可 recovery 的终态。"""

        lease = self._require_lease()
        now = _now()
        lease.reason = _redact_optional_text(reason)
        lease.returncode = returncode
        lease.termination_unconfirmed = True
        _bind_process_output(lease, self.output_path)
        lease.last_heartbeat = now.isoformat()
        lease.lease_expires_at = (
            now + timedelta(seconds=self.context.lease_timeout_seconds)
        ).isoformat()
        _write_model_atomic(self.execution_path, lease)

    def _require_lease(self) -> ExecutionLease:
        if self.lease is None:
            raise RuntimeError("execution lease 尚未创建")
        return self.lease

    def _read_run_stop_latch(self) -> StopRequest | None:
        return _read_run_stop_latch(
            self.run_dir,
            fail_closed=True,
            expected_run_id=self.context.run_id,
        )

    def _reject_latched_execution(self, request: StopRequest) -> None:
        lease = self._require_lease()
        try:
            _write_model_atomic(self.stop_request_path, request)
        except OSError:
            # run latch 本身会被 polling 读取；局部 stop request 写失败不能放行启动。
            pass
        now = _now().isoformat()
        lease.status = "stopped"
        lease.reason = _redact_optional_text(request.reason)
        lease.finished_at = now
        lease.last_heartbeat = now
        _write_model_atomic(self.execution_path, lease)
        self._record_latched_start_rejection(
            detail="execution 创建后、外部进程启动前检测到 run stop latch。",
        )

    def _record_latched_start_rejection(
        self,
        *,
        detail: str,
    ) -> None:
        latch = _read_run_stop_latch(
            self.run_dir,
            fail_closed=False,
            expected_run_id=self.context.run_id,
        )
        if latch is None:
            return
        _try_append_stop_latch_audit(
            self.run_dir,
            StopLatchAuditEntry(
                event="execution_rejected_before_start",
                run_id=self.context.run_id,
                latch_id=latch.latch_id,
                recorded_at=_now().isoformat(),
                execution_ref=_execution_ref(
                    self.run_dir,
                    self.execution_path,
                ),
                detail=_redact_process_output(detail),
            ),
        )


def run_owned_process(
    command: list[str],
    input_text: str,
    cwd: Path,
    timeout_seconds: int,
    context: RunnerExecutionContext,
) -> OwnedProcessResult:
    """运行一个可停止、可超时、可恢复判断的外部进程。

    stdout/stderr 直接落盘，避免长输出填满 PIPE 后阻塞。停止和超时只作用于本函数
    启动并写入 execution.json 的 PID，不扫描或终止系统中的其他 Codex/Node 进程。
    """

    process_env = _owned_process_environment(context)
    controller = ExecutionController(context)
    controller.prepare(command, timeout_seconds)
    process: subprocess.Popen[bytes] | None = None
    deadline = time.monotonic() + timeout_seconds
    next_heartbeat = time.monotonic()
    status: Literal["success", "error", "timed_out", "stopped"] = "error"
    error: str | None = None
    stdin_writer: threading.Thread | None = None
    output = ""
    termination_unconfirmed = False
    launch_rejected = False

    with tempfile.TemporaryFile("w+b") as output_file:
        try:
            with _hold_run_stop_start_lock(controller.run_dir):
                latched_request = controller._read_run_stop_latch()
                if latched_request is not None:
                    launch_rejected = True
                    controller._reject_latched_execution(latched_request)
                    raise ExecutionStopLatchedError(
                        "当前 run 已广播 stop，拒绝启动新的 execution。"
                    )
                if context.fault_injector is not None:
                    context.fault_injector(
                        "after_locked_stop_latch_check_before_popen"
                    )
                popen_kwargs = {
                    "cwd": cwd,
                    "stdin": subprocess.PIPE,
                    "stdout": output_file,
                    "stderr": subprocess.STDOUT,
                    **_process_group_options(),
                }
                if process_env is not None:
                    popen_kwargs["env"] = process_env
                process = subprocess.Popen(command, **popen_kwargs)
                controller.child_started(process.pid)
            stdin_writer = _start_stdin_writer(process, input_text)

            while process.poll() is None:
                request = controller.read_stop_request()
                if request is not None:
                    controller.mark_stop_requested(request)
                    termination = _terminate_owned_process(
                        process,
                        context.terminate_grace_seconds,
                    )
                    if termination.succeeded:
                        status = "stopped"
                        error = f"外部 runner 已按 stop request 停止：{request.reason}"
                    else:
                        status = "error"
                        termination_unconfirmed = True
                        error = (
                            f"外部 runner 收到 stop request，但终止未确认："
                            f"{termination.detail}；原因：{request.reason}"
                        )
                    break

                now = time.monotonic()
                if now >= deadline:
                    termination = _terminate_owned_process(
                        process,
                        context.terminate_grace_seconds,
                    )
                    if termination.succeeded:
                        status = "timed_out"
                        error = f"外部 runner 超时：{timeout_seconds}s"
                    else:
                        status = "error"
                        termination_unconfirmed = True
                        error = (
                            f"外部 runner 已超过 {timeout_seconds}s，但终止未确认："
                            f"{termination.detail}"
                        )
                    break

                if now >= next_heartbeat:
                    controller.heartbeat()
                    next_heartbeat = now + context.heartbeat_interval_seconds
                time.sleep(min(0.1, context.heartbeat_interval_seconds))

            if not termination_unconfirmed and status not in {"stopped", "timed_out"}:
                returncode = process.wait()
                if time.monotonic() >= deadline:
                    status = "timed_out"
                    error = f"外部 runner 超时：{timeout_seconds}s"
                else:
                    status = "success" if returncode == 0 else "error"
                if status == "error":
                    error = f"外部 runner 退出码：{returncode}"
        except KeyboardInterrupt:
            if process is not None and process.poll() is None:
                termination = _terminate_owned_process(
                    process,
                    context.terminate_grace_seconds,
                )
                if not termination.succeeded:
                    termination_unconfirmed = True
                    error = (
                        "Vega CLI 收到中断信号，但 owned process 终止未确认："
                        f"{termination.detail}"
                    )
                    status = "error"
                else:
                    error = "Vega CLI 收到中断信号，已停止当前 owned process。"
                    status = "stopped"
            else:
                error = "Vega CLI 收到中断信号，当前 owned process 已退出。"
                status = "stopped"
        except OSError as exc:
            if process is not None and process.poll() is None:
                termination = _terminate_owned_process(
                    process,
                    context.terminate_grace_seconds,
                )
                if not termination.succeeded:
                    termination_unconfirmed = True
                    error = (
                        f"外部 runner 执行控制失败：{exc}；"
                        f"owned process 终止未确认：{termination.detail}"
                    )
            if error is None:
                error = f"外部 runner 执行控制失败：{exc}"
            status = "error"
        finally:
            if not launch_rejected:
                if stdin_writer is not None:
                    stdin_writer.join(timeout=1.0)
                try:
                    output = _persist_redacted_output(
                        output_file,
                        controller.output_path,
                    )
                except OSError as exc:
                    if error is None:
                        error = f"runner 输出持久化失败：{exc}"
                    if status == "success":
                        status = "error"
                returncode = process.returncode if process is not None else None
                if context.fault_injector is not None and process is not None:
                    context.fault_injector(
                        "after_external_effect_before_terminal_execution"
                    )
                if termination_unconfirmed:
                    controller.record_termination_failure(
                        reason=error or "owned process 终止未确认。",
                        returncode=returncode,
                    )
                else:
                    controller.finish(
                        status,
                        reason=error,
                        returncode=returncode,
                    )

    return OwnedProcessResult(
        status=status,
        output=output,
        error=error,
        returncode=process.returncode if process is not None else None,
        termination_unconfirmed=termination_unconfirmed,
    )


def _owned_process_environment(
    context: RunnerExecutionContext,
) -> dict[str, str] | None:
    """为 owned process 注入不持久化的项目级 Git 配置。"""

    if context.git_config_global is None:
        return None
    config_path = context.git_config_global.resolve()
    if not config_path.is_file() or config_path.is_symlink():
        raise FileNotFoundError(
            f"git config global 文件不存在或不安全：{config_path}"
        )
    environment = os.environ.copy()
    environment["GIT_CONFIG_GLOBAL"] = str(config_path)
    return environment


def request_stop_for_run(run_dir: Path, reason: str) -> ExecutionRecord:
    """为指定 run 最新且仍存活的 active execution 写入 stop request。"""

    normalized_reason = _redact_process_output(reason.strip())
    if not normalized_reason:
        raise ValueError("stop 必须提供原因，方便后续追溯。")
    active_records = [
        (record, _active_execution_stale_reason(record.lease))
        for record in find_execution_records(run_dir)
        if record.lease.status in ACTIVE_EXECUTION_STATUSES
    ]
    if not active_records:
        raise ValueError("当前 run 没有可停止的 active execution；如 CLI 已中断，请使用 recover。")
    unconfirmed_records = [
        record
        for record, _ in active_records
        if record.lease.termination_unconfirmed
    ]
    if unconfirmed_records:
        record = max(
            unconfirmed_records,
            key=lambda item: _parse_datetime(item.lease.last_heartbeat),
        )
        raise ValueError(
            "owned process tree 终止未确认，已拒绝重复 stop；"
            f"请人工检查 `{record.path}` 和系统进程后再启动新的 run。"
        )
    live_active_records = [
        record for record, stale_reason in active_records if stale_reason is None
    ]
    if live_active_records:
        record = max(
            live_active_records,
            key=lambda item: _parse_datetime(item.lease.last_heartbeat),
        )
    else:
        record, stale_reason = max(
            active_records,
            key=lambda item: _parse_datetime(item[0].lease.last_heartbeat),
        )
        assert stale_reason is not None
        raise ValueError(
            f"{stale_reason}；当前 run 的 active execution 均无存活执行主体，"
            "请使用 recover 接管现场。"
        )
    request = StopRequest(
        reason=normalized_reason,
        requested_at=_now().isoformat(),
        requester_pid=os.getpid(),
    )
    _write_model_atomic(record.path.parent / "stop-request.json", request)
    return record


def request_stop_for_active_executions(
    run_dir: Path,
    reason: str,
) -> list[ExecutionRecord]:
    """建立 run 级 stop latch，并向全部 active execution 广播 stop request。"""

    normalized_reason = _redact_process_output(reason.strip())
    if not normalized_reason:
        raise ValueError("stop 必须提供原因，方便后续追溯。")
    run_dir = _validated_run_dir(run_dir)
    requested_latch = RunStopLatch(
        run_id=run_dir.name,
        latch_id=uuid4().hex,
        reason=normalized_reason,
        requested_at=_now().isoformat(),
        requester_pid=os.getpid(),
    )
    initial_scan_error: ValueError | None = None
    try:
        initial_active_records = sorted(
            (
                record
                for record in find_execution_records(run_dir)
                if record.lease.status in ACTIVE_EXECUTION_STATUSES
            ),
            key=lambda item: item.path.as_posix(),
        )
    except ValueError as exc:
        initial_scan_error = exc
        initial_active_records = []
    latch = _create_or_read_run_stop_latch(run_dir, requested_latch)
    request = StopRequest(
        reason=latch.reason,
        requested_at=latch.requested_at,
        requester_pid=latch.requester_pid,
    )
    audit_failures: list[str] = []
    if not _try_append_stop_latch_audit(
        run_dir,
        StopLatchAuditEntry(
            event="broadcast_started",
            run_id=run_dir.name,
            latch_id=latch.latch_id,
            recorded_at=_now().isoformat(),
            detail=(
                "已创建 run stop latch。"
                if latch.latch_id == requested_latch.latch_id
                else "复用已存在的 run stop latch。"
            ),
        ),
    ):
        audit_failures.append("无法追加 broadcast_started 审计事件")
    if initial_scan_error is not None:
        detail = _redact_process_output(str(initial_scan_error))
        _try_append_stop_latch_audit(
            run_dir,
            StopLatchAuditEntry(
                event="broadcast_failed",
                run_id=run_dir.name,
                latch_id=latch.latch_id,
                recorded_at=_now().isoformat(),
                detail=detail,
            ),
        )
        raise ValueError(
            "run stop latch 已建立，但 execution 扫描发现不可信记录；"
            f"{detail}"
        ) from initial_scan_error

    records_by_path: dict[Path, ExecutionRecord] = {}
    write_failures: list[str] = []
    trust_failures: list[str] = []
    seed_records = initial_active_records
    while True:
        try:
            active_records = sorted(
                (
                    record
                    for record in find_execution_records(run_dir)
                    if record.lease.status in ACTIVE_EXECUTION_STATUSES
                ),
                key=lambda item: item.path.as_posix(),
            )
        except ValueError as exc:
            detail = _redact_process_output(str(exc))
            _try_append_stop_latch_audit(
                run_dir,
                StopLatchAuditEntry(
                    event="broadcast_failed",
                    run_id=run_dir.name,
                    latch_id=latch.latch_id,
                    recorded_at=_now().isoformat(),
                    detail=detail,
                ),
            )
            raise ValueError(
                "run stop latch 已建立，但 execution 扫描发现不可信记录；"
                f"{detail}"
            ) from exc
        candidates_by_path = {
            record.path: record
            for record in (*seed_records, *active_records)
        }
        seed_records = []
        pending_records = [
            record
            for record in candidates_by_path.values()
            if record.path not in records_by_path
        ]
        if not pending_records:
            break
        for record in pending_records:
            records_by_path[record.path] = record
            stale_reason = _active_execution_stale_reason(record.lease)
            if record.lease.termination_unconfirmed:
                trust_failures.append(
                    f"{_execution_ref(run_dir, record.path)}: "
                    "owned process tree 终止未确认"
                )
            elif stale_reason is not None:
                trust_failures.append(
                    f"{_execution_ref(run_dir, record.path)}: {stale_reason}"
                )
            stop_path = record.path.parent / "stop-request.json"
            try:
                _assert_run_control_path(run_dir, stop_path)
                _write_model_atomic(stop_path, request)
            except OSError as exc:
                detail = (
                    f"{_execution_ref(run_dir, record.path)}: "
                    f"{type(exc).__name__}: {exc}"
                )
                write_failures.append(detail)
                event = "execution_stop_write_failed"
            else:
                detail = "已写入 execution-local stop request。"
                event = "execution_stop_written"
            if not _try_append_stop_latch_audit(
                run_dir,
                StopLatchAuditEntry(
                    event=event,
                    run_id=run_dir.name,
                    latch_id=latch.latch_id,
                    recorded_at=_now().isoformat(),
                    execution_ref=_execution_ref(run_dir, record.path),
                    detail=_redact_process_output(detail),
                ),
            ):
                audit_failures.append(
                    f"无法追加 {_execution_ref(run_dir, record.path)} 的审计事件"
                )

    failures = [*trust_failures, *write_failures, *audit_failures]
    final_event = "broadcast_failed" if failures else "broadcast_completed"
    final_detail = (
        "；".join(failures)
        if failures
        else f"已核对并覆盖 {len(records_by_path)} 个 active execution。"
    )
    if not _try_append_stop_latch_audit(
        run_dir,
        StopLatchAuditEntry(
            event=final_event,
            run_id=run_dir.name,
            latch_id=latch.latch_id,
            recorded_at=_now().isoformat(),
            detail=_redact_process_output(final_detail),
        ),
    ):
        failures.append("无法追加广播终态审计事件")

    if not records_by_path:
        raise ValueError(
            "run stop latch 已建立，但当前 run 没有可停止的 active execution；"
            "后续 execution 仍会被拒绝启动。"
        )
    if failures:
        raise ValueError(
            "run stop latch 已建立，但广播未能完整确认；"
            + "；".join(failures)
        )
    return list(records_by_path.values())


def find_execution_records(run_dir: Path) -> list[ExecutionRecord]:
    run_dir = _validated_run_dir(run_dir)
    records: list[ExecutionRecord] = []
    for path in sorted(run_dir.rglob("execution.json")):
        _assert_run_control_path(run_dir, path)
        try:
            lease = _read_execution_lease(path)
        except (OSError, ValueError) as exc:
            relative = path.relative_to(run_dir)
            raise ValueError(
                f"无法解析 execution 记录：{relative}；为避免并发接管，已拒绝 stop/recover。"
            ) from exc
        if lease.run_id != run_dir.name:
            relative = path.relative_to(run_dir)
            raise ValueError(
                f"execution 记录身份不一致：{relative} 的 run_id={lease.run_id!r}，"
                f"预期为 {run_dir.name!r}；为避免串用证据，已拒绝 stop/recover。"
            )
        _validate_execution_record_identity(run_dir, path, lease)
        records.append(ExecutionRecord(path=path, lease=lease))
    return records


def inspect_execution_for_recovery(run_dir: Path) -> ExecutionRecoveryInspection:
    """判断 running run 是否已经失去安全执行主体。

    任一 owned PID 仍存活的 execution 都不能直接 recover，避免另一个 CLI 在 worker/reviewer
    仍运行时篡改状态。recover 只接管缺失、终态，或 owned PID 均已消失的现场；lease/deadline
    过期只是诊断信息，不能替代进程存活检查。
    """

    records = find_execution_records(run_dir)
    if not records:
        return ExecutionRecoveryInspection(True, "未找到 execution.json，running 状态已无可确认执行主体。")
    unconfirmed_records = [
        record for record in records if record.lease.termination_unconfirmed
    ]
    if unconfirmed_records:
        record = max(
            unconfirmed_records,
            key=lambda item: _parse_datetime(item.lease.last_heartbeat),
        )
        return ExecutionRecoveryInspection(
            False,
            "owned process tree 终止未确认；即使已知 PID 消失，也不能证明后代进程全部退出。"
            "请人工检查 execution 证据和系统进程，当前 run 不允许自动 recovery。",
            record,
        )
    active_records: list[tuple[ExecutionRecord, str | None]] = []
    for record in records:
        if record.lease.status in ACTIVE_EXECUTION_STATUSES:
            active_records.append((record, _active_execution_stale_reason(record.lease)))

    live_active_records = [record for record, stale_reason in active_records if stale_reason is None]
    if live_active_records:
        record = max(live_active_records, key=lambda item: _parse_datetime(item.lease.last_heartbeat))
        return ExecutionRecoveryInspection(
            False,
            "active execution 至少一个 owned/child PID 仍存活；"
            "请先使用 vega stop 请求安全停止。",
            record,
        )

    live_terminal_records = [
        record
        for record in records
        if record.lease.status in TERMINAL_EXECUTION_STATUSES
        and _execution_has_live_owned_pid(record.lease)
    ]
    if live_terminal_records:
        record = max(
            live_terminal_records,
            key=lambda item: _parse_datetime(item.lease.last_heartbeat),
        )
        return ExecutionRecoveryInspection(
            False,
            f"terminal execution 已标记为 {record.lease.status}，"
            "但 owned/child PID 仍存活；已拒绝 recovery，避免并发接管。",
            record,
        )

    if active_records:
        record, stale_reason = max(
            active_records,
            key=lambda item: _parse_datetime(item[0].lease.last_heartbeat),
        )
        assert stale_reason is not None
        return ExecutionRecoveryInspection(True, f"{stale_reason}。", record)

    record = max(records, key=lambda item: _parse_datetime(item.lease.last_heartbeat))
    if record.lease.status in TERMINAL_EXECUTION_STATUSES:
        return ExecutionRecoveryInspection(
            True,
            f"最新 execution 已进入终态：{record.lease.status}。",
            record,
        )
    return ExecutionRecoveryInspection(
        True,
        f"最新 execution 状态允许 recovery：{record.lease.status}。",
        record,
    )


def _active_execution_stale_reason(lease: ExecutionLease) -> str | None:
    if lease.termination_unconfirmed:
        return "owned process tree 终止未确认"
    owner_alive = is_process_alive(lease.owner_pid)
    child_alive = lease.child_pid is not None and is_process_alive(lease.child_pid)
    if owner_alive or child_alive:
        # heartbeat/deadline 过期不足以证明执行主体已经退出。只要任一 owned PID
        # 仍存活，recover 就可能与原进程并发写状态，必须先 stop 或等待进程终止。
        return None

    now = _now()
    if _parse_datetime(lease.deadline) <= now:
        return "active execution deadline 已过期"
    if _parse_datetime(lease.lease_expires_at) <= now:
        return "active execution heartbeat lease 已过期"
    if not owner_alive:
        return "execution owner PID 已消失"
    if lease.child_pid is not None and not child_alive:
        return "execution child PID 已消失"
    return None


def _execution_has_live_owned_pid(lease: ExecutionLease) -> bool:
    return is_process_alive(lease.owner_pid) or (
        lease.child_pid is not None and is_process_alive(lease.child_pid)
    )


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _is_windows_platform():
        return _is_windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_options() -> dict[str, object]:
    if _is_windows_platform():
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _start_stdin_writer(process: subprocess.Popen[bytes], input_text: str) -> threading.Thread:
    assert process.stdin is not None
    stdin = process.stdin

    def write_stdin() -> None:
        try:
            stdin.write(input_text.encode("utf-8"))
        except (BrokenPipeError, OSError, ValueError):
            # 子进程可能不读 stdin 或已退出；最终以 returncode 和输出为准。
            pass
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    writer = threading.Thread(
        target=write_stdin,
        name="vega-stdin-writer",
        daemon=True,
    )
    writer.start()
    return writer


def _persist_redacted_output(output_file: object, output_path: Path) -> str:
    raw_output = _read_output_file(output_file)
    output = _normalize_output_newlines(_redact_process_output(raw_output))
    output_path.write_text(output, encoding="utf-8", newline="\n")
    return output


def _bind_process_output(
    lease: ExecutionLease,
    output_path: Path,
) -> None:
    if not output_path.is_file():
        lease.process_output_sha256 = None
        lease.process_output_bytes = 0
        return
    payload = output_path.read_bytes()
    lease.process_output_sha256 = hashlib.sha256(payload).hexdigest()
    lease.process_output_bytes = len(payload)


def _read_output_file(output_file: object) -> str:
    output_file.flush()
    output_file.seek(0)
    return output_file.read().decode("utf-8", errors="replace")


def _redact_process_output(output: str) -> str:
    if not output:
        return output
    redactor = _load_redact_text()
    if redactor is None:
        return _REDACTION_UNAVAILABLE_OUTPUT
    try:
        return redactor(output)
    except Exception:
        # 脱敏边界必须 fail-closed，不能因 redaction 集成异常回退到原始输出。
        return _REDACTION_UNAVAILABLE_OUTPUT


def _redact_optional_text(output: str | None) -> str | None:
    if output is None:
        return None
    return _redact_process_output(output)


def _redact_runner_identity(
    identity: dict[str, str],
) -> dict[str, str]:
    try:
        from .redaction import redact_value

        redacted = redact_value(identity)
    except Exception:
        return {"redaction_status": _REDACTION_UNAVAILABLE_OUTPUT}
    if (
        not isinstance(redacted, dict)
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            for key, value in redacted.items()
        )
    ):
        return {"redaction_status": _REDACTION_UNAVAILABLE_OUTPUT}
    return redacted


def _normalize_output_newlines(output: str) -> str:
    return output.replace("\r\n", "\n").replace("\r", "\n")


def _load_redact_text() -> Callable[[str], str] | None:
    try:
        from .redaction import redact_text
    except (ImportError, NameError):
        return None
    return redact_text


def _terminate_owned_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
) -> ProcessTerminationResult:
    failures: list[str] = []
    tree_termination_failed = False
    if _is_windows_platform():
        # CTRL_BREAK_EVENT 在共享控制台中可能波及无关进程；taskkill 使用明确 PID，
        # 先不加 /F 请求结束 owned tree，超过 grace period 后才强制终止。
        taskkill_failure = _run_windows_taskkill(
            process.pid,
            force=False,
            timeout=max(1.0, grace_seconds),
        )
        if taskkill_failure is not None:
            failures.append(taskkill_failure)
            tree_termination_failed = True
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return _confirm_owned_process_terminated(process, failures)
        except OSError as exc:
            failures.append(f"发送 SIGTERM 失败：{exc}")

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    except OSError as exc:
        failures.append(f"等待 owned process 退出失败：{exc}")
    confirmation = _confirm_owned_process_terminated(
        process,
        [*failures],
        tree_termination_failed=tree_termination_failed,
    )
    if confirmation.succeeded:
        return confirmation
    if (
        _is_windows_platform()
        and tree_termination_failed
        and process.poll() is not None
    ):
        # 根进程已经退出，但 taskkill /T 失败时无法证明后代进程已退出。
        return confirmation

    if _is_windows_platform():
        # PID 必须来自当前 execution lease；/T 只处理该 owned process tree，不枚举其他进程。
        taskkill_failure = _run_windows_taskkill(process.pid, force=True, timeout=10)
        tree_termination_failed = taskkill_failure is not None
        if taskkill_failure is not None:
            failures.append(taskkill_failure)
        if process.poll() is None:
            try:
                process.kill()
            except OSError as exc:
                failures.append(f"强制终止 owned process 失败：{exc}")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            failures.append(f"发送 SIGKILL 失败：{exc}")
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        failures.append("强制终止后最终 wait 超时。")
    except OSError as exc:
        failures.append(f"强制终止后最终 wait 失败：{exc}")
    return _confirm_owned_process_terminated(
        process,
        failures,
        tree_termination_failed=tree_termination_failed,
    )


def _confirm_owned_process_terminated(
    process: subprocess.Popen[bytes],
    failures: list[str],
    *,
    tree_termination_failed: bool = False,
) -> ProcessTerminationResult:
    try:
        process_alive = process.poll() is None or is_process_alive(process.pid)
    except Exception as exc:
        failures.append(f"无法确认 owned process PID 是否存活：{exc}")
        process_alive = True
    process_group_alive = False
    if not _is_windows_platform():
        try:
            process_group_alive = _is_posix_process_group_alive(process.pid)
        except OSError as exc:
            failures.append(f"无法确认 owned process group 是否存活：{exc}")
            process_group_alive = True
    if tree_termination_failed:
        failures.append("taskkill 未能确认 owned process tree 已终止。")
    if process_alive:
        failures.append(f"owned process PID {process.pid} 仍存活。")
    if process_group_alive:
        failures.append(f"owned process group {process.pid} 仍存活。")
    if process_alive or process_group_alive or tree_termination_failed:
        return ProcessTerminationResult(False, "；".join(failures))
    if failures:
        return ProcessTerminationResult(
            True,
            "owned process tree 已确认退出；终止过程中出现非致命诊断："
            + "；".join(failures),
        )
    return ProcessTerminationResult(True, "owned process tree 已确认退出。")


def _is_posix_process_group_alive(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    linux_states = _linux_process_group_states(process_group_id)
    if linux_states is None or not linux_states:
        # /proc 不可用、扫描不完整或没有找到成员时无法证明进程组已安全退出。
        return True
    # Zombie/dead 进程已不能继续执行或写文件，不应把已终止的进程树误报为存活。
    return any(state not in {"Z", "X", "x"} for state in linux_states)


def _linux_process_group_states(
    process_group_id: int,
    proc_root: Path = Path("/proc"),
) -> list[str] | None:
    if not proc_root.is_dir():
        return None
    states: list[str] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_text = entry.joinpath("stat").read_text(encoding="utf-8")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, UnicodeError):
            return None
        command_end = stat_text.rfind(")")
        if command_end < 0:
            return None
        fields = stat_text[command_end + 1 :].split()
        if len(fields) < 3:
            return None
        state = fields[0]
        if state not in {"R", "S", "D", "Z", "T", "t", "X", "x", "K", "W", "P", "I"}:
            return None
        try:
            member_group_id = int(fields[2])
        except ValueError:
            return None
        if member_group_id == process_group_id:
            states.append(state)
    return states


def _run_windows_taskkill(pid: int, *, force: bool, timeout: float) -> str | None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    mode = "强制 taskkill" if force else "taskkill"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{mode} 调用超时。"
    except OSError as exc:
        return f"{mode} 调用失败：{exc}"
    if result.returncode == 0:
        return None
    diagnostic = (result.stderr or result.stdout or "").strip()
    suffix = f"：{diagnostic}" if diagnostic else ""
    return f"{mode} 退出码 {result.returncode}{suffix}"


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _is_windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER 表示 PID 不存在；权限不足或其他探测失败不能
        # 被解释为“进程已退出”，否则 recovery 可能与仍存活的进程并发。
        return ctypes.get_last_error() != 87
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _read_execution_lease(path: Path, attempts: int = 5) -> ExecutionLease:
    last_error: OSError | ValueError | None = None
    for _ in range(attempts):
        try:
            return ExecutionLease.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            last_error = exc
            time.sleep(0.02)
    assert last_error is not None
    raise last_error


def _run_dir_for_execution(execution_dir: Path, run_id: str) -> Path:
    candidate = Path(os.path.abspath(execution_dir))
    for parent in (candidate, *candidate.parents):
        if parent.name == run_id:
            return parent
    # standalone runner 没有外层业务 run；其 execution_dir 自身就是隔离控制边界。
    return candidate


@contextmanager
def hold_run_control_file_lock(
    run_dir: Path,
    lock_path: Path,
) -> Iterator[None]:
    """在 run 边界内持有一个崩溃自动释放的 OS 文件锁。"""

    run_dir = _validated_run_dir(run_dir)
    lock_path = Path(os.path.abspath(lock_path))
    _assert_run_control_path(run_dir, lock_path)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        _validate_open_run_control_file(run_dir, lock_path, descriptor)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        _acquire_os_file_lock(descriptor)
        locked = True
        # 锁载体可以永久存在，但互斥状态只属于当前 descriptor；崩溃会由 OS 自动释放。
        _validate_open_run_control_file(run_dir, lock_path, descriptor)
        yield
    finally:
        if locked:
            _release_os_file_lock(descriptor)
        os.close(descriptor)


@contextmanager
def _hold_run_stop_start_lock(run_dir: Path) -> Iterator[None]:
    """用 OS 文件锁串行化同一 run 的 stop latch 与进程启动。"""

    run_dir = _validated_run_dir(run_dir)
    with hold_run_control_file_lock(
        run_dir,
        run_dir / ".stop-start.lock",
    ):
        yield


def _validate_open_run_control_file(
    run_dir: Path,
    path: Path,
    descriptor: int,
) -> None:
    _assert_run_control_path(run_dir, path)
    path_metadata = path.lstat()
    descriptor_metadata = os.fstat(descriptor)
    if not stat.S_ISREG(path_metadata.st_mode):
        raise ValueError("execution control 锁载体必须是普通文件。")
    if (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ) != (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
    ):
        raise ValueError("execution control 锁载体在获取期间被替换，已 fail-closed。")


def _acquire_os_file_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        retryable_errors = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
        while True:
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in retryable_errors:
                    raise
                time.sleep(0.01)

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _release_os_file_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _validated_run_dir(run_dir: Path) -> Path:
    candidate = Path(os.path.abspath(run_dir))
    if not candidate.is_dir():
        raise ValueError(f"run 目录不存在：{candidate}")
    _assert_run_control_path(candidate, candidate)
    return candidate


def _assert_run_control_path(run_dir: Path, target: Path) -> None:
    run_dir = Path(os.path.abspath(run_dir))
    target = Path(os.path.abspath(target))
    try:
        relative = target.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("execution control 路径必须位于当前 run 内。") from exc
    for candidate in (run_dir, *(run_dir / part for part in _path_prefixes(relative))):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
            raise ValueError(
                "execution control 路径不能包含链接或 reparse point。"
            )
    resolved_run = run_dir.resolve()
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_run)
    except ValueError as exc:
        raise ValueError("execution control 路径解析后越出当前 run。") from exc


def _path_prefixes(relative: Path) -> list[Path]:
    prefixes: list[Path] = []
    current = Path()
    for part in relative.parts:
        current /= part
        prefixes.append(current)
    return prefixes


def _create_or_read_run_stop_latch(
    run_dir: Path,
    requested_latch: RunStopLatch,
) -> RunStopLatch:
    with _hold_run_stop_start_lock(run_dir):
        latch_path = run_dir / "stop-latch.json"
        _assert_run_control_path(run_dir, latch_path)
        try:
            _write_model_create_once(latch_path, requested_latch)
        except ExecutionAlreadyExistsError:
            pass
        latch = _read_run_stop_latch(
            run_dir,
            fail_closed=False,
            expected_run_id=run_dir.name,
        )
        if latch is None:
            raise ValueError("run stop latch 写入后无法读取，已拒绝继续广播。")
        return latch


def _read_run_stop_latch(
    run_dir: Path,
    *,
    fail_closed: bool,
    expected_run_id: str,
) -> RunStopLatch | None:
    latch_path = run_dir / "stop-latch.json"
    _assert_run_control_path(run_dir, latch_path)
    if not latch_path.exists():
        return None
    try:
        latch = RunStopLatch.model_validate_json(
            latch_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        if not fail_closed:
            raise ValueError(
                "run stop latch 无法验证；为避免继续启动 execution，已 fail-closed。"
            )
        return RunStopLatch(
            run_id=expected_run_id,
            latch_id="unverified-stop-latch",
            reason="run stop latch 无法验证，已 fail-closed 停止。",
            requested_at=_now().isoformat(),
            requester_pid=os.getpid(),
        )
    if latch.run_id != expected_run_id:
        if not fail_closed:
            raise ValueError(
                "run stop latch 身份不一致；为避免串用 stop 意图，已 fail-closed。"
            )
        return RunStopLatch(
            run_id=expected_run_id,
            latch_id="mismatched-stop-latch",
            reason="run stop latch 身份不一致，已 fail-closed 停止。",
            requested_at=_now().isoformat(),
            requester_pid=os.getpid(),
        )
    return latch


def _validate_execution_record_identity(
    run_dir: Path,
    path: Path,
    lease: ExecutionLease,
) -> None:
    relative = path.relative_to(run_dir)
    parts = relative.parts
    iteration = _iteration_from_execution_path(parts)

    if len(parts) >= 3 and parts[-3] == "executions":
        slot = parts[-2]
        if slot != lease.step and not slot.startswith(f"{lease.step}-"):
            raise ValueError(
                f"execution 记录路径无法绑定 identity：{relative} 的 execution "
                f"slot={slot!r}，但 lease.step={lease.step!r}；"
                "已拒绝 stop/recover。"
            )
        if lease.step == "verification" and slot.startswith("verification-"):
            try:
                verification_index = int(slot.removeprefix("verification-"))
            except ValueError as exc:
                raise ValueError(
                    f"execution 记录路径无法绑定 identity：{relative} 的 verification "
                    "slot 后缀不是有效整数；已拒绝 stop/recover。"
                ) from exc
            if lease.iteration != verification_index:
                raise ValueError(
                    f"execution 记录路径无法绑定 identity：{relative} 的 verification "
                    f"序号={verification_index}，但 lease.iteration={lease.iteration}；"
                    "已拒绝 stop/recover。"
                )
        elif iteration is not None:
            _require_matching_execution_iteration(
                relative,
                lease,
                iteration,
            )
        return

    if "parallel-reviews" in parts:
        if lease.step != "reviewer":
            raise ValueError(
                f"execution 记录路径无法绑定 identity：{relative} 位于 "
                f"parallel-reviews，但 lease.step={lease.step!r}；"
                "已拒绝 stop/recover。"
            )
        if lease.attempt_id is None:
            raise ValueError(
                f"execution 记录路径无法绑定 identity：{relative} 缺少 "
                "lease.attempt_id；已拒绝 stop/recover。"
            )
        if iteration is None:
            raise ValueError(
                f"execution 记录路径无法绑定 identity：{relative} 缺少规范化 "
                "iterations 目录；已拒绝 stop/recover。"
            )
        _require_matching_execution_iteration(relative, lease, iteration)
        attempt_dir = parts[-2]
        if attempt_dir.startswith("a-"):
            expected = (
                "a-"
                + hashlib.sha256(
                    lease.attempt_id.encode("utf-8")
                ).hexdigest()[:16]
            )
            if attempt_dir != expected:
                raise ValueError(
                    f"execution 记录路径无法绑定 identity：{relative} 的 attempt "
                    "目录与 lease.attempt_id 哈希不一致；已拒绝 stop/recover。"
                )
        elif not attempt_dir.startswith("attempt-"):
            raise ValueError(
                f"execution 记录路径无法绑定 identity：{relative} 既不是规范化 "
                "attempt 哈希目录，也不是可解释的旧版 attempt 目录；"
                "已拒绝 stop/recover。"
            )
        return

    raise ValueError(
        f"execution 记录路径无法绑定 identity：{relative} 不属于受支持的 "
        "executions 或 parallel-reviews 布局；已拒绝 stop/recover。"
    )


def _require_matching_execution_iteration(
    relative: Path,
    lease: ExecutionLease,
    iteration: int,
) -> None:
    if lease.iteration is None:
        raise ValueError(
            f"execution 记录路径无法绑定 identity：{relative} 位于 iteration "
            "目录，但 lease.iteration 缺失；已拒绝 stop/recover。"
        )
    if lease.iteration != iteration:
        raise ValueError(
            f"execution 记录路径无法绑定 identity：{relative} 的 iteration="
            f"{iteration}，但 lease.iteration={lease.iteration}；"
            "已拒绝 stop/recover。"
        )


def _iteration_from_execution_path(parts: tuple[str, ...]) -> int | None:
    try:
        index = parts.index("iterations")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    try:
        return int(parts[index + 1])
    except ValueError as exc:
        raise ValueError(
            "execution 记录的 iterations 目录不是有效整数。"
        ) from exc


def _try_append_stop_latch_audit(
    run_dir: Path,
    entry: StopLatchAuditEntry,
) -> bool:
    audit_path = run_dir / "stop-latch-audit.jsonl"
    try:
        _assert_run_control_path(run_dir, audit_path)
        payload = (
            json.dumps(
                entry.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            audit_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError(
                    f"stop latch audit 短写：{written}/{len(payload)} bytes"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return False
    return True


def _execution_ref(run_dir: Path, execution_path: Path) -> str:
    _assert_run_control_path(run_dir, execution_path)
    return Path(os.path.abspath(execution_path)).relative_to(
        Path(os.path.abspath(run_dir))
    ).as_posix()


def _write_model_create_once(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".cl-{uuid4().hex[:10]}")
    content = json.dumps(
        model.model_dump(),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    try:
        with temp_path.open(
            "x",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise ExecutionAlreadyExistsError(
                "同一 attempt 的 execution 已被其他执行者认领"
            ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _write_model_atomic(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".aw-{uuid4().hex[:10]}")
    try:
        temp_path.write_text(
            json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        last_error: OSError | None = None
        for _ in range(10):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                # Windows 读取方短暂持有文件句柄时 replace 可能失败，有限重试避免误判 runner error。
                last_error = exc
                time.sleep(0.02)
        assert last_error is not None
        raise last_error
    finally:
        temp_path.unlink(missing_ok=True)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _command_sha256(command: list[str]) -> str:
    payload = json.dumps(
        command,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
