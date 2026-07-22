from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")

from tests.experimental.langgraph_engine.test_interrupt_resume import (
    _start_pending_run,
)
from vega.cli import app
from vega.decision import DecisionStore
from vega.loop_graph_decision import read_pending_decision
from vega.loop_graph_state import read_graph_state
from vega.loop_runtime import LoopAutomationRuntime


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_PATTERN.sub("", value)


def test_hitl_cli_records_decision_id_and_resumes_by_identity_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _, run_dir, _, _ = _start_pending_run(tmp_path)
    pending_id = read_graph_state(run_dir)["pending_human_decision_id"]
    assert pending_id is not None
    pending = read_pending_decision(run_dir, pending_id)
    monkeypatch.chdir(workspace)
    runner = CliRunner()

    approve = runner.invoke(
        app,
        [
            "decision",
            "approve",
            "--run",
            run_dir.name,
            "--type",
            "gate",
            "--reason",
            "CLI 人工确认当前高风险变更。",
            "--ref",
            pending.artifact_ref,
        ],
    )

    assert approve.exit_code == 0, approve.output
    decisions = DecisionStore(run_dir).list()
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.id in approve.output
    assert decision.references == [pending.artifact_ref]

    status = runner.invoke(app, ["status", "--run", run_dir.name])
    assert status.exit_code == 0, status.output
    assert pending.artifact_ref in status.output
    assert "vega resume" in status.output
    assert "--decision-id <dec-id>" in status.output

    calls: list[tuple[Path, str, str, str | None]] = []

    def fake_resume(
        self: LoopAutomationRuntime,
        run: str,
        decision_id: str,
        *,
        engine: str | None = None,
    ) -> Path:
        calls.append((self.workspace.resolve(), run, decision_id, engine))
        return run_dir

    monkeypatch.setattr(
        LoopAutomationRuntime,
        "resume_langgraph_decision",
        fake_resume,
    )
    resumed = runner.invoke(
        app,
        [
            "resume",
            "--run",
            run_dir.name,
            "--decision-id",
            decision.id,
            "--engine",
            "langgraph",
        ],
    )

    assert resumed.exit_code == 0, resumed.output
    assert f"resume 完成：{run_dir}" in resumed.output
    assert calls == [
        (
            workspace.resolve(),
            run_dir.name,
            decision.id,
            "langgraph",
        )
    ]

    missing_identity = runner.invoke(
        app,
        [
            "resume",
            "--run",
            run_dir.name,
            "--engine",
            "langgraph",
        ],
    )
    assert missing_identity.exit_code == 2
    assert "--decision-id" in _strip_ansi(missing_identity.output)
