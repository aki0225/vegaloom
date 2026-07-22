# Gate 5 Phase 1：Reviewer 结果合同与确定性聚合

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
> 实现起点：`private-gate-5-entry-redacted`
>
> 真实 provider 调用：`0`

> 后续状态：本阶段最初用于压力测试的“固定三路”假设，已在 Phase 2 复审后降级为
> `fixed_three` 对照 topology；候选默认改为确定性 `adaptive ReviewPlan`。本文件保留
> Phase 1 当时的历史证据，不再代表当前 Reviewer 调度合同。

---

## 1. 阶段结论

Gate 5 第一阶段已经完成以下确定性基础：

```text
共享 evidence snapshot identity
稳定 finding identity
单 reviewer result identity
Graph State 可用的窄 result ref
result / result ref 幂等 reducer
checkpoint 反序列化字典兼容
完成顺序无关的 deterministic aggregator
verification / evidence / HITL 硬规则
自由文本与 canary 不进入 aggregate
```

本阶段没有实现三路 reviewer subgraph，也没有调用真实 provider，因此：

```text
Gate 5 Phase 1 = pass
Gate 5 = in progress
Gate 5.5 = not started
```

## 2. 为什么先做合同而不是直接 fan-out

比较过两种方案：

### 方案 A：先定义结构化合同和聚合规则

优点：

- 可以在没有模型随机性和并发变量时验证 reducer；
- 先固定 evidence、attempt、finding 和 artifact identity；
- 能证明完成顺序不影响结果；
- provider timeout、旧 evidence 和 verification failure 可以先 fail-closed；
- 后续 subgraph 只负责生成符合合同的独立结果。

### 方案 B：直接把 `dispatch_review` 改成三路并发

问题：

- 当前单 reviewer `review-verdict.json` 不足以表达三路独立结果；
- finding 没有稳定去重 identity；
- Graph State 还没有可信 result ref；
- 并发、provider、schema 和聚合错误会同时进入调试；
- 容易用模型投票覆盖 deterministic verification。

因此本阶段选择方案 A。它不是缩小 Gate 5，而是先关闭 fan-out 依赖的状态与证据歧义。

## 3. 新增实现

新增：

```text
src/vega/parallel_review.py
tests/experimental/langgraph_engine/test_parallel_review_contract.py
```

### 3.1 Evidence Snapshot

`ReviewEvidenceSnapshot` 绑定：

```text
run_id
iteration
workspace_fingerprint
policy_snapshot_sha256
verification_result_sha256
risk_result_sha256
acceptance_evidence_manifest_sha256
```

`evidence_snapshot_sha256` 从上述字段的 canonical JSON 计算。任一输入变化，snapshot identity
必须变化；snapshot 字段与其 identity 不一致时拒绝解析。

本阶段只冻结并校验“证据声明的身份”，不会读取这些 hash 所引用的实际 artifact。实际文件
内容与声明 hash 是否一致，必须由下一阶段的 artifact persistence / integrity validation
负责，不能把本阶段结果表述成已证明 artifact 未被篡改。

### 3.2 Finding Identity

每条 finding 使用：

```text
category
+ rule_id
+ normalized_path
+ normalized_location
+ evidence_snapshot_sha256
```

计算 `finding-<sha256>`。

路径必须是安全的仓库相对路径，拒绝：

```text
../secret
<outside-workspace>/auth.json
/etc/passwd
```

同一问题被不同 reviewer 重复发现时使用同一 identity；不同 evidence snapshot 下的同一位置
不会被错误合并。

### 3.3 Reviewer Result

`ParallelReviewResult` 绑定：

```text
run_id
iteration
reviewer_role
attempt_id
evidence_snapshot_sha256
execution_ref
execution_sha256
status
verdict
findings
```

非 `completed` execution：

- 只能输出 `needs_human`；
- findings 必须为空；
- 不能利用 timeout 或 provider error 的残缺输出形成 approve。

### 3.4 窄 Result Ref

`ParallelReviewResultRef` 只保留：

```text
result_id
reviewer_role
evidence_snapshot_sha256
attempt_id
artifact_ref
artifact_sha256
```

它不包含：

- summary；
- finding 自由文本；
- checked items；
- process output；
- reviewer 私有 canary。

后续 Graph State 只能保存该类窄引用，不能保存完整 reviewer 输出。

### 3.5 Reducer

结果和窄引用分别提供幂等 reducer：

```text
相同 identity + 相同内容
  -> 幂等复用

相同 identity + 不同内容
  -> fail-closed

map key != result_id
  -> fail-closed
```

返回结果按 identity 排序，避免并发完成顺序进入持久化状态。

LangGraph checkpoint 恢复后，reducer 输入可能是普通 JSON object，而不再是 Pydantic
实例。Phase 1 因此同时验证：

```text
Pydantic model 输入
  -> 正常合并

checkpoint 反序列化 dict 输入
  -> 重新执行 schema 与 identity 校验后合并

相同 identity + 不同 dict 内容
  -> fail-closed
```

## 4. Deterministic Aggregator

Aggregator 固定要求三个角色各有且只有一个可信结果：

```text
correctness_reviewer
verification_adequacy_reviewer
security_design_reviewer
```

规则优先级：

```text
verification failed
  -> request_changes

verification unresolved
  -> needs_human

evidence stale / truncated / hash mismatch
  -> needs_human

reviewer 缺失、重复或 snapshot identity 不一致
  -> needs_human

reviewer timeout / provider error / unknown termination
  -> needs_human

存在 blocker / major finding
  -> request_changes

high risk 且缺少有效 human approval
  -> needs_human

reviewer 明确 needs_human
  -> needs_human

reviewer 明确 request_changes
  -> request_changes

否则
  -> approve
```

三个 reviewer 全部 `approve` 也不能覆盖 verification failure。

Aggregator 输出：

- 按 `finding_id` 去重；
- severity 采用最高等级；
- reviewer 来源按固定角色顺序记录；
- 不复制 title、evidence、recommendation 或 summary；
- 使用 `aggregate_sha256` 绑定 canonical 聚合内容。

## 5. Canary 与信息边界

测试将不同 reviewer 私有 canary 放入：

- result summary；
- finding title；
- finding evidence。

聚合结果只保留稳定 finding identity、结构化位置、severity 和 reviewer roles。序列化 aggregate
中没有出现任何 `PRIVATE_CANARY`。

该结果只证明**结构化 reducer 不传播自由文本**。它尚未证明真实 reviewer prompt、execution
output 或并发 subgraph 之间没有上下文泄漏；这些属于后续 Gate 5 隔离测试。

## 6. 自动化验证

### 6.1 Gate 5 Phase 1 与 Graph State 合同

```text
tests/experimental/langgraph_engine/test_parallel_review_contract.py
tests/experimental/langgraph_engine/test_graph_state_contract.py

43 passed in 0.89s
```

其中 Gate 5 新增 26 个测试，覆盖：

- snapshot 字段与 identity 一致性校验；
- finding identity 与路径边界；
- stale finding 拒绝；
- 非终态 reviewer 约束；
- reviewer 自由文本出站脱敏；
- result / result ref reducer；
- checkpoint 反序列化 dict reducer；
- reducer conflict；
- 3! 完成顺序排列；
- finding 去重与 canary 隔离；
- verification failure override；
- stale / truncated / hash mismatch evidence；
- 缺失或重复 reviewer；
- timeout / provider error / unknown termination；
- stale reviewer snapshot；
- high-risk approval；
- reviewer request changes。

### 6.2 Gate 4.5 历史合同回归

```text
tests/experimental/langgraph_engine/test_core_dogfood_harness.py

48 passed in 15.97s
```

现有 Core Dogfood 继续保持“单 reviewer execution 恰好为 1”的历史合同，没有为了 Gate 5
修改旧通过标准。

### 6.3 静态检查

```text
python -m compileall -q src
ruff check src tests

passed
```

## 7. 已在合同层关闭的风险

在调用方提供可信 evidence freshness / truncation / hash-validity 机器事实的前提下，本阶段
已经关闭以下聚合歧义：

- 相同 finding 被三路重复计数；
- reducer 受完成顺序影响；
- 相同 identity 被不同内容静默覆盖；
- 旧 evidence finding 与当前 snapshot 合并；
- timeout/provider error 形成伪 approve；
- 三个 approve 覆盖 verification failure；
- high risk 缺少有效批准仍 approve；
- reviewer 自由文本进入 aggregate。

本阶段尚未实现实际 artifact 的写入、读取与 hash 复核，因此“旧证据、截断证据和文件 hash
不匹配如何被 Runtime 检出”仍属于下一阶段，不能仅凭 aggregator context 的布尔字段宣称
端到端风险已经关闭。

## 8. 尚未完成

Gate 5 仍缺少：

1. result artifact 的安全原子写入与内容哈希复核；
2. Graph State 对 Gate 5 窄 result ref 的版本化接入；
3. 三路 isolated reviewer subgraph；
4. 三路独立 read-only execution；
5. 同一 evidence package 的真实 prompt 注入；
6. provider timeout/error 的 Runtime 接线；
7. aggregate 到现有 loop `review-verdict.json` 的兼容边界；
8. prompt、checkpoint、parent state 和 reviewer 之间的完整 canary 扫描；
9. Gate 5 独立复审；
10. Gate 5.5 真实 Reviewer Dogfood。

## 9. 下一步

下一阶段固定顺序：

```text
result artifact persistence
  -> narrow ref integrity validation
  -> Gate 5 Graph State contract
  -> fake isolated reviewer fan-out
  -> deterministic aggregate node
  -> failure / canary matrix
  -> Gate 5 review
```

在上述确定性实现通过前，不调用真实 provider，不运行 Gate 5.5。
