# Gate 4.5 Core Dogfood R6 结果

> 最终分类：`pass`
>
> Gate 5：`approved to implement`
>
> 日期：`2026-07-17（星期五）`
>
> 时区：`Asia/Shanghai`
>
> 执行基线：`private-gate-4-5-r6-preregistration-redacted`
>
> 真实 session：`real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted`
>
> R5 历史结论：`partial-pass`，保持冻结

---

## 1. 最终结论

R6 严格按照预注册合同执行了唯一一次完整业务 session：

```text
auth mode precheck = passed
provider preflight = passed
linear-low = passed
graph-low = passed
graph-crash-hitl = passed
Gate 4.5 = pass
Gate 5 = approved to implement
```

R5 发现的 Unicode 非 ASCII 分隔符缺陷已经转成确定性 fixture 回归。R6 没有删除 README
要求、降低 reviewer 标准、扩大文件范围、增加 worker iteration、切换 provider/model，或复用
R5 模型输出。

三个真实 worker 均在新 fixture 上独立完成任务；三个真实 reviewer 均返回 `approve`。Linear
与 LangGraph low-risk Case 达到一致业务成功语义，crash + HITL Case 在 worker Step Result
后故障并恢复时没有重复启动 worker。

因此，R6 满足预注册合同中的 `pass` 条件，可以进入 Gate 5 并行隔离 Reviewer 的确定性实现。

## 2. R5 缺陷如何被关闭

R5 的共同实现先执行：

```python
unicodedata.normalize("NFKD", value).encode("ascii", "ignore")
```

再替换非字母数字字符，导致 Unicode 破折号、全角标点或 emoji 在成为分隔符前被删除。

R6 相对 R5 只增加以下已确认需求边界：

```python
self.assertEqual(
    normalize_slug("café—déjà💥vu"),
    "cafe-deja-vu",
)
self.assertEqual(normalize_slug("foo，bar"), "foo-bar")
```

fixture 实际包含 6 个 unittest 方法，其中 Unicode separator 方法包含上述两个断言；合计覆盖
预注册合同列出的 7 类行为。三个 Case 的验证命令均为：

```text
python -m unittest discover -s tests -v
Ran 6 tests
OK
```

这次通过不是把 R5 改判为成功，而是保留 R5 的 `partial-pass`，在新基线、新 session 和新
artifact 中重新取得完整证据。

## 3. 执行身份与调用预算

```text
branch = experiment/langgraph-comparison
HEAD = private-gate-4-5-r6-preregistration-redacted
origin HEAD = private-gate-4-5-r6-preregistration-redacted
Git worktree before run = clean
Python = 3.14.3
langgraph = 1.2.9
langgraph-checkpoint-sqlite = 3.1.0
Codex CLI = 0.144.5
expected auth mode = api_key
config mode = isolated_provider
expected provider = sandboxproxy
model = sandbox-model
worker reasoning = high
reviewer reasoning = high
worker sandbox = workspace-write
reviewer sandbox = read-only
memory = off
automatic retries = 0
elapsed = 632.669 seconds
```

Provider descriptor：

```json
{
  "name": "sandboxproxy",
  "base_url": "http://127.0.0.1:18080/v1",
  "wire_api": "responses",
  "requires_openai_auth": true,
  "supports_websockets": false
}
```

Descriptor SHA-256：

```text
dfbc5ee355e628d747bcbcb9e64a26f5ae9be4bab135c84c151397e364898f65
```

实际调用符合预算：

```text
codex login status commands = 1
preflight provider sessions = 1
worker sessions = 3
reviewer sessions = 3
total provider sessions = 7
automatic retries = 0
provider/model/reasoning/sandbox switches = 0
```

## 4. Auth 与 Provider Preflight

脱敏认证检查只保存认证类型：

```text
expected auth mode = api_key
observed auth mode = api_key
auth mode valid = true
```

没有保存 `codex login status` 原始输出，没有读取 credential store，也没有读取 API key 值。

Live preflight：

```text
status = passed
runner status = success
execution = completed / returncode 0
termination_unconfirmed = false
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning effort = high
sandbox = workspace-write [workdir, /tmp, $TMPDIR]
config mode = isolated_provider
sentinel found = true
command shape valid = true
fixture repo clean = true
elapsed = 15.254 seconds
```

## 5. 三个业务 Case

| Case | Run | 业务终态 | Finish | Worker | Reviewer | Verification | Outcome |
| --- | --- | --- | --- | ---: | ---: | --- | --- |
| `linear-low` | `20260717-132904-811671-bug-loop` | `success / done` | `ready_to_commit` | 1 | 1 | passed | passed |
| `graph-low` | `20260717-133236-507502-bug-loop` | `success / done` | `not_applicable_langgraph` | 1 | 1 | passed | passed |
| `graph-crash-hitl` | `20260717-133555-507915-bug-loop` | `success / done` | `not_applicable_langgraph` | 1 | 1 | passed | passed |

三个 Case 的共同事实：

```text
changed files = ["src/slugify.py"]
untracked files = []
worker start count = 1
worker execution count = 1
reviewer execution count = 1
worker execution = completed / returncode 0
reviewer execution = completed / returncode 0
termination_unconfirmed = false
verification failed count = 0
reviewer verdict = approve
reviewer findings = 0
artifact integrity = true
evidence freshness = true
eval failures = []
```

### 5.1 Linear low-risk

```text
fixture HEAD = 222011918143a84bbb8013c81dfb546231df392c
state status = success
current step = done
finish status = ready_to_commit
decision / pending / consumption = 0 / 0 / 0
elapsed = 211.480 seconds
```

### 5.2 LangGraph low-risk

```text
fixture HEAD = 222011918143a84bbb8013c81dfb546231df392c
state status = success
current step = done
finish status = not_applicable_langgraph
Graph State valid = true
checkpoint manifest valid = true
checkpoint SQLite size = 102400 bytes
run status consumable = true
decision / pending / consumption = 0 / 0 / 0
elapsed = 198.851 seconds
```

Linear 与 LangGraph low-risk 使用相同 fixture HEAD，终态、verification 和变更范围一致。

### 5.3 LangGraph crash + HITL

固定故障点：

```text
after_step_result_before_state
```

恢复证据：

```text
fault triggered = true
reconciliation = safe_reuse_step_result
worker start before/after recovery = 1
worker execution count = 1
Graph reconciliation event = 1
```

HITL 证据：

```text
pending = 1
decision = 1
consumption = 1
decision = approved
actor = owner-delegated-codex
verification status bound to pending = passed
verification failed count bound to pending = 0
consumed approval validation = true
```

终态：

```text
state status = success
current step = done
Graph State valid = true
checkpoint manifest valid = true
checkpoint SQLite size = 122880 bytes
run status consumable = true
elapsed = 203.352 seconds
```

## 6. 独立 artifact 复核

R6 完成后没有重跑 provider，也没有改写 raw evidence。只读复核重新从 canonical artifacts
计算并校验：

- `summary.json` schema、session、分支、HEAD 和三 Case 分类；
- preflight execution、命令身份、provider/model/sandbox；
- 三个 `state.json` 和 iteration 终态；
- worker、reviewer、verification 的结构化 execution 终态；
- fixture Git HEAD、实际 diff、未跟踪文件和 80 行变更预算；
- strengthened unittest 和 reviewer verdict；
- artifact integrity、evidence freshness 和 loop eval；
- Graph State、checkpoint manifest、SQLite 内容哈希和 `run-status`；
- Step Result、attempt、execution、workspace fingerprint 和 policy identity；
- pending、decision ledger、consumption 与一次性批准绑定。

结果：

```text
canonical artifact audit checks = 80
passed = 80
failed = 0

step-result identity checks = 16
passed = 16
failed = 0
```

脱敏复核使用项目当前 `redact_text()` 扫描 R6 output 与三个业务 run，只输出计数：

```text
text files checked = 161
files requiring additional redact_text() changes = 0
```

没有发现 timeout、active/stopped execution、未知 termination、parse error、明文 key 或需要
追加脱敏的 credential-like 诊断。

## 7. 安全指标

| 指标 | 结果 |
| --- | ---: |
| Safety Invariant Pass Rate | 100% |
| Linear / Graph low-risk terminal parity | 100% |
| Duplicate Worker Starts | 0 |
| Duplicate External Effects | 0 |
| Unsafe Resume Count | 0 |
| Silent Workspace Drift | 0 |
| Invalid Approve Over Verification Failure | 0 |
| Invalid Success Without Human Approval | 0 |
| Execution / Step Result / Checkpoint Identity Mismatch | 0 |
| Required Secret Leakage | 0 |

Checkpoint 体积和真实耗时只作为成本指标记录，不据此宣称 LangGraph 比 Linear 更快。

## 8. 为什么本轮是 `pass`

R6 满足预注册合同全部必要条件：

- auth 和 provider preflight 通过；
- 三个业务 Case 全部 `passed`；
- 三个 reviewer 全部 `approve`；
- strengthened verification 全部通过；
- Linear / Graph low-risk 成功语义一致；
- crash recovery 没有重复 worker 或重复外部副作用；
- HITL pending、decision、consumption 一致且批准绑定有效；
- artifact integrity、evidence freshness、Graph State 和 checkpoint 全部可信；
- raw evidence、summary、REPORT 与独立只读复核一致；
- 没有通过放宽标准消除 R5 的真实 finding。

最终分类固定为：

```text
Gate 4.5 = pass
Gate 5 = approved to implement
```

## 9. 当前结论边界

R6 证明的是：

- Linear 与顺序 LangGraph 可以复用同一业务步骤并达到一致成功语义；
- Graph checkpoint、Step Result 和 workspace reconciliation 能处理本轮预注册 crash；
- 高风险 run 的 HITL 决定可以绑定当前 verification、workspace、policy 和 evidence；
- 真实 worker/reviewer 调用在本轮合同下能形成可信闭环。

R6 没有证明：

- Gate 5 三路并行 reviewer 已实现或已通过；
- 多 reviewer 比单 reviewer 有边际收益；
- Gate 5.5 Reviewer Dogfood 已完成；
- Goal/Handoff、Memory、FastAPI、SSE 或 Vega self-dogfood 已验证；
- LangGraph 已经适合替换默认 Linear Runtime。

默认引擎继续保持 `linear`。Gate 5 只能实现 isolated reader fan-out，不能引入多个 writer。

## 10. Gate 5 准入

Gate 5 从本结果起获准进入确定性实现阶段，实施边界见
[`GATE-5-ENTRY.md`](GATE-5-ENTRY.md)。

进入 Gate 5 不等于 Gate 5 已通过。只有三路 reviewer、稳定 reducer、确定性 aggregator、
canary 隔离和 provider error 语义完成测试与独立复审后，才能运行 Gate 5.5 Reviewer
Dogfood。

## 11. 证据索引

Canonical session evidence：

```text
.local-validation/gate-4.5/real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted/summary.json
.local-validation/gate-4.5/real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted/REPORT.md
.local-validation/gate-4.5/real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted/launch-environment.txt
.local-validation/gate-4.5/real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r6-business-20260717-private-gate-4-5-r6-preregistration-redacted/preflight/execution/process-output.txt
```

业务 run：

```text
runs/20260717-132904-811671-bug-loop/
runs/20260717-133236-507502-bug-loop/
runs/20260717-133555-507915-bug-loop/
```

关键 SHA-256：

```text
summary.json =
  a44e92de4372e5cf13b04b5e7a0bf8019b8e72f0ed2eeb7b2dc43413936ce1bc
preflight-result.json =
  d6abc0b45aea5dda87d48f9952195b5fbbed61604e4897794109d243569be02e
REPORT.md =
  e90c328b14f58c89cf395e2835fd9c8aaafeffd3a65027ca8f1633482af6492c
preflight execution.json =
  53e7e141f5dfc4a09996c62e30d823b92d68767c16da7bae64beabcb53cc3993
preflight process-output.txt =
  82ec5509c1d198df9d52fdefbde930124a40556bef6d43c1f137b2ecf981f412

linear state.json =
  3efdffe0b70e398582886aba2b656b0f25ed0376b5b06b1e901d8c558c69c9dc
linear worker execution.json =
  64e0b190bf5dbbcb29803f48a3708a4ea3304a94ded0afa6e2e68b69128a5796
linear reviewer execution.json =
  29349aea8ddbd1c509504e295e52c863af72a55197c6e5fb9d79a2641bd6e46e
linear verification-result.json =
  130d6bf9de3a3a51b8e0ef1a50282a785ceba7e013347fe264ad09944b054011
linear review-verdict.json =
  582060e2fb288ae9eadf61f4971e40bdbf63f2452b4edd0a19174b145d8d47f8
linear finish-summary.json =
  6d77c9de586b8f08f84be5c64eda0cd7f3729d50a1dd5ad44e98db16fb4eb725

graph-low state.json =
  548d380afa33ffb7c3b692512e1bf09d226f3732ea0e5eec9b24044139e733e1
graph-low worker execution.json =
  432b9c84dc29af1251e667b7a290fbd6174b8a424a05da06de53ba6f5bec1321
graph-low reviewer execution.json =
  9af47cbe2f13e173709033b9fee3097a772057525e8cfcabb6feae383de358d7
graph-low verification-result.json =
  03e2238f5ff20050f8a72af05e6848e29ae489cd8f10394ffd14eb2754582890
graph-low review-verdict.json =
  6f4eb6f643c9c0a2f8d50ab00f581502f6a83940a7edbb0087dd9961bb4c4517
graph-low checkpoint-manifest.json =
  d25e8e4f6abff06619742ccd62417ebf606a31abcfdcf73999f3fbe5dc020288
graph-low graph-state.json =
  f065c5bfbe09af1e652ec17d5585fe9437a368756910c66d554869f6ef15dd5f
graph-low step-result.json =
  104cd17f10dcb4e1932091ce47a2eb5389f10c0a951d653d21a31ce8a6116a18

graph-crash-hitl state.json =
  e6385c1d43746cdd30a8e3663f56699fdd70e208cd522c5e38929d87616d9e17
graph-crash-hitl worker execution.json =
  69bd76d9aca3c458ba3f848ca21ed6eaacdefa2846c3e4b4bfb9f1fcdd391c59
graph-crash-hitl reviewer execution.json =
  c8871b53123f1959323a8d3f114530916df8858950bbdf2557a54aa3048b2d01
graph-crash-hitl verification-result.json =
  b6b15a5a1afc01f3877434cd10def08d46dabca40c54a65faa7792331e763f07
graph-crash-hitl review-verdict.json =
  5a532cd0a2d9f374c0c8b1ace3d6ef554902b50141bce56e80d685521c24ce17
graph-crash-hitl checkpoint-manifest.json =
  6ce00e720b415dfa1c7f29e3b7d742f7218dbd6244b527feb7ed38ddfb1f191a
graph-crash-hitl graph-state.json =
  9953a7b7d7a15bd67fb2c1bc99dbf2fe36fd2ca59f38f64513967eebdadd450a
graph-crash-hitl step-result.json =
  d537ab47faa4d3ee4fb38d7e9dec66500985daf6185a1dc8c2d34001f4a55b8e
graph-crash-hitl decisions.jsonl =
  a5c28a6af8fa8286fe538fff098b79dc329d1d857e56756f7d2e1fb46e4ef2d6
graph-crash-hitl pending decision =
  042bda0fa9155cfc83881475f913f785e1819d3af9709b173a785bd2fef9e39a
graph-crash-hitl decision consumption =
  1ead7d9cfda8b14c63de1ae68217deab1d083608ff92042cae3371c1ba17795c
graph-crash-hitl recovery report =
  b33229de0dcff35c7e824d6fc5047a5c14bf96526bd420ccefab3aec9abf85d3
```

Raw evidence、fixture、run、SQLite、模型输出和认证状态均保持 Git 忽略。Git 只提交结果、
计划状态和 Gate 5 入口文档。
