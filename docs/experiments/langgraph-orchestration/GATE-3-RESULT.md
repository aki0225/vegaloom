# Gate 3 Checkpoint 与恢复握手结果

> 状态：`gate-3-pass`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> 实现基线 HEAD：`private-gate-2-implementation-redacted`
>
> 复审：`pass`，见 [`GATE-3-REVIEW.md`](GATE-3-REVIEW.md)

---

## 1. 结论

Gate 3 的核心实现和预注册 P0 故障窗口已经完成。

当前证据支持以下结论：

- LangGraph checkpoint 只拥有图执行游标，不覆盖 `state.json` 业务终态；
- worker 外部副作用通过 attempt identity、`execution.json`、Step Result 和 workspace
  fingerprint 对账；
- P0-1～P0-4b 均未出现重复 worker 启动、重复外部写入或静默 workspace 漂移；
- Python `finally` 完全不执行的子进程 `os._exit` 场景仍可恢复；
- checkpoint、Step Result、execution 和 artifact identity 可以形成可验证证据链；
- 不确定的外部副作用现场统一停到 `needs_human`；
- Linear Runtime、旧 run 兼容和顺序语义等价没有被破坏。

因此，本轮实现最终判定为：

```text
Gate 3 = pass
```

复审曾发现恢复重建 generator 会重复业务 trace、覆盖既有 artifact 的 High 风险。当前已通过
静默恢复 writer、内容一致性写保护和已推进节点证据重放关闭；详情见
[`GATE-3-REVIEW.md`](GATE-3-REVIEW.md)。

## 2. 本轮实现范围

### 2.1 SQLite checkpoint

新增：

```text
src/vega/loop_graph_checkpoint.py
```

实现：

- `langgraph-checkpoint-sqlite>=3.1,<3.2` 可选依赖；
- 固定 `thread_id=run_id`；
- 顶层 `checkpoint_ns=""`；
- checkpoint 路径、run identity、namespace 和 schema 校验；
- SQLite `quick_check`；
- checkpoint 数量、最新 checkpoint id 和 pending writes 数量绑定；
- checkpoint 主文件 SHA-256 和大小绑定；
- WAL 字段兼容校验；
- symlink、junction、reparse point 和 run 目录越界拒绝；
- manifest 原子替换。

LangGraph 的 SQLite saver 默认启用 WAL。Gate 3 当前是单进程、单写者 Runtime，为避免
checkpoint 主库与 WAL 形成第二个跨文件非原子封存问题，打开数据库后会：

```text
wal_checkpoint(TRUNCATE)
-> journal_mode=DELETE
```

该选择不是把 SQLite 当成业务事务，而是缩小 checkpoint 控制面的文件集合。每次
`put` / `put_writes` 成功提交后立即刷新 manifest，不再依赖整次 `graph.invoke`
退出时的 `finally`。

如果进程恰好在 SQLite 提交与 manifest 刷新之间硬退出，恢复会因为文件 hash 不一致而
fail-closed，不会把未封存数据库解释为可信 checkpoint。

### 2.2 Graph Run Config

新增：

```text
graph/run-config.json
```

固定：

- `run_id`
- `automation_mode`
- `worker_name`
- `reviewer_name`
- `verify`
- `timeout_seconds`

恢复 Runtime 必须使用相同 `timeout_seconds`。不同超时配置会改变外部 attempt 的输入身份，
因此当前实现直接拒绝恢复，而不是静默采用调用方新值。

### 2.3 Attempt Identity

worker 启动前写入：

```text
iterations/<n>/executions/worker/attempt.json
```

身份包括：

- `step_id`
- `attempt_id`
- `idempotency_key`
- `replay_class=external_non_replayable`
- runner identity
- base Git HEAD
- before-workspace fingerprint
- policy snapshot SHA-256
- command SHA-256
- input fingerprint

attempt manifest 不声明进程终态。PID、heartbeat、deadline、returncode、stop、timeout 和
terminal status 仍只由现有 `execution.json` 拥有。

### 2.4 Content-addressed Step Result

新增：

```text
src/vega/loop_step_result.py
step-results/worker-iteration-<n>.json
```

Step Result 负责解释：

- 当前 Graph 节点对应哪个 attempt；
- 引用了哪个 terminal `execution.json`；
- execution、workspace 和输出 artifact 的 SHA-256；
- worker 的结构化结果状态；
- 当前结果绑定哪个 before / after workspace fingerprint。

Step Result：

- content-addressed；
- append-only；
- 同 step id 不允许不同内容覆盖；
- 拒绝绝对路径、上级路径和 run 外引用；
- 拒绝敏感字段；
- 读取时重新校验 execution 和所有 output refs。

### 2.5 恢复握手

新增：

```text
src/vega/loop_graph_recovery.py
```

恢复入口按以下顺序读取事实：

```text
state.json
-> graph run config
-> checkpoint manifest / SQLite
-> workspace evidence
-> attempt manifest
-> execution.json
-> Step Result
-> 当前 workspace fingerprint
```

核心判定：

| 现场 | 动作 |
|---|---|
| 无 execution，workspace 与输入基线一致 | `safe_execute` |
| 无 execution，但 workspace 已漂移 | `needs_human` |
| execution 终态未知且可能已有副作用 | `needs_human` |
| terminal execution、Step Result、workspace 一致 | `safe_reuse_step_result` |
| `state.json` 已进入下一非终态，checkpoint 落后 | `safe_resume_from_state` |
| `state.json` 已进入终态，checkpoint 落后 | `terminal_recovery` |
| 任一 identity、hash 或 artifact 无法对齐 | `needs_human` |

恢复不会用 checkpoint 覆盖 `state.json`，也不会自动回滚 Git workspace。

### 2.6 Driver 重建

恢复时使用静默业务状态对象和静默业务 trace 重建同一份生成器程序。在 driver 与 checkpoint
next node 对齐前：

- 禁止写入权威 `state.json`；
- 禁止追加重复业务 trace；
- 既有 artifact 只允许内容完全一致的复用；
- artifact 缺失或内容不一致时 fail-closed，不得覆盖旧证据。

当前自动恢复明确只注册第一轮 worker：

```text
current_iteration == 1
```

第二轮及以后若发生 crash，会生成 `needs_human`，不会把
`worker-iteration-01` 错绑到后续 iteration。

这是显式安全边界，不是静默缺陷。多轮自动恢复需要重建前序 verification、reflect、risk 和
reviewer 的完整返回值，属于后续 P1 扩展，不阻塞 Gate 3 的第一轮 P0 恢复矩阵。

### 2.7 Graph control root 隔离

Gate 3 第一轮强制：

```text
workspace/runs
与
target Git repo
互不包含
```

原因是 workspace fingerprint 会观察 ignored 文件。若 Vega 对自身仓库运行，持续变化的
checkpoint、trace 和 artifacts 可能污染目标 workspace fingerprint。

当前实现会在创建 run 前拒绝：

- control root 位于目标仓库内部；
- 目标仓库位于 control root 内部；
- control root 与目标仓库相同。

第一轮实验必须使用项目边界内的独立 fixture Git repo。Vega self-dogfood 的精确控制面排除
仍是后续独立议题。

## 3. P0 Crash Window 结果

| 窗口 | 注入点 | 预期 | 结果 |
|---|---|---|---|
| P0-1 | execution 前崩溃 | workspace 未漂移时允许唯一 worker attempt | 通过 |
| P0-2 | workspace 已修改，execution 未终态 | 禁止重启 worker，转人工 | 通过 |
| P0-3 | Step Result 已写，state 未推进 | 复用结果，不重启 worker | 通过 |
| P0-4a | 非终态 state 已推进，checkpoint 落后 | 以 state 和证据继续 | 通过 |
| P0-4b | terminal state 已写，checkpoint 落后 | 只修复 checkpoint / Graph State | 通过 |

硬指标：

| 指标 | 结果 |
|---|---:|
| Duplicate Worker Starts | `0` |
| Duplicate External Effects | `0` |
| Unsafe Resume | `0` |
| Silent Workspace Drift | `0` |
| P0 Safe Stop Rate | `100%` |
| P0 Identity Consistency | `100%` |

## 4. 真实 hard-crash 证据

异常注入会执行 Python `finally`，不能代表进程断电。因此额外增加子进程测试：

```text
test_abrupt_process_exit_keeps_resumable_checkpoint_without_finally
```

流程：

1. 子进程启动真实 LangGraph Runtime；
2. worker 写入目标 fixture；
3. terminal execution 和 Step Result 已持久化；
4. fault hook 调用 `os._exit(86)`；
5. Python context manager 和 `finally` 完全不执行；
6. 父进程校验 checkpoint manifest；
7. 新 Runtime 执行 recover；
8. Step Result 被复用；
9. 持久化 worker start 计数仍为 `1`。

结果：

```text
worker starts before crash = 1
worker starts after recovery = 1
duplicate external effect = 0
```

## 5. Checkpoint 内容与体积

semantic parity 测试直接读取 SQLite `checkpoints` 和 `writes` blob，检查：

- 完整 task prompt canary 不存在；
- diff canary 不存在；
- worker output canary 不存在；
- verification log canary 不存在；
- reviewer 私有 canary 不存在；
- `Authorization`、`Bearer`、`api_key`、`Cookie` 等标记不存在；
- 单个序列化 blob 小于 64 KiB。

实测样本：

| 样本 | SQLite 大小 | checkpoints | writes | 最大 checkpoint blob | 最大 write blob |
|---|---:|---:|---:|---:|---:|
| 单轮 success | 102,400 B | 11 | 139 | 3,491 B | 73 B |
| 五轮 request_changes | 290,816 B | 34 | 461 | 3,310 B | 73 B |

Checkpoint 体积是记录指标，不是与 Linear 比较的通过门槛。

## 6. 验证结果

### 6.1 LangGraph 实验测试

按独立 basetemp 和 cache 分片：

```text
Checkpoint / crash / Graph State / Step Result：34 tests
Engine / handler / legacy compatibility：46 passed
Semantic parity：15 passed
```

候选实现完整验证合计为 `94 passed`；复审新增 1 个 artifact mismatch 回归后，当前 Gate 3
测试集合共 `95` 个用例。复审后的受影响范围分片结果和 timeout 处理见
[`GATE-3-REVIEW.md`](GATE-3-REVIEW.md)。

五轮 semantic parity 是不可再拆分的完整 node id，实际耗时：

```text
1 passed in 146.20s
```

该用例超过 60 秒，但不是 timeout。此前 60 秒执行只记录为 timeout，未计入通过；最终使用
完整 node id 单独执行并取得明确 passed 结果。

### 6.2 受影响主线回归

```text
success semantics：27 passed
execution control safety：12 passed
CLI recovery hardening：36 passed
runtime / runner 相关 smoke 选定用例：14 passed
```

### 6.3 静态检查

收口前执行：

```text
python -m compileall src
ruff check src tests
git diff --check
```

最终结果以本文件提交前的最后一次验证记录为准。

## 7. 与 Gate 2 的兼容性

保持不变：

- 默认 engine 仍为 `linear`；
- 未安装 LangGraph 可选依赖时，Linear 模块仍可导入和运行；
- 旧 run 缺少 `engine` 时仍按 Linear 解释；
- graph run 不允许由 Linear continue / finish 修改；
- Linear 与 LangGraph 继续共用同一份 `LoopStepProgramDriver`；
- 五轮业务语义、artifact schema、success 语义和外部副作用计数保持一致。

新增：

- `recover` 会按持久化 engine 安全分派；
- 具备 Gate 3 control artifacts 的 graph run 会显示 checkpoint recovery 指引；
- 旧版顺序 graph run 缺少 checkpoint 时明确提示不能自动恢复；
- `graph_recovery_needs_human` 会优先展示恢复报告和禁止重放提示。

## 8. 当前明确限制

### 8.1 多轮 crash 自动恢复

当前只自动恢复第一轮。第二轮及以后 fail-closed。

该限制不影响五轮正常执行的 semantic parity，但意味着：

```text
multi-round execution = supported
multi-round crash auto-resume = not yet supported
```

### 8.2 Self-dogfood

Graph control root 与目标 repo 必须互不包含。当前不能直接以 Vega 仓库自身作为 LangGraph
目标仓库。

### 8.3 P1 恢复矩阵

Gate 3 结项时尚未实现：

- terminal execution 存在但 Step Result 缺失时自动修复；
- verification command 的逐命令 checkpoint；
- reviewer provider 终态未知的恢复策略；
- HITL decision ledger；
- 第二轮及以后 driver 重建；
- SQLite 提交与 manifest 刷新之间微小窗口的自动修复。

上述现场当前统一 fail-closed，不会被解释为成功。

### 8.4 后续 Gate 的恢复加固（2026-07-18）

该部分不是对 Gate 3 历史通过标准的追溯修改，而是记录当前分支在 Gate 4 / Gate 5 期间继续
关闭的恢复风险：

- SQLite 恢复现在分别捕获 `opened_data`、`observed_data` 和 `stable_data`，用主库及全部
  transaction sidecar 的完整内容快照绑定 `graph.get_state()` 与最终 manifest；
- 同 inode 的主库原位改写、sidecar 集合变化、大小或 SHA-256 漂移均 fail-closed；
- checkpoint 失信撤销已发布 `success/failed` 时，先持久化 `needs_human`，再失效 eval 和
  交付报告，最后只写一次 `run_terminal_revoked` 完成事件；
- Gate 4 已实现 HITL decision ledger 与一次性 consumption；
- Gate 5 已实现 reviewer claim-only 接管、runner-started 未知终态收敛，以及 terminal
  execution 已写但 runner-result metadata 丢失时的保守恢复。

因此第 8.3 节中的 `reviewer provider 终态未知` 和 `HITL decision ledger` 已在后续 Gate
关闭；多轮 crash 自动恢复、逐 verification command checkpoint 等限制仍保持。

## 9. 复审结论

复审已检查：

1. `state.json` 是否始终保持业务权威；
2. Step Result 是否只解释 execution，而没有复制进程终态；
3. hard crash 测试是否真正绕过 Python `finally`；
4. SQLite rollback journal 选择是否符合单写者实验约束；
5. control root 隔离是否在所有 graph 创建入口前执行；
6. 第二轮恢复是否始终 fail-closed；
7. run status 是否不会继续输出 Gate 2 的过期指导；
8. checkpoint blob 是否没有 prompt、diff、日志、凭证或 reviewer 私有内容；
9. generator 重建是否会重复业务 trace；
10. generator 重建是否会覆盖既有 artifact。

最终未保留 Blocker / High。Gate 3 通过，但第 8 节声明的多轮恢复、self-dogfood 和 P1
recovery matrix 限制继续有效。

## 10. 下一步

当前执行顺序：

1. Gate 3 复审已完成并升级为 `gate-3-pass`；
2. 进入 Gate 4：结构化 HITL；
3. 实现 pending decision identity；
4. decision ledger 先写后 resume；
5. 验证 P0-5 decision 后 crash recovery；
6. Gate 4 通过后再进入 Core Dogfood。

Gate 4 期间不同时启动并行 Reviewer 或 Goal / Handoff 扩展。
