import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

import pytest
import vega
from typer.testing import CliRunner

from vega import git_read as git_read_module
from vega.experimental.inspection import eval as eval_runner
from vega.cli import app
from vega.experimental.inspection.context_loader import load_target_context, parse_target_files
from vega.execution_control import (
    ExecutionLease,
    RunnerExecutionContext,
    request_stop_for_run,
    run_owned_process,
)
from vega.finish_runtime import FinishRuntime
from vega.experimental.goal_runtime import GoalRuntime
from vega.experimental.inspection.llm_client import LLMClient
from vega.experimental.inspection.loop_spec import (
    list_loop_specs,
    load_loop_spec,
    load_loop_spec_file,
)
from vega.loop_runtime import LoopAutomationRuntime
from vega.experimental.memory import MemoryLedgerStore
from vega.models import BriefInput, MemoryProposal, RunState, ToolResult
from vega.project_config import CodexExecOptions, check_project_config, load_project_config
from vega.project_profile import build_project_profile
from vega.reflect_runtime import ReflectRuntime
from vega.recovery_runtime import RecoveryRuntime
from vega.review_runtime import ReviewPackRuntime, ReviewRuntime, parse_review_verdict
from vega.repository_identity import repository_scope
from vega.runner import CodexExecRunner, RunnerResult
from vega.run_status import run_status_payload
from vega.run_lock import RunMutationLock
from vega.experimental.inspection.runtime import EngineeringChangeRuntime
from vega.run_utils import create_run_dir
from vega.experimental.inspection.tool_broker import ToolBroker
from vega.loop_evidence import validate_loop_artifact_integrity
from vega.tools import git_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


def _strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE_PATTERN.sub("", value)


def test_project_skeleton_exists() -> None:
    assert PROJECT_ROOT.joinpath("docs", "PRODUCT-CONTRACT.md").exists()
    assert PROJECT_ROOT.joinpath("docs", "MVP-SCOPE.md").exists()
    assert PROJECT_ROOT.joinpath("docs", "ARCHITECTURE.md").exists()
    assert PROJECT_ROOT.joinpath("loops", "engineering-change.loop.yaml").exists()
    assert PROJECT_ROOT.joinpath("examples", "tasks", "check-atg-mcp-docs.md").exists()


def test_python_package_public_api_is_version_only() -> None:
    assert vega.__all__ == ["__version__"]
    assert importlib.util.find_spec("vega.assurance") is None
    assert importlib.util.find_spec("vega.experimental.assurance") is not None


def test_packaged_baseline_loop_matches_workspace_mirror() -> None:
    workspace_text = PROJECT_ROOT.joinpath(
        "loops",
        "engineering-change.loop.yaml",
    ).read_text(encoding="utf-8")
    packaged_text = files("vega").joinpath(
        "resources",
        "loops",
        "engineering-change.loop.yaml",
    ).read_text(encoding="utf-8")

    assert packaged_text == workspace_text


def test_packaged_baseline_loop_is_available_without_workspace_config(tmp_path: Path) -> None:
    specs = list_loop_specs(tmp_path)

    assert [spec.name for spec in specs] == ["engineering-change"]
    assert load_loop_spec(tmp_path, "engineering-change").name == "engineering-change"


def test_list_loops_cli_uses_packaged_baseline_in_empty_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["list-loops"])

    assert result.exit_code == 0
    assert "engineering-change" in result.output
    assert "未找到 loop 配置" not in result.output


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


def _copy_loop_spec(tmp_path: Path) -> None:
    loop_dir = tmp_path / "loops"
    loop_dir.mkdir(exist_ok=True)
    shutil.copyfile(
        PROJECT_ROOT / "loops" / "engineering-change.loop.yaml",
        loop_dir / "engineering-change.loop.yaml",
    )


def test_workspace_loop_overrides_packaged_baseline(tmp_path: Path) -> None:
    _copy_loop_spec(tmp_path)
    loop_path = tmp_path / "loops" / "engineering-change.loop.yaml"
    loop_path.write_text(
        loop_path.read_text(encoding="utf-8").replace(
            "本地研发变更审查 loop：读取任务和仓库，受控收集上下文，生成报告、复核和评估。",
            "workspace override",
        ),
        encoding="utf-8",
    )

    spec = load_loop_spec(tmp_path, "engineering-change")

    assert spec.description == "workspace override"


def _write_task_and_repo(tmp_path: Path) -> tuple[Path, Path]:
    task_file = tmp_path / "task.md"
    task_file.write_text(
        "\n".join(
            [
                "# 检查文档一致性",
                "",
                "目标文件：",
                "- `README.md`",
                "",
                "问题：",
                "1. README 是否说明 /mcp 用法？",
                "",
                "输出：",
                "- 计划",
                "- 报告",
            ]
        ),
        encoding="utf-8",
    )
    repo_dir = tmp_path / "target-repo"
    _init_changed_git_repo(repo_dir)
    repo_dir.joinpath("README.md").write_text(
        "# 文档\n使用 /mcp 接入。\n",
        encoding="utf-8",
        newline="\n",
    )
    return task_file, repo_dir


def _write_goal_file(tmp_path: Path) -> Path:
    goal_file = tmp_path / "goal.md"
    goal_file.write_text(
        "\n".join(
            [
                "# Goal",
                "",
                "Objective: 分阶段收口 Vega goal 状态层。",
                "",
                "Non-goals:",
                "- 不调用 worker",
                "- 不自动改代码",
                "- 不自动 commit",
                "",
                "Success conditions:",
                "- pytest 通过",
                "- ruff 通过",
            ]
        ),
        encoding="utf-8",
    )
    return goal_file


def _valid_report(required_sections: list[str]) -> str:
    lines = ["# 工程变更报告", ""]
    for section in required_sections:
        lines.extend([f"## {section}", "", "- README 是否说明 /mcp 用法？已基于工具结果检查。", ""])
    return "\n".join(lines)


def _write_minimal_run_dir(tmp_path: Path) -> tuple[Path, object]:
    spec = load_loop_spec(PROJECT_ROOT, "engineering-change")
    state = RunState(
        run_id="run-test",
        loop_name=spec.name,
        status="running",
        repo_path=str(tmp_path),
        task_file=str(tmp_path / "task.md"),
        tool_results=[ToolResult(tool="file.search", output={"query": "TODO", "matches": []})],
        review_results=["PASS: reviewer pass 无失败项"],
    )
    state.save(tmp_path / "state.json")
    tmp_path.joinpath("trace.jsonl").write_text(
        "\n".join(json.dumps({"event": event}, ensure_ascii=False) for event in spec.eval.trace_events)
        + "\n",
        encoding="utf-8",
    )
    tmp_path.joinpath("plan.md").write_text("# 计划\n", encoding="utf-8")
    tmp_path.joinpath("report.md").write_text(_valid_report(spec.report.required_sections), encoding="utf-8")
    tmp_path.joinpath("review.md").write_text("# Review\n\n- PASS: reviewer pass 无失败项\n", encoding="utf-8")
    tmp_path.joinpath("eval.md").write_text("# Eval\n", encoding="utf-8")
    return tmp_path, spec


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


def _create_successful_loop_run(workspace: Path, repo_dir: Path) -> Path:
    repo_dir.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('goal verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    _commit_repo_paths(repo_dir, ".vega.yaml", message="add goal verification")
    runtime = LoopAutomationRuntime(
        workspace,
        worker_runner=TrackedChangeRunner(["worker done"]),
        reviewer_runner=StaticRunner([_review_json("approve")]),
    )
    return runtime.start(
        BriefInput(
            mode="feature",
            text="为 Goal checkpoint 生成可校验 loop 证据",
            source="test",
            repo_path=str(repo_dir),
        ),
        "auto",
        max_iterations=1,
        verify=True,
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


def test_loop_spec_loads_yaml_and_rejects_unknown_git_check(tmp_path) -> None:
    spec = load_loop_spec(PROJECT_ROOT, "engineering-change")
    assert spec.name == "engineering-change"
    assert "repo.run_check" in spec.tools.allowed
    assert spec.inspect.git_checks == ["git.status", "git.diff", "git.diff_check"]

    bad_spec = tmp_path / "bad.loop.yaml"
    bad_spec.write_text(
        """
name: bad
tools:
  allowed: [file.search]
inspect:
  git_checks: [shell.run]
""",
        encoding="utf-8",
    )
    try:
        load_loop_spec_file(bad_spec)
    except ValueError as exc:
        assert "未授权 git check" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("非法 git check 必须被拒绝")


def test_parse_target_files_from_task_markdown() -> None:
    task_text = """
# Task

Target files to search:

- `README.md`
- `docs/ai-client-integration.md`
- `examples/client-configs/`

Other section:
- `ignored.md`
"""

    assert parse_target_files(task_text) == [
        "README.md",
        "docs/ai-client-integration.md",
        "examples/client-configs/",
    ]


def test_parse_target_files_from_chinese_section() -> None:
    task_text = """
# 任务

目标文件：
- `README.md`
- `docs/ARCHITECTURE.md`

输出：
- 报告
"""

    assert parse_target_files(task_text) == ["README.md", "docs/ARCHITECTURE.md"]


def test_load_target_context_reads_files_and_directory(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo_dir.joinpath("README.md").write_text("# Readme\nUse /mcp and /mcp/sse.\n", encoding="utf-8")
    config_dir = repo_dir / "examples" / "client-configs"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("codex.json").write_text('{"url": "/mcp"}\n', encoding="utf-8")

    results = load_target_context(repo_dir, ["README.md", "examples/client-configs/"])

    assert [result.status for result in results] == ["ok", "ok"]
    assert results[0].output["kind"] == "file"
    assert "/mcp/sse" in results[0].output["content"]
    assert results[1].output["kind"] == "directory"
    assert results[1].output["files"] == ["examples/client-configs/codex.json"]


def test_load_target_context_rejects_repo_path_escape(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    tmp_path.joinpath("secret.md").write_text("outside repo\n", encoding="utf-8")

    result = load_target_context(repo_dir, ["../secret.md"])[0]

    assert result.status == "error"
    assert "路径越过仓库边界" in result.error


def test_report_eval_checks_required_sections_and_content(tmp_path) -> None:
    run_dir, spec = _write_minimal_run_dir(tmp_path)

    results = eval_runner.run_basic_eval(run_dir, spec)

    assert "PASS: report.md 包含必需章节" in results
    assert "PASS: trace.jsonl 包含必需事件" in results
    assert "PASS: 工具调用符合 allowlist" in results
    assert "PASS: reviewer pass 无失败项" in results
    assert "PASS: 未自动生成 Memory Proposal" in results


def test_report_eval_fails_missing_required_content(tmp_path) -> None:
    run_dir, spec = _write_minimal_run_dir(tmp_path)
    run_dir.joinpath("report.md").write_text("# 工程变更报告\n", encoding="utf-8")

    results = eval_runner.run_basic_eval(run_dir, spec)

    assert any(result.startswith("FAIL: report.md 缺少章节") for result in results)


def test_llm_response_strips_wrapping_markdown_code_fence() -> None:
    text = LLMClient._extract_chat_content(
        {"choices": [{"message": {"content": "```markdown\n# 计划\n\n- 使用 /mcp。\n```"}}]}
    )

    assert text == "# 计划\n\n- 使用 /mcp。\n"
    assert "```" not in text


def test_llm_client_from_env_uses_safe_defaults(monkeypatch) -> None:
    _clear_vega_env(monkeypatch)

    client = LLMClient.from_env()

    assert not client.available()
    assert client.model == "gpt-5.5"
    assert client.reasoning_effort == "xhigh"
    assert client.provider_alias == "ciii"
    assert client.timeout_seconds == 180.0


def test_cli_run_creates_core_artifacts_without_memory_proposal(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    _copy_loop_spec(tmp_path)
    task_file, repo_dir = _write_task_and_repo(tmp_path)
    tmp_path.joinpath("memory").mkdir()

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["run", "engineering-change", "--task", str(task_file), "--repo", str(repo_dir)],
    )

    assert result.exit_code == 1, result.output
    assert "运行失败" in result.output
    run_dirs = list(tmp_path.joinpath("runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for artifact in [
        "state.json",
        "trace.jsonl",
        "plan.md",
        "report.md",
        "review.md",
        "eval.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["memory_proposals"] == []
    assert not run_dir.joinpath("memory-proposals.jsonl").exists()
    assert "PASS: artifact 存在：eval.md" in run_dir.joinpath("eval.md").read_text(encoding="utf-8")
    assert not tmp_path.joinpath("memory", "ledger.jsonl").exists()


def test_runtime_uses_mock_llm_for_plan_and_report(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    task_file, repo_dir = _write_task_and_repo(tmp_path)
    spec = load_loop_spec(PROJECT_ROOT, "engineering-change")

    class MockLLMClient:
        model = "gpt-5.5"
        provider_alias = "ciii"

        def available(self) -> bool:
            return True

        def generate_plan(self, task_text: str) -> str:
            assert "README 是否说明" in task_text
            return "# Mock Plan\n\nUse model plan.\n"

        def generate_report(
            self,
            task_text: str,
            tool_results: list[object],
            required_sections: list[str] | None = None,
        ) -> str:
            assert "README 是否说明" in task_text
            assert tool_results
            assert required_sections
            return _valid_report(required_sections)

    run_dir = EngineeringChangeRuntime(
        tmp_path,
        loop_spec=spec,
        llm_client=MockLLMClient(),
    ).run(task_file, repo_dir)

    assert run_dir.joinpath("plan.md").read_text(encoding="utf-8") == "# Mock Plan\n\nUse model plan.\n"
    assert run_dir.joinpath("report.md").read_text(encoding="utf-8") == _valid_report(
        spec.report.required_sections
    )
    trace_text = run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8")
    assert '"llm_available": true' in trace_text
    assert '"provider_alias": "ciii"' in trace_text


def test_eval_failure_marks_run_failed_after_artifacts_are_written(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    task_file, repo_dir = _write_task_and_repo(tmp_path)
    spec = load_loop_spec(PROJECT_ROOT, "engineering-change")

    class BadReportLLMClient:
        model = "gpt-5.5"
        provider_alias = "ciii"

        def available(self) -> bool:
            return True

        def generate_plan(self, task_text: str) -> str:
            return "# 计划\n\n- 检查文档。\n"

        def generate_report(
            self,
            task_text: str,
            tool_results: list[object],
            required_sections: list[str] | None = None,
        ) -> str:
            return "# 工程变更报告\n\n内容不完整。\n"

    run_dir = EngineeringChangeRuntime(tmp_path, loop_spec=spec, llm_client=BadReportLLMClient()).run(
        task_file,
        repo_dir,
    )

    for artifact in [
        "state.json",
        "trace.jsonl",
        "plan.md",
        "report.md",
        "review.md",
        "eval.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    assert not run_dir.joinpath("memory-proposals.jsonl").exists()

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert any(result.startswith("FAIL:") for result in state["eval_results"])
    assert "FAIL:" in run_dir.joinpath("eval.md").read_text(encoding="utf-8")
    assert '"run_finished", "status": "failed"' in run_dir.joinpath("trace.jsonl").read_text(
        encoding="utf-8"
    )


def test_required_git_check_failure_marks_engineering_change_run_failed(tmp_path, monkeypatch) -> None:
    _clear_vega_env(monkeypatch)
    task_file, repo_dir = _write_task_and_repo(tmp_path)

    def fake_run_git(repo_path: Path, check_id: str) -> tuple[int, str, str]:
        if check_id == "git.diff_check":
            return 1, "", "trailing whitespace"
        return 0, "", ""

    monkeypatch.setattr(git_tools, "run_git", fake_run_git)
    run_dir = EngineeringChangeRuntime(tmp_path).run(task_file, repo_dir)

    for artifact in ["state.json", "trace.jsonl", "plan.md", "report.md", "review.md", "eval.md"]:
        assert run_dir.joinpath(artifact).exists(), artifact
    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "FAIL: 必需 Git 检查失败：git.diff_check" in state["eval_results"]
    assert '"run_finished", "status": "failed"' in run_dir.joinpath("trace.jsonl").read_text(
        encoding="utf-8"
    )


def test_llm_exception_falls_back_and_marks_unanswered_run_failed(
    tmp_path,
    monkeypatch,
) -> None:
    _clear_vega_env(monkeypatch)
    task_file, repo_dir = _write_task_and_repo(tmp_path)
    spec = load_loop_spec(PROJECT_ROOT, "engineering-change")

    class FailingLLMClient:
        model = "gpt-5.5"
        provider_alias = "ciii"

        def available(self) -> bool:
            return True

        def generate_plan(self, task_text: str) -> str:
            raise RuntimeError("model unavailable")

        def generate_report(
            self,
            task_text: str,
            tool_results: list[object],
            required_sections: list[str] | None = None,
        ) -> str:
            raise RuntimeError("model unavailable")

    run_dir = EngineeringChangeRuntime(tmp_path, loop_spec=spec, llm_client=FailingLLMClient()).run(
        task_file,
        repo_dir,
    )

    state = json.loads(run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "按 loop YAML 配置读取目标文件" in run_dir.joinpath("plan.md").read_text(
        encoding="utf-8"
    )
    assert "deterministic fallback 整理审查证据" in run_dir.joinpath(
        "report.md"
    ).read_text(encoding="utf-8")
    trace_text = run_dir.joinpath("trace.jsonl").read_text(encoding="utf-8")
    assert "llm_plan_failed" in trace_text
    assert "llm_report_failed" in trace_text


def test_artifacts_do_not_contain_api_key_when_llm_falls_back(tmp_path, monkeypatch) -> None:
    _copy_loop_spec(tmp_path)
    task_file, repo_dir = _write_task_and_repo(tmp_path)
    monkeypatch.setenv("VEGA_API_KEY", "sk-test-secret")
    monkeypatch.setenv("VEGA_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("VEGA_MODEL", "gpt-5.5")
    monkeypatch.setenv("VEGA_REASONING_EFFORT", "xhigh")

    def fail_without_leaking_secret(*args, **kwargs):
        raise RuntimeError("simulated provider failure with sk-test-secret")

    monkeypatch.setattr(
        "vega.experimental.inspection.llm_client.LLMClient._chat",
        fail_without_leaking_secret,
    )

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["run", "engineering-change", "--task", str(task_file), "--repo", str(repo_dir)],
    )

    assert result.exit_code == 1, result.output
    assert "运行失败：status=failed" in result.output
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in next(tmp_path.joinpath("runs").iterdir()).iterdir()
        if path.is_file()
    )
    assert "sk-test-secret" not in artifact_text


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


def test_repo_run_check_allows_only_documented_checks(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)
    broker = ToolBroker(tmp_path)

    for check_id in ["git.status", "git.diff", "git.diff_check"]:
        result = broker.run_check(check_id)
        assert result.status == "ok"
        assert result.output["check_id"] == check_id

    rejected = broker.run_check("git.diff_name_only")
    assert rejected.status == "error"
    assert calls == [
        git_read_module.harden_git_read_command(
            git_tools.ALLOWED_CHECKS[check_id]
        )
        for check_id in ["git.status", "git.diff", "git.diff_check"]
    ]


def test_repo_run_check_rejects_non_allowlisted_check(tmp_path, monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("non-allowlisted checks must not execute subprocess.run")

    monkeypatch.setattr(git_read_module.subprocess, "run", fail_if_called)

    result = ToolBroker(tmp_path).run_check("shell.run")

    assert result.status == "error"
    assert "允许列表" in result.error



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

    def fake_run_owned_process(command, input_text, cwd, timeout_seconds, context):
        captured.update(
            {
                "command": command,
                "input_text": input_text,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "context": context,
            }
        )
        return SimpleNamespace(status="success", output="ok", error=None)

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
        "-",
    ]
    assert captured["input_text"] == "完成最小修改"


def test_codex_exec_runner_executes_raw_command_but_redacts_result_command(
    tmp_path,
    monkeypatch,
) -> None:
    fake_secret = "sk-test-secret-1234567890"
    repo_dir = tmp_path / f"repo-{fake_secret}"
    repo_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_run_owned_process(command, input_text, cwd, timeout_seconds, context):
        del input_text, cwd, timeout_seconds, context
        captured["command"] = command
        return SimpleNamespace(status="success", output="ok", error=None)

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

    verdict = json.loads(run_dir.joinpath("review-verdict.json").read_text(encoding="utf-8"))
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

    assert worker.calls[0]["execution_context"].progress_reporter is reporter
    assert reviewer.calls[0]["execution_context"].progress_reporter is reporter


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
    assert "## 项目上下文" in worker_prompt
    assert "测试必须说明结果" in worker_prompt
    assert "Vega 会在 worker 返回后独立执行" in worker_prompt
    assert "所有自检产生的缓存、临时文件和中间输出" in worker_prompt
    assert "不得使用仓库父目录、工作区集合根目录、兄弟仓库或盘符根目录" in worker_prompt
    assert "不要运行带 `{{vega_verification_temp}}` 的 harness-owned 命令" in worker_prompt
    assert "不共享 harness 临时目录的最小检查" in worker_prompt


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
    captured: dict[str, CodexExecOptions | None] = {}

    def make_worker_runner(name: str, options: CodexExecOptions | None = None):
        assert name == "codex-exec"
        captured["worker"] = options
        return TrackedChangeRunner(["worker done"])

    def make_reviewer_runner(name: str, options: CodexExecOptions | None = None):
        assert name == "codex-exec"
        captured["reviewer"] = options
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

    def make_reviewer_runner(name: str, options: CodexExecOptions | None = None):
        captured["name"] = name
        captured["options"] = options
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


def test_goal_start_creates_contract_state_trace_and_progress(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        ["goal", "start", "--repo", str(repo_dir), "--input", str(goal_file), "--scope", "refactor"],
    )

    assert result.exit_code == 0, result.output
    run_dir = next(path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-goal"))
    for artifact in [
        "state.json",
        "goal-state.json",
        "goal-trace.jsonl",
        "goal-contract.md",
        "goal-contract.json",
        "progress.md",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "created"
    assert state["scope_profile"] == "refactor"
    assert state["checkpoint_count"] == 0
    assert "不调用 worker" in run_dir.joinpath("goal-contract.md").read_text(encoding="utf-8")
    assert not tmp_path.joinpath("memory", "ledger.jsonl").exists()


def test_goal_pause_resume_stop_and_status_are_manual_state_transitions(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    run_dir = GoalRuntime(tmp_path).start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    monkeypatch.chdir(tmp_path)

    pause = CliRunner().invoke(
        app,
        ["goal", "pause", "--run", run_dir.name, "--reason", "等待人工确认"],
    )
    resume = CliRunner().invoke(app, ["goal", "resume", "--run", run_dir.name])
    stop = CliRunner().invoke(
        app,
        ["goal", "stop", "--run", run_dir.name, "--reason", "方向变化"],
    )
    status = CliRunner().invoke(app, ["goal", "status", "--run", run_dir.name])

    assert pause.exit_code == 0, pause.output
    assert resume.exit_code == 0, resume.output
    assert stop.exit_code == 0, stop.output
    assert status.exit_code == 0, status.output
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    assert state["current_step"] == "stopped"
    assert "stop-report.md" in state["artifacts"]
    assert run_dir.joinpath("stop-report.md").exists()
    assert "类型：`goal`" in status.output


def test_goal_step_only_writes_checkpoint_plan_without_worker_or_repo_changes(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    before_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    goal_file = _write_goal_file(tmp_path)
    run_dir = GoalRuntime(tmp_path).start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)

    stepped = GoalRuntime(tmp_path).step(run_dir.name)

    assert stepped == run_dir
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "running"
    assert state["current_step"] == "checkpoint_planned"
    assert state["checkpoint_count"] == 1
    checkpoint_plan = run_dir / "checkpoints" / "01" / "checkpoint-plan.md"
    assert checkpoint_plan.exists()
    assert not run_dir.joinpath("worker-prompt.md").exists()
    assert not run_dir.joinpath("loop-run.txt").exists()
    after_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after_status == before_status


def test_goal_attach_records_checkpoint_evidence_without_child_execution(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    reflect_run = ReflectRuntime(tmp_path).run(repo_dir, note="人工复盘已完成")

    attached = runtime.attach(
        run_dir.name,
        checkpoint="01",
        child_run=reflect_run.name,
        evidence_type="reflect",
        note="人工复盘已完成",
    )

    state = json.loads(attached.joinpath("goal-state.json").read_text(encoding="utf-8"))
    checkpoint = state["checkpoint_records"][0]
    assert checkpoint["checkpoint"] == "01"
    assert checkpoint["refs"][0]["run"] == reflect_run.name
    assert checkpoint["refs"][0]["type"] == "reflect"
    assert checkpoint["refs"][0]["note"] == "人工复盘已完成"
    assert checkpoint["refs"][0]["validated"] is True
    assert checkpoint["refs"][0]["completion_eligible"] is False
    assert attached.joinpath("checkpoints", "01", "checkpoint-evidence.json").exists()
    assert not attached.joinpath("worker-prompt.md").exists()


def test_goal_finish_evidence_requires_ready_to_commit_summary(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    FinishRuntime(tmp_path).run(child_loop.name)

    runtime.attach(run_dir.name, "01", child_loop.name, "finish", "finish 已生成")

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["kind"] == "loop"
    assert evidence["completion_eligible"] is True
    assert "ready_to_commit" in evidence["validation_summary"]


def test_goal_finish_evidence_rejects_forged_valid_summary_after_artifact_tamper(
    tmp_path,
) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    FinishRuntime(tmp_path).run(child_loop.name)

    child_loop.joinpath("iterations", "01", "review-verdict.json").unlink()
    summary_path = child_loop / "finish-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["finish_status"] = "ready_to_commit"
    summary["artifact_integrity"]["valid"] = True
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    runtime.attach(run_dir.name, "01", child_loop.name, "finish", "finish 已生成")

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    evidence = state["checkpoint_records"][0]["refs"][0]
    assert evidence["completion_eligible"] is False
    assert "finish_artifact_integrity_mismatch" in evidence["validation_summary"]
    assert "finish_status_mismatch" in evidence["validation_summary"]


def test_goal_checkpoint_done_writes_report_and_updates_status(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    runtime.attach(run_dir.name, "01", child_loop.name, "loop", "loop 已通过")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "goal",
            "checkpoint-done",
            "--run",
            run_dir.name,
            "--checkpoint",
            "01",
            "--note",
            "checkpoint 01 已完成",
        ],
    )

    assert result.exit_code == 0, result.output
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "checkpoint_done"
    assert state["current_step"] == "checkpoint_done"
    checkpoint = state["checkpoint_records"][0]
    assert checkpoint["status"] == "done"
    assert checkpoint["completion_mode"] == "validated"
    assert checkpoint["completed_note"] == "checkpoint 01 已完成"
    report = run_dir / "checkpoints" / "01" / "checkpoint-report.md"
    assert report.exists()
    assert child_loop.name in report.read_text(encoding="utf-8")
    payload = run_status_payload(tmp_path, run_dir.name)
    assert any("goal step" in item for item in payload["next_steps"])
    assert any("checkpoint-report.md" in item for item in payload["key_artifacts"])


def test_goal_rejects_parallel_checkpoint_and_missing_evidence(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    try:
        runtime.step(run_dir.name)
    except ValueError as exc:
        assert "尚未完成" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("同一时间只能存在一个 active checkpoint")

    try:
        runtime.checkpoint_done(run_dir.name, "01", note="没有证据")
    except ValueError as exc:
        assert "没有挂载任何证据" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("没有证据的 checkpoint 不能完成")


def test_goal_attach_validates_child_run_repo_and_kind(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    other_repo = tmp_path / "other-repo"
    _init_changed_git_repo(repo_dir)
    _init_changed_git_repo(other_repo)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    try:
        runtime.attach(run_dir.name, "01", "missing-run", "loop")
    except FileNotFoundError as exc:
        assert "run 不存在" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("不存在的 child run 不能作为证据")

    reflect_run = ReflectRuntime(tmp_path).run(other_repo, note="其他仓库证据")
    try:
        runtime.attach(run_dir.name, "01", reflect_run.name, "reflect")
    except ValueError as exc:
        assert "不属于同一仓库" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("其他仓库的 child run 不能挂载")

    same_repo_reflect = ReflectRuntime(tmp_path).run(repo_dir, note="同仓库复盘")
    try:
        runtime.attach(run_dir.name, "01", same_repo_reflect.name, "review")
    except ValueError as exc:
        assert "证据类型与 child run 不匹配" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("证据类型必须与 child run kind 一致")


def test_goal_manual_evidence_requires_explicit_override_and_becomes_immutable(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    evidence_file = repo_dir / "manual-check.md"
    evidence_file.write_text("# 人工验证\n\n- 已完成验收。\n", encoding="utf-8")
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    runtime.attach(
        run_dir.name,
        "01",
        str(evidence_file),
        "manual",
        "人工检查验收记录",
    )

    try:
        runtime.checkpoint_done(run_dir.name, "01", note="人工确认")
    except ValueError as exc:
        assert "--allow-manual-evidence" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("manual evidence 必须显式 override")

    runtime.checkpoint_done(
        run_dir.name,
        "01",
        note="人工确认 manual evidence 足以完成本阶段",
        allow_manual_evidence=True,
    )
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["checkpoint_records"][0]["completion_mode"] == "manual_override"

    try:
        runtime.attach(run_dir.name, "01", str(evidence_file), "manual", "重复证据")
    except ValueError as exc:
        assert "证据不可再修改" in str(exc) or "状态不允许 attach" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("完成后的 checkpoint 证据必须不可变")


def test_goal_manual_evidence_rejects_sensitive_path_without_persisting_it(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    secret = "sk-manual-secret-123456"
    sensitive_file = repo_dir / ".env"
    sensitive_file.write_text(f"API_KEY={secret}\n", encoding="utf-8")
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    with pytest.raises(ValueError, match="environment_file"):
        runtime.attach(
            run_dir.name,
            "01",
            str(sensitive_file),
            "manual",
            "人工检查敏感文件",
        )

    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*")
        if path.is_file()
    )
    assert str(sensitive_file) not in persisted
    assert secret not in persisted
    assert not run_dir.joinpath("checkpoints", "01", "checkpoint-evidence.json").exists()


def test_goal_complete_distinguishes_success_from_stop(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    runtime.attach(run_dir.name, "01", child_loop.name, "loop", "loop 已通过")
    runtime.checkpoint_done(run_dir.name, "01", note="阶段完成")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "goal",
            "complete",
            "--run",
            run_dir.name,
            "--note",
            "已核对 pytest、ruff 和 checkpoint 证据",
        ],
    )

    assert result.exit_code == 0, result.output
    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["current_step"] == "completed"
    assert run_dir.joinpath("goal-final-report.md").exists()
    assert run_dir.joinpath("goal-eval.md").exists()
    assert all(item.startswith("PASS:") for item in state["eval_results"])
    assert "Goal 已完成" in result.output


def test_goal_complete_revalidates_checkpoint_evidence(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_clean_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)
    child_loop = _create_successful_loop_run(tmp_path, repo_dir)
    runtime.attach(run_dir.name, "01", child_loop.name, "loop", "loop 已通过")
    runtime.checkpoint_done(run_dir.name, "01", note="阶段完成")
    child_state_path = child_loop / "state.json"
    child_state = json.loads(child_state_path.read_text(encoding="utf-8"))
    child_state["status"] = "needs_human"
    child_state_path.write_text(json.dumps(child_state, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="checkpoint 完成证据已失效"):
        runtime.complete(run_dir.name, "准备完成 goal")

    state = json.loads(run_dir.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "checkpoint_done"
    assert not run_dir.joinpath("goal-final-report.md").exists()


def test_goal_attach_cli_rejects_unknown_evidence_type(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    run_dir = GoalRuntime(tmp_path).start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    GoalRuntime(tmp_path).step(run_dir.name)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "goal",
            "attach",
            "--run",
            run_dir.name,
            "--checkpoint",
            "01",
            "--ref",
            "child-run",
            "--type",
            "worker",
        ],
    )

    assert result.exit_code != 0
    assert "--type 只能是" in _strip_ansi(result.output)


def test_goal_recover_turns_running_goal_into_needs_human(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    recovered = runtime.recover(run_dir.name, "CLI 中断")

    state = json.loads(recovered.joinpath("goal-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "needs_human"
    assert state["current_step"] == "recovered"
    assert recovered.joinpath("recovery-report.md").exists()
    assert "CLI 中断" in recovered.joinpath("recovery-report.md").read_text(encoding="utf-8")


def test_goal_cli_latest_and_status_recognize_goal_runs(tmp_path, monkeypatch) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    monkeypatch.chdir(tmp_path)

    started = CliRunner().invoke(
        app,
        ["goal", "start", "--repo", str(repo_dir), "--input", str(goal_file)],
    )
    assert started.exit_code == 0, started.output
    run_dir = next(path for path in tmp_path.joinpath("runs").iterdir() if path.name.endswith("-goal"))

    status_result = CliRunner().invoke(app, ["status", "--run", run_dir.name, "--json"])
    latest_result = CliRunner().invoke(app, ["latest", "--kind", "goal", "--json"])

    assert status_result.exit_code == 0, status_result.output
    assert latest_result.exit_code == 0, latest_result.output
    assert json.loads(status_result.output)["kind"] == "goal"
    assert json.loads(latest_result.output)["run_id"] == run_dir.name


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
    assert loop_skill.exists()
    assert review_skill.exists()
    assert legacy_skill.read_text(encoding="utf-8") == "legacy skill\n"
    assert ".agents" in result.output
    assert ".codex" not in result.output
    loop_skill_text = loop_skill.read_text(encoding="utf-8")
    assert "vega loop bug" in loop_skill_text
    assert "workspace-baseline.json" in loop_skill_text
    assert "不要执行 Worker，也不要 `loop continue`" in loop_skill_text
    assert "宿主原生子代理" in loop_skill_text
    assert "不把子代理完整聊天传给 Reviewer" in loop_skill_text
    assert "vega gate" in review_skill.read_text(encoding="utf-8")

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
    assert "commit 前 checklist" in run_dir.joinpath("finish-report.md").read_text(encoding="utf-8").lower()

    json_result = CliRunner().invoke(app, ["finish", "--run", run_dir.name, "--json"])
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.output)["finish_status"] == "ready_to_commit"


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


def test_goal_status_highlights_latest_checkpoint_plan(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    _init_changed_git_repo(repo_dir)
    goal_file = _write_goal_file(tmp_path)
    runtime = GoalRuntime(tmp_path)
    run_dir = runtime.start(repo_dir, goal_file.read_text(encoding="utf-8"), str(goal_file), None)
    runtime.step(run_dir.name)

    payload = run_status_payload(tmp_path, run_dir.name)
    artifacts = "\n".join(payload["key_artifacts"])

    assert payload["kind"] == "goal"
    assert "checkpoints\\01\\checkpoint-plan.md" in artifacts or (
        "checkpoints/01/checkpoint-plan.md" in artifacts
    )


def test_dogfood_eval_case_selection_contract() -> None:
    from scripts.dogfood_eval import _case_registry, select_case_names

    available_names = [name for name, _ in _case_registry()]

    assert select_case_names([], available_names) == available_names
    assert select_case_names(
        ["execution_control", "execution_control", "goal_p0_lifecycle"],
        available_names,
    ) == ["execution_control", "goal_p0_lifecycle"]
    with pytest.raises(ValueError, match="未知 dogfood case：unknown-case"):
        select_case_names(["unknown-case"], available_names)


@pytest.mark.parametrize(
    "case_name",
    [
        "core_loop_without_memory",
        "explicit_memory_lesson",
        "config_check_invalid_verification",
        "execution_control",
        "workspace_pollution_guard",
        "prompt_budget_guard",
        "large_scope_gate",
        "goal_p0_lifecycle",
    ],
)
def test_dogfood_eval_covers_core_loop_memory_boundary_and_goal_p0(
    tmp_path_factory: pytest.TempPathFactory,
    case_name: str,
) -> None:
    workspace = tmp_path_factory.mktemp("d")
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "dogfood_eval.py"),
            "--runner",
            "none",
            "--workspace",
            str(workspace),
            "--case",
            case_name,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "成功：1/1" in result.stdout
    summary_path = next(workspace.joinpath("runs").glob("dogfood-eval-*/summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["success_count"] == 1
    assert summary["case_count"] == 1
    assert [case["name"] for case in summary["cases"]] == [case_name]


def test_dogfood_eval_rejects_unknown_case_before_workspace_creation(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace = tmp_path_factory.mktemp("dogfood-invalid") / "workspace"
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "dogfood_eval.py"),
            "--runner",
            "none",
            "--workspace",
            str(workspace),
            "--case",
            "unknown-case",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "未知 dogfood case" in result.stderr
    assert not workspace.exists()


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
            "checked_items": ["需求覆盖", "测试覆盖"],
        },
        ensure_ascii=False,
    )
