from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from .loop_graph_state import GRAPH_SCHEMA_VERSION

CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_REF = "graph/checkpoints.sqlite"
CHECKPOINT_WAL_REF = "graph/checkpoints.sqlite-wal"
CHECKPOINT_MANIFEST_REF = "graph/checkpoint-manifest.json"
CHECKPOINT_PENDING_REF = "graph/checkpoint-pending.json"
# 顶层图必须使用空 namespace；非空 checkpoint_ns 会被 LangGraph 解释为 subgraph 路径。
CHECKPOINT_NAMESPACE = ""
CHECKPOINT_MANIFEST_MAX_BYTES = 32 * 1024
CHECKPOINT_PENDING_MAX_BYTES = 8 * 1024


class GraphCheckpointValidationError(ValueError):
    pass


class GraphCheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    run_id: str
    engine: str = "langgraph"
    graph_schema_version: str
    checkpoint_ref: str = CHECKPOINT_REF
    checkpoint_sha256: str
    checkpoint_bytes: int
    checkpoint_wal_ref: str | None = None
    checkpoint_wal_sha256: str | None = None
    checkpoint_wal_bytes: int = 0
    thread_id: str
    checkpoint_ns: str
    latest_checkpoint_id: str | None
    checkpoint_count: int
    writes_count: int
    created_at: str


class GraphCheckpointPendingMarker(BaseModel):
    """标记外部 Step Result 已落盘但下一次 Graph checkpoint 尚未提交。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    run_id: str
    engine: str = "langgraph"
    graph_schema_version: str
    step_id: str
    attempt_id: str
    step_result_id: str
    created_at: str


@dataclass(frozen=True)
class CheckpointStoreIdentity:
    latest_checkpoint_id: str | None
    checkpoint_count: int
    writes_count: int
    content_sha256: str


@dataclass(frozen=True)
class CheckpointFileIdentity:
    name: str
    sha256: str
    size: int
    device: int
    inode: int
    modified_ns: int
    changed_ns: int
    snapshot_files: tuple[tuple[str, int, str], ...] = ()


@dataclass(frozen=True)
class CheckpointTrustSnapshot:
    manifest: CheckpointFileIdentity
    checkpoint: CheckpointFileIdentity
    sidecars: tuple[CheckpointFileIdentity, ...]


@dataclass(frozen=True)
class CheckpointDataSnapshot:
    checkpoint: CheckpointFileIdentity
    sidecars: tuple[CheckpointFileIdentity, ...]


@dataclass(frozen=True)
class TrustedCheckpointState:
    manifest: GraphCheckpointManifest
    files: CheckpointTrustSnapshot
    store: CheckpointStoreIdentity


class _ManifestGuardedConnection(sqlite3.Connection):
    """在恢复连接执行首条 SQL 前完成最后一次信任边界复验。"""

    _before_first_sql: Callable[[], None] | None = None

    def arm_manifest_guard(self, guard: Callable[[], None]) -> None:
        self._before_first_sql = guard

    def _run_manifest_guard(self) -> None:
        guard = self._before_first_sql
        if guard is None:
            return
        guard()
        self._before_first_sql = None

    def cursor(self, *args, **kwargs):
        self._run_manifest_guard()
        return super().cursor(*args, **kwargs)

    def execute(self, *args, **kwargs):
        self._run_manifest_guard()
        return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        self._run_manifest_guard()
        return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        self._run_manifest_guard()
        return super().executescript(*args, **kwargs)

    def commit(self) -> None:
        self._run_manifest_guard()
        super().commit()


_CHECKPOINT_RESUME_DRIFT: set[Path] = set()
_CHECKPOINT_RESUME_DRIFT_LOCK = Lock()


def checkpoint_config(run_id: str) -> dict[str, dict[str, str]]:
    if not run_id.strip():
        raise GraphCheckpointValidationError("run_id 不能为空")
    return {
        "configurable": {
            "thread_id": run_id,
            "checkpoint_ns": CHECKPOINT_NAMESPACE,
        }
    }


@contextmanager
def open_sqlite_checkpointer(
    run_dir: Path,
    *,
    require_existing: bool = False,
    expected_trusted_state: TrustedCheckpointState | None = None,
) -> Iterator[Any]:
    """在 run 控制目录内打开同步 SQLite checkpointer。

    LangGraph 依赖保持可选导入，未启用 graph engine 时基础环境仍可使用 Linear Runtime。
    """

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ModuleNotFoundError as exc:
        raise GraphCheckpointValidationError(
            "SQLite checkpoint 需要安装 `vegaloom[langgraph]`"
        ) from exc

    checkpoint_path = _resolve_run_ref(run_dir, CHECKPOINT_REF, "checkpoint_ref")
    _assert_no_link_or_reparse(run_dir, checkpoint_path)
    if require_existing and not checkpoint_path.is_file():
        raise GraphCheckpointValidationError("checkpoint 数据库不存在")
    trusted_state: TrustedCheckpointState | None = None
    if require_existing:
        try:
            trusted_state = (
                expected_trusted_state
                if expected_trusted_state is not None
                else _capture_trusted_checkpoint_state(run_dir)
            )
            _revalidate_checkpoint_before_write(
                run_dir,
                trusted_state,
            )
        except GraphCheckpointValidationError:
            _mark_checkpoint_resume_drift(run_dir)
            raise
    elif expected_trusted_state is not None:
        raise GraphCheckpointValidationError(
            "expected_trusted_state 只允许用于恢复既有 checkpoint"
        )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, checkpoint_path)

    class ManifestingSqliteSaver(SqliteSaver):
        """每次 SQLite 提交后立即封存文件身份，避免依赖 Python finally。"""

        def _seal_manifest(self, config: dict[str, Any]) -> None:
            # SqliteSaver 的 cursor 已释放锁。这里重新持锁，避免读取 WAL 时与
            # 同一 saver 的另一次写入交错，导致 manifest 绑定半个文件现场。
            with self.lock:
                write_checkpoint_manifest(
                    run_dir,
                    config,
                    connection=self.conn,
                )

        def put(self, config, checkpoint, metadata, new_versions):
            saved_config = super().put(
                config,
                checkpoint,
                metadata,
                new_versions,
            )
            self._seal_manifest(saved_config)
            # 只有新的 checkpoint 行已经提交，才代表 Graph 游标消费了
            # pending Step Result；put_writes 仍可能只是中间态，不能提前清除。
            clear_checkpoint_pending_marker(run_dir)
            return saved_config

        def put_writes(
            self,
            config,
            writes,
            task_id,
            task_path="",
        ) -> None:
            super().put_writes(config, writes, task_id, task_path)
            self._seal_manifest(config)

    connection = sqlite3.connect(
        str(checkpoint_path),
        check_same_thread=False,
        factory=_ManifestGuardedConnection,
    )
    try:
        if trusted_state is not None:
            connection.arm_manifest_guard(
                lambda: _revalidate_checkpoint_before_write(
                    run_dir,
                    trusted_state,
                )
            )
        checkpointer = ManifestingSqliteSaver(connection)
        checkpointer.setup()
        _use_manifest_safe_journal_mode(connection)
        yield checkpointer
    finally:
        connection.close()


def write_checkpoint_manifest(
    run_dir: Path,
    config: dict[str, Any],
    *,
    connection: sqlite3.Connection | None = None,
    expected_data_snapshot: CheckpointDataSnapshot | None = None,
) -> GraphCheckpointManifest:
    _assert_checkpoint_manifest_write_allowed(run_dir)
    checkpoint_path = _resolve_run_ref(run_dir, CHECKPOINT_REF, "checkpoint_ref")
    if not checkpoint_path.is_file():
        raise GraphCheckpointValidationError("checkpoint 数据库不存在")
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        raise GraphCheckpointValidationError("checkpoint config 缺少 configurable")
    thread_id = configurable.get("thread_id")
    checkpoint_ns = configurable.get("checkpoint_ns", "")
    if thread_id != run_dir.name:
        raise GraphCheckpointValidationError("checkpoint thread_id 与 run_id 不一致")
    if checkpoint_ns != CHECKPOINT_NAMESPACE:
        raise GraphCheckpointValidationError("checkpoint namespace 不一致")
    if expected_data_snapshot is not None:
        _require_checkpoint_data_snapshot(
            run_dir,
            expected_data_snapshot,
            "checkpoint 文件在 manifest 封存前发生漂移",
        )
    if connection is not None:
        store_identity = _inspect_checkpoint_connection(
            connection,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            require_integrity_check=False,
        )
    else:
        store_identity = _inspect_checkpoint_store(
            run_dir,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
        )
    data_snapshot = _checkpoint_data_snapshot(run_dir)
    if (
        expected_data_snapshot is not None
        and data_snapshot != expected_data_snapshot
    ):
        raise GraphCheckpointValidationError(
            "checkpoint 文件在 manifest 内容计算期间发生漂移"
        )
    wal_identity = next(
        (
            identity
            for identity in data_snapshot.sidecars
            if identity.name.casefold()
            == Path(CHECKPOINT_WAL_REF).name.casefold()
        ),
        None,
    )
    manifest = GraphCheckpointManifest(
        run_id=run_dir.name,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        checkpoint_sha256=data_snapshot.checkpoint.sha256,
        checkpoint_bytes=data_snapshot.checkpoint.size,
        checkpoint_wal_ref=(
            CHECKPOINT_WAL_REF if wal_identity is not None else None
        ),
        checkpoint_wal_sha256=(
            wal_identity.sha256 if wal_identity is not None else None
        ),
        checkpoint_wal_bytes=(
            wal_identity.size if wal_identity is not None else 0
        ),
        thread_id=thread_id,
        checkpoint_ns=checkpoint_ns,
        latest_checkpoint_id=store_identity.latest_checkpoint_id,
        checkpoint_count=store_identity.checkpoint_count,
        writes_count=store_identity.writes_count,
        created_at=datetime.now(UTC).isoformat(),
    )
    manifest_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_MANIFEST_REF,
        "checkpoint_manifest_ref",
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_or_reparse(run_dir, manifest_path)
    if expected_data_snapshot is not None:
        _require_checkpoint_data_snapshot(
            run_dir,
            expected_data_snapshot,
            "checkpoint 文件在 manifest 发布前发生漂移",
        )
    _write_text_atomic(
        manifest_path,
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return manifest


def write_checkpoint_pending_marker(
    run_dir: Path,
    *,
    step_id: str,
    attempt_id: str,
    step_result_id: str,
) -> GraphCheckpointPendingMarker:
    """在外部副作用与下一次 Graph checkpoint 之间写入硬退出标记。

    该标记不是业务状态，也不是第二套 execution 事实源；它只用于区分
    `os._exit` 这类不会执行 Python finally 的硬退出。正常 checkpoint seal
    成功后会立即清除，硬退出则保留并让恢复 fail-closed。
    """

    if not all(
        isinstance(value, str) and value.strip()
        for value in (step_id, attempt_id, step_result_id)
    ):
        raise GraphCheckpointValidationError(
            "checkpoint pending marker identity 不能为空"
        )
    marker_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_PENDING_REF,
        "checkpoint_pending_ref",
    )
    if marker_path.exists():
        raise GraphCheckpointValidationError(
            "checkpoint pending marker 已存在，禁止覆盖未完成的 Graph 提交"
        )
    marker = GraphCheckpointPendingMarker(
        run_id=run_dir.name,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        step_id=step_id,
        attempt_id=attempt_id,
        step_result_id=step_result_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    _write_text_atomic(
        marker_path,
        json.dumps(
            marker.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return marker


def clear_checkpoint_pending_marker(run_dir: Path) -> None:
    """只删除当前 run 自己的 pending marker；不存在时保持幂等。"""

    marker_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_PENDING_REF,
        "checkpoint_pending_ref",
    )
    if not marker_path.exists():
        return
    _assert_no_link_or_reparse(run_dir, marker_path)
    try:
        marker_path.unlink()
    except OSError as exc:
        raise GraphCheckpointValidationError(
            "checkpoint pending marker 无法清除"
        ) from exc


def validate_checkpoint_manifest(run_dir: Path) -> GraphCheckpointManifest:
    pending_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_PENDING_REF,
        "checkpoint_pending_ref",
    )
    if pending_path.exists():
        try:
            if pending_path.stat().st_size > CHECKPOINT_PENDING_MAX_BYTES:
                raise GraphCheckpointValidationError(
                    "checkpoint pending marker 超过大小限制"
                )
            pending = GraphCheckpointPendingMarker.model_validate(
                json.loads(
                    pending_path.read_text(encoding="utf-8"),
                    object_pairs_hook=_reject_duplicate_json_object,
                )
            )
        except GraphCheckpointValidationError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise GraphCheckpointValidationError(
                "checkpoint pending marker 无法解析"
            ) from exc
        if (
            pending.run_id != run_dir.name
            or pending.engine != "langgraph"
            or pending.graph_schema_version != GRAPH_SCHEMA_VERSION
        ):
            raise GraphCheckpointValidationError(
                "checkpoint pending marker identity 不一致"
            )
        raise GraphCheckpointValidationError(
            "checkpoint manifest 未完成本次 Graph 提交：存在 pending marker"
        )

    manifest_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_MANIFEST_REF,
        "checkpoint_manifest_ref",
    )
    _assert_no_link_or_reparse(run_dir, manifest_path)
    if not manifest_path.is_file():
        raise GraphCheckpointValidationError("checkpoint manifest 不存在")
    try:
        if manifest_path.stat().st_size > CHECKPOINT_MANIFEST_MAX_BYTES:
            raise GraphCheckpointValidationError("checkpoint manifest 超过大小限制")
        payload = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
        )
        manifest = GraphCheckpointManifest.model_validate(payload)
    except GraphCheckpointValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise GraphCheckpointValidationError("checkpoint manifest 无法解析") from exc
    if manifest.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise GraphCheckpointValidationError("checkpoint manifest schema 不受支持")
    if manifest.run_id != run_dir.name or manifest.thread_id != run_dir.name:
        raise GraphCheckpointValidationError("checkpoint manifest run identity 不一致")
    if manifest.engine != "langgraph":
        raise GraphCheckpointValidationError("checkpoint manifest engine 不一致")
    if manifest.graph_schema_version != GRAPH_SCHEMA_VERSION:
        raise GraphCheckpointValidationError("checkpoint manifest graph schema 不一致")
    if manifest.checkpoint_ref != CHECKPOINT_REF:
        raise GraphCheckpointValidationError("checkpoint manifest 引用不一致")
    if manifest.checkpoint_wal_ref not in {None, CHECKPOINT_WAL_REF}:
        raise GraphCheckpointValidationError("checkpoint WAL 引用不一致")
    if manifest.checkpoint_ns != CHECKPOINT_NAMESPACE:
        raise GraphCheckpointValidationError("checkpoint manifest namespace 不一致")
    checkpoint_path = _resolve_run_ref(
        run_dir,
        manifest.checkpoint_ref,
        "checkpoint_ref",
    )
    if not checkpoint_path.is_file():
        raise GraphCheckpointValidationError("checkpoint 数据库不存在")
    if _sha256_file(checkpoint_path) != manifest.checkpoint_sha256:
        raise GraphCheckpointValidationError("checkpoint hash 与 manifest 不一致")
    if checkpoint_path.stat().st_size != manifest.checkpoint_bytes:
        raise GraphCheckpointValidationError("checkpoint 大小与 manifest 不一致")
    wal_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_WAL_REF,
        "checkpoint_wal_ref",
    )
    _reject_unbound_sqlite_transaction_state(
        checkpoint_path,
        wal_bound=manifest.checkpoint_wal_ref is not None,
    )
    if manifest.checkpoint_wal_ref is None:
        if (
            manifest.checkpoint_wal_sha256 is not None
            or manifest.checkpoint_wal_bytes != 0
        ):
            raise GraphCheckpointValidationError(
                "checkpoint WAL manifest 字段不一致"
            )
        if wal_path.exists():
            raise GraphCheckpointValidationError(
                "checkpoint WAL 未被 manifest 绑定"
            )
    else:
        if not wal_path.is_file():
            raise GraphCheckpointValidationError("checkpoint WAL 不存在")
        if manifest.checkpoint_wal_sha256 is None:
            raise GraphCheckpointValidationError("checkpoint WAL hash 缺失")
        if _sha256_file(wal_path) != manifest.checkpoint_wal_sha256:
            raise GraphCheckpointValidationError(
                "checkpoint WAL hash 与 manifest 不一致"
            )
        if wal_path.stat().st_size != manifest.checkpoint_wal_bytes:
            raise GraphCheckpointValidationError(
                "checkpoint WAL 大小与 manifest 不一致"
            )
    immutable = manifest.checkpoint_wal_ref is None
    # DB 哈希已绑定且不存在未封存事务侧文件时，immutable 读取可以保证
    # 校验过程不修改原 checkpoint。
    store_identity = _inspect_checkpoint_store(
        run_dir,
        thread_id=manifest.thread_id,
        checkpoint_ns=manifest.checkpoint_ns,
        immutable=immutable,
    )
    if (
        store_identity.latest_checkpoint_id
        != manifest.latest_checkpoint_id
    ):
        raise GraphCheckpointValidationError(
            "checkpoint 最新游标与 manifest 不一致"
        )
    if store_identity.checkpoint_count != manifest.checkpoint_count:
        raise GraphCheckpointValidationError(
            "checkpoint 数量与 manifest 不一致"
        )
    if store_identity.writes_count != manifest.writes_count:
        raise GraphCheckpointValidationError(
            "checkpoint writes 数量与 manifest 不一致"
        )
    return manifest


def checkpoint_exists(run_dir: Path) -> bool:
    checkpoint_path = _resolve_run_ref(run_dir, CHECKPOINT_REF, "checkpoint_ref")
    manifest_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_MANIFEST_REF,
        "checkpoint_manifest_ref",
    )
    return checkpoint_path.is_file() and manifest_path.is_file()


def capture_trusted_checkpoint_state(
    run_dir: Path,
) -> TrustedCheckpointState:
    """捕获 manifest 与 SQLite 文件集合的同一可信快照。"""

    return _capture_trusted_checkpoint_state(run_dir)


def capture_checkpoint_data_snapshot(
    run_dir: Path,
) -> CheckpointDataSnapshot:
    """捕获当前 SQLite 主文件和事务 sidecar 的内容及文件身份。"""

    return _checkpoint_data_snapshot(run_dir)


def capture_trusted_checkpoint_data_for_resume(
    run_dir: Path,
    trusted: TrustedCheckpointState,
) -> CheckpointDataSnapshot:
    """把已校验 manifest 的完整文件集合固定为恢复首个内容锚点。"""

    if (
        trusted.manifest.checkpoint_wal_ref is not None
        or trusted.files.sidecars
    ):
        _mark_checkpoint_resume_drift(run_dir)
        raise GraphCheckpointValidationError(
            "checkpoint 恢复只接受 manifest-safe 的无 WAL 文件集合"
        )
    snapshot_files = tuple(
        (identity.name, identity.size, identity.sha256)
        for identity in (
            trusted.files.checkpoint,
            *trusted.files.sidecars,
        )
    )
    expected = CheckpointDataSnapshot(
        checkpoint=replace(
            trusted.files.checkpoint,
            snapshot_files=snapshot_files,
        ),
        sidecars=trusted.files.sidecars,
    )
    observed = _checkpoint_data_snapshot(run_dir)
    if observed != expected:
        _mark_checkpoint_resume_drift(run_dir)
        raise GraphCheckpointValidationError(
            "checkpoint 完整文件集合在恢复打开前发生漂移"
        )
    return expected


def capture_checkpoint_store_identity(
    run_dir: Path,
    *,
    manifest: GraphCheckpointManifest,
) -> CheckpointStoreIdentity:
    return _inspect_checkpoint_store(
        run_dir,
        thread_id=manifest.thread_id,
        checkpoint_ns=manifest.checkpoint_ns,
        immutable=manifest.checkpoint_wal_ref is None,
    )


def require_checkpoint_store_continuity(
    run_dir: Path,
    *,
    expected: CheckpointStoreIdentity,
    observed: CheckpointStoreIdentity,
) -> None:
    if observed != expected:
        _mark_checkpoint_resume_drift(run_dir)
        raise GraphCheckpointValidationError(
            "checkpoint 逻辑内容在恢复预读后发生漂移"
        )


def require_checkpoint_file_layout_continuity(
    run_dir: Path,
    *,
    expected: CheckpointDataSnapshot,
    observed: CheckpointDataSnapshot,
) -> None:
    if (
        _checkpoint_data_layout_identity(observed)
        != _checkpoint_data_layout_identity(expected)
    ):
        _mark_checkpoint_resume_drift(run_dir)
        raise GraphCheckpointValidationError(
            "checkpoint 主库或 sidecar 文件身份在恢复打开后发生漂移"
        )


def require_checkpoint_file_continuity(
    run_dir: Path,
    *,
    expected: CheckpointDataSnapshot,
    observed: CheckpointDataSnapshot,
) -> None:
    """恢复预读前后必须仍是同一个完整 SQLite 文件快照。"""

    if (
        _checkpoint_data_content_identity(observed)
        != _checkpoint_data_content_identity(expected)
    ):
        _mark_checkpoint_resume_drift(run_dir)
        raise GraphCheckpointValidationError(
            "checkpoint 主库或 sidecar 在恢复预读后发生漂移"
        )


def _checkpoint_data_content_identity(
    snapshot: CheckpointDataSnapshot,
) -> tuple[tuple[str, str, int, int, int], ...]:
    return tuple(
        (
            identity.name,
            identity.sha256,
            identity.size,
            identity.device,
            identity.inode,
        )
        for identity in (
            snapshot.checkpoint,
            *snapshot.sidecars,
        )
    )


def _checkpoint_data_layout_identity(
    snapshot: CheckpointDataSnapshot,
) -> tuple[tuple[str, int, int, int], ...]:
    return tuple(
        (
            identity.name,
            identity.size,
            identity.device,
            identity.inode,
        )
        for identity in (
            snapshot.checkpoint,
            *snapshot.sidecars,
        )
    )


def seal_checkpoint_manifest_for_resume(
    run_dir: Path,
    config: dict[str, Any],
    *,
    expected_data_snapshot: CheckpointDataSnapshot,
) -> TrustedCheckpointState:
    """只为已观测的稳定 SQLite 文件集合重新封存 manifest。"""

    try:
        write_checkpoint_manifest(
            run_dir,
            config,
            expected_data_snapshot=expected_data_snapshot,
        )
        sealed_data = _checkpoint_data_snapshot(run_dir)
        if sealed_data != expected_data_snapshot:
            raise GraphCheckpointValidationError(
                "checkpoint manifest 未绑定预期的恢复文件集合"
            )
        sealed = _capture_trusted_checkpoint_state(run_dir)
    except GraphCheckpointValidationError:
        _mark_checkpoint_resume_drift(run_dir)
        raise
    _clear_checkpoint_resume_drift(run_dir)
    return sealed


def _resolve_run_ref(run_dir: Path, ref: str, field: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref:
        raise GraphCheckpointValidationError(
            f"{field} 必须是非空 POSIX 相对路径"
        )
    pure = PurePosixPath(ref)
    if (
        pure.is_absolute()
        or ":" in pure.parts[0]
        or "." in pure.parts
        or ".." in pure.parts
    ):
        raise GraphCheckpointValidationError(f"{field} 不能越过 run 目录")
    candidate = run_dir.joinpath(*pure.parts)
    _assert_no_link_or_reparse(run_dir, candidate)
    resolved = candidate.resolve()
    root = run_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise GraphCheckpointValidationError(f"{field} 不能越过 run 目录")
    return resolved


def _assert_no_link_or_reparse(run_dir: Path, target: Path) -> None:
    try:
        relative = target.relative_to(run_dir)
    except ValueError as exc:
        raise GraphCheckpointValidationError(
            "checkpoint 路径不能越过 run 目录"
        ) from exc
    current = run_dir
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            continue
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise GraphCheckpointValidationError(
                f"无法检查 checkpoint 路径：{current.name}"
            ) from exc
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag):
            raise GraphCheckpointValidationError(
                "checkpoint 路径不能包含链接或 reparse point"
            )


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GraphCheckpointValidationError(
            f"无法读取 checkpoint 文件：{path.name}"
        ) from exc


def _use_manifest_safe_journal_mode(
    connection: sqlite3.Connection,
) -> None:
    """使用单写者 rollback journal，避免 WAL 成为第二个未原子封存文件。"""

    checkpoint_result = connection.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()
    if checkpoint_result is None or int(checkpoint_result[0]) != 0:
        raise GraphCheckpointValidationError(
            "checkpoint WAL 无法在打开时稳定合并"
        )
    journal_mode = connection.execute(
        "PRAGMA journal_mode=DELETE"
    ).fetchone()
    if (
        journal_mode is None
        or str(journal_mode[0]).lower() != "delete"
    ):
        raise GraphCheckpointValidationError(
            "checkpoint 数据库无法切换为 manifest-safe journal mode"
        )


def _reject_unbound_sqlite_transaction_state(
    checkpoint_path: Path,
    *,
    wal_bound: bool,
) -> None:
    """拒绝未被 manifest 封存、后续可写连接可能处理的事务侧文件。"""

    canonical_wal_name = f"{checkpoint_path.name}-wal"
    unbound_paths = [
        path
        for path in _sqlite_transaction_sidecars(checkpoint_path)
        if not wal_bound or path.name != canonical_wal_name
    ]
    if unbound_paths:
        names = ", ".join(path.name for path in unbound_paths)
        raise GraphCheckpointValidationError(
            f"checkpoint SQLite 事务侧文件未被 manifest 绑定：{names}"
        )


def _sqlite_transaction_sidecars(checkpoint_path: Path) -> tuple[Path, ...]:
    """按 Windows 文件名语义识别 SQLite 事务 sidecar。"""

    database_name = checkpoint_path.name.casefold()
    try:
        sidecars = []
        for path in checkpoint_path.parent.iterdir():
            folded_name = path.name.casefold()
            if not folded_name.startswith(database_name):
                continue
            suffix = folded_name[len(database_name) :]
            if suffix in {"-journal", "-wal", "-shm"} or suffix.startswith("-mj"):
                sidecars.append(path)
        return tuple(sorted(sidecars, key=lambda path: path.name.casefold()))
    except OSError as exc:
        raise GraphCheckpointValidationError(
            "无法检查 checkpoint SQLite 事务侧文件"
        ) from exc


def _capture_trusted_checkpoint_state(
    run_dir: Path,
) -> TrustedCheckpointState:
    files_before = _checkpoint_trust_snapshot(run_dir)
    manifest = validate_checkpoint_manifest(run_dir)
    store = _inspect_checkpoint_store(
        run_dir,
        thread_id=manifest.thread_id,
        checkpoint_ns=manifest.checkpoint_ns,
        immutable=manifest.checkpoint_wal_ref is None,
    )
    files_after = _checkpoint_trust_snapshot(run_dir)
    if files_before != files_after:
        raise GraphCheckpointValidationError(
            "checkpoint 文件在恢复校验期间发生漂移"
        )
    return TrustedCheckpointState(
        manifest=manifest,
        files=files_after,
        store=store,
    )


def _revalidate_checkpoint_before_write(
    run_dir: Path,
    expected: TrustedCheckpointState,
) -> None:
    try:
        current = _capture_trusted_checkpoint_state(run_dir)
        if current != expected:
            raise GraphCheckpointValidationError(
                "checkpoint manifest、数据库或 sidecar 在恢复打开前发生漂移"
            )
    except GraphCheckpointValidationError:
        _mark_checkpoint_resume_drift(run_dir)
        raise
    _clear_checkpoint_resume_drift(run_dir)


def _require_checkpoint_data_snapshot(
    run_dir: Path,
    expected: CheckpointDataSnapshot,
    message: str,
) -> None:
    if _checkpoint_data_snapshot(run_dir) != expected:
        _mark_checkpoint_resume_drift(run_dir)
        raise GraphCheckpointValidationError(message)


def _checkpoint_data_snapshot(run_dir: Path) -> CheckpointDataSnapshot:
    checkpoint_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_REF,
        "checkpoint_ref",
    )
    sidecar_paths_before = _sqlite_transaction_sidecars(checkpoint_path)
    snapshot_before = _read_checkpoint_data_snapshot(
        checkpoint_path,
        sidecar_paths_before,
    )
    sidecar_paths_between = _sqlite_transaction_sidecars(checkpoint_path)
    if _checkpoint_path_names(sidecar_paths_before) != _checkpoint_path_names(
        sidecar_paths_between
    ):
        raise GraphCheckpointValidationError(
            "checkpoint SQLite 事务侧文件在校验期间发生漂移"
        )
    snapshot_after = _read_checkpoint_data_snapshot(
        checkpoint_path,
        sidecar_paths_between,
    )
    sidecar_paths_after = _sqlite_transaction_sidecars(checkpoint_path)
    if _checkpoint_path_names(sidecar_paths_between) != _checkpoint_path_names(
        sidecar_paths_after
    ):
        raise GraphCheckpointValidationError(
            "checkpoint SQLite 事务侧文件在校验期间发生漂移"
        )
    if snapshot_before != snapshot_after:
        raise GraphCheckpointValidationError(
            "checkpoint 主库或 sidecar 在快照读取期间发生漂移"
        )
    snapshot_files = tuple(
        (identity.name, identity.size, identity.sha256)
        for identity in (
            snapshot_after.checkpoint,
            *snapshot_after.sidecars,
        )
    )
    return CheckpointDataSnapshot(
        checkpoint=replace(
            snapshot_after.checkpoint,
            snapshot_files=snapshot_files,
        ),
        sidecars=snapshot_after.sidecars,
    )


def _read_checkpoint_data_snapshot(
    checkpoint_path: Path,
    sidecar_paths: tuple[Path, ...],
) -> CheckpointDataSnapshot:
    return CheckpointDataSnapshot(
        checkpoint=_checkpoint_file_identity(checkpoint_path),
        sidecars=tuple(
            _checkpoint_file_identity(path)
            for path in sidecar_paths
        ),
    )


def _checkpoint_path_names(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(path.name for path in paths)


def _checkpoint_trust_snapshot(run_dir: Path) -> CheckpointTrustSnapshot:
    manifest_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_MANIFEST_REF,
        "checkpoint_manifest_ref",
    )
    data = _checkpoint_data_snapshot(run_dir)
    snapshot = CheckpointTrustSnapshot(
        manifest=_checkpoint_file_identity(manifest_path),
        checkpoint=replace(data.checkpoint, snapshot_files=()),
        sidecars=data.sidecars,
    )
    return snapshot


def _checkpoint_file_identity(path: Path) -> CheckpointFileIdentity:
    try:
        metadata_before = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        metadata_after = path.lstat()
    except OSError as exc:
        raise GraphCheckpointValidationError(
            f"无法读取 checkpoint 文件：{path.name}"
        ) from exc
    before_signature = _checkpoint_stat_signature(metadata_before)
    after_signature = _checkpoint_stat_signature(metadata_after)
    if before_signature != after_signature:
        raise GraphCheckpointValidationError(
            f"checkpoint 文件在读取期间发生漂移：{path.name}"
        )
    return CheckpointFileIdentity(
        name=path.name,
        sha256=digest,
        size=metadata_after.st_size,
        device=metadata_after.st_dev,
        inode=metadata_after.st_ino,
        modified_ns=metadata_after.st_mtime_ns,
        changed_ns=metadata_after.st_ctime_ns,
    )


def _checkpoint_stat_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_mode,
        metadata.st_size,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _checkpoint_run_key(run_dir: Path) -> Path:
    return run_dir.resolve()


def _mark_checkpoint_resume_drift(run_dir: Path) -> None:
    with _CHECKPOINT_RESUME_DRIFT_LOCK:
        _CHECKPOINT_RESUME_DRIFT.add(_checkpoint_run_key(run_dir))


def _clear_checkpoint_resume_drift(run_dir: Path) -> None:
    with _CHECKPOINT_RESUME_DRIFT_LOCK:
        _CHECKPOINT_RESUME_DRIFT.discard(_checkpoint_run_key(run_dir))


def _assert_checkpoint_manifest_write_allowed(run_dir: Path) -> None:
    with _CHECKPOINT_RESUME_DRIFT_LOCK:
        blocked = _checkpoint_run_key(run_dir) in _CHECKPOINT_RESUME_DRIFT
    if blocked:
        raise GraphCheckpointValidationError(
            "checkpoint 恢复信任边界已发生漂移，禁止重写 manifest"
        )


def _inspect_checkpoint_store(
    run_dir: Path,
    *,
    thread_id: str,
    checkpoint_ns: str,
    immutable: bool = False,
) -> CheckpointStoreIdentity:
    """只读校验 SQLite 结构与 run identity，并返回可绑定的逻辑游标。"""

    checkpoint_path = _resolve_run_ref(
        run_dir,
        CHECKPOINT_REF,
        "checkpoint_ref",
    )
    if not checkpoint_path.is_file():
        raise GraphCheckpointValidationError("checkpoint 数据库不存在")
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    try:
        connection = sqlite3.connect(
            f"{checkpoint_path.as_uri()}?{query}",
            uri=True,
        )
    except sqlite3.Error as exc:
        raise GraphCheckpointValidationError(
            "checkpoint 数据库无法只读打开"
        ) from exc
    try:
        return _inspect_checkpoint_connection(
            connection,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            require_integrity_check=True,
        )
    except GraphCheckpointValidationError:
        raise
    except sqlite3.Error as exc:
        raise GraphCheckpointValidationError(
            "checkpoint 数据库 schema 或内容不合法"
        ) from exc
    finally:
        connection.close()


def _inspect_checkpoint_connection(
    connection: sqlite3.Connection,
    *,
    thread_id: str,
    checkpoint_ns: str,
    require_integrity_check: bool,
) -> CheckpointStoreIdentity:
    if require_integrity_check:
        quick_check = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        if quick_check != ["ok"]:
            raise GraphCheckpointValidationError(
                "checkpoint 数据库完整性检查失败"
            )
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if table_names != {"checkpoints", "writes"}:
        raise GraphCheckpointValidationError(
            "checkpoint 数据库表结构不受支持"
        )
    identities = {
        (str(row[0]), str(row[1]))
        for table in ("checkpoints", "writes")
        for row in connection.execute(
            f"SELECT DISTINCT thread_id, checkpoint_ns FROM {table}"
        ).fetchall()
    }
    unexpected = identities - {(thread_id, checkpoint_ns)}
    if unexpected:
        raise GraphCheckpointValidationError(
            "checkpoint 数据库包含其他 run 或 namespace"
        )
    checkpoint_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM checkpoints "
            "WHERE thread_id = ? AND checkpoint_ns = ?",
            (thread_id, checkpoint_ns),
        ).fetchone()[0]
    )
    latest_row = connection.execute(
        "SELECT checkpoint_id FROM checkpoints "
        "WHERE thread_id = ? AND checkpoint_ns = ? "
        "ORDER BY checkpoint_id DESC LIMIT 1",
        (thread_id, checkpoint_ns),
    ).fetchone()
    writes_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM writes "
            "WHERE thread_id = ? AND checkpoint_ns = ?",
            (thread_id, checkpoint_ns),
        ).fetchone()[0]
    )
    content_sha256 = _checkpoint_store_content_sha256(
        connection,
        thread_id=thread_id,
        checkpoint_ns=checkpoint_ns,
    )
    return CheckpointStoreIdentity(
        latest_checkpoint_id=(
            str(latest_row[0]) if latest_row is not None else None
        ),
        checkpoint_count=checkpoint_count,
        writes_count=writes_count,
        content_sha256=content_sha256,
    )


def _checkpoint_store_content_sha256(
    connection: sqlite3.Connection,
    *,
    thread_id: str,
    checkpoint_ns: str,
) -> str:
    digest = hashlib.sha256()
    table_queries = (
        (
            "checkpoints",
            (
                "thread_id",
                "checkpoint_ns",
                "checkpoint_id",
                "parent_checkpoint_id",
                "type",
                "checkpoint",
                "metadata",
            ),
            "checkpoint_id",
        ),
        (
            "writes",
            (
                "thread_id",
                "checkpoint_ns",
                "checkpoint_id",
                "task_id",
                "idx",
                "channel",
                "type",
                "value",
            ),
            "checkpoint_id, task_id, idx",
        ),
    )
    for table, columns, order_by in table_queries:
        _hash_checkpoint_value(digest, table)
        _hash_checkpoint_value(digest, columns)
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} "
            "WHERE thread_id = ? AND checkpoint_ns = ? "
            f"ORDER BY {order_by}",
            (thread_id, checkpoint_ns),
        )
        for row in rows:
            _hash_checkpoint_value(digest, row)
    return digest.hexdigest()


def _hash_checkpoint_value(
    digest: Any,
    value: object,
) -> None:
    if value is None:
        marker = b"N"
        payload = b""
    elif isinstance(value, bytes):
        marker = b"B"
        payload = value
    elif isinstance(value, memoryview):
        marker = b"B"
        payload = value.tobytes()
    elif isinstance(value, str):
        marker = b"S"
        payload = value.encode("utf-8")
    elif isinstance(value, int) and not isinstance(value, bool):
        marker = b"I"
        payload = str(value).encode("ascii")
    elif isinstance(value, (tuple, list)):
        marker = b"L"
        payload = b""
        digest.update(marker)
        digest.update(len(value).to_bytes(8, "big"))
        for item in value:
            _hash_checkpoint_value(digest, item)
        return
    else:
        raise GraphCheckpointValidationError(
            "checkpoint 逻辑内容包含不受支持的 SQLite 值类型"
        )
    digest.update(marker)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _write_text_atomic(path: Path, content: str) -> None:
    temp_path = path.with_name(f".tmp-{uuid4().hex[:10]}")
    last_error: OSError | None = None
    try:
        temp_path.write_text(
            content,
            encoding="utf-8",
            newline="\n",
        )
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


def _reject_duplicate_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise GraphCheckpointValidationError(
                f"checkpoint manifest 包含重复字段：{key}"
            )
        payload[key] = value
    return payload
