# Gate 4.5 Core Dogfood R3 业务结果

> 最终分类：`blocked`
>
> Gate 5：`不进入`
>
> 系统日期：`2026-07-16（星期四）`
>
> 执行基线：`private-gate-4-5-r3-business-contract-redacted`
>
> 真实 session：`real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted`
>
> R3 preflight-only 结论：`preflight-passed`
>
> R2 历史结论：`blocked`

---

## 1. 最终结论

R3 完成了业务 session 内置 preflight 和三个预注册业务 Case。没有自动重试，没有切换
provider、model、reasoning 或 sandbox，也没有扩大 worker/reviewer 预算。

业务结果为：

```text
linear-low = passed
graph-low = blocked
graph-crash-hitl = passed
Gate 4.5 = blocked
Gate 5 = 不进入
```

`graph-low` 未达到 `success`，而是：

```text
state_status = needs_human
current_step = review_run_failed
artifact_integrity_valid = false
evidence_freshness_valid = false
```

因此 R3 不满足“三个 Case 全部 passed”的 Gate 4.5 硬门槛。即使 Linear 对照和
Crash + HITL Case 通过，也不能把不同 Case 的成功证据拼成虚构的完整通过结果。

本轮不实施 Gate 5，不创建 Gate 5 代码，不修改 R3 raw summary，也不把 `blocked` 改判为
`partial-pass` 或 `pass`。

## 2. 本轮完成到哪里

本轮已完成并推送：

1. R3 preflight 正式结果；
2. `reviewer_execution_count == 1` 的 harness 硬断言与回归测试；
3. R3 业务预注册合同；
4. 唯一一次 R3 真实业务 session；
5. 三个真实 worker 和三个真实 reviewer 的完整预算；
6. Linear、LangGraph 低风险和 LangGraph Crash + HITL 的逐 Case 审计；
7. 本结果文档和下一轮交接边界。

本轮没有完成：

- Gate 4.5 `pass`；
- Gate 5 准入；
- parallel reviewer 实现；
- Gate 5 deterministic fake runner 验证；
- Gate 5.5 真实 reviewer 价值评估。

## 3. 执行基线与环境

```text
branch = experiment/langgraph-comparison
HEAD = private-gate-4-5-r3-business-contract-redacted
origin HEAD = private-gate-4-5-r3-business-contract-redacted
Codex CLI = 0.144.4
authentication = existing ChatGPT login
config mode = ignore_user_config
windows sandbox session override = elevated
expected provider = openai
model = sandbox-model
worker reasoning = high
reviewer reasoning = high
ephemeral = true
automatic retries = 0
```

真实运行前工作树 clean，session、fixture 和结果目录均不存在，没有复用 R0、R1、R2、
R3 preflight-only 或 fake harness 的业务 run。

## 4. 业务 Session Preflight

业务 session 内置 preflight 通过：

| 断言 | 结果 |
| --- | --- |
| Runner status | `success` |
| execution | `completed / returncode=0` |
| termination | `termination_unconfirmed=false` |
| Codex CLI | `0.144.4` |
| provider | `openai` |
| model | `sandbox-model` |
| reasoning | `high` |
| observed sandbox | `workspace-write [workdir, /tmp, $TMPDIR]` |
| sentinel | `true` |
| command shape | `true` |
| fixture clean | `true` |
| execution valid | `true` |

preflight execution 耗时：

```text
128.891 seconds
```

preflight 内部发生 stream reconnect，并回退到 HTTPS：

```text
warning: Falling back from WebSockets to HTTPS transport. request timed out
```

Vega 没有因此启动第二个 preflight attempt。业务 fixture 只在 preflight 成功后创建。

## 5. 外部调用预算

R3 累计实际使用：

```text
独立 preflight-only session = 1
业务 session preflight = 1
worker sessions = 3
reviewer sessions = 3
R3 total external sessions = 8
automatic retries = 0
provider/model switches = 0
```

业务 session 自身：

```text
preflight = 1
worker = 3
reviewer = 3
total = 7
elapsed = 1636.266 seconds
```

每个 Case 都只有 1 个 worker execution artifact 和 1 个 reviewer execution artifact。
Codex CLI 内部 reconnect 不计为新的 Vega execution。

## 6. Case 结果

| Case | 结果 | Worker | Reviewer | Verification | 终态 |
| --- | --- | --- | --- | --- | --- |
| `linear-low` | `passed` | `1 / completed` | `1 / completed` | `passed / 0` | `success / done` |
| `graph-low` | `blocked` | `1 / completed` | `1 / completed` | `passed / 0` | `needs_human / review_run_failed` |
| `graph-crash-hitl` | `passed` | `1 / completed` | `1 / completed` | `passed / 0` | `success / done` |

### 6.1 Case A：Linear 低风险

```text
run = 20260717-003603-882439-bug-loop
fixture HEAD = 026c5e6c70530b3b4698d5534e264f264aca1d3e
elapsed = 524.750 seconds
state = success / done
finish = ready_to_commit
worker starts = 1
worker executions = 1
reviewer executions = 1
verification = passed / failed=0
changed files = [src/slugify.py]
untracked files = []
decisions = 0
consumptions = 0
artifact integrity = true
evidence freshness = true
```

Case A 满足预注册成功条件。reviewer 认可实现、测试和 scope，Linear Finish 成功生成。

Runner diagnostics 仍包含：

```text
warning: Falling back from WebSockets to HTTPS transport. request timed out
Error: --codex-run-as-apply-patch requires a UTF-8 PATCH argument.
```

这些诊断没有触发第二 worker，也没有改变最终成功事实，但必须作为 provider/CLI 稳定性风险
保留。

### 6.2 Case B：LangGraph 低风险

```text
run = 20260717-004448-713669-bug-loop
fixture HEAD = 026c5e6c70530b3b4698d5534e264f264aca1d3e
elapsed = 478.141 seconds
state = needs_human / review_run_failed
finish = not_applicable_langgraph
worker starts = 1
worker executions = 1
reviewer executions = 1
verification = passed / failed=0
changed files = [src/slugify.py]
untracked files = []
decisions = 0
consumptions = 0
Graph State = valid
checkpoint manifest = valid
checkpoint SQLite = 102400 bytes
run-status = consumable
artifact integrity = false
evidence freshness = false
```

失败 issue：

```text
artifact_integrity_issues =
  iteration_01_child_review_state_not_trusted

evidence_freshness_issues =
  review_not_approved
  latest_iteration_not_approved
  iteration_01_child_review_state_not_trusted
```

真实 reviewer 返回了合法结构化结果：

```text
verdict = needs_human
severity = major
```

reviewer 的核心判断是：

> review pack 没有提供需求所引用的完整文档定义或测试源码，因此无法确认不可分解 Unicode
> 字符的预期行为，不能证明实现覆盖完整规范。

具体例子是当前实现会把 `a中b` 处理为 `a-b`，但 reviewer 无法从现有 review pack 判断
该行为应是“分隔、删除还是转写”。

这不是虚假的模型失败。worker、verification、reviewer execution、Graph State、checkpoint
和 run-status 都形成了明确终态；真正不通过的是 reviewer evidence package 不足，导致
reviewer 不批准，进而使 review child state、integrity 和 freshness 不可信。

Runner diagnostics 还包含：

```text
warning: Falling back from WebSockets to HTTPS transport. request timed out
warning: Not a git repository. Use --no-index to compare two paths outside a working tree
Error: --codex-run-as-apply-patch requires a UTF-8 PATCH argument.
```

### 6.3 Case C：LangGraph Crash + HITL

```text
run = 20260717-005246-913778-bug-loop
fixture HEAD = ed5a84cdcd9bd2427fba24b7b012bd71011efab8
elapsed = 501.485 seconds
state = success / done
finish = not_applicable_langgraph
fault triggered = true
worker starts = 1
worker executions = 1
reviewer executions = 1
verification = passed / failed=0
changed files = [src/slugify.py]
untracked files = []
pending artifacts = 1
decision ledger entries = 1
decision consumptions = 1
actor = owner-delegated-codex
Graph State = valid
checkpoint manifest = valid
checkpoint SQLite = 122880 bytes
run-status = consumable
artifact integrity = true
evidence freshness = true
```

Case C 证明在预注册的 `after_step_result_before_state` 受控 fault 下：

1. worker 没有重复启动；
2. owned worker execution 保持唯一；
3. recovery 到达结构化 HITL；
4. pending evidence 在 decision 前得到校验；
5. delegated approval 只消费一次；
6. resume 后只执行一次 reviewer；
7. 最终 Graph terminal evidence 可消费。

Case C 不证明操作系统真实 abrupt exit 风险已经关闭。

## 7. 为什么最终是 `blocked`

### 7.1 三 Case 没有全部通过

Gate 4.5 业务合同要求三个 Case 全部 `passed`。Case B 为
`needs_human / review_run_failed`，integrity 与 freshness 不可信，因此已经足够阻止
Gate 4.5 `pass` 和 Gate 5 准入。

### 7.2 Harness provider 分类器过宽

当前 `_record_has_provider_failure()` 使用以下字符串 marker：

```text
runner_error
timed_out
provider
model
codex
```

但真实 Case 的正常 diagnostics 本来就包含：

```text
provider: openai
model: sandbox-model
```

因此只要 Case 没有先进入 `passed`，该函数几乎都会把记录识别为 provider failure。
Case B 的聚合 reason 因而变成：

```text
真实 provider/runner 未形成可判断终态
```

该 reason 不能准确表达真实根因。真实根因已有明确结构化证据：

```text
reviewer verdict = needs_human
review evidence package = insufficient
review child state = not trusted
```

R3 raw summary 和 `blocked` 结论保持冻结，不做事后回写。但下一轮必须先修复 provider
failure 分类器，使用 execution status、runner reason 和明确 transport terminal status，
不能继续扫描通用 header 字样。

### 7.3 时间基准不一致

系统日期明确为：

```text
2026-07-16（星期四）
```

但本机生成的业务 run id 和本地文件时间为：

```text
20260717-*
2026-07-17 00:xx / 01:xx
```

这些日期相对系统日期处于未来。部分 raw provider 日志和 decision `created_at` 使用
`2026-07-16T...Z`，说明 evidence 同时混用了 UTC 字符串、主机本地时间和未标注时区的
run id。

本轮内部 artifact integrity/freshness 校验使用同一主机时钟，因此 Case A/C 仍返回 true；
但这不能消除跨机器复核时的墙钟歧义。下一轮必须显式记录：

- contract date；
- UTC timestamp；
- local timestamp；
- timezone/offset；
- monotonic elapsed；
- run id 使用的时钟来源。

在修复前，不得把 `20260717-*` 名称解释为当前已经是 2026-07-17，也不得只靠 run id 日期
判断 evidence freshness。

## 8. 本轮有效安全证据

尽管最终 blocked，R3 仍获得了不可与其他轮次拼接的有效证据：

1. 业务内置 preflight 再次证明真实 `workspace-write` sandbox；
2. 三个 worker start 均为 1；
3. 三个 worker execution artifact 均为 1；
4. 三个 reviewer execution artifact 均为 1；
5. 三个 Case 都只修改 `src/slugify.py`；
6. 三个 verification 均 passed 且 failed count 为 0；
7. 两个 LangGraph Case 的 Graph State、checkpoint manifest、SQLite 和 run-status 均可消费；
8. Crash + HITL Case 的 pending、decision、consumption 各为 1；
9. 没有自动 retry 或 provider/model switch；
10. 所有 owned execution 都是明确 `completed / returncode=0 /
    termination_unconfirmed=false`。

这些证据只能说明对应子链路成立，不能替代 Gate 4.5 整体通过。

## 9. 尚未关闭的风险

### 9.1 Reviewer evidence package

review pack 没有稳定提供 reviewer 判断完整需求所需的 README、测试源码或规范化需求摘要。
这会让相同 fixture 在不同 reviewer run 中因“可见证据不足”产生不一致结论。

### 9.2 Provider failure 分类

通用 `provider/model/codex` header 会污染失败分类。当前 summary 的 `blocked` 总类可保留，
但具体 reason 不够可信。

### 9.3 Transport 稳定性

preflight、worker 和 reviewer 多次出现 WebSocket reconnect 与 HTTPS fallback。Vega 没有
重复调用，但真实延迟和 provider 终态分类仍需结构化。

### 9.4 Codex apply-patch 诊断

三个业务 Case 都出现：

```text
Error: --codex-run-as-apply-patch requires a UTF-8 PATCH argument.
```

最终 worker 仍完成正确修改，但该工具调用错误应在后续 runner 诊断中单独分类，不能与
provider failure 混在一起。

### 9.5 Windows abrupt exit

Case C 是受控 Python fault injection，不是操作系统真实硬退出。SQLite 主文件、WAL、
journal 与 manifest 的 Windows abrupt-exit 风险仍未关闭。

### 9.6 未来时间戳

当前日期是 2026-07-16，raw artifact 却使用 2026-07-17 本地日期。该问题必须在新的真实
实验前校准和预注册。

## 10. Gate 5 状态

```text
Gate 4.5 = blocked
Gate 5 = not approved
Gate 5 implementation = not started
Gate 5 real calls = 0
```

Gate 5 的静态设计方向已经明确，但本轮不得进入实现：

```text
single reviewer 默认路径保持不变
parallel reviewer 仅作为 LangGraph opt-in
三路 reviewer 共享同一冻结 evidence package
隔离 execution / prompt / checkpoint
identity-map reducer
deterministic aggregator
timeout/provider error fail-closed
```

只有新的 Gate 4.5 轮次明确 `pass`，才允许冻结 Gate 5 预注册并开始编码。

## 11. 明天下一台机器的执行顺序

### 第一步：拉取并核对

```powershell
git switch experiment/langgraph-comparison
git pull --ff-only origin experiment/langgraph-comparison
git status --short --branch
git log -5 --oneline
```

确认工作树 clean，先阅读：

```text
docs/experiments/langgraph-orchestration/GATE-4.5-R3-PREFLIGHT-RESULT.md
docs/experiments/langgraph-orchestration/GATE-4.5-R3-BUSINESS-PRE-REGISTRATION.md
docs/experiments/langgraph-orchestration/GATE-4.5-R3-DOGFOOD-RESULT.md
```

raw evidence 默认不会通过 Git 同步。不要因为另一台机器没有 `.local-validation/`、`.tmp/`
或 `runs/` 就重跑 R3；Git 中的结果文档是交接入口，原机器 raw evidence 保持原位。

### 第二步：只做确定性修复，不启动真实模型

优先顺序：

1. 把 provider failure 判定改为结构化 execution/terminal status，不匹配通用 header；
2. 为 successful transport fallback、真实 provider failure、reviewer `needs_human` 分别加
   回归测试；
3. 扩充 review evidence package，至少绑定 README 需求、测试源码或规范化验收摘要及哈希；
4. 修复 reviewer 中 `Not a git repository` 的上下文来源；
5. 为时间证据增加 UTC、local、offset、monotonic 和 clock-source 字段；
6. 增加未来时间戳和跨时区恢复测试；
7. 保持 single reviewer、Linear 和现有 LangGraph 路径回归通过。

### 第三步：重新验证

按项目规则分片，每个 pytest 分片不超过 60 秒：

```text
Gate 4.5 harness
review evidence / reviewer runtime
artifact integrity / freshness
LangGraph checkpoint / recovery
Linear-LangGraph parity
core suite
ruff
compileall
git diff --check
```

### 第四步：冻结 R4，而不是重写 R3

如果确定性修复全部通过：

1. 新增独立 R4 预注册合同；
2. 使用新 session、新 fixture、新 run 和新外部预算；
3. 不复用 R3 raw success 作为 R4 的某个 Case；
4. 不修改 R3 `blocked` 结论；
5. 只有 R4 三个 Case 全部 passed，才进入 Gate 5。

## 12. 本轮验证

在真实业务调用前，本轮新增的 reviewer 单次执行硬门槛已取得：

```text
Gate 4.5 harness = 34 passed
Ruff targeted = passed
git diff --check = passed
```

真实业务 session：

```text
preflight = passed
linear-low = passed
graph-low = blocked
graph-crash-hitl = passed
overall = blocked
```

真实业务结束后：

```text
Gate 4.5 execution process count = 0
Git worktree = clean
local HEAD = origin HEAD
raw evidence = ignored
```

## 13. 证据索引

Canonical summary：

```text
.local-validation/gate-4.5/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted/summary.json
.local-validation/gate-4.5/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted/REPORT.md
```

Preflight：

```text
.local-validation/gate-4.5/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted/preflight/execution/process-output.txt
```

Business runs：

```text
runs/20260717-003603-882439-bug-loop/
runs/20260717-004448-713669-bug-loop/
runs/20260717-005246-913778-bug-loop/
```

Fixture：

```text
.tmp/langgraph-fixtures/gate-4.5/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted/
```

Launch logs：

```text
.local-validation/gate-4.5/_launch/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted.stdout.log
.local-validation/gate-4.5/_launch/real-core-r3-business-20260716-private-gate-4-5-r3-business-contract-redacted.stderr.log
```

launch stdout 发生终端编码乱码，不能作为 canonical 证据。`summary.json`、`REPORT.md` 和各
execution artifact 均为可读 UTF-8，最终结论以这些文件为准。

以上 raw evidence、fixture、run、SQLite 和日志均保持 Git 忽略。Git 只提交本结果文档。
