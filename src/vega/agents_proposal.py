from __future__ import annotations

from pathlib import Path

from .models import BriefInput, ProjectKnowledge


def write_agents_md_proposals(run_dir: Path, brief_input: BriefInput, knowledge: ProjectKnowledge) -> None:
    run_dir.joinpath("agents-md-proposals.md").write_text(
        render_agents_md_proposals(brief_input, knowledge),
        encoding="utf-8",
    )


def render_agents_md_proposals(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    mode_name = "Bug 修复" if brief_input.mode == "bug" else "需求开发"
    lines = [
        "# AGENTS.md 建议",
        "",
        "本文件只提出建议，不会自动修改目标仓库 `AGENTS.md`。",
        "",
        "## 建议新增到 AGENTS.md 的长期规则",
        "",
    ]

    if knowledge.missing_agents_md:
        lines.extend(
            [
                "- 建议新增仓库级 `AGENTS.md`，记录技术栈、测试命令、禁止动作和交付标准。",
                "  - 原因：当前未发现项目级规则文件，后续 AI 执行容易缺少稳定约束。",
            ]
        )
    else:
        lines.extend(
            [
                f"- 建议检查现有 `AGENTS.md` 是否覆盖 `{mode_name}` 场景的验证命令和禁止动作。",
                "  - 原因：brief 运行只读取规则，不会自动保证规则完整。",
            ]
        )

    lines.extend(["", "## 可选的局部经验候选", ""])
    if knowledge.memory_hits:
        lines.append("- 已命中相关 accepted memory；先核对是否仍适用于当前代码和测试。")
    else:
        lines.append("- 本次没有命中历史 memory；不要因此强制制造 proposal。")
    lines.append("- 只有出现有证据、跨任务可复用且不适合作为稳定规范的坑位时，才使用 reflect --lesson。")

    lines.extend(
        [
            "",
            "## 可考虑新增 eval/check 的规则",
            "",
            "- 如果本次任务暴露了可自动验证的约束，建议新增到项目测试或 Vega eval。",
            "- 如果只是一次性业务判断，不建议提升为全局检查。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
