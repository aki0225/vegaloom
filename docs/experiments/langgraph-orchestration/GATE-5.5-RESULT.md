# Gate 5.5 Reviewer Topology 真实评测结果

> 状态：`completed / single wins`
>
> 真实执行日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> execution baseline：`private-gate-5-5-eval-freeze-redacted`
>
> 冻结 tag：`gate-5.5-pre-run-v1`
>
> 真实 session：`gate55-real-20260719-private-gate-5-5-eval-freeze-redacted`
>
> provider / model：`sandboxproxy / sandbox-model`
>
> auth：`chatgpt`

---

## 1. 结论

Gate 5.5 已按正式预注册合同完成唯一一次真实评测。程序输出的机器结论
`single-wins` 对应预注册术语：

```text
experiment conclusion = single wins
default topology = single
```

这不是因为 single 找到了更多 ground-truth finding，而是因为三种 topology 按冻结的
exact/alias matcher 都没有命中任何一个预注册 finding；在质量收益同为 0 的情况下，
`adaptive` 和 `fixed_three` 只增加了 false positive、clean false-major、调用数、token
和延迟。

因此：

```text
Gate 5.5 real run = completed
Gate 5.5 conclusion = single wins
candidate default topology = single
fixed_three quality advantage observed = false
owner cost confirmation required = false
```

不得在运行后用人工语义相似度把 Reviewer finding 重写为 true positive。负面结果本身就是
本 Gate 的有效结论。

## 2. 执行基线与准入

真实调用前完成了以下检查：

- 远端分支和 annotated tag 均指向
  `private-gate-5-5-eval-freeze-redacted`；
- clean checkout 分支为 `experiment/langgraph-comparison`；
- `HEAD == gate-5.5-pre-run-v1^{commit}`；
- `cases.json` SHA-256 为
  `50d2fac3f04260b6f9bbb13831fd2fbd2b9db39d064d98d5e1f4719d3b042bb1`；
- `ground-truth.json` SHA-256 为
  `2c5839d7c770a3a4e58f918a2c2fdfc5f548b7249dea2256df2281bf3e7a782b`；
- Codex CLI 为 `0.144.5`；
- observed auth mode 为 `chatgpt`；
- `127.0.0.1:18080` 可达；
- 不存在 active Gate 5.5 进程。

第一次普通 Windows clone 因 Git 自动 CRLF checkout 导致磁盘文件 hash 与冻结 LF blob
不一致。该问题在 provider 调用前被 hash 门禁发现，未启动 preflight。正式执行改用
`core.autocrlf=false` 的全新 clean clone，Git blob、磁盘文件与冻结 hash 完全一致。
没有修改 tag、数据集或 commitment。

## 3. 唯一 Preflight

唯一 preflight 通过：

```text
status = passed
elapsed = 8.516 seconds
observed auth = chatgpt
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning = high
sandbox = workspace-write
sentinel found = true
command shape valid = true
fixture repo clean = true
execution valid = true
```

preflight 只接触独立 sentinel fixture。12 个业务 case 的所有 Reviewer session 均使用
`read-only` sandbox。

## 4. Provider 预算与终态

```text
provider sessions used = 82 / 90
preflight sessions = 1
initial reviewer sessions = 72
replicate reviewer sessions = 9
remaining budget = 8
automatic retries = 0
provider session numbers = 1..82 continuous
provider statuses = all success
failures = []
```

初始矩阵和 replicate 的 81 个 Reviewer execution 全部形成 `completed` 终态。没有 timeout、
provider error、parse error、stopped、active 残留或 termination unknown。

总观测 token：

```text
preflight = 11,550
initial matrix = 740,579
replicate = 67,383
total = 819,512
token observation complete = true
```

总墙钟时间为 `1114.656` 秒，约 `18 分 34.656 秒`。

## 5. 初始质量结果

| Topology | TP | FP | FN | Precision | Recall | Blocker/Major Recall | Verdict Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| `single` | 0 | 9 | 9 | 0.0000 | 0.0000 | 0.0000 | 10/12 = 0.8333 |
| `adaptive` | 0 | 25 | 9 | 0.0000 | 0.0000 | 0.0000 | 10/12 = 0.8333 |
| `fixed_three` | 0 | 34 | 9 | 0.0000 | 0.0000 | 0.0000 | 11/12 = 0.9167 |

三种 topology 的 severity-range accuracy 均为 `1.0000`，但没有任何 finding 通过冻结的
identity matcher，因此该指标不能转化为 true positive。

clean case 结果：

| Topology | False Blocker Cases | False Major Cases | False Major Findings |
|---|---:|---:|---:|
| `single` | 0 | 1 | 1 |
| `adaptive` | 0 | 2 | 3 |
| `fixed_three` | 0 | 1 | 3 |

多 Reviewer 没有产生 unique true positive 或 unique true blocker/major。更高的
fixed-three verdict accuracy 也不能覆盖 finding-level TP 为 0、FP 增加和 clean
false-major 的事实。

## 6. 初始成本与延迟

| Topology | Sessions | Total Tokens | Token 倍率 vs Single | Block p50 | Block p95 |
|---|---:|---:|---:|---:|---:|
| `single` | 12 | 130,065 | 1.0000 | 20.586s | 33.016s |
| `adaptive` | 24 | 232,760 | 1.7896 | 24.774s | 32.237s |
| `fixed_three` | 36 | 377,754 | 2.9043 | 34.149s | 43.931s |

`adaptive` 相对 fixed-three 确实减少了调用和 token，但它仍是 single 的 2 倍调用、
1.7896 倍 token，且没有增加任何 true positive。

`fixed_three` 相对 single 是 3 倍调用、2.9043 倍 token，block p50 增加到
`1.6588` 倍，同样没有增加 true positive。

## 7. Replicate 结果

预注册规则触发了 3 个 comparison bundle，共 9 次 Reviewer session、5 个 topology
block：

| Case | Comparison | 触发原因 | 结果 |
|---|---|---|---|
| `clean_project_key_normalization` | adaptive vs single | adaptive clean false-major | adaptive 未复现该优势方向 |
| `clean_endpoint_default_port` | adaptive vs single | adaptive clean false-major | 两边均未形成 candidate-only 优势 |
| `clean_endpoint_default_port` | fixed-three vs adaptive | fixed-three clean false-major | 两边均未形成 candidate-only 优势 |

replicate 没有形成任何第 13.4 节定义的可复现 unique true blocker/major，也没有出现可以
改变 winner 的稳定质量优势。

replicate 成本单独记录为：

```text
provider sessions = 9
total tokens = 67,383
wall-clock sum = 116.047 seconds
topology blocks = 5
```

该成本没有混入日常 topology 成本比较。

## 8. 安全与隔离

最终安全指标全部为 0：

```text
reviewer context leak = 0
workspace writes by reviewer = 0
ground truth prompt leak = 0
cross-topology output leak = 0
cross-replicate output leak = 0
aggregate reconstruction mismatch = 0
```

本结论仍保留预注册残余边界：Windows 同一用户下的 `read-only` sandbox 不是 OS 级读取
保密边界。本 Gate 证明的是 prompt/dataflow 盲评、结构化 artifact 审计和按合同执行时的
真实 topology 收益，不证明可以隔离主动恶意枚举宿主文件系统的 Reviewer。

## 9. 为什么是 Single Wins

`adaptive wins` 不成立：

- 没有相对 single 的可复现 unique true blocker/major；
- finding precision、recall 和 blocker/major recall 没有提升；
- clean false-major case 和 finding 更多；
- 初始调用和 token 明显高于 single。

`fixed_three wins` 不成立：

- 没有相对 adaptive 的可复现 unique true blocker/major；
- finding precision、recall 和 blocker/major recall 没有提升；
- false positive 从 single 的 9 增加到 34；
- token 为 single 的 2.9043 倍；
- 没有形成需要 owner 成本确认的质量优势。

证据完整、成本完整、安全指标为 0，且两个候选 topology 都未满足各自全部质量和成本条件，
因此按预注册第 15.3 节：

```text
single wins
default topology = single
```

## 10. 日期元数据缺陷

raw `summary.json` 和 `REPORT.md` 的 `date` 字段错误写为过去的 `2026-07-18`。根因是冻结
execution baseline 中的 harness 将日期硬编码为 `2026-07-18`。

真实执行日期明确为 `2026-07-19（星期日）`，证据包括：

- session 名 `gate55-real-20260719-private-gate-5-5-eval-freeze-redacted`；
- preflight artifact 创建时间 `2026-07-19 09:53:15 +08:00`；
- final summary 和 report 创建时间 `2026-07-19 10:11:40 +08:00`；
- 当前执行环境日期和时区。

raw artifact 不做回写。结果提交只修复未来运行的日期生成逻辑，不修改本次 prompt、数据集、
ground truth、alias、routing、评分、阈值、raw output 或 winner，也不重跑 provider。
该元数据缺陷不属于预注册第 14 节的 blocked/fail 条件，不影响本轮质量、成本或安全结论。

## 11. Canonical Evidence

本地 canonical evidence：

```text
.local-validation/gate-5.5/gate55-real-20260719-private-gate-5-5-eval-freeze-redacted/summary.json
.local-validation/gate-5.5/gate55-real-20260719-private-gate-5-5-eval-freeze-redacted/REPORT.md
.local-validation/gate-5.5/gate55-real-20260719-private-gate-5-5-eval-freeze-redacted/
  provider-preflight/provider/preflight-result.json
```

SHA-256：

```text
summary.json =
315e8e8411d040f265d2ccaae842caf03640dd06396444d4a6224659c034e988

REPORT.md =
7d3f74f047b9921bd39c0fff3a9dddc3fbf9654ea56387011e96b3f34c928de7

preflight-result.json =
e2165a62c55c429cce79fb74bb3512cc52f674d3d7c04e2b58e2dc88f563c571
```

## 12. 证明与边界

本轮证明：

- Gate 5 的真实只读 Reviewer adapter 可在 82 次连续 provider session 中稳定执行；
- 预算、身份、hash、盲评、DLP、artifact、aggregate 和复跑合同在真实运行中成立；
- 在冻结数据集、provider、model、prompt 和 matcher 下，多 Reviewer 没有提供可复现边际
  收益；
- 默认保持 single 是由预注册规则推出的负面实验结论，不是事后偏好。

本轮不证明：

- single reviewer 在所有模型、任务或 prompt 下都优于多 Reviewer；
- 当前 ground-truth alias 覆盖所有合理的自然语言表达；
- 0 TP 可以在运行后通过人工 fuzzy match 改判；
- LangGraph 应立即成为默认产品引擎；
- Goal/Handoff 或 FastAPI/SSE 已获证明。

本结果作为 [`CORE-DECISION.md`](CORE-DECISION.md) 的输入。
