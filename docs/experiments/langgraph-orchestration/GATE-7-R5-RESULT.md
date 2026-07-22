# Gate 7 R5 API-key 真实执行结果

> 结果：`failed-before-worker-no-provider-session`
>
> 执行日期：`2026-07-20`
>
> 时区：`Asia/Shanghai`
>
> R5 baseline：`private-gate-7-r5-auth-contract-redacted`

## 1. 结论

R5 没有得到真实 Provider 或 Flask 任务结果。

Gate 7A 在 consumed tag 成功推送后、Machine E worker 启动前终止。按照预注册合同：

- R5 consumed tag 不得删除、移动或复用；
- 不得再次启动 R5 Gate 7A；
- Gate 7C 不允许触发；
- R4 的 baseline、失败证据和 tag 不受影响。

## 2. 固定身份

```text
case SHA-256 = 6b3541059cc6a2a8375424d303cb5a48b79b4b305d3dc6599f3b42b72330eaae
plan SHA-256 = cbda7c69e26370a05e44b4cd7691e386992befdb1f38af72e7d85892b754dba0
auth mode = api-key
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning = high
```

```text
baseline A = gate-7a-pre-run-r5-v1
consumed A = gate-7a-consumed-r5-v1
baseline A peel =
private-gate-7-r5-auth-contract-redacted
consumed A local/remote peel =
private-gate-7-r5-auth-contract-redacted
```

## 3. 失败边界

执行顺序实际为：

```text
arm_started
-> authority_claimed
-> consumed tag 本地创建
-> consumed tag 推送成功
-> remote consumed tag peel 复核超过 30 秒
-> Python subprocess.TimeoutExpired 未被 harness 收口
-> 进程终止
```

协调事件链重新读取并验证通过：

```text
event count = 2
event chain = passed
checkpoint_started = 0
arm_completed = 0
```

fixture 仍停在预注册 base：

```text
7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
```

`.local-validation/gate-7/gate7a-flask-5928-real-r5-v1/machine-e/` 不存在，
因此没有启动 worker。R5 运行输出中没有 `process-output.txt`、`transcript-audit.json`、
checkpoint payload 或 machine result。

## 4. Provider 预算结论

```text
preflight model sessions = 0
worker process started = false
provider sessions started = 0
checkpoint_started = 0
Gate 7C = blocked
```

这里的 `0` 依据运行顺序和 live artifacts 得出：`_spawn_machine` 位于
`_claim_real_execution` 之后，异常发生在 consumed tag 验证返回之前。

## 5. 本地终态证据

```text
.local-validation/gate-7/gate7a-flask-5928-real-r5-v1/
  terminal-state.json
  preflight-failure-evidence.json
  coordinator-events.jsonl
  preflight-machine-e.json
```

```text
terminal-state.json SHA-256 =
efc18365b795d857a77d6160dd7c2f8ed18161ded18763585a7eeac1f9feaf97
preflight-failure-evidence.json SHA-256 =
de9430021074a5e2a93c966bb5a69fac6108c812ffa96b47d78fa247cc8e7a63
```

这些是本机 `.local-validation/` 忽略证据，不进入 Git。

## 6. R5 暴露的控制面问题

`_claim_real_execution` 在 push 已返回成功后再次使用 30 秒 `git ls-remote` 验证
annotated tag peel。Windows/HTTPS 网络抖动使这次只读复核超时，且旧代码没有捕获
`subprocess.TimeoutExpired`，导致 harness 没有写出自身的 terminal-state。

这不是 Provider 失败，也不是 API-key 认证失败。后续修复必须：

1. 延长并集中管理远端 Git 只读超时；
2. 捕获超时并 fail-closed 写 terminal evidence；
3. 以全新 R6 baseline/consumed tag 命名空间继续，不能复用 R5。
