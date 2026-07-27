from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import vega.git_read as git_read_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_git_reads_trust_only_the_selected_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(hygiene.subprocess, "run", fake_run)

    assert hygiene._run_git(repo, "status", "--short") == b"ok"
    assert commands == [
        [
            "git",
            "-c",
            f"safe.directory={repo.resolve().as_posix()}",
            "status",
            "--short",
        ]
    ]


def test_controlled_git_reads_disable_replace_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

    git_read_module.run_git_capture(repo, ["git", "status", "--short"])

    assert calls[0]["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_run_git_text_requires_success_and_returns_only_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    results = iter(
        [
            subprocess.CompletedProcess(
                ["git"],
                0,
                stdout=b"trusted evidence\n",
                stderr=b"warning is not evidence\n",
            ),
            subprocess.CompletedProcess(
                ["git"],
                1,
                stdout=b"partial output\n",
                stderr=b"fatal: read failed\n",
            ),
        ]
    )
    monkeypatch.setattr(
        git_read_module,
        "run_git_capture",
        lambda repo_path, command: next(results),
    )

    assert git_read_module.run_git_text(repo, ["git", "status"]) == (
        "trusted evidence\n"
    )
    with pytest.raises(RuntimeError, match="fatal: read failed"):
        git_read_module.run_git_text(repo, ["git", "status"])


def test_ruff_lint_selection_is_explicit_and_stable() -> None:
    config = tomllib.loads(
        PROJECT_ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8")
    )

    assert config["tool"]["ruff"]["lint"]["select"] == ["E4", "E7", "E9", "F"]


def test_history_scan_catches_path_removed_by_later_commit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
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


def test_staged_text_violation_cannot_be_hidden_by_safe_worktree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    guide = repo / "guide.md"
    guide.write_text("use docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "base")

    alternate_index = tmp_path / "safe-alternate.index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))
    _git(repo, "read-tree", "HEAD")
    assert _git(repo, "show", ":guide.md").stdout == "use docs/guide.md\n"

    monkeypatch.delenv("GIT_INDEX_FILE")
    leaked_path = _windows_path("Users", "alice", "repo")
    guide.write_text(f"local path: {leaked_path}\n", encoding="utf-8")
    _git(repo, "add", "--", "guide.md")
    guide.write_text("use docs/guide.md\n", encoding="utf-8")

    assert leaked_path in _git(repo, "show", ":guide.md").stdout
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))
    assert leaked_path not in _git(repo, "show", ":guide.md").stdout

    exit_code = hygiene.main(["--repo", str(repo), "--base-ref", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Windows 盘符绝对路径" in captured.err
    assert leaked_path not in captured.err


def test_gitlink_without_local_commit_object_is_allowed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("guide.md").write_text("use docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "base")
    missing_commit = "1" * 40

    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{missing_commit},vendor/dependency",
    )
    missing = subprocess.run(
        ["git", "cat-file", "-e", missing_commit],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0

    exit_code = hygiene.main(["--repo", str(repo), "--base-ref", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "仓库路径与私密文件卫生检查通过" in captured.out


def test_intent_to_add_content_is_checked_from_worktree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("guide.md").write_text("use docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "base")
    leaked_path = _windows_path("Users", "alice", "repo")
    draft = repo / "draft.md"
    draft.write_text(f"local path: {leaked_path}\n", encoding="utf-8")
    _git(repo, "add", "-N", "--", "draft.md")

    assert _git(repo, "diff", "--cached", "--name-only").stdout == ""

    exit_code = hygiene.main(["--repo", str(repo), "--base-ref", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Windows 盘符绝对路径" in captured.err
    assert leaked_path not in captured.err


def test_main_detects_index_change_during_later_scans(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("guide.md").write_text("use docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "base")

    def mutate_index_during_worktree_scan(
        repo_root: Path,
        *,
        head_commit: str | None = None,
    ):
        assert head_commit
        repo_root.joinpath("late.md").write_text("safe\n", encoding="utf-8")
        _git(repo_root, "add", "--", "late.md")
        return []

    monkeypatch.setattr(
        hygiene,
        "scan_worktree",
        mutate_index_during_worktree_scan,
    )

    exit_code = hygiene.main(["--repo", str(repo)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Git index 在仓库卫生检查期间发生变化" in captured.err


def test_main_detects_head_change_during_check(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    repo.joinpath("guide.md").write_text("use docs/guide.md\n", encoding="utf-8")
    _commit_all(repo, "base")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head_reads = iter([head, "f" * 40])
    monkeypatch.setattr(
        hygiene,
        "_read_head_commit",
        lambda repo_root: next(head_reads),
    )

    exit_code = hygiene.main(["--repo", str(repo)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Git HEAD 在仓库卫生检查期间发生变化" in captured.err


def test_unmerged_index_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    guide = repo / "guide.md"
    guide.write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    base_branch = _git(repo, "branch", "--show-current").stdout.strip()

    _git(repo, "switch", "-c", "other")
    guide.write_text("other\n", encoding="utf-8")
    _commit_all(repo, "other change")
    _git(repo, "switch", base_branch)
    guide.write_text("current\n", encoding="utf-8")
    _commit_all(repo, "current change")

    merge = subprocess.run(
        ["git", "merge", "other"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert merge.returncode != 0
    assert _git(repo, "ls-files", "--unmerged").stdout

    exit_code = hygiene.main(["--repo", str(repo)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Git index 包含未解决冲突" in captured.err


def test_eval_history_rewrite_is_rejected_even_when_later_restored(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    evidence = repo / "eval" / "real-world-runs.md"
    evidence.parent.mkdir()
    evidence.write_text("failed\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "base evidence")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    evidence.write_text("success\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "rewrite evidence")
    evidence.write_text("failed\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "restore evidence")

    violations = hygiene.scan_history(repo, base_sha)

    assert any(item.rule == "eval-not-append-only" for item in violations)


def test_eval_worktree_rewrite_is_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    evidence = repo / "eval" / "real-world-runs.md"
    evidence.parent.mkdir()
    evidence.write_text("failed\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "base evidence")

    evidence.write_text("success\n", encoding="utf-8", newline="\n")

    violations = hygiene.scan_worktree(repo)

    assert any(item.rule == "eval-not-append-only" for item in violations)


@pytest.mark.parametrize(
    "staged_change",
    [
        pytest.param("rewrite", id="暂存改写"),
        pytest.param("deletion", id="暂存删除"),
    ],
)
def test_eval_index_violation_cannot_be_hidden_by_worktree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    staged_change: str,
) -> None:
    repo = _init_repo(tmp_path)
    evidence = repo / "eval" / "real-world-runs.md"
    evidence.parent.mkdir()
    evidence.write_text("failed\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "base evidence")

    if staged_change == "rewrite":
        evidence.write_text("success\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "--", "eval/real-world-runs.md")
        evidence.write_text("failed\n", encoding="utf-8", newline="\n")
        assert _git(repo, "show", ":eval/real-world-runs.md").stdout == "success\n"
    else:
        _git(repo, "rm", "--cached", "--", "eval/real-world-runs.md")
        assert evidence.read_text(encoding="utf-8") == "failed\n"

    exit_code = hygiene.main(["--repo", str(repo), "--base-ref", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "eval/ 证据文件只允许尾部追加" in captured.err


def test_eval_tail_append_and_new_file_are_allowed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    evidence = repo / "eval" / "real-world-runs.md"
    evidence.parent.mkdir()
    evidence.write_text("failed\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "base evidence")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    with evidence.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("next run\n")
    repo.joinpath("eval", "new-case.jsonl").write_text(
        '{"status":"failed"}\n',
        encoding="utf-8",
        newline="\n",
    )
    _git(
        repo,
        "add",
        "--",
        "eval/real-world-runs.md",
        "eval/new-case.jsonl",
    )

    assert not any(
        item.rule == "eval-not-append-only"
        for item in hygiene.scan_worktree(repo)
    )
    assert not any(
        item.rule == "eval-not-append-only"
        for item in hygiene.scan_index(repo)
    )

    _commit_all(repo, "append evidence")

    assert not any(
        item.rule == "eval-not-append-only"
        for item in hygiene.scan_history(repo, base_sha)
    )


def test_eval_worktree_line_ending_conversion_is_not_a_rewrite(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    evidence = repo / "eval" / "real-world-runs.md"
    evidence.parent.mkdir()
    evidence.write_text("failed\n", encoding="utf-8", newline="\n")
    _commit_all(repo, "base evidence")

    evidence.write_bytes(b"failed\r\nnext run\r\n")

    assert not any(
        item.rule == "eval-not-append-only"
        for item in hygiene.scan_worktree(repo)
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
