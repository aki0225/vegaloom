from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


RUN_ID = "run-001"
ITERATION = 1
ZERO_HASH = "0" * 64
ONE_HASH = "1" * 64
TWO_HASH = "2" * 64
THREE_HASH = "3" * 64
FOUR_HASH = "4" * 64


def test_safe_twin_with_complete_current_evidence_is_sufficient(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "sufficient_for_merge"
    assert result.merge_evidence_sufficient is True
    assert result.issues == []


def test_dangerous_twin_missing_required_evidence_is_insufficient(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["evidence_refs"] = []
    payload["evidence"] = []

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "insufficient"
    assert result.merge_evidence_sufficient is False
    assert "threat:T-SCOPE-001:required_evidence_missing:scope_test" in result.issues


@pytest.mark.parametrize(
    "field_path",
    [
        ("claims", 0, "statement"),
        ("threats", 0, "trigger"),
        ("evidence", 0, "command"),
    ],
)
def test_missing_required_fields_fail_closed(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    _delete_nested(payload, field_path)

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


def test_forged_evidence_reference_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["evidence_refs"] = ["E-FORGED"]

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert "threat:T-SCOPE-001:evidence_ref_missing:E-FORGED" in result.issues


def test_duplicate_record_ids_fail_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"].append(copy.deepcopy(payload["evidence"][0]))

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "evidence_id_duplicate:E-001" in result.issues


def test_evidence_iteration_mismatch_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["iteration"] = 2

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "evidence:E-001:iteration_mismatch" in result.issues


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("head_sha", "b" * 40, "snapshot_head_mismatch"),
        ("staged_diff_sha256", "6" * 64, "snapshot_staged_diff_mismatch"),
        ("unstaged_diff_sha256", "7" * 64, "snapshot_unstaged_diff_mismatch"),
        ("review_snapshot_id", "8" * 64, "snapshot_review_id_mismatch"),
        (
            "project_policy_snapshot_sha256",
            "9" * 64,
            "snapshot_project_policy_mismatch",
        ),
        ("scope_policy_sha256", "a" * 64, "snapshot_scope_policy_mismatch"),
    ],
)
def test_snapshot_mismatch_fails_closed(
    tmp_path: Path,
    field: str,
    value: str,
    issue: str,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["snapshot"][field] = value

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert f"assurance_{issue}" in result.issues


def test_artifact_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["artifacts"][0]["sha256"] = "f" * 64

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "evidence:E-001:artifact_hash_mismatch:verification-result.json" in result.issues


def test_artifact_path_escape_is_rejected(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["artifacts"][0]["relative_path"] = "../outside.txt"

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


def test_legacy_artifact_is_readable_but_never_upgraded(tmp_path: Path) -> None:
    api = _assurance_api()
    payload = {
        "run_id": RUN_ID,
        "iteration": ITERATION,
        "status": "success",
    }

    result = api.evaluate_assurance_payload(
        payload,
        workspace=tmp_path,
        expected=_context(api),
    )

    assert result.legacy_artifact is True
    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert result.merge_evidence_sufficient is False
    assert result.issues == ["assurance_schema_version_missing"]


def test_llm_candidate_does_not_create_active_threat_coverage(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    threat = payload["threats"][0]
    threat["source"] = {
        "kind": "llm_candidate",
        "reference": "llm://candidate/threat-1",
    }
    threat["status"] = "candidate"
    payload["evidence"] = []

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "insufficient"
    assert result.decision_source == "deterministic_validator"
    assert "active_threat_missing" in result.issues


def test_llm_candidate_cannot_be_declared_active(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["source"] = {
        "kind": "llm_candidate",
        "reference": "llm://candidate/threat-1",
    }

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


def test_corrupt_artifact_writes_independent_fail_closed_result(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    input_path = tmp_path / "assurance-input.json"
    result_path = tmp_path / "assurance-result.json"
    input_path.write_text('{"schema_version":', encoding="utf-8")

    result = api.evaluate_assurance_artifact(
        input_path,
        workspace=tmp_path,
        expected=_context(api),
        result_path=result_path,
    )
    persisted = json.loads(result_path.read_text(encoding="utf-8"))

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert result.decision_source == "deterministic_validator"
    assert result.issues == ["assurance_artifact_invalid_json"]
    assert persisted["status"] == "insufficient"
    assert persisted["artifact_valid"] is False


def test_assurance_result_does_not_persist_sensitive_input(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    secret = "sk-test-assurance-secret-123456"
    payload["evidence"][0]["environment"]["api_key"] = secret
    input_path = tmp_path / "assurance-input.json"
    result_path = tmp_path / "assurance-result.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    api.evaluate_assurance_artifact(
        input_path,
        workspace=workspace,
        expected=_context(api),
        result_path=result_path,
    )

    assert secret not in result_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "verification_conclusion",
    ["failed", "unknown", "interrupted"],
)
def test_non_verified_conclusion_cannot_be_sufficient(
    tmp_path: Path,
    verification_conclusion: str,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["verification_conclusion"] = verification_conclusion

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "insufficient"
    assert result.merge_evidence_sufficient is False
    assert f"verification_conclusion:{verification_conclusion}" in result.issues


def test_residual_risk_requires_staged_rollout(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["residual_risks"] = ["需要在灰度环境观察真实租户规模。"]

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "requires_staged_rollout"
    assert result.merge_evidence_sufficient is False


def test_human_required_threat_cannot_be_auto_sufficient(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["human_required"] = True

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "human_required"
    assert result.merge_evidence_sufficient is False


def _assurance_api() -> ModuleType:
    return importlib.import_module("vega.assurance")


def _context(api: ModuleType) -> Any:
    return api.AssuranceContext.model_validate(
        {
            "run_id": RUN_ID,
            "iteration": ITERATION,
            "snapshot": _snapshot(),
        }
    )


def _valid_payload(api: ModuleType, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    del api
    workspace = tmp_path / "workspace"
    iteration_dir = workspace / "runs" / RUN_ID / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    artifact_path = iteration_dir / "verification-result.json"
    artifact_path.write_text('{"status":"passed"}\n', encoding="utf-8")
    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    snapshot = _snapshot()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "iteration": ITERATION,
        "snapshot": snapshot,
        "verification_conclusion": "verified",
        "claims": [
            {
                "schema_version": 1,
                "id": "C-001",
                "statement": "批量更新只能影响声明的租户范围。",
                "status": "accepted",
                "source": {
                    "kind": "user_requirement",
                    "reference": "task://acceptance/1",
                },
            }
        ],
        "threats": [
            {
                "schema_version": 1,
                "id": "T-SCOPE-001",
                "category": "data_scope",
                "source": {
                    "kind": "deterministic_detector",
                    "reference": "detector://scope/v1",
                },
                "status": "active",
                "trigger": "批量更新缺少租户过滤条件。",
                "affected_assets": ["tenant_data"],
                "claim_refs": ["C-001"],
                "invariant": "只能修改声明租户的数据。",
                "failure_mode": "cross_tenant_update",
                "impact": "high",
                "exposure": "medium",
                "blast_radius": "per_tenant",
                "reversibility": "medium",
                "detectability": "immediate",
                "uncertainty": "low",
                "trigger_evidence": ["diff://src/service.py"],
                "required_evidence": ["scope_test"],
                "evidence_refs": ["E-001"],
                "residual_risks": [],
                "human_required": False,
            }
        ],
        "evidence": [
            {
                "schema_version": 1,
                "id": "E-001",
                "kind": "scope_test",
                "producer": {
                    "runner": "pytest",
                    "version": "8.x",
                },
                "command": "python -m pytest tests/test_scope.py",
                "environment": {
                    "os": "windows",
                    "python": "3.12",
                },
                "run_id": RUN_ID,
                "iteration": ITERATION,
                "snapshot": snapshot,
                "input": {
                    "fixture": "tenant-a",
                },
                "oracle": {
                    "statement": "非目标租户的记录数必须保持不变。",
                },
                "result": {
                    "status": "passed",
                    "exit_code": 0,
                    "duration_seconds": 0.1,
                },
                "covers": ["T-SCOPE-001"],
                "artifacts": [
                    {
                        "run_id": RUN_ID,
                        "relative_path": "iterations/01/verification-result.json",
                        "sha256": artifact_hash,
                    }
                ],
                "limitations": [],
            }
        ],
    }
    return payload, workspace


def _snapshot() -> dict[str, str]:
    return {
        "head_sha": "a" * 40,
        "staged_diff_sha256": ZERO_HASH,
        "unstaged_diff_sha256": ONE_HASH,
        "review_snapshot_id": TWO_HASH,
        "project_policy_snapshot_sha256": THREE_HASH,
        "scope_policy_sha256": FOUR_HASH,
    }


def _delete_nested(payload: dict[str, Any], field_path: tuple[str | int, ...]) -> None:
    current: Any = payload
    for part in field_path[:-1]:
        current = current[part]
    del current[field_path[-1]]
