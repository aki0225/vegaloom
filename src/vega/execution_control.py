from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
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
    execution_id: str | None = None
    execution_started_at: str | None = None


@dataclass(frozen=True)
class RunnerExecutionContext:
    """Runner 的窄执行上下文，不携带业务 prompt 或 worker 聊天。"""

    execution_dir: Path
    run_id: str
    step: str
    iteration: int | None = None
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
        self.execution_path = context.execution_dir / "execution.json"
        self.stop_request_path = context.execution_dir / "stop-request.json"
        self.output_path = context.execution_dir / "process-output.txt"
        self.lease: ExecutionLease | None = None

    def prepare(self, command: list[str] | str, timeout_seconds: int) -> ExecutionLease:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self.context.execution_dir.mkdir(parents=True, exist_ok=True)
        now = _now()
        command_parts = [command] if isinstance(command, str) else command
        self.lease = ExecutionLease(
            run_id=self.context.run_id,
            execution_id=uuid4().hex,
            step=self.context.step,
            iteration=self.context.iteration,
            owner_pid=os.getpid(),
            command=[_redact_process_output(item) for item in command_parts],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(seconds=self.context.lease_timeout_seconds)).isoformat(),
            deadline=(now + timedelta(seconds=timeout_seconds)).isoformat(),
            status="starting",
        )
        _write_model_atomic(self.execution_path, self.lease)
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
        lease.last_heartbeat = now.isoformat()
        lease.lease_expires_at = (
            now + timedelta(seconds=self.context.lease_timeout_seconds)
        ).isoformat()
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

    stdout/stderr 直接落盘，避免长输出填满 PIPE 后阻塞。停止和超时只作用于本函数
    启动并写入 execution.json 的 PID，不扫描或终止系统中的其他 Codex/Node 进程。
    """

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

    process_environment = None
    if environment is not None:
        process_environment = os.environ.copy()
        process_environment.update(environment)

    with tempfile.TemporaryFile("w+b") as output_file:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=output_file,
                stderr=subprocess.STDOUT,
                env=process_environment,
                **_process_group_options(),
            )
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
                status = "success" if returncode == 0 else "error"
                if returncode != 0:
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
            if stdin_writer is not None:
                stdin_writer.join(timeout=1.0)
            try:
                output = _persist_redacted_output(output_file, controller.output_path)
            except OSError as exc:
                if error is None:
                    error = f"runner 输出持久化失败：{exc}"
                if status == "success":
                    status = "error"
            returncode = process.returncode if process is not None else None
            if termination_unconfirmed:
                controller.record_termination_failure(
                    reason=error or "owned process 终止未确认。",
                    returncode=returncode,
                )
            else:
                controller.finish(status, reason=error, returncode=returncode)

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
    if linux_states:
        # Zombie/dead 进程已不能继续执行或写文件，不应把已终止的进程树误报为存活。
        return any(state not in {"Z", "X", "x"} for state in linux_states)
    return True


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
            stat = entry.joinpath("stat").read_text(encoding="utf-8")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            continue
        command_end = stat.rfind(")")
        if command_end < 0:
            continue
        fields = stat[command_end + 1 :].split()
        if len(fields) < 3:
            continue
        try:
            member_group_id = int(fields[2])
        except ValueError:
            continue
        if member_group_id == process_group_id:
            states.append(fields[0])
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
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
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
