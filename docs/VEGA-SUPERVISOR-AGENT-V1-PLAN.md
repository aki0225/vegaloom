# Vega Supervisor Agent V1 实施计划

> 状态：`revised / awaiting-owner-approval`
>
> 计划日期：2026-08-11
>
> 规划基线：`main@40a273d` / `v0.1.5`
>
> 本文只定义下一阶段 Agent 实验。它不改变当前 `vega do / loop / goal` 的默认行为，
> 也不代表主线已经具备本文描述的 Agent 能力。

## 一、产品决定

Vega 下一阶段候选方向是：

> **一个本地优先、可恢复、证据驱动的软件工程 Supervisor Agent。**

外部 Coding Agent 继续负责读代码、调用工具和修改文件；Vega 负责把模糊或长程工程目标组织为
可恢复的 Goal、WorkPlan、Context、Working Memory、Observation 和 Decision；现有 Vega Core
继续裁决 Workspace、Scope、Verification、Risk、Reviewer 和 Finish。

```text
用户目标
  → Supervisor 只读调查
  → 生成多步骤 WorkPlan
  → 人工批准范围、风险和预算
  → 为当前 Work Item 编译最小必要上下文
  → 外部 Coding Agent 执行一个有界 child loop
  → 对账 Workspace、验证、风险和独立 Reviewer
  → 更新工作记忆
  → 继续下一项 / Replan / Human / Finish
```

### 1.1 Agent 的验收定义

V1 必须同时满足：

1. 持久化 Goal、Non-goals、约束、成功条件和预算；
2. 面对模糊目标时先使用只读工具调查；
3. 生成可批准、可分步执行的 WorkPlan，并区分事实与假设；
4. 根据最新 Observation 动态选择下一动作，而不是只跑固定流水线；
5. 用户新指令形成 Goal/Plan revision，不静默覆盖旧目标；
6. 关键事实、失败尝试和开放子目标形成有来源、可失效的 run-local 工作记忆；
7. 每次模型调用从持久状态重新编译上下文，不依赖完整聊天历史；
8. 控制进程或模型会话中断后，先对账原 child 和 Workspace 再继续；
9. 未知副作用不自动重放，过期批准不继续使用；
10. 最终成功仍由现有确定性门禁裁决。

核心循环是：

```text
Goal → Compile Context → Observe → Decide → Act → Reconcile
     → Update Working Memory → Continue / Replan / Human / Finish
```

如果最终只是把当前 `brief → worker → verify → review → finish` 顺序放入 `StateGraph`，
没有真实的上下文重建、条件路由、工作记忆和恢复价值，则实验失败，Vega 继续保持 Harness 定位。

### 1.2 与当前主线的关系

- `vega do`、`vega loop` 和 `vega goal` 保持 Linear Runtime 和现有成功语义；
- 新入口 `vega agent` 必须显式 opt-in；
- Agent 编排现有 Core，不复制 Verification、Risk、Reviewer 或 Finish；
- Gate 3 没有证明真实增量价值前，不成为默认入口；
- 小改动、纯探索和临时原型继续直接使用宿主 Agent，不必经过 Vega Agent。

## 二、设计原则、参考与角色边界

### 2.1 五条固定原则

1. **文件状态优先于聊天历史**：会话可以压缩或消失，持久文件必须能重建下一步。
2. **上下文按角色编译**：Supervisor、Worker、Reviewer 不共享一整包聊天。
3. **工具输出先成为 Observation**：不可信正文不能直接改 Goal、Plan 或 Memory。
4. **模型提出，规则裁决**：模型选择下一步；确定性代码决定能否执行和成功。
5. **单一业务状态权威**：LangGraph checkpoint 只保存图游标，不拥有业务成功事实。

### 2.2 吸收开源项目的优点

| 来源 | 吸收 | 不照搬 |
|---|---|---|
| LangGraph | `StateGraph`、checkpoint、interrupt/resume、streaming | Store、Agent Server 和第二套业务状态 |
| mini-SWE-agent | 极小循环、透明轨迹、明确预算 | 任意 Bash 作为全部安全边界 |
| Aider | 预算化上下文和 Diff 可见性 | Repo Map、自动 commit |
| Pi | 生命周期事件、steer、恢复思想 | TUI、Provider/OAuth 平台和会话树 |
| OpenHands | Agent、Workspace、Action/Observation 分层 | 远程平台、队列和 Canvas |
| Codex / Claude Code / Goose | 通过稳定合同接入成熟 Coding Agent | 重写模型工具循环或绑定唯一厂商 |

旧 `experiment/langgraph-comparison` 已证明 checkpoint、HITL 和副作用对账值得保留，也证明默认
替换 Linear Runtime、默认多 Reviewer、同时建设 FastAPI/SSE/Memory 平台不值得继续。V1 只
选择性参考结论，不整体迁移旧分支。

### 2.3 四个责任主体

**Supervisor**：版本化 Goal，使用只读工具调查，形成 Plan，编译上下文，维护工作记忆，发起
HITL，并在受限动作集合中决定下一步；它不直接写业务代码。

**Agent Host Adapter**：连接 Codex、未来的 Claude Code 或 Pi，传递有界请求，启动/检查/停止
外部 Agent，并输出结构化结果和安全事件。它不是通用 Provider SDK。

**Vega Core**：继续拥有 Workspace Baseline、Scope Gate、Verification、Risk Gate、独立
Reviewer、Finish、Stop、Timeout、Recover、Run Lock 和进程所有权。

**人类**：批准 Plan，处理高风险、状态冲突和最终提交。Vega 仍不自动 commit、push 或 release。

Reviewer 继续与 Worker 会话隔离。它可以读取批准的 Goal/Plan、项目规则、完整 changed files、
Diff 和门禁证据，但不接收 Worker transcript、Supervisor 内部推理或未经验证的 Memory。

## 三、Goal、WorkPlan 与 LangGraph

### 3.1 Goal 和 WorkPlan

Goal 是用户意图的版本化权威记录，至少包含：

- objective、non-goals、constraints 和 success conditions；
- 必须人工处理的 checkpoint；
- Work Item、Replan、Worker attempt、时间和 Token 总预算；
- 用户原始指令的 artifact ref。

用户增加限制时产生新 revision。若变化影响范围、风险或验证，当前 Plan 自动失效并重新批准。

WorkPlan 由少量串行 Work Item 组成，每项包含：

```yaml
id: "WI-01"
objective: "可独立检查的结果"
depends_on: []
allowed_paths: []
verification: []
risk_notes: []
requires_human_before_start: false
status: "pending | active | passed | failed | blocked | superseded"
```

Plan 还保存 observed facts、hypotheses、unresolved decisions、Goal revision 和 `plan_sha256`。
V1 同一时间只有一个 active child。只有上一个 child 已产生可校验终态、Workspace/策略未漂移、
下一项仍在批准范围且预算有余量时，Agent 才能自动进入下一 Work Item。

为减少人工 Review 的重新搜索成本，WorkPlan 或后续 Observation 可以携带少量可选
`impact_hypotheses`，每项只记录 category、statement、evidence refs 和 required checks。它们只是
待核实假设，不新建独立 Impact Ledger，不宣称完整召回，也不参与成功门禁。Finish 若展示，
必须附代码或 artifact 来源；证据尚不足的项目只能进入“未核实”，不能写成“已确认影响”。

候选默认上限是四个 Work Item、每个 child 最多两次现有 Worker iteration、最多一次 Replan，
总 deadline 四小时；Gate 0 冻结最终值。该上限足以验证数小时任务，但不会变成无限自治。

### 3.2 最小 Graph

```text
START
  ↓
reconcile
  ├─ no_plan ───────────────→ investigate_plan
  ├─ approval_required ─────→ await_approval
  ├─ ready_work_item ───────→ dispatch_child
  ├─ child_terminal ────────→ reduce_and_route
  ├─ all_items_done ────────→ finalize
  └─ conflict / unknown ────→ request_human

investigate_plan → await_approval
await_approval   → reconcile | investigate_plan | END(rejected)
dispatch_child   → reconcile

reduce_and_route
  ├─ deterministic_continue ─→ reconcile
  ├─ deterministic_human ────→ request_human
  ├─ deterministic_finish ───→ finalize → END
  └─ ambiguous ──────────────→ supervisor_decide

supervisor_decide
  ├─ continue_plan ──────────→ reconcile
  ├─ replan ─────────────────→ investigate_plan
  ├─ request_human ──────────→ request_human
  └─ finish ─────────────────→ finalize → END

request_human → reconcile | investigate_plan | END(stopped)
```

节点保持窄：

- `reconcile` 读取真实 Goal、Plan、child、Workspace、进程和 artifact，校验 identity、integrity
  与 freshness；
- `investigate_plan` 编译规划上下文，调用一次只读 Supervisor operation；
- `await_approval` 使用 LangGraph `interrupt()`；
- `dispatch_child` 只启动当前 Work Item 对应的现有 child loop；
- `reduce_and_route` 归一化 Observation、更新 Memory，并优先按确定性规则继续、结束或交还人工；
- `supervisor_decide` 只处理新证据推翻 Plan、失败原因无法机械分类或用户改变目标等歧义；
- `request_human` 保存阻断原因、可选动作和恢复命令后 interrupt；
- `finalize` 调用现有确定性 Finish，Graph `END` 本身不表示成功。

Repair 留在现有 child loop。父 Agent 不拆开 Core iteration，也不绕过 child 直接修改文件。

### 3.3 确定性预路由与 Supervisor 决策

child 结束后先执行确定性预路由：

| 条件 | 动作 |
|---|---|
| child 通过、下一项已批准且现场未漂移 | `continue_plan` |
| child 内仍有现有 Repair 预算 | 继续由 child loop 处理，不返回父层决策 |
| Workspace/operation 未知、Scope/Risk 阻断或预算耗尽 | `request_human` |
| 全部 Work Item 完成且 Finish 前置条件满足 | `finish` |
| 新证据与 Plan 冲突、失败无法分类或用户改变目标 | 调用 Supervisor |

只有最后一类情况才编译 Decision Context 并调用模型：

```json
{
  "action": "continue_plan | replan | request_human | finish",
  "reason": "...",
  "evidence_refs": [],
  "memory_delta": {"add": [], "update": [], "invalidate": []},
  "plan_change_reason": null
}
```

模型结果仍受确定性规则否决：Verification 失败、Scope/Risk 阻断、Artifact 不一致、仍有 pending
item、child 状态未知或预算耗尽时，模型不能选择 `finish` 或重试未知 operation。

### 3.4 LangGraph 的限定职责

V1 必须真实使用：

- `StateGraph` 和条件边；
- run-local SQLite checkpointer；
- `interrupt()` / `Command(resume=...)`；
- 节点和安全自定义事件 streaming。

Graph State 只保存 `agent_run_id`、Agent state version/hash 和 pending interrupt。Git、进程、
Workspace、验证结果、Memory 正文和成功状态都不由 checkpoint 拥有。

## 四、上下文怎样编译、注入和维护

上下文是 V1 的核心能力，不是把所有历史拼成超长 Prompt。

### 4.1 七层来源

| 层 | 内容 | 处理 |
|---|---|---|
| L0 系统合同 | 角色、动作枚举、安全规则、输出 schema | 每次必注入 |
| L1 用户意图 | 当前 Goal、约束、成功条件、人工决定 | 版本化、每次必注入 |
| L2 项目规则 | 相关 `AGENTS.md`、`.vega.yaml`、Project Profile | 按路径选择 |
| L3 当前 Plan | 当前 Work Item、范围、预算和依赖 | 每次必注入 |
| L4 实时现场 | HEAD、Workspace fingerprint、Diff 路径、child/进程 | 调用前重算 |
| L5 Working Memory | 已确认事实、失败尝试、开放问题 | 选择性注入 |
| L6 深层证据 | 文件片段、日志、验证和 Reviewer artifact | 默认摘要加 ref |

完整聊天、Worker transcript、内部思维和所有历史日志不属于可依赖上下文。

### 4.2 Context Pack

V1 只定义一个 Context Pack schema，`planning / worker / decision / resume` 是同一 schema 的四种
`purpose`，区别只在编译策略。每次外部 Agent 调用前，Context Compiler 生成 manifest，至少记录：

- `purpose = planning | worker | decision | resume`；
- Goal/Plan/Memory revision；
- 当前 Work Item、Workspace fingerprint 和 policy digest；
- 每个 section 的 source refs、required 标记和内容 digest；
- 因预算未内联但仍可读取的 `omitted_refs`；
- 最终 rendered digest。

manifest 只证明“本次调用看到了哪些来源”，不复制第二套业务事实。run 内可保存脱敏 rendered
pack 便于复盘；密钥、Authorization、绝对本机路径和未脱敏工具正文不得写入。

### 4.3 按角色注入

**规划 Supervisor**：Goal、项目规则、Project Profile、实时 Workspace、相关 Memory、开放问题
和只读工具合同。它不获得旧 Worker transcript。

**Worker**：唯一 Work Item、精确范围、禁止项、验证、风险、相关事实/失败尝试、项目规则和
Workspace baseline。需要更多代码时由 Worker 使用自身工具读取，不预塞整个仓库。

**决策 Supervisor**：Goal/Plan、child Observation、完整 changed files、Diff 摘要、Verification、
Risk、Reviewer、相关 Memory、预算余量和动作枚举。

**Reviewer**：继续使用现有独立 Review Pack，只补批准的 Goal/Plan 和项目规则，不注入 Worker
对话或未经验证的 Memory。

**Resume**：不用模型总结。它从状态确定性生成已完成项、当前项、最后安全 checkpoint、未知
现场、阻断原因、下一条允许命令和深层证据引用。

### 4.4 编译算法和预算

固定优先级是 L0 → L1 → L3 → L4 → 相关 L2 → 相关 L5 → L6。系统合同、Goal、当前 Work Item
和安全阻断不得截断。可选内容超预算时依次降级：

```text
完整片段 → 相关片段 → 结构化摘要 + artifact ref → 只保留 ref
```

V1 不用向量检索或全仓 Repo Map。相关性先依据 Work Item、路径范围、符号/关键词、Plan
revision、risk domain 和显式 evidence ref。必需上下文本身超预算时进入
`context_budget_exceeded`，不能静默丢掉约束。

只在规划、Plan/Decision 变化、Work Item 派发、child 终态、用户 steer、Workspace/策略变化和
恢复对账后重新编译；不在每个 Tool Call 后重建整包，也不重复注入完整历史。模型会话可以是
短生命周期，会话压缩或消失不影响下一次重建。

## 五、工具调用和 Adapter

### 5.1 两类工具

**Vega 确定性工具**由 Core 直接执行：Repo Identity、Git/Workspace 对账、项目规则与 scope
解析、artifact 校验、Verification、Risk、Reviewer、Finish、lock、stop 和 recover。

**宿主 Agent 工具**留在 Adapter 内：Supervisor 使用文件读取、列表、搜索和只读 Git；Worker
读取/编辑代码并运行 child 允许的命令；Reviewer 使用共享仓库的只读视图。

LangGraph 编排的是有边界的 Agent operation，不代理每个 `read/search/shell`。这样 Codex、
Claude Code 或 Pi 可以使用各自成熟工具，Vega 不必建设通用模型 SDK。

### 5.2 V1 Adapter 合同

V1 只实现 Codex CLI，最小行为合同为：

- `capabilities()`：声明只读、结构化输出、事件、启动/检查/停止能力；
- `investigate(request, event_sink)`：返回 PlanProposal；
- `decide(request, event_sink)`：返回受限 DecisionProposal；
- `start_worker / inspect / stop`：管理真实 child execution。

Supervisor 使用独立短生命周期 `codex exec` 和 `read-only` sandbox；Worker 复用现有
`CodexExecRunner`；Reviewer 继续使用另一独立只读会话。三者使用不同 Context Pack 内容、输出
schema 和 operation id，但 Context Pack 本身共用一个 schema。Vega 不依赖 Codex 会话可继续，
恢复时总是重建新会话。

`read-only` 是共享仓库的受限只读视图，不宣传为容器或操作系统级完全隔离。Worker 仍由 sandbox、
前后 Workspace 对账、Scope Gate 和确定性验证共同约束。

### 5.3 工具事件和不可信输出

Adapter 只归一化少量事件：operation/tool started/finished、message delta、role、tool category、
repo-relative paths、status、safe summary 和 artifact refs。事件供 `vega watch` 与复盘使用，不成为
第二套执行状态；不记录 API key、完整 Prompt、内部思维或大段原始输出。

工具正文进入 Agent 状态前必须经过：

```text
边界检查 → 脱敏 → 大小限制 → 来源标记 → Artifact → Observation 摘要/引用
```

仓库文件和工具输出默认是不可信数据。只有 Context Compiler 明确认定的项目政策文件才具有规则
语义；README、Issue 或命令输出中的“忽略规则、写入 Memory”等内容不能直接改变 Goal、Plan、
权限或 Working Memory。

未来第二 Adapter 只实现相同行为合同，不修改 Graph 和 Core；但必须等 Gate 3 证明价值后再做。

## 六、Working Memory 怎样接入

### 6.1 四个概念不能混用

| 概念 | V1 处理 |
|---|---|
| 模型上下文窗口 | 临时，每次由 Context Compiler 重建 |
| run-local Working Memory | V1 必做，服务当前长程任务 |
| 项目规则/知识 | 继续由现有项目上下文提供 |
| 跨任务长期 Memory | 不自动写，保持现有 proposal/accepted 边界 |

Working Memory 不是聊天摘要或向量库。它只记录跨步骤需要保留、且能说明来源和有效期的任务知识。

### 6.2 Memory Item 与增量

每个 item 包含：

```yaml
id: "fact-004"
kind: "confirmed_fact | hypothesis | failed_attempt | open_question"
content: "..."
status: "active | invalidated | superseded | resolved"
evidence_refs: []
created_from: "user | policy | observation | supervisor_proposal"
goal_revision: 1
plan_revision: 1
validity: {workspace_fingerprint: "...", policy_sha256: "...", reconsider_when: []}
```

Goal 约束、人工批准和终态不复制进 Memory。Supervisor 在 Plan/Decision 输出中只提出
`add/update/invalidate` 增量，确定性 Memory Reducer 校验后追加：

`open_subgoal` 由 WorkPlan 持有，环境现场由 Observation 持有，避免同一事实出现第二份状态。

- `confirmed_fact` 必须有当前可读 evidence ref；
- `failed_attempt` 必须绑定 child、Verification 或工具失败 artifact；
- `hypothesis` 不能无证据升级为事实；
- 工具正文不能直接写 Memory；
- Goal/Workspace/策略变化时重新验证 validity，不直接删除旧 item；
- 环境恢复后可以重新考虑失败方案，但必须记录失效原因；
- invalidate/supersede 保留历史，不能原地抹掉。

Core 可以直接产生确定性 item，例如“WI-01 验证失败”，仍必须绑定 artifact，不额外调用模型。

### 6.3 Selective Recall 与容量

注入顺序是：当前 Work Item 显式引用 → 路径/符号/risk/动作匹配 → 未解决问题 → 可防止
重复失败或目标漂移的 failed attempt。其余 item 只保留 artifact ref。

同一提醒最近三个决策边界已经注入时默认不重复，除非风险为高或现场已变化。Context Manifest
记录实际使用的 memory id，便于判断它是否减少重复调查，而不是只统计 Memory 数量。

Gate 0 冻结小型上限；V1 候选值为所有类型合计最多 32 个 active item。超限时拒绝低价值
proposal，保留已确认事实、失败尝试和阻断问题并发出容量告警，不让模型生成一份“大总结”
覆盖历史。

run 结束时可以生成长期 Memory proposal，但不得自动 accepted、跨 repo 回填、替代当前测试或
改变 Finish。Agent V1 的成功不依赖长期 Memory。

## 七、状态权威、HITL 与恢复

### 7.1 状态所有者

| 所有者 | 唯一职责 |
|---|---|
| Git / Workspace / OS 进程 | 当前外部事实 |
| child state/execution/artifacts | Worker、验证、风险和 Reviewer 执行事实 |
| Agent `state.json` | 当前 Goal/Plan/Work Item、child binding、operation、预算、next action 和终态 |
| Goal / Plan | 用户意图和批准方案的版本化事实 |
| Working Memory ledger/snapshot | run-local 知识、来源、版本和失效状态 |
| Decision Ledger | 对指定 Plan/interrupt 的一次性人工决定 |
| LangGraph SQLite | 图游标、Agent state version/hash 和 pending interrupt |
| Finish artifacts | 是否允许 `ready_to_commit` |

冲突优先级：

```text
实时 Workspace / Git / 进程
  > 可校验 child artifacts
  > Goal / Plan / Agent state / Decision / Memory ledger
  > LangGraph checkpoint
  > Resume Brief
  > 模型自述
```

Agent state 使用现有 `RunMutationLock` 和单调 `state_version` 做 compare-and-swap；它引用当前
Goal/Plan/Memory digest、active Work Item、operation、bound child、Workspace/policy digest、预算
和 pending interrupt。Graph checkpoint 不复制这些正文。

### 7.2 Artifact 布局

```text
runs/<agent_run>/
  state.json                 goal.json / goal.md
  trace.jsonl                progress.jsonl
  plans/                     decisions.jsonl
  context/manifests/         context/rendered/
  working-memory.json        memory-deltas.jsonl
  operations/                observations/
  repo-identity.json         agent-checkpoints.sqlite
  resume-brief.md            final-report.md / eval.md
```

不创建 `.vega/tasks`、第二份 journal 或数据库服务。Memory snapshot 由 append-only delta 派生；
详细代码、日志和 child 证据留在原 artifact，只用 ref 关联。

### 7.3 Checkpoint 与 HITL

只在 Goal/Plan/Decision 变化、Work Item 开始、operation 准备/绑定/终态、child 和门禁新证据、
Memory delta、Supervisor 决策、用户 pause/stop 及 Finish 前后改变业务 checkpoint。不在每个 token
或 Tool Call 后 checkpoint；高频进度只追加 event。

`interrupt()` payload 绑定 Goal/Plan revision、Plan digest、范围、风险、验证、预算、Workspace
fingerprint 和 policy digest。恢复使用 `Command(resume={decision_id: ...})`，而不是裸布尔值。
任一 binding 变化都使旧批准失效。Decision Ledger 追加 recorded/consumed 事件，重复摘要幂等复用，
冲突进入 `state_conflict`。

LangGraph 恢复 interrupt 会从节点开头重跑，因此 interrupt 前禁止启动 Worker 或执行其他
非幂等副作用。

### 7.4 Operation ID 与 child

`dispatch_child` 是父 Graph 唯一主动产生 Workspace 副作用的节点。父 Agent 先保存包含 Plan、
Work Item、Workspace baseline 和预分配 child id 的 `operation prepared`，再执行：

```text
parent prepared
  → child 初始 state 写入 parent/operation/work-item binding
  → parent child_bound
  → child Worker execution started
  → child terminal evidence
  → parent operation terminal
```

稳定双向 identity 用于恢复，但不声称数据库事务或 exactly-once：

| 现场 | 动作 |
|---|---|
| prepared、child 不存在、Workspace 未变 | 用原 child id 启动原 operation |
| child 已初始化且 binding 一致 | 只恢复原 child |
| child/owned process 仍存活 | 等待、显示进度或人工停止 |
| child 已终态且证据一致 | 复用结果 |
| Workspace 前进但 child 不完整 | `workspace_ahead`，交给人工 |
| operation started 但无可靠终态 | `operation_unknown`，禁止重试 |
| Graph、Agent、child 或 Workspace 冲突 | `state_conflict`，禁止继续 |

### 7.5 Resume 算法和长程能力

`vega agent resume` 固定执行：

1. 获取 controller lease 和 run lock；
2. 读取 Agent、Goal、Plan、Memory、Decision 和 Graph checkpoint；
3. 重算 repo identity、HEAD、Workspace、policy 和存活进程；
4. 校验 active operation 与 bound child；
5. 将旧 Verification/Reviewer 按 freshness 降级；
6. 重放 Memory delta 并核对 snapshot hash；
7. 确定性生成 Resume Brief 和新 Context Manifest；
8. 状态可解释时才 `Command(resume=...)`，否则请求人工。

因此模型会话或控制进程结束后，Agent 仍知道已完成哪些 Work Item、当前 child 是否真实执行过、
哪些事实仍有效和下一步是否仍被批准。V1 不引入 daemon：无人值守运行时前台 controller 必须存活；
controller 中断后的承诺是安全恢复，不是后台继续。

## 八、CLI、可见性与宿主体验

```powershell
vega agent start --repo . --input task.md
vega agent status --run <agent_run>
vega agent resume --run <agent_run> --decision approve
vega agent resume --run <agent_run> --instruction "新增约束，重新生成 Plan"
vega watch --run <agent_run> --follow
vega stop --run <agent_run> --reason "人工停止"
```

V1 不增加专属 `pause/stop` 或独立 planner、researcher、memory、handoff、task 命令组；停止和观察
复用现有 `vega stop`、`vega watch`。前台和 `watch` 至少显示：

- 当前 Graph node、Work Item、耗时、预算和 deadline；
- 调查、等待批准、Worker、heartbeat、工具类别和终态；
- 完整 changed files 列表；
- Verification、Risk、Reviewer 和 Finish；
- interrupt 原因、已知/未知现场和下一条允许命令。

不显示内部思维、未脱敏 Prompt、凭据或完整工具正文。复用现有 `RunProgressLog`、Codex JSONL
安全事件和 `vega watch`，不建第二套事件总线。

未来 Codex/Claude/Pi 可以提供薄 Skill，只负责启动 CLI、转发安全进度和提交用户选择；状态始终
由 Vega run 持有。即使宿主对话压缩，Vega 仍能从 run 恢复。

## 九、V1 范围

必须实现：

1. LangGraph StateGraph、条件边和 SQLite checkpoint；
2. Goal、多步骤 WorkPlan 和绑定摘要的 HITL；
3. Context Compiler、一个 Context Pack schema、四种 purpose 和 manifest；
4. run-local Working Memory、增量 reducer、失效和 selective recall；
5. 一个 Codex Adapter、只读调查和安全事件；
6. 单 active child、operation identity 和恢复对账；
7. child 内 Repair，父层 continue/replan/human/finish；
8. 可见进度、Resume Brief 和三个真实案例。

明确不实现：

- 多 Worker 并行、多 Reviewer fan-out 或角色群聊；
- 独立 Planner/Researcher/Memory Agent；
- Claude、Pi、OpenHands 第二 Adapter；
- 通用 Provider SDK、模型路由、Agent Server 或远程平台；
- Web UI、TUI、daemon、队列、FastAPI 或 SSE 服务；
- 向量数据库、Embedding、知识图谱或全仓 Repo Map；
- 独立 Impact Ledger 或未经新证据验证的影响面检索 Runtime；
- 跨任务自动长期 Memory、跨机器自动迁移；
- 自动 stash、commit、push、release、回滚、删除或部署；
- 重写 Linear Runtime、Verification、Risk、Reviewer 或 Finish。

## 十、实施 Gate 0-3

原有四个 Gate 保留。每个 Gate 形成明确结论后再进入下一 Gate，不并行扩建后续能力。

### Gate 0：合同冻结

只做设计、ADR 和测试清单：

- 冻结 Goal、WorkPlan、Agent State、Context Manifest、Observation、Memory、Decision 和 Operation；
- 冻结 Context 优先级、必需项、预算不足和敏感信息规则；
- 冻结 Memory 写入、升级、失效、容量和 selective recall；
- 冻结 Agent state、LangGraph checkpoint、interrupt 和 child dispatch 的故障窗口；
- 冻结 Codex Adapter 行为与安全事件；
- 冻结三个真实案例、基线、模型、预算、指标和停止条件。

只需要三份小 ADR：状态权威；Context/Memory；Adapter/副作用恢复。不要写成通用 Agent 规范库。

### Gate 1：最小真实 LangGraph

使用 Fake Supervisor、Fake Adapter 和临时仓库实现：

- StateGraph、SQLite 跨进程恢复和 Plan approval interrupt；
- 两个串行 Fake Work Item，证明不是单次流水线；
- 同一 schema 下 planning/worker/decision/resume 四种 Context purpose；
- Memory delta、证据升级、失效、去重和容量保护；
- 确定性预路由，以及歧义场景下的 continue/replan/human/finish 条件边；
- Fake child 内一次现有 Core Repair；
- dispatch、child binding 和 state/checkpoint 故障注入；
- 未知副作用不重放、过期批准不接受、Graph END 不绕过 Finish；
- 密钥、绝对路径和 Prompt Injection 不进入可信 Memory 或公开 artifact。

Gate 1 不接真实 Codex，不新增第二 Adapter，也不复制旧实验的完整测试矩阵。

### Gate 2：真实 Codex 与长程恢复

正式案例前先运行一次不计入结论的 Vega 自身纵向 integration smoke，只用于发现 Adapter、Context、
Memory 和 Graph 的接线缺陷。三个正式案例及门槛仍在 Gate 0 冻结；smoke 后若必须修改合同，应退回
Gate 0 追加 amendment，不能根据 smoke 结果事后调整正式案例。

完成三个冻结案例：

1. **模糊跨模块 Bug**：Supervisor 使用只读工具调查，Plan 有至少两个依赖 Work Item，并由真实
   新证据触发一次 `continue` 或 `replan`；
2. **多步骤长任务**：至少两个 child，在安全故障窗口中断 controller；新进程找回原 child、
   Work Item 和 Memory，不重复启动；
3. **目标变化或风险接管**：用户增加约束或新证据使旧方案失效；旧批准失效，失败方案被正确
   提醒，必要时交还人工。

每个案例冻结任务、repo revision、模型、推理强度、顺序、验证、timeout 和成本。至少一个是
运行前尚未解决的真实日常任务。失败、超时和 `needs_human` 原样保留。

还必须证明进度可见、宿主会话全部丢失后可恢复、Context 不线性复制完整历史、Memory 能解释
已确认事实/失败方案/未完成事项，且 Reviewer 仍不继承 Worker transcript。

### Gate 3：真实增量价值

比较：

```text
同一 Codex Worker + 当前 Vega Harness
vs
同一 Codex Worker + Vega Supervisor Agent
```

A/D 从同一 commit 创建独立 worktree，固定配置、模型、验证、timeout 和 Worker 预算，运行前冻结
顺序。Agent 组至少在两个案例中提供当前 Harness 没有稳定提供的价值：更正确的调查范围、合理
Replan、避免重复 Worker、拒绝过期批准、减少重复失败/目标漂移，或降低长任务恢复解释成本。

人工 Review 减负只作为 Gate 3 实验指标，不新增 Runtime 状态。至少记录
`human_review_minutes`、`human_opened_files` 和 `unsupported_impact_count`；只有事先独立
冻结了 Golden impact 的案例才计算 `impact_golden_recall`。可选 impact hypothesis 未被代码或
artifact 支持时必须计入 unsupported，不能因结构化输出而视为已验证。

无增量价值的案例，Token、耗时或人工步骤任一项超过基线 `1.5x`，判定为不值得使用，不能由
其他成功案例抵消。

## 十一、指标、停止条件与下一步

### 11.1 硬门槛

- `false_success = 0`；
- `duplicate_worker_start = 0`；
- `unknown_side_effect_auto_retry = 0`；
- `stale_approval_accepted = 0`；
- `untrusted_tool_output_promoted_to_fact = 0`；
- `goal_constraint_silently_dropped = 0`；
- 至少一次跨进程 Resume；
- 至少一个两项以上 WorkPlan 完成或正确中止；
- 三个案例都产生合同允许、可解释的终态。

同时记录 Context/Memory token、omitted refs、上下文增长斜率、重复失败、stale reminder、人工重新
解释次数、`human_review_minutes`、`human_opened_files`、`unsupported_impact_count`、
Supervisor/Worker/Reviewer 分项成本、无进度最长时间和用户是否愿意再次使用。人工指标由冻结的
实验协议记录，不为此增加常驻产品 telemetry。

三个案例只能证明机制和项目案例，不包装成通用成功率；需要成功率结论时另做独立 holdout，
V1 不建设 benchmark 平台。

### 11.2 停止扩大 V1 的条件

出现任一情况时保留当前 Harness，不继续增加 Agent 能力：

1. 条件路由最终仍是固定顺序；
2. LangGraph 形成第二套业务状态；
3. Context 必须依赖向量库/Repo Map 才能工作；
4. Working Memory 变成不断增长的聊天摘要；
5. 恢复可能重复未知 Worker；
6. 必须重写现有 Core；
7. 第一个 Adapter 不稳定却开始做第二个；
8. 测试数量接近旧实验量级但没有对应真实风险；
9. 真实任务没有降低恢复、目标漂移或人工判断成本；
10. 为展示框架而加入多 Agent、长期 Memory、服务端、UI 或自动发布；
11. Agent 模式降低 fail-closed 约束。

### 11.3 对外表述门槛

Gate 2 前仍表述为“本地优先的 AI 编码验证与独立评审 Harness”。Gate 2 通过后可称为实验性
Supervisor Agent 模式。只有 Gate 3 通过，才能表述为：

> Vega 是一个本地优先、可恢复、证据驱动的软件工程 Supervisor Agent。它把长程目标编译为
> 有界 WorkPlan，以选择性上下文和 run-local Working Memory 驱动外部 Coding Agent，并由
> 确定性工程门禁裁决结果。

### 11.4 文档关系和审核后第一步

- [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md)：当前行为和红线；
- [`ARCHITECTURE.md`](ARCHITECTURE.md)：当前 Linear Runtime 与 Core；
- [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md)：调查和修改前批准；
- [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md)：当前 Goal P0/P1 边界；
- [`ROADMAP.md`](ROADMAP.md)：历史实验结论和进入门槛。

本文与当前代码或产品契约冲突时，以已发布代码、`PRODUCT-CONTRACT.md` 和真实证据为准。

审核通过后的实现顺序：

1. 在一个长期实验分支完成 Gate 1，不为每个小步骤创建分支；
2. 先写三份 ADR 和合同测试；
3. 按 `contracts → context compiler → memory reducer → graph/recovery → fake adapter` 实现；
4. Gate 1 通过后才连接 `CodexExecRunner`；
5. Gate 2 完成后再讨论 README、第二 Adapter 或版本号；
6. 不修改 Selective Memory、Multi-Agent 或其他冻结实验分支。
