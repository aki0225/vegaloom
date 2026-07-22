# Gate 5 并行隔离 Reviewer 复审

> 复审结论：`pass / Blocker=0 / High=0 / Medium=0`
>
> 日期：2026-07-18（星期六）
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 实现基线 HEAD：`private-gate-5-fanout-implementation-redacted`

---

## 1. 结论

本轮先后使用最高模型独立复审 Reviewer/Compatibility、checkpoint/recovery、owner PID、
Finish 发布协议和 Step Result identity。

上一轮复审记录为：

```text
Blocker = 0
High = 0
Medium = 3
```

后续复审发现并关闭 owner PID 误拒绝、依赖门禁假绿和 Step Result 自洽错绑风险。最终结论：

```text
Gate 5 review = pass
Blocker = 0
High = 0
Medium = 0
```

真实 provider 调用仍为 `0`。修复验证完成后的复审即使通过，也只证明确定性安全、真实只读
Runner adapter 和恢复合同，不证明真实模型质量或 topology 收益。

## 2. 上一轮 Reviewer High 处理记录

### 2.1 Compatibility legacy reader 不回溯 Gate 5 来源

原风险允许把 `provider_error / needs_human` child state/verdict 篡改为
`success / approve`。

上一轮修复使 freshness、iteration 和 eval 共用同一 validator，重新读取：

```text
snapshot
-> plan
-> result pointer
-> result
-> execution
-> process output
-> aggregate
-> derived legacy verdict / runner status / state status
```

任一 hash、identity、result 集合或派生结论不一致均 fail-closed。

### 2.2 Terminal execution 已写、metadata 丢失时永久 active

上一轮修复后：

- metadata 存在时读取并校验；
- metadata 缺失时校验 claim、runner-started、execution 和 output；
- owner/child 存活时保持 active；
- owner/child 均消失时原子发布 recovery metadata；
- 结果固定为 `provider_error / needs_human`，不重复调用 provider。

### 2.3 伪造 `source=runner` metadata 恢复 success

已发布 result/pointer 现在先作为权威 content-addressed 结果复用，并重新校验 execution 与
当前 Runner identity。

若只有 execution + metadata 而没有 result：

- `source=runner, status=success` 不能跨进程恢复 approve；
- 只能降级为 `provider_error / needs_human`；
- timeout、stop 和 failed execution 仍按 execution 事实解释。

### 2.4 删除 Compatibility 标记降级为普通 Review

类型识别不再只依赖 child 自声明字段：

- child state/context/acceptance 任一标记；
- 声明 source run 中的规范 aggregate；
- 父 iteration 的外部 `reflect_run`。

存在 Gate 5 aggregate 但缺少 binding 时返回 `parallel_review_binding_invalid`，不能返回
普通 Review。

### 2.5 Direct Review Goal 重定向 source

`GoalCheckpointRef` 新增：

```text
review_source_run
review_kind
review_binding_sha256
```

Goal complete/revalidate 以 attachment 为外部锚点。source、kind 或 binding 变化均拒绝。
历史 review attachment 缺少外部绑定时 fail-closed，要求重新 attach。

## 3. 上一轮 Checkpoint High 处理记录

### 3.1 初始 manifest 后的校验失败没有撤销旧终态

program resume 和 decision resume 现在统一捕获预读、`get_state()`、stable snapshot 和 seal
阶段的 `GraphCheckpointValidationError`。

任何阶段失败都先持久化：

```text
status = needs_human
current_step = graph_recovery_needs_human
```

再写诊断、失败 eval、交付报告失效和撤销事件。

### 3.2 首个 post-open 锚点仅比较 inode

恢复首锚现在：

- 拒绝 WAL；
- 绑定主库和 sidecar layout；
- 对 `checkpoints` 和 `writes` 全部规范化行计算逻辑 SHA-256；
- 允许 SQLite 内容等价的维护页变化；
- 不允许 Graph 逻辑内容、文件集合或 post-read SHA 漂移。

### 3.3 Terminal recovery 第二次打开未绑定 sealed identity

第二次打开现在显式传入刚生成的 `expected_trusted_state`，并再次执行：

- file layout continuity；
- store content digest continuity；
- `get_state()` 前后文件 SHA continuity。

## 4. 最终验证记录

以下是 2026-07-18（星期六）最终修复后的明确计数：

```text
Reviewer adapter 分片：36 passed
Checkpoint resume 分片：20 passed
Crash-window 分片：31 passed
Parallel Review resume：6 passed
HITL interrupt / resume：5 passed
Decision binding：15 passed
Step Result identity binding：7 passed
Finish artifact integrity：22 passed
Redaction：32 passed
Goal finish binding：3 passed
LangGraph dependency gate：4 passed
```

上一轮静态检查记录：

```text
compileall
Ruff
git diff --check
```

任何超过 60 秒的执行均不计通过；最终数字来自重新拆分后的 node 级结果。

## 5. Gate 5.1 关闭项

- seal 后替换另一份自洽 checkpoint 的 runtime 攻击 fixture 已通过；
- report/archive/eval/finish summary 使用原子发布，archive create-once；
- finish summary 作为提交标记绑定 report SHA-256，Goal 消费者 fail-closed；
- `run_terminal_state_revoked` 与 `run_terminal_revoked` 分离撤销事实和完成事实；
- active execution 的 live owner/child 一律拒绝；terminal live owner 只有可信 Step Result
  完整绑定 execution 与 attempt 时可继续 HITL；
- Step Result 绑定文件名、attempt、execution 和全部稳定 identity；
- `--require-langgraph` 会实际导入 Gate 所需模块。

## 6. 当前决定

```text
Gate 5 = pass
Gate 5.5 = not started, not authorized
real provider calls = 0
default product behavior = linear + single reviewer
```

Gate 5.5 下一步只能冻结预注册合同；在预注册完成前不得调用真实 provider。
