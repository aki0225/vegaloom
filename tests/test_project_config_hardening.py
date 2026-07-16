from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vega.gate_runtime import GATE_ARTIFACTS, GateRuntime
from vega.project_config import check_project_config, render_project_config_check
from vega.project_profile import ProjectProfileRuntime
from vega.reflect_runtime import ReflectRuntime


def test_config_check_redacts_sensitive_command_and_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_secret = "sk-config-fake-secret-123456"
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - |",
                f"      python -c \"print('{fake_secret}')\"",
                "      python -m pytest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_project_config(repo)
    rendered = render_project_config_check(result)
    dumped = result.model_dump_json(indent=2)

    assert result.status == "failed"
    assert any(issue.code == "multiline_verification_command" for issue in result.issues)
    assert fake_secret not in rendered
    assert fake_secret not in dumped
    assert all(fake_secret not in issue.evidence for issue in result.issues)
    assert all(fake_secret not in command for command in result.verification_commands)
    assert "[REDACTED]" in rendered
    assert "[REDACTED]" in dumped


def test_project_profile_invalid_config_writes_failed_terminal_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_secret = "sk-profile-fake-secret-123456"
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - |",
                f"      python -c \"print('{fake_secret}')\"",
                "      python -m pytest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    run_dir = ProjectProfileRuntime(tmp_path).run(repo)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    trace_events = [
        json.loads(line)
        for line in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    combined_artifacts = "\n".join(
        run_dir.joinpath(name).read_text(encoding="utf-8")
        for name in [
            "state.json",
            "trace.jsonl",
            "project-profile.json",
            "project-profile.md",
            "eval.md",
        ]
    )

    assert state["status"] == "failed"
    assert state["current_step"] == "project_config_invalid_failed"
    assert trace_events[-1]["event"] == "run_finished"
    assert trace_events[-1]["status"] == "failed"
    assert "multiline_verification_command" in run_dir.joinpath("eval.md").read_text(
        encoding="utf-8"
    )
    assert "Vega Config Check" in run_dir.joinpath("project-profile.md").read_text(
        encoding="utf-8"
    )
    assert fake_secret not in combined_artifacts
    assert "[REDACTED]" in combined_artifacts


def test_gate_invalid_config_writes_redacted_failed_terminal_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    fake_secret = "sk-gate-config-fake-secret-123456"
    config_path = repo / ".vega.yaml"
    config_text = (
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - |",
                f"      python -c \"print('{fake_secret}')\"",
                "      python -m pytest",
            ]
        )
        + "\n"
    )
    config_path.write_text(config_text, encoding="utf-8")
    workspace.mkdir()
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8")
    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)

    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)

    state = json.loads(gate_run.joinpath("state.json").read_text(encoding="utf-8"))
    result = json.loads(gate_run.joinpath("gate-result.json").read_text(encoding="utf-8"))
    trace_events = [
        json.loads(line)
        for line in gate_run.joinpath("trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    combined_artifacts = "\n".join(
        gate_run.joinpath(name).read_text(encoding="utf-8")
        for name in GATE_ARTIFACTS
    )

    assert all(gate_run.joinpath(name).is_file() for name in GATE_ARTIFACTS)
    assert state["status"] == "failed"
    assert state["current_step"] == "project_config_invalid_failed"
    assert state["artifacts"] == GATE_ARTIFACTS
    assert result["status"] == "failed"
    assert result["code"] == "project_config_invalid"
    assert result["config_check"]["status"] == "failed"
    assert any(
        issue["code"] == "multiline_verification_command"
        for issue in result["config_check"]["issues"]
    )
    assert trace_events[-1]["event"] == "run_finished"
    assert trace_events[-1]["status"] == "failed"
    assert "multiline_verification_command" in gate_run.joinpath("eval.md").read_text(
        encoding="utf-8"
    )
    assert "Vega Config Check" in gate_run.joinpath("gate-report.md").read_text(
        encoding="utf-8"
    )
    assert config_path.read_text(encoding="utf-8") == config_text
    assert fake_secret not in combined_artifacts
    assert "[REDACTED]" in combined_artifacts


def _init_changed_git_repo(repo: Path) -> None:
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
    repo.joinpath("README.md").write_text(
        "# Demo\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", "README.md"],
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
    repo.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )
