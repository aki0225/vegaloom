from __future__ import annotations

from .agent_task_card import AgentTaskCard


def git_checklist(
    card_path: str,
    changed_files: tuple[str, ...],
    branch: str,
) -> list[str]:
    paths = [*changed_files, card_path]
    add_paths = " ".join(f"`{path}`" for path in paths) or "`<Task Card>`"
    return [
        "人工确认旧 Writer、进程和外部副作用已经停止或已明确标记 blocked",
        "执行 `git status --short` 和 `git diff --check`",
        f"只暂存 WIP 与 Task Card：{add_paths}",
        "执行 `git diff --cached --check`，人工检查完整 staged diff",
        f"人工决定是否在任务分支 `{branch}` commit 和 push",
        "新机器使用 `git pull --ff-only` 后运行 `vega agent resume --repo .`",
    ]


def render_handoff_summary(
    *,
    card: AgentTaskCard,
    card_path: str,
    card_digest: str,
    branch: str,
    changed_files: tuple[str, ...],
    issues: list[str],
) -> str:
    lines = [
        "# Vega Handoff Summary",
        "",
        f"- Task Card：`{card_path}`",
        f"- Task Card SHA-256：`{card_digest}`",
        f"- 分支：`{branch}`",
        f"- Handoff 状态：`{card.handoff_status}`",
        f"- 当前 Work Item：`{card.current_work_item}`",
        f"- WIP 文件：{', '.join(f'`{path}`' for path in changed_files) or '无'}",
        "",
        "## 现场说明",
        "",
        *(
            [f"- 阻断原因：{issue}" for issue in issues]
            if issues
            else ["- 现场可解释，旧 Writer 未保持 active binding。"]
        ),
        "",
        "## 人工 Git 清单",
        "",
        *git_checklist(card_path, changed_files, branch),
        "",
        "Vega 不会自动执行 commit、push、release 或删除文件。",
        "",
    ]
    return "\n".join(lines)
