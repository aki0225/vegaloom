# Gate 7C LangGraph Checkpoint Orchestration 预注册合同

> 文档状态：`frozen-before-run`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 真实任务：`pallets/flask PR #5928`
>
> 真实 provider 调用：`0`

---

## 1. 唯一问题

Gate 7C 只回答：

```text
在 Gate 7A 已经以真实 linear + Goal/Handoff 成功完成同一个冻结 case 后，
LangGraph checkpoint orchestration 是否能在相同事实、相同 checkpoint、相同停止条件下，
完成 CP01 / CP02 / CP03，并从 CP02 后的 SQLite checkpoint 只恢复执行 CP03？
```

Gate 7C 不回答：

- 是否把 LangGraph 切成默认引擎；
- 是否扩大文件白名单；
- 是否允许自动拆解替代人工计划；
- 是否允许 checkpoint 重写 case、plan、handoff 或 final identity；
- 是否用 fake 运行指标替代真实 provider 结论。

Gate 7C 的结论空间只允许以下四种：

```text
contract-equivalent
completed-with-overhead
blocked
failed
```

任何结论都不能直接推出默认引擎切换。默认引擎是否切换必须另走产品决策与风险评审。

---

## 2. 触发条件

Gate 7C 必须由 Gate 7A 的真实成功事实共同触发，缺一不可：

- 远端 annotated consumed tag `gate-7a-consumed-v1` 已存在并指向同一 baseline；
- `.local-validation/gate-7/gate7a-flask-5928-real-v1/summary.json` 是真实 `success`；
- case SHA、plan SHA、baseline SHA 与 Gate 7A summary 一致；
- Gate 7A final tree 与 canonical diff SHA 复验通过；
- Gate 7A sealed handoff bundle 可解析、自校验、与远端 refs 对账通过；
- Gate 7A metrics 满足 provider sessions、retry、migration、scope、DLP 与 checkpoint 数量要求；
- source chat、memory、machine path canary 复验命中数均为 `0`。

只有上述触发条件全部满足，才允许创建并消费 Gate 7C 的真实 provider 预算。

---

## 3. Case Identity

```text
schema_version = 1
case_id = gate7-flask-teardown-goal-handoff-v1
case_sha256 = 9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9
plan_sha256 = ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f
real_session_a = gate7a-flask-5928-real-v1
real_session_c = gate7c-flask-5928-real-v1
baseline_tag_a = gate-7a-pre-run-v1
baseline_tag_c = gate-7c-langgraph-pre-run-v1
consumed_tag_a = gate-7a-consumed-v1
consumed_tag_c = gate-7c-langgraph-consumed-v1
repo task = pallets/flask PR #5928
base = 7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
merge = c34d6e81fd8e405e6d4178bf24b364918811ef17
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
diff sha256 = d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
changed files = 10
base full suite = 494 passed
merge full suite = 495 passed
```

这些身份字段是冻结输入。Gate 7C 的 graph state、SQLite checkpoint、reviewer 输出或
handoff 恢复结果都不得改写它们。

---

## 4. 精确文件白名单

Gate 7C 继承 Gate 7A 的精确 10 文件白名单：

```text
tests/test_appctx.py
tests/test_basic.py
tests/test_blueprints.py
tests/test_helpers.py
tests/test_testing.py
src/flask/helpers.py
src/flask/app.py
src/flask/ctx.py
docs/appcontext.rst
CHANGES.rst
```

任何白名单外新增、删除、修改、暂存或提交都必须进入 terminal state。

---

## 5. LangGraph 编排合同

Gate 7C 必须是真正的 LangGraph checkpoint orchestration：

- `CP01`、`CP02`、`CP03` 是图节点，不是文档标签或线性循环伪装；
- `CP01` 和 `CP02` 由 `machine-e` 执行；
- 图在 `CP02` 后通过 SQLite checkpointer interrupt；
- sealed handoff bundle 只携带 refs、hash、engine state 与 checkpoint evidence；
- `machine-f` 从同一个 SQLite checkpoint 恢复；
- `machine-f` 只能执行 `CP03`，不得重放 `CP01` 或 `CP02`；
- `CP03` 完成后 graph state 必须停在完成态，且 next node 为空；
- Linear arm 只保存同等 checkpoint evidence 的 cursor，不得被描述成 LangGraph。

LangGraph 恢复证据必须记录：

```text
before_phase = cp02_completed
after_phase = cp03_completed
resume_external_attempts = 0
replayed_external_attempts = 0
target_external_attempts = 1
checkpoint_count_before = 4
checkpoint_count_after = 5
```

如果 `machine-f` 读取了 `machine-e` 的本地路径事实、source chat、memory ledger 或私有
canary，Gate 7C 必须失败。

---

## 6. 真实运行环境

真实 Gate 7C 必须从另建的严格干净 control clone 执行。

主工作树的未跟踪 `uv.lock`：

- 不进入 baseline；
- 不是真实运行允许例外；
- 不能作为 strict clean 检查的豁免理由；
- 不得被删除、回滚或暂存来伪造干净状态。

真实运行前必须满足：

```text
control clone = strict clean checkout from pushed baseline tag
baseline tag = gate-7c-langgraph-pre-run-v1
consumed tag = gate-7c-langgraph-consumed-v1
Codex CLI = 0.144.5
auth = chatgpt
provider = sandboxproxy
wire API = responses
model = sandbox-model
reasoning = high
request_max_retries = 0
stream_max_retries = 0
provider preflight sessions = 0
fresh provider sessions per arm = 3
```

Gate 7C 一旦创建并推送 `gate-7c-langgraph-consumed-v1`，该 arm 的真实 provider 预算即视为已消费。

---

## 7. Checkpoint 验证

Gate 7C 不改变 Gate 7A 的 checkpoint 合同。

`CP01` 必须满足：

- 只改 5 个测试文件；
- 独立 robust 节点进程 expected-fail；
- 新 pytest 进程使用 `-k "not test_robust_teardown"` 达到 `494 passed`；
- 不触碰生产文件。

`CP02` 必须满足：

- 只改 `src/flask/helpers.py` 和 `src/flask/app.py`；
- request/app teardown callbacks 与 signals 全调用；
- 错误延迟统一抛出；
- robust 节点仍 expected-fail；
- 过滤后的其余 suite 达到 `494 passed`；
- direct behavior probe pass；
- 不提前触碰 `src/flask/ctx.py`、`docs/appcontext.rst` 或 `CHANGES.rst`。

`CP03` 必须满足：

- 只改 `src/flask/ctx.py`、`docs/appcontext.rst` 和 `CHANGES.rst`；
- 独立 robust 节点 pass；
- 完整 suite 达到 `495 passed`；
- 文档与实现一致。

---

## 8. 输出与 Metrics

Gate 7C summary 必须记录：

- `status = success` 或 terminal state；
- `runner_mode = real`；
- `engine = langgraph`；
- `session = gate7c-flask-5928-real-v1`；
- case、plan、baseline、consumed tag；
- single host dual node simulation 为 `true`；
- physical machine migration proven 为 `false`；
- handoff SHA、engine state、final identity；
- machine-e / machine-f checkpoint evidence；
- event hash chain、attempt、retry、recovery、migration 字段；
- provider session 计数、token 计数、DLP 扫描计数；
- SQLite checkpoint bytes、checkpoint count、恢复耗时。

真实 success 的最低 metrics 为：

```text
execution_slots_used = 3
provider_sessions_used = 3
automatic_retry_count = 0
planned_migration_count = 1
unplanned_crash_count = 0
duplicate_external_effect_count = 0
duplicate_claim_rejected = true
canary_leak_count = 0
sensitive_material_hit_count = 0
scope_violation_count = 0
checkpoint_count = 3
token_counts_complete = true
```

三阶段 worker prompt SHA 必须完全相同，证明 Linear 与 LangGraph 没有因 engine 名称、
oracle、gold diff 或 hidden topology 泄漏而改变任务输入。

---

## 9. Fake v3 Readiness 证据边界

最终 fake v3 两臂均已成功，且 provider sessions 均为 `0`。

已知 fake v3 证据：

```text
prompt SHA = 三阶段完全相同
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
diff sha256 = d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
Linear cursor state bytes = 4798
LangGraph CP02 state bytes = 53913
LangGraph CP02 checkpoint count = 4
LangGraph resumed state bytes = 66201
LangGraph resumed checkpoint count = 5
LangGraph resume + CP03 elapsed = 9.079 seconds
```

这些 fake 指标只证明本地 harness、scope guard、DLP、final identity、prompt parity、
handoff 和 checkpoint 恢复链路可以确定性通过。

它们不证明真实 provider 的身份、网络、token 计数、延迟、质量、termination 或成本表现。

---

## 10. 结论规则

`contract-equivalent` 只允许在以下条件全部满足时给出：

- Gate 7A 触发条件复验全部通过；
- Gate 7C 真实 summary 为 success；
- final tree、canonical diff SHA、文件白名单、checkpoint 顺序与 Gate 7A 等价；
- `machine-f` 只从 CP02 SQLite checkpoint 执行 CP03；
- provider sessions、retry、DLP、canary、event hash chain 全部满足合同；
- 三阶段 prompt SHA 与 Linear arm 完全一致；
- 没有额外产品承诺被引入。

`completed-with-overhead` 只允许在合同成功但 LangGraph 明确带来额外状态体积、恢复耗时、
依赖或操作复杂度时给出。

`blocked` 用于触发条件、环境、tag、provider、strict clean clone 或前置证据不足。

`failed` 用于真实执行已启动后出现 scope drift、测试失败、hash 漂移、canary 泄漏、敏感材料泄漏、
checkpoint 重放或 final identity 不一致。
