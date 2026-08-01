from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega import git_read, project_config, project_config_preflight
from vega.gate_runtime import GATE_ARTIFACTS, GateRuntime
from vega.project_config import check_project_config, render_project_config_check
from vega.project_profile import ProjectProfileRuntime
from vega.reflect_runtime import ReflectRuntime


@pytest.mark.parametrize(
    "placeholder",
    [
        "{{vega_other_temp}}",
        "{{vega_verification_temp_extra}}",
        "{{vega_}}",
    ],
)
def test_config_check_rejects_unknown_vega_verification_placeholder(
    tmp_path: Path,
    placeholder: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                f"    - echo '{placeholder}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    issues = [
        issue
        for issue in result.issues
        if issue.code == "unknown_verification_placeholder"
    ]
    assert len(issues) == 1
    assert placeholder in issues[0].evidence
    assert "{{vega_verification_temp}}" in issues[0].message


@pytest.mark.parametrize(
    "command",
    [
        "echo {{vega_verification_temp}}",
        (
            "python -c \"print('ok')\" "
            "{{vega_verification_temp}}/runs"
        ),
        (
            "python -m pytest "
            "--basetemp={{vega_verification_temp}}/runs "
            "-o cache_dir={{vega_verification_temp}}/cache"
        ),
        r"echo ^%VEGA_Q^% {{vega_verification_temp}}",
    ],
)
def test_config_check_allows_exact_verification_temp_placeholder(
    tmp_path: Path,
    command: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                f"    - {json.dumps(command)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_project_config(repo)

    assert not any(
        issue.code == "unknown_verification_placeholder"
        for issue in result.issues
    )
    assert not any(
        issue.code == "unsafe_verification_temp_placeholder_context"
        for issue in result.issues
    )


@pytest.mark.parametrize(
    ("command", "shell_kind"),
    [
        ('echo "{{vega_verification_temp}}"', None),
        ("echo '{{vega_verification_temp}}'", None),
        ('echo "prefix {{vega_verification_temp}} suffix"', None),
        ("echo prefix{{vega_verification_temp}}", None),
        ("echo {{vega_verification_temp}}suffix", None),
        (r'echo \" {{vega_verification_temp}} \"', None),
        (r'echo ^"prefix" {{vega_verification_temp}}', None),
        pytest.param(
            r"echo %VEGA_Q% {{vega_verification_temp}} %VEGA_Q%",
            "cmd",
            id="cmd-dynamic-percent-expansion",
        ),
    ],
)
def test_config_check_rejects_unsafe_verification_temp_placeholder_context(
    tmp_path: Path,
    command: str,
    shell_kind: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shell_kind is not None:
        monkeypatch.setattr(
            project_config,
            "current_verification_shell_kind",
            lambda: shell_kind,
        )
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                f"    - {json.dumps(command)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    assert any(
        issue.code == "unsafe_verification_temp_placeholder_context"
        for issue in result.issues
    )


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


def test_config_check_warns_when_project_config_is_not_tracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n  commands:\n    - echo ok\n",
        encoding="utf-8",
    )

    result = check_project_config(repo)

    assert result.status == "passed"
    issue = next(
        issue
        for issue in result.issues
        if issue.code == "project_config_not_tracked"
    )
    assert issue.severity == "warning"
    assert issue.evidence == ".vega.yaml"


def test_config_check_warns_when_pytest_src_import_path_is_unspecified(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath("setup.cfg").write_text("[metadata]\nname = demo\n", encoding="utf-8")
    package = repo / "src" / "demo"
    package.mkdir(parents=True)
    package.joinpath("__init__.py").write_text("", encoding="utf-8")
    config_path = repo / ".vega.yaml"
    config_path.write_text(
        "version: 1\nverification:\n  commands:\n    - python -m pytest -q\n",
        encoding="utf-8",
    )
    _stage_paths(repo, ".vega.yaml")

    result = check_project_config(repo)

    assert result.status == "passed"
    issue = next(
        issue
        for issue in result.issues
        if issue.code == "pytest_src_import_path_unspecified"
    )
    assert issue.evidence == "verification.commands[1]"

    config_path.write_text(
        "version: 1\nverification:\n  commands:\n"
        "    - python -m pytest -q -o pythonpath=src\n",
        encoding="utf-8",
    )
    corrected = check_project_config(repo)

    assert not any(
        issue.code == "pytest_src_import_path_unspecified"
        for issue in corrected.issues
    )


def test_config_check_warns_when_windows_autocrlf_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_changed_git_repo(repo)
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n  commands:\n    - echo ok\n",
        encoding="utf-8",
    )
    _stage_paths(repo, ".vega.yaml")
    monkeypatch.setattr(
        project_config_preflight,
        "_is_windows_environment",
        lambda: True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    result = check_project_config(repo)

    assert result.status == "passed"
    assert any(issue.code == "windows_autocrlf_enabled" for issue in result.issues)

    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    corrected = check_project_config(repo)

    assert not any(
        issue.code == "windows_autocrlf_enabled"
        for issue in corrected.issues
    )


def test_config_check_skips_optional_preflight_when_git_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text("version: 1\n", encoding="utf-8")

    def fail_git_read(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr(project_config_preflight, "run_git_capture", fail_git_read)

    result = check_project_config(repo)

    assert result.status == "passed"
    assert not any(
        issue.code.startswith(("project_config_", "pytest_src_", "windows_"))
        for issue in result.issues
    )


def test_config_check_fails_when_git_security_configuration_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text("version: 1\n", encoding="utf-8")

    def fail_git_security_check(*args, **kwargs):
        raise RuntimeError("safe.directory mismatch")

    monkeypatch.setattr(
        project_config_preflight,
        "run_git_capture",
        fail_git_security_check,
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    assert any(issue.code == "repository_preflight_failed" for issue in result.issues)


def test_git_config_read_does_not_fallback_after_repository_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        git_read,
        "run_git_capture",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["git", "config"],
            returncode=128,
            stdout=b"",
            stderr=b"fatal",
        ),
    )

    def reject_fallback(*args, **kwargs):
        pytest.fail("repository Git error must not fall back to lower precedence")

    monkeypatch.setattr(git_read, "_read_scoped_git_config", reject_fallback)

    assert git_read.read_git_config_value(tmp_path, "core.autocrlf") is None


@pytest.mark.parametrize(
    "pattern",
    [
        "../outside.py",
        "/absolute.py",
        "C:/outside.py",  # repo-path-policy: allow-test-fixture
        r"src\windows-style.py",
        "tests//double-slash.py",
        "safe\u202e.py",
        "src/sk-abcdefghijkl.py",
    ],
)
def test_config_check_rejects_unsafe_scope_patterns(tmp_path: Path, pattern: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "scope:",
                "  allowed_paths:",
                f"    - '{pattern}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_project_config(repo)

    assert result.status == "failed"
    assert [issue.code for issue in result.issues] == ["invalid_project_config"]
    assert "allowed_paths" in result.issues[0].evidence


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


def _stage_paths(repo: Path, *paths: str) -> None:
    subprocess.run(
        ["git", "add", "--", *paths],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
