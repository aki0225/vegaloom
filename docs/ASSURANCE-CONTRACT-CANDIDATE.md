# Assurance 威胁与证据充分性候选合同

> 状态：Stage 1 v1 合同已预注册，尚未接入 Runtime 成功判定。
> 基线：Vega `v0.1.2`，`main@775e1b9`。
> 建立日期：2026-07-21。
> 配套验证记录：[`eval/assurance-validation.md`](../eval/assurance-validation.md)。

## 0. Stage 1 v1 可执行合同

本节冻结 Stage 1 的最小机器合同，消除后文候选示例中的歧义。后文的 Threat Family 和纵向
detector 仍属于后续阶段，不在本轮实现。

### 0.1 版本与严格解析

- `Claim`、`Threat`、`EvidenceRecord`、`AssuranceBundle` 和 `AdequacyResult` 均使用
  `schema_version: 1`。
- 所有结构使用严格类型并拒绝未知字段；必填字段缺失、字符串到整数的隐式转换、
  布尔值冒充版本号或不受支持版本均 fail-closed。
- 缺少 `schema_version` 的旧 artifact 可以读取为历史输入，但只能得到 `insufficient`，
  不得升级为 `sufficient_for_merge`。
- Stage 1 只生成独立 Assurance 结果，不修改当前 Finish、Goal 或 Runtime 的成功规则。

### 0.2 AssuranceBundle

顶层 Bundle 必须包含：

```yaml
schema_version: 1
run_id: "..."
iteration: 1
snapshot:
  head_sha: "..."
  staged_diff_sha256: "..."
  unstaged_diff_sha256: "..."
  review_snapshot_id: "..."
  project_policy_snapshot_sha256: "..."
  scope_policy_sha256: "..."
verification_conclusion: verified
claims: []
threats: []
evidence: []
```

`snapshot` 必须同时绑定 HEAD、staged/unstaged diff、Reflect review snapshot、项目策略
snapshot 和非空 scope policy hash。“没有显式 scope”也必须绑定规范化空策略的确定性 hash，
不能使用 `null`。调用方还必须提供独立的可信期望上下文；Bundle 与期望上下文不一致时，
不能相信 Bundle 自己声明的值。

可信期望上下文还必须冻结：

- accepted Claim 集合的规范化 SHA-256。
- active Threat 集合的规范化 SHA-256。
- 允许进入充分性判定的 Evidence 合同摘要。

因此，模型输出即使把自己的来源标签改写为 `deterministic_detector`，或删除确定性 Threat，
也会因来源集合 hash 不匹配而 fail-closed。该期望上下文必须由项目规则或确定性 detector
在接收候选 Bundle 之前建立，不能从待校验 Bundle 自己反推。

### 0.3 Claim 与 Threat 来源

Claim 必须包含稳定 ID、陈述、`accepted/candidate` 状态和结构化来源。Threat 必须包含稳定
ID、类别、来源、`active/candidate` 状态、触发条件、资产、不变量、失败模式、六个风险维度、
Claim 引用、触发证据、最低证据类型、Evidence 引用、残余风险和人工决策标记。

允许的来源及引用前缀为：

| 来源 | 引用前缀 |
|---|---|
| `user_requirement` | `task://` |
| `project_contract` | `file://` 或 `policy://` |
| `machine_policy` | `policy://` |
| `public_contract` | `api://`、`schema://` 或 `file://` |
| `test_oracle` | `test://` |
| `deterministic_detector` | `detector://` |
| `llm_candidate` | `llm://` |

`llm_candidate` 只能保持 `candidate`，不能标记为 `accepted/active`。只有 `accepted` Claim 和
`active` Threat 参与充分性判定；只有 LLM 候选而没有 active Threat 时，结果必须为
`insufficient`。

### 0.4 EvidenceRecord 与 artifact 引用

EvidenceRecord 必须包含：

- 稳定 ID 和证据类型。
- producer、command、environment、input 和 oracle。
- run ID、iteration 和完整 snapshot。
- 结构化 result、覆盖的 Threat、artifact 引用和 limitations。

artifact 引用使用：

```yaml
artifact_type: verification_result
run_id: "..."
relative_path: "iterations/01/verification-result.json"
sha256: "..."
```

Stage 1 v1 只接受当前 run、当前 iteration 的
`iterations/<NN>/verification-result.json`。相对路径不得是绝对路径、不得包含 `..`、盘符、
URI scheme、空路径段或 NTFS ADS；解析后的真实文件必须仍位于当前 run 目录内。引用文件缺失、
目录替代、静态 symlink/junction 逃逸、其他 run/iteration、内容 SHA-256 不一致时，Bundle
完整性失败。

哈希一致只证明字节未被替换，不足以证明 Evidence 语义。校验器还必须解析 verification
artifact v2，交叉检查 run、iteration、shell、命令序号、声明命令、实际命令、verification
临时目录、结果数、失败数、跳过命令、中断状态、退出码和耗时，并要求 EvidenceRecord 的
command/result 与对应结构化命令结果一致。
`verification_conclusion` 由这些 artifact 重算；Bundle 自报结论只能用于一致性检查。
多命令验证若发生中断，允许保留尚未执行的 `skipped_commands`，但必须满足“已完成命令数 +
跳过命令数 = 原始选中命令数”，且总体结论只能是 `interrupted`。

输入 artifact 上限为 2 MiB，单个 Evidence artifact 上限为 4 MiB，单次校验读取总预算为
16 MiB；所有文件使用有界读取，不依赖读取前的 `stat` 结果，相同真实路径只读取一次。
成功和失败读取都会缓存并计入总预算。超限、损坏 JSON、读取期间增长或递归深度异常均写出
独立 `insufficient` 结果，而不是抛出成功路径。

### 0.5 确定性 AdequacyResult

AdequacyResult 只能由 `deterministic_validator` 生成，不接受 LLM 自报结论。判定顺序为：

1. schema、引用、run、iteration 或 snapshot 不一致：`insufficient`。
2. verification 不是 `verified`：`insufficient`。
3. active Threat 缺少任一最低证据类型，或证据未通过：`insufficient`。
4. 证据完整但 Threat 明确要求人工决定：`human_required`。
5. 证据完整且仍有残余上线风险：`requires_staged_rollout`。
6. 所有 active Threat 的最低证据均完整、通过且无上述限制：
   `sufficient_for_merge`。

`sufficient_for_merge` 在 Stage 1 仅表示“合同层证据充分”，不改变当前产品的
`ready_to_commit`，也不声称生产绝对安全。AdequacyResult 必须携带已验证 snapshot、可信
Claim/Threat 集合 hash、Evidence 合同集合 hash 和原始输入 hash，避免结果脱离来源后被重放。

## 1. 目的

Vega 已经能够较强地回答：

- 变更是否属于本轮任务。
- 验证、diff、reviewer 是否绑定同一工作区快照。
- artifact 是否缺失、过期、损坏或相互矛盾。
- 验证失败后 reviewer 是否被禁止覆盖确定性结果。

这些能力主要解决 **Evidence Integrity（证据完整性）**。下一阶段要解决的是
**Evidence Adequacy（证据充分性）**：

> 对本次变更最可能影响生产的威胁，现有证据是否足以支持当前结论；不能证明的风险是否被
> 明确交给人工或上线流程。

本合同不承诺“证明代码绝对正确”，也不输出没有统计基础的可信度百分比。它定义：

1. 如何描述一个与代码变更相关的生产威胁。
2. 如何按风险要求不同类型的证据。
3. 如何区分“命令执行成功”和“证据足以支持交付”。
4. 如何把残余风险、人工判断和分阶段上线要求留在可复核记录中。

## 2. 与现有产品边界的关系

本文件是候选演进合同，不覆盖 `docs/PRODUCT-CONTRACT.md` 的当前事实口径。

- 当前 Runtime 尚未实现本文新增的数据模型和终态。
- Vega 仍不自动 commit、push、release、部署或修改生产数据库。
- “数据库威胁”是指审查目标仓库中的 migration、DDL、DML、ORM 和数据处理变更，
  不是给 Vega 自身引入数据库。
- reviewer 仍不得获得 worker 的完整聊天记录。
- 确定性失败、证据缺失或状态不一致时只能收紧，不能被新能力放松。
- LLM 可以提出候选 threat/claim，但不能删除确定性规则识别出的威胁，也不能自行宣布证据充分。

## 3. 核心概念

### 3.1 Asset

本次变更可能影响的业务或系统资产，例如：

- 用户数据、订单、余额、库存和审计记录。
- 身份、权限、租户边界和密钥。
- API、事件协议和兼容性承诺。
- 可用性、延迟、吞吐、连接池和队列容量。
- 数据恢复能力、rollback 路径和生产可观测性。

### 3.2 Invariant

无论实现方式如何，都必须保持成立的性质，例如：

- 一个订单最多成功扣款一次。
- 任意批量更新只能影响声明的租户和主键范围。
- 滚动发布期间旧应用和新应用都能与目标 schema 正常工作。
- 余额、库存等守恒数据不会因并发更新丢失。
- 失败后可以恢复、继续或明确停止，不留下无法识别的部分状态。

### 3.3 Claim

本次变更声称实现或保持的行为。Claim 必须保留来源：

```yaml
claim:
  id: C-001
  statement: "同一订单重复投递时最多扣款一次"
  source:
    kind: user_requirement
    reference: "task://acceptance/3"
```

来源优先级：

1. 用户原始任务、项目合同和机器可执行策略。
2. 公共 API、schema、测试 oracle 和已接受业务规则。
3. 静态分析或规则引擎生成的候选。
4. LLM 生成的候选。

低优先级来源不能静默改写高优先级来源。

### 3.4 Threat

Threat 使用统一句式：

> 在触发条件 `T` 下，本次变更可能破坏不变量 `I`，对资产 `A` 造成影响 `P`；其爆炸半径、
> 可逆性、可检测性和不确定性决定最低证据要求。

Threat 不是“可能有并发问题”这种宽泛提示，而是可映射到测试、静态检查、演练或人工决策的
具体失败场景。

### 3.5 Evidence

能够降低某个 claim/threat 不确定性的可复核事实，包括：

- 编译、类型检查、静态分析。
- 单元、集成、契约、属性和并发测试。
- 数据库 schema diff、锁分析、迁移演练和数据 reconciliation。
- 故障注入、重试、重复投递、超时和中断恢复测试。
- 性能、资源和容量测量。
- 人工批准、rollback 方案和分阶段上线计划。

“运行了 pytest”不是充分的 Evidence 描述；证据必须说明它覆盖了哪个 threat、使用什么 oracle。

### 3.6 Residual Risk

当前证据仍未覆盖、无法在本地证明或必须依赖真实上线环境判断的风险。Residual Risk 不得藏在
reviewer 自由文本中，必须进入结构化结果和交付报告。

## 4. Threat 数据模型

候选结构：

```yaml
threat:
  id: T-CONC-DUPLICATE-CHARGE
  category: concurrency
  trigger: "同一支付消息被重复投递或超时重试"
  affected_assets:
    - order
    - ledger
  invariant: "一个业务订单最多成功扣款一次"
  failure_mode: duplicate_side_effect
  impact: critical
  exposure: high
  blast_radius: per_order
  reversibility: low
  detectability: delayed
  uncertainty: medium
  trigger_evidence:
    - "diff://src/payment/handler.py"
  required_evidence:
    - duplicate_delivery_test
    - idempotency_test
    - reconciliation_test
  evidence_refs: []
  residual_risks:
    - "第三方支付成功但本地响应丢失"
  status: unverified
  human_required: true
```

每个 threat 至少包含：

- 稳定 ID。
- 触发条件。
- 受影响资产。
- 被威胁的不变量。
- 可观察失败模式。
- 风险维度。
- 触发该判断的代码或配置证据。
- 最低证据组合。
- 当前证据引用和未覆盖风险。

## 5. 风险判定

### 5.1 硬触发器

命中以下任一项时，不能通过其他低风险信号平均降级：

- 数据不可逆丢失或静默损坏。
- 身份、权限、租户隔离或密钥边界变化。
- 金额、库存、积分等守恒数据变化。
- 大表 DDL、索引、约束或批量回填。
- 没有安全 rollback，只能 roll-forward。
- 全租户、核心写链路或核心依赖的广泛爆炸半径。
- 并发重复副作用、lost update、死锁或活锁。
- 消息重试、支付、发货等不可轻易撤销的外部副作用。
- 缺少有效监控，失败可能长期静默存在。

硬触发器默认要求人工确认，并要求风险专用证据。

### 5.2 风险维度

其他 threat 使用六个有序维度，不伪装成精确概率：

| 维度 | 要回答的问题 |
|---|---|
| `impact` | 失败会造成数据、安全、资金、可用性或兼容性方面的什么后果？ |
| `exposure` | 路径调用频率、触发条件和真实流量暴露程度如何？ |
| `blast_radius` | 单请求、单用户、单租户、区域还是全系统？ |
| `reversibility` | 是否能快速 rollback，数据是否可恢复？ |
| `detectability` | 多久能发现，是否可能静默错误？ |
| `uncertainty` | 业务不变量、运行环境和证据边界是否明确？ |

不确定性不是低风险。关键输入缺失时，最低结论是 `human_required` 或 `insufficient`。

## 6. 第一批 Threat Family

第一阶段只纵向验证三类高频生产威胁。未完成前不扩展完整 Risk Engine v2。

### 6.1 数据库迁移

#### Threat 模板

| ID | 失败模式 | 最低证据 |
|---|---|---|
| `T-DB-MIG-COMPAT` | 滚动发布期间旧/新应用与旧/新 schema 不兼容 | 版本兼容矩阵、契约测试、发布顺序 |
| `T-DB-MIG-LOCK` | DDL、索引或约束长时间阻塞生产读写 | 数据库版本相关锁分析、规模演练、时间预算 |
| `T-DB-MIG-DATA` | 类型转换、默认值、回填或约束造成截断、丢失或脏数据 | 前后不变量、row count/checksum、异常样本 |
| `T-DB-MIG-RESUME` | migration/backfill 中断后无法安全恢复或重跑 | 中断注入、幂等测试、断点续跑、roll-forward/rollback |

#### 最低验证矩阵

根据真实发布顺序选择需要的组合：

| 应用 | Schema | 目的 |
|---|---|---|
| OldApp | OldSchema | 基线 |
| NewApp | OldSchema | migration 前部署兼容性 |
| OldApp | NewSchema | 滚动发布共存兼容性 |
| NewApp | NewSchema | 最终状态 |

#### 结论上限

没有接近生产规模的数据演练、锁影响或恢复证据时，即使所有普通测试通过，也不能声称生产安全；
最多进入 `requires_staged_rollout` 或 `human_required`。

### 6.2 数据修改与 Backfill

#### Threat 模板

| ID | 失败模式 | 最低证据 |
|---|---|---|
| `T-DATA-SCOPE` | `UPDATE/DELETE/backfill` 影响超出声明租户或主键范围 | dry-run、row budget、范围断言 |
| `T-DATA-PARTIAL` | 分批执行部分成功，失败后无法判断或恢复 | 事务边界、checkpoint、断点续跑 |
| `T-DATA-RETRY` | 重复执行造成重复写入、重复副作用或二次转换 | 幂等和重复执行测试 |
| `T-DATA-INTEGRITY` | 修改后违反业务守恒、外键、唯一性或审计要求 | 前后不变量、reconciliation、审计输出 |

#### 最低保护

- 执行前 dry-run 和预期影响行数范围。
- 超过硬性 row budget 时停止。
- 明确租户、业务主键和时间范围。
- 分批、断点续跑和重复执行验证。
- 修改前后 row count、checksum 或业务守恒检查。
- 备份、PITR、补偿或 roll-forward 方案。

### 6.3 并发、重试与分布式副作用

#### Threat 模板

| ID | 失败模式 | 最低证据 |
|---|---|---|
| `T-CONC-LOST-UPDATE` | 并发读改写覆盖另一请求结果 | 强制交错测试、版本/锁/CAS 证明 |
| `T-CONC-DUPLICATE` | 重试或重复投递触发重复扣款、发货、通知等副作用 | 重复投递、幂等键、reconciliation |
| `T-CONC-DEADLOCK` | 锁顺序反转、资源竞争导致死锁或活锁 | 强制锁顺序测试、超时和 liveness 证据 |
| `T-CONC-RETRY-STORM` | timeout、重试和下游变慢互相放大 | 故障注入、退避/上限测试、容量观察 |
| `T-CONC-CANCEL` | 请求取消或 lease 过期后后台任务继续写入 | cancel/lease 测试、owned execution 证明 |

#### 最低验证方法

- 使用 barrier、latch 或受控调度强制关键交错，禁止仅依赖随机 `sleep`。
- 注入重复、乱序、延迟、timeout、部分失败和取消。
- 使用多连接、多事务验证数据库隔离语义。
- 对外部副作用验证幂等键、去重和 reconciliation。
- 动态 race detector 只作为一种证据，不能替代业务级并发不变量。
- 同时验证 safety 和 liveness，不能只证明“结果最终正确”而忽略无限等待。

## 7. 后续 Threat Family

只有前三类完成端到端验证后，才按以下顺序扩展：

1. 授权、身份和租户隔离。
2. API、事件和存储兼容性。
3. 性能、容量和资源耗尽。
4. 配置、feature flag 和部署顺序。
5. 依赖、构建和供应链来源。
6. 可观测性、降级、rollback 和灾难恢复。

## 8. Evidence Record

候选结构：

```yaml
evidence:
  id: E-CONC-001
  kind: duplicate_delivery_test
  producer:
    runner: pytest
    version: "8.x"
  command: "python -m pytest tests/test_payment_idempotency.py"
  environment:
    os: windows
    python: "3.12"
    database: "postgresql-16"
    container_digest: null
  snapshot:
    head_sha: "..."
    staged_diff_sha256: "..."
    unstaged_diff_sha256: "..."
    policy_sha256: "..."
  input:
    fixture_sha256: "..."
    scale: "100 concurrent deliveries"
  oracle:
    statement: "每个 order_id 的成功扣款数必须小于等于 1"
  result:
    status: passed
    exit_code: 0
    duration_seconds: 12.4
  covers:
    - T-CONC-DUPLICATE
  artifacts:
    - path: "..."
      sha256: "..."
  limitations:
    - "未连接真实支付服务"
```

可信 Evidence 至少检查六项：

1. **Provenance**：由谁、用什么工具和环境产生。
2. **Freshness**：是否绑定当前 HEAD、diff 和策略。
3. **Directness**：是否直接覆盖目标 threat/invariant。
4. **Sensitivity**：oracle 是否真的能发现错误。
5. **Independence**：是否只是多个共享同一错误假设的相似测试。
6. **Reproducibility**：能否从干净基线按记录重放。

高风险证据应尽量带 negative control：

- 移除幂等保护后测试必须失败。
- 调换 migration 顺序后兼容性测试必须失败。
- 放宽租户过滤后范围测试必须失败。

如果已知错误版本仍能让证据通过，该证据不能支持相应 threat。

## 9. 两层结论

### 9.1 Verification Conclusion

只回答结构化验证执行发生了什么：

| 状态 | 语义 |
|---|---|
| `verified` | 至少一条受信的必需验证完成，且所有必需验证通过 |
| `failed` | 任一必需验证失败 |
| `unknown` | 零命令、显式禁用、只有非结构化日志或证据缺失 |
| `interrupted` | timeout、stopped、termination-unconfirmed 或部分执行状态不确定 |

`reviewer approve` 不能覆盖 `failed/unknown/interrupted`。

### 9.2 Evidence Adequacy

回答现有证据是否足以支持下一动作：

| 状态 | 语义 |
|---|---|
| `sufficient_for_merge` | 本次要求的 threat 均有足够且有效的合并前证据 |
| `requires_staged_rollout` | 合并前证据已达到上限，但仍必须通过 canary/灰度和生产观察 |
| `insufficient` | 存在明确证据缺口，不能自动结束 |
| `human_required` | 涉及不可自动裁决的业务、生产、数据恢复或风险接受决定 |

候选成功规则：

```text
ready_to_commit requires:
  verification_conclusion == verified
  AND evidence_adequacy == sufficient_for_merge
  AND artifact integrity/freshness/scope/risk/review 全部有效
```

高风险变更即使可提交，也不等于“已证明生产安全”。需要真实上线证据的 threat 必须保留
`requires_staged_rollout` 或 `human_required`。

## 10. 非结构化测试日志

人工提供的 `--test-log` 可以作为 reviewer 和人工的补充材料，但默认不能产生 `verified`：

- 无可信 command、exit code、环境和 snapshot 绑定。
- 无法只靠关键词可靠判断测试成功或失败。
- 不能确认日志是否属于当前 diff。

未来若允许导入外部 CI 结果，必须使用结构化 Evidence Record，并校验来源、snapshot、命令、
结果和 artifact。

## 11. 分阶段实施

每个阶段必须满足退出条件后才能开始下一阶段。

### 阶段 0：修正基础成功语义

范围：

- 零验证命令不得进入 `success/ready_to_commit`。
- `--no-verify` 不得进入自动成功。
- 非结构化外部日志不得作为 `verified`。
- verification 中断、artifact 缺失或旧 `skipped` run 必须 fail-closed。
- 修复 adapter junction 越界、Node 包管理器识别和 Finish 重复证据重算。

退出条件：

- 已注册错误场景全部不能自动成功。
- 正常结构化验证通过的安全场景仍能完成闭环。
- Loop、Finish、Goal 对同一验证结论重算一致。

### 阶段 1：Threat 与 Evidence 数据合同

范围：

- 定义版本化 Threat、Claim、Evidence Record 和 Adequacy Result。
- 只支持项目规则和确定性 detector；LLM threat 仅作为候选。
- artifact integrity 能复核新字段、引用和 snapshot。

退出条件：

- 缺字段、伪造引用、错绑 iteration 或 snapshot 时 fail-closed。
- 旧 run 可复盘，但不能因缺少新证据升级成功。

### 阶段 2：数据库迁移纵向闭环

范围：

- migration/DDL/ORM schema 变更识别。
- 兼容、锁、数据转换和恢复 threat。
- 最低证据组合和 adequacy gate。

退出条件：

- 注册的危险 migration 被阻止。
- 对应安全双生案例不会被无差别升级为人工。
- 报告能逐项说明已验证、未验证和 rollout 要求。

### 阶段 3：数据修改纵向闭环

范围：

- DML、backfill、cleanup 和批处理识别。
- scope、row budget、幂等、恢复和 reconciliation。

退出条件同阶段 2。

### 阶段 4：并发纵向闭环

范围：

- 锁、事务、异步任务、消息、重试和外部副作用识别。
- 强制交错、重复投递、故障注入和 liveness 证据。

退出条件同阶段 2。

### 阶段 5：扩展与生产 Handoff

范围：

- 权限、兼容性、性能、配置、供应链和可观测性。
- 导入受信 CI/CD Evidence。
- 生成 canary、监控、停止和 rollback 要求。

Vega 仍不自动部署，只生成并校验交付要求。

## 12. 验证协议

所有新 assurance 能力按以下协议验证：

1. 运行前预注册问题、输入、期望 threat 和成功/失败条件。
2. 每个关键 threat 至少准备一个危险案例和一个安全双生案例。
3. 从干净 baseline 开始，记录 HEAD、策略和 fixture。
4. 一次只改变一个关键变量。
5. 保存结构化结果、关键 artifact 和 SHA-256。
6. 证据不足时记录 `inconclusive`，不得润色成通过。
7. `eval/assurance-validation.md` 只追加记录，不改写历史结论；更正通过新条目追加。
8. fake runner 只证明控制逻辑，不得冒充真实模型或生产环境证据。

## 13. 初始验证目录

### 基础语义

- 零条验证命令 + reviewer approve。
- `--no-verify` + reviewer approve。
- 外部失败日志 + reviewer approve。
- verification timeout/stopped/termination-unconfirmed。
- verification artifact 缺失、过期、错绑或内部字段不一致。

### 数据库迁移

- 大表普通索引构建与在线索引构建双生案例。
- 先加 `NOT NULL`、后回填与 expand/backfill/contract 双生案例。
- 类型收窄导致截断。
- 回填中断后重复执行。

### 数据修改

- 缺少租户范围的批量更新。
- 超出 row budget 的更新。
- 部分成功后的恢复。
- 重跑造成重复转换。

### 并发

- lost update。
- 重复消息导致重复副作用。
- 锁顺序反转死锁。
- timeout/retry 放大。
- 取消后后台任务继续写入。

## 14. 评价指标

不使用单一“可信度百分比”，记录以下可验证指标：

- Critical/High 危险 fixture 的自动误放行数。
- 每类 threat 的识别覆盖率。
- 安全双生案例的误阻塞率。
- threat 到 Evidence 的映射完整率。
- 证据从干净 baseline 的可重复率。
- 人工接管次数与原因。
- 时间、子进程、token 和运行成本。

Critical fixture 的目标不是追求统计概率，而是已注册错误版本不得自动成功。

## 15. 参考资料

- [NIST SP 800-30 Rev.1：Guide for Conducting Risk Assessments](https://csrc.nist.gov/pubs/sp/800/30/r1/final)
- [NIST SP 800-218：Secure Software Development Framework](https://csrc.nist.gov/pubs/sp/800/218/final)
- [OWASP Threat Modeling Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html)
- [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/)
- [PostgreSQL Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html)
- [PostgreSQL Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html)
- [Go Data Race Detector](https://go.dev/doc/articles/race_detector)
- [Google SRE Workbook：Canarying Releases](https://sre.google/workbook/canarying-releases/)
