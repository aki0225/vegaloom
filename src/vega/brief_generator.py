from __future__ import annotations

import re
from pathlib import Path

from .models import BriefInput, ProjectKnowledge


def extract_related_paths(text: str) -> list[str]:
    backticked = re.findall(r"`([^`]+)`", text)
    path_like = re.findall(r"(?:[\w.-]+/)+[\w./-]+", text.replace("\\", "/"))
    candidates = [*backticked, *path_like]
    return _dedupe([item.strip().replace("\\", "/") for item in candidates if _looks_like_path(item)])


def write_common_brief_artifacts(run_dir: Path, brief_input: BriefInput, knowledge: ProjectKnowledge) -> None:
    run_dir.joinpath("agent-brief.md").write_text(
        render_agent_brief(brief_input, knowledge),
        encoding="utf-8",
    )


def write_bug_artifacts(run_dir: Path, brief_input: BriefInput, knowledge: ProjectKnowledge) -> None:
    run_dir.joinpath("repro-plan.md").write_text(render_repro_plan(brief_input, knowledge), encoding="utf-8")
    run_dir.joinpath("root-cause-hypotheses.md").write_text(
        render_root_cause_hypotheses(brief_input, knowledge),
        encoding="utf-8",
    )
    run_dir.joinpath("regression-check.md").write_text(
        render_regression_check(brief_input, knowledge),
        encoding="utf-8",
    )


def write_feature_artifacts(run_dir: Path, brief_input: BriefInput, knowledge: ProjectKnowledge) -> None:
    run_dir.joinpath("feature-spec.md").write_text(render_feature_spec(brief_input, knowledge), encoding="utf-8")
    run_dir.joinpath("implementation-plan.md").write_text(
        render_implementation_plan(brief_input, knowledge),
        encoding="utf-8",
    )
    run_dir.joinpath("acceptance-criteria.md").write_text(
        render_acceptance_criteria(brief_input, knowledge),
        encoding="utf-8",
    )
    run_dir.joinpath("risk.md").write_text(render_feature_risk(brief_input, knowledge), encoding="utf-8")


def render_agent_brief(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    mode_title = "Bug 修复" if brief_input.mode == "bug" else "需求开发"
    sections = [
        f"# Agent Brief：{mode_title}",
        "",
        "## 当前目标",
        "",
        brief_input.text.strip(),
        "",
        "## 项目规则入口",
        "",
    ]
    if knowledge.agents_instructions:
        sections.extend(f"- `{item.path}`，作用域 `{item.scope}`。" for item in knowledge.agents_instructions)
    else:
        sections.append("- 未发现 `AGENTS.md`，需要先保守识别项目规范和验证命令。")

    if knowledge.memory_hits:
        sections.extend(["", "## 可选的历史经验 / 踩坑", ""])
        sections.extend(f"- {hit.title}：{hit.content}" for hit in knowledge.memory_hits)

    sections.extend(["", "## 建议工作方式", ""])
    if brief_input.mode == "bug":
        sections.extend(
            [
                "1. 先复现或缩小复现条件，再定位根因。",
                "2. 优先找最近变更、错误日志、边界输入和配置差异。",
                "3. 修复时补回归测试或最小验证。",
            ]
        )
    else:
        sections.extend(
            [
                "1. 先确认需求边界和不做事项，再拆实现步骤。",
                "2. 优先保持接口契约和现有项目规范一致。",
                "3. 每个子改动都要对应验收标准和验证方式。",
            ]
        )

    sections.extend(
        [
            "",
            "## 禁止动作",
            "",
            "- 不自动提交、push 或发布。",
            "- 不硬编码密钥或真实凭证。",
            "- 不绕过项目 `AGENTS.md`、测试命令和用户明确约束。",
            "- 遇到工作区/分支阻塞时先回报，不要擅自绕过。",
            "",
            "## 推荐交付",
            "",
            "- 说明做了什么、为什么这样做、如何验证。",
            "- 说明剩余风险和是否建议沉淀 memory / AGENTS.md。",
        ]
    )
    return "\n".join(sections).rstrip() + "\n"


def render_repro_plan(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "复现计划",
        [
            "提取用户反馈中的触发条件、输入、环境和实际表现。",
            "在最小范围内复现问题；无法复现时记录缺失信息。",
            "对照相关 AGENTS.md 规则和历史 memory，优先排查已知坑位。",
            "确认修复后补充回归验证。",
        ],
        brief_input,
        knowledge,
    )


def render_root_cause_hypotheses(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "根因假设",
        [
            "输入边界或空值处理不完整。",
            "前后端接口契约不一致。",
            "状态流转、缓存或异步时序导致实际行为偏离预期。",
            "近期变更引入兼容性问题。",
        ],
        brief_input,
        knowledge,
    )


def render_regression_check(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "回归检查",
        [
            "覆盖用户反馈的原始场景。",
            "覆盖相邻边界条件和失败路径。",
            "运行项目 AGENTS.md 或现有文档中要求的最小验证命令。",
            "确认没有引入自动提交、发布或配置污染。",
        ],
        brief_input,
        knowledge,
    )


def render_feature_spec(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "功能规格",
        [
            "明确用户目标、主要场景和非目标。",
            "列出输入、输出、权限、异常和兼容性要求。",
            "识别受影响模块、接口和数据结构。",
        ],
        brief_input,
        knowledge,
    )


def render_implementation_plan(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "实现计划",
        [
            "先做最小可验证骨架，再补边界处理。",
            "保持现有架构和项目规范，不引入不必要的新框架。",
            "每个实现步骤都绑定一个验证点。",
        ],
        brief_input,
        knowledge,
    )


def render_acceptance_criteria(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "验收标准",
        [
            "核心用户路径可用。",
            "异常路径有明确反馈。",
            "相关自动化测试或最小手工验证通过。",
            "交付说明包含风险和后续建议。",
        ],
        brief_input,
        knowledge,
    )


def render_feature_risk(brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    return _markdown(
        "风险清单",
        [
            "需求边界不清导致过度实现。",
            "接口契约变化影响已有调用方。",
            "若命中已接受经验，需要核对它是否仍适用于当前代码和测试。",
            "缺少验证命令导致回归风险不可见。",
        ],
        brief_input,
        knowledge,
    )


def _markdown(title: str, bullets: list[str], brief_input: BriefInput, knowledge: ProjectKnowledge) -> str:
    lines = [f"# {title}", "", "## 输入摘要", "", brief_input.text.strip(), "", "## 建议", ""]
    lines.extend(f"- {item}" for item in bullets)
    if knowledge.memory_hits:
        lines.extend(["", "## 可选的经验命中", ""])
        lines.extend(f"- {hit.title}" for hit in knowledge.memory_hits)
    return "\n".join(lines).rstrip() + "\n"


def _looks_like_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    if not normalized or normalized.startswith("http"):
        return False
    return "/" in normalized or "." in Path(normalized).name


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
