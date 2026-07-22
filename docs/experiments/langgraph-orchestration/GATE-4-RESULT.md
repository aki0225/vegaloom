# Gate 4 Human-in-the-loop 结果

> 状态：`gate-4-pass`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> 实现基线 HEAD：`private-gate-2-implementation-redacted`
>
> 复审：`pass`，见 [`GATE-4-REVIEW.md`](GATE-4-REVIEW.md)

---

## 1. 结论

Gate 4 已完成结构化 interrupt、decision ledger 绑定、一次性消费和 P0-5 恢复验证。

当前证据支持以下结论：

- 高风险 `human-review` 不再被 LangGraph 直接固化为普通终态，而是在证据落盘后进入结构化
  `interrupt()`；
- interrupt 前已写入稳定 pending identity，payload 只包含 identity、允许动作和 artifact
  引用，不包含完整 diff、prompt 或批准文本；
- 人工决定先进入现有 append-only `decisions.jsonl`，resume 只接受 `decision_id`；
- approval 同时绑定 run、iteration、workspace、policy、verification、risk、reflect 和当前
  worker Step Result；
- 同一 pending identity 只能被同一 decision identity 幂等消费，不同 decision 冲突；
- workspace、policy 或 evidence 漂移后，旧 approval 失效；
- verification failed 不能被人工批准、reviewer approve 或 graph resume 覆盖；
- ledger 已写但 graph 尚未 resume 的 P0-5 窗口可以安全继续，worker 和外部写入均不重复；
- consumption 已写但业务 state 尚未推进时，可以复用同一 decision 恢复；
- standalone 高风险 review 没有可信 consumption 时仍保持 `needs_human`。

因此：

```text
Gate 4 = pass
Gate 4.5 Core Dogfood = approved to prepare
```

本结果只证明 deterministic fake-runner 下的 HITL、安全恢复和证据绑定语义。真实模型任务、
provider 稳定性和实际操作体验仍需 Gate 4.5 单独验证。

## 2. 实现范围

### 2.1 共享 Human Decision Step

共享顺序程序新增：

```text
evaluate_risk
-> request_human_decision
-> dispatch_review
```

新增结构化合同：

```text
HumanDecisionStepRequest
HumanDecisionStepResult
ReviewStepRequest.human_approval_ref
```

Linear Runtime 默认返回 `not_provided`，保持既有安全终态：

```text
status = needs_human
current_step = risk_gate_needs_human
```

LangGraph Runtime 在同一业务步骤使用 `interrupt()`，因此高风险 case 不再要求 Linear 与
Graph 具有相同的瞬时终态；二者只要求 interrupt 前的 worker、verification、reflect、risk
和 workspace 外部效果一致。

### 2.2 Pending Decision Identity

新增：

```text
src/vega/loop_graph_decision.py
graph/pending-decisions/<pending_id>.json
```

pending identity 绑定：

- run id；
- iteration；
- allowed decisions；
- 当前 workspace fingerprint；
- policy snapshot SHA-256；
- 当前项目 policy fingerprint；
- worker Step Result identity；
- verification status、failed count、result 与 summary hash；
- risk result 与 report hash；
- reflect state 与 full diff hash。

`pending_id` 由 binding hash 派生。Graph State 只保存
`pending_human_decision_id`，不保存完整批准上下文。

### 2.3 Decision Ledger 与 Resume

CLI 流程：

```text
vega decision approve/reject
  -> append decisions.jsonl
  -> output decision_id

vega resume
  -> only accepts --decision-id
  -> reload ledger entry
  -> validate pending binding
  -> resume LangGraph interrupt
```

`vega status` 会展示 pending artifact、approve / reject 命令和后续 resume 命令。

普通：

```text
vega recover
```

不得消费等待中的 HITL decision。对于：

```text
status = running
current_step = human_decision
```

Runtime 会明确拒绝普通 recovery，并要求先写 ledger，再使用 `vega resume`。

### 2.4 一次性 Consumption

新增：

```text
graph/decision-consumptions/<pending_id>.json
```

consumption 绑定：

- pending id；
- decision id；
- approved / rejected；
- decision ledger entry SHA-256；
- pending binding SHA-256；
- consumed time。

规则：

```text
same pending + same decision
  -> idempotent reuse

same pending + different decision
  -> conflict, fail-closed
```

ReviewRuntime 不只检查 consumption 文件存在，还要求传入的 `consumption_ref` 精确对应当前
iteration 的 pending identity，防止正确 approval 被错误 artifact 别名引用。

### 2.5 Reviewer 风险批准绑定

高风险 ReviewRuntime 只有在以下证据同时成立时，reviewer approve 才能作为有效隔离审查：

- parent loop run identity 一致；
- iteration 一致；
- consumption ref 精确匹配当前 pending；
- decision ledger entry 为 approved；
- consumption 与 ledger hash 一致；
- workspace、policy 和全部 pending evidence 未漂移；
- verification failed count 为 0。

否则 review run 保持：

```text
status = needs_human
current_step = risk_gate_needs_human
```

`review-context.json` 和 `eval.md` 会记录 approval 是 `valid`、`missing` 还是 `invalid`。

## 3. 故障窗口与负面用例

| Case | 预期 | 结果 |
|---|---|---|
| interrupt 前写 pending identity | checkpoint 与 pending artifact 可复核 | 通过 |
| P0-5：ledger 已写、resume 前崩溃 | 按原 decision id 继续，不创建第二次批准 | 通过 |
| reject | 不启动 reviewer，进入 `risk_gate_rejected` | 通过 |
| consumption 后、state 更新前崩溃 | 复用同一 consumption，不重复批准 | 通过 |
| workspace drift | 旧 approval 失效 | 通过 |
| policy drift | 独立 policy fingerprint 校验拒绝旧 approval | 通过 |
| risk / verification evidence drift | 旧 approval 失效 | 通过 |
| verification failed + approval | 拒绝继续 | 通过 |
| decision 未引用 pending artifact | 拒绝继续 | 通过 |
| 不同 decision 消费同一 pending | 冲突并停止 | 通过 |
| consumption ref 指向其他 pending alias | reviewer approval 无效 | 通过 |
| 普通 recover 处理 HITL interrupt | 拒绝并提示使用 resume | 通过 |

P0-5 最终计数：

```text
decision ledger entries = 1
worker starts = 1
worker external writes = 1
reviewer starts = 1
decision consumptions = 1
duplicate pre-interrupt business events = 0
```

## 4. 复审修复

复审关闭了两个问题，详情见 [`GATE-4-REVIEW.md`](GATE-4-REVIEW.md)：

1. LangGraph success 或 HITL 状态缺少 Graph State 时，`status` 曾可能继续展示业务状态；
2. ReviewRuntime 曾只检查 `consumption_ref` 文件存在，没有证明该 ref 就是当前 iteration 的
   consumption identity。

修复后：

- success 和 HITL status 都必须具备可信 Graph State；
- consumption ref 必须与当前 pending identity 精确一致。

## 5. 验证结果

所有 pytest 分片均使用独立 `.tmp/pytest/runs/<name>` 和
`.tmp/pytest/cache/<name>`。

### 5.1 Gate 4 HITL

```text
Interrupt / recover guard / CLI：
3 passed in 38.98s
2 passed in 44.25s

Decision binding：
3 passed in 40.54s
3 passed in 40.86s
1 passed in 32.36s
2 passed in 41.50s
1 passed in 15.99s
```

覆盖：

```text
test_interrupt_resume.py = 4 tests
test_hitl_cli.py = 1 test
test_decision_binding.py = 10 tests
```

### 5.2 Graph、Engine 与兼容性

```text
Graph State contract：17 passed in 0.34s
Handler boundary：6 passed in 27.88s
Engine selection：7 passed in 3.55s
Legacy compatibility：34 passed in 2.00s
```

### 5.3 Semantic Parity 与安全消费

`test_linear_graph_semantic_parity.py` 的 15 个 node 均取得明确 passed：

- success、verification failure、review failure；
- 高风险 Linear 安全终态与 Graph HITL interrupt 分层语义；
- Graph success 缺失、损坏、身份错配和重复 key 等消费拒绝；
- Graph State 写失败与 success 撤销；
- assist 和五轮多轮等价。

五轮 node 本身不可再拆，首次 60 秒 timeout 未计入通过；随后以同一完整 node id、独立
basetemp 和 180 秒上限重跑：

```text
1 passed in 131.61s
```

### 5.4 既有风险与成功语义

```text
Standalone high-risk review 与 review evidence：2 passed in 41.86s
Failed review 不能提升 parent success：1 passed in 28.02s
Human-review risk chain 不能无批准完成：1 passed in 48.58s
```

### 5.5 静态检查

```text
python -m compileall -q src
ruff check src tests
git diff --check
git check-ignore -v .tmp/pytest/runs/... .tmp/pytest/cache/... .tmp/ruff/cache
```

结果全部通过，`.tmp/` 仍由仓库 `.gitignore` 忽略。

## 6. 硬指标

| 指标 | 结果 |
|---|---:|
| Interrupt Consistency | `100%` |
| Duplicate Worker Starts | `0` |
| Duplicate External Effects | `0` |
| Duplicate Approval Creation | `0` |
| Duplicate Decision Consumption | `0` |
| Unsafe Resume | `0` |
| Invalid Approval over Verification Failure | `0` |
| Silent Workspace Drift | `0` |
| Silent Policy Drift | `0` |
| Silent Evidence Drift | `0` |

## 7. 当前边界

Gate 4 不宣称以下能力已经完成：

- 第二轮及以后 crash / HITL driver 重建；
- `edit_scope` 同 run 动态修改策略；
- decision revoke；
- 多进程并发消费同一 pending；
- 三路并行 reviewer；
- 真实模型 Core Dogfood；
- Vega 对自身仓库运行时的控制面精确排除。

当前实现仍是本地单进程、单写者 Runtime。Graph checkpoint、JSON artifacts 和 Git
workspace 之间没有跨介质事务，安全性来自 identity、hash、append-only consumption 和
fail-closed reconciliation。

## 8. 下一步

进入 Gate 4.5 前先冻结真实 dogfood 输入：

1. 选择独立 fixture repo，不对 Vega 自身仓库写入；
2. 固定模型、runner、sandbox、timeout 和预算；
3. 固定一个低风险对照任务和一个会触发 HITL 的高风险任务；
4. 预注册 kill 点、成功标准和人工 decision；
5. Linear 与 LangGraph 使用 fresh baseline；
6. memory 继续关闭；
7. 真实 runner 结果与 deterministic fake-runner 证据分开记录。
