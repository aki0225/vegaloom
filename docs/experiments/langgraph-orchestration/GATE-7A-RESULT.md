# Gate 7A Linear + Goal/Handoff 真实执行结果

> 历史状态：`Gate 7 v1`
>
> 本文件只记录 2026-07-19 的 v1 transport failure。Gate 7 最终收口应以
> [`GATE-7-R6-RESULT.md`](GATE-7-R6-RESULT.md) 和 [`DECISION.md`](DECISION.md)
> 为准。
>
> 结果状态：`failed`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> baseline commit：`private-gate-7-v1-baseline-redacted`
>
> baseline tag：`gate-7a-pre-run-v1`
>
> consumed tag：`gate-7a-consumed-v1`
>
> session：`gate7a-flask-5928-real-v1`

---

## 1. 结论

Gate 7A 已经进入真实 provider 阶段，但在 `CP01` 首个 worker session 中因 provider
transport failure 终止。

```text
terminal status = failed
checkpoint started = CP01
checkpoint completed = none
checkpoint failed = CP01
provider sessions used = 1
automatic retry events = 0
Gate 7C trigger = not satisfied
```

本结果不能证明：

- linear + Goal/Handoff 已完成大任务；
- 三 checkpoint 的真实接力合同已通过；
- Machine F 恢复成功；
- LangGraph 有或没有增量价值。

---

## 2. 冻结身份

```text
case SHA-256 =
9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9

plan SHA-256 =
ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f

baseline commit =
private-gate-7-v1-baseline-redacted

baseline tag =
gate-7a-pre-run-v1

consumed tag peeled commit =
private-gate-7-v1-baseline-redacted
```

`gate-7a-consumed-v1` 已创建并推送，因此本 baseline 的 Gate 7A 真实预算已经消费，
不得删除 tag 后重试。

---

## 3. 执行时间线

本地时间：

```text
2026-07-19 19:03:35  coordinator arm_started
2026-07-19 19:03:59  authority claim accepted
2026-07-19 19:04:05  CP01 provider execution started
2026-07-19 19:04:39  CP01 provider execution failed
2026-07-19 19:04:40  Gate 7A terminal state written
```

控制流程在创建 consumed tag 前已经完成：

- strict clean LF control clone；
- frozen case / plan hash 校验；
- Codex CLI `0.144.5` 校验；
- ChatGPT auth 校验；
- loopback provider endpoint TCP 可达性校验；
- Flask fixture clone 与 dependency sync；
- base suite `494 passed`；
- shared authority claim 与 duplicate claim rejection。

---

## 4. Provider 证据

```text
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning effort = high
request_max_retries = 0
stream_max_retries = 0
runner returncode = 1
termination_unconfirmed = false
tokens used = 11,965
```

输出中出现两类错误：

1. `collab spawn failed: no thread with id`
2. Sandbox Proxy 对 `/responses` 返回 `502 Bad Gateway`

Sandbox Proxy 报告的上游失败是：

```text
Provider: Sandbox Provider
model: sandbox-model
cause: 转发失败，连接 https://provider.example.invalid/v1/responses 失败
```

机器输出中 `502 Bad Gateway` 出现 `2` 次。由于 Codex client 的 request / stream retry
配置均为 `0`，且 event ledger 中 retry event 为 `0`，harness 没有发起自动重试。

这里不能进一步断言上游内部请求次数；能够确认的是只启动了一个 CP01 provider session。

---

## 5. 状态与安全边界

CP01 失败后：

- Flask worker repo tracked / untracked status 为空；
- 没有任何 checkpoint 文件变更；
- 没有 checkpoint commit 或 checkpoint ref；
- 没有 handoff bundle；
- 没有 Machine E result；
- 没有 Gate 7A success summary；
- CP02、CP03、Machine F 都没有启动；
- source bare remote 不包含 oracle merge commit；
- source bare remote 只有 base 与 authority claim 两类 ref。

事件链：

```text
coordinator events = 2
machine-e events = 2
coordinator last hash =
b96c72e2fd64c490c14eba4aa3f330b10946f2f3c707b2e2b54ff2c6902d6ece
machine-e last hash =
a523a08b0c2e7d4e0c8d22298e1b7a7981b21586c65b02c4024d3042d4a58034
```

两条 event hash chain 均已重新验证。

DLP / canary 复核：

```text
artifact files scanned = 302
artifact files skipped = 8,534
canary hit count = 0
sensitive material hit count = 0
```

---

## 6. 结果解释

Gate 7A 证明了以下控制面事实：

- baseline branch、annotated baseline tag、remote consumed tag 可以正确绑定同一 commit；
- consumed claim 在本地 fixture、依赖和 base suite 通过后才发生；
- provider session 失败进入 terminal state；
- external non-replayable attempt 没有自动重放；
- 失败输出、execution identity、event chain 和 DLP 证据可追溯；
- 失败后没有产生 scope 外副作用或伪造 success summary。

Gate 7A 没有证明大任务协议成功。当前失败的直接原因是 provider 路由不可用，并同时暴露
nested Codex session 的 collab thread 能力不满足 worker 首个动作。

---

## 7. 后续边界

脱敏机器结果清单见 `eval/gate-7/result-v1.json`。

本 Gate 7A baseline 不得重试。

如果未来继续，应建立新的 Gate 7 R2：

- 新 baseline commit；
- 新 A/C baseline tags；
- 新 consumed tags；
- 新 session 名；
- 在真实预算 claim 前增加 Sandbox Proxy 上游路由健康证据；
- 明确 nested Codex worker 是否允许 collab，或移除与实际工具能力不一致的并行要求；
- 保持旧 Gate 7 v1 结果与 tags 不变。
