from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .models import GateResult
from .redaction import redact_text
from .risk_review_reporting import verdict_schema_example
from .risk_review_runtime import (
    render_required_review_pack_lines,
    render_required_review_prompt_rules,
    required_reviews_from_inputs,
)


def render_review_pack(inputs: dict[str, Any]) -> str:
    risk_gate = inputs.get("risk_gate")
    queue_context = inputs.get("review_queue")
    queue_lines: list[str] = []
    if isinstance(queue_context, dict):
        all_changed_files = _string_list(
            queue_context.get("all_changed_files")
        )
        target_files = _string_list(queue_context.get("target_files"))
        queue_lines = [
            "## Review Queue",
            "",
            f"- 当前任务：`{queue_context.get('item_id', 'unknown')}`",
            f"- Candidate SHA：`{queue_context.get('candidate_sha', 'unknown')}`",
            "- 本任务目标文件："
            + "、".join(f"`{path}`" for path in target_files),
            "- 全部变更文件："
            + "、".join(f"`{path}`" for path in all_changed_files),
            "- 这里只裁决当前目标文件；其他文件由同一 Candidate 绑定的后续队列任务覆盖。",
            "",
        ]
    required_review_lines: list[str] = []
    risk_gate_note = [
        f"- ignored 证据覆盖：Reflect `{inputs['source_ignored_coverage_level']}`，当前 "
        f"`{inputs['current_ignored_coverage_level']}`；"
        "`metadata_bounded` 仅表示稳定元数据检测。"
    ]
    if isinstance(risk_gate, dict) and risk_gate.get("status") == "success":
        try:
            result = GateResult.model_validate(risk_gate.get("result"))
        except ValidationError:
            risk_gate_note.append(
                "- 风险门禁结果格式不合法；本次审查不能作为自动通过结论。"
            )
        else:
            risk_gate_note.append(
                f"- 风险门禁：`{result.risk}` / `{result.recommendation}`。"
            )
            if result.recommendation == "human-review":
                risk_gate_note.append(
                    "- 风险门禁要求人工审查；本次 reviewer "
                    "只提供辅助发现，不能替代人工确认。"
                )
            required_review_lines.extend(
                render_required_review_pack_lines(result.required_reviews)
            )
    elif risk_gate is not None:
        risk_gate_note.append(
            "- 风险门禁评估失败；本次审查不能作为自动通过结论。"
        )
    text = "\n".join(
        [
            "# Review Pack",
            "",
            "## 审查边界",
            "",
            "- 这是隔离 reviewer 的输入包，只基于事实材料审查当前 diff。",
            "- 不包含 worker 的完整聊天记录，避免被实现过程污染。",
            "- reviewer 只使用已存在的文件、diff、测试摘要、日志和项目上下文；"
            "不运行会写文件或缓存的命令，也不修改、提交、推送或发布。",
            *(
                [
                    "- 以下证据已截断："
                    + "、".join(
                        f"`{name}`" for name in inputs["truncated_sections"]
                    )
                    + "。reviewer 可以指出已发现的问题，但不得返回 approve。"
                ]
                if inputs["truncated_sections"]
                else []
            ),
            *(
                [
                    "- Review 证据已过期或完整性校验失败："
                    + "、".join(
                        f"`{name}`" for name in inputs["evidence_issues"]
                    )
                    + "。外部 reviewer 不会启动，请重新执行 reflect。"
                    + (
                        "\n- 持久化诊断："
                        + "；".join(inputs["evidence_diagnostics"])
                        if inputs["evidence_diagnostics"]
                        else ""
                    )
                ]
                if inputs["evidence_issues"]
                else []
            ),
            *risk_gate_note,
            "",
            *queue_lines,
            "## 完整变更文件清单",
            "",
            *(
                [f"- `{path}`" for path in inputs["changed_files"]]
                if inputs["changed_files"]
                else ["- 未获得可信变更文件清单。"]
            ),
            "",
            *required_review_lines,
            "## 原始需求 / Agent Brief",
            "",
            inputs["source_brief"] or "- 未找到上游 agent-brief.md。",
            "",
            "## 执行后复盘",
            "",
            inputs["reflection"] or "- 未找到 reflection.md。",
            "",
            "## Diff Summary",
            "",
            inputs["diff_summary"] or "- 未找到 diff-summary.md。",
            "",
            "## Test Summary",
            "",
            inputs["test_summary"] or "- 未提供测试摘要。",
            "",
            "## 项目上下文",
            "",
            inputs["project_context"],
            "",
            "## Full Diff",
            "",
            "```diff",
            inputs["full_diff"].strip() or "<empty>",
            "```",
        ]
    ).rstrip() + "\n"
    return redact_text(text)


def render_review_prompt(inputs: dict[str, Any]) -> str:
    required_reviews = required_reviews_from_inputs(inputs)
    coverage_rule = (
        "- 这是 Review Queue 的一个独立任务；必须逐项检查当前任务的完整变更文件清单，"
        "`reviewed_files` 必须精确列出当前任务文件，不得声称覆盖其他队列任务。"
        if isinstance(inputs.get("review_queue"), dict)
        else "- 必须逐项检查 Review Pack 的完整变更文件清单；"
        "`reviewed_files` 必须精确列出全部变更文件，不得省略，也不得加入清单外路径。"
    )
    text = "\n".join(
        [
            "# 任务：隔离代码审查",
            "",
            "你是独立 reviewer。你没有 worker 的聊天上下文，"
            "只能基于下面的 review pack 审查当前变更。",
            "",
            "硬性要求：",
            "- 只读审查，只使用已存在的文件、diff、测试摘要、日志和项目上下文；"
            "不要自行运行验证命令或补造证据。",
            "- 不要运行测试、构建、安装依赖、格式化、代码生成或其他可能写入文件/"
            "缓存的命令，也不要修改、提交、推送、发布、删除或执行破坏性操作。",
            "- 重点找真实 bug、遗漏测试、需求不满足、项目规则违反和安全风险。",
            coverage_rule,
            "- 如果证据不足，不要强行 approve，返回 needs_human。",
            *render_required_review_prompt_rules(required_reviews),
            "- 最终只能输出一个 JSON 对象，不要包 Markdown 代码块。",
            "",
            "JSON schema：",
            "```json",
            json.dumps(
                verdict_schema_example(required_reviews),
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            render_review_pack(inputs),
        ]
    ).rstrip() + "\n"
    return redact_text(text)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
