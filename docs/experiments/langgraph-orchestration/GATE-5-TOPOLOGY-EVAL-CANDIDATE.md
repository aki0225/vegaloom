# Gate 5 Reviewer Topology 收益评估候选合同

> 状态：`superseded`
>
> 日期：`2026-07-17（星期五）`
>
> 时区：`Asia/Shanghai`
>
> 真实 provider 调用：`0`
>
> 取代日期：`2026-07-18（星期六）`
>
> 正式合同：[`GATE-5.5-PRE-REGISTRATION.md`](GATE-5.5-PRE-REGISTRATION.md)
>
> 说明：本文仅保留候选方法的历史记录；认证、数据集、预算、匹配、复跑和结论规则均以
> 正式预注册合同为准

---

## 1. 要回答的问题

本评估只回答：

```text
single
fixed_three
adaptive
```

哪一种 topology 在相同模型、相同 evidence 和相同 ground truth 下提供更好的质量、成本与延迟
取舍。

不得使用以下替代指标：

- finding 总数量；
- Reviewer 赞成票数量；
- 输出文本长度；
- 三路是否成功并发；
- 演示是否看起来更复杂。

## 2. 案例集

第一轮固定 12 个互相独立的 fixture：

| 类别 | 数量 | 目标 |
|---|---:|---|
| clean | 3 | 不应产生 blocker / major |
| correctness | 3 | 已知逻辑、边界或需求语义缺陷 |
| verification adequacy | 3 | 测试通过但不足以证明需求 |
| security / design | 3 | 已知信任边界、依赖或外部副作用缺陷 |

每个 fixture 必须提供不进入 reviewer prompt 的 ground-truth manifest：

```text
case_id
workspace fixture hash
evidence snapshot hash
expected finding identities
expected severity range
allowed alternative locations
forbidden false-blocker conditions
```

Reviewer 不得读取 ground-truth manifest。

## 3. 固定变量

三种 topology 必须保持：

```text
同一 provider
同一 model version
同一公共 evidence package
同一角色 prompt version
同一输出 schema
同一 artifact 校验规则
同一超时和最大输出预算
无跨 case memory
无跨 topology 输出共享
```

只有 Reviewer 计划不同。

## 4. 指标

### 4.1 质量

- finding-level precision；
- finding-level recall；
- blocker / major recall；
- clean case false blocker；
- clean case false major；
- 相对 single 的 unique true positive；
- 重复 finding 比例；
- 最终 verdict 是否与 ground truth 一致。

### 4.2 稳定性

- 同一 case 重跑后的 finding identity 一致率；
- verdict flip 次数；
- timeout / provider error 次数；
- parse error 次数。

只对首次结果不一致、位于决策边界或发生 provider 异常的 case 重跑一次，避免为了获得理想
结果无限重复。

### 4.3 成本与延迟

- provider call 数；
- input / output token；
- wall-clock latency；
- p50 / p95 latency；
- 每个 true positive 的 token 成本；
- 每个新增 blocker / major 的边际成本。

第一轮 provider call 硬预算不超过 90。超过预算时停止，不得删掉失败 case 后继续宣称成功。

## 5. 决策规则

### 保留 single 为默认

满足任一条件：

- adaptive 和 fixed_three 均没有增加 true blocker / major；
- 多 Reviewer 只增加重复 finding；
- 多 Reviewer 增加 false blocker；
- 多 Reviewer 的有效收益无法稳定复现。

### 选择 adaptive 为候选默认

必须同时满足：

- blocker / major recall 不低于 single；
- 相对 single 至少增加一个可复现的 true blocker / major，或者在相同质量下显著低于
  fixed_three 成本；
- 不增加 clean case false blocker；
- 总 provider 成本低于 fixed_three；
- 计划路由与实际新增 finding 的专业角色一致。

### fixed_three 只能在以下条件下成为候选默认

必须同时满足：

- 相对 adaptive 至少增加一个可复现的 true blocker / major；
- precision 不低于 adaptive；
- 不增加 clean case false blocker；
- 额外成本和延迟被完整报告；
- 项目 owner 明确接受该成本。

否则 `fixed_three` 只保留为压力测试 topology。

## 6. 有效结论

以下结论都允许：

```text
single wins
adaptive wins
fixed_three wins
no stable winner
```

负面结果仍是有效实验产出。不得因为简历需要 Multi-Agent 关键词而修改 ground truth、删除失败
case 或降低 false blocker 标准。

## 7. 当前状态

```text
candidate protocol = written
ground-truth fixtures = not created
provider adapter = not connected
real evaluation = not run
Gate 5.5 = not started
```
