# Gate 7 R3 真实重跑预注册合同

> 状态：`frozen-before-run`
>
> 日期：`2026-07-19`
>
> 分支：`experiment/langgraph-comparison`
>
> R2 结果：保持 `failed`，不得回写或重跑

## 1. 唯一目的

R3 只回答：

```text
在不改变任务、checkpoint、模型、provider、预算和停止条件的前提下，
修复 R2 暴露的 control clone Git 身份边界后，Gate 7A linear + Goal/Handoff
能否真实完成 CP01 -> CP02 -> CP03？
```

R3 不回答：

- LangGraph 是否优于 linear；
- Agent 是否能自动拆解任务；
- 是否发生真实物理换机；
- 是否可以切换默认引擎。

Gate 7C 仍然只有在 R3 Gate 7A 成功后才允许触发。

## 2. R2 不可变边界

以下 R2 身份保持不变：

- `gate-7a-pre-run-r2-v1`
- `gate-7a-consumed-r2-v1`
- `gate7a-flask-5928-real-r2-v1`
- `docs/experiments/langgraph-orchestration/GATE-7-R2-RESULT.md`
- `eval/gate-7/result-r2-v1.json`

R3 不读取 R2 raw execution 作为成功证据，不复用 R2 consumed session。

## 3. R3 固定身份

```text
case = eval/gate-7/flask-teardown-case-r3.json
case SHA-256 = e244483bb294b2d99cf934f8619729808763c791b5e0b7b6c4ce83bbbd4c5e81
plan SHA-256 = 5c6ae968bfd0378c8eb0643aea16e0e3956c708d9a15c2df805436788abbe2ab
graph schema = gate7-r3-v1
real session A = gate7a-flask-5928-real-r3-v1
real session C = gate7c-flask-5928-real-r3-v1
baseline A = gate-7a-pre-run-r3-v1
consumed A = gate-7a-consumed-r3-v1
baseline C = gate-7c-langgraph-pre-run-r3-v1
consumed C = gate-7c-langgraph-consumed-r3-v1
```

R3 仍使用 `pallets/flask PR #5928` 的原始 10 文件白名单、CP01/CP02/CP03
人工计划和 final tree/diff 身份。checkpoint 计划是人工输入，不是 worker 产出。

## 4. 唯一修复

R2 的 Git `dubious ownership` 只通过以下项目内机制修复：

1. 为每个 fixture repo 创建项目内临时 Git 配置文件；
2. 文件只包含当前 fixture repo 的一个 `safe.directory`；
3. 仅通过 `RunnerExecutionContext.git_config_global` 注入到 Vega 启动的 owned
   worker subprocess；
4. 不修改用户全局 Git 配置、不修改 ACL、不重启系统服务；
5. 在 worker 启动前由机器可判定的 `git status` 和 `git config --global` 探针验证。

该配置路径不进入 prompt、handoff、公开 summary 或结果文档。

## 5. Provider 合同

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
```

R2 之后做过一次不带凭证、无模型字段的 loopback transport diagnostic，返回上游
`400 model is required`。该请求不是 Codex worker session，不计入 R3 provider budget；
R3 readiness 只记录诊断摘要，不把它当作业务成功证据。

## 6. Gate 7A 停止线

以下任一情况出现，R3 必须写入 terminal state，不得重试：

- baseline tag 不是 annotated tag或不指向 execution baseline；
- control clone 不严格干净；
- worker Git 临时配置不是单一当前 repo safe.directory；
- provider preflight 或 worker retry 不为零；
- consumed tag 已存在或无法与 baseline 对账；
- CP01/CP02/CP03 顺序、scope、测试计数、behavior probe 或 final identity 漂移；
- handoff、event hash chain、DLP、canary 或 token evidence 失败；
- 任一 worker 失败后试图换 provider、换模型、复用 session 或自动重试。

Gate 7C 只有在 Gate 7A `success` 且全部前置证据复验通过时才可创建 R3 consumed
tag 并启动。
