from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vega.experimental.ma2b.delegation import (
    MAX_DELEGATION_INPUT_BYTES,
    DelegationValidationContext,
    PlanContract,
    evaluate_delegation_file,
    evaluate_delegation_payload,
    write_delegation_readiness_result,
)


TASK_HASH = "1" * 64
INPUT_HASH = "2" * 64
PARENT_HASH = "3" * 64


def test_valid_contract_is_budget_eligible_and_deterministic() -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["verification"]["commands"].append(
        "cmd /d /c echo verification"
    )
    context = _context(
        allowed_verification_commands=[
            "python -m pytest tests/test_example.py",
            "cmd /d /c echo verification",
        ]
    )

    first = evaluate_delegation_payload(payload, expected=context)
    second = evaluate_delegation_payload(copy.deepcopy(payload), expected=context)

    assert first.status == "budget_eligible"
    assert first.contract_valid is True
    assert first.binding_valid is True
    assert first.issue_codes == []
    assert first.checked_slice_ids == ["S-IMPLEMENT"]
    assert first == second
    assert first.plan_sha256 is not None
    assert first.input_sha256 == first.plan_sha256


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("plan_revision",), "1"),
        (("risk", "human_required"), 0),
        (("budget", "max_changed_files"), 1.0),
    ],
)
def test_contract_uses_strict_types(
    field_path: tuple[str, ...],
    value: object,
) -> None:
    payload = _plan_payload()
    _set_nested(payload, field_path, value)

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is False
    assert result.binding_valid is False
    assert result.issue_codes == ["contract_schema_invalid"]


def test_contract_cannot_self_declare_route() -> None:
    payload = _plan_payload()
    payload["route_eligibility"] = "budget_eligible"

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is False
    assert result.issue_codes == ["contract_schema_invalid"]


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "C:/private/file.py",  # repo-path-policy: allow-test-fixture
        "../outside.py",
        "src\\vega\\example.py",
        "src/vega/*.py",
        ".git/config",
        "src//example.py",
        ".env",
        "secrets/id_rsa",
    ],
)
def test_contract_rejects_unsafe_or_ambiguous_paths(unsafe_path: str) -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["allowed_write_paths"] = [unsafe_path]

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


def test_initial_revision_rejects_parent_fields() -> None:
    payload = _plan_payload()
    payload["parent_plan_ref"] = _artifact("plans/plan-v0.json", PARENT_HASH)

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


def test_later_revision_requires_parent_and_change_reason() -> None:
    payload = _plan_payload()
    payload["plan_revision"] = 2

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


def test_later_revision_binds_available_parent_artifact() -> None:
    payload = _revision_two_payload()
    context = _context(
        available_artifacts=[
            _artifact("inputs/design.json", INPUT_HASH),
            _artifact("plans/plan-v1.json", PARENT_HASH),
        ]
    )

    result = evaluate_delegation_payload(payload, expected=context)

    assert result.status == "budget_eligible"
    assert result.plan_revision == 2
    assert result.issue_codes == []


def test_later_revision_fails_closed_when_parent_artifact_is_stale() -> None:
    payload = _revision_two_payload()

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is True
    assert result.binding_valid is False
    assert result.issue_codes == ["parent_plan_artifact_unavailable"]


def test_contract_rejects_unknown_dependency() -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["dependencies"] = ["S-MISSING"]

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


def test_contract_rejects_dependency_cycle() -> None:
    payload = _two_slice_payload()
    payload["task_dag"][0]["dependencies"] = ["S-TEST"]
    payload["task_dag"][1]["dependencies"] = ["S-IMPLEMENT"]

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


def test_contract_requires_every_acceptance_fact_to_be_covered() -> None:
    payload = _plan_payload()
    payload["goal"]["acceptance_facts"].append(
        {
            "fact_id": "A-SECOND",
            "statement": "第二个验收事实也必须有明确执行 slice。",
        }
    )

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "replacement", "issue_code"),
    [
        ("head_sha", "f" * 40, "snapshot_head_mismatch"),
        ("workspace_fingerprint", "f" * 64, "snapshot_workspace_mismatch"),
        ("project_policy_sha256", "f" * 64, "snapshot_project_policy_mismatch"),
        ("scope_policy_sha256", "f" * 64, "snapshot_scope_policy_mismatch"),
    ],
)
def test_snapshot_mismatch_fails_closed(
    field_name: str,
    replacement: str,
    issue_code: str,
) -> None:
    payload = _plan_payload()
    payload["baseline"][field_name] = replacement

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is True
    assert result.binding_valid is False
    assert result.issue_codes == [issue_code]


def test_task_identity_and_artifact_mismatch_fail_closed() -> None:
    payload = _plan_payload()
    payload["task_id"] = "TASK-OTHER"
    payload["task_ref"]["sha256"] = "f" * 64

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.binding_valid is False
    assert result.issue_codes == [
        "task_artifact_mismatch",
        "task_identity_mismatch",
    ]


def test_write_path_outside_compiled_scope_fails_closed() -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["allowed_write_paths"] = ["src/vega/other.py"]

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.binding_valid is False
    assert result.issue_codes == [
        "write_path_outside_compiled_scope:S-IMPLEMENT"
    ]


def test_read_path_outside_compiled_scope_fails_closed() -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["read_paths"] = [
        "src/vega/other.py",
        "tests/test_example.py",
    ]

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.binding_valid is False
    assert result.issue_codes == [
        "read_path_outside_compiled_scope:S-IMPLEMENT"
    ]


def test_unapproved_verification_command_fails_closed() -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["verification"]["commands"] = [
        "python -m pytest tests/test_other.py"
    ]

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.binding_valid is False
    assert result.issue_codes == [
        "verification_command_not_authorized:S-IMPLEMENT"
    ]


@pytest.mark.parametrize(
    "command",
    [
        "python C:/private/check.py",  # repo-path-policy: allow-test-fixture
        r"python \\server\share\check.py",  # repo-path-policy: allow-test-fixture
        "python /tmp/check.py",
    ],
)
def test_contract_rejects_verification_commands_with_local_paths(command: str) -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["verification"]["commands"] = [command]

    with pytest.raises(ValidationError):
        PlanContract.model_validate(payload)


def test_input_artifact_hash_mismatch_fails_closed() -> None:
    payload = _plan_payload()
    payload["task_dag"][0]["input_artifact_refs"][0]["sha256"] = "f" * 64

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.binding_valid is False
    assert result.issue_codes == ["input_artifact_unavailable:S-IMPLEMENT"]


def test_unresolved_decision_requires_human_without_invalidating_binding() -> None:
    payload = _plan_payload()
    payload["decisions"]["unresolved"] = ["是否保持旧接口兼容性仍未决定。"]

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is True
    assert result.binding_valid is True
    assert result.issue_codes == ["unresolved_decisions"]


def test_explicit_human_risk_requires_human() -> None:
    payload = _plan_payload()
    payload["risk"]["human_required"] = True

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "human_required"
    assert result.binding_valid is True
    assert result.issue_codes == ["risk_requires_human"]


def test_explicit_premium_risk_requires_premium_worker() -> None:
    payload = _plan_payload()
    payload["risk"]["premium_worker_required"] = True

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "premium_required"
    assert result.contract_valid is True
    assert result.binding_valid is True
    assert result.issue_codes == ["risk_requires_premium"]


@pytest.mark.parametrize(
    ("budget_field", "value", "issue_code"),
    [
        ("max_changed_files", 4, "changed_files_exceed_budget_limit"),
        ("max_diff_lines", 301, "diff_lines_exceed_budget_limit"),
        ("max_new_files", 2, "new_files_exceed_budget_limit"),
        ("context_limit_tokens", 60_001, "context_tokens_exceed_budget_limit"),
        (
            "worker_time_limit_seconds",
            901,
            "worker_time_exceeds_budget_limit",
        ),
        ("worker_token_limit", 30_001, "worker_tokens_exceed_budget_limit"),
    ],
)
def test_budget_thresholds_require_premium_worker(
    budget_field: str,
    value: int,
    issue_code: str,
) -> None:
    payload = _plan_payload()
    payload["budget"][budget_field] = value

    result = evaluate_delegation_payload(payload, expected=_context())

    assert result.status == "premium_required"
    assert result.issue_codes == [issue_code]


def test_shared_write_path_across_slices_requires_premium_worker() -> None:
    payload = _two_slice_payload(shared_write_path=True)
    context = _context(
        allowed_write_paths=["src/vega/example.py"],
        allowed_verification_commands=[
            "python -m pytest tests/test_example.py",
            "python -m pytest tests/test_example.py -q",
        ],
        budget_limits={
            **_budget_limits(),
            "max_slices": 2,
            "max_dependency_edges": 1,
        },
    )

    result = evaluate_delegation_payload(payload, expected=context)

    assert result.status == "premium_required"
    assert result.issue_codes == ["write_path_shared_across_slices"]


def test_structural_limits_require_premium_worker() -> None:
    payload = _two_slice_payload(shared_write_path=False)
    context = _context(
        allowed_write_paths=[
            "src/vega/example.py",
            "tests/test_example.py",
        ],
        allowed_verification_commands=[
            "python -m pytest tests/test_example.py",
            "python -m pytest tests/test_example.py -q",
        ],
    )

    result = evaluate_delegation_payload(payload, expected=context)

    assert result.status == "premium_required"
    assert result.issue_codes == [
        "dependency_count_exceeds_budget_limit",
        "slice_count_exceeds_budget_limit",
        "write_path_count_exceeds_budget_limit",
    ]


def test_invalid_json_file_fails_closed(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{invalid", encoding="utf-8")

    result = evaluate_delegation_file(plan_path, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is False
    assert result.issue_codes == ["delegation_artifact_invalid_json"]
    assert result.input_sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()


def test_oversized_file_fails_closed(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(b"x" * (MAX_DELEGATION_INPUT_BYTES + 1))

    result = evaluate_delegation_file(plan_path, expected=_context())

    assert result.status == "human_required"
    assert result.contract_valid is False
    assert result.issue_codes == ["delegation_artifact_too_large"]


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    result = evaluate_delegation_file(
        tmp_path / "missing.json",
        expected=_context(),
    )

    assert result.status == "human_required"
    assert result.contract_valid is False
    assert result.issue_codes == ["delegation_artifact_unreadable"]


def test_file_input_hash_binds_original_bytes(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = evaluate_delegation_file(plan_path, expected=_context())

    assert result.status == "budget_eligible"
    assert result.input_sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert result.plan_sha256 != result.input_sha256


def test_readiness_artifact_is_utf8_and_content_addressable(tmp_path: Path) -> None:
    result = evaluate_delegation_payload(_plan_payload(), expected=_context())
    artifact_path = tmp_path / "delegation-readiness.json"

    first_hash = write_delegation_readiness_result(artifact_path, result)
    first_bytes = artifact_path.read_bytes()
    second_hash = write_delegation_readiness_result(artifact_path, result)

    assert first_hash == hashlib.sha256(first_bytes).hexdigest()
    assert second_hash == first_hash
    assert not first_bytes.startswith(b"\xef\xbb\xbf")
    persisted = json.loads(first_bytes.decode("utf-8"))
    assert persisted["status"] == "budget_eligible"
    assert persisted["plan_id"] == "PLAN-DEMO"
    assert "route_eligibility" not in persisted


def test_context_rejects_multiple_hashes_for_same_artifact_path() -> None:
    with pytest.raises(ValidationError):
        _context(
            available_artifacts=[
                _artifact("inputs/design.json", INPUT_HASH),
                _artifact("inputs/design.json", "f" * 64),
            ]
        )


def _plan_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "plan_id": "PLAN-DEMO",
        "plan_revision": 1,
        "parent_plan_ref": None,
        "change_reason_code": None,
        "change_summary": None,
        "invalidated_slice_ids": [],
        "task_id": "TASK-001",
        "task_ref": _artifact("tasks/TASK-001.md", TASK_HASH),
        "baseline": _snapshot(),
        "goal": {
            "acceptance_facts": [
                {
                    "fact_id": "A-BEHAVIOR",
                    "statement": "示例行为必须通过确定性测试验证。",
                }
            ],
            "non_goals": ["不修改 CLI 默认行为。"],
        },
        "task_dag": [
            {
                "slice_id": "S-IMPLEMENT",
                "read_paths": [
                    "src/vega/example.py",
                    "tests/test_example.py",
                ],
                "allowed_write_paths": ["src/vega/example.py"],
                "dependencies": [],
                "preconditions": ["当前基线、任务与项目策略已经冻结。"],
                "expected_change": "实现示例行为且不扩大写入范围。",
                "acceptance_refs": ["A-BEHAVIOR"],
                "input_artifact_refs": [
                    _artifact("inputs/design.json", INPUT_HASH)
                ],
                "verification": {
                    "commands": ["python -m pytest tests/test_example.py"],
                    "oracle": {"kind": "all_commands_exit_zero"},
                },
                "failure_and_recovery": "验证失败时停止委派并保留当前 artifact。",
            }
        ],
        "decisions": {
            "resolved": ["示例接口保持现有参数语义。"],
            "unresolved": [],
        },
        "risk": {
            "threat_refs": [],
            "human_required": False,
            "premium_worker_required": False,
        },
        "budget": {
            "max_changed_files": 3,
            "max_diff_lines": 200,
            "max_new_files": 1,
            "context_limit_tokens": 50_000,
            "worker_time_limit_seconds": 900,
            "worker_token_limit": 30_000,
        },
    }


def _revision_two_payload() -> dict[str, object]:
    payload = _plan_payload()
    payload["plan_revision"] = 2
    payload["parent_plan_ref"] = _artifact("plans/plan-v1.json", PARENT_HASH)
    payload["change_reason_code"] = "interface_assumption_invalid"
    payload["change_summary"] = "公共接口与原假设不一致，重新绑定当前计划。"
    payload["invalidated_slice_ids"] = ["S-OLD"]
    return payload


def _two_slice_payload(*, shared_write_path: bool = False) -> dict[str, object]:
    payload = _plan_payload()
    second_write_path = (
        "src/vega/example.py" if shared_write_path else "tests/test_example.py"
    )
    payload["task_dag"].append(
        {
            "slice_id": "S-TEST",
            "read_paths": [
                "src/vega/example.py",
                "tests/test_example.py",
            ],
            "allowed_write_paths": [second_write_path],
            "dependencies": ["S-IMPLEMENT"],
            "preconditions": ["S-IMPLEMENT 已形成可读取的受控变更。"],
            "expected_change": "补充与实现绑定的测试。",
            "acceptance_refs": ["A-BEHAVIOR"],
            "input_artifact_refs": [],
            "verification": {
                "commands": ["python -m pytest tests/test_example.py -q"],
                "oracle": {"kind": "all_commands_exit_zero"},
            },
            "failure_and_recovery": "测试失败时保留首次失败并停止。",
        }
    )
    return payload


def _context(**updates: object) -> DelegationValidationContext:
    payload: dict[str, object] = {
        "schema_version": 1,
        "task_id": "TASK-001",
        "task_ref": _artifact("tasks/TASK-001.md", TASK_HASH),
        "baseline": _snapshot(),
        "allowed_read_paths": [
            "src/vega/example.py",
            "tests/test_example.py",
        ],
        "allowed_write_paths": ["src/vega/example.py"],
        "allowed_verification_commands": [
            "python -m pytest tests/test_example.py"
        ],
        "available_artifacts": [_artifact("inputs/design.json", INPUT_HASH)],
        "budget_limits": _budget_limits(),
    }
    payload.update(updates)
    return DelegationValidationContext.model_validate(payload)


def _budget_limits() -> dict[str, int]:
    return {
        "max_slices": 1,
        "max_dependency_edges": 0,
        "max_write_paths": 1,
        "max_changed_files": 3,
        "max_diff_lines": 300,
        "max_new_files": 1,
        "max_context_tokens": 60_000,
        "max_worker_time_seconds": 900,
        "max_worker_tokens": 30_000,
    }


def _snapshot() -> dict[str, str]:
    return {
        "head_sha": "a" * 40,
        "workspace_fingerprint": "b" * 64,
        "project_policy_sha256": "c" * 64,
        "scope_policy_sha256": "d" * 64,
    }


def _artifact(relative_path: str, sha256: str) -> dict[str, str]:
    return {
        "relative_path": relative_path,
        "sha256": sha256,
    }


def _set_nested(
    payload: dict[str, object],
    field_path: tuple[str, ...],
    value: object,
) -> None:
    current: object = payload
    for segment in field_path[:-1]:
        assert isinstance(current, dict)
        current = current[segment]
    assert isinstance(current, dict)
    current[field_path[-1]] = value
