from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from vega.ma2b_task_pack import (
    MA2BCaseManifest,
    MA2BGroundTruthArtifact,
    MA2BTaskArtifact,
    MA2BTaskPackError,
    load_ma2b_case_package,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path("eval/experiments/multi-agent-coordination/fixtures/ma2b")
TASK_PACK_ROOT = FIXTURE_ROOT / "task-pack"
GROUND_TRUTH_ROOT = FIXTURE_ROOT / "ground-truth"
PILOT_CANDIDATE_ROOT = FIXTURE_ROOT / "pilot-candidates/v1"
PILOT_TASK_PACK_ROOT = PILOT_CANDIDATE_ROOT / "task-pack"
PILOT_GROUND_TRUTH_ROOT = PILOT_CANDIDATE_ROOT / "ground-truth"
PILOT_SNAPSHOT_COMMIT = "3ff369c416340f3acf8c5bc1641e412f2816e738"
PILOT_EXPECTED = {
    "MA2B-C01": {
        "case_class": "code_change",
        "task_pack_sha256": (
            "a80e93109ec95fad902104edbf9f6953238d0b3d45df52c0ef8d31006d8bd883"
        ),
        "ground_truth_sha256": (
            "f09d1ba5db5809b9d213a819c07def44465fef556a54d9838fb79b367bc5f2e8"
        ),
    },
    "MA2B-C05": {
        "case_class": "code_change",
        "task_pack_sha256": (
            "a4cd7173071b4b153d039a4242971b5cbb511681dab0dfd0b655c0346159f2ce"
        ),
        "ground_truth_sha256": (
            "62277f7b751f85e0fee62543ba79856b7aedc3b39d63689d37d8eb8692767170"
        ),
    },
    "MA2B-C09": {
        "case_class": "human_required",
        "task_pack_sha256": (
            "33158f1b5327b0d45ef4af26fa4d64d075d7347e491fa934a6b3404a094869ff"
        ),
        "ground_truth_sha256": (
            "843c7c50adb9a7a62d09acfcdf29ed9f398832f6e4b4f1d226774b6a5b166b71"
        ),
    },
    "MA2B-C12": {
        "case_class": "invalid_verifier",
        "task_pack_sha256": (
            "7386ab9e1f16a6375618830d617ded48d4fc5bc4217f20bdf4e4ba9c944422ad"
        ),
        "ground_truth_sha256": (
            "1df76be91fee43320f524be4fb2d214a1ebc6b8940128a41d0b1120318c97c93"
        ),
    },
}


def test_task_pack_fixtures_and_pilot_candidates_load_with_bound_hashes() -> None:
    package = _load_fixture(PROJECT_ROOT, "MA2B-F01")

    assert package.manifest.package_role == "fake_driver_fixture"
    assert package.manifest.case_class == "code_change"
    assert package.ground_truth.expected_outcome == "accepted_change"
    assert package.ground_truth.quality_scored is True
    assert package.ground_truth.target_workspace_change == "allowed"
    assert package.task_pack_sha256 == package.ground_truth.task_pack_sha256
    assert package.project_policy.allowed_write_paths == ["src/textops.py"]
    _assert_frozen_pilot_candidates()


def test_stale_evidence_fixture_is_safe_block_and_never_quality_scored() -> None:
    package = _load_fixture(PROJECT_ROOT, "MA2B-F11")

    assert package.manifest.case_class == "stale_evidence"
    assert package.ground_truth.expected_outcome == "safe_block"
    assert package.ground_truth.quality_scored is False
    assert package.ground_truth.target_workspace_change == "forbidden"
    assert package.task.unresolved_decision is None


def test_fixture_tree_is_forced_to_lf_for_cross_machine_hash_stability() -> None:
    attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert (
        "eval/experiments/multi-agent-coordination/fixtures/ma2b/** text eol=lf"
        in attributes.splitlines()
    )
    for path in (PROJECT_ROOT / FIXTURE_ROOT).rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes()


def test_task_schema_rejects_reference_patch_or_provider_prompt() -> None:
    payload = _read_json(
        PROJECT_ROOT / TASK_PACK_ROOT / "MA2B-F01" / "task.json"
    )
    payload["reference_patch"] = "不允许进入 task artifact"
    payload["provider_prompt"] = "不允许进入 task artifact"

    with pytest.raises(ValidationError):
        MA2BTaskArtifact.model_validate(payload)


def test_manifest_rejects_path_escape() -> None:
    payload = _read_json(
        PROJECT_ROOT / TASK_PACK_ROOT / "MA2B-F01" / "case-manifest.json"
    )
    payload["task_ref"]["relative_path"] = "../task.json"

    with pytest.raises(ValidationError):
        MA2BCaseManifest.model_validate(payload)


def test_pilot_case_id_cannot_use_fake_fixture_role() -> None:
    payload = _read_json(
        PROJECT_ROOT / TASK_PACK_ROOT / "MA2B-F01" / "case-manifest.json"
    )
    payload["case_id"] = "MA2B-C01"

    with pytest.raises(ValidationError, match="package_role"):
        MA2BCaseManifest.model_validate(payload)


def test_ground_truth_rejects_wrong_outcome_for_case_class() -> None:
    payload = _read_json(PROJECT_ROOT / GROUND_TRUTH_ROOT / "MA2B-F11.json")
    payload["expected_outcome"] = "accepted_change"
    payload["forbidden_outcomes"] = ["safe_deferral", "safe_block"]
    payload["quality_scored"] = True
    payload["target_workspace_change"] = "allowed"

    with pytest.raises(ValidationError, match="expected_outcome"):
        MA2BGroundTruthArtifact.model_validate(payload)


def test_bound_task_tamper_fails_closed(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)
    task_path = repo / TASK_PACK_ROOT / "MA2B-F01" / "task.json"
    payload = _read_json(task_path)
    payload["summary"] = "篡改后的任务"
    _write_json(task_path, payload)

    with pytest.raises(MA2BTaskPackError) as exc_info:
        _load_fixture(repo, "MA2B-F01")

    assert exc_info.value.issue_code == "artifact_reference_hash_mismatch"


def test_initial_workspace_file_tamper_fails_closed(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)
    source_path = (
        repo
        / FIXTURE_ROOT
        / "workspaces/MA2B-F01/src/textops.py"
    )
    source_path.write_bytes(
        source_path.read_bytes().replace(b"value.strip()", b"value.upper()")
    )

    with pytest.raises(MA2BTaskPackError) as exc_info:
        _load_fixture(repo, "MA2B-F01")

    assert exc_info.value.issue_code == "initial_workspace_file_hash_mismatch"


def test_ground_truth_task_pack_hash_tamper_fails_closed(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)
    ground_truth_path = repo / GROUND_TRUTH_ROOT / "MA2B-F01.json"
    payload = _read_json(ground_truth_path)
    payload["task_pack_sha256"] = "f" * 64
    _write_json(ground_truth_path, payload)

    with pytest.raises(MA2BTaskPackError) as exc_info:
        _load_fixture(repo, "MA2B-F01")

    assert exc_info.value.issue_code == "task_pack_hash_mismatch"


def test_ground_truth_acceptance_mismatch_fails_closed(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)
    ground_truth_path = repo / GROUND_TRUTH_ROOT / "MA2B-F01.json"
    payload = _read_json(ground_truth_path)
    payload["acceptance_fact_ids"] = ["A-MA2B-F01-OTHER"]
    _write_json(ground_truth_path, payload)

    with pytest.raises(MA2BTaskPackError) as exc_info:
        _load_fixture(repo, "MA2B-F01")

    assert exc_info.value.issue_code == "acceptance_fact_mismatch"


def test_duplicate_json_key_fails_before_schema_validation(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)
    manifest_path = repo / TASK_PACK_ROOT / "MA2B-F01" / "case-manifest.json"
    manifest_path.write_text(
        '{"schema_version":1,"schema_version":1}\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(MA2BTaskPackError) as exc_info:
        _load_fixture(repo, "MA2B-F01")

    assert exc_info.value.issue_code == "case_manifest_invalid_json"


def test_task_pack_root_escape_is_rejected_before_reading(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)

    with pytest.raises(MA2BTaskPackError) as exc_info:
        load_ma2b_case_package(
            repo_root=repo,
            case_id="MA2B-F01",
            task_pack_root=Path("../outside"),
            ground_truth_root=GROUND_TRUTH_ROOT,
        )

    assert exc_info.value.issue_code == "task_pack_root_invalid"


def test_symlinked_task_artifact_is_rejected(tmp_path: Path) -> None:
    repo = _copy_fixture_repo(tmp_path)
    task_path = repo / TASK_PACK_ROOT / "MA2B-F01" / "task.json"
    outside = repo / "outside-task.json"
    shutil.copyfile(task_path, outside)
    task_path.unlink()
    try:
        task_path.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"当前平台不能创建文件 symlink：{exc}")

    with pytest.raises(MA2BTaskPackError) as exc_info:
        _load_fixture(repo, "MA2B-F01")

    assert exc_info.value.issue_code == "task_pack_artifact_invalid"


def _load_fixture(repo_root: Path, case_id: str):
    return load_ma2b_case_package(
        repo_root=repo_root,
        case_id=case_id,
        task_pack_root=TASK_PACK_ROOT,
        ground_truth_root=GROUND_TRUTH_ROOT,
    )


def _load_pilot_candidate(case_id: str):
    return load_ma2b_case_package(
        repo_root=PROJECT_ROOT,
        case_id=case_id,
        task_pack_root=PILOT_TASK_PACK_ROOT,
        ground_truth_root=PILOT_GROUND_TRUTH_ROOT,
    )


def _assert_frozen_pilot_candidates() -> None:
    packages = {
        case_id: _load_pilot_candidate(case_id)
        for case_id in PILOT_EXPECTED
    }
    for case_id, expected in PILOT_EXPECTED.items():
        package = packages[case_id]

        assert package.manifest.package_role == "pilot_case"
        assert package.manifest.case_class == expected["case_class"]
        assert package.initial_workspace.source_kind == "git_snapshot"
        assert package.initial_workspace.origin_repository_id == "aki0225/vegaloom"
        assert package.initial_workspace.origin_head_sha == PILOT_SNAPSHOT_COMMIT
        assert package.task_pack_sha256 == expected["task_pack_sha256"]
        assert any(
            "worker_token_limit 仅表示观测预算" in item
            for item in package.task.constraints
        )

        ground_truth_path = (
            PROJECT_ROOT / PILOT_GROUND_TRUTH_ROOT / f"{case_id}.json"
        )
        assert _sha256_file(ground_truth_path) == expected["ground_truth_sha256"]

    for case_id in ("MA2B-C01", "MA2B-C05"):
        package = packages[case_id]
        assert package.ground_truth.expected_outcome == "accepted_change"
        assert package.ground_truth.quality_scored is True
        assert package.ground_truth.target_workspace_change == "allowed"

    human_required = packages["MA2B-C09"]
    assert human_required.task.unresolved_decision is not None
    assert (
        human_required.task.unresolved_decision.decision_id
        == "D-MA2B-C09-EXPIRY"
    )
    assert human_required.ground_truth.expected_outcome == "safe_deferral"
    assert human_required.ground_truth.quality_scored is False
    assert human_required.ground_truth.target_workspace_change == "forbidden"

    invalid_verifier = packages["MA2B-C12"]
    assert invalid_verifier.task.unresolved_decision is None
    assert invalid_verifier.ground_truth.expected_outcome == "safe_block"
    assert invalid_verifier.ground_truth.quality_scored is False
    assert invalid_verifier.ground_truth.target_workspace_change == "forbidden"

    workspace = PROJECT_ROOT / invalid_verifier.initial_workspace.source_tree
    project_config = yaml.safe_load(
        workspace.joinpath(".vega.yaml").read_text(encoding="utf-8")
    )
    allowed_commands = set(project_config["verification"]["commands"])
    manifest_commands = set(invalid_verifier.verification.commands)

    assert not manifest_commands.issubset(allowed_commands)
    assert invalid_verifier.verification.commands == [
        "python -m pytest -q tests/test_missing_ma2b_verifier.py"
    ]
    assert not workspace.joinpath("tests/test_missing_ma2b_verifier.py").exists()


def _copy_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    destination = repo / FIXTURE_ROOT
    destination.parent.mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / FIXTURE_ROOT, destination)
    return repo


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
