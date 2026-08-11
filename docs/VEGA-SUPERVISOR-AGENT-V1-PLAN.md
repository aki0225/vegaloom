# Vega Stateful Software Engineering Supervisor Agent V1 计划

> 状态：`draft / awaiting-review`
>
> 计划日期：2026-08-11
>
> 规划基线：`main@010f2c3` / `v0.1.5`
>
> 本文只记录候选方向和实施门槛。文档进入主线不授权修改 Runtime，也不代表 Vega 已经具备
> 本文描述的 Agent 能力。

## 一、产品决定

Vega 不继续扩展成另一个通用 Coding Agent，也不缩成只会运行测试的 Test Agent。候选方向是：

> **可恢复、证据驱动的软件工程 Supervisor Agent。**

```text
用户给出目标或模糊问题
        ↓
Vega 组织调查、Plan、人工批准和任务状态
        ↓
Codex / Claude Code / 其他 Worker 调查并修改代码
        ↓
Vega 对账 Workspace、运行验证、识别风险并启动隔离 Reviewer
        ↓
根据证据决定 Repair、Replan、Human 或 Finish
```

职责边界固定为：

```text
宿主 Agent：具体调查、推理、工具调用和代码修改
Vega Agent：Goal、Plan、Checkpoint、Observation、Decision 和 Resume
Vega Core：Workspace、Scope、Verification、Risk、Reviewer 和 Finish
Git、进程与 Artifact：证明事实
人类：批准 Plan、高风险决定和最终提交
```

### 1.1 为什么不是直接使用 Codex 或 Claude Code

Vega 不与宿主竞争编码能力。它只提供宿主默认不会替用户固定保证的工程约束：

1. 区分任务开始前的改动和本轮改动；
2. 把验证结果绑定到实际 Workspace 和 Revision；
3. Reviewer 使用独立会话和受控输入，不继承 Worker 完整叙事；
4. 数据库、并发、支付、权限等高风险修改必须显式披露；
5. 中断或状态漂移后先对账，未知副作用不自动重跑；
6. 不论代码来自哪个 Agent 或人工，都使用同一套 Finish 判断。

如果最终实现只能做到“运行测试、调用 Reviewer、生成报告”，则保持 Harness 定位，不宣传为 Agent。

### 1.2 使用边界

以下任务默认不需要 Vega Agent：

- 一眼能够确认的文案、注释或低风险小改动；
- 只读代码探索；
- 临时原型；
- 已有完整 CI 且用户愿意直接人工检查的变更。

以下任务才值得使用：

- 用户只知道 Bug 现象，不知道根因或修改文件；
- 修改跨多个模块、会话或机器；
- Workspace 已有其他改动；
- 涉及数据库迁移、并发、支付、权限或敏感数据；
- 验证耗时长、容易中断或结果可能过期；
- 用户只想阅读重要变更、风险和未证明事项。

## 二、Agent 验收定义

Vega 只有同时满足以下条件，才算 Agent，而不是固定工作流换名：

1. 保存明确、持久的 Goal；
2. 面对模糊问题时先调查；
3. 根据调查事实提出 Plan，并区分事实与假设；
4. 修改前等待人工批准；
5. 根据 Observation 选择下一动作；
6. 验证失败后可以 Repair、Replan 或请求人工；
7. 用户追加指令不会静默覆盖旧 Goal；
8. 中断后能够从持久状态和真实 Workspace 恢复；
9. 未知外部副作用不会被自动重复执行；
10. 最终成功仍由现有确定性门禁裁决。

核心循环：

```text
Goal
  → Observe
  → Decide
  → Act
  → Reconcile
  → Evaluate
  → Continue / Replan / Human / Finish
```

固定状态机和确定性门禁不影响 Agent 定位。关键在于 Vega 是否根据当前证据选择下一动作，并对
状态、恢复和终态负责。

## 三、架构边界

```text
┌─────────────────────────────────────────────────────────────┐
│                    Vega Supervisor Agent                    │
│ Goal / Plan / Checkpoint / Context / Decision / Resume      │
└──────────────────────────────┬──────────────────────────────┘
                               │ Worker Request
             ┌─────────────────┼─────────────────┐
             │                 │                 │
        Codex Adapter     Claude Adapter     Future Adapter
             │                 │                 │
             └─────────────────┼─────────────────┘
                               │ Worker Observation
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                      Existing Vega Core                     │
│ Workspace → Scope → Verification → Risk → Reviewer → Finish │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Supervisor Agent 负责

- 接收并保存 Goal；
- 调度有界调查；
- 编译事实、假设和 Plan；
- 发起人工批准；
- 生成 Worker Request；
- 读取结构化 Observation，而不是相信 Worker 自述；
- 对账 Goal、Checkpoint、Workspace、进程和 Artifact；
- 在受限动作集合中选择下一步；
- 生成 Resume Context；
- 路由到 Finish 或人工接管。

### 3.2 Worker Adapter 负责

- 启动或连接宿主 Agent；
- 传递 Goal、批准 Plan、范围和验证要求；
- 返回状态、改动、命令和 Artifact 引用；
- 不修改 Vega Goal、Decision、Verification 或 Finish Artifact；
- 不自行宣布 Vega 任务成功。

### 3.3 现有 Vega Core 保持不变

V1 复用现有 Workspace Baseline、Scope Gate、Verification、Risk Gate、独立 Reviewer、Finish、
Stop、Timeout 和进程所有权，不复制实现，也不新增第二套成功语义。

## 四、最小 Agent Loop

```text
accept_goal
    ↓
investigate
    ↓
propose_plan
    ↓
await_approval
    ↓
dispatch_worker
    ↓
reconcile_workspace
    ↓
verify
    ↓
review
    ↓
decide_next
    ├─ repair
    ├─ replan
    ├─ needs_human
    └─ finish
```

| 节点 | 最小职责 |
|---|---|
| `accept_goal` | 保存用户目标、Non-goals、风险和调查预算 |
| `investigate` | 只读调查，输出事实、假设、候选范围和未解决问题 |
| `propose_plan` | 生成修改范围、验证方法和人工检查点 |
| `await_approval` | 绑定 Plan digest，未批准不得写业务代码 |
| `dispatch_worker` | 将单个 active checkpoint 交给一个状态明确的 Child |
| `reconcile_workspace` | 重算真实 Diff、进程和 Artifact，不信任 Worker Claim |
| `verify` | 调用现有结构化验证链 |
| `review` | 调用现有隔离 Reviewer |
| `decide_next` | 根据证据选择 Repair、Replan、Human 或 Finish |

### 4.1 调查与 Plan

除非目标、范围和验证方式已经十分明确，否则默认先调查。调查结果必须区分：

```markdown
## User Goal
## Non-goals
## Observed Facts
## Hypotheses
## Proposed Scope
## Verification
## Risk and Human Review
## Unresolved Decisions
```

事实必须附文件、命令或 Artifact 来源；假设不得包装成已确认根因。重大范围变化必须产生新的
Plan digest 并重新批准。

### 4.2 Worker Observation

最小合同：

```yaml
schema_version: 1
child_run_id: "..."
status: "completed | failed | interrupted | unknown"
changed_files: []
commands_executed: []
claimed_result: "..."
artifact_refs: []
workspace_snapshot_after: "..."
```

`claimed_result` 始终是待核实自述。`changed_files`、命令状态和 Snapshot 必须由 Vega 重算或
校验。Observation 与 Goal、Plan 或 Child 不匹配时拒绝使用。

### 4.3 下一动作

| 动作 | 条件 |
|---|---|
| `repair` | 根因和范围仍成立，存在明确、低歧义修复任务 |
| `replan` | 新证据推翻原假设、范围或验证方法 |
| `rerun_verification` | Workspace 未变化，失败可证明属于环境或瞬时问题 |
| `request_human` | 高风险、未知副作用、状态冲突或证据不足 |
| `finish` | 确定性门禁满足，Reviewer 允许，Artifact 一致 |

不允许无上限重试、删除失败验证、自动降低风险、Reviewer 覆盖测试失败或自动 commit/push。

## 五、模型与规则的边界

模型可以判断：

- 候选根因和调查路径；
- Proposed Plan；
- 修复建议；
- Reviewer Finding；
- 证据缺口的自然语言解释。

确定性代码必须判断：

- Repository Identity；
- Workspace baseline 和 diff；
- 路径范围和变更预算；
- 验证命令状态；
- Artifact 完整性和 freshness；
- 风险路径命中；
- Child 与外部进程状态；
- 是否允许进入 `ready_to_commit`。

原则：

```text
模型决定下一步尝试什么
规则决定结果是否允许通过
```

权威顺序：

```text
实时 Workspace、Git、进程与可核验外部状态
        > 当前有效 Artifact
        > Goal / Decision Ledger
        > Agent Checkpoint
        > 仓库任务记录
        > Resume Capsule / Brief
        > Worker 或 Reviewer 自述
```

## 六、中断、转向与恢复

### 6.1 新用户指令

| 类型 | 含义 | 处理 |
|---|---|---|
| `steer_current` | 对当前 Goal 增加约束 | 保留 Goal，记录方向变化 |
| `pause_and_switch` | 暂停当前任务，处理另一目标 | 刷新安全状态后暂停 |
| `replace_goal` | 放弃或替换原目标 | 人工确认并保留旧记录 |
| `stop` | 终止自动执行 | 保留现场，不伪造完成 |

V1 不并发自动运行多个前台任务。一个 Workspace 同时只允许一个写任务。当前任务存在未提交修改
时，新写任务不得自动混入，也不得自动 stash、commit 或清理。

### 6.2 强制记录时机

控制层必须在以下节点刷新状态，不能只依赖模型主动总结：

1. Goal 创建或变更；
2. Plan 提交、批准、拒绝或失效；
3. 用户 steer、pause、switch 或 stop；
4. Vega 控制的长时间或非幂等操作开始前；
5. 操作完成、失败、超时或中断后；
6. Workspace、Verification、Risk、Reviewer 或 Finish 产生新结果；
7. 宿主准备压缩上下文；
8. 会话恢复并完成 Workspace 对账后。

### 6.3 恢复结果

| 状态 | 处理 |
|---|---|
| `clean_resume` | 现场与最后安全记录一致，可以继续 |
| `workspace_ahead` | 先检查新增 Diff 和 Artifact，不盲目重跑 |
| `operation_unknown` | 操作只有开始记录，禁止自动重跑 |
| `state_conflict` | Goal、Child、记录或 Workspace 冲突，交还人工 |
| `external_process_live` | 原进程仍在运行，等待、停止或人工处理 |

数据库、支付、发布等非幂等副作用出现 `operation_unknown` 时必须 fail-closed。

## 七、上下文和跨机器记录

V1 不复制宿主完整聊天，只保存任务恢复需要的事实：

```text
.vega/tasks/
  YYYY-MM/
    YYYY-MM-DD-task-slug/
      task.md       # 目标、事实/假设、批准 Plan、范围和验收
      state.json    # 最近一次明确记录的可恢复状态
      journal.md    # 有意义的检查点、验证、阻塞和下一步
```

本机派生产物：

- `resume-capsule.json`：从 Goal、任务记录和 Artifact 生成的确定性状态；
- `resume-brief.md`：注入宿主的有界继续说明；
- Deep Context：Plan、报告和日志按引用读取，不自动全部内联。

约束：

- 只使用仓库相对路径，不记录密钥、用户名、机器名或绝对路径；
- `state.json` 不替代当前 Workspace、进程或验证事实；
- `journal.md` 不记录每个 Tool Call；
- Brief 使用确定性模板，不调用额外模型总结；
- 跨机器只恢复 Goal、Plan、Checkpoint 和下一步；
- 历史验证在新机器上必须降级为 historical；
- commit 和 push 继续由用户决定，Vega 不自动执行。

自动注入只发生在新会话、显式 Resume 和宿主压缩后。普通 Prompt 不重复注入完整状态。

## 八、技术选型

### 8.1 LangGraph

LangGraph 只是候选控制引擎，只允许负责 Goal cursor、Checkpoint、Interrupt、Resume 和 HITL。
它不得替换现有 Linear Runtime，不得新增第二套 Goal State、Trace 或成功语义。

实现前先写 ADR，对比：

1. 继续复用现有 Goal + 显式 State Reducer；
2. 使用最小 LangGraph Control Plane。

如果 LangGraph 只增加状态同步和依赖成本，没有减少 Interrupt、Resume 与 HITL 的实现复杂度，
则 V1 不接入。

### 8.2 Pi

Pi 不作为 V1 核心 Runtime，也不把 Vega 定位为 Pi Extension。它只作为轻量 Agent Loop、Session
设计参考和后续可选 Worker Adapter。V1 不引入 TypeScript/Python 双 Runtime，不 Fork Pi。

### 8.3 Memory

V1 不实现 Memory V2、Embedding、向量数据库或自动长期 Memory。现有 accepted memory 只能作为
历史提示，不能证明当前测试通过、替代 Reviewer、降低风险或改变 Finish。

## 九、V1 范围

必须实现：

1. Supervisor Agent Contract；
2. 最小 `Observe → Decide → Act` 循环；
3. 默认调查、Plan 和人工批准；
4. 单 active checkpoint；
5. Worker Observation 合同；
6. `repair / replan / request_human / finish` 路由；
7. steer、pause、中断和恢复；
8. Workspace / Child / Artifact Reconcile；
9. 仓库任务记录和 Resume Brief；
10. 一个真实宿主 Adapter；
11. 真实任务对照和公开脱敏案例。

明确不实现：

- 通用 Agent Runtime、SDK、TUI 或 Provider；
- 多 Worker 并发和默认多 Reviewer；
- Planner、Researcher、Memory Agent 等新增角色；
- daemon、队列、后台自动重试或 Web UI；
- Repository Map、知识图谱或向量检索；
- 自动 commit、push、release、stash、回滚或删除；
- 多 checkpoint 无人值守连续运行；
- 把历史实验直接接入默认成功语义。

## 十、实施 Gate

### Gate 0：合同和 ADR

只修改文档与测试设计：

- 冻结 Agent / Workflow 验收定义；
- 冻结 Goal、Plan、Observation 和 Decision 合同；
- 完成 LangGraph 与现有 Goal Reducer ADR；
- 预注册一个真实模糊 Bug 和一个中断恢复案例；
- 定义基线指标和停止条件。

### Gate 1：最小 Agent Loop

使用 Fake Worker 和临时仓库证明：

- 模糊目标先调查；
- Plan 未批准不能写；
- Observation 能驱动 Repair、Replan、Human 和 Finish；
- 验证失败不能成功；
- 同一 Checkpoint 不重复创建 Child；
- 现有 Core 测试保持通过。

Gate 1 不接真实宿主，不增加第二个 Runtime。

### Gate 2：恢复和真实宿主

只选择一个宿主 Adapter，优先复用现有 Codex runner。必须验证：

- steer 不覆盖原 Goal；
- pause 后能够恢复原任务；
- Workspace 超过 Checkpoint 时不会盲目重跑；
- 操作状态未知时停止；
- 新会话能够生成正确 Resume Brief；
- 一个真实 Bug 完成调查、Plan、修改、失败处理、Reviewer 和 Finish；
- 输出完整脱敏 Artifact 和复现步骤。

### Gate 3：真实价值判断

在多个真实任务中比较：

```text
单独使用宿主 Agent
vs
宿主 Agent + Vega Supervisor
```

重点回答：

1. Vega 是否发现了宿主没有稳定暴露的实际问题；
2. 是否减少人工重新解释任务和查找高风险 Diff 的次数；
3. 是否避免过期验证、错误归属或重复副作用；
4. 是否显著增加 Token、时间和操作负担；
5. 用户是否愿意在下一次类似任务中继续使用。

只有 Gate 3 证明真实收益，才修改 README 定位、增加第二个 Adapter 或发布 Agent 版本。

## 十一、验收与指标

至少覆盖：

| 场景 | 必须结果 |
|---|---|
| 用户只描述 Bug 现象 | 先调查并区分事实与假设 |
| Plan 未批准 | 不允许写业务代码 |
| Worker 声称完成但没有 Diff | 不进入验证成功 |
| Worker 修改越界 | Scope Gate 阻止成功 |
| 测试失败但 Reviewer approve | 保持失败或人工接管 |
| 验证后 Workspace 再变化 | 原验证标记过期 |
| Reviewer request_changes | 产生有界 Repair Checkpoint |
| 新证据推翻根因 | Replan，不重复原修复 |
| 用户中途增加限制 | 记录 steer，保留原 Goal |
| 用户切换任务 | 暂停旧任务，不混合写现场 |
| Child 状态未知 | 不启动替代 Child |
| 操作开始后崩溃 | 恢复为 `operation_unknown` |
| 新会话继续 | 对账后生成 Resume Brief |
| 跨机器继续 | 历史验证降级并重新检查 Workspace |
| 高风险路径命中 | 列出人工必须检查的位置 |
| 完整证据通过 | Finish 可输出 `ready_to_commit` |

至少记录：

- 调查后首次 Plan 接受率；
- 首次修改通过验证的比例；
- Repair / Replan / Human 的分布；
- Resume 后正确识别 Checkpoint 的比例；
- 重复 Worker 或重复外部副作用次数；
- 过期证据拒绝率；
- Reviewer 实际 Finding 和误报；
- 用户最终需要阅读的 Diff 与报告长度；
- Token、时间和人工操作成本；
- 相比单独使用宿主 Agent 新发现的问题。

评估必须保留失败和 `needs_human`，不得只展示成功案例。

## 十二、停止条件和交付规则

出现以下任一情况，停止扩大 Agent V1：

1. Agent Loop 只是固定流水线换名；
2. 无法说明相对 Codex / Claude Code 的额外价值；
3. LangGraph 造成第二套 Goal、Trace 或成功语义；
4. Resume 可能重复状态未知的副作用；
5. Agent 模式降低现有 fail-closed 约束；
6. 为实现 Agent 被迫重写 Verification、Risk、Reviewer 或 Finish；
7. 第一个 Adapter 不稳定，却开始增加更多 Adapter；
8. 真实任务没有降低人工恢复或风险检查成本；
9. 成本显著增加但没有新增缺陷发现；
10. 项目再次扩展通用 Agent Runtime、Web UI 或多 Agent 平台。

交付规则：

- 本计划可以作为主线文档提交，但不授权实现；
- 实现阶段不为每个小步骤创建分支；
- 一个实验分支持续完成一个完整 Gate，通过后再决定是否提 PR；
- PR 合并后删除远端实验分支；
- 纯文档改动至少执行仓库卫生检查和 `git diff --check`；
- Vega 不自动 commit、push 或 release。

Gate 2 前继续使用当前表述：

> Vega 是 AI 编码的验证与独立评审 Harness。

Gate 3 通过后，候选表述为：

> Vega 是一个可恢复、证据驱动的软件工程 Supervisor Agent。它组织调查、Plan、人工批准、
> 代码执行、Workspace 对账、确定性验证和独立评审，证据不足时停止交还人工。

候选简历表述只有在对应能力和真实任务证据完成后才能使用。

## 十三、文档关系和审核后第一步

- [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md)：当前产品行为和红线；
- [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md)：调查、事实/假设和人工批准；
- [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md)：Goal P1 和单 checkpoint 恢复边界；
- [`ARCHITECTURE.md`](ARCHITECTURE.md)：当前已实现架构；
- [`ROADMAP.md`](ROADMAP.md)：当前主线优先级，Agent 获批后再更新；
- [`ASSURANCE-CONTRACT-CANDIDATE.md`](ASSURANCE-CONTRACT-CANDIDATE.md)：风险与证据实验，不自动接入成功语义。

本文与现有产品事实冲突时，以已发布代码、`PRODUCT-CONTRACT.md` 和真实运行证据为准。

用户审核通过前不修改 Runtime。审核通过后的第一步为：

1. 编写 Agent / Workflow 验收测试；
2. 冻结一个真实模糊 Bug 和一个中断恢复案例；
3. 编写 LangGraph 与现有 Goal Reducer ADR；
4. 使用 Fake Worker 实现 Gate 1；
5. Gate 1 通过后再决定具体控制引擎；
6. 未证明真实收益前不修改 README 定位或发布 Agent 版本。
