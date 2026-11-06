from __future__ import annotations

import json
from pathlib import Path

import pytest

from vega.prompt_metrics import measure_prompt
from vega.review_checks import ReviewCheck, review_checks_failed
from vega.review_eval import run_review_pack_eval


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
