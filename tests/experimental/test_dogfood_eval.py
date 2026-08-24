

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dogfood_eval_case_selection_contract() -> None:
    from scripts.dogfood_eval import _case_registry, select_case_names

    available_names = [name for name, _ in _case_registry()]

    assert select_case_names([], available_names) == available_names
    assert select_case_names(
        ["execution_control", "execution_control", "goal_p0_lifecycle"],
        available_names,
    ) == ["execution_control", "goal_p0_lifecycle"]
    with pytest.raises(ValueError, match="未知 dogfood case：unknown-case"):
        select_case_names(["unknown-case"], available_names)


@pytest.mark.parametrize(
    "case_name",
    [
        "core_loop_without_memory",
        "explicit_memory_lesson",
        "config_check_invalid_verification",
        "execution_control",
        "workspace_pollution_guard",
        "prompt_budget_guard",
        "large_scope_gate",
        "goal_p0_lifecycle",
    ],
)
def test_dogfood_eval_covers_core_loop_memory_boundary_and_goal_p0(
    tmp_path_factory: pytest.TempPathFactory,
    case_name: str,
) -> None:
    workspace = tmp_path_factory.mktemp("d")
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "dogfood_eval.py"),
            "--runner",
            "none",
            "--workspace",
            str(workspace),
            "--case",
            case_name,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "成功：1/1" in result.stdout
    summary_path = next(workspace.joinpath("runs").glob("dogfood-eval-*/summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["success_count"] == 1
    assert summary["case_count"] == 1
    assert [case["name"] for case in summary["cases"]] == [case_name]


def test_dogfood_eval_rejects_unknown_case_before_workspace_creation(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace = tmp_path_factory.mktemp("dogfood-invalid") / "workspace"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "dogfood_eval.py"),
            "--runner",
            "none",
            "--workspace",
            str(workspace),
            "--case",
            "unknown-case",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "未知 dogfood case" in result.stderr
    assert not workspace.exists()
