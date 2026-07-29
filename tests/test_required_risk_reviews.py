from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.gate_runtime import GateRuntime, _run_gate_eval
from vega.project_config import check_project_config
from vega.reflect_runtime import ReflectRuntime


def test_required_reviews_reject_duplicate_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: 支付与资金",
                "      paths:",
                "        - src/money/**",
                "    - id: payment",
                "      label: 重复领域",
                "      paths:",
                "        - src/billing/**",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    assert [issue.code for issue in result.issues] == ["invalid_project_config"]
    assert "id 必须唯一" in result.issues[0].evidence


def test_required_reviews_reject_duplicate_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: 支付与资金",
                "      paths:",
                "        - src/money/**",
                "        - src/money/**",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    assert [issue.code for issue in result.issues] == ["invalid_project_config"]
    assert "paths 不能包含重复规则" in result.issues[0].evidence


@pytest.mark.parametrize(
    "pattern",
    [
        "../outside.py",
        "/absolute.py",
        "C:/outside.py",  # repo-path-policy: allow-test-fixture
        r"src\money\**",
        "src//money/**",
    ],
)
def test_required_reviews_reuse_strict_scope_glob_validation(
    tmp_path: Path,
    pattern: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: 支付与资金",
                "      paths:",
                f"        - {json.dumps(pattern)}",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    assert [issue.code for issue in result.issues] == ["invalid_project_config"]
    assert "required_reviews.paths" in result.issues[0].evidence


def test_gate_records_segment_glob_required_review_hits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="\n".join(
            [
                "version: 1",
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: Payment",
                "      paths:",
                "        - src/money/**",
                "    - id: concurrency",
                "      label: Concurrency",
                "      paths:",
                "        - src/jobs/*.py",
            ]
        )
        + "\n",
        files={
            "src/money/charge.py": "VALUE = 1\n",
            "src/moneybox/ignored.py": "VALUE = 1\n",
            "src/jobs/runner.py": "VALUE = 1\n",
            "src/jobs/nested/worker.py": "VALUE = 1\n",
        },
    )
    for relative_path in (
        "src/money/charge.py",
        "src/moneybox/ignored.py",
        "src/jobs/runner.py",
        "src/jobs/nested/worker.py",
    ):
        repo.joinpath(relative_path).write_text(
            "VALUE = 2\n",
            encoding="utf-8",
            newline="\n",
        )
    workspace.mkdir()
    test_log = workspace / "tests.log"
    test_log.write_text("4 passed\n", encoding="utf-8", newline="\n")

    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    result = _read_json(gate_run / "gate-result.json")
    state = _read_json(gate_run / "state.json")
    report = (gate_run / "gate-report.md").read_text(encoding="utf-8")
    eval_text = (gate_run / "eval.md").read_text(encoding="utf-8")

    assert result["risk"] == "high"
    assert result["recommendation"] == "human-review"
    assert result["required_reviews"] == [
        {
            "id": "payment",
            "label": "Payment",
            "matched_files": ["src/money/charge.py"],
        },
        {
            "id": "concurrency",
            "label": "Concurrency",
            "matched_files": ["src/jobs/runner.py"],
        },
    ]
    assert state["required_reviews"] == result["required_reviews"]
    assert any(
        reason["code"] == "required_risk_review"
        and reason["severity"] == "high"
        for reason in result["reasons"]
    )
    assert "## 必须披露的风险审查" in report
    assert "### Payment (`payment`)" in report
    assert "### Concurrency (`concurrency`)" in report
    assert "PASS: 必须披露风险命中保持 fail-closed 语义" in eval_text
    assert "FAIL:" not in eval_text


def test_gate_eval_binds_required_reviews_to_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="\n".join(
            [
                "version: 1",
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: Payment",
                "      paths:",
                "        - src/money/**",
            ]
        )
        + "\n",
        files={"src/money/charge.py": "VALUE = 1\n"},
    )
    repo.joinpath("src/money/charge.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8", newline="\n")
    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)
    state_path = gate_run / "state.json"
    state = _read_json(state_path)
    state["required_reviews"] = []
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    results = _run_gate_eval(gate_run)

    assert "FAIL: gate-result.json 与 GateState 身份或关键字段不一致" in results


def _init_repo(
    repo: Path,
    *,
    config: str,
    files: dict[str, str],
) -> None:
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- Run minimal verification.\n",
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath(".vega.yaml").write_text(
        config,
        encoding="utf-8",
        newline="\n",
    )
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    subprocess.run(
        ["git", "add", "--", "AGENTS.md", ".vega.yaml", *files],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
