# Gate 7 R5 API-key 认证真实重跑预注册合同

> 状态：`ready-for-baseline-freeze-not-frozen`
>
> 日期：`2026-07-20`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`

## 1. 为什么必须创建 R5

R4 已在 2026-07-20 创建并推送以下 annotated baseline tag：

```text
gate-7a-pre-run-r4-v1
gate-7c-langgraph-pre-run-r4-v1
```

两个 tag 均绑定提交：

```text
private-gate-7-r4-test-closure-redacted
```

R4 冻结认证模式为 `chatgpt`，但本机当前可用 Codex 登录模式为 `api-key`。项目 owner
已在 2026-07-20 明确授权使用现有 API-key 登录执行实验。

因此不能修改、移动或冒用 R4 tag，也不能把 API-key 执行写成 R4 结果。R5 使用独立
case、session、baseline tag 和 consumed tag，只改变已声明的认证变量，并保留 R4
原始冻结事实。

## 2. R5 唯一问题

R5 只回答：

```text
在沿用 R4 的任务、checkpoint、10 文件白名单、prompt、provider、模型、reasoning、
重试策略、有界 transcript 审计和 Gate 7C 条件触发规则时，
把 Codex 登录模式从 chatgpt 改为用户明确授权的 api-key，
Gate 7A linear + Goal/Handoff 能否真实完成 CP01 -> CP02 -> CP03？
```

R5 不回答：

- API-key 是否优于 ChatGPT auth；
- LangGraph 是否优于 linear；
- provider 对任意长度流式会话是否稳定；
- 是否发生真实物理换机；
- 是否应修改 Vega 默认执行引擎。

## 3. R4 与 R5 的变量差异

| 变量 | R4 | R5 |
| --- | --- | --- |
| auth mode | `chatgpt` | `api-key` |
| case/session/tag namespace | `r4-v1` | `r5-v1` |
| graph schema | `gate7-r4-v1` | `gate7-r4-v1` |
| transcript parser | `gate7-r4-v1` | `gate7-r4-v1` |
| provider/model/reasoning | 不变 | 不变 |
| prompt 与检查预算 | 不变 | 不变 |
| retry | `0` | `0` |

R5 另包含一项控制面健壮性修复：`_spawn_machine` 对子进程输出显式使用 UTF-8 并以
`errors=replace` 失败安全解码。该修复来自 R5 首次 fake linear 运行中观察到的 Windows
默认 GBK 解码线程异常，不改变 worker prompt、任务、模型请求或验证命令。

## 4. 固定身份

```text
case = eval/gate-7/flask-teardown-case-r5.json
case SHA-256 = 6b3541059cc6a2a8375424d303cb5a48b79b4b305d3dc6599f3b42b72330eaae
plan SHA-256 = cbda7c69e26370a05e44b4cd7691e386992befdb1f38af72e7d85892b754dba0
graph schema = gate7-r4-v1

Gate 7A session = gate7a-flask-5928-real-r5-v1
Gate 7C session = gate7c-flask-5928-real-r5-v1

baseline A = gate-7a-pre-run-r5-v1
consumed A = gate-7a-consumed-r5-v1
baseline C = gate-7c-langgraph-pre-run-r5-v1
consumed C = gate-7c-langgraph-consumed-r5-v1
```

任务身份继续固定为：

```text
pallets/flask PR #5928
base = 7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
canonical diff bytes = 19266
canonical diff SHA-256 =
d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
```

冻结 prompt：

```text
CP01 = 4df182033096692bba758ca824a1d54042380ccfa202c9d424138f3e673e9fb3
CP02 = 51ee8cd22813b048b04794ae37d02f7964fd5f9204a79281f774ea9da02d96a8
CP03 = b2ae159146e5298edfe4e02c23715758640b00ab5124a0f7541dd7d32b9dd705
```

这些 prompt hash 与 R4 fake v2 完全相同。

## 5. Provider 与 CLI 合同

```text
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
auth = api-key
request_max_retries = 0
stream_max_retries = 0
preflight model sessions = 0
fresh sessions for Gate 7A = 3
automatic retries = 0
multi_agent = disabled
Codex CLI = 0.144.5
tool_output_token_limit = 2048
model_verbosity = low
model_reasoning_summary = none
worker wall-clock timeout = 600 seconds
```

执行使用项目 `.tmp/` 下隔离安装的 Codex CLI `0.144.5`，但复用当前 `CODEX_HOME`
中已经存在的 API-key 登录状态。脚本只读取 `codex login status` 的认证类型，不读取、
复制、打印或持久化 API key。

以下内容仍禁止进入 artifact：

- API key 原文或片段；
- Authorization header；
- `.env`；
- Codex auth 文件；
- 用户级配置内容。

任一 DLP 命中都使实验失败。

## 6. 有界检查与验证合同

R5 完整继承 R4：

```text
max tool waves = 2
max exec commands = 8
max Get-Content output window = 60 lines
max rg matches per file = 20
max single command output = 8,192 bytes
max cumulative command output = 32,768 bytes
max complete transcript = 65,536 bytes
max total tokens = 45,000
max duplicate commands = 0
```

只允许：

```text
git status --short
rg -n --max-count <N> -e '<pattern>' -- <registered paths>
Get-Content <registered path> -First <N>
Get-Content <registered path> | Select-Object -Skip <N> -First <N>
```

代码修改只能使用 `apply_patch`。任一额外命令、第三轮探索、路径越界、可展开
PowerShell pattern、错误 cwd 或 transcript/audit 对账失败都立即停止。

## 7. Fake 双臂冻结证据

最终采用修复后的 v2 证据：

```text
fake linear session = gate7-r5-fake-linear-v2
fake LangGraph session = gate7-r5-fake-langgraph-v2

status parity = success
case hash parity = true
plan hash parity = true
prompt hash parity = true
final tree parity = true
canonical diff parity = true
automatic retries = 0
planned migrations = 1
Machine F target external attempts = 1
scope violations = 0
duplicate external effects = 0
canary leaks = 0
sensitive material hits = 0
```

fake runner 不调用 Provider，也不产生真实 Codex transcript；它只证明 R5 case、状态传递、
范围、验证、handoff 和最终身份仍保持 engine-neutral。

## 8. Baseline 与真实预算

只有在以下条件全部满足后才创建 R5 baseline tag：

1. 受影响测试完整通过；
2. compileall、Ruff 和 `git diff --check` 通过；
3. fake v2 双臂证据完成；
4. 工作区干净；
5. 远端分支与本地 HEAD 一致；
6. 四个 R5 tag 均不存在。

真实 Gate 7A 启动前必须再次确认：

- baseline A 与 baseline C 是 annotated tag；
- 两个 tag 和远端分支 peel 到同一提交；
- PATH 首个 `codex` 为 `0.144.5`；
- `codex login status` 为 API-key 模式；
- loopback Provider 可达；
- real session 目录不存在；
- consumed A 与 consumed C 均不存在。

Gate 7A 只允许一次。consumed A 成功推送后，无论 worker 成功或失败都不得重跑。

## 9. 停止线

以下任一情况出现，R5 必须 terminal，不能换模型、换 Provider、换认证或重试：

- R4 tag 被修改、删除或移动；
- R5 baseline/consumed tag 身份不一致；
- control checkout 不干净；
- API-key auth、CLI 版本或 loopback Provider 漂移；
- case、plan、prompt 或 inspection contract 漂移；
- transcript parser 不完整或预算超限；
- CP01/CP02/CP03 顺序、scope、验证计数或 final identity 漂移；
- handoff、event hash chain、DLP、canary 或 token evidence 失败；
- 任一 worker 失败后尝试启动新 session。

Gate 7C 只有在 R5 Gate 7A `success` 且原始 transcript 全链重新复验通过后才允许触发。
