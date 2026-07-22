from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")

import vega.loop_graph_checkpoint as loop_graph_checkpoint
from langgraph.graph import END, START, StateGraph

from vega.loop_graph_checkpoint import (
    GraphCheckpointValidationError,
    capture_checkpoint_data_snapshot,
    capture_checkpoint_store_identity,
    capture_trusted_checkpoint_data_for_resume,
    capture_trusted_checkpoint_state,
    checkpoint_config,
    open_sqlite_checkpointer,
    require_checkpoint_file_continuity,
    require_checkpoint_file_layout_continuity,
    require_checkpoint_store_continuity,
    seal_checkpoint_manifest_for_resume,
    validate_checkpoint_manifest,
    write_checkpoint_manifest,
)
from vega.loop_graph_recovery import (
    read_graph_run_config,
    reconcile_graph_resume,
    write_graph_run_config,
)
from vega.models import LoopAutomationState


class CounterState(TypedDict):
    value: int


def _build_graph(checkpointer):
    builder = StateGraph(CounterState)
    builder.add_node("increment", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    return builder.compile(checkpointer=checkpointer)


def _write_counter_checkpoint(run_dir: Path):
    config = checkpoint_config(run_dir.name)
    with open_sqlite_checkpointer(run_dir) as checkpointer:
        graph = _build_graph(checkpointer)
        graph.invoke({"value": 0}, config)
    return write_checkpoint_manifest(run_dir, config)


def test_sqlite_checkpoint_can_be_reopened_and_resumed(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    config = checkpoint_config(run_dir.name)

    with open_sqlite_checkpointer(run_dir) as checkpointer:
        graph = _build_graph(checkpointer)
        assert graph.invoke({"value": 0}, config) == {"value": 1}
        live_manifest = validate_checkpoint_manifest(run_dir)
        assert live_manifest.checkpoint_count >= 1
        assert live_manifest.latest_checkpoint_id is not None
    write_checkpoint_manifest(run_dir, config)

    manifest = validate_checkpoint_manifest(run_dir)
    assert manifest.run_id == run_dir.name
    assert manifest.thread_id == run_dir.name

    with open_sqlite_checkpointer(run_dir, require_existing=True) as checkpointer:
        graph = _build_graph(checkpointer)
        snapshot = graph.get_state(config)
        assert snapshot.values == {"value": 1}
        assert snapshot.next == ()
        assert graph.invoke(None, config) == {"value": 1}


def test_checkpoint_manifest_detects_database_tampering(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    config = checkpoint_config(run_dir.name)

    with open_sqlite_checkpointer(run_dir) as checkpointer:
        graph = _build_graph(checkpointer)
        graph.invoke({"value": 0}, config)
    manifest = write_checkpoint_manifest(run_dir, config)
    checkpoint_path = run_dir / manifest.checkpoint_ref
    checkpoint_path.write_bytes(checkpoint_path.read_bytes() + b"tamper")

    with pytest.raises(GraphCheckpointValidationError, match="checkpoint hash"):
        validate_checkpoint_manifest(run_dir)


def test_checkpoint_manifest_rejects_constructed_rollback_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _write_counter_checkpoint(run_dir)
    checkpoint_path = run_dir / manifest.checkpoint_ref
    checkpoint_before = checkpoint_path.read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sqlite3, sys\n"
                "connection = sqlite3.connect(sys.argv[1])\n"
                "connection.execute('PRAGMA journal_mode=DELETE')\n"
                "connection.execute('BEGIN IMMEDIATE')\n"
                "connection.execute('PRAGMA user_version=1')\n"
                "assert os.path.exists(sys.argv[1] + '-journal')\n"
                "os._exit(86)\n"
            ),
            str(checkpoint_path),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 86, completed.stderr
    assert checkpoint_path.read_bytes() == checkpoint_before
    assert checkpoint_path.with_name(f"{checkpoint_path.name}-journal").is_file()

    def reject_sqlite_open(*_args, **_kwargs):
        raise AssertionError("未绑定 rollback journal 必须在 SQLite open 前被拒绝")

    monkeypatch.setattr(
        loop_graph_checkpoint.sqlite3,
        "connect",
        reject_sqlite_open,
    )
    with pytest.raises(
        GraphCheckpointValidationError,
        match="SQLite 事务侧文件未被 manifest 绑定",
    ):
        validate_checkpoint_manifest(run_dir)


@pytest.mark.parametrize(
    "sidecar_name",
    [
        "checkpoints.sqlite-journal",
        "CHECKPOINTS.SQLITE-JOURNAL",
        "checkpoints.sqlite-wal",
        "Checkpoints.SQLite-WAL",
        "checkpoints.sqlite-shm",
        "CHECKPOINTS.sqlite-SHM",
        "checkpoints.sqlite-mj01234567",
    ],
)
def test_checkpoint_manifest_rejects_legacy_sqlite_transaction_state(
    tmp_path: Path,
    sidecar_name: str,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    _write_counter_checkpoint(run_dir)
    run_dir.joinpath("graph", sidecar_name).write_bytes(b"legacy sqlite state")

    with pytest.raises(
        GraphCheckpointValidationError,
        match="SQLite 事务侧文件未被 manifest 绑定",
    ):
        validate_checkpoint_manifest(run_dir)


@pytest.mark.parametrize("drift_kind", ["manifest", "database", "sidecar"])
def test_resume_rechecks_checkpoint_before_first_writable_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_kind: str,
) -> None:
    run_dir = tmp_path / "runs" / f"loop-{drift_kind}"
    run_dir.mkdir(parents=True)
    manifest = _write_counter_checkpoint(run_dir)
    checkpoint_path = run_dir / manifest.checkpoint_ref
    manifest_path = run_dir / "graph" / "checkpoint-manifest.json"
    real_connect = loop_graph_checkpoint.sqlite3.connect
    drifted_manifest_bytes: bytes | None = None
    injected = False

    def connect_with_drift(database, *args, **kwargs):
        nonlocal drifted_manifest_bytes, injected
        if not kwargs.get("uri") and not injected:
            injected = True
            if drift_kind == "manifest":
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                payload["created_at"] = "2099-01-01T00:00:00+00:00"
                manifest_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                drifted_manifest_bytes = manifest_path.read_bytes()
            elif drift_kind == "database":
                checkpoint_path.write_bytes(
                    checkpoint_path.read_bytes() + b"resume-race"
                )
            else:
                checkpoint_path.with_name(
                    "Checkpoints.SQLite-WAL"
                ).write_bytes(b"unbound wal")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(
        loop_graph_checkpoint.sqlite3,
        "connect",
        connect_with_drift,
    )

    with pytest.raises(GraphCheckpointValidationError):
        with open_sqlite_checkpointer(run_dir, require_existing=True):
            pytest.fail("恢复漂移必须在返回可写 checkpointer 前失败")

    manifest_after_failure = manifest_path.read_bytes()
    if drift_kind == "manifest":
        assert manifest_after_failure == drifted_manifest_bytes
    else:
        assert json.loads(manifest_after_failure) == manifest.model_dump(mode="json")

    with pytest.raises(
        GraphCheckpointValidationError,
        match="禁止重写 manifest",
    ):
        write_checkpoint_manifest(run_dir, checkpoint_config(run_dir.name))
    assert manifest_path.read_bytes() == manifest_after_failure


def test_resume_rejects_self_consistent_checkpoint_replacement(
    tmp_path: Path,
) -> None:
    run_a = tmp_path / "a" / "same-run"
    run_b = tmp_path / "b" / "same-run"
    run_a.mkdir(parents=True)
    run_b.mkdir(parents=True)
    _write_counter_checkpoint(run_a)
    trusted_a = capture_trusted_checkpoint_state(run_a)
    _write_counter_checkpoint(run_b)

    graph_a = run_a / "graph"
    graph_b = run_b / "graph"
    for path in graph_a.iterdir():
        if path.is_file():
            path.unlink()
    for path in graph_b.iterdir():
        if path.is_file():
            graph_a.joinpath(path.name).write_bytes(path.read_bytes())

    validate_checkpoint_manifest(run_a)
    with pytest.raises(
        GraphCheckpointValidationError,
        match="漂移",
    ):
        with open_sqlite_checkpointer(
            run_a,
            require_existing=True,
            expected_trusted_state=trusted_a,
        ):
            pytest.fail("自洽但不同的 checkpoint 不能替代已预读快照")


@pytest.mark.parametrize("drift_kind", ["database", "sidecar"])
def test_resume_rejects_in_place_checkpoint_drift_after_state_read(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    run_dir = tmp_path / "a" / "same-run"
    replacement_run = tmp_path / "b" / "same-run"
    run_dir.mkdir(parents=True)
    replacement_run.mkdir(parents=True)
    manifest = _write_counter_checkpoint(run_dir)
    _write_counter_checkpoint(replacement_run)
    config = checkpoint_config(run_dir.name)
    trusted_before = capture_trusted_checkpoint_state(run_dir)
    trusted_data = capture_trusted_checkpoint_data_for_resume(
        run_dir,
        trusted_before,
    )
    manifest_path = run_dir / "graph" / "checkpoint-manifest.json"
    manifest_before = manifest_path.read_bytes()

    with open_sqlite_checkpointer(
        run_dir,
        require_existing=True,
        expected_trusted_state=trusted_before,
    ) as checkpointer:
        graph = _build_graph(checkpointer)
        opened_data = capture_checkpoint_data_snapshot(run_dir)
        require_checkpoint_file_layout_continuity(
            run_dir,
            expected=trusted_data,
            observed=opened_data,
        )
        opened_store = capture_checkpoint_store_identity(
            run_dir,
            manifest=trusted_before.manifest,
        )
        require_checkpoint_store_continuity(
            run_dir,
            expected=trusted_before.store,
            observed=opened_store,
        )
        snapshot = graph.get_state(config)
        assert snapshot.values == {"value": 1}
        observed_data = capture_checkpoint_data_snapshot(run_dir)
        require_checkpoint_file_continuity(
            run_dir,
            expected=opened_data,
            observed=observed_data,
        )
        observed_store = capture_checkpoint_store_identity(
            run_dir,
            manifest=trusted_before.manifest,
        )
        require_checkpoint_store_continuity(
            run_dir,
            expected=opened_store,
            observed=observed_store,
        )

    checkpoint_path = run_dir / manifest.checkpoint_ref
    identity_before = checkpoint_path.lstat()
    if drift_kind == "database":
        replacement_path = replacement_run / manifest.checkpoint_ref
        with checkpoint_path.open("r+b") as stream:
            stream.write(replacement_path.read_bytes())
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
    else:
        checkpoint_path.with_name(
            f"{checkpoint_path.name}-wal"
        ).write_bytes(b"in-place sidecar drift")
    identity_after = checkpoint_path.lstat()
    assert identity_after.st_dev == identity_before.st_dev
    assert identity_after.st_ino == identity_before.st_ino

    stable_data = capture_checkpoint_data_snapshot(run_dir)
    with pytest.raises(
        GraphCheckpointValidationError,
        match="主库或 sidecar 在恢复预读后发生漂移",
    ):
        require_checkpoint_file_continuity(
            run_dir,
            expected=observed_data,
            observed=stable_data,
        )

    with pytest.raises(
        GraphCheckpointValidationError,
        match="禁止重写 manifest",
    ):
        seal_checkpoint_manifest_for_resume(
            run_dir,
            config,
            expected_data_snapshot=stable_data,
        )
    assert manifest_path.read_bytes() == manifest_before


def test_checkpoint_manifest_accepts_trusted_database_after_abrupt_exit_without_journal(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    manifest = _write_counter_checkpoint(run_dir)
    checkpoint_path = run_dir / manifest.checkpoint_ref
    checkpoint_before = checkpoint_path.read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sqlite3, sys\n"
                "connection = sqlite3.connect(sys.argv[1])\n"
                "connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone()\n"
                "os._exit(86)\n"
            ),
            str(checkpoint_path),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 86, completed.stderr
    assert not checkpoint_path.with_name(f"{checkpoint_path.name}-journal").exists()
    validated = validate_checkpoint_manifest(run_dir)
    assert validated == manifest
    assert checkpoint_path.read_bytes() == checkpoint_before


def test_checkpoint_path_rejects_reparse_or_link(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    graph_dir = run_dir / "graph"
    try:
        graph_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建目录链接")

    with pytest.raises(GraphCheckpointValidationError, match="链接或 reparse point"):
        with open_sqlite_checkpointer(run_dir):
            pass


def test_graph_run_config_persists_timeout_and_rejects_identity_drift(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)

    config = write_graph_run_config(
        run_dir,
        automation_mode="auto",
        worker_name="worker-fixture",
        reviewer_name="reviewer-fixture",
        verify=True,
        timeout_seconds=37,
    )

    assert read_graph_run_config(run_dir) == config
    assert config.timeout_seconds == 37
    with pytest.raises(ValueError, match="已固定"):
        write_graph_run_config(
            run_dir,
            automation_mode="auto",
            worker_name="worker-fixture",
            reviewer_name="reviewer-fixture",
            verify=True,
            timeout_seconds=38,
        )


def test_second_iteration_recovery_fails_closed_before_reading_old_evidence(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "loop-gate3"
    run_dir.mkdir(parents=True)
    state = LoopAutomationState(
        run_id=run_dir.name,
        task_mode="bug",
        automation_mode="auto",
        engine="langgraph",
        repo_path=str(tmp_path / "repo"),
        input_source="test",
        status="running",
        current_step="worker",
        current_iteration=2,
        max_iterations=2,
    )

    result = reconcile_graph_resume(
        run_dir,
        state=state,
        next_node="execute_worker_epoch",
    )

    assert result.action == "needs_human"
    assert "只注册第一轮 worker" in result.reason
