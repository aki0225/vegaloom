# Gate 5 并行隔离 Reviewer 结果

> 状态：`pass`
>
> 日期：2026-07-18（星期六）
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 实现基线 HEAD：`private-gate-5-fanout-implementation-redacted`
>
> 复审：`passed / Blocker=0 / High=0 / Medium=0`，见
> [`GATE-5-REVIEW.md`](GATE-5-REVIEW.md)
>
> 真实 provider 调用：`0`

---

## 1. 结论

Gate 5 已形成并行隔离 Reviewer 的确定性执行合同、真实只读 Runner adapter、崩溃恢复和
Compatibility legacy reader 实现。2026-07-18（星期六）的最终独立复审确认
`Blocker=0 / High=0 / Medium=0`，本文件是 Gate 5 的冻结结果。

当前候选证据覆盖：

- `single`、`fixed_three` 和 `adaptive` 使用同一 ReviewPlan / evidence snapshot；
- 一至三路 reviewer 只读执行，不能修改目标 workspace；
- prompt、public evidence、role prompt、execution、process output、result 和 aggregate 形成
  可重放证据链；
- reducer 和 aggregate 与 reviewer 完成顺序无关；
- verification failure、provider error、timeout、stop、parse error、workspace drift 和旧
  evidence 都不能聚合为伪 approve；
- claim-only、runner-started、active execution、terminal execution 和 metadata 丢失窗口
  都不会重复调用 provider；
- 已发布 content-addressed result 可以复用；只有 metadata 而没有 result 时，跨进程恢复
  不得恢复 success；
- Compatibility legacy state/verdict 会回溯 Gate 5 plan、pointer、result、execution、
  process output、aggregate 和 snapshot；
- Goal attachment 额外绑定 review source、review kind 和 binding digest，历史缺少绑定的
  review attachment fail-closed；
- checkpoint 预读与 seal 通过文件 layout、主库/sidecar SHA 和 SQLite 全行逻辑摘要绑定；
- checkpoint 失信时先持久化 `needs_human`，再撤销旧终态和交付报告。
- `vega stop --run <run>` 建立永久 run 级 latch，向该 run 的全部 active execution
  广播 stop request，并阻止同一 run 的后续 execution 启动外部进程。

因此：

```text
Gate 5 implementation = complete
Gate 5 final review = pass
Gate 5.5 = not started, not authorized
```

## 2. 产品边界

本轮 Gate 5 实现没有改变默认产品路径：

```text
default engine = linear
default review topology = single reviewer
```

并行 Reviewer 仍是实验能力。真实 provider 调用保持 `0`，所以本 Gate 不证明：

- 真实模型 finding 质量；
- `fixed_three` 或 `adaptive` 相对单 Reviewer 的边际收益；
- token 成本、总延迟或 provider 稳定性；
- 多 Reviewer 应成为默认产品行为。

这些问题只属于 Gate 5.5。

## 3. 关键实现

### 3.1 ReviewPlan 与 Artifact

- 内容寻址的 `ReviewEvidenceSnapshot`、`ParallelReviewPlan`、result 和 aggregate；
- Graph State 只保存窄 result ref，不保存 reviewer 自由文本；
- result pointer、execution、process output 和 aggregate 全部校验 canonical path 与 SHA-256；
- 计划外角色、旧 snapshot、重复 result 或冲突 aggregate 均 fail-closed。

### 3.2 真实只读 Runner Adapter

- 每个角色使用独立 role prompt 和公共 evidence package；
- Runner 必须使用 `read-only` sandbox；
- private canary 不进入其他角色、parent state 或 aggregate；
- attempt identity 绑定 plan、role、public evidence、role prompt、policy 和 workspace；
- attempt OS 文件锁、create-once claim 和 runner-started marker 防止重复 provider 调用。

### 3.3 Recovery

```text
claim only
  -> 同一 attempt 可安全接管

runner-started + execution missing
  -> owner 存活则等待
  -> owner 消失则 termination_unconfirmed / needs_human

active execution
  -> 任一 owner/child 存活则拒绝接管
  -> 全部消失则保守收敛，不重调 provider

terminal execution + metadata missing
  -> owner/child 存活则等待
  -> 全部消失且证据可信则 provider_error / needs_human

published result
  -> 校验 execution identity 后直接复用
```

### 3.4 Compatibility 与 Goal

Compatibility run 写出单一 `parallel_review_source` binding。旧 reader 重新读取源 run 的：

- evidence snapshot；
- ReviewPlan；
- result pointer；
- result；
- execution；
- process output；
- aggregate。

随后重新派生 legacy verdict、runner status、state status，并与 child state/verdict 比较。
freshness、iteration integrity 和 review eval 共用同一 validator。

Goal review attachment 另外封存：

```text
review_source_run
review_kind
review_binding_sha256
```

后续 complete/revalidate 必须与 attachment 一致。旧 review attachment 缺少这些字段时要求
重新 attach，不能静默按普通 Review 继续。

### 3.5 Checkpoint 加固

Gate 5 与 Gate 5.1 已完成以下 checkpoint 恢复加固：

- resume 首锚拒绝 WAL，避免打开后把新字节当可信基线；
- 文件 layout 绑定名称、大小、device 和 inode；
- `checkpoints` / `writes` 全行规范化逻辑摘要绑定真实 Graph 内容；
- post-open、`get_state()` 后和 close 后分别复核逻辑摘要与文件 SHA；
- terminal recovery 第二次打开绑定刚 seal 的 `TrustedCheckpointState`；
- 完整攻击 fixture 会在 seal 后替换为另一份同 `run_id`、可独立通过 manifest 校验的
  checkpoint，并确认第二次 checkpointer 在交给 Graph 前 fail-closed；
- 任一预读、稳定化或 seal 失败都进入统一 `needs_human` 补偿路径。

## 4. 最终验证记录

以下记录均来自 2026-07-18（星期六）的明确 `passed` 计数。超过 60 秒的整文件或并行分片
全部不计通过，均改用独立 `.tmp/pytest/runs/<name>` 和 cache 的小 node 分片重跑。

```text
Parallel Review runner adapter：36 passed
Parallel Review resume：6 passed
Checkpoint resume：20 passed
Crash windows：31 passed
HITL interrupt / resume：5 passed
Decision binding：15 passed
Step Result identity binding：7 passed
Finish artifact integrity：22 passed
Redaction：32 passed
Goal finish binding：3 passed
LangGraph dependency gate：4 passed
```

静态检查：

```text
python -m compileall -q src：通过
ruff check src tests：通过
git diff --check：通过，仅有既有 LF/CRLF 提示
```

## 5. Gate 5.1 Hardening

上一轮记录的三个 Medium 已全部关闭：

1. seal 后自洽 checkpoint 替换攻击已有完整 runtime fixture；
2. report、archive、eval 和 finish summary 使用原子发布，archive 使用 create-once；summary
   作为提交标记并绑定 report SHA-256，Goal 消费者会拒绝半发布现场；
3. `run_terminal_state_revoked` 记录权威 state 已撤销，
   `run_terminal_revoked` 只在 eval、state 和交付报告全部完成后作为最后完成事件。

同时关闭提交前发现的 owner PID、LangGraph 依赖假绿和 Step Result 错绑风险。详细记录见
[`GATE-5.1-HARDENING-RESULT.md`](GATE-5.1-HARDENING-RESULT.md)。

## 6. 下一步

Gate 5 已正式 `pass`。下一步只冻结 Gate 5.5 预注册，不立即调用真实 provider：

- provider 和模型；
- 任务/ground-truth 数据集；
- `single`、`fixed_three`、`adaptive` 对照；
- 数据出站边界；
- token、延迟和调用次数预算；
- stop 条件与失败口径。

在预注册完成前，真实 provider 调用继续保持 `0`。
