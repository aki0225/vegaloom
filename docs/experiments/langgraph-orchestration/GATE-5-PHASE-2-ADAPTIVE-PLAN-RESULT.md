# Gate 5 Phase 2：自适应 ReviewPlan

> 阶段状态：`pass`
>
> Gate 5：`in progress`
>
> Gate 5.5：`not started`
>
> 日期：`2026-07-17（星期五）`
>
> 时区：`Asia/Shanghai`
>
> 实现起点：`private-gate-5-phase-1-result-redacted`
>
> 真实 provider 调用：`0`

---

## 1. 阶段结论

Phase 1 证明了固定三路输入下的 schema、identity、reducer 和 deterministic aggregator，
但没有证明“三路”是合理的产品默认值。

Phase 2 将该假设修正为：

```text
三个角色 = 可选 reviewer pool
ReviewPlan = 本次实际需要执行的角色集合
adaptive = 候选默认 topology
single = 单 reviewer 对照 topology
fixed_three = 压力测试和收益评估对照 topology
```

因此当前状态是：

```text
固定三路默认假设 = rejected
固定三路实验 topology = retained
自适应 ReviewPlan 合同 = pass
可变 N 路 LangGraph fan-out = not implemented
Gate 5 = not passed
```

## 2. 为什么修改固定三路

固定三路存在工程测试价值：

- 三路产生 `3!` 种完成顺序，适合验证 reducer determinism；
- 能验证跨角色 finding 去重；
- 能验证多个独立只读 execution 的隔离边界。

但这些只能说明它是一个有价值的压力测试 topology，不能说明每次真实运行都应该支付三次
provider 成本。

Vega 的 aggregator 不采用多数投票，Reviewer 也不能覆盖 deterministic verification。
因此“奇数三个可以打破平票”不构成本项目的架构依据。

## 3. 新合同

### 3.1 Reviewer Pool

当前 pool 保留：

```text
correctness_reviewer
verification_adequacy_reviewer
security_design_reviewer
```

保留现有角色名称是为了控制本阶段变量。是否需要把 security 和 design 进一步拆开，必须等待
真实 finding 数据，而不是现在继续增加角色。

### 3.2 ReviewRoutingContext

确定性路由只读取：

```text
run_id
iteration
evidence_snapshot_sha256
verification_status
verification_failed_count
risk
changed_files
gate_reason_codes
```

它不读取模型自然语言，也不增加 LLM Router。

### 3.3 ReviewPlan

`ParallelReviewPlan` 内容寻址并绑定：

```text
schema_version
policy_version
topology
run_id
iteration
evidence_snapshot_sha256
required_roles
role_reasons
max_parallelism
```

任一字段变化，`review-plan-<sha256>` identity 必须变化。

Reviewer result、供后续 Graph State 接线使用的窄 result ref，以及最终 aggregate 均增加
`review_plan_id`。其他 plan、旧 plan 或计划外 reviewer 的结果不能进入有效聚合。

## 4. 自适应规则

当前 `adaptive-review-v1` 规则是：

| 机器事实 | required roles |
|---|---|
| 低风险普通变更 | correctness |
| 测试路径变化、缺少测试或 verification 未解决 | correctness + verification adequacy |
| 风险路径、新依赖、较大变更或设计影响 | correctness + security/design |
| 需要多维交叉检查的高风险变更 | 三路 |

确定性 blocker 不会仅因为 risk=`high` 就自动扩展成三路。例如：

```text
no_diff
diff_check_failed
verification failed
project_config_invalid
risk_evaluation_failed
```

这些问题已经有机器结论。额外启动三个模型不会增加能够支持成功的可信证据。
verification failed 或 failed_count>0 时同样不扩展 specialist；先回到 worker / verification
修复路径。

## 5. Aggregator 修改

Aggregator 不再要求全局固定三个角色，而是要求：

```text
ReviewPlan.required_roles
中的每个角色
各有且只有一个
与当前 plan、run、iteration、snapshot 一致的可信结果
```

以下情况 fail-closed：

- required reviewer 缺失；
- required reviewer 重复；
- reviewer 使用其他 plan；
- 计划外 reviewer 被注入结果集；
- snapshot、run 或 iteration 不一致；
- result identity 冲突；
- timeout、provider error 或未知 execution 终态。

verification、evidence、risk 和 HITL 的原有硬规则保持不变。

## 6. 自动化验证

### 6.1 自适应合同与 Graph State 回归

```text
tests/experimental/langgraph_engine/test_parallel_review_contract.py
tests/experimental/langgraph_engine/test_graph_state_contract.py

56 passed in 0.65s
```

其中 `test_parallel_review_contract.py` 共 39 个测试，新增覆盖：

- 低风险单 reviewer；
- 测试路径触发 verification reviewer；
- gate risk reason 触发 security/design reviewer；
- 高风险触发三路交叉检查；
- deterministic blocker 不扩张 reviewer 数量；
- verification failed 不扩张 reviewer 数量；
- `single` / `fixed_three` / `adaptive` 显式 topology；
- ReviewPlan content identity；
- parallelism 不得超过 required roles；
- 一路、两路、三路聚合；
- 计划外 reviewer 拒绝；
- 其他 plan 的结果拒绝；
- result ref 绑定 plan identity。

### 6.2 Gate 4.5 Core Dogfood 回归

```text
tests/experimental/langgraph_engine/test_core_dogfood_harness.py

48 passed in 13.62s
```

Gate 4.5 的单 reviewer 历史合同保持不变。

## 7. 没有被证明的内容

本阶段没有证明：

- 多 Reviewer 比单 Reviewer 发现更多真实问题；
- 自适应路由比固定三路质量更高；
- 三个 prompt 能产生真正独立的模型判断；
- 真实 provider 下的成本、延迟和稳定性；
- 可变 N 路 LangGraph fan-out 已经实现；
- Reviewer result artifact 已完成原子持久化和读取复核。

这些问题不能通过 fake runner 或 schema 单测回答。

## 8. 下一步

> 后续状态（2026-07-17）：下列 topology 无关基础设施已在
> [`GATE-5-PHASE-3-ARTIFACT-FANOUT-RESULT.md`](GATE-5-PHASE-3-ARTIFACT-FANOUT-RESULT.md)
> 完成并通过确定性测试。Phase 2 的历史结论不反向改写；真实隔离 Reviewer adapter
> 仍未实现。

下一阶段只实现 topology 无关的基础设施：

```text
ReviewPlan artifact persistence
  -> result artifact persistence
  -> plan / result / narrow ref hash validation
  -> Graph State 版本化接入
  -> 可变 N 路 fake fan-out
  -> deterministic aggregate node
```

真实 provider 阶段必须使用同一批带 ground truth 的案例比较：

```text
single
fixed_three
adaptive
```

只有真实边际收益和成本证据能够决定最终默认 topology。

候选评估方法见
[`GATE-5-TOPOLOGY-EVAL-CANDIDATE.md`](GATE-5-TOPOLOGY-EVAL-CANDIDATE.md)。
