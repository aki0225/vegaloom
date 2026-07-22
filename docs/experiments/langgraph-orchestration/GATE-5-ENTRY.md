# Gate 5 并行隔离 Reviewer 入口

> 当前状态：`pass`
>
> 入口日期：`2026-07-17（星期五）`
>
> 一致性更新日期：`2026-07-18（星期六）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 准入证据基线：`private-gate-4-5-r6-preregistration-redacted`
>
> 实现起点：`private-gate-5-entry-redacted`
>
> Gate 4.5：`pass`
>
> Gate 5：`pass`
>
> Gate 5.5：`not started / not authorized`

> 最终结果见 [`GATE-5-RESULT.md`](GATE-5-RESULT.md)，最终复核状态见
> [`GATE-5-REVIEW.md`](GATE-5-REVIEW.md)。真实 provider 调用仍为 `0`，默认产品路径仍为
> `linear + 单 Reviewer`。Gate 5.1 hardening 见
> [`GATE-5.1-HARDENING-RESULT.md`](GATE-5.1-HARDENING-RESULT.md)。

---

## 1. 准入决定

Gate 4.5 R6 的唯一真实 session 已通过预注册合同和 80 项只读 artifact 复核，因此 Gate 5
获准进入确定性实现阶段。

这里的“进入 Gate 5”只表示：

```text
允许实现并行隔离 reviewer
```

不表示：

```text
三路 reviewer 已实现
Gate 5 已通过
多 reviewer 已证明有边际收益
Gate 5.5 已获准运行真实 dogfood
```

准入证据见
[`GATE-4.5-R6-DOGFOOD-RESULT.md`](GATE-4.5-R6-DOGFOOD-RESULT.md)。

Phase 1 的结构化结果合同与确定性 aggregator 结果见
[`GATE-5-PHASE-1-RESULT.md`](GATE-5-PHASE-1-RESULT.md)。

固定三路假设的复审与自适应 ReviewPlan 结果见
[`GATE-5-PHASE-2-ADAPTIVE-PLAN-RESULT.md`](GATE-5-PHASE-2-ADAPTIVE-PLAN-RESULT.md)。

Artifact 持久化、Graph State v2 与可变 N 路 fake fan-out 结果见
[`GATE-5-PHASE-3-ARTIFACT-FANOUT-RESULT.md`](GATE-5-PHASE-3-ARTIFACT-FANOUT-RESULT.md)。

三种 topology 的真实收益评估候选方法见
[`GATE-5-TOPOLOGY-EVAL-CANDIDATE.md`](GATE-5-TOPOLOGY-EVAL-CANDIDATE.md)。

## 2. 当前真实代码状态

当前产品 Loop Runtime 仍然是单 reviewer 顺序图：

```text
evaluate_risk
  -> request_human_decision（按风险条件）
  -> dispatch_review
  -> finalize_run
```

Gate 5 独立实验图已经具备：

- 从真实 run 文件计算 evidence snapshot hash；
- ReviewPlan、execution、result 和 aggregate 的 append-only artifact；
- Graph State v2 的窄 result ref 与实际 artifact hash 复核；
- `single`、两路 adaptive 和 `fixed_three` 共用的可变 N 路 LangGraph `Send` fan-out；
- 与完成顺序无关的 deterministic aggregate node；
- role-specific 真实只读 Runner adapter 与公共 evidence / 私有 role prompt 隔离；
- 部分完成复用、attempt 恢复、Compatibility provenance 与 Goal attachment binding；
- run 级永久 stop latch，并向该 run 的全部 active execution 广播 stop request；
- reviewer 私有 canary 不进入 parent state、aggregate 或 checkpoint。

当前限制同样明确：

- 产品 Loop 的 `dispatch_review` 仍只启动一个 reviewer；
- 现有 Core Dogfood harness 固定要求 reviewer execution 恰好为 1；
- 当前 `review-verdict.json` 是单 reviewer 终态，不是 ReviewPlan 结果聚合；
- 真实 provider 调用仍为 `0`，不能证明真实模型质量或多 Reviewer 边际收益；
- Gate 5.5 预注册、真实 dogfood 和 topology 成本收益比较尚未开始。

Gate 5 必须显式升级这些合同，不能通过静默放宽 Gate 3 校验或修改 Gate 4.5 历史通过标准来
伪造并行能力。

Graph State 已通过显式 v2 接入 result ref；历史 v1 仍要求 `review_results` 为空，因此旧
Gate 3/4 安全边界没有被静默放宽。

## 3. 固定架构边界

### 3.1 Single writer / multiple isolated readers

保持：

```text
worker = 唯一 workspace writer
reviewers = isolated read-only readers
aggregator = deterministic pure logic
```

禁止：

- reviewer 修改目标 workspace；
- reviewer 读取其他 reviewer 私有 prompt、输出或 canary；
- reviewer 通过投票覆盖 deterministic verification；
- 任一模型自然语言直接写入 `state.json` 成功语义；
- 多个 reviewer 共用可变会话状态；
- 把 provider 重试伪装成并行容错。

### 3.2 Reviewer Pool 与确定性 ReviewPlan

当前保留三个**可选专业角色**：

1. `correctness_reviewer`
   - 需求语义；
   - 行为正确性；
   - 边界条件；
   - 明显逻辑缺陷。
2. `verification_adequacy_reviewer`
   - 测试是否覆盖需求和主要边界；
   - 是否缺少能够推翻当前结论的验证；
   - verification evidence 是否完整并绑定当前 workspace；
   - 不重新决定测试到底 passed 还是 failed。
3. `security_design_reviewer`
   - 安全设计与信任边界；
   - 新依赖和外部系统影响；
   - 敏感信息风险；
   - 不能由静态规则完全判断的设计问题。

三个角色组成 reviewer pool，不表示每次必须全部执行。每个 iteration 必须先由确定性
`ReviewPlan` 选择本次 required roles：

```text
普通低风险变更
  -> correctness

测试范围变化、测试缺失或 verification 未解决
  -> correctness + verification adequacy

风险路径、新依赖、跨模块或较大变更
  -> correctness + security/design

需要多维交叉检查的高风险变更
  -> 三路
```

ReviewPlan 只能读取 risk artifact、verification artifact、changed files 和 gate reason
codes 等机器事实，不能由另一个 LLM 自由路由。

`single`、`fixed_three` 和 `adaptive` 都是显式 topology：

- `adaptive` 是候选默认策略；
- `single` 是单 reviewer 对照组；
- `fixed_three` 只作为压力测试和收益评估对照组，不是产品默认值。

精确路径、变更预算、Git 漂移、verification 终态、checkpoint 和 approval binding 继续由
确定性代码负责。

### 3.3 同一 ReviewPlan 与 Evidence Snapshot

本次被选择的所有 reviewer 必须绑定同一个：

```text
review plan id
run_id
iteration
workspace fingerprint
policy snapshot sha256
verification result sha256
risk result sha256
acceptance evidence manifest sha256
evidence snapshot sha256
```

任一路使用其他 plan、旧 snapshot、不同 revision、截断但未声明的证据或 hash mismatch，
最终聚合必须 fail-closed。计划外 reviewer 结果同样不能进入有效聚合。

## 4. 计划中的最小实现

### 4.1 先冻结 schema，不先写 fan-out

先定义并测试：

- reviewer role 枚举；
- topology 与 ReviewPlan identity；
- role trigger reasons；
- reviewer attempt identity；
- evidence snapshot identity；
- 单路结构化 result artifact；
- finding severity、category、rule id、路径和位置；
- finding 稳定 identity；
- 聚合 result artifact；
- Graph State 中只保存窄引用，不保存模型长文本。

稳定 finding identity 固定由以下字段计算：

```text
category
+ rule_id
+ normalized_path
+ normalized_location
+ evidence_snapshot_sha256
```

### 4.2 再实现可变 N 路 isolated reviewer

ReviewPlan 选择的一至三路 reviewer 各自：

- 使用独立短生命周期执行；
- 使用只读 runner；
- 只读取公共 evidence package 和本角色 prompt；
- 输出独立 execution、process output、structured result；
- 不读取其他 reviewer artifact；
- timeout、provider error、未知 termination 均形成明确失败分类，不自动重试。

`vega stop --run <run>` 建立永久 run 级 latch，不是只停止当前或最新 execution。停止意图会
广播到该 run 的全部 active execution；latch 建立后，同一 run 后续新建的 execution 也必须
在启动外部进程前 fail-closed。广播只作用于各 execution 绑定的 owned process tree，不枚举
或终止其他 run、其他 Codex/Node 进程。

确定性 blocker 不应仅因为 risk=`high` 就无条件扩展成三路。例如 `no_diff`、
`diff_check_failed` 或 verification failed 已经有机器结论，额外启动三个模型不会增加
有效信息。

### 4.3 最后实现确定性 aggregator

Aggregator 只读取结构化 artifact，按稳定 identity 去重，并执行硬规则：

```text
verification != passed
  -> 不能 approve

存在 blocker / major
  -> request_changes

risk = high 且缺少有效 human approval
  -> needs_human

evidence stale / truncated / hash mismatch
  -> needs_human

同一高风险事实存在不可消解冲突
  -> needs_human

任一必需 reviewer timeout / provider error / unknown termination
  -> needs_human

否则
  -> approve
```

自然语言 summary 不能覆盖这些规则。

## 5. 测试顺序

Gate 5 先使用 fake/deterministic runner，不立即调用真实 provider。

### 5.1 P0 schema 与 reducer

- 相同 result identity 重放幂等；
- 相同 identity 不同内容 fail-closed；
- reviewer 完成顺序所有排列得到相同聚合结果；
- finding identity 去重稳定；
- 不同 evidence snapshot 的 finding 不合并；
- Graph State 仍满足大小和禁入内容约束。

### 5.2 P0 安全语义

- verification failed 时任意数量 reviewer 全 approve 也不能 success；
- stale evidence 不能合并；
- 缺少任一路必需结果不能伪 approve；
- timeout、provider error、active 或 unknown termination 不能伪 approve；
- high risk 缺少有效 consumed approval 不能 success；
- 旧 approval、旧 policy 或漂移 workspace 不能复用。

### 5.3 P0 隔离

固定 canary：

```text
WORKER_PRIVATE_CANARY_<uuid>
CORRECTNESS_PRIVATE_CANARY_<uuid>
VERIFICATION_PRIVATE_CANARY_<uuid>
SECURITY_PRIVATE_CANARY_<uuid>
```

必须证明：

- worker canary 不进入 reviewer prompt、checkpoint 或 parent shared state；
- reviewer canary 不进入其他 reviewer；
- aggregator 只读取结构化结果；
- checkpoint 和 Graph State 不保存 reviewer 私有正文。

### 5.4 回归

- Linear Runtime 行为不变；
- Gate 1～4 直接相关回归继续通过；
- Gate 4.5 harness 保持单 reviewer 历史合同，不改成“多路后也算一次”；
- Gate 5 使用独立测试和后续独立 dogfood harness；
- 未安装 LangGraph optional dependency 时基础 Linear import 继续通过。

## 6. Gate 5 退出标准

只有以下全部成立，Gate 5 才能标记为 `pass`：

```text
reviewer context leak = 0
reducer determinism = 100%
finding identity conflict 未被静默覆盖
verification failure override = 0
stale evidence merge = 0
invalid success without approval = 0
timeout/provider error fake approve = 0
Graph State / checkpoint secret leakage = 0
Linear regression = 0
```

还必须：

- ReviewPlan 所要求的 reviewer 使用同一 plan identity 和 evidence snapshot；
- `single`、`fixed_three`、`adaptive` 均能确定性重建；
- 计划外、缺失或重复 reviewer 均不能伪 approve；
- execution 和 result artifact 身份完整；
- aggregator 输出可从输入 artifact 确定性重建；
- 独立复审没有未关闭 Blocker / High；
- 测试取得明确 passed/failed 计数，timeout 不计通过。

## 7. Gate 5.5 准入

Gate 5 通过后，才能冻结独立 Gate 5.5 Reviewer Dogfood 预注册合同。Gate 5.5 使用带
ground truth 的同一组案例比较：

- `single`、`fixed_three`、`adaptive` 的有效新增 finding、precision、recall 和 false
  blocker；
- 固定三路相对自适应路由的额外 provider 成本是否值得；
- 真实并发下隔离、timeout 和聚合语义是否仍成立；
- 最终产品默认应是单路、自适应路由还是固定 fan-out。

固定三路只有在真实收益显著高于自适应路由时才有资格成为候选默认；否则只保留为实验
topology。

当前不得运行 Gate 5.5 真实 provider dogfood。

## 8. 当前状态

```text
Gate 4.5 = pass
Gate 5 implementation phase = entered
Gate 5 deterministic implementation = adaptive ReviewPlan pass
Gate 5 artifact persistence = pass
Gate 5 Graph State v2 = pass
Gate 5 variable-N fake fan-out = pass
Gate 5 real isolated reviewer adapter = implemented
Gate 5 recovery and compatibility contract = implemented
Gate 5 run-level permanent stop latch = implemented
Gate 5 code-level High remediation = pass
Gate 5.1 hardening = pass
Gate 5 final review = Blocker 0 / High 0 / Medium 0
Gate 5.5 = not started
Gate 5.5 authorization = not granted
candidate review topology = adaptive
default engine = linear
memory = off
multiple workspace writers = forbidden
```
