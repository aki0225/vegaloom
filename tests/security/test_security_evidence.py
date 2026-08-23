from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from vega import gate_runtime as gate_runtime_module
from vega import git_read as git_read_module
from vega import project_config as project_config_module
from vega import project_context as project_context_module
from vega import project_knowledge as project_knowledge_module
from vega import project_profile as project_profile_module
from vega import reflect_runtime as reflect_runtime_module
from vega import repository_identity as repository_identity_module
from vega import risk_gate_evidence as risk_gate_evidence_module
from vega import workspace_check as workspace_check_module
from vega.brief_runtime import BriefRuntime
from vega.models import (
    AgentsInstruction,
    BriefInput,
    ProjectKnowledge,
    ProjectProfile,
)
from vega.project_config import load_project_config
from vega.project_knowledge import load_agents_instructions
from vega.project_profile import build_project_profile
from vega.redaction import REDACTION_TEXT, redact_text
from vega.experimental.inspection.runtime import EngineeringChangeRuntime
from vega.tools import git_tools as git_tools_module
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

    assert snapshot.ignored_manifest_complete is True
    assert snapshot.ignored_content_complete is False
    assert snapshot.ignored_coverage_level == "metadata_bounded"
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


def test_git_reads_disable_external_config_and_diff_drivers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

    workspace_check_module._run_git_bytes(
        repo,
        ["git", "status", "--porcelain=v1"],
    )
    commands: list[list[str]] = []

    def fake_run_git_bytes(
        repo_path: Path,
        command: list[str],
        *,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> bytes:
        del repo_path, allowed_returncodes
        commands.append(command)
        return b""

    monkeypatch.setattr(workspace_check_module, "_run_git_bytes", fake_run_git_bytes)
    workspace_check_module.collect_tracked_diff_parts(repo, ["--binary"])

    actual_command, kwargs = calls[0]
    assert ["-c", f"core.excludesFile={os.devnull}"] == actual_command[1:3]
    assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert kwargs["env"]["GIT_ATTR_NOSYSTEM"] == "1"
    assert all("--no-ext-diff" in command for command in commands)
    assert all("--no-textconv" in command for command in commands)


def test_git_read_environment_removes_parent_git_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variables = (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_UNRECOGNIZED_PARENT_OVERRIDE",
    )
    for variable in variables:
        monkeypatch.setenv(variable, "untrusted-parent-value")

    environment = git_read_module.git_read_environment()

    assert all(variable not in environment for variable in variables)


def test_git_read_environment_replaces_controlled_git_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in (
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_ATTR_NOSYSTEM",
        "GIT_OPTIONAL_LOCKS",
        "GIT_PAGER",
    ):
        monkeypatch.setenv(variable, "untrusted-parent-value")

    environment = git_read_module.git_read_environment()

    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_ATTR_NOSYSTEM"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_PAGER"] == "cat"


def test_resolved_git_revision_reuse_avoids_duplicate_git_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    commit = "a" * 40
    calls: list[list[str]] = []

    def fake_run(
        repo_path: Path,
        command: list[str],
        **kwargs: object,
    ) -> SimpleNamespace:
        assert repo_path == repo.resolve()
        calls.append(command)
        stdout = (
            str(repo.resolve()).encode("utf-8")
            if "--show-toplevel" in command
            else commit.encode("ascii")
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(repository_identity_module, "run_git_capture", fake_run)

    resolved = repository_identity_module.resolve_git_revision(repo)
    reused = repository_identity_module.resolve_git_revision(repo, resolved or "")

    assert resolved is not None
    assert resolved.commit == commit
    assert reused is resolved
    assert len(calls) == 2

    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    with pytest.raises(RuntimeError, match="目标仓库不匹配"):
        repository_identity_module.resolve_git_revision(other_repo, resolved)
    assert len(calls) == 2

    forged = object.__new__(repository_identity_module.ResolvedGitRevision)
    object.__setattr__(
        forged,
        "repo_key",
        os.path.normcase(str(repo.resolve())),
    )
    object.__setattr__(forged, "commit", "")
    with pytest.raises(RuntimeError, match="未经过当前读取事务校验"):
        repository_identity_module.resolve_git_revision(repo, forged)
    assert len(calls) == 2


def test_project_context_reuses_preloaded_knowledge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    knowledge = ProjectKnowledge(
        repo_name=repo.name,
        repo_path=str(repo.resolve()),
        agents_instructions=[
            AgentsInstruction(
                path="AGENTS.md",
                scope=".",
                content="- PRELOADED_RULE",
            )
        ],
    )
    profile = ProjectProfile(
        repo_name=repo.name,
        repo_path=str(repo.resolve()),
    )

    monkeypatch.setattr(
        project_context_module,
        "build_project_profile",
        lambda *args, **kwargs: profile,
    )
    monkeypatch.setattr(
        project_context_module,
        "load_project_config",
        lambda *args, **kwargs: project_config_module.ProjectConfig(),
    )

    def fail_reload(*args: object, **kwargs: object) -> ProjectKnowledge:
        raise AssertionError("不应重复加载项目知识")

    monkeypatch.setattr(
        project_context_module,
        "load_project_knowledge",
        fail_reload,
    )

    context = project_context_module.build_project_context(
        tmp_path,
        repo,
        "检查项目上下文",
        tracked_only=False,
        knowledge=knowledge,
    )

    assert "PRELOADED_RULE" in context
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    foreign_knowledge = knowledge.model_copy(
        update={"repo_path": str(other_repo.resolve())}
    )
    with pytest.raises(ValueError, match="目标仓库不匹配"):
        project_context_module.build_project_context(
            tmp_path,
            repo,
            "检查项目上下文",
            tracked_only=False,
            knowledge=foreign_knowledge,
        )


def test_tracked_project_context_rejects_worktree_knowledge(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {"AGENTS.md": "# TRACKED_RULE\n"})
    repo.joinpath("AGENTS.md").write_text(
        "# MUTABLE_WORKTREE_RULE\n",
        encoding="utf-8",
        newline="\n",
    )
    knowledge = project_knowledge_module.load_project_knowledge(
        tmp_path,
        repo,
        "检查项目上下文",
        tracked_only=False,
    )
    assert "MUTABLE_WORKTREE_RULE" in knowledge.agents_instructions[0].content

    with pytest.raises(ValueError, match="tracked revision 不匹配"):
        project_context_module.build_project_context(
            tmp_path,
            repo,
            "检查项目上下文",
            tracked_only=True,
            knowledge=knowledge,
        )


def test_tracked_project_context_requires_loader_provenance(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {"AGENTS.md": "# TRACKED_RULE\n"})
    plain_knowledge = ProjectKnowledge(
        repo_name=repo.name,
        repo_path=str(repo.resolve()),
        agents_instructions=[
            AgentsInstruction(
                path="AGENTS.md",
                scope=".",
                content="# TRACKED_RULE\n",
            )
        ],
    )

    with pytest.raises(ValueError, match="tracked revision 不匹配"):
        project_context_module.build_project_context(
            tmp_path,
            repo,
            "检查项目上下文",
            tracked_only=True,
            knowledge=plain_knowledge,
        )

    loaded = project_knowledge_module.load_project_knowledge(
        tmp_path,
        repo,
        "检查项目上下文",
        tracked_only=True,
    )
    assert loaded.model_dump().keys() == plain_knowledge.model_dump().keys()


def test_risk_gate_rejects_non_string_reflect_changed_files() -> None:
    with pytest.raises(ValueError, match="changed_files 不合法"):
        risk_gate_evidence_module.validated_reflect_changed_files(
            {"changed_files": ["src/app.py", {"path": "forged.py"}]}
        )


@pytest.mark.parametrize("runtime_name", ["brief", "reflect"])
def test_runtime_reuses_revision_and_knowledge_in_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_name: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            "AGENTS.md": "# TRACKED_RULE\n",
            "README.md": "# Demo\n",
        },
    )
    original_git_capture = repository_identity_module.run_git_capture
    original_memory_search = project_knowledge_module.search_related_memory
    revision_reads = 0
    memory_searches = 0

    def count_revision_reads(
        repo_path: Path,
        command: list[str],
        **kwargs: object,
    ) -> object:
        nonlocal revision_reads
        if command[1:3] == ["rev-parse", "--verify"]:
            revision_reads += 1
        return original_git_capture(repo_path, command, **kwargs)

    def count_memory_searches(*args: object, **kwargs: object) -> list:
        nonlocal memory_searches
        memory_searches += 1
        return original_memory_search(*args, **kwargs)

    def fail_reload(*args: object, **kwargs: object) -> ProjectKnowledge:
        raise AssertionError("project context 不应重复加载项目知识")

    monkeypatch.setattr(
        repository_identity_module,
        "run_git_capture",
        count_revision_reads,
    )
    monkeypatch.setattr(
        project_knowledge_module,
        "search_related_memory",
        count_memory_searches,
    )
    monkeypatch.setattr(
        project_context_module,
        "load_project_knowledge",
        fail_reload,
    )

    if runtime_name == "brief":
        run_dir = BriefRuntime(workspace).run(
            BriefInput(
                mode="bug",
                text="检查项目上下文复用",
                source="fixture",
                repo_path=str(repo),
            )
        )
    else:
        repo.joinpath("README.md").write_text(
            "# Demo\n\nchanged\n",
            encoding="utf-8",
            newline="\n",
        )
        run_dir = reflect_runtime_module.ReflectRuntime(workspace).run(repo)

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert revision_reads == 1
    assert memory_searches == 1


def test_git_read_ignores_parent_repository_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_repo = tmp_path / "expected"
    redirected_repo = tmp_path / "redirected"
    _init_repo(expected_repo, {"README.md": "# Expected\n"})
    _init_repo(redirected_repo, {"README.md": "# Redirected\n"})
    expected_head = _git(expected_repo, "rev-parse", "HEAD").strip()
    redirected_head = _git(redirected_repo, "rev-parse", "HEAD").strip()
    assert expected_head != redirected_head
    monkeypatch.setenv("GIT_DIR", str(redirected_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(redirected_repo))

    actual_head = workspace_check_module.read_head_sha(expected_repo)

    assert actual_head == expected_head


def test_explicit_matching_safe_directory_is_scoped_to_git_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[list[str], dict[str, object], str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        environment = kwargs["env"]
        config_path = Path(environment["GIT_CONFIG_GLOBAL"])
        calls.append(
            (
                command,
                kwargs,
                config_path.read_text(encoding="utf-8"),
            )
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setenv("VEGA_GIT_SAFE_DIRECTORY", str(repo))
    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

    git_read_module.run_git_capture(repo, ["git", "status", "--short"])

    command, environment, config_text = calls[0][0], calls[0][1]["env"], calls[0][2]
    config_path = Path(environment["GIT_CONFIG_GLOBAL"])
    assert command == git_read_module.harden_git_read_command(
        ["git", "status", "--short"]
    )
    assert config_text == (
        f'[safe]\n\tdirectory = "{repo.resolve().as_posix()}"\n'
    )
    assert not config_path.exists()
    assert "VEGA_GIT_SAFE_DIRECTORY" not in environment


def test_explicit_matching_safe_directory_uses_isolated_global_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    monkeypatch.setenv("VEGA_GIT_SAFE_DIRECTORY", str(repo))

    output = git_read_module.run_git_bytes(
        repo,
        ["git", "config", "--show-scope", "--get-all", "safe.directory"],
    ).decode("utf-8")

    assert output == f"global\t{repo.resolve().as_posix()}\n"


def test_safe_directory_config_rejects_control_characters() -> None:
    unsafe_path = Path("repo\n[include]")

    with pytest.raises(RuntimeError, match="不得包含控制字符"):
        git_read_module.create_safe_directory_global_config(unsafe_path)


def test_safe_directory_config_cleanup_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    original_unlink = Path.unlink

    def fail_safe_config_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith("vega-git-safe-"):
            original_unlink(path, *args, **kwargs)
            raise OSError("cleanup failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setenv("VEGA_GIT_SAFE_DIRECTORY", str(repo))
    monkeypatch.setattr(
        git_read_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"",
            stderr=b"",
        ),
    )
    monkeypatch.setattr(Path, "unlink", fail_safe_config_cleanup)

    with pytest.raises(RuntimeError, match="无法清理隔离的 Git safe.directory 配置"):
        git_read_module.run_git_capture(repo, ["git", "status", "--short"])


def test_explicit_safe_directory_must_match_target_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    monkeypatch.setenv("VEGA_GIT_SAFE_DIRECTORY", str(other))

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("不匹配的信任目录不得启动 Git")

    monkeypatch.setattr(git_read_module.subprocess, "run", fail_if_called)

    with pytest.raises(RuntimeError, match="必须与目标仓库完全一致"):
        git_read_module.run_git_capture(repo, ["git", "status", "--short"])


def test_explicit_safe_directory_rejects_invalid_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    missing = tmp_path / "missing"
    file_path = tmp_path / "file.txt"
    repo.mkdir()
    file_path.write_text("not a directory\n", encoding="utf-8")

    for invalid_value in ("relative/repo", str(missing), str(file_path)):
        monkeypatch.setenv("VEGA_GIT_SAFE_DIRECTORY", invalid_value)
        with pytest.raises(RuntimeError):
            git_read_module.run_git_capture(repo, ["git", "status", "--short"])


def test_reflect_git_reads_use_hardened_environment_and_diff_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

    reflect_runtime_module.collect_git_reflection(repo)

    assert calls
    for command, kwargs in calls:
        assert ["-c", f"core.excludesFile={os.devnull}"] == command[1:3]
        assert "core.fsmonitor=" in command
        assert "core.fsmonitor=false" not in command
        assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
        assert kwargs["env"]["GIT_ATTR_NOSYSTEM"] == "1"
    diff_commands = [command for command, _ in calls if "diff" in command]
    assert diff_commands
    assert all("--check" in command for command in diff_commands)
    assert all("--stat" not in command for command in diff_commands)
    assert all("--name-only" not in command for command in diff_commands)
    assert all("--no-ext-diff" in command for command in diff_commands)
    assert all("--no-textconv" in command for command in diff_commands)
    assert all("status" not in command for command, _ in calls)


def test_gate_git_reads_use_hardened_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"status", stderr=b"warning")

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

    output = gate_runtime_module._git(
        repo,
        ["git", "status", "--short", "--untracked-files=all"],
    )

    assert output == "status"
    command, kwargs = calls[0]
    assert ["-c", f"core.excludesFile={os.devnull}"] == command[1:3]
    assert "core.fsmonitor=" in command
    assert "core.fsmonitor=false" not in command
    assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert kwargs["env"]["GIT_ATTR_NOSYSTEM"] == "1"


@pytest.mark.parametrize(
    "module",
    [
        gate_runtime_module,
        project_config_module,
        project_knowledge_module,
        project_profile_module,
        reflect_runtime_module,
        repository_identity_module,
        git_tools_module,
    ],
)
def test_production_git_read_modules_do_not_bypass_hardened_client(
    module: ModuleType,
) -> None:
    assert module.__file__ is not None
    module_path = Path(module.__file__)
    source = module_path.read_text(encoding="utf-8")

    assert "subprocess.run(" not in source


def test_allowed_git_diff_checks_disable_external_drivers() -> None:
    for check_id in ("git.diff", "git.diff_check"):
        command = git_tools_module.ALLOWED_CHECKS[check_id]
        assert "--no-ext-diff" in command
        assert "--no-textconv" in command


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

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

    result = evaluate_workspace(repo)
    diagnostic = "\n".join(result.reasons)

    assert result.status == "failed"
    assert "safe.directory" in diagnostic
    assert "VEGA_GIT_SAFE_DIRECTORY" in diagnostic
    assert "git config --global" not in diagnostic
    assert all(command[:2] != ["git", "config"] for command in commands)


def test_workspace_check_accepts_string_git_output_from_test_double(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        if command[-3:] == ["rev-parse", "--verify", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)

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
