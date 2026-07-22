# Gate 4.5 Core Dogfood R5 结果

> 最终分类：`partial-pass`
>
> Gate 5：`不进入`
>
> 日期：`2026-07-17（星期五）`
>
> 时区：`Asia/Shanghai`
>
> 执行基线：`private-gate-4-5-r5-preregistration-redacted`
>
> 真实 session：`real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted`
>
> R4 历史结论：`blocked`，保持冻结

---

## 1. 最终结论

R5 严格按照预注册合同执行了唯一一次完整业务 session：

```text
auth mode precheck = passed
provider preflight = passed
linear-low = quality_failed
graph-low = quality_failed
graph-crash-hitl = quality_failed
Gate 4.5 = partial-pass
Gate 5 = 不进入
```

R5 已经解除 R4 的外部执行身份阻塞：

- 当前认证模式确认为 `api_key`；
- 显式 provider 为 `sandboxproxy`；
- provider descriptor、命令、Runner identity 和 live header 一致；
- `sandbox-model`、reasoning、sandbox 和 Codex CLI 均符合合同；
- preflight、3 个 worker 和 3 个 reviewer 都形成明确成功 execution 终态。

三个业务 Case 没有通过的原因不是 provider、timeout、scope、verification、checkpoint、
recovery 或证据篡改，而是三个独立 reviewer 一致发现同一个真实实现缺陷：

> worker 在处理 Unicode 分隔符前先执行 `encode("ascii", "ignore")`，会直接删除全角标点、
> Unicode 破折号或 emoji，错误拼接相邻单词，不满足 README 中“非字母数字字符作为分隔符”
> 的明确要求。

因此本轮既不能改判 `blocked`，也不能为了进入 Gate 5 忽略 reviewer 结论改判 `pass`。

## 2. 执行基线与调用预算

```text
branch = experiment/langgraph-comparison
HEAD = private-gate-4-5-r5-preregistration-redacted
origin HEAD = private-gate-4-5-r5-preregistration-redacted
Git worktree before run = clean
Git worktree after run = clean
Codex CLI = 0.144.5
Python = 3.14.3
langgraph = 1.2.9
langgraph-checkpoint-sqlite = 3.1.0
model = sandbox-model
worker reasoning = high
reviewer reasoning = high
expected auth mode = api_key
config mode = isolated_provider
expected provider = sandboxproxy
windows sandbox session override = elevated
automatic retries = 0
```

实际调用：

```text
codex login status commands = 1
preflight provider sessions = 1
worker sessions = 3
reviewer sessions = 3
total provider sessions = 7
provider/model/reasoning/sandbox switches = 0
elapsed = 945.351 seconds
```

没有自动重试，没有切换 endpoint、provider、model、reasoning、sandbox 或认证模式。

## 3. 认证与 Provider Preflight

### 3.1 Provider descriptor

```json
{
  "name": "sandboxproxy",
  "base_url": "http://127.0.0.1:18080/v1",
  "wire_api": "responses",
  "requires_openai_auth": true,
  "supports_websockets": false
}
```

descriptor SHA-256：

```text
dfbc5ee355e628d747bcbcb9e64a26f5ae9be4bab135c84c151397e364898f65
```

### 3.2 脱敏认证模式

```text
expected auth mode = api_key
observed auth mode = api_key
auth mode valid = true
```

harness 只消费认证类型，没有保存 `codex login status` 的原始输出，没有打开 credential
store，也没有读取 API key 值。

### 3.3 Live preflight

```text
status = passed
runner status = success
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning effort = high
sandbox = workspace-write [workdir, /tmp, $TMPDIR]
config mode = isolated_provider
sentinel found = true
command shape valid = true
execution valid = true
fixture repo clean = true
elapsed = 43.685 seconds
```

preflight `execution.json` 为：

```text
status = completed
returncode = 0
termination_unconfirmed = false
```

preflight 阶段没有创建业务 run，也没有修改 fixture workspace。

## 4. 三个业务 Case

| Case | Run | Worker | Reviewer process | Verification | Verdict | Outcome |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `linear-low` | `20260717-125539-015683-bug-loop` | 1 / success | 1 / success | passed | request_changes | quality_failed |
| `graph-low` | `20260717-130110-613737-bug-loop` | 1 / success | 1 / success | passed | request_changes | quality_failed |
| `graph-crash-hitl` | `20260717-130521-200221-bug-loop` | 1 / success | 1 / success | passed | request_changes | quality_failed |

三个 Case 的共同事实：

```text
changed files = ["src/slugify.py"]
untracked files = []
worker start count = 1
worker execution count = 1
reviewer execution count = 1
worker execution = completed / returncode 0
reviewer execution = completed / returncode 0
verification failed count = 0
artifact integrity = true
eval failures = []
```

`linear-low` 与 `graph-low` 的 fixture HEAD 完全相同：

```text
66eb9eeaf93a8726241cf91598731c65a09e7f74
```

### 4.1 Linear

```text
state status = needs_human
current step = done
finish status = not produced
elapsed = 331.413 seconds
```

Linear 没有进入 `ready_to_commit`，因为 reviewer 返回 `request_changes`。

### 4.2 LangGraph low-risk

```text
state status = needs_human
finish status = not_applicable_langgraph
graph state valid = true
checkpoint manifest valid = true
checkpoint SQLite size = 102400 bytes
run status consumable = true
elapsed = 250.247 seconds
```

### 4.3 LangGraph crash + HITL

```text
state status = needs_human
finish status = not_applicable_langgraph
fault triggered = true
worker start count after recovery = 1
decision count = 1
pending count = 1
consumption count = 1
graph state valid = true
checkpoint manifest valid = true
checkpoint SQLite size = 122880 bytes
run status consumable = true
elapsed = 299.770 seconds
```

这证明 recovery、HITL 和一次性 decision consumption 没有因为最终质量失败而破坏安全语义。

## 5. Reviewer 发现

三个独立 reviewer 都指出同一个 major finding：

```text
title = 非 ASCII 分隔符在替换前被直接删除
affected file = src/slugify.py
```

三个 worker 虽然代码细节略有差异，但核心实现都等价于：

```python
ascii_value = (
    unicodedata.normalize("NFKD", value)
    .encode("ascii", "ignore")
    .decode("ascii")
)
return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
```

该顺序对重音字母有效，但会在分隔符正则运行前删除无法表示为 ASCII 的分隔字符：

```text
normalize_slug("foo—bar") -> "foobar"
normalize_slug("foo，bar") -> "foobar"
normalize_slug("foo💥bar") -> "foobar"
```

README 的冻结需求明确写着：

```text
非字母数字字符作为分隔符
```

预期应为：

```text
"foo-bar"
```

因此 reviewer 结论成立，不是误报。

## 6. 为什么现有 Verification 没有发现

R5 fixture 的现有测试覆盖：

- ASCII 空白和标点；
- 重复 ASCII 分隔符；
- Unicode 重音转 ASCII；
- 纯分隔符；
- 非字符串输入。

但没有覆盖：

```text
Unicode 破折号
全角标点
emoji 位于两个单词之间
```

这使三个 worker 都能通过现有 unittest，却仍然违反 README 的一般性要求。

本轮最有价值的真实结论之一是：

> 确定性测试通过不等于需求覆盖完整；冻结 README 与基线测试进入 reviewer evidence 后，
> 单 reviewer 能稳定发现测试未覆盖的需求缺口。

后续修复不能删除或弱化 README 要求，也不能绕过 reviewer。正确动作是把已确认的需求缺口
补成确定性 fixture 测试。

## 7. Evidence 与安全审计

三个 Case 的 `evidence_freshness_valid=false` 只包含：

```text
review_not_approved
latest_iteration_not_approved
```

没有出现 workspace drift、旧 snapshot、内容哈希不一致或证据篡改。Artifact integrity
均为 true；该字段在这里表示“当前证据不足以支持成功”，不是 raw evidence 已过期。

脱敏复审对 R5 output 和三个业务 run 的文本 artifact 共检查：

```text
text files checked = 162
files requiring additional redact_text() changes = 0
```

新证据中没有：

- `Incorrect API key`；
- `Invalid API key`；
- `API key provided`；
- request id；
- `x-request-id`；
- `cf-ray`；
- 明文 credential。

R4 暴露的 credential-like 诊断传播问题已经在 R5 新证据中关闭。

## 8. 为什么是 `partial-pass`

R5 不属于 `blocked`：

- auth mode、provider、model 和 sandbox 可用；
- 7 个 provider sessions 都形成明确终态；
- 没有 timeout、未知终态或外部执行身份漂移。

R5 不属于 `fail`：

- 没有 duplicate worker；
- 没有 duplicate reviewer；
- 没有 scope escape；
- 没有 verification failure 被升级；
- 没有 unsafe resume；
- 没有 silent workspace drift；
- 没有 checkpoint、decision 或 evidence 篡改；
- 没有 credential 泄漏。

R5 也不能属于 `pass`：

- 三个 reviewer 均返回有效 `request_changes`；
- 三个 run 均为 `needs_human`；
- Linear 没有形成 `ready_to_commit`；
- 三个 Case 都没有达到预注册业务成功语义。

因此最终分类固定为：

```text
Gate 4.5 = partial-pass
Gate 5 = 不进入
```

## 9. 下一轮准入顺序

R5 不得重跑或改写。下一步固定为：

1. 提交并推送本结果文档；
2. 在 fixture 基线测试中增加 Unicode 非 ASCII 分隔符回归；
3. 保持 README、任务、文件范围、provider、model、reasoning、sandbox 和预算不降级；
4. 增加 harness 测试，证明新 fixture 初始失败且正确实现通过；
5. 完成完整 fake Core Dogfood 和受影响回归；
6. 形成新的干净实现提交；
7. 冻结独立后续预注册合同和全新 session；
8. 只有后续三个真实 Case 全部 `passed`，Gate 4.5 才能改判 `pass`；
9. 只有 Gate 4.5 `pass`，才允许进入 Gate 5。

这不是为了让模型“背测试”，而是把真实 reviewer 已经证明成立的需求边界转成确定性
verification，减少同一缺陷在不同引擎中重复发生。

## 10. 证据索引

Canonical evidence：

```text
.local-validation/gate-4.5/real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted/summary.json
.local-validation/gate-4.5/real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted/REPORT.md
.local-validation/gate-4.5/real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted/launch-environment.txt
.local-validation/gate-4.5/real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted/preflight/execution/execution.json
.local-validation/gate-4.5/real-core-r5-business-20260717-private-gate-4-5-r5-preregistration-redacted/preflight/execution/process-output.txt
```

Review verdicts：

```text
runs/20260717-125539-015683-bug-loop/iterations/01/review-verdict.json
runs/20260717-130110-613737-bug-loop/iterations/01/review-verdict.json
runs/20260717-130521-200221-bug-loop/iterations/01/review-verdict.json
```

关键 SHA-256：

```text
summary.json =
  1f3560d5467eac2f44b1fde2e3ea56f2804d2e0b8dfaac09282999064ace46e2
preflight-result.json =
  8780678357cd3bfeccc174f773059ea65e3633bed9265068ce1b6a73bd14a7be
REPORT.md =
  a1db017493af4b24499d44c6d7da8f13bf6d895d956f5e4311b470e56acf6256
preflight execution.json =
  c1ee0f44cb2971c486c532884cf4bb534d5e8624411508cf872d090d48f00f3d
preflight process-output.txt =
  22be6d5b798359cccd72f26262962a576a636198163f6a471120b3333a6c0f35
linear review-verdict.json =
  06682a46a0ffd2c41f430d8270e9f35e52f3c16b0bad6a9f00141fb7f15c920e
graph-low review-verdict.json =
  821680d1a75bcca8c3e77378b93ccb4e97d7a2cdde506e78892fde49ac0ddf8c
graph-crash-hitl review-verdict.json =
  196be439d2842d08e8e50b80db01708c37d8e10186c340b5044bae0cd304791f
```

raw evidence、fixture、run、SQLite、模型输出和认证状态均保持 Git 忽略。Git 只提交本结果
文档。
