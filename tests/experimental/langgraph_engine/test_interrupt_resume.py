from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")

from vega.decision import DecisionStore
from vega.execution_control import ExecutionController
from vega.loop_graph_decision import (
    read_decision_consumption,
    read_pending_decision,
)
from vega.loop_graph_state import read_graph_state
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import BriefInput
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.verification import VerificationRunResult


class HitlWorker:
    def __init__(self) -> None:
        self.calls = 0
        self.write_calls = 0

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        return ["gate4-hitl-worker"]

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        assert execution_context is not None
        self.calls += 1
        command = self.build_command(repo_path, sandbox)
        controller = ExecutionController(execution_context)
        controller.prepare(command, timeout_seconds)
        self.write_calls += 1
        repo_path.joinpath("README.md").write_text(
            "# Demo\nGATE4_HITL_EFFECT\n",
            encoding="utf-8",
            newline="\n",
        )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output="GATE4_HITL_WORKER_OUTPUT",
            command=command,
        )


class HitlReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        self.calls += 1
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "Gate 4 HITL 后 reviewer 通过。",
                    "findings": [],
                },
                ensure_ascii=False,
            ),
            command=["gate4-hitl-reviewer"],
        )


def _init_high_risk_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    path.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    path.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "risk:",
                "  high_paths:",
                "    - README.md",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "."],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Tests",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "init",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _build_runtime(
    workspace: Path,
    worker: HitlWorker,
    reviewer: HitlReviewer,
    *,
    verification_failed_count: int = 0,
    graph_fault_injector: Callable[[str], None] | None = None,
) -> LoopAutomationRuntime:
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
        graph_fault_injector=graph_fault_injector,
    )

    def run_verification(request) -> VerificationRunResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        result_path = request.output_dir / "verification-result.json"
        summary_path = request.output_dir / "verification-summary.md"
        result_path.write_text(
            json.dumps(
                {
                    "command_count": 1,
                    "failed_count": verification_failed_count,
                    "interruption_status": None,
                    "interruption_command": None,
                    "interruption_reason": None,
                    "results": [
                        {
                            "command": "fixture-check",
                            "status": (
                                "failed"
                                if verification_failed_count
                                else "passed"
                            ),
                            "returncode": (
                                1 if verification_failed_count else 0
                            ),
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        summary_path.write_text(
            "# Verification\n\n"
            + (
                "- failed\n"
                if verification_failed_count
                else "- passed\n"
            ),
            encoding="utf-8",
            newline="\n",
        )
        return VerificationRunResult(
            summary_path=summary_path,
            result_path=result_path,
            command_count=1,
            failed_count=verification_failed_count,
        )

    runtime.step_services = replace(
        runtime.step_services,
        run_verification=run_verification,
    )
    return runtime


def _start_pending_run(
    tmp_path: Path,
    *,
    verification_failed_count: int = 0,
) -> tuple[
    Path,
    Path,
    Path,
    HitlWorker,
    HitlReviewer,
]:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_high_risk_repo(repo)
    worker = HitlWorker()
    reviewer = HitlReviewer()
    runtime = _build_runtime(
        workspace,
        worker,
        reviewer,
        verification_failed_count=verification_failed_count,
    )
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="Gate 4 HITL fixture",
            source="test",
            repo_path=str(repo),
        ),
        "auto",
        max_iterations=1,
        verify=True,
        engine="langgraph",
    )
    return workspace, repo, run_dir, worker, reviewer


def _state(run_dir: Path) -> dict[str, object]:
    return json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))


def _event_counts(run_dir: Path) -> Counter[str]:
    return Counter(
        json.loads(line)["event"]
        for line in run_dir.joinpath("trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    )


def test_hitl_interrupt_writes_pending_identity_before_decision(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)

    state = _state(run_dir)
    graph_state = read_graph_state(run_dir)
    pending_id = graph_state["pending_human_decision_id"]

    assert state["status"] == "running"
    assert state["current_step"] == "human_decision"
    assert pending_id is not None
    pending = read_pending_decision(run_dir, pending_id)
    assert pending.iteration == 1
    assert pending.verification_status == "passed"
    assert worker.calls == 1
    assert worker.write_calls == 1
    assert reviewer.calls == 0
    assert DecisionStore(run_dir).list() == []

    status = run_status_payload(workspace, run_dir.name)
    assert status["pending_human_decision_id"] == pending_id
    assert any(
        "vega decision approve" in item
        for item in status["next_steps"]
    )
    assert any("vega resume" in item for item in status["next_steps"])


def test_p0_5_ledger_written_before_resume_consumes_once_without_replay(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    graph_state = read_graph_state(run_dir)
    pending_id = graph_state["pending_human_decision_id"]
    assert pending_id is not None
    pending = read_pending_decision(run_dir, pending_id)
    events_before = _event_counts(run_dir)
    decision = DecisionStore(run_dir).append(
        decision_type="gate",
        decision="approved",
        reason="已人工确认高风险 README 变更范围。",
        references=[pending.artifact_ref],
    )

    # 模拟 ledger 已写、graph resume 前原进程退出：使用新的 Runtime 实例继续。
    resumed = _build_runtime(
        workspace,
        worker,
        reviewer,
    ).resume_langgraph_decision(
        run_dir.name,
        decision.id,
        engine="langgraph",
    )

    assert resumed == run_dir
    state = _state(run_dir)
    assert state["status"] == "success"
    assert worker.calls == 1
    assert worker.write_calls == 1
    assert reviewer.calls == 1
    assert len(DecisionStore(run_dir).list()) == 1
    consumption = read_decision_consumption(run_dir, pending_id)
    assert consumption.decision_id == decision.id
    assert consumption.decision == "approved"
    assert read_graph_state(run_dir)["pending_human_decision_id"] is None

    events_after = _event_counts(run_dir)
    for event in (
        "brief_finished",
        "worker_prompt_measured",
        "worker_started",
        "worker_finished",
        "workspace_check_finished",
        "verification_finished",
        "risk_gate_finished",
    ):
        assert events_after[event] == events_before[event]
    assert events_after["human_decision_finished"] == 1


def test_rejected_decision_stops_without_starting_reviewer(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    pending_id = read_graph_state(run_dir)["pending_human_decision_id"]
    assert pending_id is not None
    pending = read_pending_decision(run_dir, pending_id)
    decision = DecisionStore(run_dir).append(
        decision_type="gate",
        decision="rejected",
        reason="人工判断风险不可接受。",
        references=[pending.artifact_ref],
    )

    _build_runtime(
        workspace,
        worker,
        reviewer,
    ).resume_langgraph_decision(
        run_dir.name,
        decision.id,
        engine="langgraph",
    )

    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_rejected"
    assert worker.calls == 1
    assert reviewer.calls == 0
    consumption = read_decision_consumption(run_dir, pending_id)
    assert consumption.decision == "rejected"


def test_decision_resume_with_unbound_sqlite_sidecar_stops_before_consumption(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    pending_id = read_graph_state(run_dir)["pending_human_decision_id"]
    assert pending_id is not None
    pending = read_pending_decision(run_dir, pending_id)
    decision = DecisionStore(run_dir).append(
        decision_type="gate",
        decision="approved",
        reason="验证 checkpoint 信任链失败时不得消费 decision。",
        references=[pending.artifact_ref],
    )
    journal = run_dir / "graph" / "checkpoints.sqlite-journal"
    journal.write_bytes(b"unbound rollback journal")
    journal_before = journal.read_bytes()

    resumed = _build_runtime(
        workspace,
        worker,
        reviewer,
    ).resume_langgraph_decision(
        run_dir.name,
        decision.id,
        engine="langgraph",
    )

    assert resumed == run_dir
    assert worker.calls == 1
    assert worker.write_calls == 1
    assert reviewer.calls == 0
    assert journal.read_bytes() == journal_before
    assert not list(run_dir.glob("graph/decision-consumptions/*.json"))
    state = _state(run_dir)
    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_recovery_needs_human"
    report = run_dir.joinpath("graph-recovery-report.md").read_text(
        encoding="utf-8"
    )
    assert "checkpoint_validation_failed" in report
    assert "未调用恢复用可写 SQLite checkpointer" in report


def test_plain_recover_refuses_pending_human_decision(
    tmp_path: Path,
) -> None:
    workspace, _, run_dir, worker, reviewer = _start_pending_run(tmp_path)
    state_before = run_dir.joinpath("state.json").read_bytes()

    with pytest.raises(ValueError, match="vega resume"):
        _build_runtime(
            workspace,
            worker,
            reviewer,
        ).recover_langgraph(
            run_dir.name,
            "不应把 HITL interrupt 当成普通 crash recovery",
            engine="langgraph",
        )

    assert run_dir.joinpath("state.json").read_bytes() == state_before
    assert not run_dir.joinpath("graph-recovery-report.md").exists()
    assert worker.calls == 1
    assert reviewer.calls == 0
