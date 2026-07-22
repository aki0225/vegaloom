from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

import pytest

from vega.parallel_review import (
    AVAILABLE_REVIEWER_ROLES,
    ParallelReviewRoutingContext,
    build_parallel_review_plan,
)
from vega.reviewer_topology_eval import (
    ReviewerTopologyEvaluationDataset,
    load_ground_truth,
    load_public_dataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_DIR = PROJECT_ROOT / "eval" / "gate-5.5"
PUBLIC_PATH = DATASET_DIR / "cases.json"
GROUND_TRUTH_PATH = DATASET_DIR / "ground-truth.json"

PUBLIC_DATA = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))
GROUND_TRUTH = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
CASES = PUBLIC_DATA["cases"]

EXPECTED_CATEGORY_COUNTS = {
    "clean": 3,
    "correctness": 3,
    "verification_adequacy": 3,
    "security_design": 3,
}
EXPECTED_RULES = {
    "correctness.off_by_one",
    "correctness.expiry_boundary",
    "correctness.unicode_separator",
    "verification.mocked_subject",
    "verification.missing_side_effect_assertion",
    "verification.missing_boundary_case",
    "security.path_traversal",
    "security.command_injection",
    "design.non_atomic_persistence",
}
PUBLIC_CASE_KEYS = {
    "case_id",
    "category",
    "task",
    "acceptance",
    "before_files",
    "after_files",
    "verification",
    "routing_facts",
}
FINDING_KEYS = {
    "severity",
    "severity_aliases",
    "category",
    "category_aliases",
    "rule",
    "rule_aliases",
    "path",
    "path_aliases",
    "location",
    "location_aliases",
}
GROUND_TRUTH_CASE_KEYS = {
    "case_id",
    "expected_verdict",
    "findings",
    "forbidden_false_blocker_conditions",
}
FORBIDDEN_PUBLIC_KEYS = {
    "expected_finding",
    "expected_findings",
    "finding",
    "findings",
    "ground_truth",
    "location_aliases",
    "path_aliases",
    "rule",
    "rule_aliases",
    "severity",
    "severity_aliases",
}


def _assert_safe_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    assert value == path.as_posix()
    assert value
    assert not path.is_absolute()
    assert "\\" not in value
    assert ":" not in path.parts[0]
    assert all(part not in {"", ".", ".."} for part in path.parts)


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(_walk_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for nested in value:
            keys.update(_walk_keys(nested))
        return keys
    return set()


def _case_by_id() -> dict[str, dict[str, object]]:
    return {case["case_id"]: case for case in CASES}


def test_public_dataset_has_strict_twelve_case_balance() -> None:
    assert set(PUBLIC_DATA) == {"schema_version", "dataset_id", "cases"}
    assert PUBLIC_DATA["schema_version"] == 1
    assert PUBLIC_DATA["dataset_id"] == "gate-5.5-reviewer-topology-v1"
    assert len(CASES) == 12

    case_ids = [case["case_id"] for case in CASES]
    assert len(case_ids) == len(set(case_ids))
    assert Counter(case["category"] for case in CASES) == EXPECTED_CATEGORY_COUNTS

    for case in CASES:
        assert set(case) == PUBLIC_CASE_KEYS
        assert isinstance(case["task"], str) and case["task"].strip()
        assert (
            isinstance(case["acceptance"], list)
            and len(case["acceptance"]) >= 3
            and all(isinstance(item, str) and item.strip() for item in case["acceptance"])
        )
        assert case["verification"] == {
            "command": "python -m pytest -q",
            "expected_exit_code": 0,
            "expected_status": "passed",
        }


def test_fixture_paths_are_safe_and_every_case_has_a_real_diff() -> None:
    for case in CASES:
        before_files = case["before_files"]
        after_files = case["after_files"]
        assert isinstance(before_files, dict) and before_files
        assert isinstance(after_files, dict) and after_files

        all_paths = set(before_files) | set(after_files)
        for path in all_paths:
            _assert_safe_relative_path(path)
        assert all(isinstance(content, str) for content in before_files.values())
        assert all(isinstance(content, str) for content in after_files.values())

        changed_paths = sorted(
            path for path in all_paths if before_files.get(path) != after_files.get(path)
        )
        assert changed_paths

        routing = case["routing_facts"]
        assert routing["verification_status"] == "passed"
        assert routing["verification_failed_count"] == 0
        assert routing["changed_files"] == changed_paths
        assert routing["gate_reason_codes"] == sorted(set(routing["gate_reason_codes"]))
        for path in routing["changed_files"]:
            _assert_safe_relative_path(path)


def test_routing_facts_build_the_expected_adaptive_review_plans() -> None:
    expected_roles = {
        "clean": {
            "correctness_reviewer",
            "verification_adequacy_reviewer",
        },
        "correctness": {"correctness_reviewer"},
        "verification_adequacy": {
            "correctness_reviewer",
            "verification_adequacy_reviewer",
        },
    }

    for case in CASES:
        facts = case["routing_facts"]
        routing = ParallelReviewRoutingContext.model_validate(
            {
                "run_id": case["case_id"],
                "iteration": 1,
                "evidence_snapshot_sha256": "a" * 64,
                **facts,
            }
        )
        plan = build_parallel_review_plan(routing, topology="adaptive")
        if case["category"] == "security_design":
            expected_security_roles = {
                "correctness_reviewer",
                "security_design_reviewer",
            }
            if any(path.startswith("tests/") for path in facts["changed_files"]):
                expected_security_roles.add("verification_adequacy_reviewer")
            if facts["risk"] == "high":
                expected_security_roles = set(AVAILABLE_REVIEWER_ROLES)
            assert set(plan.required_roles) == expected_security_roles
        else:
            assert set(plan.required_roles) == expected_roles[case["category"]]


def test_ground_truth_is_private_and_has_machine_matchable_aliases() -> None:
    assert set(GROUND_TRUTH) == {"schema_version", "dataset_id", "cases"}
    assert GROUND_TRUTH["schema_version"] == PUBLIC_DATA["schema_version"]
    assert GROUND_TRUTH["dataset_id"] == PUBLIC_DATA["dataset_id"]

    public_text = PUBLIC_PATH.read_text(encoding="utf-8")
    public_keys = _walk_keys(PUBLIC_DATA)
    assert public_keys.isdisjoint(FORBIDDEN_PUBLIC_KEYS)
    assert '"blocker"' not in public_text
    assert '"major"' not in public_text

    truth_case_ids: set[str] = set()
    observed_rules: set[str] = set()
    cases_by_id = _case_by_id()
    for truth_case in GROUND_TRUTH["cases"]:
        assert set(truth_case) == GROUND_TRUTH_CASE_KEYS
        case_id = truth_case["case_id"]
        assert case_id not in truth_case_ids
        truth_case_ids.add(case_id)

        public_case = cases_by_id[case_id]
        expected_clean = public_case["category"] == "clean"
        assert truth_case["expected_verdict"] == (
            "approve" if expected_clean else "request_changes"
        )
        if expected_clean:
            assert truth_case["findings"] == []
        else:
            assert truth_case["findings"]
        conditions = truth_case["forbidden_false_blocker_conditions"]
        assert isinstance(conditions, list)
        if expected_clean:
            assert conditions == [
                "any blocker finding",
                "any major finding",
            ]
        else:
            assert conditions == []
        for finding in truth_case["findings"]:
            assert set(finding) == FINDING_KEYS
            observed_rules.add(finding["rule"])
            assert finding["rule"] in finding["rule_aliases"]
            assert finding["severity"] in finding["severity_aliases"]
            assert {"major", "blocker"}.issubset(finding["severity_aliases"])
            assert finding["category"] in finding["category_aliases"]
            assert finding["path"] in finding["path_aliases"]
            assert finding["location"] in finding["location_aliases"]
            assert finding["path"] in public_case["after_files"]
            _assert_safe_relative_path(finding["path"])
            for alias_field in (
                "severity_aliases",
                "category_aliases",
                "rule_aliases",
                "path_aliases",
                "location_aliases",
            ):
                aliases = finding[alias_field]
                assert aliases
                assert len(aliases) == len(set(aliases))
            assert finding["rule"] not in public_text

    assert observed_rules == EXPECTED_RULES


def test_ground_truth_models_bind_all_twelve_public_cases() -> None:
    dataset = ReviewerTopologyEvaluationDataset(
        public_dataset=load_public_dataset(PUBLIC_PATH),
        ground_truth=load_ground_truth(GROUND_TRUTH_PATH),
    )

    assert len(dataset.public_dataset.cases) == 12
    assert len(dataset.ground_truth.cases) == 12


def test_every_case_has_explicit_ground_truth_contract() -> None:
    truth_case_ids = {case["case_id"] for case in GROUND_TRUTH["cases"]}
    public_case_ids = {case["case_id"] for case in CASES}
    defect_case_ids = {case["case_id"] for case in CASES if case["category"] != "clean"}
    clean_case_ids = {case["case_id"] for case in CASES if case["category"] == "clean"}

    assert truth_case_ids == public_case_ids
    assert defect_case_ids.issubset(truth_case_ids)
    assert clean_case_ids.issubset(truth_case_ids)
    assert len(truth_case_ids) == 12
    assert len(clean_case_ids) == 3


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_each_after_fixture_declared_verification_passes(
    case: dict[str, object],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / case["case_id"]
    workspace.mkdir()
    for relative_path, content in case["after_files"].items():
        path = workspace.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    command = case["verification"]["command"].split()
    assert command[0] == "python"
    command[0] = sys.executable
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "--basetemp=.pytest-runs -p no:cacheprovider",
    }
    completed = subprocess.run(
        command,
        cwd=workspace,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == case["verification"]["expected_exit_code"], (
        f"{case['case_id']} verification failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
