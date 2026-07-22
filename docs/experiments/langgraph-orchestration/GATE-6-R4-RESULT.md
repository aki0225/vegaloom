# Gate 6 R4 Goal / Checkpoint / Handoff 真实执行结果

> 状态：`pass / real handoff`
>
> 真实执行日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> Extended Decision：`reuse-independent-of-langgraph`

---

## 1. 结论

R4 在固定的 provider、模型、认证、sandbox 和预算合同下完成了完整真实流程：

```text
real decision = reuse-independent-of-langgraph
phase = completed
provider sessions used = 3
execution slots used = 3
runner invocations = 3
automatic retries = 0
tokens used total = 81,479
```

三次调用分别是 preflight、Session A 和 Session B，全部 `success`。Session B 使用
Session A 产生的 checkpoint / handoff 继续完成第二阶段，最终：

```text
handoff version = v0001
handoff SHA-256 =
7a7c86c6c9651b27e2e08bdb8ac0434b0e097de54793485e67971cd6e9661ab4

context status = ready
context SHA-256 =
10c66fe0738ad0bc63a4ea5df36eea7dca14b29099dad2d0bd844161b5ed4457

Session A/B distinct = true
failures = []
```

这证明 Goal / Checkpoint / Handoff 合同可以在两个 fresh provider session 之间完成
可审计接力，并且不依赖 LangGraph 才能成立。因此本轮选择
`reuse-independent-of-langgraph`，而不是把 LangGraph 本身提升为默认编排引擎。

---

## 2. 执行基线与准入

```text
baseline commit =
private-gate-6-r4-cache-fix-redacted

baseline tag =
gate-6-r4-pre-run-v1

consumed tag =
gate-6-r4-consumed-v1

consumed tag commit =
private-gate-6-r4-cache-fix-redacted
```

R4 consumed tag 已推送到远端。R1、R2、R3 consumed tag 均在真实执行前确认存在，
R4 consumed tag 在真实执行前不存在；R4 没有复用或重跑任何已消费轮次。

最终真实执行使用的 clean checkout：

```text
.tmp/gate-6-r4/clean-checkout-gate6-r4-real-v1-lf/
HEAD = gate-6-r4-pre-run-v1^{commit}
core.autocrlf = false
core.eol = lf
working tree = clean
```

准备阶段发现宿主机系统级 `core.autocrlf=true` 会导致普通 clone 在设置本地配置前
检出 CRLF。该目录没有用于真实执行；随后使用命令级 `core.autocrlf=false`、
`core.eol=lf` 重建了上面的最终 checkout。该环境修正发生在 provider 启动前，
没有消耗真实 session。

冻结 fixture：

```text
eval/gate-6/handoff-case.json
SHA-256 =
84bbdadb73eb85a088c597f9fafe76e525729a99e7007d861b6a3236921e7270
```

固定真实身份和预算：

```text
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
auth = chatgpt
Codex CLI = 0.144.5
ephemeral = true
memory = off
automatic retries = 0
provider session hard limit = 3
```

确定性准入在真实调用前已取得明确终态：

```text
test_checkpoint_handoff.py = 19 passed
test_goal_cross_session.py = 3 passed
test_gate6_handoff_dogfood.py = 7 passed
Gate 6 R4 related total = 29 passed
compileall = pass
ruff = pass
git diff --check = pass
fake preflight + Session A + Session B = 3 slots
fake handoff/context = ready
```

---

## 3. 真实执行账本

唯一真实命令：

```powershell
python scripts/gate6_handoff_dogfood.py `
  --runner real `
  --confirm-real `
  --session gate6-r4-real-v1 `
  --output-root .local-validation/gate-6-r4 `
  --fixture-root .tmp/gate-6-r4
```

调用结果：

```text
preflight:
  status = success
  session = gate6-preflight
  tokens = 11,325
  elapsed = 10.453s

Session A:
  status = success
  session = gate6-session-a
  tokens = 14,623
  elapsed = 89.515s

Session B:
  status = success
  session = gate6-session-b
  tokens = 55,531
  elapsed = 182.391s
```

三个 execution artifact 的 `returncode` 均为 `0`，`termination_unconfirmed` 均为
`false`。没有自动重试，也没有额外启动 provider session。

---

## 4. 安全边界

```text
sensitive fixture lock = windows-share-deny
source chat included = false
memory mode = off
accepted memory writes = 0
canary sent = false
real project data sent = false
```

真实 worker 只在 synthetic fixture workspace 中执行。真实仓库源码、source chat、
accepted memory 和 canary 均未发送给 provider。未知 ignored artifact、Git metadata、
refs、remote 和权威 artifact 的 guard 没有触发失败。

---

## 5. Deterministic 与 Fake 对照

R4 fake 合同在真实调用前已经通过，fake handoff/context hash 为：

```text
handoff SHA-256 =
3ae9adf411d90c33110bbf5e271c64d2abcf32cc9fbc2e9139213ce153bd3c9c

context SHA-256 =
788f60df92f7b1d32ffdf6649fca81d16ea658b010682d1a5cf33047014d51e5
```

R4 代码同时修复了 R3 暴露的 verification cache 误报：任意层级的
`__pycache__` 和 `.pytest_cache` 不再被当作 worker 业务写入，其他未知 ignored
路径仍保持 fail-closed。R4 的 7 个 harness 回归测试覆盖了这一边界。

---

## 6. 证明范围与未证明范围

本轮证明了：

- 两个 fresh provider session 可以通过受约束的 handoff/context 完成真实接力；
- handoff artifact、context 编译、workspace 事实和 checkpoint evidence 可以形成可
  校验的跨 session 合同；
- provider 身份、模型、认证、sandbox、预算、进程终止和敏感 fixture 隔离均取得真实
  execution evidence；
- 该合同可以独立于 LangGraph 的 graph cursor 复用。

本轮没有证明：

- LangGraph checkpoint 相对普通 Goal / Handoff contract 的不可替代业务收益；
- LangGraph 应该替换默认 linear engine；
- handoff 应立即进入所有产品路径；
- 多 Reviewer topology 的业务收益。

因此 Core Decision 继续保持 `partial`，默认引擎继续是 `linear`，LangGraph 继续是
experimental；只有 Goal / Checkpoint / Handoff 合同获得 engine-neutral 的复用结论。

---

## 7. 审查发现与后续加固

R4 通过的是本次预注册实验，不等于 harness 已经达到生产级分布式 gate controller
标准。只读审查发现以下后续工作：

- preflight 后才建立 Session A 基线，当前没有对 preflight 前后的 ignored 文件和
  Git metadata 做完整差分；
- consumed tag 是本地 refs 锁，尚未提供跨 clone 的远端原子 claim；
- `__pycache__` 和 `.pytest_cache` 当前按目录整体豁免，后续应限制文件形态、链接和
  payload 大小；
- `core.eol`、旧轮次 consumed tag 和冻结的 session/output/fixture 参数主要靠人工
  准入检查，脚本还没有全部机械化执行。

这些是产品化前的 harness 加固项，不改变 R4 已完成的 provider 账本，也不授权在
`gate-6-r4-consumed-v1` 上重跑。

`GATE-6-R4-READINESS.md` 中的 `baseline commit = pending explicit commit` 保留为
baseline 冻结前的历史快照；实际使用的 baseline 是
`private-gate-6-r4-cache-fix-redacted`，不能据此覆盖或重建已消费 tag。

---

## 8. Canonical Evidence

真实证据位于最终 LF clean checkout：

```text
.tmp/gate-6-r4/clean-checkout-gate6-r4-real-v1-lf/
  .local-validation/gate-6-r4/gate6-r4-real-v1/summary.json
  .local-validation/gate-6-r4/gate6-r4-real-v1/report.md
  .local-validation/gate-6-r4/gate6-r4-real-v1/executions/preflight/execution.json
  .local-validation/gate-6-r4/gate6-r4-real-v1/executions/session-a/execution.json
  .local-validation/gate-6-r4/gate6-r4-real-v1/executions/session-b/execution.json
```

SHA-256：

```text
summary.json =
9ebe51b6356efb32c32a10457c108e8a227dd4d6daed1a59dcb5d729853f6219

report.md =
d244fd723370538cc133e675ebfeacee8c1bcf844c469b0e8816c63c1a471396

preflight/execution.json =
6cc9d6c8bc3221e5b62ab6b26e4ed3b50dfbba07dca128e503fff0beb1c57155

session-a/execution.json =
37c12fb1a3907ca47bfada6cd2e861a07555cad2e62fda440ce0019799daeffd

session-b/execution.json =
13b89813e7be3227ccf620691cb022c262160ec189a9d69df2cf328de9881f7e
```

R4 consumed tag 已锁定本次 provider 预算。后续如需评估其他产品问题，必须创建新的
预注册轮次和新的 baseline，不得在 `gate-6-r4-consumed-v1` 上重跑。
