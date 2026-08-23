from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from vega.cli import app


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _experiment_module() -> ModuleType:
    script = PROJECT_ROOT / "scripts" / "run_assurance_stage2_sqlite_experiment.py"
    spec = importlib.util.spec_from_file_location("assurance_stage2_sqlite_experiment", script)
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
            errors="replace",
            check=False,
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


def test_stage2_sqlite_experiment_rejects_dangerous_twin_and_keeps_safe_twin_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    monkeypatch.chdir(tmp_path)
    output_dir = Path(".local-validation") / "sqlite-twin"

    result = module.run_experiment(output_dir)
    persisted = json.loads(output_dir.joinpath("result.json").read_text(encoding="utf-8"))

    assert result["overall_decision"] == "continue-experiment"
    assert persisted["dangerous_twin"]["decision"] == "reject"
    assert persisted["dangerous_twin"]["execution"]["status"] == "failed"
    assert persisted["dangerous_twin"]["post_failure_matches_baseline"] is True
    assert persisted["safe_twin"]["decision"] == "passed-local"
    assert persisted["safe_twin"]["idempotent_wrapper"] is True
    assert persisted["safe_twin"]["nullable_column"] is True
    assert persisted["safe_twin"]["data_invariant"]["passed"] is True
    assert persisted["safe_twin"]["data_invariant"]["stored_rows_passed"] is True
    assert persisted["safe_twin"]["data_invariant"]["new_app_contract_passed"] is True
    assert persisted["safe_twin"]["data_invariant"]["external_ids"] == [None, None]
    assert persisted["safe_twin"]["data_invariant"]["schema_modes"] == [
        "expanded",
        "expanded",
    ]
    assert all(case["passed"] for case in persisted["safe_twin"]["matrix"].values())
    assert output_dir.joinpath("report.md").is_file()
    assert not tmp_path.joinpath("runs").exists()
    assert "assurance-stage2" not in CliRunner().invoke(app, ["--help"]).output


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("external_id", "unexpected", id="external-id"),
        pytest.param("schema_mode", "unexpected", id="schema-mode"),
    ],
)
def test_stage2_sqlite_matrix_rejects_wrong_new_app_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    module = _experiment_module()
    original_read = module._new_app_reads

    def wrong_contract(connection: Any) -> list[dict[str, Any]]:
        rows = original_read(connection)
        return [
            {**row, field: value}
            if field in row
            else row
            for row in rows
        ]

    monkeypatch.setattr(module, "_new_app_reads", wrong_contract)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "wrong-contract")

    assert result["safe_twin"]["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"
    assert not all(case["passed"] for case in result["safe_twin"]["matrix"].values())


def test_stage2_sqlite_oracle_rejects_data_corruption_after_successful_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    original_apply = module._apply_expand_only_migration
    apply_count = 0

    def corrupt_after_matrix(connection: Any) -> str:
        nonlocal apply_count
        apply_count += 1
        status = original_apply(connection)
        if apply_count == 2:
            connection.execute("UPDATE customer SET display_name = 'CORRUPTED'")
            connection.commit()
        return status

    monkeypatch.setattr(module, "_apply_expand_only_migration", corrupt_after_matrix)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "corrupted")

    assert all(case["passed"] for case in result["safe_twin"]["matrix"].values())
    assert result["safe_twin"]["idempotent_wrapper"] is True
    assert result["safe_twin"]["data_invariant"]["passed"] is False
    assert result["safe_twin"]["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_sqlite_oracle_independently_checks_stored_external_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    original_apply = module._apply_expand_only_migration
    original_read = module._new_app_reads
    apply_count = 0

    def corrupt_stored_external_id(connection: Any) -> str:
        nonlocal apply_count
        apply_count += 1
        status = original_apply(connection)
        if apply_count == 2:
            connection.execute("UPDATE customer SET external_id = 'BROKEN-EXT'")
            connection.commit()
        return status

    def masking_new_app_read(connection: Any) -> list[dict[str, Any]]:
        rows = original_read(connection)
        return [
            {**row, "external_id": None}
            if row.get("external_id") == "BROKEN-EXT"
            else row
            for row in rows
        ]

    monkeypatch.setattr(module, "_apply_expand_only_migration", corrupt_stored_external_id)
    monkeypatch.setattr(module, "_new_app_reads", masking_new_app_read)
    monkeypatch.chdir(tmp_path)

    result = module.run_experiment(Path(".local-validation") / "masked-corruption")

    assert all(case["passed"] for case in result["safe_twin"]["matrix"].values())
    assert result["safe_twin"]["idempotent_wrapper"] is True
    assert result["safe_twin"]["data_invariant"]["new_app_contract_passed"] is True
    assert result["safe_twin"]["data_invariant"]["stored_rows_passed"] is False
    assert result["safe_twin"]["decision"] == "inconclusive"
    assert result["overall_decision"] == "inconclusive"


def test_stage2_sqlite_experiment_rejects_output_outside_local_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
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
def test_stage2_sqlite_experiment_rejects_linked_output_component(
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
    monkeypatch.chdir(workspace)

    try:
        with pytest.raises(ValueError, match=r"\.local-validation"):
            module.run_experiment(output_dir)

        assert not outside.joinpath("linked").exists()
    finally:
        _remove_directory_link(link_path)
