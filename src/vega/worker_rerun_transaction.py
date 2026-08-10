from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import LoopAutomationState, WorkerRerunAuthorization
from .trace import TraceWriter, read_trace_items
from .worker_baseline import (
    WORKER_BASELINE_ARTIFACT_VERSION,
    prepare_auto_worker_workspace_baseline,
    read_worker_workspace_baseline,
    worker_baseline_relative_path,
    worker_workspace_baseline_fingerprint,
    worker_workspace_fingerprint,
)
from .workspace_inventory import WorkspaceSnapshot

WORKER_RERUN_TRANSACTION_ARTIFACT = "worker-rerun-transaction.json"


class WorkerRerunTransaction(BaseModel):
    """跨 state、trace 与 baseline 提交显式重跑，崩溃后可重复补齐。"""

    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    run_id: str
    authorization: WorkerRerunAuthorization
    expected_workspace_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rerun_worker_baseline_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def commit_worker_rerun_authorization(
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
    *,
    authorization: WorkerRerunAuthorization,
    expected_workspace_snapshot: WorkspaceSnapshot,
) -> None:
    """幂等提交重跑授权；事务保留到 Worker 启动边界。"""

    transaction = WorkerRerunTransaction(
        run_id=state.run_id,
        authorization=authorization,
        expected_workspace_fingerprint=worker_workspace_fingerprint(
            expected_workspace_snapshot
        ),
    )
    pending = _read_worker_rerun_transaction(run_dir)
    if pending is None:
        _write_worker_rerun_transaction(run_dir, transaction)
    elif not _same_worker_rerun_intent(pending, transaction):
        raise ValueError("pending Worker 重跑事务与当前授权或工作区不一致")

    active = pending or transaction
    _ensure_worker_rerun_authorization_state(run_dir, state, active)
    _ensure_worker_rerun_request_trace(run_dir, trace, active)


def pending_worker_rerun_iteration(
    run_dir: Path,
    state: LoopAutomationState,
    *,
    expected_workspace_snapshot: WorkspaceSnapshot,
    source_interrupted_iteration: int,
    source_worker_baseline_sha256: str,
) -> int | None:
    """识别可幂等重入的同一重跑事务，避免把已准备目录当成新 iteration。"""

    transaction = _read_worker_rerun_transaction(run_dir)
    if transaction is None:
        return None
    authorization = transaction.authorization
    expected_iteration = state.iterations[-1].iteration + 1 if state.iterations else 1
    if (
        authorization.rerun_iteration != expected_iteration
        or authorization.source_interrupted_iteration
        != source_interrupted_iteration
        or authorization.recovery_id != state.last_recovery_id
        or authorization.source_worker_baseline_artifact_version
        != WORKER_BASELINE_ARTIFACT_VERSION
        or authorization.source_worker_baseline_sha256
        != source_worker_baseline_sha256
        or transaction.expected_workspace_fingerprint
        != worker_workspace_fingerprint(expected_workspace_snapshot)
    ):
        raise ValueError("pending Worker 重跑事务与当前 recovery 计划不一致")
    return authorization.rerun_iteration


def prepare_worker_rerun_baseline(
    run_dir: Path,
    trace: TraceWriter,
    *,
    authorization: WorkerRerunAuthorization,
    snapshot: WorkspaceSnapshot,
) -> str:
    """在 iteration claim 前准备并绑定本轮 baseline 到重跑事务。"""

    transaction = _require_worker_rerun_transaction(run_dir, authorization)
    if (
        transaction.expected_workspace_fingerprint
        != worker_workspace_fingerprint(snapshot)
    ):
        raise ValueError("工作区已偏离 pending Worker 重跑事务")
    digest = prepare_auto_worker_workspace_baseline(
        run_dir,
        trace,
        iteration=authorization.rerun_iteration,
        snapshot=snapshot,
    )
    if (
        transaction.rerun_worker_baseline_sha256 is not None
        and transaction.rerun_worker_baseline_sha256 != digest
    ):
        raise ValueError("pending Worker 重跑事务的 baseline 哈希不一致")
    if transaction.rerun_worker_baseline_sha256 is None:
        transaction = transaction.model_copy(update={"rerun_worker_baseline_sha256": digest})
        _write_worker_rerun_transaction(run_dir, transaction)
    return digest


def complete_worker_rerun_start(
    run_dir: Path,
    state: LoopAutomationState,
    *,
    authorization: WorkerRerunAuthorization,
) -> None:
    """确认 Worker 启动证据后提交事务；缺证据时保留事务供 recovery。"""

    transaction = _require_worker_rerun_transaction(run_dir, authorization)
    _validate_claimed_worker_rerun(run_dir, state, transaction)
    items = read_trace_items(run_dir / "trace.jsonl")
    started = [
        item
        for item in items
        if item.get("event") == "worker_started"
        and item.get("iteration") == authorization.rerun_iteration
    ]
    if len(started) != 1:
        raise ValueError("Worker 重跑缺少唯一 worker_started 启动证据")
    _delete_worker_rerun_transaction(run_dir)


def cancel_worker_rerun_before_start(
    run_dir: Path,
    trace: TraceWriter,
    *,
    authorization: WorkerRerunAuthorization,
    reason: str,
) -> None:
    """在 runner 尚未启动时终止重跑，并留下唯一、可核验的取消事件。"""

    _require_worker_rerun_transaction(run_dir, authorization)
    items = read_trace_items(run_dir / "trace.jsonl")
    if any(
        item.get("event") == "worker_started"
        and item.get("iteration") == authorization.rerun_iteration
        for item in items
    ):
        raise ValueError("Worker 已记录启动，不能再写入重跑取消事件")
    expected = {
        **authorization.model_dump(mode="json"),
        "reason": reason,
    }
    matches = [
        item
        for item in items
        if item.get("event") == "auto_worker_rerun_cancelled"
        and item.get("rerun_iteration") == authorization.rerun_iteration
        and item.get("recovery_id") == authorization.recovery_id
    ]
    if matches:
        if len(matches) != 1 or any(
            matches[0].get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Worker 重跑取消 trace 已存在冲突记录")
    else:
        trace.write("auto_worker_rerun_cancelled", **expected)
    _delete_worker_rerun_transaction(run_dir)


def reconcile_worker_rerun_transaction_for_recovery(
    run_dir: Path,
    state: LoopAutomationState,
    trace: TraceWriter,
    *,
    reason: str,
) -> Literal["none", "prestart_pending", "prestart_restored", "worker_started"]:
    """在通用 recovery 前处理重跑事务，避免把未启动 Worker 误记为中断。"""

    transaction = _read_worker_rerun_transaction(run_dir)
    if transaction is None:
        return "none"
    authorization = transaction.authorization
    items = read_trace_items(run_dir / "trace.jsonl")
    started = [
        item
        for item in items
        if item.get("event") == "worker_started"
        and item.get("iteration") == authorization.rerun_iteration
    ]
    if len(started) > 1:
        raise ValueError("pending Worker 重跑事务存在重复 worker_started")
    if started:
        _validate_claimed_worker_rerun(run_dir, state, transaction)
        _delete_worker_rerun_transaction(run_dir)
        return "worker_started"
    if (
        state.status == "needs_human"
        and state.current_step == "recovered"
        and state.current_iteration
        == authorization.source_interrupted_iteration
    ):
        return "prestart_pending"
    if (
        state.status != "running"
        or state.current_step != "worker"
        or state.current_iteration != authorization.rerun_iteration
    ):
        raise ValueError("pending Worker 重跑事务与当前 loop claim 不一致")
    _validate_claimed_worker_rerun(run_dir, state, transaction)
    _restore_source_worker_claim(run_dir, state, transaction)
    _ensure_worker_rerun_claim_recovered_trace(
        trace,
        items,
        transaction,
        reason=reason,
    )
    return "prestart_restored"


def worker_rerun_transaction_pending(run_dir: Path) -> bool:
    return _worker_rerun_transaction_path(run_dir).exists()


def _restore_source_worker_claim(
    run_dir: Path,
    state: LoopAutomationState,
    transaction: WorkerRerunTransaction,
) -> None:
    authorization = transaction.authorization
    state.status = "needs_human"
    state.current_step = "recovered"
    state.current_iteration = authorization.source_interrupted_iteration
    state.worker_baseline_artifact_version = (
        authorization.source_worker_baseline_artifact_version
    )
    state.worker_baseline_iteration = authorization.source_interrupted_iteration
    state.worker_baseline_sha256 = authorization.source_worker_baseline_sha256
    rerun_baseline = worker_baseline_relative_path(
        authorization.rerun_iteration
    )
    state.artifacts = [
        item for item in state.artifacts if item != rerun_baseline
    ]
    state.save(run_dir / "state.json")


def _ensure_worker_rerun_claim_recovered_trace(
    trace: TraceWriter,
    items: list[dict[str, object]],
    transaction: WorkerRerunTransaction,
    *,
    reason: str,
) -> None:
    authorization = transaction.authorization
    expected = {
        **authorization.model_dump(mode="json"),
        "reason": reason,
        "prepared_worker_baseline_sha256": (
            transaction.rerun_worker_baseline_sha256
        ),
    }
    matches = [
        item
        for item in items
        if item.get("event") == "auto_worker_rerun_claim_recovered"
        and item.get("rerun_iteration") == authorization.rerun_iteration
        and item.get("recovery_id") == authorization.recovery_id
    ]
    if matches:
        if len(matches) != 1 or any(
            matches[0].get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Worker 重跑 claim recovery trace 已存在冲突记录")
        return
    trace.write("auto_worker_rerun_claim_recovered", **expected)


def _same_worker_rerun_intent(
    pending: WorkerRerunTransaction,
    expected: WorkerRerunTransaction,
) -> bool:
    return (
        pending.run_id == expected.run_id
        and pending.authorization == expected.authorization
        and pending.expected_workspace_fingerprint
        == expected.expected_workspace_fingerprint
    )


def _require_worker_rerun_transaction(
    run_dir: Path,
    authorization: WorkerRerunAuthorization,
) -> WorkerRerunTransaction:
    transaction = _read_worker_rerun_transaction(run_dir)
    if transaction is None:
        raise ValueError("Worker 重跑事务缺失")
    if transaction.authorization != authorization:
        raise ValueError("Worker 重跑事务与当前授权不一致")
    return transaction


def _validate_claimed_worker_rerun(
    run_dir: Path,
    state: LoopAutomationState,
    transaction: WorkerRerunTransaction,
) -> None:
    authorization = transaction.authorization
    digest = transaction.rerun_worker_baseline_sha256
    if digest is None:
        raise ValueError("pending Worker 重跑事务缺少已准备 baseline")
    if (
        state.current_iteration != authorization.rerun_iteration
        or state.worker_baseline_artifact_version
        != WORKER_BASELINE_ARTIFACT_VERSION
        or state.worker_baseline_iteration != authorization.rerun_iteration
        or state.worker_baseline_sha256 != digest
    ):
        raise ValueError("pending Worker 重跑事务与 iteration claim 不一致")
    authorization_matches = [
        item
        for item in state.worker_rerun_authorizations
        if (
            item.rerun_iteration == authorization.rerun_iteration
            or item.recovery_id == authorization.recovery_id
        )
    ]
    if len(authorization_matches) != 1 or authorization_matches[0] != authorization:
        raise ValueError("pending Worker 重跑事务与 state 授权不一致")
    baseline = read_worker_workspace_baseline(
        run_dir / worker_baseline_relative_path(authorization.rerun_iteration),
        expected_sha256=digest,
    )
    if (
        worker_workspace_baseline_fingerprint(baseline)
        != transaction.expected_workspace_fingerprint
    ):
        raise ValueError("pending Worker 重跑事务与已准备 baseline 不一致")
    items = read_trace_items(run_dir / "trace.jsonl")
    if not _has_rerun_request(items, authorization):
        raise ValueError("pending Worker 重跑事务缺少唯一授权 trace")
    baseline_events = [
        item
        for item in items
        if item.get("event") == "worker_baseline_captured"
        and item.get("iteration") == authorization.rerun_iteration
    ]
    if (
        len(baseline_events) != 1
        or baseline_events[0].get("artifact")
        != worker_baseline_relative_path(authorization.rerun_iteration)
        or baseline_events[0].get("artifact_version")
        != WORKER_BASELINE_ARTIFACT_VERSION
        or baseline_events[0].get("sha256") != digest
    ):
        raise ValueError("pending Worker 重跑事务缺少唯一 baseline trace")


def _has_rerun_request(
    trace_items: list[dict[str, object]],
    authorization: WorkerRerunAuthorization,
) -> bool:
    matches = [
        item
        for item in trace_items
        if item.get("event") == "auto_worker_rerun_requested"
        and item.get("rerun_iteration") == authorization.rerun_iteration
        and item.get("source_interrupted_iteration")
        == authorization.source_interrupted_iteration
        and item.get("recovery_id") == authorization.recovery_id
        and item.get("source_worker_baseline_artifact_version")
        == authorization.source_worker_baseline_artifact_version
        and item.get("source_worker_baseline_sha256")
        == authorization.source_worker_baseline_sha256
    ]
    return len(matches) == 1


def _ensure_worker_rerun_authorization_state(
    run_dir: Path,
    state: LoopAutomationState,
    transaction: WorkerRerunTransaction,
) -> None:
    authorization = transaction.authorization
    matches = [
        item
        for item in state.worker_rerun_authorizations
        if (
            item.rerun_iteration == authorization.rerun_iteration
            or item.recovery_id == authorization.recovery_id
        )
    ]
    if not matches:
        if state.status != "needs_human" or state.current_step != "recovered":
            raise ValueError("Worker 重跑授权提交期间 loop 不再处于 recovered 状态")
        state.worker_rerun_authorizations.append(authorization)
        state.save(run_dir / "state.json")
        return
    if len(matches) != 1 or matches[0] != authorization:
        raise ValueError("Worker 重跑授权 state 已存在冲突记录")


def _ensure_worker_rerun_request_trace(
    run_dir: Path,
    trace: TraceWriter,
    transaction: WorkerRerunTransaction,
) -> None:
    authorization = transaction.authorization
    try:
        items = read_trace_items(run_dir / "trace.jsonl")
    except (OSError, ValueError) as exc:
        raise ValueError("Worker 重跑授权 trace 无法验证") from exc
    matches = [
        item
        for item in items
        if item.get("event") == "auto_worker_rerun_requested"
        and (
            item.get("rerun_iteration") == authorization.rerun_iteration
            or item.get("recovery_id") == authorization.recovery_id
        )
    ]
    expected = authorization.model_dump(mode="json")
    if matches:
        if len(matches) != 1 or any(
            matches[0].get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Worker 重跑授权 trace 已存在冲突记录")
        return
    trace.write("auto_worker_rerun_requested", **expected)


def _worker_rerun_transaction_path(run_dir: Path) -> Path:
    return run_dir / ".control" / WORKER_RERUN_TRANSACTION_ARTIFACT


def _read_worker_rerun_transaction(
    run_dir: Path,
) -> WorkerRerunTransaction | None:
    path = _worker_rerun_transaction_path(run_dir)
    if not path.exists():
        return None
    try:
        transaction = WorkerRerunTransaction.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise ValueError("pending Worker 重跑事务无法验证") from exc
    if transaction.run_id != run_dir.name:
        raise ValueError("pending Worker 重跑事务与 run 身份不一致")
    return transaction


def _write_worker_rerun_transaction(
    run_dir: Path,
    transaction: WorkerRerunTransaction,
) -> None:
    path = _worker_rerun_transaction_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".w.{uuid4().hex[:16]}")
    try:
        temp_path.write_text(
            json.dumps(
                transaction.model_dump(mode="json"),
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


def _delete_worker_rerun_transaction(run_dir: Path) -> None:
    try:
        _worker_rerun_transaction_path(run_dir).unlink(missing_ok=True)
    except OSError:
        # 已提交授权时残留事务只会在下次显式重跑时被再次验证和清理。
        pass
