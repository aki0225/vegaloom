# Gate 4.5 Core Dogfood R1 重跑结果

> 最终分类：`blocked`
>
> 日期：2026-07-16
>
> 执行基线：`private-gate-4-5-r1-rerun-baseline-redacted`
>
> 真实 session：`real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted`
>
> R0 结果：`blocked`

---

## 1. 最终结论

R1 没有解除 Gate 4.5 的真实 Runner 阻塞，也没有进入任何业务 Case。

fail-fast preflight 使用预注册的完整命令形态：

```text
runner profile = sandbox-provider
expected provider = sandbox-provider
model = gpt-5.6
Codex CLI = 0.144.5
reasoning effort = high
session = ephemeral
sandbox = workspace-write
```

但 Codex live header 报告：

```text
provider = sandboxproxy
model = gpt-5.6
result = HTTP 404
cause = Model "gpt-5.6" is not supported by any configured account in this group
```

因此 R1 的正式结论是：

```text
R1 = blocked
Gate 4.5 = blocked
Gate 5 = 暂不进入
Gate 3/4 deterministic 结论 = 保留
```

这不是 LangGraph 业务安全失败，也不是 R1 harness 漏传 profile。真实 provider identity 与
预注册身份不一致，且模型调用没有形成成功终态，无法继续获取 worker、verification、HITL
和 reviewer 的真实证据。

## 2. Preflight 事实

### 2.1 命令确实显式绑定 profile

`execution.json` 记录的命令包含：

```text
codex exec
--cd <isolated-preflight-repo>
--sandbox workspace-write
--profile sandbox-provider
--model gpt-5.6
--config model_reasoning_effort="high"
--ephemeral
-
```

harness 的 `command_shape_valid=true`。本机 `codex exec --help` 也明确把
`--profile <CONFIG_PROFILE_V2>` 列为 `exec` 支持的参数，因此没有证据支持“profile 因参数
位置错误而被 CLI 忽略”。

能确定的最窄事实是：

> 在这次真实 `codex exec` 调用中，显式 `--profile sandbox-provider` 没有产生预期的
> `provider: sandbox-provider` live identity。

R1 不读取用户 Codex 配置、credential store 或认证文件，因此不进一步猜测这是 profile
内容漂移、配置层优先级、代理映射还是其他本地环境原因。

### 2.2 Live Runtime 优先于声明身份

`execution.json.runner_identity` 记录的是预注册期望：

```text
profile = sandbox-provider
provider = sandbox-provider
```

`process-output.txt` 是实际启动进程的 live header：

```text
OpenAI Codex v0.144.5
model: gpt-5.6
provider: sandboxproxy
sandbox: workspace-write [...]
reasoning effort: high
```

按照实验的证据优先级，live runtime 高于预期配置和声明字段，所以必须按
`provider=sandboxproxy` 分类，不能用 `runner_identity` 覆盖实际观察值。

### 2.3 执行终态

preflight 只启动了一次 owned Codex execution：

```text
step = provider-preflight
replay_class = external_non_replayable
status = failed
returncode = 1
termination_unconfirmed = false
```

Codex CLI 在同一个进程中执行了 5 次 transport reconnect。这不构成第二个 Vega preflight
session、第二个 external attempt 或第二个业务 worker。

execution artifact 存在，run/step/命令/runner identity/base HEAD 可以解析，但其终态是
`failed`，因此 `execution_valid=false` 是“不能作为成功 preflight 证据”，不是 artifact
缺失或 schema 损坏。

## 3. Fail-fast 安全结果

R1 preflight 在 37.157 秒后停止，符合预注册合同：

| 断言 | 结果 |
| --- | --- |
| Codex CLI 版本 | `0.144.5`，匹配 |
| command shape | 匹配 |
| model | `gpt-5.6`，匹配 |
| reasoning | `high`，匹配 |
| sandbox | `workspace-write`，匹配 |
| provider | `sandboxproxy`，与 `sandbox-provider` 不匹配 |
| sentinel | 缺失 |
| execution 成功终态 | 不成立，`failed / returncode=1` |
| preflight repo clean | 成立 |
| termination confirmed | 成立 |

fail-fast 后：

```text
business fixture count = 0
business case count = 0
new Vega *-loop run count = 0
worker session count = 0
reviewer session count = 0
```

没有创建：

- `linear-low`；
- `graph-low`；
- `graph-crash-hitl`；
- 新的 Graph SQLite；
- decision ledger；
- reviewer execution。

没有自动重试 preflight，没有切换 profile/provider/model，也没有把 R0 与 R1 结果合并。

## 4. R1 证明了什么

R1 获得了以下有效证据：

1. harness 可以在业务 run 前使用与 worker 同源的 `CodexExecRunner` 做真实可用性检查；
2. profile、model、reasoning、ephemeral 和 sandbox 已进入真实命令；
3. live provider identity 可以从非敏感 Codex header 中提取并与预期比较；
4. provider mismatch、runner error、sentinel 缺失和失败 execution 终态会稳定得到
   `blocked`；
5. preflight 失败不会创建业务 fixture 或 Vega loop run；
6. preflight fixture 没有工作区副作用；
7. R0 中“先启动三个 worker 才发现 provider 不可用”的实验浪费已经消除。

## 5. R1 没有证明什么

R1 仍未获得：

- 真实 worker 的代码生成质量；
- Linear 与 LangGraph 的真实成功语义对照；
- 真实 verification；
- worker 修改 workspace 后的真实 crash recovery；
- 真实 HITL pending、approval 和 consumption；
- 真实 reviewer 质量；
- Gate 5 并行 reviewer 的进入依据。

因此不能把 fake harness、Gate 3/4 deterministic 测试或 R0 的安全停止证据包装成真实
Gate 4.5 通过。

## 6. 基线验证

R1 执行前完成：

- 受影响 harness 回归：`14 passed`；
- fake Core Dogfood：三个 Case 全部 `passed`；
- 额外慢测试 node 分片：`37 / 37 passed`；
- 全仓测试收集：`458 tests collected`；
- `python -m compileall src scripts`；
- `ruff check src tests scripts`；
- `git diff --check`；
- UTF-8 无 BOM、无 Tab、无尾随空格检查；
- HEAD 与远端基线一致、Git 工作区 clean。

本轮没有修改 `src/` Runtime。完整 Runtime 测试在 R0 结果提交前已通过 447 个 node；R1
只对新增 harness、配置传递和 fail-fast 行为做了受影响范围回归，没有声称本次重新执行了
全部 458 个测试。

## 7. 后续决策

当前不应自动创建 R2。解除阻塞需要项目 owner 先明确选择以下一种新合同：

1. 提供并确认一个真实 `codex exec --profile <name> --model gpt-5.6` 调用中，live header
   确实报告预期 provider 且模型可用的 profile；
2. 单独评审是否为 `CodexExecOptions` 增加受限、强类型的 provider 绑定能力，再冻结新的
   R2 合同；
3. 如果当前环境没有任何可验证的 `gpt-5.6` provider，则接受外部环境阻塞，暂停真实
   Gate 4.5，保留 Gate 3/4 deterministic 结论。

禁止直接换模型、修改全局默认 provider、读取 credential store 后静默修补，或继续复用
本 session。

## 8. 证据索引

```text
.local-validation/gate-4.5/real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted/summary.json
.local-validation/gate-4.5/real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted/REPORT.md
.local-validation/gate-4.5/real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted/preflight/execution/process-output.txt
.tmp/langgraph-fixtures/gate-4.5/real-core-r1-20260716-private-gate-4-5-r1-rerun-baseline-redacted/preflight/repo/
```

以上 raw evidence 位于 Git 忽略目录；Git 只提交本结果文档。
