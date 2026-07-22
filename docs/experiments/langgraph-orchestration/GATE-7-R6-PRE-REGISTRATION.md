# Gate 7 R6 API-key 远端 tag 校验修复预注册合同

> 状态：`ready-for-baseline-freeze-not-frozen`
>
> 日期：`2026-07-20`
>
> 时区：`Asia/Shanghai`

## 1. R6 触发原因

R5 已按合同 terminal，且不得重试。R5 的 consumed tag 已成功推送，但旧 harness 随后
用 30 秒 `git ls-remote` 做 peel 复核时发生 `TimeoutExpired`，导致 worker 尚未启动就
终止。R5 结果见 `GATE-7-R5-RESULT.md`。

R6 使用全新身份命名空间，只修复控制面的两个确定问题：

1. 远端 Git 只读校验统一使用 120 秒上限；
2. `subprocess.TimeoutExpired` 转换为可审计的 `Gate7Blocked`，确保写出 terminal state。

R6 不删除、移动或复用任何 R4/R5 tag，不重跑 R5。

baseline 冻结前的复审进一步确认：首次 remote consumed tag 预检、annotated tag 创建、
tag push 和 push 后 peel 复核都属于同一执行权控制面。R6 因此用同一个小型 helper 收口
这些 Git 子进程的超时；tag push 超时后保持 fail-closed，不启动 worker，也不自动重试。
这不改变任务、prompt、Provider、模型、reasoning、重试预算或 Graph 语义。

同一次复审还补充 Machine E/F 子控制进程 `stdout` 与 `stderr` 的脱敏尾部，使 Python
traceback 不再被父进程静默丢失。该改动只增强失败证据，不改变成功路径。

## 2. 唯一实验问题

```text
在 R5 相同 API-key、provider、模型、reasoning、任务、checkpoint、prompt、
10 文件白名单、有界 transcript 和 retry 合同下，
修复远端 consumed tag 校验的控制面超时收口后，
Gate 7A linear + Goal/Handoff 能否真实完成 CP01 -> CP02 -> CP03？
```

R6 仍不回答 API-key 与 ChatGPT auth 的优劣，也不改变 Gate 7C 的条件触发规则。

## 3. 固定身份

```text
case = eval/gate-7/flask-teardown-case-r6.json
case SHA-256 = b8475f796f9ec8bac1c51eee9f1d30975e00c5b4e70859933f158663867b3f8d
plan SHA-256 = c0e372e5c56d6a322882ff147cee0e7e890bc4f9d20654fd18bb074f34ee8ddf
graph schema = gate7-r4-v1
transcript parser = gate7-r4-v1

Gate 7A session = gate7a-flask-5928-real-r6-v1
Gate 7C session = gate7c-flask-5928-real-r6-v1

baseline A = gate-7a-pre-run-r6-v1
consumed A = gate-7a-consumed-r6-v1
baseline C = gate-7c-langgraph-pre-run-r6-v1
consumed C = gate-7c-langgraph-consumed-r6-v1
```

任务、prompt、最终 tree 和 canonical diff 继续绑定 R5/R4 冻结值：

```text
base = 7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
canonical diff bytes = 19266
canonical diff SHA-256 =
d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b

CP01 = 4df182033096692bba758ca824a1d54042380ccfa202c9d424138f3e673e9fb3
CP02 = 51ee8cd22813b048b04794ae37d02f7964fd5f9204a79281f774ea9da02d96a8
CP03 = b2ae159146e5298edfe4e02c23715758640b00ab5124a0f7541dd7d32b9dd705
```

## 4. Provider 与停止线

```text
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
auth = api-key
Codex CLI = 0.144.5
request_max_retries = 0
stream_max_retries = 0
multi_agent = disabled
fresh sessions for Gate 7A = 3
```

R6 只允许一次真实 Gate 7A。consumed A 成功推送后，无论结果如何都不得重试。任何
case/plan/prompt/provider/CLI/auth/retry/scope/transcript/DLP 漂移都立即 terminal。
Gate 7C 只有 R6 Gate 7A success 且 transcript 全链复验通过才可触发。

## 5. Fake 双臂冻结证据

2026-07-20 完成以下不调用 Provider 的 engine-neutral 证据：

```text
fake linear = gate7-r6-fake-linear-v2
fake LangGraph = gate7-r6-fake-langgraph-v1

status parity = success
case hash parity = true
plan hash parity = true
prompt hash parity = true
checkpoint tree parity = true
final tree parity = true
canonical diff parity = true
automatic retries = 0
planned migrations = 1
Machine F target external attempts = 1
scope violations = 0
duplicate external effects = 0
canary leaks = 0
sensitive material hits = 0
provider sessions = 0
```

`gate7-r6-fake-linear-v1` 在 CP03 完成代码提交与验证后返回码为 1，但旧父进程只保存
`stdout`，没有留下 `stderr` traceback，因此该次失败不能被可靠分类，也不作为通过证据。
失败证据保持原位；R6 没有覆盖或改写它。新增双流尾部后，独立 v2 session 完整成功。
