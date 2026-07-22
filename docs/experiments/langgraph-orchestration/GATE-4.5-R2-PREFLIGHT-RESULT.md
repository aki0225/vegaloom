# Gate 4.5 Core Dogfood R2 Preflight 结果

> 最终分类：`blocked`
>
> 日期：2026-07-16
>
> 执行基线：`private-gate-4-5-r2-preregistration-redacted`
>
> 真实 session：`real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted`
>
> session-auth 支持实现：
> `private-gate-4-5-r2-auth-preflight-redacted`
>
> 后运行解析器修复：
> `private-gate-4-5-r2-preflight-fix-redacted`
>
> R0 / R1 结论：`blocked`

---

## 1. 最终结论

R2 只执行了预注册允许的 1 次真实 preflight，没有自动重试，没有切换 provider、model、
profile 或 reasoning，也没有进入业务 Case。

本次调用证明当前 ChatGPT 登录态能够启动：

```text
Codex CLI = 0.144.4
provider = openai
model = sandbox-model
reasoning effort = high
runner status = success
execution status = completed
returncode = 0
```

但 live header 报告：

```text
command sandbox = workspace-write
observed sandbox = read-only
```

因此 R2 的正式结论是：

```text
R2 preflight = blocked
Gate 4.5 = blocked
Gate 5 = 暂不进入
business case count = 0
```

这不是 provider 或 model 不可用。真正独立成立的阻塞是：

> 当前 `--ignore-user-config` + ChatGPT 登录态的真实 Codex 会话没有按预注册命令获得
> `workspace-write` sandbox，因而不能证明后续业务 worker 具备受控写入能力。

## 2. 执行合同

真实调用使用：

```text
runner = codex-exec
executable = <codex-wrapper>
config mode = ignore_user_config
profile = none
expected provider = openai
model = sandbox-model
reasoning effort = high
ephemeral = true
requested sandbox = workspace-write
preflight only = true
preflight timeout = 180 seconds
business call budget = 0
```

命令包含：

```text
codex exec
--cd <isolated-preflight-repo>
--sandbox workspace-write
--ignore-user-config
--model sandbox-model
--config model_reasoning_effort="high"
--ephemeral
-
```

命令不包含 `--profile`。`command_shape_valid=true`，原始命令哈希与
`execution.command_sha256` 一致。

## 3. Preflight 事实

### 3.1 身份与终态

| 断言 | 结果 |
| --- | --- |
| Codex CLI | `0.144.4`，匹配 |
| provider | `openai`，匹配 |
| model | `sandbox-model`，匹配 |
| reasoning | `high`，匹配 |
| requested sandbox | `workspace-write` |
| observed sandbox | `read-only`，不匹配 |
| Runner status | `success` |
| execution status | `completed / returncode=0` |
| command shape | `true` |
| command hash | 匹配 |
| execution identity | 匹配 |
| fixture repo | clean |
| execution artifact | valid |

execution 总耗时为 `132.453s`，完整 harness 总耗时为 `132.531s`。

### 3.2 Transport 行为

Codex CLI 先发生 5 次 stream reconnect，随后从 WebSocket 回退到 HTTPS：

```text
warning: Falling back from WebSockets to HTTPS transport. request timed out
```

尽管 transport 发生重连和回退，最终 Runner 返回 `success`，owned execution
`returncode=0` 且 `termination_unconfirmed=false`。R2 没有把 CLI 内部 transport reconnect
计为第二个 Vega preflight session，也没有启动第二次外部 attempt。

## 4. Sentinel 观测校正

原始 `summary.json` 记录：

```text
sentinel_found = false
```

但 `process-output.txt` 实际包含：

```text
user
<prompt 中的 sentinel>
codex
VEGA_GATE_4_5_PREFLIGHT_OK
tokens used
...
VEGA_GATE_4_5_PREFLIGHT_OK
```

raw output 中共有 3 个单独 sentinel 行：

- 1 个来自 CLI 回显的用户 prompt；
- `codex` 输出角色之后存在模型 sentinel；
- CLI 末尾又输出了一次最终文本。

运行时解析器只接受 `assistant` 和 `assistant/final` 角色，没有识别 Codex CLI `0.144.4`
使用的 `codex` 角色标签，因此产生了 false negative。

后运行修复 `private-gate-4-5-r2-preflight-fix-redacted`：

- 接受 `assistant`、`assistant/final` 和 `codex` 输出角色；
- 仍拒绝只出现在 `user` prompt 回显中的 sentinel；
- Gate 4.5 harness 明确得到 `27 passed`。

本修复没有回写原始 summary，没有重新执行真实 preflight，也没有把 R2 改判为通过。即使
sentinel 正确识别，`observed sandbox=read-only` 仍足以独立得到 `blocked`。

## 5. Fail-fast 安全结果

本轮符合 preflight-only 合同：

```text
business case count = 0
business fixture count = 0
new Vega business run count = 0
worker session count = 0
reviewer session count = 0
```

session fixture 根目录下只存在：

```text
preflight/
```

没有创建：

- `linear-low`；
- `graph-low`；
- `graph-crash-hitl`；
- 新的 Graph SQLite；
- decision ledger；
- reviewer execution。

preflight fixture 前后 Git 工作区为空，命令没有产生文件副作用。

## 6. 认证与数据边界

本轮复用 Codex CLI 已有 ChatGPT 登录态：

```text
codex login status = Logged in using ChatGPT
```

本轮没有：

- 读取或打印明文 key；
- 读取 Codex credential store；
- 读取 `.env`；
- 复制 Authorization header；
- 修改用户 `config.toml`；
- 修改全局 provider、model 或 profile；
- 把 credential 写入 summary、report、execution 或 Git。

真实出站 prompt 只包含隔离 fixture 的固定 sentinel 请求，没有发送 Vega 业务源码或 diff。

## 7. 本轮证明了什么

R2 获得了以下有效证据：

1. `--ignore-user-config` 继续复用现有 ChatGPT 认证；
2. `provider=openai` 和 `model=sandbox-model` 在当前会话中可用；
3. 真实命令包含预注册 model、reasoning、ephemeral 和 requested sandbox；
4. 原始命令哈希、脱敏命令文本、Runner identity 和 execution artifact 可以一致绑定；
5. provider transport 重连后仍能获得成功 execution 终态；
6. preflight-only 不创建业务 fixture 或业务 run；
7. explicit `workspace-write` 可能被当前真实会话降为 `read-only`，live header 可以发现该漂移；
8. Codex CLI `0.144.4` 的模型输出角色可能是 `codex`，不能只解析 `assistant`。

## 8. 本轮没有证明什么

R2 仍未获得：

- 真实 worker 修改代码的能力；
- Linear 与 LangGraph 的真实业务成功语义；
- 真实 verification；
- workspace 写入后的 crash recovery；
- 真实 HITL approval 与 consumption；
- 真实 reviewer 质量；
- Gate 5 reviewer fan-out 的进入依据。

provider 和 model 可用不等于 coding worker 可用。`read-only` 会话只能证明只读模型调用，
不能替代需要修改 fixture 的业务 worker。

## 9. 已知独立残余风险

`test_abrupt_process_exit_keeps_resumable_checkpoint_without_finally` 在 R2 支持工作树和未修改的
`HEAD=private-gate-4-5-r2-hitl-hardening-redacted` 归档副本中都复现了 Windows 硬退出后的
SQLite journal / manifest 一致性失败。

该失败不是本次 session-auth diff 引入，但业务 Case 启动前仍必须单独处理或接受其阻塞
含义。R2 preflight-only 没有执行 LangGraph crash recovery，因此不能关闭这项风险。

## 10. 后续决策

当前不得自动创建 R3 或启动业务 Case。下一轮真实调用前，项目 owner 需要重新冻结：

1. 为什么显式 `--sandbox workspace-write` 在 live header 中成为 `read-only`；
2. 是否存在一个不修改全局配置、且 live header 确实为 `workspace-write` 的受控执行环境；
3. 是否先修复或重新校准 Windows abrupt-exit checkpoint 风险；
4. 新的外部调用预算和 stop 条件。

禁止：

- 直接把 preflight sandbox 合同放宽为 `read-only` 后继续业务 worker；
- 静默修改用户 Codex 配置；
- 读取 credential store 后修补 provider；
- 自动重试当前 session；
- 把 R2 provider/model 成功包装成 Gate 4.5 通过。

## 11. 证据索引

```text
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/summary.json
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/REPORT.md
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/preflight/execution/process-output.txt
.tmp/langgraph-fixtures/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/preflight/repo/
```

以上 raw evidence 保持在 Git 忽略目录。Git 只提交本结果文档和后运行解析器修复。
