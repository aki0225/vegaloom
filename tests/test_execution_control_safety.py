from __future__ import annotations

import _thread
import ctypes
import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import vega.execution_control as execution_control
import vega.execution_feedback as execution_feedback
import vega.execution_process as execution_process
import vega.windows_command as windows_command
from vega.codex_mcp_isolation import CodexMcpIsolationError
from vega.execution_control import (
    ExecutionController,
    ExecutionLease,
    OwnedProcessResult,
    RunnerExecutionContext,
    inspect_execution_for_recovery,
    request_stop_for_run,
    run_owned_process,
)
from vega.execution_output import (
    MAX_JSONL_LINE_CHARS,
    ProcessOutputCapture,
    redact_jsonl_output,
)
from vega.execution_process import prepare_subprocess_command
from vega.runner import CodexExecRunner


def _create_directory_link(link_path: Path, target_path: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("当前 Windows 环境不能创建 junction")
        return
    link_path.symlink_to(target_path, target_is_directory=True)


def test_windows_batch_command_uses_comspec_without_changing_exe_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "COMSPEC",
        "C:/Windows/System32/cmd.exe",  # repo-path-policy: allow-test-fixture
    )
    logical = [
        "C:/Program Files/OpenAI/codex.CMD",  # repo-path-policy: allow-test-fixture
        "exec",
        "--model",
        "gpt test",
    ]

    assert prepare_subprocess_command(logical, windows=True) == [
        "C:/Windows/System32/cmd.exe",  # repo-path-policy: allow-test-fixture
        "/d",
        "/v:off",
        "/s",
        "/c",
        subprocess.list2cmdline(logical),
    ]
    executable = [
        "C:/Program Files/OpenAI/codex.exe",  # repo-path-policy: allow-test-fixture
        "exec",
    ]
    assert prepare_subprocess_command(executable, windows=True) is executable


def test_windows_codex_npm_shim_preserves_quoted_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher_dir = tmp_path / "Codex Tools"
    launcher_dir.mkdir()
    launcher = launcher_dir / "codex.CMD"
    launcher.write_text("@echo off\n", encoding="utf-8")
    script = (
        launcher_dir
        / "node_modules"
        / "@openai"
        / "codex"
        / "bin"
        / "codex.js"
    )
    script.parent.mkdir(parents=True)
    script.write_text("// test shim\n", encoding="utf-8")
    node = tmp_path / "Node Tools" / "node.exe"
    node.parent.mkdir()
    node.write_bytes(b"test node")
    monkeypatch.setattr(
        windows_command.shutil,
        "which",
        lambda name: str(node) if name == "node" else None,
    )
    worker_temp = tmp_path / "space and O'Connor" / "worker-temp"
    config = (
        "sandbox_workspace_write.writable_roots="
        f"[{json.dumps(worker_temp.as_posix(), ensure_ascii=True)}]"
    )
    logical = [str(launcher), "exec", "--config", config]

    assert prepare_subprocess_command(logical, windows=True) == [
        str(node),
        str(script),
        "exec",
        "--config",
        config,
    ]


def test_windows_nonstandard_codex_batch_rejects_quoted_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = tmp_path / "codex.CMD"
    launcher.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(windows_command.shutil, "which", lambda _: None)

    with pytest.raises(OSError, match="无法安全传递带双引号"):
        prepare_subprocess_command(
            [
                str(launcher),
                "exec",
                "--config",
                'sandbox_workspace_write.writable_roots=["worker-temp"]',
            ],
            windows=True,
        )


def test_owned_process_uses_compat_command_but_preserves_logical_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logical = [
        "C:/Tools/codex.CMD",  # repo-path-policy: allow-test-fixture
        "exec",
    ]
    invocation = ["cmd.exe", "/d", "/s", "/c", "wrapped-codex"]
    captured: dict[str, object] = {}

    class FinishedProcess:
        pid = 4401
        returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    def prepare(
        command: list[str] | str,
        *,
        windows: bool,
    ) -> list[str] | str:
        assert command == logical
        assert windows is (os.name == "nt")
        return invocation

    def popen(command, *args, **kwargs):
        del args, kwargs
        captured["command"] = command
        return FinishedProcess()

    monkeypatch.setattr(execution_control, "prepare_subprocess_command", prepare)
    monkeypatch.setattr(execution_control.subprocess, "Popen", popen)
    monkeypatch.setattr(
        execution_control,
        "_create_windows_job_for_execution",
        lambda *_: None,
    )
    monkeypatch.setattr(
        execution_control,
        "_add_windows_job_creation_flag",
        lambda options, _job: options,
    )
    monkeypatch.setattr(execution_control, "_activate_windows_job_process", lambda *_: None)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(execution_control, "_get_process_creation_token", lambda _: 1)
    monkeypatch.setattr(
        execution_control,
        "_owned_process_tree_is_active",
        lambda *_args, **_kwargs: False,
    )
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "cmd-wrapper" / "executions" / "worker",
        run_id="cmd-wrapper",
        step="worker",
    )

    result = run_owned_process(logical, "", tmp_path, 5, context)
    lease = ExecutionLease.model_validate_json(
        (context.execution_dir / "execution.json").read_text(encoding="utf-8")
    )

    assert result.status == "success"
    assert captured["command"] == invocation
    assert lease.command == logical


def _open_windows_reader_without_delete_sharing(path: Path) -> tuple[object, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000,
        0x00000001 | 0x00000002,
        None,
        3,
        0x00000080,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel32, int(handle)


def _close_windows_handle(kernel32: object, handle: int) -> None:
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def test_execution_model_temp_path_preserves_windows_path_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution_control.os, "getpid", lambda: 0x7FFFFFFF)
    monkeypatch.setattr(
        execution_control,
        "uuid4",
        lambda: SimpleNamespace(hex="a" * 32),
    )

    initial_path = tmp_path / "r" / "execution.json"
    padding = 250 - len(str(initial_path))
    assert padding >= 0
    execution_dir = tmp_path / ("r" * (padding + 1))
    execution_path = execution_dir / "execution.json"
    temp_path = execution_control._execution_model_temp_path(execution_path)

    assert len(str(execution_path)) == 250
    assert temp_path.parent == execution_path.parent
    assert temp_path.name == ".e.7fffffff.aaaaaaaa"
    assert len(str(temp_path)) < 260


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 存在目标文件共享删除锁")
def test_execution_model_atomic_waits_for_transient_windows_reader_lock(
    tmp_path: Path,
) -> None:
    execution_path = tmp_path / "execution.json"
    old_lease = _execution_lease_for_atomic_publish("old")
    new_lease = _execution_lease_for_atomic_publish("new")
    execution_path.write_text(old_lease.model_dump_json(indent=2), encoding="utf-8")
    kernel32, handle = _open_windows_reader_without_delete_sharing(execution_path)
    released = threading.Event()

    def release_reader() -> None:
        time.sleep(0.6)
        _close_windows_handle(kernel32, handle)
        released.set()

    release_thread = threading.Thread(target=release_reader)
    release_thread.start()
    started = time.monotonic()
    try:
        execution_control._write_model_atomic(execution_path, new_lease)
    finally:
        release_thread.join(2)
        if not released.is_set():
            _close_windows_handle(kernel32, handle)

    elapsed = time.monotonic() - started
    persisted = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    assert 0.5 <= elapsed < 1.5
    assert persisted.execution_id == "new"
    assert list(tmp_path.glob(".e.*")) == []


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 存在目标文件共享删除锁")
def test_execution_model_atomic_fails_closed_for_persistent_windows_reader_lock(
    tmp_path: Path,
) -> None:
    execution_path = tmp_path / "execution.json"
    old_lease = _execution_lease_for_atomic_publish("old")
    new_lease = _execution_lease_for_atomic_publish("new")
    execution_path.write_text(old_lease.model_dump_json(indent=2), encoding="utf-8")
    kernel32, handle = _open_windows_reader_without_delete_sharing(execution_path)
    started = time.monotonic()

    try:
        with pytest.raises(PermissionError, match="等待约 1 秒"):
            execution_control._write_model_atomic(execution_path, new_lease)
    finally:
        _close_windows_handle(kernel32, handle)

    elapsed = time.monotonic() - started
    persisted = ExecutionLease.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    assert 0.9 <= elapsed < 1.5
    assert persisted.execution_id == "old"


def test_execution_prepare_rejects_linked_descendant_before_launch(tmp_path: Path) -> None:
    trusted_root = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted_root.mkdir()
    outside.mkdir()
    _create_directory_link(trusted_root / "executions", outside)
    marker = tmp_path / "child-started.txt"
    context = RunnerExecutionContext(
        execution_root=trusted_root,
        execution_dir=trusted_root / "executions" / "worker",
        run_id="linked-execution",
        step="worker",
    )

    with pytest.raises(OSError, match="符号链接、junction 或 reparse point"):
        run_owned_process(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            "",
            tmp_path,
            5,
            context,
        )

    assert not marker.exists()
    assert not outside.joinpath("worker").exists()


def test_execution_records_one_hour_deadline_without_waiting(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "run"
    execution_root.mkdir()
    controller = ExecutionController(
        RunnerExecutionContext(
            execution_root=execution_root,
            execution_dir=execution_root / "executions" / "worker",
            run_id="one-hour-runner",
            step="worker",
        )
    )

    lease = controller.prepare(["runner"], 3600)

    started_at = datetime.fromisoformat(lease.started_at)
    deadline = datetime.fromisoformat(lease.deadline)
    assert (deadline - started_at).total_seconds() == 3600


def test_execution_prepare_preserves_explicit_operation_identity(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "run"
    execution_root.mkdir()
    context = RunnerExecutionContext(
        execution_root=execution_root,
        execution_dir=execution_root / "executions" / "worker",
        run_id="explicit-identity",
        step="worker",
        execution_id="operation-explicit-01",
    )

    lease = ExecutionController(context).prepare(["runner"], 60)
    persisted = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )

    assert lease.execution_id == "operation-explicit-01"
    assert persisted.execution_id == "operation-explicit-01"


@pytest.mark.parametrize(
    "execution_id",
    ["", " leading", "trailing ", "line\nbreak", "nul\0byte", "x" * 129],
)
def test_execution_context_rejects_invalid_explicit_identity(
    tmp_path: Path,
    execution_id: str,
) -> None:
    with pytest.raises(ValueError, match="execution_id"):
        RunnerExecutionContext(
            execution_root=tmp_path,
            execution_dir=tmp_path / "executions" / "worker",
            run_id="invalid-explicit-identity",
            step="worker",
            execution_id=execution_id,
        )


@pytest.mark.parametrize("operation", ["heartbeat", "output", "stderr"])
def test_execution_revalidates_directory_before_later_writes(
    tmp_path: Path,
    operation: str,
) -> None:
    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    execution_dir = trusted_root / "executions" / "worker"
    context = RunnerExecutionContext(
        execution_root=trusted_root,
        execution_dir=execution_dir,
        run_id="redirected-execution",
        step="worker",
    )
    controller = ExecutionController(context)
    controller.prepare(["runner"], 5)
    preserved = trusted_root / "preserved-execution"
    execution_dir.rename(preserved)
    outside = tmp_path / "outside"
    outside.mkdir()
    _create_directory_link(execution_dir, outside)

    with pytest.raises(OSError, match="符号链接、junction 或 reparse point"):
        if operation == "heartbeat":
            controller.heartbeat()
        elif operation == "output":
            controller.persist_output(io.BytesIO(b"must stay inside the trusted root"))
        else:
            controller.persist_stderr(io.BytesIO(b"must stay inside the trusted root"))

    assert preserved.joinpath("execution.json").is_file()
    assert list(outside.iterdir()) == []


def test_execution_model_temp_paths_are_unique_within_one_process(
    tmp_path: Path,
) -> None:
    execution_dir = tmp_path / "executions" / "worker"

    execution_temp = execution_control._execution_model_temp_path(
        execution_dir / "execution.json"
    )
    stop_temp = execution_control._execution_model_temp_path(
        execution_dir / "stop-request.json"
    )

    assert execution_temp.parent == stop_temp.parent
    assert execution_temp != stop_temp


def test_codex_exec_runner_propagates_termination_unconfirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "vega.runner.run_owned_process",
        lambda *args, **kwargs: OwnedProcessResult(
            status="error",
            output="partial output",
            error="owned process tree 终止未确认",
            returncode=None,
            termination_unconfirmed=True,
        ),
    )

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
    )

    assert result.status == "error"
    assert result.termination_unconfirmed is True


def test_codex_exec_runner_records_terminal_preflight_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "missing-codex" / "executions" / "worker",
        run_id="missing-codex",
        step="worker",
        execution_id="a" * 32,
    )
    monkeypatch.setattr("vega.runner.shutil.which", lambda _: None)

    result = CodexExecRunner(executable="missing-codex").run(
        "test prompt",
        repo,
        sandbox="workspace-write",
        timeout_seconds=60,
        execution_context=context,
    )

    lease = ExecutionLease.model_validate_json(
        (context.execution_dir / "execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "error"
    assert lease.execution_id == context.execution_id
    assert lease.status == "failed"
    assert lease.child_pid is None


def test_codex_exec_runner_writes_output_schema_inside_execution_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "review" / "executions" / "reviewer",
        run_id="review",
        step="reviewer",
    )
    output_schema = {
        "type": "object",
        "properties": {"verdict": {"type": "string"}},
        "required": ["verdict"],
        "additionalProperties": False,
    }
    captured: dict[str, object] = {}

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, stream_context, **kwargs
    ):
        del input_text, cwd, timeout_seconds
        schema_index = command.index("--output-schema") + 1
        schema_path = Path(command[schema_index])
        captured["schema_path"] = schema_path
        captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        assert stream_context.output_line_observer is not None
        payload = {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": '{"verdict":"approve"}',
            },
        }
        return OwnedProcessResult(
            status="success",
            output=json.dumps(payload),
            error=None,
            returncode=0,
        )

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)

    result = CodexExecRunner(output_schema=output_schema).run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
        execution_context=context,
    )

    assert result.status == "success"
    assert captured["schema"] == output_schema
    assert captured["schema_path"] == context.execution_dir / "output-schema.json"


def test_codex_exec_runner_single_writer_disables_target_multi_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "writer" / "executions" / "worker",
        run_id="writer",
        step="worker",
    )
    captured: dict[str, object] = {}

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, stream_context, **kwargs
    ):
        del input_text, cwd, timeout_seconds, stream_context
        captured["command"] = command
        captured["environment"] = kwargs["environment"]
        payload = {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": '{"status":"ok"}',
            },
        }
        return OwnedProcessResult(
            status="success",
            output=json.dumps(payload),
            error=None,
            returncode=0,
        )

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)
    monkeypatch.setattr(
        "vega.runner.build_mcp_disable_overrides",
        lambda *args, **kwargs: (
            "mcp_servers.safe-server.enabled=false",
            "mcp_servers.already_disabled.enabled=false",
        ),
    )

    result = CodexExecRunner(single_writer=True).run(
        "test prompt",
        repo,
        sandbox="workspace-write",
        timeout_seconds=5,
        execution_context=context,
    )

    assert result.status == "success"
    command = captured["command"]
    assert isinstance(command, list)
    disabled = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--disable"
    ]
    assert disabled == [
        "hooks",
        "memories",
        "plugins",
        "multi_agent",
        "multi_agent_v2",
    ]
    config_values = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--config"
    ]
    assert "sandbox_workspace_write.network_access=false" in config_values
    worker_temp = context.execution_dir / "worker-temp"
    assert (
        "sandbox_workspace_write.writable_roots="
        f"[{json.dumps(worker_temp.as_posix(), ensure_ascii=True)}]"
        in config_values
    )
    assert "mcp_servers.safe-server.enabled=false" in config_values
    assert "mcp_servers.already_disabled.enabled=false" in config_values
    assert worker_temp.is_dir()
    assert captured["environment"] == {
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(worker_temp),
        "TMP": str(worker_temp),
        "TMPDIR": str(worker_temp),
    }


def test_codex_exec_runner_single_writer_rejects_invalid_temp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "writer" / "executions" / "worker",
        run_id="writer",
        step="worker",
    )
    context.execution_dir.mkdir(parents=True)
    (context.execution_dir / "worker-temp").write_text(
        "not a directory",
        encoding="utf-8",
    )

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "vega.runner.build_mcp_disable_overrides",
        lambda *args, **kwargs: (),
    )

    def unexpected_process_start(*args, **kwargs):
        del args, kwargs
        raise AssertionError("临时目录不可信时不得启动 Codex Worker")

    monkeypatch.setattr(
        "vega.runner.run_owned_process",
        unexpected_process_start,
    )

    result = CodexExecRunner(single_writer=True).run(
        "test prompt",
        repo,
        sandbox="workspace-write",
        timeout_seconds=5,
        execution_context=context,
    )

    lease = ExecutionLease.model_validate_json(
        (context.execution_dir / "execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "error"
    assert result.error == "无法准备 Worker 隔离临时目录：OSError"
    assert lease.status == "failed"
    assert lease.child_pid is None


def test_codex_exec_runner_single_writer_fails_closed_when_mcp_isolation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = "fake-mcp-password"

    def fail_isolation(*args, **kwargs):
        del args, kwargs
        raise CodexMcpIsolationError(
            "无法读取 Codex MCP 配置，拒绝启动 Supervisor Worker。"
        )

    def unexpected_process_start(*args, **kwargs):
        del args, kwargs
        raise AssertionError("隔离检查失败后不得启动 Codex Worker")

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "vega.runner.build_mcp_disable_overrides",
        fail_isolation,
    )
    monkeypatch.setattr(
        "vega.runner.run_owned_process",
        unexpected_process_start,
    )

    result = CodexExecRunner(single_writer=True).run(
        f"不得泄露 {secret}",
        repo,
        sandbox="workspace-write",
        timeout_seconds=5,
    )

    assert result.status == "error"
    assert "拒绝启动 Supervisor Worker" in (result.error or "")
    assert secret not in (result.error or "")


def test_codex_exec_runner_can_isolate_reviewer_mcp_without_writer_restrictions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    captured: dict[str, list[str]] = {}

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, stream_context, **kwargs
    ):
        del input_text, cwd, timeout_seconds, stream_context, kwargs
        captured["command"] = command
        payload = {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": '{"verdict":"approve"}',
            },
        }
        return OwnedProcessResult(
            status="success",
            output=json.dumps(payload),
            error=None,
            returncode=0,
        )

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)
    monkeypatch.setattr(
        "vega.runner.build_mcp_disable_overrides",
        lambda *args, **kwargs: ("mcp_servers.safe-review.enabled=false",),
    )

    result = CodexExecRunner(isolate_mcp=True).run(
        "review prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
    )

    assert result.status == "success"
    command = captured["command"]
    disabled = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--disable"
    ]
    assert disabled == ["hooks", "memories", "plugins"]
    config_values = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--config"
    ]
    assert "mcp_servers.safe-review.enabled=false" in config_values
    assert "sandbox_workspace_write.network_access=false" not in config_values


def test_codex_exec_runner_emits_only_sanitized_jsonl_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_secret = "sk-jsonl-fake-secret-123456"
    events: list[tuple[str, int]] = []
    captured_command: list[str] = []
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "jsonl" / "executions" / "worker",
        run_id="jsonl",
        step="worker",
        progress_reporter=lambda step, elapsed: events.append((step, elapsed)),
    )

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, stream_context, **kwargs
    ):
        del input_text, cwd, timeout_seconds
        captured_command.extend(command)
        assert kwargs["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        assert stream_context.capture_stderr_separately is True
        observer = stream_context.output_line_observer
        assert observer is not None
        observer("not-json")
        payloads = [
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "reasoning", "text": fake_secret},
            },
            {
                "type": "item.started",
                "item": {
                    "type": "command_execution",
                    "command": f"tool --token {fake_secret}",
                    "status": "in_progress",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "aggregated_output": fake_secret,
                    "status": "completed",
                    "exit_code": 0,
                },
            },
            {
                "type": "item.updated",
                "item": {
                    "type": "todo_list",
                    "items": [{"text": fake_secret, "completed": False}],
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "changes": [{"path": fake_secret, "kind": "update"}],
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": f"READY {fake_secret}"},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 10}},
        ]
        for payload in payloads:
            observer(json.dumps(payload))
        return OwnedProcessResult(
            status="success",
            output="\n".join(json.dumps(payload) for payload in payloads),
            error=None,
            returncode=0,
        )

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="workspace-write",
        timeout_seconds=5,
        execution_context=context,
    )

    assert result.status == "success"
    assert result.output.startswith("READY ")
    assert fake_secret not in result.output
    assert "--json" in captured_command
    assert [step for step, _ in events] == [
        "worker.turn_started",
        "worker.command_started",
        "worker.command_completed",
        "worker.plan_updated",
        "worker.file_changed",
        "worker.turn_completed",
    ]
    assert fake_secret not in json.dumps(events)


@pytest.mark.parametrize(
    "lines",
    [
        [],
        ["not-json"],
        [json.dumps({"type": "future.event"})],
        [
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ],
    ],
    ids=["empty", "malformed", "unknown-event", "known-events"],
)
def test_codex_exec_runner_rejects_success_without_final_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lines: list[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, context, **kwargs
    ):
        del command, input_text, cwd, timeout_seconds
        assert kwargs["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        observer = context.output_line_observer
        assert observer is not None
        for line in lines:
            observer(line)
        return OwnedProcessResult("success", "\n".join(lines), None, 0)

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
    )

    assert result.status == "error"
    assert result.error == "codex exec JSONL 未包含最终 agent_message。"


def test_codex_exec_runner_keeps_parsing_after_malformed_event_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    events: list[tuple[str, int]] = []
    lines = [
        json.dumps({"type": []}),
        json.dumps({"type": "item.completed", "item": {"type": []}}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "status": []},
            }
        ),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "first"},
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "last"},
            }
        ),
    ]

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, context, **kwargs
    ):
        del command, input_text, cwd, timeout_seconds
        assert kwargs["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        observer = context.output_line_observer
        assert observer is not None
        for line in lines:
            observer(line)
        return OwnedProcessResult("success", "\n".join(lines), None, 0)

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
        execution_context=RunnerExecutionContext(
            execution_root=tmp_path,
            execution_dir=tmp_path / "runs" / "malformed" / "executions" / "worker",
            run_id="malformed",
            step="worker",
            progress_reporter=lambda step, elapsed: events.append((step, elapsed)),
        ),
    )

    assert result.status == "success"
    assert result.output == "last"
    assert ("worker.turn_started", 0) in events


def test_codex_exec_runner_scans_mixed_jsonl_line_endings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}
    )
    last = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "last"}}
    )
    output = f"{first}\r\n\n{last}"

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, context, **kwargs
    ):
        del command, input_text, cwd, timeout_seconds, context
        assert kwargs["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        return OwnedProcessResult("success", output, None, 0)

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
    )

    assert result.status == "success"
    assert result.output == "last"


@pytest.mark.parametrize("include_small_message", [False, True])
def test_codex_exec_runner_fails_closed_when_final_jsonl_line_is_oversized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_small_message: bool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    oversized_line = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "x" * (4 * 1024 * 1024),
            },
        }
    )
    lines = (
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "first"},
                }
            )
        ]
        if include_small_message
        else []
    )
    lines.append(oversized_line)

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, context, **kwargs
    ):
        del command, input_text, cwd, timeout_seconds
        assert kwargs["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        observer = context.output_line_observer
        assert observer is not None
        for line in lines:
            observer(line)
        return OwnedProcessResult("success", "\n".join(lines), None, 0)

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
    )

    assert result.status == "error"
    assert result.output == ""
    assert "超出终态扫描上限" in (result.error or "")


def test_output_dispatcher_does_not_block_control_and_close_stops_queueing() -> None:
    entered = threading.Event()
    release = threading.Event()
    observed: list[str] = []

    def blocked_observer(line: str) -> None:
        observed.append(line)
        entered.set()
        release.wait(10)

    output = io.BytesIO()
    capture = ProcessOutputCapture(output, blocked_observer)
    capture.start(SimpleNamespace(stdout=io.BytesIO(b"line-1\nline-2\n")))
    assert entered.wait(1)

    started = time.monotonic()
    assert capture.poll(max_lines=1, budget_seconds=1) == 0
    assert time.monotonic() - started < 0.1

    started = time.monotonic()
    capture.finish(0.01)
    assert time.monotonic() - started < 0.5
    assert output.getvalue() == b"line-1\nline-2\n"
    assert observed == ["line-1"]

    release.set()

    broken = ProcessOutputCapture(io.BytesIO(), lambda _: None)
    broken._disable_observer()
    broken._lines.put_nowait("queued-before-close")
    broken._read_stream(io.BytesIO(b"new-1\nnew-2\nnew-3\n"))
    assert broken._lines.qsize() == 1
    broken.finish(0)
    assert broken._lines.qsize() == 1


def test_output_reader_natural_drain_finishes_before_forced_close() -> None:
    class NaturalDrainStream:
        def __init__(self, chunks: list[bytes]) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.done_reading = threading.Event()
            self.forced_close = False
            self._chunks = iter(chunks)

        def read1(self, _: int) -> bytes:
            self.started.set()
            self.release.wait(1)
            try:
                return next(self._chunks)
            except StopIteration:
                self.done_reading.set()
                return b""

        def close(self) -> None:
            if not self.done_reading.is_set():
                self.forced_close = True

    chunks = [b"line-1\n", b"line-2\n", b"line-3\n"]
    stream = NaturalDrainStream(chunks)
    output = io.BytesIO()
    capture = ProcessOutputCapture(output, lambda _: None)
    capture.start(SimpleNamespace(stdout=stream))
    assert stream.started.wait(1)
    stream.release.set()

    capture.finish(1)

    assert not stream.forced_close
    assert output.getvalue() == b"".join(chunks)


def test_output_reader_freezes_partial_sink_after_unwakeable_shutdown() -> None:
    class UnwakeableStream:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.closed = threading.Event()
            self._sent = False

        def read1(self, _: int) -> bytes:
            self.started.set()
            self.release.wait(10)
            if self._sent:
                return b""
            self._sent = True
            return b"late-output\n"

        def close(self) -> None:
            self.closed.set()

    stream = UnwakeableStream()
    output = io.BytesIO()
    capture = ProcessOutputCapture(output, lambda _: None)
    capture.start(SimpleNamespace(stdout=stream))
    assert stream.started.wait(1)

    with pytest.raises(OSError, match="输出读取线程关闭超时"):
        capture.finish(0.01)
    partial = output.getvalue()

    stream.release.set()
    assert capture._reader is not None
    capture._reader.join(1)

    assert stream.closed.is_set()
    assert output.getvalue() == partial


def test_output_reader_runtime_error_is_recorded_and_fails_closed() -> None:
    class ErrorStream:
        def read1(self, _: int) -> bytes:
            raise RuntimeError("simulated reader failure")

        def close(self) -> None:
            return

    capture = ProcessOutputCapture(io.BytesIO(), lambda _: None)
    capture.start(SimpleNamespace(stdout=ErrorStream()))

    with pytest.raises(OSError, match="输出读取失败"):
        capture.finish(1)


def test_dual_output_reader_shutdown_is_bounded_without_cross_thread_close() -> None:
    class BlockingStream:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.closed = threading.Event()
            self._released = threading.Event()

        def read1(self, _: int) -> bytes:
            self.started.set()
            self._released.wait(10)
            return b""

        def close(self) -> None:
            self.closed.set()
            self._released.set()

    stdout_stream = BlockingStream()
    stderr_stream = BlockingStream()
    capture = ProcessOutputCapture(
        io.BytesIO(),
        lambda _: None,
        io.BytesIO(),
        separate_stderr=True,
    )
    capture.start(SimpleNamespace(stdout=stdout_stream, stderr=stderr_stream))
    assert stdout_stream.started.wait(1)
    assert stderr_stream.started.wait(1)

    started = time.monotonic()
    with pytest.raises(OSError, match="runner 输出读取线程关闭超时.*runner stderr"):
        capture.finish(0.1)
    assert time.monotonic() - started < 0.3
    assert not stdout_stream.closed.is_set()
    assert not stderr_stream.closed.is_set()

    stdout_stream._released.set()
    stderr_stream._released.set()
    assert capture._reader is not None
    assert capture._stderr_reader is not None
    capture._reader.join(1)
    capture._stderr_reader.join(1)
    assert stdout_stream.closed.is_set()
    assert stderr_stream.closed.is_set()
    assert not capture._reader.is_alive()
    assert not capture._stderr_reader.is_alive()


def test_output_reader_drops_realtime_lines_without_blocking_when_queue_full() -> None:
    output = io.BytesIO()
    capture = ProcessOutputCapture(output, lambda _: None)
    for index in range(capture._lines.maxsize):
        capture._lines.put_nowait(f"queued-{index}")
    payload = b"".join(f"line-{index}\n".encode() for index in range(400))

    reader = threading.Thread(target=capture._read_stream, args=(io.BytesIO(payload),))
    reader.start()
    reader.join(1)

    assert not reader.is_alive()
    assert output.getvalue() == payload
    assert capture._lines.qsize() == capture._lines.maxsize


def test_high_frequency_slow_observer_does_not_delay_timeout_or_output_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = "".join(f"noise-{index}\n" for index in range(600))

    def slow_observer(_: str) -> None:
        time.sleep(0.01)

    class FakeProcess:
        pid = 4242

        def __init__(self) -> None:
            self.stdout = io.BytesIO(lines.encode())
            self.returncode: int | None = None
            self.alive = True

        def poll(self) -> int | None:
            return None if self.alive else self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.alive = False
            self.returncode = 1
            return self.returncode

    process = FakeProcess()

    def fake_terminate(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        process.alive = False
        process.returncode = -15
        return SimpleNamespace(succeeded=True, detail="fake process stopped")

    monkeypatch.setattr(execution_control.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(execution_control, "_create_windows_job_for_execution", lambda *_: None)
    monkeypatch.setattr(
        execution_control,
        "_add_windows_job_creation_flag",
        lambda options, _job: options,
    )
    monkeypatch.setattr(execution_control, "_activate_windows_job_process", lambda *_: None)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(execution_control, "_get_process_creation_token", lambda _: 1)
    monkeypatch.setattr(
        execution_control,
        "_owned_process_tree_is_active",
        lambda current_process, *_args, **_kwargs: current_process.alive,
    )
    monkeypatch.setattr(execution_control, "_terminate_owned_process", fake_terminate)

    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "slow-observer" / "executions" / "worker",
        run_id="slow-observer",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
        output_line_observer=slow_observer,
    )
    script = f"import sys,time; sys.stdout.write({lines!r}); sys.stdout.flush(); time.sleep(5)"

    started = time.monotonic()
    result = run_owned_process(
        [sys.executable, "-c", script],
        "",
        tmp_path,
        0.1,
        context,
    )
    elapsed = time.monotonic() - started
    persisted = context.execution_dir.joinpath("process-output.txt").read_text(encoding="utf-8")

    assert result.status == "timed_out"
    assert elapsed < 1
    assert persisted == result.output
    assert persisted.count("noise-") == 600


def test_run_owned_process_persists_stable_partial_output_after_reader_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorAfterDataStream:
        def __init__(self) -> None:
            self._returned_data = False

        def read1(self, _: int) -> bytes:
            if not self._returned_data:
                self._returned_data = True
                return b"partial-output\n"
            raise RuntimeError("simulated reader failure")

        def close(self) -> None:
            return

    class FinishedProcess:
        pid = 4343
        returncode = 0
        stdout = ErrorAfterDataStream()

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    process = FinishedProcess()
    monkeypatch.setattr(execution_control.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(execution_control, "_create_windows_job_for_execution", lambda *_: None)
    monkeypatch.setattr(
        execution_control,
        "_add_windows_job_creation_flag",
        lambda options, _job: options,
    )
    monkeypatch.setattr(execution_control, "_activate_windows_job_process", lambda *_: None)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(execution_control, "_get_process_creation_token", lambda _: 1)
    monkeypatch.setattr(
        execution_control,
        "_owned_process_tree_is_active",
        lambda *_args, **_kwargs: False,
    )

    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "partial-output" / "executions" / "worker",
        run_id="partial-output",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
        output_line_observer=lambda _: None,
    )

    result = run_owned_process(["fake-runner"], "", tmp_path, 5, context)
    persisted = context.execution_dir.joinpath("process-output.txt").read_text(encoding="utf-8")

    assert result.status == "error"
    assert "输出读取失败" in (result.error or "")
    assert persisted == "partial-output\n"
    assert result.output == persisted


def test_large_stdin_does_not_delay_owned_process_timeout(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "stdin-timeout" / "executions" / "worker",
        run_id="stdin-timeout",
        step="worker",
        iteration=1,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    large_input = "x" * (16 * 1024 * 1024)

    started = time.monotonic()
    result = run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(8)"],
        large_input,
        tmp_path,
        1,
        context,
    )
    elapsed = time.monotonic() - started

    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "timed_out"
    assert lease.status == "timed_out"
    assert elapsed < 5
    assert all(thread.name != "vega-stdin-writer" for thread in threading.enumerate())


def test_owned_process_redacts_output_before_persisting_and_returning(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "redacted-output" / "executions" / "worker",
        run_id="redacted-output",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )
    fake_secret = "sk-runner-fake-secret-123456"

    result = run_owned_process(
        [
            sys.executable,
            "-c",
            f"print('api_key={fake_secret}')",
            f"--api-key={fake_secret}",
        ],
        "prompt should only go to stdin",
        tmp_path,
        5,
        context,
    )
    persisted_output = context.execution_dir.joinpath("process-output.txt").read_text(
        encoding="utf-8"
    )
    execution_payload = context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")

    assert result.status == "success"
    assert result.output == persisted_output
    assert fake_secret not in result.output
    assert fake_secret not in persisted_output
    assert fake_secret not in execution_payload
    assert "prompt should only go to stdin" not in execution_payload


def test_owned_process_separates_and_redacts_stderr_for_jsonl_runner(tmp_path: Path) -> None:
    observed_ready = threading.Event()
    observed: list[str] = []

    def observe(line: str) -> None:
        observed.append(line)
        observed_ready.set()

    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "separate-stderr" / "executions" / "worker",
        run_id="separate-stderr",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        output_line_observer=observe,
        capture_stderr_separately=True,
    )
    fake_secret = "sk-stderr-fake-secret-123456"
    jsonl_line = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": f"ready api_key={fake_secret}",
            },
        }
    )
    fake_path = tmp_path / "private-target"
    diagnostic = (
        "2026-08-03T00:00:00Z ERROR codex_core::tools::router: "
        f'command="git status" path="{fake_path}" '
        f'output="model text" api_key={fake_secret}'
    )
    child_code = (
        "import sys,time; "
        f"print({jsonl_line!r}, flush=True); "
        f"print({diagnostic!r}, file=sys.stderr, flush=True); "
        "print('fatal: raw command output', file=sys.stderr, flush=True); "
        "sys.stderr.write('x' * (256 * 1024)); "
        "sys.stderr.flush(); "
        "time.sleep(0.3)"
    )

    result = run_owned_process(
        [sys.executable, "-c", child_code],
        "",
        tmp_path,
        5,
        context,
    )

    persisted_output = context.execution_dir.joinpath("process-output.txt").read_text(
        encoding="utf-8"
    )
    persisted_stderr = context.execution_dir.joinpath("process-stderr.txt").read_text(
        encoding="utf-8"
    )

    assert result.status == "success"
    assert observed_ready.is_set()
    assert observed == [jsonl_line]
    persisted_payloads = [json.loads(line) for line in persisted_output.splitlines()]
    assert result.output == persisted_output
    assert persisted_payloads[0]["item"]["text"] == "ready api_key=[REDACTED]"
    assert fake_secret not in persisted_output
    assert (
        "2026-08-03T00:00:00Z ERROR "
        "codex_core::tools::router: [DIAGNOSTIC_REDACTED]"
    ) in persisted_stderr
    assert persisted_stderr.count("[DIAGNOSTIC_REDACTED]") == 3
    assert fake_secret not in persisted_stderr
    assert str(fake_path) not in persisted_stderr
    assert "git status" not in persisted_stderr
    assert "model text" not in persisted_stderr
    assert "raw command output" not in persisted_stderr
    assert "x" * 1024 not in persisted_stderr


def test_owned_process_keeps_default_stderr_merged_with_stdout(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "merged-stderr" / "executions" / "verification",
        run_id="merged-stderr",
        step="verification",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )
    child_code = (
        "import sys; "
        "print('stdout-line', flush=True); "
        "print('stderr-line', file=sys.stderr, flush=True)"
    )

    result = run_owned_process(
        [sys.executable, "-c", child_code],
        "",
        tmp_path,
        5,
        context,
    )

    persisted_output = context.execution_dir.joinpath("process-output.txt").read_text(
        encoding="utf-8"
    )

    assert result.status == "success"
    assert result.output == persisted_output
    assert "stdout-line" in persisted_output
    assert "stderr-line" in persisted_output
    assert not context.execution_dir.joinpath("process-stderr.txt").exists()


def test_owned_process_keeps_exit_error_when_stderr_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "stderr-persist-failure" / "executions" / "worker",
        run_id="stderr-persist-failure",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        capture_stderr_separately=True,
    )

    def fail_stderr_persistence(self, stderr_file):
        del self, stderr_file
        raise RuntimeError("simulated stderr persistence failure")

    monkeypatch.setattr(ExecutionController, "persist_stderr", fail_stderr_persistence)

    result = run_owned_process(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        "",
        tmp_path,
        5,
        context,
    )

    assert result.status == "error"
    assert "外部 runner 退出码：3" in (result.error or "")
    assert "runner stderr 持久化失败" in (result.error or "")


def test_jsonl_redaction_replaces_invalid_lines_without_leaking_raw_text() -> None:
    fake_secret = "sk-jsonl-invalid-secret-123456"
    valid_line = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": f"Authorization: Bearer {fake_secret}",
            },
        }
    )

    deeply_nested_json = "[" * 5000 + "0" + "]" * 5000
    oversized_json = "x" * (MAX_JSONL_LINE_CHARS + 1)
    redacted = redact_jsonl_output(
        f"not-json {fake_secret}\n{deeply_nested_json}\n{oversized_json}\n{valid_line}\n"
    )
    payloads = [json.loads(line) for line in redacted.splitlines()]

    assert payloads[0] == {"type": "vega.invalid_jsonl"}
    # 不同 CPython 构建可能在 JSON 解析或递归脱敏阶段先触及递归上限；
    # 两种 sentinel 都表示该物理行已被安全替换并会让 Runner fail-closed。
    assert payloads[1] in (
        {"type": "vega.invalid_jsonl"},
        {"type": "vega.redaction_failed"},
    )
    assert payloads[2] == {"type": "vega.oversized_jsonl"}
    assert payloads[3]["item"]["text"] == "Authorization: Bearer [REDACTED]"
    assert fake_secret not in redacted


def test_codex_exec_runner_fails_closed_on_jsonl_sanitization_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = [
        json.dumps({"type": "vega.invalid_jsonl"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "must not pass"},
            }
        ),
    ]

    monkeypatch.setattr("vega.runner.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr(
        "vega.runner.run_owned_process",
        lambda *args, **kwargs: OwnedProcessResult(
            status="success",
            output="\n".join(lines),
            error=None,
            returncode=0,
        ),
    )

    result = CodexExecRunner().run(
        "test prompt",
        repo,
        sandbox="read-only",
        timeout_seconds=5,
    )

    assert result.status == "error"
    assert result.output == ""
    assert "包含无效或无法安全脱敏的行" in (result.error or "")


def test_owned_process_reports_bounded_progress_without_persisting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(execution_feedback, "PROGRESS_INTERVAL_SECONDS", 0.03)
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "progress" / "executions" / "worker",
        run_id="progress",
        step="worker",
        heartbeat_interval_seconds=0.01,
        lease_timeout_seconds=2.0,
        progress_reporter=lambda step, elapsed: events.append((step, elapsed)),
    )

    result = run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        "",
        tmp_path,
        5,
        context,
    )

    execution_payload = context.execution_dir.joinpath("execution.json").read_text(
        encoding="utf-8"
    )
    assert result.status == "success"
    assert events[0] == ("worker", 0)
    assert len(events) >= 2
    assert all(step == "worker" and elapsed >= 0 for step, elapsed in events)
    assert "progress_reporter" not in execution_payload

    def broken_reporter(step: str, elapsed: int) -> None:
        raise RuntimeError(f"progress failed: {step}/{elapsed}")

    broken_context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "broken-progress" / "executions" / "reviewer",
        run_id="broken-progress",
        step="reviewer",
        heartbeat_interval_seconds=0.01,
        lease_timeout_seconds=2.0,
        progress_reporter=broken_reporter,
    )
    broken_result = run_owned_process(
        [sys.executable, "-c", "print('review completed')"],
        "",
        tmp_path,
        5,
        broken_context,
    )
    broken_lease = ExecutionLease.model_validate_json(
        broken_context.execution_dir.joinpath("execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert broken_result.status == "success"
    assert "review completed" in broken_result.output
    assert broken_lease.status == "completed"


def test_owned_process_observes_complete_lines_before_child_exit(tmp_path: Path) -> None:
    fake_secret = "sk-stream-fake-secret-123456"
    first_line = threading.Event()
    second_line = threading.Event()
    observed: list[str] = []
    result_holder: dict[str, OwnedProcessResult] = {}

    def observe(line: str) -> None:
        observed.append(line)
        if line == '{"type":"turn.started"}':
            first_line.set()
        elif line == f"api_key={fake_secret}":
            second_line.set()

    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "stream" / "executions" / "worker",
        run_id="stream",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        output_line_observer=observe,
    )
    child_code = (
        "import time; "
        "print('{\"type\":\"turn.started\"}', flush=True); "
        "time.sleep(0.6); "
        f"print('api_key={fake_secret}', flush=True)"
    )

    thread = threading.Thread(
        target=lambda: result_holder.setdefault(
            "result",
            run_owned_process(
                [sys.executable, "-c", child_code],
                "",
                tmp_path,
                5,
                context,
            ),
        )
    )
    thread.start()
    assert first_line.wait(3)
    assert thread.is_alive()
    assert second_line.wait(3)
    thread.join(5)

    assert not thread.is_alive()
    assert result_holder["result"].status == "success"
    assert observed == ['{"type":"turn.started"}', f"api_key={fake_secret}"]
    persisted = context.execution_dir.joinpath("process-output.txt").read_text(encoding="utf-8")
    assert fake_secret not in persisted
    assert all(item.name != "vega-output-reader" for item in threading.enumerate())


def test_output_reader_start_failure_terminates_owned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "reader-start" / "executions" / "worker",
        run_id="reader-start",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
        output_line_observer=lambda _: None,
    )
    original_start = threading.Thread.start

    def fail_output_reader_start(thread: threading.Thread) -> None:
        if thread.name == "vega-output-reader":
            raise RuntimeError("simulated thread start failure")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_output_reader_start)

    result = run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        "",
        tmp_path,
        5,
        context,
    )

    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "error"
    assert "输出读取线程启动失败" in (result.error or "")
    assert lease.status == "failed"
    assert lease.child_pid is not None
    assert not execution_control.is_process_alive(lease.child_pid)
    assert context.execution_dir.joinpath("process-output.txt").is_file()


def test_owned_process_tree_stays_active_for_posix_descendants() -> None:
    process = _FakeOwnedProcess(pid=5151, wait_times_out=False)
    process.returncode = 0

    def process_group_alive(process_group_id: int) -> bool:
        assert process.poll_calls == 1
        return process_group_id == process.pid

    assert execution_process.owned_process_tree_is_active(
        process,
        None,
        process_group_alive=process_group_alive,
    )
    assert process.poll_calls == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group 专项回归")
def test_posix_background_descendant_prevents_early_success(
    tmp_path: Path,
) -> None:
    child_code = "import time; time.sleep(30)"
    root_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])"
    )
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "posix-descendant" / "executions" / "worker",
        run_id="posix-descendant",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )

    result = run_owned_process(
        [sys.executable, "-c", root_code],
        "",
        tmp_path,
        1,
        context,
    )
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )

    assert result.status == "timed_out"
    assert lease.status == "timed_out"


def test_owned_process_persists_partial_output_after_keyboard_interrupt(tmp_path: Path) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "interrupted-output" / "executions" / "worker",
        run_id="interrupted-output",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    ready_path = tmp_path / "child-ready"
    script = (
        "from pathlib import Path; import time; "
        "print('partial-output', flush=True); "
        f"Path({str(ready_path)!r}).write_text('ready', encoding='utf-8'); "
        "time.sleep(8)"
    )

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready_path.exists():
            time.sleep(0.02)
        _thread.interrupt_main()

    interrupter = threading.Thread(target=interrupt_when_ready, daemon=True)
    interrupter.start()
    result = run_owned_process(
        [sys.executable, "-u", "-c", script],
        "",
        tmp_path,
        10,
        context,
    )
    interrupter.join(timeout=1)

    persisted_output = context.execution_dir.joinpath("process-output.txt").read_text(
        encoding="utf-8"
    )
    assert result.status == "stopped"
    assert "partial-output" in result.output
    assert persisted_output == result.output


def test_recovery_rejects_older_fresh_active_execution_when_newer_terminal_exists(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "masked-active"
    now = datetime.now(UTC)
    active_path = run_dir / "executions" / "worker" / "execution.json"
    terminal_path = run_dir / "executions" / "reviewer" / "execution.json"

    _write_execution(
        active_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            iteration=1,
            owner_pid=os.getpid(),
            child_pid=os.getpid(),
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(seconds=10)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _write_execution(
        terminal_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="reviewer",
            owner_pid=os.getpid(),
            child_pid=None,
            command=["reviewer"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="completed",
            returncode=0,
            finished_at=now.isoformat(),
        ),
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert inspection.can_recover is False
    assert inspection.record is not None
    assert inspection.record.path == active_path
    assert "PID 仍存活" in inspection.summary


def test_windows_access_denied_probe_blocks_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "access-denied"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=4242,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _install_fake_windows_process_api(
        monkeypatch,
        open_handle=0,
        last_error=5,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path


def test_windows_recovery_allows_reused_pid_with_different_creation_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "reused-pid"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=4242,
            owner_creation_token=100,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _install_fake_windows_process_api(
        monkeypatch,
        open_handle=1,
        exit_code=259,
        creation_token=200,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "PID" in inspection.summary


def test_windows_recovery_keeps_legacy_lease_without_creation_token_conservative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "legacy-lease"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=4242,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _install_fake_windows_process_api(
        monkeypatch,
        open_handle=1,
        exit_code=259,
        creation_token=200,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path


def test_windows_recovery_blocks_when_named_job_still_has_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "detached-descendant"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=1111,
            owner_creation_token=100,
            child_pid=2222,
            child_creation_token=200,
            windows_job_name="Local\\Vega-test-detached-descendant",
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(minutes=1)).isoformat(),
            lease_expires_at=(now - timedelta(seconds=30)).isoformat(),
            deadline=(now - timedelta(seconds=10)).isoformat(),
            status="running",
        ),
    )
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda *_: execution_control.ProcessProbe("gone"),
    )
    monkeypatch.setattr(
        execution_control,
        "_probe_windows_job",
        lambda _: execution_control.WindowsJobProbe("active", active_processes=1),
        raising=False,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "Job Object" in inspection.summary
    assert "原 execution owner 已退出" in inspection.summary
    assert "人工核对并终止" in inspection.summary


def test_recovery_rechecks_unconfirmed_named_job_before_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "unconfirmed-job-now-empty"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=1111,
            owner_creation_token=100,
            child_pid=2222,
            child_creation_token=200,
            windows_job_name="Local\\Vega-unconfirmed-job-now-empty",
            termination_unconfirmed=True,
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(minutes=1)).isoformat(),
            lease_expires_at=(now - timedelta(seconds=30)).isoformat(),
            deadline=(now - timedelta(seconds=10)).isoformat(),
            status="running",
        ),
    )
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda *_: execution_control.ProcessProbe("gone"),
    )
    monkeypatch.setattr(
        execution_control,
        "_probe_windows_job",
        lambda _: execution_control.WindowsJobProbe("empty", active_processes=0),
        raising=False,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "已重新确认退出" in inspection.summary


def test_posix_recovery_uses_child_pid_as_process_group_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "posix-live-group"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    child_pid = 2222
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=1111,
            child_pid=child_pid,
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(minutes=1)).isoformat(),
            lease_expires_at=(now - timedelta(seconds=30)).isoformat(),
            deadline=(now - timedelta(seconds=10)).isoformat(),
            status="running",
        ),
    )
    probed_groups: list[int] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda *_: execution_control.ProcessProbe("gone"),
    )
    monkeypatch.setattr(
        execution_control,
        "_is_posix_process_group_alive",
        lambda pgid: probed_groups.append(pgid) or True,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert probed_groups == [child_pid]


def test_posix_recovery_reconfirms_unconfirmed_tree_after_owner_root_and_group_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "posix-unconfirmed-gone"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    child_pid = 3333
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=1111,
            child_pid=child_pid,
            termination_unconfirmed=True,
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(minutes=1)).isoformat(),
            lease_expires_at=(now - timedelta(seconds=30)).isoformat(),
            deadline=(now - timedelta(seconds=10)).isoformat(),
            status="running",
        ),
    )
    probed_groups: list[int] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda *_: execution_control.ProcessProbe("gone"),
    )
    monkeypatch.setattr(
        execution_control,
        "_is_posix_process_group_alive",
        lambda pgid: probed_groups.append(pgid) or False,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert probed_groups
    assert set(probed_groups) == {child_pid}
    assert "已重新确认退出" in inspection.summary


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object 专项回归")
def test_windows_detached_grandchild_prevents_success_and_is_terminated_on_timeout(
    tmp_path: Path,
) -> None:
    run_id = "detached-grandchild-timeout"
    execution_dir = tmp_path / "runs" / run_id / "executions" / "worker"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    grandchild_code = (
        "from pathlib import Path; import os, time; "
        f"Path({str(grandchild_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(30)"
    )
    root_code = (
        "import subprocess, sys; "
        "subprocess.Popen("
        f"[sys.executable, '-c', {grandchild_code!r}], "
        "creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP"
        ")"
    )
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=execution_dir,
        run_id=run_id,
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    grandchild_pid: int | None = None

    try:
        result = run_owned_process(
            [sys.executable, "-c", root_code],
            "",
            tmp_path,
            1,
            context,
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not grandchild_pid_path.exists():
            time.sleep(0.02)
        if grandchild_pid_path.exists():
            grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))

        lease = ExecutionLease.model_validate_json(
            execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
        )
        assert grandchild_pid is not None
        assert result.status == "timed_out"
        assert lease.status == "timed_out"
        assert lease.windows_job_name is not None
        assert not execution_control.is_process_alive(grandchild_pid)
        assert execution_control._probe_windows_job(lease.windows_job_name).status == "gone"
    finally:
        if grandchild_pid is not None and execution_control.is_process_alive(grandchild_pid):
            subprocess.run(
                ["taskkill", "/PID", str(grandchild_pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object 专项回归")
@pytest.mark.parametrize("failure_stage", ["assign", "resume"])
def test_windows_job_startup_failure_never_runs_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    run_id = f"job-startup-{failure_stage}"
    execution_dir = tmp_path / "runs" / run_id / "executions" / "worker"
    child_marker = tmp_path / f"{failure_stage}.started"
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=execution_dir,
        run_id=run_id,
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )

    if failure_stage == "assign":
        monkeypatch.setattr(
            execution_control.NamedWindowsJob,
            "assign_process_id",
            lambda *_: (_ for _ in ()).throw(
                execution_control.WindowsJobError("simulated assignment failure")
            ),
        )
    else:
        monkeypatch.setattr(
            execution_process,
            "resume_suspended_process",
            lambda *_: (_ for _ in ()).throw(
                execution_control.WindowsJobError("simulated resume failure")
            ),
        )

    result = run_owned_process(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                f"Path({str(child_marker)!r}).write_text('started', encoding='utf-8')"
            ),
        ],
        "",
        tmp_path,
        5,
        context,
    )

    lease = ExecutionLease.model_validate_json(
        execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "error"
    assert lease.status == "failed"
    assert lease.windows_job_name is not None
    assert lease.child_pid is not None
    assert not child_marker.exists()
    assert not execution_control.is_process_alive(lease.child_pid)
    assert execution_control._probe_windows_job(lease.windows_job_name).status == "gone"


def test_recovery_rejects_execution_record_from_another_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "current-run"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id="other-run",
            step="worker",
            owner_pid=999999,
            command=["worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
            deadline=(now - timedelta(seconds=1)).isoformat(),
            status="timed_out",
        ),
    )

    with pytest.raises(ValueError, match="execution 记录身份不一致"):
        inspect_execution_for_recovery(run_dir)


def test_stop_request_reason_is_redacted_before_persisting(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "stop-redaction"
    now = datetime.now(UTC)
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    fake_secret = "sk-stop-fake-secret-123456"
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=os.getpid(),
            child_pid=os.getpid(),
            command=["worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )

    request_stop_for_run(run_dir, f"api_key={fake_secret}")

    request_payload = execution_path.with_name("stop-request.json").read_text(
        encoding="utf-8"
    )
    assert fake_secret not in request_payload
    assert "[REDACTED]" in request_payload
    assert set(json.loads(request_payload)) == {
        "reason",
        "requested_at",
        "requester_pid",
    }


def test_stop_rejects_mismatched_expected_execution_before_writing(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "stop-identity-mismatch"
    now = datetime.now(UTC)
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            execution_id="operation-current",
            step="worker",
            owner_pid=os.getpid(),
            command=["worker"],
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )

    with pytest.raises(ValueError, match="期望 operation 身份不一致"):
        request_stop_for_run(
            run_dir,
            "stop wrong execution",
            expected_execution_id="operation-other",
        )

    assert not execution_path.with_name("stop-request.json").exists()


def test_stop_prefers_latest_live_active_execution_over_newer_stale_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "stop-live-active"
    now = datetime.now(UTC)
    live_path = run_dir / "executions" / "worker" / "execution.json"
    stale_path = run_dir / "executions" / "verification" / "execution.json"
    live_pid = 1111
    stale_pid = 2222
    _write_execution(
        live_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=live_pid,
            command=["worker"],
            started_at=(now - timedelta(minutes=2)).isoformat(),
            last_heartbeat=(now - timedelta(seconds=10)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    _write_execution(
        stale_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="verification",
            owner_pid=stale_pid,
            command=["verification"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "is_process_alive",
        lambda pid: pid == live_pid,
    )

    record = request_stop_for_run(run_dir, "stop live execution")

    assert record.path == live_path
    assert live_path.with_name("stop-request.json").exists()
    assert not stale_path.with_name("stop-request.json").exists()


def test_stop_does_not_write_request_when_owner_is_gone_but_child_is_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "stop-without-owner"
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    now = datetime.now(UTC)
    owner_pid = 1111
    child_pid = 2222
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="worker",
            owner_pid=owner_pid,
            child_pid=child_pid,
            command=["worker"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
        ),
    )
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda pid, _: execution_control.ProcessProbe(
            "gone" if pid == owner_pid else "alive"
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "_is_posix_process_group_alive",
        lambda pgid: pgid == child_pid,
    )

    with pytest.raises(ValueError, match="无执行者可消费"):
        request_stop_for_run(run_dir, "stop orphaned tree")

    assert not execution_path.with_name("stop-request.json").exists()


def test_windows_taskkill_nonzero_keeps_stop_execution_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "taskkill-failure" / "executions" / "worker",
        run_id="taskkill-failure",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )
    process = _FakeOwnedProcess(pid=4242, wait_times_out=False)
    taskkill_commands: list[list[str]] = []
    request = execution_control.StopRequest(
        reason="manual stop",
        requested_at=datetime.now(UTC).isoformat(),
        requester_pid=os.getpid(),
    )

    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(
        execution_control,
        "_create_windows_job_for_execution",
        lambda *_: None,
    )
    monkeypatch.setattr(execution_control.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=5, stdout="", stderr="access denied")
        ),
    )
    monkeypatch.setattr(
        execution_control.ExecutionController,
        "read_stop_request",
        lambda _: request,
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    result = run_owned_process(["fake-runner"], "", tmp_path, 5, context)
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    inspection = inspect_execution_for_recovery(context.execution_dir.parents[1])

    assert result.status == "error"
    assert lease.status == "stop_requested"
    assert lease.finished_at is None
    assert lease.termination_unconfirmed is True
    assert lease.reason is not None
    assert "taskkill 退出码 5" in lease.reason
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]
    assert not inspection.can_recover


def test_windows_taskkill_replaces_undecodable_localized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_kwargs: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed_kwargs.update(kwargs)
        if kwargs.get("errors") != "replace":
            raise UnicodeDecodeError(
                "utf-8",
                b"\xb3",
                0,
                1,
                "invalid start byte",
            )
        return SimpleNamespace(
            returncode=5,
            stdout="",
            stderr="localized output \ufffd",
        )

    monkeypatch.setattr(execution_control.subprocess, "run", fake_run)

    result = execution_control._run_windows_taskkill(4242, force=False, timeout=1)

    assert observed_kwargs["text"] is True
    assert observed_kwargs["errors"] == "replace"
    assert result == "taskkill \u9000\u51fa\u7801 5\uff1alocalized output \ufffd"


def test_recovery_rejects_persisted_unconfirmed_live_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / "unconfirmed-tree"
    execution_path = run_dir / "executions" / "verification" / "execution.json"
    now = datetime.now(UTC)
    _write_execution(
        execution_path,
        ExecutionLease(
            run_id=run_dir.name,
            step="verification",
            owner_pid=1111,
            child_pid=2222,
            termination_unconfirmed=True,
            command=["verification"],
            started_at=(now - timedelta(minutes=1)).isoformat(),
            last_heartbeat=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
            deadline=(now + timedelta(minutes=2)).isoformat(),
            status="running",
            reason="owned process tree termination unconfirmed",
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "_probe_process",
        lambda *_: execution_control.ProcessProbe("gone"),
    )
    monkeypatch.setattr(
        execution_control,
        "_is_posix_process_group_alive",
        lambda _: True,
    )

    inspection = inspect_execution_for_recovery(run_dir)

    assert not inspection.can_recover
    assert inspection.record is not None
    assert inspection.record.path == execution_path
    assert "进程全部退出" in inspection.summary
    assert "不允许自动 recovery" in inspection.summary


def test_windows_taskkill_failure_keeps_tree_termination_unconfirmed_when_root_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=4343, wait_times_out=False)
    taskkill_commands: list[list[str]] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=5, stdout="", stderr="access denied")
        ),
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: False)

    result = execution_control._terminate_owned_process(process, 0.1)

    assert not result.succeeded
    assert "taskkill 退出码 5" in result.detail
    assert "process tree" in result.detail
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]


def test_windows_does_not_force_kill_reused_pid_after_owned_handle_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=4444, wait_times_out=False)
    taskkill_commands: list[list[str]] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        execution_control,
        "is_process_alive",
        lambda _: True,
    )

    result = execution_control._terminate_owned_process(process, 0.1)

    assert result.succeeded
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]
    assert not process.kill_called


def test_posix_termination_requires_owned_process_group_to_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeOwnedProcess(pid=5353, wait_times_out=False)
    signals: list[int] = []
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(execution_control.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        execution_control.os,
        "killpg",
        lambda pid, sent_signal: signals.append(sent_signal),
        raising=False,
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: False)
    monkeypatch.setattr(
        execution_control,
        "_is_posix_process_group_alive",
        lambda _: True,
    )

    result = execution_control._terminate_owned_process(process, 0.1)

    assert not result.succeeded
    assert execution_control.signal.SIGTERM in signals
    assert execution_control.signal.SIGKILL in signals
    assert f"process group {process.pid} 仍存活" in result.detail


def test_posix_process_group_ignores_terminal_linux_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc_root = tmp_path / "proc"
    for pid, state in [(101, "Z"), (102, "X"), (103, "S")]:
        process_dir = proc_root / str(pid)
        process_dir.mkdir(parents=True)
        process_dir.joinpath("stat").write_text(
            f"{pid} (child process) {state} 1 5353 5353 0",
            encoding="utf-8",
        )

    states = execution_control._linux_process_group_states(5353, proc_root)

    assert sorted(states) == ["S", "X", "Z"]
    monkeypatch.setattr(execution_control.os, "killpg", lambda *_: None, raising=False)
    monkeypatch.setattr(
        execution_control,
        "_linux_process_group_states",
        lambda _: ["Z", "X"],
    )
    assert not execution_control._is_posix_process_group_alive(5353)

    monkeypatch.setattr(
        execution_control,
        "_linux_process_group_states",
        lambda _: ["Z", "S"],
    )
    assert execution_control._is_posix_process_group_alive(5353)


def test_windows_final_wait_timeout_keeps_timeout_execution_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "wait-timeout" / "executions" / "worker",
        run_id="wait-timeout",
        step="worker",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )
    process = _FakeOwnedProcess(pid=5252, wait_times_out=True)
    taskkill_commands: list[list[str]] = []

    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(
        execution_control,
        "_create_windows_job_for_execution",
        lambda *_: None,
    )
    monkeypatch.setattr(execution_control.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: True)

    result = run_owned_process(["fake-runner"], "", tmp_path, 1, context)
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    inspection = inspect_execution_for_recovery(context.execution_dir.parents[1])

    assert result.status == "error"
    assert lease.status == "running"
    assert lease.finished_at is None
    assert lease.reason is not None
    assert "最终 wait 超时" in lease.reason
    assert f"PID {process.pid} 仍存活" in lease.reason
    assert process.kill_called
    assert taskkill_commands[-1][-1] == "/F"
    assert not inspection.can_recover


class _FakeOwnedProcess:
    def __init__(self, *, pid: int, wait_times_out: bool) -> None:
        self.pid = pid
        self.wait_times_out = wait_times_out
        self.returncode: int | None = None
        self.stdin = io.BytesIO()
        self.kill_called = False
        self.poll_calls = 0

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_times_out:
            raise subprocess.TimeoutExpired(cmd=["fake-runner"], timeout=timeout)
        self.returncode = 1
        return self.returncode

    def kill(self) -> None:
        self.kill_called = True


class _FakeWindowsFunction:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)  # type: ignore[operator]


class _FakeWindowsKernel32:
    def __init__(
        self,
        *,
        open_handle: int,
        exit_code: int,
        creation_token: int,
    ) -> None:
        self.OpenProcess = _FakeWindowsFunction(lambda *_: open_handle)
        self.GetExitCodeProcess = _FakeWindowsFunction(
            lambda _handle, pointer: self._set_exit_code(pointer, exit_code)
        )
        self.GetProcessTimes = _FakeWindowsFunction(
            lambda _handle, creation, _exit, _kernel, _user: self._set_creation_token(
                creation,
                creation_token,
            )
        )
        self.CloseHandle = _FakeWindowsFunction(lambda _handle: 1)

    @staticmethod
    def _set_exit_code(pointer: object, exit_code: int) -> int:
        pointer._obj.value = exit_code  # type: ignore[attr-defined]
        return 1

    @staticmethod
    def _set_creation_token(pointer: object, creation_token: int) -> int:
        pointer._obj.dwLowDateTime = creation_token & 0xFFFFFFFF  # type: ignore[attr-defined]
        pointer._obj.dwHighDateTime = creation_token >> 32  # type: ignore[attr-defined]
        return 1


def _install_fake_windows_process_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    open_handle: int,
    last_error: int = 0,
    exit_code: int = 0,
    creation_token: int = 0,
) -> None:
    kernel32 = _FakeWindowsKernel32(
        open_handle=open_handle,
        exit_code=exit_code,
        creation_token=creation_token,
    )
    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)


def _write_execution(path: Path, lease: ExecutionLease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")


def _execution_lease_for_atomic_publish(execution_id: str) -> ExecutionLease:
    now = datetime.now(UTC)
    return ExecutionLease(
        run_id="atomic-publish",
        execution_id=execution_id,
        step="worker",
        owner_pid=os.getpid(),
        command=["worker"],
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
        deadline=(now + timedelta(minutes=2)).isoformat(),
        status="running",
    )
