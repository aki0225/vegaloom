# Gate 6 Goal / Checkpoint / Handoff 预注册合同

> 文档状态：`frozen / ready-for-baseline-freeze / provider not started`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> Core Decision：`partial`
>
> 默认产品引擎：`linear`
>
> 默认 Reviewer topology：`single`
>
> 真实 provider 调用：`0（Gate 6）`

---

## 1. 唯一研究问题

Gate 6 只回答：

```text
一个长 Goal 能否通过 versioned checkpoint handoff，
让两个互相独立的短生命周期 worker session 安全接力，
而不依赖 Session A 的完整聊天、不覆盖当前 workspace 事实、
不自动写 accepted memory，并在 handoff 漂移时 fail-closed？
```

本 Gate 不重新判断 Gate 5.5，不评价多 Reviewer 收益，不切换默认产品引擎，也不实现
FastAPI、SSE、前端、多租户或远程 worker 平台。

## 2. 固定能力范围

实现范围固定为：

- worker epoch identity；
- checkpoint context compiler；
- versioned `checkpoint-handoff.json`；
- workspace 与 policy binding；
- evidence/artifact hash binding；
- source session 与 consumer session 隔离；
- context budget；
- deterministic split checkpoint plan；
- handoff blocked report；
- 两个 fresh worker session 接力；
- `memory.mode=off` 和 accepted memory 禁写。

不实现：

- LLM 自动无限拆分 checkpoint；
- 多 writer 同时修改 workspace；
- 自动 commit、push、release；
- 自动接受 memory proposal；
- handoff 覆盖 Git、verification 或权威业务 state；
- 复用 Session A 聊天或 provider session；
- 把 handoff 作为第二套业务状态。

## 3. Handoff Schema

每个完成 checkpoint 的 handoff 使用独立版本目录：

```text
runs/<goal-run>/checkpoints/<checkpoint>/handoffs/vNNNN/
  checkpoint-handoff.json
```

`checkpoint-handoff.json` 至少绑定：

```text
schema version
handoff version
handoff SHA-256
goal run identity
checkpoint identity
source worker epoch
target worker epoch
source session identity
objective
next action
user / policy hard constraints
verified facts
verified failed approaches
open questions
authoritative artifact refs + SHA-256
checkpoint record SHA-256
workspace HEAD / diff / untracked binding
project policy snapshot
memory mode = off
source chat included = false
created_at
```

可从当前 workspace、Git 或 verification artifact 重新读取的详细代码事实不得复制为长期
handoff state。handoff 只保存不能安全重猜的决定、约束、失败原因和证据引用。

## 4. Context Compiler

Session B 必须通过独立 compiler 读取 handoff：

```text
handoff + current goal contract + current checkpoint evidence
+ fresh workspace snapshot + fresh policy snapshot
-> checkpoint context
```

compiler 必须：

- 重新读取 workspace，而不是相信 handoff 中复制的代码事实；
- 重新校验 checkpoint evidence；
- 校验 handoff self hash 和 artifact hash；
- 校验 source session 与 consumer session 不同；
- 校验 goal/checkpoint/epoch/version identity；
- 校验 workspace 与 policy 未漂移；
- 生成确定性的 context metrics；
- 不读取 Session A 聊天；
- 不调用 runner；
- 不写 accepted memory。

输出目录固定为：

```text
runs/<goal-run>/checkpoints/<checkpoint>/handoffs/vNNNN/consumers/<session>/
```

允许终态：

```text
ready
split_required
blocked
```

不得把 `split_required` 或 `blocked` 包装成 ready。

## 5. Context Budget 与 Split

真实 Gate 6 的 worker context 字符预算冻结为：

```text
max_chars = 12000
```

超预算时：

- 不静默截断 objective、constraints、verified facts、failure reasons 或 evidence refs；
- 不启动 provider；
- 写出确定性 `checkpoint-split-plan.json` 和 Markdown report；
- split plan 只建议 section grouping，不自动创建新 checkpoint；
- Goal 仍等待人工或预注册 fixture 决定新的 checkpoint 边界。

## 6. 确定性测试矩阵

Gate 6 在任何真实 provider 调用前必须覆盖：

| Case | 预期 |
|---|---|
| clean create / compile | `ready` |
| source session == consumer session | `blocked` |
| workspace HEAD/diff/untracked drift | `blocked` |
| project policy drift | `blocked` |
| handoff self hash tamper | `blocked` |
| authoritative artifact tamper/missing | `blocked` |
| checkpoint evidence stale | `blocked` |
| context over budget | `split_required` |
| source chat canary exists but未引用 | context 不含 canary |
| memory ledger before/after | 不新增 accepted memory |

硬指标：

```text
Handoff Consistency = 100%
Safety Invariant Pass Rate = 100%
Duplicate Worker Starts = 0
Duplicate External Effects = 0
Unsafe Resume Count = 0
Silent Workspace Drift = 0
Source Chat Reads = 0
Accepted Memory Writes = 0
```

测试分片固定为：

```text
tests/experimental/langgraph_engine/test_checkpoint_handoff.py
tests/experimental/langgraph_engine/test_goal_cross_session.py
```

单分片超过 60 秒必须继续按 node id 拆分，timeout 不计通过。

## 7. 真实双 Session Fixture

真实 dogfood 只使用合成 Git fixture，不使用 Vega 或其他项目真实源码。

冻结流程：

1. 创建 clean synthetic repo；
2. 创建 Goal contract 和两个预注册 checkpoint；
3. Session A 只执行 checkpoint 01；
4. verification 通过后形成权威 checkpoint evidence；
5. 生成 versioned handoff；
6. 关闭 Session A，不保存或复用聊天；
7. Session B 使用全新 ephemeral worker session；
8. Session B 只读取 compiled checkpoint context 和权威 artifacts；
9. Session B 从当前 workspace 重新读取代码事实并执行 checkpoint 02；
10. 最终 verification、handoff consistency 和 memory 禁写全部复核。

Session A 与 Session B 必须：

```text
provider = same
model = same
reasoning = same
sandbox = workspace-write on isolated synthetic fixture
ephemeral = true
memory = off
automatic retries = 0
session identity = different
```

Session B prompt 禁止包含：

- Session A 聊天；
- Session A process output；
- Session A 私有 canary；
- 未绑定 artifact 的自由文本结论；
- accepted memory；
- 真实项目数据或凭证。

## 8. 真实执行身份与预算

除非冻结前显式修订，本轮真实身份沿用 Gate 5.5 已验证环境：

```text
Codex CLI = 0.144.5
auth = chatgpt
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
ephemeral = true
automatic retries = 0
```

预算冻结为：

```text
provider preflight = 1
Session A worker = 1
Session B worker = 1
total external provider sessions = 3
hard limit = 3
automatic retries = 0
```

任一 preflight、worker、parse、timeout 或 unknown termination 失败后停止，不得重试、切换
provider/model/auth/reasoning 或删除失败 checkpoint。

## 9. 数据出站边界

允许发送：

- synthetic Goal objective 和 checkpoint acceptance；
- synthetic repo 当前代码与 diff；
- compiled checkpoint context；
- handoff 中的硬约束、verified facts、failed approaches、open questions；
- 已绑定的 synthetic artifact refs；
- 中性 session/epoch/checkpoint identity。

禁止发送：

- Vega 真实源码和未列入 fixture 的项目文档；
- 其他项目源码、业务数据或日志；
- 用户聊天记录；
- Session A 完整聊天或 process output；
- `.env`、API key、token、Cookie、Authorization header；
- memory ledger；
- 未绑定证据的人工猜测。

## 10. Stop / Blocked / Fail

### `blocked`

- preflight 失败；
- provider/model/auth/network 不可用；
- context budget 超出且尚未人工 split；
- workspace 或 policy 漂移；
- evidence/artifact 缺失或 hash 不一致；
- handoff identity/self hash 不一致；
- Session B 无法只依赖 context 和权威 artifacts 继续；
- token/latency/termination 证据缺失。

### `fail`

- Session B 读取或依赖 Session A 完整聊天；
- workspace/policy 漂移后仍继续；
- source/consumer session 相同；
- 自动重试大于 0；
- 自动写 accepted memory；
- 自动 commit/push/release；
- handoff 覆盖当前 Git、verification 或权威业务 state；
- synthetic 边界外数据出站；
- provider 调用超过 3。

### `stopped`

- owner 显式停止；
- 下一个 session 会超过预算；
- 检测到未预注册身份切换；
- permanent stop latch 已建立。

## 11. Extended Decision

Gate 6 结果只允许：

```text
retain-as-langgraph-extension
reuse-independent-of-langgraph
experiment-only
reject-handoff
blocked
fail
```

### `retain-as-langgraph-extension`

- 全部确定性 case 通过；
- 两个 fresh worker session 都成功；
- Session B 不读取 Session A 聊天；
- handoff consistency 为 100%；
- drift/tamper 全部 fail-closed；
- context 在预算内或按预注册 split 后通过；
- duplicate/unsafe/memory 指标全为 0；
- LangGraph checkpoint identity 对 handoff 有不可替代的绑定价值。

### `reuse-independent-of-langgraph`

- handoff compiler 和跨 session 接力成立；
- 但其正确性只依赖 Goal contract、workspace、policy 和 artifact binding；
- LangGraph checkpoint 没有提供不可替代增量。

### `experiment-only`

- 确定性安全合同成立；
- 真实 provider 被外部条件 blocked，或收益不足以产品化；
- 保留实验代码和证据，不进入默认路径。

### `reject-handoff`

- Session B 必须依赖聊天才能继续；
- drift/tamper 无法可靠阻止；
- handoff 成为第二套状态；
- 维护成本明显高于可复用价值。

## 12. 冻结与执行顺序

真实 provider 调用前必须完成：

1. 实现 handoff/context compiler；
2. 新增两个 Gate 6 测试分片；
3. 固化 synthetic fixture；
4. 记录 fixture、contract 和 policy SHA-256；
5. 运行静态检查和全部 Gate 6 回归；
6. 独立复审无未关闭 Blocker/High；
7. 提交 execution baseline；
8. 创建不可变 annotated tag `gate-6-pre-run-v1`；
9. 从 tag 对应的 LF clean checkout 运行；
10. 执行唯一一次 preflight 和两个 fresh worker session。

任一条件不满足，不得启动 Gate 6 provider。

## 13. 最终证据

最终必须生成：

```text
docs/experiments/langgraph-orchestration/GATE-6-RESULT.md
docs/experiments/langgraph-orchestration/EXTENDED-DECISION.md
```

至少记录：

- execution baseline SHA 和 tag；
- fixture / contract / policy hash；
- preflight identity；
- Session A / Session B execution；
- handoff artifact 和 self hash；
- context compiler result；
- workspace/policy/artifact revalidation；
- context budget 与 split 结果；
- verification 结果；
- provider session ledger；
- token、latency 和 termination；
- source chat/canary/memory 扫描；
- deterministic case 原始计数；
- Extended Decision。

## 14. 当前状态

```text
Gate 6 pre-registration = frozen
Gate 6 implementation = complete
Gate 6 deterministic tests = pass
Gate 6 fake readiness = pass
Gate 6 real provider calls = 0
Gate 6 execution baseline = pending explicit commit
Gate 6 execution tag = gate-6-pre-run-v1 (created after baseline commit)
current date = 2026-07-19 (Sunday)
```
