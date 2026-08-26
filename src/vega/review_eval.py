from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .models import GateResult
from .prompt_metrics import PromptMetrics
from .redaction import redact_text
from .review_contract import ReviewVerdict
from .review_coverage import run_review_file_coverage_eval
from .review_queue_contract import REVIEW_QUEUE_ARTIFACT
from .review_runner_contract import (
    review_result_diagnostic,
    review_result_is_trusted,
)
from .risk_review_runtime import (
    required_review_outcome_line,
    run_required_review_pack_eval,
    run_required_review_prompt_eval,
)
from .runner import RunnerResult


def append_review_eval_outcome(
    eval_results: list[str],
    *,
    result: RunnerResult,
    review_execution_issues: list[str],
    pre_review_evidence_issues: list[str],
    metrics: PromptMetrics,
    prompt_budget_blocked: bool,
    risk_gate_result: GateResult | None,
    verdict: ReviewVerdict,
    risk_disclosure_issues: list[str],
) -> None:
    eval_results.extend(
        f"FAIL: required risk disclosure：{issue}"
        for issue in risk_disclosure_issues
    )
    outcome = _review_execution_outcome(
        eval_results,
        result=result,
        review_execution_issues=review_execution_issues,
        pre_review_evidence_issues=pre_review_evidence_issues,
        metrics=metrics,
        prompt_budget_blocked=prompt_budget_blocked,
        risk_gate_result=risk_gate_result,
        verdict=verdict,
        risk_disclosure_issues=risk_disclosure_issues,
    )
    if outcome is not None:
        eval_results.append(outcome)


def _review_execution_outcome(
    eval_results: list[str],
    *,
    result: RunnerResult,
    review_execution_issues: list[str],
    pre_review_evidence_issues: list[str],
    metrics: PromptMetrics,
    prompt_budget_blocked: bool,
    risk_gate_result: GateResult | None,
    verdict: ReviewVerdict,
    risk_disclosure_issues: list[str],
) -> str | None:
    if result.termination_unconfirmed:
        return (
            "FAIL: reviewer owned process tree 终止未确认，"
            "未读取或采用 runner 输出"
        )
    if review_execution_issues:
        return "FAIL: reviewer 执行期间工作区发生变化或无法完成快照校验"
    if pre_review_evidence_issues:
        return None
    if prompt_budget_blocked:
        return "FAIL: reviewer prompt 超过上下文预算，未启动外部 runner"
    if metrics.exceeded:
        return "PASS: 超预算 reviewer 输入已由 Review Queue 完整覆盖"
    if risk_gate_result is None:
        return "FAIL: review 风险门禁评估失败，未启动外部 runner"
    if not review_result_is_trusted(result):
        return (
            "FAIL: reviewer Runner 未形成可采信结论"
            f"（{review_result_diagnostic(result)}），未读取或采用 runner 输出"
        )
    return _trusted_review_outcome(
        eval_results,
        risk_gate_result,
        verdict,
        risk_disclosure_issues,
    )


def _trusted_review_outcome(
    eval_results: list[str],
    risk_gate_result: GateResult,
    verdict: ReviewVerdict,
    risk_disclosure_issues: list[str],
) -> str | None:
    if risk_gate_result.recommendation == "human-review":
        return required_review_outcome_line(
            risk_gate_result,
            verdict,
            risk_disclosure_issues,
        )
    if verdict.verdict == "needs_human" and not _has_failures(eval_results):
        return "PASS: reviewer 输出可信的 needs_human 人工阻断结论"
    if verdict.verdict != "needs_human":
        return "PASS: reviewer 输出 verdict 可解析"
    return None


def run_review_pack_eval(run_dir: Path, artifacts: list[str]) -> list[str]:
    results = [
        f"{'PASS' if (run_dir / item).exists() else 'FAIL'}: artifact 存在：{item}"
        for item in artifacts
    ]
    prompt_text = _review_prompt_eval(run_dir, results)
    queue = _read_json(run_dir / REVIEW_QUEUE_ARTIFACT)
    queue_completed = queue.get("status") == "completed"
    _append_budget_eval(run_dir, queue_completed, results)
    context = _read_json(run_dir / "review-context.json")
    _append_context_eval(context, queue, queue_completed, results)
    changed_files = _string_list(context.get("changed_files"))
    results.extend(
        run_review_file_coverage_eval(
            run_dir,
            artifacts,
            changed_files,
            prompt_text,
        )
    )
    _append_risk_eval(context, run_dir, artifacts, prompt_text, results)
    return results


def _review_prompt_eval(run_dir: Path, results: list[str]) -> str:
    prompt = run_dir / "review-prompt.md"
    if not prompt.exists():
        return ""
    prompt_text = prompt.read_text(encoding="utf-8", errors="replace")
    results.append(
        "PASS: review prompt 标记不包含 worker 聊天"
        if "worker 的完整聊天记录" in prompt_text
        else "FAIL: review prompt 缺少隔离说明"
    )
    return prompt_text


def _append_budget_eval(
    run_dir: Path,
    queue_completed: bool,
    results: list[str],
) -> None:
    metrics_path = run_dir / "review-prompt-metrics.json"
    if not metrics_path.exists():
        return
    metrics = PromptMetrics.model_validate_json(
        metrics_path.read_text(encoding="utf-8")
    )
    if not metrics.exceeded:
        results.append("PASS: review prompt 未超过上下文预算")
    elif queue_completed:
        results.append("PASS: review prompt 超预算且 Review Queue 已完成")
    else:
        results.append("FAIL: review prompt 超过上下文预算")


def _append_context_eval(
    context: dict[str, object],
    queue: dict[str, object],
    queue_completed: bool,
    results: list[str],
) -> None:
    truncated = _string_list(context.get("truncated_sections"))
    unresolved = [
        item
        for item in truncated
        if not (queue_completed and item == "full_diff")
    ]
    if unresolved:
        results.append("WARN: review evidence 已截断：" + ", ".join(unresolved))
    elif truncated:
        results.append("PASS: 完整 Diff 已由 Review Queue 分片覆盖")
    else:
        results.append("PASS: review evidence 未截断")
    if queue:
        results.append(
            "PASS: Review Queue 已覆盖全部变更文件"
            if queue_completed
            else "FAIL: Review Queue 未完成；remaining="
            + ",".join(_string_list(queue.get("remaining")))
        )
    evidence_issues = _string_list(context.get("evidence_issues"))
    if not evidence_issues:
        results.append("PASS: review 证据与当前工作区属于同一快照")
        return
    results.append("FAIL: review 证据与当前工作区不属于同一快照")
    results.extend(
        f"FAIL: review evidence issue：{issue}" for issue in evidence_issues
    )
    results.extend(
        f"FAIL: review evidence diagnostic：{diagnostic}"
        for diagnostic in _string_list(context.get("evidence_diagnostics"))
    )


def _append_risk_eval(
    context: dict[str, object],
    run_dir: Path,
    artifacts: list[str],
    prompt_text: str,
    results: list[str],
) -> None:
    risk_gate = context.get("risk_gate")
    if risk_gate is None:
        return
    if not isinstance(risk_gate, dict):
        results.append("FAIL: review 风险门禁记录格式不合法")
        return
    if risk_gate.get("status") != "success":
        results.append("FAIL: review 风险门禁评估失败")
        return
    try:
        result = GateResult.model_validate(risk_gate.get("result"))
    except ValidationError:
        results.append("FAIL: review 风险门禁结果格式不合法")
        return
    if "review-verdict.json" in artifacts:
        results.extend(
            run_required_review_pack_eval(
                result,
                run_dir / "review-verdict.json",
            )
        )
    else:
        results.extend(run_required_review_prompt_eval(result, prompt_text))


def render_eval(results: list[str]) -> str:
    return redact_text(
        "# Eval\n\n" + "\n".join(f"- {item}" for item in results) + "\n"
    )


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _has_failures(results: list[str]) -> bool:
    return any(item.startswith("FAIL:") for item in results)
