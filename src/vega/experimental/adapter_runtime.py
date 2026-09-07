from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AdapterInitResult:
    target: str
    created_files: list[Path]
    skipped_files: list[Path]


CODEX_SKILLS: dict[str, str] = {
    "vega-agent": """---
name: "vega-agent"
description: "当任务需要先调查、批准边界，再由 Vega 调度 Coding Agent 持续实现、验证和独立审查时使用。"
---

# Vega Agent

Vega 管 ChangeRun、Git Worktree、验证、风险门禁和 Reviewer。宿主会话展示计划、处理人工决定和汇报结果；Worker 由 Vega 启动。

## 选择入口

在目标仓库根目录执行以下命令，后续操作留在同一目录。先读取适用的 `AGENTS.md`、`.vega.yaml` 和 Git 状态。已有任务时先看 `status`、`explain`，确认目标再继续；不要为了绕过失败新建任务。

- 还没有明确计划：使用自然语言入口，由 Vega 的只读 Planner 调查。宿主不必先做一遍相同调查。
- 主会话已经调查并形成计划：使用显式合同入口，沿用已确认的事实与边界，不再调用 Planner 重做。
- Work Item 按实际可检查的改动拆分；小任务可以只有一个，不为凑数量拆步骤。

## 自然语言任务

目标项目需要在已提交的 `.vega.yaml` 中登记真实的 `verification.commands`。先确认命令能在项目中执行；缺少配置时向用户说明，不能自行猜测并授权验证命令。

宿主调用使用 `--json`，避免等待终端输入：

```powershell
vega config check --repo . --change
vega change "描述目标或 Bug 现象" --json
```

从返回值读取真实 run_id。`awaiting_approval` 表示调查完成、仍待批准，不能报告成任务完成。展示当前 Plan Card、Contract 和 Execution Plan 中的范围、验收、风险和未决问题。

`vega change` 带文本会新建任务；继续任务使用 `vega change --run <run_id> --json`，不要重复传入目标。多个活动任务时明确选择，不猜最新一个。

## 已有计划

把主会话已有计划整理为两份 JSON：Change Contract 记录目标、验收、不变量、Non-goals、范围、风险授权和必跑验证；Execution Plan 记录事实、假设、Work Item、实现安排和未决问题。

```powershell
vega start --repo . --contract <change-contract.json> --execution-plan <execution-plan.json>
```

转换后的合同与计划也要展示给用户。讨论中的假设不是事实，之前对方向的同意也不能替代对实际执行边界的确认。

## 批准和执行

默认人工模式下，两条入口都在实际材料核对并得到用户明确批准后继续：

```powershell
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

首次选择 Claude Code 时，自然语言入口的预检和 `change` 都加 `--provider claude`；显式合同入口在首次 `run` 选择 Provider。同一任务后续沿用绑定的 Provider。

默认 Codex 通过 App Server 复用 Worker Thread。只有明确需要一次性短会话时才加 `--fresh-session`；App Server 不可用时不会静默换执行路径。

## 看进度和干预

```powershell
vega capabilities
vega status --run <run_id>
vega explain --run <run_id>
vega watch --run <run_id> --follow
vega steer --run <run_id> --role worker --text "补充检查这个边界"
```

状态和 `watch` 只显示阶段、Work Item、安全事件、变更、验证、风险及待响应请求，不转发模型推理、完整正文或原始命令参数。

- 方向需要微调但合同没变：用 `steer`。
- 合同或执行计划要改：先生成新 revision，再运行 `vega revise`；触及合同字段时重新等待人工批准。
- 高级 `run` 仍持有活动 Codex Turn 时，核对原始请求并得到用户授权后才可 `vega respond`。`change` 已停止 attempt 并关闭的 pending 请求不能再响应。
- 响应 JSON 含凭据或其他敏感信息：不要写进 Vega Artifact，改用 `vega takeover` 接管原生会话。
- 只有空闲 Session、没有 active Writer binding 且 Workspace 没有变化时才能 `vega reclaim`。活动 attempt 被接管后先做 Recovery 或 Handoff。

## 按状态处理

- `ready`：再次运行 `vega run --run <run_id>`，执行当前 Work Item 或明确的 Repair。
- `awaiting_approval`：展示 revision 差异，等待批准。
- `needs_human`：先读 `explain` 的原因和安全动作，再按需检查 Checkpoint 和失败证据。不要把 `change` 当万能恢复命令。
- `finalizing`：重新运行同一个 `vega run`，幂等发布已有可信 Finish。
- `completed`：读取 `agent-final-report.md`，展示完整变更文件、Reviewer 重点、验证、风险和未证明事项；同时给出 `status` 中的代码目录、任务分支、累计 Diff 基线和 Candidate，供用户检查与交付。

普通 Finding 会生成 Fix Packet 并回到同一个 Worker Thread。Reviewer 使用独立只读 Thread；不要把 Worker 的完整聊天或中间推理转给 Reviewer。

## 中断和换机器

没有活动 Writer 时用 `pause` 暂停；运行中需要结束执行时用 `stop`，确认进程已停止后再交接。

```powershell
vega pause --run <run_id> --reason "暂时离开"
vega handoff --run <run_id> --reason "换机器继续"
```

`handoff` 只生成 Task Card 和本机恢复材料，不替用户提交或推送任务分支。人工检查 WIP 与 Task Card 后，在任务分支 commit、push；新机器拉取该分支，再运行：

```powershell
vega resume --repo .
```

Provider Thread ID 只用于本机续接。换机器时以任务分支、Candidate SHA、Change Contract、Execution Plan 和 Task Card 恢复，不依赖旧聊天记录。

## 固定边界

- 一个 ChangeRun 同时只有一个可写 Worker。
- Reviewer 只读，不继承 Worker Thread。
- Verification、Risk 和 Reviewer 证据不足时 fail-closed。
- Git 自动化只限于受管 Worktree 中的本地 Candidate/Checkpoint Commit；用户分支、push、merge、release、部署、回滚、删除文件和长期 Memory 仍由人工控制。
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
