from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .models import GateResult, LoopIterationState


REPORT_BINDING_START = "<!-- vega-risk-gate-binding\n"
REPORT_BINDING_END = "\n-->"


@dataclass(frozen=True)
class LoopRiskGateArtifactIntegrity:
    """当前 loop iteration 的本地风险门禁证据校验结果。"""

    valid: bool
    issues: tuple[str, ...]
    result: GateResult | None = None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_risk_gate_report_binding(
    *,
    status: str,
    iteration: int,
    source_run: str,
    result_sha256: str,
    risk: str | None,
    recommendation: str | None,
) -> str:
    """在 Markdown 报告中写入机器可校验的绑定元数据。

    报告文本哈希只能证明“这份文本没有被静默替换”，不能证明文本本身引用的是
    当前 iteration 的 Reflect 与 Gate result。这里使用固定注释包裹 JSON，既不干扰
    人类阅读，又能让 Finish/Goal 对两份风险门禁产物做语义绑定校验。
    """
    payload = {
        "schema_version": 1,
        "status": status,
        "iteration": iteration,
        "source_run": source_run,
        "result_sha256": result_sha256,
        "risk": risk,
        "recommendation": recommendation,
    }
    return (
        "## 证据绑定\n\n"
        + REPORT_BINDING_START
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        + REPORT_BINDING_END
        + "\n"
    )


def validate_iteration_risk_gate_artifacts(
    iteration_dir: Path,
    iteration: LoopIterationState,
    *,
    workspace: Path | None = None,
    repo_path: Path | None = None,
    trace_path: Path | None = None,
) -> LoopRiskGateArtifactIntegrity:
    """校验 loop 内风险门禁产物与 iteration state 是否绑定一致。

    loop 内的风险门禁不创建独立 Gate run，因此必须把结果、报告哈希、来源 Reflect
    以及 iteration 编号一并写入当前 iteration state。否则删除或替换本地产物后，
    后续 Finish 可能错误地沿用已失去依据的 reviewer approve。
    """
    issues: list[str] = []
    result_path = iteration_dir / "risk-gate-result.json"
    report_path = iteration_dir / "risk-gate-report.md"

    if iteration.risk_gate_status == "skipped":
        if result_path.exists():
            issues.append("risk_gate_result_unexpected")
        if report_path.exists():
            issues.append("risk_gate_report_unexpected")
        if any(
            value is not None
            for value in (
                iteration.risk_gate_source_run,
                iteration.risk_gate_risk,
                iteration.risk_gate_recommendation,
                iteration.risk_gate_result_sha256,
                iteration.risk_gate_report_sha256,
            )
        ):
            issues.append("risk_gate_state_unexpected")
        if iteration.review_run or iteration.verdict is not None:
            issues.append("risk_gate_required_for_review")
        return _integrity(issues)

    if not iteration.reflect_run:
        issues.append("risk_gate_reflect_run_missing")
    if iteration.risk_gate_source_run != iteration.reflect_run:
        issues.append("risk_gate_source_mismatch")

    result_text = _read_text(result_path, "risk_gate_result", issues)
    report_text = _read_text(report_path, "risk_gate_report", issues)
    _validate_hash(
        result_text,
        iteration.risk_gate_result_sha256,
        "risk_gate_result",
        issues,
    )
    _validate_hash(
        report_text,
        iteration.risk_gate_report_sha256,
        "risk_gate_report",
        issues,
    )
    report_binding = _load_report_binding(report_text, report_path, issues)

    payload = _load_json_object(result_text, issues)
    if payload is None:
        return _integrity(issues)

    if payload.get("schema_version") != 1:
        issues.append("risk_gate_schema_unsupported")
    if payload.get("status") != iteration.risk_gate_status:
        issues.append("risk_gate_status_mismatch")
    if payload.get("iteration") != iteration.iteration:
        issues.append("risk_gate_iteration_mismatch")
    if payload.get("source_run") != iteration.reflect_run:
        issues.append("risk_gate_result_source_mismatch")

    result: GateResult | None = None
    if iteration.risk_gate_status == "success":
        try:
            result = GateResult.model_validate(payload)
        except ValidationError:
            issues.append("risk_gate_result_schema_invalid")
        else:
            if result.risk != iteration.risk_gate_risk:
                issues.append("risk_gate_risk_mismatch")
            if result.recommendation != iteration.risk_gate_recommendation:
                issues.append("risk_gate_recommendation_mismatch")
        if iteration.risk_gate_risk is None:
            issues.append("risk_gate_risk_missing")
        if iteration.risk_gate_recommendation is None:
            issues.append("risk_gate_recommendation_missing")
        if result and _reviewer_evidence_present(iteration):
            if result.recommendation == "human-review":
                issues.append("risk_gate_human_review_bypassed")
        if result:
            _validate_result_policy(result, issues)
            _validate_recomputed_result(
                workspace,
                repo_path,
                iteration,
                result,
                issues,
            )
    else:
        if not isinstance(payload.get("code"), str) or not payload["code"].strip():
            issues.append("risk_gate_failure_code_missing")
        if any(
            value is not None
            for value in (
                iteration.risk_gate_risk,
                iteration.risk_gate_recommendation,
            )
        ):
            issues.append("risk_gate_failed_state_inconsistent")

    _validate_report_binding(
        report_binding,
        iteration,
        result,
        issues,
    )
    _validate_trace_binding(
        trace_path,
        iteration,
        result,
        issues,
    )
    if (iteration.review_run or iteration.verdict is not None) and (
        iteration.risk_gate_status != "success"
    ):
        issues.append("risk_gate_required_for_review")
    return _integrity(issues, result=result)


def _reviewer_evidence_present(iteration: LoopIterationState) -> bool:
    return (
        iteration.reviewer_status != "skipped"
        or iteration.review_run is not None
        or iteration.verdict is not None
    )


def _read_text(path: Path, prefix: str, issues: list[str]) -> str:
    if not path.is_file():
        issues.append(f"{prefix}_missing")
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        issues.append(f"{prefix}_unreadable")
        return ""


def _validate_hash(
    text: str,
    expected_hash: str | None,
    prefix: str,
    issues: list[str],
) -> None:
    if not expected_hash:
        issues.append(f"{prefix}_hash_missing")
    elif expected_hash != sha256_text(text):
        issues.append(f"{prefix}_hash_mismatch")


def _load_json_object(text: str, issues: list[str]) -> dict[str, object] | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        issues.append("risk_gate_result_invalid_json")
        return None
    if not isinstance(payload, dict):
        issues.append("risk_gate_result_schema_invalid")
        return None
    return payload


def _load_report_binding(
    report_text: str,
    report_path: Path,
    issues: list[str],
) -> dict[str, object] | None:
    if not report_text:
        if report_path.is_file():
            issues.append("risk_gate_report_binding_missing")
        return None
    if report_text.count(REPORT_BINDING_START) != 1:
        issues.append("risk_gate_report_binding_missing")
        return None
    start = report_text.find(REPORT_BINDING_START) + len(REPORT_BINDING_START)
    end = report_text.find(REPORT_BINDING_END, start)
    if end < 0:
        issues.append("risk_gate_report_binding_invalid")
        return None
    try:
        payload = json.loads(report_text[start:end])
    except json.JSONDecodeError:
        issues.append("risk_gate_report_binding_invalid_json")
        return None
    if not isinstance(payload, dict):
        issues.append("risk_gate_report_binding_schema_invalid")
        return None
    return payload


def _validate_report_binding(
    binding: dict[str, object] | None,
    iteration: LoopIterationState,
    result: GateResult | None,
    issues: list[str],
) -> None:
    if binding is None:
        return
    if binding.get("schema_version") != 1:
        issues.append("risk_gate_report_binding_schema_unsupported")
    if binding.get("status") != iteration.risk_gate_status:
        issues.append("risk_gate_report_status_mismatch")
    if binding.get("iteration") != iteration.iteration:
        issues.append("risk_gate_report_iteration_mismatch")
    if binding.get("source_run") != iteration.reflect_run:
        issues.append("risk_gate_report_source_mismatch")
    if binding.get("result_sha256") != iteration.risk_gate_result_sha256:
        issues.append("risk_gate_report_result_hash_mismatch")
    expected_risk = result.risk if result else None
    expected_recommendation = result.recommendation if result else None
    if binding.get("risk") != expected_risk:
        issues.append("risk_gate_report_risk_mismatch")
    if binding.get("recommendation") != expected_recommendation:
        issues.append("risk_gate_report_recommendation_mismatch")


def _validate_result_policy(result: GateResult, issues: list[str]) -> None:
    """校验风险等级、建议与原因严重级别之间的确定性映射。"""
    severities = {reason.severity for reason in result.reasons}
    expected_risk = (
        "high"
        if "high" in severities
        else "medium"
        if "medium" in severities
        else "low"
    )
    expected_recommendation = {
        "low": "self-check",
        "medium": "isolated-review",
        "high": "human-review",
    }[expected_risk]
    if result.risk != expected_risk:
        issues.append("risk_gate_result_risk_inconsistent")
    if result.recommendation != expected_recommendation:
        issues.append("risk_gate_result_recommendation_inconsistent")


def _validate_recomputed_result(
    workspace: Path | None,
    repo_path: Path | None,
    iteration: LoopIterationState,
    result: GateResult,
    issues: list[str],
) -> None:
    """以已绑定 Reflect 和当前可信快照重算风险结论，拒绝只改本地产物的降级。"""
    if workspace is None or repo_path is None or not iteration.reflect_run:
        return
    try:
        # gate_runtime 在模块加载时依赖 loop_evidence；这里延迟导入以避免循环依赖。
        from .gate_runtime import evaluate_risk

        expected = evaluate_risk(workspace, repo_path, iteration.reflect_run)
    except Exception:  # noqa: BLE001 - 重算失败不能把旧结果视为可信
        issues.append("risk_gate_recomputation_failed")
        return
    if _result_semantics(expected) != _result_semantics(result):
        issues.append("risk_gate_recomputed_result_mismatch")


def _result_semantics(result: GateResult) -> tuple[object, ...]:
    return (
        result.risk,
        result.recommendation,
        tuple((reason.code, reason.severity) for reason in result.reasons),
        tuple(result.changed_files),
        result.scope_profile,
    )


def _validate_trace_binding(
    trace_path: Path | None,
    iteration: LoopIterationState,
    result: GateResult | None,
    issues: list[str],
) -> None:
    if trace_path is None:
        return
    if not trace_path.is_file():
        issues.append("risk_gate_trace_missing")
        return
    try:
        events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        issues.append("risk_gate_trace_invalid")
        return

    expected_event = (
        "risk_gate_finished"
        if iteration.risk_gate_status == "success"
        else "risk_gate_failed"
    )
    matching_events = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("event") == expected_event
        and event.get("iteration") == iteration.iteration
    ]
    if not matching_events:
        issues.append("risk_gate_trace_event_missing")
        return
    if len(matching_events) != 1:
        issues.append("risk_gate_trace_event_duplicate")
        return

    event = matching_events[0]
    if event.get("status") != iteration.risk_gate_status:
        issues.append("risk_gate_trace_status_mismatch")
    if event.get("source_run") != iteration.risk_gate_source_run:
        issues.append("risk_gate_trace_source_mismatch")
    if event.get("result_sha256") != iteration.risk_gate_result_sha256:
        issues.append("risk_gate_trace_result_hash_mismatch")
    if event.get("report_sha256") != iteration.risk_gate_report_sha256:
        issues.append("risk_gate_trace_report_hash_mismatch")
    if result:
        if event.get("risk") != result.risk:
            issues.append("risk_gate_trace_risk_mismatch")
        if event.get("recommendation") != result.recommendation:
            issues.append("risk_gate_trace_recommendation_mismatch")


def _integrity(
    issues: list[str],
    *,
    result: GateResult | None = None,
) -> LoopRiskGateArtifactIntegrity:
    unique_issues = tuple(dict.fromkeys(issues))
    return LoopRiskGateArtifactIntegrity(
        valid=not unique_issues,
        issues=unique_issues,
        result=result if not unique_issues else None,
    )
