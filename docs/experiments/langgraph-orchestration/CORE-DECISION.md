# Vega LangGraph 核心实验决策

> 决策：`partial`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 4.5 R6：`pass`
>
> Gate 5：`pass`
>
> Gate 5.1：`pass`
>
> Gate 5.5：`completed / single wins`

---

## 1. 最终决策

```text
core decision = partial
default product engine = linear
experimental LangGraph core = retain
default review topology = single
adaptive / fixed_three = do not promote
Gate 6 = allowed only as an isolated extension experiment
FastAPI / SSE = not started
```

本轮不选择 `accept`，因为真实 Gate 5.5 没有证明 Reviewer fan-out 的业务收益；也不选择
`reject`，因为 checkpoint、interrupt、恢复、HITL、artifact binding 和单 Reviewer
执行链已经形成充分的确定性与真实运行证据。

## 2. 为什么不是 Accept

`accept` 要求 LangGraph 核心编排、恢复、HITL 和 Reviewer fan-out 整体值得继续。
Gate 5.5 的真实结果否定了最后一部分：

- `single`、`adaptive`、`fixed_three` 的 true positive 均为 0；
- adaptive 相对 single 没有可复现 unique true blocker/major；
- fixed-three 相对 adaptive 没有可复现 unique true blocker/major；
- adaptive 使用 2 倍调用、1.7896 倍 token；
- fixed-three 使用 3 倍调用、2.9043 倍 token；
- 多 Reviewer 增加 false positive 和 clean false-major；
- 最终冻结结论为 `single wins`。

因此不能把“并发、隔离和恢复机制成功运行”包装成“多 Reviewer 有业务价值”。

## 3. 为什么不是 Reject

`reject` 意味着停止扩大 LangGraph 范围，不继续 Goal/Handoff。现有证据尚不支持完全否定：

- Gate 4.5 R6 的真实 core dogfood 已通过；
- Gate 5 的 ReviewPlan、evidence snapshot、隔离 Reviewer、确定性 aggregate 和恢复合同已
  通过；
- Gate 5.1 的 checkpoint seal、SQLite 逻辑摘要、terminal recovery、atomic publish 和
  identity binding 已通过；
- Gate 5.5 的 82 次 provider session 全部成功，证明真实 adapter、预算和 artifact 链可
  稳定运行；
- safety metrics 全部为 0；
- 默认 linear Runtime 从未被提前替换。

这些证据足以保留并继续评估 LangGraph 的状态恢复与长任务交接价值。

## 4. 保留范围

继续保留并维护：

- persisted LangGraph checkpoint；
- `interrupt` / resume / recover；
- HITL ledger 与 resume binding；
- execution、result、aggregate 和 artifact identity；
- stop latch 与 owned process tree；
- checkpoint 预读、seal 和第二次打开连续性；
- Compatibility legacy reader；
- 单 Reviewer 的真实只读 adapter；
- `single` topology 作为默认 Reviewer 路径；
- Gate 5 的多 Reviewer 实验实现和测试，作为负面实验证据与可复现基线。

保留实验代码不等于提升为产品默认。默认产品路径继续是：

```text
engine = linear
review topology = single
```

## 5. 不推广范围

当前不推广：

- `fixed_three` 默认 fan-out；
- `adaptive` 默认 fan-out；
- LLM Reviewer router；
- 以 finding 数量或 verdict 投票代替 ground-truth 收益；
- 在当前 Gate 5.5 上追加 provider 重跑；
- 事后扩张 aliases 或 fuzzy matching 改判 0 TP；
- 将 LangGraph 切换为默认产品引擎；
- FastAPI / SSE 控制面。

如未来重新评估 Reviewer topology，必须创建新的预注册轮次、数据集 commitment、tag、
预算和结果文档，不能改写 Gate 5.5。

## 6. Gate 6 边界

允许进入 Gate 6，但必须作为独立扩展实验，只回答 Goal / Checkpoint / Handoff 是否有增量
价值：

- Session B 能否只依赖 handoff artifact 接续 Session A；
- checkpoint context compiler 能否稳定恢复约束、已验证事实和当前任务；
- workspace 事实是否在恢复时重新读取；
- handoff 漂移能否 fail-closed；
- context budget 和 split checkpoint 是否可验证；
- 不自动写 accepted memory；
- Reviewer 默认保持 single。

Gate 6 不得：

- 用多 Reviewer 数量增加来弥补 Gate 5.5 的 0 TP；
- 重新消费 Gate 5.5 provider 预算；
- 把 Goal/Handoff 与 FastAPI/SSE 混成一个 Gate；
- 在 Extended Decision 前切换默认引擎。

## 7. 产品与实验边界

当前产品决策：

```text
linear Runtime remains authoritative
LangGraph remains experimental
single reviewer remains default
```

当前实验决策：

```text
checkpoint / interrupt / recovery = evidence-backed
single Reviewer adapter = evidence-backed
multi-Reviewer quality benefit = not demonstrated
Goal / Handoff = still unproven
```

完成 Gate 6 后再输出 `EXTENDED-DECISION.md`。只有 Core Decision 与 Extended Decision
均形成可审计证据后，才允许最终 `DECISION.md` 判断 LangGraph 是否进入产品路径。

## 8. 可用于面试的工程结论

本实验最重要的结果不是“做出了三个 Reviewer”，而是：

1. 先用 deterministic contracts 证明并发、隔离、恢复和 artifact correctness；
2. 再冻结真实数据集、ground truth、预算和 winner 规则；
3. 真实运行得到负面结果：多 Reviewer 增加成本和误报，没有增加预注册 true positive；
4. 因此没有把复杂架构强行包装成收益，而是做出 `partial` 决策，保留有证据的恢复能力，
   拒绝无证据的默认 fan-out。

这说明项目具备可证伪实验、成本约束、负面结果接受和产品边界控制，而不只是 LangGraph API
集成。

## 9. 决策证据

- [`GATE-4.5-R6-DOGFOOD-RESULT.md`](GATE-4.5-R6-DOGFOOD-RESULT.md)
- [`GATE-5-RESULT.md`](GATE-5-RESULT.md)
- [`GATE-5-REVIEW.md`](GATE-5-REVIEW.md)
- [`GATE-5.1-HARDENING-RESULT.md`](GATE-5.1-HARDENING-RESULT.md)
- [`GATE-5.5-PRE-REGISTRATION.md`](GATE-5.5-PRE-REGISTRATION.md)
- [`GATE-5.5-RESULT.md`](GATE-5.5-RESULT.md)
