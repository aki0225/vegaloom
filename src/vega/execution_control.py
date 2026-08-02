from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .execution_feedback import ExecutionProgressReporter, ExecutionProgressTicker
from .execution_output import ExecutionOutputLineObserver, ProcessOutputCapture
from .execution_paths import ExecutionPathGuard
from .execution_process import (
    ProcessProbe,
    ProcessTerminationResult,
    activate_windows_job_process as _activate_windows_job_process,
    add_windows_job_creation_flag as _add_windows_job_creation_flag,
    close_windows_job as _close_windows_job,
    create_windows_job_for_execution as _create_execution_job,
    get_process_creation_token as _process_creation_token,
    is_process_alive as _process_is_alive,
    linux_process_group_states,
    owned_process_tree_is_active as _owned_process_tree_is_active,
    owned_process_tree_may_be_active as _owned_process_tree_may_be_active,
    posix_process_group_is_alive,
    probe_process as _process_probe,
    probe_windows_job as _windows_job_probe,
    process_group_options as _platform_process_group_options,
    terminate_posix_process as _terminate_posix_process,
    windows_job_blocks_recovery,
    windows_job_recovery_summary,
)
from .windows_job import (
    NamedWindowsJob,
    WindowsJobError as WindowsJobError,
    WindowsJobProbe,
)
from .windows_process_termination import (
    run_windows_taskkill as _taskkill_windows_process_tree,
    terminate_windows_process as _terminate_windows_process,
)

ExecutionStatus = Literal[
    "starting",
    "running",
    "stop_requested",
    "stopped",
    "timed_out",
    "completed",
    "failed",
]
ACTIVE_EXECUTION_STATUSES = {"starting", "running", "stop_requested"}
TERMINAL_EXECUTION_STATUSES = {"stopped", "timed_out", "completed", "failed"}
_REDACTION_UNAVAILABLE_OUTPUT = "[REDACTION_UNAVAILABLE]"


class ExecutionLease(BaseModel):
    """Vega 对单个外部进程的所有权和存活记录。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    execution_id: str | None = None
    step: str
    iteration: int | None = None
    owner_pid: int = Field(ge=1)
    owner_creation_token: int | None = Field(default=None, ge=0)
    child_pid: int | None = Field(default=None, ge=1)
    child_creation_token: int | None = Field(default=None, ge=0)
    windows_job_name: str | None = None
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
    execution_id: str | None = None
    execution_started_at: str | None = None


@dataclass(frozen=True)
class RunnerExecutionContext:
    """Runner 的窄执行上下文，不携带业务 prompt 或 worker 聊天。"""

    execution_root: Path
    execution_dir: Path
    run_id: str
    step: str
    iteration: int | None = None
    heartbeat_interval_seconds: float = 1.0
    lease_timeout_seconds: float = 10.0
    terminate_grace_seconds: float = 3.0
    progress_reporter: ExecutionProgressReporter | None = None
    output_line_observer: ExecutionOutputLineObserver | None = None

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
        self.path_guard = ExecutionPathGuard(context.execution_root, context.execution_dir)
        self.execution_path = context.execution_dir / "execution.json"
        self.stop_request_path = context.execution_dir / "stop-request.json"
        self.output_path = context.execution_dir / "process-output.txt"
        self.lease: ExecutionLease | None = None

    def prepare(self, command: list[str] | str, timeout_seconds: int) -> ExecutionLease:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.path_guard.prepare()
        now = _now()
        command_parts = [command] if isinstance(command, str) else command
        self.lease = ExecutionLease(
            run_id=self.context.run_id,
            execution_id=uuid4().hex,
            step=self.context.step,
            iteration=self.context.iteration,
            owner_pid=os.getpid(),
            owner_creation_token=_get_process_creation_token(os.getpid()),
            command=[_redact_process_output(item) for item in command_parts],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(seconds=self.context.lease_timeout_seconds)).isoformat(),
            deadline=(now + timedelta(seconds=timeout_seconds)).isoformat(),
            status="starting",
        )
        self._write_lease()
        return self.lease

    def attach_windows_job(self, job_name: str) -> None:
        lease = self._require_lease()
        lease.windows_job_name = job_name
        self._write_lease()

    def child_created(self, child_pid: int) -> None:
        lease = self._require_lease()
        lease.child_pid = child_pid
        lease.child_creation_token = _get_process_creation_token(child_pid)
        self._write_lease()

    def child_started(self, child_pid: int) -> None:
        lease = self._require_lease()
        if lease.child_pid != child_pid:
            self.child_created(child_pid)
            lease = self._require_lease()
        lease.status = "running"
        self.heartbeat()

    def heartbeat(self) -> None:
        lease = self._require_lease()
        now = _now()
        lease.last_heartbeat = now.isoformat()
        lease.lease_expires_at = (
            now + timedelta(seconds=self.context.lease_timeout_seconds)
        ).isoformat()
        self._write_lease()

    def read_stop_request(self) -> StopRequest | None:
        self.path_guard.validate_artifact(self.stop_request_path)
        if not self.stop_request_path.exists():
            return None
        try:
            request = StopRequest.model_validate_json(
                self.stop_request_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            # stop request 可能正由另一个 CLI 原子替换；下一轮 polling 再读取即可。
            return None
        if not _stop_request_matches_lease(request, self._require_lease()):
            # execution 目录原则上不复用；仍显式绑定 identity，防止旧请求误停新执行。
            return None
        return request

    def mark_stop_requested(self, request: StopRequest) -> None:
        lease = self._require_lease()
        lease.status = "stop_requested"
        lease.reason = _redact_optional_text(request.reason)
        self._write_lease()

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
        lease.finished_at = _now().isoformat()
        lease.last_heartbeat = lease.finished_at
        self._write_lease()

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
        lease.last_heartbeat = now.isoformat()
        lease.lease_expires_at = (
            now + timedelta(seconds=self.context.lease_timeout_seconds)
        ).isoformat()
        self._write_lease()

    def persist_output(self, output_file: object) -> str:
        return self.path_guard.persist_output(
            output_file,
            self.output_path,
            _redact_process_output,
        )

    def _write_lease(self) -> None:
        lease = self._require_lease()
        self.path_guard.validate_artifact(self.execution_path)
        _write_model_atomic(self.execution_path, lease)

    def _require_lease(self) -> ExecutionLease:
        if self.lease is None:
            raise RuntimeError("execution lease 尚未创建")
        return self.lease


def run_owned_process(
    command: list[str] | str,
    input_text: str,
    cwd: Path,
    timeout_seconds: int,
    context: RunnerExecutionContext,
    *,
    environment: Mapping[str, str] | None = None,
) -> OwnedProcessResult:
    """运行一个可停止、可超时、可恢复判断的外部进程。

    stdout/stderr 写入匿名临时文件；启用观察器时由专用线程持续排空 PIPE。停止和超时
    只作用于本函数启动并写入 execution.json 的 PID，不扫描其他 Codex/Node 进程。
    """
    controller = ExecutionController(context)
    lease = controller.prepare(command, timeout_seconds)
    process: subprocess.Popen[bytes] | None = None
    windows_job: NamedWindowsJob | None = None
    deadline = time.monotonic() + timeout_seconds
    next_heartbeat = time.monotonic()
    progress = ExecutionProgressTicker(context.step, context.progress_reporter)
    status: Literal["success", "error", "timed_out", "stopped"] = "error"
    error: str | None = None
    output = ""
    termination_unconfirmed = False
    process_environment = None
    if environment is not None:
        process_environment = os.environ.copy()
        process_environment.update(environment)
    process_group_alive = None if _is_windows_platform() else _is_posix_process_group_alive
    with (
        tempfile.TemporaryFile("w+b") as output_file,
        tempfile.TemporaryFile("w+b") as input_file,
    ):
        output_capture = ProcessOutputCapture(output_file, context.output_line_observer)
        try:
            input_file.write(input_text.encode("utf-8"))
            input_file.seek(0)
            windows_job = _create_windows_job_for_execution(controller, lease)
            process_options = _add_windows_job_creation_flag(
                _process_group_options(),
                windows_job,
            )
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=input_file,
                stdout=output_capture.popen_stdout,
                stderr=subprocess.STDOUT,
                env=process_environment,
                **process_options,
            )
            _activate_windows_job_process(
                windows_job,
                process.pid,
                controller.child_created,
            )
            controller.child_started(process.pid)
            output_capture.start(process)
            progress.started()
            while _owned_process_tree_is_active(
                process,
                windows_job,
                process_group_alive=process_group_alive,
            ):
                request = controller.read_stop_request()
                if request is not None:
                    controller.mark_stop_requested(request)
                    termination = _terminate_owned_process(
                        process,
                        context.terminate_grace_seconds,
                        windows_job=windows_job,
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
                        windows_job=windows_job,
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
                output_capture.poll()
                progress.tick(now)
                time.sleep(min(0.1, context.heartbeat_interval_seconds))

            if not termination_unconfirmed and status not in {"stopped", "timed_out"}:
                returncode = process.wait()
                status = "success" if returncode == 0 else "error"
                if returncode != 0:
                    error = f"外部 runner 退出码：{returncode}"
        except KeyboardInterrupt:
            if process is not None and _owned_process_tree_may_be_active(
                process,
                windows_job,
                process_group_alive=process_group_alive,
            ):
                termination = _terminate_owned_process(
                    process,
                    context.terminate_grace_seconds,
                    windows_job=windows_job,
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
            if process is not None and _owned_process_tree_may_be_active(
                process,
                windows_job,
                process_group_alive=process_group_alive,
            ):
                termination = _terminate_owned_process(
                    process,
                    context.terminate_grace_seconds,
                    windows_job=windows_job,
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
            try:
                output_capture.finish(context.terminate_grace_seconds + 1.0)
                output = controller.persist_output(output_file)
            except OSError as exc:
                if error is None:
                    error = f"runner 输出持久化失败：{exc}"
                if status == "success":
                    status = "error"
            returncode = process.returncode if process is not None else None
            try:
                if termination_unconfirmed:
                    controller.record_termination_failure(
                        reason=error or "owned process 终止未确认。",
                        returncode=returncode,
                    )
                else:
                    controller.finish(status, reason=error, returncode=returncode)
            finally:
                _close_windows_job(windows_job)

    return OwnedProcessResult(
        status=status,
        output=output,
        error=error,
        returncode=process.returncode if process is not None else None,
        termination_unconfirmed=termination_unconfirmed,
    )


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
    owner_probe = _probe_process(
        record.lease.owner_pid,
        record.lease.owner_creation_token,
    )
    if owner_probe.status != "alive":
        raise ValueError(
            "active execution 的原 owner 已退出或无法确认存活，"
            "stop request 已无执行者可消费；"
            "请人工核对并终止剩余 owned process tree，确认现场稳定后再执行 recover。"
        )
    request = StopRequest(
        reason=normalized_reason,
        requested_at=_now().isoformat(),
        requester_pid=os.getpid(),
        execution_id=record.lease.execution_id,
        execution_started_at=(
            record.lease.started_at
            if record.lease.execution_id is not None
            else None
        ),
    )
    # 旧版 owner 的 StopRequest 使用 extra=forbid。目标 execution 没有 execution_id
    # 时必须写旧三字段形态，否则升级后的 CLI 会让仍在运行的旧 owner 永久忽略请求。
    _write_model_atomic(
        record.path.parent / "stop-request.json",
        request,
        exclude_none=True,
    )
    return _confirm_stop_target_still_active(run_dir, record)


def _confirm_stop_target_still_active(
    run_dir: Path,
    requested_record: ExecutionRecord,
) -> ExecutionRecord:
    """写入后确认 execution 未切换；已消费为 stopped 也属于成功。"""

    records = find_execution_records(run_dir)
    matching = [
        record
        for record in records
        if record.path.resolve() == requested_record.path.resolve()
        and _same_execution_identity(record.lease, requested_record.lease)
    ]
    live_active_records = [
        record
        for record in records
        if record.lease.status in ACTIVE_EXECUTION_STATUSES
        and not record.lease.termination_unconfirmed
        and _active_execution_stale_reason(record.lease) is None
    ]
    competing = [
        record
        for record in live_active_records
        if not (
            record.path.resolve() == requested_record.path.resolve()
            and _same_execution_identity(record.lease, requested_record.lease)
        )
    ]
    if competing:
        raise ValueError(
            "active execution 在 stop request 写入期间发生切换或并发；"
            "旧请求未绑定新 execution，请重新执行 stop。"
        )
    if len(matching) != 1:
        raise ValueError(
            "目标 execution 在 stop request 写入期间已结束或身份变化；"
            "无法确认请求已被消费，请检查 status 后按需重试。"
        )
    confirmed = matching[0]
    if confirmed.lease.status == "stopped":
        return confirmed
    if (
        confirmed.lease.status in ACTIVE_EXECUTION_STATUSES
        and not confirmed.lease.termination_unconfirmed
        and _active_execution_stale_reason(confirmed.lease) is None
    ):
        return confirmed
    raise ValueError(
        "目标 execution 在 stop request 写入期间已结束，但不是 stopped；"
        "无法确认请求是否生效，请检查 status 后按需重试。"
    )


def _same_execution_identity(left: ExecutionLease, right: ExecutionLease) -> bool:
    if left.execution_id is not None or right.execution_id is not None:
        return (
            left.execution_id is not None
            and left.execution_id == right.execution_id
        )
    return (
        left.started_at == right.started_at
        and left.step == right.step
        and left.iteration == right.iteration
        and left.owner_pid == right.owner_pid
    )


def _stop_request_matches_lease(
    request: StopRequest,
    lease: ExecutionLease,
) -> bool:
    if request.execution_id is not None:
        return (
            lease.execution_id is not None
            and request.execution_id == lease.execution_id
        )
    if request.execution_started_at is not None:
        return request.execution_started_at == lease.started_at
    # 旧三字段请求只能由同样没有 execution_id 的旧 lease 消费；升级后的新 execution
    # 必须忽略它，避免 CLI 在身份切换窗口拒绝后，旧请求反而误停新进程。
    return lease.execution_id is None


def find_execution_records(run_dir: Path) -> list[ExecutionRecord]:
    records: list[ExecutionRecord] = []
    for path in sorted(run_dir.rglob("execution.json")):
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
        record
        for record in records
        if _termination_unconfirmed_blocks_recovery(record.lease)
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
        job_summary = _active_windows_job_recovery_summary(record.lease)
        if job_summary is not None:
            return ExecutionRecoveryInspection(
                False,
                job_summary,
                record,
            )
        return ExecutionRecoveryInspection(
            False,
            "active execution 至少一个 owner/child PID 仍存活，"
            "或 Job/POSIX process group 尚未退出；"
            "请先使用 vega stop 请求安全停止。",
            record,
        )

    live_terminal_records = [
        record
        for record in records
        if record.lease.status in TERMINAL_EXECUTION_STATUSES
        and _terminal_execution_has_live_process_tree(record.lease)
    ]
    if live_terminal_records:
        record = max(
            live_terminal_records,
            key=lambda item: _parse_datetime(item.lease.last_heartbeat),
        )
        return ExecutionRecoveryInspection(
            False,
            f"terminal execution 已标记为 {record.lease.status}，"
            "但 owned process tree 仍存活；已拒绝 recovery，避免并发接管。",
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
        if _termination_unconfirmed_reconfirmed(lease):
            return "owned process tree 已重新确认退出"
        return "owned process tree 终止未确认"
    job_probe = _execution_windows_job_probe(lease)
    if windows_job_blocks_recovery(job_probe):
        return None
    owner_probe = _probe_process(lease.owner_pid, lease.owner_creation_token)
    child_probe = _execution_child_probe(lease)
    if (
        owner_probe.status != "gone"
        or child_probe.status != "gone"
        or _execution_posix_process_group_is_active(lease)
    ):
        # heartbeat/deadline 过期不足以证明执行主体已经退出。只要任一 owned PID
        # 或 POSIX 进程组仍存活，recover 就可能与原进程并发写状态，必须保守阻止。
        return None

    now = _now()
    if _parse_datetime(lease.deadline) <= now:
        return "active execution deadline 已过期"
    if _parse_datetime(lease.lease_expires_at) <= now:
        return "active execution heartbeat lease 已过期"
    if owner_probe.status == "gone":
        return "execution owner PID 已消失或已复用"
    if lease.child_pid is not None and child_probe.status == "gone":
        return "execution child PID 已消失或已复用"
    return None


def _terminal_execution_has_live_process_tree(lease: ExecutionLease) -> bool:
    job_probe = _execution_windows_job_probe(lease)
    if windows_job_blocks_recovery(job_probe):
        return True
    child_probe = _execution_child_probe(lease)
    return (
        child_probe.status != "gone"
        or _execution_posix_process_group_is_active(lease)
    )


def _execution_windows_job_probe(lease: ExecutionLease) -> WindowsJobProbe | None:
    if lease.windows_job_name is None:
        return None
    return _probe_windows_job(lease.windows_job_name)


def _active_windows_job_recovery_summary(lease: ExecutionLease) -> str | None:
    summary = windows_job_recovery_summary(_execution_windows_job_probe(lease))
    if summary is None or _probe_process(
        lease.owner_pid,
        lease.owner_creation_token,
    ).status != "gone":
        return summary
    return (
        "active execution 的 Windows Job Object 仍有成员或状态无法确认，"
        "但原 execution owner 已退出，新的 stop request 无执行者可消费；请人工核对并终止"
        "该 Job 对应进程树，确认现场稳定后再执行 recover。"
    )


def _termination_unconfirmed_reconfirmed(lease: ExecutionLease) -> bool:
    if not lease.termination_unconfirmed:
        return False
    owner_probe = _probe_process(lease.owner_pid, lease.owner_creation_token)
    child_probe = _execution_child_probe(lease)
    if owner_probe.status != "gone" or child_probe.status != "gone":
        return False
    if _is_windows_platform():
        if lease.windows_job_name is None:
            return False
        job_probe = _execution_windows_job_probe(lease)
        return job_probe is not None and job_probe.status in {"empty", "gone"}
    if lease.child_pid is None:
        return False
    return not _execution_posix_process_group_is_active(lease)


def _termination_unconfirmed_blocks_recovery(lease: ExecutionLease) -> bool:
    return lease.termination_unconfirmed and not _termination_unconfirmed_reconfirmed(lease)


def _execution_child_probe(lease: ExecutionLease) -> ProcessProbe:
    if lease.child_pid is None:
        return ProcessProbe("gone")
    return _probe_process(lease.child_pid, lease.child_creation_token)


def _execution_posix_process_group_is_active(lease: ExecutionLease) -> bool:
    if _is_windows_platform() or lease.child_pid is None:
        return False
    try:
        return _is_posix_process_group_alive(lease.child_pid)
    except OSError:
        return True


def _linux_process_group_states(
    process_group_id: int,
    proc_root: Path = Path("/proc"),
) -> list[str] | None:
    return linux_process_group_states(process_group_id, proc_root)


def _is_posix_process_group_alive(process_group_id: int) -> bool:
    return posix_process_group_is_alive(
        process_group_id,
        signal_probe=os.killpg,
        states_probe=_linux_process_group_states,
    )


def _probe_windows_job(job_name: str) -> WindowsJobProbe:
    return _windows_job_probe(job_name)


def is_process_alive(pid: int) -> bool:
    return _process_is_alive(pid, windows=_is_windows_platform())


def _probe_process(pid: int, expected_creation_token: int | None) -> ProcessProbe:
    return _process_probe(
        pid,
        expected_creation_token,
        windows=_is_windows_platform(),
        alive_probe=is_process_alive,
    )


def _get_process_creation_token(pid: int) -> int | None:
    return _process_creation_token(pid, windows=_is_windows_platform())


def _create_windows_job_for_execution(
    controller: ExecutionController,
    lease: ExecutionLease,
) -> NamedWindowsJob | None:
    return _create_execution_job(
        lease.execution_id,
        controller.attach_windows_job,
        windows=_is_windows_platform(),
    )


def _process_group_options() -> dict[str, object]:
    return _platform_process_group_options(windows=_is_windows_platform())


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


def _load_redact_text() -> Callable[[str], str] | None:
    try:
        from .redaction import redact_text
    except (ImportError, NameError):
        return None
    return redact_text


def _terminate_owned_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    *,
    windows_job: NamedWindowsJob | None = None,
) -> ProcessTerminationResult:
    if _is_windows_platform():
        return _terminate_windows_process(
            process,
            grace_seconds,
            windows_job,
            subprocess.run,
        )
    return _terminate_posix_process(
        process,
        grace_seconds,
        is_process_alive,
        _is_posix_process_group_alive,
        signal.SIGTERM,
        signal.SIGKILL,
    )


def _run_windows_taskkill(pid: int, *, force: bool, timeout: float) -> str | None:
    return _taskkill_windows_process_tree(
        pid,
        force=force,
        timeout=timeout,
        runner=subprocess.run,
    )


def _is_windows_platform() -> bool:
    return os.name == "nt"


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


def _write_model_atomic(
    path: Path,
    model: BaseModel,
    *,
    exclude_none: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _execution_model_temp_path(path)
    temp_path.write_text(
        json.dumps(
            model.model_dump(exclude_none=exclude_none),
            ensure_ascii=False,
            indent=2,
        )
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


def _execution_model_temp_path(path: Path) -> Path:
    # 同一进程会并发写 execution 与 stop request，短随机后缀避免临时文件互相覆盖。
    return path.with_name(f".e.{os.getpid():x}.{uuid4().hex[:8]}")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)
