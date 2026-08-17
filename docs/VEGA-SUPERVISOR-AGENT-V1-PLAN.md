# Vega Supervisor Agent V1 实施计划

> 状态：`approved / Gate 2B gate-exit-pass / Gate 2C gate-exit-pass / Gate 3A gate-exit-pass / Gate 3B SAG3B-07 timeout-preserved / runtime-compat-fix-merged / SAG3B-08 preregistered / Gate 3C 冻结`
>
> 计划日期：2026-08-13
>
> 规划基线：`main@8884458` / `v0.1.5`
>
> 本文已获批准，并按 Gate 推进。PR `#57` 最终文档 HEAD `8ca75f2` 已通过 workflow
> `31718680069` 的 9 项 CI，并以 `6a5c927` 合并到 `main`。Gate 2A 已完成；Gate 2B 已在
> 单一实验分支完成机械合同、两个冻结真实案例、最终 PR CI 与合并前审阅，当前状态为
> `gate-exit-pass`。2026-08-14 路线复核后增加 Gate 2C，用当前主线补一条真实完整成功路径。
> SAG2C-01 因 pytest 提前导入控制环境中的 `packaging` 而记为 `invalid-harness`；修正验证入口的
> SAG2C-02 已于 2026-08-14 通过 Gate 2C。Gate 3A 已于 2026-08-15 完成 Handoff 生产端、
> 同机双隔离副本往返、PR CI 和合并前审阅，状态为 `gate-exit-pass`。Gate 3B 的
> SAG3B-01～03 分别暴露 handoff 机会、控制源码身份和 committed baseline 缺口；
> SAG3B-04～06 又依次暴露 ignored Workspace、Writer MCP 和 Reviewer MCP 边界问题。
> SAG3B-07 已完成 Git-only 双 fresh clone 恢复，但 machine B 在 Python 3.14.3 /
> pytest 9.0.2 环境中超过冻结 Worker 预算。相关安全修复已进入主线，Windows
> `codex.CMD` 启动与人工 replan attempt epoch 又通过 PR `#68` 合入 `main@70282d1`。
> 历史 Case 均保持原判定；下一次只执行预注册的 SAG3B-08 稳定环境 Case，Gate 3C 仍冻结。

## 一、产品决定

Vega 下一阶段要做成一个**轻量但完整的软件工程 Supervisor Agent**。

它不替代 Codex、Claude Code 的读代码、调用工具和修改能力，也不与它们竞争通用 Coding Agent；
它负责把一个可能很模糊、可能持续数天的工程任务组织成可以调查、批准、执行、观察、恢复和验收的过程。

一句话说明这条链路：

> 用户提出目标，宿主主会话先在只读边界内调查并提交计划；Vega 校验、固定并等待人工批准；
> Coding Agent 在批准范围内修改；Vega 持续核对真实 Workspace、验证证据和风险，主会话随时
> 看得到进度，也能暂停、改计划或接管；最后仍由现有 Vega Core 判断任务能不能交付。

```text
用户目标
  → 只读调查
  → Plan 与人工批准
  → 编译当前 Task Brief
  → 派发一个可写 Worker
  → 对账进程、Workspace 和 Artifact
  → Verification / Risk / 独立 Reviewer
  → next / repair / replan / human / finalize
  → Checkpoint、恢复与 Finish
```

### 1.1 为什么它是 Agent，而不只是换名后的流水线

V1 必须具备以下完整能力：

1. 保存目标、边界、成功条件和人工决定；
2. 允许宿主主会话在只读边界内调查未知 Bug，并把事实、假设和未决问题结构化提交；
3. 校验并持久化可批准的执行计划，不把模型自由文本直接提升为控制事实；
4. 派发外部 Coding Agent 执行当前 Work Item；
5. 把 Worker 的自述与机器观察到的事实分开；
6. 根据不同 Observation 选择不同下一动作；
7. 在会话压缩、429、断网、进程退出或换机器后恢复；
8. 允许用户从主会话查询、批准、纠偏、暂停和接管；
9. 对失败尝试和当前进度保留可追溯记录；
10. 继续使用确定性验证、风险门禁、隔离 Reviewer 和 Finish 裁决。

Agent 的关键不是角色数量，而是形成以下循环：

```text
Goal → Observe → Decide → Act → Reconcile
  ↑                              ↓
  └──── Checkpoint / Human ──────┘
```

如果某个 LangGraph 节点、数据结构或 Adapter 没有带来实际价值，应替换或删除该实现；
**这不等于放弃 Supervisor Agent 主线**。Vega 保持 Agent 方向，只限制外围范围，避免变成通用平台。

### 1.2 与 Codex、Claude Code 和 Vega Core 的边界

- Codex、Claude Code 继续负责理解代码、使用工具和修改文件；
- Vega Supervisor 负责任务状态、计划批准、派发、观察、路由、检查点和恢复；
- Vega Core 继续拥有 Workspace、Scope、Verification、Risk、Reviewer 和 Finish 的成功语义；
- Reviewer 保持独立只读会话，不继承 Worker 的完整对话和中间推理；
- 人工继续负责批准计划、高风险确认、提交、推送和发布；
- `vega do / loop / goal` 在 V1 验证完成前保持现有行为。

## 二、责任主体与事实权威

### 2.1 五个责任主体

| 主体 | 职责 | 明确不做 |
|---|---|---|
| 主会话 | 接收用户目标、展示计划与状态、转发人工决定 | 不凭聊天文本宣称任务成功 |
| Supervisor | 校验并固化计划、编译上下文、派发、观察、选择下一动作 | 不直接修改业务代码，不把宿主调查结果自动提升为事实，不绕过 Core |
| Worker | 完成一个有界 Work Item，返回 Claim 和执行 Artifact | 不决定任务是否完成 |
| Reviewer | 在独立只读会话中审查 Diff、规则和验证证据 | 不接受 Worker 完整聊天，不覆盖确定性 Gate |
| 人工 | 批准、纠偏、处理高风险和最终 Git 操作 | 不需要通读每一行日志才能知道当前状态 |

主会话既是用户入口，也是 Vega 的控制台；Supervisor 的持久状态不依赖主会话是否还保留完整聊天。

### 2.2 两类权威顺序

任务意图的权威顺序：

```text
用户当前指令、AGENTS.md、.vega.yaml
  > 已批准 Plan
  > Worker 建议和历史 Memory
```

执行事实的权威顺序：

```text
存活进程、Git 与真实 Workspace
  > 当前结构化 Artifact 和新鲜度校验
  > Task Card / Checkpoint 中已核对的历史
  > Worker Claim、模型摘要和聊天文本
```

Worker 说“已经改好”只是一项 `Claim`。只有 Workspace、Artifact 和门禁能够形成 `Observation`；
只有 Supervisor 或现有 Core 根据受信任 Observation 才能形成 `Decision`。

## 三、完整运行链路

### 3.1 接收目标

Vega 首先记录：

- 用户原始要求；
- Non-goals；
- 成功条件；
- 目标仓库、分支和 Workspace；
- 已知风险与人工必须确认的节点；
- 时间、Worker attempt 和 Replan 预算。

默认先形成 Plan。只有用户已给出精确范围、验证方式和成功条件时，调查可以缩短为快速核实，
不能因为任务看起来很小就直接跳过边界确认。

主会话显示：目标已登记、当前 Workspace、下一步是快速核实还是完整调查。

### 3.2 主会话只读调查

宿主主会话或用户选择的 Coding Agent 在只读边界内检查：

- `AGENTS.md`、`.vega.yaml` 和项目规则；
- 相关源码、调用链、测试、配置和历史证据；
- 用户提供的错误、堆栈、复现步骤或 Issue；
- 当前分支、HEAD、工作区状态和已有运行产物。

调查结果必须分成：

- `Observed Facts`：有文件、命令或 Artifact 来源的事实；
- `Hypotheses`：尚未证明的根因或影响判断；
- `Unresolved Decisions`：进入修改前仍需用户决定的问题。

支付、数据库与迁移、并发与异步、权限、敏感数据、部署和外部 API 副作用应在计划前标记，
不能等到 Finish 才第一次披露。

调查结果通过结构化 Agent Plan 提交给 Vega。当前 Runtime 不内置 Planner 模型，也不自行调用
模型完成调查；Vega 负责校验字段、版本化 Plan、等待人工批准并编译后续 Task Brief。

主会话显示“调查完成”以及少量关键事实、假设和未决问题，不倾倒全部搜索日志。

### 3.3 制定 Plan

Plan Schema 支持 1～4 个粗粒度 Work Item。V1 的真实 Adapter 同一时刻只接受一个未完成
Work Item；多 Work Item 的累计 Diff 归因在独立 Gate 证明前，不属于 V1 必过能力。每项至少说明：

```yaml
id: WI-01
objective: 可独立检查的结果
allowed_paths: []
forbidden_paths: []
verification: []
risk_notes: []
depends_on: []
status: pending
```

整份 Plan 还必须包含：

- Goal 与 Non-goals；
- 已观察事实与根因假设；
- 允许读取和修改的范围；
- 验证命令和人工检查项；
- 失败、中断和 Replan 的处理方式；
- 总预算和停止条件。

Plan 不是模型对未来的保证，而是当前证据下的执行合同。

### 3.4 人工批准

主会话展示 Plan，用户可以：

- 批准；
- 修改；
- 缩小范围；
- 要求补充调查；
- 拒绝或停止任务。

批准绑定：

- Goal revision；
- Plan revision 与 digest；
- Workspace baseline；
- 范围、风险、验证和预算。

用户改变目标、范围、风险、验证或成功条件时，旧批准立即失效。Supervisor 生成新 revision，
再次展示并等待批准；不会把“用户说继续”解释成对未知扩大范围的默认同意。

### 3.5 准备 Task Brief

每次准备派发 Worker 或恢复新会话前，Vega 从持久材料重新生成短 `Task Brief`：

```text
Task Card
+ 当前 Git / Workspace
+ 当前 Work Item
+ 最近 Checkpoint
+ 最新 Verification / Risk / Review
+ 相关 Artifact 引用
+ 可验证的 accepted memory
```

它只回答：现在要做什么、已经确认什么、哪里失败过、当前 Workspace 是什么、下一步允许做什么。
完整聊天、内部推理和完整历史日志不属于恢复依赖。

### 3.6 派发 Worker

- 同一 Workspace 同时只能存在一个可写 Worker；
- 一个 Task 只绑定一个任务 Workspace，不为每个 Work Item 再创建分支或 Worktree；
- Assist 模式可以绑定用户已授权的当前 Workspace；需要隔离或并行处理其他任务时，再为整个 Task
  使用一个任务分支和一个 Worktree；
- 每次 Worker 执行是一个不可覆盖的 child attempt；
- Worker 必须接收当前 Work Item、允许范围、禁止项、验证、风险和 Task Brief；
- Worker Claim 单独记录，不直接升级为事实或完成状态。

主会话显示 Worker attempt、Work Item、开始时间、绑定 Workspace 和最近安全事件。

### 3.7 观察与现场对账

Worker 正常结束、异常消失或用户打断后，Supervisor 先检查：

- owned process 是否仍存活；
- HEAD、tracked/untracked Diff 和 changed files；
- Workspace fingerprint；
- operation、child 与 run binding；
- 执行 Artifact 是否完整、匹配且仍新鲜；
- 是否出现未知外部副作用。

然后分别记录：

```text
Worker Claim       Worker 声称完成了什么
Machine Observation 机器实际看到什么
Supervisor Decision 允许进入哪一步
```

现场可以解释后才能写 `safe` Checkpoint。`safe` 只表示“下一步可判断”，不表示代码正确。

### 3.8 确定性验证与风险检查

继续复用现有 Vega Core：

- Workspace 与 Scope Gate；
- 项目验证命令；
- Evidence freshness；
- Risk Gate；
- 高风险逐类披露。

验证失败不能被 Worker 自述、Supervisor 建议或 Reviewer approve 覆盖。数据库、支付、并发、权限等
高风险命中后，主会话必须显示命中文件、行为影响、当前证据、未证明事项和人工检查位置。

### 3.9 独立 Reviewer

Reviewer 读取：

- 当前 Goal 与已批准 Plan；
- 项目规则；
- 完整 changed files 和 Diff；
- 当前验证与风险证据；
- 必要的只读源码。

Reviewer 不读取 Worker 完整聊天、内部推理、未核实 Claim 或未经验证的长期 Memory。Reviewer 结果
是重要审查材料，但不能替代确定性 Gate，也不能自动确认高风险变更安全。

### 3.10 Supervisor 决策

确定性规则先过滤不允许的动作，再由 Supervisor 在剩余动作中选择：

| 动作 | 使用条件 |
|---|---|
| `next` | 当前 Work Item 已满足条件，下一项仍在已批准范围内 |
| `repair` | 问题明确且修复仍在原范围内；复用现有 child repair 或创建新的串行 attempt |
| `replan` | 新证据推翻假设、范围或依赖，需要新 Plan revision 和人工批准 |
| `human` | 现场不明、未知副作用、高风险确认、状态冲突或证据不足 |
| `finalize` | 所有 Work Item 完成，允许进入现有 Finish |

每次选择必须记录 Observation、允许动作、实际动作、理由、证据引用和剩余预算。相同流程面对不同
Observation 必须能产生不同路由；这是 Gate 1 判断它是否真正形成 Agent 控制循环的核心测试。

### 3.11 Finish

`finalize` 只调用现有 Finish，不自行创造第二套成功状态。主会话第一屏展示：

- 当前裁决；
- 重要 Diff 和 changed files；
- 实际执行的验证及结果；
- Risk 命中；
- Reviewer finding；
- 尚未证明的事项；
- 人工下一步。

LangGraph `END`、Worker `completed` 或 Reviewer `approve` 都不等于 `ready_to_commit`。

父 Agent 的 `completed` 只镜像可信 Core Finish：必须重新校验绑定的 child/operation、
Artifact SHA、Verification、Risk、Reviewer、完整性、新鲜度和当前 Workspace。Core Finish
已经生成、但父终态发布前中断时，允许用幂等 `agent finalize` 恢复；不得重新运行 Finish 或
根据聊天结论补造成功。

### 3.12 可选 Memory

任务进度、当前事实和失败尝试属于 Task Card 与 Checkpoint，不直接写成长期 Memory。

任务结束后可以生成 Memory Proposal，由人工选择接受、修改或拒绝。Memory 永远不能作为：

- 当前测试通过证据；
- 当前 Reviewer 结论；
- 当前 Plan 审批；
- 降低风险等级或绕过 Gate 的理由。

## 四、主会话必须看得到什么

Vega 不建设独立 Web UI 或 TUI。Codex、Claude Code 等宿主主会话就是控制台，通过稳定的状态卡、
低频事件和人工控制协议展示过程。

### 4.1 默认状态卡

主会话在开始、状态变化、用户查询和恢复时显示：

```text
Vega Agent
阶段：observe
任务：修复高频输出拖慢 timeout
Work Item：WI-02 / 3
Worker：attempt-02，已运行 06:42
Workspace：7 个 changed files，0 个未知文件
最近 Checkpoint：cp-004 / safe
Verification：2 passed，1 pending
Risk：concurrency
Reviewer：尚未启动
下一步：完成 Workspace 对账后决定 repair 或 human
```

状态卡只使用结构化状态和 Observation，不由模型自由总结成“基本完成”。

### 4.2 关键事件流

默认只显示低频事件：

```text
调查完成
Plan 已生成 / 已批准 / 已失效
Worker 启动 / 正常结束 / 失去可信终态
Workspace 对账完成
Checkpoint 已写入
验证通过 / 失败 / 超时
Risk 命中
Reviewer request_changes / approve / needs_human
Supervisor 选择 next / repair / replan / human / finalize
任务暂停 / 停止 / 完成
```

不在主会话刷每条工具调用、Token 统计和完整命令输出。需要时再按 Artifact 引用查看。

### 4.3 按需证据

用户可以从主会话要求查看：

- 当前修改文件和重要 Diff；
- 失败测试和验证日志引用；
- Worker Claim；
- Machine Observation；
- Reviewer finding；
- 最近 Checkpoint 或 Trace；
- 高风险命中；
- Supervisor 的路由理由。

### 4.4 人工控制

主会话允许：

- `approve`：批准当前 Plan revision；
- `query`：只查询状态，不改变运行；
- `steer`：增加或修改约束，必要时使旧 Plan 失效；
- `pause` / `stop`：停止继续调度并保留现场；
- `resume_with_new_worker`：保留当前 Diff，由新 attempt 接手；
- `verify_current_work`：不再写代码，直接验证现有现场；
- `stop_and_preserve`：停止并保留全部文件；
- `abandon_task`：记录放弃，不自动回滚或删除。

主会话能看进度，不代表 Worker 与 Reviewer 的完整上下文被打通。Reviewer 仍只读取明确编译的
Review Pack 和只读仓库视图。

## 五、Task Card、本机状态、Checkpoint 与 Trace

四类材料各自只管一件事：

| 材料 | 唯一职责 | 是否跨机器 |
|---|---|---|
| Task Card | 目标、批准 Plan、粗粒度进度与最后一次人工交接 | 是，人工 commit/push |
| `state.json` | 当前节点、active child、Workspace binding 和允许动作 | 否 |
| Checkpoint | 某个已对账时刻的不可变现场快照 | 否 |
| `trace.jsonl` | 解释发生了什么、为何选择下一动作 | 默认否 |

详细 Diff、测试日志、Reviewer 结果和原始执行输出继续留在现有 run Artifact，不在 Agent 层复制。

### 5.1 Task Card

建议路径：

```text
.vega/tasks/YYYY-MM/YYYY-MM-DD-task-slug.md
```

YAML Front Matter 保存少量机器字段：

```yaml
---
kind: VegaTask
schema_version: 1
task_id: 2026-08-13-fix-timeout
status: planning
branch: feature/fix-timeout
base_revision: abc123
goal_revision: 1
plan_revision: 1
approved_plan_digest: null
current_work_item: null
handoff_sequence: 0
handoff_status: none
handoff_base_revision: null
handoff_workspace_digest: null
last_handoff_checkpoint: null
---
```

正文固定为：

```markdown
## Goal and Non-goals
## Success Conditions
## Observed Facts and Hypotheses
## Approved Plan
## Progress and Failed Attempts
## Risks and Verification
## Last Handoff
## Next Step
```

规则：

- 事实必须附仓库相对路径、命令或 Artifact 来源；
- 假设不能写成事实；
- 只记录 Work Item 级进度，不写逐条工具日志；
- Plan 批准、显式暂停、交接和终态时才同步；
- Vega 可以生成和校验 Task Card，但不自动 commit 或 push；
- 未完成任务也可以形成合法的 Task Card 交接状态，不能把 WIP 提交写成验证通过或可合并；
- Worker 开始前必须将 Task Card 作为 control artifact 绑定到 Workspace baseline，不能把它误归因成
  Worker 代码变更。

### 5.2 本机 Run

候选目录：

```text
runs/<agent-run>/
  state.json
  trace.jsonl
  task-brief.md
  task-brief-manifest.json
  checkpoints/
  observations/
  graph-checkpoints.sqlite
```

`state.json` 只保存当前控制状态：Task Card digest、Goal/Plan revision、当前节点与 Work Item、
operation/child binding、Workspace fingerprint、最近 Checkpoint、预算、允许动作和终态。

LangGraph SQLite 只保存图游标和 pending interrupt。它丢失后可以根据 Task Card、本机状态、Checkpoint
和真实 Workspace 重建；不能凭 SQLite checkpoint 宣称代码正确或任务成功。

所有持久对象带 `schema_version`。损坏、未知版本或 digest 不一致时 fail-closed。V1 只处理当前版本，
不提前建设通用迁移平台。

### 5.3 未完成任务的跨机器接力

“任务还没做完，但需要先提交到任务分支，换一台机器或换一个新会话继续”是 V1 必须支持的正常
场景，不是异常补救。

Task Card 在 `Last Handoff` 中保存一份可直接重建 Task Brief 的 **Resume Capsule**：

- Goal、Non-goals 和成功条件；
- 已批准 Plan revision、digest 和各 Work Item 状态；
- 当前 Work Item 与停下的位置；
- 已确认事实、仍未确认的假设和失败尝试；
- 禁止项、风险约束和人工必须检查的位置；
- 本次 WIP 的 changed files 与内容摘要；
- 最近 Verification、Risk 和 Reviewer 的状态、来源 revision 与时间；
- 已知或未知的外部副作用；
- 下一步允许动作和推荐的第一条命令。

这里保存的是恢复所需的关键节点，不复制完整聊天、内部推理、Trace 和长日志。旧验证与 Reviewer
结论必须明确标记为 `historical`，不能因为被写进 Task Card 就继续算作当前证据。

准备跨机器接力时固定执行：

```text
暂停继续调度
→ 确认旧 Worker 和 owned process 已停止，或将现场标记为 blocked
→ 对账 Git、Workspace、partial diff 和 Artifact
→ 写本机 Handoff Checkpoint
→ 把 Resume Capsule 同步到 Task Card
→ 计算排除 Task Card 与 runs/ 后的 Workspace 内容摘要
→ 检查私密文件、绝对路径和意外运行产物
→ 输出待提交文件、WIP 提交说明和 push 检查清单
→ 人工明确授权 commit / push
```

Task Card 的交接状态只有两种：

- `handoff_ready`：旧 Writer 已停止，现场能够解释，可以在新机器继续；
- `handoff_blocked`：代码可以提交保存，但存在未知进程、副作用、状态冲突或证据缺口，新机器只能先
  调查或人工处理，不能自动启动 Worker。

WIP 代码、对应测试和 Task Card 可以一起提交到**任务分支**。测试尚未通过、Reviewer 尚未执行或
当前代码不可合并都不是禁止交接的理由，但必须原样写进 Resume Capsule；提交信息和 Task Card 状态
不得暗示 `ready_to_commit`。`runs/`、本机 Trace、SQLite、凭据和无关临时文件不得进入 Git。

Task Card 不保存“包含它自己的最终提交 SHA”，避免形成自引用摘要。`handoff_base_revision` 记录
生成交接时的旧 HEAD，`handoff_workspace_digest` 绑定即将提交的 WIP 内容；恢复时再以 Git 找到包含
该 Task Card 的实际提交，并校验当前 HEAD 与内容摘要。

Vega 本身仍不自动 commit 或 push。用户可以在主会话明确授权宿主执行 Git 操作，或者自行执行；
无论由谁执行，都必须使用 Vega 生成的文件清单并在 push 后核对远端分支 HEAD。

新会话不要求用户记住 Task Card 路径。`vega agent resume --repo .` 使用确定性发现规则：

1. 只扫描 Git 已跟踪的 `.vega/tasks/**/*.md`；
2. 只选择 `branch` 与当前分支一致、任务状态尚未终止且 `handoff_status` 不为 `none` 的 Task Card；
3. 恰好命中一个时显示摘要并等待人工确认；
4. 命中零个时报告没有可恢复任务；
5. 命中多个时列出任务，不自行猜测，由用户通过 `--task <path>` 选择。

新机器或新会话恢复时固定执行：

```text
fetch / pull --ff-only 指定任务分支
→ 按当前分支发现 Task Card，或使用显式 --task
→ 定位包含该 Task Card 的实际 Git 提交
→ 校验仓库身份、分支、Plan digest 和 Workspace 内容摘要
→ 确认本机没有额外 Diff 和 active Writer
→ 创建新的本机 agent run
→ 将旧验证与 Reviewer 降级为历史证据
→ 从 Resume Capsule 和真实 Workspace 重新生成 Task Brief
→ 在主会话展示恢复状态卡
→ 人工确认后继续当前 Work Item
```

换机器本身不会使 Plan approval 失效。只有 Goal/Plan digest、项目规则、风险、预期 Workspace 或
成功条件发生变化时，旧批准才失效并进入 `replan`。本机 Checkpoint ID 只作为历史引用；新会话真正
依赖的是 Git 中的 Resume Capsule 和重新计算的现场。

V1 不引入服务端分布式锁。跨机器单 Writer 依靠以下轻量协议保证：

1. 只有旧 Writer 已停止的 Task Card 才能标记为 `handoff_ready`；
2. 新机器开始写入前必须重新拉取远端并确认 HEAD 仍是该交接提交；
3. 旧机器若再次恢复，也必须先拉取远端并检查 Task Card 状态；
4. 分支发生分叉或远端 HEAD 已变化时进入 `state_conflict`，不得强推或静默覆盖。

这不能阻止用户绕过 Vega 在两台离线机器上同时手工修改，但能覆盖 Vega 控制范围内最常见的换机接力，
而不为此建设中心化 Lease 服务。

## 六、Checkpoint 与异常恢复

### 6.1 什么时候写 Checkpoint

只在粗粒度边界写入：

- Plan 批准或 revision 改变；
- Work Item 开始或结束；
- Worker 正常结束，或失去可信终态后完成现场对账；
- 用户 steer、pause、stop；
- Verification、Risk、Reviewer 改变下一动作；
- 本机恢复、显式交接或换机器；
- Finish 前。

不在每次 Tool Call 后写 Checkpoint，也不依赖猜测宿主何时进行上下文压缩。

### 6.2 Checkpoint 最小内容

```yaml
checkpoint_id: cp-004
reason: worker_terminal
status: safe | uncertain | blocked
task_digest: sha256:...
goal_revision: 1
plan_revision: 1
work_item: WI-02
child_run: child-02
workspace_fingerprint: sha256:...
changed_files: []
evidence_refs: []
completed: []
pending: []
failed_attempts: []
allowed_next_actions: []
created_at: ...
```

- `safe`：现场足以解释并选择下一步，不表示验证通过；
- `uncertain`：仍有进程、Diff 或 Artifact 无法解释；
- `blocked`：已确认存在冲突、未知副作用或必须人工处理的风险。

写入顺序：

```text
采集实时现场
→ 写临时 Checkpoint 并校验引用与 digest
→ 原子替换 Checkpoint
→ 若下一状态允许 dispatch，先写入匹配的 Task Brief 与 manifest
→ 原子更新 state.json
→ 由后续生命周期 Trace 引用该 Checkpoint
→ 仅在交接边界更新 Task Card
```

State 与 Checkpoint 是恢复权威；Trace 只提供追加式审计线索，不额外承担一次
`checkpoint_committed` 提交协议。崩溃发生在 State 更新前时，新 Checkpoint 可以作为未发布
Artifact 保留，但不能据此启动 Writer。

V1 不追求 Git、文件系统、外部进程和 SQLite 之间的分布式事务；恢复时始终回到真实 Workspace 对账。

### 6.3 Worker 异常统一处理

上下文压缩、429、网络断开、Provider 5xx、终端关闭、控制进程退出和突然关机统一进入：

```text
失去可信 Worker 终态
→ 检查旧进程
→ 对账 Workspace 和 Artifact
→ 写 safe / uncertain / blocked Checkpoint
→ 决定继续、替换 Worker、验证当前现场或交还人工
```

| 现场 | 动作 |
|---|---|
| 只是宿主压缩，会话和 owned process 仍健康 | 继续原 Worker，不启动新 Writer |
| 旧 Worker 或 owned process tree 仍存活 | 继续观察或请求停止，禁止第二 Writer |
| 受信 Adapter 在 dispatch 的同一原子边界内证明 operation 未启动，且 Workspace 未变 | 可评估创建新 child attempt |
| dispatch 已提交但缺少可信 execution / process 证据 | 保留旧 binding，`human` |
| Worker 消失但存在可解释的 partial diff | 等待人工选择新 Worker 接手或直接验证 |
| 存在未知外部副作用 | `human`，不得自动重放 |
| child 已有可信终态 | 复用 Artifact，但重新检查 freshness |
| Task、Plan、child 或 Workspace binding 冲突 | `state_conflict`，停止自动执行 |

数据库迁移、支付、部署、外部 API 和其他非幂等操作只要终态未知，就不能自动重试。

新 Worker 不是恢复旧模型的思维，而是读取 Goal、批准 Plan、当前 Diff、失败尝试和证据后重新判断。
旧 child 永远保留，不覆盖；Vega 不自动 stash、reset、回滚或删除。

## 七、上下文生成、压缩与注入

### 7.1 不保存完整聊天，只保存可重建材料

Vega 不接管 Codex、Claude Code 自带的上下文压缩，也不尝试保存模型内部思维。它保存能够确定性重建
下一步的结构化材料，并按需引用长 Artifact。

宿主自身的压缩仍然有价值，它可以帮助原会话延续一般对话和近期操作；但压缩内容由宿主生成，
具体保留了什么、何时更新以及是否遗漏批准边界，对 Vega 来说都不是可验证事实。因此：

- Vega 不解析或复用宿主压缩摘要作为任务状态；
- 宿主摘要不能替代 Goal/Plan revision、人工批准、Workspace 对账和当前验证证据；
- Task Brief 只补充恢复和决策必需的关键内容，不复制完整对话，也不重复生成一份聊天总结；
- 原会话仍可用时，继续利用其自身上下文；换会话或恢复时，新 Worker 依靠同一份可重建 Task Brief
  接手。

Task Brief 固定结构：

```markdown
# Current Task
## Goal and Boundaries
## Approved Plan
## Current Work Item
## Confirmed Facts
## Failed Attempts
## Workspace Now
## Latest Verification and Risks
## Required Next Action
## Evidence References
```

### 7.2 三层压缩策略

Task Brief 不设置下限，也不要求为了填满预算而增加内容。V1 使用**默认 `32 KiB` 软上限**，
按最终渲染结果的 UTF-8 字节数计算；Gate 0 可以根据冻结案例调低该上限，但不得在运行时按模型
偏好任意放大。能用更少内容说清楚时，就只生成实际需要的内容。

内容按三层处理：

1. **必须完整保留**：Goal、Non-goals、当前批准、当前 Work Item、禁止项、成功条件、高风险和下一动作；
2. **结构化压缩**：已确认事实、失败尝试、changed files、最近验证和 Reviewer 状态；
3. **只保留引用**：旧 attempt、长日志、完整代码、历史报告和低相关细节。

接近软上限时，先去重并把长内容降为 Artifact 引用。必需内容仍超过上限时，进入 `needs_human`、
要求拆分 Work Item 或重新规划，不能静默截断约束。模型生成的自由文本摘要若保留，只能标记为
Claim；恢复所需内容必须能从结构化状态和 Artifact 重新生成。

### 7.3 尽量保留宿主 Prompt Cache

Vega 只能控制自己提供给宿主的内容，不能承诺 Codex 或 Claude Code 内部缓存命中率。V1 采用：

- 稳定的角色合同、项目规则和工具说明放在输入前部；
- 章节顺序稳定，未变化内容保持字节一致；
- 动态 Task Brief 放在当前请求尾部；
- 代码和长日志通过引用按需读取，不反复重写稳定前缀；
- manifest 记录 section digest，便于判断哪些段落真的变化。

不会把 Vega 摘要插到宿主压缩摘要之前，也不会在运行中的会话里频繁注入新摘要。只有创建新 Worker、
Work Item 边界、恢复或人工 steer 后，才向动态尾部注入最新 Task Brief。

### 7.4 Task Brief 刷新节点

- 调查完成；
- Plan 批准或 revision 改变；
- Work Item 开始；
- Worker 终态或异常对账完成；
- 用户 steer、pause、stop；
- Verification、Risk、Reviewer 改变路由；
- 本机恢复、交接或换机器；
- Finish 前。

`task-brief-manifest.json` 只记录 Task Card digest、Workspace fingerprint、Artifact 引用、各节 digest
和生成时间，用于说明这次 Worker 看到了哪些来源；它不成为第二份任务状态。

## 八、Memory 的召回与写入时机

V1 不建设新的 Memory Agent、向量库或自动长期记忆。复用现有 accepted memory，并收紧使用方式。

### 8.1 召回节点

只在以下节点检索：

1. Goal 已绑定到具体仓库后，供只读调查参考；
2. Plan 批准后、编译当前 Work Item 的 Task Brief 时；
3. steer 或 replan 使目标、路径或风险发生变化后；
4. Finish 时决定是否生成新的 Memory Proposal。

不在每次 Tool Call 后召回，也不因为某条 Worker 输出包含相似关键词就立即改写当前上下文。

### 8.2 使用规则

- 只使用人工 `accepted` 且仓库身份匹配的条目；
- 路径、适用条件或来源已经明显失效时跳过，并记录 `stale` 原因；
- 缺少可复核来源的旧条目只能作为 `historical_hint`，不能进入 `Confirmed Facts`；
- 当前代码、测试、项目规则和用户指令始终优先；
- Memory 不能替代当前验证、Reviewer、批准或风险确认。

任务完成后的经验仍先生成 Proposal；只有人工 accept 后，才可能在后续任务被召回。

## 九、LangGraph 的位置

LangGraph 只作为 Supervisor 的控制面，不接管 Vega Core 的业务事实。

```text
prepare
  → await_approval
  → dispatch
  → reconcile
  → decide
      ├─ next / repair → dispatch
      ├─ replan        → prepare → await_approval
      ├─ human         → interrupt
      └─ finalize      → Vega Finish
```

业务上的十二个节点被归并到少量 Graph node，避免把每个工具调用建成状态节点。

V1 必须真实使用：

- `StateGraph` 条件边；
- run-local checkpointer；
- `interrupt()` 与人工 resume；
- 安全、低频的节点事件流。

Graph State 只引用 `agent_run_id`、`state_version` 和 pending interrupt。Git、Workspace、进程、Plan、
Verification、Reviewer 和成功状态仍由现有文件与 Core 持有。

`interrupt()` 前禁止启动非幂等副作用。LangGraph 从节点开头重跑时，节点必须先读取 operation/child
binding 并对账，不能重复启动 Writer。

Decision Contract 至少包含：

```yaml
observations: []
evidence_refs: []
allowed_actions: []
selected_action: human
reason: "存在无法解释的外部副作用"
budget_remaining: {}
decision_source: deterministic | supervisor | human
```

Gate 1 必须证明不同 Observation 会产生不同合理动作；若所有路径最终都是固定顺序，就说明具体 Graph
设计失败，需要简化或重做，而不是继续叠加节点。

## 十、CLI 与宿主接入

Gate 2A 已实现的命令保持少量：

```powershell
vega agent start --repo . --input <task-card-or-text>
vega agent status --run <agent-run>
vega agent finalize --run <agent-run>
vega agent resume-local --run <agent-run>
vega agent resume --repo .                     # 按当前分支发现可恢复 Task Card
vega agent resume --repo . --task <task-card> # 换机器后创建新本机 run
vega agent steer --run <agent-run> --instruction "新增约束"
vega agent pause --run <agent-run> --reason "等待人工处理"
vega agent stop --run <agent-run> --reason "人工停止"
```

低频观察可复用现有命令：

```powershell
vega watch --run <agent-run> --follow
```

Gate 3A 已实现：

```powershell
vega agent checkpoint --run <agent-run> --handoff --reason "准备换机"
```

该命令生成 Handoff Checkpoint、Resume Capsule、Git Task Card、manifest 和人工 Git 清单，
但不会执行 `git add`、commit 或 push。跨机器接力仍依赖受版本控制的 Task Card 和人工 Git 操作。

打包后的顶层 CLI 已新增 opt-in `agent` 子命令，但既有 `do / loop / goal` 命令行为和成功语义
保持不变。Gate 3 通过前不把 `agent` 提升为默认推荐入口。

主会话 Skill 或薄 Adapter 只做三件事：

1. 调用上述 CLI；
2. 把状态卡、关键事件和人工选项显示在当前会话；
3. 将用户选择提交给同一个 agent run。

状态不保存在 Skill 或聊天里。Codex 作为第一个真实 Adapter；核心合同稳定后再接 Claude Code 的薄
Adapter，两者复用同一 Task Card、Task Brief、Checkpoint、Trace 和 Vega Core，不建设通用 Provider SDK。

2026-08-16 的 V1 产品合同补全在现有 Codex 初始化中增加 `$vega-agent`，并让通用
`vega status/watch` 识别父 Agent run。`watch` 复用父 Trace 与 child progress，不新增第二套
证据文件；这项改动不改变 Gate 3B/3C 实验结论，也不把 Agent 提升为默认入口。

## 十一、实施 Gate

每个 Gate 形成可审查结论后再进入下一 Gate。每个未合并 Gate 只使用一个短生命周期实验分支和
一个专用 Worktree，不为每个 Work Item 反复创建分支；合并后删除该分支与 Worktree。

### Gate 0：冻结最小合同

只做 ADR、Schema 和测试清单，不接真实 Worker：

- Task Card、Run State、Task Brief、Checkpoint、Trace；
- Goal/Plan revision 与 approval digest；
- Resume Capsule、Task Card 分支发现与 WIP 内容摘要；
- 单 Writer、operation/child identity 和替代 Worker规则；
- Decision Contract 与允许动作；
- Checkpoint 触发边界、Task Brief 默认 `32 KiB` 软上限和敏感信息规则；
- 主会话状态卡和事件格式；
- 两个真实案例、模型、预算、timeout、验证和停止条件。

退出条件：没有重复事实源，没有仅因“以后可能需要”而存在的对象，所有异常都有明确人工路径。

### Gate 1：Fake Worker 与可见 Agent 循环

在临时仓库完成：

- 两个串行 Work Item；
- Plan approval 与 steer 后失效；
- Task Brief 生成、分层压缩和 manifest；
- 状态卡、低频事件、按需 Observation；
- `next / repair / replan / human / finalize` 条件路由；
- Worker 启动前、partial diff 后和验证失败后的 Checkpoint；
- Graph `END` 不能绕过 Finish；
- Task Brief 与 Trace 不含密钥、本机绝对路径、完整聊天或内部推理。

关键验收：至少三种不同 Observation 产生三种不同 Decision，且路由理由可由 Trace 和 Artifact 复核。

### Gate 2A：中断、对账和恢复

使用可控 Worker 做故障注入：

- 旧 Worker 存活时拒绝第二 Writer；
- dispatch 在发布 `acting` State 前登记 run-local write-once operation identity，同一 run
  禁止复用旧 `operation_id`；
- dispatch 提交时即原子绑定 Writer，并保守跨过不可自动重试边界；
- dispatch 后缺少可信 execution / process 证据时保留旧 binding；
- 只有未来受信 Adapter 能在同一原子边界内证明 operation 未启动时，才允许评估新 child；
- partial diff 后失去 Worker，必须人工选择；
- 未知数据库、支付、部署或外部 API 副作用禁止重放；
- state 损坏、Trace 尾部截断、SQLite 丢失和未知 schema；
- Recovery 机器 Observation 不得覆盖历史证据；
- Task Card 恢复写入失败时不得发布可 dispatch State；
- 控制进程退出后从真实 Workspace 恢复；
- 主会话 steer、pause、stop 不丢失 Goal。

退出条件：`duplicate_writer_start = 0`、`unknown_side_effect_auto_retry = 0`，所有恢复都先对账再动作。

### Gate 2B：真实 Codex 与主会话控制

具体 Adapter 信任边界、预算、冻结案例和停止条件见
[`SUPERVISOR-AGENT-GATE-2B-PLAN.md`](SUPERVISOR-AGENT-GATE-2B-PLAN.md)。冻结计划已经人工
批准，真实 Codex Adapter 的机械合同已实现；代码 HEAD `799bb29` 已通过 PR `#58` 的 9 项
CI，两个冻结案例也已形成合同允许终态；最终 PR CI 与合并前审阅均已完成。Gate 2B 当时判定为
`gate-exit-pass`，Gate 3 当时仍冻结并需另行批准。

Gate 2B 当前只执行一个未完成 Work Item，并允许一次同 child repair。多 Work Item 的真实派发
仍缺少累计 Diff 归因证据；现有 assist baseline 会拒绝旧 tracked diff，因此本轮不通过放宽
Core 或自动 commit 绕过。V1 的多 Work Item 目标仍保留，但必须在后续 Gate 单独证明。

冻结两个案例：

1. **模糊 Bug**：用户不知道根因位置；宿主主会话先只读调查并提交计划，Vega 校验后请求人工批准，
   真实 Worker 结果根据证据进入合理的 `finalize`、`repair`、`replan` 或 `human`；
2. **长任务中断**：真实 Worker 留下 partial diff 后中断，主会话看到现场并由人工选择新 Worker
   接手或验证当前工作。

必须证明：

- 主会话能看到阶段、Work Item、Worker、changed files、风险、Checkpoint 和下一步；
- 用户可以查询、steer、暂停和恢复；
- 新 Worker 不依赖旧 Worker 完整聊天；
- Reviewer 仍与 Worker 会话隔离；
- 最终 Finish 继续 fail-closed。

### Gate 2C：当前主线真实完整成功路径

Gate 2B 证明了真实 Worker 的 Claim 不会越过门禁，以及 partial Diff 能被安全停止并交还人工，
但两个正式案例都没有完整经过 Verification、Risk、Reviewer 与 Finish。进入跨机器实现前，先用
当前 `main` 完成一个单 Work Item、低风险、可重建的真实 Codex 案例。

SAG2C-01 已证明失败验证不能被 Worker Claim 或 Reviewer 覆盖，但其 pytest 命令验证了错误的
Python 包来源，因此不计为 Gate 通过或模型失败。修正后的 SAG2C-02 已使用全新目标和正确的
验证入口，完整经过 Verification、Risk、独立 Reviewer、Finish 和 Supervisor `finalize`，
结果为 `gate-exit-pass`。原协议与无效结果见
[`SUPERVISOR-AGENT-GATE-2C-PLAN.md`](SUPERVISOR-AGENT-GATE-2C-PLAN.md)，修正后的冻结协议见
[`SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md`](SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md)。

通过条件：

- 真实 Worker 只修改批准路径；
- Verification、Risk、独立 Reviewer 和 Finish 均产生可信 Artifact；
- Supervisor 只能根据机器 Observation 进入 `finalize`；
- `false_success = 0`、`duplicate_writer_start = 0`；
- 任务、模型和预算不因结果而替换。

Gate 2C 只验证当前主线的完整可用路径，不新增 Handoff、Claude、Memory、多 Work Item 或默认入口。
Gate 2C 完成后，Gate 3A～3C 当时仍冻结，进入下一阶段前必须单独批准 Handoff 范围和停止条件。

### Gate 3A：Handoff 机械生产与本地往返

只实现计划中的 Handoff 生产端：

```text
当前 Agent run
  → 停止继续调度并对账 Writer、Workspace 和副作用
  → 写 Handoff Checkpoint
  → 生成 Resume Capsule 与 Git Task Card
  → 输出待提交文件和人工 Git 检查清单
```

先在同一机器的两个隔离 clone/worktree 间完成往返，排除 OS、依赖和宿主变化。Vega 仍不自动
commit 或 push，也不建设第二套 Handoff Runtime。

2026-08-15 本地结果为 `local-dogfood-pass / merge-pending`：

- Agent run `20260815-132909-agent` 在旧 Writer 已停止后生成 `handoff_ready` Task Card；
- 人工只暂存一个 WIP 文件和 Task Card，`git diff --cached --check` 通过后形成 Handoff 提交；
- 第二个隔离 clone 不携带旧 `runs/`、Trace、SQLite 或聊天，只从 Git Task Card 创建新 run
  `20260815-144839-agent-resume`；
- 新 run 为 `ready`，当前 Work Item 为 `W1`，Trace 包含 `task_card_resumed`；
- 旧 Verification、Risk、Reviewer 只保留为 historical，新状态卡三项均为 `not_run`；
- 错误仓库历史、错误 HEAD、active Writer、`needs_human`、路径逃逸、敏感信息和 Artifact
  发布失败均有 fail-closed 回归；
- Vega 没有自动执行 Git 写入、启动真实模型 Worker 或进入 Gate 3B。

本地 CI 同款分片合计 `1239 collected / 1227 passed / 12 skipped / 0 failed`；Ruff、compileall、
repository hygiene、architecture growth 和 `git diff --check` 均通过。实现提交 `33c4ac1`
对应 PR `#60` 的 9 项 CI 全部通过，两轮独立本地审阅无剩余阻断项，Gate 3A 判定为
`gate-exit-pass`。

### Gate 3B：单 Work Item 的真实跨机器接力

完成一次真实接力：

```text
机器 A：Handoff Checkpoint + Task Card + 人工 commit/push
机器 B：pull + 按分支发现 Task Card + 重新对账 + 继续执行
```

Task Card 必须包含未完成 Work Item、失败尝试、约束、风险、WIP changed files 和准确的下一步；
新会话不得依赖旧聊天就能解释当前任务。旧验证和 Reviewer 结果必须被识别为历史证据，重新验证后
才能进入 Finish。

该 Gate 固定使用同一个 Codex Adapter、一个未完成 Work Item 和同一任务分支。多 Work Item、
Claude Code、Memory 和自动 Git 不进入本实验。

### Gate 3C：小规模日常价值观察

Gate 3B 通过后，使用少量真实任务记录：

```text
恢复到可执行状态的时间
重复调查次数
人工重建步骤
错误恢复或重复 Writer 次数
用户是否愿意再次使用
```

这不是正式成功率或 Token A/B。只有出现真实使用收益，才讨论扩大样本或把 `vega agent` 提升为
推荐入口。

Claude Code 已通过外部 assist 复用 Vega Core，但尚未满足 Supervisor 受信 Worker 合同。V1 只承诺
Codex Adapter；Claude Code 薄接入移到 V1 之后单独评估，不与跨机器恢复混跑。

## 十二、V1 范围与硬门槛

### 12.1 必须实现

- 一个 Supervisor；
- Goal、Plan、人工批准和 revision；
- 一个 Task Card、一个本机状态、一条 Trace；
- 粗粒度 Checkpoint 与 Task Brief；
- 主会话状态卡、事件和人工控制；
- 引擎无关的状态、条件路由和人工 interrupt/resume 合同；当前实现可以使用 LangGraph，但框架本身
  不是验收目标；
- 单 Writer、operation/child 对账；
- 一个真实 Codex Adapter；
- 一个未完成 Work Item 的真实完整成功路径；
- 本机恢复和一次跨机器接力；
- 现有 Verification、Risk、Reviewer 和 Finish 集成；

### 12.2 V1 不做

- 多 Worker 并行、群聊或多 Reviewer fan-out；
- 多 Work Item 的真实累计 Diff 自动归因与连续派发；
- Planner、Researcher、Memory 等额外角色；
- Web UI、TUI、服务端、队列或 daemon；
- Provider SDK、模型托管或重写 Codex/Claude Code；
- 向量库、Embedding、Repo Map、知识图谱；
- Worktree 管理平台或每个 Work Item 独立分支；
- 自动 commit、push、release、部署、回滚或删除；
- 自动接受长期 Memory；
- 重写 Vega Core 或放松 fail-closed。

### 12.3 硬门槛

```text
false_success = 0
duplicate_writer_start = 0
unknown_side_effect_auto_retry = 0
stale_approval_accepted = 0
cross_machine_stale_evidence_accepted = 0
goal_constraint_silently_dropped = 0
untrusted_output_promoted_to_fact = 0
reviewer_worker_context_leak = 0
```

### 12.4 控制复杂度的规则

出现以下情况时，先删减或替换具体实现，不向外围扩张：

1. Task Card、State、Checkpoint 出现竞争的当前事实；
2. LangGraph 形成第二套成功状态；
3. 每个 Tool Call 都需要 Checkpoint；
4. 恢复必须保存完整聊天或建设向量库；
5. 中断后仍无法阻止重复 Writer 或未知副作用重放；
6. 第一个 Adapter 尚不稳定就开始建设 Provider 平台；
7. 主会话为了“可见”而刷出大量工具日志；
8. 测试和 Schema 数量快速增长，却没有对应真实故障；
9. 为了简历展示增加多 Agent、服务端或 UI，而真实日常任务没有受益；
10. 任何设计放松现有成功语义、写审隔离或人工 Git 边界。

## 十三、审核通过后的实际顺序

当前执行进度：第 1～7 项已完成。第 6 项的首次运行记为 `invalid-harness`，修正后的
SAG2C-02 已判定为 `gate-exit-pass`；第 7 项 Gate 3A 已完成实现、本地往返、PR CI 与
合并前审阅，判定为 `gate-exit-pass`。Gate 3B 已执行 SAG3B-01～07，Git-only Task Card
Handoff、fresh clone 恢复、committed baseline、Writer/Reviewer MCP 隔离和 dispatch 身份
门禁均已形成真实证据。SAG3B-07 的 machine B Worker 在冻结环境中超时，历史结果保持
`gate-not-passed`。PR `#68` 已将 Windows `codex.CMD` 启动和 replan attempt epoch 修复合入
`main@70282d1`；下一次只执行 SAG3B-08 稳定 Python 3.12 环境 Case，Gate 3C 仍冻结。

2026-08-16 另行补齐了 V1 已承诺但此前未完整发布的三个产品合同：可信 Core Finish 到父
Agent `completed` 的终态发布、`$vega-agent` 主会话 Skill、父 Agent 的通用
`status/watch`。它们属于既有 V1 合同，不代表 Gate 3B 已通过，也不新增多 Work Item、
Planner、Memory、Provider SDK 或自动 Git。

1. 先把本文的关键决定登记到 `ROADMAP.md`，写一份小型状态权威 ADR；
2. 使用一个实验分支和一个专用 Worktree 完成 Gate 0；
3. 按 `Task Card / State → Checkpoint / Trace → Task Brief / 状态卡 → LangGraph` 实现 Gate 1；
4. Gate 1 独立审查通过后做 Gate 2A 故障注入；
5. Gate 2A 通过后才连接真实 Codex；
6. Gate 2B 通过后先完成 Gate 2C 当前主线真实完整成功路径（已完成，`gate-exit-pass`）；
7. 获得单独批准后实现 Gate 3A Handoff 生产端并完成本地往返；
8. Gate 3A 通过后做 Gate 3B 单 Work Item 跨机器接力；
9. Gate 3B 通过后做 Gate 3C 小规模日常价值观察；
10. 每个 Gate 都先给用户看证据和下一步，不一次性跨过全部阶段。

## 十四、已确认的决定

截至 2026-08-15 已确认：

1. Vega 的主线定位为轻量、可恢复、主会话可控的软件工程 Supervisor Agent；
2. Task Card 进入 Git，运行状态、Checkpoint 和 Trace 默认留在本机；
3. Task Brief 使用分层压缩，不设下限，默认软上限为 `32 KiB`；
4. V1 只承诺 Codex Adapter，Claude Code 薄接入不与跨机器恢复混跑；
5. 每个未合并 Gate 只使用一个短生命周期实验分支和一个专用 Worktree，不为每个小步骤创建
   新分支；合并后删除；
6. Gate 2C 先证明当前主线完整成功路径，再进入 Handoff 实现；
7. 多 Work Item、Memory Proposal 和 Provider 平台不属于 Supervisor Agent V1 必过范围。
8. Gate 3A 只证明同机机械接力；真实跨机器继续执行和日常价值分别属于 Gate 3B、Gate 3C。

本文与当前代码或产品契约冲突时，在新版本发布前仍以已发布代码、
[`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) 和真实运行证据为准。
