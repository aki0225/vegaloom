from __future__ import annotations

import json
from pathlib import Path

import pytest

from vega.agent_contract import AgentState
from vega.agent_operation import (
    bound_operation_kind,
    child_summary_ref,
    operation_ref,
    reserve_operation_identity,
)


def test_operation_refs_are_canonical_and_stable() -> None:
    assert operation_ref("operation-01") == operation_ref("operation-01")
    assert child_summary_ref("child-01", "operation-01") == child_summary_ref(
        "child-01",
        "operation-01",
    )
    assert child_summary_ref("child-01", "operation-01") != child_summary_ref(
        "child-02",
        "operation-01",
    )


def test_reserve_operation_identity_writes_one_bound_artifact(
    tmp_path: Path,
) -> None:
    state = _state()

    relative = reserve_operation_identity(
        tmp_path,
        state,
        child_run="child-01",
        operation_id="operation-01",
        operation_kind="verification_retry",
        details={"source_operation_id": "operation-source"},
    )

    payload = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
    assert relative == operation_ref("operation-01")
    assert payload == {
        "schema_version": 1,
        "authority": "agent_operation",
        "operation_kind": "verification_retry",
        "run_id": "agent-run",
        "state_version": 3,
        "work_item_id": "W1",
        "child_run": "child-01",
        "operation_id": "operation-01",
        "source_operation_id": "operation-source",
    }

    bound = state.model_copy(
        update={
            "phase": "observing",
            "active_child_run": "child-01",
            "active_operation_id": "operation-01",
            "operation_started": True,
        }
    )
    assert bound_operation_kind(tmp_path, bound) == "verification_retry"

    with pytest.raises(ValueError, match="operation_id 已在当前 Agent run 使用"):
        reserve_operation_identity(
            tmp_path,
            state,
            child_run="child-01",
            operation_id="operation-01",
        )


def test_operation_details_cannot_override_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不得覆盖身份字段"):
        reserve_operation_identity(
            tmp_path,
            _state(),
            child_run="child-01",
            operation_id="operation-01",
            details={"child_run": "forged-child"},
        )

    assert not (tmp_path / operation_ref("operation-01")).exists()


def test_bound_operation_kind_keeps_legacy_worker_compatibility(
    tmp_path: Path,
) -> None:
    state = _state().model_copy(
        update={
            "phase": "acting",
            "active_child_run": "legacy-child",
            "active_operation_id": "legacy-operation",
            "operation_started": True,
        }
    )
    path = tmp_path / operation_ref("legacy-operation")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": state.run_id,
                "state_version": state.state_version,
                "work_item_id": state.current_work_item,
                "child_run": state.active_child_run,
                "operation_id": state.active_operation_id,
            }
        ),
        encoding="utf-8",
    )

    assert bound_operation_kind(tmp_path, state) == "worker"


def _state() -> AgentState:
    return AgentState(
        run_id="agent-run",
        task_id="task-01",
        repository_id="repo-01",
        phase="ready",
        state_version=3,
        current_work_item="W1",
    )
