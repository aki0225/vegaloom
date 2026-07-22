# Gate 5.5 Reviewer Topology 正式预注册合同

> 文档状态：`frozen-before-run`
>
> 合同日期：`2026-07-18（星期六）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 5：`pass`
>
> Gate 5.5：`pre-registered / not run`
>
> 真实 provider 调用：`0（本文编写期间）`
>
> 默认产品 topology：`single`

---

## 1. 授权边界与唯一问题

本文冻结 Gate 5.5 的数据集、真实 Reviewer 身份、调用预算、盲评、评分、复跑、停止条件和
结论规则。本文编写与复核期间不得调用 provider。项目 owner 已在 2026-07-18 当前会话中
明确授权使用当前 provider 执行 Gate 5.5；真实调用仍必须等到 execution baseline 已提交、
readiness 证据通过后才能开始。

Gate 5.5 只回答：

```text
在同一批合成 fixture、同一模型、同一 evidence 和同一 ground truth 下，
single、fixed_three、adaptive 哪一种 Reviewer topology
提供最可信的质量、成本、延迟与稳定性取舍？
```

不得用以下指标代替真实收益：

- finding 总数；
- Reviewer 赞成票数；
- 输出文本长度；
- 三路是否成功并发；
- Demo 是否更复杂；
- 某个 topology 是否更符合预期叙事。

有效实验结论只有：

```text
single wins
adaptive wins
fixed_three wins
no stable winner
```

负面结果是有效结果。默认保持 `single`，除非第 15 节的预注册规则完整满足。

## 2. 冻结执行身份

Gate 5.5 的真实执行身份固定为：

```text
runner = codex-exec
Codex CLI = 0.144.5
expected auth mode = chatgpt
provider = sandboxproxy
provider base URL = http://127.0.0.1:18080/v1
provider wire API = responses
model = sandbox-model
reviewer reasoning effort = high
reviewer sandbox = read-only
preflight sandbox = workspace-write on isolated clean sentinel fixture
session persistence = ephemeral
memory = off
automatic retries = 0
provider/model/reasoning/sandbox/auth switches = 0
```

Provider descriptor 固定为：

```json
{
  "name": "sandboxproxy",
  "base_url": "http://127.0.0.1:18080/v1",
  "wire_api": "responses",
  "requires_openai_auth": true,
  "supports_websockets": false
}
```

### 2.1 Auth 历史边界

2026-07-18（星期六）的实时本地观察为：

```text
codex login status
-> Logged in using ChatGPT
```

因此本轮必须冻结：

```text
expected_auth_mode = chatgpt
observed_auth_mode = chatgpt
```

Gate 4.5 R6 的 `api_key` 是历史实验配置，只能用于解释 R6 历史证据，不得：

- 作为 Gate 5.5 的 expected auth mode；
- 沿用 R6 的认证结论替代本轮 preflight；
- 切换回 `api_key` 以解除本轮失败；
- 把 `chatgpt` 与 `api_key` 混写为等价通过。

真实执行前只记录脱敏后的认证类型，不保存登录状态原始输出、token、Cookie、Authorization
header、credential store 内容或其他凭证。

## 3. 数据集冻结

数据集固定为 12 个互相独立的合成 fixture，四类各 3 个：

| 类别 | 数量 | 目的 |
|---|---:|---|
| `clean` | 3 | 不应产生任何 ground-truth finding，不应出现 false blocker / major |
| `correctness` | 3 | 已知逻辑、边界或需求语义缺陷 |
| `verification_adequacy` | 3 | 验证命令通过，但证据不足以证明需求 |
| `security_design` | 3 | 已知信任边界、安全或持久化设计缺陷 |

冻结文件：

```text
eval/gate-5.5/cases.json
eval/gate-5.5/ground-truth.json
```

2026-07-18 预注册时观察到的 SHA-256：

```text
cases.json =
50d2fac3f04260b6f9bbb13831fd2fbd2b9db39d064d98d5e1f4719d3b042bb1

ground-truth.json =
2c5839d7c770a3a4e58f918a2c2fdfc5f548b7249dea2256df2281bf3e7a782b
```

真实执行基线必须包含本文档和上述两个数据文件。执行前重新计算 SHA-256；任一 hash
不匹配时不得调用 provider，Gate 5.5 分类为 `blocked`。第一次 provider session
启动后不得修改数据集、ground truth、alias、prompt、路由事实、评分代码或结论阈值。

execution baseline 必须由以下不可变 Git tag 冻结：

```text
frozen execution tag = gate-5.5-pre-run-v1
```

该 tag 必须在任何 provider 调用前指向包含本文、冻结数据集、harness、scorer 和测试的已提交
commit。执行时 `HEAD` 必须与 `gate-5.5-pre-run-v1^{commit}` 完全一致；tag 缺失、指向变化、
无法解析或与 `HEAD` 不一致时均为 `blocked`。本轮合同禁止移动、删除或复用该 tag；任何修订
必须使用新的预注册轮次和新 tag。

### 3.1 Case 数量与自适应路由调用数

按照冻结数据集的实际 routing facts，`adaptive` 必须产生：

| 类别 | Case 数 | 每 Case Reviewer 数 | 调用数 |
|---|---:|---:|---:|
| `clean` | 3 | 2 | 6 |
| `correctness` | 3 | 1 | 3 |
| `verification_adequacy` | 3 | 2 | 6 |
| `security_design` | 3 | 3 | 9 |
| **合计** | **12** |  | **24** |

任何 readiness 计算若不是：

```text
adaptive reviewer sessions = 24
```

必须在 provider 调用前 `blocked`，不得临时改 topology 或删除 case 以配平预算。

## 4. Ground Truth Commitment

Ground truth 必须在任何 provider 调用前完成 commitment。commitment 至少绑定：

```text
dataset_id
case_id
expected verdict
expected finding identity
expected severity 或 severity 范围
预先允许的 category aliases
预先允许的 rule aliases
预先允许的 path aliases
预先允许的 location aliases
forbidden false-blocker conditions
ground-truth.json SHA-256
包含 commitment 的 Git execution baseline SHA
```

`cases.json` 的整体 SHA-256 负责绑定全部 task、acceptance、before/after files、
verification 和 routing facts。运行时的 fixture Git HEAD、workspace fingerprint 和
evidence snapshot 包含中性 run identity，只能在具体 Case 创建后生成，因此它们不伪装成
预先已知的 ground-truth 字段；harness 必须在每个 Reviewer session 前记录并把三种 topology
绑定到同一 Case snapshot，评分时再从 result/aggregate 反向复核该绑定。

### 4.1 全部 12 Case 的显式 commitment

以下表格显式冻结每个 case 的 expected verdict 和
`forbidden_false_blocker_conditions`。`[]` 表示该已知缺陷 case 不设置 clean-case
false-blocker 禁止条件，不表示允许删除或忽略其 expected finding。

| Case | Expected verdict | Expected canonical rule | Forbidden false-blocker conditions |
|---|---|---|---|
| `clean_project_key_normalization` | `approve` | `none` | `["any blocker finding", "any major finding"]` |
| `clean_endpoint_default_port` | `approve` | `none` | `["any blocker finding", "any major finding"]` |
| `clean_stable_metric_summary` | `approve` | `none` | `["any blocker finding", "any major finding"]` |
| `correctness_pagination_page_size` | `request_changes` | `correctness.off_by_one` | `[]` |
| `correctness_expiry_exact_boundary` | `request_changes` | `correctness.expiry_boundary` | `[]` |
| `correctness_unicode_tag_separator` | `request_changes` | `correctness.unicode_separator` | `[]` |
| `verification_webhook_mocked_persistence` | `request_changes` | `verification.mocked_subject` | `[]` |
| `verification_receipt_side_effects` | `request_changes` | `verification.missing_side_effect_assertion` | `[]` |
| `verification_discount_threshold_boundary` | `request_changes` | `verification.missing_boundary_case` | `[]` |
| `security_report_output_path` | `request_changes` | `security.path_traversal` | `[]` |
| `security_git_revision_command` | `request_changes` | `security.command_injection` | `[]` |
| `design_atomic_settings_persistence` | `request_changes` | `design.non_atomic_persistence` | `[]` |

三个 clean case 还统一冻结：

```text
expected findings = 0
```

其余九个 case 的完整 expected finding identity、severity 范围和 aliases 由冻结的
`ground-truth.json` 及其 SHA-256 绑定，不得只依据上表重建或放宽。

Ground truth、aliases 和 expected verdict 不得依据 Reviewer 输出补写、删除、扩张或重分类。
如发现 commitment 自身错误，当前 Gate 5.5 必须 `fail`；修正只能进入新的、重新编号的
预注册轮次，不能回写本轮。

## 5. 盲评与防泄漏

Reviewer 只能读取当前 case 的公共 evidence 与自己的 role prompt。不得读取：

- `ground-truth.json`；
- expected finding、severity、alias 或 expected verdict；
- case 的评分类别标签；
- 带 `clean`、`correctness`、`verification`、`security` 等类别含义的 evaluator case id；
- 其他 topology 的输出；
- 同一 topology 其他复跑的输出；
- 其他 Reviewer 的私有 prompt、自由文本、process output 或 canary；
- 汇总评分、中间榜单或候选 winner；
- 本文的决策规则正文。

Reviewer 可见 case id 必须由 harness 映射为中性标识，例如：

```text
case-01
case-02
...
case-12
```

原始 dataset `case_id`、`category` 和中性 id 的映射只允许进入 evaluator-side commitment
artifact，不得进入 Reviewer prompt 或公共 evidence。

语义隔离与字符串 DLP 是两层不同控制。ground truth artifact、其他 topology / replicate
输出和其他 Reviewer 私有上下文必须通过数据流、artifact provenance 与 prompt 构造边界隔离，
不能仅依赖字符串扫描。

每次调用前对最终 prompt 执行的字符串 DLP denylist **只允许**包含：

```text
evaluator IDs
canonical rule IDs
cross-canary
```

其中 evaluator IDs 是原始 dataset `case_id` 等只供 evaluator-side 使用的非中性标识，
不包括 Reviewer 可见的 `case-01` 至 `case-12`；canonical rule IDs 是 ground truth 中冻结的
canonical `rule` 字符串；cross-canary 是当前 Reviewer 不应持有的其他 role、topology 或
replicate 私有 canary。命中任一项时，必须在启动 provider 前 `fail`。

category、rule、path、location 的语义 aliases 以及普通领域词可能自然出现在合成源码、
acceptance、diff 或 verification evidence 中，**不得**把这些 alias 或普通词按字符串加入
DLP denylist，也不得因自然出现而判定 prompt 泄漏。alias 只能用于 evaluator-side 匹配；
是否发生语义数据泄漏必须依据 artifact 来源和字段流向判断，而不是依据 alias 字符串命中。

当前 Windows `read-only` sandbox 约束写入，但不作为同一用户下的 OS 级读取保密边界。本轮
因此只声明 prompt/dataflow 盲评，不声明可以阻止恶意 Reviewer 主动枚举宿主文件系统。
每个 Reviewer 结束后必须审计其完整 process output；只要出现
`ground-truth.json`、`eval/gate-5.5`、预注册合同文件名或任一原始 evaluator case id 的
读取/引用痕迹，立即分类为 `fail`，停止新 session。最终结果必须明确保留这一残余边界。

## 6. 固定 Topology

三种 topology 固定为：

### `single`

```text
required roles = correctness_reviewer
每 Case Reviewer sessions = 1
12 Case 合计 = 12
```

### `fixed_three`

```text
required roles =
  correctness_reviewer
  verification_adequacy_reviewer
  security_design_reviewer

每 Case Reviewer sessions = 3
12 Case 合计 = 36
```

### `adaptive`

`adaptive` 必须使用当前确定性 `adaptive-review-v1` 和冻结 routing facts，不增加 LLM
router，不因 Reviewer 文本改变路由。12 Case 合计固定为 24 次 Reviewer session，见第
3.1 节。

三种 topology 必须保持：

```text
同一 provider
同一 model
同一 reasoning effort
同一 auth mode
同一 evidence 内容
同一 evidence snapshot identity
同一结构化输出 schema
同一 role prompt version
同一 artifact 校验规则
同一 timeout 与输出上限
无跨 Case memory
无跨 topology 输出共享
无跨复跑输出共享
```

只有 ReviewPlan 与 required roles 不同。

## 7. Preflight

真实业务执行前只允许一次 provider preflight session。preflight 使用独立的最小合成
sentinel，不创建 12 个业务 Case 的 Reviewer result，不读取 ground truth。

preflight 必须同时证明：

```text
observed auth mode = chatgpt
Codex CLI = 0.144.5
provider = sandboxproxy
provider base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning effort = high
sandbox = workspace-write [isolated clean sentinel fixture]
session = ephemeral
memory = off
sentinel found = true
execution = completed / returncode 0
termination_unconfirmed = false
fixture before/after = clean
business Reviewer sessions = 0
```

该 `workspace-write` 只用于复用 Gate 4.5 已验证的 provider/model/command-shape preflight，
不得接触 12 个业务 fixture。preflight 之外的全部 Gate 5.5 Reviewer session，包括初始矩阵
与所有 replicate，均固定为 `read-only`，不得降级、继承或切换为 `workspace-write`。

preflight 失败后：

```text
Gate 5.5 = blocked
business Reviewer sessions = 0
automatic retry = 0
```

不得切换到 R6 的 `api_key`、其他 provider、其他 model、较低 reasoning 或可写 sandbox
重新尝试。

## 8. 调用预算

初始 Reviewer 调用预算固定为：

| Topology | Reviewer sessions |
|---|---:|
| `single` | 12 |
| `fixed_three` | 36 |
| `adaptive` | 24 |
| **初始 Reviewer 合计** | **72** |

总预算固定为：

```text
preflight provider sessions = 1
initial reviewer sessions = 72
initial external provider sessions = 73
rerun reserve external provider sessions <= 17
total external provider sessions hard limit = 90
automatic retries = 0
```

Session 必须在启动 provider 前原子预留预算。第 91 次 session 必须在启动前 fail-closed。
不得把 timeout、parse error、provider error、stop 或未知终态从计数中删除；只要外部 session
已经启动，就消耗一个预算。

17 次余额只用于第 13 节定义的预注册实验复跑。实验复跑是新的、显式标记的 replicate，
不是 transport 自动重试，也不得复用原 session。

## 9. 执行顺序

执行顺序固定为：

1. 解析不可变 tag `gate-5.5-pre-run-v1^{commit}`，确认其与当前 `HEAD` 一致，并记录
   execution baseline SHA、工作区状态、数据集 hash、ground-truth hash 和 Codex 版本。
2. 本地观察一次脱敏 auth mode，必须为 `chatgpt`。
3. 执行唯一一次 preflight。
4. preflight 通过后，按 `cases.json` 中的冻结顺序执行 12 个 case。
5. 每个 case 只允许一个 topology block 活跃；不同 case 不得重叠。
6. topology block 的顺序按 case 序号轮换，避免固定顺序偏差。
7. 全部 36 个初始 case-topology block 终态后，才计算初始评分和复跑清单。
8. 只按第 13 节的固定排序执行复跑。
9. 复跑结束或预算不足后，执行一次离线评分与结论生成，不再调用 provider。

Topology 顺序轮换固定为：

| Case 序号 | Topology 顺序 |
|---:|---|
| `1, 4, 7, 10` | `single -> adaptive -> fixed_three` |
| `2, 5, 8, 11` | `adaptive -> fixed_three -> single` |
| `3, 6, 9, 12` | `fixed_three -> single -> adaptive` |

`fixed_three` 和多路 `adaptive` block 内部按 ReviewPlan 同时 fan-out；aggregator 必须与完成
顺序无关。不得同时运行不同 topology 或不同 case 来追求更低总 wall clock。

## 10. 评分输入与结构化终态

只有满足以下全部条件的 Reviewer result 才能进入评分：

```text
status = completed
execution returncode = 0
termination_unconfirmed = false
review plan identity valid
evidence snapshot identity valid
reviewer role 属于 required roles
artifact hash 与 canonical path 有效
aggregate 可从 result artifacts 确定性重建
```

`approve`、`request_changes` 和 `needs_human` 是质量输出，不按文字倾向人工改判。
timeout、stopped、provider error、parse error、active 或 termination unknown 不得利用残缺
输出形成 finding 或 verdict。

## 11. Exact 与 Alias Matching

Finding 匹配只允许 `exact` 或预提交的 `alias`，禁止运行后语义猜测。

### 11.1 Exact match

预测 finding 经过与产品相同的规范化后，以下核心字段与 ground truth canonical identity
全部一致：

```text
category
rule_id
normalized_path
severity 位于预注册允许范围
```

### 11.2 Alias match

只有预测核心字段分别落入该 expected finding 在 commitment 中预先声明的允许集合，才算
alias：

```text
category in {canonical category + category aliases}
rule_id in {canonical rule_id + rule aliases}
normalized_path in {canonical path + path aliases}
severity in allowed severity set/range
```

所有 alias 集合必须在 provider 调用前冻结，且不同 expected findings 之间不得形成歧义交集。

`normalized_location` 用于同一 path/category/rule 下出现多个候选 ground-truth finding 时
消歧。只有一个核心 identity 候选时，不因模型使用 `line:<n>` 或等价结构化位置而拒绝该
finding；出现多个候选时，location 必须 exact 或命中预注册 location aliases，否则计为 FP。

### 11.3 一对一匹配

- 一个预测 finding 最多匹配一个 expected finding；
- 一个 expected finding 最多被计为一个 true positive；
- exact 优先于 alias；
- 多个 Reviewer 报告同一 expected finding，只计一个 TP，其余计入 duplicate；
- 无唯一匹配的预测 finding 计为 FP；
- 未匹配的 expected finding 计为 FN；
- identity 匹配但 severity 不在允许范围时，该预测计 FP，对应 expected finding仍计 FN；
- 禁止人工 fuzzy match、标题相似度 match 或结果后新增 alias。

## 12. 冻结指标

### 12.1 质量

按 topology 记录全量 micro-average，并按四类分别报告：

- finding-level precision；
- finding-level recall；
- blocker / major recall；
- verdict accuracy；
- clean case false blocker case count 与 finding count；
- clean case false major case count 与 finding count；
- 相对 `single` 的 unique true positive；
- 相对 `single` 的 unique true blocker / major；
- raw finding、unique raw finding、duplicate finding 和 duplicate ratio；
- severity-range accuracy。

公式固定为：

```text
precision = TP / (TP + FP)
recall = TP / (TP + FN)
blocker_major_recall =
  matched expected blocker/major / all expected blocker/major
duplicate_ratio =
  (raw findings - semantic unique raw findings) / raw findings
verdict_accuracy = correct verdict cases / 12
```

分母为 0 时：

```text
无 expected finding 且无 predicted finding的 precision = 1
无 expected finding的 recall = 1
无 expected blocker/major 的 blocker_major_recall = 1
无 raw finding 的 duplicate_ratio = 0
```

### 12.2 成本与延迟

每个 Reviewer session 和 topology block 必须记录：

- provider session 序号；
- Codex CLI `tokens used` 提供的 total token；
- prompt 字符数；
- wall-clock latency；
- topology block latency；
- p50 / p95 latency；
- 每个 TP 的 token 成本；
- 每个 unique true blocker / major 的边际 token 成本；
- 相对 `single` 与 `adaptive` 的调用、token 和延迟倍率。

当前 Codex CLI `0.144.5` 不稳定提供 input、cached input 与 output token 分项，因此这些
分项不进入本轮 winner 硬门槛，也不得事后估算。

日常 topology 成本比较只使用初始 12-case 矩阵：

| Topology | 日常成本比较所含 Reviewer sessions |
|---|---:|
| `single` | 12 |
| `adaptive` | 24 |
| `fixed_three` | 36 |

preflight 和第 13 节 replicate 不得混入日常 topology 成本、部署成本或成本倍率。replicate
只用于稳定性确认，必须单独报告 session、total token、wall-clock latency 和预算消耗。
最终报告可以同时展示“初始矩阵成本”和“实验 replicate 成本”，但不得把两者相加后伪装成
某 topology 的日常部署成本。

任一初始 Reviewer session 或 topology block 缺少可信 total token 或 wall-clock latency，
Gate 5.5 必须分类为 `blocked`，不得输出任何 topology winner。证据完整但候选 topology
不满足第 15 节冻结的成本优势条件时，按 `single wins` 处理；不得把“成本证据完整”误写为
“成本更优”。

### 12.3 隔离与安全

必须报告：

```text
reviewer context leak = 0
workspace writes by reviewer = 0
ground truth prompt leak = 0
cross-topology output leak = 0
cross-rerun output leak = 0
aggregate reconstruction mismatch = 0
```

任一非零值按第 14 节分类。

## 13. 显式复跑规则

自动重试固定为 0。只有全部 72 次初始 Reviewer session 已形成可信终态后，才允许生成
复跑清单。

### 13.1 允许触发复跑的条件

每个 decision-relevant comparison bundle 只允许复跑一次。触发条件为：

1. `adaptive` 或 `fixed_three` 在某 case 相对 `single` 产生 unique true blocker / major；
2. `fixed_three` 在某 case 相对 `adaptive` 产生 unique true blocker / major；
3. 某个 clean case 出现 false blocker / major，且该结果会使候选 topology 失去资格；
4. 两个候选 topology 在某个 expected blocker / major 上出现相反命中，且会改变 winner。

provider error、network error、timeout、parse error、stop 或未知终态不触发复跑；它们按第
14 节处理，禁止借“复跑”规避 automatic retries = 0。

### 13.2 Comparison bundle

复跑必须成对比较，不得只重跑有利 topology：

```text
adaptive vs single
  -> 同一 case 重跑 adaptive block + single block

fixed_three vs single
  -> 同一 case 重跑 fixed_three block + single block

fixed_three vs adaptive
  -> 同一 case 重跑 fixed_three block + adaptive block
```

同一 `case + topology` 最多有一个 replicate。replicate 使用新的 run/plan/attempt identity，
但 evidence 内容、model、prompt version 和 routing facts不变；初始输出不得进入 replicate
prompt。

replicate 的调用、total token 和 latency 必须作为实验稳定性成本单独汇总。它们消耗第 8 节
硬预算，但不进入第 12.2 节的初始 12-case 日常 topology 成本比较，也不用于估算部署成本。

### 13.3 固定优先级与预算不足

触发项按以下键排序：

```text
case 在 cases.json 中的序号
comparison kind: adaptive-vs-single, fixed-three-vs-single, fixed-three-vs-adaptive
ground-truth finding id
```

按排序逐个加入完整 comparison bundle。若下一个 bundle 会使总外部 session 超过 90，
不得部分执行该 bundle，立即停止复跑。

未解决项足以影响 winner 时，结论必须是：

```text
no stable winner
default topology = single
```

不得通过选择性跳过高成本 bundle 获得想要的 winner。

### 13.4 可复现 finding

一个“可复现 unique true blocker / major”必须同时满足：

- candidate topology 的初始和 replicate 都 exact/alias 命中同一 ground-truth finding id；
- comparison topology 的初始和 replicate 都未命中该 ground-truth finding id；
- 两次 candidate 输出的 severity 均在允许范围；
- 两次执行均无泄漏、身份、artifact 或终态异常。

没有完整 comparison bundle 时，该 finding 只能报告为初始观察，不能用于切换默认 topology。

## 14. Stop、Fail 与 Blocked

### `stopped`

出现以下任一情况立即停止启动新 session：

- 项目 owner 显式 stop；
- run-level permanent stop latch 已建立；
- 总外部 session 已达到 90；
- 下一个完整复跑 bundle 无法放入剩余预算；
- 检测到未预注册的 provider/model/auth/reasoning/sandbox 切换请求。

如果只是复跑预算不足且初始 72 次 Reviewer 全部可信，允许输出 `no stable winner`；其他
`stopped` 情况不得输出 topology winner，默认继续保持 `single`。

### `blocked`

以下情况表示外部条件或证据不足，当前轮次不能判断：

- observed auth mode 不是 `chatgpt`；
- 唯一 preflight 失败；
- provider、model、network 或 loopback endpoint 不可用；
- 初始 72 次 Reviewer 任一路 timeout、provider error、parse error、stopped、active 或
  termination unknown；
- 数据集 hash、执行基线或 readiness 计数在首次 provider 调用前不匹配；
- 任一初始 Reviewer session 或 topology block 的可信 total token、wall-clock latency、
  result 或 aggregate 证据缺失，无法完成必要比较。

`blocked` 不得自动重试，不得切换到 R6 `api_key`，不得删除失败 case 后继续。

### `fail`

以下任一情况属于实验合同或安全失败：

- ground truth、alias、prompt、routing、评分或阈值在首次 provider 调用后被修改；
- ground truth、类别、原始 evaluator id 或其他 topology 输出泄漏给 Reviewer；
- Reviewer 写入 fixture workspace；
- reviewer private canary 跨角色、跨 topology、跨 replicate 或进入 aggregate；
- 使用真实项目源码、真实业务数据或非合成 fixture 出站；
- 预算超过 90，或已启动 session 未计入预算；
- 自动重试大于 0；
- 选择性删除 case、finding、失败 execution 或不利结果；
- artifact hash、identity 或 aggregate 无法确定性复算仍宣称 winner。

`fail` 不得产出 winner。修复后必须创建新的预注册轮次。

## 15. Winner 与默认 topology 规则

### 15.1 `adaptive wins`

只有以下全部成立：

1. 12 个 case 的初始结果完整可信；
2. blocker / major recall 不低于 `single`；
3. finding recall、precision 和 verdict accuracy 均不低于 `single`；
4. 相对 `single` 至少有一个第 13.4 节定义的可复现 unique true blocker / major；
5. clean false blocker 为 0；
6. clean false major 不高于 `single`；
7. 初始 12-case 矩阵的 total Reviewer sessions、total token 和 latency 完整报告；
8. 初始矩阵的 Reviewer sessions 和 total token 均低于 `fixed_three`；
9. 所有隔离与安全指标为 0。

满足后：

```text
experiment conclusion = adaptive wins
candidate default topology = adaptive
```

产品默认变更仍需单独产品决策，不由实验脚本自动修改配置。

### 15.2 `fixed_three wins`

只有以下全部成立：

1. 12 个 case 的初始结果完整可信；
2. blocker / major recall、finding recall、precision 和 verdict accuracy 均不低于
   `adaptive`；
3. 相对 `adaptive` 至少有一个第 13.4 节定义的可复现 unique true blocker / major；
4. clean false blocker 为 0；
5. clean false major 不高于 `adaptive`；
6. 初始 12-case 矩阵的额外调用、total token 和 p50 / p95 latency 完整报告；
7. 所有隔离与安全指标为 0；
8. 项目 owner 在看到完整成本报告后明确接受固定三路成本。

离线 scorer 或执行程序不得自动宣布 `fixed_three wins`。当第 1-7 项满足、只缺 owner
成本确认时，程序必须先输出：

```text
fixed_three quality advantage observed
experiment conclusion = no stable winner
default topology = single
owner cost confirmation required = true
```

只有 owner 在看到初始矩阵成本和单列 replicate 成本后显式接受固定三路日常成本，人工控制的
最终化步骤才可把结论更新为 `fixed_three wins`；该步骤不得再次调用 provider，也不得修改
原始评分。owner 明确拒绝成本时，结论为 `single wins`。

### 15.3 `single wins`

当初始质量、total token 和 latency 证据完整可信，且 `adaptive` 与 `fixed_three` 均未满足
各自全部质量与成本条件时，结论为：

```text
experiment conclusion = single wins
default topology = single
```

唯一例外是第 15.2 节中 fixed-three 质量条件已满足、程序仅等待 owner 成本确认的暂态；
该暂态必须保持 `no stable winner`，不得进入上述 `single wins` 分支。

以下情况尤其保持 `single`：

- 多 Reviewer 没有可复现 unique true blocker / major；
- 多 Reviewer 只增加 duplicate finding；
- 多 Reviewer 增加 clean false blocker / major；
- 收益无法在预注册 replicate 中复现；
- 完整成本证据显示候选 topology 不满足冻结的成本优势条件；
- owner 在完整成本报告后明确拒绝固定三路成本。

成本或延迟证据不完整不属于 `single wins`，必须按第 14 节分类为 `blocked`。

### 15.4 `no stable winner`

以下任一情况成立：

- 两个候选 topology 同时满足质量条件但无法按规则消解；
- decision-relevant comparison bundle 因 90 次硬预算无法完成；
- 初始优势在 replicate 中发生 finding 或 verdict flip；
- 结果依赖执行顺序、单次异常或不稳定 alias 命中；
- fixed_three 仅显示质量优势，程序正在等待 owner 的显式成本确认。

结论固定为：

```text
experiment conclusion = no stable winner
default topology = single
```

## 16. 数据出站边界

唯一允许发送给 provider 的数据是：

- 12 个合成 fixture 的 Reviewer 可见投影；
- 合成任务、acceptance、before/after diff 和合成 verification evidence；
- 当前 topology 的公共 evidence；
- 当前 Reviewer 的 role prompt；
- 不含类别和 ground truth 的中性 case id；
- 为输出结构化结果所需的 schema。

禁止发送：

- Vega 仓库真实源码或本文之外的项目文档；
- 其他项目源码、文档或 run artifact；
- 真实业务数据、用户数据或生产日志；
- 用户聊天记录；
- Memory ledger；
- `ground-truth.json`、expected finding、alias、expected verdict 或 winner 阈值；
- 其他 Reviewer、topology 或 replicate 的输出；
- `.env`、API key、token、Cookie、Authorization header、credential store、SSH key、
  浏览器或云凭证。

数据出站即使通过 loopback `127.0.0.1:18080` 也按外部 provider 边界管理。发现非合成数据
进入 prompt 时必须在调用前 `fail`；调用后才发现时，立即 stop 并将当前轮次标记为 `fail`。

## 17. 执行前门槛

真实执行前必须同时满足：

1. 当前目录是本项目 Git 根目录；
2. 分支为 `experiment/langgraph-comparison`；
3. execution baseline 已提交并记录完整 SHA；
4. 不可变 tag `gate-5.5-pre-run-v1^{commit}` 可解析且与当前 `HEAD` 完全一致；
5. 本文和冻结数据集均包含在该 tagged execution baseline；
6. 第 3 节两个数据文件 hash 完全匹配；
7. 12 case 四类各 3，且第 4.1 节逐 case verdict / forbidden commitment 完整；
8. `single=12`、`fixed_three=36`、`adaptive=24`；
9. 初始 Reviewer 合计为 72，preflight 后初始外部调用为 73；
10. 总外部调用 hard limit 为 90；
11. Codex CLI 为 `0.144.5`；
12. expected auth mode 为 `chatgpt`；
13. provider/model 为 `sandboxproxy / sandbox-model`；
14. preflight 为 `workspace-write` 且只接触隔离 sentinel；
15. Reviewer 为 `high / read-only / ephemeral / memory off`；
16. automatic retries 为 0；
17. neutral case id 投影和仅含 evaluator IDs、canonical rule IDs、cross-canary 的最终
    prompt DLP 已通过；
18. exact/alias matcher、预算计数器和离线 scorer 的确定性测试通过；
19. 不存在本 Gate 的 active provider、Reviewer 或复跑进程。

任一条件不满足，不得启动 preflight。

## 18. 最终证据

最终结果至少必须记录：

```text
execution baseline SHA
frozen execution tag = gate-5.5-pre-run-v1
dataset / ground-truth SHA-256
auth observation
preflight execution
provider session ledger 1..N
每个 case/topology/role 的 plan、execution、result 和 aggregate
初始 72 次 Reviewer 调用清单
复跑 trigger、bundle、排序和预算消耗
exact/alias matching 明细
每 case 与每 topology score
初始 12-case topology token / latency / error 汇总
单独的 replicate token / latency / budget 汇总
leak / canary / workspace-write 扫描
stop / blocked / fail 事件
程序初步结论、owner 成本确认状态、最终 winner 与 default topology
```

最终必须新增独立结果文档：

```text
docs/experiments/langgraph-orchestration/GATE-5.5-RESULT.md
```

结果文档不得只给汇总数字；必须能从冻结 commitment、原始结构化 artifacts 和 scorer
确定性复算。任何人工解释不得覆盖第 14、15 节的硬规则。

## 19. 当前状态

```text
Gate 5 = pass
Gate 5.5 pre-registration = frozen
Gate 5.5 real run = not started
real provider calls during this documentation task = 0
expected auth mode = chatgpt
R6 api_key = historical only / forbidden for Gate 5.5
provider = sandboxproxy
model = sandbox-model
initial reviewer sessions = 72
preflight + initial sessions = 73
total external provider session hard limit = 90
automatic retries = 0
frozen execution tag = gate-5.5-pre-run-v1
default topology = single
```
