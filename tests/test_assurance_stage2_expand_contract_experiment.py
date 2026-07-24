from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from vega.cli import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROWS = [
    {"id": 1, "display_name": "Ada", "external_id": "cust-0001"},
    {"id": 2, "display_name": "Lin", "external_id": "cust-0002"},
]


def _bind_project_root(
    module: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "PROJECT_ROOT", root.resolve(), raising=False)


def _experiment_module() -> ModuleType:
    script = PROJECT_ROOT / "scripts" / "run_assurance_stage2_expand_contract_experiment.py"
    spec = importlib.util.spec_from_file_location(
        "assurance_stage2_expand_contract_experiment",
        script,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _create_directory_link(link_path: Path, target_path: Path) -> None:
    """Windows 使用 junction，POSIX 使用目录 symlink。"""

    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.fail(
                "无法创建 Windows junction："
                f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
            )
        return
    link_path.symlink_to(target_path, target_is_directory=True)


def _remove_directory_link(link_path: Path) -> None:
    if not os.path.lexists(link_path):
        return
    if os.name == "nt":
        link_path.rmdir()
        return
    link_path.unlink()


def test_stage2_002_rejects_dangerous_order_and_accepts_safe_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    output_dir = Path(".local-validation") / "expand-contract"

    result = module.run_experiment(output_dir)
    persisted = json.loads(output_dir.joinpath("result.json").read_text(encoding="utf-8"))

    assert result["schema_version"] == 2
    assert result["experiment_id"] == "AV-STAGE2-002"
    assert result["overall_decision"] == "inconclusive"
    assert result["candidate_decision"] == "continue-experiment"
    assert result["evidence_adequacy"] == "insufficient"
    assert result["runtime_integration"] == "disabled"
    assert result["decision_scope"] == "experiment-execution-only"
    assert result["external_quality_gates"]["status"] == "not_evaluated"
    assert module._result_exit_code(result) == 1
    assert persisted["dangerous_twin"]["phase_order"] == [
        "expand",
        "contract",
        "backfill",
    ]
    assert persisted["dangerous_twin"]["detector_issues"] == [
        "T-DB-MIG-COMPAT:contract_before_backfill:"
        "external_id_contains_null_rows"
    ]
    assert persisted["dangerous_twin"]["execution"]["status"] == "failed"
    assert persisted["dangerous_twin"]["post_failure_matches_pre_contract_snapshot"] is True
    assert persisted["dangerous_twin"]["oracle"]["external_ids"] == [None, None]
    assert persisted["dangerous_twin"]["oracle"]["temp_table_present"] is False
    assert persisted["dangerous_twin"]["decision"] == "reject"
    safe = persisted["safe_twin"]
    assert safe["phase_order"] == ["expand", "backfill", "contract"]
    assert safe["backfill"]["first_run"]["updated_rows"] == 2
    assert safe["backfill"]["second_run"]["updated_rows"] == 0
    assert safe["backfill"]["idempotent"] is True
    assert safe["contract"]["first_run"]["status"] == "applied"
    assert safe["contract"]["second_run"]["status"] == "already_contracted"
    assert safe["contract"]["idempotent"] is True
    assert all(case["passed"] for case in safe["matrix"].values())
    assert safe["oracle"]["passed"] is True
    assert safe["oracle"]["schema_mode"] == "contracted_not_null"
    assert safe["oracle"]["rows"] == EXPECTED_ROWS
    assert safe["oracle"]["null_external_id_count"] == 0
    assert safe["oracle"]["distinct_external_id_count"] == 2
    assert safe["oracle"]["temp_table_present"] is False
    assert safe["decision"] == "candidate-passed-local"
    assert output_dir.joinpath("report.md").is_file()
    assert output_dir.joinpath("dangerous.sqlite").is_file()
    assert output_dir.joinpath("safe.sqlite").is_file()
    assert not tmp_path.joinpath("runs").exists()
    assert "assurance-stage2" not in CliRunner().invoke(app, ["--help"]).output

    main_output_dir = Path(".local-validation") / "main-entry"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_assurance_stage2_expand_contract_experiment.py",
            "--output-dir",
            str(main_output_dir),
        ],
    )

    assert module.main() == 1
    console_output = capsys.readouterr().out
    assert "decision=inconclusive" in console_output
    assert "candidate=continue-experiment" in console_output
    assert "artifact=.local-validation/main-entry/result.json" in console_output
    assert str(tmp_path.resolve()) not in console_output
    assert main_output_dir.joinpath("result.json").is_file()


def test_stage2_002_partial_backfill_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    calls = 0
    contract_calls = 0
    original_contract = module._apply_contract

    def partial_backfill(connection: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            cursor = connection.execute(
                "UPDATE customer SET external_id = 'cust-0001' "
                "WHERE id = 1 AND external_id IS NULL"
            )
            connection.commit()
            return {"status": "applied", "updated_rows": cursor.rowcount}
        return {"status": "already_backfilled", "updated_rows": 0}

    def recording_contract(connection: Any) -> dict[str, Any]:
        nonlocal contract_calls
        contract_calls += 1
        return original_contract(connection)

    monkeypatch.setattr(module, "_backfill_external_ids", partial_backfill)
    monkeypatch.setattr(module, "_apply_contract", recording_contract)
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "partial-backfill")

    safe = result["safe_twin"]
    assert safe["backfill"]["first_run"]["updated_rows"] == 1
    assert safe["contract"]["first_run"]["status"] == "skipped"
    assert contract_calls == 1
    assert safe["oracle"]["null_external_id_count"] == 1
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_002_independent_oracle_rejects_masked_wrong_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    original_read = module._new_app_reads
    original_detector = module._contract_precondition_issues
    calls = 0

    def wrong_backfill(connection: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            cursor = connection.execute(
                "UPDATE customer SET external_id = 'wrong-' || printf('%04d', id) "
                "WHERE external_id IS NULL"
            )
            connection.commit()
            return {"status": "applied", "updated_rows": cursor.rowcount}
        return {"status": "already_backfilled", "updated_rows": 0}

    def masking_new_app_read(connection: Any) -> list[dict[str, Any]]:
        rows = original_read(connection)
        return [
            {
                **row,
                "external_id": (
                    f"cust-{row['id']:04d}"
                    if str(row.get("external_id", "")).startswith("wrong-")
                    else row.get("external_id")
                ),
            }
            for row in rows
        ]

    def detector_blind_to_wrong_mapping(connection: Any) -> list[str]:
        issues = original_detector(connection)
        if issues == [
            "T-DB-MIG-COMPAT:contract_after_wrong_backfill:"
            "external_id_mapping_mismatch"
        ]:
            return []
        return issues

    monkeypatch.setattr(module, "_backfill_external_ids", wrong_backfill)
    monkeypatch.setattr(module, "_new_app_reads", masking_new_app_read)
    monkeypatch.setattr(
        module,
        "_contract_precondition_issues",
        detector_blind_to_wrong_mapping,
    )
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "masked-wrong-mapping")

    safe = result["safe_twin"]
    assert all(case["passed"] for case in safe["matrix"].values())
    assert safe["oracle"]["rows"] != EXPECTED_ROWS
    assert safe["oracle"]["stored_rows_passed"] is False
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_002_independent_oracle_does_not_share_application_row_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    original_stored_rows = module._stored_rows
    calls = 0

    def wrong_backfill(connection: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            cursor = connection.execute(
                "UPDATE customer SET external_id = 'wrong-' || printf('%04d', id) "
                "WHERE external_id IS NULL"
            )
            connection.commit()
            return {"status": "applied", "updated_rows": cursor.rowcount}
        return {"status": "already_backfilled", "updated_rows": 0}

    def masking_shared_row_reader(connection: Any) -> list[dict[str, Any]]:
        rows = original_stored_rows(connection)
        return [
            {
                **row,
                "external_id": (
                    f"cust-{row['id']:04d}"
                    if str(row["external_id"]).startswith("wrong-")
                    else row["external_id"]
                ),
            }
            for row in rows
        ]

    monkeypatch.setattr(module, "_backfill_external_ids", wrong_backfill)
    monkeypatch.setattr(module, "_stored_rows", masking_shared_row_reader)
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(
        Path(".local-validation") / "shared-reader-masked-wrong-mapping"
    )

    safe = result["safe_twin"]
    assert safe["detector_issues"] == []
    assert all(case["passed"] for case in safe["matrix"].values())
    assert safe["oracle"]["rows"] != EXPECTED_ROWS
    assert safe["oracle"]["stored_rows_passed"] is False
    assert safe["oracle"]["passed"] is False
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_002_rejects_contract_without_not_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()

    def create_nullable_contract_table(connection: Any) -> None:
        connection.execute(
            "CREATE TABLE customer__contract_tmp ("
            "id INTEGER PRIMARY KEY, "
            "display_name TEXT NOT NULL, "
            "external_id TEXT UNIQUE)"
        )

    monkeypatch.setattr(module, "_create_contract_table", create_nullable_contract_table)
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "nullable-contract")

    safe = result["safe_twin"]
    assert safe["contract"]["first_run"]["status"] == "applied"
    assert safe["oracle"]["not_null_columns"]["external_id"] is False
    assert safe["oracle"]["passed"] is False
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


@pytest.mark.parametrize(
    "contract_variant",
    [
        pytest.param("missing-unique", id="missing-unique"),
        pytest.param("partial-unique", id="partial-unique"),
        pytest.param("wrong-type", id="wrong-type"),
    ],
)
def test_stage2_002_rejects_incomplete_contract_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract_variant: str,
) -> None:
    module = _experiment_module()

    def create_incomplete_contract_table(connection: Any) -> None:
        external_type = "BLOB" if contract_variant == "wrong-type" else "TEXT"
        unique_clause = " UNIQUE" if contract_variant == "wrong-type" else ""
        connection.execute(
            "CREATE TABLE customer__contract_tmp ("
            "id INTEGER PRIMARY KEY, "
            "display_name TEXT NOT NULL, "
            f"external_id {external_type} NOT NULL{unique_clause})"
        )
        if contract_variant == "partial-unique":
            connection.execute(
                "CREATE UNIQUE INDEX customer__external_id_partial "
                "ON customer__contract_tmp(external_id) WHERE id = 1"
            )

    monkeypatch.setattr(
        module,
        "_create_contract_table",
        create_incomplete_contract_table,
    )
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(
        Path(".local-validation") / f"incomplete-contract-{contract_variant}"
    )

    safe = result["safe_twin"]
    assert safe["contract"]["first_run"]["status"] == "applied"
    assert safe["oracle"]["passed"] is False
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_002_rejects_contract_that_drops_baseline_constraints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()

    def create_weakened_contract_table(connection: Any) -> None:
        connection.execute(
            "CREATE TABLE customer__contract_tmp ("
            "id INTEGER, "
            "display_name TEXT, "
            "external_id TEXT NOT NULL UNIQUE)"
        )

    monkeypatch.setattr(
        module,
        "_create_contract_table",
        create_weakened_contract_table,
    )
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(
        Path(".local-validation") / "weakened-baseline-constraints"
    )

    safe = result["safe_twin"]
    assert safe["contract"]["first_run"]["status"] == "applied"
    assert safe["contract"]["second_run"]["status"] != "already_contracted"
    assert safe["oracle"]["schema_columns_passed"] is False
    assert safe["oracle"]["passed"] is False
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_002_backfill_only_updates_frozen_fixture_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    connection = sqlite3.connect(":memory:")
    try:
        module._create_baseline(connection)
        module._apply_expand(connection)
        connection.execute(
            "INSERT INTO customer (id, display_name, external_id) "
            "VALUES (3, 'Out of scope', NULL)"
        )
        connection.commit()

        result = module._backfill_external_ids(connection)
        rows = module._stored_rows(connection)
    finally:
        connection.close()

    assert result == {"status": "applied", "updated_rows": 2}
    assert rows[-1] == {
        "id": 3,
        "display_name": "Out of scope",
        "external_id": None,
    }


def test_stage2_002_duplicate_external_ids_have_specific_detector_issue() -> None:
    module = _experiment_module()
    connection = sqlite3.connect(":memory:")
    try:
        module._create_baseline(connection)
        module._apply_expand(connection)
        connection.execute("UPDATE customer SET external_id = 'duplicate'")
        connection.commit()

        issues = module._contract_precondition_issues(connection)
    finally:
        connection.close()

    assert issues == [
        "T-DB-MIG-COMPAT:contract_after_wrong_backfill:"
        "external_id_not_unique"
    ]


def test_stage2_002_rejects_leftover_contract_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    original_apply = module._apply_contract
    failure_injected = False

    def apply_with_leftover(connection: Any) -> dict[str, Any]:
        nonlocal failure_injected
        if not failure_injected and not module._contract_precondition_issues(connection):
            connection.execute("CREATE TABLE customer__contract_tmp (leak INTEGER)")
            connection.commit()
            failure_injected = True
            return {
                "status": "failed",
                "error_type": "InjectedContractFailure",
                "error_message": "模拟 contract 失败后残留临时表。",
            }
        return original_apply(connection)

    monkeypatch.setattr(module, "_apply_contract", apply_with_leftover)
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "leftover-table")

    safe = result["safe_twin"]
    assert safe["contract"]["first_run"]["status"] == "failed"
    assert safe["oracle"]["temp_table_present"] is True
    assert safe["oracle"]["passed"] is False
    assert safe["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_002_rejects_output_outside_local_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match=r"\.local-validation"):
        module.run_experiment(Path("outside"))

    assert not tmp_path.joinpath("outside").exists()


def test_stage2_002_resolves_output_against_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    project_root = tmp_path / "project"
    other_workspace = tmp_path / "other"
    project_root.mkdir()
    other_workspace.mkdir()
    _bind_project_root(module, project_root, monkeypatch)
    monkeypatch.chdir(other_workspace)
    output_dir = Path(".local-validation") / "project-bound"

    module.run_experiment(output_dir)

    assert project_root.joinpath(output_dir, "result.json").is_file()
    assert not other_workspace.joinpath(output_dir).exists()
    case_variant_root = str(project_root).swapcase()
    assert case_variant_root not in module._safe_exception_message(
        OSError(case_variant_root)
    )


@pytest.mark.parametrize(
    "link_depth",
    [
        pytest.param("root", id="root-link"),
        pytest.param("nested", id="nested-link"),
    ],
)
def test_stage2_002_rejects_linked_output_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_depth: str,
) -> None:
    module = _experiment_module()
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    if link_depth == "root":
        link_path = workspace / ".local-validation"
        output_dir = Path(".local-validation") / "linked"
    else:
        workspace.joinpath(".local-validation").mkdir()
        link_path = workspace / ".local-validation" / "nested"
        output_dir = Path(".local-validation") / "nested" / "linked"
    _create_directory_link(link_path, outside)
    _bind_project_root(module, workspace, monkeypatch)
    monkeypatch.chdir(workspace)

    try:
        with pytest.raises(ValueError, match=r"\.local-validation"):
            module.run_experiment(output_dir)

        assert not outside.joinpath("linked").exists()
    finally:
        _remove_directory_link(link_path)
