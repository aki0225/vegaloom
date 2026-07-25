# Vega 后续演进路线

> 更新时间：2026-07-25
> 稳定基线：`v0.1.3`
> 当前状态：`M-001`、`M-002`、`M-003`、Assurance Stage 1、Stage 2 两个 SQLite 个案
> 与 Stage 3 有界 DML/Backfill 个案均已进入 `main`。它们仍不属于 Runtime 或默认能力，
> 也不能解释为生产数据库安全证明。当前 `Now` 是冻结主线、整理交接和复习材料，不启动
> Stage 4 或新的 Runtime/Memory/LangGraph 集成。

本文是 Vega 当前路线的统一入口，只回答：

- 现在做到哪里。
- 当前唯一的下一步是什么。
- 哪些方向属于主线，哪些仍是实验。
- 什么条件满足后才能进入下一阶段。

产品行为以 [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) 为准，详细 Assurance 合同以
[`ASSURANCE-CONTRACT-CANDIDATE.md`](ASSURANCE-CONTRACT-CANDIDATE.md) 为准，历史验证证据
以 [`../eval/assurance-validation.md`](../eval/assurance-validation.md) 为准。本文不复制这些
文档的完整内容。

## 一、当前主线

```text
v0.1.3 稳定与冻结
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

执行顺序：

```text
M-001 Adapter 边界
  -> M-002 Node 包管理器
  -> M-003 Finish 快照复用
  -> Assurance Stage 1
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

状态：`planned`

覆盖锁、事务、异步任务、消息、重试、重复投递、取消和 liveness。验证必须使用受控
交错和故障注入，不能只依赖随机 sleep。

### Stage 5：扩展与生产 Handoff

状态：`planned`

扩展到权限、兼容性、性能、配置、供应链、可观测性，以及受信 CI/CD evidence、canary、
监控、停止和 rollback 要求。Vega 仍不自动部署，只生成和校验交付要求。

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

## 七、更新规则

路线变化时只更新本文，并写清：

- 日期和变更原因。
- 影响的路线条目。
- 新增或移除的验证证据。
- 是否改变默认行为。
- 下一步和停止条件。
