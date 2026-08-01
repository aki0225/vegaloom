from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AdapterInitResult:
    target: str
    created_files: list[Path]
    skipped_files: list[Path]


CODEX_SKILLS: dict[str, str] = {
    "vega-loop": """---
name: "vega-loop"
description: "当用户要求用 Vega/loop 做 bug 修复或需求开发时使用。生成 brief，指导主会话实现，完成后运行 reflect、gate、隔离 review，并根据 verdict 继续修复或交付。"
---

# Vega Loop Skill

## 什么时候使用

- 用户说“用 loop 做”“走 Vega 流程”“按 agent loop 修 bug / 做功能”。
- 用户希望保留可复盘产物，而不是只让当前会话直接改代码。
- 用户希望 worker 和 reviewer 上下文隔离。

## 默认策略

- 默认使用 `assist`，由当前主会话负责实现，Vega 负责上下文、复盘、门禁和隔离审查。
- Vega 是控制面；当前主会话或宿主原生子代理是执行面，不引入额外的 Multi-Worker 调度。
- 只有用户明确要求小任务全自动时，才使用 `--mode auto`。
- 不要自动 commit、push、release。
- 不要自动接受 memory proposal。
- 遇到高风险 gate 结果时，先回报用户，不要继续 auto。
- 不要把 Worker 的完整聊天记录或中间推理交给 Reviewer。

## 推荐流程

1. 判断任务类型：
   - bug：`vega loop bug --repo . --text "<用户需求>" --mode assist`
   - feature：`vega loop feature --repo . --text "<用户需求>" --mode assist`
   - 边界清晰的小任务也可用日常入口：`vega do feature --repo . --text "<用户需求>"`
2. 先运行 `vega status --run <loop_run_id>`，确认状态是 `waiting_for_worker`，并确认
   `workspace-baseline.json`、`worker-prompt.md` 和 `project-context.md` 都已生成。
3. 如果状态是 `workspace_baseline_dirty`、`workspace_baseline_unavailable` 或
   `workspace_head_changed`，不要执行 Worker，也不要 `loop continue`；先清理或稳定仓库，
   再创建新的 loop。
4. 当前主会话按 `worker-prompt.md` 完成最小必要修改；也可以调用宿主原生子代理，但只把
   代码与工作区结果交给 Vega，不把子代理完整聊天传给 Reviewer。
5. 继续 loop；默认会自动执行 project profile 识别出的验证命令：
   - `vega loop continue --repo . --run <loop_run_id>`
   - 如果已有外部测试日志，可显式传入：`vega loop continue --repo . --run <loop_run_id> --test-log test.log`
6. 查看输出：
   - 如果有 `fix-prompt.md`，按它继续修复后再次 `loop continue`。
   - 如果有 `final-report.md`，用它整理交付结论。
   - 如果 gate/reviewer 要求人工判断，先向用户说明风险。
7. 用户明确批准继续或交付时，记录 decision：
   - `vega decision approve --run <run_id> --type finish --reason "<原因>"`

## 状态查询

- 最近 loop：`vega latest --kind loop`
- 机器可读状态：`vega latest --kind loop --json`
- 指定 run：`vega status --run <run_id>`

## 交付要求

交付时说明：

- 做了什么。
- 为什么这样做。
- 如何验证。
- reviewer / gate 结论。
- 是否有 decision 记录。
- 剩余风险。
- 是否建议沉淀 memory 或 AGENTS.md。
""",
    "vega-review": """---
name: "vega-review"
description: "当用户要求审查当前 Vega run、当前 diff 或 AI 修改结果时使用。优先运行 risk gate，再生成 review-pack，并使用隔离 codex exec reviewer 或输出 review prompt。"
---

# Vega Review Skill

## 什么时候使用

- 用户说“审一下”“review 一下”“看 diff 有没有问题”。
- 用户已经完成一轮实现，需要隔离 reviewer。
- 用户不想手动复制 brief、diff、测试日志给另一个会话。

## 默认流程

1. 如果还没有 reflect run，先运行：
   - `vega reflect --repo . --run <brief_run_id> --test-log <log> --note "<简短备注>"`
2. 运行风险门禁：
   - `vega gate --repo . --run <reflect_run_id>`
3. 根据 gate 结果处理：
   - `self-check`：可以主会话自检，但仍可按需 review。
   - `isolated-review`：运行隔离 reviewer。
   - `human-review`：先向用户报告风险，不要继续 auto；如用户确认继续，记录 `vega decision approve --run <run_id> --type gate --reason "<原因>"`。
4. 运行隔离审查：
   - `vega review --repo . --run <reflect_run_id> --runner codex-exec`
5. 如果当前环境不适合调用外部 runner：
   - `vega review-pack --repo . --run <reflect_run_id>`
   - 读取 `review-prompt.md`，交给干净 reviewer 会话。

## 输出解读

- `review-verdict.json` 是结构化结论。
- `review-findings.md` 是人类可读 findings。
- `approve`：可整理最终交付，但仍需人工决定是否 commit。
- `request_changes`：把 findings 转成修复任务。
- `needs_human`：不要强行通过，先向用户说明阻塞。

## 禁止动作

- reviewer 只读，不修改代码。
- 不要自动 commit、push、release。
- 不要把 worker 的完整聊天记录塞给 reviewer。
""",
}

CODEX_SKILLS_ROOT = Path(".agents") / "skills"


def init_adapter(repo_path: Path, target: str, force: bool = False) -> AdapterInitResult:
    normalized = target.strip().lower()
    if normalized != "codex":
        raise ValueError(f"暂不支持的 adapter target：{target}")
    try:
        repo = repo_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("无法确认 adapter 目标仓库路径") from exc
    if not repo.is_dir():
        raise ValueError("adapter 目标仓库必须是目录")

    created: list[Path] = []
    skipped: list[Path] = []
    targets = [
        (repo / CODEX_SKILLS_ROOT / skill_name / "SKILL.md", content)
        for skill_name, content in CODEX_SKILLS.items()
    ]

    # 必须先校验整批目标，避免后一个危险链接让前一个文件已经部分落盘。
    for path, _ in targets:
        _resolve_adapter_write_path(repo, path)

    for path, content in targets:
        resolved_path = _resolve_adapter_write_path(repo, path)
        if resolved_path.exists() and not force:
            skipped.append(path)
            continue

        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        # mkdir 后再次解析逻辑目标；边界在检查期间发生变化时必须停止。
        resolved_path = _resolve_adapter_write_path(repo, path)
        if resolved_path.exists() and not force:
            skipped.append(path)
            continue
        resolved_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        created.append(path)
    return AdapterInitResult(target=normalized, created_files=created, skipped_files=skipped)


def _resolve_adapter_write_path(repo: Path, path: Path) -> Path:
    relative_path = path.relative_to(repo).as_posix()
    try:
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法确认 adapter 写入路径边界：{relative_path}") from exc
    if not resolved_path.is_relative_to(repo):
        raise ValueError(f"adapter 写入路径越过目标仓库边界：{relative_path}")
    return resolved_path


def render_adapter_init_summary(result: AdapterInitResult) -> str:
    lines = [f"adapter 初始化完成：{result.target}", ""]
    if result.created_files:
        lines.extend(["已写入：", *[f"- {path}" for path in result.created_files], ""])
    if result.skipped_files:
        lines.extend(
            [
                "已存在，未覆盖：",
                *[f"- {path}" for path in result.skipped_files],
                "",
                "如需覆盖，请重新运行并添加 `--force`。",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
