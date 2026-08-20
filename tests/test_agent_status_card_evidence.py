from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vega.agent_contract import (
    AgentObservation,
    AgentPlan,
    AgentState,
    AgentStatusCard,
    AgentWorkItem,
    canonical_digest,
)
from vega.agent_status_evidence import build_supervisor_evidence
from vega.agent_visibility import render_agent_status_card
from vega.execution_control import ExecutionLease
from vega.project_config import ScopeConfig, scope_policy_sha256
from vega.scope_gate import ScopeGateResult


def test_status_card_renders_plan_risk_notes_without_changing_risk_gate() -> None:
    card = AgentStatusCard(
        run_id="run-01",
        task_id="task-01",
        phase="ready",
        task_goal="修复问题",
        work_item_label="W1 / 1",
        worker_label="未启动",
        risk="not_run",
        next_step="等待执行",
        plan_risk_notes=["涉及并发状态，需要人工关注"],
    )

    rendered = render_agent_status_card(card)

    assert "## 计划风险提示" in rendered
    assert "涉及并发状态，需要人工关注" in rendered
    assert "不改变 Risk Gate 结果" in rendered
    assert "- Risk：尚未运行" in rendered

    without_notes = render_agent_status_card(card.model_copy(update={"plan_risk_notes": []}))
    assert "## 计划风险提示" not in without_notes


def test_supervisor_evidence_requires_bound_success_artifacts(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state()
    observation = _observation()
    observation = _write_valid_evidence(run_dir, observation, state)

    evidence = build_supervisor_evidence(run_dir, state, observation, _plan())

    assert [item.label for item in evidence] == [
        "Worker 执行",
        "计划范围（Worker 后）",
        "计划范围（Core 后）",
        "核心完成",
    ]
    assert [item.status for item in evidence] == [
        "passed",
        "passed",
        "passed",
        "passed",
    ]


def test_supervisor_evidence_marks_machine_observation_without_refs_unverified(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state()
    observation = _observation().model_copy(
        update={
            "authority": "fake_worker",
            "evidence_refs": [],
            "evidence_sha256": {},
            "verification": "passed",
            "risk": "passed",
            "review": "passed",
            "work_item_completed": True,
            "all_work_items_completed": True,
        }
    )

    evidence = build_supervisor_evidence(run_dir, state, observation, _plan())

    assert [item.label for item in evidence] == [
        "Worker 执行",
        "计划范围（Worker 后）",
        "计划范围（Core 后）",
        "核心完成",
    ]
    assert [item.status for item in evidence] == [
        "unverified",
        "unverified",
        "unverified",
        "unverified",
    ]
    assert all("evidence_refs" in item.detail for item in evidence)


@pytest.mark.parametrize(
    "tamper",
    [
        "remove_child",
        "wrong_child_binding",
        "wrong_scope_digest",
        "invalid_scope",
        "scope_hash",
        "execution_hash",
        "finish_hash",
    ],
)
def test_supervisor_evidence_never_guesses_tampered_artifacts(
    tmp_path: Path,
    tamper: str,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state()
    observation = _observation()
    observation = _write_valid_evidence(run_dir, observation, state)

    if tamper == "remove_child":
        (run_dir / _child_ref(observation)).unlink()
    elif tamper == "wrong_child_binding":
        path = run_dir / _child_ref(observation)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["operation_id"] = "different-operation"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif tamper == "wrong_scope_digest":
        original = _scope_ref(observation, "post-worker")
        wrong = run_dir / "plan-scope/post-worker-0000000000000000000000000000000000000000000000000000000000000000.json"
        wrong.parent.mkdir(parents=True, exist_ok=True)
        wrong.write_bytes((run_dir / original).read_bytes())
        (run_dir / original).unlink()
        observation = observation.model_copy(
            update={
                "evidence_refs": [
                    wrong.relative_to(run_dir).as_posix()
                    if ref == original
                    else ref
                    for ref in observation.evidence_refs
                ]
            }
        )
    else:
        child_dir = run_dir.parent / str(observation.child_run)
        if tamper == "invalid_scope":
            path = run_dir / _scope_ref(observation, "post-core")
            path.write_text('{"status":"success","violations":[]}', encoding="utf-8")
        elif tamper == "scope_hash":
            path = run_dir / _scope_ref(observation, "post-core")
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        elif tamper == "execution_hash":
            path = (
                child_dir
                / "executions"
                / "worker"
                / str(observation.operation_id)
                / "execution.json"
            )
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        else:
            path = child_dir / "finish-summary.json"
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    evidence = build_supervisor_evidence(run_dir, state, observation, _plan())

    assert all(item.status != "passed" for item in evidence if (
        (tamper in {"remove_child", "wrong_child_binding"} and item.label in {"Worker 执行", "核心完成"})
        or (tamper == "wrong_scope_digest" and item.label == "计划范围（Worker 后）")
        or (tamper == "invalid_scope" and item.label == "计划范围（Core 后）")
        or (tamper == "scope_hash" and item.label == "计划范围（Core 后）")
        or (tamper == "execution_hash" and item.label == "Worker 执行")
        or (tamper == "finish_hash" and item.label == "核心完成")
    ))


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "workspace" / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    return run_dir


def _state() -> AgentState:
    return AgentState(
        run_id="agent-run",
        task_id="task-01",
        repository_id="repo-01",
        phase="ready",
        current_work_item="W1",
    )


def _plan() -> AgentPlan:
    return AgentPlan(
        task_id="task-01",
        user_goal="修复问题",
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="修改示例文件",
                allowed_paths=["src/example.py"],
                forbidden_paths=["secrets/**"],
            )
        ],
    )


def _observation() -> AgentObservation:
    return AgentObservation(
        observation_id="observation-01",
        work_item_id="W1",
        child_run="child-01",
        operation_id="operation-01",
        machine_summary="已完成机器对账",
        workspace_fingerprint="0" * 64,
        changed_files=["src/example.py"],
        evidence_refs=[
            f"children/{canonical_digest({'child': 'child-01', 'operation_id': 'operation-01'})}.json",
            _scope_ref_for_ids("operation-01", "post-worker"),
            _scope_ref_for_ids("operation-01", "post-core"),
        ],
        authority="machine_reconcile",
    )


def _write_valid_evidence(
    run_dir: Path,
    observation: AgentObservation,
    state: AgentState,
) -> AgentObservation:
    assert observation.child_run is not None
    assert observation.operation_id is not None
    child_dir = run_dir.parent / observation.child_run
    execution_path = (
        child_dir
        / "executions"
        / "worker"
        / observation.operation_id
        / "execution.json"
    )
    execution_path.parent.mkdir(parents=True, exist_ok=True)
    execution_bytes = (
        ExecutionLease(
            run_id=observation.child_run,
            execution_id=observation.operation_id,
            step="worker",
            owner_pid=1,
            started_at="2026-08-20T00:00:00+00:00",
            last_heartbeat="2026-08-20T00:00:01+00:00",
            lease_expires_at="2026-08-20T00:00:11+00:00",
            deadline="2026-08-20T00:01:00+00:00",
            status="completed",
            returncode=0,
            finished_at="2026-08-20T00:00:01+00:00",
        ).model_dump_json(indent=2)
        + "\n"
    ).encode()
    execution_path.write_bytes(execution_bytes)
    finish_bytes = (
        json.dumps(
            {
                "run_id": observation.child_run,
                "finish_status": "ready_to_commit",
                "verification_passed": True,
                "latest_verification_failed": False,
                "artifact_integrity": {"valid": True},
                "evidence_freshness": {"fresh": True},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode()
    (child_dir / "finish-summary.json").write_bytes(finish_bytes)
    child_path = run_dir / _child_ref(observation)
    child_path.parent.mkdir(parents=True, exist_ok=True)
    child_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "child_binding_summary",
                "agent_run_id": state.run_id,
                "work_item_id": state.current_work_item,
                "child_run": observation.child_run,
                "operation_id": observation.operation_id,
                "worker": {
                    "runner_status": "success",
                    "lease_status": "completed",
                    "execution_artifact": (
                        f"executions/worker/{observation.operation_id}/execution.json"
                    ),
                    "execution_sha256": hashlib.sha256(execution_bytes).hexdigest(),
                },
                "core": {
                    "status": "success",
                    "current_step": "done",
                    "finish_status": "ready_to_commit",
                    "finish_sha256": hashlib.sha256(finish_bytes).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    scope = ScopeConfig(
        allowed_paths=["src/example.py"],
        forbidden_paths=["secrets/**"],
    )
    for stage in ("post-worker", "post-core"):
        path = run_dir / _scope_ref(observation, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                ScopeGateResult(
                    status="success",
                    iteration=1,
                    phase="pre_verification",
                    scope_policy_sha256=scope_policy_sha256(scope),
                    expected_head_sha="a" * 40,
                    current_head_sha="a" * 40,
                    allowed_paths=list(scope.allowed_paths),
                    forbidden_paths=list(scope.forbidden_paths),
                    changed_files=["src/example.py"],
                ).model_dump(mode="json")
            ),
            encoding="utf-8",
        )
    return observation.model_copy(
        update={
            "evidence_sha256": {
                ref: hashlib.sha256((run_dir / ref).read_bytes()).hexdigest()
                for ref in observation.evidence_refs
            }
        }
    )


def _child_ref(observation: AgentObservation) -> str:
    assert observation.child_run is not None
    assert observation.operation_id is not None
    return (
        "children/"
        f"{canonical_digest({'child': observation.child_run, 'operation_id': observation.operation_id})}.json"
    )


def _scope_ref(observation: AgentObservation, stage: str) -> str:
    assert observation.operation_id is not None
    return _scope_ref_for_ids(observation.operation_id, stage)


def _scope_ref_for_ids(operation_id: str, stage: str) -> str:
    return (
        f"plan-scope/{stage}-"
        f"{canonical_digest({'operation_id': operation_id, 'stage': stage})}.json"
    )
