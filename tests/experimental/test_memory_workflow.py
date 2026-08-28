

import subprocess
from pathlib import Path


from vega.experimental.memory import MemoryLedgerStore
from vega.models import (
    MemoryProposal,
)


def _clear_vega_env(monkeypatch) -> None:
    for name in [
        "VEGA_API_KEY",
        "VEGA_BASE_URL",
        "VEGA_MODEL",
        "VEGA_REASONING_EFFORT",
        "VEGA_PROVIDER_ALIAS",
        "VEGA_TIMEOUT_SECONDS",
    ]:
        monkeypatch.delenv(name, raising=False)


def _init_clean_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    repo_dir.joinpath("AGENTS.md").write_text(
        "# AGENTS.md\n\n- 测试必须说明结果。\n",
        encoding="utf-8",
        newline="\n",
    )
    repo_dir.joinpath("README.md").write_text("# Demo\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_changed_git_repo(repo_dir: Path) -> None:
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath("README.md").write_text(
        "# Demo\nchanged\n",
        encoding="utf-8",
        newline="\n",
    )


def _commit_repo_paths(repo_dir: Path, *paths: str, message: str = "test update") -> None:
    subprocess.run(
        ["git", "add", "--", *paths],
        cwd=repo_dir,
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
            message,
        ],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )



def test_memory_search_filters_by_repo_tag_and_path(tmp_path) -> None:
    store = MemoryLedgerStore(tmp_path)
    proposal = MemoryProposal(
        id="mp-filter",
        type="pitfall",
        title="Admin API 返回格式",
        content="Admin API 直接返回对象，不包 code/data。",
        source_run_id="manual",
        tags=["api-contract"],
        repo="demo-repo",
        paths=["src/api/admin.py"],
    )
    store.append_decision(proposal, "accepted")

    assert store.search("Admin", repo="demo-repo", tags=["api-contract"], path="src/api")
    assert not store.search("Admin", repo="other-repo")
    assert not store.search("Admin", tags=["frontend"])
