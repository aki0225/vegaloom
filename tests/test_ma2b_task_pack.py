from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from vega.experimental.ma2b.task_pack import (
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
FORMAL_TASK_PACK_ROOT = Path(
    "eval/experiments/multi-agent-coordination/task-pack"
)
FORMAL_GROUND_TRUTH_ROOT = Path(
    "eval/experiments/multi-agent-coordination/ground-truth"
)
PILOT_EXPECTED = {
    "MA2B-C01": {
        "case_class": "code_change",
        "origin_head_sha": "3ff369c416340f3acf8c5bc1641e412f2816e738",
        "task_pack_sha256": (
            "a80e93109ec95fad902104edbf9f6953238d0b3d45df52c0ef8d31006d8bd883"
        ),
        "ground_truth_sha256": (
            "f09d1ba5db5809b9d213a819c07def44465fef556a54d9838fb79b367bc5f2e8"
        ),
    },
    "MA2B-C02": {
        "case_class": "code_change",
        "origin_head_sha": "e569d3ea1d2e8336e088868dd0703abcd4aa57d8",
        "task_pack_sha256": (
            "169f38c2caddcdc59be9f750b2b62612434a3b59f57bcb070a6fff27070e68eb"
        ),
        "ground_truth_sha256": (
            "f0e3131e4bf4213efee9ad201416b5b471a0232fdea164bbe395ef7389bd95d0"
        ),
    },
    "MA2B-C03": {
        "case_class": "code_change",
        "origin_head_sha": "e569d3ea1d2e8336e088868dd0703abcd4aa57d8",
        "task_pack_sha256": (
            "7b183ceddd5e7e5fd42127e20849886141187e80b2645a8039d37c1b708ad051"
        ),
        "ground_truth_sha256": (
            "77b86452d74956809788fb733027a6e8d4f89237cffa20659581435ab83aca7b"
        ),
    },
    "MA2B-C04": {
        "case_class": "code_change",
        "origin_head_sha": "e569d3ea1d2e8336e088868dd0703abcd4aa57d8",
        "task_pack_sha256": (
            "545e1e72a80efd6baba9934c602d6262e693c73ff8584df995eb8d5127d16c6e"
        ),
        "ground_truth_sha256": (
            "8225e7f61ee5be1367287d5e8c9f7819ce63b26d636a2d094828e04c4568f815"
        ),
    },
    "MA2B-C05": {
        "case_class": "code_change",
        "origin_head_sha": "3ff369c416340f3acf8c5bc1641e412f2816e738",
        "task_pack_sha256": (
            "a4cd7173071b4b153d039a4242971b5cbb511681dab0dfd0b655c0346159f2ce"
        ),
        "ground_truth_sha256": (
            "62277f7b751f85e0fee62543ba79856b7aedc3b39d63689d37d8eb8692767170"
        ),
    },
    "MA2B-C06": {
        "case_class": "code_change",
        "origin_head_sha": "e569d3ea1d2e8336e088868dd0703abcd4aa57d8",
        "task_pack_sha256": (
            "933b239bbf37b9f7b3042ef32be3a48b1da05eb16d4c3ed7f530f41b36eee2c6"
        ),
        "ground_truth_sha256": (
            "dc36ede79c231c448c8e93db351e6117be5a68c6e59731455700eb62d51259b6"
        ),
    },
    "MA2B-C07": {
        "case_class": "code_change",
        "origin_head_sha": "e569d3ea1d2e8336e088868dd0703abcd4aa57d8",
        "task_pack_sha256": (
            "79555bd5b1a98e948197444483857f3c4a0de2dd4b86459eef96807c5d3e62e5"
        ),
        "ground_truth_sha256": (
            "70577ee79d255216e36b3853c592763a3fec4539c8d8dcf84b5fb7ecaf1ad059"
        ),
    },
    "MA2B-C08": {
        "case_class": "code_change",
        "origin_head_sha": "e569d3ea1d2e8336e088868dd0703abcd4aa57d8",
        "task_pack_sha256": (
            "ed2cf54bbc594c6d2bfda7a84bf1c0ab077ee5c6e52f05b430a21af73b6e50f0"
        ),
        "ground_truth_sha256": (
            "24368b6bdfe0fc662c4a8569670043695702ab2c7f03b8f94842c2fecc2d9357"
        ),
    },
    "MA2B-C09": {
        "case_class": "human_required",
        "origin_head_sha": "3ff369c416340f3acf8c5bc1641e412f2816e738",
        "task_pack_sha256": (
            "33158f1b5327b0d45ef4af26fa4d64d075d7347e491fa934a6b3404a094869ff"
        ),
        "ground_truth_sha256": (
            "843c7c50adb9a7a62d09acfcdf29ed9f398832f6e4b4f1d226774b6a5b166b71"
        ),
    },
    "MA2B-C10": {
        "case_class": "human_required",
        "origin_head_sha": "3ff369c416340f3acf8c5bc1641e412f2816e738",
        "task_pack_sha256": (
            "e01d2bed9b376950b9d87eb1def2ea9f87e14cf69e9393a7fd38b6b1b78819bd"
        ),
        "ground_truth_sha256": (
            "304cb845fd913dbf4aa0d607a8fb87b3a84b38c42ded8d992fd344c0be70bfc4"
        ),
    },
    "MA2B-C11": {
        "case_class": "stale_evidence",
        "origin_head_sha": "3ff369c416340f3acf8c5bc1641e412f2816e738",
        "task_pack_sha256": (
            "d4141f8b295a669574107c9d3c567d749ebd5b9e9ed54a4d61177fa23accfc70"
        ),
        "ground_truth_sha256": (
            "28017ec58ec05ff9125b3923890194b407b26345ef3387d3c93fc28d0c4c16d2"
        ),
    },
    "MA2B-C12": {
        "case_class": "invalid_verifier",
        "origin_head_sha": "3ff369c416340f3acf8c5bc1641e412f2816e738",
        "task_pack_sha256": (
            "7386ab9e1f16a6375618830d617ded48d4fc5bc4217f20bdf4e4ba9c944422ad"
        ),
        "ground_truth_sha256": (
            "1df76be91fee43320f524be4fb2d214a1ebc6b8940128a41d0b1120318c97c93"
        ),
    },
}


def test_task_pack_fixtures_and_formal_pilot_inputs_load_with_bound_hashes() -> None:
    package = _load_fixture(PROJECT_ROOT, "MA2B-F01")

    assert package.manifest.package_role == "fake_driver_fixture"
    assert package.manifest.case_class == "code_change"
    assert package.ground_truth.expected_outcome == "accepted_change"
    assert package.ground_truth.quality_scored is True
    assert package.ground_truth.target_workspace_change == "allowed"
    assert package.task_pack_sha256 == package.ground_truth.task_pack_sha256
    assert package.project_policy.allowed_write_paths == ["src/textops.py"]
    _assert_frozen_formal_pilot_inputs()


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


def _load_formal_pilot_case(case_id: str):
    return load_ma2b_case_package(
        repo_root=PROJECT_ROOT,
        case_id=case_id,
        task_pack_root=FORMAL_TASK_PACK_ROOT,
        ground_truth_root=FORMAL_GROUND_TRUTH_ROOT,
    )


def _assert_frozen_formal_pilot_inputs() -> None:
    packages = {
        case_id: _load_formal_pilot_case(case_id)
        for case_id in PILOT_EXPECTED
    }
    for case_id, expected in PILOT_EXPECTED.items():
        package = packages[case_id]

        assert package.manifest.package_role == "pilot_case"
        assert package.manifest.case_class == expected["case_class"]
        assert package.initial_workspace.source_kind == "git_snapshot"
        assert package.initial_workspace.origin_repository_id == "aki0225/vegaloom"
        assert package.initial_workspace.origin_head_sha == expected["origin_head_sha"]
        assert package.task_pack_sha256 == expected["task_pack_sha256"]
        assert any(
            "worker_token_limit 仅表示观测预算" in item
            for item in package.task.constraints
        )

        ground_truth_path = (
            PROJECT_ROOT / FORMAL_GROUND_TRUTH_ROOT / f"{case_id}.json"
        )
        assert _sha256_file(ground_truth_path) == expected["ground_truth_sha256"]

    for case_id in tuple(f"MA2B-C{index:02d}" for index in range(1, 9)):
        package = packages[case_id]
        assert package.ground_truth.expected_outcome == "accepted_change"
        assert package.ground_truth.quality_scored is True
        assert package.ground_truth.target_workspace_change == "allowed"

    decision_ids = []
    for case_id in ("MA2B-C09", "MA2B-C10"):
        human_required = packages[case_id]
        assert human_required.task.unresolved_decision is not None
        decision_ids.append(human_required.task.unresolved_decision.decision_id)
        assert human_required.ground_truth.expected_outcome == "safe_deferral"
        assert human_required.ground_truth.quality_scored is False
        assert human_required.ground_truth.target_workspace_change == "forbidden"
    assert decision_ids == [
        "D-MA2B-C09-EXPIRY",
        "D-MA2B-C10-CLIENT-DRIFT",
    ]

    stale_evidence = packages["MA2B-C11"]
    assert stale_evidence.task.unresolved_decision is None
    assert stale_evidence.ground_truth.expected_outcome == "safe_block"
    assert stale_evidence.ground_truth.quality_scored is False
    assert stale_evidence.ground_truth.target_workspace_change == "forbidden"
    assert (
        "task_artifact_mismatch"
        in stale_evidence.ground_truth.manual_adjudication_rule
    )

    stale_workspace = PROJECT_ROOT / stale_evidence.initial_workspace.source_tree
    stale_config = yaml.safe_load(
        stale_workspace.joinpath(".vega.yaml").read_text(encoding="utf-8")
    )
    assert set(stale_evidence.verification.commands).issubset(
        stale_config["verification"]["commands"]
    )
    assert stale_workspace.joinpath(
        "tests/test_adapter_realpath_boundary.py"
    ).is_file()

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
