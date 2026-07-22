# Vega LangGraph 编排实验 Gate 0 ADR

> 状态：`accepted-for-gate-1`
>
> 日期：2026-07-15
>
> 当前分支：`experiment/langgraph-comparison`
>
> Gate 0 开始前 HEAD：`private-gate-0-contract-redacted`
>
> 代码实验基线：`private-experiment-base-redacted`
>
> 适用范围：`docs/experiments/langgraph-orchestration/`

---

## 1. 背景

本 ADR 冻结 LangGraph 编排实验进入 Gate 1 之前必须遵守的架构决策。

当前分支事实需要分成两层理解：

- `private-gate-0-contract-redacted` 是 Gate 0 开始前 HEAD，已经加入 LangGraph 实验执行计划与演示契约文档。
- `private-experiment-base-redacted` 是代码实验基线。第一轮代码实验仍以该基线的 Vega Runtime 能力为准。

因此，Gate 0 的目标不是实现 LangGraph，也不是追认其他实验分支的能力，而是明确：

- 默认执行语义仍是现有 `linear` Runtime；
- LangGraph 只能作为 optional experimental engine；
- 第一轮实验不移植其他分支中的 Scope Gate 或 Selective Memory；
- 多 Agent 审查模型采用 single writer / multiple isolated readers；
- 后续 Gate 只能按证据顺序推进。

---

## 2. 决策摘要

### 2.1 采纳：默认引擎保持 linear

Vega 当前稳定语义继续由线性 Runtime 承担。

在实验给出明确证据前，`linear` 是默认引擎、主线安全语义和既有 artifact contract 的基准。
LangGraph 不能改变现有成功、失败、验证、风险门禁、人工批准或 Finish 语义。

### 2.2 采纳：LangGraph 是 optional experimental engine

LangGraph 只作为可选实验编排引擎引入。

要求：

- 依赖必须是 optional，不得让未启用 LangGraph 的用户或测试路径承担运行时依赖。
- run 创建后 engine 必须固定，禁止 `linear` 与 `langgraph` 在恢复时互相切换。
- LangGraph node 只负责路由、引用解析和调用 engine-agnostic handler，不复制 Vega 业务逻辑。
- Graph checkpoint 只拥有图执行游标，不拥有业务成功语义。

### 2.3 采纳：第一轮不移植 Scope Gate / Selective Memory

第一轮 LangGraph 实验基于 `private-experiment-base-redacted` 的当前代码能力。

明确不纳入第一轮：

- `experiment/daily-loop-dogfood-mainline@private-mainline-dogfood-status-redacted` 的精确路径 Scope Gate；
- `experiment/selective-memory@private-selective-memory-calibration-redacted` 的完整 Selective Memory runtime 与校准结果；
- 其他未合并分支中的 dogfood 结论或主线化文档。

这些能力可以作为后续独立基线升级或移植任务讨论，但不能静默混入本次 A/B 条件。

### 2.4 采纳：single writer / multiple isolated readers

第一轮多 Agent 模型采用一个 writer 与多个隔离只读 reviewer。

含义：

- 只有 worker 可以写目标 workspace。
- reviewer 只读取同一 evidence snapshot，不直接修改代码、不启动第二个 writer。
- reviewer 结论由确定性 aggregator 合并。
- reviewer approve 不能覆盖 verification failure、stale evidence、workspace drift 或缺失人工批准。

该决策用于避免多个 writer 争用同一 Git workspace，降低无法解释的副作用和恢复风险。

### 2.5 采纳：按 Gate 顺序推进

上一 Gate 未得到明确终态时不得进入下一 Gate。

固定顺序如下：

1. Gate 0：冻结实验契约、状态所有权、恢复协议、评测协议和 blocker。
2. Gate 1：建立最小 Engine / Handler 边界，保持 linear 语义不变。
3. Gate 2：实现顺序 LangGraph 等价图，并验证 semantic parity。
4. Gate 3：实现 checkpoint 与恢复握手，验证不重复外部副作用。
5. Gate 4：实现 Human-in-the-loop，并绑定 decision ledger。
6. Gate 4.5：Core Dogfood，决定是否继续核心编排实验。
7. Gate 5：实现并行隔离 reviewer。
8. Gate 5.5：Reviewer Dogfood，验证多 reviewer 是否有边际收益。
9. Core Decision：给出核心实验 `accept / partial / reject`。
10. Gate 6：可选 Goal / Checkpoint / Handoff 扩展实验。
11. Extended Decision：判断扩展能力保留、独立复用、继续实验或删除。
12. Final Decision：汇总核心与扩展结论。

---

## 3. 为什么不直接替换 linear

不直接用 LangGraph 替换 linear，原因不是否定 LangGraph，而是避免把编排实验变成主线语义迁移。

关键原因：

- 当前 linear 已经承载 Vega 的成功语义、安全门禁、验证证据、风险结论和 artifact contract。
- LangGraph checkpoint 不能覆盖 Git workspace、外部进程、文件写入和人工决策这些非事务化事实。
- 节点恢复可能重放节点，若没有 execution evidence、step result 和 workspace reconciliation，可能重复启动 worker 或重复外部副作用。
- 如果先替换 linear，就无法清晰比较 A/B 终态，也无法判断 LangGraph 的收益来自图编排本身还是来自顺手重写 Runtime。
- 直接替换会把实验风险转移给稳定路径，违背 optional experimental engine 的边界。

因此，LangGraph 第一阶段只能证明它能在不改变 linear 语义的前提下复用业务步骤，并通过证据说明是否值得保留。

---

## 4. 明确拒绝的决策

### 4.1 拒绝：把其他分支能力写成当前基线事实

拒绝把 Scope Gate、Selective Memory 或其他 dogfood 分支中的能力描述为 `private-experiment-base-redacted` 已具备能力。

如果后续引用这些能力，必须标注分支、提交和用途，并作为独立移植或基线升级处理。

### 4.2 拒绝：LangGraph checkpoint 成为业务状态真相源

拒绝让 graph checkpoint 覆盖 `state.json`、`execution.json`、verification artifacts、risk artifacts 或 workspace fingerprint。

发生不一致时，必须进入 reconciliation；无法解释时进入 `needs_human`，不得让 graph 游标自动声明业务成功。

### 4.3 拒绝：多个 writer 同时写同一 workspace

拒绝在第一轮实验中让多个 worker 或 reviewer 同时修改目标仓库。

多 reviewer 只能作为 isolated readers 读取同一证据快照，不能通过模型投票绕过确定性验证。

### 4.4 拒绝：把 Memory 与编排实验混在一起

拒绝在 Gate 1 到 Gate 4 的核心编排实验中引入 Selective Memory。

第一轮固定 `memory.mode=off`。Goal/Handoff 或 Memory 相关能力只能在 Core Decision 之后作为独立变量评估。

### 4.5 拒绝：用人工编码工期或 AI 生成速度代替 Gate 证据

拒绝因为 AI 可以快速生成代码就跳过 deterministic tests、crash windows、独立复审或 dogfood。

也拒绝因为人工开发看起来较重就提前降低研究目标。Gate 是否通过只看证据，不看生成速度。

---

## 5. 明确延后的决策

### 5.1 延后：是否把 LangGraph 合入主线

是否保留 LangGraph、部分保留还是删除实验代码，延后到 Core Decision 和 Final Decision。

Gate 0 只允许建立实验边界，不承诺主线采用。

### 5.2 延后：Scope Gate 是否移植到本实验基线

Scope Gate 是否需要从其他分支移植，延后到核心编排语义有证据后再判断。

如果移植，必须作为单独提交、单独评审和新的 A/B 条件记录。

### 5.3 延后：Selective Memory 是否与 LangGraph 组合

Selective Memory 与 LangGraph 的组合延后到核心编排实验之后。

原因是 Memory、Goal handoff 和 orchestration 是三个不同变量，过早组合会污染因果判断。

### 5.4 延后：FastAPI / SSE 控制面

FastAPI / SSE 不属于 Gate 0 到 Core Decision 的接受条件。

只有核心结论成立且项目 owner 明确需要演示控制面时，才在独立后续分支或提交中评估。

### 5.5 延后：Vega self-dogfood 控制面排除机制

第一轮使用项目内独立 fixture repo，避免 Vega 的 runtime 控制面污染目标 Git workspace fingerprint。

Vega 对自身仓库运行时的 `runs/` 排除、外置 graph control root 或 self-dogfood 机制延后到独立实验。

---

## 6. Gate 0 Blocker 清单

进入 Gate 1 前，以下 blocker 必须关闭或被项目 owner 明确降级：

1. 当前分支事实必须更新为：Gate 0 开始前 HEAD=`private-gate-0-contract-redacted`，代码实验基线=`private-experiment-base-redacted`。
2. ADR、状态所有权、恢复协议和评测协议必须拆分并互相一致。
3. `linear` 默认行为必须冻结，且不得被 LangGraph 适配工作隐式改变。
4. `memory.mode=off` 必须成为核心编排实验的固定前提。
5. engine 必须在 run 创建后固定，恢复时不得切换 engine。
6. execution evidence、step result、workspace fingerprint 与 graph checkpoint 的权威边界必须无歧义。
7. P0 crash windows 必须预注册，并能由恢复协议解释。
8. 项目内独立 fixture repo 与 Vega control root 的路径隔离必须固定。
9. LangGraph dependency 必须保持 optional。
10. 独立 reviewer 必须确认无未关闭 Blocker / High 后，才能进入 Gate 1 或启动真实 runner。

---

## 7. Gate 0 结论

本 ADR 采纳 LangGraph 作为可选、可证伪、分阶段推进的实验编排引擎。

本 ADR 不采纳直接替换 linear，不采纳从其他分支静默移植 Scope Gate 或 Selective Memory，不采纳多个 writer 写同一 workspace，也不采纳由 graph checkpoint 覆盖 Vega 业务状态。

Gate 0 的核心判断是：

```text
linear remains default.
LangGraph is optional and experimental.
One writer writes.
Multiple isolated reviewers read.
Vega owns business truth.
LangGraph owns graph cursor.
Evidence decides safe resume.
```
