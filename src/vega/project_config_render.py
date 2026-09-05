from __future__ import annotations

from .project_config import ProjectConfigCheckResult
from .redaction import redact_text


def render_project_config_check(result: ProjectConfigCheckResult) -> str:
    lines = [
        "# Vega Config Check",
        "",
        f"- 仓库：`{redact_text(result.repo_path)}`",
        f"- 配置文件：`{redact_text(result.source_path or '未发现')}`",
        f"- 状态：`{result.status}`",
        f"- Provider：`{result.provider or '未确定'}`（CLI：{_render_provider_cli_status(result.provider_cli_status)}；登录状态未验证）",
        "",
        "## 问题",
        "",
    ]
    if result.issues:
        for issue in result.issues:
            lines.extend(
                [
                    f"- [{issue.severity.upper()}] `{issue.code}`：{redact_text(issue.message)}",
                    f"  - 证据：{redact_text(issue.evidence or '无')}",
                ]
            )
    else:
        lines.append("- 未发现配置问题。")

    lines.extend(["", "## 显式验证命令", ""])
    if result.verification_commands:
        lines.extend(
            f"- `{redact_text(command)}`" for command in result.verification_commands
        )
    else:
        lines.append("- 未登记固定验证命令；自然语言 Change 需要先在 `.vega.yaml` 中配置并提交。")
    return redact_text("\n".join(lines).rstrip() + "\n")


def _render_provider_cli_status(status: str) -> str:
    return {
        "available": "已找到",
        "missing": "未找到",
        "unverified": "未检查",
    }.get(status, "未检查")
