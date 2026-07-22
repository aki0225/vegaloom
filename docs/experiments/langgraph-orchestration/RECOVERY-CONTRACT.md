# Vega LangGraph Gate 0 恢复契约

> 文档状态：`frozen-recovery-contract`
>
> 日期：2026-07-15
>
> 依赖文档：`INDEPENDENT-REVIEW-AND-EXECUTION-PLAN.md`
>
> 适用范围：LangGraph 编排实验 Gate 0～Gate 4 的恢复协议冻结
>
> 当前主线事实：线性 Runtime 仍是默认行为；本文件定义后续实验契约，不声明当前主线已经具备
> LangGraph checkpoint / step result 恢复能力。

---

## 1. 目标与非目标

本契约回答一个具体问题：

> 当 LangGraph 节点可能被 checkpoint / resume 重新进入，而 worker、reviewer、verification
> 等外部动作已经对 Git workspace 或 provider 产生副作用时，Vega 如何判断可以重放、复用、
> 补写、终止恢复，或必须交还人工。

目标：

- 固化 Gate 0 恢复语义，作为 Gate 3 / Gate 4 实现和故障注入测试的判定标准；
- 保持现有 `execution.json` 是外部进程生命周期事实来源；
- 通过 `step-result.json` 绑定 Graph 节点解释、execution、workspace 和 artifacts；
- 在 checkpoint、JSON state、trace、workspace 无法原子提交时，使用 identity 和 reconciliation
  避免重复外部副作用；
- 对无法证明安全的现场统一 fail-closed。

非目标：

- 不实现 Git workspace 回滚；
- 不把 LangGraph checkpoint 作为业务状态权威；
- 不新增第二套进程生命周期状态机；
- 不自动杀进程、清理工作区或继续执行半完成 worker；
- 不把 fake runner 结果包装成真实模型恢复能力。

---

## 2. 权威来源

| 信息 | 权威来源 | 恢复约束 |
|---|---|---|
| 业务状态 | `state.json` | Graph checkpoint 不得覆盖更可信的业务状态 |
| 外部进程生命周期 | `execution.json` | step result 只能引用和校验，不得重新声明 PID、heartbeat、deadline 或终态 |
| Graph 节点输出解释 | `step-results/<step_id>.json` | 必须绑定 execution hash、workspace fingerprint 和输出 artifact hash |
| 当前代码事实 | Git HEAD、tracked diff、untracked manifest、workspace fingerprint | 恢复前必须重新采集并对账 |
| 过程审计 | `trace.jsonl` | 不是业务真相源；重复事件必须可用 event / attempt identity 区分 |
| 图执行游标 | LangGraph checkpoint | 只表示图进度和最小 routing 数据 |
| 人工决定 | decision ledger | 必须先写 ledger，再 resume；批准必须绑定 workspace / policy / evidence |

冲突处理原则：

1. `state.json`、`execution.json`、`step-result.json`、workspace 和 checkpoint 全部一致时，允许自动继续。
2. checkpoint 与 `state.json` 不一致时，不用 checkpoint 覆盖业务状态；进入 reconciliation。
3. `execution.json` 与 step result 绑定不一致时，进入 `needs_human`。
4. workspace 与 step result 绑定不一致时，进入 `needs_human`。
5. 无法解释差异时，统一 fail-closed，不猜测动作已经完成。

---

## 3. Replay Class

每个 Graph 节点在实现前必须预注册 replay class。未注册节点不得进入恢复测试。

| replay class | 定义 | 允许恢复动作 | 禁止动作 |
|---|---|---|---|
| `pure_replayable` | 纯计算；不读写外部状态，不调用 provider | 输入身份一致时可重算 | 不得读取 workspace 当前漂移来改变旧结论 |
| `read_only_replayable` | 只读 Git、artifact 或确定性报告 | 输入身份一致且读取目标一致时可重跑 | 不得写 workspace，不得调用有外部副作用的命令 |
| `external_non_replayable` | worker、provider 调用、可能写 workspace 的 verification command | 只能通过 execution、step result 和 workspace reconciliation 复用或交还人工 | 不得在未知终态下重复启动同一 attempt |
| `human_interrupt` | 等待人工批准、拒绝或修改范围 | 可通过 decision id 恢复等待或消费一次性批准 | 不得重复创建批准，不得在 evidence 漂移后复用旧批准 |
| `terminal_recovery` | final artifact 已写但 graph 终态未推进 | 校验 artifact、state、execution、step result 后补推进终态 | 不得绕过 verification / risk / decision 直接 success |

`verification` 默认按命令粒度分类。只读、确定性、无工作区写入的命令可以作为
`read_only_replayable`；可能写文件、访问外部服务或产生不可重复副作用的命令必须按
`external_non_replayable` 处理。

---

## 4. Attempt Identity

所有可能产生外部副作用的节点必须有稳定 attempt identity。attempt identity 的职责是证明：

- 这是哪个 run、哪个 engine、哪个 graph schema、哪个 step、哪个 iteration；
- 这次外部动作基于哪个 Git HEAD、workspace baseline、policy snapshot 和命令；
- 如果 Runtime 崩溃，恢复方可以判断“这是同一 attempt 的补写 / 复用”，还是“新的外部执行”。

### 4.1 必填字段

```json
{
  "schema_version": 1,
  "run_id": "loop-run-id",
  "engine": "langgraph",
  "graph_schema_version": "1",
  "step_id": "worker-iteration-01",
  "step_name": "worker",
  "iteration": 1,
  "attempt_id": "attempt-...",
  "idempotency_key": "sha256:...",
  "replay_class": "external_non_replayable",
  "runner_identity": {
    "kind": "codex",
    "model": "gpt-...",
    "reasoning_effort": "..."
  },
  "base_head": "git-sha",
  "before_workspace_fingerprint": "sha256:...",
  "policy_snapshot_sha256": "sha256:...",
  "command_sha256": "sha256:...",
  "started_at": "2026-07-15T00:00:00+00:00"
}
```

### 4.2 Identity 生成规则

- `attempt_id` 在外部动作启动前分配，写入 attempt manifest 或兼容扩展后的 execution evidence。
- `idempotency_key` 由稳定输入派生，不得包含随机时间戳；建议绑定 run、step、iteration、
  base head、workspace fingerprint、policy snapshot 和 command hash。
- 同一 `attempt_id` 只能对应一次外部动作启动。
- 第二次进入同一 node 时，如发现同一 `attempt_id` 已有 terminal execution 或 step result，只能走
  reuse / reconcile，不能重新启动 worker。
- 如输入身份发生变化，必须分配新的 `attempt_id`，并明确记录旧 attempt 不再可自动复用。

### 4.3 与 `execution.json` 的关系

现有 `ExecutionLease` 记录 owner PID、child PID、heartbeat、deadline、status、returncode 和
termination evidence。Gate 3 可以通过兼容扩展或窄 attempt manifest 绑定上述 identity，但必须满足：

- `execution.json` 仍是进程生命周期权威来源；
- attempt manifest 不得声明独立进程终态；
- 如果 attempt manifest 与 `execution.json` 的 run、step、iteration 或 command hash 不一致，
  recovery 必须拒绝自动接管；
- `termination_unconfirmed=true` 时，即使 PID 当前不可见，也不能把该 attempt 当成安全终态。
- active execution 的 owner 或 child PID 仍存活时一律拒绝 Graph recovery；
- terminal execution 的 child PID 仍存活时一律拒绝；
- terminal worker 的 owner PID 仍存活时，只有 run 级 Graph operation lock 已释放，且
  content-addressed Step Result 同时绑定文件名 `step_id`、`attempt.json`、`execution.json`
  及全部稳定 identity，才允许继续 HITL resume；否则仍按可能正在提交证据处理。

---

## 5. Step Result Schema

`step-result.json` 是 Graph 节点完成后的内容寻址结果清单。它说明“这个节点如何解释已有证据”，
而不是重新管理进程。

### 5.1 最小 schema

```json
{
  "schema_version": 1,
  "step_result_id": "sha256:...",
  "run_id": "loop-run-id",
  "engine": "langgraph",
  "graph_schema_version": "1",
  "step_id": "worker-iteration-01",
  "attempt_id": "attempt-...",
  "replay_class": "external_non_replayable",
  "input_fingerprint": "sha256:...",
  "base_head": "git-sha",
  "before_workspace_fingerprint": "sha256:...",
  "after_workspace_fingerprint": "sha256:...",
  "execution_ref": "executions/worker/execution.json",
  "execution_sha256": "sha256:...",
  "output_refs": [
    {
      "path": "executions/worker/process-output.txt",
      "sha256": "sha256:..."
    },
    {
      "path": "workspace/after-worker.json",
      "sha256": "sha256:..."
    }
  ],
  "result": {
    "status": "completed",
    "summary": "worker produced tracked diff"
  },
  "created_at": "2026-07-15T00:00:00+00:00"
}
```

### 5.2 约束

- `step_result_id` 必须由规范化 JSON 内容计算，不得由文件路径或写入时间单独决定。
- `execution_sha256` 必须覆盖当时已进入 terminal 或可解释状态的 `execution.json`。
- `after_workspace_fingerprint` 必须来自恢复协议认可的 workspace snapshot。
- `output_refs` 只能引用项目授权边界内的相对路径。
- step result 不保存原始 secret、Authorization header、provider raw response 中的敏感字段。
- step result 不包含 PID 存活判断；需要判断时重新读取 `execution.json` 和系统进程。
- step result 一经写入按 append-only 处理；需要修正时写新的 manifest，并记录 supersedes 关系。

---

## 6. 写入顺序

跨 SQLite checkpoint、JSON state、trace、workspace 和 Git 文件无法形成原子事务。恢复安全依赖固定写入顺序
和 crash window 解释。

### 6.1 外部节点写入顺序

```text
1. 校验当前 state、policy、workspace baseline 和 replay class
2. 分配 attempt_id / idempotency_key
3. 预写 attempt manifest 或兼容扩展后的 starting execution evidence
4. 通过现有 ExecutionController 启动外部动作
5. 记录 child_started、heartbeat、stop / timeout / terminal execution
6. 捕获 terminal execution.json 的 hash
7. 捕获当前 workspace snapshot 和 output artifact hash
8. 写 append-only、content-addressed step-result manifest
9. 更新权威 state.json
10. node 返回最小结构化结果
11. LangGraph checkpointer 持久化图进度
12. 写入 trace 事件，事件必须绑定 event_id、step_id、attempt_id 和 phase
```

### 6.2 Human decision 写入顺序

```text
1. interrupt 前写入 pending decision identity
2. 人工决定写入 decision ledger
3. 校验 decision 绑定的 workspace / policy / evidence 仍有效
4. graph resume 消费 decision id
5. 写入 append-only decision consumption artifact
6. state.json 进入下一业务状态
7. checkpoint 持久化 resume 后游标
```

decision ledger 先于 graph resume。若 ledger 已写但 checkpoint 未推进，恢复时可以按 decision id 安全继续；
若 checkpoint 声称已批准但 ledger 缺失，必须进入 `needs_human`。

若 consumption artifact 已写但 `state.json` 或 checkpoint 尚未推进，恢复必须复用同一
decision identity；不同 decision identity 不得覆盖既有 consumption。

---

## 7. P0 Crash Windows

P0 是 Core Dogfood 前必须用故障注入证明的核心不原子窗口。P0-1～P0-4 在 Gate 3
验证，P0-5 在 Gate 4 验证；结果出来后不得修改预期分类。

P0 窗口、P0-4a/P0-4b 拆分和期望分类只以 `EVAL-PROTOCOL.md` 第 4.2 节为规范来源。
本文件只解释实现机制：

- P0-1 在无 execution 且 workspace 未漂移时创建唯一 attempt。
- P0-2 固定命中“workspace 已修改但 terminal execution 尚未持久化”的未知副作用现场，
  必须停止并交还人工，不得借用“可安全补写”的其他现场改变结果。
- terminal execution 已存在但 step result 缺失属于 P1 独立 case，证据一致时才允许补写。
- P0-3 复用已绑定的 step result，不重复外部动作。
- P0-4a 从权威非终态 `state.json` 恢复下一步路由；P0-4b 只补齐终态 checkpoint 或索引。
- P0-5 按仍有效的 decision id 消费一次，不能创建第二个批准。

P0 通过标准：

- `duplicate_worker_starts = 0`；
- `duplicate_external_effects = 0`；
- `silent_workspace_drift = 0`；
- 未知 execution / workspace / step result 组合统一进入 `needs_human`；
- graph checkpoint 不能把失败、待人工或旧批准覆盖成 success。

---

## 8. Reconciliation 判定表

恢复入口必须先读取 `state.json`，再读取 execution records、step results、workspace snapshot 和 checkpoint
manifest。判定顺序应先排除危险现场，再处理可复用现场。

| 现场 | 判定 | 允许动作 | 禁止动作 |
|---|---|---|---|
| `state.json` 无法解析 | `corrupt_state` | 写 recovery report 后停止 | 不猜测当前步骤 |
| state.run_id 与 run 目录不一致 | `state_identity_mismatch` | 停止并报错 | 不串用证据 |
| 没有 execution，workspace 与输入基线一致 | `safe_execute` | 分配 attempt 并执行 | 不复用不存在的结果 |
| 没有 execution，workspace 已漂移 | `needs_human / workspace_drift_without_execution` | 生成报告 | 不启动 worker 覆盖现场 |
| execution 为 `starting`，且可证明 child 未启动 | `safe_execute` | 按预注册策略处理同一 attempt | 不分配第二个外部 attempt |
| execution 为 active，owned / child PID 仍存活 | `active_process_alive` | 要求先 stop 或等待 | 不 recover，不并发接管 |
| execution 为 active，但 PID 消失且 termination 已确认 | `stale_active_execution` | 可按 crash window reconciliation | 不直接 success |
| `termination_unconfirmed=true` | `needs_human / termination_unconfirmed` | 人工检查 process tree | 不因 PID 消失而自动恢复 |
| terminal execution 与 step result 存在，hash 和 workspace 一致 | `safe_reuse_step_result` | 返回已记录结果或补推进 state/checkpoint | 不重复外部动作 |
| terminal execution 存在但 step result 缺失，workspace 与 artifact hash 一致 | `safe_repair_step_result` | 补写 step result | 不重复外部动作 |
| terminal execution 存在但 step result 缺失，workspace 或 artifact 无法一致解释 | `needs_human / incomplete_step_result_evidence` | 停止并报告 | 不补写，不重跑外部动作 |
| step result 存在但 execution hash 不一致 | `needs_human / execution_binding_mismatch` | 停止并报告 | 不选择较新的 hash 猜测 |
| step result 存在但 workspace 已漂移 | `needs_human / stale_step_result` | 停止并报告 | 不用旧 evidence 继续 verification |
| artifact 存在但 execution / step result 不完整 | `needs_human / incomplete_evidence_chain` | 停止并报告 | 不把 artifact 当作完成证明 |
| read-only 节点输入身份一致 | `safe_recompute_read_only` | 重算并校验输出 | 不读取新漂移作为旧输入 |
| verification 只完成部分命令 | `partial_verification` | 按每条 command execution 判定 | 未知副作用命令不得盲目重跑 |
| reviewer attempt claim 已写、`runner-started.json` 尚未写，且 attempt 锁可重新取得 | `safe_reuse_claim_only` | 沿用同一 attempt 进入 Runner | 不分配第二个 attempt |
| `runner-started.json` 已写但 execution 缺失 | `needs_human / reviewer_external_unknown` | owner 存活时等待；owner 消失时发布 `termination_unconfirmed` 证据 | 不重复调用 provider |
| reviewer terminal execution 已写、`runner-result.json` 缺失，owner / child 仍存活 | `active_process_alive` | 等待 metadata 提交 | 不发布 result，不并发接管 |
| reviewer terminal execution 已写、`runner-result.json` 缺失，owner / child 均消失且 claim、marker、execution、output 全部可信 | `reviewer_result_metadata_lost` | 原子补写 recovery metadata，并发布 `provider_error / needs_human` | 不恢复 success，不重复调用 provider |
| reviewer provider 调用终态未知 | `needs_human / reviewer_external_unknown` | 交还人工或按预注册策略处理 | 不重复调用 provider |
| final artifact 已写但 graph 终态未推进 | `terminal_recovery` | 校验全链路后补推进终态 | 不跳过失败的 verification / risk |
| checkpoint 与 state 业务状态冲突 | `checkpoint_state_conflict` | 以 state 为准并报告 | 不用 checkpoint 覆盖 state |
| decision checkpoint 存在但 ledger 缺失 | `needs_human / missing_decision_ledger` | 停止并报告 | 不伪造批准 |

### 8.1 Checkpoint 预读与封存连续性

恢复不能只证明 SQLite 文件 inode 未变化，还必须证明 `graph.get_state()` 返回的图状态与随后
封存进 manifest 的数据库内容属于同一完整快照。当前恢复顺序固定为：

```text
打开并规范化 SQLite
  -> capture opened_data
  -> graph.get_state()
  -> capture observed_data
  -> 关闭连接
  -> capture stable_data
  -> 按 stable_data 封存 manifest
```

`opened_data`、`observed_data` 和 `stable_data` 都包含主库及全部 SQLite 事务 sidecar 的文件名、
大小、SHA-256 和文件身份。任一阶段出现主库原位改写、sidecar 集合变化或内容 hash 漂移，
恢复都会标记 checkpoint drift 并停止，不能把与预读 Graph State 不同的数据库重新封存为可信。

### 8.2 已发布终态的撤销事务

当 checkpoint 失信而 `state.json` 已发布 `success` 或 `failed` 时，恢复必须按可重入阶段执行：

```text
先原子持久化 needs_human
  -> 写失败 eval
  -> 归档并失效当前交付报告
  -> 写 run_terminal_revoked 完成事件
  -> 刷新最终 eval
```

诊断写入、报告归档或进程在任一中间阶段失败，都不能让权威业务状态回到旧终态。重试必须补齐
未完成阶段；只有 eval 和当前交付报告全部失效后，才允许写一次完成事件。

---

## 9. 终止未确认与 PID 复用边界

现有执行控制已经区分 `termination_unconfirmed`，并且 recover 只在无存活执行主体时接管。LangGraph
实验不得弱化这条边界。

### 9.1 终止未确认

以下情况必须视为自动恢复不安全：

- stop / timeout 后无法确认 owned process tree 已退出；
- Windows `taskkill /T` 失败或无法证明后代进程退出；
- 父进程退出，但 child 或孙进程可能仍在写 workspace；
- `execution.json` 标记 terminal，但 child PID 仍可见；
- terminal owner PID 仍可见，且没有可信 Step Result 完整绑定 execution / attempt；
- 进程检查失败或权限不足导致无法确认存活状态。

处理方式：

- 写入或保留 `termination_unconfirmed=true`；
- recovery 返回 `needs_human / termination_unconfirmed`；
- 不启动同一 step 的新 attempt；
- 不补写 step result 为 completed；
- 人工需要检查 `execution.json`、系统进程和 workspace 后决定放弃、清理或新建 run。

### 9.2 PID 复用

仅凭 PID 不足以证明原进程仍存在或已经退出。恢复判断必须绑定：

- PID；
- 进程创建时间或平台可获取的等价身份；
- owner / child 关系；
- command hash；
- run_id、step_id、attempt_id；
- sandbox 和 cwd；
- execution started_at / heartbeat / deadline。

如果平台无法可靠取得进程创建时间，且 PID 当前存活但身份无法确认，必须按“可能仍是原进程”处理，
拒绝自动 recovery。宁可 fail-closed，也不能因为 PID 可能复用而并发写同一 workspace。

本轮不把“可信 Step Result 已提交”解释为 PID 身份证明。它只是说明该 worker execution 的
不可变业务提交已经完成；如果 Graph operation lock 仍被原执行持有、child 仍存活、Step Result
缺失或任何 identity 不一致，recovery 仍必须拒绝。平台级进程创建时间、Job/process-group
identity 与 detached descendant 观测仍是后续运行时增强边界，不得通过放宽当前 fail-closed
规则替代。

---

## 10. Fail-Closed 语义

Fail-closed 是本契约的默认安全姿态：证据缺失、冲突、过期、被篡改、无法读取或无法归因时，
系统停在安全状态，并让人工接管。

### 10.1 统一终态

自动恢复无法证明安全时，业务状态应进入或保持：

```text
status = needs_human
current_step = recovered | recovery_blocked | reconciliation_failed
```

同时写入：

- `recovery-report.md`；
- `trace.jsonl` 中的 recovery / reconciliation 事件；
- 必要时记录判定码，例如 `ambiguous_external_side_effect`、`stale_step_result`、
  `execution_binding_mismatch`。

### 10.2 禁止降级

以下行为被明确禁止：

- 因 checkpoint 显示节点已过而跳过 execution / workspace 校验；
- 因 artifact 文件存在而推断 worker 成功；
- 因 heartbeat / deadline 过期而忽略仍存活 PID；
- 因 PID 消失而忽略 `termination_unconfirmed=true`；
- 因 reviewer approve 而覆盖 verification failed；
- 因人工 approve 而覆盖 policy / workspace / evidence 漂移；
- 因 fake runner 通过而宣称真实 runner crash recovery 通过。

---

## 11. Control Root 与 Fixture 隔离

第一轮 LangGraph 恢复实验必须使用项目内独立目标 repo，避免 Vega runtime 控制面污染目标
workspace fingerprint。

固定布局：

```text
Vega repo:
  <vega-repo>/

Vega control root:
  <vega-repo>/runs/<run_id>/

target fixture repo:
  <vega-repo>/.tmp/langgraph-fixtures/<case>/repo/

pytest basetemp:
  <vega-repo>/.tmp/pytest/runs/<name>/

pytest cache:
  <vega-repo>/.tmp/pytest/cache/
```

隔离规则：

- control root 与 target fixture repo 的解析后绝对路径必须互不包含；
- fixture 必须是独立 Git repo，有自己的 `.git`；
- fixture、basetemp 和验证中间产物只能放在 `.tmp/`；
- Vega 正式 run evidence 只能放在 `runs/<run_id>/`；
- 不写仓库父目录、用户级目录、系统临时目录或其他项目；
- 不使用 symlink、junction、hardlink 或 reparse point 绕过边界；
- 不把 `.env`、API key、Authorization header、Cookie 等凭证写入 fixture、trace、report、snapshot
  或 step result；
- 第一轮不实现 self-dogfood control root 排除机制，不默认写外置 Graph control root。

路径校验失败时，不得创建 fixture、不得启动 runner、不得写 checkpoint。

---

## 12. Gate 0 退出检查清单

Gate 0 只有在以下内容全部冻结后才能进入 Gate 1：

- replay class 列表和每类恢复动作已固定；
- attempt identity 必填字段已固定；
- step result schema 已固定；
- 外部节点和 decision 的写入顺序已固定；
- P0 crash windows 的预期动作已固定；
- reconciliation 判定表已固定；
- `termination_unconfirmed` 与 PID 复用边界已固定；
- fail-closed 终态和禁止降级规则已固定；
- control root / fixture 隔离路径已固定；
- 独立 reviewer 没有未关闭 Blocker / High。

如果后续实现发现本契约无法满足，需要先修改本文件并重新复审，不能在测试失败后临时改变预期分类。

---

## 13. 一句话原则

```text
LangGraph 只管理图游标。
Vega 管理业务状态与证据。
execution.json 记录外部进程事实。
step result 绑定节点解释与输出证据。
workspace reconciliation 决定能否安全继续。
无法证明安全时，停止并交还人工。
```
