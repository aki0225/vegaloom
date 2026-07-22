from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest

pytest.importorskip("langgraph")

import vega.loop_graph_runtime as loop_graph_runtime
import vega.loop_runtime as loop_runtime
from vega.execution_control import ExecutionController
from vega.loop_graph_checkpoint import validate_checkpoint_manifest
from vega.loop_graph_decision import read_pending_decision
from vega.goal_evidence import (
    validate_loop_artifact_integrity,
    validate_loop_evidence_freshness,
)
from vega.loop_graph_state import GRAPH_STATE_ARTIFACT, validate_graph_state
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval
from vega.models import BriefInput, LoopAutomationState
from vega.run_status import run_status_payload
from vega.runner import RunnerResult
from vega.verification import VerificationRunResult


@dataclass(frozen=True)
class CaseSpec:
    name: str
    verification_failed_count: int
    risk: Literal["low", "high"]
    recommendation: Literal["self-check", "human-review"]
    reviewer_status: Literal["success", "error"]
    expected_status: Literal["success", "needs_human"]
    expected_step: str
    expected_reviewer_calls: int
    expected_events: tuple[str, ...]


RISK_HUMAN_REVIEW_CASE = CaseSpec(
    name="risk_human_review",
    verification_failed_count=0,
    risk="high",
    recommendation="human-review",
    reviewer_status="success",
    expected_status="needs_human",
    expected_step="risk_gate_needs_human",
    expected_reviewer_calls=0,
    expected_events=(
        "prepare_run",
        "capture_workspace",
        "execute_worker_epoch",
        "reconcile_workspace",
        "run_verification",
        "run_reflect",
        "evaluate_risk",
        "request_human_decision",
        "finalize_run",
    ),
)

CASES = (
    CaseSpec(
        name="success",
        verification_failed_count=0,
        risk="low",
        recommendation="self-check",
        reviewer_status="success",
        expected_status="success",
        expected_step="done",
        expected_reviewer_calls=1,
        expected_events=(
            "prepare_run",
            "capture_workspace",
            "execute_worker_epoch",
            "reconcile_workspace",
            "run_verification",
            "run_reflect",
            "evaluate_risk",
            "dispatch_review",
            "finalize_run",
        ),
    ),
    CaseSpec(
        name="verification_failure",
        verification_failed_count=1,
        risk="low",
        recommendation="self-check",
        reviewer_status="success",
        expected_status="needs_human",
        expected_step="done",
        expected_reviewer_calls=1,
        expected_events=(
            "prepare_run",
            "capture_workspace",
            "execute_worker_epoch",
            "reconcile_workspace",
            "run_verification",
            "run_reflect",
            "evaluate_risk",
            "dispatch_review",
            "finalize_run",
        ),
    ),
    CaseSpec(
        name="review_failure",
        verification_failed_count=0,
        risk="low",
        recommendation="self-check",
        reviewer_status="error",
        expected_status="needs_human",
        expected_step="reviewer_error",
        expected_reviewer_calls=1,
        expected_events=(
            "prepare_run",
            "capture_workspace",
            "execute_worker_epoch",
            "reconcile_workspace",
            "run_verification",
            "run_reflect",
            "evaluate_risk",
            "dispatch_review",
            "finalize_run",
        ),
    ),
)


class CountingWorker:
    def __init__(self) -> None:
        self.calls = 0
        self.write_calls = 0

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
        self.write_calls += 1
        assert execution_context is not None
        command = self.build_command(repo_path, sandbox)
        controller = ExecutionController(execution_context)
        controller.prepare(command, timeout_seconds)
        repo_path.joinpath("README.md").write_text(
            "# Demo\nDIFF_CANARY_GATE2\n",
            encoding="utf-8",
            newline="\n",
        )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output="WORKER_OUTPUT_CANARY_GATE2",
            command=command,
        )

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        return ["gate2-worker"]


class CountingReviewer:
    def __init__(
        self,
        status: Literal["success", "error"],
        verdict: Literal["approve", "request_changes"] = "approve",
    ) -> None:
        self.status = status
        self.verdict = verdict
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
        if self.status == "error":
            return RunnerResult(
                status="error",
                output="",
                error="REVIEWER_ERROR_CANARY_GATE2",
                command=["gate2-review-error"],
            )
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": self.verdict,
                    "summary": "REVIEWER_PRIVATE_CANARY_GATE2",
                    "findings": (
                        []
                        if self.verdict == "approve"
                        else [
                            {
                                "severity": "major",
                                "title": "继续下一轮",
                                "evidence": "MULTI_ROUND_CANARY_GATE2",
                                "recommendation": "再次执行 worker",
                            }
                        ]
                    ),
                },
                ensure_ascii=False,
            ),
            command=["gate2-review-approve"],
        )


@dataclass
class RunEvidence:
    run_dir: Path
    repo: Path
    worker_calls: int
    worker_write_calls: int
    reviewer_calls: int
    verification_calls: int
    events: list[str]
    business_write_counts: dict[str, int]
    child_run_counts: dict[str, int]


def _run_tree_bytes(run_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(run_dir).as_posix(): path.read_bytes()
        for path in run_dir.rglob("*")
        if path.is_file()
    }


def _checkpoint_payload_blobs(run_dir: Path) -> list[bytes]:
    checkpoint_path = run_dir / "graph" / "checkpoints.sqlite"
    connection = sqlite3.connect(
        f"{checkpoint_path.as_uri()}?mode=ro",
        uri=True,
    )
    try:
        rows = [
            *connection.execute(
                "SELECT checkpoint, metadata FROM checkpoints"
            ).fetchall(),
            *connection.execute(
                "SELECT value, NULL FROM writes"
            ).fetchall(),
        ]
    finally:
        connection.close()
    return [
        bytes(value)
        for row in rows
        for value in row
        if isinstance(value, bytes)
    ]


def _final_report_conclusion(run_dir: Path) -> str:
    for line in run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("- 结论："):
            return line.removeprefix("- 结论：").strip()
    raise AssertionError("final-report.md 缺少结论")


def _init_git_repo(path: Path, *, high_risk_readme: bool) -> None:
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
    if high_risk_readme:
        path.joinpath(".vega.yaml").write_text(
            "version: 1\nrisk:\n  high_paths:\n    - README.md\n",
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


def _run_case(
    root: Path,
    engine: Literal["linear", "langgraph"],
    case: CaseSpec,
    *,
    max_iterations: int = 1,
    verify: bool = True,
    reviewer_verdict: Literal["approve", "request_changes"] = "approve",
) -> RunEvidence:
    workspace = root / f"{engine}-workspace"
    repo = root / f"{engine}-repo"
    _init_git_repo(repo, high_risk_readme=case.risk == "high")
    worker = CountingWorker()
    reviewer = CountingReviewer(case.reviewer_status, reviewer_verdict)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )
    verification_calls = 0

    def run_verification(request) -> VerificationRunResult:
        nonlocal verification_calls
        verification_calls += 1
        request.output_dir.mkdir(parents=True, exist_ok=True)
        result_path = request.output_dir / "verification-result.json"
        summary_path = request.output_dir / "verification-summary.md"
        command = "python -m pytest -q"
        status = (
            "failed"
            if case.verification_failed_count
            else "passed"
        )
        result_path.write_text(
            json.dumps(
                {
                    "repo_path": str(request.repo_path.resolve()),
                    "config_path": None,
                    "config_check": {},
                    "commands": [command],
                    "results": [
                        {
                            "command": command,
                            "status": status,
                            "output": "VERIFICATION_LOG_CANARY_GATE2",
                            "error": (
                                "deterministic failure"
                                if status == "failed"
                                else None
                            ),
                            "returncode": 1 if status == "failed" else 0,
                            "interruption_status": None,
                            "interruption_reason": None,
                        }
                    ],
                    "command_count": 1,
                    "failed_count": case.verification_failed_count,
                    "selected_command_count": 1,
                    "skipped_commands": [],
                    "interruption_status": None,
                    "interruption_command": None,
                    "interruption_reason": None,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        summary_path.write_text(
            "# Verification\n\nVERIFICATION_LOG_CANARY_GATE2\n",
            encoding="utf-8",
        )
        return VerificationRunResult(
            summary_path=summary_path,
            result_path=result_path,
            command_count=1,
            failed_count=case.verification_failed_count,
        )

    services = replace(
        runtime.step_services,
        run_verification=run_verification,
    )
    events: list[str] = []

    def record(name, handler):
        def recorded(request):
            events.append(name)
            return handler(request)

        return recorded

    runtime.step_services = replace(
        services,
        prepare_run=record("prepare_run", services.prepare_run),
        capture_workspace=record(
            "capture_workspace",
            services.capture_workspace,
        ),
        execute_worker_epoch=record(
            "execute_worker_epoch",
            services.execute_worker_epoch,
        ),
        reconcile_workspace=record(
            "reconcile_workspace",
            services.reconcile_workspace,
        ),
        run_verification=record(
            "run_verification",
            services.run_verification,
        ),
        run_reflect=record("run_reflect", services.run_reflect),
        evaluate_risk=record("evaluate_risk", services.evaluate_risk),
        request_human_decision=record(
            "request_human_decision",
            services.request_human_decision,
        ),
        dispatch_review=record("dispatch_review", services.dispatch_review),
        finalize_run=record("finalize_run", services.finalize_run),
    )
    business_write_counts: Counter[str] = Counter()
    original_write_text = Path.write_text

    def record_write_text(
        path: Path,
        data: str,
        *args,
        **kwargs,
    ) -> int:
        identity = _business_write_identity(path, workspace, repo)
        if identity is not None:
            business_write_counts[identity] += 1
        return original_write_text(path, data, *args, **kwargs)

    with patch.object(Path, "write_text", record_write_text):
        run_dir = runtime.start(
            BriefInput(
                mode="bug",
                text="TASK_PROMPT_CANARY_GATE2",
                source="test",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=max_iterations,
            verify=verify,
            engine=engine,
        )
    return RunEvidence(
        run_dir=run_dir,
        repo=repo,
        worker_calls=worker.calls,
        worker_write_calls=worker.write_calls,
        reviewer_calls=reviewer.calls,
        verification_calls=verification_calls,
        events=events,
        business_write_counts=dict(sorted(business_write_counts.items())),
        child_run_counts=_child_run_counts(workspace, run_dir),
    )


def _parity_snapshot(evidence: RunEvidence) -> dict[str, object]:
    state = json.loads(
        evidence.run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    iteration = state["iterations"][-1]
    normalized_iteration = {
        key: value
        for key, value in iteration.items()
        if key
        not in {
            "reflect_run",
            "risk_gate_source_run",
            "risk_gate_result_sha256",
            "risk_gate_report_sha256",
            "review_run",
        }
    }
    relative_files = {
        path.relative_to(evidence.run_dir).as_posix()
        for path in evidence.run_dir.rglob("*")
        if path.is_file()
        and not _is_graph_control_artifact(
            path.relative_to(evidence.run_dir).as_posix()
        )
    }
    trace = [
        json.loads(line)
        for line in evidence.run_dir.joinpath("trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    diff = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "HEAD"],
        cwd=evidence.repo,
        check=True,
        capture_output=True,
    ).stdout
    workspace = evidence.run_dir.parent.parent
    artifact_integrity = validate_loop_artifact_integrity(
        workspace,
        evidence.repo,
        evidence.run_dir,
    )
    evidence_freshness = validate_loop_evidence_freshness(
        workspace,
        evidence.repo,
        evidence.run_dir,
    )
    current_iteration = state["current_iteration"]
    event_counts = Counter(evidence.events)
    duplicate_effect_count = sum(
        (
            max(0, evidence.worker_calls - current_iteration),
            max(0, evidence.worker_write_calls - evidence.worker_calls),
            max(
                0,
                evidence.reviewer_calls
                - event_counts["dispatch_review"],
            ),
            max(
                0,
                evidence.verification_calls
                - event_counts["run_verification"],
            ),
            max(
                0,
                evidence.child_run_counts.get("brief", 0)
                - event_counts["prepare_run"],
            ),
            max(
                0,
                evidence.child_run_counts.get("reflect", 0)
                - event_counts["run_reflect"],
            ),
            max(
                0,
                evidence.child_run_counts.get("review", 0)
                - event_counts["dispatch_review"],
            ),
        )
    )
    success_semantics = not (
        state["status"] == "success"
        and (
            iteration["verification_status"] == "failed"
            or iteration["verification_failed_count"] > 0
            or iteration["risk_gate_recommendation"] == "human-review"
            or iteration["verdict"] != "approve"
            or not artifact_integrity.valid
            or not evidence_freshness.fresh
        )
    )
    return {
        "terminal_status": state["status"],
        "current_step": state["current_step"],
        "current_iteration": current_iteration,
        "success_semantics": success_semantics,
        "recommendation": _final_report_conclusion(evidence.run_dir),
        "iteration": normalized_iteration,
        "human_required": state["status"] == "needs_human",
        "human_reason": (
            state["current_step"] if state["status"] == "needs_human" else None
        ),
        "required_artifact_types": sorted(relative_files),
        "required_artifact_schema": _json_artifact_schema(evidence.run_dir),
        "artifact_integrity": {
            "valid": artifact_integrity.valid,
            "issues": artifact_integrity.issues,
            "review_verdict_count": len(
                artifact_integrity.review_verdicts
            ),
            "verification_result_count": len(
                artifact_integrity.verification_results
            ),
            "risk_gate_result_count": len(
                artifact_integrity.risk_gate_results
            ),
        },
        "evidence_freshness": {
            "fresh": evidence_freshness.fresh,
            "issues": evidence_freshness.issues,
        },
        "workspace_diff_hash": hashlib.sha256(diff).hexdigest(),
        "worker_calls": evidence.worker_calls,
        "worker_write_calls": evidence.worker_write_calls,
        "reviewer_calls": evidence.reviewer_calls,
        "verification_calls": evidence.verification_calls,
        "external_effect_count": (
            evidence.worker_calls
            + evidence.worker_write_calls
            + evidence.reviewer_calls
            + evidence.verification_calls
        ),
        "duplicate_effect_count": duplicate_effect_count,
        "business_write_counts": evidence.business_write_counts,
        "child_run_counts": evidence.child_run_counts,
        "step_events": evidence.events,
        "terminal_trace": {
            "event": trace[-1]["event"],
            "status": trace[-1]["status"],
        },
        "eval_failures": [
            item for item in state["eval_results"] if item.startswith("FAIL:")
        ],
    }


def _json_artifact_schema(run_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for path in sorted(run_dir.rglob("*.json")):
        relative = path.relative_to(run_dir).as_posix()
        if _is_graph_control_artifact(relative):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if relative.endswith("/executions/worker/execution.json"):
            for field in (
                "engine",
                "graph_schema_version",
                "step_id",
                "attempt_id",
                "idempotency_key",
                "replay_class",
                "runner_identity",
                "base_head",
                "before_workspace_fingerprint",
                "policy_snapshot_sha256",
                "input_fingerprint",
            ):
                payload.pop(field, None)
        result[relative] = _json_schema_shape(payload)
    return result


def _json_schema_shape(value: object) -> object:
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                (key, _json_schema_shape(item))
                for key, item in sorted(value.items())
            ),
        )
    if isinstance(value, list):
        unique_shapes = {
            repr(_json_schema_shape(item)): _json_schema_shape(item)
            for item in value
        }
        return (
            "array",
            tuple(unique_shapes[key] for key in sorted(unique_shapes)),
        )
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _business_write_identity(
    path: Path,
    workspace: Path,
    repo: Path,
) -> str | None:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(repo.resolve())
    except ValueError:
        pass
    else:
        return f"repo/{relative.as_posix()}"

    try:
        relative = resolved.relative_to(workspace.resolve())
    except ValueError:
        return None
    parts = list(relative.parts)
    if tuple(parts[:3]) == (
        ".tmp",
        "vega",
        "graph-operation-leases",
    ):
        # Graph operation lease 是引擎控制面，不属于 linear/graph 业务写入语义。
        return None
    if len(parts) >= 3 and parts[0] == "runs":
        run_relative = Path(*parts[2:]).as_posix()
        if _is_graph_control_artifact(
            run_relative
        ) or parts[-1].startswith((".tmp-", ".aw-")):
            return None
        parts[1] = f"<{_run_kind_from_id(parts[1])}>"
    if parts:
        parts[-1] = re.sub(
            r"^\.(.+)\.\d+\.[0-9a-f]+\.tmp$",
            r".\1.tmp",
            parts[-1],
        )
    return "workspace/" + Path(*parts).as_posix()


def _is_graph_control_artifact(relative: str) -> bool:
    if relative.startswith(
        (
            "graph/",
            "step-results/",
        )
    ):
        return True
    return bool(
        re.fullmatch(
            r"iterations/\d+/(workspace-(?:before|after)-worker\.json|"
            r"executions/worker/attempt\.json)",
            relative,
        )
    )


def _child_run_counts(
    workspace: Path,
    loop_run: Path,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in workspace.joinpath("runs").iterdir():
        if path == loop_run:
            continue
        counts[_run_kind_from_id(path.name)] += 1
    return dict(sorted(counts.items()))


def _run_kind_from_id(run_id: str) -> str:
    # create_run_dir 遇到同秒冲突会追加 -02/-03，分类仍应保留原始 run 类型。
    match = re.search(
        r"-(loop|brief|reflect|review)(?:-\d{2,})?$",
        run_id,
    )
    if match:
        return match.group(1)
    return "other"


@pytest.mark.requires_langgraph
def test_graph_assist_run_is_independent_and_stops_safely(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo, high_risk_readme=False)

    run_dir = LoopAutomationRuntime(tmp_path / "workspace").start(
        BriefInput(
            mode="bug",
            text="assist graph fixture",
            source="test",
            repo_path=str(repo),
        ),
        "assist",
        engine="langgraph",
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["engine"] == "langgraph"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "waiting_for_worker"
    graph_state = json.loads(
        run_dir.joinpath(GRAPH_STATE_ARTIFACT).read_text(encoding="utf-8")
    )
    assert validate_graph_state(run_dir, graph_state)["terminal_ref"] == "eval.md"
    assert not run_dir.joinpath("iterations").exists()


@pytest.mark.requires_langgraph
def test_five_round_linear_and_graph_programs_remain_equivalent(
    tmp_path: Path,
) -> None:
    assert _run_kind_from_id("20260721-000000-reflect-02") == "reflect"
    assert _run_kind_from_id("20260721-000000-reflect-extra") == "other"
    case = CASES[0]
    linear = _run_case(
        tmp_path / "multi-round",
        "linear",
        case,
        max_iterations=5,
        verify=False,
        reviewer_verdict="request_changes",
    )
    graph = _run_case(
        tmp_path / "multi-round",
        "langgraph",
        case,
        max_iterations=5,
        verify=False,
        reviewer_verdict="request_changes",
    )

    linear_snapshot = _parity_snapshot(linear)
    graph_snapshot = _parity_snapshot(graph)
    assert linear_snapshot == graph_snapshot
    assert graph_snapshot["duplicate_effect_count"] == 0
    assert graph_snapshot["external_effect_count"] == 15
    assert graph_snapshot["child_run_counts"] == {
        "brief": 1,
        "reflect": 5,
        "review": 5,
    }
    state = json.loads(
        graph.run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "done"
    assert state["current_iteration"] == 5
    assert graph.worker_calls == 5
    assert graph.worker_write_calls == 5
    assert graph.reviewer_calls == 5
    assert graph.verification_calls == 0
    assert len(graph.events) == 32


@pytest.mark.requires_langgraph
def test_graph_state_write_failure_revokes_untrusted_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, high_risk_readme=False)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker(),
        reviewer_runner=CountingReviewer("success"),
    )
    monkeypatch.setattr(
        loop_graph_runtime,
        "write_graph_state",
        lambda *_: (_ for _ in ()).throw(OSError("graph write blocked")),
    )

    with pytest.raises(OSError, match="graph write blocked"):
        runtime.start(
            BriefInput(
                mode="bug",
                text="graph failure quarantine fixture",
                source="test",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=1,
            verify=False,
            engine="langgraph",
        )

    loop_runs = [
        path
        for path in workspace.joinpath("runs").iterdir()
        if path.name.endswith("-loop")
    ]
    assert len(loop_runs) == 1
    run_dir = loop_runs[0]
    state = json.loads(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    trace = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert state["status"] == "needs_human"
    assert state["current_step"] == "graph_evidence_failed"
    assert any(
        item.startswith("FAIL: LangGraph 终态证据")
        for item in state["eval_results"]
    )
    assert "graph-failure-report.md" in state["artifacts"]
    assert run_dir.joinpath("graph-failure-report.md").is_file()
    assert not run_dir.joinpath(GRAPH_STATE_ARTIFACT).exists()
    run_finished = [
        item for item in trace if item["event"] == "run_finished"
    ]
    assert len(run_finished) == 1
    assert run_finished[0]["status"] == "success"
    assert trace[-2]["event"] == "run_terminal_state_revoked"
    assert trace[-2]["reason"] == "graph_evidence_failed"
    assert trace[-1]["event"] == "run_terminal_revoked"
    assert trace[-1]["reason"] == "graph_evidence_failed"
    assert trace[-1]["previous_status"] == "success"
    assert trace[-1]["status"] == "needs_human"
    audit_results = run_loop_eval(
        run_dir,
        state["artifacts"],
        require_terminal=True,
    )
    assert not any(item.startswith("FAIL: trace") for item in audit_results)
    assert any("append-only 撤销事实明确撤销" in item for item in audit_results)
    assert state["eval_results"][:-1] == audit_results
    assert state["eval_results"][-1].startswith(
        "FAIL: LangGraph 终态证据"
    )
    status = run_status_payload(workspace, run_dir.name)
    next_steps = "\n".join(status["next_steps"])
    assert "graph-failure-report.md" in next_steps
    assert "graph/graph-state.json" not in next_steps
    assert any(
        path.endswith("graph-failure-report.md")
        for path in status["key_artifacts"]
    )


def test_loop_eval_accepts_legacy_single_terminal_revocation_event(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "legacy-revocation"
    run_dir.mkdir()
    run_dir.joinpath("trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "run_finished",
                        "status": "success",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event": "run_terminal_revoked",
                        "reason": "graph_evidence_failed",
                        "previous_status": "success",
                        "status": "needs_human",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    results, status = loop_runtime._loop_trace_checks(
        run_dir,
        "langgraph",
    )

    assert status == "needs_human"
    assert not any(item.startswith("FAIL: trace") for item in results)
    assert any("append-only 撤销事实明确撤销" in item for item in results)


def test_loop_eval_rejects_mismatched_terminal_revocation_pair(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "mismatched-revocation"
    run_dir.mkdir()
    run_dir.joinpath("trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "run_finished",
                        "status": "success",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event": "run_terminal_state_revoked",
                        "reason": "graph_evidence_failed",
                        "previous_status": "success",
                        "status": "needs_human",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "event": "run_terminal_revoked",
                        "reason": "checkpoint_validation_failed",
                        "previous_status": "success",
                        "status": "needs_human",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    results, status = loop_runtime._loop_trace_checks(
        run_dir,
        "langgraph",
    )

    assert status is None
    assert any("run_finished 必须是 trace.jsonl 最后一条事件" in item for item in results)


@pytest.mark.requires_langgraph
def test_double_write_failure_never_consumes_persisted_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_git_repo(repo, high_risk_readme=False)
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=CountingWorker(),
        reviewer_runner=CountingReviewer("success"),
    )
    original_save = LoopAutomationState.save

    def fail_revocation_save(
        state: LoopAutomationState,
        path: Path,
    ) -> None:
        if (
            state.engine == "langgraph"
            and state.status == "needs_human"
            and state.current_step == "graph_evidence_failed"
        ):
            raise OSError("revocation state write blocked")
        original_save(state, path)

    monkeypatch.setattr(
        loop_graph_runtime,
        "write_graph_state",
        lambda *_: (_ for _ in ()).throw(OSError("graph write blocked")),
    )
    monkeypatch.setattr(LoopAutomationState, "save", fail_revocation_save)

    with pytest.raises(RuntimeError, match="无法撤销不可信 success"):
        runtime.start(
            BriefInput(
                mode="bug",
                text="double graph write failure fixture",
                source="test",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=1,
            verify=False,
            engine="langgraph",
        )

    loop_runs = [
        path
        for path in workspace.joinpath("runs").iterdir()
        if path.name.endswith("-loop")
    ]
    assert len(loop_runs) == 1
    run_dir = loop_runs[0]
    state = json.loads(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    trace = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert state["status"] == "success"
    assert not run_dir.joinpath(GRAPH_STATE_ARTIFACT).exists()
    assert any(
        item["event"] == "run_finished" and item["status"] == "success"
        for item in trace
    )
    assert not any(
        item["event"] == "run_terminal_revoked"
        for item in trace
    )
    assert not any(
        item["event"] == "run_terminal_state_revoked"
        for item in trace
    )

    before = _run_tree_bytes(run_dir)
    with pytest.raises(ValueError, match="Graph State"):
        run_status_payload(workspace, run_dir.name)
    audit_results = run_loop_eval(
        run_dir,
        state["artifacts"],
        require_terminal=True,
    )
    integrity = validate_loop_artifact_integrity(
        workspace,
        repo,
        run_dir,
    )
    freshness = validate_loop_evidence_freshness(
        workspace,
        repo,
        run_dir,
    )
    assert any(
        item.startswith("FAIL: LangGraph success")
        and "Graph State" in item
        for item in audit_results
    )
    assert integrity.valid is False
    assert "loop_graph_state_untrusted" in integrity.issues
    assert freshness.fresh is False
    assert "loop_graph_state_untrusted" in freshness.issues
    assert _run_tree_bytes(run_dir) == before


@pytest.mark.requires_langgraph
@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "malformed",
        "identity_mismatch",
        "review_results_list",
        "boolean_schema_version",
        "duplicate_key",
    ],
)
def test_graph_success_consumers_reject_untrusted_graph_state(
    tmp_path: Path,
    corruption: str,
) -> None:
    evidence = _run_case(
        tmp_path / corruption,
        "langgraph",
        CASES[0],
    )
    run_dir = evidence.run_dir
    graph_path = run_dir / GRAPH_STATE_ARTIFACT
    if corruption == "missing":
        graph_path.unlink()
    elif corruption == "malformed":
        graph_path.write_text(
            '{"schema_version": 1',
            encoding="utf-8",
            newline="\n",
        )
    elif corruption == "duplicate_key":
        raw = graph_path.read_text(encoding="utf-8")
        graph_path.write_text(
            raw.replace(
                '  "run_id": ',
                '  "run_id": "shadow-run",\n  "run_id": ',
                1,
            ),
            encoding="utf-8",
            newline="\n",
        )
    else:
        graph_state = json.loads(graph_path.read_text(encoding="utf-8"))
        if corruption == "identity_mismatch":
            graph_state["run_id"] = "other-run"
        elif corruption == "review_results_list":
            graph_state["review_results"] = []
        else:
            graph_state["schema_version"] = True
        graph_path.write_text(
            json.dumps(graph_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    state = json.loads(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    before = _run_tree_bytes(run_dir)

    with pytest.raises(ValueError, match="Graph State"):
        run_status_payload(run_dir.parent.parent, run_dir.name)
    audit_results = run_loop_eval(
        run_dir,
        state["artifacts"],
        require_terminal=True,
    )
    integrity = validate_loop_artifact_integrity(
        run_dir.parent.parent,
        evidence.repo,
        run_dir,
    )
    freshness = validate_loop_evidence_freshness(
        run_dir.parent.parent,
        evidence.repo,
        run_dir,
    )

    assert any(
        item.startswith("FAIL: LangGraph success")
        and "Graph State" in item
        for item in audit_results
    )
    assert integrity.valid is False
    assert "loop_graph_state_untrusted" in integrity.issues
    assert freshness.fresh is False
    assert "loop_graph_state_untrusted" in freshness.issues
    assert _run_tree_bytes(run_dir) == before


@pytest.mark.requires_langgraph
def test_valid_graph_success_remains_consumable(tmp_path: Path) -> None:
    evidence = _run_case(
        tmp_path / "valid",
        "langgraph",
        CASES[0],
    )
    state = json.loads(
        evidence.run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    before = _run_tree_bytes(evidence.run_dir)

    status = run_status_payload(
        evidence.run_dir.parent.parent,
        evidence.run_dir.name,
    )
    audit_results = run_loop_eval(
        evidence.run_dir,
        state["artifacts"],
        require_terminal=True,
    )
    integrity = validate_loop_artifact_integrity(
        evidence.run_dir.parent.parent,
        evidence.repo,
        evidence.run_dir,
    )
    freshness = validate_loop_evidence_freshness(
        evidence.run_dir.parent.parent,
        evidence.repo,
        evidence.run_dir,
    )

    assert status["status"] == "success"
    assert "PASS: LangGraph success 的 Graph State 终态证据可信" in audit_results
    assert not any(
        item.startswith("FAIL: LangGraph success")
        for item in audit_results
    )
    assert integrity.valid is True
    assert freshness.fresh is True
    assert _run_tree_bytes(evidence.run_dir) == before


@pytest.mark.requires_langgraph
def test_high_risk_linear_terminal_and_graph_interrupt_preserve_pre_hitl_effects(
    tmp_path: Path,
) -> None:
    case = RISK_HUMAN_REVIEW_CASE
    linear = _run_case(tmp_path / case.name, "linear", case)
    graph = _run_case(tmp_path / case.name, "langgraph", case)

    linear_state = json.loads(
        linear.run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    graph_state = json.loads(
        graph.run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    graph_control_state = validate_graph_state(
        graph.run_dir,
        json.loads(
            graph.run_dir.joinpath(GRAPH_STATE_ARTIFACT).read_text(
                encoding="utf-8"
            )
        ),
    )

    assert linear_state["status"] == "needs_human"
    assert linear_state["current_step"] == "risk_gate_needs_human"
    assert len(linear_state["iterations"]) == 1
    assert graph_state["status"] == "running"
    assert graph_state["current_step"] == "human_decision"
    assert graph_state["current_iteration"] == 1
    assert graph_state["iterations"] == []

    pending_id = graph_control_state["pending_human_decision_id"]
    assert pending_id is not None
    pending = read_pending_decision(graph.run_dir, pending_id)
    assert pending.iteration == 1
    assert pending.verification_status == "passed"
    assert pending.verification_failed_count == 0

    common_events = case.expected_events[:7]
    assert tuple(graph.events) == common_events
    assert tuple(linear.events[:7]) == common_events
    assert tuple(linear.events[7:]) == (
        "request_human_decision",
        "finalize_run",
    )
    assert linear.worker_calls == graph.worker_calls == 1
    assert linear.worker_write_calls == graph.worker_write_calls == 1
    assert linear.verification_calls == graph.verification_calls == 1
    assert linear.reviewer_calls == graph.reviewer_calls == 0

    linear_diff = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "HEAD"],
        cwd=linear.repo,
        check=True,
        capture_output=True,
    ).stdout
    graph_diff = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "HEAD"],
        cwd=graph.repo,
        check=True,
        capture_output=True,
    ).stdout
    assert hashlib.sha256(linear_diff).hexdigest() == hashlib.sha256(
        graph_diff
    ).hexdigest()

    linear_iteration_files = {
        path.relative_to(linear.run_dir / "iterations" / "01").as_posix()
        for path in (linear.run_dir / "iterations" / "01").rglob("*")
        if path.is_file()
        and not _is_graph_control_artifact(
            f"iterations/01/{path.relative_to(linear.run_dir / 'iterations' / '01').as_posix()}"
        )
    }
    graph_iteration_files = {
        path.relative_to(graph.run_dir / "iterations" / "01").as_posix()
        for path in (graph.run_dir / "iterations" / "01").rglob("*")
        if path.is_file()
        and not _is_graph_control_artifact(
            f"iterations/01/{path.relative_to(graph.run_dir / 'iterations' / '01').as_posix()}"
        )
    }
    assert graph_iteration_files
    assert graph_iteration_files <= linear_iteration_files
    assert linear_iteration_files - graph_iteration_files == {"fix-prompt.md"}

    linear_reflect_run = linear_state["iterations"][0]["reflect_run"]
    assert isinstance(linear_reflect_run, str)
    assert (
        linear.run_dir.parent
        .joinpath(linear_reflect_run, "full-diff.patch")
        .read_bytes()
        == graph.run_dir.parent
        .joinpath(pending.reflect_run_id, "full-diff.patch")
        .read_bytes()
    )


@pytest.mark.requires_langgraph
@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_linear_and_graph_have_semantic_parity(
    tmp_path: Path,
    case: CaseSpec,
) -> None:
    linear = _run_case(tmp_path / case.name, "linear", case)
    graph = _run_case(tmp_path / case.name, "langgraph", case)

    linear_snapshot = _parity_snapshot(linear)
    graph_snapshot = _parity_snapshot(graph)

    assert linear_snapshot == graph_snapshot
    assert graph_snapshot["terminal_status"] == case.expected_status
    assert graph_snapshot["current_step"] == case.expected_step
    assert graph_snapshot["success_semantics"] is True
    assert graph_snapshot["recommendation"]
    assert graph_snapshot["duplicate_effect_count"] == 0
    assert graph_snapshot["required_artifact_schema"]
    artifact_integrity = graph_snapshot["artifact_integrity"]
    assert isinstance(artifact_integrity, dict)
    assert artifact_integrity["valid"] is True, artifact_integrity["issues"]
    evidence_freshness = graph_snapshot["evidence_freshness"]
    assert isinstance(evidence_freshness, dict)
    assert evidence_freshness["fresh"] is (
        case.name in {"success", "verification_failure"}
    )
    assert graph_snapshot["external_effect_count"] == (
        3 + case.expected_reviewer_calls
    )
    assert graph_snapshot["business_write_counts"]
    assert graph_snapshot["child_run_counts"]["brief"] == 1
    iteration = graph_snapshot["iteration"]
    assert isinstance(iteration, dict)
    assert iteration["risk_gate_risk"] == case.risk
    assert iteration["risk_gate_recommendation"] == case.recommendation
    assert graph.worker_calls == 1
    assert graph.reviewer_calls == case.expected_reviewer_calls
    assert graph.verification_calls == 1
    assert tuple(graph.events) == case.expected_events
    assert graph.run_dir.joinpath(GRAPH_STATE_ARTIFACT).is_file()
    graph_state = json.loads(
        graph.run_dir.joinpath(GRAPH_STATE_ARTIFACT).read_text(encoding="utf-8")
    )
    validate_graph_state(graph.run_dir, graph_state)
    serialized = json.dumps(graph_state, ensure_ascii=False)
    checkpoint_blobs = _checkpoint_payload_blobs(graph.run_dir)
    assert checkpoint_blobs
    assert max(map(len, checkpoint_blobs)) < 64 * 1024
    serialized_checkpoint = b"\n".join(checkpoint_blobs).lower()
    for canary in (
        "TASK_PROMPT_CANARY_GATE2",
        "DIFF_CANARY_GATE2",
        "WORKER_OUTPUT_CANARY_GATE2",
        "VERIFICATION_LOG_CANARY_GATE2",
        "REVIEWER_PRIVATE_CANARY_GATE2",
        "REVIEWER_ERROR_CANARY_GATE2",
    ):
        assert canary not in serialized
        assert canary.lower().encode("utf-8") not in serialized_checkpoint
    for forbidden in (
        b"authorization:",
        b"bearer ",
        b"api_key",
        b"api-key",
        b"cookie:",
    ):
        assert forbidden not in serialized_checkpoint
    graph_only_files = {
        path.name.lower()
        for path in graph.run_dir.rglob("*")
        if path.is_file()
    }
    assert validate_checkpoint_manifest(graph.run_dir).run_id == graph.run_dir.name
    assert not {
        name
        for name in graph_only_files
        if name.endswith("-wal")
        or name.endswith("-shm")
    }
