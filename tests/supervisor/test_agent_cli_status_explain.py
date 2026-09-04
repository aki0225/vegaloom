from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vega.agent_cli_status as cli_status_module
from vega.agent_persistence import load_agent_state, save_agent_state
from vega.agent_runtime import SupervisorAgentRuntime
from vega.agent_runtime_logic import update_state
from vega.cli_entrypoint import app
from vega.provider_session import (
    ProviderSessionState,
    load_provider_sessions,
    save_provider_sessions,
)


def test_status_selects_unique_change_run_from_repository_subdirectory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    run = SupervisorAgentRuntime(repo).start_planning(
        repo,
        goal="修复设置页重复提交",
    )
    subdirectory = repo / "src" / "settings"
    subdirectory.mkdir(parents=True)
    monkeypatch.chdir(subdirectory)

    result = CliRunner().invoke(app, ["status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == run.run_dir.name
    assert payload["selected_run"] == {
        "run_id": run.run_dir.name,
        "selection_source": "repository_active",
        "active": True,
    }
    assert payload["explanation"]["reason_code"] == "planning.required"
    assert payload["persisted_agent_state"]["run_id"] == run.run_dir.name


def test_status_rejects_multiple_active_change_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    runtime = SupervisorAgentRuntime(repo)
    first = runtime.start_planning(repo, goal="修复第一个问题")
    second = runtime.start_planning(repo, goal="修复第二个问题")
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code != 0
    assert "多个未完成 ChangeRun" in result.output
    assert "拒绝自动选择" in result.output
    assert first.run_dir.name in result.output
    assert second.run_dir.name in result.output


def test_status_default_full_and_explain_share_read_only_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    run = SupervisorAgentRuntime(repo).start_planning(
        repo,
        goal="修复导出按钮无响应",
    )
    monkeypatch.chdir(repo)
    before = _artifact_snapshot(run.run_dir)

    compact = CliRunner().invoke(app, ["status"])
    full = CliRunner().invoke(app, ["status", "--full"])
    explanation = CliRunner().invoke(app, ["explain"])
    explanation_json = CliRunner().invoke(app, ["explain", "--json"])

    assert compact.exit_code == 0, compact.output
    assert full.exit_code == 0, full.output
    assert explanation.exit_code == 0, explanation.output
    assert explanation_json.exit_code == 0, explanation_json.output
    assert "# Vega Status" in compact.output
    assert "执行会话" in compact.output
    assert "Verification：尚未运行" in compact.output
    assert "原因：当前任务仍需完成只读调查和合同编译。" in compact.output
    assert "# Vega Agent" in full.output
    assert "## 为什么停在这里" in explanation.output
    assert "原因代码：`planning.required`" in explanation.output
    json_payload = json.loads(explanation_json.output)
    assert json_payload["selected_run"]["run_id"] == run.run_dir.name
    assert json_payload["explanation"]["reason_code"] == "planning.required"
    assert "status" not in json_payload
    assert _artifact_snapshot(run.run_dir) == before


@pytest.mark.parametrize(
    ("mutations", "expected_exit"),
    [(1, 0), (2, 2)],
)
def test_status_snapshot_retries_once_then_fails_closed_on_continuous_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutations: int,
    expected_exit: int,
) -> None:
    repo = _repo(tmp_path / "repo")
    run = SupervisorAgentRuntime(repo).start_planning(
        repo,
        goal="修复状态竞态",
    )
    monkeypatch.chdir(repo)
    original = cli_status_module.build_agent_explanation
    calls = 0

    def mutate_after_explanation(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls <= mutations:
            path = run.run_dir / "agent-state.json"
            state = load_agent_state(path)
            save_agent_state(
                path,
                update_state(
                    state,
                    state_version=state.state_version + 1,
                ),
            )
        return result

    monkeypatch.setattr(
        cli_status_module,
        "build_agent_explanation",
        mutate_after_explanation,
    )

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == expected_exit, result.output
    assert calls == (2 if mutations else 1)
    if expected_exit == 0:
        assert "# Vega Status" in result.output
    else:
        assert "状态快照构建期间持续变化" in result.output


@pytest.mark.parametrize(
    ("mutations", "expected_exit"),
    [(1, 0), (2, 2)],
)
def test_status_snapshot_binds_provider_session_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutations: int,
    expected_exit: int,
) -> None:
    repo = _repo(tmp_path / "repo")
    run = SupervisorAgentRuntime(repo).start_planning(
        repo,
        goal="绑定 Provider Session 快照",
    )
    save_provider_sessions(
        run.run_dir,
        ProviderSessionState(run_id=run.run_dir.name),
    )
    monkeypatch.chdir(repo)
    original = cli_status_module.build_agent_explanation
    calls = 0

    def mutate_after_explanation(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls <= mutations:
            path = run.run_dir / "provider-sessions.json"
            provider_state = load_provider_sessions(run.run_dir)
            provider_state.revision += 1
            save_provider_sessions(path.parent, provider_state)
        return result

    monkeypatch.setattr(
        cli_status_module,
        "build_agent_explanation",
        mutate_after_explanation,
    )

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == expected_exit, result.output
    assert calls == (2 if mutations else 1)
    if expected_exit == 0:
        assert "# Vega Status" in result.output
    else:
        assert "状态快照构建期间持续变化" in result.output


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Vega Test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "vega@example.invalid"],
        cwd=path,
        check=True,
    )
    (path / ".gitignore").write_text(
        "runs/\n.vega-worktrees/\n",
        encoding="utf-8",
        newline="\n",
    )
    (path / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path.resolve()


def _artifact_snapshot(run_dir: Path) -> dict[str, tuple[int, bytes]]:
    return {
        path.relative_to(run_dir).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }
