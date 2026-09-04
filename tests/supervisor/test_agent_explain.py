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
from vega.agent_explain_codes import public_action_ids
from vega.agent_persistence import save_agent_checkpoint
from vega.agent_status_sources import load_status_decision_for_display
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
    assert result.safe_actions == [
        "provider.respond_decision",
        "provider.takeover",
        "run.stop",
    ]
    assert "请求 ID 为 interaction-001" in result.facts


@pytest.mark.parametrize(
    ("method", "expected_response"),
    [
        ("item/permissions/requestApproval", "provider.respond_input"),
        ("item/unknown/request", None),
    ],
)
def test_provider_interaction_actions_match_request_method(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    expected_response: str | None,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="acting", active=True)
    save_provider_sessions(
        run_dir,
        ProviderSessionState(
            run_id=run_dir.name,
            handles={"worker": _provider_handle()},
            interactions=[_interaction(method=method)],
        ),
    )
    _stub_status(monkeypatch, state)

    actions = build_agent_explanation(run_dir, state, _plan()).safe_actions

    assert actions[-2:] == ["provider.takeover", "run.stop"]
    assert [item for item in actions if item.startswith("provider.respond")] == (
        [expected_response] if expected_response is not None else []
    )


def test_active_worker_only_lists_supported_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="acting", active=True)
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "execution.worker_active"
    assert result.safe_actions == [
        "status.view",
        "provider.steer",
        "run.stop",
    ]


def test_finalizing_offers_idempotent_continue(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="finalizing")
    _stub_status(monkeypatch, state, allowed_actions=["finalize"])

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.safe_actions == ["run.continue", "run.stop"]


def test_awaiting_approval_keeps_explicit_revise(tmp_path: Path, monkeypatch) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="awaiting_approval")
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.safe_actions == [
        "plan.approve",
        "plan.revise",
        "run.stop",
    ]


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
        pending_actions=["replan", "human"],
    )
    save_agent_checkpoint(run_dir / "checkpoints/checkpoint-001.json", checkpoint)
    _stub_status(monkeypatch, state, allowed_actions=["replan", "human"])

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "checkpoint.needs_human"
    assert result.source == "checkpoint"
    assert result.reason == checkpoint.reason
    assert result.safe_actions == ["plan.revise", "human.review"]


def test_stopped_explanation_uses_checkpoint_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(phase="stopped", checkpoint_id="checkpoint-001")
    checkpoint = AgentCheckpoint(
        checkpoint_id="checkpoint-001",
        run_id=state.run_id,
        state_version=state.state_version,
        reason="用户决定暂不继续这个变更",
        status="safe",
        phase="stopped",
        current_work_item="W1",
        workspace_fingerprint="0" * 64,
        pending_actions=[],
    )
    save_agent_checkpoint(run_dir / "checkpoints/checkpoint-001.json", checkpoint)
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert result.reason_code == "run.stopped"
    assert result.source == "checkpoint"
    assert result.reason == checkpoint.reason
    assert "handoff.create" not in result.safe_actions
    assert "change.start" in result.safe_actions


def test_stopped_explanation_does_not_offer_duplicate_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run_dir(tmp_path)
    state = _state(
        phase="stopped",
        checkpoint_id="checkpoint-001",
    ).model_copy(update={"handoff_status": "handoff_ready"})
    checkpoint = AgentCheckpoint(
        checkpoint_id="checkpoint-001",
        run_id=state.run_id,
        state_version=state.state_version,
        reason="跨机交接已经生成",
        status="safe",
        phase="stopped",
        current_work_item="W1",
        workspace_fingerprint="0" * 64,
        pending_actions=[],
        evidence_refs=["planning-proposal.json", "planning-report.md"],
    )
    save_agent_checkpoint(run_dir / "checkpoints/checkpoint-001.json", checkpoint)
    for name in (
        "planning-request.json",
        "planning-proposal.json",
        "planning-report.md",
    ):
        (run_dir / name).write_text("{}\n", encoding="utf-8")
    _stub_status(monkeypatch, state)

    result = build_agent_explanation(run_dir, state, _plan())

    assert "handoff.create" not in result.safe_actions
    assert "change.start" in result.safe_actions


def test_public_action_ids_hide_internal_route_actions() -> None:
    assert public_action_ids(
        ["next", "repair", "finalize", "replan", "human", "unknown"]
    ) == [
        "run.continue",
        "plan.revise",
        "human.review",
    ]
    assert public_action_ids(
        ["replan", "human"],
        replan_action="run.continue",
    ) == ["run.continue", "human.review"]


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


def test_completed_checkpoint_accepts_bound_finalize_decision(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(tmp_path)
    decision = AgentDecision(
        decision_id="decision-finalize",
        observation_id="observation-finalize",
        allowed_actions=["finalize"],
        selected_action="finalize",
        reason_code="workflow.all_work_items_completed",
        reason="全部 Work Item 与门禁已完成",
        source="deterministic",
    )
    checkpoint = AgentCheckpoint(
        checkpoint_id="checkpoint-completed",
        run_id=run_dir.name,
        state_version=1,
        reason="已采用可信 Core Finish ready_to_commit 终态",
        status="safe",
        phase="completed",
        current_work_item="W1",
        workspace_fingerprint="0" * 64,
        pending_actions=[],
        evidence_refs=[
            "observations/observation-finalize.json",
            "decisions/decision-finalize.json",
        ],
    )

    loaded, issue = load_status_decision_for_display(
        run_dir,
        checkpoint,
        decisions=(decision,),
    )

    assert loaded == decision
    assert issue is None


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


def _interaction(
    *,
    method: str = "item/commandExecution/requestApproval",
) -> PendingInteraction:
    return PendingInteraction(
        interaction_id="interaction-001",
        role_key="worker",
        rpc_request_id="rpc-001",
        method=method,
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
