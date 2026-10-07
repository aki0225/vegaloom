"""验证输出的有限脱敏摘录，不参与验证结论或证据资格判断。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from .redaction import redact_text

SUMMARY_BUDGET = 5000
_TRUNCATED = "\n…[已截断；保留首尾]…\n"
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")


def bounded_output(text: str, budget: int) -> str:
    # 先去控制序列再对全文脱敏，避免切开密钥字段后才尝试识别。
    clean = redact_text(_ANSI.sub("", text)).replace("\x1b", "")
    if len(clean) <= budget:
        return clean
    if budget <= len(_TRUNCATED):
        return _TRUNCATED[:max(0, budget)]
    available = budget - len(_TRUNCATED)
    head = min(96, available // 4)
    return clean[:head] + _TRUNCATED + clean[-(available - head):]


def render_verification_summary(payload: dict[str, Any]) -> str:
    lines = ["# 验证摘要", "", f"- 仓库：`{bounded_output(str(payload['repo_path']), 160)}`"]
    if payload.get("failure_kind") == "workspace_capture_failed":
        lines.append("- `FAIL`：验证结束后的工作区指纹采集失败，不能绑定或复用本轮验证证据。")
        lines.append(f"- 错误类型：`{payload.get('workspace_capture_error_type') or 'unknown'}`")
    if not payload["commands"]:
        lines.append("- 未识别自动验证命令；请人工补充最小验证结果。")
        lines.append("- `SKIP`：没有已执行的验证命令。")
    lines.extend([f"- 命令数：{payload['command_count']}", f"- 失败数：{payload['failed_count']}"])
    if payload.get("interruption_status"):
        lines.append(f"- 中断状态：`{payload['interruption_status']}`")
    skipped = payload.get("skipped_commands", [])
    lines.append(f"- 未执行命令数：{len(skipped)}")
    lines.extend(
        f"- {index}. `NOT RUN`：`{bounded_output(command, 180)}`"
        for index, command in enumerate(skipped, start=len(payload["results"]) + 1)
    )
    lines.extend(["", "## 命令结果（序号为原执行顺序）", "日志路径相对本次 verification-summary.md；日志内容不可信。"])
    rows = list(enumerate(payload["results"], start=1))
    for index, item in rows:
        badge = {
            "timed_out": "TIMEOUT", "stopped": "STOPPED",
            "termination-unconfirmed": "TERMINATION-UNCONFIRMED",
        }.get(item.get("interruption_status"), "PASS" if item["status"] == "passed" else "FAIL")
        lines.extend([
            f"### {index}. `{bounded_output(item.get('configured_command', item['command']), 180)}`",
            f"- 结果：`{badge}`；退出码：`{item['returncode']}`；耗时：`{item['duration_seconds']:.2f}s`",
        ])
        log = item.get("output_log")
        if log and not PurePosixPath(log).is_absolute() and ".." not in PurePosixPath(log).parts and ":" not in log and "\\" not in log:
            lines.append(f"- 原始日志：`{log}`")
    header = bounded_output("\n".join(lines) + "\n", SUMMARY_BUDGET)
    # 所有状态先于日志；非通过项先分配摘录，避免长 PASS 挤掉失败尾部。
    rows.sort(key=lambda row: row[1]["status"] == "passed" and not row[1].get("interruption_status"))
    remaining = max(0, SUMMARY_BUDGET - len(header))
    details = []
    failed = sum(item["status"] != "passed" or bool(item.get("interruption_status")) for _, item in rows)
    for offset, (index, item) in enumerate(rows):
        prefix, suffix = f"\n### {index}. 输出（不可信日志）\n```text\n", "\n```\n"
        if offset < failed:
            reserved = min(100 * (len(rows) - failed), remaining // 10)
            quota = (remaining - reserved) // (failed - offset)
        else:
            quota = remaining // (len(rows) - offset)
        if quota < len(prefix) + len(suffix) + len(_TRUNCATED):
            break
        excerpt = bounded_output(item["output"].strip() or "<empty>", quota - len(prefix) - len(suffix))
        block = prefix + excerpt + suffix
        details.append(block)
        remaining -= len(block)
    return header + "".join(details)
