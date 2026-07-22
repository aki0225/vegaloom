# Gate 7 R2 大任务执行预注册合同

> 文档状态：`frozen-before-run`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 真实 provider 调用：`0`

---

## 1. 本轮只验证什么

Gate 7 R2 不验证 Agent 自动拆解大任务。checkpoint 计划是人类输入，先预注册、封存并
计算 plan hash；Agent 不能重排、合并、拆分或扩大 checkpoint。

本轮只验证：

```text
给定一个真实开源项目的跨模块 PR、3 个预注册 checkpoint 和完整项目测试，
linear + Goal/Handoff 是否能在中断后接力完成；
LangGraph 是否能在同一任务、同一计划、同一 prompt 和同一 provider 合同下恢复完成。
```

Gate 7A 自动拆解另立实验，不把“最终完成”倒推成“计划合理”。

## 2. Case 与任务身份

R2 case 是一个只包含变更身份和协议差异的 overlay，基础任务复用 Gate 7 v1 已封存的
真实 Flask PR case；v1 case、v1 结果和 v1 tags 不修改、不删除、不重跑。

```text
case file = eval/gate-7/flask-teardown-case-r2.json
base case = eval/gate-7/flask-teardown-case.json
schema = 2 overlay
case id = gate7-flask-teardown-goal-handoff-r2-v1
case contract SHA-256 = b618a8e1db2e0ea2fbfdc3b7c0c42c6a5270eca872b2ede186aae189c80b5acb
plan SHA-256 = 1cfe5b9ae1080b015ecc8050a15515c41879861e4f5275e4ac7b30204d26268b
case hash mode = canonical-json
```

canonical-json 规则绑定解析后的完整 case 合同，因此不受 Windows 工作树 LF/CRLF 差异影响。
plan hash 还包含每个 checkpoint 的 `expected_state` 和验证合同。

真实任务仍为：

```text
repository = pallets/flask
PR = #5928
base = 7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
merge = c34d6e81fd8e405e6d4178bf24b364918811ef17
changed files = 10
base full suite = 494 passed
merge full suite = 495 passed
```

官方 diff 只保留 metadata 和 SHA-256；正确性只由 Flask 自己的测试与行为 probe 判定。

## 3. 固定 checkpoint 输入

```text
CP01 = tests-only precheck
CP02 = behavior repair
CP03 = context and docs freeze
```

每个 checkpoint 都有 exact scope、目标、依赖、expected state 和机器可执行验收。
硬门槛包括：

- scope escape：`git diff` 路径与预注册白名单比较；
- source chat / memory / machine path leakage：对封存 artifact 做 canary 检索；
- sensitive material：扫描 Authorization bearer 和 API key 形态；
- retry：event ledger 中按 logical operation 与 idempotency key 计数；
- recovery：中断后只允许目标 checkpoint 产生一个新的 external attempt；
- integration：CP03 最后必须运行完整 `495 passed` suite；
- final identity：最终 tree、canonical diff bytes 和 SHA 与 case 身份比较。

“自动重试为 0”只限制同一 external effect 的自动再次执行，不禁止一次明确的
planned migration 和新会话恢复。

## 4. A/B 执行顺序

先执行 A：

```text
linear + Goal/Handoff
```

A 成功、summary/handoff/metrics/canary/final identity 全部复验通过后，才允许执行 C：

```text
LangGraph + Goal/Handoff
```

A 失败或 blocked 时，C 必须记录为 `not-triggered`，不能把 C 写成失败，更不能用 C
补偿 A 的失败。

A 与 C 必须保持以下输入完全一致：

- 任务与 case；
- checkpoint 计划；
- 模型、推理强度和 provider；
- worker prompt；
- 工具与预算；
- CP02 后中断点；
- CP03 完整集成测试。

只比较恢复成本、重复执行、人工接管、状态错误、总 token 和耗时，不把成功归因给
LangGraph 本身。

## 5. 子会话隔离

R2 真实 worker 的 Codex argv 必须恰好包含：

```text
--disable
multi_agent
```

prompt 也明确禁止调用 `collab`、sub-agent 或委派工具。这样本轮验证的是固定
checkpoint 协议承载能力，不混入“系统自动拆解/自动并行”的第二个实验。

## 6. 预算与停止线

```text
provider = sandboxproxy
base_url = http://127.0.0.1:18080/v1
wire_api = responses
model = sandbox-model
reasoning = high
auth = chatgpt
session = ephemeral
memory = off
automatic retries = 0
request_max_retries = 0
stream_max_retries = 0
provider preflight sessions = 0
fresh provider sessions per arm = 3
```

真实 arm 启动前必须完成 CLI、auth、loopback TCP、baseline tag 和 clean clone 检查；
这些检查不得创建模型会话。第一个真实 worker 启动前才创建并推送对应 consumed tag。

任一真实 arm 的 consumed tag 创建后，不得重试、换 provider、换模型、换 reasoning、
重用 session 或修改 case。

## 7. 模拟换机边界

本机通过两个独立 control process、两个独立 clone、私有 canary、sealed handoff 和
新进程恢复模拟 E/F 接力。结果只能称为 `single-host dual-node simulation`；
不得声称发生了真实物理换机。真换机是后续实验条件，不是 R2 的事实。
