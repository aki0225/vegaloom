# Gate 4.5 Core Dogfood R2 Preflight 预注册合同

> 文档状态：`frozen-before-r2-preflight`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> R2 session-auth 支持实现：
> `private-gate-4-5-r2-auth-preflight-redacted`
>
> R1 结论：`blocked`
>
> R2 当前范围：`preflight-only`
>
> R2 执行基线：包含本文档的首个干净提交。真实运行结果必须记录其完整 SHA。

---

## 1. 本轮决策

R2 接受项目 owner 的明确授权：

> 使用当前 Codex 会话已有的 ChatGPT 登录态完成一次真实 preflight。

本轮不读取、打印、复制或持久化任何明文 key，也不读取 Codex credential store、
`.env`、Authorization header 或认证文件。`--ignore-user-config` 只忽略用户
`config.toml`，Codex CLI 仍从自己的 `CODEX_HOME` 复用现有认证。

R2 第一阶段只回答：

1. 当前 Codex CLI 能否在不加载用户配置的情况下启动；
2. live header 是否报告预注册的 provider、model、reasoning 和 sandbox；
3. Runner 命令、原始命令哈希、执行身份和 execution artifact 是否一致；
4. preflight fixture 是否保持 clean；
5. preflight-only 是否没有创建任何业务 fixture、Vega loop run、worker 或 reviewer。

本阶段不回答模型的代码质量、LangGraph 业务恢复、HITL 或 reviewer 质量。

## 2. R2 环境合同

### 2.1 固定执行身份

```text
runner = codex-exec
Python shutil.which("codex") = <codex-wrapper>
Codex CLI = 0.144.4
authentication = existing ChatGPT login
config mode = ignore_user_config
runner profile = none
expected provider = openai
model = sandbox-model
worker reasoning effort = high
reviewer reasoning effort = high
session persistence = ephemeral
preflight sandbox = workspace-write
preflight only = true
memory = off
```

模型标识来自当前 Codex bundled model catalog。R2 不静默切换 provider、model、reasoning
或 profile，也不修改用户全局 Codex 配置。

### 2.2 配置来源

真实命令必须包含：

```text
--ignore-user-config
--model sandbox-model
--config model_reasoning_effort="high"
--ephemeral
--sandbox workspace-write
```

真实命令不得包含：

```text
--profile
```

`CodexExecOptions` 和 `CodexExecRunner.build_command()` 都必须拒绝
`profile + ignore_user_config` 冲突。execution/attempt identity 至少记录：

```text
runner
kind
config_mode
ignore_user_config
model
reasoning_effort
ephemeral
sandbox
```

所有持久化 identity、命令文本、summary 和 report 必须经过脱敏。原始命令只允许以
`command_sha256` 绑定，不得原样复制到报告。

## 3. 干净执行基线

真实 R2 preflight 前必须同时满足：

1. 当前分支为 `experiment/langgraph-comparison`；
2. Git 工作区 clean；
3. 当前 HEAD 包含 `private-gate-4-5-r2-auth-preflight-redacted`；
4. 本文档已经单独提交并推送；
5. 本地 HEAD 与 `origin/experiment/langgraph-comparison` 完整 SHA 一致；
6. 使用全新 session、fixture 和结果目录；
7. 执行期间不修改 Runtime、harness、合同或通过标准；
8. 不复用 R0、R1 或 fake harness 的 fixture、execution 或结果。

任一条件不满足，本轮不得启动外部调用。

## 4. Preflight 合同

### 4.1 同源命令

preflight 必须直接复用项目的 `CodexExecRunner`、`CodexExecOptions` 和
`ExecutionController`，不得由外部 shell 拼接近似命令。

preflight 在启动前冻结：

- 原始命令的 `command_sha256`；
- 脱敏后的 evidence command；
- Runner identity；
- fixture Git HEAD；
- session 和 step identity。

运行后必须同时验证原始命令哈希和脱敏命令文本。两者任一不一致都分类为 `blocked`。

### 4.2 隔离 fixture

preflight fixture 固定在：

```text
.tmp/langgraph-fixtures/gate-4.5/<r2-session>/preflight/repo/
```

结果固定在：

```text
.local-validation/gate-4.5/<r2-session>/
```

fixture 和 output root 不得指向项目 `runs/` 或其子目录。创建任何 session 目录前，harness
必须先记录现有业务 run 集合。

fixture 只包含最小 `AGENTS.md` 与 `README.md`。prompt 只要求输出固定 sentinel：

```text
VEGA_GATE_4_5_PREFLIGHT_OK
```

provider 不得修改、创建或删除文件，也不得执行与 sentinel 无关的命令。

### 4.3 必须同时成立的断言

preflight 只有在以下条件全部成立时才为 `passed`：

1. Runner 终态为 `success`；
2. assistant 输出包含单独一行固定 sentinel；
3. Codex header 版本为 `0.144.4`；
4. Codex header provider 为 `openai`；
5. Codex header model 为 `sandbox-model`；
6. Codex header reasoning effort 为 `high`；
7. Codex header sandbox 以 `workspace-write` 开头；
8. evidence command 与预注册脱敏命令一致；
9. `execution.command_sha256` 与预注册原始命令哈希一致；
10. execution identity 与预注册 identity 一致；
11. preflight fixture 前后 `git status --short` 为空；
12. execution artifact 为成功终态且引用、HEAD 和 session 一致；
13. 新业务 run 数量为 0；
14. 业务 Case 数量为 0；
15. 没有创建 `linear-low`、`graph-low` 或 `graph-crash-hitl` fixture。

## 5. 外部调用预算

R2 第一阶段允许的最大真实调用数：

```text
preflight sessions = 1
worker sessions = 0
reviewer sessions = 0
automatic retries = 0
provider/model switches = 0
```

timeout 固定为：

```text
preflight timeout = 180 seconds
business timeout = 900 seconds
```

`business timeout` 只作为后续合同占位，本阶段不得启动业务 Case。

provider error、timeout、unknown terminal status、identity mismatch、sentinel 缺失或任何
安全断言失败后，不得自动重试，也不得换用其他 provider、model 或 profile。

## 6. 数据出站边界

本次真实 preflight 只允许向当前 Codex 会话发送：

- 固定 sentinel 请求；
- 隔离 fixture 中的最小 `AGENTS.md` 和 `README.md`；
- Codex CLI 正常运行所需的非敏感命令参数。

不得发送：

- Vega 业务源码或 diff；
- R0/R1 原始模型输出；
- 用户聊天记录；
- `.env`、key、token、Authorization header；
- Codex credential store 内容；
- 其他仓库内容。

## 7. 结果与证据

summary schema 固定为 `3`。本轮至少写出：

```text
.local-validation/gate-4.5/<r2-session>/summary.json
.local-validation/gate-4.5/<r2-session>/preflight-result.json
.local-validation/gate-4.5/<r2-session>/REPORT.md
.local-validation/gate-4.5/<r2-session>/preflight/execution/execution.json
.local-validation/gate-4.5/<r2-session>/preflight/execution/process-output.txt
```

这些 raw evidence 位于 Git 忽略目录。Git 只提交最终结果文档，不提交认证信息、运行日志、
fixture、SQLite、`.tmp/` 或 `.local-validation/`。

### 7.1 允许的结论

```text
preflight-passed
  只表示配置身份、CLI、provider、model、命令证据和隔离断言通过。

blocked
  任一预注册断言失败，或执行基线不满足。
```

`preflight-passed` 不等于 Gate 4.5 通过。无论 preflight 结果如何，本阶段结束时：

```text
Gate 4.5 = blocked
Gate 5 = 暂不进入
business case count = 0
```

preflight 通过后，只有在项目 owner 再次明确授权最多 3 次 worker 和 3 次 reviewer 调用，
并重新确认已知残余风险后，才允许冻结业务 Case 合同。

## 8. 已知残余风险

本轮验证中，
`test_abrupt_process_exit_keeps_resumable_checkpoint_without_finally` 在当前工作树与未修改的
`HEAD=private-gate-4-5-r2-hitl-hardening-redacted` 归档副本中都复现了 Windows 硬退出后的
SQLite journal / manifest 一致性失败。

当前证据说明：

- 该失败不是 R2 session-auth diff 引入；
- 其他 7 个 crash window node 明确 passed；
- 本阶段 preflight-only 不创建业务 run，也不执行 LangGraph crash recovery；
- 该风险不得因 preflight 通过而关闭；
- 启动业务 Case 前必须单独决定是否修复、重新校准环境，或将其作为阻塞条件。

本合同不修改 Gate 3 历史结果，也不把当前基线失败包装成通过。

## 9. 支持实现准入证据

在冻结本文档前，`private-gate-4-5-r2-auth-preflight-redacted` 已取得：

```text
Gate 4.5 harness = 26 passed
execution control safety = 12 passed
Codex Runner / config affected smoke = 6 passed
step result manifest = 5 passed
other crash window nodes = 7 passed
compileall = passed
Ruff = passed
git diff --check = passed
independent review = no open Blocker / High / Medium
```

一次 `tests/test_smoke.py` 整文件组合运行超过外层 60 秒限制，未计为通过；受影响的 6 个
node 已使用独立 basetemp 明确通过。

## 10. 预注册命令

将 `<baseline-short-sha>` 替换为包含本文档并已推送的执行基线短 SHA：

```powershell
.tmp\langgraph-validation-venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner real `
  --ignore-user-config `
  --preflight-only `
  --session real-core-r2-preflight-20260716-<baseline-short-sha> `
  --expected-provider openai `
  --expected-codex-version 0.144.4 `
  --model sandbox-model `
  --worker-reasoning high `
  --reviewer-reasoning high `
  --preflight-timeout-seconds 180 `
  --timeout-seconds 900
```

执行前后必须记录：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/experiment/langgraph-comparison
```

只有 HEAD、远端 SHA、本文档和工作区状态全部一致，才允许把本次结果写入 R2 结论。
