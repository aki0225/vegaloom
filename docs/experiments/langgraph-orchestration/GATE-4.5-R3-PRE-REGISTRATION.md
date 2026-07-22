# Gate 4.5 Core Dogfood R3 Preflight 预注册合同

> 文档状态：`frozen-before-r3-preflight`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> Windows sandbox 受限修复：
> `private-gate-4-5-r3-windows-recovery-fix-redacted`
>
> R2 历史结论：`blocked`
>
> R3 当前范围：`preflight-only`
>
> R3 执行基线：包含本文档的首个干净提交。真实结果必须记录其完整 SHA。

---

## 1. 授权与本轮问题

项目 owner 已在当前会话明确授权：

> 继续完成 R3、Gate 4.5，并在 Gate 4.5 满足准入时推进到 Gate 5。

该授权允许按每个 Gate 的预注册预算执行真实 Codex 调用，但不允许跳过预注册、扩大单阶段
调用次数、自动重试、切换 provider/model，或在安全断言失败后强行进入下一 Gate。

R3 preflight 只回答：

1. `--ignore-user-config` 下恢复
   `windows.sandbox="elevated"` 后，真实 session 是否获得 `workspace-write`；
2. Codex CLI、provider、model、reasoning 和 sandbox live header 是否与合同一致；
3. 完整 argv、原始命令哈希、Runner identity 和 execution artifact 是否一致；
4. 隔离 preflight fixture 是否保持 clean；
5. preflight-only 是否没有创建业务 fixture、业务 run、worker 或 reviewer。

本阶段不回答真实 worker/reviewer 质量，也不运行 Gate 4.5 业务 Case。

## 2. 环境合同

### 2.1 固定执行身份

```text
runner = codex-exec
Python shutil.which("codex") = <codex-wrapper>
Codex CLI = 0.144.4
authentication = existing ChatGPT login
config mode = ignore_user_config
runner profile = none
windows sandbox session override = elevated
expected provider = openai
model = sandbox-model
worker reasoning effort = high
reviewer reasoning effort = high
session persistence = ephemeral
preflight sandbox = workspace-write
preflight only = true
memory = off
```

R3 不读取、打印、复制或持久化明文 key，也不读取 Codex credential store、`.env`、
Authorization header 或认证文件。现有认证只由 Codex CLI 自己使用。

### 2.2 完整命令合同

真实 preflight 命令必须由项目 `CodexExecRunner` 构造，并按以下顺序包含：

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

命令不得包含：

```text
--profile
--full-auto
--dangerously-bypass-approvals-and-sandbox
任意额外 --config
任意其他 CLI 参数
```

Gate 4.5 harness 必须根据 repo、sandbox、配置来源、override、model、reasoning 和
ephemeral 重建完整允许 argv，并与 Runner 命令精确相等。

### 2.3 Runner identity

identity 至少固定为：

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

原始命令只允许通过 `command_sha256` 绑定。持久化命令、identity、summary 和 report 必须
脱敏。

## 3. 干净基线

真实 R3 preflight 前必须同时满足：

1. 当前分支为 `experiment/langgraph-comparison`；
2. Git 工作区 clean；
3. 当前 HEAD 包含
   `private-gate-4-5-r3-windows-recovery-fix-redacted`；
4. 本文档已经提交并推送；
5. 本地 HEAD 与 `origin/experiment/langgraph-comparison` 完整 SHA 一致；
6. 使用全新 session、fixture 和结果目录；
7. 执行期间不修改 Runtime、harness、合同或通过标准；
8. 不复用 R0、R1、R2 或 fake harness 的 fixture、execution 或结果；
9. 不存在仍在运行的 pytest 或其他 Gate 4.5 execution。

任一条件不满足，不得启动外部调用。

## 4. Preflight 合同

### 4.1 隔离 fixture

fixture 固定在：

```text
.tmp/langgraph-fixtures/gate-4.5/<r3-session>/preflight/repo/
```

结果固定在：

```text
.local-validation/gate-4.5/<r3-session>/
```

fixture 只包含最小 `AGENTS.md` 和 `README.md`。prompt 只要求输出：

```text
VEGA_GATE_4_5_PREFLIGHT_OK
```

provider 不得修改、创建或删除文件，也不得执行与 sentinel 无关的命令。

### 4.2 必须同时成立的断言

preflight 只有在以下条件全部成立时才为 `passed`：

1. Runner 终态为 `success`；
2. assistant/codex 输出包含固定 sentinel；
3. Codex header 版本为 `0.144.4`；
4. Codex header provider 为 `openai`；
5. Codex header model 为 `sandbox-model`；
6. Codex header reasoning effort 为 `high`；
7. Codex header sandbox 以 `workspace-write` 开头；
8. Runner 自身的 live-header fail-closed 校验通过；
9. 完整 evidence command 与预注册合同一致；
10. `execution.command_sha256` 与预注册原始命令哈希一致；
11. execution identity 与预注册 identity 一致；
12. preflight fixture 前后 `git status --short` 为空；
13. execution artifact 为成功终态且 HEAD、session 和 step identity 一致；
14. 新业务 run 数量为 0；
15. 业务 Case 数量为 0；
16. 没有创建 `linear-low`、`graph-low` 或 `graph-crash-hitl` fixture。

### 4.3 Fail-fast

任一断言失败：

```text
R3 preflight = blocked
business case count = 0
worker session count = 0
reviewer session count = 0
automatic retry count = 0
```

失败后不得自动重试，不得切换 provider、model、reasoning、profile 或 sandbox，也不得继续
Gate 4.5 业务 Case。

## 5. 外部调用预算

R3 preflight-only 最大预算：

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

`business timeout` 只是后续合同参数，本阶段不得启动业务 Case。

## 6. 数据出站边界

本次真实 preflight 只允许发送：

- 固定 sentinel 请求；
- 隔离 fixture 的最小 `AGENTS.md` 和 `README.md`；
- Codex CLI 正常运行所需的非敏感命令参数。

不得发送：

- Vega 业务源码或 diff；
- R0/R1/R2 原始模型输出；
- 用户聊天记录；
- `.env`、key、token、Cookie 或 Authorization header；
- Codex credential store 内容；
- 用户目录或其他仓库内容。

## 7. 结果证据

summary schema 固定为 `4`。至少生成：

```text
.local-validation/gate-4.5/<r3-session>/summary.json
.local-validation/gate-4.5/<r3-session>/preflight-result.json
.local-validation/gate-4.5/<r3-session>/REPORT.md
.local-validation/gate-4.5/<r3-session>/preflight/execution/execution.json
.local-validation/gate-4.5/<r3-session>/preflight/execution/process-output.txt
```

raw evidence 保持在 Git 忽略目录。Git 只提交 R3 最终结果文档，不提交 fixture、认证信息、
运行日志、SQLite、`.tmp/` 或 `.local-validation/`。

允许的本阶段结论：

```text
preflight-passed
  只表示 R3 配置、真实 sandbox、provider/model 和 execution 证据通过。

blocked
  任一预注册断言失败，或执行基线不满足。
```

R3 preflight 通过不等于 Gate 4.5 通过。通过后必须先冻结并提交独立的业务 Case 合同，再使用
当前 owner 授权执行最多 3 次 worker 和 3 次 reviewer session。

## 8. 已知残余风险

Windows abrupt-exit checkpoint 测试曾偶发出现 SQLite 主文件 hash 与 manifest 不一致；
隔离复跑和 `test_crash_windows.py` 整文件复跑通过。

该风险：

- 不影响本次 preflight-only，因为本阶段不创建 LangGraph 业务 run；
- 不因 preflight 通过而关闭；
- 在 Gate 4.5 业务 Case 合同中继续单独记录；
- 若业务 Case 出现 checkpoint、WAL、journal 或 manifest 不一致，立即分类为 `fail`，
  不得进入 Gate 5。

## 9. 支持实现准入证据

`private-gate-4-5-r3-windows-recovery-fix-redacted` 已取得：

```text
Codex Runner targeted nodes = 11 passed
Gate 4.5 harness = 32 passed
Core suite = 340 passed, 1 skipped
LangGraph suite = 148 passed, 1 skipped
compileall = passed
Ruff = passed
git diff --check = passed
independent review = no open findings
```

两个 skip 都来自当前 Windows 环境没有目录 symlink 权限。

## 10. 预注册命令

将 `<baseline-short-sha>` 替换为包含本文档且已经推送的执行基线短 SHA：

```powershell
.tmp\langgraph-validation-venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner real `
  --ignore-user-config `
  --windows-sandbox-session-override elevated `
  --preflight-only `
  --session real-core-r3-preflight-20260716-<baseline-short-sha> `
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

只有 HEAD、远端 SHA、本文档、Codex 版本、登录状态和工作区状态全部一致，才允许使用本次
结果更新 R3 结论。
