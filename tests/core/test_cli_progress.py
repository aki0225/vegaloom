from vega.cli_support import report_execution_progress


def test_execution_progress_is_stderr_only_and_uses_safe_step_labels(capsys) -> None:
    report_execution_progress("worker", 25)
    report_execution_progress("worker.command_started", 26)
    report_execution_progress("reviewer.file_changed", 27)
    report_execution_progress("sk-test-secret", 50)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[vega] worker 运行中，已用时 25 秒" in captured.err
    assert "[vega] worker 开始执行命令，已用时 26 秒" in captured.err
    assert "[vega] reviewer 已应用文件修改，已用时 27 秒" in captured.err
    assert "[vega] runner 运行中，已用时 50 秒" in captured.err
    assert "sk-test-secret" not in captured.err
