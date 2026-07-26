from __future__ import annotations

import sys
from pathlib import Path

import pytest

import vega.execution_control as execution_control


def test_owned_process_reports_bounded_progress_without_persisting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vega.execution_feedback as execution_feedback

    events: list[tuple[str, int]] = []
    monkeypatch.setattr(execution_feedback, "PROGRESS_INTERVAL_SECONDS", 0.03)
    context = execution_control.RunnerExecutionContext(
        execution_dir=tmp_path / "runs" / "progress" / "executions" / "worker",
        run_id="progress",
        step="worker",
        heartbeat_interval_seconds=0.01,
        lease_timeout_seconds=0.5,
        progress_reporter=lambda step, elapsed: events.append((step, elapsed)),
    )

    result = execution_control.run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(0.12)"],
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

    broken_context = execution_control.RunnerExecutionContext(
        execution_dir=(
            tmp_path / "runs" / "broken-progress" / "executions" / "worker"
        ),
        run_id="broken-progress",
        step="worker",
        heartbeat_interval_seconds=0.01,
        lease_timeout_seconds=0.5,
        progress_reporter=broken_reporter,
    )
    broken_result = execution_control.run_owned_process(
        [sys.executable, "-c", "print('worker completed')"],
        "",
        tmp_path,
        5,
        broken_context,
    )

    assert broken_result.status == "success"
    assert "worker completed" in broken_result.output
