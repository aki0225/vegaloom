<div align="center">

<img src="docs/assets/vega-hero.jpg" width="100%" alt="Vega：一个写，一个审，独立会话共享证据">

# Vega

<h3>AI 编码 Supervisor Agent 与验证 Harness</h3>

<p><strong>One writes, one reviews.</strong></p>

<p>
  <a href="https://github.com/aki0225/vegaloom/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/aki0225/vegaloom/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Stable-v0.2.1-4fb8d8?style=for-the-badge" alt="当前稳定版本 v0.2.1">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-F8FAFC?style=for-the-badge" alt="MIT License"></a>
</p>

**[在线展示](https://aki0225.github.io/vegaloom/)** ·
**[核心设计](#核心设计)** ·
**[核心能力](#核心能力)** ·
**[安装](#安装)** ·
**[入口怎么选](#入口怎么选)** ·
**[快速开始](#快速开始)** ·
**[Codex 接入](#codex-接入)** ·
**[关键行为](#关键行为)** ·
**[文档](#文档)** ·
**[定位与边界](#定位与边界)**

</div>

Vega 同时提供 opt-in 的软件工程 Supervisor Agent 和稳定的 Coding Harness。它不追求堆叠
复杂的 Multi-Agent 架构，而是把代码修改、确定性验证与独立评审分流，让 AI 编码的每一步
都有明确的输入边界、验证证据和退出条件。

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
- 可选的 Supervisor Agent 为长任务补充 Plan 批准、单 Writer、Checkpoint、主会话状态和
  Git Task Card 恢复，不改变 Core 的成功语义。
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
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
vega --version
```

需要使用 Supervisor Agent 时安装可选依赖：

```powershell
python -m pip install -e ".[agent]"
vega agent capabilities
```

参与 Vega 本身开发时使用 `python -m pip install -e ".[dev]"`；`dev` 已包含测试、静态检查和
Agent 依赖。普通 `do / loop / finish` 用户不需要安装 `dev` extra。

更新源码后应重新执行对应的 `pip install -e ...`。Git pull 会更新 Python 源码，但不会自动
刷新已经安装的 console-script 入口；只看 `vega --version` 可能遗漏旧入口。若
`vega agent` 提示命令不存在，先确认当前命令来源并重新安装：

```powershell
Get-Command vega
python -m pip install -e ".[agent]"
vega agent capabilities
```

## 入口怎么选

| 场景 | 推荐入口 | 说明 |
|---|---|---|
| 根因、范围或验收还不明确 | Plan-first + `vega loop ... --mode assist` | 主会话先只读调查并让人工确认，再修改 |
| 边界清晰的一次性小任务 | `vega do bug|feature` | 自动 Worker，仍经过验证、Risk、Reviewer 和 Finish |
| 需要暂停、接手或 Git-only fresh-clone 恢复 | `$vega-agent` 或 `vega agent` | 单 Work Item、单 Writer、显式批准和 Checkpoint |
| 只读检查现有仓库 | `vega run engineering-change` | 不启动可写 Worker |

不确定时先走 Plan-first。Supervisor Agent 不是更强的默认模式，而是为长时间运行、人工控制和
恢复增加一层状态管理。

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

`vega finish --run <loop_run>` 面向普通 `do / loop` 的 Core run，读取当前 Core Artifact 并生成
交付结论。`vega agent finalize --run <agent_run>` 只在父 Agent 已处于 `finalizing` 时使用，
采用已经绑定且可信的 Core Finish 发布父终态；它不重新运行 Core Finish，也不绕过验证、
Risk 或 Reviewer。正常的 `vega agent run` 会自动完成这一步，显式 `agent finalize` 主要用于
父终态发布前中断后的幂等恢复。

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

需要让 Codex 主会话按 Skill 调用 Vega 时，先为目标仓库初始化，再切换到该目标仓库：

```powershell
$targetRepo = "<target-repo>"
vega adapters init codex --repo $targetRepo
Set-Location $targetRepo
vega agent capabilities
```

`adapters init` 只决定仓库级 Skill 的写入位置，不会改变当前 shell 的工作目录。后续
`vega agent start / approve / run / status / finalize` 必须在目标仓库目录中执行，因为 Agent
run、状态和恢复入口以当前工作目录作为 Vega workspace。

命令会生成仓库级 Skill：

```text
.agents/skills/vega-loop/SKILL.md
.agents/skills/vega-review/SKILL.md
.agents/skills/vega-agent/SKILL.md
```

在该仓库的新 Codex 任务中可通过 `$vega-loop`、`$vega-review` 或 `$vega-agent` 显式调用。
`$vega-loop` 会在模糊任务下先要求只读调查、固定 Plan 和修改前人工确认；`$vega-agent`
面向需要暂停恢复或持续控制的任务，由主会话提交单 Work Item Plan，人工批准后再驱动真实
Codex Worker、Verification、Risk、独立 Reviewer 和可信 Finish。

推荐先让主会话使用 `$vega-agent`，由 Skill 展示 Plan、状态和下一步。需要直接操作 CLI 或
排查恢复流程时，参见
[日常使用 Walkthrough 的 Supervisor Agent V1 小节](docs/USAGE-WALKTHROUGH.md#supervisor-agent-v1)。

Supervisor Agent V1 保持 opt-in，当前只接受一个未完成 Work Item。通用
`vega status --run <agent_run>` 与 `vega watch --run <agent_run> --follow` 可以直接查看父 Agent
状态、Supervisor 低频事件和绑定 child 的安全进度。只有 Core Finish 为 `ready_to_commit`、
父 Agent phase 为 `completed`，并且实时状态重新校验得到 `evidence_health=passed` 与
`workspace_current=true`、`commit_recommended=true` 时，才建议人工检查并提交。完成后如果
HEAD、Diff、未跟踪文件或 Git 控制状态发生变化，实时状态会撤销提交建议。

`vega agent status --run <agent_run>` 会重新读取当前 child 阶段，并核对 Supervisor Worker、
批准范围和 Core Finish 的证据文件。`status-card.md` 只是生成时快照，不能替代实时状态命令。
`vega agent status --run <agent_run> --json` 与文本输出使用同一份实时证据投影。Plan 中的风险
备注会单独列出，不会被当成 Risk Gate 的实际运行结果。

每个 Work Item 还必须声明仓库外副作用为 `none`、`known` 或 `unknown`。数据库写入、支付、
部署与外部 API 等动作不会因为命令返回成功就自动视为无副作用；`known` 或 `unknown` 会停在
人工处理，避免在 Worker 异常后自动重放。

Skill 不安装 hook、不修改 Codex 全局配置，初始化时也不会启动 Worker；执行阶段仍需要当前
会话按 Skill 调用 CLI，并遵守人工批准和 fail-closed 门禁。
初始化默认不覆盖已有文件；`--force` 只覆盖新的 `.agents/skills` 目标，不删除或改写历史
`.codex/skills` 文件。是否将生成的仓库级 Skill 纳入版本控制，由目标项目自行决定。

## Claude Code 接入

Claude Code 主会话复用同一份
[Plan-first 与修改前确认协议](docs/PLAN-FIRST-PROTOCOL.md#8-claude-code-使用方式)，并在批准后
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
| 选择日常入口与完整操作 | [USAGE-WALKTHROUGH](docs/USAGE-WALKTHROUGH.md) |
| Supervisor Agent 的产品边界 | [PRODUCT-CONTRACT](docs/PRODUCT-CONTRACT.md) |
| Supervisor Agent 状态权威与恢复合同 | [SUPERVISOR-AGENT-STATE-AUTHORITY](docs/SUPERVISOR-AGENT-STATE-AUTHORITY.md) |
| 调查、Plan 与修改前人工确认 | [PLAN-FIRST-PROTOCOL](docs/PLAN-FIRST-PROTOCOL.md) |
| 当前演进路线与下一步 | [ROADMAP](docs/ROADMAP.md) |
| v0.2.1 发布说明 | [RELEASE-NOTES-0.2.1](docs/RELEASE-NOTES-0.2.1.md) |
| v0.2.0 发布摘要 | [RELEASE-SUMMARY-0.2.0](docs/RELEASE-SUMMARY-0.2.0.md) |
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
- 当前稳定基线为 `v0.2.1`。v0.2.0 在保留既有 `do / loop / goal` 成功语义的同时发布
  opt-in
  Supervisor Agent V1：主会话调查和提交单 Work Item Plan，人工批准后由 Vega 约束一个
  真实 Worker、核对 Workspace、支持 Git-only fresh-clone / 换目录接手，并复用现有
  Verification、Risk、独立 Reviewer 与 Finish 完成交付判断。该证据不外推为另一台物理机器
  已完成验收。

## 开发验证

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```
