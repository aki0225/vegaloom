# Gate 6 Goal / Checkpoint / Handoff 真实执行结果总览

> 当前状态：`pass / real handoff`
>
> 当前真实轮次：`R4`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> Extended Decision：`reuse-independent-of-langgraph`

---

## 1. 当前 Canonical 结果

Gate 6 在 R4 完成了预注册的真实 preflight、Session A 和 Session B：

```text
decision = reuse-independent-of-langgraph
phase = completed
provider sessions used = 3
execution slots used = 3
tokens used total = 81,479
handoff version = v0001
context status = ready
automatic retries = 0
failures = []
```

R4 证明了两个 fresh provider session 可以通过 Goal / Checkpoint / Handoff 合同完成
真实接力，同时不发送 source chat、accepted memory 或真实项目数据。该证据支持
engine-neutral 复用，不支持把 LangGraph 直接提升为默认产品引擎。

完整 R4 结果：

- [`GATE-6-R4-RESULT.md`](GATE-6-R4-RESULT.md)
- baseline：`gate-6-r4-pre-run-v1` / `private-gate-6-r4-cache-fix-redacted`
- consumed：`gate-6-r4-consumed-v1`

## 2. 轮次索引

- R1：`blocked / preflight launcher`，provider sessions `0`；
- R2：`fail / DLP false positive`，provider sessions `1`；
- R3：`fail / verification cache misclassified`，provider sessions `2`；
- R4：`pass / real handoff`，provider sessions `3`。

R1、R2、R3、R4 都有独立 baseline 和 consumed tag，任何已消费轮次都没有重跑。

---

## 附录：R1 原始阻断记录

### A.1 结论

Gate 6 的唯一一次真实流程在 preflight 启动前被 `blocked`。本次没有启动任何真实
provider session，也没有启动 Session A 或 Session B。

```text
real decision = blocked
phase = failed:preflight
provider sessions used = 0
runner invocations = 0
execution slots used = 0
automatic retries = 0
handoff artifact = not-created
consumer context = not-created
```

这不是 handoff 业务合同失败，也不是 provider 身份、模型、token、网络或 latency
结果。阻断原因是 Windows 下 Codex 可执行文件解析的 harness 缺陷，发生在 provider
调用之前。

因此本 Gate 不能证明真实 Session A/B 接力成功，也不能据此否定 handoff 设计本身。

---

### A.2 执行基线与准入

```text
execution baseline commit =
private-gate-6-handoff-implementation-redacted

annotated baseline tag =
gate-6-pre-run-v1

consumed tag =
gate-6-consumed-v1

baseline tag commit =
private-gate-6-handoff-implementation-redacted

consumed tag commit =
private-gate-6-handoff-implementation-redacted
```

真实执行使用仓库内的独立 clean checkout：

```text
.tmp/gate-6/clean-checkout-gate6-real-v1
HEAD = gate-6-pre-run-v1^{commit}
core.autocrlf = false
core.eol = lf
working tree = clean
```

冻结 commitment：

```text
fixture =
eval/gate-6/handoff-case.json

fixture SHA-256 =
84bbdadb73eb85a088c597f9fafe76e525729a99e7007d861b6a3236921e7270

AGENTS.md LF SHA-256 =
8d7a20344511fdf6358dc11f63bd20fe59ec829d790d41712907578f56cc0a8a

docs/PRODUCT-CONTRACT.md LF SHA-256 =
b0e0551a8dc2a3bffdce1cd7ad0f488f10904bba7b2e8545962a52e6b89483b4
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
hard limit = 3
```

---

### A.3 真实执行账本

唯一执行命令：

```text
python scripts/gate6_handoff_dogfood.py --runner real --confirm-real --session gate6-real-v1
```

机器证据：

```text
phase = failed:preflight
decision = blocked
provider_sessions_used = 0
execution_slots_used = 0
runner_invocations = 0
tokens_used_total = null
retry_count = 0
sensitive_fixture_lock = windows-share-deny
source_chat_included = false
accepted_memory_writes = 0
real_project_data_sent = false
```

失败信息原文：

```text
FileNotFoundError: [WinError 2] 系统找不到指定的文件。
```

真实 harness 已建立 synthetic fixture 和 goal run，但在 preflight 的 Codex 版本检查
阶段停止：

```text
goal run =
.tmp/gate-6/gate6-real-v1/workspace/runs/20260719-130137-goal
```

没有生成真实 handoff、context 或 worker execution artifact。

---

### A.4 阻断根因

当前 runner 的 preflight 路径保留裸执行名 `codex`：

```text
_codex_version("codex")
_codex_auth_mode("codex")
subprocess.run(["codex", ...])
```

在本机，Python 的 `shutil.which("codex")` 可以解析到：

```text
<codex-wrapper>
```

但版本和登录状态检查只验证了 `which` 结果，没有把解析后的路径传给
`subprocess.run`。Windows `CreateProcess` 在该调用形态下没有成功执行 `.CMD` 包装器，
最终产生 `WinError 2`。

这说明：

- Codex CLI 文件在宿主机 PATH 中可发现；
- real harness 的 preflight launcher 兼容性不足；
- provider 请求尚未发出；
- 不能把本次结果解释为 `sandboxproxy`、`sandbox-model` 或 ChatGPT auth 失败。

---

### A.5 确定性与 Fake 证据

真实调用前的本地证据全部取得明确终态：

```text
test_checkpoint_handoff.py = 19 passed
test_goal_cross_session.py = 3 passed
Gate 6 deterministic total = 22 passed
test_gate6_handoff_dogfood.py = 2 passed
Gate 6 related total = 24 passed
goal smoke = 19 passed
evidence freshness = 20 passed
compileall = pass
ruff = pass
git diff --check = pass
```

最近一次完整 fake harness：

```text
session = gate6-fake-contract-007
decision = fake-passed
phase = completed
runner invocations = 3
provider sessions = 0
handoff version = v0001
handoff SHA-256 =
b56d06b876a0fa98e84b7d9d1b90e45b35cf05d22de24e7c0b1b0b97aaf1ad73
context status = ready
context SHA-256 =
818e810f11dc2cc4f83ef661a3b853ee638872ab054999b6d75b25b25e59f699
Session A/B distinct = true
source chat included = false
accepted memory writes = 0
automatic retries = 0
failures = []
```

Fake 证据证明本地 handoff/context、漂移检测、artifact 绑定和安全边界链路成立，但
不替代真实 provider 证据。

---

### A.6 安全、预算与停止线

- consumed tag `gate-6-consumed-v1` 已创建并推送到远端，锁定本次 baseline 的唯一执行权。
- 没有重试，没有切换 provider、model、auth 或 reasoning。
- 没有发送 source chat、canary、accepted memory 或真实项目数据。
- 没有写入 accepted memory。
- 没有自动 commit、push 或 release synthetic fixture。
- 不允许在当前 consumed baseline 上再次运行 real harness。

---

### A.7 后续边界

本次 Gate 证明了：

- Goal / Checkpoint / Handoff 的确定性安全合同可以通过本地和 fake dogfood；
- baseline、tag、consumed latch、证据 hash 和停止线按预注册流程工作；
- preflight launcher 缺陷会在 provider 调用前 fail-closed。

本次 Gate 没有证明：

- 真实 provider 身份、token、latency 或 termination；
- 真实 Session A / Session B 的 handoff 接力；
- LangGraph checkpoint 相对普通 Goal contract 的不可替代增量；
- handoff 可以进入默认产品路径。

下一次若继续，必须先修复 executable resolution 并新增 Windows 回归测试，再创建新的
预注册合同、baseline commit 和 execution tag。不得复用本次
`gate-6-pre-run-v1` 或 `gate-6-consumed-v1` 重跑。

---

### A.8 Canonical Evidence

真实阻断证据位于 clean checkout：

```text
.tmp/gate-6/clean-checkout-gate6-real-v1/
  .local-validation/gate-6/gate6-real-v1/summary.json
  .local-validation/gate-6/gate6-real-v1/report.md
```

SHA-256：

```text
summary.json =
f6147932df08530ad90a9bda76f26a3d75dbb75e9fa509b6f20b435804086ac7

report.md =
bdce49a2e6a5680c36e9630c139de95239854c9c4a5e3cf1e9bcce441fe0a2f5
```

Fake 证据：

```text
.local-validation/gate-6/gate6-fake-contract-007/summary.json
.local-validation/gate-6/gate6-fake-contract-007/report.md
```

```text
summary.json =
0752c34f793cba588e83216a7030da0374d200992b36648533253f88a571d19a

report.md =
1e8c1683d3f5925bc9bff3880da4d8ff0f2fccdc965078f23d424f48c4b6336d
```
