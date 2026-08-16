from __future__ import annotations

import json
from typing import Any

from .redaction import redact_text

REPORT_BINDING_START = "<!-- vega-scope-gate-binding\n"
REPORT_BINDING_END = "\n-->"


def render_scope_gate_report(result: Any, result_sha256: str) -> str:
    lines = [
        "# 精确路径范围门禁报告",
        "",
        f"- iteration：`{result.iteration:02d}`",
        f"- 阶段：`{result.phase}`",
        f"- 状态：`{result.status}`",
        f"- 启动 HEAD：`{result.expected_head_sha or '未绑定'}`",
        f"- 当前 HEAD：`{result.current_head_sha or '无法读取'}`",
        f"- 对比基线：`{result.comparison_base_sha or '当前工作区'}`",
        "- 对比路径："
        + (
            "、".join(f"`{path}`" for path in result.comparison_paths)
            if result.comparison_paths
            else "`全部 tracked 路径`"
        ),
        f"- scope policy SHA-256：`{result.scope_policy_sha256 or '未绑定'}`",
        f"- index flags SHA-256：`{result.index_flags_sha256 or '无法读取'}`",
        f"- committed tracked 文件数：`{len(result.committed_changed_files)}`",
        f"- staged tracked 文件数：`{len(result.staged_changed_files)}`",
        f"- unstaged tracked 文件数：`{len(result.unstaged_changed_files)}`",
        f"- untracked 文件数：`{len(result.untracked_changed_files)}`",
        "",
        "## 规则",
        "",
        "- allowed_paths："
        + (
            "、".join(f"`{item}`" for item in result.allowed_paths)
            if result.allowed_paths
            else "未配置"
        ),
        "- forbidden_paths："
        + (
            "、".join(f"`{item}`" for item in result.forbidden_paths)
            if result.forbidden_paths
            else "未配置"
        ),
        "",
        "## 当前工作区变更",
        "",
    ]
    if result.changed_files:
        lines.extend(f"- `{path}`" for path in result.changed_files)
    else:
        lines.append("- 无。")
    if result.unsafe_index_paths:
        lines.extend(["", "## 不安全 index 标记", ""])
        lines.extend(f"- `{path}`" for path in result.unsafe_index_paths)
    lines.extend(["", "## 结论", ""])
    if result.status == "skipped":
        lines.append("- 未配置精确路径范围；为兼容既有项目，本轮未限制工作区变更。")
    elif result.status == "success":
        lines.append("- 当前工作区变更全部符合精确路径范围，可进入当前阶段的后续流程。")
    elif result.violations:
        lines.append("- 检测到越界工作区变更；Vega 已停止当前阶段的后续流程。")
        for violation in result.violations:
            patterns = (
                "、".join(f"`{item}`" for item in violation.matched_patterns)
                if violation.matched_patterns
                else "无匹配 allowlist"
            )
            lines.append(f"- `{violation.code}`：`{violation.path}`；规则：{patterns}")
    else:
        lines.append("- scope gate 无法给出可放行结论；Vega 已 fail-closed。")
        if result.failure_code:
            lines.append(f"- failure code：`{result.failure_code}`")
        lines.append(f"- 诊断：{result.diagnostic or '未提供'}")

    binding = {
        "schema_version": 1,
        "status": result.status,
        "iteration": result.iteration,
        "phase": result.phase,
        "result_sha256": result_sha256,
    }
    lines.extend(
        [
            "",
            "## 证据绑定",
            "",
            REPORT_BINDING_START.rstrip(),
            json.dumps(binding, ensure_ascii=False, sort_keys=True),
            REPORT_BINDING_END,
        ]
    )
    return redact_text("\n".join(lines).rstrip() + "\n")
