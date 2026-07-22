# Gate 4.5 Core Dogfood R3 Preflight 结果

> 最终分类：`preflight-passed`
>
> 日期：2026-07-16
>
> 执行基线：`private-gate-4-5-r3-preflight-contract-redacted`
>
> 真实 session：`real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted`
>
> Windows sandbox 修复：
> `private-gate-4-5-r3-windows-recovery-fix-redacted`
>
> R2 历史结论：`blocked`

---

## 1. 最终结论

R3 按预注册合同只执行了 1 次真实 preflight，没有自动重试，没有切换 provider、model、
reasoning、profile 或 sandbox，也没有创建业务 Case、worker 或 reviewer session。

本次 preflight 的全部硬断言通过：

```text
R3 preflight = preflight-passed
Gate 4.5 = 尚未判定
Gate 5 = 尚未进入
business case count = 0
worker session count = 0
reviewer session count = 0
automatic retry count = 0
```

`preflight-passed` 只证明 R3 的真实 Codex 执行身份、sandbox、命令形态和 execution 证据
符合合同，不等于 Gate 4.5 通过。业务 Case 必须在独立预注册合同提交并推送后执行。

R2 的 `blocked` 历史结论保持不变。本结果没有回写或改判 R2。

## 2. 执行合同

真实调用使用：

```text
runner = codex-exec
Codex CLI = 0.144.4
authentication = existing ChatGPT login
config mode = ignore_user_config
windows sandbox session override = elevated
expected provider = openai
model = sandbox-model
reasoning effort = high
ephemeral = true
requested sandbox = workspace-write
preflight only = true
preflight timeout = 180 seconds
```

实际命令形态为：

```text
codex exec
--cd <isolated-preflight-repo>
--sandbox workspace-write
--ignore-user-config
--config windows.sandbox="elevated"
--model sandbox-model
--config model_reasoning_effort="high"
--ephemeral
-
```

命令没有包含：

- `--profile`；
- `--full-auto`；
- `--dangerously-bypass-approvals-and-sandbox`；
- 未预注册的 `--config`；
- 其他额外 CLI 参数。

## 3. Preflight 事实

### 3.1 身份与 live header

| 断言 | 结果 |
| --- | --- |
| Codex CLI | `0.144.4`，匹配 |
| provider | `openai`，匹配 |
| model | `sandbox-model`，匹配 |
| reasoning | `high`，匹配 |
| requested sandbox | `workspace-write` |
| observed sandbox | `workspace-write [workdir, /tmp, $TMPDIR]`，匹配 |
| Runner status | `success` |
| sentinel | 已找到 |
| command shape | `true` |
| fixture repo | clean |
| execution artifact | valid |

Runner identity 为：

```text
kind = CodexExecRunner
runner = codex-exec
config_mode = ignore_user_config
ignore_user_config = true
windows_sandbox_session_override = elevated
model = sandbox-model
reasoning_effort = high
ephemeral = true
sandbox = workspace-write
provider = openai
```

### 3.2 Command 与 execution 绑定

```text
command_sha256 =
a2e92017f3a9e6d4ac6ba50c91cca1454f7db3184cce571a5a70a7cde30a4bcc

fixture HEAD =
f4f0b3c1c1d0f10127afd047a8fe8417b4cb7fa7

execution status = completed
returncode = 0
termination_unconfirmed = false
execution_valid = true
execution_issues = []
```

持久化 command、Runner identity、session、step、base HEAD 和
`execution.command_sha256` 一致。execution 没有未知终态或无法确认的子进程终止。

### 3.3 时间

```text
execution elapsed = 112.093 seconds
harness elapsed = 112.203 seconds
```

调用在预注册的 180 秒 preflight timeout 内完成。

## 4. Transport 行为

Codex CLI 运行中发生 stream reconnect，并从 WebSocket 回退到 HTTPS：

```text
warning: Falling back from WebSockets to HTTPS transport.
```

该行为没有触发第二次 Vega preflight，也没有创建新的 execution attempt。最终 execution
为 `completed / returncode=0`，Runner 为 `success`，因此只记录为 transport warning，不改变
本次通过结论。

## 5. Fail-fast 与隔离结果

本轮结果目录的业务计数为：

```text
business_case_count = 0
fixtures = {}
cases = []
```

R3 fixture session 根目录只存在：

```text
preflight/
```

没有创建：

- `linear-low`；
- `graph-low`；
- `graph-crash-hitl`；
- Vega 业务 run；
- worker execution；
- reviewer execution；
- Graph SQLite；
- decision ledger。

preflight fixture 前后 `git status --short` 为空，HEAD 保持：

```text
f4f0b3c1c1d0f10127afd047a8fe8417b4cb7fa7
```

## 6. 认证与数据边界

本轮只复用 Codex CLI 现有 ChatGPT 登录态。没有：

- 读取或打印明文 key；
- 读取 Codex credential store；
- 读取 `.env`；
- 复制 Authorization header；
- 修改用户全局 Codex 配置；
- 把凭证写入 summary、report、execution 或 Git。

真实出站 prompt 只包含隔离 fixture 的固定 sentinel 请求，没有发送 Vega 业务源码、diff、
历史模型输出或用户聊天记录。

## 7. 本轮证明了什么

R3 preflight 获得以下有效证据：

1. `--ignore-user-config` 下可以复用现有 ChatGPT 登录；
2. `windows.sandbox="elevated"` session override 使 live header 与
   `workspace-write` 合同一致；
3. `provider=openai`、`model=sandbox-model` 和 `reasoning=high` 与预注册一致；
4. 完整 argv、command hash、Runner identity 和 execution artifact 可以一致绑定；
5. provider transport fallback 后仍获得明确成功终态；
6. preflight-only 没有创建业务 fixture、业务 run、worker 或 reviewer。

## 8. 本轮没有证明什么

R3 preflight 仍未证明：

- 真实 worker 能按任务边界修改 fixture；
- Linear 与 LangGraph 的真实业务成功语义一致；
- 真实 verification 能阻止错误 success；
- crash recovery 后不会重复外部副作用；
- HITL approval 与 consumption identity 可信；
- 真实 reviewer 只执行一次且结果可消费；
- Gate 5 三路 reviewer fan-out 可以进入实施。

## 9. 已知残余风险

Windows abrupt-exit checkpoint 的 SQLite 主文件、WAL、journal 与 manifest 一致性风险仍未
关闭。R3 preflight 没有创建 LangGraph 业务 run，因此不能为该风险提供新证据。

后续业务 Case 中只要出现 checkpoint、WAL、journal 或 manifest 不一致：

```text
Gate 4.5 = fail
Gate 5 = 不进入
```

不得把 Case C 的受控 fault injection 当成真实硬退出风险已经关闭。

## 10. 后续准入

业务 Case 启动前必须：

1. 在 harness 中硬断言每个 Case 的 `reviewer_execution_count == 1`；
2. 运行并通过对应 targeted tests；
3. 冻结独立的 R3 业务预注册合同；
4. 提交并推送 harness 与业务合同；
5. 使用全新 session 执行固定的三个业务 Case；
6. 不自动重试，不切换 provider、model、reasoning 或 sandbox。

只有三个业务 Case 全部通过，并满足所有安全不变量，才能把 Gate 4.5 判为 `pass` 并进入
Gate 5。

## 11. 证据索引

```text
.local-validation/gate-4.5/real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted/summary.json
.local-validation/gate-4.5/real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted/REPORT.md
.local-validation/gate-4.5/real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted/preflight/execution/process-output.txt
.tmp/langgraph-fixtures/gate-4.5/real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted/preflight/repo/
```

以上 raw evidence 保持在 Git 忽略目录。Git 只提交本结果文档。
