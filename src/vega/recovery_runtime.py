from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .execution_control import ExecutionRecoveryInspection, inspect_execution_for_recovery
from .loop_initialization import loop_initialization_issues
from .models import (
    LoopAutomationState,
    LoopIterationState,
    SupersededTerminalRecord,
)
from .redaction import redact_text, write_redacted_text
from .recovery_reporting import (
    render_corrupt_state_recovery_report,
    render_iteration_interruption_report,
    render_worker_rerun_prestart_recovery_report,
)
from .run_lock import RunMutationLock
from .run_utils import resolve_run_dir
from .trace import (
    RUN_TERMINAL_SUPERSEDED_EVENT,
    TraceWriter,
    active_run_finished_indices,
    read_trace_items,
)
from .worker_rerun_transaction import (
    reconcile_worker_rerun_transaction_for_recovery,
)
from .workspace_baseline import recovered_initialization_step


class RecoveryTransaction(BaseModel):
    """记录 recovery 的跨文件待提交状态，使崩溃后可幂等补完。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    recovery_id: str
    reason: str
    previous_step: str
    previous_iteration: int = Field(ge=0)
    interrupted_iteration: int | None = Field(default=None, ge=1)
    append_iteration: bool = False
    recovered_at: str
    initialization_issues: list[str] = Field(default_factory=list)
    terminal_record: SupersededTerminalRecord | None = None


class RecoveryRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def recover_loop(self, run: str, reason: str) -> Path:
        """把中断在 running 的 loop 标记为 needs_human，并保留恢复说明。

        recover 只修复 Vega 自己的状态机，不清理工作区、不杀进程、不自动继续。
        这是为了处理 worker 超时、外部命令中断或 CLI 被关闭后留下的半完成 run。
        """
        run_dir = resolve_run_dir(self.workspace, run)
        with RunMutationLock.acquire(run_dir, "loop.recover"):
            return self._recover_loop_locked(run_dir, reason)

    def _recover_loop_locked(self, run_dir: Path, reason: str) -> Path:
        state_path = run_dir / "state.json"
        try:
            state = LoopAutomationState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            diagnostic = render_corrupt_state_recovery_report(run_dir, reason, exc)
            write_redacted_text(run_dir / "recovery-report.md", diagnostic)
            TraceWriter(run_dir / "trace.jsonl").write(
                "loop_recovery_blocked",
                reason=reason,
                error_type=type(exc).__name__,
                recovery_report="recovery-report.md",
            )
            raise ValueError(
                "state.json 无法解析，已保留现场并生成 recovery-report.md；"
                "Vega 不会猜测或覆盖损坏状态。"
            ) from exc
        _require_recovery_request(state, run_dir, reason)
        preflight_inspection = _preflight_recovery_inspection(run_dir, state)
        rerun_recovery = reconcile_worker_rerun_transaction_for_recovery(
            run_dir,
            state,
            TraceWriter(run_dir / "trace.jsonl"),
            reason=redact_text(reason.strip()),
        )
        if rerun_recovery in {"prestart_pending", "prestart_restored"}:
            report = render_worker_rerun_prestart_recovery_report(
                run_id=state.run_id,
                reason=reason,
                rerun_recovery=rerun_recovery,
            )
            write_redacted_text(run_dir / "recovery-report.md", report)
            state.artifacts = _dedupe(
                [*state.artifacts, "recovery-report.md", "state.json", "trace.jsonl"]
            )
            state.save(state_path)
            return run_dir
        transaction = _read_recovery_transaction(run_dir)
        if transaction is not None and _recovery_transaction_applied_to_state(
            state,
            transaction,
        ):
            _ensure_recovery_trace_events(run_dir, transaction)
            _delete_recovery_transaction(run_dir)
            if state.status != "running":
                return run_dir
            transaction = None
        if state.status != "running":
            if transaction is not None:
                raise ValueError(
                    "pending recovery 与当前非 running state 不一致，已拒绝猜测恢复。"
                )
            raise ValueError(
                f"只能 recover status=running 的 loop，当前状态：{state.status}"
            )
        inspection = preflight_inspection or inspect_execution_for_recovery(run_dir)
        if not inspection.can_recover:
            raise ValueError(inspection.summary)

        if transaction is None:
            previous_step = state.current_step
            previous_iteration = state.current_iteration
            try:
                interrupted_iteration, append_iteration = (
                    _interrupted_iteration_action(state)
                )
            except ValueError as exc:
                diagnostic = render_inconsistent_state_recovery_report(
                    run_dir,
                    reason,
                    exc,
                )
                write_redacted_text(run_dir / "recovery-report.md", diagnostic)
                TraceWriter(run_dir / "trace.jsonl").write(
                    "loop_recovery_blocked",
                    reason=reason,
                    error_type="IterationStateError",
                    recovery_report="recovery-report.md",
                )
                raise ValueError(
                    "loop iteration 状态不一致，已保留现场并拒绝自动 recovery。"
                ) from exc

            try:
                terminal_record = _prepare_incomplete_terminal_record(
                    run_dir,
                    uuid4().hex,
                    state.superseded_terminal_events,
                )
            except ValueError as exc:
                diagnostic = render_inconsistent_trace_recovery_report(
                    run_dir,
                    reason,
                    exc,
                )
                write_redacted_text(run_dir / "recovery-report.md", diagnostic)
                raise ValueError(
                    "loop trace 终态证据不一致，已保留现场并拒绝自动 recovery。"
                ) from exc

            initialization_issues = loop_initialization_issues(
                self.workspace,
                run_dir,
                state,
                Path(state.repo_path),
            )
            recovery_id = (
                terminal_record.recovery_id
                if terminal_record is not None
                else uuid4().hex
            )
            transaction = RecoveryTransaction(
                run_id=state.run_id,
                recovery_id=recovery_id,
                reason=redact_text(reason.strip()),
                previous_step=previous_step,
                previous_iteration=previous_iteration,
                interrupted_iteration=interrupted_iteration,
                append_iteration=append_iteration,
                recovered_at=datetime.now(UTC).isoformat(),
                initialization_issues=initialization_issues,
                terminal_record=terminal_record,
            )
            _write_recovery_transaction(run_dir, transaction)
        else:
            if (
                state.current_step != transaction.previous_step
                or state.current_iteration != transaction.previous_iteration
            ):
                raise ValueError(
                    "pending recovery 与当前 running state 不一致，已拒绝猜测恢复。"
                )
            expected_interrupted, expected_append = (
                _interrupted_iteration_action(state)
            )
            if (
                transaction.interrupted_iteration != expected_interrupted
                or transaction.append_iteration != expected_append
            ):
                raise ValueError(
                    "pending recovery 的 interruption plan 与 state 不一致。"
                )
            expected_initialization_issues = loop_initialization_issues(
                self.workspace,
                run_dir,
                state,
                Path(state.repo_path),
            )
            if (
                transaction.initialization_issues
                != expected_initialization_issues
            ):
                raise ValueError(
                    "pending recovery 的初始化判断与当前证据不一致。"
                )
            expected_terminal = _prepare_incomplete_terminal_record(
                run_dir,
                transaction.recovery_id,
                state.superseded_terminal_events,
                pending_recovery_id=transaction.recovery_id,
            )
            if transaction.terminal_record != expected_terminal:
                raise ValueError(
                    "pending recovery 的 terminal plan 与 trace 不一致。"
                )
            try:
                datetime.fromisoformat(transaction.recovered_at)
            except ValueError as exc:
                raise ValueError(
                    "pending recovery 的 recovered_at 不合法。"
                ) from exc

        _ensure_terminal_supersede(run_dir, transaction)
        previous_step = transaction.previous_step
        previous_iteration = transaction.previous_iteration
        interrupted_iteration = transaction.interrupted_iteration
        append_iteration = transaction.append_iteration
        recovery_id = transaction.recovery_id
        superseded_terminal_record = transaction.terminal_record
        initialization_issues = transaction.initialization_issues
        initialization_incomplete = bool(initialization_issues)
        recovered_at = transaction.recovered_at
        report = render_recovery_report(
            run_id=state.run_id,
            repo_path=state.repo_path,
            previous_step=previous_step,
            previous_iteration=previous_iteration,
            reason=transaction.reason,
            inspection=inspection,
            interrupted_iteration=interrupted_iteration,
            recovery_id=recovery_id,
            superseded_terminal_event=(
                superseded_terminal_record.terminal_event_index
                if superseded_terminal_record is not None
                else None
            ),
            initialization_incomplete=initialization_incomplete,
            initialization_issues=initialization_issues,
            recovered_at=recovered_at,
        )
        write_redacted_text(run_dir / "recovery-report.md", report)
        if interrupted_iteration is not None:
            if append_iteration:
                state.iterations.append(
                    LoopIterationState(
                        iteration=interrupted_iteration,
                        lifecycle="interrupted",
                        interrupted_step=previous_step,
                        interrupted_at=recovered_at,
                    )
                )
            else:
                iteration = state.iterations[-1]
                iteration.lifecycle = "interrupted"
                iteration.interrupted_step = previous_step
                iteration.interrupted_at = recovered_at

            interruption_artifact = (
                f"iterations/{interrupted_iteration:02d}/interruption-report.md"
            )
            write_redacted_text(
                run_dir / interruption_artifact,
                render_iteration_interruption_report(
                    run_id=state.run_id,
                    iteration=interrupted_iteration,
                    previous_step=previous_step,
                    recovered_at=recovered_at,
                    inspection=inspection,
                ),
            )
            state.artifacts = _dedupe([*state.artifacts, interruption_artifact])
        if (
            superseded_terminal_record is not None
            and superseded_terminal_record not in state.superseded_terminal_events
        ):
            state.superseded_terminal_events.append(superseded_terminal_record)
        state.status = "needs_human"
        state.last_recovery_id = recovery_id
        state.current_step = recovered_initialization_step(
            initialization_issues
        )
        state.artifacts = _dedupe([*state.artifacts, "recovery-report.md", "state.json", "trace.jsonl"])
        state.save(state_path)
        _ensure_recovery_trace_events(run_dir, transaction)
        _delete_recovery_transaction(run_dir)
        return run_dir


def render_recovery_report(
    *,
    run_id: str,
    repo_path: str,
    previous_step: str,
    previous_iteration: int,
    reason: str,
    inspection: ExecutionRecoveryInspection,
    interrupted_iteration: int | None = None,
    recovery_id: str | None = None,
    superseded_terminal_event: int | None = None,
    initialization_incomplete: bool = False,
    initialization_issues: list[str] | None = None,
    recovered_at: str | None = None,
) -> str:
    recovered_at = recovered_at or datetime.now(UTC).isoformat()
    lines = [
        "# Recovery Report",
        "",
        f"- run：`{run_id}`",
        f"- 仓库：`{repo_path}`",
        f"- 恢复时间：`{recovered_at}`",
        f"- 原步骤：`{previous_step}`",
        f"- 原迭代：`{previous_iteration}`",
        f"- 原因：{reason}",
        f"- execution 判断：{inspection.summary}",
    ]
    if recovery_id is not None:
        lines.append(f"- Recovery ID：`{recovery_id}`")
    if interrupted_iteration is not None:
        lines.append(f"- 已冻结中断迭代：`{interrupted_iteration}`")
    if superseded_terminal_event is not None:
        lines.append(
            f"- 已作废未提交的终态 trace 事件索引：`{superseded_terminal_event}`"
        )
    if inspection.record is not None:
        lease = inspection.record.lease
        lines.extend(
            [
                f"- execution 状态：`{lease.status}`",
                f"- owner PID：`{lease.owner_pid}`",
                f"- child PID：`{lease.child_pid or '无'}`",
                f"- 最后心跳：`{lease.last_heartbeat}`",
                f"- lease 到期：`{lease.lease_expires_at}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 已将 loop 状态从 `running` 标记为 `needs_human`。",
            "- recover 只接管缺失、终态、超时、lease 过期或 PID 消失的 execution。",
            "- 未自动清理工作区，未终止进程，未继续执行 worker/reviewer。",
            "- 请先检查目标仓库 `git status`、execution.json、worker-output.txt 和相关日志。",
            "",
            "## 建议下一步",
            "",
        ]
    )
    if initialization_incomplete:
        lines.extend(
            [
                "- loop 初始化证据不完整，当前 run 不允许 continue。",
                "- 初始化问题：`"
                + "`, `".join(initialization_issues or ["unknown"])
                + "`",
                "- 保留本 run 作为中断证据，并从新的 run 重新开始任务。",
            ]
        )
    else:
        lines.extend(
            [
                "- 如果工作区已有合理修复：运行 `vega loop continue --repo <repo> --run <run>`。",
                "- 如果工作区被污染：人工清理后再继续，或放弃该 run 重新开始。",
            ]
        )
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_inconsistent_state_recovery_report(
    run_dir: Path,
    reason: str,
    error: Exception,
) -> str:
    return redact_text(
        "\n".join(
            [
                "# Recovery Report",
                "",
                f"- run：`{run_dir.name}`",
                f"- 恢复时间：`{datetime.now(UTC).isoformat()}`",
                f"- 请求原因：{reason.strip() or '未提供'}",
                f"- 状态错误类型：`{type(error).__name__}`",
                "",
                "## 结论",
                "",
                "- iteration 序列与 current_iteration 不一致，自动 recovery 已停止。",
                "- Vega 不会猜测缺失轮次，也不会移动、删除或覆盖已有 iteration 目录。",
                "- 原 `state.json`、execution 和工作区现场保持不变；trace 只追加 blocked 诊断事件。",
                "",
                "## 建议下一步",
                "",
                "- 检查 `state.json`、`trace.jsonl` 和 `iterations/` 的编号与来源。",
                "- 保留当前 run 作为损坏证据；确认原因后从新的 run 继续。",
            ]
        ).rstrip()
        + "\n"
    )


def render_inconsistent_trace_recovery_report(
    run_dir: Path,
    reason: str,
    error: Exception,
) -> str:
    return redact_text(
        "\n".join(
            [
                "# Recovery Report",
                "",
                f"- run：`{run_dir.name}`",
                f"- 恢复时间：`{datetime.now(UTC).isoformat()}`",
                f"- 请求原因：{reason.strip() or '未提供'}",
                f"- Trace 错误类型：`{type(error).__name__}`",
                "",
                "## 结论",
                "",
                "- trace 中的终态或 superseded 证据不一致，自动 recovery 已停止。",
                "- Vega 不会删除、重写或猜测旧 run_finished 事件。",
                "- 原 state、iteration、execution 和工作区现场保持不变。",
                "",
                "## 建议下一步",
                "",
                "- 检查 `trace.jsonl` 中的 run_finished 与 run_terminal_superseded 事件。",
                "- 保留当前 run 作为损坏证据；确认原因后从新的 run 继续。",
            ]
        ).rstrip()
        + "\n"
    )


def _recovery_transaction_path(run_dir: Path) -> Path:
    return run_dir / ".control" / "recovery-transaction.json"


def _read_recovery_transaction(run_dir: Path) -> RecoveryTransaction | None:
    path = _recovery_transaction_path(run_dir)
    if not path.exists():
        return None
    try:
        transaction = RecoveryTransaction.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise ValueError("pending recovery transaction 无法验证") from exc
    if transaction.run_id != run_dir.name:
        raise ValueError("pending recovery transaction 与 run 身份不一致")
    _validate_recovery_transaction_shape(transaction)
    return transaction


def _validate_recovery_transaction_shape(
    transaction: RecoveryTransaction,
) -> None:
    expected_interrupted = (
        transaction.previous_iteration
        if transaction.previous_iteration > 0
        else None
    )
    if transaction.interrupted_iteration != expected_interrupted:
        raise ValueError(
            "pending recovery 的 interrupted_iteration 与 previous_iteration 不一致"
        )
    if transaction.previous_iteration == 0 and transaction.append_iteration:
        raise ValueError(
            "pending recovery 在 iteration=0 时不得声明 append_iteration"
        )
    record = transaction.terminal_record
    if record is not None and record.recovery_id != transaction.recovery_id:
        raise ValueError(
            "pending recovery 的 terminal record 与 recovery_id 不一致"
        )


def _write_recovery_transaction(
    run_dir: Path,
    transaction: RecoveryTransaction,
) -> None:
    path = _recovery_transaction_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _recovery_transaction_temp_path(path)
    try:
        temp_path.write_text(
            json.dumps(
                transaction.model_dump(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _recovery_transaction_temp_path(path: Path) -> Path:
    # Run 目录可能很深；短临时名避免原子写入额外触发 Windows MAX_PATH。
    return path.with_name(f".r.{uuid4().hex[:16]}")


def _delete_recovery_transaction(run_dir: Path) -> None:
    try:
        _recovery_transaction_path(run_dir).unlink(missing_ok=True)
    except OSError:
        # 事务已提交时残留文件只会触发下次幂等清理，不影响可信业务状态。
        pass


def _prepare_incomplete_terminal_record(
    run_dir: Path,
    recovery_id: str,
    expected_superseded: list[SupersededTerminalRecord],
    *,
    pending_recovery_id: str | None = None,
) -> SupersededTerminalRecord | None:
    """在写事务日志前定位唯一未提交终态，但不立即修改 trace。"""

    trace_path = run_dir / "trace.jsonl"
    if not trace_path.exists() or not trace_path.read_text(
        encoding="utf-8"
    ).strip():
        return None
    try:
        items = read_trace_items(trace_path)
    except (OSError, ValueError) as exc:
        raise ValueError("trace.jsonl 无法解析") from exc
    validation_items = [
        item
        for item in items
        if not (
            pending_recovery_id is not None
            and item.get("event") == RUN_TERMINAL_SUPERSEDED_EVENT
            and item.get("recovery_id") == pending_recovery_id
        )
    ]
    active_terminal_indices, issues = active_run_finished_indices(
        validation_items,
        expected_superseded=[
            record.model_dump() for record in expected_superseded
        ],
    )
    if issues:
        raise ValueError(
            "run_terminal_superseded 证据无效：" + ", ".join(issues)
        )
    if len(active_terminal_indices) > 1:
        raise ValueError("存在多个未被 supersede 的 run_finished 事件")
    if not active_terminal_indices:
        return None

    terminal_index = active_terminal_indices[0]
    terminal_status = items[terminal_index].get("status")
    if terminal_status not in {"success", "failed"}:
        raise ValueError("run_finished 缺少合法 status")
    return SupersededTerminalRecord(
        terminal_event_index=terminal_index,
        terminal_status=terminal_status,
        recovery_id=recovery_id,
    )


def _ensure_terminal_supersede(
    run_dir: Path,
    transaction: RecoveryTransaction,
) -> None:
    record = transaction.terminal_record
    if record is None:
        return
    trace_path = run_dir / "trace.jsonl"
    items = read_trace_items(trace_path)
    if record.terminal_event_index >= len(items):
        raise ValueError("pending recovery 指向不存在的 run_finished")
    terminal = items[record.terminal_event_index]
    if (
        terminal.get("event") != "run_finished"
        or terminal.get("status") != record.terminal_status
    ):
        raise ValueError("pending recovery 指向的 run_finished 已变化")
    matches = [
        item
        for item in items
        if item.get("event") == RUN_TERMINAL_SUPERSEDED_EVENT
        and item.get("recovery_id") == transaction.recovery_id
    ]
    if len(matches) > 1:
        raise ValueError("pending recovery 存在重复 supersede 事件")
    if matches:
        event = matches[0]
        if (
            event.get("terminal_event_index")
            != record.terminal_event_index
            or event.get("terminal_status") != record.terminal_status
        ):
            raise ValueError("pending recovery 的 supersede 绑定不一致")
        return
    TraceWriter(trace_path).write(
        RUN_TERMINAL_SUPERSEDED_EVENT,
        terminal_event_index=record.terminal_event_index,
        terminal_status=record.terminal_status,
        recovery_id=transaction.recovery_id,
        reason=transaction.reason,
    )


def _ensure_recovery_trace_events(
    run_dir: Path,
    transaction: RecoveryTransaction,
) -> None:
    """幂等补齐 recovery trace；重复调用只验证，不重复追加。"""

    _ensure_terminal_supersede(run_dir, transaction)
    trace_path = run_dir / "trace.jsonl"
    items = read_trace_items(trace_path) if trace_path.exists() else []
    trace = TraceWriter(trace_path)
    interrupted_iteration = transaction.interrupted_iteration
    if interrupted_iteration is not None:
        interruption_matches = [
            item
            for item in items
            if item.get("event") == "loop_iteration_interrupted"
            and item.get("recovery_id") == transaction.recovery_id
        ]
        if len(interruption_matches) > 1:
            raise ValueError("pending recovery 存在重复 interruption 事件")
        if interruption_matches:
            interruption = interruption_matches[0]
            if (
                interruption.get("iteration") != interrupted_iteration
                or interruption.get("previous_step")
                != transaction.previous_step
            ):
                raise ValueError("pending recovery 的 interruption 绑定不一致")
        else:
            trace.write(
                "loop_iteration_interrupted",
                iteration=interrupted_iteration,
                previous_step=transaction.previous_step,
                lifecycle="interrupted",
                artifact=(
                    f"iterations/{interrupted_iteration:02d}/"
                    "interruption-report.md"
                ),
                recovery_id=transaction.recovery_id,
            )
            items = read_trace_items(trace_path)

    recovered_matches = [
        item
        for item in items
        if item.get("event") == "loop_recovered"
        and item.get("recovery_id") == transaction.recovery_id
    ]
    if len(recovered_matches) > 1:
        raise ValueError("pending recovery 存在重复 loop_recovered 事件")
    superseded_index = (
        transaction.terminal_record.terminal_event_index
        if transaction.terminal_record is not None
        else None
    )
    if recovered_matches:
        recovered = recovered_matches[0]
        if (
            recovered.get("previous_step") != transaction.previous_step
            or recovered.get("previous_iteration")
            != transaction.previous_iteration
            or recovered.get("continuation_allowed")
            != (not transaction.initialization_issues)
            or recovered.get("superseded_terminal_event")
            != superseded_index
        ):
            raise ValueError("pending recovery 的 loop_recovered 绑定不一致")
        return
    trace.write(
        "loop_recovered",
        previous_step=transaction.previous_step,
        previous_iteration=transaction.previous_iteration,
        reason=transaction.reason,
        continuation_allowed=not transaction.initialization_issues,
        recovery_id=transaction.recovery_id,
        superseded_terminal_event=superseded_index,
    )


def _recovery_transaction_applied_to_state(
    state: LoopAutomationState,
    transaction: RecoveryTransaction,
) -> bool:
    if state.last_recovery_id != transaction.recovery_id:
        return False
    record = transaction.terminal_record
    if record is not None and record not in state.superseded_terminal_events:
        return False
    if transaction.interrupted_iteration is not None:
        matching_iterations = [
            iteration
            for iteration in state.iterations
            if iteration.iteration == transaction.interrupted_iteration
        ]
        if len(matching_iterations) != 1:
            return False
        iteration = matching_iterations[0]
        if (
            iteration.lifecycle != "interrupted"
            or iteration.interrupted_step != transaction.previous_step
            or iteration.interrupted_at != transaction.recovered_at
        ):
            return False
    if state.status == "needs_human":
        expected_step = recovered_initialization_step(
            transaction.initialization_issues
        )
        return state.current_step == expected_step
    return (
        state.status == "running"
        and (
            state.current_step != transaction.previous_step
            or state.current_iteration != transaction.previous_iteration
        )
    )


def _interrupted_iteration_action(
    state: LoopAutomationState,
) -> tuple[int | None, bool]:
    expected = list(range(1, len(state.iterations) + 1))
    actual = [item.iteration for item in state.iterations]
    if actual != expected:
        raise ValueError(
            f"iteration 序列不连续：期望 {expected or '[]'}，实际 {actual or '[]'}"
        )

    if state.current_iteration == 0:
        if state.iterations:
            raise ValueError("current_iteration=0 但已经存在 iteration")
        return None, False

    if not state.iterations:
        if state.current_iteration != 1:
            raise ValueError(
                "尚无已登记 iteration 时，current_iteration 只能为 1"
            )
        return state.current_iteration, True

    last = state.iterations[-1]
    if state.current_iteration == last.iteration:
        if last.lifecycle == "interrupted":
            raise ValueError("当前 iteration 已被标记为 interrupted")
        return state.current_iteration, False
    if state.current_iteration == last.iteration + 1:
        return state.current_iteration, True
    raise ValueError(
        "current_iteration 必须等于最后已登记 iteration，"
        "或恰好是下一连续编号"
    )


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _require_recovery_request(
    state: LoopAutomationState,
    run_dir: Path,
    reason: str,
) -> None:
    if state.run_id != run_dir.name:
        raise ValueError(
            "loop state.run_id 与 run 目录身份不一致；"
            "为避免在错误证据链上 recovery，已拒绝接管。"
        )
    if not reason.strip():
        raise ValueError("recover 必须提供原因，方便后续追溯。")


def _preflight_recovery_inspection(
    run_dir: Path,
    state: LoopAutomationState,
) -> ExecutionRecoveryInspection | None:
    if state.status != "running":
        return None
    inspection = inspect_execution_for_recovery(run_dir)
    if not inspection.can_recover:
        raise ValueError(inspection.summary)
    return inspection
