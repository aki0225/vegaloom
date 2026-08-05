# Vega 后续演进路线

> 更新时间：2026-08-05
> 当前稳定基线：`v0.1.4`
> 发布记录：annotated Tag 与 GitHub Release 已发布
> 当前顺序：Phase 4 真实使用验收已完成；进入日常使用观察。不启动 Stage 4 或新的 Runtime、
> Memory、LangGraph 集成。

本文是 Vega 当前路线的统一入口，只回答：

- 现在做到哪里。
- 当前唯一的下一步是什么。
- 哪些方向属于主线，哪些仍是实验。
- 什么条件满足后才能进入下一阶段。

产品行为以 [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) 为准，详细 Assurance 合同以
[`ASSURANCE-CONTRACT-CANDIDATE.md`](ASSURANCE-CONTRACT-CANDIDATE.md) 为准，历史验证证据
以 [`../eval/assurance-validation.md`](../eval/assurance-validation.md) 为准。本文不复制这些
文档的完整内容。当前执行计划见
[`DAILY-USAGE-COMPLETION-PLAN.md`](DAILY-USAGE-COMPLETION-PLAN.md)，文档状态见
[`README.md`](README.md)。

## 一、当前主线

```text
v0.1.4 发布（完成）
  -> CRWP-V1 合同允许终态（完成）
  -> 调查现有入口并固定 Plan 与人工确认协议（完成）
  -> 改进现有 Finish 第一屏（完成）
  -> 真实使用验收（完成）
  -> 停止扩张，进入日常使用观察（当前）
```

Phase 3 已完成：

1. `finish-summary.json` 增加兼容性的 `first_screen` 派生视图；
2. `finish-report.md` 第一屏按裁决、实际变更、确定性 Gate、验证、Reviewer、证据上限和
   下一步排序；
3. 继续从现有结构化 artifact 确定性生成，没有新增模型调用、状态、命令或第二套裁决；
4. Reviewer 缺少有效行号时明确显示缺失，不补造位置。

Phase 4 已完成：

1. Codex assist 在未公开结算任务上覆盖调查、计划确认、Reviewer 打回和高风险
   `needs_human`；
2. `vega do` 在 Echo Vault 登录页任务上完成 fresh auto 验收；
3. Claude Code assist 在 Echo Vault 历史会话任务上完成调查、计划、修改、验证、独立审查和
   Finish；
4. 未参与执行的新会话只读 Finish 后，能够判断实际变更、验证、风险和提交前人工检查要求。

当前阶段只做真实日常使用观察。已发现的测试名称缺失、空 Scope 展示和 Workspace 汇总不一致
先作为观察项保留；只有它们重复造成误判时才做最小修正，不新增报告 Runtime。

CRWP-V1 已完成合同允许的全部处理：

- Case 01：`needs_human / workspace_check_failed`；
- Case 02：`needs_human / timed_out`，Worker 未修改文件，Verification 与 Reviewer 未启动；
- Case 03：`eligibility-changed-before-run`。

这些结果不计算成功率，也不选择性重跑。详细证据见
[`CORE-REAL-WORLD-PILOT-V1-HANDOFF.md`](CORE-REAL-WORLD-PILOT-V1-HANDOFF.md) 和
[`../eval/real-world-runs.md`](../eval/real-world-runs.md)。

Phase 2 已完成：

- [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) 固定了事实/假设分离的 Plan 模板；
- 生成的 Codex `$vega-loop` Skill 会在模糊任务下先只读调查并等待修改前确认；
- Claude Code 复用同一协议，但不新增原生 adapter 或自动 Runner；
- `vega do` 继续只表示调用者已经确认边界的小任务，Runtime 行为没有改变。

已经完成并保留为历史依据的 Assurance 与维护路线：

```text
v0.1.4 可信执行维护
  -> Assurance Stage 0 维护完成
  -> Assurance Stage 1：Threat / Evidence 数据合同
  -> Assurance Stage 2：数据库 Migration 纵向实验
  -> Assurance Stage 3：固定 SQLite 有界 DML/Backfill 实验
  -> 冻结证据点，暂不启动 Stage 4
```

Stage 0 的三个已确认维护缺口按以下顺序完成：

1. Adapter junction/reparse point 的真实路径边界。
2. pnpm/yarn 项目的包管理器命令选择。
3. Finish 调用内重复的证据快照和 Git 读取。

这三项必须分别验证，不能与新的 Threat/Evidence 数据模型混在同一改动中。

## 二、维护任务

### M-001：Adapter 真实路径边界

- 优先级：`P0`
- 状态：`completed`
- 建议分支：`fix/adapter-realpath-boundary`
- 证据：`AV-BASE-003`
- 问题：Windows junction 解析后的真实路径可能位于目标仓库外。
- 最小修复：adapter 写入前校验解析后的真实路径仍在目标仓库内；不确定时停止。
- 退出条件：越界写入被阻止，仓库内正常路径仍可工作，Windows 专项回归通过。

### M-002：Node 包管理器选择

- 优先级：`P1`
- 状态：`completed`
- 建议分支：`fix/node-package-manager-selection`
- 证据：`AV-BASE-004`
- 问题：pnpm/yarn 项目可能混入错误的 npm 命令。
- 最小修复：依据 lockfile 和项目配置选择唯一正确的包管理器命令。
- 退出条件：npm、pnpm、yarn fixture 的命令集合都与实际项目一致。

### M-003：Finish 证据快照复用

- 优先级：`P1`
- 状态：`completed`
- 建议分支：`perf/finish-evidence-snapshot`
- 证据：`AV-BASE-005`
- 问题：同一 Finish 调用重复捕获 workspace、Git 和风险 evidence。
- 最小修复：复用一次可信 Evidence Validation Snapshot。
- 安全边界：不能删除终态完整性和新鲜度重算，只消除同一调用内的重复读取。
- 退出条件：功能结果不变，重复 Git/subprocess 调用下降，单测可在 60 秒约束内运行。

### M-004：主线可信执行维护

- 优先级：`P0/P1`
- 状态：`completed`
- 合并证据：PR `#20` 与 PR `#22` 已进入 `main`；后续必审高风险披露与 Goal/Gate
  防篡改回归在 2026-07-29 的 896 节点快照下通过。
- 来源：Stage 3 冻结后，真实 dogfood 与独立审查又复现了主线可信执行缺口；这是一项
  新增维护决策，不属于原 Assurance Stage，也不代表启动 Stage 4。
- 固定范围：目标仓库与 Git 读取边界、staged/index 候选卫生、workspace/review evidence
  完整性，以及 stop/timeout/recovery 的 owned process 边界。代码拆分只服务于这些修复，
  不是独立重构目标。
- 非目标：不增加新 Runtime、Stage、Memory、LangGraph、多 Reviewer 或默认实验能力；
  不继续把新的泛化风险加入本 PR。
- 退出条件：当前 PR 的关键回归、跨平台 CI、架构增长检查、公开仓库卫生和独立 diff
  审查全部通过。真实任务 dogfood 在合并后单独执行，不作为继续扩张本 PR 的理由。

当前路线分成两条，不把维护任务伪装成 Assurance Stage：

```text
Assurance：Stage 1 -> Stage 2 -> Stage 3 -> 冻结，不启动 Stage 4
主线维护：M-001 -> M-002 -> M-003 -> M-004
M-004 合并后：CRWP-V1 真实代码任务能力与成本验证
```

## 三、主线阶段

### Stage 0：基础成功语义与维护完成

状态：`completed`

`v0.1.2` 已完成：

- 零验证命令、`--no-verify`、非结构化日志不能自动成功。
- 验证中断、缺失、损坏或错绑的 evidence fail-closed。
- Loop、Finish 和 Goal 使用最新可信验证结论。

Stage 0 的三个维护任务均已完成，并由各自的预注册回归、本地证据和 PR CI 验证。

### Stage 1：Threat 与 Evidence 数据合同

状态：`completed`

范围：

- 定义版本化 `Threat`、`Claim`、`Evidence Record` 和 `Adequacy Result`。
- 只使用项目规则和确定性 detector。
- LLM 只能提出候选 threat，不能自行宣布证据充分。
- artifact integrity 校验字段、引用、iteration 和 workspace snapshot。

退出条件：

- 缺字段、伪造引用、错绑 iteration 或 snapshot 时停止。
- 旧 run 可以复盘，但不能因缺少新字段升级成功。
- 每个关键 threat 至少有一个危险案例和一个安全双生案例。

完成证据：

- PR `#7` 最终 head `428573e` 的 workflow `29973922619`：10 项检查全部成功。
- 合并提交：`main@521f9b9`。
- 合并后的主线 workflow `29977016358`：10 项检查全部成功。
- Stage 1 定向测试：`59 passed`；完整收集合同：`600 tests collected`。

Stage 1 只完成独立数据合同和确定性充分性校验器，没有接入默认 Runtime、Finish、Goal 或
`ready_to_commit`。`sufficient_for_merge` 仍只是合同层结论，不是生产安全证明。

### Stage 2：数据库 Migration 纵向验证

状态：`experimental` / `two-cases-merged`

识别 migration、DDL 和 ORM schema 变化，验证兼容矩阵、锁影响、数据转换和恢复路径。
这不是给 Vega 自身接数据库，而是审查目标项目中的数据库变更风险。没有接近生产规模的
演练时，最高只能给出 `requires_staged_rollout` 或 `human_required`。

`AV-STAGE2-001` 独立 SQLite 危险/安全双生实验已经通过审查、PR CI 和合并后主线 CI，
以 `main@0280b9f` 成为 Stage 2 的第一份公开实验代码与证据。它没有注册默认 CLI，也没有
修改成功语义；结论仍只能是 `continue-experiment / do-not-integrate`。

`AV-STAGE2-002` 在固定两行 fixture 上比较
`expand -> contract -> backfill` 与 `expand -> bounded fixture backfill -> contract`，
已通过 PR `#10`、合并提交 `main@1922e5f` 和合并后主线 10 项 CI。该实验仍只覆盖
`T-DB-MIG-COMPAT`，不会扩展为 Stage 3 的通用 backfill 能力。详细合同见
[`ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md`](ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md)。
不得直接把 SQLite 个案扩大为通用数据库安全声明。

### Stage 3：数据修改与 Backfill

状态：`experimental` / `merged-frozen`

覆盖 DML、backfill、cleanup、scope、row budget、幂等、恢复和 reconciliation。

`AV-STAGE3-001` 已通过 PR `#13` 以 squash merge 进入 `main@572af85`：在冻结的双租户
SQLite fixture 上验证有界 DML 是否只修改明确目标，能否在首批提交后的受控子进程硬退出后
恢复，并由独立 SQL oracle 检查越界、幂等和 reconciliation。
详细合同见
[`ASSURANCE-STAGE3-DML-BACKFILL-PREREGISTRATION.md`](ASSURANCE-STAGE3-DML-BACKFILL-PREREGISTRATION.md)。
当前实现只存在于 `scripts/` 与 `tests/`，没有新增默认命令或 Runtime 集成。独立审查发现
并修复了目标 ID scope 误判、声明 artifact 哈希未全覆盖和 policy hash sentinel 三个问题；
最终 PR head `6302dc2` 的 10 项 PR CI 全部成功，合并提交 `572af85` 的主线 workflow
`30143380213` 也为 10/10 success。无论已经合并，仍不得把该 SQLite 个案解释为通用
backfill、生产数据库或 Runtime 自动执行能力。

Stage 3 当前停止条件：

1. 不把实验脚本注册为默认 CLI 或 Runtime 步骤。
2. 不启动 Stage 4，除非重新预注册并说明为什么必须继续。
3. 面试和公开介绍只说“固定 SQLite 个案证明机制”，不说“已支持生产 backfill”。

### Stage 4：并发与外部副作用

状态：`frozen` / `not-scheduled`

该方向只作为未来研究题保留，不属于当前主线。若未来重新启动，覆盖锁、事务、异步任务、
消息、重试、重复投递、取消和 liveness；验证必须重新预注册并使用受控交错和故障注入，
不能只依赖随机 sleep。

### Stage 5：扩展与生产 Handoff

状态：`frozen` / `not-scheduled`

该方向同样不在当前计划中。只有真实日常使用反复证明现有 Finish 和人工接管不足时，才重新
评估权限、兼容性、性能、配置、供应链、可观测性，以及受信 CI/CD evidence、canary、
监控、停止和 rollback 要求。Vega 仍不自动部署。

## 四、实验方向

实验用于验证假设，不自动扩展产品。实验默认关闭、独立运行、保留失败证据，只有满足
明确门槛后才提出 opt-in 合并建议。

### LangGraph

- 状态：`experimental` / `partial`
- 默认引擎仍是 Linear，默认 reviewer 仍是 Single。
- 已证明的价值主要是 crash recovery、HITL 和跨 Session handoff。
- 不进入默认主线：LangGraph 默认顺序引擎、多 Reviewer fan-out、FastAPI/SSE 控制面。
- 只有 recovery/HITL 的最小、引擎无关能力具备独立证据时，才重新评估 opt-in 集成。

### Selective Memory Reminder

- 状态：`experimental`
- 默认配置：`memory_mode = off`
- 先验证独立 schema、event replay、snapshot 和离线 A/B/C/D evaluator。
- 没有离线收益证据前，不进入 Shadow、prompt 注入或主线成功条件。
- Phase 2 后必须停止，进入下一阶段需要重新授权。

### Goal P1

- 状态：`planned` / `design-only`
- Goal P0 人工状态层已实现，P1 有限自动 checkpoint 仍是设计草案。
- 只有真实长任务反复需要自动 checkpoint，才进入实现评估。
- 不与 LangGraph 绑定，先证明 Goal/Handoff 是引擎无关能力。
- 详细设计见 [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md)。

### 多 Reviewer

- 状态：`rejected-for-default`
- 已有实验显示 fan-out 增加调用、token、误报和延迟，没有稳定增加有效发现。
- 主线继续使用 Single Reviewer，除非未来重新预注册对照实验。

## 五、统一进入门槛

1. 运行前冻结 baseline、输入、指标和停止条件。
2. 至少准备一个危险案例和一个安全双生案例。
3. 实验默认不改变主线行为、退出码和必需 artifact。
4. 结果记录原始计数、比例、成本和限制。
5. 样本不足时标记 `insufficient-evidence`，不包装成成功。
6. 结论只能是 `reject`、`continue-experiment` 或 `candidate-for-opt-in`。
7. 新能力进入主线前必须证明至少改善成功率、缺陷发现率、人工步骤、恢复能力或成本
   中的一项。

## 六、路线决策记录

### 2026-07-21：v0.1.2 后冻结功能扩张

主线不继续堆叠 Agent、Memory、LangGraph 或 Web 能力，先处理已确认的 Assurance 维护缺口。

### 2026-07-21：主线转向 Evidence Adequacy

Evidence Integrity 已有基础，下一问题是证据是否足以支撑交付结论。先定义合同和确定性
detector，再选择单一 Threat 做纵向实验，不一次性实现完整 Risk Engine。

### 2026-07-21：实验与主线分离

LangGraph、Selective Memory、Goal P1 和多 Reviewer 保持独立实验状态；实验只能提出
opt-in 合并建议，不能自动改变默认 Linear 路径。

### 2026-07-23：Assurance Stage 1 完成

Stage 1 已由最终 PR head 和合并后的主线 CI 验证并进入 `main`。下一阶段只选择数据库
Migration 这一类 Threat 做纵向实验；在 detector、真实证据和危险/安全双生案例形成独立
结论前，不把 AdequacyResult 接入默认成功语义。

### 2026-07-27：新增 M-004，但不推进 Assurance Stage

Stage 3 证据冻结后，真实 dogfood 与独立审查复现了目标仓库读取、证据新鲜度和 owned
process 管理缺口，因此新增一次有界主线维护。它只收紧已有默认路径，不新增 Stage 或
实验能力。M-004 完成后停止横向基础设施扩张，下一步改为真实代码任务的成功率、耗时、
token 成本、人工接管和恢复体验验证。

### 2026-08-04：完成发布与 CRWP-V1，进入日常入口协议

`v0.1.4` annotated Tag、GitHub Release 和精确 Tag smoke 已完成。CRWP-V1 三个 Case 均已
取得合同允许的终态，其中 Case 02 为 `needs_human / timed_out`，不为得到成功样本而重跑。

主线停止继续扩建研究能力。唯一下一步改为调查现有 Codex、Claude Code 与命令行入口，固定
Plan-first 和修改前人工确认协议；协议确认前不修改 Runtime，Finish 报告作为下一阶段单独
实施。

### 2026-08-04：完成 Plan-first 协议，进入 Finish 第一屏改进

Phase 2 只增加权威协议文档、生成的 Codex Skill 约定、Claude Code 等价说明和窄测试。
它没有增加 Planner Agent、Runtime 路由、命令、状态、schema 或 Claude Code 原生 Runner。
当前唯一下一步改为 Phase 3，只重排现有 Finish 输出并保持确定性裁决。

### 2026-08-04：完成 Finish 第一屏，进入真实使用验收

Phase 3 只增加 `first_screen` 派生视图和确定性 Markdown 展示，原 Finish 字段与裁决语义
保持不变。展示按七段固定顺序呈现运行身份、实际变更、Gate、验证、Reviewer、证据上限和
下一步；无效 Reviewer 行号不会被补造。

当前唯一下一步改为 Phase 4 真实使用验收。只有真实使用证明第一屏仍缺关键信息时，才做
最小信息重排；不新增命令、模型调用、状态或新的报告 Runtime。

### 2026-08-05：完成 Phase 4，进入日常使用观察

Codex assist、Claude Code assist、`vega do`、Reviewer 打回和 fail-closed 五类场景均已有
真实记录。未参与执行的新会话能够从 Finish 判断变更、验证、风险和人工检查要求，同时指出
测试用例名称缺失、空 Scope 展示和 Workspace 汇总不一致等信息问题。

主线停止新增产品能力。后续只处理日常使用中能够复现、确实影响可信判断或恢复体验的问题；
不因为单次展示不完美而扩建新的报告 Runtime、Planner、Multi-Worker 或基础设施。

## 七、更新规则

路线变化时只更新本文，并写清：

- 日期和变更原因。
- 影响的路线条目。
- 新增或移除的验证证据。
- 是否改变默认行为。
- 下一步和停止条件。
