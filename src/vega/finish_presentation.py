from __future__ import annotations

from typing import Any

from .loop_evidence import EvidenceFreshness
from .loop_integrity import LoopArtifactIntegrity
from .models import GateResult, LoopAutomationState, ReviewVerdict
from .redaction import redact_text


_BUDGET_REASON_CODES = {
    "budget_changed_files",
    "budget_diff_lines",
    "budget_new_files",
    "new_dependencies",
    "large_generated_files",
}


def build_finish_first_screen(
    state: LoopAutomationState,
    finish_status: str,
    latest_verdict: ReviewVerdict | None,
    verification_results: list[dict[str, Any]],
    risk_gate_results: list[GateResult],
    artifact_integrity: LoopArtifactIntegrity,
    evidence_freshness: EvidenceFreshness,
    handoff_notes: list[str],
    *,
    latest_verification_failed: bool,
    verification_passed: bool,
    has_verification_failures: bool,
) -> dict[str, Any]:
    iteration = state.iterations[-1] if state.iterations else None
    gate = risk_gate_results[-1] if risk_gate_results else None
    changed_files, changed_files_source = _changed_files(gate, iteration)
    reasons = list(gate.reasons) if gate else []
    return {
        "decision": {
            "run_id": state.run_id,
            "repo_path": state.repo_path,
            "task_mode": state.task_mode,
            "automation_mode": state.automation_mode,
            "status": finish_status,
            "loop_status": state.status,
            "loop_step": state.current_step,
            "reasons": handoff_notes,
        },
        "actual_changes": {
            "changed_file_count": len(changed_files),
            "changed_files": changed_files,
            "changed_files_source": changed_files_source,
            "scope_profile": gate.scope_profile if gate else None,
            "workspace_new_files_count": iteration.workspace_new_files_count if iteration else 0,
            "risk": gate.risk if gate else None,
            "recommendation": gate.recommendation if gate else None,
            "budget_findings": [
                reason.model_dump() for reason in reasons if reason.code in _BUDGET_REASON_CODES
            ],
            "high_risk_findings": [
                reason.model_dump() for reason in reasons if reason.severity == "high"
            ],
            "required_reviews": [
                item.model_dump() for item in (gate.required_reviews if gate else [])
            ],
        },
        "gates": {
            "workspace": iteration.workspace_status if iteration else "skipped",
            "scope": {
                "pre_verification": iteration.scope_gate_status if iteration else "skipped",
                "post_verification": (
                    iteration.scope_gate_post_verification_status if iteration else "skipped"
                ),
                "pre_review": (
                    iteration.scope_gate_pre_review_status if iteration else "skipped"
                ),
            },
            "verification": iteration.verification_status if iteration else "skipped",
            "risk": {
                "status": iteration.risk_gate_status if iteration else "skipped",
                "level": gate.risk if gate else None,
                "recommendation": gate.recommendation if gate else None,
            },
            "artifact_integrity": {
                "status": "valid" if artifact_integrity.valid else "invalid",
                "issues": list(artifact_integrity.issues),
            },
            "evidence_freshness": {
                "status": "fresh" if evidence_freshness.fresh else "stale",
                "issues": list(evidence_freshness.issues),
            },
        },
        "verification": {
            "trusted_passed": verification_passed,
            "latest_failed": latest_verification_failed,
            "historical_failures": has_verification_failures,
            "checks": _verification_checks(verification_results),
        },
        "review": latest_verdict.model_dump() if latest_verdict else _empty_review(),
        "evidence_limits": _evidence_limits(
            finish_status,
            latest_verdict,
            gate,
            artifact_integrity,
            evidence_freshness,
            verification_passed,
        ),
        "next_steps": _next_steps(
            finish_status,
            gate,
            artifact_integrity,
            evidence_freshness,
            latest_verification_failed,
            verification_passed,
        ),
    }


def render_finish_report(summary: dict[str, Any]) -> str:
    first_screen = summary.get("first_screen") or {}
    lines = ["# Finish Report", ""]
    lines.extend(_render_decision(first_screen.get("decision") or {}))
    lines.extend(_render_changes(first_screen.get("actual_changes") or {}))
    lines.extend(_render_gates(first_screen.get("gates") or {}))
    lines.extend(_render_verification(first_screen.get("verification") or {}))
    lines.extend(_render_review(first_screen.get("review") or {}))
    lines.extend(_render_bullets("证据上限", first_screen.get("evidence_limits") or []))
    lines.extend(_render_bullets("下一步", first_screen.get("next_steps") or []))
    lines.extend(_render_details(summary))
    return redact_text("\n".join(lines).rstrip() + "\n")


def _changed_files(gate: GateResult | None, iteration: Any) -> tuple[list[str], str]:
    if gate is not None:
        return list(gate.changed_files), "trusted_risk_gate"
    if iteration is not None:
        for field_name in (
            "scope_gate_pre_review_changed_files",
            "scope_gate_post_verification_changed_files",
            "scope_gate_changed_files",
        ):
            changed_files = list(getattr(iteration, field_name, []))
            if changed_files:
                return changed_files, "loop_state_scope_gate"
    return [], "unavailable"


def _verification_checks(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for payload in results:
        common = {"iteration": payload.get("iteration"), "artifact": payload.get("path")}
        for item in payload.get("results") or []:
            if isinstance(item, dict):
                checks.append(
                    {
                        **common,
                        "command": item.get("configured_command")
                        or item.get("command")
                        or "<unknown>",
                        "status": item.get("interruption_status")
                        or item.get("status")
                        or "unknown",
                        "returncode": item.get("returncode"),
                        "duration_seconds": item.get("duration_seconds"),
                    }
                )
        for command in payload.get("skipped_commands") or []:
            if isinstance(command, str):
                checks.append(
                    {
                        **common,
                        "command": command,
                        "status": "skipped",
                        "returncode": None,
                        "duration_seconds": None,
                    }
                )
    return checks


def _empty_review() -> dict[str, Any]:
    return {
        "verdict": None,
        "summary": "",
        "findings": [],
        "risk_disclosures": [],
        "checked_items": [],
    }


def _evidence_limits(
    finish_status: str,
    verdict: ReviewVerdict | None,
    gate: GateResult | None,
    integrity: LoopArtifactIntegrity,
    freshness: EvidenceFreshness,
    verification_passed: bool,
) -> list[str]:
    limits = [
        "Finish 只整理既有结构化 artifact，没有重新运行验证命令或调用模型。",
        "Reviewer 意见与确定性 Gate 分开呈现，不等同于已经证明。",
    ]
    if finish_status == "ready_to_commit":
        limits.append(
            "ready_to_commit 只表示满足人工提交前检查，不表示已经 commit、合并或证明生产安全。"
        )
    if not integrity.valid:
        limits.append("Artifact 完整性无效，未绑定或损坏的证据不能作为结论。")
    if not freshness.fresh:
        limits.append("Reviewer 通过后的工作区证据已过期，现有审查结论不能继续沿用。")
    if not verification_passed:
        limits.append("缺少最新受信验证通过证据，不能把 Reviewer 意见解释为验证成功。")
    if verdict is None:
        limits.append("没有可采用的 Reviewer verdict，独立审查尚未完成。")
    elif _review_has_missing_lines(verdict):
        limits.append("Reviewer 对部分 finding 或风险位置未提供关键行；Finish 未补造行号。")
    if gate and gate.required_reviews:
        limits.append("命名高风险披露只作为人工检查材料，不构成自动安全证明。")
    return limits


def _review_has_missing_lines(verdict: ReviewVerdict) -> bool:
    return any(item.line == 0 for item in verdict.findings) or any(
        location.line == 0
        for disclosure in verdict.risk_disclosures
        for location in disclosure.locations
    )


def _next_steps(
    finish_status: str,
    gate: GateResult | None,
    integrity: LoopArtifactIntegrity,
    freshness: EvidenceFreshness,
    latest_verification_failed: bool,
    verification_passed: bool,
) -> list[str]:
    if finish_status == "ready_to_commit":
        return [
            "人工检查最终 `git diff`、验证命令和高风险位置。",
            "确认无误后由用户自行 commit；Vega 不会自动 commit、push 或 release。",
        ]
    if finish_status == "needs_fix":
        return [
            "按 Reviewer findings 或最新 `fix-prompt.md` 完成最小修复。",
            "重新运行 `vega loop continue` 和 `vega finish`，不要直接提交。",
        ]
    if finish_status != "needs_human":
        return ["继续当前 loop；终态证据完整后重新运行 `vega finish`。"]
    steps: list[str] = []
    if not integrity.valid:
        steps.append("检查完整性 issues，重新生成缺失或失效的证据链。")
    if not freshness.fresh:
        steps.append("工作区稳定后重新执行 reflect、独立 review 和 finish。")
    if latest_verification_failed:
        steps.append("修复失败或超时的验证命令，再重新执行验证。")
    elif not verification_passed:
        steps.append("补充并运行至少一条受信的项目验证命令。")
    if gate and (gate.recommendation == "human-review" or gate.required_reviews):
        steps.append("人工逐项检查高风险命中、Reviewer 关键位置和剩余风险。")
    if not steps:
        steps.append("阅读 handoff 原因和关键产物，人工决定继续修复、重跑或停止。")
    steps.append("人工处理完成后重新运行 `vega finish` 获取新的裁决。")
    return steps


def _render_decision(decision: dict[str, Any]) -> list[str]:
    lines = [
        "## 当前裁决",
        "",
        f"- Run：`{decision.get('run_id', 'unknown')}`",
        f"- 仓库：`{decision.get('repo_path', 'unknown')}`",
        f"- 任务 / 模式：`{decision.get('task_mode', 'unknown')}` / "
        f"`{decision.get('automation_mode', 'unknown')}`",
        f"- Finish / Loop：`{decision.get('status', 'unknown')}` / "
        f"`{decision.get('loop_status', 'unknown')}`",
        f"- 当前步骤：`{decision.get('loop_step', 'unknown')}`",
    ]
    lines.extend(f"- 原因：{item}" for item in decision.get("reasons") or [])
    return lines


def _render_changes(changes: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## 实际变更",
        "",
        f"- 文件数：`{changes.get('changed_file_count', 0)}`；"
        f"来源：`{changes.get('changed_files_source', 'unavailable')}`；"
        f"新增未跟踪：`{changes.get('workspace_new_files_count', 0)}`",
        f"- Scope：`{changes.get('scope_profile') or '未记录'}`；"
        f"风险：`{changes.get('risk') or '未评估'}`；"
        f"建议：`{changes.get('recommendation') or '未评估'}`",
    ]
    files = changes.get("changed_files") or []
    lines.append("- 变更文件：" + ("、".join(f"`{item}`" for item in files) if files else "未获得可信列表"))
    budget_findings = changes.get("budget_findings") or []
    if budget_findings:
        lines.append("- 预算命中：")
        lines.extend(
            f"  - `{item.get('code')}`：{item.get('message')}（{item.get('evidence') or '无证据'}）"
            for item in budget_findings
        )
    else:
        lines.append("- 预算命中：无已记录超限信号；Finish 没有重新计算预算。")
    high_risk_findings = changes.get("high_risk_findings") or []
    if high_risk_findings:
        lines.append("- 高风险信号：")
        lines.extend(
            f"  - `{item.get('code')}`：{item.get('message')}（{item.get('evidence') or '无证据'}）"
            for item in high_risk_findings
        )
    reviews = changes.get("required_reviews") or []
    if reviews:
        lines.append("- 必审高风险：")
        lines.extend(
            f"  - `{item.get('id')}` / {item.get('label')}："
            + "、".join(f"`{path}`" for path in item.get("matched_files") or [])
            for item in reviews
        )
    return lines


def _render_gates(gates: dict[str, Any]) -> list[str]:
    scope = gates.get("scope") or {}
    risk = gates.get("risk") or {}
    integrity = gates.get("artifact_integrity") or {}
    freshness = gates.get("evidence_freshness") or {}
    lines = [
        "",
        "## 确定性 Gate",
        "",
        f"- Workspace：`{gates.get('workspace', 'skipped')}`",
        f"- Scope：pre-verification=`{scope.get('pre_verification', 'skipped')}`，"
        f"post-verification=`{scope.get('post_verification', 'skipped')}`，"
        f"pre-review=`{scope.get('pre_review', 'skipped')}`",
        f"- Verification：`{gates.get('verification', 'skipped')}`",
        f"- Risk：`{risk.get('status', 'skipped')}`，"
        f"level=`{risk.get('level') or '未评估'}`，"
        f"recommendation=`{risk.get('recommendation') or '未评估'}`",
        f"- Artifact integrity：`{integrity.get('status', 'invalid')}`",
        f"- Evidence freshness：`{freshness.get('status', 'stale')}`",
    ]
    if integrity.get("issues"):
        lines.append(f"- Integrity issues：`{', '.join(integrity['issues'])}`")
    if freshness.get("issues"):
        lines.append(f"- Freshness issues：`{', '.join(freshness['issues'])}`")
    return lines


def _render_verification(verification: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## 验证结果",
        "",
        f"- 最新受信通过：`{bool(verification.get('trusted_passed'))}`",
        f"- 最新失败：`{bool(verification.get('latest_failed'))}`；"
        f"历史失败：`{bool(verification.get('historical_failures'))}`",
    ]
    checks = verification.get("checks") or []
    if not checks:
        lines.append("- 未发现可展示的受信验证命令结果。")
        return lines
    for item in checks:
        iteration = item.get("iteration")
        prefix = f"iteration {iteration}" if iteration is not None else "iteration 未知"
        lines.append(
            f"- {prefix} / `{str(item.get('status', 'unknown')).upper()}`："
            f"`{item.get('command', '<unknown>')}`"
        )
        details = []
        if item.get("returncode") is not None:
            details.append(f"exit={item['returncode']}")
        if isinstance(item.get("duration_seconds"), (int, float)):
            details.append(f"duration={item['duration_seconds']:.2f}s")
        if details:
            lines.append(f"  - {', '.join(details)}")
    return lines


def _render_review(review: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Reviewer 意见",
        "",
        f"- Verdict：`{review.get('verdict') or '无'}`",
        f"- Summary：{review.get('summary') or '未提供'}",
    ]
    findings = review.get("findings") or []
    if not findings:
        lines.append("- Findings：无。")
    else:
        lines.append("- Findings：")
        for finding in findings:
            lines.extend(
                [
                    f"  - `{finding.get('severity', 'minor')}` "
                    f"{_review_location(finding.get('file'), finding.get('line'))}："
                    f"{finding.get('title', '未命名 finding')}",
                    f"    - 证据：{finding.get('evidence') or '未提供'}",
                    f"    - 建议：{finding.get('recommendation') or '未提供'}",
                ]
            )
    for disclosure in review.get("risk_disclosures") or []:
        locations = "、".join(
            _review_location(item.get("file"), item.get("line"))
            for item in disclosure.get("locations") or []
        )
        lines.extend(
            [
                f"- 高风险 `{disclosure.get('risk_id')}` / "
                f"`{disclosure.get('assessment')}`：{locations or '未提供位置'}",
                f"  - 修改：{disclosure.get('change_summary') or '未提供'}",
                f"  - 证据：{disclosure.get('evidence') or '未提供'}",
                f"  - 剩余风险：{disclosure.get('residual_risk') or '未提供'}",
            ]
        )
    return lines


def _review_location(file: Any, line: Any) -> str:
    path = str(file or "").strip()
    if path and isinstance(line, int) and line > 0:
        return f"`{path}:{line}`"
    if path:
        return f"`{path}`（Reviewer 未提供行号）"
    return "未提供文件或行号"


def _render_bullets(title: str, items: list[str]) -> list[str]:
    lines = ["", f"## {title}", ""]
    lines.extend(f"- {item}" for item in items)
    if not items:
        lines.append("- 未记录；需要人工检查原始 artifact。")
    return lines


def _render_details(summary: dict[str, Any]) -> list[str]:
    lines = _render_bullets("Commit 前 Checklist", summary.get("commit_checklist") or [])
    lines.extend(["", "## 迭代记录", ""])
    for item in summary.get("iterations") or []:
        interruption = (
            f"，interrupted_step=`{item.get('interrupted_step')}`"
            if item.get("lifecycle") == "interrupted"
            else ""
        )
        lines.append(
            f"- 第 {item['iteration']} 轮：lifecycle=`{item.get('lifecycle', 'completed')}`，"
            f"worker=`{item['worker_status']}`，"
            f"verification=`{item.get('verification_status', 'skipped')}`，"
            f"verdict=`{item.get('verdict') or '无'}`，"
            f"findings={item.get('findings_count', 0)}{interruption}"
        )
    if not summary.get("iterations"):
        lines.append("- 尚未产生迭代记录。")
    proposals = summary.get("memory_proposals") or []
    if proposals:
        lines.extend(_render_bullets(
            "可选 Memory Proposal",
            [f"`{item['id']}`：{item['title']}" for item in proposals],
        ))
    decisions = summary.get("decisions") or []
    decision_lines = [
        f"`{item['type']}` / `{item['decision']}`：{item['reason']}（actor: {item['actor']}）"
        for item in decisions
    ]
    lines.extend(_render_bullets("人工决策", decision_lines or ["暂无人工 decision 记录。"]))
    lines.extend(_render_bullets("关键产物", [f"`{item}`" for item in summary.get("key_artifacts") or []]))
    lines.extend(
        _render_bullets(
            "明确边界",
            [
                "Finish 不会自动 commit、push、release。",
                "Finish 不会自动接受 memory proposal。",
                "是否提交或接受经验候选，仍由用户人工决定。",
            ],
        )
    )
    return lines
