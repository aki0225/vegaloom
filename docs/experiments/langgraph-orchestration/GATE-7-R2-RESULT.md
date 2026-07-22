# Gate 7 R2 真实执行结果

> 状态：`failed`
>
> 日期：`2026-07-19（星期日）`
>
> 分支：`experiment/langgraph-comparison`
>
> baseline commit：`private-gate-7-r2-baseline-redacted`
>
> 真实 provider 调用：`1 session`

---

## 1. 结论

Gate 7A R2 已真实启动，但在 CP01 失败；因此不能声称大任务协议成功，也不能启动 Gate 7C。

```text
Gate 7A = failed
Gate 7C = not-triggered
最终结论 = failed
```

这不是可安全重试的 blocked：A 的 consumed tag 已创建并推送，真实 worker 已经消耗
`34,086` tokens。按照预注册合同，不能删除 consumed tag、重跑 A、换 provider、换模型、
换 reasoning 或复用 session。

## 2. 冻结身份

```text
baseline commit = private-gate-7-r2-baseline-redacted
case = eval/gate-7/flask-teardown-case-r2.json
case SHA-256 = b618a8e1db2e0ea2fbfdc3b7c0c42c6a5270eca872b2ede186aae189c80b5acb
plan SHA-256 = 1cfe5b9ae1080b015ecc8050a15515c41879861e4f5275e4ac7b30204d26268b
A pre-run = gate-7a-pre-run-r2-v1
A consumed = gate-7a-consumed-r2-v1
C pre-run = gate-7c-langgraph-pre-run-r2-v1
C consumed = not created
A session = gate7a-flask-5928-real-r2-v1
C session = gate7c-flask-5928-real-r2-v1
```

v1 case、v1 tags、v1 consumed 状态和 v1 失败现场保持不变。

## 3. 真实执行轨迹

```text
arm_started
-> authority_claimed
-> CP01 checkpoint_started
-> CP01 checkpoint_failed
-> terminal failed
```

CP02、CP03、planned migration、Machine F、最终 tree/diff 复验均未发生。

真实 worker 命令已经记录并通过本轮新增的命令门禁：

```text
--disable
multi_agent
```

因此本次失败不是 nested collab 再次触发。

## 4. 失败根因

CP01 的 Codex exec 返回码为 `1`，现场同时记录了两类阻断：

1. **control clone 所有权不匹配**

   control clone 由 `BUILTIN/Administrators` 所有，但 Codex worker 运行用户为
   `<windows-sandbox-host>/CodexSandboxOffline`。Git 因 `dubious ownership` 拒绝读取仓库，
   `rg` 也无法读取预期测试路径。这是本机执行身份与 clone 所有权的环境问题。

2. **provider 上游 502**

   Sandbox Proxy 对 `/v1/responses` 的两次转发都返回 `502 Bad Gateway`，上游为
   `https://provider.example.invalid/v1/responses`。本轮 request/stream retry 配置均为 `0`，
   没有自动重试。

这两个原因都由 `machine-e/executions/cp01/process-output.txt` 的封存证据支持。

## 5. 已证明与未证明

已证明：

- R2 case overlay、canonical case hash 和完整 plan hash 可加载并冻结；
- A/C pre-run tags 指向同一 baseline；
- R2 fake linear 和 fake LangGraph 双臂完成三 checkpoint；
- A/C prompt hash 在三个 checkpoint 上一致；
- 真实 worker 命令可机器判定为禁用 `multi_agent`；
- consumed 后失败会落入 terminal state，且不会自动重试；
- A 失败时 C 不会被触发。

未证明：

- 真实 provider 下 CP01 能否完成；
- 真实三 checkpoint 接力；
- LangGraph 真实恢复成本或收益；
- Gate 7 的 `contract-equivalent` 或 `completed-with-overhead`；
- 真实物理换机。

## 6. 下一步

不能在本 R2 consumed 身份上继续。若要继续实验，应新建 R3 baseline，并在新 baseline
前先解决两个前置问题：

- 用与 Codex worker 相同的执行用户创建 clean control clone，或提供不改变全局权限的
  仓库级 safe-directory 方案；
- 修复并验证 Sandbox Proxy 到 `provider.example.invalid/v1/responses` 的上游转发和 TLS/网络路径。

修复后仍需新建 case/session/tag 命名空间；R2 的失败结果必须保留，不能覆盖。
