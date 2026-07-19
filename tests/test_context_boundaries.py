from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vega.memory import MemoryLedgerStore
from vega.models import MemoryProposal
from vega.project_context import build_project_context
from vega.project_knowledge import load_agents_instructions, search_related_memory
from vega.project_profile import build_project_profile
from vega.repository_identity import repository_scope
from vega.runtime import EngineeringChangeRuntime


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def _init_repo(repo: Path, files: dict[str, str]) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Vega Test")
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "fixture")


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"当前环境不支持符号链接测试：{type(exc).__name__}")


@pytest.mark.parametrize(
    "directory",
    [
        ".tmp",
        "runs",
        "memory",
        ".local-validation",
        "pytest-case",
        ".tmp-pytest-case",
        ".pytest-tmp-case",
        "pytest-cache-files-case",
        "test-output-case",
        "validation-case",
    ],
)
def test_generated_directories_never_contribute_agents_instructions(
    tmp_path: Path,
    directory: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("AGENTS.md").write_text("# Root\n", encoding="utf-8")
    repo.joinpath("src").mkdir()
    repo.joinpath("src", "AGENTS.md").write_text("# Source\n", encoding="utf-8")
    generated = repo / directory / "fixture"
    generated.mkdir(parents=True)
    generated.joinpath("AGENTS.md").write_text("# Polluted\n", encoding="utf-8")

    instructions = load_agents_instructions(repo)

    assert [item.path for item in instructions] == ["AGENTS.md", "src/AGENTS.md"]
    assert all("Polluted" not in item.content for item in instructions)


def test_related_paths_cannot_reintroduce_ignored_or_escaped_agents(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("AGENTS.md").write_text("# Root\n", encoding="utf-8")
    polluted = repo / ".tmp" / "fixture"
    polluted.mkdir(parents=True)
    polluted.joinpath("AGENTS.md").write_text("# Polluted\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.joinpath("AGENTS.md").write_text("# Outside\n", encoding="utf-8")

    instructions = load_agents_instructions(
        repo,
        [".tmp/fixture/example.py", "../outside/example.py"],
    )

    assert [item.path for item in instructions] == ["AGENTS.md"]


def test_nested_source_memory_directory_can_define_real_project_rules(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    nested = repo / "src" / "memory"
    nested.mkdir(parents=True)
    nested.joinpath("AGENTS.md").write_text("# Real memory module rules\n", encoding="utf-8")

    instructions = load_agents_instructions(repo)

    assert [item.path for item in instructions] == ["src/memory/AGENTS.md"]


def test_generated_prefixes_only_apply_at_repository_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested_paths = [
        repo / "plugins" / "pytest-vega" / "AGENTS.md",
        repo / "src" / "validation-engine" / "AGENTS.md",
    ]
    for index, path in enumerate(nested_paths):
        path.parent.mkdir(parents=True)
        path.write_text(f"# Real rules {index}\n", encoding="utf-8")

    instructions = load_agents_instructions(repo)

    assert [item.path for item in instructions] == [
        "plugins/pytest-vega/AGENTS.md",
        "src/validation-engine/AGENTS.md",
    ]


def test_agents_symlinks_outside_repo_are_never_read(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside-agents.md"
    outside.write_text("# Outside secret rules\n", encoding="utf-8")
    _symlink_or_skip(repo / "AGENTS.md", outside)
    nested = repo / "src"
    nested.mkdir()
    _symlink_or_skip(nested / "AGENTS.md", outside)

    instructions = load_agents_instructions(repo, ["src/example.py"])

    assert instructions == []


def test_agents_symlink_cannot_reintroduce_ignored_repo_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    polluted = repo / ".tmp" / "fixture" / "AGENTS.md"
    polluted.parent.mkdir(parents=True)
    polluted.write_text("# Polluted rules\n", encoding="utf-8")
    source = repo / "src"
    source.mkdir()
    _symlink_or_skip(source / "AGENTS.md", polluted)

    instructions = load_agents_instructions(repo, ["src/example.py"])

    assert instructions == []


def test_tracked_generated_agents_files_are_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            "AGENTS.md": "# Root\n",
            "src/AGENTS.md": "# Source\n",
            "runs/AGENTS.md": "# Polluted\n",
        },
    )

    instructions = load_agents_instructions(repo, tracked_only=True)

    assert [item.path for item in instructions] == ["AGENTS.md", "src/AGENTS.md"]


def test_tracked_agents_symlink_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, {"README.md": "# Demo\n"})
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="rules/AGENTS.md\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},AGENTS.md")
    _git(repo, "commit", "-m", "tracked symlink")

    instructions = load_agents_instructions(repo, tracked_only=True)

    assert instructions == []


def _accept_memory(
    workspace: Path,
    *,
    proposal_id: str,
    repo: str | None,
    content: str,
) -> None:
    proposal = MemoryProposal(
        id=proposal_id,
        type="lesson_candidate",
        title="导出按钮经验",
        content=content,
        source_run_id="fixture",
        sensitivity="public",
        tags=["导出"],
        repo=repo,
    )
    MemoryLedgerStore(workspace).append_decision(proposal, "accepted", "fixture")


def test_memory_search_keeps_repo_and_unscoped_entries_separate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    repo_a_scope = repository_scope(repo_a)
    _accept_memory(
        workspace,
        proposal_id="mp-repo-a",
        repo=repo_a_scope,
        content="导出按钮需要检查空状态。",
    )
    _accept_memory(
        workspace,
        proposal_id="mp-repo-b",
        repo="repo-b",
        content="导出按钮需要修改全局配置。",
    )
    _accept_memory(
        workspace,
        proposal_id="mp-general",
        repo=None,
        content="导出按钮修改后应执行回归测试。",
    )

    hits = search_related_memory(workspace, repo_a, "导出按钮")

    assert {item.proposal_id for item in hits} == {"mp-repo-a", "mp-general"}
    rendered_sources = {item.proposal_id: item.repo for item in hits}
    assert rendered_sources == {"mp-repo-a": repo_a_scope, "mp-general": None}


def test_memory_search_does_not_mix_same_name_repositories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = tmp_path / "checkout-a" / "backend"
    repo_b = tmp_path / "checkout-b" / "backend"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)
    _accept_memory(
        workspace,
        proposal_id="mp-backend-a",
        repo=repository_scope(repo_a),
        content="导出按钮必须保留旧参数。",
    )
    _accept_memory(
        workspace,
        proposal_id="mp-general",
        repo=None,
        content="导出按钮修改后应执行回归测试。",
    )

    hits = search_related_memory(workspace, repo_b, "导出按钮")

    assert {item.proposal_id for item in hits} == {"mp-general"}


def test_memory_store_rejects_conflicting_repo_filters(tmp_path: Path) -> None:
    store = MemoryLedgerStore(tmp_path)

    with pytest.raises(ValueError, match="不能同时使用"):
        store.search(repo="repo-a", repo_unscoped_only=True)


def test_project_profile_reads_pyproject_script_entrypoints(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                "[project.scripts]",
                'demo = "demo.cli:app"',
                'worker = "demo.worker:main"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    profile = build_project_profile(tmp_path, repo)

    assert profile.script_entrypoints == [
        "demo = demo.cli:app",
        "worker = demo.worker:main",
    ]


def test_tracked_profile_reads_scripts_from_fixed_revision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            "pyproject.toml": (
                "[project]\n"
                'name = "demo"\n'
                "[project.scripts]\n"
                'demo = "demo.cli:tracked"\n'
            ),
        },
    )
    repo.joinpath("pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        "[project.scripts]\n"
        'demo = "demo.cli:worktree"\n',
        encoding="utf-8",
    )

    profile = build_project_profile(tmp_path, repo, tracked_only=True)

    assert profile.script_entrypoints == ["demo = demo.cli:tracked"]


def test_invalid_pyproject_scripts_do_not_fail_profile(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("pyproject.toml").write_text(
        "[project.scripts\ninvalid",
        encoding="utf-8",
    )

    profile = build_project_profile(tmp_path, repo)

    assert profile.script_entrypoints == []


def test_tracked_project_context_rejects_git_subdirectory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        {
            "AGENTS.md": "# Root rules\n",
            "packages/app/README.md": "# App\n",
        },
    )

    with pytest.raises(RuntimeError, match="Git 仓库根目录"):
        build_project_context(
            tmp_path,
            repo / "packages" / "app",
            "检查项目上下文",
        )


def test_engineering_change_runtime_rejects_sensitive_task_symlink_before_run(
    tmp_path: Path,
) -> None:
    secret = tmp_path / ".env"
    secret.write_text("VEGA_API_KEY=secret\n", encoding="utf-8")
    task = tmp_path / "task.md"
    _symlink_or_skip(task, secret)
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError, match="拒绝读取敏感路径"):
        EngineeringChangeRuntime(tmp_path).run(task, repo)

    assert not tmp_path.joinpath("runs").exists()
