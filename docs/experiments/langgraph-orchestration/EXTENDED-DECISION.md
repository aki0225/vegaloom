# Vega LangGraph Extended Decision

> 决策：`reuse-independent-of-langgraph`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 6：`pass / real handoff`

---

## 1. 最终决策

Gate 6 保留为隔离实验，不进入默认产品路径：

```text
extended decision = reuse-independent-of-langgraph
default product engine = linear
LangGraph = experimental
Goal / Checkpoint / Handoff = engine-neutral reusable contract
real provider evidence = available / R4
```

这个决策表示 Goal / Checkpoint / Handoff 合同已经通过一次完整真实的双 session
provider 接力，并且不需要 LangGraph graph cursor 才能成立。它不是把 LangGraph
提升为默认引擎，也不是把 handoff 自动扩大到所有产品路径。

---

## 2. 为什么不是 `retain-as-langgraph-extension`

`retain-as-langgraph-extension` 会把 handoff 的价值绑定到 LangGraph 实现本身。R4 的
真实证据反而显示，Goal / Checkpoint / Handoff 的安全合同可以由 engine-neutral 的
workspace、policy、artifact binding 和 context compiler 承担：

- preflight、Session A、Session B 三次真实调用全部成功；
- provider sessions 使用数为 `3`，没有重试或隐藏额外调用；
- handoff artifact 和 consumer context 均有版本与 SHA-256；
- source chat、accepted memory 和真实项目数据均未发送。

因此没有证据支持把该合同的复用范围限制为 LangGraph extension。

---

## 3. 为什么选择 `reuse-independent-of-langgraph`

R4 完成了预注册的真实条件：固定 provider、模型、认证、sandbox、预算和 clean
checkout 下，Session B 使用 Session A 的 handoff/context 完成第二阶段，并通过
workspace drift、checkpoint evidence、artifact identity 和终态检查。

这足以支持一个窄结论：

> Goal / Checkpoint / Handoff 可以作为独立于 LangGraph 的跨 session 合同复用。

这不是 LangGraph 的产品采纳结论。LangGraph 仍然只负责实验编排和 checkpoint 游标，
默认 linear Runtime 仍然是权威产品路径。

---

## 4. 为什么不是 `reject-handoff`

完全拒绝 handoff 也不符合现有证据：

- workspace、policy、checkpoint evidence 和 artifact identity 已纳入绑定；
- drift、tamper、session/epoch mismatch 和 context budget 边界均有确定性覆盖；
- blocked handoff 可以在人工修复后从新 version/session 恢复；
- fake dogfood 和 R4 真实 dogfood 都证明安全链路可重复执行。

因此保留代码和 engine-neutral 实验合同，但不把 LangGraph 或 handoff 自动扩大为默认
产品承诺。

---

## 5. 当前可复述的工程结论

截至 Gate 6：

1. 我们证明了跨 session handoff 的本地状态机和 fail-closed 安全合同，而不是只证明了
   “能把一段聊天复制给下一个 worker”。
2. 我们证明了 baseline、annotated tag、consumed tag、独立 clean checkout 和一次性
   provider 预算可以形成可审计执行边界。
3. R4 真实证明了 fresh Session A / Session B 可以通过 handoff/context 完成接力，且
   不发送 source chat、accepted memory 或真实项目数据。
4. 我们没有把该结果升级成 LangGraph 默认引擎采纳；真实证据支持的是
   `reuse-independent-of-langgraph`，不是“LangGraph 已经证明有不可替代收益”。

---

## 6. 下一步

Gate 6 已取得完整真实终态，不需要在当前 consumed baseline 上继续执行。后续若要把
handoff 进入产品路径，应另建一个独立的产品化 gate，至少重新确认：

1. preflight 前后完整比较 ignored 文件和 Git metadata；
2. 使用远端原子 claim 替代仅本地 consumed tag 锁；
3. 收窄 verification cache allowlist，并拒绝链接和异常 payload；
4. 机械校验 `core.eol`、旧 consumed tag 和冻结参数；
5. linear Runtime 如何调用同一 engine-neutral handoff contract；
6. accepted memory、source chat 和真实项目数据的边界是否继续保持；
7. 产品化后的恢复、漂移和 artifact binding 是否仍然 fail-closed；
8. LangGraph 是否有独立于 handoff 合同的增量价值。

在上述新证据出现前，默认产品引擎仍为 `linear`，LangGraph 仍为 `experimental`，不得
重用 `gate-6-r4-consumed-v1`。

---

## 7. 证据索引

- [`GATE-6-PRE-REGISTRATION.md`](GATE-6-PRE-REGISTRATION.md)
- [`GATE-6-READINESS.md`](GATE-6-READINESS.md)
- [`GATE-6-RESULT.md`](GATE-6-RESULT.md)
- [`GATE-6-R4-RESULT.md`](GATE-6-R4-RESULT.md)
- `.local-validation/gate-6/gate6-fake-contract-007/summary.json`
- `.tmp/gate-6-r4/clean-checkout-gate6-r4-real-v1-lf/.local-validation/gate-6-r4/gate6-r4-real-v1/summary.json`
