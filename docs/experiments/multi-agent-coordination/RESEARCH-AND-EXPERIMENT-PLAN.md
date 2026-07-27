# 多智能体协调 Harness：调研结论与实验计划

> 初始状态（2026-07-23）：`MA-0-frozen / candidate-execution-contract`<br>
> 建立日期：2026-07-23<br>
> 调研基线：公开 `main@521f9b9`（Assurance Stage 1 数据合同）<br>
> 分支：`experiment/multi-agent-coordination`<br>
> 默认产品行为：**不变**<br>
> 初始裁决：已批准进入 `MA-1`；尚未批准真实 Worker、多 Worker、A2A 或主线合并<br>
> 2026-07-27 状态：`MA-2B / formal_inputs_complete / readiness_blocked / provider_not_authorized`

---

## 0. 先给结论

Vega 不应再造一个「能创建更多 Agent、能发更多消息」的框架。

Codex、Claude Code 和 Agents SDK 已分别提供子 Agent、Team、handoff、共享任务或 tracing 等
编排原语。若 Vega 只新增 mailbox、共享 todo、三路 reviewer 或通用 A2A 聊天层，既没有明显
产品差异，也会重复现有运行时已经承担的调度复杂度。

本实验建议把 Vega 明确定位为：

> **Evidence-Governed Adaptive Coding Harness（证据治理的自适应编码 Harness）**

它不竞争模型本身的规划、编码或对话能力；它负责在一次真实编码任务中回答：

1. 当前任务是否具备可执行、可验证、可委派的计划？
2. 哪个角色需要较强模型，哪个角色可以安全使用较低成本模型？
3. 外部 worker、原生子 Agent 或多个隔离 worktree 的结果，是否仍绑定同一任务、策略、
   工作区快照与确定性验证事实？
4. 多轮未完成时，问题究竟在需求、计划、执行、审查、环境还是协调信息，而不是笼统地说
   「模型不够强」？
5. 何时应该重试、重规划、升级模型、交还人工或停止，而不是无限循环？

第一项目标不是「做一个强 Planner」，而是建立并验证一份**可验证的委派合同**：

```text
任务边界、依赖、写入范围、验证 oracle、未决决策、恢复方式
能否在 Worker 启动前被严格编译、版本化、绑定当前快照并 fail-closed？
```

只有该合同成立后，才进入下面这个可证伪的执行假设：

```text
高质量 PlanContract
  + 成本较低但受控的 Worker
能否在确定性验证、人工介入和安全不退化的前提下，
达到强 Worker 基线相近的有效完成率，并降低成本或耗时？
```

若这个假设不成立，Vega 没有理由继续扩张协调层；若成立，才有条件测试原生子 Agent、隔离
多 worker 与跨运行时 handoff 的边际收益。

---

## 1. 这轮研究回答什么，不回答什么

### 1.1 研究问题

| 编号 | 问题 | 可接受答案 |
|---|---|---|
| `RQ-1` | 强 Planner 能否让较低成本 Worker 完成受限编码任务？ | 可以、部分可以或不可以 |
| `RQ-2` | 委派就绪性是否能在启动 Worker 前被显式检查？ | 能形成可审计的 `PlanContract` 与 `DelegationReadiness`，或证明检查不足 |
| `RQ-3` | 原生子 Agent 对 Vega 的工作流是否有可测的边际收益？ | 有、无或证据不足 |
| `RQ-4` | 多个外部 Worker 是否在特定 DAG 任务上带来净收益？ | 仅限明确可并行任务，或不值得 |
| `RQ-5` | A2A 是否解决了本项目当前真实缺口？ | 仅跨运行时互操作时需要，否则不实现 |
| `RQ-6` | 重复失败如何避免无意义重跑？ | 用证据归因后重规划、升级、停止或人工接管 |

### 1.2 明确不做

本分支及本实验不：

- 比较或宣传某个模型「更聪明」；
- 替代 Codex、Claude Code、Agents SDK 的子 Agent 创建、共享任务列表、对等消息或 UI；
- 建通用 Agent mailbox、聊天系统、长期 daemon、向量 Memory 或 Web 控制面；
- 把多个 reviewer 的 finding 数量当作质量；
- 让 LLM 覆盖确定性验证失败、证据过期或策略漂移；
- 自动 commit、push、release、部署或写入长期 Memory；
- 因为实验实现很快，就跳过预注册、隔离变量、ground truth 与停止线。

「AI 可以在一两天内完成多项实现」可以改变开发吞吐，但不能消除状态空间、评测样本、
归因难度和长期维护成本。Gate 是否通过只看证据，不看编码耗时。

---

## 2. 已有证据是本实验的约束，不是可推翻的前提

### 2.1 LangGraph 归档已经给出的结论

公开归档分支 `experiment/langgraph-comparison` 已完成一次独立实验，核心结论为：

```text
classification = partial
default engine = linear
default reviewer topology = single
LangGraph = 可选的恢复 / HITL 控制面
Goal / Checkpoint / Handoff = 应保持引擎无关
```

其中真实 Gate 5.5 的预注册 reviewer topology 结果如下：

| Topology | TP | FP | Token 倍率（相对 Single） |
|---|---:|---:|---:|
| `single` | 0 | 9 | 1.0000 |
| `adaptive` | 0 | 25 | 1.7896 |
| `fixed_three` | 0 | 34 | 2.9043 |

三种 topology 都没有命中冻结的 true positive；多 reviewer 增加了调用、token、误报和
clean false-major。故这轮研究**不得**通过换一个说法重新推广默认 fan-out，也不得用
「更多 Agent」取代真实质量收益。

### 2.2 与当前公开主线的关系

公开 `main` 的当前主线是 Evidence Adequacy / Assurance Stage 1，不是多 Agent 产品化。
本方案只能作为独立实验，且必须遵守现有产品契约：

- 主线只建设版本化的 `Threat`、`Claim`、`EvidenceRecord`、`AdequacyResult` 及其可信
  裁决链，不加入 Planner、Worker 路由、多 Agent 或 A2A；
- `linear + single reviewer` 仍是日常默认；
- reviewer 仍是独立只读角色，不能接收 worker 的完整聊天记录；
- 确定性验证、工作区快照、策略摘要和 fail-closed 语义高于模型判断；
- 新实验没有真实收益证据前，不得改变成功条件或默认 CLI 行为。

两条线的合同边界固定为：

```text
执行前：Delegation Contract / Delegation Readiness
回答任务是否适合委派，以及适合什么层级的 Worker。

执行后：Assurance Contract / Evidence Adequacy
回答当前真实证据是否足以支持交付结论。
```

依赖方向只能是实验复用主线的 artifact、snapshot 与 evidence 基础设施；主线不得依赖本实验
的 Planner、路由、多 Worker 或 A2A。

若实现前主线发生精简或重构，必须重新冻结 baseline、测试清单和接口边界；不得假设本文件
中的模块名、测试节点数或命令形状永远有效。

---

## 3. 为什么不是重复造轮子

### 3.1 现有运行时已经拥有的能力

| 能力 | 现有生态的合理承担者 | Vega 的立场 |
|---|---|---|
| 创建、配置和并发运行子 Agent | Codex、Claude Code、Agents SDK | 将其作为可替换的 worker backend，不重做 |
| Team 的共享任务、消息和成员生命周期 | Claude Code Agent Teams 等原生能力 | 不建立通用 mailbox 或共享 todo |
| handoff、manager-as-tools、trace | Agents SDK 等显式编排 SDK | 后续可作为 treatment，不是首轮实现目标 |
| 跨厂商远程 Agent 协议 | A2A 等互操作协议 | 仅在跨运行时需求成立后评估 |
| 模型选择、内部推理、子 Agent 的局部协作 | Provider / 模型运行时 | Vega 不试图读取或复制完整内部推理 |
| 编辑器、终端与人机交互体验 | Codex / Claude Code / IDE | Vega 保持本地 CLI Harness 边界 |

### 3.2 Vega 应拥有的差异化控制面

| 控制面 | Vega 的责任 | 不应委托给模型自由判断的原因 |
|---|---|---|
| `PlanContract` | 编译任务、项目规则、scope、依赖、验证 oracle 与未决决策 | 「有一个计划」不能证明计划可执行或可低成本委派 |
| 路由合同 | 按风险、委派就绪性、变更范围和 oracle 强度选择角色 / 模型层级 | 不能仅因 token 预算低就让弱 worker 接手高风险任务 |
| 证据绑定 | 将 task、policy、snapshot、diff、verification、review 与成本记录绑定 | Provider 成功返回不等于任务在当前工作区成功 |
| 隔离执行 | worktree / sandbox / artifact packet 的范围与读取边界 | 多 Agent 共享写入会让归因和恢复失效 |
| 失败归因 | 将重复失败归因到可观察阶段 | 防止每次失败都盲目增加 Agent 或重跑 |
| 升级与停止 | 规定 retry、replan、escalate、human、stop 的触发条件 | 防止自主系统无限循环、掩盖环境或需求问题 |
| 评测账本 | 记录实际成功、人工介入、成本、延迟和安全违例 | 防止只凭 demo 感觉宣称收益 |

因此，Vega 的创新不是「又一个 Agent 网络」，而是：

```text
Provider-native agent capability
  + Vega 编译的计划、策略和证据合同
  + 独立的确定性验证与失败归因
  + 可证伪的成本 / 质量决策
```

---

## 4. 目标架构：控制与执行分离

```text
Task Intake
  -> Spec Compiler
  -> Plan Candidate / PlanContract
  -> Delegation Readiness Check
  -> Role / Model Router
  -> Provider Adapter
  -> Isolated Worker or Native Subagent Backend
  -> Deterministic Verification
  -> Single Balanced Reviewer
  -> Failure Attribution
  -> Retry | Replan | Escalate | Human | Stop | Finish
```

### 4.1 所有权原则

| 事实 | 权威来源 |
|---|---|
| 任务、项目规则、允许范围、验证命令 | 编译后的 task / policy contract |
| 当前代码与实际改动 | 当前 worktree 的 Git / workspace evidence |
| 验证是否通过 | 结构化 deterministic verification artifact |
| 审查意见 | 隔离 reviewer artifact；它不能覆盖验证失败 |
| 执行是否发生、是否超时、是否可恢复 | 现有 execution / recovery evidence |
| 模型路由与委派就绪性结论 | 本实验新增的 delegation-readiness artifact |
| 跨角色通信 | 只允许明确的 artifact reference，不传递完整私有对话 |

`PlanContract` 是一次运行的输入合同，不是新的长期 Memory，也不是「模型自述的计划」。
它必须与 task、policy 和当前 workspace snapshot 绑定；workspace 或项目规则变化后，旧合同
不得继续驱动新的写入。

### 4.2 工作单位

一个需要写代码的 Worker 只获得：

- 编译后的任务事实与明确的非目标；
- 必要的项目规则、读取范围和写入范围；
- 对应 slice 的 `PlanContract`；
- 有界的工具 / 时间 / token 预算；
- 该 slice 的预期验证命令和 oracle；
- 上一阶段公开声明的 artifact reference。

它不获得：

- 其他 worker 的完整对话或隐含推理；
- reviewer 的完整思维过程；
- 不属于当前 slice 的凭据、Memory 或本机信息；
- 对其他 worker worktree 的写入权限。

这里的「Worker 不直接交流」只约束 Vega 管理的多个外部 Worker：它们不得建立 P2P 聊天，
只能读取经过 schema 校验和内容哈希绑定的 artifact packet。Provider 原生子 Agent 内部
可能自行通信，Vega 将整次 provider execution 视为不透明 backend，不宣称能够禁止或审计
其内部全部消息。

---

## 5. `PlanContract`：先验证计划能否承担路由责任

### 5.1 最小字段

```yaml
schema_version: 1
plan_id: "PLAN-..."
plan_revision: 2
parent_plan_ref:
  relative_path: "plans/plan-v1.json"
  sha256: "..."
change_reason_code: "interface_assumption_invalid"
change_summary: "发现公共接口与原假设不一致"
invalidated_slice_ids: []
task_id: "..."
task_ref:
  relative_path: "tasks/task.md"
  sha256: "..."
baseline:
  head_sha: "..."
  workspace_fingerprint: "..."
  project_policy_sha256: "..."
  scope_policy_sha256: "..."
goal:
  acceptance_facts:
    - fact_id: "A-..."
      statement: "..."
  non_goals: []
task_dag:
  - slice_id: "..."
    read_paths: []
    allowed_write_paths: []
    dependencies: []
    preconditions: []
    expected_change: "..."
    acceptance_refs: []
    input_artifact_refs: []
    verification:
      commands: []
      oracle:
        kind: "all_commands_exit_zero"
    failure_and_recovery: "..."
decisions:
  resolved: []
  unresolved: []
risk:
  threat_refs: []
  human_required: false
  premium_worker_required: false
budget:
  max_changed_files: 3
  max_diff_lines: 200
  max_new_files: 1
  context_limit_tokens: 50000
  worker_time_limit_seconds: 900
  worker_token_limit: 30000
```

自 2026-07-26 的 MA-2B Pilot 输入资格协议起，`worker_token_limit` 只表示
`worker_token_observation_budget`。现有字段名为了 schema v1 兼容暂时保留，但不能宣称
Provider 会在达到该值时被 Runtime 硬终止；实际超额只进入成本与容量观测。

同日的输入资格更新先在隔离根冻结 `MA2B-C01`～`MA2B-C12`，随后由提交 `399e746`
迁入正式默认根，固定
`case_set_sha256=33b2caa335b417b47ee45bb5de7051aef20682bbf938eddf5d2e4ad5d3d4f137`。
正式 `task-pack/` 与 `ground-truth/` 是后续 readiness 和执行的唯一权威输入；候选目录中的
同名 task-pack 与 ground truth 仅作为历史冻结副本保留，不再读取、比较或继续维护。候选
`workspaces/` 仍由正式 `initial-workspace.json` 引用，不能停用。

该迁移只解除 case 缺失问题，不代表 execution binding、authorization 或 Provider 执行已获批准。

`schema_version` 表达数据格式版本，`plan_revision` 表达同一业务计划的修订次数，二者不能
混用。首版计划必须是 `plan_revision = 1` 且没有 parent；后续 revision 必须绑定父计划
artifact、变化原因和失效 slice。新 revision 必须重新绑定当前 workspace / policy snapshot，
旧 revision 继续保留审计价值，但不能驱动新的写入。

`route_eligibility` 不属于 `PlanContract`，因为计划不能自行宣布自己适合什么 Worker。它只能
由确定性的 `DelegationReadiness` 校验产生：

```text
budget_eligible
premium_required
human_required
```

实际 schema 使用严格解析、版本化和仓库相对 artifact 引用；本文件冻结研究语义，但不把
实验 Runtime API 提升为主线合同。

### 5.2 低成本 Worker 准入条件

只有全部条件满足时，router 才能输出 `budget_eligible`：

1. 写入范围和关键读取范围明确，且通过项目 policy / scope gate。
2. 每个可写 slice 都有确定性 verification command 与可判定 oracle。
3. 业务决策、兼容性选择、数据语义和外部接口假设没有未决项。
4. 依赖顺序已表达为 DAG；不能依赖另一个未完成 worker 的隐含状态。
5. diff、风险和预算有上界；不触及未被充分描述的敏感路径。
6. 失败后有明确恢复或交还人工路径。
7. 当前 workspace、policy、task 和 verification 基线仍与合同一致。

任何一项不满足，都不能把「省成本」当作理由；router 必须选择：

```text
premium_required
或 human_required
```

### 5.3 计划质量不是主观打分

LLM 可以提出计划候选，但下列检查必须由编译器、policy 或确定性校验完成：

- schema、必填字段和未知字段；
- 路径与 scope policy 一致性；
- 每个写入 slice 是否存在可执行 oracle；
- dependency 是否引用已知 slice；
- 未决决策是否阻止低成本路由；
- artifact 是否绑定当前 snapshot；
- 风险标记是否要求人工判断。

这不能证明计划一定正确；它只避免把一个不完整、无 oracle、边界不清的自然语言计划伪装成
「弱 worker 可以安全执行」的依据。

### 5.4 输入合同与执行结果必须分离

`PlanContract` 是委派输入，不能在 Worker 完成后原地补写执行结果。进入真实执行 Gate 后，
每次 slice 尝试应单独形成 `DelegationAttempt` 或 `TaskSliceReceipt`：

```yaml
attempt_id: "..."
plan_ref: {relative_path: "...", sha256: "..."}
task_slice_ref: "..."
worker_tier: "budget | premium"
allowed_paths_sha256: "..."
input_artifact_refs: []
output_artifact_refs: []
workspace_snapshot_before: "..."
workspace_snapshot_after: "..."
execution_ref: {relative_path: "executions/worker/execution.json", sha256: "..."}
verification_ref: {relative_path: "...", sha256: "..."}
failure_signature: null
```

其中 `execution.json` 仍是进程启动、PID、heartbeat、deadline 与 terminal status 的权威事实；
Attempt 只引用并解释现有 execution evidence，不重造第二套执行系统。`MA-1` 只冻结这条边界，
不启动 Worker，也不实现 Attempt Runtime。

---

## 6. 模型与角色路由：强 Planner、弱 Worker、平衡 Reviewer

### 6.1 角色分工

| 角色 | 默认能力层级 | 可做 | 不可做 |
|---|---|---|---|
| `planner` | `premium` 或 `balanced` | 形成 / 修订 PlanContract，提出风险与验证建议 | 宣布验证通过、绕过 policy、直接决定成功 |
| `worker` | `budget` 或 `premium` | 在隔离范围内实施一个 slice，运行声明的验证 | 改写任务、扩大 scope、替代最终验证 |
| `reviewer` | 固定 `balanced` | 审查 diff、证据充分性和项目规则遵守情况 | 覆盖 deterministic verification failure |
| `router` | 确定性规则优先，可有 LLM 候选解释 | 根据合同和风险选择角色档位 | 自行放宽准入条件 |
| `adjudicator` | 人工或预注册 ground truth | 判断评价集中的未知 / 争议项 | 事后修改冻结评分规则 |

模型具体名称、版本、reasoning 设置、上下文窗口、价格、provider 和可用性属于一次评测的
冻结 manifest。概念合同只使用 `premium`、`balanced`、`budget`，避免把短期产品命名写成
长期架构事实。

### 6.2 首轮对照

固定 `reviewer = balanced`，固定项目任务包、初始 snapshot、验证 oracle、风险门禁和
总预算；只改变 Planner 与 Worker 层级：

| Treatment | Planner | Worker | 目的 |
|---|---|---|---|
| `A` | 无显式 PlanContract（现有编译上下文） | `premium` | 当前强 Worker 基线 |
| `B` | `premium` | `premium` | 分离计划本身是否有质量价值 |
| `C` | `premium` | `budget` | 核心假设：强计划能否换取较低执行成本 |

每个 treatment 必须：

- 使用独立、干净的 worktree；
- 使用同一任务事实、项目规则、验收标准、冻结输入和验证命令；`A` 只是不获得编译后的
  `PlanContract`，不能故意缺少需求事实；
- 将 provider / model / token / wall-clock / 重试记录入 manifest；
- 让 reviewer 看不到 treatment、模型档位与其他 treatment 输出；
- 用确定性 verifier 和预注册 ground truth，而非 reviewer 自报，计算结果。

首轮不比较多个 reviewer，不把同一任务的多个输出串起来，也不让后一个 worker 读取前一个
worker 的完整过程。只有 `C` 出现正向信号后，才增加
`D = balanced planner + budget worker`，避免在核心假设尚未成立时扩张变量。

### 6.3 原生子 Agent 的正确位置

Codex 或 Claude Code 中的原生子 Agent 可以成为一个 `WorkerExecution` backend：

```text
Vega 负责：任务 / policy / PlanContract / scope / evidence / decision
Provider 负责：该 worker 内部是否使用子 Agent、如何分配局部探索
```

Vega 只消费 backend 明确产出的 diff、执行事实、结构化验证结果和允许的摘要 artifact；不需要
抓取、转发或长期保存内部聊天记录。

这允许后续公平比较：

```text
single worker
vs
同一 provider 的 native subagent worker
vs
Vega 管理的隔离 multi-worker
```

但只有前一层已显示可观测缺口时，才进入后一层。

---

## 7. 多轮失败：归因优先于重跑

### 7.1 失败分类

| 分类 | 最低判据 | 下一步 |
|---|---|---|
| `spec_gap` | 验收事实、业务决策或约束互相冲突 / 缺失 | 人工澄清；不能用更强模型猜 |
| `plan_gap` | PlanContract 缺 scope、依赖、oracle、风险或恢复路径 | 回到 Planner，产出新合同 |
| `worker_execution` | 合同充分且环境有效，但 patch / 工具使用 / 局部实现失败 | 一次有界修复尝试，随后升级或停止 |
| `reviewer_instruction_miss` | reviewer 与确定性证据、冻结项目规则或 ground truth 矛盾 | 不让其阻断成功；单独诊断 reviewer |
| `verification_environment` | 依赖、fixture、sandbox、命令或平台本身无效 | 修复环境；不得给出代码质量结论 |
| `coordination_message` | handoff packet 缺字段、hash 不符、接收方无法恢复约束 | 修复 packet 后只重跑受影响接收方 |
| `stale_evidence` | workspace、policy、task 或 snapshot 已漂移 | fail-closed，重新编译基线 |
| `unknown` | 现有 artifact 不能支持上列任何结论 | 人工诊断；禁止自动第三次猜测 |

分类是基于 artifact 的结论，不是某个 Agent 的自我评价。`unknown` 是有效结果，不能被压缩成
「worker 再试一次」。

### 7.2 重试控制

```text
第一次有效失败
  -> 生成 failure signature 与分类
  -> 只允许一个与分类匹配的修复动作
第二次出现同一有效 failure signature
  -> 停止该自动路径
  -> 写 diagnosis artifact
  -> 形成新的 plan revision proposal
  -> 显式批准后 replan / escalate / human / stop
第三次执行
  -> 必须有新的 PlanContract、环境修复、task 澄清或预注册的变化理由
```

建议的 `failure_signature` 至少绑定：

```text
classification
+ task_id
+ PlanContract hash
+ workspace snapshot
+ normalized verifier / error class
+ policy hash
```

这条规则的目的不是减少所有 retry，而是防止「同一不完整计划 + 同一弱 worker + 同一失败」
被包装成学习或自主性。

`MA-2.5` 不实现后台自动重规划。首次失败、归因结果、修订后的计划和再次执行必须分别保留；
不得用修订后的成功覆盖原 treatment 的首次失败，否则 A/B/C 的变量隔离失效。

### 7.3 升级规则

| 观察到的事实 | 合法动作 | 不合法动作 |
|---|---|---|
| `PlanContract` 不充分 | 重新规划或人工澄清 | 直接加大 worker 预算 |
| 合同充分、budget worker 一次执行失败 | 同合同下允许一次 `premium` worker 对照 | 无限重试 budget worker |
| verifier 无效 | 修复环境后重新冻结输入 | 用 reviewer 判定「大概没问题」 |
| workspace 漂移 | 重新编译 task / snapshot | 继续消费旧 evidence |
| reviewer 指令漏检 | 记录为 reviewer 质量问题 | 让 reviewer 改写验证结论 |
| 手动审查发现需求不清 | `human_required` | 让模型自行补业务规则 |

---

## 8. 多 Worker 与 A2A 的进入条件

### 8.1 外部多 Worker 不是默认

一个任务只有同时满足以下条件，才有资格使用多个外部 worker：

1. `PlanContract.task_dag` 中至少两个 slice 的写入集合和语义边界可证明独立。
2. 不共享 schema、公共接口、全局配置、锁、生成产物或易冲突测试夹具。
3. 每个 slice 能在独立 worktree 中运行自己的验证。
4. 合并阶段有独立的集成验证，而不是「两个 worker 都说完成」。
5. 并行的预期节省大于额外 worktree、context、review 和 reconciliation 成本。

否则默认使用单 worker。对于一个紧耦合功能，让两个 Agent 并发改同一仓库通常只会增加
上下文复制、冲突、归因困难和验证成本。

### 8.2 A2A 不是第一层实现

A2A 的价值是跨运行时、跨厂商或跨机器时的互操作；它不自动提供正确的计划、隔离、验证、
成本收益或代码合并语义。

只有以下条件同时成立时，才创建 A2A 设计 Gate：

1. 需要调用独立部署、不同 provider 或不同权限域的 Agent；
2. 本地 provider-native subagent / artifact packet 不能表达所需交接；
3. 对方必须发现能力、接收任务、返回 artifact，且这些交互需要可重放审计；
4. 安全边界、身份、数据最小化和取消语义已经定义；
5. 前述 PlanContract 与 evidence binding 已在单运行时实验中证明有价值。

未满足时，使用本地 `HandoffPacket` 即可：

```yaml
task_ref: "..."
plan_ref: "..."
policy_ref: "..."
snapshot_ref: "..."
allowed_actions: []
budget: {}
artifact_refs: []
resume_conditions: []
```

它只传递内容哈希绑定的事实和最小必要摘要，不传递 API key、完整会话、隐藏推理或不受控环境
信息。日后如需接入 A2A，应把这个 packet 映射到协议，而不是先造聊天协议再寻找用途。

---

## 9. 分阶段实验路线

| Gate | 名称 | 只回答的问题 | 进入条件 | 不通过时 |
|---|---|---|---|---|
| `MA-0` | 研究合同冻结 | 目标、边界、变量和停止线是否明确 | 本文件经 owner 认可 | 不写 Runtime |
| `MA-1` | 委派合同与就绪性 | `PlanContract` 能否严格编译并拒绝不可委派计划 | 刷新到实现当日 `main` 基线 | 保持 design-only |
| `MA-2` | Planner × Worker Pilot | 强计划是否可能让 budget worker 有条件可用 | 预注册任务包、真实 provider、独立 worktree | 结论为 reject / inconclusive |
| `MA-2.5` | 失败归因与计划修订 | 分类、显式 revision 与升级是否比盲目重试更可解释 | 有真实失败样本和人工复核 | 删除不可靠分类 |
| `MA-3` | 原生子 Agent 边际评测 | native subagent 是否优于同 provider 单 worker | `MA-2` 存在可测瓶颈 | 不实现外部协调 |
| `MA-4` | 隔离 multi-worker | 仅在可并行 DAG 上是否有净收益 | 独立 scope 与集成 oracle 均成立 | 保持单 worker |
| `MA-5` | A2A 设计 Gate | 跨运行时互操作是否真实需要 | 本节 8.2 五项全满足 | 不实现 A2A |

### 9.1 主线与实验分支同步规则

1. 每个实验 Gate 开始前冻结 `baseline_commit`、实验合同 hash；涉及真实任务时同时冻结
   task-pack hash。
2. 真实 provider 调用开始后不 rebase。需要换用新主线时，关闭旧 Gate 并建立新的
   pre-registration。
3. 实验发现的通用主线 Bug，先在主线独立修复，再决定是否同步回实验分支。
4. 主线修复如果改变冻结变量，实验必须重新预注册，不能直接同步后继续累计原 Gate 结果。
5. 实验能力进入产品时，从最新 `main` 新建小分支重新提取独立能力，不整分支合并。

`PlanVersion`、`HandoffPacket`、`failure_signature`、通用 snapshot 绑定等都只是候选基础设施；
只有证明脱离多 Agent 场景仍有独立价值后，才有资格进入主线。

### 9.2 `MA-1` 的最小实现范围

若进入代码，只实现并测试：

- 严格 `PlanContract` schema；
- `schema_version` 与 `plan_revision` 的独立语义及父计划绑定；
- path / policy / snapshot / oracle 的确定性检查；
- `budget_eligible`、`premium_required`、`human_required` 三种路由结果；
- 最小的 route evidence artifact；
- 对无效合同的 fail-closed 测试。

不实现真实 multi-worker、A2A mailbox、默认 LangGraph 调度或自动 Memory。

### 9.3 `MA-2` 的真实 Pilot

Pilot 用于检验可执行性和估计方差，**不**产生产品合并结论。

建议任务包为 12 个冻结 case：

| 类型 | 数量 | 作用 |
|---|---:|---|
| 边界清晰、具备确定性 oracle 的小修复 | 4 | 检验 `budget_eligible` 是否有实际价值 |
| 跨文件但需求明确的行为变更 | 4 | 检验 PlanContract 是否能表达依赖和验证 |
| 故意存在未决业务 / 兼容性选择的 case | 2 | 检验 router 是否拒绝错误委派 |
| stale evidence / verifier 无效的故障注入 case | 2 | 检验 fail-closed 与归因，不计入代码质量分 |

真实代码 case 必须在独立 worktree 执行；故障注入 case 只验证 Harness 是否停止，不能被当作
模型能力比较样本。

Pilot 后冻结：

- 真正的 quality 指标与人工裁决规则；
- 成本、时间、重试和人工介入的确认集阈值；
- 需要的重复次数与随机执行顺序；
- 可接受的预算上限；
- 任何已发现的环境不稳定项。

然后才可以启动不少于两个独立任务包的 confirmatory run。Pilot 的结果不能被事后挑选为
「证明确实有效」的唯一证据。

---

## 10. 指标、判定与停止线

### 10.1 每次真实运行必须记录

| 类别 | 指标 |
|---|---|
| 质量 | 当前 snapshot 上的结构化验证结论、预注册 acceptance、人工裁决、回归数 |
| 成本 | provider token、可计价成本、调用数、Planner / Worker / Reviewer 分项 |
| 时间 | wall-clock、每阶段耗时、排队与人工等待时间 |
| 人工负担 | 需要的澄清、接管、重新运行与手工合并次数 |
| 安全 | scope violation、stale evidence 后继续、重复外部副作用、无效 approve、跨角色泄漏 |
| 路由 | route eligibility、拒绝原因、升级次数、失败分类和 signature |
| 可维护性 | 新 artifact 数量、恢复步骤、对现有默认流程的侵入面 |

### 10.2 硬安全条件

以下任一项非零，当前 treatment 不得获得正向推广结论：

```text
verified success over deterministic verification failure
verified success over stale workspace / policy evidence
out-of-scope write accepted as success
duplicate non-replayable external effect
reviewer received worker full transcript
unattributed cross-worker artifact consumption
```

安全门禁触发可以是一次有效的 fail-closed 结果；真正禁止的是「门禁失效后仍被标为成功」。

### 10.3 产品决策口径

| 结果 | 条件 |
|---|---|
| `reject` | 安全条件失败，或确认集显示无质量收益且成本 / 人工负担上升 |
| `inconclusive` | 样本不足、provider / 环境不稳定、ground truth 无法冻结或差异不显著 |
| `continue-experiment` | 有局部信号，但只适合扩大受控样本，不改变默认行为 |
| `candidate-for-opt-in` | 两个独立确认包均满足安全条件，并显示质量不退化且至少一项成本、延迟或人工步骤有预注册的实质改善 |

确认集的数值阈值必须在 `MA-2` Pilot 结束、任何确认 provider 调用开始**之前**冻结。不能在
看到强 Planner 或弱 Worker 的结果后再倒推「什么算成功」。

### 10.4 立即停止线

发生以下任一情形时，停止扩大范围并回到最近的确定点：

- 为了让弱 worker 通过而放宽 scope、verification 或 evidence 规则；
- 为了让多 Agent 看起来有价值而改变 task、ground truth 或评分方式；
- 因为 provider 原生能力存在，就把其内部会话视为可审计业务事实；
- 因为 worker 可自行启动子 Agent，就绕过外层预算、worktree 或 artifact 边界；
- 多 worker 需要共享隐藏状态才能工作；
- A2A 仅用于「看起来更前沿」而没有跨运行时需求；
- 任何结果需要靠复述模型自评而非验证 / artifact / 人工裁决才能成立。

---

## 11. 预期产物与隐私边界

本轮调研阶段只产生本文件。后续每个 Gate 才能按需新增下列**相对路径** artifact：

```text
eval/experiments/multi-agent-coordination/
  MA-<gate>-pre-registration.md
  task-pack/
  ground-truth/
  manifests/
  results/
  decision.md
```

具体位置必须在实现 Gate 前与当前仓库目录契约复核；不得因本示意路径而绕过 `eval/` 的
追加式证据规则。

所有公开内容必须：

- 只使用仓库相对路径或占位符；
- 不包含本机盘符、UNC 路径、用户名、私人邮箱、API key、Authorization header、真实 provider
  endpoint 或未脱敏会话内容；
- 不包含 worker / reviewer 完整对话；
- 对模型、provider、价格与版本保留复现实验所需的最小、可公开信息；
- 以 hash、相对 artifact reference 和不可变运行记录绑定事实。

---

## 12. Owner 裁决与当前下一步

Owner 已于 2026-07-23 确认以下五项，而不是直接开始 A2A 或多 worker 实现：

1. 接受 Vega 的定位是「证据治理与自适应路由层」，不是新的 Agent Team 平台。
2. 接受 `MA-2` 的首个核心问题是「计划质量 × Worker 层级」。
3. 接受 `single reviewer` 固定为评测控制条件，不重开默认 fan-out。
4. 接受 provider-native 子 Agent 先作为 treatment，而不是 Vega 要复制的功能。
5. 接受 A2A 仅在跨运行时需求被证明后才进入设计 Gate。

因此在 **2026-07-23 的初始裁决时点**，唯一实验实现范围是 `MA-1`：在冻结的公开主线
baseline 上，写出最小、严格、fail-closed 的 `PlanContract` 与 `DelegationReadiness`，
产出确定性 route evidence artifact。完成验证后先形成 Gate 结论，不自动进入 `MA-2`。

截至 **2026-07-27**，后续实验已推进到 MA-2B 正式输入准备完成，但正式执行资格仍被
readiness 门禁阻断：

```text
formal_inputs_complete / readiness_blocked / provider_not_authorized
```

该状态不表示真实 Worker 或 Pilot 已获批准；在 execution binding、owner authorization 和
Provider 授权齐备前，不得调用真实 Provider，也不得提前进入 Reviewer、MA-3 或 multi-worker。

---

## 13. 参考资料

### 官方能力与互操作资料

1. OpenAI. *Codex Subagents*，官方文档，访问日期：2026-07-23。<br>
   <https://developers.openai.com/codex/concepts/subagents/>
2. OpenAI Agents SDK. *Orchestrating multiple agents*，官方文档，访问日期：2026-07-23。<br>
   <https://openai.github.io/openai-agents-python/multi_agent/>
3. Anthropic. *Agent teams*，Claude Code 官方文档，访问日期：2026-07-23。<br>
   <https://code.claude.com/docs/en/agent-teams>
4. A2A Protocol. *Protocol Specification*，访问日期：2026-07-23。<br>
   <https://a2a-protocol.org/latest/specification/>

### 研究与评测资料

5. Yang et al. *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering*，
   arXiv:2405.15793，2024。
6. Xia et al. *Agentless: Demystifying LLM-based Software Engineering Agents*，
   arXiv:2407.01489，2024。
7. Chen et al. *CodePlan: Repository-level Coding using Large Language Models and Planning*，
   arXiv:2309.12499，2023。
8. Ong et al. *RouteLLM: Learning to Route LLMs with Preference Data*，arXiv:2406.18665，2024。
9. Shinn et al. *Reflexion: Language Agents with Verbal Reinforcement Learning*，
   arXiv:2303.11366，2023。
10. Gou et al. *CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing*，
    arXiv:2305.11738，2023。
11. MAST. *Why Do Multi-Agent LLM Systems Fail?*，arXiv:2503.13657，2025。
12. *AgentPrune: Adaptive Dynamic Pruning for Multi-Agent Systems*，arXiv:2501.06201，2025。
13. OpenAI. *SWE-Lancer: Can Frontier Language Models Earn $1 Million from Real-World Freelance
    Software Engineering?*，arXiv:2502.12115，2025。

这些资料的共同启示不是「Agent 越多越好」：接口 / Harness、计划、工具反馈、模型路由、
通信拓扑和真实可合并性都会影响结果，且都必须通过独立的质量、成本和安全证据验证。
