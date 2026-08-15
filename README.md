<div align="center">

<img src="docs/assets/vega-hero.jpg" width="100%" alt="Vega：一个写，一个审，独立会话共享证据">

# Vega

<h3>本地优先的 AI 编码与验证编排框架</h3>

<p>
  <a href="https://github.com/aki0225/vegaloom/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/aki0225/vegaloom/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Baseline-v0.1.5-4fb8d8?style=for-the-badge" alt="v0.1.5">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-F8FAFC?style=for-the-badge" alt="MIT License"></a>
</p>

**[在线展示](https://aki0225.github.io/vegaloom/)** ·
**[核心设计](#核心设计)** ·
**[核心能力](#核心能力)** ·
**[安装](#安装)** ·
**[快速开始](#快速开始)** ·
**[Codex 接入](#codex-接入)** ·
**[关键行为](#关键行为)** ·
**[文档](#文档)** ·
**[定位与边界](#定位与边界)**

</div>

Vega 不追求堆叠复杂的 Multi-Agent 架构。它专注于将代码修改、确定性验证与独立评审分流，
让 AI 编码的每一步都有明确的输入边界、验证证据和退出条件。

<p align="center">
  <img src="docs/assets/vega-pipeline.svg" width="100%" alt="Vega 任务流水线：task 到 report，worker 与 reviewer 使用独立会话，失败 fail-closed 交还人工">
</p>

<p align="center"><sub>写与审使用独立会话；验证失败或证据不足时，Vega 停止自动执行并交还人工。</sub></p>

## 核心设计

### 双角色会话隔离（Role Segregation）

Worker（编写）与 Reviewer（评审）使用彼此独立的新会话。Reviewer 仅读取任务目标、项目规则、
代码 Diff 及运行证据，不继承 Worker 的历史对话与中间推理上下文。

从输入机制上切断 AI “自编自审”的上下文继承。*（注：本框架基于会话级上下文隔离，
非系统或容器级安全沙箱。）*

### 确定性指标拦截（Deterministic Gating）

不盲信模型的口头结论。代码变更必须通过项目本机的验证命令，例如 pytest、eslint 和静态检查。
即使 Reviewer 给出 `approve`，验证未通过的任务也不能标记为成功，不允许模型结论静默覆盖工程错误。

### Fail-Closed 现场保留（State Preservation）

遇到验证失败、执行超时、Provider 异常或证据冲突时，Vega 立即停止后续自动执行，保留当前运行状态、
执行 Trace、验证报告和未提交的 Diff，不进行自动回滚、commit 或 push，交由人工接管。

## 核心能力

- 从任务、`AGENTS.md`、项目画像和 `.vega.yaml` 编译执行上下文。
- 支持 bug、feature 的人工协作 `assist` 与显式自动化 `auto` 流程。
- `assist` 在生成 Worker Prompt 前封存可校验的工作区基线，避免把旧改动归因给本轮任务。
- 运行项目自己的测试、静态检查和其他确定性验证。
- 使用独立只读 reviewer 会话审查 diff、测试证据和项目规则，不传递 worker 完整对话。
- 根据变更路径、diff 规模和预算输出风险等级与审查建议。
- 在失败、中断或证据不足时保存 state、trace、报告和人工接管入口。
- 不自动 commit、push、release，也不自动接受长期 Memory。

## 安装

要求 Python `>=3.11` 和 Git。只有使用自动 worker 或隔离 reviewer 时才需要已安装并登录
Codex CLI。

命名约定：`Vega` 是产品名，`vegaloom` 是公开仓库、Python distribution 和发布制品名，
`vega` 是 Python 导入包与 CLI 命令。

稳定用户入口是本文和产品契约记录的 `vega` CLI。Python 程序化接口只承诺
`vega.__version__`；`vega` 下的其他模块属于内部实现，允许随核心精简移动，不提供旧导入路径
兼容层。`vega.experimental.*` 更不承诺跨版本稳定，不能作为默认成功语义的扩展点。

```powershell
git clone https://github.com/aki0225/vegaloom.git
cd vegaloom

python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\Activate.ps1
vega --version
```

## 快速开始

推荐由当前主会话或人工负责实现，Vega 负责收集证据、执行验证并启动隔离审查：

如果用户只描述了现象、根因和修改范围尚不明确，先按
[Plan-first 与修改前确认协议](docs/PLAN-FIRST-PROTOCOL.md)做只读调查、提交固定 Plan 并等待
确认。只有边界和验收已经明确，且用户要求直接执行时，才跳过重复调查。

```powershell
vega config check --repo .
vega loop bug --repo . --text "修复导出按钮无响应" --mode assist
vega latest --kind loop
vega loop continue --repo . --run <run_id>
```

首次启动 loop 前先处理 `config check` 的 warning：项目策略应进入明确的准备提交；Python
`src` layout 的 pytest 命令应确保导入当前 checkout；Windows 正式 Pilot 应在干净副本
checkout 前冻结行尾策略。warning 只暴露准备风险，不执行验证命令，也不等同于运行验收通过。

启动 `assist` 前应先清理 staged 与 unstaged tracked diff。Vega 会先写入并校验
`workspace-baseline.json`，确认基线可用后才生成 `worker-prompt.md`。当前主会话或宿主工具的
原生子代理按 Prompt 实现后，再运行 `loop continue`；Vega 依据真实工作区和验证证据继续，
不采信 Worker 的口头完成结论。

如果基线不完整、已有 tracked diff 或 HEAD 在初始化期间变化，Vega 不会生成 Worker Prompt，
也不会创建 iteration。应保留该 run 作为失败证据，清理或稳定仓库后新建 loop，而不是强行
continue。

后续命令应在同一个 workspace 中执行。边界清晰的小任务可以使用默认启用 auto 的 `do`；
如需人工实现，仍可显式传入 `--mode assist`：

```powershell
vega do feature --repo . --text "新增批量导入用户功能"
```

当 Codex CLI 持续输出可解析 JSONL 时，Vega 会在 stderr 显示 worker/reviewer 当前回合、
命令、文件修改、计划或工具调用的安全事件名称和已用时间。实时提示不包含原始命令、文件路径、
命令输出、模型正文、推理内容或工具参数；完整原始输出仍先脱敏，再写入 run artifacts。
如果外部 CLI 没有及时输出或执行超时，Vega 不会伪造进度，而是按 timeout 与 fail-closed
规则保留现场并交还人工。

实验性长任务入口把大目标拆成显式 checkpoint。当前只允许自动执行一个 checkpoint，
结束后必须停在证据边界，不会继续调度下一阶段：

```powershell
vega goal start --repo . --input goal.md --scope refactor
vega goal step --run <goal_run> --text "完成第一阶段的明确修改与验证"
vega goal run --run <goal_run> --max-checkpoints 1 --max-iterations 5 --runner-timeout 3600
vega goal status --run <goal_run>
```

`goal run` 复用普通 auto loop 的 Worker、确定性验证、风险门禁和独立 Reviewer。child run
创建后会写入 Goal 状态；可在另一个终端运行 `vega watch --run <child_run> --follow`
查看不含模型正文、推理、原始命令参数和敏感路径的安全阶段事件。child 失败、状态损坏或
证据不足时，Goal 写出 `checkpoint-blocked.md` 并转为 `needs_human`。

`--runner-timeout` 控制单次 Worker 或 Reviewer 外部进程，范围为 60 到 3600 秒；一个
checkpoint 最多 5 轮，因此控制器可以覆盖由多个长模型回合、验证和审查组成的数小时任务。
这不代表单个模型调用会稳定运行数小时，也不代表无人值守自动串联多个 checkpoint。

控制 CLI 或机器中断后，Goal 会保留 checkpoint 与唯一绑定的 child。确认原进程已退出后，
按同一证据链恢复：

```powershell
vega goal reconcile --run <goal_run>
vega recover --run <child_run> --reason "控制进程中断"
vega loop continue --run <child_run> --repo .
vega goal reconcile --run <goal_run>
```

`goal reconcile` 只锁定并重新校验已绑定 child，不启动新 Worker，不自动重试或替换 child。
当前实验仍不自动串联多个 checkpoint，也不自动 commit、push、回滚或写长期 Memory。

查看运行状态并生成交付结论：

```powershell
vega status --run <run_id>
vega finish --run <run_id>
vega finish --run <run_id> --json
```

`finish-summary.json.first_screen.actual_changes.changed_files` 保留本轮可信 Git 证据中的完整
变更文件清单；`review.coverage`、`priority_files` 和 `other_changed_files` 分别展示 Reviewer
声明的文件覆盖、带 finding/风险位置的重点文件，以及其余实际变更。Reviewer 的重点排序只用于
帮助人工导航，不能过滤或替代完整变更事实。

在 Codex 等宿主中优先使用 Changes / Diff 视图查看代码。需要终端复核时，应分别检查 staged
与 unstaged Diff；新增未跟踪文件还需从 `git status --short` 中识别并单独打开：

```powershell
git diff --cached --no-ext-diff --unified=3
git diff --no-ext-diff --unified=3
git status --short
```

只需要只读检查和报告时，可以使用兼容的 Inspection Loop：

```powershell
vega run engineering-change --task examples/tasks/check-vega-runtime-docs.md --repo .
```

`vega do/loop` 使用 `LoopAutomationRuntime`，是当前日常 Coding Harness 主线；
`vega run engineering-change` 使用 `EngineeringChangeRuntime`，保留为 YAML 驱动的只读基线。

## Codex 接入

需要让 Codex 主会话按 Skill 调用 Vega 时，在目标仓库显式初始化：

```powershell
vega adapters init codex --repo .
```

命令会生成仓库级 Skill：

```text
.agents/skills/vega-loop/SKILL.md
.agents/skills/vega-review/SKILL.md
```

在该仓库的新 Codex 任务中可通过 `$vega-loop` 或 `$vega-review` 显式调用。Skill 只提供
Vega 工作流说明；`$vega-loop` 会在模糊任务下先要求只读调查、固定 Plan 和修改前人工确认。
Skill 不安装 hook、不修改 Codex 全局配置，也不会自行启动自动 worker。
初始化默认不覆盖已有文件；`--force` 只覆盖新的 `.agents/skills` 目标，不删除或改写历史
`.codex/skills` 文件。是否将生成的仓库级 Skill 纳入版本控制，由目标项目自行决定。

## Claude Code 接入

Claude Code 主会话复用同一份
[Plan-first 与修改前确认协议](docs/PLAN-FIRST-PROTOCOL.md#7-claude-code-使用方式)，并在批准后
使用 `assist` 进入 Vega 判断链。Claude Code 可以实现 `worker-prompt.md`，但同一会话不能
替代独立 Reviewer。本阶段不增加 Claude Code 原生 adapter 或自动 Runner。

## 关键行为

- 确定性验证高于模型结论；测试失败时 reviewer 的 `approve` 不能把运行变成成功。
- assist 的工作区基线、根状态和 trace 使用内容哈希绑定；缺失、篡改或不一致时拒绝 continue。
- auto 首轮不会接管已有 tracked diff，避免把历史改动错误归因给本轮 worker。
- staged 与 unstaged 变更都会进入审查证据，不使用可能相互抵消的净差异代替。
- 高风险路径、超预算变更或明确的 `human-review` 不会被 AI reviewer 自动放行。
- 可配置的支付、数据库、并发等命名高风险会由 Reviewer 逐类披露，但最终仍由人工确认。
- 证据缺失、过期或相互不一致时 fail-closed，并交还人工判断。
- Vega 不会自动提交、推送、发布、删除文件或写入长期 Memory。

## 文档

| 想了解 | 文档 |
|---|---|
| 全部文档及状态 | [文档导航](docs/README.md) |
| 当前 Supervisor Agent 实施计划 | [Supervisor Agent V1 计划](docs/VEGA-SUPERVISOR-AGENT-V1-PLAN.md) |
| Gate 2C 首次运行记录 | [Supervisor Agent Gate 2C](docs/SUPERVISOR-AGENT-GATE-2C-PLAN.md) |
| Gate 2C R2 修正协议 | [Supervisor Agent Gate 2C R2](docs/SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md) |
| 调查、Plan 与修改前人工确认 | [PLAN-FIRST-PROTOCOL](docs/PLAN-FIRST-PROTOCOL.md) |
| 完整使用流程 | [USAGE-WALKTHROUGH](docs/USAGE-WALKTHROUGH.md) |
| 产品定位、非目标与成功语义 | [PRODUCT-CONTRACT](docs/PRODUCT-CONTRACT.md) |
| 当前演进路线与下一步 | [ROADMAP](docs/ROADMAP.md) |
| v0.1.5 发布摘要 | [RELEASE-SUMMARY-0.1.5](docs/RELEASE-SUMMARY-0.1.5.md) |
| 安装、验收与发布前检查 | [RELEASE-CHECKLIST](docs/RELEASE-CHECKLIST.md) |
| Runtime、配置、证据链与风险门禁 | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| 实验性长任务 Goal 与 checkpoint 边界 | [LONG-RUNNING-GOALS](docs/LONG-RUNNING-GOALS.md) |
| Assurance 逐项验证记录 | [assurance-validation](eval/assurance-validation.md) |
| v0.1 范围与取舍 | [MVP-SCOPE](docs/MVP-SCOPE.md) |
| 真实 Issue 上的运行记录与边界 | [real-world-runs](eval/real-world-runs.md) |
| 工作区与验证规范 | [WORKSPACE-HYGIENE](docs/WORKSPACE-HYGIENE.md) |

## 定位与边界

- Vega 面向个人和小团队的 AI 辅助研发流程治理，不是通用 Agent 框架或多 Agent 平台。
- Vega 是外围 Harness，不替代 Codex、Claude Code、Cursor 等编码工具。
- Vega 的本地策略、证据链和 reviewer 隔离不等同于操作系统级安全沙箱。
- `loop` 默认使用 `assist`；只有显式选择 `auto` 或 `do` 才启动外部 worker。
- Goal、Memory proposal、adapters 和 `vega agent` 是可选能力，不扩大核心 loop 的成功条件。
- 当前稳定基线为 `v0.1.5`。它补齐 Plan-first、Finish 第一屏、Reviewer 覆盖与显式
  Worker 重跑的 fail-closed 恢复边界；Goal P1 仍是人工触发的实验能力，不扩大默认
  `vega do`、Reviewer 或成功语义。

## 开发验证

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```
