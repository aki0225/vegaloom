from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


RUN_ID = "run-001"
ITERATION = 1
COMMAND = "python -m pytest tests/test_scope.py"
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

    assert result.artifact_valid is False
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


@pytest.mark.parametrize(
    ("collection", "expected_issue"),
    [
        ("claims", "claim_id_duplicate:C-001"),
        ("threats", "threat_id_duplicate:T-SCOPE-001"),
        ("evidence", "evidence_id_duplicate:E-001"),
    ],
)
def test_duplicate_record_ids_fail_closed(
    tmp_path: Path,
    collection: str,
    expected_issue: str,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload[collection].append(copy.deepcopy(payload[collection][0]))

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert expected_issue in result.issues


def test_unknown_field_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["unexpected"] = True

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


@pytest.mark.parametrize("schema_version", [2, True])
def test_unsupported_schema_version_fails_closed(
    tmp_path: Path,
    schema_version: int | bool,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["schema_version"] = schema_version

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert result.issues == ["assurance_schema_version_unsupported"]


def test_invalid_field_type_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["iteration"] = "1"

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("run_id", "run-002", "assurance_run_id_mismatch"),
        ("iteration", 2, "assurance_iteration_mismatch"),
    ],
)
def test_bundle_context_mismatch_fails_closed(
    tmp_path: Path,
    field: str,
    value: str | int,
    issue: str,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload[field] = value

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert issue in result.issues


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


def test_missing_claim_reference_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["claim_refs"] = ["C-FORGED"]

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "threat:T-SCOPE-001:claim_ref_missing:C-FORGED" in result.issues


def test_unknown_covered_threat_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["covers"] = ["T-FORGED"]

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "evidence:E-001:unknown_threat:T-FORGED" in result.issues


def test_evidence_must_cover_referencing_threat(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    second_threat = copy.deepcopy(payload["threats"][0])
    second_threat["id"] = "T-OTHER-001"
    payload["threats"].append(second_threat)
    payload["evidence"][0]["covers"] = ["T-OTHER-001"]

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "threat:T-SCOPE-001:evidence_cover_mismatch:E-001" in result.issues


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


def test_scope_policy_snapshot_cannot_be_null(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["snapshot"]["scope_policy_sha256"] = None
    payload["evidence"][0]["snapshot"]["scope_policy_sha256"] = None

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


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


def test_artifact_reference_cannot_switch_to_another_run(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    source = workspace / "runs" / RUN_ID / "iterations" / "01" / "verification-result.json"
    other = workspace / "runs" / "run-002" / "iterations" / "01"
    other.mkdir(parents=True)
    other_file = other / "verification-result.json"
    other_file.write_bytes(source.read_bytes())
    payload["evidence"][0]["artifacts"][0] = {
        "artifact_type": "verification_result",
        "run_id": "run-002",
        "relative_path": "iterations/01/verification-result.json",
        "sha256": hashlib.sha256(other_file.read_bytes()).hexdigest(),
    }

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "evidence:E-001:artifact_run_mismatch:run-002" in result.issues


def test_artifact_reference_must_match_current_iteration(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    source = workspace / "runs" / RUN_ID / "iterations" / "01" / "verification-result.json"
    other = workspace / "runs" / RUN_ID / "iterations" / "02"
    other.mkdir(parents=True)
    other_file = other / "verification-result.json"
    other_file.write_bytes(source.read_bytes())
    payload["evidence"][0]["artifacts"][0] = {
        "artifact_type": "verification_result",
        "run_id": RUN_ID,
        "relative_path": "iterations/02/verification-result.json",
        "sha256": hashlib.sha256(other_file.read_bytes()).hexdigest(),
    }

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert (
        "evidence:E-001:artifact_iteration_path_mismatch:verification-result.json"
        in result.issues
    )


def test_missing_artifact_reference_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    artifact_path.unlink()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert (
        "evidence:E-001:artifact_path_invalid:verification-result.json"
        in result.issues
    )


def test_artifact_directory_reference_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    artifact_path.unlink()
    artifact_path.mkdir()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert (
        "evidence:E-001:artifact_not_file:verification-result.json"
        in result.issues
    )


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


def test_ntfs_ads_artifact_path_is_rejected(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["artifacts"][0]["relative_path"] = (
        "iterations/01/verification-result.json:stream"
    )

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


def test_empty_artifact_path_segment_is_rejected(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["artifacts"][0]["relative_path"] = (
        "iterations//01/verification-result.json"
    )

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert any(issue.startswith("assurance_schema_invalid:") for issue in result.issues)


def test_resolved_artifact_link_escape_is_rejected(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "verification-result.json"
    _write_verification_artifact(outside_file, status="passed")
    iteration_dir = workspace / "runs" / RUN_ID / "iterations" / "01"
    iteration_dir.joinpath("verification-result.json").unlink()
    iteration_dir.rmdir()
    link = workspace / "runs" / RUN_ID / "iterations" / "01"
    _create_directory_link_or_skip(outside, link)
    payload["evidence"][0]["artifacts"][0]["sha256"] = hashlib.sha256(
        outside_file.read_bytes()
    ).hexdigest()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert (
        "evidence:E-001:artifact_path_invalid:verification-result.json"
        in result.issues
    )


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

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert result.decision_source == "deterministic_validator"
    assert "assurance_active_threats_hash_mismatch" in result.issues


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


def test_llm_output_cannot_masquerade_as_trusted_detector(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["source"] = {
        "kind": "deterministic_detector",
        "reference": "detector://forged/from-model",
    }

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "assurance_active_threats_hash_mismatch" in result.issues


def test_llm_candidate_with_forged_references_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    candidate = copy.deepcopy(payload["threats"][0])
    candidate.update(
        {
            "id": "T-CANDIDATE-001",
            "source": {
                "kind": "llm_candidate",
                "reference": "llm://candidate/threat-2",
            },
            "status": "candidate",
            "claim_refs": ["C-FORGED"],
            "evidence_refs": ["E-FORGED"],
        }
    )
    payload["threats"].append(candidate)

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "threat:T-CANDIDATE-001:claim_ref_missing:C-FORGED" in result.issues
    assert "threat:T-CANDIDATE-001:evidence_ref_missing:E-FORGED" in result.issues


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


def test_deeply_nested_json_still_writes_fail_closed_result(tmp_path: Path) -> None:
    api = _assurance_api()
    input_path = tmp_path / "assurance-input.json"
    result_path = tmp_path / "assurance-result.json"
    input_path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")

    result = api.evaluate_assurance_artifact(
        input_path,
        workspace=tmp_path,
        expected=_context(api),
        result_path=result_path,
    )

    assert result.artifact_valid is False
    assert result.issues in (
        ["assurance_artifact_invalid_json"],
        ["assurance_schema_invalid:root:model_type"],
    )
    assert result_path.is_file()


def test_oversized_assurance_input_fails_closed_without_reading(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    input_path = tmp_path / "assurance-input.json"
    input_path.write_bytes(b"x" * (api.MAX_ASSURANCE_INPUT_BYTES + 1))

    result = api.evaluate_assurance_artifact(
        input_path,
        workspace=tmp_path,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.issues == ["assurance_artifact_too_large"]


def test_assurance_input_growth_after_stat_stays_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _assurance_api()
    input_path = tmp_path / "assurance-input.json"
    input_path.write_bytes(b"x" * (api.MAX_ASSURANCE_INPUT_BYTES + 1))
    original_stat = Path.stat

    def underreported_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == input_path:
            return SimpleNamespace(st_size=1)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", underreported_stat)
    result = api.evaluate_assurance_artifact(
        input_path,
        workspace=tmp_path,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert result.issues == ["assurance_artifact_too_large"]


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


def test_persisted_result_binds_expected_snapshot_and_source_hashes(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    context = _context(api)
    input_path = tmp_path / "assurance-input.json"
    result_path = tmp_path / "assurance-result.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    api.evaluate_assurance_artifact(
        input_path,
        workspace=workspace,
        expected=context,
        result_path=result_path,
    )
    persisted = json.loads(result_path.read_text(encoding="utf-8"))

    assert persisted["snapshot"] == context.snapshot.model_dump(mode="json")
    assert persisted["accepted_claims_sha256"] == context.accepted_claims_sha256
    assert persisted["active_threats_sha256"] == context.active_threats_sha256


def test_sensitive_residual_risk_is_redacted_in_result(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    secret = "sk-test-residual-secret-123456"
    payload["threats"][0]["residual_risks"] = [f"token={secret}"]
    context = _context(api, threats=payload["threats"])
    input_path = tmp_path / "assurance-input.json"
    result_path = tmp_path / "assurance-result.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    api.evaluate_assurance_artifact(
        input_path,
        workspace=workspace,
        expected=context,
        result_path=result_path,
    )

    persisted = result_path.read_text(encoding="utf-8")
    assert secret not in persisted
    assert "[REDACTED]" in persisted


def test_hashed_failed_artifact_cannot_masquerade_as_passed(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    _write_verification_artifact(artifact_path, status="failed")
    payload["evidence"][0]["artifacts"][0]["sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert "evidence:E-001:verification_evidence_status_mismatch" in result.issues
    assert result.merge_evidence_sufficient is False


def test_verification_artifact_binds_declared_and_executed_commands(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    original = json.loads(artifact_path.read_text(encoding="utf-8"))
    mutations = [
        (
            "configured_command",
            "python -m pytest tests/test_other.py",
            "verification_configured_command_mismatch",
        ),
        (
            "executed_command",
            "python -m pytest tests/test_other.py",
            "verification_executed_command_mismatch",
        ),
        ("command_index", 2, "verification_command_index_mismatch"),
        (
            "verification_temp",
            ".tmp/vega-verification/forged",
            "verification_temp_path_mismatch",
        ),
    ]

    for field, value, issue in mutations:
        artifact = copy.deepcopy(original)
        artifact["results"][0][field] = value
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        payload["evidence"][0]["artifacts"][0]["sha256"] = hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()

        result = api.evaluate_assurance_payload(
            payload,
            workspace=workspace,
            expected=_context(api),
        )

        assert result.artifact_valid is False
        assert f"evidence:E-001:{issue}" in result.issues


def test_oversized_evidence_artifact_fails_closed(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    artifact_path.write_bytes(b"x" * (api.MAX_EVIDENCE_ARTIFACT_BYTES + 1))
    payload["evidence"][0]["artifacts"][0]["sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is False
    assert (
        "evidence:E-001:artifact_too_large:verification-result.json"
        in result.issues
    )


def test_repeated_oversized_artifact_is_read_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    artifact_path.write_bytes(b"x" * (api.MAX_EVIDENCE_ARTIFACT_BYTES + 1))
    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    payload["evidence"][0]["artifacts"][0]["sha256"] = artifact_hash
    second_evidence = copy.deepcopy(payload["evidence"][0])
    second_evidence["id"] = "E-002"
    payload["evidence"].append(second_evidence)
    payload["threats"][0]["evidence_refs"].append("E-002")
    context = _context(
        api,
        threats=payload["threats"],
        evidence_contracts=payload["evidence"],
    )
    resolved_artifact = artifact_path.resolve()
    original_open = Path.open
    read_count = 0

    def counting_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal read_count
        if path == resolved_artifact and args and args[0] == "rb":
            read_count += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=context,
    )

    assert result.artifact_valid is False
    assert read_count == 1


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("failed", 1),
        ("interrupted", None),
    ],
)
def test_non_passing_evidence_is_insufficient(
    tmp_path: Path,
    status: str,
    exit_code: int | None,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["evidence"][0]["result"] = {
        "status": status,
        "exit_code": exit_code,
        "duration_seconds": 0.1,
    }
    payload["verification_conclusion"] = status
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    _write_verification_artifact(
        artifact_path,
        status=status,
    )
    payload["evidence"][0]["artifacts"][0]["sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.status == "insufficient"
    assert "threat:T-SCOPE-001:required_evidence_missing:scope_test" in result.issues


def test_interrupted_multi_command_artifact_keeps_valid_structure(
    tmp_path: Path,
) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    artifact_path = (
        workspace
        / "runs"
        / RUN_ID
        / "iterations"
        / "01"
        / "verification-result.json"
    )
    second_command = "python -m ruff check src"
    skipped_command = "python -m compileall src"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["commands"] = [COMMAND, second_command]
    artifact["results"].append(
        {
            "command": second_command,
            "configured_command": second_command,
            "executed_command": second_command,
            "command_index": 2,
            "verification_temp": None,
            "status": "timeout",
            "returncode": None,
            "duration_seconds": 0.2,
            "output": "",
            "interruption_status": "timed_out",
            "interruption_reason": "timed out",
        }
    )
    artifact["command_count"] = 2
    artifact["failed_count"] = 1
    artifact["selected_command_count"] = 3
    artifact["skipped_commands"] = [skipped_command]
    artifact["interruption_status"] = "timed_out"
    artifact["interruption_command"] = second_command
    artifact["interruption_reason"] = "timed out"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload["verification_conclusion"] = "interrupted"
    payload["evidence"][0]["artifacts"][0]["sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api),
    )

    assert result.artifact_valid is True
    assert result.verification_conclusion == "interrupted"
    assert result.status == "insufficient"
    assert result.merge_evidence_sufficient is False


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

    assert result.artifact_valid is False
    assert result.status == "insufficient"
    assert result.merge_evidence_sufficient is False
    assert "assurance_verification_conclusion_mismatch:verified" in result.issues


def test_residual_risk_requires_staged_rollout(tmp_path: Path) -> None:
    api = _assurance_api()
    payload, workspace = _valid_payload(api, tmp_path)
    payload["threats"][0]["residual_risks"] = ["需要在灰度环境观察真实租户规模。"]

    result = api.evaluate_assurance_payload(
        payload,
        workspace=workspace,
        expected=_context(api, threats=payload["threats"]),
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
        expected=_context(api, threats=payload["threats"]),
    )

    assert result.artifact_valid is True
    assert result.status == "human_required"
    assert result.merge_evidence_sufficient is False


def _assurance_api() -> ModuleType:
    return importlib.import_module("vega.assurance")


def _context(
    api: ModuleType,
    *,
    claims: list[dict[str, Any]] | None = None,
    threats: list[dict[str, Any]] | None = None,
    evidence_contracts: list[dict[str, Any]] | None = None,
) -> Any:
    return api.build_assurance_context(
        run_id=RUN_ID,
        iteration=ITERATION,
        snapshot=_snapshot(),
        claims=claims or [_claim_record()],
        threats=threats or [_threat_record()],
        evidence_contracts=evidence_contracts or [
            _evidence_record(artifact_hash="f" * 64)
        ],
    )


def _valid_payload(api: ModuleType, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    del api
    workspace = tmp_path / "workspace"
    iteration_dir = workspace / "runs" / RUN_ID / "iterations" / "01"
    iteration_dir.mkdir(parents=True)
    artifact_path = iteration_dir / "verification-result.json"
    _write_verification_artifact(artifact_path, status="passed")
    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    snapshot = _snapshot()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "iteration": ITERATION,
        "snapshot": snapshot,
        "verification_conclusion": "verified",
        "claims": [_claim_record()],
        "threats": [_threat_record()],
        "evidence": [_evidence_record(artifact_hash=artifact_hash, snapshot=snapshot)],
    }
    return payload, workspace


def _claim_record() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "C-001",
        "statement": "批量更新只能影响声明的租户范围。",
        "status": "accepted",
        "source": {
            "kind": "user_requirement",
            "reference": "task://acceptance/1",
        },
    }


def _threat_record() -> dict[str, Any]:
    return {
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


def _evidence_record(
    *,
    artifact_hash: str,
    snapshot: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "E-001",
        "kind": "scope_test",
        "producer": {
            "runner": "pytest",
            "version": "8.x",
        },
        "command": COMMAND,
        "environment": {
            "os": "windows",
            "python": "3.12",
        },
        "run_id": RUN_ID,
        "iteration": ITERATION,
        "snapshot": snapshot or _snapshot(),
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
                "artifact_type": "verification_result",
                "run_id": RUN_ID,
                "relative_path": "iterations/01/verification-result.json",
                "sha256": artifact_hash,
            }
        ],
        "limitations": [],
    }


def _write_verification_artifact(path: Path, *, status: str) -> None:
    if status == "passed":
        result_status = "passed"
        returncode = 0
        interruption_status = None
        failed_count = 0
    elif status == "failed":
        result_status = "failed"
        returncode = 1
        interruption_status = None
        failed_count = 1
    else:
        result_status = "timeout"
        returncode = None
        interruption_status = "timed_out"
        failed_count = 1
    payload = {
        "artifact_version": 2,
        "run_id": RUN_ID,
        "iteration": ITERATION,
        "shell_kind": "cmd",
        "repo_path": "repo",
        "commands": [COMMAND],
        "results": [
            {
                "command": COMMAND,
                "configured_command": COMMAND,
                "executed_command": COMMAND,
                "command_index": 1,
                "verification_temp": None,
                "status": result_status,
                "returncode": returncode,
                "duration_seconds": 0.1,
                "output": "",
                "interruption_status": interruption_status,
                "interruption_reason": (
                    "timed out" if interruption_status is not None else None
                ),
            }
        ],
        "command_count": 1,
        "failed_count": failed_count,
        "selected_command_count": 1,
        "skipped_commands": [],
        "interruption_status": interruption_status,
        "interruption_command": COMMAND if interruption_status else None,
        "interruption_reason": "timed out" if interruption_status else None,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _create_directory_link_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        if sys.platform != "win32":
            pytest.skip(f"当前平台不能创建目录 symlink：{exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"当前平台不能创建目录 symlink 或 junction：{exc}")
