from __future__ import annotations

from pathlib import Path

from .models import ProjectKnowledge, ProjectProfile
from .project_config import load_project_config, render_project_config_summary
from .project_knowledge import load_project_knowledge
from .project_profile import build_project_profile
from .redaction import redact_text


def build_project_context(
    workspace: Path,
    repo_path: Path,
    input_text: str,
    related_paths: list[str] | None = None,
    *,
    tracked_only: bool = True,
) -> str:
    """构建 worker/reviewer 共用的项目上下文。

    这里故意只合并“项目规则、画像、accepted memory”，不包含 worker 聊天记录，
    这样主会话、worker 和隔离 reviewer 能共享稳定项目知识，但仍保持审查上下文隔离。
    """
    profile = build_project_profile(workspace, repo_path, tracked_only=tracked_only)
    knowledge = load_project_knowledge(
        workspace,
        repo_path,
        input_text,
        related_paths or [],
        tracked_only=tracked_only,
    )
    config = load_project_config(repo_path, tracked_only=tracked_only)
    return render_project_context(profile, knowledge, render_project_config_summary(config))


def render_project_context(
    profile: ProjectProfile,
    knowledge: ProjectKnowledge,
    project_policy: str | None = None,
) -> str:
    lines = [
        "# 项目上下文",
        "",
        "## 使用边界",
        "",
        "- 这是 Vega 为当前仓库生成的稳定项目上下文。",
        "- worker 和 reviewer 都可以读取它，但它不包含 worker 的完整聊天记录。",
        "- 稳定规则优先进入 AGENTS.md；只有跨任务局部经验才考虑显式 Memory Proposal。",
        "",
        "## 项目画像",
        "",
        f"- 项目：`{profile.repo_name}`",
        f"- 路径：`{profile.repo_path}`",
        f"- 技术栈：{_inline_list(profile.tech_stack)}",
        f"- 包管理器：{_inline_list(profile.package_managers)}",
        f"- 入口文件：{_inline_list(profile.entrypoints)}",
        f"- 关键目录：{_inline_list(profile.key_directories)}",
        f"- 配置文件：{_inline_list(profile.config_files)}",
        "",
        "## 推荐验证命令",
        "",
    ]
    if profile.test_commands or profile.lint_commands:
        for command in profile.test_commands:
            lines.append(f"- 测试：`{command}`")
        for command in profile.lint_commands:
            lines.append(f"- 静态检查：`{command}`")
    else:
        lines.append("- 未识别自动验证命令；修改后需要人工补充最小验证。")

    lines.extend(["", "## Runtime 策略", "", project_policy or "- 使用默认 Vega 策略。"])

    lines.extend(["", "## AGENTS.md 规则", ""])
    if knowledge.agents_instructions:
        for item in knowledge.agents_instructions:
            lines.extend(
                [
                    f"### {item.path}",
                    "",
                    f"- 作用域：`{item.scope}`",
                    "",
                    "```md",
                    item.content.strip(),
                    "```",
                    "",
                ]
            )
    else:
        lines.extend(["- 未发现目标仓库 `AGENTS.md`。", ""])

    if knowledge.memory_hits:
        lines.extend(["## 可选的已接受经验", ""])
        for hit in knowledge.memory_hits:
            tags = ", ".join(hit.tags) if hit.tags else "无"
            paths = ", ".join(hit.paths) if hit.paths else "未限定"
            lines.extend(
                [
                    f"### {hit.title}",
                    "",
                    f"- ID：`{hit.proposal_id}`",
                    f"- 标签：{tags}",
                    f"- 路径：{paths}",
                    f"- 内容：{hit.content}",
                    "",
                ]
            )
    return redact_text("\n".join(lines).rstrip() + "\n")


def write_project_context(
    run_dir: Path,
    workspace: Path,
    repo_path: Path,
    input_text: str,
    related_paths: list[str] | None = None,
    *,
    tracked_only: bool = True,
) -> None:
    run_dir.joinpath("project-context.md").write_text(
        build_project_context(
            workspace,
            repo_path,
            input_text,
            related_paths or [],
            tracked_only=tracked_only,
        ),
        encoding="utf-8",
    )


def _inline_list(items: list[str]) -> str:
    if not items:
        return "未识别"
    return "、".join(f"`{item}`" for item in items)
