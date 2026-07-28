from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from vega import execution_control
from vega.execution_control import ExecutionLease, inspect_execution_for_recovery
from vega.experimental.ma2b.probe_harness import (
    ProbeHarnessError,
    build_probe_execution_context,
    build_probe_run_root,
    build_probe_worker_prompt,
    load_probe_candidate,
)


def test_probe_candidate_prompt_only_contains_assigned_context_packet(
    tmp_path: Path,
) -> None:
    candidate_root = _write_candidate(tmp_path)
    candidate = load_probe_candidate(candidate_root)

    first = build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=("first-slice",),
    )
    second = build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=("second-slice",),
    )
    combined = build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=("first-slice", "second-slice"),
    )

    assert "FIRST_PACKET_BOUNDARY" in first
    assert "SECOND_PACKET_BOUNDARY" not in first
    assert "SECOND_PACKET_BOUNDARY" in second
    assert "FIRST_PACKET_BOUNDARY" not in second
    assert "FIRST_PACKET_BOUNDARY" in combined
    assert "SECOND_PACKET_BOUNDARY" in combined
    assert "verifier" not in combined.casefold()
    assert "reference patch" not in combined.casefold()
    assert combined == build_probe_worker_prompt(
        candidate,
        assigned_slice_ids=("first-slice", "second-slice"),
    )


def test_probe_candidate_rejects_context_packet_with_verifier_content(
    tmp_path: Path,
) -> None:
    candidate_root = _write_candidate(tmp_path)
    candidate_root.joinpath("context", "first.md").write_text(
        "读取 verifier/test_node_profile_probe.py\n",
        encoding="utf-8",
    )

    try:
        load_probe_candidate(candidate_root)
    except ProbeHarnessError as exc:
        assert exc.issue_code == "probe_context_packet_forbidden_content"
    else:
        raise AssertionError("包含 verifier 的 context packet 必须被拒绝")


def test_probe_run_root_is_short_and_execution_identity_comes_from_directory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    run_root = build_probe_run_root(repo, "node-v2-m")
    context = build_probe_execution_context(
        repo_root=repo,
        run_root=run_root,
        execution_label="m1",
    )

    assert run_root == repo.resolve() / ".tmp" / "m2n" / "node-v2-m"
    assert context.run_id == run_root.name
    assert context.execution_dir == run_root / "x" / "m1"


def test_probe_execution_context_survives_fake_windows_timeout_and_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_root = build_probe_run_root(repo, "timeout-v2")
    context = build_probe_execution_context(
        repo_root=repo,
        run_root=run_root,
        execution_label="m1",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.1,
    )
    process = _FakeOwnedProcess(pid=4242)
    taskkill_commands: list[list[str]] = []
    ticks = iter((0.0, 0.0, 2.0))

    monkeypatch.setattr(execution_control, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(execution_control, "_process_group_options", lambda: {})
    monkeypatch.setattr(
        execution_control.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        execution_control.subprocess,
        "run",
        lambda command, **kwargs: (
            taskkill_commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(execution_control, "is_process_alive", lambda _: False)
    monkeypatch.setattr(
        execution_control.time,
        "monotonic",
        lambda: next(ticks, 2.0),
    )

    result = execution_control.run_owned_process(
        ["fake-runner"],
        "",
        repo,
        1,
        context,
    )
    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    inspection = inspect_execution_for_recovery(run_root)

    assert result.status == "timed_out"
    assert result.termination_unconfirmed is False
    assert lease.status == "timed_out"
    assert lease.run_id == run_root.name
    assert inspection.can_recover
    assert "身份不一致" not in inspection.summary
    assert taskkill_commands == [["taskkill", "/PID", str(process.pid), "/T"]]


class _FakeOwnedProcess:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = io.BytesIO()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 1
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1


def _write_candidate(root: Path) -> Path:
    candidate = root / "candidate"
    context = candidate / "context"
    context.mkdir(parents=True)
    candidate.joinpath("task.md").write_text(
        "# 任务\n\n完成两个互斥切片。\n",
        encoding="utf-8",
    )
    candidate.joinpath("plan.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_id": "MA2B-UNIT-V1",
                "slices": [
                    {
                        "slice_id": "first-slice",
                        "summary": "修改第一个文件。",
                        "allowed_write_paths": ["src/first.py"],
                        "context_packet": "context/first.md",
                    },
                    {
                        "slice_id": "second-slice",
                        "summary": "修改第二个文件。",
                        "allowed_write_paths": ["src/second.py"],
                        "context_packet": "context/second.md",
                    },
                ],
                "treatments": {
                    "S": "单 Worker",
                    "M": "双 Worker",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    context.joinpath("first.md").write_text(
        "# FIRST_PACKET_BOUNDARY\n\n只包含第一个切片。\n",
        encoding="utf-8",
    )
    context.joinpath("second.md").write_text(
        "# SECOND_PACKET_BOUNDARY\n\n只包含第二个切片。\n",
        encoding="utf-8",
    )
    return candidate
