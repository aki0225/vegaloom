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
description: "当用户要求用 Vega/loop 做 bug 修复或需求开发时使用。模糊任务先只读调查、提交固定 Plan 并等待人工确认；确认后再生成 brief，指导主会话实现并完成验证、隔离 review 与 finish。"
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

## 修改前协议

1. 先判断任务边界是否已经明确。只有同时具备明确行为、修改范围、验证方式，且用户明确要求
   直接执行时，才可以跳过重复调查。
2. 其余任务先只读调查。可以读取项目规则、代码、配置、历史和测试，也可以运行不会修改
   工作区的查询命令；不得修改文件，不得启动 Worker，不得先运行 `vega loop`。
3. 调查后使用以下固定结构输出 Plan：

```markdown
## User Goal
## Non-goals
## Observed Facts
## Hypotheses
## Proposed Scope
## Verification
## Risk Areas
## Unresolved Decisions
```

4. `Observed Facts` 只写已经由文件、代码或命令确认的事实并注明来源；未验证的根因只能放在
   `Hypotheses`。
5. `Proposed Scope` 必须写清允许读取和修改的范围，不能覆盖 `AGENTS.md`、`.vega.yaml`
   或用户明确约束。
6. 把 Plan 交给用户确认。只有用户明确批准，或用户一开始已经给出精确范围、验收标准并明确
   要求直接执行，才能进入修改。
7. `vega plan` 面向大目标的 scope/phase 规划，不替代这里的日常调查与修改前确认。

## 推荐流程

1. 先完成“修改前协议”，不要把任务描述直接当成已经确认的根因或修改范围。
2. 获得批准后判断任务类型：
   - bug：`vega loop bug --repo . --text "<用户需求>" --mode assist`
   - feature：`vega loop feature --repo . --text "<用户需求>" --mode assist`
   - 已满足直接执行条件的小任务可用：`vega do feature --repo . --text "<用户需求>"`
3. 运行 `vega status --run <loop_run_id>`，确认状态是 `waiting_for_worker`，并确认
   `workspace-baseline.json`、`worker-prompt.md` 和 `project-context.md` 都已生成。
4. 如果状态是 `workspace_baseline_dirty`、`workspace_baseline_unavailable` 或
   `workspace_head_changed`，不要执行 Worker，也不要 `loop continue`；先清理或稳定仓库，
   再创建新的 loop。
5. 当前主会话按 `worker-prompt.md` 完成最小必要修改；也可以调用宿主原生子代理，但只把
   代码与工作区结果交给 Vega，不把子代理完整聊天传给 Reviewer。
6. 继续 loop；默认会自动执行 project profile 识别出的验证命令：
   - `vega loop continue --repo . --run <loop_run_id>`
   - 如果已有外部测试日志，可显式传入：`vega loop continue --repo . --run <loop_run_id> --test-log test.log`
7. 查看输出：
   - 如果有 `fix-prompt.md`，按它继续修复后再次 `loop continue`。
   - 如果有 `final-report.md`，用它整理交付结论。
   - 如果 gate/reviewer 要求人工判断，先向用户说明风险。
8. 无论前一步是否已经生成 `final-report.md`，都运行
   `vega finish --run <loop_run_id> --json`，从现有证据生成最终交付结论。
9. 把 Finish 结果带回主会话时，必须同时展示完整变更事实和 Reviewer 重点：
   - 先列出 `first_screen.actual_changes.changed_files` 的全部路径，不得让模型筛掉任何文件。
   - 展示 Reviewer 文件覆盖率、重点文件、其他已变更项；未被 Reviewer 标记为重点不代表
     文件不重要。
   - 优先使用宿主的 Changes / Diff 视图展示代码。需要终端 Diff 时，分别运行
     `git diff --cached --no-ext-diff --unified=3` 和
     `git diff --no-ext-diff --unified=3`，不能用其中一条代替完整 staged/unstaged 事实。
   - 小 Diff 直接展示全部代码块；大 Diff 可以分批展示，但必须列出全部文件，并明确说明
     已展示与尚未展示的范围，不得静默省略。新增未跟踪文件需要单独打开或展示其完整内容。
10. 用户明确批准继续或交付时，记录 decision：
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
    "vega-agent": """---
name: "vega-agent"
description: "当用户明确要求使用 Vega Supervisor Agent 完成长任务、可恢复编码任务或受控 Coding Agent 执行时使用。主会话只读调查、提交单 Work Item Plan、等待人工批准，再驱动真实 Codex Worker、验证、独立 Reviewer 和可信 Finish。"
---

# Vega Supervisor Agent Skill

## 什么时候使用

- 用户明确说“用 Vega Agent”“用 Supervisor Agent”或调用 `$vega-agent`。
- 任务需要长时间执行、暂停恢复、跨会话接手或保留可审计证据。
- 用户希望主会话负责计划与控制，外部 Worker 负责写代码，Reviewer 保持独立只读。

简单的一次性修改仍优先使用 `$vega-loop`。纯审查使用 `$vega-review`。

## V1 边界

- V1 只派发一个未完成 Work Item，不自动连续执行多 Work Item。
- 主会话负责只读调查和 Plan，不增加 Planner、Researcher、Memory Agent 或 Provider SDK。
- Worker 使用现有 Codex Adapter；不要把 Worker 完整聊天或推理交给 Reviewer。
- Vega 不自动 commit、push、release、部署、回滚、删除文件或接受长期 Memory。
- 状态只保存在 Agent run 的 Task Card、State、Checkpoint、Trace 和 Task Brief 中，
  不依赖当前聊天记录。

## 启动前检查

1. 读取目标仓库的 `AGENTS.md`、`.vega.yaml`、相关代码、测试和 Git 状态。
2. 调查阶段只读，不修改目标文件，不启动 Worker。
3. 运行 `vega agent capabilities`。只有 `supervisor_runtime=true` 且 `langgraph=true`
   才能继续；缺少依赖时报告问题，不自动安装。
4. 如果目标仓库已有无法解释的修改，先交给用户判断；不要把旧 Diff 混入新 Agent run。
5. Plan 输入文件放在宿主临时目录或项目已忽略的专用临时目录，不放在仓库根目录，
   不提交到 Git。

## Plan 合同

调查后生成一个未批准的 JSON Plan。V1 只保留一个 `pending` Work Item：

```json
{
  "schema_version": 1,
  "task_id": "task-short-name",
  "goal_revision": 1,
  "plan_revision": 1,
  "user_goal": "与启动命令完全一致的用户目标",
  "non_goals": ["明确不做的事项"],
  "success_conditions": ["可验证的成功条件"],
  "observed_facts": ["已由文件或命令确认的事实"],
  "hypotheses": ["仍需 Worker 验证的假设"],
  "unresolved_decisions": [],
  "work_items": [
    {
      "schema_version": 1,
      "work_item_id": "W1",
      "objective": "当前唯一可执行目标",
      "allowed_paths": ["src/example.py", "tests/test_example.py"],
      "forbidden_paths": [".env"],
      "verification": ["python -m pytest tests/test_example.py -q"],
      "risk_notes": ["需要人工关注的风险"],
      "depends_on": [],
      "status": "pending"
    }
  ],
  "approved": false
}
```

`observed_facts` 不能混入推测；`allowed_paths`、`forbidden_paths` 和验证命令必须来自调查证据。

## 推荐流程

1. 把 Plan 和关键调查事实展示给用户，不隐藏未决问题和风险。
2. 创建 Agent run：
   - `vega agent start --repo . --plan <plan.json> --text "<与 Plan 一致的用户目标>"`
3. 记录输出的 `<agent_run>`，运行：
   - `vega status --run <agent_run>`
   - `vega watch --run <agent_run> --no-follow`
4. 只有用户明确批准当前 Plan 后才运行：
   - `vega agent approve --run <agent_run> --actor human`
5. 执行当前 Work Item：
   - `vega agent run --run <agent_run> --timeout 900`
6. 执行期间可以在另一个终端跟随安全低频事件：
   - `vega watch --run <agent_run> --follow`
7. 每次命令返回后读取 `vega status --run <agent_run> --json`，按真实 phase 处理：
   - `completed`：展示全部 changed files、关键 Diff、验证、Risk、Reviewer 和 Finish。
   - `finalizing`：运行 `vega agent finalize --run <agent_run>`，采用已有可信 Core Finish。
   - `ready` 且允许 `repair`：说明 Reviewer 或验证结果后，只允许再执行一次
     `vega agent run`；第二次仍未完成就停止并交还人工。
   - `awaiting_approval`：新证据要求 replan；重新只读调查、提交 revision，并再次等待批准。
   - `needs_human`：停止自动执行，展示阻断、Checkpoint、未知副作用和人工选项。
   - `stopped`：保留现场，除非用户明确要求，否则不要恢复。

## 人工控制与恢复

- 查询：`vega agent status --run <agent_run>` 或 `vega status --run <agent_run>`。
- 新约束：`vega agent steer --run <agent_run> --instruction "<约束>"`。
- 暂停：`vega agent pause --run <agent_run> --reason "<原因>"`。
- 停止：`vega agent stop --run <agent_run> --reason "<原因>"`。
- 本机恢复：先确认无 active Writer，再运行
  `vega agent resume-local --run <agent_run>`。
- 跨机器准备：仅在用户明确要求时运行
  `vega agent checkpoint --run <agent_run> --handoff --reason "<原因>"`；
  Vega 只生成材料，不执行 Git 操作。

## 主会话展示要求

- 默认展示阶段、Work Item、Worker、changed files、Checkpoint、Verification、Risk、
  Reviewer、Finish 和下一步。
- 列出全部 changed files，不能只展示模型认为重要的文件。
- 小 Diff 直接展示完整代码；大 Diff 分批展示，但必须声明已展示和未展示范围。
- 只展示安全低频事件，不输出模型正文、隐藏推理、完整命令参数或凭据。
- `Worker completed`、`Reviewer approve` 或 LangGraph `END` 都不等于成功；
  只有可信 Core Finish 为 `ready_to_commit` 且 Agent phase 为 `completed` 才能建议人工提交。
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
