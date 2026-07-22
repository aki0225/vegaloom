# Gate 4.5 Core Dogfood R1 重跑预注册合同

> 文档状态：`frozen-before-r1-real-run`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> R0 执行基线：`private-gate-4-5-core-dogfood-baseline-redacted`
>
> R0 结论：`blocked`
>
> R1 执行基线：包含本文档、显式 profile 绑定、fail-fast preflight 和回归测试的首个干净
> 提交；真实运行结果必须记录其完整 SHA。

---

## 1. 为什么允许 R1

R0 的三个业务 Case 都被同一个真实 Runner 身份漂移阻断：

```text
预注册只读 probe provider = sandbox-provider
真实 worker provider = sandboxproxy
model = gpt-5.6
result = HTTP 404 / model unavailable
```

R0 没有出现重复 worker、未知工作区副作用、unsafe resume 或错误 success，但也没有获得真实
worker、verification、HITL 和 reviewer 的可接受证据。R0 合同、fixture、session、原始 summary
和最终 `blocked` 结论保持冻结，不回写、不删除，也不与 R1 合并统计。

R1 只修复一个已识别的实验设计缺口：

> 在创建任何 Vega 业务 run 前，显式绑定并验证真实 worker 使用的 Codex profile、provider、
> model、CLI 版本和命令形态。

R1 不修改 Gate 4.5 的业务任务、通过标准、安全不变量、故障点或 HITL 条件。

## 2. R1 环境合同

### 2.1 固定身份

```text
runner = codex-exec
runner profile = sandbox-provider
expected provider = sandbox-provider
model = gpt-5.6
Codex CLI = 0.144.5
worker reasoning effort = high
reviewer reasoning effort = high
session persistence = ephemeral
worker sandbox = workspace-write
reviewer sandbox = read-only
memory = off
reviewer count = 1
```

worker 与 reviewer 的 `.vega.yaml` 配置必须同时显式写入 `profile: sandbox-provider`。不得通过修改用户
全局 Codex 默认 profile、临时切换 provider、删除 model 参数或使用另一个模型来解除阻塞。

### 2.2 干净基线

真实 R1 执行前必须：

1. 当前分支为 `experiment/langgraph-comparison`；
2. Git 工作区 clean；
3. harness、本文档和测试已经提交并推送；
4. 记录执行基线完整 SHA；
5. 使用全新 fixture session 和全新结果目录；
6. 不复用 R0 fixture，不恢复 R0 run；
7. 执行期间不修改 Runtime、harness、合同或通过标准。

## 3. Fail-fast Preflight

### 3.1 命令同源

preflight 必须直接复用项目现有 `CodexExecRunner` 和 `CodexExecOptions`，不得由外部 shell
另行拼接近似命令。除工作目录和 prompt 外，preflight 与真实 worker 使用相同的：

- `--profile sandbox-provider`；
- `--model gpt-5.6`；
- `--config model_reasoning_effort="high"`；
- `--ephemeral`；
- `--sandbox workspace-write`；
- stdin prompt 入口。

preflight 的 timeout 独立冻结为 180 秒；timeout 不改变 Codex CLI command shape。

### 3.2 隔离 Fixture

preflight 使用当前 R1 session 下的独立最小 Git fixture：

```text
.tmp/langgraph-fixtures/gate-4.5/<r1-session>/preflight/repo/
```

fixture 只包含 `AGENTS.md` 和 `README.md`。prompt 只要求输出固定 sentinel：

```text
VEGA_GATE_4_5_PREFLIGHT_OK
```

provider 不得修改、创建或删除文件。preflight 前后 `git status --short` 必须为空。

### 3.3 必须同时成立的断言

preflight 只有在以下条件全部成立时才为 `passed`：

1. Runner 终态为 `success`；
2. 输出包含单独一行固定 sentinel；
3. Codex header 报告版本 `0.144.5`；
4. Codex header 报告 `provider: sandbox-provider`；
5. Codex header 报告 `model: gpt-5.6`；
6. Codex header 报告 `reasoning effort: high`；
7. Codex header 报告 `sandbox: workspace-write ...`；
8. Runner 返回的命令与预构造命令完全一致；
9. preflight fixture 仍然 clean；
10. preflight execution artifact 已按现有 `execution.json` 机制记录。

只解析 Runner 自身输出的非敏感 header、warning 和 error；不得读取 Codex credential store、
`.env`、API key、Authorization header 或用户目录中的认证文件。

### 3.4 Fail-fast 行为

任一断言失败：

```text
R1 conclusion = blocked
business case count = 0
new Vega *-loop run count = 0
```

harness 必须写出：

```text
.local-validation/gate-4.5/<r1-session>/preflight-result.json
.local-validation/gate-4.5/<r1-session>/summary.json
.local-validation/gate-4.5/<r1-session>/REPORT.md
```

失败后不得自动重试 preflight，不得切换 profile/provider/model，也不得继续创建
`linear-low`、`graph-low` 或 `graph-crash-hitl` 业务 fixture。

## 4. 外部调用预算

R1 允许的最大真实调用数：

```text
preflight session = 1
worker sessions = 3
reviewer sessions = 3
```

preflight 成功后，业务 Case 仍按 R0 合同各执行一次 worker 和一次 reviewer。provider error、
timeout 或 unknown terminal status 后不得自动启动第二个 attempt。

## 5. 业务 Case 与通过标准

preflight 成功后才创建并执行：

1. `linear-low`；
2. `graph-low`；
3. `graph-crash-hitl`。

业务任务、fixture 内容、verification、预算、`after_step_result_before_state` fault、delegated
approval、单 reviewer 和所有 `pass / partial-pass / blocked / fail` 分类，继续以
`GATE-4.5-PRE-REGISTRATION.md` 为准。

R1 没有放宽以下硬门槛：

- duplicate worker starts = 0；
- duplicate external effects = 0；
- unsafe resume count = 0；
- silent workspace drift = 0；
- verification failure 不得升级为 success；
- Graph State、checkpoint manifest、decision ledger 和 consumption identity 必须可信；
- Linear 与 LangGraph 的核心业务成功语义必须一致。

## 6. Session 与结果隔离

R1 session 使用：

```text
real-core-r1-20260716-<baseline-short-sha>
```

R1 必须使用新的：

- preflight fixture；
- 三个业务 fixture；
- Vega run id；
- Graph SQLite；
- decision ledger；
- `.local-validation` 结果目录。

R0 与 R1 的原始结果分别保留。最终结果文档可以并列比较两轮，但不得把 R0 的安全证据、
R1 的模型成功证据或不同 session 的指标拼成一个虚构的单次通过结果。

## 7. 预注册执行命令

将 `<baseline-short-sha>` 替换为提交并推送后的执行基线短 SHA：

```powershell
.tmp\gate3-venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner real `
  --session real-core-r1-20260716-<baseline-short-sha> `
  --runner-profile sandbox-provider `
  --expected-provider sandbox-provider `
  --expected-codex-version 0.144.5 `
  --model gpt-5.6 `
  --worker-reasoning high `
  --reviewer-reasoning high `
  --preflight-timeout-seconds 180 `
  --timeout-seconds 900
```

执行前后都要记录：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/experiment/langgraph-comparison
```

只有 HEAD、远端基线和本文档一致，且工作区 clean，才允许把 R1 结果用于 Gate 4.5 结论。
