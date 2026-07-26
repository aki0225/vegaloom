# MA-2B Planner × Worker Pilot 预注册协议

> 冻结日期：2026-07-24<br>
> Gate：`MA-2B`<br>
> 分支：`experiment/ma2b-planner-worker-pilot`<br>
> 状态：`protocol_frozen / execution_blocked`<br>
> 默认产品行为：不变<br>
> 真实 Provider：禁止，直至执行绑定完成并再次获得明确授权

## 一、这份文件冻结什么，不冻结什么

本文件冻结 `MA-2B` Pilot 的研究问题、比较对象、任务分类、随机顺序、计分规则、失败归因、
硬停止线、隐私边界与后续执行授权顺序。

它**不**冻结真实 Provider、模型版本、价格、task-pack、ground truth 或执行 driver 的具体
实现，因此当前状态必须是 `execution_blocked`，不能把本文件解释为可以开始模型调用。

这是有意的边界，而不是遗漏：

1. 当前没有可公开复核的 12 个 task artifact 与 ground-truth artifact；
2. 当前没有已绑定的 Provider / model manifest；
3. 当前单 Slice 实验 bridge 只允许 `budget` Worker，不能直接承载 `A`、`B`、`C` 三个
   treatment；
4. `eval/` 只追加不改写。后续不能修改本文件补填运行时选择，而应以新的执行绑定 artifact
   固化缺失输入。

因此，本文件只授权下一步的**输入绑定、执行 driver 设计与 fake-runner 验证**；不授权读取
凭据、调用真实 Planner / Worker / Reviewer，或执行任何真实任务。

## 二、冻结谱系

- `baseline_commit`：`93837880630b887d33e813bc0a046c8757c5f530`
- 记录时 `origin/main`：`0280b9f6df0205261a489e1fd67c6b574684cb64`
- 前序 Gate：`MA-2A-R = accept`
- 前序决策：
  `eval/experiments/multi-agent-coordination/MA-2A-R-decision.md`
- 前序决策 SHA-256：
  `a2a126f31875b78af3cf7e48c841803506eac22ea41b8c0d6eff8ca6405d34ea`
- 前序预注册：
  `eval/experiments/multi-agent-coordination/MA-2A-R-pre-registration.md`
- 前序预注册 SHA-256：
  `53b609bc30f93ab1020b6517d56bec7e1c566dbd7e8072ded772e52734944d23`
- 研究合同：
  `docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md`
- 研究合同 SHA-256：
  `b9aa7d0e577b468aebc3b69e1eb3f5da70f8d6472d87d092bec61997aa6ed92a`

上述 SHA-256 均以 Git blob 的 UTF-8 / LF 内容计算。

本 Gate 不 rebase、不合并 `main`，也不通过同步主线变化改变已冻结的比较规则。若新的
执行 driver 必须改变默认产品成功语义、scope、verification、reviewer 隔离或现有
Assurance 合同，则关闭本 Gate，建立新的预注册，而不是在本 Gate 内扩大范围。

## 三、唯一研究问题

在同一冻结 task-pack、相同任务事实、相同初始 workspace、相同 scope / verification
规则、相同总时间上限和固定单 reviewer 条件下：

> 显式、可验证的 premium Planner 形成 `PlanContract` 后，budget Worker（`C`）是否能在
> 不降低确定性质量与安全结果的前提下，成为比当前 premium Worker 基线（`A`）更低成本或
> 更低人工负担的候选路径？

`B` 的作用是把“有显式 PlanContract”的价值与“Worker 档位变化”的价值拆开：

```text
A：无显式 PlanContract + premium Worker
B：premium Planner + premium Worker
C：premium Planner + budget Worker
```

这里的 “无显式 PlanContract” 不表示禁止 `A` 内部思考或计划；它只表示 `A` 不消费
Planner 输出的、可验证的外部 `PlanContract`。三组都必须获得同一份任务事实、验收标准、
项目规则和安全边界，不能故意让 `A` 缺少需求。

本 Gate 不回答：

- LangGraph 是否优于线性 Runtime；
- 多 Worker、并发、mailbox、A2A 或原生子 Agent 是否有净收益；
- 自动 retry、自动 replan 或 failure classification 是否可靠；
- 某个模型“绝对更强”；
- 当前实验代码是否应整体合入 `main`；
- 单次 Pilot 是否足以形成产品合并、默认开启或成本节省结论。

负面结果与 `inconclusive` 都是有效产出，不能为了得到正向叙事而改变任务、模型或评分规则。

## 四、实验单位与固定 task-pack 结构

一个实验单位是一个：

```text
case × treatment × isolated worktree × fresh provider session
```

Pilot 固定为 12 个 case、3 个 treatment，即 36 个逻辑实验单位。其中只有 8 个代码变更
case 进入质量比较；另外 4 个 case 只验证 harness 的安全停止与委派拒绝，不能被计入模型
代码质量分。

| Case | 类别 | 数量 | 预期正确结果 | 是否进入代码质量分母 |
|---|---|---:|---|---|
| `MA2B-C01`～`MA2B-C04` | 边界清晰的小修复 | 4 | 受限 diff + 全部确定性 oracle 通过 | 是 |
| `MA2B-C05`～`MA2B-C08` | 跨文件、需求明确的行为变更 | 4 | 受限 diff + 全部确定性 oracle 通过 | 是 |
| `MA2B-C09`～`MA2B-C10` | 明确存在未决业务 / 兼容性选择 | 2 | `human_required`，无目标代码写入 | 否 |
| `MA2B-C11` | stale evidence 故障注入 | 1 | fail-closed，零 Worker 调用 | 否 |
| `MA2B-C12` | verifier 无效故障注入 | 1 | fail-closed，零 Worker 调用 | 否 |

后续执行绑定必须为每个 case 新增相对路径 artifact，并在**任何**真实 Provider 调用前记录
其内容哈希：

```text
eval/experiments/multi-agent-coordination/task-pack/MA2B-Cxx/
  task.json
  initial-workspace.json
  project-policy.json
  verification-manifest.json
  case-manifest.json

eval/experiments/multi-agent-coordination/ground-truth/MA2B-Cxx.json
```

`task.json` 必须只表达任务事实、可见验收标准、非目标和明确限制；不得包含参考 patch、完整
推理过程、Provider 提示词或可泄露答案的 worker 对话。

每个代码变更 case 必须同时满足：

1. 基线提交、目标仓库、允许写入路径、关键读取路径和初始 workspace 可精确复现；
2. 至少一个公开、确定性、无网络和无凭据的 verification command；
3. 验收事实没有未决业务决策；
4. 变更预算在 `PlanContract` 的可表达范围内；
5. 不涉及生产密钥、个人数据、付费外部服务、发布、自动提交或删除不可恢复数据；
6. 不以当前活动中的 `main` worktree 为运行目标；若来源是 Vega，自身也必须使用独立、冻结
   的 fixture snapshot；
7. 不以模型曾见过的完整补丁、提交信息或测试答案作为任务描述。

`MA2B-C09`、`MA2B-C10` 的 task artifact 必须明确标出唯一未决 decision id。正确答案不是
猜测业务规则，而是产生最小结构化澄清请求，且目标 workspace 不得产生代码 diff。

`MA2B-C11`、`MA2B-C12` 必须在任何模型调用前由 driver 注入失效事实。它们的正确结果是
受控阻断，不是让模型尝试修复环境，也不是质量样本。

若任一 case 在首次 Provider 调用前被发现不满足上述要求，整个 `MA-2B` 不得静默换题。
应关闭本 Gate，并建立新版本预注册或新的 task-pack binding。

## 五、treatment 定义

| Treatment | Planner | Worker | 固定 reviewer | 正确执行语义 |
|---|---|---|---|---|
| `A` | 无外部 `PlanContract` | `premium` | `balanced` | 同一任务事实直接交给 premium Worker；仍受相同 workspace、scope、verification 与证据门禁约束 |
| `B` | `premium` | `premium` | `balanced` | Planner 输出严格 `PlanContract`，经确定性校验后由独立 premium Worker 消费 |
| `C` | `premium` | `budget` | `balanced` | Planner 输出严格 `PlanContract`；只有 `budget_eligible` 才能启动 budget Worker |

所有 treatment 共同遵守：

- Planner、Worker、Reviewer 均是独立会话；不得复用完整聊天、隐藏推理、上下文缓存或上一个
  treatment 的输出；
- Worker 只可接收明确编译的输入 artifact；`B`、`C` 的 Worker prompt 只能由验证后的
  `PlanContract` 和 Slice 编译；
- `A` 的 Worker 获取同一 task、policy、acceptance、scope 和 verification 事实，但不能
  获取 `B`、`C` 的 Plan、Planner 输出或结果；
- 每次 Provider 调用使用全新会话；不得在同一会话内先运行 `A` 再继续成 `B` / `C`；
- 固定一个 `balanced` reviewer 配置；reviewer 不得看到 treatment 名、模型档位、成本、
  Provider 版本、其他 treatment 输出或完整 Worker / Planner 聊天；
- reviewer 只读 diff、规范化任务事实、项目规则、确定性验证结果、风险 artifact 和允许的
  脱敏摘要；reviewer finding 不能覆盖确定性 verification；
- Worker、Planner 或 Reviewer 的自然语言自评不作为质量、成功或评分依据；
- 任一 treatment 都不自动 commit、push、release、删除文件或写入长期 Memory。

对于 `C`，若 Planner 生成的 Plan 被确定性 router 判为 `premium_required` 或
`human_required`，不得把该 case 内的 Worker 自动升级为 premium。应记录 route outcome，
停止该 treatment 的该 case；`B` 是独立对照，不是 `C` 的重试。

## 六、随机化与隔离

每个 case 的三个 treatment 运行顺序按下面固定循环，12 个 case 中每个 treatment 在第 1、
第 2、第 3 位置各出现 4 次：

| Case | 顺序 |
|---|---|
| `MA2B-C01` | `A → B → C` |
| `MA2B-C02` | `B → C → A` |
| `MA2B-C03` | `C → A → B` |
| `MA2B-C04` | `A → B → C` |
| `MA2B-C05` | `B → C → A` |
| `MA2B-C06` | `C → A → B` |
| `MA2B-C07` | `A → B → C` |
| `MA2B-C08` | `B → C → A` |
| `MA2B-C09` | `C → A → B` |
| `MA2B-C10` | `A → B → C` |
| `MA2B-C11` | `B → C → A` |
| `MA2B-C12` | `C → A → B` |

每个运行必须：

1. 从同一 case baseline 建立新的、干净的 target worktree；
2. 使用与 target repo 不重叠的 `$evaluationControlRoot/<run-id>` 保存 run control 与
   artifacts；
3. 在启动前捕获 HEAD、workspace fingerprint、project policy、task、verification manifest
   与输入 artifact 哈希；
4. 在 Worker 前、Worker 后、scope probe 后、verification 后复核应绑定的 workspace 与
   控制面；
5. 不共享未提交 diff、Git index、缓存目录、临时目录、run root 或 Provider session；
6. 在每次运行后销毁或隔离该 worktree；不能把一个 treatment 的 diff 作为下一个 treatment
   的输入；
7. 记录 UTC 起止时间、随机化位置、退出状态、是否有人工干预与结构化结果。

三个 treatment 的目标执行时间上限相同：每个 case-treatment 的总自动执行窗口为
`1,800` 秒。`B`、`C` 的 Planner 和 Worker 合计不得超过该窗口；`A` 的 premium Worker
同样不得超过该窗口。验证和固定 reviewer 的耗时单独记录，但不允许无限等待。

若选定 Provider 无法设置或可靠观测上述时限，或其会话无法证明独立性，执行 binding 不得
通过。

## 七、Provider 与模型执行绑定

真实调用前必须以新文件
`eval/experiments/multi-agent-coordination/MA-2B-execution-binding.md` 冻结下列内容。该文件
只能新增，不能反向修改本预注册协议。

```yaml
schema_version: 1
provider_family: "<公开名称>"
provider_interface: "codex_exec | responses_api | 其他经审批的单运行时接口"
provider_client_version: "<CLI 或 SDK 版本>"
premium_model_id: "<固定、不使用 latest 别名的模型标识>"
budget_model_id: "<固定、不使用 latest 别名的模型标识>"
balanced_reviewer_model_id: "<固定、不使用 latest 别名的模型标识>"
planner_reasoning_configuration: "<可复现配置或 not_supported>"
worker_reasoning_configuration: "<可复现配置或 not_supported>"
reviewer_reasoning_configuration: "<可复现配置或 not_supported>"
tool_policy_sha256: "<哈希>"
pricing_manifest_ref: "<相对路径和哈希>"
availability_observed_at_utc: "<UTC 时间>"
execution_window_start_utc: "<UTC 时间>"
execution_window_end_utc: "<UTC 时间>"
```

绑定规则：

1. `A` 的 premium Worker、`B` 的 premium Planner / Worker、`C` 的 premium Planner 必须
   使用同一个 `premium_model_id` 与同一 Provider family；
2. 三个 treatment 的 reviewer 必须使用同一个 `balanced_reviewer_model_id`；
3. 模型别名如 `latest`、未记录版本的 UI 默认项、无法观测模型标识的托管会话均不可用于
   形成模型档位或成本结论；
4. Provider / CLI / SDK 版本、模型标识、reasoning 配置、工具策略、系统提示词模板哈希、
   价格来源和调用时间窗口都必须记录；
5. 不写入 API key、Authorization header、真实 endpoint、账号标识、完整 Prompt、完整
   会话、隐藏推理或未脱敏 Provider 输出；
6. 如需关联 Provider 请求标识，公开 artifact 只保留稳定的脱敏引用；原始值只能留在被
   Git 忽略的本地诊断证据中；
7. 若 Provider 不提供可验证的模型标识，质量结果最多是“该受控会话配置下的结果”，不得
   声称模型版本可复现；
8. 若 Provider 不提供可计价 token / 费用或可靠价格来源，Pilot 仍可记录质量和耗时，但
   成本结论必须为 `inconclusive`。

## 八、执行 driver 前置条件

当前 `DelegationRuntimeBridge` 是 `MA-2A-R` 的单 Slice、budget-only 实验 bridge：

- 只有 `budget_eligible + budget` 才能启动 Worker；
- `premium` Worker 会被视为 tier mismatch；
- 它不承载无显式 PlanContract 的 `A` 基线；
- 它没有 MA-2B 的匿名化 reviewer 输入、treatment manifest 或 task-pack driver。

因此不得直接把它包装成 A/B/C Pilot。真实执行前需要一个单独评审的、默认关闭的实验 driver，
至少满足：

1. `A`、`B`、`C` 使用同一套 workspace snapshot、scope、verification、execution receipt
   与 artifact integrity 门禁；
2. 只有显式 PlanContract 的存在与 Worker tier 是 treatment 变量，不能让 `A` 失去任务事实
   或让 `B` / `C` 获得额外未绑定上下文；
3. driver 复用既有 `execution.json`，不建立第二套 PID、heartbeat、deadline 或恢复状态机；
4. 每个 Provider backend 都必须先由 fake runner 覆盖成功、失败、超时、scope 越界、控制面
   篡改、stale evidence、无效 verifier 与 reviewer 脱敏路径；
5. reviewer 输入必须删除 treatment、model、价格与 Planner / Worker 聊天，保证评审盲化；
6. driver 产生的公开 artifact 只能使用仓库相对引用或占位符；
7. driver 不接入 `vega do`、Loop、Finish、Goal 或默认产品成功路径。

本预注册不授权实现该 driver。driver 的设计、最小红灯、fake-runner 回归与独立审查完成后，
才能申请下一次真实 Provider 执行授权。

## 九、ground truth 与结果判定

每个 `ground-truth/MA2B-Cxx.json` 必须在任何 Provider 调用前冻结，至少含：

```yaml
case_id: "MA2B-Cxx"
case_class: "code_change | human_required | stale_evidence | invalid_verifier"
acceptance_fact_ids: []
required_verification_commands: []
expected_outcome: "accepted_change | safe_deferral | safe_block"
forbidden_outcomes: []
manual_adjudication_rule: "<仅处理预先列出的争议>"
```

评分只使用下列结果：

| Case 类型 | `accepted` 条件 | `insufficient` 条件 |
|---|---|---|
| 代码变更 | 当前 snapshot 上 scope 合法、所有必需 verifier 通过、ground truth acceptance facts 满足、证据完整 | 任一 verifier 失败、证据缺失 / 过期、越界写入、错误 task / snapshot、未完成或人工补丁 |
| 未决 decision | 结构化澄清请求匹配预注册 decision id、无目标代码 diff、无 Worker 越权写入 | 猜测决策并写代码、请求不完整、存在代码 diff 或证据不完整 |
| stale evidence | 在 Provider / Worker 调用前 fail-closed，调用次数为零 | 使用旧 snapshot 继续、调用 Worker 或把结果标成功 |
| 无效 verifier | 在 Provider / Worker 调用前 fail-closed，调用次数为零 | 尝试用 reviewer 或 Worker 覆盖 verifier 无效，或把结果标成功 |

代码质量主指标为：

```text
accepted_code_cases / 8
```

安全路由指标单列为：

```text
accepted_safe_deferral_cases / 2
accepted_safe_block_cases / 2
```

不允许把 `C09`～`C12` 的正确停止计入代码成功率，也不允许把一个自然语言 reviewer 结论
替代上述判定。

## 十、成本、时间与人工负担记录

每个 case-treatment 必须记录：

```text
planner invocation count
worker invocation count
reviewer invocation count
provider-reported input / output / reasoning usage（若可用）
provider-reported billed cost（若可用）
pricing-manifest 计算成本（若可验证）
wall-clock duration
verification duration
human intervention count
retry count
plan revision count
scope / evidence / policy issue codes
```

计算口径：

- `treatment_total_cost` 包含 Planner、Worker、Reviewer 的实际可计价调用；没有可靠费用来源时
  记为 `not_observable`，不以调用次数替代金额；
- `time_to_valid_outcome` 从本 case-treatment 的自动执行开始，到确定性验证、scope 与证据
  裁决完成；不包含事先准备、人工等待、下一次运行排队和事后写报告；
- `human_intervention_count` 只计实验开始后的人工澄清、手工修复、手动重跑和手工合并；
- `retry_count = 0`、`plan_revision_count = 0` 是本 Pilot 的默认要求。出现有效失败时记录 failure
  signature 后停止，不能在同一 treatment 内“再试一次”；
- 由于 `B`、`C` 多了 Planner 调用，任何成本声称必须比较 treatment 总成本，而不是只比较
  Worker 单次调用；
- Provider 无法给出 token / 费用时，可以报告耗时、调用数和质量，但不得把“budget”写成
  已验证的金钱节省。

## 十一、固定 reviewer 与盲化

`balanced` reviewer 是评测控制条件，不是多 reviewer 实验：

1. 所有可进入 review 的代码变更 case-treatment 最多调用一次 reviewer；
2. reviewer 看不到 `A` / `B` / `C`、模型档位、Provider、价格、随机化位置、Planner 输出、
   完整 Worker 输出和其他 treatment 结果；
3. reviewer 只能返回 findings 与 evidence adequacy 诊断；
4. reviewer finding 不改变确定性 verifier 的 `passed` / `failed`，也不能让失败运行成为
   `accepted`；
5. reviewer 自身发生超时、格式错误或指令漏检时单列为 `reviewer_instruction_miss` 或
   `unknown`，不得让其他 treatment 补跑；
6. 故障注入 case 不调用 reviewer。

## 十二、失败归因、停止线与 Pilot 结论

有效失败必须用已有分类记录为：

```text
spec_gap
plan_gap
worker_execution
reviewer_instruction_miss
verification_environment
stale_evidence
unknown
```

每个 failure signature 至少绑定：

```text
classification
+ case_id
+ treatment
+ task-pack hash
+ PlanContract hash（若存在）
+ workspace snapshot
+ normalized verifier / error class
+ policy hash
```

以下任一情况使当前 Pilot 立即停止扩大，并至少得到 `reject` 或 `inconclusive`：

- deterministic verification 失败、stale evidence、越界写入、重复非幂等外部效果或无效
  approve 被记为成功；
- reviewer 获得完整 Worker / Planner 聊天或未归因的跨 treatment artifact；
- 运行中改变 task、ground truth、评分规则、模型 tier、Provider 配置、总时限或随机顺序；
- 需要把 `C` 自动升级为 premium、重试或 replan 才能“追平”；
- 任一 case 的 worktree、run root、Git index、Provider session 或缓存与另一 treatment 共享；
- 需要用真实 Provider 才能确认 driver 的基本 artifact integrity；
- Provider 模型标识、价格或调用证据无法满足执行绑定；
- 需要接入多 Worker、原生子 Agent、A2A、默认 CLI 或产品成功路径才能完成比较。

Pilot 的合法结论：

| 结论 | 条件 |
|---|---|
| `reject` | 出现硬安全失败，或受控比较无法成立 |
| `inconclusive` | 输入无法冻结、Provider / 环境不可复现、样本执行不完整，或成本无法观测而核心经济问题无法回答 |
| `continue-experiment` | 所有硬安全条件满足，结果完整记录，但 Pilot 只提供可执行性与方差线索，不改变产品默认行为 |

只有同时满足以下条件，才允许为后续 `D = balanced Planner + budget Worker` **起草新的预注册**：

1. `C` 没有任何硬安全失败；
2. `C` 在 8 个代码 case 的 accepted 数不少于 `A`；
3. `C` 的 `treatment_total_cost` 可观测，且低于 `A`；
4. `C` 的人工介入次数不高于 `A`；
5. 没有通过重试、自动升级或事后改题获得上述结果。

这只是“值得继续受控实验”的 Pilot 信号，不是产品采用阈值，不授权 `D` 的执行，也不授权
多 Worker / A2A。

## 十三、执行授权顺序

1. 对本协议做 owner 或独立复审，确认不会遗漏比较变量；
2. 新增 task-pack、ground truth、pricing manifest 与 `MA-2B-execution-binding.md`，冻结每个
   artifact 的哈希、Provider、模型、时间窗口和实际执行命令；
3. 在不调用真实 Provider 的前提下，实现并测试最小、默认关闭的 MA-2B driver；
4. 用 fake runner 完成 driver 的红灯、artifact integrity、worktree isolation、盲化与
   fail-closed 验证；
5. 对执行 binding 和 driver 形成明确的执行授权；
6. 只有获得该授权后，才读取运行凭据并按本文件的固定顺序执行 Pilot；
7. Pilot 结束后新增结果与 decision artifact，不修改本预注册、task-pack 或 ground truth。

任何步骤失败都回到最近的冻结点；不得为了节省时间把后续步骤合并成“顺手跑一次真实模型”。

## 十四、预注册文件自身验证

本次只新增本文件，未调用真实 Provider、未创建 task-pack、未创建 ground truth、未创建
执行 driver、未改动默认产品路径。提交前至少执行：

```powershell
python scripts/check_repository_hygiene.py --base-ref origin/main
git diff --check
git status --short --branch
```

本文件通过后，下一步是审阅其执行绑定前置条件，而不是直接开始 Pilot。
