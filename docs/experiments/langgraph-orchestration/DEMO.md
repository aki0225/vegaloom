# Vega LangGraph 三分钟演示

> 文档状态：`final-demo`
>
> 更新日期：`2026-07-20（星期一）`
>
> 最终决策：`partial`
>
> 主演示：无 Provider 的 deterministic fake replay
>
> 真实补充证据：Gate 4.5 R6、Gate 5.5、Gate 6 R4、Gate 7 R6

---

## 1. 演示结论

这不是“LangGraph 画图”演示，而是一个窄而可复核的恢复证明：

> worker 已经修改 Git workspace，但图状态尚未完成业务提交时，Vega 使用 execution、
> Step Result、workspace evidence 与 checkpoint 对账，避免重复 worker；高风险路径进入
> 结构化 HITL，人工决定只消费一次。

最终演示不声称 LangGraph 应替换默认 Linear Runtime。仓库的最终架构决策仍是：

```text
default engine = linear
default reviewer topology = single
LangGraph = optional experimental recovery / HITL control plane
```

---

## 2. 一条可重复入口

前置条件：

- 当前位于仓库根目录；
- 已创建 `.venv`；
- 已安装 `.[dev,langgraph]`；
- tracked Git 工作区干净；
- 不需要 API key，不调用真实 Provider。

PowerShell：

```powershell
$session = "portfolio-demo-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\.venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner fake `
  --session $session
```

预期终态：

```text
Gate 4.5 fake dogfood：pass
- linear-low: passed
- graph-low: passed
- graph-crash-hitl: passed
```

如正在修改 tracked 文件，只能在提交前本地验证时显式增加 `--allow-dirty`。正式演示和真实
dogfood 不应使用该参数。

---

## 3. 三个 Case

### 3.1 `linear-low`

证明现有 Linear Runtime 在同一 fixture 上可以完成：

```text
worker -> verification -> risk -> single reviewer -> success
```

它是 LangGraph 对照组，不是可省略的暖场。

### 3.2 `graph-low`

使用与 `linear-low` 相同的 fixture HEAD，证明顺序 LangGraph 可以复用同一业务 Handler，并
达到一致的：

- 业务终态；
- verification；
- changed files；
- reviewer verdict；
- artifact integrity；
- evidence freshness。

### 3.3 `graph-crash-hitl`

固定故障点：

```text
after_step_result_before_state
```

固定链路：

```text
worker completed
-> execution / attempt / Step Result persisted
-> deterministic fault
-> 独立 CLI recover 进程
-> reconciliation = safe_reuse_step_result
-> verification
-> high-risk interrupt
-> decision ledger
-> resume by decision_id
-> one-time consumption
-> single reviewer
-> success
```

核心断言：

```text
worker_start_count = 1
worker_execution_count = 1
reviewer_execution_count = 1
decision_count = 1
pending_count = 1
consumption_count = 1
verification_status = passed
```

---

## 4. 为什么 Recover 必须是独立进程

`recover` 会继续执行 verification，并停在 HITL。旧版 demo harness 在同一个 Python
进程中立即执行 `resume`，此时 verification execution 的 owner PID 仍然存活。恢复守卫
正确地将其视为潜在并发副作用来源并 fail-closed：

```text
GraphRecoveryValidationError:
execution owner PID 仍存活，已拒绝 Graph recovery
```

2026-07-20 的首次最终重放因此得到：

```text
session = portfolio-final-20260720
linear-low = passed
graph-low = passed
graph-crash-hitl = safety_failed
```

修复没有伪造死亡 PID，也没有放宽恢复守卫。当前 harness 使用独立
`python -m vega.cli recover` 子进程；该进程退出后，父进程才写 decision 并 resume。
同时，CLI recover 会从 Graph run config 读取创建时冻结的 `timeout_seconds`，避免恢复身份
漂移。

修正后：

```text
session = portfolio-final-r2-20260720
runner = fake
provider sessions = 0
conclusion = pass
elapsed = 118.924 seconds
```

这个失败历史应保留，因为它证明安全规则不是为了 Demo 临时绕过，也暴露了“同进程故障注入”
与“真实多命令恢复流程”之间的边界。

---

## 5. 三分钟讲解时间轴

当前机器上的最终 fake replay 用时约 119 秒。现场可以边运行边展示以下内容：

| 时间 | 展示内容 | 关键结论 |
| --- | --- | --- |
| 00:00～00:20 | fixture、分支、三个 Case | Linear 是对照组，Graph 不改业务 Handler |
| 00:20～00:50 | `linear-low` 与 `graph-low` | 顺序语义等价，不声称 Graph 更快 |
| 00:50～01:20 | worker execution、attempt、Step Result | 外部副作用与图游标不是同一状态 |
| 01:20～01:50 | fault、独立 recover、reconciliation | worker 没有第二次启动 |
| 01:50～02:15 | verification、risk interrupt | 模型 approve 不能覆盖确定性验证 |
| 02:15～02:35 | pending、decision、consumption | 人工批准绑定当前证据且只消费一次 |
| 02:35～03:00 | summary、最终决策 | LangGraph 只保留恢复/HITL 增益 |

真实 Provider dogfood 通常更慢，不应为了三分钟现场展示而重跑。可以直接展示冻结的
Gate 4.5 R6 结果作为补充证据。

---

## 6. 演示后检查

Harness 会打印证据目录。至少检查：

```text
.local-validation/gate-4.5/<session>/
  summary.json
  REPORT.md

runs/<graph-crash-hitl-run>/
  state.json
  trace.jsonl
  decisions.jsonl
  graph/
    graph-state.json
    checkpoint-manifest.json
    checkpoints.sqlite
    pending-decisions/
    decision-consumptions/
  step-results/
    worker-iteration-01.json
  iterations/01/
    executions/
      worker/execution.json
      verification-01/execution.json
      reviewer/execution.json
    verification-result.json
    risk-gate-result.json
  final-report.md
```

快速查看结构化结论：

```powershell
Get-Content ".local-validation\gate-4.5\$session\summary.json"
```

这些目录均被 Git 忽略，不应提交。

---

## 7. 自动化验证入口

只验证本次修复的进程边界：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\experimental\langgraph_engine\test_core_dogfood_harness.py::test_crash_hitl_recover_uses_exited_process_before_resume `
  -q `
  --require-langgraph `
  --basetemp .tmp\pytest\runs\demo-process-boundary
```

验证完整 Core Dogfood harness：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\experimental\langgraph_engine\test_core_dogfood_harness.py `
  -q `
  --require-langgraph `
  --basetemp .tmp\pytest\runs\demo-core-harness
```

最终收口时该测试文件结果为：

```text
49 passed
```

---

## 8. Reviewer 结果如何展示

不要再把“三路 Reviewer”当成默认亮点，也不建议现场重新消耗 Provider 预算。

应该展示 [`GATE-5.5-RESULT.md`](GATE-5.5-RESULT.md) 中的冻结结论：

```text
single:
  TP = 0
  FP = 9
  token multiplier = 1.0000

adaptive:
  TP = 0
  FP = 25
  token multiplier = 1.7896

fixed_three:
  TP = 0
  FP = 34
  token multiplier = 2.9043
```

这部分的演示重点是：

1. 多 Reviewer 控制面确实可以正确运行；
2. 82 次真实 Provider session 的安全指标为 0；
3. 但没有新增 true positive；
4. 因此产品默认保持 single。

负面结果比展示三个并发方框更有架构价值。

---

## 9. 真实证据补充

### Gate 4.5 R6：真实 crash + HITL

```text
日期 = 2026-07-17
provider sessions = 7
linear-low = passed
graph-low = passed
graph-crash-hitl = passed
duplicate worker starts = 0
unsafe resume = 0
```

### Gate 5.5：真实 Reviewer topology

```text
日期 = 2026-07-19
provider sessions = 82
conclusion = single wins
```

### Gate 6 R4：真实跨 Session Handoff

```text
日期 = 2026-07-19
conclusion = reuse-independent-of-langgraph
```

### Gate 7 R6：真实大任务停止线

```text
日期 = 2026-07-20
API-key / Provider / CP01 worker = passed
transcript = 89,908 / 65,536
tokens = 53,469 / 45,000
Gate 7A = failed
Gate 7C = not started
```

Gate 7 不能用于声称 LangGraph 大任务优于 Linear；它只能证明证据预算失败时系统会安全停止。

---

## 10. 禁止结论

完成本演示后仍不得声称：

- LangGraph 已替换默认 Linear Runtime；
- checkpoint 可以自动回滚 Git workspace；
- 所有 P1 crash window 都可以自动恢复；
- fake runner 证明真实模型质量；
- 三路 Reviewer 比单 Reviewer 更可靠；
- Goal/Handoff 的价值来自 LangGraph；
- Gate 7 已形成真实 Linear/LangGraph 大任务 A/B；
- FastAPI、SSE、Web UI 或多租户已经实现；
- 本地文件是物理不可篡改的。

正式结论见 [`DECISION.md`](DECISION.md)。
