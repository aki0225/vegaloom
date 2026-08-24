

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from vega import git_read as git_read_module
from vega.cli import app
from vega.experimental.inspection import eval as eval_runner
from vega.experimental.inspection.context_loader import load_target_context, parse_target_files
from vega.experimental.inspection.llm_client import LLMClient
from vega.experimental.inspection.loop_spec import (
    list_loop_specs,
    load_loop_spec,
    load_loop_spec_file,
)
from vega.experimental.inspection.runtime import EngineeringChangeRuntime
from vega.experimental.inspection.tool_broker import ToolBroker
from vega.models import (
    RunState,
    ToolResult,
)
from vega.tools import git_tools

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
