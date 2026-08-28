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

Vega 管 ChangeRun、Git Worktree、验证、风险门禁和 Reviewer。宿主会话负责调查、展示计划和处理人工决定；Coding Agent 负责写代码。

## 先调查，再启动

1. 读取目标仓库的 `AGENTS.md`、`.vega.yaml`、相关代码、测试和 Git 状态。
2. 根因、范围或验收还不明确时，只读调查。不要先改代码，也不要把猜测写成事实。
3. 生成两份 JSON：
   - Change Contract：目标、验收、不变量、Non-goals、允许范围、风险授权和必跑验证；
   - Execution Plan：已确认事实、假设、2～4 个粗粒度 Work Item、实现安排和未决问题。
4. 把合同、计划和关键证据展示给用户。没有明确批准，不启动 Worker。

## 启动

```powershell
vega capabilities
vega start --repo . --contract <change-contract.json> --execution-plan <execution-plan.json>
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

默认 `run` 通过 Codex App Server 复用 Worker Thread。只有明确需要一次性短会话时才加 `--fresh-session`；App Server 不可用时 Vega 会直接报错，不会静默换执行路径。

## 看进度和干预

```powershell
vega status --run <run_id>
vega watch --run <run_id> --follow
vega steer --run <run_id> --role worker --text "补充检查这个边界"
vega respond --run <run_id> --interaction <request_id> --decision accept
```

状态和 `watch` 只显示阶段、Work Item、安全事件、变更、验证、风险及待响应请求，不转发模型推理、完整正文或原始命令参数。

- 方向需要微调但合同没变：用 `steer`。
- 合同或执行计划要改：先生成新 revision，再运行 `vega revise`；触及合同字段时重新等待人工批准。
- 响应 JSON 含凭据或其他敏感信息：不要写进 Vega Artifact，改用 `vega takeover` 接管原生会话。
- 只有空闲 Session、没有 active Writer binding 且 Workspace 没有变化时才能 `vega reclaim`。活动 attempt 被接管后先做 Recovery 或 Handoff。

## 按状态处理

- `ready`：再次运行 `vega run --run <run_id>`，执行当前 Work Item 或明确的 Repair。
- `awaiting_approval`：展示 revision 差异，等待批准。
- `needs_human`：读取状态卡、Checkpoint 和失败证据；按问题选择 `retry`、`recover`、`revise`、`handoff` 或停止。
- `finalizing`：重新运行同一个 `vega run`，幂等发布已有可信 Finish。
- `completed`：读取 `agent-final-report.md`，展示完整变更文件、Reviewer 重点、验证、风险和未证明事项，再由用户决定是否提交或创建 PR。

普通 Finding 会生成 Fix Packet 并回到同一个 Worker Thread。Reviewer 使用独立只读 Thread；不要把 Worker 的完整聊天或中间推理转给 Reviewer。

## 中断和换机器

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
