from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .gate_runtime import evaluate_risk
from .models import GateResult
from .redaction import redact_text


@dataclass(frozen=True)
class PrecomputedReviewRiskGate:
    """Loop 传给内嵌 reviewer 的已绑定确定性风险门禁。"""

    source_run: str
    result: GateResult
    project_policy_snapshot: dict[str, str | None]


def evaluate_review_risk_gate(
    workspace: Path,
    repo_path: Path,
    source_run: str,
) -> dict[str, Any]:
    """为独立 reviewer 固化同一份确定性风险结论。"""
    try:
        result = evaluate_risk(workspace, repo_path, source_run)
    except Exception as exc:  # noqa: BLE001 - 风险评估失败必须阻止自动审查结论
        return {
            "status": "failed",
            "source_run": source_run,
            "diagnostic": redact_text(f"{type(exc).__name__}: {exc}")[:1000],
        }
    return {
        "status": "success",
        "source_run": source_run,
        "result": result.model_dump(mode="json"),
    }


def review_risk_gate_result(inputs: dict[str, Any]) -> GateResult | None:
    gate = inputs.get("risk_gate")
    if not isinstance(gate, dict) or gate.get("status") != "success":
        return None
    try:
        return GateResult.model_validate(gate.get("result"))
    except ValidationError:
        return None
