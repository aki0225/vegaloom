from pathlib import Path

from typer.testing import CliRunner

import vega.cli as cli_module
from vega.cli import app
from vega.cli_support import make_loop_runtime, report_execution_progress


def test_execution_progress_is_stderr_only_and_uses_safe_step_labels(capsys) -> None:
    report_execution_progress("worker", 25)
    report_execution_progress("sk-test-secret", 50)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[vega] worker 运行中，已用时 25 秒" in captured.err
    assert "[vega] runner 运行中，已用时 50 秒" in captured.err
    assert "sk-test-secret" not in captured.err


def test_loop_cli_injects_progress_reporter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    captured: dict[str, object] = {}

    class FakeLoopRuntime:
        def start(self, *args, **kwargs) -> Path:
            report_execution_progress("worker", 25)
            return tmp_path / "runs" / "fake-loop"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "make_loop_runtime",
        lambda workspace: captured.update(workspace=workspace) or FakeLoopRuntime(),
    )
    monkeypatch.setattr(cli_module, "_ensure_git_ready", lambda _: None)
    monkeypatch.setattr(cli_module, "echo_run_status", lambda _: None)
    monkeypatch.setattr(cli_module, "exit_for_loop_result", lambda *args, **kwargs: None)

    result = CliRunner().invoke(
        app,
        [
            "loop",
            "bug",
            "--repo",
            str(repo),
            "--text",
            "验证进度输出",
            "--mode",
            "assist",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["workspace"] == tmp_path
    assert "[vega] worker 运行中" not in result.stdout
    assert "[vega] worker 运行中，已用时 25 秒" in result.stderr


def test_loop_runtime_factory_uses_cli_progress_reporter(tmp_path: Path) -> None:
    runtime = make_loop_runtime(tmp_path)

    assert runtime.progress_reporter is report_execution_progress
