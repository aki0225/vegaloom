# Gate 7 R4 有界检查真实重跑预注册合同

> 状态：`ready-for-baseline-freeze-not-frozen`
>
> 日期：`2026-07-20`
>
> 分支：`experiment/langgraph-comparison`
>
> R3 结果：保持 `failed`，不得回写或重跑

## 1. 唯一目的

R4 只回答：

```text
在不改变任务、checkpoint、10 文件白名单、模型、provider、reasoning、
重试策略和 Gate 7C 条件触发规则的前提下，
把 worker 的代码检查限制为可机器审计的有界协议后，
Gate 7A linear + Goal/Handoff 能否真实完成 CP01 -> CP02 -> CP03？
```

R4 不回答：

- Agent 是否能自动拆解大任务；
- LangGraph 是否优于 linear；
- 是否发生真实物理换机；
- 是否可以切换默认引擎；
- provider 流式传输是否对任意长度会话都稳定。

Gate 7C 仍然只有在 R4 Gate 7A 成功且 transcript 证据重新复验通过后才允许触发。

## 2. R3 失败事实

R3 的项目内 Git `safe.directory` 修复已经生效。R3 CP01 的真实失败发生在编辑前：

```text
process output bytes = 86,721
process output lines = 2,230
exec declarations = 23
paired exec results = 17
duplicate commands = 5
largest command output = 15,530 bytes
cumulative command output = 75,046 bytes
tokens used = 67,804
workspace edits before failure = 0
```

R4 audit parser 对 R3 原始 transcript 的复验结论为 `failed`，命中：

- exec/result 解析不完整；
- 工具波次、exec 数、重复命令超限；
- 无界或超限 `Get-Content`；
- 未带 `--max-count` 的 `rg`；
- 单命令输出、累计输出、完整 transcript 和 token 超限。

因此 R4 修复的是检查会话无界膨胀，不把 R3 的网络流错误改写成 Git、实现或测试失败。

## 3. 固定身份

```text
case = eval/gate-7/flask-teardown-case-r4.json
case SHA-256 = e14720051ff970e489176db8ef4165f90cc382f714e341c0734c90b8acf1e737
plan SHA-256 = f39ce91758867b4e5f7c5e338c85e4f4b8e5afa5a2374aee0dee44919fce7e2d
graph schema = gate7-r4-v1
real session A = gate7a-flask-5928-real-r4-v1
real session C = gate7c-flask-5928-real-r4-v1
baseline A = gate-7a-pre-run-r4-v1
consumed A = gate-7a-consumed-r4-v1
baseline C = gate-7c-langgraph-pre-run-r4-v1
consumed C = gate-7c-langgraph-consumed-r4-v1
```

R4 继续使用：

- `pallets/flask PR #5928`；
- base `7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b`；
- 人工冻结的 CP01、CP02、CP03；
- 原始 10 文件白名单；
- final tree `a5b249e710d1253bee4c099faf91e45f9ebfbddd`；
- canonical diff bytes `19266`；
- canonical diff SHA-256
  `d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b`。

checkpoint 计划仍是人工输入，不是 worker 产出。R4 的行号提示只缩小检查范围，不包含
官方 diff、merge commit 或实现答案。

## 4. Provider 合同

```text
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
auth = chatgpt
request_max_retries = 0
stream_max_retries = 0
preflight model sessions = 0
fresh sessions for Gate 7A = 3
automatic retries = 0
multi_agent = disabled
```

Codex CLI 固定为 `0.144.5`。R4 只增加以下已识别的减量配置：

```text
tool_output_token_limit = 2048
model_verbosity = low
model_reasoning_summary = none
worker wall-clock timeout = 600 seconds
```

这些配置不能替代外层 Gate；最终是否通过仍由 transcript 和 workspace 证据判断。

## 5. 有界检查合同

每个 checkpoint 的真实 worker 必须满足：

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

`exec` 必须严格匹配以下一种模板：

```text
git status --short
rg -n --max-count <N> -e '<pattern>' -- <registered paths>
Get-Content <registered path> -First <N>
Get-Content <registered path> | Select-Object -Skip <N> -First <N>
```

wrapper 必须是 `pwsh` 或 `pwsh.exe`。`rg -e` pattern 必须使用 PowerShell
单引号字面量，命令正文中的 `$` 可展开变量/子表达式和反引号转义全部失败关闭。
transcript 声明的 `in <workdir>` 必须与当前 checkpoint 的 fixture repo 规范化绝对路径
完全一致。

额外命令、复合命令、未注册参数、未注册路径、Python introspection、递归
`Select-String`、网络命令和写文件命令全部失败关闭。修改只能使用 `apply_patch`。

第二个工具波次结束后，worker 必须立即编辑或明确停止为证据不足，不允许继续第三轮探索。

## 6. Transcript 证据

每个真实 checkpoint 必须写入：

```text
executions/<checkpoint>/process-output.txt
executions/<checkpoint>/transcript-audit.json
```

audit 至少绑定：

- parser version；
- policy SHA-256；
- transcript SHA-256；
- expected workdir 与逐命令 observed workdir；
- command/result 数；
- 工具波次；
- 重复命令；
- 单命令与累计输出字节；
- transcript 字节；
- token 数；
- violations。

audit 必须同时进入 checkpoint payload、handoff/engine state 和
`checkpoint_completed` event。成功后 Gate 7C 不得只信 `summary.json`：

1. 重新读取三个 `process-output.txt`；
2. 重新运行冻结 parser 与 policy；
3. 对账 `transcript-audit.json` 和 checkpoint payload；
4. 对账 output hash、audit hash、parser version；
5. 重新验证 machine event hash chain 和唯一 completion event。

任一项不一致，Gate 7C 直接 blocked。

## 7. Engine-neutral 假运行

安全复审前已经完成、但不再作为最终 baseline 证据：

```text
fake linear session = gate7-r4-fake-linear-v1
fake LangGraph session = gate7-r4-fake-langgraph-v1
```

两臂均成功完成三个 checkpoint，且：

```text
case hash parity = true
plan hash parity = true
CP01 prompt SHA = 9cf348625ab3c422b4e21dc365b2b9afb81072ddfff4cfbba89595889604d046
CP02 prompt SHA = 6c3d0abf2dcf9b45313c8ce2c49347f448328bcb5e255ed0389b87f8e7a9ad40
CP03 prompt SHA = 14b0ccd0ba1f55a71c3eb29d6aa62e6631f9a6f91d558933ba057bbf22ed8e9d
final tree parity = true
canonical diff parity = true
automatic retries = 0
planned migrations = 1
Machine F target external attempts = 1
```

fake runner 不产生 Codex transcript，因此 audit 状态为 `not_applicable`；它只验证协议、
状态传递、测试、范围和最终身份，不参与真实 provider 结论。

严格 cwd 绑定和单引号 pattern 修复改变了 worker prompt。修复后已使用以下新 session
重跑双臂，且没有覆盖或删除 v1 artifacts：

```text
fake linear session = gate7-r4-fake-linear-v2
fake LangGraph session = gate7-r4-fake-langgraph-v2

status parity = success
case hash parity = true
plan hash parity = true
CP01 prompt SHA = 4df182033096692bba758ca824a1d54042380ccfa202c9d424138f3e673e9fb3
CP02 prompt SHA = 51ee8cd22813b048b04794ae37d02f7964fd5f9204a79281f774ea9da02d96a8
CP03 prompt SHA = b2ae159146e5298edfe4e02c23715758640b00ab5124a0f7541dd7d32b9dd705
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

两个 v2 session 均得到冻结 final tree
`a5b249e710d1253bee4c099faf91e45f9ebfbddd`，canonical diff bytes 为
`19266`，diff SHA-256 为
`d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b`。

这只证明修复后的协议在 fake runner 下仍保持 engine-neutral、可接力且最终身份一致；
不证明真实 provider 成功。全量测试门槛已由 2026-07-20 的短路径复验独立关闭。

## 8. Baseline 冻结就绪

项目全量 LangGraph 环境共收集 `838` 个测试记录，node id 去重后仍为 `838`。
2026-07-20 在短路径 checkout 完成重新验证：

```text
473 个节点由完整文件分片明确通过
348 个节点由超时文件拆成完整 node id 后明确通过
17 个 execution control 节点由系统 Python 完整文件明确通过
------------------------------------------------------------
838 passed
0 failed
0 skipped
0 timeout-unresolved
```

原 77 个记录的多数现象由 Windows 深路径和分片时限解释。短路径复验仍发现一个真实恢复
回归：terminal execution 与 Step Result 已写，但 `os._exit` 发生在下一条 Graph
checkpoint 之前时，上一条 manifest 仍然自洽，无法表达“本次 Graph 提交未闭合”。

实现提交 `private-gate-7-r4-crash-marker-fix-redacted` 新增
`graph/checkpoint-pending.json`：

- Step Result 落盘后写 marker；
- 只有新的 checkpoint 行和 manifest seal 成功后才清除；
- `put_writes` 中间态不得提前清除；
- 硬退出保留 marker，恢复 fail-closed 且不得重复启动 worker。

完整结果见 `GATE-7-R4-TEST-CLOSURE.md`。

测试阻塞已经解除，但本轮未创建 baseline tag，所以预注册状态只能是
`ready-for-baseline-freeze-not-frozen`，不能写成 `frozen-before-run`。真实 Provider
调用仍为 `0`。

## 9. 停止线

以下任一情况出现，R4 必须写入 terminal state，不得重试：

- baseline 或 consumed tag 身份不一致；
- control clone 不干净；
- worker Git 临时配置不安全；
- provider、模型、reasoning、CLI 版本或 retry 配置漂移；
- inspection contract、case hash、plan hash 或 prompt hash 漂移；
- transcript parser 不完整或任一预算超限；
- exec wrapper、PowerShell 字面量、工作目录或命令正文不匹配冻结只读模板；
- audit artifact、checkpoint payload 或 event ledger 无法对账；
- CP01/CP02/CP03 顺序、scope、测试计数、behavior probe 或 final identity 漂移；
- handoff、event hash chain、DLP、canary 或 token evidence 失败；
- 任一 worker 失败后试图换 provider、换模型、复用 session 或自动重试。

Gate 7C 只有在 Gate 7A `success` 且上述证据全部复验通过后才可创建 R4 consumed tag并启动。
