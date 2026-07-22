# Vega LangGraph 编排实验独立评审与执行计划

> 文档状态：`execution-contract`
>
> 日期：2026-07-15
>
> 当前分支：`experiment/langgraph-comparison`
>
> Gate 0 开始前 HEAD：`private-gate-0-contract-redacted`
>
> 代码实验基线：`private-experiment-base-redacted`
>
> 规范性拆分：`GATE-0-BASELINE.md`、`ADR.md`、`STATE-OWNERSHIP.md`、
> `RECOVERY-CONTRACT.md`、`EVAL-PROTOCOL.md`、`DEMO.md`
>
> 评审输入：《Vega LangGraph 双编排引擎与长任务恢复实验计划》
>
> 实验性质：AI 原生、可证伪的架构实验，不是主线产品承诺
>
> 默认引擎：实验得出明确结论前仍为现有线性 Runtime
>
> 当前推进状态（2026-07-20，星期一）：Gate 4.5 R6、Gate 5 与 Gate 5.1 已 `pass`；
> Gate 5.5 已按冻结 tag `gate-5.5-pre-run-v1` 完成真实评测，结论为 `single wins`，
> 82 次 provider session 全部成功，安全指标全为 0。Core Decision 为 `partial`：
> 保留有证据的 LangGraph checkpoint、interrupt、恢复与单 Reviewer 路径，不把
> `adaptive` 或 `fixed_three` 提升为默认 topology；默认产品引擎仍为 linear。
> Gate 7 R6 已完成唯一一次 API-key 真实 Gate 7A：远端执行权控制与 CP01 worker
> 成功，但 transcript/token 超过冻结上限，Gate 7A `failed`、Gate 7C 未启动，见
> [`GATE-7-R6-RESULT.md`](GATE-7-R6-RESULT.md)。
> 最终汇总已于 2026-07-20 完成，分类为 `partial`，见
> [`DECISION.md`](DECISION.md)；可重复现场演示见 [`DEMO.md`](DEMO.md)。

---

## 1. 评审结论

原方案的研究方向正确，而且对 LangGraph 的理解没有停留在“把流程画成图”：

- checkpoint 只解决图执行状态，不等于 Git workspace 事务；
- `interrupt` 恢复可能重新执行节点，外部副作用不能盲目重放；
- worker、reviewer、verification、人工决定和 artifacts 需要明确的权威关系；
- 多 Agent 第一阶段应采用单 writer、多 isolated reader，而不是多个 writer 争用同一个
  workspace；
- 实验必须允许得出 `accept / partial / reject`，不能因为已经投入实现就强行合入主线。

本评审不同意因为计划“对人工开发显得宏大”就主动砍掉研究目标。当前实验应采用 AI 原生执行
方式：高能力 Coding Agent 可以显著压缩实现、测试、故障注入和文档收口成本，但人工编码工期
不应成为唯一裁剪依据。

同时，AI 生成速度不能代替状态语义、证据独立性和真实验证。真正需要控制的不是代码行数，而是：

```text
同一个错误假设
  -> 同时进入实现
  -> 同时进入测试
  -> 同时进入文档
  -> 最终形成“证据很多但结论仍然错误”的完整外观
```

因此，本实验保留完整方向，但采用严格 Gate：

```text
快速生成
  -> 确定性验证
  -> 独立上下文复审
  -> 证据绑定
  -> 通过后进入下一 Gate
  -> 不通过则停止、修正或给出负面结论
```

最终建议不是缩减为教程式 Demo，而是完成一条可运行、可恢复、可审计的实验分支，并将核心
编排实验与长任务、API 等扩展实验分别给出结论。

当前文档只是一份候选执行契约。只有项目 owner 正式采纳本次状态所有权、execution 复用、
Gate 分层与控制面隔离方案，并完成 Gate 0 的基线冻结和独立复审，文档状态才可升级为
`execution-contract`。

---

## 2. 当前仓库事实

### 2.1 当前基线

本实验当前实际位于：

```text
branch: experiment/langgraph-comparison
Gate 0 开始前 HEAD: private-gate-0-contract-redacted
代码实验基线:      private-experiment-base-redacted
base:              main / origin/main @ private-experiment-base-redacted
```

Gate 0 开始前，当前分支在 `main@private-experiment-base-redacted` 上只增加了本目录的执行计划与演示契约。
当时没有 LangGraph 依赖或实现，也不需要再创建一个语义重复的实验分支。Gate 0 后续文档会
继续推进分支 HEAD，因此 `private-experiment-base-redacted` 只能称为代码实验基线，不能称为当前 HEAD。

### 2.2 当前主线已有能力

当前基线已经包含：

- bug / feature 的 assist 与 auto loop；
- worker 与独立只读 reviewer；
- workspace snapshot 和 staged / unstaged 双证据流；
- deterministic verification；
- risk gate、变更预算和 prompt budget；
- state、trace、execution、artifact hash 与 child run 绑定；
- Finish、status、stop、recover；
- Goal P0 人工 checkpoint 状态层；
- Memory proposal 的显式 accept / reject；
- 证据缺失、过期、篡改或语义不一致时 fail-closed。

### 2.3 不能误写成当前主线事实的内容

以下内容存在于其他实验分支，但不属于当前基线：

- 精确路径 Scope Gate：
  `experiment/daily-loop-dogfood-mainline@private-mainline-dogfood-status-redacted`；
- Selective Memory 完整 runtime 与校准结果：
  `experiment/selective-memory@private-selective-memory-calibration-redacted`；
- 历史公开 Bugfix dogfood：
  `experiment/daily-loop-dogfood@private-dogfood-timeout-fix-redacted`；
- 主线 Scope Gate dogfood 文档：
  `experiment/daily-loop-dogfood-mainline@private-mainline-dogfood-status-redacted`。

当前文档和后续代码不得把这些分支中的能力写成 `private-experiment-base-redacted` 已具备的功能。需要引用时必须记录
分支与提交，不使用当前 checkout 中不存在的相对路径制造错误来源。

### 2.4 基线选择原则

第一轮 LangGraph 代码实验直接基于代码实验基线 `private-experiment-base-redacted`：

- 不先合入 Selective Memory；
- 不把未合并的精确路径 Scope Gate 当作前置依赖；
- 不同时解决其他实验分支的遗留问题；
- 如果后续需要 Scope Gate，只能作为单独、可审查的移植或后续基线升级，不得在结果出来后
  静默改变 A/B 条件。

---

## 3. 求职与项目定位

广州、深圳目标 JD 的主要能力信号包括：

```text
RAG
Python
LangChain / LangGraph
向量数据库
Eval
FastAPI / 后端 API
Tool Calling / Multi-Agent
部署、可观测和失败恢复
```

Vega 不需要承担全部市场关键词。项目组合应保持职责分工：

| 项目 | 主要证明内容 |
|---|---|
| GZIS AI 导办 | 真实政务场景、RAG、知识库、评测、Guardrail、业务集成 |
| AgentToolGate | Tool Calling、MCP、Policy、Approval、Audit、Secret 与治理 |
| Vega | Python Agent Runtime、LangGraph、状态、恢复、验证、隔离审查、Eval |
| Echo Vault | FastAPI、OpenAI-compatible API、streaming、quota、fallback、发布验收 |

因此，本实验的职责是补齐招聘方容易识别的 LangGraph / Agent Runtime 证据，而不是在 Vega
内部再造 RAG、向量数据库或聊天应用。

面向简历的目标不是“使用了 LangGraph”，而是证明：

> 能区分框架提供的图状态恢复，与外部 coding worker 对 Git workspace 产生的不可事务化
> 副作用；能够在二者之间建立可复核的恢复握手，而不是把 checkpoint 等同于安全恢复。

---

## 4. 实验目标与允许结论

### 4.1 核心研究问题

在不改变 Vega 既有成功语义、安全门禁和 artifact contract 的前提下：

1. LangGraph 能否与现有线性 Runtime 复用相同业务步骤？
2. 顺序图能否得到与线性 Runtime 一致的语义终态？
3. 进程崩溃后能否在不重复外部写副作用的前提下安全恢复或安全交接？
4. `interrupt / resume` 能否与 Vega decision ledger、workspace 和 evidence 正确绑定？
5. 多个 isolated reviewer 能否并行读取同一证据快照，并由确定性规则聚合？
6. 一个长 Goal 能否通过多个短生命周期 worker epoch 接力，而不是依赖单个超长会话？
7. LangGraph 引入的依赖、状态、维护和运行开销是否值得？

### 4.2 允许结论

```text
accept
  LangGraph 作为可选编排引擎保留，默认仍由实验结论决定。

partial
  只保留 checkpoint、interrupt、review fan-out、handoff 等有明确收益的局部能力。

reject
  额外状态与复杂度没有改善恢复、隔离或可维护性；实验代码不进入主线。
```

负面结果同样是有效产出。不得为了简历描述修改实验门槛、隐藏 timeout 或把安全停止包装为成功。

---

## 5. AI 原生执行原则

### 5.1 不以人工编码工期作为唯一裁剪依据

本实验不因传统人工编码速度低估 AI Coding Agent 的实现能力，也不因生成速度快而自动扩大
范围。范围由以下因素共同决定：

- 研究问题是否具有独立决策价值；
- 实验变量能否隔离；
- 状态空间和验证组合是否可控；
- 失败能否得到明确解释；
- 长期维护负担是否与收益匹配。

Gate 是否通过只看证据，不看完成时间。执行过程中必须满足：

- 一个提交只回答一个主要问题；
- 变更可单独回滚和比较；
- 每个 Gate 的测试有明确 passed / failed / skipped / timeout；
- timeout 不得记为通过；
- 未关闭 Blocker / High 时不得启动真实 runner；
- 不因 AI 能快速生成就合并多个实验变量；
- 不以模型自述“已完成”代替 Git、测试和 artifacts。

### 5.2 实现者与复审者分离

同一模型或同一上下文不应同时成为实现与最终证明的唯一来源。

每个重要 Gate 至少保留三类证据：

```text
实现证据
  代码、配置和提交差异

确定性证据
  pytest、静态检查、artifact hash、workspace fingerprint

独立复审证据
  不读取实现会话完整推理的 reviewer 结论
```

独立 reviewer 不能覆盖测试失败，也不能将缺失证据解释为通过。

### 5.3 模型与运行参数记录

每次真实 runner 或重要 AI 实现阶段都应记录：

- 实际模型标识；
- reasoning effort；
- runner 类型；
- sandbox；
- timeout；
- 开始与结束时间；
- 是否发生 provider error、compaction 或人工补改。

模型能力只解释执行环境，不作为实验结果本身。

---

## 6. 状态所有权

### 6.1 权威来源

| 信息 | 权威来源 |
|---|---|
| 当前目标、非目标和验收条件 | task / Goal contract |
| 人工批准、拒绝和范围决定 | Vega decision ledger |
| 当前代码事实 | Git HEAD、staged、unstaged、untracked 和 workspace fingerprint |
| 外部进程是否已启动或完成 | 现有 `execution.json` |
| Graph 节点如何解释执行与输出 | content-addressed step result manifest |
| 验证结果 | verification artifacts |
| 风险结论 | risk gate artifacts |
| Reviewer 结论 | 结构化 review artifacts |
| 当前 run 的业务状态 | `state.json` |
| 图执行游标 | LangGraph checkpoint |
| 长任务交接 | versioned checkpoint handoff artifact |
| 跨任务经验 | accepted memory ledger；第一轮关闭 |

### 6.2 `state.json` 的定位

当前 Vega 没有一套可以百分之百重建全部业务状态的 append-only domain event 流，因此
`state.json` 不能被称为投影。本实验中：

```text
state.json
= 当前 run 的权威业务状态

LangGraph checkpoint
= 图执行游标和最小恢复数据，不拥有业务成功语义

execution.json
= 外部进程生命周期事实

step-result.json
= Graph 节点如何引用并解释 execution、workspace 与输出证据

Artifacts
= 业务状态和结论成立的证据
```

`state.json` 与 graph checkpoint 不得独立声明互相矛盾的业务事实。发生不一致时，恢复流程应
以 `state.json` 的业务状态为准，同时校验 execution、step result、artifact hash 和当前
workspace。无法解释差异时进入 `needs_human`，不能根据 graph 游标覆盖业务状态。

### 6.3 节点重放分类与执行证据协议

节点先按重放语义分为：

```text
pure_replayable
  纯计算；输入身份一致时可以安全重放。

read_only_replayable
  只读 Git、读取 artifact 或确定性分析；输入身份一致时可以重跑。

external_non_replayable
  worker、provider 调用、可能写文件的 verification command 等；
  必须复用 execution.json，并在继续前完成 workspace reconciliation。
```

不是每个节点都需要独立的 started / completion 文件。外部进程生命周期继续由现有
`ExecutionController` 和 `execution.json` 管理，不新增第二套执行状态机。

所有可能产生外部副作用的节点必须使用稳定 identity：

```text
run_id
engine
graph_schema_version
step
iteration
attempt_id
runner identity
base_head
before_workspace_fingerprint
policy_snapshot_sha256
command_sha256
started_at
```

Gate 3 中，现有 `ExecutionLease` 需要按兼容方式补充 `attempt_id`、输入身份与 replay
class，或由一个窄的 attempt manifest 绑定这些字段；它不能与 `execution.json` 独立声明
进程终态。Gate 1 只建立 engine / handler 边界，不实现这套恢复身份协议。

外部节点完成后写入轻量的 step result manifest。规范 schema 以
`RECOVERY-CONTRACT.md` 第 5 节为唯一来源；下面只展示所有权关系，不构成第二份 schema：

```json
{
  "schema_version": 1,
  "step_id": "worker-iteration-01",
  "attempt_id": "attempt-xxx",
  "replay_class": "external_non_replayable",
  "input_fingerprint": "sha256:...",
  "execution_ref": "executions/worker/execution.json",
  "execution_sha256": "sha256:...",
  "workspace_fingerprint": "sha256:...",
  "output_refs": [
    {
      "path": "worker-output.txt",
      "sha256": "sha256:..."
    }
  ]
}
```

step result 不重新维护 PID、heartbeat、deadline 或进程终态。需要展示终态时只能从绑定的
`execution.json` 派生，并在校验时拒绝语义不一致。

推荐写入顺序：

```text
1. validate current authority and workspace
2. allocate attempt_id / idempotency_key
3. prewrite attempt manifest or starting execution evidence
4. execute through existing ExecutionController
5. persist execution lifecycle evidence
6. capture terminal execution.json
7. capture current workspace and output artifacts
8. write append-only, content-addressed, tamper-evident step result manifest
9. update authoritative state.json
10. node returns minimal result
11. LangGraph checkpointer persists graph progress
12. append trace event bound to event_id / step_id / attempt_id / phase
```

SQLite checkpoint、JSON state、trace 和 workspace 无法形成一个跨介质原子事务，因此恢复安全
依赖 identity、现有 execution evidence、step result 和 reconciliation，而不是依赖理想化的
“一次提交”。

### 6.4 恢复判定表

| 现场 | 允许动作 |
|---|---|
| 没有 execution，workspace 与输入基线一致 | 可以分配 attempt 并执行 |
| execution 为 starting，且可证明 child 未启动 | 可以按预注册策略处理同一 attempt |
| execution 为 active 或终态未知 | `needs_human / ambiguous_external_side_effect` |
| terminal execution 与 step result 存在，workspace 一致 | 返回已记录结果，不重复外部动作 |
| terminal execution 存在但 step result 缺失 | reconcile 后安全补写，无法解释则 `needs_human` |
| step result 存在但 execution hash 不一致 | `needs_human / execution_binding_mismatch` |
| step result 存在但 workspace 已漂移 | `needs_human / stale_step_result` |
| artifact 存在但 execution / step result 不完整 | `needs_human`，不得猜测动作已完成 |
| 纯只读节点已完成且输入身份一致 | 可以安全重算 |
| verification 只完成部分命令 | 按 command execution 判断；未知副作用命令不得盲目重跑 |
| reviewer 外部调用终态未知 | 不重复调用，交还人工或按预注册策略处理 |
| final artifact 已写但 graph 终态未推进 | 校验证据后进行 terminal recovery |

### 6.5 外部进程身份

worker 或 reviewer 的 execution evidence 至少包含或绑定：

- PID；
- 进程创建时间；
- command hash；
- run / step / attempt identity；
- sandbox；
- 输出 artifact；
- 终态；
- 父进程和当前可确认的 process tree。

仅凭 PID 不足以判断原进程仍然存在，必须结合进程创建时间和 execution identity 防止 PID
复用。父进程崩溃后，若不能确认子进程是否仍在运行，不得启动同一 worker 的第二个 attempt。

Graph 节点重放还可能向 `trace.jsonl` 重复追加事件。Trace 不是业务真相源，但事件必须增加或
绑定稳定的 `event_id`、`step_id`、`attempt_id` 和 phase，使审计者能够区分同一 attempt 的
重复记录与真正的第二次外部执行。

---

## 7. Workspace 与控制面隔离

### 7.1 问题

Vega 的正式 artifacts 位于：

```text
runs/<run_id>/
```

当目标仓库就是 Vega 自身时，LangGraph SQLite 数据库也可能位于：

```text
runs/<run_id>/graph/checkpoints.sqlite
```

当前主线 workspace fingerprint 会观察 ignored 文件。SQLite、WAL、trace 和 report 的持续写入
可能让 Vega 把自己的 runtime 控制面误判为目标代码 workspace 漂移。

### 7.2 固定要求

第一轮优先保证 Graph 控制面位于**目标 Git 仓库之外**，但仍处于当前 Vega 项目授权边界内。

#### 第一轮方案：项目内独立目标仓库

```text
Vega workspace / control root:
  <vega-repo>/runs/<run_id>/

目标 fixture repo:
  <vega-repo>/.tmp/langgraph-fixtures/<case>/repo/
```

这样 Graph SQLite、WAL、trace 和 report 位于目标 Git repo 之外，目标 workspace fingerprint
不会观察 Vega 控制面，同时所有生成物仍留在当前项目边界。

固定要求：

- fixture 必须是独立 Git repo；
- fixture、basetemp 和验证产物放在 `.tmp/`；
- Vega 正式运行证据仍放在 `runs/`；
- control root 与 target repo 的解析后绝对路径必须互不包含；
- 不写用户级目录、仓库父目录或其他项目；
- 不使用 symlink、junction、hardlink 或 reparse point 绕过边界。

#### 后续方案 A：精确排除当前 run 控制面

- 只排除当前 Vega workspace 下、由 `resolve_runs_root` 验证过的真实 `runs/` 控制面；
- 不排除普通 ignored 文件；
- 不排除 tracked diff；
- 不接受 symlink、junction、hardlink 或其他越界路径；
- run root 与 target repo 关系必须明确记录；
- 只在专门的 Vega self-dogfood 实验中启用。

#### 后续方案 B：显式授权的外置 Graph Control Root

- Graph checkpoint 放在明确的 Vega control workspace；
- 目标 repo workspace fingerprint 不包含该目录；
- control root 的授权边界必须由项目 owner 明确修改或批准；
- state、artifact 和 checkpoint 通过 run identity 与 hash 绑定。

第一轮不实现 self-dogfood 排除机制，也不默认写用户级控制目录。先使用独立目标 repo 证明
LangGraph 的编排和恢复语义，再单独评估 Vega 对自身仓库运行时的控制面处理。

---

## 8. Graph State 约束

Graph state 的规范 schema 只在 `STATE-OWNERSHIP.md` 第 5 节定义。本执行计划不维护
第二份 `VegaGraphState`，避免 `graph_schema_version`、`state_ref` 或后续字段在不同文档中
漂移。

节点路由需要的 status、risk 或 verification 结论，应从当前绑定 execution、step result 或
artifact 读取，或由结构化结果返回，不长期维护多套独立镜像。

Graph state 禁止保存：

- 完整 prompt；
- 完整 diff；
- stdout / stderr；
- 完整测试日志；
- 完整聊天记录；
- worker 或 reviewer 的隐式推理；
- API key、Authorization、Cookie 或 `.env`；
- 未脱敏的本地敏感路径；
- reviewer 私有消息。

`review_results` 使用稳定 identity 映射，不使用简单 list append，避免节点重放时产生重复结果。
推荐 key：

```text
reviewer_role
+ evidence_snapshot_sha256
+ attempt_id
```

引擎在 run 创建后固定。禁止将已经由 `linear` 创建的 run 在恢复时切换为 `langgraph`，反之亦然。

---

## 9. Engine 与 Step Handler 边界

### 9.1 目标

LangGraph node 不复制 Vega 业务逻辑，只负责：

1. 解析当前引用；
2. 调用 engine-agnostic handler；
3. 写入 execution 引用、step result 与 artifacts；
4. 返回结构化结果；
5. 根据确定性字段路由。

### 9.2 不预设大重构

第一步不是立即把整个 `loop_runtime.py` 拆成通用框架，而是先建立最小可复用边界：

```text
prepare_run
capture_workspace
execute_worker_epoch
reconcile_workspace
run_verification
run_reflect
evaluate_risk
request_human_decision
dispatch_review
finalize_run
```

允许 Step Handler 调用已有 Runtime 服务。只有当两个引擎都需要同一逻辑时，才抽取公共实现。

### 9.3 停止线

出现以下情况时暂停 Gate 1：

- 为增加第二个引擎需要重写大部分线性 Runtime；
- 线性引擎必须改变现有成功语义才能适配 graph；
- artifact schema 被迫为 LangGraph 大规模重写；
- Handler 开始依赖 LangGraph 类型；
- 同一业务状态在 linear 和 graph 中出现两套模型；
- 仅为了抽象美观增加大量没有第二调用方的接口。

如果最小 adapter 足以完成可证伪实验，不继续追求平台化抽象。

---

## 10. Human-in-the-loop

### 10.1 第一轮动作

核心动作：

```text
approve
reject
```

第一轮继续复用现有 append-only decision ledger；“一次性”不通过修改旧 ledger entry 表达，
而是由独立、append-only 的 pending decision 与 consumption artifact 约束。一个 pending
identity 只能被同一个 decision identity 幂等消费，不能被第二个 decision 覆盖。

可消费的 approval 必须绑定：

- run id；
- engine；
- graph schema version；
- iteration；
- action type；
- workspace fingerprint；
- policy snapshot hash；
- 相关 evidence hashes；
- 允许继续的唯一下一步；
- 一次性使用状态。

### 10.2 `edit_scope`

`edit_scope` 不得直接复用旧 evidence：

```text
scope 变化
  -> policy version 变化
  -> 原 approval 失效
  -> 重新 preflight
  -> 重新校验 workspace
  -> 重新生成相关 risk / verification evidence
```

第一轮优先将 `edit_scope` 解释为“创建新 policy version，并开始新的受控执行阶段”；如果无法
证明同一 run 内恢复安全，则创建新 run，而不是强行 resume。

### 10.3 Decision Ledger 协议

人工决定只以 Vega decision ledger 为权威：

```text
1. validate pending interrupt identity
2. append decision entry referencing current pending artifact
3. obtain decision_id
4. resume graph with decision_id only
5. graph loads and validates ledger entry
6. write append-only consumption artifact
7. continue or fail-closed
```

Graph 不接受无法在 ledger 中重放的原始“批准”文本。发生以下情况时停止：

- resume value 与 ledger 不一致；
- decision 已被撤销；
- workspace 或 policy 在批准后变化；
- evidence hash 与批准绑定不一致；
- 同一一次性批准已被消费。

---

## 11. 并行隔离 Reviewer

### 11.1 角色

保留三个专业角色组成 reviewer pool，但每次由确定性 ReviewPlan 选择一至三路：

1. `correctness_reviewer`
   - 需求语义；
   - 行为正确性；
   - 边界条件；
   - 明显逻辑缺陷。

2. `verification_adequacy_reviewer`
   - 现有测试是否覆盖需求和主要边界；
   - 是否缺少能够推翻当前结论的验证；
   - verification evidence 是否完整、充分并绑定当前 workspace；
   - 不重新决定测试到底 passed 还是 failed。

3. `security_design_reviewer`
   - 安全设计；
   - 不可由静态规则完全判断的风险；
   - 新依赖的用途和信任边界；
   - 敏感信息和外部系统影响。

精确路径、变更数量、HEAD 漂移、测试是否失败等机器可判定问题继续由 deterministic gate 负责，
不交给 LLM reviewer 重复决定。

固定三路只作为压力测试和评估对照。候选默认是 `adaptive`：

```text
普通低风险
  -> correctness

测试范围变化或 verification 证据不足
  -> correctness + verification adequacy

风险路径、新依赖或较大设计影响
  -> correctness + security/design

需要多维交叉检查的高风险
  -> 三路
```

ReviewPlan 必须内容寻址，并记录 topology、required roles、role trigger reasons、
evidence snapshot identity 和 max parallelism。

### 11.2 输入隔离

每个 reviewer：

- 使用独立短生命周期 subgraph；
- 使用只读 runner；
- 读取同一个 versioned evidence package；
- 不读取 worker 完整聊天；
- 不读取其他 reviewer 私有上下文；
- 不保存跨 run 会话状态；
- 输出统一结构化 schema。

每条 finding 必须提供稳定去重 identity：

```text
category
+ rule_id
+ normalized_path
+ normalized_location
+ evidence_snapshot_sha256
```

Aggregator 先按 identity 去重，再统计 finding 数量和 reviewer 的边际新增发现，避免同一问题被
多路 reviewer 重复报告后误写成多个独立缺陷。

### 11.3 聚合

Aggregator 是纯确定性逻辑：

```text
verification != passed
  -> 不能 approve

存在 blocker / major
  -> request_changes

risk = high 且缺少有效 human approval
  -> needs_human

evidence stale / truncated / hash mismatch
  -> needs_human

同一高风险事实存在不可消解冲突
  -> needs_human

否则
  -> approve
```

自然语言总结不能覆盖硬规则。

### 11.4 Canary

分别加入：

```text
WORKER_PRIVATE_CANARY_<uuid>
CORRECTNESS_PRIVATE_CANARY_<uuid>
VERIFICATION_PRIVATE_CANARY_<uuid>
SECURITY_PRIVATE_CANARY_<uuid>
```

检查：

- worker 私有 canary 不进入 reviewer prompt、checkpoint 或 parent shared state；
- reviewer 私有 canary 不进入其他 reviewer；
- Aggregator 只读取结构化结果；
- output 中没有 canary 是补充证据，真正的隔离证据仍是 prompt、checkpoint 和 shared state。

---

## 12. 长任务与 Handoff

长任务属于扩展实验变量，但保留在完整计划中。它在核心编排、恢复和 HITL 通过后再执行，并单独
给出结论，避免污染 linear / LangGraph 顺序等价实验。

### 12.1 第一轮边界

- Goal contract 和 checkpoint plan 由用户或预注册 fixture 定义；
- 不让 LLM 自动无限拆分 checkpoint；
- 一个 checkpoint 对应一个短生命周期 worker epoch；
- 当前代码事实必须从 workspace 重读；
- handoff 只保存无法安全重猜的决定、约束、失败原因和引用；
- accepted memory 保持关闭；
- 不自动 commit、push 或发布。

### 12.2 Handoff 不成为第二套状态

`checkpoint-handoff.json` 主要保存：

- goal / checkpoint identity；
- 当前目标和下一动作；
- user / policy 级硬约束；
- 已验证事实的 statement + evidence ref；
- 已验证失败方案的适用条件；
- open questions；
- 当前权威 artifact refs；
- handoff 自身 hash 和 workspace fingerprint。

可直接从当前 workspace 或 verification artifact 读取的详细事实不重复复制。Handoff 过期时
必须重新编译，不能覆盖当前 Git 和 verification。

### 12.3 跨 Session 验证

至少使用两个全新 worker session：

1. Session A 完成第一个 checkpoint；
2. 生成 handoff；
3. 关闭 Session A；
4. Session B 只读取 checkpoint context 和权威 artifacts；
5. 不读取 Session A 完整聊天；
6. 检查能否恢复目标、硬约束、失败原因和下一步；
7. workspace / policy 不一致时阻止继续。

---

## 13. 分阶段执行

上一 Gate 未得到明确终态时不得进入下一 Gate。核心编排实验、Reviewer 扩展和 Goal/Handoff
分别形成可独立接受或拒绝的决策点，避免所有能力完成后才获得第一次真实反馈。

### Gate 0：冻结实验契约

工作：

- 固化 branch、baseline SHA、Python、Git、平台；
- 记录当前主线测试基线和已知 timeout；
- 固化 linear 默认行为；
- 固化第一轮 `memory.mode=off`；
- 完成 ADR、状态所有权、恢复协议和评测协议；
- 冻结 `DEMO.md` 的 Core Demo 演示契约；
- 预注册 crash windows、case 和指标；
- 记录模型与 runner 参数。

退出标准：

- 状态所有权无歧义；
- execution / step result 协议可解释 P0 crash window；
- 当前分支事实准确；
- 独立 reviewer 无未关闭 Blocker / High。

### Gate 1：最小 Engine / Handler 边界

> 执行状态：`pass`，结果见 `GATE-1-RESULT.md`。

工作：

- 增加 engine selection；
- run 创建后固定 engine；
- 建立最小 handler 边界；
- linear 继续使用现有语义；
- fake runner 回归。

退出标准：

- linear 核心回归通过；
- 现有终态和 artifact contract 无意外变化；
- 新 run 未指定 engine 时默认写入 `linear`；
- 旧 run 缺少 engine 字段时仍可 status、continue、finish 和 recover，并按 `linear` 解释；
- continue / recover 请求的 engine 与已持久化 engine 不一致时，在修改 run 前拒绝；
- Handler 不依赖 LangGraph；
- 没有复制业务模型。

### Gate 2：顺序 LangGraph 等价图

> 执行状态：`pass`，结果见 `GATE-2-RESULT.md`。

工作：

- optional dependency；
- 顺序节点和确定性 routing；
- 独立 run；
- 薄 Graph state；
- state / artifact 交叉校验。

退出标准：

- semantic parity case 全部通过；
- verification failed 不能 success；
- risk high 进入安全终态；
- Graph state 及其实际序列化输出不含大文本和凭证；
- 基础依赖环境未安装 LangGraph 时，`linear` 仍可导入、创建 run 和完成 Gate 1 回归；
- 安装 LangGraph 可选 extra 后，graph 路径才可导入和执行。

### Gate 3：Checkpoint 与恢复握手

> 执行状态：`pass`（2026-07-16）。复审见
> [`GATE-3-REVIEW.md`](GATE-3-REVIEW.md)。
>
> 实现与验证证据见
> [`GATE-3-RESULT.md`](GATE-3-RESULT.md)。

工作：

- SQLite checkpointer；
- 扩展并复用现有 execution evidence；
- content-addressed step result manifest；
- 恢复握手；
- workspace reconciliation；
- P0-1～P0-4 crash injection；
- terminal recovery。

退出标准：

- 重复 worker 启动为 0；
- 重复外部写副作用为 0；
- silent workspace drift 为 0；
- 未知副作用统一 fail-closed；
- checkpoint、execution、step result 和 artifacts 身份一致；
- 持久化 checkpoint 不含大文本、完整日志、凭证或 reviewer 私有消息。

### Gate 4：Human-in-the-loop

> 执行状态：`pass`（2026-07-16）。复审见
> [`GATE-4-REVIEW.md`](GATE-4-REVIEW.md)。
>
> 实现与验证证据见
> [`GATE-4-RESULT.md`](GATE-4-RESULT.md)。

工作：

- 结构化 interrupt；
- approve / reject；
- decision ledger 先写后 resume；
- 批准绑定；
- P0-5 decision 后 crash recovery。

退出标准：

- 未批准高风险不能 success；
- resume 不重复 interrupt 前副作用；
- 旧 approval 在 workspace / policy 变化后失效；
- ledger 与 checkpoint 一致。

### Gate 4.5：Core Dogfood

> 当前状态：`pass`（2026-07-17）。R6 真实 session 的三个业务 Case 全部通过，见
> [`GATE-4.5-R6-DOGFOOD-RESULT.md`](GATE-4.5-R6-DOGFOOD-RESULT.md)。
>
> R0～R5 的 blocked / partial-pass 结论保持历史冻结，不由 R6 反向改写。

目的：在引入并行 Reviewer 和长任务变量前，尽早回答 LangGraph 核心编排是否值得继续。

前提：

- Gate 0～4 deterministic tests 有明确终态；
- P0 crash windows 全部符合预注册期望；
- 无未关闭 Blocker / High；
- 任务、模型、预算、数据出站和成功标准已预注册；
- 使用当前项目内的独立 fixture repo；
- memory 关闭；
- 使用现有单 reviewer。

执行：

```text
fresh baseline A
  -> linear

fresh baseline B
  -> langgraph sequential
  -> checkpoint / reconciliation
  -> HITL
  -> single reviewer
```

Core Dogfood 只回答：

- 顺序语义是否一致；
- 外部 worker 是否会被重复启动；
- crash 后是安全继续还是安全交接；
- HITL 是否正确绑定 decision、workspace 和 evidence；
- LangGraph 是否值得继续进入 Reviewer fan-out。

### Gate 5：并行隔离 Reviewer

> 当前状态：`pass`（2026-07-18，星期六）。Phase 1 的结构化结果、稳定 identity、
> 窄引用 reducer 和
> deterministic aggregator 已通过，见
> [`GATE-5-PHASE-1-RESULT.md`](GATE-5-PHASE-1-RESULT.md)。
>
> Phase 2 已把固定三路降级为实验对照 topology，并实现确定性自适应 ReviewPlan，见
> [`GATE-5-PHASE-2-ADAPTIVE-PLAN-RESULT.md`](GATE-5-PHASE-2-ADAPTIVE-PLAN-RESULT.md)。
>
> Phase 3 已完成 append-only artifact、Graph State v2 和可变 N 路 fake fan-out，见
> [`GATE-5-PHASE-3-ARTIFACT-FANOUT-RESULT.md`](GATE-5-PHASE-3-ARTIFACT-FANOUT-RESULT.md)。
>
> Gate 5 已形成 role-specific 只读 Runner adapter、attempt 恢复、部分完成复用、
> Compatibility provenance 和冻结复审证据，见
> [`GATE-5-RESULT.md`](GATE-5-RESULT.md) 与
> [`GATE-5-REVIEW.md`](GATE-5-REVIEW.md)。
>
> 真实 provider 调用仍为 `0`。当前证据只覆盖确定性安全、真实执行 adapter 和恢复合同，
> 不证明真实模型质量或多 Reviewer 边际收益。Gate 5.1 hardening 已关闭 checkpoint 替换、
> 原子发布、撤销事件、owner PID、依赖门禁和 Step Result identity 风险。

已完成：

- `single` / `fixed_three` / `adaptive` ReviewPlan；
- 可变 N 路 reviewer subgraph；
- 从真实 run artifact 绑定同一 evidence snapshot；
- 稳定 reducer；
- 确定性 aggregator；
- fake canary 与完成顺序矩阵；
- result / execution / aggregate artifact hash 复核；
- 复用现有只读 Runner 的 role-specific 真实执行 adapter；
- 公共 evidence package 与独立角色 prompt；
- provider timeout / error、parse error、stop 和终态未知语义；
- 永久 run 级 stop latch，并向该 run 的全部 active execution 广播 stop request；
- 部分 fan-out 完成后的 checkpoint / resume 合同；
- 真实进程级 canary 隔离；
- claim-only、runner-started、terminal execution 和 metadata 崩溃窗口；
- Compatibility legacy reader 回溯 plan、pointer、result、execution、output 和 aggregate；
- Goal attachment 外部绑定 source、review kind 和 binding digest；
- SQLite 预读、逻辑内容摘要和 terminal recovery 第二次打开连续性；
- Step Result 文件名、execution 和 attempt 全 identity 绑定；
- Finish summary 提交标记绑定 report SHA-256；
- 最终独立复审确认 `Blocker=0 / High=0 / Medium=0`。

退出标准：

- reviewer context leak 为 0；
- reducer 与完成顺序无关；
- reviewer 不能覆盖验证失败；
- 旧 evidence 结果不能合并；
- 高风险冲突进入人工。

### Gate 5.5：Reviewer Dogfood

> 当前状态：`completed / single wins`。2026-07-19（星期日）在冻结提交
> `private-gate-5-5-eval-freeze-redacted` 上完成 1 次 preflight、72 次初始
> Reviewer session 和 9 次预注册 replicate。完整结果见
> [`GATE-5.5-RESULT.md`](GATE-5.5-RESULT.md)。

使用与 Core Dogfood 相同的已知任务类别和安全边界，验证：

- ReviewPlan 要求的一至三路 reviewer 读取同一 evidence snapshot；
- finding identity 去重；
- reviewer 完成顺序变化不影响聚合；
- provider timeout / error 不产生伪 approve；
- 在同一批 ground-truth 案例上比较 `single`、`fixed_three` 和 `adaptive`；
- 统计有效新增发现、precision、recall、false blocker、token 成本和总延迟；
- canary 不进入其他 reviewer 或 parent shared state。

固定三路不是默认答案。只有它相对 `adaptive` 有稳定、可复现且值得额外成本的收益时，
Core Decision 才能考虑保留固定 fan-out。

本轮按冻结 exact/alias matcher 计算，三种 topology 的 true positive 均为 0；
`adaptive` 和 `fixed_three` 只增加 false positive、clean false-major 与 token 成本。
因此保持默认 `single`，不把多 Reviewer fan-out 提升为产品默认。

正式预注册和结果见：

- [`GATE-5.5-PRE-REGISTRATION.md`](GATE-5.5-PRE-REGISTRATION.md)
- [`GATE-5.5-RESULT.md`](GATE-5.5-RESULT.md)

### Core Decision

> 当前状态：`partial`，见 [`CORE-DECISION.md`](CORE-DECISION.md)。

```text
core decision = partial
default product engine = linear
default review topology = single
Gate 6 = allowed only as an isolated extension experiment
```

Core Decision 不等待 Goal/Handoff 或 FastAPI。Gate 6 可以继续验证 Goal / Checkpoint /
Handoff，但不得把本轮负面 Reviewer topology 结果包装成多 Agent 收益，也不得提前切换默认
产品引擎。

### Gate 6：Goal / Checkpoint / Handoff 扩展实验

工作：

- worker epoch；
- checkpoint context compiler；
- versioned handoff；
- context budget；
- split checkpoint；
- 两个 fresh worker session 接力。

退出标准：

- Session B 不需要 Session A 完整聊天；
- 已验证事实和约束可恢复；
- 当前代码事实从 workspace 重读；
- handoff 漂移时阻止继续；
- 不自动写 accepted memory。

### Extended Decision

Gate 6 完成后单独输出 `EXTENDED-DECISION.md`，判断 Goal/Handoff 是否：

- 作为 LangGraph 扩展保留；
- 独立于 LangGraph 复用；
- 继续留在实验分支；
- 因状态重复或收益不足而不再推广；是否物理删除由最终决策另行说明。

### 独立附录：可选 FastAPI + SSE

FastAPI / SSE 不属于 LangGraph 核心或扩展实验的接受条件。只有 Core Decision 已完成且用户
明确决定需要演示控制面时，才在独立后续分支或提交中增加：

```text
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
POST /runs/{run_id}/resume
POST /runs/{run_id}/stop
```

固定边界：

- 不做前端；
- 不做多租户；
- 不做远程 worker 平台；
- API 只调用 engine；
- SSE 只是观察通道；
- 不新增第二套状态。

### 最终汇总

> 当前状态：`completed / partial`，见 [`DECISION.md`](DECISION.md)。

最终 `DECISION.md` 汇总 `CORE-DECISION.md` 与 `EXTENDED-DECISION.md`，分别回答：

1. 顺序编排是否值得？
2. checkpoint 是否真实改善恢复？
3. 安全恢复比例与安全停止比例分别是多少？
4. HITL 是否比现有 needs_human 更清晰？
5. 并行 reviewer 是否有新增有效发现？
6. Handoff 是否能独立复用？
7. 引入了多少额外状态和维护成本？
8. 哪些能力合入、保留实验、不再推广或另行决定删除？
9. 最终为 `accept / partial / reject` 的证据是什么？

---

## 14. 预注册故障窗口

完整威胁模型分为 P0 和 P1。只有 P0 全部符合预注册期望，才能进入 Core Dogfood。

### P0：核心不原子窗口

1. execution 创建前崩溃；
2. worker 已修改 workspace、terminal execution 尚未持久化前崩溃；
3. terminal execution 与 step result 已写、`state.json` 更新前崩溃；
4. `state.json` 已更新、graph checkpoint 前崩溃；按非终态与终态拆为 P0-4a / P0-4b；
5. decision ledger 已写、graph resume 前崩溃。

P0 必须证明：

- 不重复启动已产生副作用的 worker；
- 不让 graph checkpoint 覆盖权威业务状态；
- execution、step result 和 workspace 无法解释时统一进入 `needs_human`；
- decision 可以通过 decision id 安全重放，而不是重复创建批准。

### P1：完整恢复矩阵

P1 窗口、P1-6a/P1-6b、P1-16a/P1-16b、期望分类和自动继续口径只在
`EVAL-PROTOCOL.md` 第 4.3 节定义。本执行计划不复制 P1 矩阵或分类集合。结果出来后不得
修改该规范表来提高通过率。

---

## 15. 评测协议

### 15.1 分组

```text
A：Linear Runtime
B：LangGraph 顺序等价
C：LangGraph + checkpoint / recovery
D：LangGraph + HITL
E：LangGraph + parallel reviewers
F：LangGraph + Goal handoff
```

A/B 使用 fresh baseline，并共享：

- task contract；
- repo commit；
- policy snapshot；
- deterministic runner；
- verification commands；
- 成功语义；
- 业务 artifact schema。

### 15.2 Semantic Parity

不要求 run ID、时间戳、trace 事件和 graph 文件字节一致。Parity 比较：

- terminal status；
- verification status / failed count；
- risk / recommendation；
- workspace diff hash；
- 必需 artifact 类型和 schema；
- Finish / handoff 结论；
- 是否进入人工；
- 是否发生重复副作用。

engine-specific artifacts 单独记录，不进入业务 parity 分母。

### 15.3 指标

| 指标 | 说明 |
|---|---|
| Terminal State Parity | A/B 终态语义一致率 |
| Artifact Contract Parity | 必需业务 artifact 一致率 |
| Safety Invariant Pass Rate | 硬安全门禁通过率 |
| Duplicate Worker Starts | 已有副作用后重复启动次数 |
| Duplicate External Effects | 重复写入、命令或 provider 动作 |
| Automatic Resume Rate | 可以自动安全继续的 crash window 比例 |
| Safe Stop Rate | 正确进入 needs_human 的不确定窗口比例 |
| Unsafe Resume Count | 本应停止却自动继续的次数 |
| Silent Workspace Drift | 漂移后继续的次数 |
| Execution / Step Result / Checkpoint Consistency | 三者身份、引用和结果一致率 |
| Interrupt Consistency | interrupt、ledger、resume 一致率 |
| Reviewer Context Leak | canary 泄漏次数 |
| Reducer Determinism | 完成顺序变化后的聚合一致率 |
| Reviewer Marginal Findings | 多 reviewer 相对单 reviewer 的有效新增 finding |
| Handoff Consistency | handoff 与权威业务状态一致率 |
| Checkpoint Size | SQLite 与序列化状态规模 |
| Runtime Overhead | 相对 linear 的时间和 I/O 开销 |
| Core Change Footprint | 对稳定主线文件的侵入范围 |
| Test Wall Time | 实际验证耗时和 timeout |

`Automatic Resume Rate` 与 `Safe Stop Rate` 必须分开，不能把所有 `needs_human` 都包装成恢复成功。

### 15.4 硬门槛

```text
Safety invariant pass rate = 100%
Duplicate worker starts = 0
Duplicate external effects = 0
Unsafe resume count = 0
Silent workspace drift = 0
Reviewer context leak = 0
Invalid approve over verification failure = 0
Invalid success without required human approval = 0
Reducer nondeterminism = 0
```

Checkpoint 体积、运行开销和自动恢复率记录真实结果，不预设必须优于 linear。

---

## 16. 测试策略

### 16.1 实验测试

建议目录：

```text
tests/experimental/langgraph_engine/
  test_linear_graph_semantic_parity.py
  test_graph_state_contract.py
  test_step_result_manifest.py
  test_checkpoint_resume.py
  test_crash_windows.py
  test_workspace_reconciliation.py
  test_interrupt_resume.py
  test_decision_binding.py
  test_parallel_reviewers.py
  test_reviewer_isolation.py
  test_reducer_determinism.py
  test_context_budget.py
  test_checkpoint_handoff.py
  test_goal_cross_session.py
  test_secret_redaction.py
  test_target_repo_control_isolation.py
```

### 16.2 主线回归

至少覆盖：

```powershell
python -m compileall -q src
ruff check src tests
git diff --check
pytest -q --require-langgraph tests/experimental/langgraph_engine
pytest -q tests/test_success_semantics.py
pytest -q tests/test_evidence_freshness.py
pytest -q tests/test_runtime_safety_integration.py
pytest -q tests/test_finish_artifact_integrity.py
pytest -q tests/test_execution_control_safety.py
pytest -q tests/test_review_artifact_integrity.py
```

### 16.3 项目验证规则

- pytest 临时目录放入 `.tmp/pytest/runs/`；
- 不同分片使用独立 basetemp；
- cache 放入 `.tmp/pytest/cache/`；
- 单次测试超过 60 秒时按预注册 node 集合分片；
- 核对分片覆盖全部 collected tests；
- 只有明确计数可以作为证据；
- timeout 不等于通过或失败；
- 纯文档阶段只运行 `git diff --check`，并明确说明未运行代码测试的原因。

---

## 17. 接受、部分接受与拒绝标准

### Accept

- 安全硬门槛全部通过；
- 恢复协议没有重复副作用；
- LangGraph 明确改善了 checkpoint、HITL、并行 review 或 handoff；
- 线性 Runtime 未被迫改变核心成功语义；
- 状态所有权可以解释；
- 维护成本与收益匹配；
- 真实 dogfood 至少证明安全闭环，不夸大模型能力。

### Partial

- 顺序图本身收益有限，但 checkpoint / interrupt / reviewer fan-out 中部分能力有价值；
- 长任务 handoff 可独立于 LangGraph 复用；
- 需要保留 experimental adapter，暂不成为正式 engine；
- 真实 runner 证据不足，但 deterministic safety 已成立。

### Reject

- 需要复制或重写大部分业务 Runtime；
- Graph 与 Vega 无法避免两套真相源；
- 恢复主要依赖猜测，而不是 execution、step result 和 workspace 对账；
- 出现重复 worker 或外部副作用；
- 旧 approval、旧 evidence 或漂移 workspace 可以继续；
- 多 reviewer 只增加成本，没有新增有效信息；
- 大多数 crash window 只能产生与现有 recover 相同的结果，且没有改善表达或审计；
- 维护和测试负担显著增加，但没有可证明收益。

Reject 不等于实验失败。清楚证明“框架不值得引入”同样是有效的架构能力证据。

---

## 18. 提交与变更边界

建议提交顺序：

```text
文档：冻结 LangGraph 实验评审与执行契约
文档：明确状态所有权和恢复协议
重构：增加最小编排引擎选择边界
重构：抽取可复用 Loop Step Handler
功能：增加顺序 LangGraph 实验引擎
测试：增加 linear 与 graph 语义等价用例
功能：扩展 execution 并增加 step result 与 SQLite checkpoint
测试：增加 crash window 故障注入
功能：增加结构化 interrupt 与 decision 绑定
测试：验证批准失效和 resume 一致性
功能：增加并行隔离 reviewer subgraph
测试：增加 canary 和 reducer 确定性验证
功能：增加 Goal checkpoint handoff
测试：验证跨 fresh session 接力
文档：记录实验结果与最终决策
```

即使 AI 在数小时内完成多个提交，也不能合并成一个无法定位问题的大提交。

未经用户明确要求：

- 不切换分支；
- 不提交；
- 不 push；
- 不修改其他仓库；
- 不读取 `.env` 或凭证；
- 不写长期 Memory；
- 不自动清理失败现场。

---

## 19. 简历与面试输出

### 19.1 项目定位

推荐名称：

> **Vega：可验证、可恢复的 AI Coding Agent Runtime**

LangGraph 是关键实现证据，不应覆盖 Vega 的核心产品定位。

### 19.2 简历 Bullet 候选

只有对应实验完成后才能使用：

> 为自研 AI Coding Harness 增加可选 LangGraph 编排引擎，与线性 Runtime 复用 Step
> Handler 和 Artifact Contract；通过 execution evidence、step result、workspace fingerprint
> 与 artifact hash 对账处理外部代码写入的不可安全重放问题，并使用故障注入验证
> crash-resume 过程中重复 worker 启动为 0。

> 将正确性、测试证据和安全设计 Reviewer 实现为隔离只读 subgraph，绑定同一 evidence
> snapshot，并使用确定性 Aggregator 合并结果；确保 verification failure、evidence stale
> 或缺少人工批准时，模型 approve 无法升级为成功。

如果 Goal Handoff 完成：

> 设计 Goal / Checkpoint / Handoff 协议，使长任务可以由多个短生命周期 Coding Agent
> Session 接力；下一 Session 通过结构化证据恢复目标、硬约束和失败原因，无需继承上一
> Session 的完整聊天上下文。

### 19.3 面试重点

面试时优先解释：

1. 为什么 LangGraph checkpoint 不等于 Git workspace 事务；
2. 为什么节点恢复可能重复副作用；
3. execution、step result 和 reconciliation 如何解决 graph 与外部状态不原子的问题；
4. 为什么只允许一个 writer；
5. 为什么 reviewer approve 不能覆盖 verification；
6. 为什么 Memory、Goal 和 orchestration 是三个不同变量；
7. 最终哪些能力被接受，哪些被拒绝。

不要优先强调“代码量大”或“AI 一天写完”。真正有价值的是：

> 能用 AI 快速实现复杂系统，同时用独立验证、状态契约和负面结果防止高速生成错误。

---

## 20. 立即执行顺序

第一次代码变更前：

1. 将原始 LangGraph 实验草案纳入仓库受控文档；
2. 用当前真实 branch、Gate 0 开始前 HEAD=`private-gate-0-contract-redacted` 和代码实验基线=`private-experiment-base-redacted`
   替换占位基线；
3. 拆出：

   ```text
   ADR.md
   STATE-OWNERSHIP.md
   RECOVERY-CONTRACT.md
   EVAL-PROTOCOL.md
   DEMO.md
   ```

4. 固化项目内独立目标 repo 与 Vega control root 的路径隔离；
5. 完成 execution 扩展、step result schema 和恢复判定表；
6. 明确 engine 在 run 创建后不可切换；
7. 明确 decision ledger 先写后 resume；
8. 预注册 crash windows 和 semantic parity comparator；
9. 进行一次不读取实现会话完整上下文的独立架构评审；
10. 关闭 Blocker 后进入 Gate 1；
11. 第一段 LangGraph 代码只实现顺序图，不同时引入 Memory；
12. 后续 Gate 只依据退出证据推进，不以人工编码工期或 AI 生成速度替代 Gate 判断。

---

## 21. 最终原则

```text
不因人工开发显得宏大而主动降低目标。

不因 AI 生成很快而降低证据标准。

LangGraph 管理执行游标。
Vega 管理业务状态与证据。
execution.json 记录外部进程事实。
step result 绑定节点解释与输出证据。
Git workspace 提供当前代码事实。
Human decision 必须可审计。
Verification 决定是否真的通过。
```

最终叙事：

> One writes. Multiple isolated reviewers read. Runtime governs.
> Execution records process facts. Step results bind evidence. Reconciliation decides safe resume.

---

## 22. 参考资料

项目内：

- `README.md`
- `docs/PRODUCT-CONTRACT.md`
- `docs/ARCHITECTURE.md`
- `docs/LONG-RUNNING-GOALS.md`
- `docs/WORKSPACE-HYGIENE.md`
- `docs/experiments/SELECTIVE-MEMORY-REMINDER-PLAN.md`
- `src/vega/loop_runtime.py`
- `src/vega/execution_control.py`
- `src/vega/workspace_check.py`
- `src/vega/review_runtime.py`
- `src/vega/gate_runtime.py`
- `src/vega/goal_evidence.py`

其他实验分支：

- `experiment/selective-memory@private-selective-memory-calibration-redacted`
- `experiment/daily-loop-dogfood@private-dogfood-timeout-fix-redacted`
- `experiment/daily-loop-dogfood-mainline@private-mainline-dogfood-status-redacted`

LangGraph 官方语义：

- `https://docs.langchain.com/oss/python/langgraph/durable-execution`
- `https://docs.langchain.com/oss/python/langgraph/interrupts`
- `https://docs.langchain.com/oss/python/langgraph/persistence`
- `https://docs.langchain.com/oss/python/langgraph/use-subgraphs`
- `https://github.com/langchain-ai/langgraph-checkpoint-sqlite`

求职校准：

- 《2026-07-15 广州深圳中小企业 Agent 岗位市场快照》（本地研究输入，不随仓库提交）
