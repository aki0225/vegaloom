# Gate 6 Handoff 真实执行 Readiness

> 状态：`ready-for-baseline-freeze / provider not started`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 真实 provider 调用：`0`

---

## 1. Readiness 结论

Gate 6 的 Goal / Checkpoint / Handoff 实现、冻结 synthetic fixture、确定性测试、fake
dogfood 和独立状态机边界回归均已完成，可以进入 execution baseline 提交阶段。

当前结论只表示“允许冻结并启动唯一一次真实流程”，不表示真实 provider 已通过。

```text
Gate 5.5 = pass
Gate 6 pre-registration = frozen
Gate 6 implementation = complete
Gate 6 deterministic tests = pass
Gate 6 fake readiness = pass
Gate 6 real provider calls = 0
```

真实调用前仍必须按以下顺序执行：

1. 显式暂存 Gate 6 文件，排除用户未跟踪的 `uv.lock`；
2. 提交 execution baseline；
3. 创建 annotated tag `gate-6-pre-run-v1` 指向该提交；
4. 推送分支和 tag；
5. 用 `core.autocrlf=false` 从该 tag 建立 LF clean checkout；
6. 确认 clean checkout 的 `HEAD` 与 tag 完全一致后，只运行一次 real harness。

---

## 2. 冻结 Commitment

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

冻结 synthetic 合同：

```text
initial API = src.labels.normalize_label
final API = src.labels.normalize_label + src.labels.normalize_labels
checkpoint 01 empty input = ValueError
checkpoint 01 repeated spaces/hyphens = one hyphen
checkpoint 02 dedup order = normalized first appearance
source canary = GATE6_SOURCE_CHAT_PRIVATE_CANARY_7f2a9d4c
memory mode = off
automatic retries = 0
```

---

## 3. 实现边界

已完成并纳入 baseline 的文件范围：

```text
src/vega/goal_handoff.py
src/vega/goal_runtime.py
src/vega/cli.py
src/vega/run_status.py
scripts/gate6_handoff_dogfood.py
tests/experimental/langgraph_engine/test_checkpoint_handoff.py
tests/experimental/langgraph_engine/test_goal_cross_session.py
tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py
eval/gate-6/handoff-case.json
```

实现合同：

- handoff 使用不可覆盖的 `vNNNN/checkpoint-handoff.json`；
- handoff、checkpoint evidence、workspace、policy 和 artifact 均绑定 SHA-256；
- consumer context 重新读取当前 workspace、policy 和 evidence；
- source / consumer session 必须不同；
- source / target worker epoch 必须不同且 target 必须匹配 consumer；
- `ready`、`split_required`、`blocked` 三种终态不可互相包装；
- `blocked` 后允许人工修复并从新 handoff version / consumer session 恢复；
- Session B 不读取 source chat、process output 或 accepted memory；
- worker 不得自动 commit、push、release 或修改已绑定 evidence。

---

## 4. 确定性验证

Gate 6 两个测试分片的明确终态：

```text
tests/experimental/langgraph_engine/test_checkpoint_handoff.py = 19 passed
tests/experimental/langgraph_engine/test_goal_cross_session.py = 3 passed
Gate 6 deterministic total = 22 passed
tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py = 2 passed
Gate 6 related total = 24 passed
```

覆盖内容：

```text
clean create / compile = ready
same source / consumer session = blocked
worker epoch mismatch = blocked
same source / target worker epoch = rejected
workspace drift = blocked
policy drift (.vega / AGENTS / product contract) = blocked
handoff self-hash tamper = blocked
authoritative artifact tamper = blocked
checkpoint evidence stale = blocked
context over budget = split_required
source canary present but absent from context = pass
accepted memory before/after unchanged = pass
blocked handoff recovery with new version = pass
```

静态验证：

```text
python -m compileall -q src scripts/gate6_handoff_dogfood.py = pass
ruff check Gate 6 source/tests/scripts = pass
git diff --check = pass
```

所有 pytest 临时目录必须位于 `.tmp/pytest/runs/`，cache 必须位于
`.tmp/pytest/cache/`；`.local-validation/` 只保留人工验证最终证据。

---

## 5. Fake Readiness

最近一次完整 fake harness：

```text
command =
python scripts/gate6_handoff_dogfood.py --runner fake --session gate6-fake-contract-007

evidence =
.local-validation/gate-6/gate6-fake-contract-007

decision = fake-passed
phase = completed
runner invocations = 3
provider sessions = 0
provider hard limit = 3
context status = ready
handoff version = v0001
handoff SHA-256 = b56d06b876a0fa98e84b7d9d1b90e45b35cf05d22de24e7c0b1b0b97aaf1ad73
context SHA-256 = 818e810f11dc2cc4f83ef661a3b853ee638872ab054999b6d75b25b25e59f699
Session A/B distinct = true
source chat included = false
accepted memory writes = 0
automatic retries = 0
failures = []
```

Fake readiness 只证明本地执行契约、artifact 绑定和安全扫描链路确定性成立，不替代
真实 provider 的身份、网络、token、latency 或 termination 证据。

---

## 6. 真实身份与预算

```text
Codex CLI = 0.144.5
auth = chatgpt
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
sandbox = workspace-write on isolated synthetic fixture
ephemeral = true
memory = off
automatic retries = 0
```

唯一真实调用预算：

```text
preflight = 1
Session A = 1
Session B = 1
total = 3
hard limit = 3
```

任一 preflight、identity parse、prompt/output DLP、verification、token evidence、
timeout 或 termination 失败后，立即固化 `blocked` 或 `fail`，不得重试、切换
provider/model/auth/reasoning 或继续启动后续 session。

---

## 7. 未决事项与停止线

- 当前 execution baseline SHA 在提交前为空，必须由提交结果补入最终结果文档。
- real harness 只允许从 `gate-6-pre-run-v1` 对应 LF clean checkout 启动。
- `uv.lock` 是用户已有未跟踪文件，不属于 Gate 6 baseline，不得暂存或删除。
- 当前 provider 隧道由外部进程维护，真实执行前只做只读连通性和身份检查，不终止该进程。
- 真实流程结束后必须生成 `GATE-6-RESULT.md` 和 `EXTENDED-DECISION.md`，并单独提交、
  push；不得把失败或 blocked 结果改写成成功。
