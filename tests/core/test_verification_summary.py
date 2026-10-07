"""受控输出验证摘要预算；不运行项目命令或 Provider。"""

import pytest

from vega.reflect_runtime import render_test_summary
from vega.verification import _redact_process_output, render_verification_summary


def payload(results, **extra):
    return {
        "repo_path": "fixture", "commands": [r["command"] for r in results],
        "results": results, "command_count": len(results),
        "failed_count": sum(r["status"] != "passed" for r in results), **extra,
    }


def result(command, status="passed", output="", interruption=None):
    return dict(command=command, status=status, output=output, returncode=0 if status == "passed" else 1,
                duration_seconds=1.0, interruption_status=interruption)


def test_failure_tails_survive_verification_and_reviewer_budgets():
    rows = [result("pass-command", output="PASS-LONG\n" * 2000),
            result("fail-two", "failed", _redact_process_output("noise\n" * 3000 + "DB_UNKNOWN", "")),
            result("fail-three", "failed", _redact_process_output("noise\n" * 3000 + "FileNotFoundError", ""))]
    summary = render_verification_summary(payload(rows))
    text = render_test_summary(summary)
    for word in ("1.", "2.", "3.", "pass-command", "fail-two", "fail-three", "DB_UNKNOWN", "FileNotFoundError"):
        assert word in text
    assert text.index("DB_UNKNOWN") < text.index("PASS-LONG")
    assert "截断" in text
    assert len(summary) <= 5000
    assert "退出码：`1`" in text


@pytest.mark.parametrize("interruption", [None, "timed_out", "stopped", "termination-unconfirmed"])
def test_empty_and_interrupted_results_remain_distinct(interruption):
    row = result("check", "failed" if interruption else "passed", interruption=interruption)
    text = render_verification_summary(payload([row], interruption_status=interruption, skipped_commands=["later"] if interruption else []))
    assert ("PASS" if interruption is None else {"timed_out": "TIMEOUT", "stopped": "STOPPED", "termination-unconfirmed": "TERMINATION-UNCONFIRMED"}[interruption]) in text
    assert "<empty>" in text
    if interruption:
        assert "未执行命令数：1" in text
        assert "later" in text


def test_full_output_redacted_before_head_tail_excerpt():
    raw = '\x1b[31mapi_key="head-secret"\x1b[0m\n' + "padding\n" * 3000 + 'password="tail-secret"\nTAIL'
    output = _redact_process_output(raw, None)
    assert len(output) <= 8000
    assert "TAIL" in output
    assert "head-secret" not in output and "tail-secret" not in output
    assert "\x1b" not in output
    assert "截断" in output
    text = render_test_summary(raw)
    assert "TAIL" in text and "tail-secret" not in text and "head-secret" not in text


def test_no_commands_does_not_claim_pass():
    text = render_verification_summary(payload([]))
    assert "SKIP" in text and "PASS" not in text


@pytest.mark.parametrize("with_log", [False, True])
def test_only_actual_execution_log_is_referenced(tmp_path, monkeypatch, with_log):
    from types import SimpleNamespace

    from vega.execution_control import RunnerExecutionContext
    from vega.verification import _run_command

    context = RunnerExecutionContext(execution_root=tmp_path, execution_dir=tmp_path / "executions" / "verification-01",
                                     run_id="fixture", step="verification")
    raw = context.execution_dir / "process-output.txt"
    if with_log:
        raw.parent.mkdir(parents=True)
        raw.write_text("raw fixture", encoding="utf-8")
    monkeypatch.setattr("vega.verification.run_owned_process", lambda *a, **k: SimpleNamespace(
        termination_unconfirmed=False, status="error", output="failure", error="", returncode=7))
    row = _run_command(tmp_path, "fixture-command", "fixture-command", None, 1, 10, context)
    text = render_test_summary(render_verification_summary(payload([row])))
    assert ("executions/verification-01/process-output.txt" in text) is with_log
    if with_log:
        assert raw.read_text(encoding="utf-8") == "raw fixture"
