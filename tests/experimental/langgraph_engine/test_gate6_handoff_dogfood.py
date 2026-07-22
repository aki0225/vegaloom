from __future__ import annotations

import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

import scripts.gate6_handoff_dogfood as gate6
from scripts.gate6_handoff_dogfood import _git_guard_snapshot


def test_git_guard_ignores_authorized_tracked_worktree_change(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    baseline = _git_guard_snapshot(repo)

    (repo / "src/labels.py").write_text(
        "VALUE = 'changed'\n",
        encoding="utf-8",
        newline="\n",
    )

    current = _git_guard_snapshot(repo)
    assert current["status_ignored"] == baseline["status_ignored"]


def test_git_guard_detects_new_ignored_path(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = _git_guard_snapshot(repo)

    ignored = repo / ".cache/worker-output.txt"
    ignored.parent.mkdir()
    ignored.write_text("unexpected\n", encoding="utf-8", newline="\n")

    current = _git_guard_snapshot(repo)
    assert current["status_ignored"] != baseline["status_ignored"]


def test_git_guard_ignores_known_verification_caches(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = _git_guard_snapshot(repo)

    cache_file = repo / "src/__pycache__/generated.cpython-312.pyc"
    cache_file.parent.mkdir()
    cache_file.write_bytes(b"synthetic bytecode")

    current = _git_guard_snapshot(repo)
    assert current["status_ignored"] == baseline["status_ignored"]


def test_codex_preflight_uses_resolved_windows_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        gate6.shutil,
        "which",
        lambda executable: (
            "C:/fixtures/bin/codex.CMD" if executable == "codex" else None
        ),
    )

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        commands.append(command)
        if command[1] == "--version":
            return SimpleNamespace(
                returncode=0,
                stdout="codex-cli 0.144.5\n",
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout="Logged in using ChatGPT\n",
            stderr="",
        )

    monkeypatch.setattr(gate6.subprocess, "run", fake_run)

    executable = gate6._resolve_codex_executable("codex")

    assert executable.endswith("codex.CMD")
    assert gate6._codex_version(executable) == "0.144.5"
    assert gate6._codex_auth_mode(executable) == "chatgpt"
    assert commands == [
        ["C:/fixtures/bin/codex.CMD", "--version"],
        ["C:/fixtures/bin/codex.CMD", "login", "status"],
    ]


def test_codex_preflight_fails_closed_when_executable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate6.shutil, "which", lambda _: None)

    with pytest.raises(gate6.Gate6Blocked, match="未找到 codex"):
        gate6._resolve_codex_executable("codex")


def test_codex_preflight_wraps_windows_process_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(*_: object, **__: object) -> object:
        raise FileNotFoundError(2, "synthetic missing wrapper")

    monkeypatch.setattr(gate6.subprocess, "run", fail_start)

    with pytest.raises(gate6.Gate6Blocked, match="Codex 版本检查启动失败"):
        gate6._codex_version("C:/fixtures/bin/codex.CMD")
    with pytest.raises(gate6.Gate6Blocked, match="Codex 登录状态检查启动失败"):
        gate6._codex_auth_mode("C:/fixtures/bin/codex.CMD")


def test_output_dlp_allows_synthetic_fixture_workdir_only() -> None:
    fixture_root = gate6.PROJECT_ROOT / ".tmp" / "gate6-dlp-test"
    fixture = SimpleNamespace(
        root=fixture_root,
        repo=fixture_root / "repo",
        source_chat_text="private source chat",
        memory_ledger_text="accepted memory",
    )

    gate6._assert_output_safe(
        f"workdir: {fixture.repo.resolve()}",
        None,
        fixture,
    )

    with pytest.raises(gate6.Gate6Failure, match="真实项目路径"):
        gate6._assert_output_safe(
            f"workdir: {gate6.PROJECT_ROOT.resolve()}",
            None,
            fixture,
        )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text(
        ".cache/\n__pycache__/\n.pytest_cache/\n",
        encoding="utf-8",
        newline="\n",
    )
    source = repo / "src/labels.py"
    source.parent.mkdir()
    source.write_text("VALUE = 'initial'\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "--", ".gitignore", "src/labels.py")
    _git(
        repo,
        "-c",
        "user.email=gate6@example.invalid",
        "-c",
        "user.name=Gate 6 Test",
        "commit",
        "-m",
        "create fixture",
    )
    return repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
