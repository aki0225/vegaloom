from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega import workspace_check as workspace_check_module
from vega.brief_runtime import BriefRuntime
from vega.models import BriefInput
from vega.project_config import load_project_config
from vega.project_knowledge import load_agents_instructions
from vega.project_profile import build_project_profile
from vega.redaction import REDACTION_TEXT, redact_text
from vega.experimental.inspection.runtime import EngineeringChangeRuntime
from vega.workspace_check import capture_review_workspace, evaluate_workspace


def test_redaction_covers_aws_gitlab_and_database_credentials() -> None:
    aws_secret = "a" * 40
    gitlab_token = "glpat-" + "b" * 24
    database_password = "db-password-123"
    text = "\n".join(
        [
            f"AWS_SECRET_ACCESS_KEY={aws_secret}",
            f"gitlab_token={gitlab_token}",
            f"DATABASE_URL=postgresql://app:{database_password}@db.example.test/app",
            f"connect postgresql://reader:{database_password}@db.example.test/reporting",
        ]
    )

    redacted = redact_text(text)

    assert aws_secret not in redacted
    assert gitlab_token not in redacted
    assert database_password not in redacted
    assert redacted.count(REDACTION_TEXT) >= 4
    assert "db.example.test/reporting" in redacted


def test_redaction_preserves_ordinary_source_expressions() -> None:
    text = "\n".join(
        [
            "password: str",
            "password = candidate",
            'password = get_secret("name")',
            "password = compute_password(user)",
            "client_secret = settings.client_secret",
            "database_url = build_database_url(settings)",
        ]
    )

    assert redact_text(text) == text


def test_engineering_runtime_rejects_sensitive_task_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    repo.mkdir()
    task = tmp_path / ".env"
    task.write_text("PASSWORD=must-not-be-read\n", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):
        if path == task:
            raise AssertionError("sensitive task content must not be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(
        "vega.experimental.inspection.runtime.LLMClient.from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("sensitive task must be rejected before LLM initialization")
        ),
    )

    with pytest.raises(ValueError, match="environment_file"):
        EngineeringChangeRuntime(workspace).run(task, repo)

    assert not workspace.joinpath("runs").exists()


def test_runtime_state_and_artifacts_do_not_persist_package_credentials(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "# Demo\n"})
    npm_token = "npm_" + "c" * 24
    pypi_token = "pypi-" + "d" * 24

    run_dir = BriefRuntime(workspace).run(
        BriefInput(
            mode="bug",
            text=(
                "修复凭据问题："
                f"//registry.npmjs.org/:_authToken={npm_token} "
                f"password = {pypi_token}"
            ),
            source=f"https://build-user:build-password@example.test/job?token={npm_token}",
            repo_path=str(repo),
        )
    )

    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*")
        if path.is_file()
    )

    assert npm_token not in persisted
    assert pypi_token not in persisted
    assert "build-password" not in persisted
    assert REDACTION_TEXT in persisted
    assert run_dir.joinpath("state.json").exists()
    assert run_dir.joinpath("agent-brief.md").exists()


def test_tracked_agents_instructions_read_fixed_head_blob(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {"AGENTS.md": "# Rules\n\n- TRACKED_HEAD_RULE\n"})
    revision = _git(repo, "rev-parse", "HEAD").strip()
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- MUTABLE_WORKTREE_RULE\n",
        encoding="utf-8",
    )

    instructions = load_agents_instructions(
        repo,
        ["src/example.py"],
        tracked_only=True,
        tracked_revision=revision,
    )

    assert len(instructions) == 1
    assert "TRACKED_HEAD_RULE" in instructions[0].content
    assert "MUTABLE_WORKTREE_RULE" not in instructions[0].content


def test_tracked_agents_instructions_use_git_blob_in_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "linked-worktree"
    _init_repo(repo, {"AGENTS.md": "# Rules\n\n- TRACKED_HEAD_RULE\n"})
    _git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
    assert worktree.joinpath(".git").is_file()
    revision = _git(worktree, "rev-parse", "HEAD").strip()
    worktree.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- MUTABLE_WORKTREE_RULE\n",
        encoding="utf-8",
    )

    instructions = load_agents_instructions(
        worktree,
        ["src/example.py"],
        tracked_only=True,
        tracked_revision=revision,
    )

    assert len(instructions) == 1
    assert "TRACKED_HEAD_RULE" in instructions[0].content
    assert "MUTABLE_WORKTREE_RULE" not in instructions[0].content


def test_tracked_project_config_reads_fixed_head_blob(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            ".vega.yaml": (
                "version: 1\nprompt_budget:\n  reviewer_max_chars: 60000\n"
            ),
        },
    )
    revision = _git(repo, "rev-parse", "HEAD").strip()
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nprompt_budget:\n  reviewer_max_chars: 1000\n",
        encoding="utf-8",
    )

    config = load_project_config(
        repo,
        tracked_only=True,
        tracked_revision=revision,
    )

    assert config.prompt_budget.reviewer_max_chars == 60000


def test_tracked_project_profile_uses_head_tree_after_worktree_deletions(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            ".vega.yaml": (
                "version: 1\nverification:\n"
                "  commands:\n"
                "    - python -m pytest tracked\n"
            ),
            "Demo.csproj": "<Project />\n",
            "pyproject.toml": "[project]\nname = \"demo\"\n",
            "src/main.py": "print('tracked')\n",
            "tests/test_demo.py": "def test_demo():\n    assert True\n",
        },
    )
    for relative_path in [
        ".vega.yaml",
        "Demo.csproj",
        "pyproject.toml",
        "src/main.py",
        "tests/test_demo.py",
    ]:
        repo.joinpath(relative_path).unlink()

    profile = build_project_profile(tmp_path, repo, tracked_only=True)

    assert profile.config_files == ["pyproject.toml", ".vega.yaml"]
    assert profile.tech_stack == ["Python", ".NET"]
    assert profile.package_managers == ["pip / pyproject"]
    assert profile.test_commands == ["python -m pytest tracked"]
    assert profile.lint_commands == []
    assert profile.entrypoints == ["src/main.py"]
    assert profile.key_directories == ["src", "tests"]


def test_tracked_project_profile_ignores_worktree_additions_and_modifications(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            ".vega.yaml": (
                "version: 1\nverification:\n"
                "  commands:\n"
                "    - python -m pytest tracked\n"
            ),
            "README.md": "# Demo\n",
        },
    )
    revision = _git(repo, "rev-parse", "HEAD").strip()
    tracked_head_additions = {
        "New.sln": "Microsoft Visual Studio Solution File\n",
        "package.json": '{"scripts":{"test":"echo head","lint":"echo head"}}\n',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "src/index.ts": "console.log('head')\n",
        "tests/test_head.py": "def test_head():\n    assert True\n",
    }
    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n"
        "  commands:\n"
        "    - pnpm test tracked-head\n",
        encoding="utf-8",
    )
    for relative_path, content in tracked_head_additions.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", ".vega.yaml", *tracked_head_additions)
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "change profile inputs",
    )

    repo.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n"
        "  commands:\n"
        "    - echo WORKTREE_COMMAND\n",
        encoding="utf-8",
    )
    for relative_path in ["New.sln", "package.json", "src/index.ts"]:
        repo.joinpath(relative_path).unlink()
    additions = {
        "Cargo.toml": "[package]\nname = \"pollution\"\nversion = \"0.1.0\"\n",
        "backend/service.py": "print('pollution')\n",
        "cmd/server/main.go": "package main\n",
        "go.mod": "module example.test/pollution\n",
        "main.go": "package main\n",
    }
    for relative_path, content in additions.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    profile = build_project_profile(
        tmp_path,
        repo,
        tracked_only=True,
        tracked_revision=revision,
    )

    assert profile.config_files == [".vega.yaml"]
    assert profile.tech_stack == []
    assert profile.package_managers == []
    assert profile.test_commands == ["python -m pytest tracked"]
    assert profile.lint_commands == []
    assert profile.entrypoints == []
    assert profile.key_directories == []


def test_tracked_project_profile_rejects_unreadable_revision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "# Demo\n"})

    with pytest.raises(
        RuntimeError,
        match="已拒绝使用空画像继续",
    ):
        build_project_profile(
            tmp_path,
            repo,
            tracked_only=True,
            tracked_revision="missing-revision",
        )


def test_ignored_file_content_change_updates_fingerprint_with_same_size_and_mtime(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {".gitignore": "*.tmp\n", "README.md": "# Demo\n"})
    ignored = repo / "cache.tmp"
    ignored.write_text("alpha\n", encoding="utf-8")
    before = capture_review_workspace(repo)
    original_stat = ignored.stat()

    ignored.write_text("bravo\n", encoding="utf-8")
    os.utime(
        ignored,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    after = capture_review_workspace(repo)

    assert before.ignored_manifest_sha256 != after.ignored_manifest_sha256
    assert before.fingerprint != after.fingerprint


def test_ignored_sensitive_file_content_is_not_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {".gitignore": ".env\n", "README.md": "# Demo\n"})
    repo.joinpath(".env").write_text("AWS_SECRET_ACCESS_KEY=fake\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.name == ".env":
            raise AssertionError("ignored sensitive content must not be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    snapshot = capture_review_workspace(repo)

    assert len(snapshot.ignored_manifest_sha256) == 64


def test_ignored_content_hashing_respects_file_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {".gitignore": "*.tmp\n", "README.md": "# Demo\n"})
    for index in range(3):
        repo.joinpath(f"{index}.tmp").write_text(f"value-{index}\n", encoding="utf-8")
    opened: list[str] = []
    original_open = Path.open

    def tracking_open(path: Path, *args, **kwargs):
        if path.suffix == ".tmp":
            opened.append(path.name)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(workspace_check_module, "MAX_IGNORED_CONTENT_FILES", 1)
    monkeypatch.setattr(Path, "open", tracking_open)

    capture_review_workspace(repo)

    assert len(opened) == 1


def test_workspace_check_reports_dubious_ownership_without_modifying_git_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(
            returncode=128,
            stdout="",
            stderr="fatal: detected dubious ownership in repository",
        )

    monkeypatch.setattr(workspace_check_module.subprocess, "run", fake_run)

    result = evaluate_workspace(repo)
    diagnostic = "\n".join(result.reasons)

    assert result.status == "failed"
    assert "safe.directory" in diagnostic
    assert "不会自动修改全局 Git 配置" in diagnostic
    assert all(command[:2] != ["git", "config"] for command in commands)


def test_workspace_check_accepts_string_git_output_from_test_double(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        if command[1:3] == ["rev-parse", "--verify"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(workspace_check_module.subprocess, "run", fake_run)

    assert workspace_check_module.read_head_sha(repo) == "a" * 40


def _init_repo(repo: Path, files: dict[str, str]) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "core.autocrlf", "false")
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", *files)
    _git(
        repo,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "init",
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=True,
    )
    return result.stdout
