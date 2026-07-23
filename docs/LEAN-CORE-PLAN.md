# Vega 轻量核心精简计划

> 基线日期：2026-07-23
>
> 基线提交：`521f9b9`
>
> 工作分支：`refactor/lean-core`
>
> 本轮边界：不放松 fail-closed，不改变默认成功语义，不接入 Stage 2 Runtime。

## 一、结论

Vega 仍然保持本地文件优先、无数据库服务、无后台 daemon、无多 Agent 平台的部署边界，
但源码依赖、CLI 表面积和状态裁决已经超出“轻量 worker-reviewer harness”的维护目标。

本轮把最小核心固定为：

```text
任务与项目规则
  -> 受控 worker
  -> 工作区与范围门禁
  -> 确定性验证
  -> 隔离 reviewer
  -> Finish / Recover / 人工接管
```

精简不以删除安全检查为目标。优先处理重复顶层 Runtime、实验能力反向依赖核心、巨型编排函数、
重复事实源和没有真实消费者的产品表面积。

## 二、基线规模

| 范围 | 文件数 | 物理行数 | 判断 |
|---|---:|---:|---|
| `src/vega/` | 48 | 23,569 | 默认路径认知负担偏高 |
| `tests/` | 23 | 17,200 | 安全资产，不以机械删减为目标 |
| `docs/` | 15 | 3,690 | 需要统一入口，避免重复口径 |
| `scripts/` | 2 | 1,148 | 只保留可复现验证和仓库门禁 |
| `eval/` | 3 | 1,968 | 历史证据只追加，不改写 |

`LoopAutomationRuntime` 的静态依赖闭包为 28 个模块、17,118 行。当前 Ruff `C901`
诊断存在 48 个既有复杂度超限函数；`loop_runtime.py` 为 3,485 行，其中两个核心方法分别为
595 行和 823 行。

这些数字只用于冻结基线和判断变化方向，不代表可以通过删注释、合并语句或移除防御检查来达标。

## 三、模块清单与去留决策

### 3.1 保留为核心

| 模块 | 主要职责 | 决策 |
|---|---|---|
| `brief_generator.py`、`brief_runtime.py` | 任务输入和 worker brief | 保留 |
| `project_config.py`、`project_profile.py` | 项目策略与项目画像 | 保留 |
| `project_context.py`、`project_knowledge.py` | 编译项目规则和上下文 | 保留；移除对实验 Memory 存储的静态依赖 |
| `loop_runtime.py` | 日常 worker-reviewer 主编排 | 保留；拆解巨型阶段函数 |
| `workspace_check.py`、`scope_gate.py` | 工作区、路径和范围边界 | 保留 |
| `verification.py` | 项目确定性验证 | 保留 |
| `reflect_runtime.py` | 固化 diff、测试和审查输入 | 保留；Memory proposal 改为可选扩展 |
| `gate_runtime.py`、`risk_gate_evidence.py` | 风险计算和风险证据 | 保留 |
| `review_runtime.py` | 独立 reviewer 和审查证据 | 保留 |
| `finish_runtime.py` | 最终交付判断 | 保留；Memory 展示改为可选读取 |
| `recovery_runtime.py`、`execution_control.py` | 中断、停止、恢复和进程归属 | 保留 |
| `run_lock.py`、`run_status.py`、`run_utils.py` | 本地运行互斥、状态和路径解析 | 保留 |
| `runner.py`、`prompt_metrics.py` | 外部 runner 和上下文预算 | 保留 |
| `decision.py` | 人工决策账本 | 保留 |
| `redaction.py`、`repository_identity.py` | 脱敏和仓库身份 | 保留 |
| `trace.py` | 运行事件记录 | 保留 |
| `models.py` | 共享数据模型 | 暂时保留；后续按核心/实验边界拆分 |
| `agents_proposal.py` | 可选 AGENTS.md 建议 | 保留为核心输出，不自动写目标仓库 |
| `change_plan_runtime.py` | 人工变更计划 | 保留为高级阶段命令 |
| `cli.py` | 组合根和公开 CLI | 保留；实验命令使用延迟导入 |

### 3.2 隔离为可选实验

| 当前模块 | 目标边界 | 决策 |
|---|---|---|
| `assurance.py` | `experimental/assurance.py` | Stage 1 合同保持纯函数和独立测试，不接入默认 Runtime |
| `goal_runtime.py` | `experimental/goal_runtime.py` | 保留现有 CLI，默认主流程不导入 |
| `goal_evidence.py` | 核心 Loop 证据与实验 Goal 证据拆分 | 核心部分改为职责明确的 `loop_evidence.py` |
| `memory.py` | `experimental/memory.py` | CLI 与存储实现隔离；核心只通过可选端口读取 |
| `adapter_runtime.py` | `experimental/adapter_runtime.py` | 保留显式命令，不在 CLI 启动时加载 |

实验模块可以依赖核心模块；核心模块不得静态导入 `vega.experimental`。

### 3.3 兼容隔离并列入待废弃评估

以下模块共同实现 YAML 驱动的只读 `engineering-change` Inspection Loop：

```text
runtime.py
context_loader.py
llm_client.py
loop_spec.py
reviewer.py
state.py
tool_broker.py
eval.py
resources/loops/engineering-change.loop.yaml
```

决策：

1. 本轮不直接删除，避免破坏 v0.1.x 已发布的 CLI 和 wheel baseline。
2. 移入 `experimental/inspection/`，公开命令保持兼容，但标记为只读兼容入口。
3. 不再给该路径新增能力、artifact 或成功语义。
4. 后续只有存在独立真实使用证据才保留；否则在下一次破坏性版本中移除。

### 3.4 Python 导入兼容决策

采用“CLI 为稳定入口”的方案：

1. 稳定 Python 导出仅为 `vega.__version__`。
2. `vega` 下 Runtime、Memory、Goal、Assurance、Adapter 和 Inspection 模块均为内部实现。
3. 不恢复 `vega.assurance`、`vega.memory`、`vega.runtime` 等旧路径兼容 shim。
4. 架构门禁必须拒绝重新创建已移除的顶层内部模块。
5. 下一次发布说明必须明确该边界；若未来提供 Python SDK，另建有版本合同的命名空间。

该决策不影响已有 CLI 命令兼容要求，也不允许 Experimental 反向进入核心成功语义。

## 四、CLI 清单与决策

### 4.1 日常核心入口

```text
vega do bug|feature
vega loop bug|feature
vega loop continue
vega status
vega finish
vega stop
vega recover
```

这些命令构成产品默认闭环。

### 4.2 核心阶段与排障入口

```text
vega brief bug|feature
vega profile
vega plan
vega config check
vega reflect
vega gate
vega review-pack
vega review
vega latest
vega decision approve|reject|list
```

保留，但不继续扩展顶级命令。新阶段优先作为现有闭环内部步骤，而不是新增 CLI。

### 4.3 实验入口

```text
vega goal ...
vega memory ...
vega adapters init ...
```

保持显式 opt-in，使用延迟加载，不得成为核心运行的必需条件。

### 4.4 待废弃兼容入口

```text
vega run engineering-change
vega list-loops
```

保留现有行为，不再扩建。

## 五、Artifact 清单与事实源

### 5.1 核心 Loop

| 类别 | 权威机器事实 | 派生的人类报告 |
|---|---|---|
| 根状态 | `state.json`、`trace.jsonl` | `loop-plan.md`、`final-report.md` |
| 项目策略 | `project-policy-snapshot.json` | `project-context.md`、`agent-brief.md` |
| worker | `execution.json`、Prompt metrics JSON | `worker-prompt.md`、`worker-output.txt` |
| 工作区 | `workspace-check.json` | `workspace-check.md` |
| 范围门禁 | `scope-gate-*-result.json` | `scope-gate-*-report.md` |
| 验证 | `verification-result.json` | `verification-summary.md` |
| Reflect | `review-evidence.json`、`full-diff.patch` | `diff-summary.md`、`test-summary.md`、`reflection.md` |
| 风险 | `risk-gate-result.json` | `risk-gate-report.md` |
| Review | `review-state.json`、`review-verdict.json` | `review-findings.md`、`review-pack.md` |
| Finish | `finish-summary.json` | `finish-report.md` |
| 人工决策 | `decisions.jsonl` | Finish 中的决策摘要 |
| 恢复与停止 | `execution.json`、`stop-request.json`、`recovery-transaction.json` | interruption/timeout/recovery 报告 |

规则：JSON/JSONL 和 patch 是机器事实源，Markdown 只负责渲染与交接，不得形成第二套成功判定。

### 5.2 实验与兼容 Artifact

| 能力 | Artifact | 决策 |
|---|---|---|
| Memory | `memory-proposals.jsonl`、`memory/ledger.jsonl` | 可选；不存在时核心闭环必须完整工作 |
| Goal | `goal-state.json`、`goal-trace.jsonl`、contract/checkpoint/report | 实验目录和模型隔离 |
| Adapter | `.codex/skills/.../SKILL.md` | 仅显式命令写入目标仓库 |
| Assurance | 输入 bundle 和 `AdequacyResult` | 独立实验，不写默认 run |
| Inspection | `plan.md`、`report.md`、`review.md`、`eval.md` | 兼容路径，不扩建 |

## 六、状态清单与裁决边界

| 状态机 | 状态 | 权威来源 |
|---|---|---|
| 核心 Loop | `created/running/success/failed/needs_human` | `LoopAutomationState.status` |
| 单轮 iteration | `completed/interrupted` 与各阶段状态 | `LoopIterationState` |
| Review | `created/running/success/failed/needs_human` | `ReviewState` 与受信 verdict |
| Brief/Profile/Reflect/Gate/Plan | `created/running/success/failed` | 各自 state model |
| Goal | `created/running/checkpoint_done/paused/completed/stopped` | 实验 `GoalState` |
| Inspection | `created/running/success/failed` | 兼容 `RunState` |

`ready_to_commit` 是 Finish 输出，不是可以绕过验证写入的 Runtime 成功状态。人工裁决不能伪装成
确定性验证成功。

## 七、依赖方向

允许的方向：

```text
cli 组合根
  -> core runtime/stages
  -> core evidence, state, policy, I/O

cli 实验命令
  -> experimental
  -> core
```

禁止的方向：

```text
core -> experimental
core -> experimental state model
core 成功语义 -> 可选 artifact 是否存在
experimental -> 修改核心终态或默认 CLI 行为
```

当前需要消除的主要反向依赖：

- `loop_runtime.py`、`gate_runtime.py`、`finish_runtime.py` 导入 `goal_evidence.py`。
- `project_knowledge.py`、`project_profile.py`、`reflect_runtime.py`、`finish_runtime.py`
  导入 Memory 存储实现。
- `cli.py` 在进程启动时加载全部实验和兼容 Runtime。

## 八、增量增长门禁

本轮将加入机器可执行门禁：

1. 不允许新增 Ruff `C901` 诊断，也不允许既有超限函数复杂度继续增加。
2. 新模块不得超过 500 行；既有超过 500 行的模块不得继续增长。
3. 新增核心模块不得静态导入 `vega.experimental`。
4. 架构基线只能减少，不能通过修改 allowlist 扩大。
5. 门禁对既有债务采用基线方式，不要求一次重写全部 Runtime。

## 九、实施与停止条件

### 步骤一：清单和决策

本文即为基线清单。后续变更如改变分类、公开命令、artifact 或状态，必须先更新决策理由。

### 步骤二：增量门禁

实现架构基线、检查脚本、自动化测试和 CI 接入，不改变产品行为。

### 步骤三：隔离与拆解

- 创建 `vega.experimental`。
- 拆分 Loop/Goal 证据职责。
- 隔离 Memory、Goal、Adapter、Assurance 和 Inspection。
- 拆解 `loop_runtime.py` 巨型编排方法，但不改变阶段顺序、artifact、退出码和成功语义。

### 步骤四：Assurance 纵向实验

只运行一个数据库 migration 危险案例和安全双生案例：

- 使用独立脚本和实验 fixture。
- 不注册新 CLI。
- 不写默认 `runs/`。
- 输出进入 `.local-validation/`，公开记录只追加实验摘要和限制。
- 结果只能支持 `reject`、`continue-experiment` 或 `candidate-for-opt-in`。

任一情况下都不在本轮把 Stage 2 接入核心 Runtime。

### 本轮候选执行记录（2026-07-23）

1. **清单与决策**：已建立本文件，冻结核心闭环、实验隔离、artifact 和状态边界。
2. **增量门禁**：`scripts/check_architecture_growth.py` 已检查 C901 增长、模块行数增长和
   Core → Experimental 静态依赖；门禁相对 `origin/main` 必须通过。
3. **非核心隔离**：Memory、Goal、Adapter、Assurance 与兼容 Inspection 已移动到
   `vega.experimental`；核心 Loop 的证据校验位于 `loop_evidence.py`，不静态依赖实验模块。
   `loop_runtime.py` 只完成第一轮 iteration 状态聚合，既有巨型编排债务仍保留。
4. **Stage 2 实验**：仅增加
   [`ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md`](ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md)
   所述 SQLite 危险/安全双生脚本和测试。它不新增 `vega` 命令、默认状态、默认 `runs/`
   或成功条件；总体结论最高为 `continue-experiment`。

本节是候选分支的执行边界，不替代最终本地全量测试和 PR 跨平台 CI 结论。

## 十、进入主线的门槛

只有同时满足以下条件，精简分支才可以建议合并：

1. Python 3.11/3.12、Windows、POSIX 和 wheel CI 全绿。
2. 核心 CLI、artifact、状态和成功语义保持兼容；内部 Python 模块路径按 3.4 不属于该承诺。
3. 架构门禁能在危险样例上失败、在当前基线上通过。
4. 默认 Loop 不再静态依赖实验模块。
5. 实验模块不能改变默认运行和 Finish 结果。
6. 数据库 migration 实验诚实记录不能证明的生产结论。
7. `eval/` 历史记录未被改写。
