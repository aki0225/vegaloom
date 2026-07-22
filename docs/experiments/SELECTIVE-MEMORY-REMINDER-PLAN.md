# Selective Memory Reminder 实验计划

## 文档状态

- 状态：Phase 0、Phase 1 与完整 10-case Phase 2 已实施；当前停在离线评审。
- 日期：2026-07-13。
- 当前动作：审阅完整 Phase 2 证据；不修改 runtime，不运行真实 LLM，不进入 Shadow。
- 实施前提：已完成。`main` 已冻结并创建可复现的 `v0.1.0` tag。
- 实验基线：公开标签 `v0.1.0`；私有源提交身份在公开归档中记为
  `<source-phase-0-baseline>`，详见 Phase 0 报告。
- 主线原则：实验不得改变 v0.1.0 的默认行为、成功条件、退出码或必需 artifacts。
- 停止线：完整 Phase 2 完成后必须停止，只有用户再次明确授权才能进入 Phase 3。
- 范围纪律：本计划的完备度已足够启动。此后只允许收敛和修正正确性漏洞，不再扩张 case 数、
  阶段数或字段数。最小可交付档（3-case + 决策）达成即停，不得以“更完整”为由无限延期。
  一个跑出真实决策的小实验，价值高于一份永远没执行的完美计划。

2026-07-13 起项目更名：品牌 Vega（织女星），发布名 vegaloom，Python 包与 CLI 均为 vega（原名 LoopForge）。
历史文档与旧 run artifacts 中的旧名指同一项目；改名与本实验相互独立，不是实验实施的前置条件。

## 1. 实验摘要

本实验研究的不是“保存更多聊天记录”，而是：

> Vega 能否维护少量、带证据和生命周期的任务记忆，在 Coding Agent 即将重复失败、
> 偏离最新目标、绕过审批或错误恢复 Session 时才发出提醒，并以低于 Always-on Memory
> 的上下文成本改善执行质量。

实验比较四种模式：

| 模式 | Memory 策略 |
|---|---|
| A：No Memory | 不维护 Run-local Memory，不注入 Memory。 |
| B：Always-on Memory | 每个 checkpoint 注入完整 active Memory。 |
| C：Top-K Memory | 每个 checkpoint 注入固定 K 条候选。 |
| D：Selective Reminder | 使用与 C 相同的候选，只在存在具体风险时注入短提醒。 |

四组共享相同任务、Canonical State、安全门禁、候选生成规则和执行条件。唯一主要变量是
Memory 的维护与注入策略。

若本计划经用户评审通过，第一轮拟议实施范围只包含：

1. Phase 0：冻结和记录实验基线。
2. Phase 1：独立 schema、事件重放、snapshot 和确定性策略。
3. Phase 2：离线数据集与 A/B/C/D evaluator。

即使后续批准第一轮，也不接真实 worker，不修改 worker prompt，不扩展长期 Memory，不声称已经改善真实任务成功率。

## 2. 背景与动机

Vega v0.1 已有以下基础：

- run artifacts、state、trace 和中断恢复证据。
- Memory proposal 与人工 accept/reject。
- 本地 JSONL accepted memory ledger。
- 按仓库、路径和关键词检索 accepted memory。
- 将项目规则、项目画像和 accepted memory 编译进项目上下文。

现有机制解决了“如何显式沉淀和检索经验”，但没有回答：

- 一条任务内记忆何时生效、何时失效。
- 新需求如何替代旧需求。
- 已失败方案在前置条件变化后能否合理重试。
- worker/reviewer 推断能否成为事实。
- 当前 checkpoint 是否真的需要注入某条记忆。
- 同一提醒如何避免重复污染上下文。
- Session 重启后如何恢复最新目标和限制。
- Memory 的收益是否大于上下文、延迟和额外模型调用成本。

因此，本实验优先研究“状态所有权、记忆治理和提醒策略”，暂不引入向量数据库、
embedding、数据库或自动长期学习。

## 3. 研究问题

1. Selective Reminder 是否能减少前置条件未变化时重复已确认失败方案？
2. Selective Reminder 是否能降低继续执行已被新需求替代目标的概率？
3. Selective Reminder 是否能改善 Session 恢复后的行动一致性？
4. Selective Reminder 是否能以显著低于 Always-on 的注入规模获得相近效果？
5. 来源、证据、生命周期和冲突规则能否控制过期提醒与 Memory 污染？
6. Judge 或 Intent Preview 的总成本是否会抵消上下文节省？

无效结果同样有效。以下情况可以直接得出 `reject`：

- D 没有稳定优于 B/C。
- 误报、过期提醒或错误阻止不可接受。
- Preview 与真实动作相关性不足。
- 额外调用成本抵消收益。
- 状态所有权无法保持清晰。

实验结论不得因为已经写了代码而改变。

## 4. 产品与安全边界

### 4.1 本实验包含

- Run-local Working Memory。
- append-only 增量事件。
- 可从事件完全重建的 snapshot。
- 来源、证据、权限、生命周期、适用条件和失效机制。
- 不使用向量检索的候选选择。
- `allow / remind / block / escalate` 决策。
- 最近三个 checkpoint 的提醒去重。
- 离线 Replay、Shadow 和跨 Session 恢复评估计划。
- 可复现的原始计数、比例和成本报告。

### 4.2 本实验不包含

- 数据库、SQLite、向量数据库或 embedding。
- 自动长期 Memory 写入或自动 accept。
- 完整聊天记录、Chain-of-Thought 或隐藏推理存储。
- 常驻 Memory daemon 或自主递归 Memory Agent。
- 多 Agent 编排平台。
- Web UI。
- 自动 patch、commit、push 或 release。
- 修改 v0.1.0 baseline 的成功条件、退出码或必需 artifacts。
- 仓库改名、品牌改名或包名迁移。

### 4.3 Updater 和 Judge 的含义

Memory Updater 和 Reminder Judge 是无工具、无自主循环的结构化组件：

- 优先实现为确定性函数。
- 必要时才允许一次受限的结构化 LLM 分类调用。
- 不拥有代码修改权限。
- 不启动子 Agent。
- 不自行执行任务。
- 不直接写 accepted memory ledger。
- LLM 输出默认只能成为候选，不能直接成为权威事实。

## 5. 状态所有权

### 5.1 Canonical State

Memory 可以影响行动，但不能替代以下权威状态：

| 权威来源 | 负责内容 |
|---|---|
| 当前用户输入和 task brief | 最新目标、非目标、验收标准和明确约束 |
| `state.json` / `goal-state.json` | run 与 Goal 生命周期状态 |
| goal contract / checkpoint plan | 长任务阶段、停止条件和开放 checkpoint |
| `decisions.jsonl` | 当前支持的人工批准和拒绝记录 |
| `verification-result.json` | 确定性验证结果 |
| `review-verdict.json` | reviewer 结论，分析文本仍不自动成为事实 |
| 当前仓库与 workspace snapshot | 当前代码、diff、文件和工作区事实 |
| `AGENTS.md` / `.vega.yaml` | 项目规则、验证命令、预算和风险策略 |
| accepted memory ledger | 人工接受的跨任务经验，优先级低于当前事实 |

Canonical State 安全门禁对 A/B/C/D 四组共同生效。A 组“无 Memory”不等于关闭审批、
验证、项目规则或其他已有安全边界。

### 5.2 Run-local Working Memory

Run-local Memory 只保存不能通过一次权威状态读取稳定恢复、但可能显著影响下一步行动的
最小信息，例如：

- 已被证据确认、后续仍可能被遗忘的执行事实。
- 当前任务中的解释性约束或适用范围。
- 已验证失败的方案及其适用条件。
- 尚未验证、需要继续验证的假设候选。

以下内容第一版不复制成 Run-local Memory Item：

- 当前用户最新要求。
- 当前 Goal、checkpoint 和开放子目标。
- 当前审批状态。
- 当前 run 状态。
- 项目规则原文。
- accepted memory 原文。
- 已被替代的目标版本。

这些内容在决策时直接从 Canonical State 投影为候选，避免出现第二套相互冲突的状态机。

### 5.3 Accepted Long-term Memory

长期 Memory 沿用现有流程：

```text
run-local evidence
  -> memory proposal
  -> 人工 accept / reject
  -> memory/ledger.jsonl
```

实验不得跨过人工决策，也不得修改 v0.1 的 `MemoryProposal`、`MemoryLedgerEntry` 或 ledger
写入规则。

### 5.4 知识优先级

```text
当前用户指令
  > 人工审批与决策
  > 当前可复现验证证据
  > 当前仓库和 workspace 事实
  > AGENTS.md / .vega.yaml
  > accepted memory
  > worker / reviewer 推断
  > 外部工具原始文本
```

低优先级内容不能覆盖高优先级内容。同级冲突不能静默合并，必须 `escalate`。

## 6. 实验数据契约

实验 schema 必须独立于现有长期 Memory 模型，至少分为以下结构：

1. `RunMemoryItem`：run 内带证据和生命周期的执行记忆。
2. `MemoryEvent`：对 RunMemoryItem 的 append-only 增量操作。
3. `MemorySnapshot`：由事件重建的派生视图。
4. `InterventionCandidate`：Run Memory 与 Canonical State 的统一只读候选。
5. `ReminderDecision`：对当前 planned action 的干预决定。

不得用一个“大而全”的 Memory Item 同时承担状态、候选和决策职责。

### 6.1 RunMemoryItem

```json
{
  "schema_version": 1,
  "id": "failure-003",
  "task_id": "task-001",
  "run_id": "run-001",
  "repo_identity": "repo-fingerprint",
  "kind": "failed_attempt",
  "statement": "依赖升级方案 A 会造成当前 API 不兼容",
  "status": "active",
  "source_type": "verification",
  "source_ref": "event-018",
  "evidence_refs": [
    {
      "artifact": "iterations/01/verification-result.json",
      "sha256": "<sha256>"
    }
  ],
  "authority": "verified",
  "risk": "high",
  "applicability": {
    "dependency_version": "1.x",
    "api_version": "v2"
  },
  "created_seq": 18,
  "updated_seq": 18,
  "replacement_id": null,
  "invalidation_reason": null
}
```

第一版允许的 `kind`：

- `confirmed_fact`
- `constraint_interpretation`
- `failed_attempt`
- `open_hypothesis`

第一版允许的 `status`：

- `candidate`
- `active`
- `invalidated`
- `superseded`
- `rejected`

第一版允许的 `authority`：

- `verified`
- `inferred`
- `untrusted`

当前用户指令、人工决策和项目规则不作为 RunMemoryItem 存储，而由 Canonical State 直接提供。

第一版不引入浮点 `confidence` 字段。一个“不参与任何硬性决策、只做分析元数据”的分值，
要么日后被悄悄用于 `block`/`escalate`（风险），要么沦为死字段（冗余），两头不讨好，还多
一个被误用的口子。硬决策只依据来源类型、证据、适用条件和明确规则。确有分析需要时再单独
引入，并同时补“decision 路径不读该字段”的回归测试。

### 6.2 MemoryEvent

```json
{
  "schema_version": 1,
  "event_id": "me-000021",
  "seq": 21,
  "op": "invalidate",
  "memory_id": "failure-003",
  "patch": {
    "status": "invalidated",
    "invalidation_reason": "依赖版本已变化",
    "replacement_id": "failure-009"
  },
  "source_type": "verification",
  "source_ref": "trace:event-021",
  "created_at": "2026-07-12T12:00:00Z"
}
```

只允许三种操作：

- `add`
- `update`
- `invalidate`

约束：

- `add` 创建新 item。
- `update` 只能补充证据、风险和适用条件，不能静默重写 `statement`。
- 语义变化时必须新增 item，并 invalidate/supersede 旧 item。
- `invalidate` 必须记录原因；被替代时必须记录 replacement ID。
- seq 重复、倒序、缺失或 repo/run 绑定不匹配时 fail-closed。

### 6.3 MemorySnapshot

```json
{
  "schema_version": 1,
  "task_id": "task-001",
  "run_id": "run-001",
  "source_event_count": 21,
  "source_events_sha256": "<sha256>",
  "active_items": [],
  "candidate_items": [],
  "invalidated_items": [],
  "conflicts": []
}
```

Snapshot 是派生缓存，不是事实来源。它必须能从事件日志完整重建；event count 或 SHA-256
不一致时必须丢弃旧 snapshot 并重建。

### 6.4 InterventionCandidate

```json
{
  "candidate_id": "canonical:approval:deploy",
  "source_layer": "canonical_state",
  "source_ref": "decisions.jsonl#latest-deploy",
  "kind": "pending_approval",
  "statement": "部署操作尚未获得有效批准",
  "authority": "authoritative",
  "risk": "high",
  "applicable": true
}
```

候选集合由两部分组成：

```text
Active RunMemoryItem
  + Current Canonical State Projection
```

C 与 D 必须使用完全相同的候选集合、顺序和 Top-K 参数。

### 6.5 ReminderDecision

```json
{
  "checkpoint_id": "cp-012",
  "decision": "remind",
  "reason_code": "repeats_failed_attempt",
  "risk": "high",
  "candidate_ids": ["memory:failure-003"],
  "reminder": "当前适用条件未变化，不要再次尝试依赖升级方案 A。",
  "dedupe_key": "failure-003:repeats_failed_attempt",
  "suppressed_by_dedupe": false,
  "decision_source": "deterministic_rule"
}
```

第一版 `decision`：

- `allow`
- `remind`
- `block`
- `escalate`

第一版 `reason_code`：

- `repeats_failed_attempt`
- `violates_constraint`
- `pending_approval_conflict`
- `superseded_goal`
- `conflicting_candidates`
- `evidence_stale`
- `session_resume_risk`
- `none`

### 6.6 去重

- 同一 `dedupe_key` 最近三个 checkpoint 已注入过时，不重复注入。
- 高风险 `block/escalate` 不受普通提醒去重限制。
- 相关候选状态变化后允许重新提醒。
- Session 恢复时允许重新提醒。
- Agent 再次明确计划执行同一冲突动作时允许重新提醒。
- 被抑制的决定仍写入实验结果，便于统计。

## 7. 来源与更新规则

### 7.1 Checkpoint

Vega 当前不能可靠观察外部编码会话内部每次工具调用，因此不把模型内部动作伪装成
可观测 step。第一版只使用 Vega 能稳定记录的 checkpoint：

- 用户新增或修改需求。
- worker attempt 开始或结束。
- verification 完成。
- reviewer 返回。
- 人工决策新增。
- `loop continue`。
- `stop/recover`。
- Session restart/resume。

### 7.2 来源权限

| 来源 | 第一版处理 |
|---|---|
| 当前用户输入 | 留在 Canonical State，立即生效 |
| 人工 decision | 留在 Canonical State，立即生效 |
| verification | 可生成 verified RunMemoryItem |
| 当前仓库事实 | 决策时读取，不复制稳定事实 |
| reviewer 输出 | 只能生成 inferred candidate |
| worker 输出 | 只能生成 inferred candidate |
| 外部工具原始文本 | 只能生成 untrusted candidate |

自由文本提取可以使用受限 LLM，但只能生成 candidate event，不能自动升级为 verified，
不能直接写长期 ledger。

### 7.3 审批撤销的当前限制

v0.1 的 `DecisionStore` 只支持 `approved/rejected`，尚无 `revoked` 状态。因此：

- Phase 1–2 可以在离线 fixture 中表达“批准后来被撤销”的语义。
- 离线 fixture 不得伪装成当前 runtime 已具备撤销能力。
- 是否扩展 runtime decision contract 必须在 Phase 3 前单独评审。
- 未获再次授权前，不修改 `DecisionStore`。

## 8. Planned Action 的来源

Reminder Judge 需要 `planned_action`，但当前 runtime 没有稳定的结构化行动预告。实验分阶段处理：

### Phase 1–2：离线 Replay

每个 case 直接提供结构化 `planned_action` 和 golden label，用于验证 schema、候选选择和决策策略。

### Phase 3：Shadow Mode（不属于本轮实施）

只记录 checkpoint 后可观察到的动作，做事后分析，不改变真实 worker prompt。

### Phase 4：选择性注入（不属于本轮实施）

只有离线和 Shadow 指标达标后，才考虑受限 Intent Preview：

- 输入仅包含当前目标、下一轮任务摘要和候选。
- 输出为少量结构化 intended actions。
- 无工具权限，不修改仓库。
- Preview 不是事实，只是 Judge 输入。
- Preview 与 Judge 的字符、token、延迟和费用全部计入 D 总成本。

如果 Preview 与真实动作相关性不足，停止产品化。

## 9. 四组实验设计

四组共享：

- 相同 case、事件序列和 Canonical State。
- 相同已有安全门禁。
- 相同候选生成规则。
- 相同 base model/provider（进入 LLM 阶段后）。
- 相同 runner、验证命令、超时、重试和最大迭代次数。
- 相同 accepted memory 输入；第一轮建议关闭。
- 相同 case 顺序和随机化策略。

### A：No Memory

- 不维护 RunMemoryItem。
- 不注入 Memory。
- 保留 Canonical State、安全门禁、普通 state/trace。

### B：Always-on Memory

- 维护相同 RunMemoryItem。
- 每个 checkpoint 注入完整 active Memory。
- 用于观察高上下文成本基线。

### C：Top-K Memory

- 维护相同 RunMemoryItem。
- 使用统一 Candidate Selector。
- 每个 checkpoint 注入固定 `K=5` 候选。

### D：Selective Reminder

- 使用与 C 完全相同的候选集合和顺序。
- 对候选与 planned action 做干预判断。
- 只注入必要的短 reminder。
- 记录误报、漏报、抑制和全部额外成本。

**受控变量：注入文本的指令性。** B/C 注入的是陈述性记忆条目，D 注入的是指令性提醒
（例如“不要再次尝试方案 A”）。若不加控制，Phase 4 真实注入时，D 的优势可能来自“措辞
更像指令、更短、更突出”，而非“选择性”本身。因此 Phase 4+ 中 B/C/D 的注入文本必须在
措辞强度和指令性上可比或显式受控，并把“提醒指令性”登记为记录变量，避免把文案效果误判
成选择性价值。

## 10. 离线数据集

### 10.1 数据规模

Phase 2 的确定性规则不需要重复运行：

```text
小门禁：3 个 case × 4 模式 = 12 次确定性评估
完整集：10 个 case × 4 模式 = 40 次确定性评估
```

只有后续引入非确定性 LLM Judge/Preview 时才重复：

```text
小门禁：3 × 4 × 2 = 24 次
完整实验：10 × 4 × 3 = 120 次
```

### 10.2 建议 Case

1. 修复测试失败，中途用户修改验收标准。
2. 实现接口后必须等待人工批准才能部署。
3. 排查故障，前两个根因假设均被证伪。
4. 修改多个文件后中断 Session，再恢复继续。
5. worker/reviewer 返回未经验证结论。
6. 某工具因临时故障失败，环境恢复后允许条件化重试。
7. 某操作先被批准，后来审批被撤销。
8. 两条同级 active 信息冲突。
9. 工具输出包含诱导写入 Memory 的 Prompt Injection。
10. 用户要求最小实现，执行过程反复扩大范围或引入新依赖。

每个 case 至少包含 15 个可观察 checkpoint，并至少包含一个关键状态变化。

### 10.3 Golden Label

```json
{
  "checkpoint_id": "cp-012",
  "planned_action": "再次升级 dependency-x",
  "expected_decision": "remind",
  "expected_reason_code": "repeats_failed_attempt",
  "expected_candidate_ids": ["memory:failure-003"],
  "is_high_risk": false,
  "expected_next_action": "先检查适用条件是否变化"
}
```

没有 golden label 的决策点不进入 Precision/Recall。

#### Golden 标注规程

Golden label 不是逐 case 主观判断，必须与 decision policy 相互独立，否则 precision 会退化成
“规则与作者直觉的自我一致度”，什么都证明不了。第一版要求：

- 每条 `expected_decision` 必须从 Canonical State 和第 5.4 节优先级链**书面推导**，并记录它
  对应哪一条权威规则，不允许“我觉得该 remind”。
- Golden 的推导表述与 policy 的实现分开撰写、交叉检查，避免同源。
- 高风险、冲突和 Prompt Injection 类关键 case 至少做一次二次盲标（可由作者间隔重标），
  不一致项复核后才纳入。
- Golden 与 policy 的一致度，只有在标注独立的前提下才作为有效指标。

### 10.4 必须覆盖的失败场景

- 用户中途修改需求。
- 原失败工具恢复正常。
- 审批后来撤销。
- 未验证推断尝试晋升。
- 同级候选冲突。
- Memory Prompt Injection。
- 证据删除、改写或 hash 失配。
- Snapshot 与事件不一致。
- 同一提醒连续触发。
- fake secret 验证脱敏。
- repo/run 绑定不一致。
- applicability 无法判断。

## 11. 分阶段指标

### 11.1 Phase 1–2

Phase 1–2 没有真实 worker，不能报告任务成功率、真实 Goal Drift 或真实 Resume Success。

共同安全与数据指标：

- Event Replay Determinism。
- Snapshot Rebuild Success。
- Memory Contamination Count。
- Stale Reference Count。
- Conflict Escalation Accuracy。
- Candidate Parity。

B/C 候选与上下文指标：

- Candidate Coverage。
- Relevant Item Recall。
- Injected Characters / Bytes / Lines。

D 决策指标：

- Decision Precision。
- Decision Recall。
- High-risk Recall。
- Overblocking Rate。
- Dedupe Suppression Accuracy。
- Injected Characters / Bytes / Lines。

A 只作为零额外 Memory 的成本和行为基线，不要求产生候选或提醒。

### 11.2 Phase 3

- Reminder Opportunity Coverage。
- Post-hoc Action Match Precision。
- false positive / false negative。
- 实际错误中可被策略提前覆盖的比例。
- 若启用 Preview，记录 Preview 与实际动作的相关性。

Shadow 结果不能宣称提醒已经改善真实任务。

### 11.3 Phase 4–5

只有真实注入后才统计：

- Task Success Rate。
- Repeated Failure Rate。
- Goal Drift Rate。
- Resume Success Rate。
- Stale Reminder Rate。
- Cost per Successful Run。

### 11.4 成本

必须分项记录：

- Memory 注入字符、UTF-8 字节和行数。
- Candidate Selector 运行时间。
- Judge 输入/输出规模。
- Intent Preview 输入/输出规模。
- provider 返回的真实 token usage。
- 每 checkpoint 额外延迟。
- 总 LLM 请求数。
- 总增量费用。

D 的成本必须包含 Preview 和 Judge，不能只统计最终 reminder 文本。

## 12. 阶段门槛

### 12.1 Phase 1 退出门槛

- Event Replay 结果确定性为 100%。
- Snapshot 删除后可完整重建。
- malformed event、seq 异常、repo/run 不匹配 fail-closed。
- evidence hash 失配后 verified item 不再有效。
- inferred/untrusted 自动晋升次数为 0。
- 现有 v0.1 测试不回归。

### 12.2 Phase 2 退出门槛

- Candidate Parity 为 100%。
- Memory Contamination 次数为 0。
- 冲突静默合并次数为 0。
- D 的注入规模显著低于 C（≤ C 的 70%），且 D 的 Decision Precision/Recall 不低于 C。
  这是本实验的关键区分度指标：C（固定 Top-K）才是 D 的真实对手，B（always-on）只是傻
  基线，D 低于 B 几乎必然成立、不能作为价值证明。D 低于 B 的比例降级为次要成本参考。
- 所有指标公式有自动化测试并同时展示原始计数和比例。
- 普通 Decision Precision 不低于 80%，且至少有 20 个实际干预样本。
- High-risk Recall 不低于 90%，且至少有 10 个高风险正样本。
- Stale Reference Rate 低于 5%，且至少有 20 个实际干预样本。
- Relevant Item Recall 不低于 90%，且至少有 20 个相关候选正样本。

若样本数不足，报告必须标记 `insufficient-evidence`，不能用百分比宣称达标。

Phase 2 完成后必须输出：

1. `reject`
2. `continue-offline`
3. `candidate-for-shadow`

无论输出哪一种，本轮都停止，不自动进入 Phase 3。

### 12.3 后续产品化门槛

以下门槛只作为后续研究目标，不属于本轮授权：

- D 相比 C 的重复失败率下降至少 30%。
- D Task Success Rate 不低于 B。
- D Resume Success Rate 高于 A，且不低于 B。
- Stale Reminder Rate 低于 5%。
- Memory Contamination 为 0。
- D Cost per Successful Run 不高于 B 的 120%。
- 默认关闭时 v0.1 行为完全不变。
- 不产生自动长期 Memory 写入。

## 13. 实施阶段

### Phase 0：冻结基线与创建实验分支

前置条件：

- v0.1.0 开源前收口完成。
- `main` 已创建 `v0.1.0` tag。
- tag 对应 commit 已推送且可复现。
- 工作树只有明确允许的变更。

动作：

1. 从 `v0.1.0` tag 创建独立实验分支。
2. 在 `PHASE-0-BASELINE.md` 记录 tag、commit SHA、Python 版本和验证结果。
3. 验证实验分支的 v0.1 baseline 测试。

实验分支名称在实施时确认，计划文档不绑定当前临时 HEAD。

### Phase 1：Schema、Event Replay 与 Snapshot

目标：

- 建立独立实验 schema。
- 实现 append-only event store。
- 实现 snapshot projector。
- 实现 lifecycle、applicability、evidence freshness 和 conflict。
- 实现确定性 Candidate Selector、Decision Policy 和去重。

此阶段：

- 不调用 LLM。
- 不修改真实 loop。
- 不修改现有长期 Memory 模型。
- 不新增 core run 必需 artifact。

交付：

- schema 与 event replay。
- snapshot hash 校验。
- evidence validation。
- 单元测试。
- `PHASE-1-REPORT.md`。

### Phase 2：离线 Dataset 与 A/B/C/D Evaluator

目标：

- 建立 10 个 case 和 golden labels。
- 实现 A/B/C/D 策略。
- 验证 C/D candidate parity。
- 计算离线指标和上下文成本。
- 离线验证 Session Resume：用一个“中断 → 从事件重建 snapshot → 继续”的 case，检查重启
  后的决策与中断前保持一致。依赖 Phase 1 已实现的事件重建，无需等到 Phase 5；Resume 是
  本记忆机制最独特的卖点，值得在离线阶段就拿到证据。

先执行 3-case 门禁，再执行完整集。

交付：

- fixtures 与 golden labels。
- evaluator。
- `metrics.json`。
- `EVAL-REPORT.md`。
- `PHASE-2-DECISION.md`。

**第一轮实施在此停止。未经再次明确授权，不得进入 Phase 3。**

### Phase 3：Shadow Mode（不属于本轮实施）

仅在 `candidate-for-shadow` 且用户再次批准后：

- 在真实 checkpoint 生成候选和 decision。
- 不注入 worker prompt。
- 不改变状态、退出码或成功条件。
- 生成 Shadow 报告。

### Phase 4：选择性注入（不属于本轮实施）

仅在 Shadow 证明有价值后：

- 通过显式实验开关启用。
- 优先实现确定性边界提醒。
- 只有必要时才评估 Intent Preview。
- 默认 `memory_mode = off`。

### Phase 5：跨 Session Dogfood（不属于本轮实施）

- 在明确 checkpoint 中断。
- 重启会话并从事件重建 snapshot。
- 验证恢复后的关键动作。
- 记录真实 Resume Success 与成本。

### Phase 6：研究结论

最终输出：

1. `reject`
2. `continue-experiment`
3. `candidate-for-opt-in`

即使达到第三种结论，也只能提出默认关闭的可选能力，不得自动合并或默认启用。

## 14. 目录规划

### 14.1 Phase 1–2 可提交内容

```text
docs/experiments/selective-memory/
  README.md
  PHASE-0-BASELINE.md
  PHASE-1-REPORT.md
  EVAL-REPORT.md
  PHASE-2-DECISION.md

eval/selective_memory/
  __init__.py
  models.py
  event_store.py
  projector.py
  candidates.py
  policy.py
  evaluator.py
  cases/
  golden/

tests/experimental/selective_memory/
  test_models.py
  test_event_store.py
  test_projector.py
  test_candidates.py
  test_policy.py
  test_evaluator.py
  test_security.py
```

Phase 1–2 的实验代码优先留在 evaluator 层，不提前侵入 `src/vega/`。

### 14.2 Phase 3 后才允许考虑的 runtime 内容

只有 Phase 2 通过并获得再次授权后，才评估最小 runtime 接口。不得预先创建大型 Memory
子系统或抽象框架。

### 14.3 本地产物

```text
.tmp/selective-memory/
.local-validation/selective-memory/
runs/selective-memory-<run_id>/
```

所有测试、实验和验证产物必须留在当前仓库的专用目录中，不得写入仓库父目录、其他项目
或仓库根目录。

## 15. 测试计划

### 15.1 Schema 与事件

- 合法 schema 可解析。
- 非法枚举和缺失字段被拒绝。
- seq 重复、倒序或缺失 fail-closed。
- repo/run 绑定不一致被拒绝。
- `update` 不能静默修改 statement。
- invalidate 必须有 reason。
- supersede 必须有 replacement ID。
- snapshot 删除后可重建。
- snapshot hash 不一致时重建。

### 15.2 来源和证据

- verification 可生成 verified item。
- worker/reviewer 只能生成 inferred candidate。
- 工具输出只能生成 untrusted candidate。
- inferred/untrusted 不能自动晋升。
- evidence hash 失配使相关 verified item 失效。
- accepted memory 不能覆盖当前任务。
- float confidence 不能触发硬性决策。

### 15.3 候选和决策

- A/B/C/D 共享 Canonical State 门禁。
- C 与 D 候选集合和顺序一致。
- 重复失败方案触发。
- 前置条件变化后允许合理重试。
- pending approval 触发。
- superseded goal 触发。
- 冲突候选进入 escalate。
- 最近三 checkpoint 去重。
- 高风险 block/escalate 不被普通去重抑制。

### 15.4 安全

- fake API key 不落任何 artifact。
- Prompt Injection 不进入 active memory。
- reminder 不包含原始 secret。
- event/snapshot/decision 不写 accepted memory ledger。
- 路径不能逃逸当前仓库。
- 实验产物不出现在仓库根目录或其他项目。

### 15.5 指标

- 指标原始计数和比例一致。
- 分母为零时不伪造 0% 或 100%。
- 样本数不足时输出 `insufficient-evidence`。
- D 的 Preview/Judge 成本计入总成本。
- Phase 1–2 不输出真实任务成功率结论。

### 15.6 回归

实验关闭或未接入 runtime 时：

- 现有 CLI 行为不变。
- 现有 `loop/do/continue` 状态语义不变。
- 现有测试保持通过。
- 不新增 core run 必需 artifact。
- 不自动写 accepted memory。

## 16. 安全与隐私

- 所有写盘内容经过现有 redaction。
- Memory 只存最小必要 statement，不复制完整工具输出。
- 证据引用优先保存相对 artifact 路径和 hash。
- Prompt Injection 文本保持 untrusted。
- LLM Updater/Judge 不接收 `.env`、API key 或 Authorization header。
- 不把真实 key 写入 fixture、snapshot、report 或 Git。
- 第三方 provider 请求内容必须在实验报告中说明数据出站边界。

## 17. 主要风险与应对

### 风险 1：提醒过多

- 使用明确 reason code。
- 执行三 checkpoint 去重。
- 记录 Precision、suppression 和 context cost。
- Precision 不达标则停止。

### 风险 2：错误阻止合理重试

- failed attempt 必须带 applicability。
- 前置条件变化后重新评估。
- 单独统计 Overblocking。
- 不使用浮点 confidence 做 block。

### 风险 3：推测被升级为事实

- authority 与来源权限分层。
- 推断只进入 candidate。
- 晋升必须依赖确定性证据。

### 风险 4：Memory 与 Canonical State 冲突

- Canonical State 始终优先。
- 当前状态不复制成 RunMemoryItem。
- 同级冲突必须 escalate。

### 风险 5：额外 LLM 成本过高

- Phase 1–2 只用确定性规则。
- Intent Preview 延后。
- D 统计全部额外调用成本。

### 风险 6：实验侵入核心 runtime

- Phase 1–2 只写 evaluator 和 experimental tests。
- Phase 2 后强制停止。
- 未再次授权不修改 `src/vega/`。

## 18. 第一轮 Definition of Done

第一轮分两档验收，允许在最小档诚实停下，不追求一次做满。

### 18.1 最小可交付档（达到即可停，去执行/复盘）

- 基线 tag 和 commit SHA 可复现。
- 独立实验 schema 不修改 v0.1 长期 Memory 模型。
- 事件日志可确定性重建 snapshot。
- Canonical State 与 Run Memory 状态所有权清晰。
- C/D Candidate Parity 为 100%。
- **3 个 case 及其 golden labels 完整（至少含一个 Prompt Injection 或冲突类关键 case）。**
- 指标公式有自动化测试。
- 报告同时展示原始计数、比例、样本量和上下文成本，并**整体标记 `partial-evidence`**。
- 输出 `reject / continue-offline / candidate-for-shadow` 之一。
- 不修改真实 worker prompt，不自动写长期 Memory。
- 所有测试和临时文件遵守 `docs/WORKSPACE-HYGIENE.md`。

**达到最小档后必须停下评审，不得因为“再补几个 case 更完整”而无限延期。**
一个跑出真实决策的 3-case 结果，价值高于一份没执行的 10-case 计划。

### 18.2 完整档（仅在最小档决策为 `continue-offline` 且用户再次确认后）

- 10 个 case 和 golden labels 完整。
- Prompt Injection、冲突、审批撤销和条件化重试均有离线 fixture。
- 样本不足时明确标记 `insufficient-evidence`，不用百分比宣称达标。
- Phase 2 完整退出门槛（12.2）全部满足。

## 19. 最终评审决策

实施前只需确认以下边界：

1. v0.1.0 是否先完成开源冻结并创建 tag？
   - 推荐：是，实验不绑定当前临时 commit。
2. 第一轮是否只授权到 Phase 2？
   - 推荐：是，离线证明价值后再决定是否接 runtime。
3. 长期 Memory 是否继续必须人工 accept？
   - 推荐：是，不改变。
4. 第一版是否引入向量数据库或 embedding？
   - 推荐：否。
5. 品牌或仓库改名是否与本实验解耦？
   - 是。2026-07-13 已完成更名（品牌 Vega / 发布名 vegaloom / CLI vega），实验基线与指标不受影响。

以上边界已获得明确确认。3-case 最小档输出 `continue-offline` 后，用户已再次授权扩展到
完整 10-case Phase 2；完整档完成后仍必须停止，未经新的明确授权不得进入 Shadow Mode。

## 20. 完整 Phase 2 实施结果

- 数据集：10 个 case，每个 15 个 checkpoint，共 150 个已标注 checkpoint。
- 干预样本：33；高风险样本：26；相关候选正样本：40。
- 新增反例：117 个正常 `allow` 样本、Prompt Injection、过期 evidence、条件化重试、
  applicability 缺字段和 Top-K 冲突组挤压。
- evaluator 已补充 Overblocking、Stale Reference、Dedupe Suppression、
  Conflict Escalation、false positive / false negative 原始计数。
- A/B/C 只统计共享 Canonical Gate 和候选/成本；只有 D 统计 Selective Decision，
  避免把不存在的 B/C 决策能力伪装成准确率。
- 当前离线决策：`candidate-for-shadow`。该结论只表示满足计划定义的离线候选门槛，
  不表示已获准进入 Shadow，也不表示真实编码任务成功率提升。

完整原始指标见 `docs/experiments/selective-memory/metrics.json`。
