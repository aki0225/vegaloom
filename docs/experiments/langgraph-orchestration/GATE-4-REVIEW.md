# Gate 4 Human-in-the-loop 复审

> 复审结论：`pass`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> 实现基线 HEAD：`private-gate-2-implementation-redacted`

---

## 1. 结论

本轮复审发现并关闭一个 High 和一个 Medium，未发现剩余 Blocker / High。

Gate 4 最终状态：

```text
gate-4-pass
```

允许准备 Gate 4.5 Core Dogfood，但 deterministic Gate 4 通过不等于真实 runner 已验证。

## 2. High：缺失 Graph State 时可能继续展示 success 或 HITL 状态

Gate 4 为了向 `status` 增加 pending decision 指引，曾把 Graph State 读取改成：

```text
graph-state.json 存在
  -> 校验

graph-state.json 不存在
  -> graph_state = None
```

这会破坏原有 fail-closed 语义：如果业务 `state.json` 声明 LangGraph success，但
`graph/graph-state.json` 缺失，CLI status 可能继续展示 success，而不是拒绝消费不可信终态。

HITL 也存在同类风险：业务 state 声明等待 `human_decision`，但缺少可信 pending Graph
State 时，不应展示可直接照抄的 resume 流程。

### 修复

`run_status_payload()` 现在明确区分：

```text
LangGraph success
或
running + human_decision
```

这两类状态必须具备可信 Graph State：

- 文件缺失：拒绝；
- JSON 或 schema 不合法：拒绝；
- run identity、pending identity 或 hash 不可信：拒绝。

回归重新覆盖了：

- missing；
- malformed；
- identity mismatch；
- review_results list；
- boolean schema version；
- duplicate key。

## 3. Medium：ReviewRuntime 的 consumption ref 可能记录错误 artifact identity

原实现已经会通过 `validate_consumed_approval()` 校验当前 iteration 存在真实 approved
consumption，因此错误 ref 不能直接绕过人工批准。

但 `_evaluate_review_human_approval()` 只检查调用方传入的 `consumption_ref` 文件存在，再独立
查找当前 iteration 的 consumption。这会产生证据来源歧义：

```text
传入 ref = 另一个 pending 的 consumption 文件
实际校验 = 当前 iteration 的 consumption
```

最终 approval 仍需要真实批准，不构成直接安全绕过，但 `review-context.json` 可能记录一个
并非本次批准来源的 artifact ref，破坏可审计性。

### 修复

`validate_consumed_approval()` 新增精确 ref 校验：

```text
consumption_ref
==
graph/decision-consumptions/<current_pending_id>.json
```

别名文件、其他 pending identity 或路径不一致均返回 invalid，review 保持
`risk_gate_needs_human`。

## 4. 复审重点

### 4.1 Decision 顺序

确认实现顺序为：

```text
pending artifact
-> interrupt checkpoint
-> decisions.jsonl
-> binding validation
-> consumption artifact
-> state transition
-> checkpoint progress
```

P0-5 和 consumption 后崩溃均有故障注入覆盖。

### 4.2 真相源

未新增第二套人工决定真相源：

- `decisions.jsonl` 仍是人工决定权威；
- pending artifact 只描述等待决定的证据 binding；
- consumption artifact 只描述 graph 如何一次性解释 ledger decision；
- Graph State 只保存 pending identity；
- `state.json` 仍拥有业务状态。

### 4.3 Verification 优先级

确认：

- `verification_failed_count > 0` 时 approved decision 被拒绝；
- reviewer approve 不能覆盖 verification failure；
- risk gate evidence 只有在 consumed approval 可信时才允许高风险 review 进入成功链。

### 4.4 恢复入口分离

确认：

- crash reconciliation 使用 `vega recover`；
- HITL decision consumption 使用 `vega resume --decision-id`；
- 普通 recover 遇到 `running / human_decision` 时拒绝；
- CLI 不接受原始批准文本作为 resume value。

## 5. 复审验证

Gate 4 专项：

```text
Interrupt / CLI / recover guard：5 passed
Decision binding / consumption / drift：10 passed
```

回归：

```text
Graph State contract：17 passed
Handler boundary：6 passed
Engine selection：7 passed
Legacy compatibility：34 passed
Semantic parity：15 个完整 node id 全部 passed
```

安全语义：

```text
Standalone high-risk review 无 consumption：needs_human
可信 consumption：review success
evidence 漂移后的 consumption：needs_human
consumption ref alias：needs_human
verification failed + approval：拒绝
```

静态检查：

```text
python -m compileall -q src
ruff check src tests
git diff --check
```

结果全部通过。

## 6. 剩余风险

以下内容不阻塞 Gate 4，但必须保持显式：

1. driver crash / HITL 恢复只支持第一轮；
2. 本地单进程假设下没有验证多进程并发消费；
3. ledger 当前没有 revoke 语义；
4. `edit_scope` 尚未实现；
5. standalone 高风险 review 仍可运行只读 reviewer，但其结论不会进入成功链；
6. 真实 runner 的交互成本、provider failure 和人工体验尚未验证；
7. 多 reviewer fan-out 属于 Gate 5。

## 7. 最终审批

| 指标 | 结果 |
|---|---:|
| Interrupt Consistency | `100%` |
| Duplicate Worker Starts | `0` |
| Duplicate External Effects | `0` |
| Duplicate Approval / Consumption | `0` |
| Invalid Approval over Verification Failure | `0` |
| Unsafe Resume | `0` |
| Silent Workspace / Policy / Evidence Drift | `0` |

审批结果：

```text
Gate 4 = pass
Gate 4.5 = approved to prepare
```
