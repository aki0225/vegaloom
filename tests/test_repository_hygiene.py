from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def _load_hygiene_module():
    script_path = Path(__file__).parents[1] / "scripts" / "check_repository_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_repository_hygiene", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hygiene = _load_hygiene_module()


def _windows_path(*parts: str) -> str:
    return "C:" + "\\" + "\\".join(parts)


def _unc_path() -> str:
    return "\\" * 2 + "build-server" + "\\" + "private-share" + "\\" + "repo"


def _posix_home_path() -> str:
    return "/" + "home" + "/" + "alice" + "/" + "repo"


@pytest.mark.parametrize(
    ("value", "expected_rule"),
    [
        pytest.param(_windows_path("Users", "alice", "repo"), "windows-drive-absolute"),
        pytest.param(_unc_path(), "windows-unc"),
        pytest.param(_posix_home_path(), "posix-user-home"),
    ],
)
def test_find_text_violations_rejects_machine_specific_paths(
    value: str,
    expected_rule: str,
) -> None:
    violations = hygiene.find_text_violations("docs/guide.md", f"路径：{value}")

    assert [item.rule for item in violations] == [expected_rule]


def test_find_text_violations_allows_repository_relative_paths() -> None:
    text = "\n".join(
        [
            "读取 `docs/ARCHITECTURE.md`。",
            '$worktreePath = "<worktree-path>"',
            "$repoRoot = Resolve-Path .",
        ]
    )

    assert hygiene.find_text_violations("docs/guide.md", text) == []


def test_explicit_test_fixture_marker_allows_path_only_in_tests() -> None:
    value = _windows_path("repo", "src", "app.py")
    text = f'value = r"{value}"  {hygiene.ALLOW_TEST_FIXTURE_MARKER}'

    assert hygiene.find_text_violations("tests/test_example.py", text) == []


def test_fixture_marker_cannot_bypass_documentation_policy() -> None:
    value = _windows_path("Users", "alice", "repo")
    text = f"`{value}` {hygiene.ALLOW_TEST_FIXTURE_MARKER}"

    violations = hygiene.find_text_violations("docs/guide.md", text)

    assert [item.rule for item in violations] == ["invalid-fixture-exemption"]


def test_unused_fixture_marker_fails_closed() -> None:
    violations = hygiene.find_text_violations(
        "tests/test_example.py",
        f'value = "relative/path"  {hygiene.ALLOW_TEST_FIXTURE_MARKER}',
    )

    assert [item.rule for item in violations] == ["unused-fixture-exemption"]


def test_sensitive_filenames_are_rejected_but_env_example_is_allowed() -> None:
    rejected = [
        ".env",
        "config/.env.local",
        "credentials.json",
        "certs/service.pem",
        "review/private.docx",
        "state/cache.sqlite",
    ]

    assert all(
        hygiene.find_sensitive_filename_violation(path) is not None for path in rejected
    )
    assert hygiene.find_sensitive_filename_violation(".env.example") is None


def test_history_scan_catches_path_removed_by_later_commit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    guide = repo / "guide.md"
    guide.write_text("使用 docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    leaked_path = _windows_path("Users", "alice", "repo")
    guide.write_text(f"本机路径：{leaked_path}\n", encoding="utf-8")
    _commit_all(repo, f"add machine path {leaked_path}")
    guide.write_text("使用 docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "remove machine path")

    assert hygiene.scan_worktree(repo) == []
    violations = hygiene.scan_history(repo, base_sha)
    assert any(item.rule == "windows-drive-absolute" for item in violations)
    assert any(item.relative_path == "<commit-message>" for item in violations)

    exit_code = hygiene.main(["--repo", str(repo), "--base-ref", base_sha])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert leaked_path not in captured.err
    assert str(repo) not in captured.err


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
