# Gate 3 Checkpoint 与恢复握手复审

> 复审结论：`pass`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> 实现基线 HEAD：`private-gate-2-implementation-redacted`

---

## 1. 结论

本轮复审发现并关闭了一个 High 风险，未发现剩余 Blocker / High。

Gate 3 最终状态：

```text
gate-3-pass
```

允许进入 Gate 4，但继续保留以下边界：

- 自动 crash recovery 只覆盖第一轮 worker；
- 第二轮及以后 fail-closed；
- Graph control root 与目标 Git 仓库必须互不包含；
- P1 recovery matrix 不作为 Gate 3 已完成能力对外宣称。

## 2. 复审发现

### High：生成器重建会重复业务 trace，并可能覆盖既有 artifact

恢复时需要从头重建 Python generator，直至与 checkpoint 的 next node 对齐。原实现只静默了
`state.json`，但生成器 yield 之间仍会执行：

- `brief_finished`；
- `worker_prompt_measured`；
- `worker_started`；
- `worker_finished`；
- `workspace_check_finished`；
- prompt、metrics、worker output 等 artifact 写入。

P0-3 复现中，恢复后曾出现：

```text
brief_finished = 2
worker_started = 2
worker_finished = 2
```

worker 外部进程没有重复启动，但业务 trace 会把一次执行错误表示成两次，且恢复重建会再次
覆盖已有 artifact。这违反“恢复读取旧证据，而不是重新生成旧证据”的要求。

## 3. 修复

### 3.1 静默恢复 TraceWriter

新增 `RecoveryTraceWriter`：

- driver 与 checkpoint 对齐前，业务 `.write()` 为 no-op；
- 对齐完成后才启用真实业务 trace；
- `graph_reconciliation_finished`、`graph_terminal_recovered` 等恢复事件仍由独立
  `TraceWriter` 正常记录。

同时把同一 `TraceWriter` 显式传入 auto generator，删除内部重新创建 writer 的隐式行为，
确保恢复静默边界不会被子生成器绕过。

### 3.2 恢复 artifact 写保护

新增：

```text
src/vega/loop_recovery_replay.py
```

生成器对齐期间：

- 目标必须位于当前 run 目录；
- 路径不得包含 symlink、junction 或 reparse point；
- artifact 必须已经存在；
- 新内容必须与既有内容完全一致；
- 内容一致时只复用，不再次写盘；
- 缺失或不一致时 fail-closed，不覆盖旧证据。

prompt metrics 和 risk gate artifact 已接入同一受保护写入路径。

### 3.3 已推进节点只做证据重放

对于：

- P0-3：worker Step Result 已写，checkpoint 仍指向 worker；
- P0-4a：`state.json` 已进入 verify，checkpoint 仍指向 workspace reconcile；

driver 在静默阶段使用已校验证据推进。LangGraph 随后只补过期 checkpoint 节点，不再次调用
worker 或 workspace reconcile handler。

这避免了：

- 重复外部动作；
- 重复只读 handler 的 artifact 写入；
- 重复业务 trace；
- checkpoint 把权威业务状态向后回退。

## 4. 新增回归

`tests/experimental/langgraph_engine/test_crash_windows.py` 新增或加强：

1. P0-3 恢复前后关键业务 event 计数保持不变；
2. P0-4a 恢复前后关键业务 event 计数保持不变；
3. 恢复前已存在的稳定业务 artifact hash 不变化；
4. 人工篡改 `loop-plan.md` 后，恢复必须拒绝覆盖；
5. artifact 不一致时 worker 启动数和外部写次数仍保持 `1`。

## 5. 复审验证

所有 pytest 分片均使用独立 basetemp 和 cache。

```text
P0 crash windows：
3 passed in 21.78s
4 passed in 27.03s
1 passed in 13.28s

Checkpoint / Step Result / Graph State contracts：
26 passed in 1.33s

Engine / handler：
12 passed in 48.50s

Legacy compatibility：
34 passed in 2.42s

Graph 终态失败与撤销语义：
3 passed in 54.54s
```

一次整文件 crash 测试和两次 semantic parity 组合测试超过 60 秒，均只记录为 timeout，
随后改用完整 node id 分片并取得明确 passed 结果；timeout 未计入通过。

静态检查：

```text
python -m compileall src
ruff check 受影响文件
git diff --check
```

结果均通过。

## 6. 最终审批

Gate 3 的核心安全结论保持成立：

| 指标 | 结果 |
|---|---:|
| Duplicate Worker Starts | `0` |
| Duplicate External Effects | `0` |
| Duplicate Recovery Business Events | `0` |
| Recovery Artifact Overwrite | `0` |
| Unsafe Resume | `0` |
| Silent Workspace Drift | `0` |

审批结果：

```text
Gate 3 = pass
Gate 4 = approved to start
```
