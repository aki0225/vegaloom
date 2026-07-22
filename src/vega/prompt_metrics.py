from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptMetrics(BaseModel):
    """记录真实发送给 runner 的 prompt 规模，不伪造精确 token 数。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["worker", "reviewer"]
    chars: int = Field(ge=0)
    utf8_bytes: int = Field(ge=0)
    lines: int = Field(ge=0)
    max_chars: int = Field(ge=1)
    status: Literal["within_budget", "exceeded"]
    over_by_chars: int = Field(ge=0)
    sections: dict[str, int] = Field(default_factory=dict)

    @property
    def exceeded(self) -> bool:
        return self.status == "exceeded"


def measure_prompt(
    prompt: str,
    *,
    role: Literal["worker", "reviewer"],
    max_chars: int,
    sections: dict[str, str] | None = None,
) -> PromptMetrics:
    chars = len(prompt)
    return PromptMetrics(
        role=role,
        chars=chars,
        utf8_bytes=len(prompt.encode("utf-8")),
        lines=len(prompt.splitlines()),
        max_chars=max_chars,
        status="exceeded" if chars > max_chars else "within_budget",
        over_by_chars=max(0, chars - max_chars),
        sections={name: len(text) for name, text in (sections or {}).items()},
    )


def write_prompt_metrics(
    run_dir: Path,
    prefix: str,
    metrics: PromptMetrics,
    *,
    write_text: Callable[[Path, str], None] | None = None,
) -> None:
    writer = write_text or _write_text
    writer(
        run_dir.joinpath(f"{prefix}-metrics.json"),
        json.dumps(metrics.model_dump(), ensure_ascii=False, indent=2) + "\n",
    )
    writer(
        run_dir.joinpath(f"{prefix}-metrics.md"),
        render_prompt_metrics(metrics),
    )


def render_prompt_metrics(metrics: PromptMetrics) -> str:
    lines = [
        "# Prompt 指标",
        "",
        f"- 角色：`{metrics.role}`",
        f"- 字符数：`{metrics.chars}`",
        f"- UTF-8 字节数：`{metrics.utf8_bytes}`",
        f"- 行数：`{metrics.lines}`",
        f"- 字符预算：`{metrics.max_chars}`",
        f"- 状态：`{metrics.status}`",
        f"- 超出字符数：`{metrics.over_by_chars}`",
        "",
        "## 分段规模",
        "",
    ]
    if metrics.sections:
        lines.extend(f"- `{name}`：`{chars}` chars" for name, chars in metrics.sections.items())
    else:
        lines.append("- 未提供分段指标。")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 这里记录的是确定性的字符/字节规模，不把字符数伪装成精确 token 数。",
            "- runner 输出中的 token usage 仍受模型、工具调用和执行路径影响，应单独对比。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_context_budget_report(run_dir: Path, prefix: str, metrics: PromptMetrics) -> str:
    filename = f"{prefix}-context-budget-report.md"
    run_dir.joinpath(filename).write_text(
        "\n".join(
            [
                "# 上下文预算报告",
                "",
                f"- 角色：`{metrics.role}`",
                f"- 实际字符数：`{metrics.chars}`",
                f"- 字符预算：`{metrics.max_chars}`",
                f"- 超出字符数：`{metrics.over_by_chars}`",
                "",
                "## 结论",
                "",
                "- prompt 已超过项目声明的上下文预算，本次不会启动外部 runner。",
                "- Vega 不会静默裁掉需求、项目规则、测试证据或 diff 后继续执行。",
                "- 请缩小任务范围、精简项目规则，或人工确认后调整 `.vega.yaml` 预算。",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )
    return filename


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
