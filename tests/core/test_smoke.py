import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import vega
from vega.cli import app
from vega.execution_control import (
    ExecutionLease,
    RunnerExecutionContext,
    request_stop_for_run,
    run_owned_process,
)
from vega.finish_presentation import render_finish_report
from vega.finish_runtime import FinishRuntime
from vega.loop_evidence import validate_loop_artifact_integrity
from vega.loop_runtime import LoopAutomationRuntime
from vega.models import (
    BriefInput,
    ReviewFinding,
    ReviewVerdict,
)
from vega.progress import ExecutionProgressAdapter
from vega.project_config import CodexExecOptions, check_project_config, load_project_config
from vega.project_profile import build_project_profile
from vega.recovery_runtime import RecoveryRuntime
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewPackRuntime, ReviewRuntime, parse_review_verdict
from vega.risk_review_reporting import build_finish_review_section
from vega.run_lock import RunMutationLock
from vega.run_status import run_status_payload
from vega.run_utils import create_run_dir
from vega.runner import CodexExecRunner, RunnerResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]


_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_PATTERN.sub("", value)


def test_project_skeleton_exists() -> None:
    assert PROJECT_ROOT.joinpath("docs", "PRODUCT-CONTRACT.md").exists()
    assert PROJECT_ROOT.joinpath("docs", "MVP-SCOPE.md").exists()
    assert PROJECT_ROOT.joinpath("docs", "ARCHITECTURE.md").exists()
    assert PROJECT_ROOT.joinpath("docs", "PLAN-FIRST-PROTOCOL.md").exists()
    assert PROJECT_ROOT.joinpath("loops", "engineering-change.loop.yaml").exists()
    assert PROJECT_ROOT.joinpath("examples", "tasks", "check-atg-mcp-docs.md").exists()


def test_python_package_public_api_is_version_only() -> None:
    assert vega.__all__ == ["__version__"]
    assert importlib.util.find_spec("vega.assurance") is None
    assert importlib.util.find_spec("vega.experimental.assurance") is not None


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


class StaticRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "sandbox": sandbox,
                "timeout_seconds": timeout_seconds,
                "execution_context": execution_context,
            }
        )
        output = self.outputs.pop(0) if self.outputs else "{}"
        return RunnerResult(status="success", output=output, command=["fake-runner"])


class TrackedChangeRunner(StaticRunner):
    """模拟 worker 产生可归因的 tracked diff，供 auto 主链测试使用。"""

    def __init__(self, outputs: list[str]) -> None:
        super().__init__(outputs)
        self.change_count = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        result = super().run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        self.change_count += 1
        readme = repo_path / "README.md"
        readme.write_text(
            f"{readme.read_text(encoding='utf-8').rstrip()}\n"
            f"worker tracked change {self.change_count}\n",
            encoding="utf-8",
            newline="\n",
        )
        return result


class PollutingRunner:
    def __init__(self, file_count: int) -> None:
        self.file_count = file_count

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        for index in range(self.file_count):
            repo_path.joinpath(f"pollution-{index}.tmp").write_text(
                "noise\n",
                encoding="utf-8",
                newline="\n",
            )
        return RunnerResult(status="success", output="created pollution", command=["polluting-runner"])


class StatusRunner:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        self.calls.append(
            {
                "prompt": prompt,
                "repo_path": repo_path,
                "sandbox": sandbox,
                "timeout_seconds": timeout_seconds,
                "execution_context": execution_context,
            }
        )
        return RunnerResult(
            status=self.status,  # type: ignore[arg-type]
            output="",
            error=f"模拟 runner 状态：{self.status}",
            command=["status-runner"],
        )


class WritingErrorRunner:
    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        repo_path.joinpath("README.md").write_text("# Partial Work\n", encoding="utf-8")
        return RunnerResult(
            status="error",
            output="provider failed after write",
            error="模拟 provider 在写入后异常退出",
            command=["writing-error-runner"],
        )


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


def _write_isolated_verification_config(repo_dir: Path) -> None:
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                (
                    "    - python -c \"import sys; from pathlib import Path; "
                    "path=Path(sys.argv[1]); "
                    "assert path.is_dir(); print('verification one')\" "
                    "{{vega_verification_temp}}"
                ),
                (
                    "    - python -c \"import sys; from pathlib import Path; "
                    "path=Path(sys.argv[1]); "
                    "assert path.is_dir(); print('verification two')\" "
                    "{{vega_verification_temp}}"
                ),
                "  max_commands: 2",
                "  timeout_seconds: 60",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(
        repo_dir,
        ".vega.yaml",
        message="add isolated verification config",
    )


def test_brief_feature_supports_input_file_and_generates_feature_artifacts(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath("AGENTS.md").write_text("# AGENTS.md\n\n- API 保持向后兼容。\n", encoding="utf-8")
    _commit_repo_paths(repo_dir, "AGENTS.md", message="update project rules")
    feature_file = tmp_path / "feature.md"
    feature_file.write_text("# 批量导入用户\n\n需要支持 CSV 批量导入。\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["brief", "feature", "--repo", str(repo_dir), "--input", str(feature_file)],
    )

    assert result.exit_code == 0, result.output
    run_dir = next(tmp_path.joinpath("runs").iterdir())
    for artifact in [
        "feature-spec.md",
        "implementation-plan.md",
        "acceptance-criteria.md",
        "risk.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    assert "批量导入用户" in run_dir.joinpath("agent-brief.md").read_text(encoding="utf-8")
    assert "API 保持向后兼容" in run_dir.joinpath("knowledge-context.md").read_text(encoding="utf-8")


def test_brief_cli_requires_exactly_one_input_source(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    input_file = tmp_path / "bug.md"
    input_file.write_text("bug", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    both = CliRunner().invoke(
        app,
        [
            "brief",
            "bug",
            "--repo",
            str(repo_dir),
            "--input",
            str(input_file),
            "--text",
            "bug",
        ],
    )
    assert both.exit_code != 0
    assert "只能二选一" in _strip_ansi(both.output)

    none = CliRunner().invoke(app, ["brief", "bug", "--repo", str(repo_dir)])
    assert none.exit_code != 0
    assert "必须提供 --input 或 --text" in _strip_ansi(none.output)


def test_project_profile_cli_detects_python_project(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "python-repo"
    repo_dir.mkdir()
    repo_dir.joinpath("pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    repo_dir.joinpath("AGENTS.md").write_text("# AGENTS.md\n\n- 运行 pytest。\n", encoding="utf-8")
    repo_dir.joinpath("src").mkdir()
    repo_dir.joinpath("tests").mkdir()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["profile", "--repo", str(repo_dir)])

    assert result.exit_code == 0, result.output
    run_dir = next(tmp_path.joinpath("runs").iterdir())
    profile = json.loads(run_dir.joinpath("project-profile.json").read_text(encoding="utf-8"))
    assert "Python" in profile["tech_stack"]
    assert "python -m pytest -q" in profile["test_commands"]
    assert "AGENTS.md" in profile["agents_files"]
    assert "PASS:" in run_dir.joinpath("eval.md").read_text(encoding="utf-8")


def test_reflect_cli_generates_post_run_artifacts(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess_run = __import__("subprocess").run
    subprocess_run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess_run(["git", "config", "core.autocrlf", "false"], cwd=repo_dir, check=True, capture_output=True, text=True)
    repo_dir.joinpath("README.md").write_bytes(b"# Demo\n")
    subprocess_run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess_run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    repo_dir.joinpath("README.md").write_bytes(b"# Demo\nchanged\n")
    test_log = tmp_path / "test.log"
    test_log.write_text("pytest passed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["reflect", "--repo", str(repo_dir), "--test-log", str(test_log), "--note", "修复后复盘"],
    )

    assert result.exit_code == 0, result.output
    run_dir = next(tmp_path.joinpath("runs").iterdir())
    for artifact in [
        "state.json",
        "trace.jsonl",
        "diff-summary.md",
        "test-summary.md",
        "reflection.md",
        "agents-md-proposals.md",
        "eval.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    assert not run_dir.joinpath("memory-proposals.jsonl").exists()
    assert "README.md" in run_dir.joinpath("diff-summary.md").read_text(encoding="utf-8")
    assert "pytest passed" in run_dir.joinpath("test-summary.md").read_text(encoding="utf-8")
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["changed_files"] == ["README.md"]
    assert len(state["workspace_fingerprint"]) == 64


def test_reflect_lesson_generates_optional_proposal_without_writing_ledger(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nmemory:\n  default_tags:\n    - export-module\n",
        encoding="utf-8",
    )
    repo_dir.joinpath("README.md").write_text("# Demo\nchanged\n", encoding="utf-8")
    lesson = "导出模块修改空状态分支后必须执行 tests/export 下的回归测试。"
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "reflect",
            "--repo",
            str(repo_dir),
            "--note",
            "修复完成",
            "--lesson",
            lesson,
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = next(tmp_path.joinpath("runs").iterdir())
    proposals = [
        json.loads(line)
        for line in run_dir.joinpath("memory-proposals.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(proposals) == 1
    assert proposals[0]["type"] == "lesson_candidate"
    assert proposals[0]["content"] == lesson
    assert "export-module" in proposals[0]["tags"]
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["memory_proposals"][0]["id"] == proposals[0]["id"]
    assert "memory-proposals.jsonl" in state["artifacts"]
    assert "memory_proposal_written" in run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8")
    assert not tmp_path.joinpath("memory", "ledger.jsonl").exists()


def test_review_pack_generates_isolated_context_from_reflect_run(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    brief_run = _make_brief_run(tmp_path, repo_dir)
    test_log = tmp_path / "test.log"
    test_log.write_text("pytest passed\n", encoding="utf-8")
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, source_run=brief_run.name, test_log=test_log)

    run_dir = ReviewPackRuntime(tmp_path).run(repo_dir, reflect_run.name)

    for artifact in [
        "state.json",
        "trace.jsonl",
        "review-pack.md",
        "review-prompt.md",
        "review-checklist.md",
        "review-context.json",
        "review-prompt-metrics.json",
        "review-prompt-metrics.md",
        "eval.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    prompt = run_dir.joinpath("review-prompt.md").read_text(encoding="utf-8")
    assert "不包含 worker 的完整聊天记录" in prompt
    assert "pytest passed" in prompt
    assert "changed" in prompt
    assert "## Project Profile" not in prompt
    assert "## Project Knowledge" not in prompt
    assert prompt.count("测试必须说明结果") == 1
    assert "只使用已存在的文件、diff、测试摘要、日志和项目上下文" in prompt
    assert "不要自行运行验证命令或补造证据" in prompt
    assert "不要运行测试、构建、安装依赖、格式化、代码生成" in prompt
    metrics = json.loads(run_dir.joinpath("review-prompt-metrics.json").read_text(encoding="utf-8"))
    assert metrics["chars"] == len(prompt)
    assert metrics["status"] == "within_budget"
    assert metrics["sections"]["project_context"] > 0
    context = json.loads(run_dir.joinpath("review-context.json").read_text(encoding="utf-8"))
    assert context["contains_worker_chat"] is False
    assert context["changed_files"] == ["README.md"]
    assert context["truncated_sections"] == []


def test_reviewer_prompt_excludes_untracked_file_content(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    marker = "UNTRACKED_PAYLOAD_MUST_NOT_REACH_REVIEWER"
    repo_dir.joinpath("private").mkdir()
    repo_dir.joinpath("private", "generated.txt").write_text(marker + "\n", encoding="utf-8")
    repo_dir.joinpath("nested").mkdir()
    repo_dir.joinpath("nested", "AGENTS.md").write_text(
        f"# Local Rule\n\n{marker}\n",
        encoding="utf-8",
    )
    repo_dir.joinpath(".vega.yaml").write_text(
        f"version: 1\nverification:\n  commands:\n    - echo {marker}\n",
        encoding="utf-8",
    )
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir)
    runner = StaticRunner([_review_json("approve")])

    review_run = ReviewRuntime(tmp_path, runner=runner).run(repo_dir, reflect_run.name)

    for path in [
        reflect_run / "full-diff.patch",
        reflect_run / "project-context.md",
        review_run / "project-context.md",
        review_run / "review-pack.md",
        review_run / "review-prompt.md",
    ]:
        assert marker not in path.read_text(encoding="utf-8"), path
    evidence = json.loads(reflect_run.joinpath("review-evidence.json").read_text(encoding="utf-8"))
    assert set(evidence["untracked_files"]) >= {
        ".vega.yaml",
        "nested/AGENTS.md",
        "private/generated.txt",
    }
    assert runner.calls == []
    review_state = json.loads(review_run.joinpath("state.json").read_text(encoding="utf-8"))
    assert review_state["status"] == "needs_human"
    assert review_state["current_step"] == "evidence_stale"
    review_context = json.loads(
        review_run.joinpath("review-context.json").read_text(encoding="utf-8")
    )
    assert "source_untracked_files_present" in review_context["evidence_issues"]


def test_project_profile_excludes_untracked_agents_when_requested(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath("nested").mkdir()
    repo_dir.joinpath("nested", "AGENTS.md").write_text(
        "# Local Rule\n\n- 不应进入隔离审查上下文。\n",
        encoding="utf-8",
        newline="\n",
    )

    tracked_profile = build_project_profile(tmp_path, repo_dir, tracked_only=True)
    full_profile = build_project_profile(tmp_path, repo_dir)

    assert tracked_profile.agents_files == ["AGENTS.md"]
    assert "nested/AGENTS.md" in full_profile.agents_files


def test_review_pack_cli_resolves_reflect_run(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["review-pack", "--repo", str(repo_dir), "--run", reflect_run.name])

    assert result.exit_code == 0, result.output
    review_runs = [path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-review-pack")]
    assert len(review_runs) == 1
    assert review_runs[0].joinpath("review-prompt.md").exists()
    assert "下一步" in result.output

    status_result = CliRunner().invoke(app, ["status", "--run", review_runs[0].name])
    assert status_result.exit_code == 0, status_result.output
    assert "类型：`review-pack`" in status_result.output


def test_review_runtime_uses_read_only_runner_and_writes_verdict(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    brief_run = _make_brief_run(tmp_path, repo_dir)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, source_run=brief_run.name)
    runner = StaticRunner([_review_json("approve")])

    run_dir = ReviewRuntime(tmp_path, runner=runner).run(repo_dir, reflect_run.name)

    assert runner.calls[0]["sandbox"] == "read-only"
    verdict = json.loads(run_dir.joinpath("review-verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "approve"
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    context = json.loads(run_dir.joinpath("review-context.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert context["evidence_consistent"] is True
    assert context["evidence_issues"] == []
    assert "隔离 reviewer" in run_dir.joinpath("review-pack.md").read_text(encoding="utf-8")


def test_review_runtime_rejects_workspace_changes_after_reflect(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir)
    repo_dir.joinpath("README.md").write_text("# Demo\nchanged again\n", encoding="utf-8")
    runner = StaticRunner([_review_json("approve")])

    run_dir = ReviewRuntime(tmp_path, runner=runner).run(repo_dir, reflect_run.name)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    verdict = json.loads(run_dir.joinpath("review-verdict.json").read_text(encoding="utf-8"))
    context = json.loads(run_dir.joinpath("review-context.json").read_text(encoding="utf-8"))
    assert runner.calls == []
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_stale"
    assert verdict["verdict"] == "needs_human"
    assert context["evidence_consistent"] is False
    assert context["source_workspace_fingerprint"] != context["current_workspace_fingerprint"]
    assert "workspace_changed_since_reflect" in context["evidence_issues"]
    assert "FAIL: review 证据与当前工作区不属于同一快照" in run_dir.joinpath(
        "eval.md"
    ).read_text(encoding="utf-8")


def test_codex_exec_runner_builds_allowlisted_role_command(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, context, **kwargs
    ):
        captured.update(
            {
                "command": command,
                "input_text": input_text,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "context": context,
                "environment": kwargs["environment"],
            }
        )
        assert context.output_line_observer is not None
        context.output_line_observer(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
        )
        return SimpleNamespace(
            status="success",
            output=json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}
            ),
            error=None,
        )

    monkeypatch.setattr(
        "vega.runner.shutil.which",
        lambda _: "D:/tools/codex.cmd",  # repo-path-policy: allow-test-fixture
    )
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)
    runner = CodexExecRunner(
        options=CodexExecOptions(
            profile="vega-worker",
            model="gpt-test",
            reasoning_effort="medium",
            ephemeral=True,
        )
    )

    result = runner.run(
        "完成最小修改",
        repo_dir,
        sandbox="workspace-write",
        timeout_seconds=60,
    )

    assert result.status == "success"
    assert captured["command"] == [
        "D:/tools/codex.cmd",  # repo-path-policy: allow-test-fixture
        "exec",
        "--cd",
        str(repo_dir.resolve()),
        "--sandbox",
        "workspace-write",
        "--config",
        "notify=[]",
        "--disable",
        "hooks",
        "--disable",
        "memories",
        "--disable",
        "plugins",
        "--profile",
        "vega-worker",
        "--model",
        "gpt-test",
        "--config",
        'model_reasoning_effort="medium"',
        "--ephemeral",
        "--json",
        "-",
    ]
    assert captured["input_text"] == "完成最小修改"
    assert captured["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}


def test_codex_exec_runner_executes_raw_command_but_redacts_result_command(
    tmp_path,
    monkeypatch,
) -> None:
    fake_secret = "sk-test-secret-1234567890"
    repo_dir = tmp_path / f"repo-{fake_secret}"
    repo_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_run_owned_process(
        command, input_text, cwd, timeout_seconds, context, **kwargs
    ):
        del input_text, cwd, timeout_seconds
        captured["command"] = command
        assert kwargs["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        assert context.output_line_observer is not None
        context.output_line_observer(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
        )
        return SimpleNamespace(
            status="success",
            output=json.dumps(
                {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}
            ),
            error=None,
        )

    monkeypatch.setattr(
        "vega.runner.shutil.which",
        lambda _: "D:/tools/codex.cmd",  # repo-path-policy: allow-test-fixture
    )
    monkeypatch.setattr("vega.runner.run_owned_process", fake_run_owned_process)
    runner = CodexExecRunner(options=CodexExecOptions(profile=f"profile-{fake_secret}"))

    result = runner.run("完成最小修改", repo_dir, sandbox="read-only", timeout_seconds=60)

    command = captured["command"]
    assert isinstance(command, list)
    assert str(repo_dir.resolve()) in command
    assert f"profile-{fake_secret}" in command
    assert result.command is not None
    assert str(repo_dir.resolve()) not in result.command
    assert f"profile-{fake_secret}" not in result.command
    assert any("[REDACTED]" in item for item in result.command)


def test_review_runtime_turns_invalid_json_into_needs_human(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir)
    runner = StaticRunner(["不是 JSON"])

    run_dir = ReviewRuntime(tmp_path, runner=runner).run(repo_dir, reflect_run.name)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    verdict = json.loads(run_dir.joinpath("review-verdict.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert not any(
        result.startswith("PASS: reviewer 输出")
        for result in state["eval_results"]
    )
    assert verdict["verdict"] == "needs_human"
    assert "无法解析" in verdict["summary"]


def test_parse_review_verdict_ignores_codex_exec_transcript_suffix() -> None:
    output = "\n".join(
        [
            _review_json("approve"),
            "",
            "OpenAI Codex transcript",
            '{"event":"session","payload":{"note":"这不是 verdict"}}',
            "codex tokens used: 1234",
        ]
    )

    verdict = parse_review_verdict(output)

    assert verdict.verdict == "approve"
    assert verdict.summary == "测试 reviewer 结论"


def test_parse_review_verdict_accepts_identical_codex_exec_candidates() -> None:
    payload = _review_json("approve")
    output = "\n".join(
        [
            "codex",
            payload,
            "tokens used: 1234",
            payload,
        ]
    )

    verdict = parse_review_verdict(output)

    assert verdict.verdict == "approve"
    assert verdict.summary == "测试 reviewer 结论"


def test_parse_review_verdict_rejects_multiple_valid_candidates() -> None:
    output = "\n".join(
        [
            _review_json("approve"),
            "",
            _review_json("request_changes"),
        ]
    )

    verdict = parse_review_verdict(output)

    assert verdict.verdict == "needs_human"
    assert "无法解析" in verdict.summary
    assert "多个合法 verdict 候选" in verdict.summary


def test_parse_review_verdict_rejects_extra_fields() -> None:
    payload = json.loads(_review_json("approve"))
    payload["unexpected"] = True

    verdict = parse_review_verdict(json.dumps(payload, ensure_ascii=False))

    assert verdict.verdict == "needs_human"
    assert "无法解析" in verdict.summary


@pytest.mark.parametrize(
    "payload",
    [
        {
            "verdict": "approve",
            "summary": " ",
            "findings": [],
            "checked_items": ["scope"],
        },
        {
            "verdict": "approve",
            "summary": "looks good",
            "findings": [],
            "checked_items": [],
        },
        {
            "verdict": "approve",
            "summary": "looks good",
            "findings": [
                {
                    "severity": "major",
                    "title": "仍有主要问题",
                }
            ],
            "checked_items": ["scope"],
        },
    ],
)
def test_parse_review_verdict_rejects_invalid_approve_contract(
    payload: dict[str, object],
) -> None:
    verdict = parse_review_verdict(json.dumps(payload, ensure_ascii=False))

    assert verdict.verdict == "needs_human"
    assert "无法解析" in verdict.summary


def test_loop_assist_continue_generates_fix_prompt_from_review_findings(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    reviewer = StaticRunner([_review_json("request_changes")])
    runtime = LoopAutomationRuntime(tmp_path, reviewer_runner=reviewer)
    brief_input = BriefInput(
        mode="bug",
        text="修复 README 展示问题",
        source="inline-text",
        repo_path=str(repo_dir),
    )
    loop_run = runtime.start(brief_input, "assist")
    start_state = json.loads(
        loop_run.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert start_state["workspace_baseline_artifact_version"] == 1
    assert start_state["workspace_baseline_sha256"]
    trace_items = [
        json.loads(line)
        for line in loop_run.joinpath("trace.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    event_names = [item["event"] for item in trace_items]
    assert event_names.index("workspace_baseline_captured") < event_names.index(
        "worker_prompt_measured"
    )
    assert event_names.index("worker_prompt_measured") < event_names.index(
        "loop_initialized"
    )
    repo_dir.joinpath("README.md").write_text(
        "# Demo\nworker change\n",
        encoding="utf-8",
        newline="\n",
    )

    result_run = runtime.continue_assist(loop_run.name, repo_dir, reviewer_name="codex-exec")

    state = json.loads(result_run.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["iterations"][0]["verdict"] == "request_changes"
    assert result_run.joinpath("iterations", "01", "fix-prompt.md").exists()
    assert reviewer.calls[0]["sandbox"] == "read-only"


def test_assist_continue_rejects_reordered_initialization_trace(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="验证初始化 trace 的完整顺序",
            source="initialization-trace-order",
            repo_path=str(repo_dir),
        ),
        "assist",
    )
    trace_path = run_dir / "trace.jsonl"
    trace_items = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    worker_index = next(
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "worker_prompt_measured"
        and "iteration" not in item
    )
    initialized_index = next(
        index
        for index, item in enumerate(trace_items)
        if item.get("event") == "loop_initialized"
    )
    trace_items[worker_index], trace_items[initialized_index] = (
        trace_items[initialized_index],
        trace_items[worker_index],
    )
    trace_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in trace_items
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        ValueError,
        match="workspace_baseline_trace_order_invalid",
    ):
        runtime.continue_assist(run_dir.name, repo_dir, verify=False)
    state = json.loads(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    assert state["current_step"] == "waiting_for_worker"
    assert state["iterations"] == []


def test_assist_start_blocks_dirty_tracked_baseline_before_worker_handoff(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_baseline_dirty"
    assert state["iterations"] == []
    assert run_dir.joinpath("workspace-baseline.json").exists()
    assert run_dir.joinpath("workspace-check.json").exists()
    for name in (
        "worker-prompt.md",
        "worker-prompt-metrics.json",
        "worker-prompt-metrics.md",
    ):
        assert not run_dir.joinpath(name).exists()
        assert name not in state["artifacts"]
    assert "无法把后续修改安全归因" in run_dir.joinpath(
        "final-report.md"
    ).read_text(encoding="utf-8")
    next_steps = run_status_payload(tmp_path, run_dir.name)["next_steps"]
    assert not any("loop continue" in item for item in next_steps)
    with pytest.raises(ValueError, match="启动基线不可用"):
        runtime.continue_assist(run_dir.name, repo_dir, verify=False)
    assert not list(run_dir.glob("iterations/*"))


def test_assist_continue_rejects_tampered_workspace_baseline_without_iteration(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="新增 README 使用说明",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )
    baseline_path = run_dir / "workspace-baseline.json"
    baseline_path.write_bytes(baseline_path.read_bytes() + b" ")
    repo_dir.joinpath("README.md").write_text(
        "# Demo\nworker change\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="workspace_baseline_invalid"):
        runtime.continue_assist(run_dir.name, repo_dir, verify=False)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["current_step"] == "waiting_for_worker"
    assert state["iterations"] == []


def test_assist_continue_rejects_missing_workspace_baseline_without_iteration(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )
    run_dir.joinpath("workspace-baseline.json").unlink()

    with pytest.raises(ValueError, match="workspace-baseline.json_missing_or_empty"):
        runtime.continue_assist(run_dir.name, repo_dir, verify=False)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["current_step"] == "waiting_for_worker"
    assert state["iterations"] == []


def test_assist_continue_rejects_head_drift_before_iteration(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )
    repo_dir.joinpath("README.md").write_text(
        "# Demo\nworker change\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, "README.md", message="unexpected worker commit")

    result_run = runtime.continue_assist(run_dir.name, repo_dir, verify=False)

    state = json.loads(result_run.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "workspace_head_changed"
    assert len(state["iterations"]) == 1
    assert state["iterations"][0]["workspace_status"] == "failed"
    assert "无法继续归因" in result_run.joinpath("final-report.md").read_text(
        encoding="utf-8"
    )


def test_assist_baseline_ignores_harness_artifacts_in_same_repository(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath(".gitignore").write_text(
        "runs/*\n!runs/.gitkeep\n",
        encoding="utf-8",
        newline="\n",
    )
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nscope:\n  forbidden_paths:\n    - README.md\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(
        repo_dir,
        ".gitignore",
        ".vega.yaml",
        message="configure harness repository",
    )
    runtime = LoopAutomationRuntime(repo_dir)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
        verify=False,
    )
    repo_dir.joinpath("README.md").write_text(
        "# Demo\nworker change\n",
        encoding="utf-8",
        newline="\n",
    )

    runtime.continue_assist(run_dir.name, repo_dir, verify=False)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["iterations"][0]["workspace_status"] != "failed"
    assert state["current_step"] == "scope_gate_failed"
    workspace_check = json.loads(
        run_dir.joinpath(
            "iterations",
            "01",
            "workspace-check.json",
        ).read_text(encoding="utf-8")
    )
    assert workspace_check["baseline_ignored_changed"] is False


def test_loop_runtime_propagates_progress_reporter_to_worker_and_reviewer(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    worker = TrackedChangeRunner(["worker done"])
    reviewer = StaticRunner([_review_json("approve")])

    def reporter(step: str, elapsed: int) -> None:
        del step, elapsed

    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
        progress_reporter=reporter,
    )

    runtime.start(
        BriefInput(
            mode="bug",
            text="验证进度回调透传",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    worker_reporter = worker.calls[0]["execution_context"].progress_reporter
    reviewer_reporter = reviewer.calls[0]["execution_context"].progress_reporter
    assert isinstance(worker_reporter, ExecutionProgressAdapter)
    assert isinstance(reviewer_reporter, ExecutionProgressAdapter)
    assert worker_reporter.delegate is reporter
    assert reviewer_reporter.delegate is reporter


def test_loop_continue_rejects_repo_mismatch_without_mutating_run(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    other_repo = tmp_path / "other-repo"
    _init_clean_git_repo(repo_dir)
    _init_clean_git_repo(other_repo)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )

    with pytest.raises(ValueError, match="目标仓库不匹配"):
        runtime.continue_assist(run_dir.name, other_repo, verify=False)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "waiting_for_worker"
    assert state["iterations"] == []


def test_loop_continue_rejects_terminal_run(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    _write_isolated_verification_config(repo_dir)
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
    )
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=True,
    )

    with pytest.raises(ValueError, match="只有 needs_human"):
        runtime.continue_assist(run_dir.name, repo_dir, verify=False)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert len(state["iterations"]) == 1


def test_loop_writes_project_context_into_worker_prompt(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    brief_input = BriefInput(
        mode="feature",
        text="新增 README 功能说明",
        source="inline-text",
        repo_path=str(repo_dir),
    )

    run_dir = runtime.start(brief_input, "assist")

    project_context = run_dir.joinpath("project-context.md").read_text(encoding="utf-8")
    worker_prompt = run_dir.joinpath("worker-prompt.md").read_text(encoding="utf-8")
    assert "项目上下文" in project_context
    assert "测试必须说明结果" in project_context
    assert "## 验证职责边界" in project_context
    assert "不是对 worker 命令执行能力的确定性拦截" in project_context
    assert "必须保持未加引号" in project_context
    assert "worker 自检不得新增或修改 ignored、未跟踪文件或 Git 控制状态" in project_context
    assert "仓库内目录不属于 Workspace Gate 豁免区" in project_context
    assert "否则跳过并说明，交给 Runtime 固定 verification" in project_context
    assert "## 项目上下文" in worker_prompt
    assert "测试必须说明结果" in worker_prompt
    assert "Vega 会在 worker 返回后独立执行" in worker_prompt
    assert "Worker 自检不得新增或修改 ignored、未跟踪文件或 Git 控制状态" in worker_prompt
    assert "仓库内的 `.tmp/`、`target/` 等路径也不属于 Workspace Gate 豁免区" in worker_prompt
    assert "不得为了绕过检查把自检产物写到仓库父目录" in worker_prompt
    assert "不要运行带 `{{vega_verification_temp}}` 的 harness-owned 命令" in worker_prompt
    assert "否则跳过并说明，交给 Vega 固定 verification" in worker_prompt
    assert "所有自检产生的缓存、临时文件和中间输出" not in worker_prompt


def test_loop_auto_runs_detected_verification_commands(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    repo_dir.joinpath(".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n",
        encoding="utf-8",
    )
    repo_dir.joinpath("pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    tests_dir.joinpath("test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _commit_repo_paths(
        repo_dir,
        ".gitignore",
        "pyproject.toml",
        "tests/test_ok.py",
        message="add passing verification fixture",
    )
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
        timeout_seconds=60,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )

    verification_summary = run_dir / "iterations" / "01" / "verification-summary.md"
    assert verification_summary.exists()
    text = verification_summary.read_text(encoding="utf-8")
    assert "python -m pytest -q" in text
    assert "PASS" in text
    assert "python -m pytest -q" in (run_dir / "iterations" / "01" / "test-summary.md").read_text(
        encoding="utf-8"
    )
    assert run_dir.joinpath("iterations", "01", "review-context.json").exists()
    assert run_dir.joinpath("iterations", "01", "review-checklist.md").exists()
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["memory_proposals"] == []
    assert not run_dir.joinpath("memory-proposals.jsonl").exists()


def test_loop_uses_vega_yaml_verification_commands(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('configured verification')\"",
                "  max_commands: 1",
                "  timeout_seconds: 60",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add verification config")
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
        timeout_seconds=60,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="新增 README 功能说明",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )

    summary = (run_dir / "iterations" / "01" / "verification-summary.md").read_text(encoding="utf-8")
    payload = json.loads(
        run_dir.joinpath(
            "iterations",
            "01",
            "verification-result.json",
        ).read_text(encoding="utf-8")
    )
    assert "configured verification" in summary
    assert "python -c" in summary
    assert payload["results"][0]["command"] == payload["results"][0]["configured_command"]
    assert payload["results"][0]["command"] == payload["results"][0]["executed_command"]


def test_loop_uses_separate_worker_and_reviewer_codex_options(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "runner:",
                "  worker: codex-exec",
                "  reviewer: codex-exec",
                "  codex_exec:",
                "    worker:",
                "      profile: vega-worker",
                "      reasoning_effort: medium",
                "      ephemeral: true",
                "    reviewer:",
                "      profile: vega-reviewer",
                "      reasoning_effort: high",
                "      ephemeral: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add runner config")
    captured: dict[str, object] = {}

    def make_worker_runner(name: str, options: CodexExecOptions | None = None):
        assert name == "codex-exec"
        captured["worker"] = options
        return TrackedChangeRunner(["worker done"])

    def make_reviewer_runner(
        name: str,
        options: CodexExecOptions | None = None,
        *,
        output_schema: dict[str, object] | None = None,
    ):
        assert name == "codex-exec"
        captured["reviewer"] = options
        captured["reviewer_output_schema"] = output_schema
        return StaticRunner([_review_json("approve")])

    monkeypatch.setattr("vega.loop_runtime.make_runner", make_worker_runner)
    monkeypatch.setattr("vega.review_runtime.make_runner", make_reviewer_runner)

    run_dir = LoopAutomationRuntime(tmp_path, timeout_seconds=60).start(
        BriefInput(
            mode="feature",
            text="新增 README 功能说明",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    assert captured["worker"] == CodexExecOptions(
        profile="vega-worker",
        reasoning_effort="medium",
        ephemeral=True,
    )
    assert captured["reviewer"] == CodexExecOptions(
        profile="vega-reviewer",
        reasoning_effort="high",
        ephemeral=True,
    )
    reviewer_schema = captured["reviewer_output_schema"]
    assert isinstance(reviewer_schema, dict)
    assert reviewer_schema["properties"]["risk_disclosures"]["maxItems"] == 0
    project_context = run_dir.joinpath("project-context.md").read_text(encoding="utf-8")
    assert "`worker.reasoning_effort`：`medium`" in project_context
    assert "`reviewer.reasoning_effort`：`high`" in project_context


def test_auto_loop_keeps_start_time_reviewer_policy_after_worker_changes_config(
    tmp_path,
    monkeypatch,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "prompt_budget:",
                "  reviewer_max_chars: 60000",
                "runner:",
                "  reviewer: codex-exec",
                "  codex_exec:",
                "    reviewer:",
                "      reasoning_effort: high",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add reviewer policy")

    class ConfigMutatingWorker:
        def run(
            self,
            prompt: str,
            repo_path: Path,
            *,
            sandbox: str,
            timeout_seconds: int,
            execution_context: RunnerExecutionContext | None = None,
        ) -> RunnerResult:
            repo_path.joinpath(".vega.yaml").write_text(
                "\n".join(
                    [
                        "version: 1",
                        "prompt_budget:",
                        "  reviewer_max_chars: 1000",
                        "runner:",
                        "  reviewer: none",
                    ]
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            return RunnerResult(status="success", output="config changed", command=["mutating-worker"])

    reviewer = StaticRunner([_review_json("approve")])
    captured: dict[str, object] = {}

    def make_reviewer_runner(
        name: str,
        options: CodexExecOptions | None = None,
        *,
        output_schema: dict[str, object] | None = None,
    ):
        captured["name"] = name
        captured["options"] = options
        captured["output_schema"] = output_schema
        return reviewer

    monkeypatch.setattr("vega.review_runtime.make_runner", make_reviewer_runner)
    run_dir = LoopAutomationRuntime(
        tmp_path,
        worker_runner=ConfigMutatingWorker(),
        timeout_seconds=60,
    ).start(
        BriefInput(
            mode="feature",
            text="验证 auto loop 的 reviewer 策略快照",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration_dir = run_dir / "iterations" / "01"
    assert captured == {}
    assert reviewer.calls == []
    assert state["status"] == "needs_human"
    assert state["current_step"] == "project_policy_changed"
    assert state["iterations"][0]["worker_status"] == "success"
    assert not iteration_dir.joinpath("verification-summary.md").exists()
    assert not iteration_dir.joinpath("review-prompt.md").exists()
    report = iteration_dir.joinpath("project-policy-change-report.md").read_text(encoding="utf-8")
    assert "未继续自动 verification、reflect 或 reviewer" in report


def test_create_run_dir_avoids_existing_directory_collision(tmp_path) -> None:
    existing = tmp_path / "runs" / "20260711-120000-demo"
    existing.mkdir(parents=True)
    existing.joinpath("sentinel.txt").write_text("do not overwrite\n", encoding="utf-8")

    first_id, first_dir = create_run_dir(tmp_path, "20260711-120000-demo")
    second_id, second_dir = create_run_dir(tmp_path, "20260711-120000-demo")

    assert first_id != second_id
    assert first_dir != second_dir
    assert first_dir.exists()
    assert second_dir.exists()
    assert existing.joinpath("sentinel.txt").read_text(encoding="utf-8") == "do not overwrite\n"


def test_config_check_rejects_truncated_verification_command(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n  commands:\n    - python -c \\\n",
        encoding="utf-8",
    )
    result = check_project_config(repo_dir)
    assert result.status == "failed"
    assert any(issue.code == "truncated_verification_command" for issue in result.issues)

    monkeypatch.chdir(tmp_path)
    cli_result = CliRunner().invoke(app, ["config", "check", "--repo", str(repo_dir)])
    assert cli_result.exit_code == 1
    assert "truncated_verification_command" in cli_result.output


def test_project_config_parses_codex_exec_role_options(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "runner:",
                "  codex_exec:",
                "    worker:",
                "      model: gpt-worker",
                "      reasoning_effort: medium",
                "    reviewer:",
                "      model: gpt-reviewer",
                "      reasoning_effort: high",
                "      ephemeral: true",
                "prompt_budget:",
                "  worker_max_chars: 12000",
                "  reviewer_max_chars: 24000",
                "  reviewer_diff_max_chars: 8000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_project_config(repo_dir)

    assert config.runner.codex_exec.worker.model == "gpt-worker"
    assert config.runner.codex_exec.worker.reasoning_effort == "medium"
    assert not config.runner.codex_exec.worker.ephemeral
    assert config.runner.codex_exec.reviewer.model == "gpt-reviewer"
    assert config.runner.codex_exec.reviewer.reasoning_effort == "high"
    assert config.runner.codex_exec.reviewer.ephemeral
    assert config.prompt_budget.worker_max_chars == 12000
    assert config.prompt_budget.reviewer_max_chars == 24000
    assert config.prompt_budget.reviewer_diff_max_chars == 8000


def test_loop_stops_before_worker_when_prompt_budget_is_exceeded(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nprompt_budget:\n  worker_max_chars: 1000\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add prompt budget")
    worker = StaticRunner(["worker should not run"])
    reviewer = StaticRunner([_review_json("approve")])
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
        timeout_seconds=60,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="新增 README 功能说明",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    metrics = json.loads(
        run_dir.joinpath("iterations", "01", "worker-prompt-metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["status"] == "needs_human"
    assert state["current_step"] == "worker_context_budget"
    assert state["iterations"][0]["worker_status"] == "skipped"
    assert metrics["status"] == "exceeded"
    assert run_dir.joinpath("iterations", "01", "worker-context-budget-report.md").exists()
    assert worker.calls == []
    assert reviewer.calls == []
    payload = run_status_payload(tmp_path, run_dir.name)
    assert any("prompt_budget" in step for step in payload["next_steps"])
    assert any("worker-prompt-metrics.md" in path for path in payload["key_artifacts"])


def test_review_runtime_stops_before_runner_when_prompt_budget_is_exceeded(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nprompt_budget:\n  reviewer_max_chars: 1000\n",
        encoding="utf-8",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add review budget")
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir)
    runner = StaticRunner([_review_json("approve")])

    run_dir = ReviewRuntime(tmp_path, runner=runner).run(repo_dir, reflect_run.name)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    verdict = json.loads(run_dir.joinpath("review-verdict.json").read_text(encoding="utf-8"))
    metrics = json.loads(run_dir.joinpath("review-prompt-metrics.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "context_budget"
    assert state["runner_status"] == "skipped"
    assert verdict["verdict"] == "needs_human"
    assert metrics["status"] == "exceeded"
    assert run_dir.joinpath("review-context-budget-report.md").exists()
    assert runner.calls == []
    payload = run_status_payload(tmp_path, run_dir.name)
    assert any("reviewer 未启动" in step for step in payload["next_steps"])
    assert any("review-prompt-metrics.md" in path for path in payload["key_artifacts"])


def test_review_runtime_cannot_approve_truncated_evidence(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "prompt_budget:",
                "  reviewer_max_chars: 60000",
                "  reviewer_diff_max_chars: 1000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add review diff budget")
    repo_dir.joinpath("README.md").write_bytes(
        ("# Demo\n" + "\n".join(f"changed line {index}" for index in range(400)) + "\n").encode(
            "utf-8"
        )
    )
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir)
    runner = StaticRunner([_review_json("approve")])

    run_dir = ReviewRuntime(tmp_path, runner=runner).run(repo_dir, reflect_run.name)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    verdict = json.loads(run_dir.joinpath("review-verdict.json").read_text(encoding="utf-8"))
    context = json.loads(run_dir.joinpath("review-context.json").read_text(encoding="utf-8"))
    assert len(runner.calls) == 1
    assert context["truncated_sections"] == ["full_diff"]
    assert verdict["verdict"] == "needs_human"
    assert verdict["findings"][0]["title"] == "Review 证据不完整"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_truncated"


@pytest.mark.parametrize(
    "worker_config",
    [
        "      reasoning_effort: extreme",
        "      args:\n        - --dangerously-bypass-approvals-and-sandbox",
        "      profile: --dangerously-bypass-approvals-and-sandbox",
        "      profile: ../unsafe-profile",
    ],
)
def test_config_check_rejects_unsafe_codex_exec_options(tmp_path, worker_config) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "runner:",
                "  codex_exec:",
                "    worker:",
                worker_config,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_project_config(repo_dir)

    assert result.status == "failed"
    assert any(issue.code == "invalid_project_config" for issue in result.issues)


def test_invalid_verification_config_blocks_verification_with_clear_summary(tmp_path) -> None:
    from vega.verification import run_project_verification

    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n  commands:\n    - python -c \\\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "workspace" / "runs" / "run-config-invalid" / "iterations" / "03"

    result = run_project_verification(
        tmp_path,
        repo_dir,
        output_dir,
        iteration=3,
    )

    assert result.has_failures
    assert result.failure_kind == "project_config_invalid"
    assert result.command_count == 0
    assert result.failed_count == 0
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    assert payload["artifact_version"] == 2
    assert payload["run_id"] == "run-config-invalid"
    assert payload["iteration"] == 3
    assert payload["shell_kind"] in {"cmd", "posix-sh"}
    assert payload["failure_kind"] == "project_config_invalid"
    assert payload["commands"] == []
    assert payload["results"] == []
    assert payload["command_count"] == 0
    assert payload["failed_count"] == 0
    assert payload["selected_command_count"] == 0
    assert payload["skipped_commands"] == []
    text = result.summary_path.read_text(encoding="utf-8")
    assert "项目验证配置预检失败" in text
    assert "truncated_verification_command" in text


def test_loop_verification_failure_blocks_reviewer_approve(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    repo_dir.joinpath(".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n",
        encoding="utf-8",
    )
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nverification:\n  commands:\n    - python -m pytest -q\n  max_commands: 1\n",
        encoding="utf-8",
    )
    repo_dir.joinpath("pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    tests_dir.joinpath("test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    _commit_repo_paths(
        repo_dir,
        ".gitignore",
        ".vega.yaml",
        "pyproject.toml",
        "tests/test_fail.py",
        message="add failing verification fixture",
    )
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
        timeout_seconds=60,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["iterations"][0]["verdict"] == "approve"
    assert state["iterations"][0]["verification_status"] == "failed"
    assert "验证命令失败" in run_dir.joinpath("final-report.md").read_text(encoding="utf-8")


def test_loop_auto_stops_on_workspace_pollution_before_review(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "budget:",
                "  max_new_files: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('should not run')\"",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add workspace budget")
    reviewer = StaticRunner([_review_json("approve")])
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=PollutingRunner(file_count=3),
        reviewer_runner=reviewer,
        timeout_seconds=60,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration = state["iterations"][0]
    assert state["status"] == "needs_human"
    assert iteration["workspace_status"] == "failed"
    assert iteration["verification_status"] == "skipped"
    assert reviewer.calls == []
    workspace_check = run_dir / "iterations" / "01" / "workspace-check.md"
    assert workspace_check.exists()
    assert "3 > 1" in workspace_check.read_text(encoding="utf-8")
    assert "工作区完整性检查失败" in run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    )
    next_steps = run_status_payload(tmp_path, run_dir.name)["next_steps"]
    assert any("工作区完整性检查失败" in item for item in next_steps)
    assert any("ignored 路径、Git 控制状态和启动基线变化" in item for item in next_steps)


def test_latest_and_status_cli_show_next_steps_for_loop(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["loop", "bug", "--repo", str(repo_dir), "--text", "修复 README 展示问题", "--mode", "assist"],
    )
    assert result.exit_code == 0, result.output
    assert "下一步" in result.output
    assert "worker-prompt.md" in result.output
    run_dir = next(path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-bug-loop"))

    status_result = CliRunner().invoke(app, ["status", "--run", run_dir.name])
    assert status_result.exit_code == 0, status_result.output
    assert "Run Status" in status_result.output
    assert "loop continue" in status_result.output

    latest_result = CliRunner().invoke(app, ["latest", "--kind", "loop"])
    assert latest_result.exit_code == 0, latest_result.output
    assert run_dir.name in latest_result.output

    json_result = CliRunner().invoke(app, ["status", "--run", run_dir.name, "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["kind"] == "loop"
    assert payload["next_steps"]


def test_do_cli_runs_assist_loop_as_daily_entry(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["do", "feature", "--repo", str(repo_dir), "--text", "新增 README 使用说明", "--mode", "assist"],
    )

    assert result.exit_code == 0, result.output
    assert "loop 运行完成" in result.output
    run_dir = next(path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-feature-loop"))
    assert run_dir.joinpath("project-context.md").exists()


def test_gate_cli_flags_high_risk_paths_and_missing_tests(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    auth_dir = repo_dir / "src" / "auth"
    auth_dir.mkdir(parents=True)
    auth_dir.joinpath("token.py").write_text("TOKEN_TTL = 60\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, note="修改鉴权 token 逻辑")

    result = CliRunner().invoke(app, ["gate", "--repo", str(repo_dir), "--run", reflect_run.name])

    assert result.exit_code == 0, result.output
    assert "human-review" in result.output
    gate_runs = [path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-gate")]
    assert len(gate_runs) == 1
    gate_result = json.loads(gate_runs[0].joinpath("gate-result.json").read_text(encoding="utf-8"))
    assert gate_result["risk"] == "high"
    assert gate_result["recommendation"] == "human-review"
    assert {reason["code"] for reason in gate_result["reasons"]} >= {"high_risk_paths", "missing_tests"}

    json_result = CliRunner().invoke(
        app,
        ["gate", "--repo", str(repo_dir), "--run", reflect_run.name, "--json"],
    )
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output)["recommendation"] == "human-review"


def test_gate_uses_vega_yaml_high_risk_paths(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nrisk:\n  high_paths:\n    - README.md\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, note="修改 README")

    result = CliRunner().invoke(app, ["gate", "--repo", str(repo_dir), "--run", reflect_run.name, "--json"])

    assert result.exit_code == 0, result.output
    gate_result = json.loads(result.output)
    assert gate_result["risk"] == "high"
    assert any(reason["code"] == "high_risk_paths" for reason in gate_result["reasons"])


def test_gate_uses_vega_yaml_change_budget(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "budget:",
                "  max_changed_files: 1",
                "  max_diff_lines: 0",
                "  max_new_files: 0",
                "  forbid_new_dependencies: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    repo_dir.joinpath("pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    docs_dir = repo_dir / "docs"
    docs_dir.mkdir()
    docs_dir.joinpath("new.md").write_text("# 新文件\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, note="触发变更预算")

    result = CliRunner().invoke(app, ["gate", "--repo", str(repo_dir), "--run", reflect_run.name, "--json"])

    assert result.exit_code == 0, result.output
    gate_result = json.loads(result.output)
    reason_codes = {reason["code"] for reason in gate_result["reasons"]}
    assert gate_result["risk"] == "high"
    assert {
        "budget_changed_files",
        "budget_diff_lines",
        "budget_new_files",
        "new_dependencies",
    }.issubset(reason_codes)


def test_gate_scope_profile_relaxes_change_budget(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "budget:",
                "  max_changed_files: 1",
                "budget_profiles:",
                "  refactor:",
                "    max_changed_files: 10",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_dir / "a.txt").write_text("a\n", encoding="utf-8")
    (repo_dir / "b.txt").write_text("b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, note="refactor scope")

    default_result = CliRunner().invoke(app, ["gate", "--repo", str(repo_dir), "--run", reflect_run.name, "--json"])
    scoped_result = CliRunner().invoke(
        app,
        ["gate", "--repo", str(repo_dir), "--run", reflect_run.name, "--scope", "refactor", "--json"],
    )

    assert default_result.exit_code == 0, default_result.output
    assert scoped_result.exit_code == 0, scoped_result.output
    default_codes = {reason["code"] for reason in json.loads(default_result.output)["reasons"]}
    scoped_payload = json.loads(scoped_result.output)
    scoped_codes = {reason["code"] for reason in scoped_payload["reasons"]}
    assert "budget_changed_files" in default_codes
    assert "budget_changed_files" not in scoped_codes
    assert scoped_payload["scope_profile"] == "refactor"


def test_plan_cli_writes_change_plan_with_scope_profile(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath(".vega.yaml").write_text(
        "version: 1\nbudget_profiles:\n  refactor:\n    max_changed_files: 20\n    max_diff_lines: 1200\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["plan", "--repo", str(repo_dir), "--text", "重构 loop 状态机", "--scope", "refactor"],
    )

    assert result.exit_code == 0, result.output
    run_dir = next(path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-change-plan"))
    assert run_dir.joinpath("change-plan.md").exists()
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    scope_text = run_dir.joinpath("scope-profile.md").read_text(encoding="utf-8")
    assert "refactor" in scope_text
    assert "1200" in scope_text


def test_adapters_init_codex_writes_vega_skills(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    legacy_skill = repo_dir / ".codex" / "skills" / "vega-loop" / "SKILL.md"
    legacy_skill.parent.mkdir(parents=True)
    legacy_skill.write_text("legacy skill\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["adapters", "init", "codex", "--repo", str(repo_dir)])

    assert result.exit_code == 0, result.output
    loop_skill = repo_dir / ".agents" / "skills" / "vega-loop" / "SKILL.md"
    review_skill = repo_dir / ".agents" / "skills" / "vega-review" / "SKILL.md"
    agent_skill = repo_dir / ".agents" / "skills" / "vega-agent" / "SKILL.md"
    assert loop_skill.exists()
    assert review_skill.exists()
    assert agent_skill.exists()
    assert legacy_skill.read_text(encoding="utf-8") == "legacy skill\n"
    assert ".agents" in result.output
    assert ".codex" not in result.output
    loop_skill_text = loop_skill.read_text(encoding="utf-8")
    assert "vega loop bug" in loop_skill_text
    assert "workspace-baseline.json" in loop_skill_text
    assert "不要执行 Worker，也不要 `loop continue`" in loop_skill_text
    assert "宿主原生子代理" in loop_skill_text
    assert "不把子代理完整聊天传给 Reviewer" in loop_skill_text
    assert "vega finish --run <loop_run_id> --json" in loop_skill_text
    assert "git diff --cached --no-ext-diff --unified=3" in loop_skill_text
    assert "git diff --no-ext-diff --unified=3" in loop_skill_text
    assert "不得静默省略" in loop_skill_text
    assert "未被 Reviewer 标记为重点" in loop_skill_text
    required_plan_sections = [
        "## User Goal",
        "## Non-goals",
        "## Observed Facts",
        "## Hypotheses",
        "## Proposed Scope",
        "## Verification",
        "## Risk Areas",
        "## Unresolved Decisions",
    ]
    protocol_text = PROJECT_ROOT.joinpath("docs", "PLAN-FIRST-PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    for section in required_plan_sections:
        assert section in loop_skill_text
        assert section in protocol_text
    assert loop_skill_text.index("其余任务先只读调查") < loop_skill_text.index(
        "用户明确批准"
    )
    assert loop_skill_text.index("用户明确批准") < loop_skill_text.index(
        "vega loop bug"
    )
    assert "同一会话不能同时充当独立 Reviewer" in protocol_text
    assert "本阶段不增加 `vega adapters init claude`" in protocol_text
    assert "vega gate" in review_skill.read_text(encoding="utf-8")
    agent_skill_text = agent_skill.read_text(encoding="utf-8")
    assert "vega agent capabilities" in agent_skill_text
    assert "V1 只保留一个 `pending` Work Item" in agent_skill_text
    assert "vega agent approve --run <agent_run> --actor human" in agent_skill_text
    assert "vega agent run --run <agent_run> --timeout 900" in agent_skill_text
    assert "vega watch --run <agent_run> --follow" in agent_skill_text
    assert "vega agent finalize --run <agent_run>" in agent_skill_text
    assert "stopped`：当前本机 run 已终止" in agent_skill_text
    assert "不要运行 `resume-local`" in agent_skill_text
    assert "不执行 Git 操作" in agent_skill_text
    assert "只有可信 Core Finish 为 `ready_to_commit`" in agent_skill_text

    second = CliRunner().invoke(app, ["adapters", "init", "codex", "--repo", str(repo_dir)])
    assert second.exit_code == 0, second.output
    assert "未覆盖" in second.output


def test_finish_cli_summarizes_successful_loop(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    _write_isolated_verification_config(repo_dir)
    worker = TrackedChangeRunner(["worker done"])
    reviewer = StaticRunner([_review_json("approve")])
    runtime = LoopAutomationRuntime(tmp_path, worker_runner=worker, reviewer_runner=reviewer)
    brief_input = BriefInput(
        mode="bug",
        text="修复 README 展示问题",
        source="inline-text",
        repo_path=str(repo_dir),
    )
    run_dir = runtime.start(brief_input, "auto", max_iterations=1)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["finish", "--run", run_dir.name])

    assert result.exit_code == 0, result.output
    assert run_dir.joinpath("finish-report.md").exists()
    summary = json.loads(run_dir.joinpath("finish-summary.json").read_text(encoding="utf-8"))
    assert summary["finish_status"] == "ready_to_commit"
    assert summary["latest_verdict"]["verdict"] == "approve"
    first_screen = summary["first_screen"]
    assert first_screen["decision"]["status"] == "ready_to_commit"
    assert first_screen["decision"]["run_id"] == run_dir.name
    assert Path(first_screen["decision"]["repo_path"]) == repo_dir.resolve()
    assert first_screen["decision"]["task_mode"] == "bug"
    assert first_screen["decision"]["automation_mode"] == "auto"
    assert first_screen["actual_changes"]["changed_files"] == ["README.md"]
    assert first_screen["actual_changes"]["changed_files_source"] == "trusted_risk_gate"
    assert first_screen["gates"]["workspace"] == "skipped"
    assert first_screen["gates"]["scope"] == {
        "pre_verification": "skipped",
        "post_verification": "skipped",
        "pre_review": "skipped",
    }
    assert first_screen["gates"]["artifact_integrity"]["status"] == "valid"
    assert first_screen["gates"]["evidence_freshness"]["status"] == "fresh"
    assert first_screen["verification"]["checks"]
    assert {
        item["status"] for item in first_screen["verification"]["checks"]
    } == {"passed"}
    assert first_screen["review"]["verdict"] == "approve"
    assert first_screen["review"]["coverage"]["complete"] is True
    assert first_screen["review"]["coverage"]["reviewed_files"] == ["README.md"]
    assert first_screen["review"]["priority_files"] == []
    assert first_screen["review"]["other_changed_files"] == ["README.md"]

    report_text = run_dir.joinpath("finish-report.md").read_text(encoding="utf-8")
    expected_sections = [
        "## 当前裁决",
        "## 实际变更",
        "## 确定性 Gate",
        "## 验证结果",
        "## Reviewer 意见",
        "## 证据上限",
        "## 下一步",
    ]
    assert [report_text.index(section) for section in expected_sections] == sorted(
        report_text.index(section) for section in expected_sections
    )
    assert "ready_to_commit 只表示满足人工提交前检查" in report_text
    assert "Reviewer 文件覆盖：`1/1`，状态=`complete`" in report_text
    assert "其他已变更项：`README.md`" in report_text
    assert "不代表这些文件不重要" in report_text
    assert "commit 前 checklist" in report_text.lower()

    json_result = CliRunner().invoke(app, ["finish", "--run", run_dir.name, "--json"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output)["finish_status"] == "ready_to_commit"


def test_finish_report_preserves_missing_reviewer_line_and_verification_statuses() -> None:
    summary = {
        "first_screen": {
            "decision": {
                "status": "needs_human",
                "loop_status": "needs_human",
                "loop_step": "review",
                "reasons": ["Reviewer 要求人工处理。"],
            },
            "actual_changes": {
                "changed_file_count": 1,
                "changed_files": ["README.md"],
                "changed_files_source": "trusted_risk_gate",
                "workspace_new_files_count": 0,
                "budget_findings": [],
                "high_risk_findings": [],
                "required_reviews": [],
            },
            "gates": {},
            "verification": {
                "trusted_passed": False,
                "latest_failed": True,
                "historical_failures": False,
                "checks": [
                    {
                        "iteration": 1,
                        "command": "python -m compileall src",
                        "status": "passed",
                        "returncode": 0,
                        "duration_seconds": 0.5,
                    },
                    {
                        "iteration": 1,
                        "command": "python -m pytest",
                        "status": "failed",
                        "returncode": 1,
                        "duration_seconds": 2.0,
                    },
                    {
                        "iteration": 1,
                        "command": "ruff check src tests",
                        "status": "timed_out",
                        "returncode": None,
                        "duration_seconds": 60.0,
                    },
                    {
                        "iteration": 1,
                        "command": "git diff --check",
                        "status": "skipped",
                        "returncode": None,
                        "duration_seconds": None,
                    },
                ],
            },
            "review": {
                "verdict": "request_changes",
                "summary": "需要补充验证。",
                "findings": [
                    {
                        "severity": "major",
                        "file": "README.md",
                        "line": 0,
                        "title": "缺少验证说明",
                        "evidence": "未看到验证结果。",
                        "recommendation": "补充验证。",
                    }
                ],
                "risk_disclosures": [],
                "checked_items": ["验证证据"],
            },
            "evidence_limits": ["Reviewer 未提供关键行；Finish 未补造行号。"],
            "next_steps": ["补充验证后重新运行 finish。"],
        },
        "commit_checklist": [],
        "iterations": [],
        "memory_proposals": [],
        "decisions": [],
        "key_artifacts": [],
    }

    report_text = render_finish_report(summary)

    assert "`README.md:0`" not in report_text
    assert "`README.md`（Reviewer 未提供行号）" in report_text
    assert "needs_human" in report_text
    assert "基本可以" not in report_text
    for status in ["PASSED", "FAILED", "TIMED_OUT", "SKIPPED"]:
        assert f"`{status}`" in report_text


def test_finish_review_keeps_priority_and_other_changed_files() -> None:
    review = build_finish_review_section(
        ReviewVerdict(
            verdict="request_changes",
            summary="主实现存在需要修复的问题。",
            findings=[
                ReviewFinding(
                    severity="major",
                    file="src/main.py",
                    line=12,
                    title="错误处理不完整",
                    evidence="异常分支未保留原始原因。",
                    recommendation="保留异常链并补测试。",
                )
            ],
            reviewed_files=["src/main.py", "src/glue.py"],
            checked_items=["完整 diff", "错误处理"],
        ),
        ["src/main.py", "src/glue.py"],
        changed_files_source="trusted_risk_gate",
    )

    assert review["coverage"]["complete"] is True
    assert review["priority_files"] == ["src/main.py"]
    assert review["other_changed_files"] == ["src/glue.py"]


def test_decision_cli_records_run_decisions_and_status_shows_them(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
    )
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )
    monkeypatch.chdir(tmp_path)

    approve = CliRunner().invoke(
        app,
        [
            "decision",
            "approve",
            "--run",
            run_dir.name,
            "--type",
            "finish",
            "--reason",
            "已人工检查 diff 和测试结果",
            "--ref",
            "finish-report.md",
        ],
    )
    assert approve.exit_code == 0, approve.output
    assert run_dir.joinpath("decisions.jsonl").exists()

    listed = CliRunner().invoke(app, ["decision", "list", "--run", run_dir.name, "--json"])
    assert listed.exit_code == 0, listed.output
    decisions = json.loads(listed.output)
    assert decisions[0]["type"] == "finish"
    assert decisions[0]["decision"] == "approved"

    status_result = CliRunner().invoke(app, ["status", "--run", run_dir.name, "--json"])
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["decision_count"] == 1
    assert payload["latest_decisions"][0]["reason"] == "已人工检查 diff 和测试结果"

    finish = CliRunner().invoke(app, ["finish", "--run", run_dir.name])
    assert finish.exit_code == 0, finish.output
    assert "已人工检查 diff 和测试结果" in run_dir.joinpath("finish-report.md").read_text(encoding="utf-8")


def test_loop_auto_stops_after_max_iterations(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    _write_isolated_verification_config(repo_dir)
    worker = TrackedChangeRunner(["worker done", "worker done"])
    reviewer = StaticRunner([_review_json("request_changes"), _review_json("request_changes")])
    runtime = LoopAutomationRuntime(tmp_path, worker_runner=worker, reviewer_runner=reviewer)
    brief_input = BriefInput(
        mode="feature",
        text="新增 README 功能说明",
        source="inline-text",
        repo_path=str(repo_dir),
    )

    run_dir = runtime.start(brief_input, "auto", max_iterations=2)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert len(state["iterations"]) == 2
    assert len(worker.calls) == 2
    assert worker.calls[0]["sandbox"] == "workspace-write"
    assert all(call["sandbox"] == "read-only" for call in reviewer.calls)
    assert run_dir.joinpath("final-report.md").exists()
    verification_temp_root = (
        repo_dir.resolve()
        / ".tmp"
        / "vega-verification"
        / run_dir.name
    )
    executed_commands: set[str] = set()
    verification_temp_paths: set[str] = set()
    for iteration in (1, 2):
        iteration_dir = run_dir / "iterations" / f"{iteration:02d}"
        payload = json.loads(
            iteration_dir.joinpath("verification-result.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload["command_count"] == 2
        for command_index, command_result in enumerate(payload["results"], start=1):
            expected_temp = (
                verification_temp_root
                / f"iteration-{iteration}"
                / f"command-{command_index}"
            )
            assert expected_temp.is_dir()
            assert "{{vega_verification_temp}}" in command_result["configured_command"]
            assert command_result["command"] == command_result["configured_command"]
            assert "VEGA_VERIFICATION_TEMP" in command_result["executed_command"]
            assert str(expected_temp) not in command_result["executed_command"]
            assert command_result["verification_temp"] == expected_temp.relative_to(
                repo_dir.resolve()
            ).as_posix()
            verification_temp_paths.add(command_result["verification_temp"])
            assert "{{vega_verification_temp}}" not in command_result["executed_command"]
            executed_commands.add(command_result["executed_command"])
            execution = json.loads(
                iteration_dir.joinpath(
                    "executions",
                    f"verification-{command_index:02d}",
                    "execution.json",
                ).read_text(encoding="utf-8")
            )
            assert execution["iteration"] == iteration
    assert len(executed_commands) == 2
    assert len(verification_temp_paths) == 4
    integrity = validate_loop_artifact_integrity(tmp_path, repo_dir, run_dir)
    assert integrity.valid, integrity.issues
    assert len(integrity.verification_results) == 2
    assert all(
        result["results"][0]["configured_command"]
        and result["results"][0]["executed_command"]
        for result in integrity.verification_results
    )


def test_loop_two_iteration_success_finishes_with_isolated_verification(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    _write_isolated_verification_config(repo_dir)
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["first worker pass", "second worker pass"]),
        reviewer_runner=StaticRunner(
            [_review_json("request_changes"), _review_json("approve")]
        ),
    )

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="两轮修复后通过隔离验证",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=2,
    )
    FinishRuntime(tmp_path).run(run_dir.name)

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    summary = json.loads(
        run_dir.joinpath("finish-summary.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "success"
    assert [item["verdict"] for item in state["iterations"]] == [
        "request_changes",
        "approve",
    ]
    verification_temp_paths: set[str] = set()
    for iteration in (1, 2):
        payload = json.loads(
            run_dir.joinpath(
                "iterations",
                f"{iteration:02d}",
                "verification-result.json",
            ).read_text(encoding="utf-8")
        )
        assert payload["command_count"] == 2
        assert payload["failed_count"] == 0
        for command_index, command_result in enumerate(payload["results"], start=1):
            expected_temp = (
                repo_dir.resolve()
                / ".tmp"
                / "vega-verification"
                / run_dir.name
                / f"iteration-{iteration}"
                / f"command-{command_index}"
            )
            assert expected_temp.is_dir()
            assert command_result["verification_temp"] == expected_temp.relative_to(
                repo_dir.resolve()
            ).as_posix()
            verification_temp_paths.add(command_result["verification_temp"])
    assert len(verification_temp_paths) == 4
    assert summary["finish_status"] == "ready_to_commit"
    assert summary["evidence_freshness"]["fresh"] is True
    assert summary["artifact_integrity"]["valid"] is True

    result_path = run_dir / "iterations" / "02" / "verification-result.json"
    original_result = json.loads(result_path.read_text(encoding="utf-8"))
    mutations = [
        (
            ("results", 0, "configured_command"),
            "tampered configured command",
            "iteration_02_verification_configured_command_binding_mismatch",
        ),
        (
            ("results", 0, "executed_command"),
            "tampered executed command",
            "iteration_02_verification_executed_command_binding_mismatch",
        ),
        (
            ("results", 0, "verification_temp"),
            ".tmp/vega-verification/other-run/iteration-2/command-1",
            "iteration_02_verification_temp_path_mismatch",
        ),
        (
            ("iteration",),
            1,
            "iteration_02_verification_iteration_binding_mismatch",
        ),
    ]
    for path_parts, value, expected_issue in mutations:
        tampered = json.loads(json.dumps(original_result))
        target = tampered
        for part in path_parts[:-1]:
            target = target[part]
        target[path_parts[-1]] = value
        result_path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        integrity = validate_loop_artifact_integrity(
            tmp_path,
            repo_dir,
            run_dir,
        )
        assert not integrity.valid
        assert expected_issue in integrity.issues

    tampered = json.loads(json.dumps(original_result))
    del tampered["artifact_version"]
    result_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity = validate_loop_artifact_integrity(
        tmp_path,
        repo_dir,
        run_dir,
    )
    assert not integrity.valid
    assert "iteration_02_verification_artifact_version_invalid" in integrity.issues

    result_path.write_text(
        json.dumps(original_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    execution_path = (
        run_dir
        / "iterations"
        / "02"
        / "executions"
        / "verification-01"
        / "execution.json"
    )
    original_execution = json.loads(execution_path.read_text(encoding="utf-8"))
    tampered_execution = dict(original_execution)
    tampered_execution["run_id"] = "other-run"
    execution_path.write_text(
        json.dumps(tampered_execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity = validate_loop_artifact_integrity(tmp_path, repo_dir, run_dir)
    assert not integrity.valid
    assert "iteration_02_verification_execution_run_id_mismatch" in integrity.issues

    tampered_execution = dict(original_execution)
    tampered_execution["command"] = [
        f"{original_execution['command'][0]} & echo forged"
    ]
    execution_path.write_text(
        json.dumps(tampered_execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity = validate_loop_artifact_integrity(tmp_path, repo_dir, run_dir)
    assert not integrity.valid
    assert "iteration_02_verification_execution_command_mismatch" in integrity.issues

    tampered_execution = dict(original_execution)
    tampered_execution["command"] = [
        f"echo forged & {original_execution['command'][0]}"
    ]
    execution_path.write_text(
        json.dumps(tampered_execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity = validate_loop_artifact_integrity(tmp_path, repo_dir, run_dir)
    assert not integrity.valid
    assert "iteration_02_verification_execution_command_mismatch" in integrity.issues

    tampered_execution = dict(original_execution)
    tampered_execution["status"] = "failed"
    execution_path.write_text(
        json.dumps(tampered_execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity = validate_loop_artifact_integrity(tmp_path, repo_dir, run_dir)
    assert not integrity.valid
    assert "iteration_02_verification_execution_status_mismatch" in integrity.issues

    execution_path.write_text(
        json.dumps(original_execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    restored = validate_loop_artifact_integrity(tmp_path, repo_dir, run_dir)
    assert restored.valid, restored.issues


def test_loop_continue_can_resume_auto_loop_after_manual_fix(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    _write_isolated_verification_config(repo_dir)
    worker = TrackedChangeRunner(["worker done"])
    reviewer = StaticRunner([_review_json("request_changes"), _review_json("approve")])
    runtime = LoopAutomationRuntime(tmp_path, worker_runner=worker, reviewer_runner=reviewer)
    brief_input = BriefInput(
        mode="feature",
        text="新增 README 功能说明",
        source="inline-text",
        repo_path=str(repo_dir),
    )
    run_dir = runtime.start(brief_input, "auto", max_iterations=1, verify=True)

    resumed = runtime.continue_assist(run_dir.name, repo_dir, verify=True)

    state = json.loads(resumed.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert len(state["iterations"]) == 2
    assert state["iterations"][1]["worker_status"] == "skipped"
    assert state["iterations"][1]["verdict"] == "approve"
    payload = run_status_payload(tmp_path, run_dir.name)
    artifacts = "\n".join(payload["key_artifacts"])
    assert "iterations\\01\\fix-prompt.md" not in artifacts
    assert "iterations/01/fix-prompt.md" not in artifacts
    assert "iterations\\02\\review-findings.md" in artifacts or "iterations/02/review-findings.md" in artifacts


def test_recover_marks_running_loop_as_needs_human(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="修复 README 展示问题",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running"
    state["current_step"] = "worker"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    recovered = RecoveryRuntime(tmp_path).recover_loop(run_dir.name, "测试模拟 worker 中断")

    recovered_state = json.loads(recovered.joinpath("state.json").read_text(encoding="utf-8"))
    assert recovered_state["status"] == "needs_human"
    assert recovered_state["current_step"] == "recovered"
    assert recovered.joinpath("recovery-report.md").exists()

    monkeypatch.chdir(tmp_path)
    cli_result = CliRunner().invoke(
        app,
        ["recover", "--run", run_dir.name, "--reason", "再次 recover 应失败"],
    )
    assert cli_result.exit_code != 0
    assert "只能 recover status=running" in cli_result.output


def test_owned_process_updates_heartbeat_and_times_out(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "timeout-loop"
    run_dir.mkdir(parents=True)
    context = RunnerExecutionContext(
        execution_root=run_dir,
        execution_dir=run_dir / "executions" / "worker",
        run_id=run_dir.name,
        step="worker",
        iteration=1,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )

    result = run_owned_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        "",
        tmp_path,
        1,
        context,
    )

    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "timed_out"
    assert lease.status == "timed_out"
    assert lease.child_pid is not None
    assert datetime.fromisoformat(lease.last_heartbeat) > datetime.fromisoformat(lease.started_at)
    assert context.execution_dir.joinpath("process-output.txt").exists()


def test_owned_process_success_persists_output_and_completed_lease(tmp_path) -> None:
    context = RunnerExecutionContext(
        execution_root=tmp_path,
        execution_dir=tmp_path / "runs" / "success-loop" / "executions" / "reviewer",
        run_id="success-loop",
        step="reviewer",
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
    )

    result = run_owned_process(
        [sys.executable, "-c", "print('owned process ok')"],
        "",
        tmp_path,
        5,
        context,
    )

    lease = ExecutionLease.model_validate_json(
        context.execution_dir.joinpath("execution.json").read_text(encoding="utf-8")
    )
    assert result.status == "success"
    assert "owned process ok" in result.output
    assert lease.status == "completed"
    assert lease.returncode == 0


def test_stop_cli_only_stops_recorded_owned_process(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "runs" / "stop-loop"
    run_dir.mkdir(parents=True)
    run_dir.joinpath("trace.jsonl").touch()
    context = RunnerExecutionContext(
        execution_root=run_dir,
        execution_dir=run_dir / "executions" / "worker",
        run_id=run_dir.name,
        step="worker",
        iteration=1,
        heartbeat_interval_seconds=0.05,
        lease_timeout_seconds=0.5,
        terminate_grace_seconds=0.2,
    )
    holder: dict[str, object] = {}
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def run_controlled_process() -> None:
        holder["result"] = run_owned_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            "",
            tmp_path,
            20,
            context,
        )

    thread = threading.Thread(target=run_controlled_process)
    try:
        with RunMutationLock.acquire(run_dir, "loop.start"):
            thread.start()
            _wait_for_execution_child(context.execution_dir / "execution.json")
            active_payload = run_status_payload(tmp_path, run_dir.name)
            assert active_payload["execution"]["status"] == "running"
            assert active_payload["execution"]["child_pid"] != unrelated.pid
            monkeypatch.chdir(tmp_path)
            cli_result = CliRunner().invoke(
                app,
                ["stop", "--run", run_dir.name, "--reason", "测试请求停止"],
            )
            thread.join(timeout=10)

            assert cli_result.exit_code == 0, cli_result.output
            assert not thread.is_alive()
            result = holder["result"]
            assert getattr(result, "status") == "stopped"
            assert unrelated.poll() is None
            lease = ExecutionLease.model_validate_json(
                context.execution_dir.joinpath("execution.json").read_text(
                    encoding="utf-8"
                )
            )
            assert lease.status == "stopped"
            stopped_payload = run_status_payload(tmp_path, run_dir.name)
            assert stopped_payload["execution"]["status"] == "stopped"
            assert context.execution_dir.joinpath("stop-request.json").exists()
            assert run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8") == ""
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)
        if thread.is_alive():
            request_stop_for_run(run_dir, "测试清理")
            thread.join(timeout=5)


def test_recover_rejects_live_execution_even_when_lease_expires(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(tmp_path)
    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="模拟 fresh execution recover 保护",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "assist",
    )
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running"
    state["current_step"] = "worker"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    now = datetime.now(UTC)
    execution_path = run_dir / "executions" / "worker" / "execution.json"
    execution_path.parent.mkdir(parents=True)
    lease = ExecutionLease(
        run_id=run_dir.name,
        step="worker",
        iteration=1,
        owner_pid=os.getpid(),
        child_pid=os.getpid(),
        command=["test"],
        started_at=now.isoformat(),
        last_heartbeat=now.isoformat(),
        lease_expires_at=(now + timedelta(minutes=1)).isoformat(),
        deadline=(now + timedelta(minutes=2)).isoformat(),
        status="running",
    )
    execution_path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="PID 仍存活"):
        RecoveryRuntime(tmp_path).recover_loop(run_dir.name, "不应接管 fresh execution")

    lease.lease_expires_at = (now - timedelta(seconds=1)).isoformat()
    execution_path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="PID 仍存活"):
        RecoveryRuntime(tmp_path).recover_loop(run_dir.name, "lease 过期但 PID 仍存活")

    lease.owner_pid = 999999
    lease.child_pid = 999998
    execution_path.write_text(lease.model_dump_json(indent=2), encoding="utf-8")
    recovered = RecoveryRuntime(tmp_path).recover_loop(run_dir.name, "lease 已过期")

    recovered_state = json.loads(recovered.joinpath("state.json").read_text(encoding="utf-8"))
    assert recovered_state["status"] == "needs_human"
    report = recovered.joinpath("recovery-report.md").read_text(encoding="utf-8")
    assert "heartbeat lease 已过期" in report
    assert "未终止进程" in report


@pytest.mark.parametrize("worker_status", ["timed_out", "stopped"])
def test_loop_interruption_skips_verification_and_review(tmp_path, worker_status) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    worker = StatusRunner(worker_status)
    reviewer = StaticRunner([_review_json("approve")])
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=worker,
        reviewer_runner=reviewer,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="模拟 worker 中断",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    report_name = "timeout-report.md" if worker_status == "timed_out" else "stop-report.md"
    iteration_dir = run_dir / "iterations" / "01"
    assert state["status"] == "needs_human"
    assert state["current_step"] == worker_status
    assert state["iterations"][0]["worker_status"] == worker_status
    assert reviewer.calls == []
    assert iteration_dir.joinpath(report_name).exists()
    assert not iteration_dir.joinpath("verification-summary.md").exists()
    assert not iteration_dir.joinpath("review-verdict.json").exists()


def test_worker_runner_error_preserves_partial_work_for_human_handoff(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    reviewer = StaticRunner([_review_json("approve")])
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=WritingErrorRunner(),
        reviewer_runner=reviewer,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="bug",
            text="模拟 provider 在修改后异常退出",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration_dir = run_dir / "iterations" / "01"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "worker_error"
    assert state["iterations"][0]["worker_status"] == "failed"
    assert reviewer.calls == []
    assert iteration_dir.joinpath("runner-error-report.md").exists()
    report = iteration_dir.joinpath("runner-error-report.md").read_text(encoding="utf-8")
    assert "部分完成" in report
    assert "M README.md" in report
    assert not iteration_dir.joinpath("verification-summary.md").exists()
    payload = run_status_payload(tmp_path, run_dir.name)
    assert any("部分完成" in item for item in payload["next_steps"])


def test_reviewer_timeout_becomes_needs_human_with_report(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    reviewer = StatusRunner("timed_out")
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=reviewer,
    )

    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="模拟 reviewer 超时",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    iteration = state["iterations"][0]
    assert state["status"] == "needs_human"
    assert state["current_step"] == "timed_out"
    assert iteration["reviewer_status"] == "timed_out"
    assert run_dir.joinpath("iterations", "01", "timeout-report.md").exists()
    assert "reviewer 单次执行超时" in run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    )


def test_reviewer_runner_error_becomes_needs_human_with_report(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    runtime = LoopAutomationRuntime(
        tmp_path,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StatusRunner("error"),
    )

    run_dir = runtime.start(
        BriefInput(
            mode="feature",
            text="模拟 reviewer provider 错误",
            source="inline-text",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=False,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "reviewer_error"
    assert state["iterations"][0]["reviewer_status"] == "error"
    assert run_dir.joinpath("iterations", "01", "runner-error-report.md").exists()
    assert "未产生可信审查结论" in run_dir.joinpath("final-report.md").read_text(
        encoding="utf-8"
    )


def _wait_for_execution_child(path: Path, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            try:
                lease = ExecutionLease.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.02)
                continue
            if lease.child_pid is not None and lease.status == "running":
                return
        time.sleep(0.02)
    raise AssertionError(f"等待 execution child 启动超时：{path}")


def _make_brief_run(tmp_path: Path, repo_dir: Path) -> Path:
    from vega.brief_runtime import BriefRuntime

    brief_input = BriefInput(
        mode="bug",
        text="修复 README 展示问题",
        source="inline-text",
        repo_path=str(repo_dir),
    )
    return BriefRuntime(tmp_path).run(brief_input)


def _review_json(verdict: str) -> str:
    findings = []
    if verdict == "request_changes":
        findings = [
            {
                "severity": "major",
                "file": "README.md",
                "line": 1,
                "title": "README 缺少验证说明",
                "evidence": "diff 未体现测试或验证结果。",
                "recommendation": "补充验证说明或测试日志。",
            }
        ]
    return json.dumps(
        {
            "verdict": verdict,
            "summary": "测试 reviewer 结论",
            "findings": findings,
            "reviewed_files": ["README.md"],
            "checked_items": ["需求覆盖", "测试覆盖"],
        },
        ensure_ascii=False,
    )
