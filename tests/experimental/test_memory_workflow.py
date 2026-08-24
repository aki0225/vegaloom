

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from vega.cli import app
from vega.experimental.memory import MemoryLedgerStore
from vega.models import (
    MemoryProposal,
)
from vega.repository_identity import repository_scope


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


def test_memory_cli_accepts_and_searches_proposal(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    repo_dir = tmp_path / "target-repo"
    _init_changed_git_repo(repo_dir)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "reflect",
            "--repo",
            str(repo_dir),
            "--note",
            "测试显式经验候选",
            "--lesson",
            "README 变更后必须执行文档链接检查。",
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = next(tmp_path.joinpath("runs").iterdir())
    proposal = json.loads(run_dir.joinpath("memory-proposals.jsonl").read_text(encoding="utf-8"))

    accept_result = CliRunner().invoke(
        app,
        ["memory", "accept", proposal["id"], "--run", run_dir.name, "--reason", "测试接受"],
    )
    assert accept_result.exit_code == 0, accept_result.output
    assert MemoryLedgerStore(tmp_path).list_entries()[0].status == "accepted"

    search_result = CliRunner().invoke(app, ["memory", "search", "文档链接"])
    assert search_result.exit_code == 0, search_result.output
    assert proposal["id"] in search_result.output


def test_brief_bug_generates_agent_context_with_agents_and_memory(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    repo_dir = tmp_path / "target-repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath("AGENTS.md").write_text(
        "# AGENTS.md\n\n- 修复 bug 后必须补回归验证。\n- 不要自动提交。\n",
        encoding="utf-8",
    )
    _commit_repo_paths(repo_dir, "AGENTS.md", message="update project rules")
    proposal = MemoryProposal(
        id="mp-export-bug",
        type="pitfall",
        title="导出按钮曾因空状态无响应",
        content="导出按钮问题优先检查前端 disabled 状态和接口错误分支。",
        source_run_id="manual",
        tags=["bug", "导出"],
        repo=repository_scope(repo_dir),
        paths=["README.md"],
    )
    MemoryLedgerStore(tmp_path).append_decision(proposal, "accepted", "测试准备")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["brief", "bug", "--repo", str(repo_dir), "--text", "用户反馈导出按钮点击后无响应"],
    )

    assert result.exit_code == 0, result.output
    run_dir = next(tmp_path.joinpath("runs").iterdir())
    for artifact in [
        "state.json",
        "trace.jsonl",
        "knowledge-context.md",
        "agent-brief.md",
        "agents-md-proposals.md",
        "eval.md",
        "repro-plan.md",
        "root-cause-hypotheses.md",
        "regression-check.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact

    brief = run_dir.joinpath("agent-brief.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in brief
    assert "导出按钮曾因空状态无响应" in brief
    assert "不自动提交" in brief
    proposal_text = run_dir.joinpath("agents-md-proposals.md").read_text(encoding="utf-8")
    assert "不会自动修改" in proposal_text
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["memory_hits"][0]["proposal_id"] == "mp-export-bug"
    assert state["memory_proposals"] == []
    assert not run_dir.joinpath("memory-proposals.jsonl").exists()


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
