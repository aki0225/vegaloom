from __future__ import annotations

from typing import Any

from .models import GateResult, RequiredReviewHit, ReviewVerdict
from .redaction import redact_text
from .review_contract import normalize_review_path
from .review_coverage import build_review_file_coverage
from .runner import RunnerResult
from .risk_review import render_required_review_gate_lines
from .risk_review_runtime import (
    empty_findings_line,
    render_final_risk_disclosure_lines,
    render_review_risk_disclosure_lines,
    risk_disclosure_schema_example,
)


def render_gate_report(result: GateResult) -> str:
    lines = [
        "# Risk Gate Report",
        "",
        f"- 风险等级：`{result.risk}`",
        f"- 建议：`{result.recommendation}`",
        f"- scope：`{result.scope_profile or 'default'}`",
        "",
        "## 变更文件",
        "",
    ]
    if result.changed_files:
        lines.extend(f"- `{item}`" for item in result.changed_files)
    else:
        lines.append("- 未发现变更文件。")
    lines.extend(render_required_review_gate_lines(result.required_reviews))
    lines.extend(["", "## 门禁原因", ""])
    for reason in result.reasons:
        lines.extend(
            [
                f"### {reason.code}",
                "",
                f"- 严重级别：`{reason.severity}`",
                f"- 说明：{reason.message}",
                f"- 证据：{reason.evidence or '无'}",
                "",
            ]
        )
    lines.extend(["## 建议解释", ""])
    if result.recommendation == "self-check":
        lines.append("- 可以由主会话做自检；仍建议保留 reflect/report 证据。")
    elif result.recommendation == "isolated-review":
        lines.append("- 建议运行隔离 reviewer，但不需要直接升级到人工阻塞。")
    else:
        lines.append("- 建议人工判断后再继续 auto loop 或合并变更。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_review_checklist() -> str:
    return "\n".join(
        [
            "# Review Checklist",
            "",
            "- 需求是否被真实满足，是否存在遗漏路径。",
            "- diff 是否引入明显行为回归、边界错误或兼容性风险。",
            "- 测试日志是否覆盖核心路径；缺测试时必须指出风险。",
            "- 是否违反 AGENTS.md、accepted memory 或本次 brief 的约束。",
            "- 是否存在输入校验、权限、安全、敏感信息或破坏性操作风险。",
            "- 是否逐项检查完整变更文件清单，并在 reviewed_files 中精确列出全部路径。",
            "- 命中 risk.required_reviews 时，是否逐类覆盖全部命中文件并说明证据和剩余风险。",
            "- reviewer 只读且仅使用现有证据；禁止运行测试、构建、安装依赖、格式化、代码生成或其他会写文件/缓存的命令，也不修改、提交或发布。",
        ]
    ).rstrip() + "\n"


def render_runner_output(result: RunnerResult) -> str:
    lines = ["# Runner Output", "", f"- status: `{result.status}`"]
    if result.termination_unconfirmed:
        lines.append("- termination_unconfirmed: `true`")
    if result.command:
        lines.extend(["", "## Command", "", "```text", " ".join(result.command), "```"])
    if result.error:
        lines.extend(["", "## Error", "", result.error])
    if result.termination_unconfirmed:
        lines.extend(
            [
                "",
                "## Output",
                "",
                "owned process tree 终止未确认，未读取或复制 runner 输出。",
            ]
        )
    else:
        lines.extend(["", "## Output", "", "```text", result.output.strip(), "```"])
    return redact_text("\n".join(lines).rstrip() + "\n")


def render_review_findings(
    verdict: ReviewVerdict,
    required_reviews: list[RequiredReviewHit] | None = None,
) -> str:
    lines = [
        "# Review Findings",
        "",
        f"- 结论：`{verdict.verdict}`",
        f"- 摘要：{verdict.summary}",
        "",
    ]
    lines.extend(
        render_review_risk_disclosure_lines(
            verdict.risk_disclosures,
            required_reviews or [],
        )
    )
    lines.extend(["## Findings", ""])
    if verdict.findings:
        for index, finding in enumerate(verdict.findings, start=1):
            location = f"{finding.file}:{finding.line}" if finding.file else "未指定位置"
            lines.extend(
                [
                    f"### {index}. {finding.title}",
                    "",
                    f"- 严重级别：`{finding.severity}`",
                    f"- 位置：`{location}`",
                    f"- 证据：{finding.evidence or '未提供'}",
                    f"- 建议：{finding.recommendation or '未提供'}",
                    "",
                ]
            )
    else:
        lines.append(empty_findings_line(verdict))
    lines.extend(["", "## Reviewed Files", ""])
    if verdict.reviewed_files:
        lines.extend(f"- `{path}`" for path in verdict.reviewed_files)
    else:
        lines.append("- reviewer 未声明已检查的变更文件。")
    lines.extend(["", "## Checked Items", ""])
    if verdict.checked_items:
        lines.extend(f"- {item}" for item in verdict.checked_items)
    else:
        lines.append("- reviewer 未列出检查项。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def build_finish_review_section(
    verdict: ReviewVerdict | None,
    changed_files: list[str],
    *,
    changed_files_source: str,
) -> dict[str, Any]:
    review = (
        verdict.model_dump()
        if verdict
        else {
            "verdict": None,
            "summary": "",
            "findings": [],
            "risk_disclosures": [],
            "reviewed_files": [],
            "checked_items": [],
        }
    )
    coverage = build_review_file_coverage(
        changed_files,
        review.get("reviewed_files") or [],
    )
    coverage["available"] = changed_files_source != "unavailable"
    if not coverage["available"]:
        coverage["complete"] = False
    focus_paths = {
        normalize_review_path(str(item.get("file") or ""))
        for item in review.get("findings") or []
    }
    focus_paths.update(
        normalize_review_path(str(location.get("file") or ""))
        for disclosure in review.get("risk_disclosures") or []
        for location in disclosure.get("locations") or []
    )
    priority_files = [
        path for path in changed_files if normalize_review_path(path) in focus_paths
    ]
    review["coverage"] = coverage
    review["priority_files"] = priority_files
    review["other_changed_files"] = [
        path for path in changed_files if path not in priority_files
    ]
    return review


def render_finish_review_section(review: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Reviewer 意见",
        "",
        f"- Verdict：`{review.get('verdict') or '无'}`",
        f"- Summary：{review.get('summary') or '未提供'}",
    ]
    coverage = review.get("coverage") or {}
    if coverage:
        if coverage.get("available") is False:
            lines.append(
                "- Reviewer 文件覆盖：`unavailable`；"
                "可信变更文件清单不可用，不能判断覆盖完整性。"
            )
        else:
            coverage_status = "complete" if coverage.get("complete") else "incomplete"
            lines.append(
                f"- Reviewer 文件覆盖：`{coverage.get('reviewed_count', 0)}/"
                f"{coverage.get('expected_count', 0)}`，状态=`{coverage_status}`"
            )
        if coverage.get("missing_files"):
            lines.append(
                "- 未覆盖文件："
                + "、".join(f"`{path}`" for path in coverage["missing_files"])
            )
        if coverage.get("unexpected_files"):
            lines.append(
                "- 清单外文件："
                + "、".join(f"`{path}`" for path in coverage["unexpected_files"])
            )
    priority_files = review.get("priority_files") or []
    if priority_files:
        lines.append(
            "- Reviewer 重点文件："
            + "、".join(f"`{path}`" for path in priority_files)
        )
    else:
        lines.append("- Reviewer 重点文件：未标记；这不代表变更文件不重要。")
    other_changed_files = review.get("other_changed_files") or []
    if other_changed_files:
        lines.append(
            "- 其他已变更项："
            + "、".join(f"`{path}`" for path in other_changed_files)
            + "；未被 Reviewer 标记为重点不代表这些文件不重要。"
        )
    findings = review.get("findings") or []
    if not findings:
        lines.append("- Findings：无。")
    else:
        lines.append("- Findings：")
        for finding in findings:
            lines.extend(
                [
                    f"  - `{finding.get('severity', 'minor')}` "
                    f"{_finish_review_location(finding.get('file'), finding.get('line'))}："
                    f"{finding.get('title', '未命名 finding')}",
                    f"    - 证据：{finding.get('evidence') or '未提供'}",
                    f"    - 建议：{finding.get('recommendation') or '未提供'}",
                ]
            )
    for disclosure in review.get("risk_disclosures") or []:
        locations = "、".join(
            _finish_review_location(item.get("file"), item.get("line"))
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


def _finish_review_location(file: Any, line: Any) -> str:
    path = str(file or "").strip()
    if path and isinstance(line, int) and line > 0:
        return f"`{path}:{line}`"
    if path:
        return f"`{path}`（Reviewer 未提供行号）"
    return "未提供文件或行号"


def verdict_schema_example(
    required_reviews: list[RequiredReviewHit],
) -> dict[str, Any]:
    return {
        "verdict": "approve | request_changes | needs_human",
        "summary": "简短中文结论",
        "findings": [
            {
                "severity": "blocker | major | minor | suggestion",
                "file": "相对路径或空字符串",
                "line": 0,
                "title": "问题标题",
                "evidence": "基于 diff/test/规则的证据",
                "recommendation": "建议修复方式",
            }
        ],
        "risk_disclosures": risk_disclosure_schema_example(required_reviews),
        "reviewed_files": ["src/example.py", "tests/test_example.py"],
        "checked_items": ["需求覆盖", "测试覆盖", "项目规则", "安全风险"],
    }


def verdict_output_schema(
    required_reviews: list[RequiredReviewHit],
) -> dict[str, Any]:
    """生成 Codex structured output 使用的严格 Reviewer schema。"""
    schema = ReviewVerdict.model_json_schema()
    _require_all_output_fields(schema)
    disclosures = schema["properties"]["risk_disclosures"]
    if required_reviews:
        required_count = len(required_reviews)
        disclosures["minItems"] = required_count
        disclosures["maxItems"] = required_count
    else:
        disclosures["maxItems"] = 0
    return schema


def _require_all_output_fields(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _require_all_output_fields(item)
        return
    if not isinstance(value, dict):
        return
    value.pop("default", None)
    properties = value.get("properties")
    if isinstance(properties, dict):
        value["required"] = list(properties)
        value["additionalProperties"] = False
    for item in value.values():
        _require_all_output_fields(item)


def render_final_review_details(verdict: ReviewVerdict) -> list[str]:
    lines = render_final_risk_disclosure_lines(verdict.risk_disclosures)
    if verdict.findings:
        lines.extend(["## 剩余 Findings", ""])
        lines.extend(
            f"- [{finding.severity}] {finding.title}：{finding.recommendation}"
            for finding in verdict.findings
        )
    else:
        lines.append(empty_findings_line(verdict))
    return lines
