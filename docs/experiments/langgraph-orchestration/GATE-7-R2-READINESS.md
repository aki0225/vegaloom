# Gate 7 R2 Readiness

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

R2 的 case overlay、canonical case/plan hash、人工 checkpoint 输入、A→C 条件触发、
`multi_agent` 禁用门禁、single-host dual-node 模拟和 fake 双臂证据已形成执行闭环。

当前结论只表示“允许冻结新 baseline 并启动 Gate 7A real”，不表示真实 provider 已通过，
也不表示 LangGraph 可以成为默认引擎。

```text
case contract SHA-256 = b618a8e1db2e0ea2fbfdc3b7c0c42c6a5270eca872b2ede186aae189c80b5acb
plan SHA-256 = 1cfe5b9ae1080b015ecc8050a15515c41879861e4f5275e4ac7b30204d26268b
case hash mode = canonical-json
graph schema = gate7-r2-v1
provider preflight sessions = 0
worker multi_agent = disabled-by-cli
```

## 2. Fake 双臂证据

两次 fake run 使用相同 R2 case、相同 checkpoint、相同 prompt builder 和相同验证合同，
不消耗 provider session：

```text
linear session = gate7-r2-linear-fake-v1
linear status = success
langgraph session = gate7-r2-langgraph-fake-v1
langgraph status = success
provider sessions = 0 / arm
checkpoints = CP01 -> CP02 -> CP03
prompt SHA parity = CP01 / CP02 / CP03 全部一致
automatic retries = 0
duplicate external effects = 0
canary leaks = 0
sensitive material hits = 0
scope violations = 0
```

fake 结果只证明本地协议和 harness 的确定性，不证明真实 provider 的质量、延迟、成本、
token 或网络稳定性。

## 3. 非推理环境检查

执行前只做不消耗模型会话的检查：

```text
DNS <redacted-provider-host> = resolved in source environment
TCP <redacted-provider-host>:443 = reachable in source environment
TCP 127.0.0.1:18080 = reachable
Codex CLI = 0.144.5
```

Windows Schannel 对上游根地址的直接 TLS HEAD 仍报告 handshake failure；该检查不等同于
loopback provider 的 Responses 请求失败，因此不额外调用 provider 探针。真实 CP01 将是
唯一的最终 provider 可用性判定；若失败，必须记录 terminal `failed` 或 `blocked`，不重试。

## 4. 冻结与触发规则

新 baseline 必须满足：

- A/C 两个 annotated pre-run tag 指向同一新提交；
- control clone 严格干净，父工作树的用户 `uv.lock` 不进入 baseline；
- R2 case overlay、harness、runner option、测试和本预注册文档已提交；
- v1 case、v1 tags、v1 consumed 状态和 v1 结果保持不变；
- consumed tag 只在即将启动第一个真实 worker 前创建并推送；
- A 未成功时，C consumed tag 不得创建。

真实执行顺序固定为：

```text
freeze baseline -> push A/C pre-run tags -> clean clone preflight
-> claim/push A consumed -> run A
-> if A success: claim/push C consumed -> run C
-> write R2 result and terminal evidence
```

## 5. 允许结论

R2 最终只能写入以下之一：

```text
contract-equivalent
completed-with-overhead
blocked
failed
```

任何结果都必须把真实 provider 证据与 fake readiness 分开陈述。
