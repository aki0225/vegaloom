# LangGraph 编排实验摘要

> 实验日期：`2026-07-15` 至 `2026-07-20`
>
> 最终分类：`partial`
>
> 主线基线：`private-release-validation-baseline-redacted`
>
> 源实验冻结标识：已脱敏；公开归档不保留私有 SHA 或 tag

## 核心决策

本实验不支持用 LangGraph 全面替换 Vega 的线性 Runtime，也不否定 LangGraph 的全部价值。
主线继续保持：

```text
default engine = linear
default reviewer topology = single
```

LangGraph 只保留为可选的实验性 recovery/HITL 控制面。Goal、Checkpoint 和 Handoff
被视为引擎无关能力，不绑定为 LangGraph 专属功能。

## 已证明的内容

### Crash recovery

当 worker 已经修改 Git workspace，但图状态尚未完成业务提交时，单独依赖 checkpoint
不足以判断是否可以继续。实验通过以下证据进行对账：

- 外部进程 execution；
- 非幂等 attempt 和 Step Result；
- workspace before/after fingerprint；
- 业务 `state.json`；
- checkpoint manifest 与 SQLite；
- verification、risk 和 reviewer evidence。

在冻结的 crash + HITL dogfood 中，恢复过程复用了已完成的 worker 结果，没有重复启动
worker，也没有重复外部副作用。

### 结构化 HITL

人工决定被绑定到当前 workspace、policy、verification 和 risk evidence，并通过追加式
decision ledger 记录。批准只能消费一次，旧决定或证据漂移时必须拒绝恢复。

### 跨 Session Handoff

独立会话可以只依赖版本化 handoff、workspace 和已验证证据继续任务，不需要继承完整聊天。
该合同可以由 Linear 或 LangGraph 调用，因此后续若进入主线，应作为引擎无关能力单独评估。

## 未证明或不推广的内容

### 顺序编排

Linear 与顺序 LangGraph 可以复用相同业务 Handler 并达到一致终态，但没有证据表明
LangGraph 在顺序任务质量、速度或维护性上优于现有 Runtime。

### 多 Reviewer

82 次真实 Provider session 的冻结结果为：

| Topology | True Positive | False Positive | Token 倍率 |
|---|---:|---:|---:|
| `single` | 0 | 9 | `1.0000` |
| `adaptive` | 0 | 25 | `1.7896` |
| `fixed_three` | 0 | 34 | `2.9043` |

多 Reviewer 没有增加可复现的有效发现，却增加了误报、调用、token 和延迟，因此主线不推广
adaptive 或 fixed-three fan-out。

### Gate 7 大任务

Gate 7 R6 中 API key、Provider/model identity、远端执行权控制和 CP01 worker 均正常工作，
但证据预算超过预注册上限：

```text
transcript = 89,908 / 65,536 bytes
tokens = 53,469 / 45,000
Gate 7A = failed
Gate 7C = not started
```

因此不能声称 LangGraph 已在真实大任务中证明比 Linear 更可靠、更便宜或更高效。该轮真正
证明的是 fail-closed：模型进程成功并修改 workspace，不等于 checkpoint 业务成功。

## 主线集成边界

本摘要进入主线不代表实验代码已经产品化。后续如继续集成，应拆成四个独立 PR：

1. Goal checkpoint 基础模型、状态机和状态查询；
2. Goal/Handoff 引擎无关合同、CLI 和最小跨 Session 测试；
3. `langgraph` optional extra 下的 checkpoint/recovery 基础；
4. 结构化 HITL decision ledger、resume 和一次性消费。

Goal/Handoff 应先在不依赖 LangGraph 的条件下证明可用。Recovery/HITL 必须保持显式 opt-in，
并且每个 PR 都要独立证明默认 Linear 路径没有行为变化。实验历史中存在同时修改 recovery
hardening 与 parallel-review 的提交，因此后续只能按文件和职责移植，不能整提交
cherry-pick。

以下内容不进入默认主线：

- LangGraph 作为默认顺序引擎；
- adaptive/fixed-three Reviewer fan-out；
- Gate 7 大任务 harness、Provider 专用配置和已消费实验身份；
- FastAPI、SSE 或 Web 控制面。

## 公开脱敏说明

公开归档保留实验行为、测试、指标和结论，但不会逐字公开本机环境身份：

- 本机绝对路径改为语义占位符，测试所需路径改为明显的 `C:/fixtures/...`；
- Provider/profile、model、域名和 loopback 端口改为合成标识与保留域名；
- 会暴露跨仓冻结终点或临时公开修复链路的 commit SHA 改为语义标签；
- 原始 `runs/`、`.local-validation/`、`.tmp/` 和认证材料不进入 Git。

因此，公开配置用于复核代码合同与相对行为，不应解释为原实验环境的字面复现参数。

## 完整实验资料

完整实验源码、预注册合同和已提交的证据文档冻结在
`experiment/langgraph-comparison`。例如：

```powershell
git show experiment/langgraph-comparison:docs/experiments/langgraph-orchestration/DECISION.md
git show experiment/langgraph-comparison:docs/experiments/langgraph-orchestration/GATE-7-R6-RESULT.md
```

本地 `.local-validation/` 和 `runs/` 原始 artifact 不随 Git 分发，不得把提交中的结论摘要
解释成在任意新机器上已经重放了相同实验。
