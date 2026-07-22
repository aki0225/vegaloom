# Vega LangGraph 编排实验最终决策

> 最终分类：`partial`
>
> 决策日期：`2026-07-20（星期一）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 默认产品引擎：`linear`
>
> 默认 Reviewer topology：`single`

---

## 1. 执行摘要

本实验不接受“LangGraph 全面替换 Vega 线性 Runtime”，也不否定 LangGraph 的全部价值。
最终决策为：

```text
final classification = partial
default product engine = linear
default reviewer topology = single
LangGraph = optional experimental recovery / HITL control plane
Goal / Checkpoint / Handoff = engine-neutral reusable experiment
adaptive / fixed_three = do not promote
FastAPI / SSE = not started
```

LangGraph 已证明的增益集中在**持久化执行游标、故障恢复对账和结构化 HITL**，而不是顺序流程
本身、默认多 Reviewer 或真实大任务效率。Vega 的业务成功语义继续由 `state.json`、Git
workspace、确定性 verification、risk gate、decision ledger 和 evidence artifacts 共同约束。

---

## 2. 九个最终问题

### 2.1 顺序编排是否值得？

**值得保留为实验实现，不值得替换默认线性 Runtime。**

Gate 2 与 Gate 4.5 R6 证明 Linear 和顺序 LangGraph 可以复用同一批 Step Handler，并在相同
低风险 fixture 上取得一致的业务终态、verification 和变更范围。这说明引擎边界是可行的，
但没有证明顺序图相对现有线性 Runtime 提高了任务质量、速度或维护性。

因此：

```text
shared business handlers = accept
sequential graph parity = accept
LangGraph as default sequential engine = reject
```

### 2.2 Checkpoint 是否真实改善恢复？

**是，但只在与外部副作用证据握手后成立。**

LangGraph checkpoint 能保存图执行位置，但不能自动回滚或解释 worker 已经写入的 Git
workspace。实验通过以下证据建立恢复握手：

- `execution.json`：外部进程事实；
- `attempt.json`：本次非幂等尝试身份；
- `step-results/<step_id>.json`：节点对 execution 与输出证据的绑定；
- workspace before/after evidence：代码事实；
- checkpoint manifest 与 SQLite seal：图控制面事实；
- reconciliation：决定安全继续、复用、修复或停止。

Gate 4.5 R6 在 `after_step_result_before_state` 故障点安全复用了 worker 结果，恢复前后
worker start count 均为 1。Checkpoint 的增益因此是“让恢复点可定位并可对账”，不是“把
workspace 变成事务”。

### 2.3 安全恢复比例与安全停止比例分别是多少？

以 Core 准入所冻结的 P0 六类窗口为口径：

| 分类 | 窗口 | 结果 |
| --- | --- | ---: |
| 允许自动继续 | P0-1、P0-3、P0-4a、P0-4b、P0-5 | `5 / 5 = 100%` |
| 必须安全停止 | P0-2 | `1 / 1 = 100%` |
| Unsafe Resume | 全部 P0 | `0` |
| Duplicate Worker Starts | 全部 P0 与 Gate 4.5 R6 | `0` |
| Duplicate External Effects | 全部 P0 与 Gate 4.5 R6 | `0` |
| Silent Workspace Drift | 全部 P0 与 Gate 4.5 R6 | `0` |

这里没有把所有后续 P1 条件分支压成一个容易误导的总百分比。P1 包含 snapshot 缺失、
termination unknown、artifact tamper、旧 reviewer evidence 等不同停止条件，应按各自
预注册分类解释。Gate 7 R6 还补充证明：即使 worker 进程成功并修改 workspace，只要
transcript/token 证据预算失败，系统仍会 fail-closed，不接受 checkpoint 或启动下一 arm。

### 2.4 HITL 是否比现有 `needs_human` 更清晰？

**是。**

普通 `needs_human` 只能表达“需要人处理”。结构化 HITL 进一步固定了：

```text
pending identity
-> bound workspace / policy / verification / risk evidence
-> append-only decision ledger
-> resume by decision_id
-> one-time consumption artifact
```

实验已覆盖批准、拒绝、旧批准失效、verification failed 不得被批准覆盖、同一 pending 不得被
第二个 decision 重复消费，以及 ledger 已写但 graph 尚未 resume 的 P0-5 窗口。它显著改善了
审计和恢复表达，但增加了 pending、decision 与 consumption 三类状态。

### 2.5 并行 Reviewer 是否有新增有效发现？

**没有。**

Gate 5 证明了隔离只读 Reviewer、可变 N 路 fan-out、checkpoint、确定性 reducer 和 artifact
binding 可以正确运行；Gate 5.5 则回答了它是否值得默认启用。

冻结数据集上的真实结果：

| Topology | TP | FP | Token 倍率 vs Single |
| --- | ---: | ---: | ---: |
| `single` | 0 | 9 | `1.0000` |
| `adaptive` | 0 | 25 | `1.7896` |
| `fixed_three` | 0 | 34 | `2.9043` |

82 次 provider session 全部成功，安全指标为 0，但多 Reviewer 没有新增可复现 true
blocker/major，只增加误报、调用、token 和延迟。因此固定三路的现实依据没有被本轮数据支持，
默认 topology 保持 `single`。

### 2.6 Handoff 是否能独立复用？

**可以，且不应绑定为 LangGraph 专属能力。**

Gate 6 R4 完成了真实双 session handoff：Session B 只使用版本化 handoff/context 接续
Session A，不继承完整聊天；workspace、policy、checkpoint evidence、artifact identity 和
context budget 均参与校验。

实际证据支持：

```text
Goal / Checkpoint / Handoff = reuse-independent-of-langgraph
```

该合同可以由 Linear 或 LangGraph 引擎调用。它仍是实验能力，尚未自动进入默认产品路径。

### 2.7 引入了多少额外状态和维护成本？

新增控制面至少包含以下状态类别：

1. Graph run config、Graph State、checkpoint manifest 与 SQLite；
2. workspace before/after evidence；
3. worker attempt 与 Step Result；
4. pending decision、decision ledger 与 consumption；
5. reviewer plan、attempt、result、aggregate 与 compatibility reader；
6. Goal checkpoint、handoff、claim/tag 与跨 session context；
7. dogfood、故障注入、数据集、预算和独立复核脚本。

以实验代码基线 `private-experiment-base-redacted` 到最终收口前的分支统计为量级参考，整个实验已涉及约 176 个变更
文件、约 7.8 万行新增；其中 `src/`、`scripts/`、`tests/` 合计 75 个文件、约 5.3 万行新增。
这些数字包含大量测试、实验脚本和证据代码，不能等同于产品代码量，但足以说明维护成本不是
理论问题。

因此最终没有为了“已经写了很多”而提升默认引擎。保留范围必须窄，且实验代码不得继续反向
扩大核心产品成功条件。

### 2.8 哪些能力合入、保留实验或不再推广？

| 分类 | 能力 |
| --- | --- |
| 产品默认保持 | Linear Runtime、single Reviewer、确定性 verification、risk gate、现有 evidence contract |
| 保留实验 | LangGraph checkpoint/recover/HITL、execution/Step Result reconciliation、单 Reviewer Graph adapter |
| 独立复用实验 | Goal / Checkpoint / Handoff |
| 保留为负面基线 | adaptive/fixed-three fan-out、Reviewer topology 数据集与评测 harness |
| 不推广 | LangGraph 默认引擎、多 Reviewer 默认 fan-out、LLM Reviewer router、以 finding 数量投票 |
| 未启动 | FastAPI/SSE 控制面 |
| 未形成结论 | Gate 7 真实大任务 Linear/LangGraph A/B |

本分支暂不物理删除多 Reviewer 或 Gate 7 实验代码，因为它们是可复现的负面结果和停止线证据；
但它们不进入默认运行路径，也不应继续无边界扩建。

### 2.9 为什么最终是 `partial`？

选择 `accept` 不成立，因为：

- 顺序 LangGraph 没有证明优于 Linear；
- 多 Reviewer 没有质量增益；
- Gate 7 没有完成真实 Linear/LangGraph 大任务 A/B；
- 状态面和维护面显著扩大。

选择 `reject` 也不成立，因为：

- Gate 4.5 R6 已真实证明 crash recovery 不重复 worker；
- P0 安全恢复与安全停止均符合预注册分类；
- HITL ledger、binding 与一次性消费已形成确定性证据；
- Gate 5.1 的 checkpoint seal、identity 和恢复加固通过；
- Gate 6 真实证明 handoff 合同可以跨 session 工作。

所以：

```text
有证据的恢复与 HITL 能力保留
没有证据的默认替换与多 Reviewer 收益拒绝
最终分类 = partial
```

---

## 3. 2026-07-20 最终演示重放修正

最终收口前首次执行无 Provider 的 deterministic fake replay：

```text
session = portfolio-final-20260720
linear-low = passed
graph-low = passed
graph-crash-hitl = safety_failed
```

失败不是 worker 被重复执行。首次 `recover` 已安全复用 worker Step Result，并运行
verification/risk gate 到 HITL；随后旧 harness 在**同一个 Python 进程**中立即 resume。
恢复守卫发现刚完成的 verification execution owner PID 仍存活，因此按合同拒绝接管。

修复没有放宽 PID 或 execution 信任规则，而是：

1. 使用独立 `python -m vega.cli recover` 子进程执行 recover；
2. 子进程退出后，父进程再写 decision 并 resume；
3. CLI recover 从持久化 Graph run config 复用创建时的 `timeout_seconds`；
4. 增加自动化测试证明 verification owner 来自已退出进程。

修正后的重放：

```text
session = portfolio-final-r2-20260720
runner = fake
provider sessions = 0
linear-low = passed
graph-low = passed
graph-crash-hitl = passed
conclusion = pass
elapsed = 118.924 seconds
```

三个 Case 的 worker start、worker execution 和 reviewer execution 均为 1；crash + HITL
Case 的 decision、pending、consumption 均为 1。这个修正说明安全守卫真实生效，也说明演示
harness 必须模拟用户命令之间的进程边界，不能为了演示通过而伪造死亡 PID 或放宽恢复条件。

---

## 4. Gate 7 对最终决策的影响

Gate 7 R6 已证明 API-key、Provider/model identity、远端 consumed tag 控制和 CP01 worker
过程可用，但：

```text
transcript = 89,908 / 65,536 bytes
tokens = 53,469 / 45,000
Gate 7A = failed
Gate 7C = not started
```

因此不能声称 LangGraph 在真实大任务中比 Linear 更可靠、更便宜或更高效。Gate 7 的有效
结论是预算门禁与停止线能阻止“模型进程成功”被错误升级成“checkpoint 业务成功”。

当前不立即启动 R7。若未来继续，只允许一次改变一个变量：缩小任务、提高预算或把部分探索改为
确定性步骤，不能同时修改后再宣称因果成立。

---

## 5. 产品建议

未来一个版本周期内建议：

1. 默认 `linear + single reviewer` 保持不变；
2. LangGraph 只作为显式 opt-in 实验引擎；
3. 只维护 crash recovery、HITL 和证据对账所需的最小 Graph 路径；
4. Goal/Handoff 在独立产品化 Gate 中验证 Linear 接入，不与 LangGraph 绑定；
5. 不继续扩大 fixed-three/adaptive Reviewer；
6. 不启动 FastAPI/SSE，除非出现明确的独立展示或产品需求；
7. 不用新的 Provider 重跑覆盖已冻结的负面结果。

---

## 6. 简历与面试表述

推荐表述：

> 为自研 AI Coding Harness 增加可选 LangGraph 恢复控制面，通过 execution、Step Result、
> workspace fingerprint 与 checkpoint 对账处理非幂等 worker 的 crash-resume；真实
> dogfood 中 worker 重复启动为 0，并用结构化 HITL 将人工批准绑定到当前 verification、
> policy 和 workspace evidence。

> 预注册并真实评测 single、adaptive 与 fixed-three Reviewer topology；82 次模型会话表明
> 多 Reviewer 未增加 true positive，却将 token 提高到 single 的 1.79 倍和 2.90 倍，因此
> 保持 single 为默认，接受负面实验结果而不强推复杂架构。

面试时必须同时说明：

- LangGraph checkpoint 不等于 Git workspace 事务；
- LangGraph 的已证实增益是恢复与 HITL，不是默认顺序编排；
- Handoff 的价值独立于 LangGraph；
- Gate 7 大任务 A/B 未完成；
- 最终结论是 `partial`，不是“全面采用 LangGraph”。

---

## 7. 证据索引

- [`CORE-DECISION.md`](CORE-DECISION.md)
- [`EXTENDED-DECISION.md`](EXTENDED-DECISION.md)
- [`GATE-3-RESULT.md`](GATE-3-RESULT.md)
- [`GATE-4-RESULT.md`](GATE-4-RESULT.md)
- [`GATE-4.5-R6-DOGFOOD-RESULT.md`](GATE-4.5-R6-DOGFOOD-RESULT.md)
- [`GATE-5.1-HARDENING-RESULT.md`](GATE-5.1-HARDENING-RESULT.md)
- [`GATE-5.5-RESULT.md`](GATE-5.5-RESULT.md)
- [`GATE-6-R4-RESULT.md`](GATE-6-R4-RESULT.md)
- [`GATE-7-R6-RESULT.md`](GATE-7-R6-RESULT.md)
- [`DEMO.md`](DEMO.md)

本地最终 deterministic replay 证据位于忽略目录：

```text
.local-validation/gate-4.5/portfolio-final-r2-20260720/
runs/20260720-173516-565402-bug-loop/
runs/20260720-173554-849495-bug-loop/
runs/20260720-173629-706978-bug-loop/
```

这些本地 artifact 不进入 Git；Git 中提交的是决策、复现入口、测试和可审计结论。
