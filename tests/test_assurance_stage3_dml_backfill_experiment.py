from __future__ import annotations

import copy
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
EXPECTED_FINAL_ROWS = [
    {
        "id": 101,
        "tenant_id": "tenant-a",
        "legacy_handle": "ada",
        "canonical_handle": "cust-a-0101",
        "backfill_version": 1,
    },
    {
        "id": 102,
        "tenant_id": "tenant-a",
        "legacy_handle": "lin",
        "canonical_handle": "cust-a-0102",
        "backfill_version": 1,
    },
    {
        "id": 201,
        "tenant_id": "tenant-b",
        "legacy_handle": "sentinel",
        "canonical_handle": None,
        "backfill_version": 0,
    },
    {
        "id": 202,
        "tenant_id": "tenant-b",
        "legacy_handle": "kept",
        "canonical_handle": "keep-b",
        "backfill_version": 1,
    },
]


def _experiment_module() -> ModuleType:
    script = (
        PROJECT_ROOT
        / "scripts"
        / "run_assurance_stage3_dml_backfill_experiment.py"
    )
    spec = importlib.util.spec_from_file_location(
        "assurance_stage3_dml_backfill_experiment",
        script,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bind_project_root(
    module: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "PROJECT_ROOT", root.resolve(), raising=False)


def _bind_trusted_snapshot(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    snapshot = {
        "head": "a" * 40,
        "worktree_clean": True,
        "policy": {
            "path": module.POLICY_RELATIVE_PATH,
            "sha256": module.POLICY_SHA256,
        },
        "fixture_sha256": module.FIXTURE_SHA256,
        "plan_digest": module.FROZEN_PLAN_DIGEST,
    }
    monkeypatch.setattr(
        module,
        "_repository_snapshot",
        lambda: copy.deepcopy(snapshot),
    )
    return snapshot


def _read_rows(database_path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT id, tenant_id, legacy_handle, canonical_handle, "
            "backfill_version FROM customer ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row[0],
            "tenant_id": row[1],
            "legacy_handle": row[2],
            "canonical_handle": row[3],
            "backfill_version": row[4],
        }
        for row in rows
    ]


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


def test_stage3_001_runs_bounded_interruption_recovery_and_repeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    trusted_snapshot = _bind_trusted_snapshot(module, monkeypatch)
    monkeypatch.chdir(tmp_path)
    output_dir = Path(".local-validation") / "stage3"

    result = module.run_experiment(output_dir)
    persisted = json.loads(output_dir.joinpath("result.json").read_text(encoding="utf-8"))

    assert result == persisted
    assert result["schema_version"] == 1
    assert result["experiment_id"] == "AV-STAGE3-001"
    assert result["threat_id"] == "T-DB-DML-SCOPE"
    assert result["plan_digest"] == module.FROZEN_PLAN_DIGEST
    assert result["overall_decision"] == "inconclusive"
    assert result["candidate_decision"] == "continue-experiment"
    assert result["evidence_adequacy"] == "insufficient"
    assert result["runtime_integration"] == "disabled"
    assert result["decision_scope"] == "experiment-execution-only"
    assert result["external_quality_gates"]["status"] == "not_evaluated"
    assert module._result_exit_code(result) == 1

    dangerous = result["dangerous_twin"]
    assert dangerous["detector_issues"] == [
        "T-DB-DML-SCOPE:unbounded_update:tenant_or_target_scope_missing"
    ]
    assert dangerous["forced_execution"]["updated_rows"] == 3
    assert dangerous["oracle"]["scope_preserved"] is False
    assert dangerous["oracle"]["out_of_scope_rows"][0]["id"] == 201
    assert dangerous["oracle"]["out_of_scope_rows"][0]["canonical_handle"] == "cust-0201"
    assert dangerous["decision"] == "reject"

    safe = result["safe_twin"]
    assert safe["dry_run"] == {
        "candidate_rows": [101, 102],
        "candidate_count": 2,
        "row_budget": 2,
        "within_budget": True,
        "plan_digest": module.FROZEN_PLAN_DIGEST,
        "write_count": 0,
        "database_unchanged": True,
        "issues": [],
    }
    assert safe["interruption"]["status"] == "interrupted"
    assert safe["interruption"]["updated_ids"] == [101]
    assert (
        safe["interruption"]["process_exit_code"]
        == module.INJECTED_PROCESS_EXIT_CODE
    )
    assert safe["interruption"]["process_boundary_observed"] is True
    assert safe["interruption"]["verification"]["status"] == "interrupted"
    assert safe["recovery"]["status"] == "verified"
    assert safe["recovery"]["updated_ids"] == [102]
    assert safe["recovery"]["skipped_exact_ids"] == [101]
    assert safe["recovery"]["verification"]["status"] == "verified"
    assert safe["repeat"]["status"] == "verified"
    assert safe["repeat"]["updated_ids"] == []
    assert safe["repeat"]["skipped_exact_ids"] == [101, 102]
    assert safe["repeat"]["verification"]["status"] == "verified"
    assert safe["reconciliation"]["passed"] is True
    assert safe["reconciliation"]["rows"] == EXPECTED_FINAL_ROWS
    assert safe["oracle"]["passed"] is True
    assert safe["decision"] == "candidate-passed-local"
    assert result["interruption"] == safe["interruption"]
    assert result["recovery"] == safe["recovery"]
    assert result["reconciliation"] == safe["reconciliation"]

    assert {entry["kind"] for entry in result["evidence"]} == {
        "dangerous-twin",
        "dry-run",
        "interruption",
        "recovery",
        "repeat",
        "reconciliation",
    }
    for entry in result["evidence"]:
        assert entry["snapshot"] == trusted_snapshot
        assert entry["input"]["fixture_sha256"] == module.FIXTURE_SHA256
        assert entry["input"]["plan_digest"] == module.FROZEN_PLAN_DIGEST
        assert set(entry) >= {
            "id",
            "kind",
            "producer",
            "command",
            "environment",
            "snapshot",
            "input",
            "oracle",
            "result",
            "covers",
            "artifacts",
            "artifact_hashes",
            "limitations",
        }
        assert set(entry["artifact_hashes"]) == set(entry["artifacts"])
        assert all(
            len(value) == 64
            for value in entry["artifact_hashes"].values()
        )
        assert str(tmp_path.resolve()) not in json.dumps(entry, ensure_ascii=False)

    assert output_dir.joinpath("dangerous.sqlite").is_file()
    assert output_dir.joinpath("safe.sqlite").is_file()
    assert output_dir.joinpath("dangerous-oracle.json").is_file()
    assert output_dir.joinpath("safe-oracle.json").is_file()
    assert output_dir.joinpath("reconciliation.json").is_file()
    assert output_dir.joinpath("report.md").is_file()
    assert not tmp_path.joinpath("runs").exists()
    assert "assurance-stage3" not in CliRunner().invoke(app, ["--help"]).output

    main_output = Path(".local-validation") / "main-entry"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_assurance_stage3_dml_backfill_experiment.py",
            "--output-dir",
            str(main_output),
        ],
    )

    assert module.main() == 1
    console_output = capsys.readouterr().out
    assert "decision=inconclusive" in console_output
    assert "candidate=continue-experiment" in console_output
    assert "artifact=.local-validation/main-entry/result.json" in console_output
    assert str(tmp_path.resolve()) not in console_output


def test_stage3_001_dry_run_is_read_only(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "dry-run.sqlite"
    module._create_fixture_database(database_path)
    before_hash = module._sha256_file(database_path)

    result = module._run_dry_run(
        database_path,
        copy.deepcopy(module.FROZEN_PLAN),
        module.SAFE_UPDATE_SQL,
    )

    assert result["candidate_rows"] == [101, 102]
    assert result["write_count"] == 0
    assert result["database_unchanged"] is True
    assert module._sha256_file(database_path) == before_hash
    assert _read_rows(database_path) == module.BASELINE_ROWS


def test_stage3_001_missing_tenant_scope_stops_before_write(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "missing-tenant.sqlite"
    module._create_fixture_database(database_path)

    result = module._run_initial_backfill(
        database_path,
        copy.deepcopy(module.FROZEN_PLAN),
        module.SAFE_UPDATE_WITHOUT_TENANT_SQL,
        interrupt_after_batches=None,
    )

    assert result["status"] == "rejected"
    assert result["issues"] == [
        "T-DB-DML-SCOPE:bounded_update:tenant_scope_missing"
    ]
    assert result["updated_ids"] == []
    assert _read_rows(database_path) == module.BASELINE_ROWS


def test_stage3_001_missing_target_scope_stops_before_write(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "missing-target.sqlite"
    module._create_fixture_database(database_path)
    update_without_target_scope = (
        "UPDATE customer "
        "SET canonical_handle = ?, backfill_version = 1 "
        "WHERE tenant_id = ? AND legacy_handle <> ? "
        "AND canonical_handle IS NULL AND backfill_version = 0"
    )

    result = module._run_initial_backfill(
        database_path,
        copy.deepcopy(module.FROZEN_PLAN),
        update_without_target_scope,
        interrupt_after_batches=None,
    )

    assert result["status"] == "rejected"
    assert result["issues"] == [
        "T-DB-DML-SCOPE:bounded_update:target_scope_missing"
    ]
    assert result["updated_ids"] == []
    assert module._checkpoint_row(database_path) is None
    assert _read_rows(database_path) == module.BASELINE_ROWS


def test_stage3_001_out_of_scope_target_stops_before_write(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "out-of-scope-target.sqlite"
    module._create_fixture_database(database_path)
    plan = copy.deepcopy(module.FROZEN_PLAN)
    plan["target_ids"].append(201)
    plan["execution_order"].append(201)
    plan["mapping"]["201"] = "cust-b-0201"
    plan["row_budget"] = 3

    result = module._run_initial_backfill(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=None,
    )

    assert result["status"] == "rejected"
    assert "T-DB-DML-SCOPE:plan:target_set_out_of_scope" in result["issues"]
    assert result["updated_ids"] == []
    assert _read_rows(database_path) == module.BASELINE_ROWS


def test_stage3_001_row_budget_exceeded_is_zero_write(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "row-budget.sqlite"
    module._create_fixture_database(database_path)
    plan = copy.deepcopy(module.FROZEN_PLAN)
    plan["row_budget"] = 1

    result = module._run_initial_backfill(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=None,
    )

    assert result["status"] == "rejected"
    assert "T-DB-DML-SCOPE:plan:row_budget_exceeded" in result["issues"]
    assert result["dry_run"]["candidate_count"] == 2
    assert result["dry_run"]["within_budget"] is False
    assert result["updated_ids"] == []
    assert _read_rows(database_path) == module.BASELINE_ROWS


def test_stage3_001_conflicting_existing_value_is_not_overwritten(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "conflict.sqlite"
    module._create_fixture_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE customer SET canonical_handle = 'wrong-existing', "
            "backfill_version = 1 WHERE id = 101"
        )
        connection.commit()
    finally:
        connection.close()

    result = module._run_initial_backfill(
        database_path,
        copy.deepcopy(module.FROZEN_PLAN),
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=None,
    )

    assert result["status"] == "rejected"
    assert result["conflict_ids"] == [101]
    rows = _read_rows(database_path)
    assert rows[0]["canonical_handle"] == "wrong-existing"
    assert rows[1]["canonical_handle"] is None


def test_stage3_001_recovery_uses_database_fact_over_completed_checkpoint(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "checkpoint-overstates.sqlite"
    module._create_fixture_database(database_path)
    plan = copy.deepcopy(module.FROZEN_PLAN)
    interrupted = module._run_initial_backfill(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=1,
    )
    assert interrupted["status"] == "interrupted"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE backfill_checkpoint "
            "SET completed_count = 2, status = 'completed' "
            "WHERE experiment_id = ?",
            (module.EXPERIMENT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    recovery = module._run_recovery(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
    )

    assert recovery["status"] == "verified"
    assert recovery["updated_ids"] == [102]
    assert recovery["checkpoint_before"]["completed_count"] == 2
    assert recovery["database_fact_before"]["pending_ids"] == [102]
    assert _read_rows(database_path) == EXPECTED_FINAL_ROWS


def test_stage3_001_checkpoint_plan_digest_mismatch_stops_recovery(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "checkpoint-digest.sqlite"
    module._create_fixture_database(database_path)
    plan = copy.deepcopy(module.FROZEN_PLAN)
    interrupted = module._run_initial_backfill(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=1,
    )
    assert interrupted["status"] == "interrupted"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE backfill_checkpoint SET plan_digest = ? "
            "WHERE experiment_id = ?",
            ("0" * 64, module.EXPERIMENT_ID),
        )
        connection.commit()
    finally:
        connection.close()

    recovery = module._run_recovery(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
    )

    assert recovery["status"] == "rejected"
    assert recovery["issues"] == [
        "T-DB-DML-SCOPE:checkpoint:plan_digest_mismatch"
    ]
    assert recovery["updated_ids"] == []
    rows = _read_rows(database_path)
    assert rows[0]["canonical_handle"] == "cust-a-0101"
    assert rows[1]["canonical_handle"] is None


@pytest.mark.parametrize(
    ("checkpoint_update", "expected_issue"),
    [
        pytest.param(
            "target_count = 3",
            "T-DB-DML-SCOPE:checkpoint:target_count_mismatch",
            id="target-count-mismatch",
        ),
        pytest.param(
            "completed_count = 3",
            "T-DB-DML-SCOPE:checkpoint:completed_count_invalid",
            id="completed-count-invalid",
        ),
    ],
)
def test_stage3_001_checkpoint_metadata_mismatch_stops_recovery(
    tmp_path: Path,
    checkpoint_update: str,
    expected_issue: str,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "checkpoint-metadata.sqlite"
    module._create_fixture_database(database_path)
    plan = copy.deepcopy(module.FROZEN_PLAN)
    interrupted = module._run_initial_backfill(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=1,
    )
    assert interrupted["status"] == "interrupted"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            f"UPDATE backfill_checkpoint SET {checkpoint_update} "
            "WHERE experiment_id = ?",
            (module.EXPERIMENT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    recovery = module._run_recovery(
        database_path,
        plan,
        module.SAFE_UPDATE_SQL,
    )

    assert recovery["status"] == "rejected"
    assert recovery["issues"] == [expected_issue]
    assert recovery["updated_ids"] == []
    rows = _read_rows(database_path)
    assert rows[0]["canonical_handle"] == "cust-a-0101"
    assert rows[1]["canonical_handle"] is None


def test_stage3_001_batch_and_checkpoint_are_atomic(
    tmp_path: Path,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "checkpoint-atomicity.sqlite"
    module._create_fixture_database(database_path)
    module._initialize_checkpoint(
        database_path,
        module.FROZEN_PLAN_DIGEST,
        len(module.FROZEN_PLAN["target_ids"]),
    )
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TRIGGER fail_checkpoint_update "
            "BEFORE UPDATE ON backfill_checkpoint "
            "BEGIN "
            "SELECT RAISE(ABORT, 'injected checkpoint failure'); "
            "END"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="checkpoint failure"):
        module._apply_target_batch(
            database_path,
            copy.deepcopy(module.FROZEN_PLAN),
            module.SAFE_UPDATE_SQL,
            101,
            checkpoint_status="running",
        )

    rows = _read_rows(database_path)
    assert rows[0]["canonical_handle"] is None
    assert rows[0]["backfill_version"] == 0
    assert module._checkpoint_row(database_path) == {
        "plan_digest": module.FROZEN_PLAN_DIGEST,
        "target_count": 2,
        "completed_count": 0,
        "status": "running",
    }


def test_stage3_001_independent_oracle_rejects_masked_sentinel_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "masked-danger.sqlite"

    def masked_application_rows(_: Path) -> list[dict[str, Any]]:
        return copy.deepcopy(module.BASELINE_ROWS)

    monkeypatch.setattr(module, "_application_rows", masked_application_rows)
    monkeypatch.setattr(module, "_detect_update_scope", lambda _: [])

    result = module._run_dangerous_twin(database_path)

    assert result["detector_issues"] == []
    assert result["application_view_matches_baseline"] is True
    assert result["oracle"]["scope_preserved"] is False
    assert result["oracle"]["out_of_scope_rows"][0]["canonical_handle"] == "cust-0201"
    assert result["decision"] == "inconclusive"


@pytest.mark.parametrize(
    ("mutation_sql", "mutation_parameters"),
    [
        pytest.param(
            "DELETE FROM customer WHERE id = ?",
            (102,),
            id="missing-target",
        ),
        pytest.param(
            "UPDATE customer SET canonical_handle = ? WHERE id = ?",
            ("wrong-target", 102),
            id="wrong-target-value",
        ),
        pytest.param(
            "UPDATE customer SET canonical_handle = ?, backfill_version = 1 "
            "WHERE id = ?",
            ("changed-sentinel", 201),
            id="out-of-scope-change",
        ),
    ],
)
def test_stage3_001_oracle_rejects_reconciliation_tampering(
    tmp_path: Path,
    mutation_sql: str,
    mutation_parameters: tuple[Any, ...],
) -> None:
    module = _experiment_module()
    database_path = tmp_path / "reconciliation-tampered.sqlite"
    module._create_fixture_database(database_path)
    initial = module._run_initial_backfill(
        database_path,
        copy.deepcopy(module.FROZEN_PLAN),
        module.SAFE_UPDATE_SQL,
        interrupt_after_batches=None,
    )
    assert initial["status"] == "verified"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(mutation_sql, mutation_parameters)
        connection.commit()
    finally:
        connection.close()

    oracle = module._independent_oracle(database_path)

    assert oracle["passed"] is False


def test_stage3_001_evidence_binding_rejects_snapshot_or_artifact_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    snapshot = _bind_trusted_snapshot(module, monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "binding")

    assert result["safe_twin"]["evidence_bindings_valid"] is True
    assert module._evidence_entries_are_bound(
        result["evidence"],
        snapshot,
        Path(".local-validation") / "binding",
    )

    tampered_entries = copy.deepcopy(result["evidence"])
    tampered_entries[-1]["snapshot"]["head"] = "b" * 40
    assert not module._evidence_entries_are_bound(
        tampered_entries,
        snapshot,
        Path(".local-validation") / "binding",
    )

    tampered_entries = copy.deepcopy(result["evidence"])
    tampered_entries[-1]["oracle"]["database_sha256"] = "0" * 64
    assert not module._evidence_entries_are_bound(
        tampered_entries,
        snapshot,
        Path(".local-validation") / "binding",
    )


def test_stage3_001_evidence_binding_rejects_declared_oracle_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    snapshot = _bind_trusted_snapshot(module, monkeypatch)
    monkeypatch.chdir(tmp_path)
    output_dir = Path(".local-validation") / "oracle-binding"

    result = module.run_experiment(output_dir)
    output_dir.joinpath("safe-oracle.json").write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )

    assert not module._evidence_entries_are_bound(
        result["evidence"],
        snapshot,
        output_dir,
    )


def test_stage3_001_policy_hash_sentinel_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    monkeypatch.setattr(module, "POLICY_SHA256", "unavailable")
    snapshot = {
        "head": "a" * 40,
        "worktree_clean": True,
        "policy": {
            "path": module.POLICY_RELATIVE_PATH,
            "sha256": "unavailable",
        },
        "fixture_sha256": module.FIXTURE_SHA256,
        "plan_digest": module.FROZEN_PLAN_DIGEST,
    }

    assert module._snapshot_is_trusted(snapshot) is False


def test_stage3_001_plan_digest_covers_all_frozen_plan_fields() -> None:
    module = _experiment_module()
    mutations = [
        ("tenant_id", "tenant-b"),
        ("target_ids", [102, 101]),
        ("execution_order", [102, 101]),
        ("row_budget", 3),
        ("batch_size", 2),
        ("mapping", {"101": "other", "102": "cust-a-0102"}),
    ]

    for field, value in mutations:
        plan = copy.deepcopy(module.FROZEN_PLAN)
        plan[field] = value
        assert module._plan_digest(plan) != module.FROZEN_PLAN_DIGEST


def test_stage3_001_rejects_output_outside_local_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    _bind_project_root(module, tmp_path, monkeypatch)
    _bind_trusted_snapshot(module, monkeypatch)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match=r"\.local-validation"):
        module.run_experiment(Path("outside"))

    assert not tmp_path.joinpath("outside").exists()


@pytest.mark.parametrize(
    "link_depth",
    [
        pytest.param("root", id="root-link"),
        pytest.param("nested", id="nested-link"),
    ],
)
def test_stage3_001_rejects_linked_output_component(
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
    _bind_trusted_snapshot(module, monkeypatch)
    monkeypatch.chdir(workspace)

    try:
        with pytest.raises(ValueError, match=r"\.local-validation"):
            module.run_experiment(output_dir)

        assert not outside.joinpath("linked").exists()
    finally:
        _remove_directory_link(link_path)
