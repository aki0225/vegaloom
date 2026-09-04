from __future__ import annotations

from pathlib import Path

import pytest

import vega.agent_explain as explain_module
from vega.agent_contract import (
    AgentCheckpoint,
    AgentDecision,
    AgentPlan,
    AgentState,
    AgentWorkItem,
)
from vega.agent_explain import build_agent_explanation
from vega.agent_persistence import save_agent_checkpoint
from vega.provider_session import (
    PendingInteraction,
    ProviderSessionHandle,
    ProviderSessionState,
    save_provider_sessions,
)


def test_evidence_override_precedes_provider_and_recorded_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="acting", active=True)
    save_provider_sessions(
        run_dir,
        ProviderSessionState(
            run_id=run_dir.name,
            handles={"worker": _provider_handle()},
            interactions=[_interaction()],
        ),
    )
    _stub_status(
        monkeypatch,
        state,
        effective_phase="needs_human",
        integrity_warning="当前 Workspace 与最近证据不一致。",
        workspace_current=False,
        evidence_health="stale",
    )

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "workspace.snapshot_stale"
    assert result.block_category == "evidence"
    assert result.source == "evidence"


def test_pending_interaction_precedes_active_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="acting", active=True)
    save_provider_sessions(
        run_dir,
        ProviderSessionState(
            run_id=run_dir.name,
            handles={"worker": _provider_handle()},
            interactions=[_interaction()],
        ),
    )
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "provider.interaction_required"
    assert result.block_category == "authorization"
    assert result.source == "provider"


def test_invalid_provider_state_is_warning_not_core_phase_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="planning")
    (run_dir / "provider-sessions.json").write_text(
        '{"kind":"provider_sessions","data":{},"digest":"invalid"}\n',
        encoding="utf-8",
    )
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "planning.required"
    assert result.phase == "planning"
    assert any("Provider 协调告警" in item for item in result.unknowns)


def test_completed_core_ignores_stale_pending_interaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="completed")
    save_provider_sessions(
        run_dir,
        ProviderSessionState(
            run_id=run_dir.name,
            handles={"worker": _provider_handle()},
            interactions=[_interaction()],
        ),
    )
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "run.completed.ready_to_commit"
    assert result.outcome == "completed"
    assert any("陈旧 Provider 请求" in item for item in result.unknowns)


@pytest.mark.parametrize(
    ("reason_code", "expected_code", "expected_category"),
    [
        ("side_effects.unknown", "side_effects.unknown", "authorization"),
        (None, "decision.legacy", None),
    ],
)
def test_checkpoint_decision_uses_stable_code_with_legacy_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason_code: str | None,
    expected_code: str,
    expected_category: str | None,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="needs_human", checkpoint_id="checkpoint-001")
    checkpoint = _write_checkpoint_decision(
        run_dir,
        state,
        reason_code=reason_code,
    )
    _stub_status(monkeypatch, state, allowed_actions=["human"])

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == expected_code
    assert result.block_category == expected_category
    assert result.reason == "需要人工确认外部副作用"
    assert f"checkpoints/{checkpoint.checkpoint_id}.json" in result.evidence_refs


def test_checkpoint_reason_is_used_when_no_decision_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="needs_human", checkpoint_id="checkpoint-001")
    checkpoint = AgentCheckpoint(
        checkpoint_id="checkpoint-001",
        run_id=state.run_id,
        state_version=state.state_version,
        reason="恢复现场仍需人工选择",
        status="blocked",
        phase="needs_human",
        current_work_item="W1",
        workspace_fingerprint="0" * 64,
        pending_actions=["human"],
    )
    save_agent_checkpoint(run_dir / "checkpoints/checkpoint-001.json", checkpoint)
    _stub_status(monkeypatch, state, allowed_actions=["human"])

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "checkpoint.needs_human"
    assert result.source == "checkpoint"
    assert result.reason == checkpoint.reason


def test_invalid_checkpoint_decision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="needs_human", checkpoint_id="checkpoint-001")
    _write_checkpoint_decision(run_dir, state, reason_code="side_effects.unknown")
    (run_dir / "decisions/decision-001.json").write_text(
        '{"invalid":true}\n',
        encoding="utf-8",
    )
    _stub_status(monkeypatch, state, allowed_actions=["human"])

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "evidence.decision_unverified"
    assert result.block_category == "evidence"
    assert result.outcome == "attention_required"


def _write_checkpoint_decision(
    run_dir: Path,
    state: AgentState,
    *,
    reason_code: str | None,
) -> AgentCheckpoint:
    decision = AgentDecision(
        decision_id="decision-001",
        observation_id="observation-001",
        allowed_actions=["human"],
        selected_action="human",
        reason_code=reason_code,
        reason="需要人工确认外部副作用",
        source="deterministic",
    )
    decision_path = run_dir / "decisions/decision-001.json"
    decision_path.parent.mkdir()
    decision_path.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
    checkpoint = AgentCheckpoint(
        checkpoint_id="checkpoint-001",
        run_id=state.run_id,
        state_version=state.state_version,
        reason=decision.reason,
        status="blocked",
        phase="needs_human",
        current_work_item="W1",
        workspace_fingerprint="0" * 64,
        pending_actions=["human"],
        evidence_refs=[
            "observations/observation-001.json",
            "decisions/decision-001.json",
        ],
    )
    save_agent_checkpoint(run_dir / "checkpoints/checkpoint-001.json", checkpoint)
    return checkpoint


def _stub_status(
    monkeypatch: pytest.MonkeyPatch,
    state: AgentState,
    **updates: object,
) -> None:
    payload: dict[str, object] = {
        "recorded_phase": state.phase,
        "effective_phase": state.phase,
        "integrity_warning": None,
        "workspace_current": True,
        "evidence_health": "passed",
        "allowed_actions": list(state.allowed_actions),
        "commit_recommended": False,
        "next_step": "查看当前状态",
    }
    payload.update(updates)
    monkeypatch.setattr(
        explain_module,
        "build_agent_status_payload",
        lambda *_args, **_kwargs: payload,
    )


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "agent-run"
    run_dir.mkdir(parents=True)
    return run_dir


def _state(
    *,
    phase: str,
    active: bool = False,
    checkpoint_id: str | None = None,
) -> AgentState:
    return AgentState(
        run_id="agent-run",
        task_id="task-01",
        repository_id="repo-01",
        phase=phase,
        current_work_item="W1",
        active_child_run="child-01" if active else None,
        active_operation_id="operation-01" if active else None,
        operation_started=active,
        latest_checkpoint_id=checkpoint_id,
        allowed_actions=["human"] if phase == "needs_human" else [],
        terminal_status=(
            "ready_to_commit" if phase == "completed" else None
        ),
    )


def _plan() -> AgentPlan:
    return AgentPlan(
        task_id="task-01",
        user_goal="修复问题",
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="修复示例",
                allowed_paths=["src/example.py"],
            )
        ],
    )


def _interaction() -> PendingInteraction:
    return PendingInteraction(
        interaction_id="interaction-001",
        role_key="worker",
        rpc_request_id="rpc-001",
        method="item/commandExecution/requestApproval",
        thread_id="thread-001",
        turn_id="turn-001",
        summary="执行项目测试",
    )


def _provider_handle() -> ProviderSessionHandle:
    return ProviderSessionHandle(
        provider="codex",
        role="worker",
        thread_id="thread-001",
        owner="vega",
        lifecycle="waiting_user",
        work_item_id="W1",
        permissions_verified=True,
        last_turn_id="turn-001",
    )
