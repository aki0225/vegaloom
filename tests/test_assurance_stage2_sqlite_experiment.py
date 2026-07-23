from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from vega.cli import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _experiment_module() -> ModuleType:
    script = PROJECT_ROOT / "scripts" / "run_assurance_stage2_sqlite_experiment.py"
    spec = importlib.util.spec_from_file_location("assurance_stage2_sqlite_experiment", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
    assert all(case["passed"] for case in persisted["safe_twin"]["matrix"].values())
    assert output_dir.joinpath("report.md").is_file()
    assert not tmp_path.joinpath("runs").exists()
    assert "assurance-stage2" not in CliRunner().invoke(app, ["--help"]).output


def test_stage2_sqlite_experiment_rejects_output_outside_local_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _experiment_module()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match=r"\.local-validation"):
        module.run_experiment(Path("outside"))

    assert not tmp_path.joinpath("outside").exists()
