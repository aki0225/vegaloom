from __future__ import annotations

import json
from pathlib import Path

import pytest

import vega.review_runtime as runtime
from vega.models import GateResult
from vega.prompt_metrics import measure_prompt
from vega.review_checks import ReviewCheck, review_checks_failed
from vega.review_eval import run_review_pack_eval


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        ("fail", "检查未通过（不含英文前缀）", "failed"),
        ("pass", "FAIL: 仅为被引用的展示文字", "success"),
    ],
)
def test_review_pack_terminal_uses_check_status_not_report_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    message: str,
    expected: str,
) -> None:
    # 受控检查事实与任意展示文字分开；不启动模型或真实 Git。
    check = ReviewCheck(status, message)
    monkeypatch.setattr(
        runtime,
        "collect_review_inputs",
        lambda *args: {
            "changed_files": [],
            "evidence_consistent": True,
            "evidence_issues": [],
            "evidence_diagnostics": [],
        },
    )
    gate = GateResult(risk="low", recommendation="self-check")
    monkeypatch.setattr(
        runtime,
        "_review_risk_gate_payload",
        lambda *args: {
            "status": "success",
            "result": gate.model_dump(),
        },
    )
    monkeypatch.setattr(
        runtime,
        "_write_review_pack_artifacts",
        lambda *args: measure_prompt("", role="reviewer", max_chars=100),
    )
    monkeypatch.setattr(runtime, "run_review_pack_eval", lambda *args: [check])

    run = runtime.ReviewPackRuntime(tmp_path).run(tmp_path, "controlled-reflect")

    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == expected
    assert state["eval_results"] == [message]
    assert message in (run / "eval.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "damage",
    [None, "artifact", "prompt", "budget", "queue", "snapshot", "coverage", "risk"],
)
def test_review_pack_checks_preserve_failure_facts(
    tmp_path: Path,
    damage: str | None,
) -> None:
    prompt = 'worker 的完整聊天记录 "reviewed_files" sample.py'
    context = {"changed_files": ["sample.py"]}
    if damage == "prompt":
        prompt = '"reviewed_files" sample.py'
    elif damage == "coverage":
        prompt = 'worker 的完整聊天记录 "reviewed_files"'
    elif damage == "snapshot":
        context["evidence_issues"] = ["workspace_changed"]
    elif damage == "risk":
        context["risk_gate"] = {"status": "success", "result": {}}
    if damage == "queue":
        (tmp_path / "review-queue.json").write_text(
            json.dumps({"status": "pending", "remaining": ["sample.py"]}),
            encoding="utf-8",
        )
    if damage == "budget":
        metrics = measure_prompt(prompt, role="reviewer", max_chars=1)
        (tmp_path / "review-prompt-metrics.json").write_text(
            metrics.model_dump_json(),
            encoding="utf-8",
        )
    (tmp_path / "review-prompt.md").write_text(prompt, encoding="utf-8")
    (tmp_path / "review-context.json").write_text(json.dumps(context), encoding="utf-8")
    artifacts = ["review-prompt.md", "review-context.json"]
    if damage == "artifact":
        artifacts.append("missing.md")

    checks = run_review_pack_eval(tmp_path, artifacts)

    assert review_checks_failed(checks) is (damage is not None)
    # 把全部报告换成不含 FAIL 的文案也不能改变事实汇总。
    rewritten = [ReviewCheck(item.status, "重新表述的检查说明") for item in checks]
    assert review_checks_failed(rewritten) is (damage is not None)
