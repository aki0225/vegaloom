from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.agent_contract import AgentPlan, AgentWorkItem
from vega.agent_runtime import SupervisorAgentRuntime
from vega.project_config import check_project_config
from vega.verification_command_preflight import inspect_verification_commands


def test_corepack_pnpm_dir_requires_manifest_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")

    issues = inspect_verification_commands(
        repo,
        ["corepack pnpm --dir frontend test"],
    )

    assert len(issues) == 1
    assert issues[0].code == "corepack_package_manager_version_ambiguous"
    assert issues[0].evidence == "frontend/package.json"
    assert issues[0].suggestion == "corepack pnpm@10.10.0 --dir frontend test"


def test_matching_explicit_corepack_version_passes_preflight(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")

    issues = inspect_verification_commands(
        repo,
        [
            "corepack pnpm@10.10.0 --dir frontend test",
            "corepack pnpm@10.10.0 -C frontend build",
        ],
    )

    assert issues == []


def test_corepack_preflight_supports_dot_slash_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")

    issues = inspect_verification_commands(
        repo,
        ["corepack pnpm --dir ./frontend test"],
    )

    assert len(issues) == 1
    assert issues[0].suggestion == "corepack pnpm@10.10.0 --dir ./frontend test"


def test_mismatched_corepack_version_reports_exact_replacement(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")

    issues = inspect_verification_commands(
        repo,
        ["corepack pnpm@11.1.0 --dir=frontend build"],
    )

    assert len(issues) == 1
    assert issues[0].code == "corepack_package_manager_version_mismatch"
    assert issues[0].suggestion == "corepack pnpm@10.10.0 --dir=frontend build"


def test_corepack_preflight_does_not_cross_shell_command_boundary(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")

    issues = inspect_verification_commands(
        repo,
        ["corepack pnpm --version; corepack pnpm@10.10.0 --dir frontend test"],
    )

    assert issues == []


def test_config_check_reports_corepack_version_ambiguity(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / ".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - corepack pnpm --dir frontend test",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = check_project_config(repo)

    issue = next(
        item
        for item in result.issues
        if item.code == "corepack_package_manager_version_ambiguous"
    )
    assert issue.severity == "error"
    assert "pnpm@10.10.0" in issue.message


def test_agent_approve_rejects_ambiguous_corepack_before_mutation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = AgentPlan(
        task_id="task-corepack-preflight",
        user_goal="验证前端命令版本。",
        success_conditions=["前端测试通过"],
        work_items=[
            AgentWorkItem(
                work_item_id="W1",
                objective="运行前端测试。",
                allowed_paths=["frontend/package.json"],
                verification=["corepack pnpm --dir frontend test"],
                external_side_effects="none",
            )
        ],
    )
    runtime = SupervisorAgentRuntime(workspace)
    run = runtime.start(repo, goal=plan.user_goal, plan=plan)
    state_before = (run.run_dir / "agent-state.json").read_bytes()
    plan_before = (run.run_dir / "agent-plan.json").read_bytes()

    with pytest.raises(ValueError, match="验证命令预检失败"):
        runtime.approve(run.run_dir.name)

    assert (run.run_dir / "agent-state.json").read_bytes() == state_before
    assert (run.run_dir / "agent-plan.json").read_bytes() == plan_before
    assert not (run.run_dir / "checkpoints").exists()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "vega@example.test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Vega Test"],
        cwd=path,
        check=True,
    )
    frontend = path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "name": "frontend",
                "private": True,
                "packageManager": "pnpm@10.10.0",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "测试：初始化仓库"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return path
