# Vega 文档导航

> 更新时间：2026-08-18

本文只负责说明每份文档现在是什么用途。产品行为以产品契约、源码和实际运行证据为准；
历史交接中的“下一步”“不要合并”或旧分支状态，不代表当前主线。

## 建议阅读顺序

| 目的 | 文档 | 状态 |
|---|---|---|
| 快速了解产品与真实运行 | [Vega 在线展示](https://aki0225.github.io/vegaloom/) | GitHub Pages |
| 先了解怎么使用 | [`../README.md`](../README.md) | 当前入口 |
| 查看日常完整流程 | [`USAGE-WALKTHROUGH.md`](USAGE-WALKTHROUGH.md) | 当前使用说明 |
| 查看调查与修改前确认协议 | [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) | v0.2.0 当前协议 |
| 确认产品边界和成功语义 | [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) | 当前权威契约 |
| 查看 Supervisor 状态与恢复合同 | [`SUPERVISOR-AGENT-STATE-AUTHORITY.md`](SUPERVISOR-AGENT-STATE-AUTHORITY.md) | v0.2.0 当前合同 |
| 查看长期路线和历史决策 | [`ROADMAP.md`](ROADMAP.md) | 当前路线入口 |
| 查看 Runtime 与证据链 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 当前架构说明 |
| 查看版本范围和非目标 | [`MVP-SCOPE.md`](MVP-SCOPE.md) | v0.1 baseline + v0.2.0 可选能力 |
| 查看工作区规范 | [`WORKSPACE-HYGIENE.md`](WORKSPACE-HYGIENE.md) | 当前规范 |
| 查看 v0.2.0 发布内容 | [`RELEASE-NOTES-0.2.0.md`](RELEASE-NOTES-0.2.0.md) | 已发布 |
| 查看历史实验归档 Tag | [`EXPERIMENT-ARCHIVES.md`](EXPERIMENT-ARCHIVES.md) | 已结束实验，只读复核 |

下面这些文档用于复盘实现过程，不是开始使用 Vega 的前置阅读：

| 主题 | 文档 | 结果 |
|---|---|---|
| Supervisor Agent 总计划 | [`VEGA-SUPERVISOR-AGENT-V1-PLAN.md`](VEGA-SUPERVISOR-AGENT-V1-PLAN.md) | V1 已随 v0.2.0 发布 |
| 当前发布交接与历史时间线 | [`SUPERVISOR-AGENT-V1-HANDOFF.md`](SUPERVISOR-AGENT-V1-HANDOFF.md) | 最新结论在文档顶部和末尾 |
| Gate 2B | [`SUPERVISOR-AGENT-GATE-2B-PLAN.md`](SUPERVISOR-AGENT-GATE-2B-PLAN.md) | `gate-exit-pass` |
| Gate 2C 首次运行与修正 | [`SUPERVISOR-AGENT-GATE-2C-PLAN.md`](SUPERVISOR-AGENT-GATE-2C-PLAN.md)、[`SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md`](SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md) | `invalid-harness` / `gate-exit-pass` |
| Gate 3B 预注册系列 | [`SUPERVISOR-AGENT-GATE-3B-PLAN.md`](SUPERVISOR-AGENT-GATE-3B-PLAN.md) | SAG3B-01～08 原始结果保留 |
| Reviewer 上下文实验 | [`REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md`](REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md)、[`REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md`](REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md)、[`REVIEWER-HYPOTHESIS-RECON-PREREGISTRATION.md`](REVIEWER-HYPOTHESIS-RECON-PREREGISTRATION.md) | 未接入默认 Reviewer |
| 日常流程阶段计划 | [`DAILY-USAGE-COMPLETION-PLAN.md`](DAILY-USAGE-COMPLETION-PLAN.md) | Phase 4 已完成 |

## 当前工作

### 当前版本：Supervisor Agent V1

`v0.2.0` 发布 opt-in Supervisor Agent V1。宿主主会话负责只读调查和提交结构化 Plan，
Coding Agent 继续读代码和修改文件；Vega 负责 Plan revision 与人工批准、单 Writer、
Workspace 对账、Checkpoint、Git Task Card、恢复、主会话状态展示，以及复用现有 Core 的
最终可信完成判断。

Gate 0～3A 的状态权威、最小 LangGraph 控制、真实 Codex Adapter、父终态和 Handoff 生产端
已经进入主线。SAG3B-01～08 的失败、超时和副作用阻断保持原始记录，没有事后改写。
2026-08-18 另行批准的发布验收使用真实设置页并发缺陷，完成 partial WIP 停止、Git-only
fresh clone 恢复、Provider 429 fail-closed、Reviewer 打回、人工 Plan revision 2、重新执行
完整 Verification/Risk/Reviewer/Finish 和人工 PR 合入，正式判定为
`release-acceptance-pass`。

当前 Adapter 仍只接受一个未完成 Work Item，并在创建第二 Writer 前 fail-closed。打包 CLI
通过显式 `vega agent` 暴露 V1，不改变 `vega do / loop / goal` 和现有成功语义。完整链路、
历史 Gate 与当前交接分别见：

- [`VEGA-SUPERVISOR-AGENT-V1-PLAN.md`](VEGA-SUPERVISOR-AGENT-V1-PLAN.md)
- [`SUPERVISOR-AGENT-V1-HANDOFF.md`](SUPERVISOR-AGENT-V1-HANDOFF.md)
- [`../eval/real-world-runs.md`](../eval/real-world-runs.md)

### 真实日常使用观察

`v0.2.0` 发布后进入真实日常使用观察。CRWP-V1、Plan-first、Finish 第一屏、Reviewer 覆盖、
Goal P1 显式 Worker 重跑和 Supervisor Agent V1 都已有实现或真实证据；Codex assist、
Claude Code assist、`vega do`、Reviewer 打回、Provider 失败和 Git-only 恢复场景均有记录。

下一阶段不增加多 Work Item、Memory、Provider 平台或新 Runtime。只记录恢复耗时、重复调查、
人工步骤、误判和再次使用意愿；测试名称缺失、空 Scope 展示和 Workspace 汇总不一致等观察项
只有在重复造成误判时才做最小修正。当前状态与历史停止条件见：

- [`DAILY-USAGE-COMPLETION-PLAN.md`](DAILY-USAGE-COMPLETION-PLAN.md)：已完成阶段与验收记录。
- [`ROADMAP.md`](ROADMAP.md)：当前观察期和冻结方向。

### Reviewer Context Bootstrap 对照实验

PR `#49` 已保证 Reviewer 的 `reviewed_files` 覆盖完整变更文件，但路径声明完整不等于理解
未修改的调用方、测试、配置和公共契约。`RCB-01` 已按预注册协议完成 20 次运行，正式裁决为
`insufficient-evidence`：

- A 组上下文 Golden 命中 `0/6`，B 组有效机会命中 `0/5`；
- 候选必要路径召回为 `1/5 = 20%`；
- B 组 Token 中位数约为 A 组 `2.57x`，有效终态耗时约为 `1.52x`；
- 三次 Provider/模型失败按协议消费，C5 还发现了真实的 candidate 契约缺陷，安全负向对照失效；
- 默认 Reviewer、Runtime、CLI、Verdict Schema 和成功语义均未改变。

完整预注册合同见
[`REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md`](REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md)，
脱敏结果见 [`../eval/reviewer-context-bootstrap.md`](../eval/reviewer-context-bootstrap.md)。

当前不把 Context Appendix 接入主线。后续离线计划
[`REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md`](REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md)
已在 Phase 0 停止：开发集需要超出冻结两跳边界的字段数据流、工厂 dispatch 和嵌套实参来源
分析，C3 还存在历史标签误标。Holdout 未解封、未评分，默认 Reviewer 没有改变。

[`REVIEWER-HYPOTHESIS-RECON-PREREGISTRATION.md`](REVIEWER-HYPOTHESIS-RECON-PREREGISTRATION.md)
登记的 RCB-03 开发门槛也已完成。六次调用全部有效；A 命中 `0/3`，B 命中 `1/3`，只增加一次，
虽然误报、只读预算和中位成本均通过，仍未达到至少 `2/3` 且增量 2 次的命中门槛。正式裁决为
`reject-before-holdout`，不进入新 Holdout，不修改默认 Reviewer，也不继续增加提示层或检索
基础设施。脱敏结果已追加到
[`../eval/reviewer-context-bootstrap.md`](../eval/reviewer-context-bootstrap.md)。

### 已完成的当前阶段

- `v0.2.0` annotated Tag 与 GitHub Release 已发布；PR `#73` 和精确 Tag package smoke
  均已通过，该版本发布 opt-in Supervisor Agent V1 和 Git-only 恢复能力。
- `v0.1.5` annotated Tag 与 GitHub Release 已发布；该版本汇总日常使用协议、Finish 展示、
  Reviewer 可信度修复和显式 Worker 重跑的恢复边界。
- `v0.1.4` annotated Tag 与 GitHub Release 已发布；精确 Tag smoke 已登记在
  [`../eval/real-world-runs.md`](../eval/real-world-runs.md)。
- CRWP-V1 三个 Case 都有合同允许的终态，不再选择性重跑。
- [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md)：Codex、Claude Code 与 `vega do`
  共用的调查、固定 Plan 和修改前人工确认协议。
- Finish 第一屏：从既有结构化 artifact 确定性展示裁决、变更、Gate、验证、Reviewer、
  证据上限和下一步，不新增模型调用或第二套裁决。
- Phase 4：Codex assist、Claude Code assist、`vega do`、Reviewer 打回和 fail-closed 五类
  真实场景均已验收，公开摘要追加在 [`../eval/real-world-runs.md`](../eval/real-world-runs.md)。

- [`CORE-REAL-WORLD-PILOT-V1-HANDOFF.md`](CORE-REAL-WORLD-PILOT-V1-HANDOFF.md)：
  最终状态与本机证据接力。
- [`CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md`](CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md)：
  冻结的预注册合同，不反映运行后的最新状态。
- [`CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md`](CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md)：
  运行前控制登记，不替代 Handoff。
- [`../eval/real-world-runs.md`](../eval/real-world-runs.md)：
  真实运行证据，只允许追加。
- [`RELEASE-NOTES-0.2.0.md`](RELEASE-NOTES-0.2.0.md)：v0.2.0 详细变更。
- [`RELEASE-SUMMARY-0.2.0.md`](RELEASE-SUMMARY-0.2.0.md)：v0.2.0 GitHub Release 文案。
- [`RELEASE-NOTES-0.1.5.md`](RELEASE-NOTES-0.1.5.md)：v0.1.5 详细变更。
- [`RELEASE-SUMMARY-0.1.5.md`](RELEASE-SUMMARY-0.1.5.md)：GitHub Release 文案。
- [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)：发布门禁记录。

## 冻结实验与研究记录

这些文档用于解释已有实验、威胁模型和设计取舍，不是当前待实现功能：

| 文档 | 用途 |
|---|---|
| [`ASSURANCE-CONTRACT-CANDIDATE.md`](ASSURANCE-CONTRACT-CANDIDATE.md) | Stage 1 Threat / Evidence 候选合同 |
| [`ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md`](ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md) | SQLite migration 双生实验 |
| [`ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md`](ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md) | expand/backfill/contract 双生实验 |
| [`ASSURANCE-STAGE3-DML-BACKFILL-PREREGISTRATION.md`](ASSURANCE-STAGE3-DML-BACKFILL-PREREGISTRATION.md) | Stage 3 冻结预注册 |
| [`../eval/assurance-validation.md`](../eval/assurance-validation.md) | Assurance 追加式验证记录 |
| [`LEAN-CORE-PLAN.md`](LEAN-CORE-PLAN.md) | 轻量核心清单和增长约束决策 |
| [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md) | Goal P0 与单 checkpoint P1 实验边界 |

## 发布历史

| 版本 | 发布说明 | 发布摘要 |
|---|---|---|
| `v0.2.0` | [`RELEASE-NOTES-0.2.0.md`](RELEASE-NOTES-0.2.0.md) | [`RELEASE-SUMMARY-0.2.0.md`](RELEASE-SUMMARY-0.2.0.md) |
| `v0.1.5` | [`RELEASE-NOTES-0.1.5.md`](RELEASE-NOTES-0.1.5.md) | [`RELEASE-SUMMARY-0.1.5.md`](RELEASE-SUMMARY-0.1.5.md) |
| `v0.1.4` | [`RELEASE-NOTES-0.1.4.md`](RELEASE-NOTES-0.1.4.md) | [`RELEASE-SUMMARY-0.1.4.md`](RELEASE-SUMMARY-0.1.4.md) |
| `v0.1.3` | [`RELEASE-NOTES-0.1.3.md`](RELEASE-NOTES-0.1.3.md) | [`RELEASE-SUMMARY-0.1.3.md`](RELEASE-SUMMARY-0.1.3.md) |
| `v0.1.2` | [`RELEASE-NOTES-0.1.2.md`](RELEASE-NOTES-0.1.2.md) | [`RELEASE-SUMMARY-0.1.2.md`](RELEASE-SUMMARY-0.1.2.md) |
| `v0.1.1` | [`RELEASE-NOTES-0.1.1.md`](RELEASE-NOTES-0.1.1.md) | 无单独摘要 |

远端 Tag 与 GitHub Release 是发布状态的权威来源；上表只提供仓库内发布文档导航。

## 历史交接

已完成、已合并或已冻结的阶段交接移到
[`archive/handoffs/`](archive/handoffs/README.md)。这些文件保留当时的审查、CI 和恢复记录，
但不再作为当前执行入口。

`M002-NODE-PACKAGE-MANAGER-HANDOFF.md` 与
`M003-FINISH-EVIDENCE-SNAPSHOT-HANDOFF.md` 暂时保留在当前目录，因为
`eval/assurance-validation.md` 已按原路径登记它们。为保持追加式证据引用稳定，本轮不移动。

## 文档维护规则

1. 新的当前计划必须从本导航或 `ROADMAP.md` 可达。
2. 已完成的阶段 Handoff 不继续追加新任务；需要保留时移入 `archive/handoffs/`。
3. 预注册和 `eval/` 证据不因结果不理想而改写。
4. 版本未发布前使用“发布候选”，只有远端 Tag 和 Release 存在后才写“已发布”。
5. 文档默认使用仓库相对路径，不记录本机工作区绝对路径、凭据或本地运行产物。
