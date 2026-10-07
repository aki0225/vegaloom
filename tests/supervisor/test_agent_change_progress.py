import json
import sys

import pytest
from typer.testing import CliRunner

from vega import agent_change_cli as cli
from vega import cli_support
from vega.agent_change_presentation import ChangeDriverResult
from vega.cli_entrypoint import app
from vega.execution_feedback import ExecutionProgressTicker
from vega.execution_control import RunnerExecutionContext, run_owned_process


@pytest.mark.parametrize("mode", [[], ["--json"], ["--json", "--progress"]])
@pytest.mark.parametrize("broken_stderr", [False, True])
def test_change_progress_streams_and_noninteractive_boundary(monkeypatch, tmp_path, mode,
                                                           broken_stderr):
    monkeypatch.setattr(cli, "resolve_repository_root", lambda _: tmp_path)
    original_echo = cli_support.typer.echo

    def echo(message=None, *args, **kwargs):
        if broken_stderr and kwargs.get("err"):
            raise OSError("受控 stderr 故障")
        return original_echo(message, *args, **kwargs)

    monkeypatch.setattr(cli_support.typer, "echo", echo)

    class Driver:
        def __init__(self, *args, **kwargs):
            self.options = kwargs

        def change(self, **kwargs):
            def forbidden_read(*args, **kwargs):
                pytest.fail("JSON 进度不能读取 stdin")

            monkeypatch.setattr(sys.stdin, "read", forbidden_read)
            monkeypatch.setattr(sys.stdin, "readline", forbidden_read)
            if "--json" in mode:
                assert self.options["interaction_reporter"] is None
                assert not self.options["interactive"]
                assert self.options["confirm"] is None
            event = self.options["event_reporter"]
            progress = self.options["progress_reporter"]
            if event:
                event('准备结束 api_key="synthetic-head-secret"\x1b[31m\r\n'
                      'token="synthetic-tail-secret"')
            if progress:
                for step in ("environment_prepare", "worker", "verification", "reviewer"):
                    ticker = ExecutionProgressTicker(step, progress, started_at=0)
                    ticker.started()
                    ticker.tick(25)
                progress("worker.waiting_user", 26)
                progress("reviewer.turn_completed", 27)
                progress("secret.prompt\x1b[31m", 28)
            return ChangeDriverResult(None, "attention_required", "human.required", "等待人工")

    monkeypatch.setattr(cli, "AgentChangeDriver", Driver)
    result = CliRunner().invoke(app, ["change", *mode])
    assert result.exit_code == 2, result.output
    if "--json" in mode:
        assert json.loads(result.stdout)["reason_code"] == "human.required"
    else:
        assert result.stdout.strip() == "等待人工"
    if mode == ["--json"] or broken_stderr:
        assert result.stderr == ""
    else:
        assert "环境准备" in result.stderr
        assert "已用时 25 秒" in result.stderr
        assert "等待人工响应" in result.stderr
        assert "完成模型回合" in result.stderr
        assert "synthetic-head-secret" not in result.stderr
        assert "synthetic-tail-secret" not in result.stderr
        assert "\x1b" not in result.stderr and "\r" not in result.stderr
        assert "secret.prompt" not in result.stderr


def test_short_owned_command_reports_stage_without_exposing_output(tmp_path, capsys):
    context = RunnerExecutionContext(
        execution_root=tmp_path, execution_dir=tmp_path / "execution", run_id="controlled",
        step="verification", progress_reporter=cli_support.report_execution_progress,
    )
    result = run_owned_process(
        [sys.executable, "-I", "-B", "-c", "print('private-command-output')"],
        "", tmp_path, 10, context,
    )
    assert result.returncode == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "项目验证" in captured.err and "已用时 0 秒" in captured.err
    assert "private-command-output" not in captured.err


@pytest.mark.parametrize("separator", ["\x1b[31m", "\x00", "\x1b]0;hidden-osc\x07",
                                       "\x1b]0;hidden-osc\x1b\\", "\x9d0;hidden-osc\x9c"])
def test_change_progress_redacts_final_visible_text(monkeypatch, tmp_path, separator):
    monkeypatch.setattr(cli, "resolve_repository_root", lambda _: tmp_path)

    class Driver:
        def __init__(self, *args, **kwargs):
            self.report = kwargs["event_reporter"]

        def change(self, **kwargs):
            self.report(f'正常中文 api_{separator}key="synthetic-review-secret"')
            self.report("保留正文\x1b]0;hidden-unclosed")
            return ChangeDriverResult(None, "attention_required", "human.required", "等待人工")

    monkeypatch.setattr(cli, "AgentChangeDriver", Driver)
    result = CliRunner().invoke(app, ["change", "--json", "--progress"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["reason_code"] == "human.required"
    assert "正常中文 api_key=[REDACTED]" in result.stderr.replace('"', "")
    assert "保留正文" in result.stderr
    assert "synthetic-review-secret" not in result.stderr
    assert "hidden-" not in result.stderr
    assert all(char.isprintable() or char == "\n" for char in result.stderr)
