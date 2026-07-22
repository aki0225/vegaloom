# Gate 7 大任务双节点执行 Readiness

> 状态：`ready-for-baseline-freeze / provider not started`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 真实 provider 调用：`0`

---

## 1. Readiness 结论

Gate 7 的协议附录、Gate 7A 预注册、Gate 7C 预注册、冻结 case JSON、脚本门禁、
真实 LangGraph checkpoint orchestration、strict clean control clone 规则和 fake v3
双臂 readiness 证据已经形成闭环，可以进入 execution baseline 冻结阶段。

当前结论只表示“允许冻结并按顺序启动真实 Gate 7A / Gate 7C 流程”，不表示真实 provider
已经通过，也不表示 LangGraph 可切成默认引擎。

```text
Gate 7 protocol addendum = frozen
Gate 7A pre-registration = frozen
Gate 7C pre-registration = frozen
case JSON = frozen
case SHA-256 = 9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9
plan SHA-256 = ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f
fake v3 linear = success
fake v3 langgraph = success
linear fake session = gate7-linear-final-preflight-v3
langgraph fake session = gate7-langgraph-final-preflight-v3
real provider calls = 0
```

---

## 2. 冻结 Commitment

```text
case =
eval/gate-7/flask-teardown-case.json

case SHA-256 =
9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9

plan SHA-256 =
ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f

final tree =
a5b249e710d1253bee4c099faf91e45f9ebfbddd

canonical diff SHA-256 =
d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
```

固定身份：

```text
repo task = pallets/flask PR #5928
real_session_a = gate7a-flask-5928-real-v1
real_session_c = gate7c-flask-5928-real-v1
baseline_tag_a = gate-7a-pre-run-v1
baseline_tag_c = gate-7c-langgraph-pre-run-v1
consumed_tag_a = gate-7a-consumed-v1
consumed_tag_c = gate-7c-langgraph-consumed-v1
changed files = 10
base full suite = 494 passed
merge full suite = 495 passed
```

---

## 3. 实现与脚本边界

Gate 7 的执行脚本与 case 合同必须保持以下边界：

- `CP01`、`CP02`、`CP03` 顺序固定；
- exact 10 文件白名单固定；
- `request_max_retries = 0`；
- `stream_max_retries = 0`；
- provider preflight session 为 `0`；
- 每个真实 arm 使用 3 个 fresh provider sessions；
- fake runner 的 provider sessions 必须为 `0`；
- 真实 runner 必须检查 Codex CLI、auth、provider endpoint、baseline tag 和 consumed tag；
- 真实 runner 必须在创建 consumed tag 后才启动 provider budget；
- final tree、canonical diff SHA、DLP、canary 和 scope drift 都是硬门禁。

LangGraph arm 的实现边界：

- `CP01`、`CP02`、`CP03` 是 LangGraph 图节点；
- `CP02` 后通过 SQLite checkpointer interrupt；
- handoff 记录 `langgraph-sqlite` engine state；
- `machine-f` 用同一 checkpoint 恢复；
- `machine-f` 只执行 `CP03`；
- checkpoint manifest、database hash 和 bundle evidence 必须复验。

Linear arm 的实现边界：

- linear cursor 保存同等 checkpoint evidence；
- cursor self hash、文件 hash、bundle hash 必须复验；
- linear cursor 不得被描述为 LangGraph checkpoint。

---

## 4. Fake v3 Readiness

最终 fake v3 双臂结果：

```text
linear fake v3 = success
langgraph fake v3 = success
provider sessions = 0
prompt SHA = CP01 / CP02 / CP03 三阶段完全相同
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
diff sha256 = d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
```

引擎状态证据：

```text
Linear cursor state bytes = 4798
LangGraph CP02 state bytes = 53913
LangGraph CP02 checkpoint count = 4
LangGraph resumed state bytes = 66201
LangGraph resumed checkpoint count = 5
LangGraph resume + CP03 elapsed = 9.079 seconds
```

Fake v3 readiness 只证明以下本地合同确定性成立：

- case / plan hash 绑定；
- prompt parity；
- checkpoint 顺序；
- exact scope guard；
- expected-fail 与 pass 验证分离；
- sealed handoff；
- Linear cursor 与 LangGraph SQLite state 都可恢复；
- final tree / diff identity；
- DLP 与 canary 扫描链路；
- provider session hard limit 在 fake 下不会被消费。

Fake v3 readiness 不证明真实 provider 质量、网络、身份、token、latency、成本或 termination。

---

## 5. 全项目验收

基线冻结前已经完成：

```text
python -m compileall -q src = passed
ruff check src tests = passed
git diff --check = passed
dev + LangGraph environment collected = 812 tests
accepted disjoint shards total = 812 passed
```

完整依赖环境使用：

```text
uv run --isolated --no-project --with ".[dev,langgraph]"
```

为遵守单个 pytest 进程不超过 60 秒的规则，812 项按全局 collection 顺序做固定模分片。
任何超过 60 秒的较大分片都被继续二分，并只接受明确退出且打印 `passed` 计数的最终子分片。
超时分片没有被计入 `812 passed`。

---

## 6. 真实身份与预算

```text
Codex CLI = 0.144.5
auth = chatgpt
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
request_max_retries = 0
stream_max_retries = 0
memory = off
automatic retries = 0
provider preflight sessions = 0
fresh provider sessions per arm = 3
```

真实调用顺序：

1. 从当前准备提交创建 execution baseline；
2. 让 `gate-7a-pre-run-v1` 与 `gate-7c-langgraph-pre-run-v1` 都成为指向该提交的 annotated tag；
3. 在任何真实调用前同时推送两个 baseline tag；
4. 另建一个严格干净 control clone，从共享 baseline 启动 Gate 7A real；
5. Gate 7A 本地 fixture、依赖、base suite、CLI/auth/endpoint 检查通过后，
   创建并推送 `gate-7a-consumed-v1`，再启动第一个真实 worker；
6. Gate 7A success summary、handoff、metrics、canary、final tree/diff 全复验；
7. 同一个 strict clean control clone 保持 HEAD 不变，启动 Gate 7C real；
8. Gate 7C 前置复验与本地 preflight 通过后，创建并推送
   `gate-7c-langgraph-consumed-v1`，再启动第一个真实图节点；
9. 根据四种允许结论之一生成最终结果。

任一真实 arm 一旦创建远端 consumed tag，就不得重试、换 provider、换模型、换 reasoning 或重用 session。

---

## 7. Strict Clean Control Clone

真实运行必须从另建的严格干净 control clone 执行，不得直接从当前主工作树启动。

真实 control clone 必须满足：

- `HEAD` 与对应 baseline tag 的 commit 完全一致；
- baseline tag 是 annotated tag；
- 远端 baseline tag 与本地 tag 指向一致；
- `git status --porcelain=v1 --untracked-files=all` 为空；
- 没有未跟踪 `uv.lock`；
- 没有 `.tmp/`、`.local-validation/`、`runs/` 或其他本地验证残留；
- 没有白名单外源码改动；
- 没有未暂存、已暂存或未跟踪文件。

当前主工作树存在的未跟踪 `uv.lock` 不进入 baseline，也不是允许例外。不得删除、暂存、
回滚或覆盖该文件来制造干净状态。

---

## 8. Gate 7C 前置复验

Gate 7C 必须由 Gate 7A 的以下真实证据共同触发：

```text
remote consumed tag = gate-7a-consumed-v1
summary status = success
runner_mode = real
engine = linear
session = gate7a-flask-5928-real-v1
case SHA-256 = 9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9
plan SHA-256 = ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f
provider_sessions_used = 3
automatic_retry_count = 0
planned_migration_count = 1
canary_leak_count = 0
sensitive_material_hit_count = 0
checkpoint_count = 3
token_counts_complete = true
```

还必须复验：

- Gate 7A baseline 与 Gate 7C baseline 一致；
- Gate 7A final tree 为 `a5b249e710d1253bee4c099faf91e45f9ebfbddd`；
- Gate 7A canonical diff SHA 为 `d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b`；
- Gate 7A handoff bundle 的 self hash、case hash、plan hash 和 source ref；
- `machine-e` 为 planned migration；
- `machine-f` 为 success；
- checkpoint evidence 顺序为 `CP01 -> CP02 -> CP03`；
- source chat、memory、machine path canary 在公开 evidence 中命中数为 `0`。

---

## 9. 停止线

以下任一情况出现时，必须立即写入 terminal state：

- baseline tag 不是 annotated tag；
- control clone 不严格干净；
- 当前主工作树未跟踪 `uv.lock` 被纳入 baseline 或被声明为真实运行例外；
- provider preflight session 不为 `0`；
- request 或 stream retry 不为 `0`；
- 真实 arm provider sessions 不等于 `3`；
- 自动 retry 不为 `0`；
- Gate 7A 未成功却启动 Gate 7C；
- LangGraph 没有在 CP02 后 SQLite interrupt；
- `machine-f` 重放 CP01 或 CP02；
- prompt SHA 三阶段不一致；
- final tree 或 diff SHA 漂移；
- 白名单外路径被改动；
- source chat、memory、machine path canary 泄漏；
- Authorization bearer、API key 或其他敏感材料进入 artifact；
- 任何完整测试结果不是明确 `passed`；
- 试图把 fake v3 指标写成真实 provider 结论；
- 试图把 Gate 7C 结论直接升级为默认引擎切换。

---

## 10. 允许结论

Gate 7 最终结果只能使用以下结论之一：

```text
contract-equivalent
completed-with-overhead
blocked
failed
```

`contract-equivalent` 表示 LangGraph 在真实 provider 下与 Gate 7A 保持相同合同、相同 final identity、
相同 prompt input 和相同安全边界。

`completed-with-overhead` 表示合同成功，但 LangGraph 引入了可量化状态体积、恢复时间、依赖安装或操作复杂度。

`blocked` 表示真实执行前置条件不足、环境不干净、tag/summary/handoff/metrics/canary 复验失败或 provider
不可达。

`failed` 表示真实执行已启动后出现测试、scope、hash、DLP、checkpoint、prompt parity 或 final identity 失败。

---

## 11. 交付清单

真实流程结束后必须生成：

- Gate 7A real summary、report、machine evidence、handoff 与 metrics；
- Gate 7C real summary、report、SQLite checkpoint manifest、handoff 与 metrics；
- Gate 7 最终结果文档；
- 对 fake v3 与 real provider 结论的明确区分；
- 对是否 contract-equivalent 或 completed-with-overhead 的证据化说明；
- 对“不能直接推出默认引擎切换”的明确声明。
