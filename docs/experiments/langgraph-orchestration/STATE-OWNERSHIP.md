# Gate 0 状态所有权契约

> 文档状态：`frozen-state-ownership-contract`
>
> 日期：2026-07-15

本文是 LangGraph orchestration 实验在 Gate 0 阶段必须冻结的状态所有权契约。它基于
`INDEPENDENT-REVIEW-AND-EXECUTION-PLAN.md`、当前 linear runtime 源码和现有 run
artifact 语义，目的是在引入 LangGraph checkpoint 前先回答一个问题：

```text
哪一份文件可以声明哪一类事实，发生冲突时谁说了算。
```

本契约是实现约束，不是概念说明。Gate 1 以后新增的 engine、handler、checkpoint、
step-result manifest 和 HITL resume 都必须遵守这里的权威边界；如果无法遵守，应停止在
`needs_human` 或暂停实验，而不是用 graph checkpoint 覆盖现有 Vega 事实链。

## 1. 适用范围

适用对象：

- 当前 `linear` runtime 生成的 Vega run。
- 后续 `langgraph` engine 为等价实验新增的独立 run。
- 同一个 run 内的 `state.json`、`trace.jsonl`、`executions/*/execution.json`、
  step-result manifest、workspace fingerprint、artifacts、decision ledger 和
  LangGraph checkpoint。

不适用对象：

- Git 本身的提交历史、远端分支和用户手工改动。
- 长期 accepted memory ledger。
- Web UI、后台 daemon、跨机器调度或多进程同时写同一个 run。

当前源码边界：

- `linear` 已存在并继续使用 `state.json`、`trace.jsonl`、`executions/*/execution.json`、
  workspace check、risk gate artifacts、review artifacts、finish artifacts 和
  `decisions.jsonl`。
- `step-result` 与 LangGraph checkpoint 目前尚未实现；本契约定义它们引入时的职责，不能
  反向改变 `linear` 的成功语义。

## 2. 权威范围

| 对象 | 权威范围 | 不拥有的事实 | 当前或目标位置 |
|---|---|---|---|
| `state.json` | 当前 run 的业务状态、当前步骤、迭代摘要、artifact 列表和最终状态 | 外部进程 PID/heartbeat、Graph 游标、完整日志、完整 diff、人工批准原文 | `runs/<run_id>/state.json` |
| LangGraph checkpoint | 图执行游标、下一节点、最小路由状态和可恢复的 graph metadata | 业务成功/失败结论、Git workspace 事务、外部进程终态、人工批准事实 | `runs/<run_id>/graph/*`，并由 manifest 绑定 |
| `execution.json` | 单个外部进程 attempt 的生命周期事实：owner PID、child PID、command、heartbeat、lease、deadline、returncode、终态 | 节点业务解释、artifact 是否可信、workspace 是否当前、review verdict | `runs/<run_id>/**/executions/<step>/execution.json` |
| step-result manifest | Graph 节点如何解释一次 attempt：输入身份、执行引用、执行哈希、workspace fingerprint、输出 artifact refs | PID、heartbeat、deadline、进程终态、完整输出内容 | `runs/<run_id>/step-results/<step_id>.json` |
| workspace | 当前代码事实：Git HEAD、staged diff、unstaged diff、untracked manifest、ignored manifest 和 fingerprint | run 状态、批准状态、Graph 路由状态 | 目标 repo 工作区，由 snapshot/fingerprint 记录 |
| artifacts | 业务结论成立的证据：prompt、worker 输出、verification、risk、review、finish、report、eval 等 | 当前状态机真相、进程生命周期、Graph 游标 | `runs/<run_id>/...` |
| decision ledger | 人工批准、拒绝、范围判断和原因 | Git 事实、执行终态、Graph checkpoint、长期 memory 决策 | 当前为 `runs/<run_id>/decisions.jsonl`；Graph HITL 可新增按 id 文件，但必须可追溯到 ledger |
| trace | 事件时间线、关键转移、诊断和审计线索 | 权威业务状态、唯一恢复来源、人工批准事实 | `runs/<run_id>/trace.jsonl` 或 goal 的 `goal-trace.jsonl` |

### 2.1 `state.json`

`state.json` 是当前 run 的权威业务状态。它回答：

- run 是否 `running`、`success`、`failed` 或 `needs_human`。
- 当前步骤是什么。
- 当前迭代摘要是什么。
- 哪些 artifact 属于本 run 的证据集合。
- risk、review、verification、goal checkpoint 等业务结论在该 run 中如何归档。

`state.json` 不保存：

- 外部进程的 PID、heartbeat、lease、deadline。
- LangGraph checkpoint cursor。
- 完整 stdout/stderr、完整测试日志、完整 prompt、完整 diff。
- 只能由 decision ledger 证明的人工批准。

写入规则：

1. 外部副作用节点必须先完成 execution 和 step-result 对账，再更新 `state.json`。
2. `state.json` 只能记录由当前已绑定 artifacts 支撑的业务状态。
3. `state.json` 损坏、schema 不合法或 run_id 与目录不一致时，不得由 checkpoint 猜测恢复；
   必须停止并保留诊断。
4. Goal P0 的 `state.json` 与 `goal-state.json` 必须完全一致；源码已有该校验，Graph 不得
   为 goal 引入第三份状态镜像。

### 2.2 LangGraph checkpoint

LangGraph checkpoint 只拥有图执行控制面。它回答：

- 图执行到哪个节点。
- 哪些最小引用可用于下一次路由。
- pending interrupt 或 pending human decision 的 id 是什么。
- reducer 合并后的最小 routing map 是什么。

LangGraph checkpoint 不回答：

- worker 是否真的完成。
- verification 是否通过。
- reviewer 是否 approve。
- high risk 是否已被人工批准。
- workspace 是否仍与 evidence 一致。
- run 是否最终 success。

恢复时即使 checkpoint 指向“下一节点”，也必须重新读取并校验：

1. `state.json` 的业务状态。
2. 对应 `execution.json` 的身份和终态。
3. 对应 step-result manifest 的 content hash 和 workspace fingerprint。
4. 当前 workspace fingerprint。
5. 相关 artifacts 的 hash。
6. pending decision 是否存在于 ledger 且仍有效。

任一项无法解释时，Graph 必须 fail-closed 到 `needs_human`，不得仅凭 checkpoint 继续。

### 2.3 `execution.json`

`execution.json` 是外部进程生命周期的唯一权威来源。当前源码中
`ExecutionLease` 已记录：

- `run_id`
- `step`
- `iteration`
- `owner_pid`
- `child_pid`
- `termination_unconfirmed`
- `command`
- `started_at`
- `last_heartbeat`
- `lease_expires_at`
- `deadline`
- `status`
- `reason`
- `returncode`
- `finished_at`

Gate 3 可以用兼容方式补充 `attempt_id`、`replay_class`、输入身份或 command hash，但不得
新增第二套进程状态机。Gate 1 只建立 engine / handler 边界，不实现恢复身份协议。尤其禁止：

- 在 step-result 里重新声明 PID、heartbeat、deadline 或进程终态。
- 在 Graph state 里维护“worker 已完成”这类独立终态。
- 对 active、unknown 或 `termination_unconfirmed=true` 的 execution 自动重跑外部节点。

### 2.4 step-result manifest

step-result manifest 是 Graph 节点解释执行结果的权威来源。它回答：

- 这个节点对应哪个 `step_id` 和 `attempt_id`。
- 该节点属于哪类 replay 语义。
- 它绑定哪个 `execution.json`。
- 它读取或产出了哪些 artifact。
- 它看到的 workspace fingerprint 是什么。
- 它如何把 execution 和 artifact 解释成结构化节点结果。

规范 schema 以 `RECOVERY-CONTRACT.md` 第 5 节为唯一来源。本文不维护第二份 schema，
只约束字段的所有权和冲突处理。

约束：

1. `execution_ref` 必须指向同一 run 内的 `execution.json`。
2. `execution_sha256` 必须匹配读取时的文件内容。
3. `workspace_fingerprint` 必须来自同一算法族，且在继续前与当前 workspace 对账。
4. `output_refs` 只保存相对路径和 hash，不内嵌大文本。
5. `result_summary` 只能保存节点路由所需的摘要，不替代 artifact 内容。
6. 对 `external_non_replayable` 节点，manifest 缺失或 hash 不一致时不能重跑，只能进入
   reconciliation 或 `needs_human`。

### 2.5 workspace

workspace 是代码事实来源。它包括：

- `git rev-parse HEAD`
- staged diff
- unstaged diff
- untracked manifest
- ignored manifest
- 由上述内容组合出的 workspace fingerprint

workspace fingerprint 是证据绑定，不是状态机。它不能声明 run success，也不能批准继续。

恢复和 resume 前必须检查：

- 当前 repo path 与 run 中记录的 `repo_path` 是否一致。
- 当前 fingerprint 是否等于 step-result、reflect、review 或 approval 绑定的 fingerprint。
- 如果 fingerprint 不一致，是否有新的人工 decision 明确开启新阶段。

无法证明一致时，必须停止在 `needs_human / stale_workspace` 或更具体的错误码。

### 2.6 artifacts

artifacts 是业务结论的证据，不是状态所有者。它们可以证明：

- worker 看到了什么 brief 和 prompt。
- verification 实际运行了哪些命令、输出了什么摘要。
- risk gate 的 `risk` 和 `recommendation` 如何得出。
- reviewer verdict 和 findings 是什么。
- finish 是否认为可以进入提交前检查。

约束：

1. `state.artifacts` 必须声明当前 run 依赖的核心 artifact。
2. 缺失核心 artifact 时不能成功。
3. artifact 被篡改、hash 不一致或与 `state.json` 摘要矛盾时，进入 `needs_human`。
4. artifact 不能覆盖 deterministic verification failure。
5. reviewer artifact 不能覆盖 risk gate 的 `human-review` 要求。

### 2.7 decision ledger

decision ledger 是人工决定的唯一权威来源。当前实现是 append-only 的
`runs/<run_id>/decisions.jsonl`，每条 `DecisionEntry` 包含：

- `id`
- `run_id`
- `type`
- `decision`
- `reason`
- `actor`
- `references`
- `created_at`

Graph HITL 的最低要求：

1. 先写 ledger，再 resume graph。
2. resume 只能传 `decision_id` 或其稳定引用，不能传无法审计的原始“批准”文本。
3. Graph 必须重新读取 ledger，并校验 run、type、decision、references、workspace、
   policy 和 evidence 绑定。
4. 一次性批准消费后必须可审计，旧 approval 在 workspace、policy 或 evidence 变化后失效。
5. 当前 `DecisionStore` 不原地增加 revoked / consumed 字段；Gate 4 通过
   `graph/pending-decisions/<pending_id>.json` 与
   `graph/decision-consumptions/<pending_id>.json` 显式表达 binding 和一次性消费。
6. 同一 pending identity 与同一 decision identity 可以幂等复用；不同 decision identity
   试图消费同一 pending 时必须冲突并停止。

decision ledger 不替代：

- Git workspace。
- verification artifact。
- risk gate artifact。
- review artifact。
- accepted memory ledger。

### 2.8 trace

trace 是审计时间线，不是业务状态真相。它用于：

- 复盘事件顺序。
- 发现缺失事件、重复事件或异常窗口。
- 交叉校验 risk gate、finish、recover 等路径是否走过。
- 支持人工诊断。

trace 不得作为唯一恢复来源。`trace.jsonl` 里出现 `run_finished` 不等于成功成立，仍要以
`state.json`、artifacts、execution 和 workspace 对账为准。

## 3. 冲突优先级

当多个对象声明的事实不一致时，按以下顺序处理。

### 3.1 身份与边界先于一切

以下冲突直接停止，不进入优先级合并：

1. run 目录名与 `state.run_id` 不一致。
2. `execution.json.run_id` 与 run 目录名不一致。
3. artifact、step-result 或 checkpoint 引用了 run 外路径。
4. workspace repo 与 `state.repo_path` 不一致。
5. `runs/` 越过 workspace 边界或是 symlink、junction、reparse point。
6. 文件含未脱敏凭证、`.env` 内容、Authorization header 或 API key。

处理结果：`needs_human / identity_or_boundary_mismatch`，不得自动修复。

### 3.2 业务状态冲突

| 冲突 | 优先级 | 处理 |
|---|---|---|
| `state.json` 与 Graph checkpoint 对 run 状态理解不一致 | `state.json` 优先 | 校验 execution、step-result、workspace 和 artifacts；无法解释则 `needs_human` |
| `state.json` 声明 success，但核心 artifact 缺失 | artifact 完整性优先 | 标记失败或 `needs_human`，不得 success |
| `state.json` 声明 success，但 verification artifact 失败 | verification artifact 优先 | 不得由 reviewer 或 decision 覆盖为 success |
| `state.json` 声明 review approve，但 reviewer artifact 缺失或 request_changes | review artifact 优先 | `needs_human` 或 failed |
| risk gate 要求 `human-review`，但缺少有效 decision | risk gate artifact 优先 | `needs_human` |
| trace 显示 finished，但 state 或 artifact 不支持 | state/artifact 优先 | trace 只作为诊断 |

### 3.3 外部执行冲突

| 冲突 | 优先级 | 处理 |
|---|---|---|
| `execution.json` active 或终态未知，Graph 想重跑外部节点 | `execution.json` 优先 | 拒绝重跑，`needs_human / ambiguous_external_side_effect` |
| terminal execution 存在，step-result 缺失 | `execution.json` 加 workspace 对账优先 | 可安全解释才补写 manifest，否则 `needs_human` |
| step-result 存在但 `execution_sha256` 不匹配 | `execution.json` 文件事实优先 | `needs_human / execution_binding_mismatch` |
| step-result 存在但 workspace drift | 当前 workspace 优先 | `needs_human / stale_step_result` |
| artifact 存在但 execution 与 step-result 不完整 | 执行证据优先 | 不得猜测外部动作已完成 |

### 3.4 人工决定冲突

| 冲突 | 优先级 | 处理 |
|---|---|---|
| resume value 与 ledger 不一致 | ledger 优先 | 停止 |
| ledger approval 与当前 workspace/policy/evidence 不一致 | 当前事实优先 | approval 失效 |
| old approval 被重复消费 | consumption binding 优先 | 停止 |
| decision 试图覆盖 verification failed | verification 优先 | 不得 success |
| decision 试图覆盖缺失 artifact | artifact 完整性优先 | 不得 success |

## 4. 禁止双真相源

以下规则是硬约束。

1. 禁止同时让 `state.json` 和 Graph checkpoint 各自声明业务终态。
2. 禁止同时让 `execution.json` 和 step-result 各自声明进程终态。
3. 禁止同时让 reviewer artifact 和 verification artifact 决定测试是否通过。
4. 禁止同时让 decision ledger 和 risk gate artifact 决定是否需要人工。
5. 禁止把 trace event 当成状态机的唯一恢复依据。
6. 禁止把 workspace fingerprint 当成“已批准继续”的事实。
7. 禁止把 accepted memory 或项目经验覆盖当前 run artifacts。
8. 禁止为 LangGraph 单独复制一套 `LoopAutomationState`、`LoopIterationState`、
   `ReviewState` 或 `GateState` 业务模型。

允许的镜像只有两类：

- 为快速路由保存的最小引用，如 `latest_step_result_id`。
- 为兼容已有读取链路保留的镜像，如 Goal P0 中完全一致的 `state.json` 和
  `goal-state.json`。

任何镜像都必须可由权威来源校验。校验失败时，镜像作废，不能反向覆盖权威来源。

## 5. Graph state 最小契约

Graph state 只能保存路由所需的最小字段。推荐 TypedDict：

```python
class VegaGraphState(TypedDict):
    schema_version: int
    engine_version: str
    graph_schema_version: str
    run_id: str
    engine: Literal["langgraph"]
    task_contract_ref: str
    state_ref: str
    policy_snapshot_ref: str
    policy_snapshot_sha256: str
    latest_step_result_id: str | None
    pending_human_decision_id: str | None
    review_results: Annotated[
        dict[str, "ReviewResultRef"],
        merge_review_results_by_identity,
    ]
    terminal_ref: str | None
```

字段含义：

- `schema_version`：Graph state schema 版本。
- `engine_version`：Vega graph engine 版本。
- `graph_schema_version`：节点和路由 schema 版本。
- `run_id`：当前 run 身份。
- `engine`：固定为 `langgraph`。
- `task_contract_ref`：任务或 goal contract 的 artifact 引用。
- `state_ref`：权威 `state.json` 引用。
- `policy_snapshot_ref`：项目策略快照引用。
- `policy_snapshot_sha256`：策略快照 hash。
- `latest_step_result_id`：最近一次节点结果 manifest 的 id。
- `pending_human_decision_id`：当前等待消费的 pending decision identity，不是 ledger
  `decision_id`。
- `review_results`：按稳定 identity 合并的 reviewer result refs。
- `terminal_ref`：终态 artifact 或 final report 引用。

节点路由如果需要 `status`、`risk`、`recommendation`、`verification_status` 或
`verdict`，必须从当前绑定的 artifact、step-result 或 `state.json` 读取，或由当前节点返回
结构化结果。不得在 Graph state 中长期维护第二套业务字段。

### 5.1 Graph state 禁入内容

Graph state 和 checkpoint 禁止保存：

- 完整 prompt。
- 完整 diff。
- stdout 或 stderr。
- 完整测试日志。
- 完整聊天记录。
- worker 或 reviewer 的隐式推理。
- reviewer 私有消息。
- API key、Authorization、Cookie、token、`.env` 内容。
- 未脱敏的本地敏感路径。
- 大型 artifact 正文。
- 可从 `execution.json` 派生的 PID、heartbeat、deadline 或终态。
- 可从 decision ledger 派生的批准原文。

### 5.2 reducer 规则

并行 reviewer 或 fan-out 节点必须使用稳定 identity map，不使用简单 list append。

推荐 reviewer key：

```text
reviewer_role
+ evidence_snapshot_sha256
+ attempt_id
```

同一 key 重放时必须覆盖同一值或判定为冲突，不得产生重复 reviewer 结果。不同完成顺序下，
`review_results` 合并结果必须确定。

## 6. 写入与恢复顺序

### 6.1 外部副作用节点

所有 `external_non_replayable` 节点必须按以下顺序写入：

```text
1. 读取并校验 state.json、policy snapshot 和当前 workspace。
2. 分配 attempt_id 和 idempotency key。
3. 预写 attempt manifest 或兼容扩展后的 starting execution evidence。
4. 通过现有 ExecutionController 启动外部进程。
5. 写入并持续更新 execution.json。
6. 进程终止后读取 terminal execution.json。
7. 捕获当前 workspace fingerprint 和输出 artifact hash。
8. 写入 content-addressed step-result manifest。
9. 更新 state.json。
10. 节点返回最小 routing result。
11. LangGraph checkpointer 持久化图游标。
12. 追加绑定 event_id、step_id、attempt_id 和 phase 的 trace 事件。
```

如果在任一步骤崩溃，恢复时不得跳过前置校验。特别是：

- 第 5 步后崩溃：先按 `execution.json` 判断是否有存活或未知副作用。
- 第 6 步后崩溃：可对 terminal execution 做 workspace reconciliation，安全时补写
  step-result。
- 第 8 步后崩溃：可读取 step-result 和 artifact hash 更新 `state.json`。
- 第 9 步后崩溃：checkpoint 缺失不代表业务状态未更新；以 `state.json` 对账。

### 6.2 纯计算和只读节点

`pure_replayable` 与 `read_only_replayable` 节点可以在输入身份一致时重放，但仍要遵守：

- 不写目标 repo。
- 不产生外部副作用。
- 不读取禁入凭证。
- 不把重算结果覆盖已有权威 artifact，除非 hash 一致或进入新 attempt。

### 6.3 HITL resume

HITL 必须按以下顺序：

```text
1. Graph 产生结构化 interrupt artifact。
2. 用户或主会话作出决定。
3. 先 append decision ledger。
4. resume 只传 decision_id。
5. Graph 读取 ledger 并验证 binding。
6. 校验 workspace、policy、evidence 是否仍与批准绑定一致。
7. 继续执行或 fail-closed。
```

禁止：

- resume 直接传自然语言批准。
- Graph 在 ledger 写入前继续。
- 旧 approval 在 workspace、policy 或 evidence 变化后继续生效。

## 7. 兼容现有 linear runtime

LangGraph 实验不得破坏当前 linear 行为。

### 7.1 engine 固定

run 创建后必须固定 engine：

- `linear` run 只能由 linear runtime 继续或 recover。
- `langgraph` run 只能由 graph runtime 继续或 recover。
- 禁止把既有 `linear` run 在恢复时切换成 `langgraph`。
- 禁止把 `langgraph` run 降级为 `linear` 来绕过 checkpoint 对账。

Gate 1 引入 engine selection 时，应以新增字段或 engine manifest 记录，不得改变旧 run 的 schema
校验。旧 run 缺少 engine 字段时，按 `linear` 解释。

### 7.2 linear artifact contract 保持不变

当前 linear run 的核心 artifact 继续有效：

```text
state.json
trace.jsonl
agent-brief.md
project-context.md
loop-plan.md
worker-prompt.md
iterations/<n>/executions/<step>/execution.json
iterations/<n>/workspace-check.md
iterations/<n>/verification-summary.md
iterations/<n>/risk-gate-result.json
iterations/<n>/risk-gate-report.md
final-report.md
eval.md
decisions.jsonl
```

Graph 可以新增：

```text
graph/checkpoint-manifest.json
graph/checkpoints.sqlite
step-results/<step_id>.json
workspace/<snapshot_id>.json
graph/pending-decisions/<pending_id>.json
graph/decision-consumptions/<pending_id>.json
```

但新增文件只能服务 graph run，不能要求 linear run 生成它们。linear eval、finish、recover 和
status 不应因为缺少 graph-only artifact 而失败。

### 7.3 复用现有安全语义

Graph runtime 必须复用或等价遵守以下现有 linear 语义：

- 外部进程由 `ExecutionController` 管理。
- stop 只请求当前 run 中已记录的 active execution。
- recover 不杀进程、不清理 workspace、不继续执行。
- 任一 active execution 仍有存活 PID 时拒绝接管。
- worker stopped、timed_out、provider error 或 unknown side effect 后进入 `needs_human`。
- verification failed、stopped 或 timed_out 不能被 reviewer approve 覆盖为 success。
- reviewer stopped、timed_out 或 runner error 不能解析成 approve。
- workspace drift、policy drift 或 evidence stale 时停止。
- 当前本地单用户 CLI 不支持多个进程并发写同一个 run。

## 8. Gate 0 可执行检查清单

实现 Gate 1 前，必须能逐项回答：

1. 每个业务事实是否只有一个权威来源。
2. 每个新增 graph-only artifact 是否不会改变 linear 成功语义。
3. `state.json` 与 checkpoint 冲突时是否总以 `state.json` 和证据对账为准。
4. `execution.json` 与 step-result 冲突时是否拒绝猜测外部副作用。
5. workspace fingerprint 漂移时是否统一停止。
6. decision 是否先写 ledger 再 resume。
7. Graph state 是否只含最小引用和 routing data。
8. checkpoint 是否不含大文本、凭证、完整日志或 reviewer 私有消息。
9. reviewer fan-out reducer 是否确定且不会重复 append。
10. 旧 `linear` run 是否仍可 status、finish、recover。

Gate 0 退出标准：

- 本文档已作为实现约束被引用。
- Gate 1 的 engine/handler 设计不需要复制业务状态模型。
- Gate 2 的 Graph state schema 能通过禁入内容审查。
- Gate 3 的恢复协议能解释 P0-1～P0-4 crash windows。
- Gate 4 的 HITL resume 能解释 P0-5，并证明 ledger、workspace、policy 和 evidence 绑定。

## 9. 失败处理原则

遇到以下情况时，不得“自动修好”：

- 身份不一致。
- 路径越界。
- execution active、unknown 或 termination unconfirmed。
- step-result 与 execution hash 不一致。
- workspace fingerprint 漂移。
- artifact 缺失或 hash 不一致。
- decision 绑定失效。
- checkpoint 包含禁入内容。
- Graph state 与 `state.json` 业务事实冲突且无法由证据解释。

统一处理：

```text
写入诊断 artifact
更新 state.json 为 needs_human 或 failed
写入 trace 事件
停止自动执行
交还人工
```

如果 `state.json` 本身损坏，不能覆盖它；应写入独立 recovery report，并要求人工决定是否创建
新 run。
