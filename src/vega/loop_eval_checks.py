from __future__ import annotations

import json
from pathlib import Path

from .models import GateResult, LoopIterationState, ReviewVerdict
from .risk_gate_evidence import validate_iteration_risk_gate_artifacts
from .risk_review_evidence import required_review_iteration_eval_results
from .scope_gate import (
    LoopScopeGateArtifactIntegrity,
    validate_iteration_scope_gate_artifacts,
)


def loop_iteration_evidence_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    workspace: Path | None = None,
    repo_path: Path | None = None,
    trace_path: Path | None = None,
    scope_gate_required: bool = False,
    expected_head_sha: str | None = None,
    expected_policy_sha256: str | None = None,
    comparison_base_sha: str | None = None,
    comparison_paths: tuple[str, ...] = (),
) -> list[str]:
    if not iteration_dir.exists():
        return [f"FAIL: iteration artifact 目录不存在：{iteration.iteration:02d}"]
    results = [
        *_reflect_reference_checks(iteration_dir, iteration),
        *_verification_artifact_checks(iteration_dir, iteration),
        *_scope_gate_checks(
            iteration_dir,
            iteration,
            repo_path=repo_path,
            trace_path=trace_path,
            required=scope_gate_required,
            expected_head_sha=expected_head_sha,
            expected_policy_sha256=expected_policy_sha256,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        ),
    ]
    risk_results, risk_result = _risk_gate_checks(
        iteration_dir,
        iteration,
        workspace=workspace,
        repo_path=repo_path,
        trace_path=trace_path,
    )
    results.extend(risk_results)
    results.extend(
        _review_evidence_checks(
            iteration_dir,
            iteration,
            risk_result,
        )
    )
    return results


def _reflect_reference_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
) -> list[str]:
    if not iteration.reflect_run:
        return []
    reflect_ref = _read_optional_text(iteration_dir / "reflect-run.txt").strip()
    if not reflect_ref:
        return ["FAIL: iteration 声明 reflect_run 但缺少 reflect-run.txt"]
    if Path(reflect_ref).name != iteration.reflect_run:
        return ["FAIL: reflect-run.txt 与 iteration.reflect_run 不一致"]
    return []


def _verification_artifact_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
) -> list[str]:
    results = _verification_iteration_state_checks(iteration)
    if iteration.verification_status not in {"passed", "failed"}:
        return results
    if not (iteration_dir / "verification-summary.md").exists():
        results.append("FAIL: verification 状态已记录但缺少 verification-summary.md")
    if not (iteration_dir / "test-summary.md").exists():
        results.append("FAIL: verification 状态已记录但缺少 test-summary.md")
    return results


def _scope_gate_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    repo_path: Path | None,
    trace_path: Path | None,
    required: bool,
    expected_head_sha: str | None,
    expected_policy_sha256: str | None,
    comparison_base_sha: str | None,
    comparison_paths: tuple[str, ...],
) -> list[str]:
    results: list[str] = []
    for phase, current_repo, pass_message, issue_prefix in (
        (
            "pre_verification",
            None,
            "PASS: pre-verification scope gate artifact 与 iteration 一致",
            "",
        ),
        (
            "post_verification",
            None,
            "PASS: post-verification scope gate artifact 与 iteration 一致",
            "post_verification_",
        ),
        (
            "pre_review",
            repo_path,
            "PASS: pre-review scope gate artifact 与 iteration 一致",
            "pre_review_",
        ),
    ):
        integrity = validate_iteration_scope_gate_artifacts(
            iteration_dir,
            iteration,
            phase=phase,
            repo_path=current_repo,
            trace_path=trace_path,
            required=required,
            expected_head_sha=expected_head_sha,
            expected_policy_sha256=expected_policy_sha256,
            comparison_base_sha=comparison_base_sha,
            comparison_paths=comparison_paths,
        )
        results.extend(
            _scope_gate_integrity_results(
                integrity,
                pass_message,
                issue_prefix,
            )
        )
    return results


def _scope_gate_integrity_results(
    integrity: LoopScopeGateArtifactIntegrity,
    pass_message: str,
    issue_prefix: str,
) -> list[str]:
    if integrity.valid and integrity.evaluated:
        return [pass_message]
    return [f"FAIL: {issue_prefix}{issue}" for issue in integrity.issues]


def _risk_gate_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    workspace: Path | None,
    repo_path: Path | None,
    trace_path: Path | None,
) -> tuple[list[str], GateResult | None]:
    integrity = validate_iteration_risk_gate_artifacts(
        iteration_dir,
        iteration,
        workspace=workspace,
        repo_path=repo_path,
        trace_path=trace_path,
    )
    if integrity.valid and iteration.risk_gate_status != "skipped":
        return ["PASS: risk gate artifact 与 iteration 一致"], integrity.result
    return (
        [f"FAIL: {issue}" for issue in integrity.issues],
        integrity.result,
    )


def _review_evidence_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
    risk_result: GateResult | None,
) -> list[str]:
    results: list[str] = []
    if iteration.verdict is None:
        return results
    verdict_path = iteration_dir / "review-verdict.json"
    if not verdict_path.exists():
        return ["FAIL: iteration 声明 verdict 但缺少 review-verdict.json"]
    try:
        verdict = ReviewVerdict.model_validate_json(
            verdict_path.read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - Eval 必须报告损坏 verdict
        results.append(f"FAIL: review-verdict.json 不合法：{type(exc).__name__}")
        return results
    if verdict.verdict != iteration.verdict:
        results.append("FAIL: review-verdict.json 与 iteration.verdict 不一致")
    else:
        results.append("PASS: reviewer verdict artifact 与 iteration 一致")
    results.extend(required_review_iteration_eval_results(risk_result, verdict))

    context_path = iteration_dir / "review-context.json"
    if not context_path.exists():
        results.append("FAIL: iteration 声明 verdict 但缺少 review-context.json")
        return results
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        results.append(f"FAIL: review-context.json 不合法：{exc.msg}")
        return results
    if iteration.reflect_run and context.get("source_run") != iteration.reflect_run:
        results.append("FAIL: review-context.json 与 iteration.reflect_run 不一致")
    return results


def interrupted_iteration_evidence_checks(
    iteration_dir: Path,
    iteration: LoopIterationState,
) -> list[str]:
    results: list[str] = []
    if not iteration_dir.is_dir():
        return [f"FAIL: interrupted iteration 目录不存在：{iteration.iteration:02d}"]
    if not iteration.interrupted_step:
        results.append("FAIL: interrupted iteration 缺少 interrupted_step")
    if not iteration.interrupted_at:
        results.append("FAIL: interrupted iteration 缺少 interrupted_at")
    report_path = iteration_dir / "interruption-report.md"
    if not report_path.is_file():
        results.append("FAIL: interrupted iteration 缺少 interruption-report.md")
        return results
    report = _read_optional_text(report_path)
    if f"- 迭代：`{iteration.iteration}`" not in report:
        results.append("FAIL: interruption-report.md 与 iteration 编号不一致")
    if (
        iteration.interrupted_step
        and f"- 原步骤：`{iteration.interrupted_step}`" not in report
    ):
        results.append("FAIL: interruption-report.md 与 interrupted_step 不一致")
    if iteration.interrupted_at and iteration.interrupted_at not in report:
        results.append("FAIL: interruption-report.md 与 interrupted_at 不一致")
    if not results:
        results.append("PASS: interrupted iteration 证据已保留且不参与成功判定")
    return results


def _verification_iteration_state_checks(
    iteration: LoopIterationState,
) -> list[str]:
    results: list[str] = []
    if iteration.verification_failed_count and iteration.verification_status != "failed":
        results.append("FAIL: verification 失败计数与状态不一致")
    if (
        iteration.verification_failure_kind is not None
        and iteration.verification_status != "failed"
    ):
        results.append("FAIL: verification failure_kind 与状态不一致")
    return results


def _read_optional_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
