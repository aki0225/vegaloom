# Gate 4.5 Core Dogfood R4 结果

> 最终分类：`blocked`
>
> Gate 5：`不进入`
>
> 日期：`2026-07-17（星期五）`
>
> 执行基线：`private-gate-4-5-r4-preregistration-redacted`
>
> 真实 session：`real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted`
>
> R3 历史结论：`blocked`，保持冻结

---

## 1. 最终结论

R4 严格按照预注册合同启动了唯一一次完整业务 session，但在内置 fail-fast preflight 阶段
形成明确失败终态：

```text
preflight = blocked
business case count = 0
linear-low = not created
graph-low = not created
graph-crash-hitl = not created
Gate 4.5 = blocked
Gate 5 = 不进入
```

本轮没有自动重试，没有切换 provider、model、reasoning 或 sandbox，也没有复用 R3 的任何
成功子证据。按照 R4 合同，preflight 失败后必须停止，因此本轮没有获得新的真实 worker、
reviewer、verification、HITL 或 LangGraph checkpoint 业务证据。

R4 不是 LangGraph 业务安全失败。最窄、可复核的结论是：

> R4 冻结的认证与 provider 前提已经发生环境漂移；`--ignore-user-config` 命令仍然使用当前
> `CODEX_HOME` 的 API key 认证，但不再加载当前可用的自定义 provider 配置，最终向内置
> OpenAI endpoint 发起请求并得到 `401 Unauthorized`。

因此不能重跑同一个 R4 session，也不能把该外部身份阻塞改判为 `partial-pass` 或 `pass`。

## 2. 执行基线与预算

```text
branch = experiment/langgraph-comparison
HEAD = private-gate-4-5-r4-preregistration-redacted
origin HEAD = private-gate-4-5-r4-preregistration-redacted
Git worktree before run = clean
Codex CLI = 0.144.5
Python = 3.14.3
langgraph = 1.2.9
langgraph-checkpoint-sqlite = 3.1.0
model = sandbox-model
worker reasoning = high
reviewer reasoning = high
config mode = ignore_user_config
windows sandbox session override = elevated
automatic retries = 0
```

实际外部调用预算：

```text
preflight sessions = 1
worker sessions = 0
reviewer sessions = 0
total external sessions = 1
provider/model switches = 0
elapsed = 125.712 seconds
```

## 3. Preflight 事实

### 3.1 命令与声明身份

结构化证据确认命令包含：

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

固定声明身份为：

```text
expected provider = openai
expected model = sandbox-model
expected Codex CLI = 0.144.5
expected reasoning = high
expected sandbox = workspace-write
```

### 3.2 Live header

真实 Codex header 与命令声明中的以下字段一致：

```text
Codex CLI = 0.144.5
provider = openai
model = sandbox-model
reasoning effort = high
sandbox = workspace-write [workdir, /tmp, $TMPDIR]
command shape valid = true
```

这说明 R3 后新增的 Windows sandbox override 仍然生效，R4 并不是再次被 read-only sandbox
阻断。

### 3.3 Runner 终态

唯一 preflight execution：

```text
step = provider-preflight
replay_class = external_non_replayable
status = failed
returncode = 1
termination_unconfirmed = false
sentinel_found = false
execution_valid = false
```

Runner 先发生 WebSocket reconnect 和 HTTPS fallback，随后得到明确的 `401 Unauthorized`。
错误正文包含 provider 自己生成的 credential-like 掩码片段；本文档不复制该片段、request id
或完整 endpoint error。

结构化终态已经足以判定本次调用失败，不存在 active、unknown terminal status 或未确认终止，
因此无需也不允许重试来“确认结果”。

### 3.4 Fixture 与业务副作用

```text
preflight fixture HEAD = f4f0b3c1c1d0f10127afd047a8fe8417b4cb7fa7
preflight fixture status = clean
business fixture directories = 0
new Vega business runs = 0
worker workspace writes = 0
reviewer executions = 0
```

preflight 失败后 harness 按合同停止，没有产生未知 workspace 副作用。

## 4. 认证与 Provider 漂移

R4 合同冻结时记录的是：

```text
authentication = existing ChatGPT login
config mode = ignore_user_config
expected provider = openai
```

R4 失败后使用 Codex CLI `doctor --json` 获取了脱敏诊断。没有打开 `auth.json`，没有读取或
输出 API key 值，只消费以下非秘密状态：

```text
stored auth mode = api_key
stored API key = true
stored ChatGPT tokens = false
loaded user-config provider = sandboxproxy
loaded user-config model = sandbox-model
active provider endpoint = loopback provider
```

本机 `codex exec --help` 同时明确说明：

```text
--ignore-user-config
  不加载 $CODEX_HOME/config.toml；
  auth 仍然使用 CODEX_HOME。
```

结合 live runtime，当前最强解释是：

1. R4 命令正确忽略了用户 provider 配置；
2. 认证仍来自当前 `CODEX_HOME`，但当前已经是 API key 模式，不是 R2/R3 时记录的 ChatGPT
   token 模式；
3. 忽略配置后 Codex 使用内置 `openai` provider；
4. 当前 API key 身份并不适用于该 endpoint，因此得到明确 401；
5. 当前用户配置中的 `sandboxproxy` provider 与 loopback endpoint 没有进入 R4 命令。

这是外部执行身份漂移，不应通过读取 credential store、修改全局配置或把真实错误改写为
LangGraph 业务失败来处理。

## 5. R4 暴露的工程问题

### 5.1 Blocker：合同没有验证认证模式

R4 只冻结了：

```text
expected provider
expected model
expected Codex version
config mode
```

但“existing ChatGPT login”只写在文档中，没有在启动前通过脱敏事实验证。当前认证已经变成
API key，precheck 仍然通过，直到真实请求才发现漂移。

后续 precheck 至少要绑定：

```text
auth mode
provider config mode
provider descriptor identity
Codex config fingerprint
```

不得绑定或持久化 credential value。

### 5.2 High：Provider 配置不能继续只靠 profile 或完全忽略

历史实验已经分别证明：

- 默认或 profile 模式可能发生声明 provider 与 live provider 漂移；
- 完全 `--ignore-user-config` 在 ChatGPT token 模式下曾可用；
- 当前 API key + 自定义 provider 环境中，完全忽略配置会丢失可用 endpoint。

下一轮应使用受限、强类型的显式 provider descriptor，只传递非秘密配置：

```text
provider label
base URL
wire API
auth requirement
supports_websockets
descriptor fingerprint
```

API key 仍由 Codex 自己从现有认证存储读取，Vega 不读取、不复制、不记录 key。

### 5.3 High：Credential-like 诊断脱敏不足

R4 的本地 `process-output.txt`、`summary.json` 和 `REPORT.md` 保留了 provider 返回的掩码化
API key 片段。该片段不是完整 key，但仍属于不应在汇总和报告中传播的 credential-like
诊断。

R4 raw evidence 保持冻结，不做事后回写。下一轮前必须：

1. 扩充 `redact_text()` 对 API key 错误句式的处理；
2. 确保 RunnerResult、summary、REPORT 和结构化 diagnostics 不保留 credential-like
   片段；
3. 增加 provider 401、masked key、request id 和 endpoint error 的回归测试；
4. 保留可判定的错误类别，例如 `authentication_failed / 401`，但移除 credential 内容。

## 6. 为什么最终是 `blocked`

R4 合同将以下情况预注册为 `blocked`：

- provider unavailable；
- 认证或执行身份漂移；
- preflight execution 失败；
- sentinel 缺失；
- 业务证据不足。

本轮同时满足这些条件中的多项，但没有出现：

- duplicate worker；
- unknown external effect；
- unsafe resume；
- silent workspace drift；
- verification failure 被升级为 success；
- 业务证据被错误拼接。

因此最终分类保持：

```text
Gate 4.5 = blocked
Gate 5 = 不进入
```

## 7. 下一轮准入顺序

R4 之后不得直接重跑。下一步固定为：

1. 冻结本结果文档并提交推送；
2. 修复 credential-like 诊断脱敏；
3. 为 `CodexExecOptions` 增加受限、强类型的显式 provider descriptor；
4. 将 provider descriptor 和 config fingerprint 绑定进 command、runner identity 和
   execution evidence；
5. 增加命令形态、配置冲突、脱敏和 live provider mismatch 回归测试；
6. 完成受影响测试、Ruff、compileall 和 `git diff --check`；
7. 形成新的干净实现提交；
8. 冻结独立 R5 预注册合同；
9. R5 先执行唯一一次 preflight，成功后才允许创建三个业务 Case；
10. 只有 R5 三个 Case 全部 passed，才允许进入 Gate 5。

R5 不复用 R4 的 session、fixture、execution 或成功子证据。

## 8. 证据索引

Canonical summary：

```text
.local-validation/gate-4.5/real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted/summary.json
.local-validation/gate-4.5/real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted/REPORT.md
```

Execution：

```text
.local-validation/gate-4.5/real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted/preflight/execution/process-output.txt
```

Fixture：

```text
.tmp/langgraph-fixtures/gate-4.5/real-core-r4-business-20260717-private-gate-4-5-r4-preregistration-redacted/preflight/repo/
```

关键 SHA-256：

```text
summary.json =
  e21b4a264275063f632518a44f787573fe070636e15fc5d951992dd0d6d6fe23
preflight-result.json =
  22f0b9f9b769bec013b5332f70d5bbbea4c01091eb465d059a6e801ffdae1d7b
REPORT.md =
  f0bc0c2558d8f219523db572f2fe717827825b553a7f3203e25bfec3ed4d9246
execution.json =
  2ef943fc19510dd06415d5325e896a935fb89a9ed7ee4b5373789322b0fd4031
process-output.txt =
  1e3da19e1b106014d51cc79a2bd2db24531f64e59d3b23cfac8169eb5a0e9b49
```

以上 raw evidence、fixture、doctor 输出和 launch logs 均保持 Git 忽略。Git 只提交本结果
文档。
