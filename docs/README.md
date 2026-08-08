# Vega 文档导航

> 更新时间：2026-08-08

本文只负责说明每份文档现在是什么用途。产品行为以产品契约、源码和实际运行证据为准；
历史交接中的“下一步”“不要合并”或旧分支状态，不代表当前主线。

## 建议阅读顺序

| 目的 | 文档 | 状态 |
|---|---|---|
| 快速了解产品与真实运行 | [Vega 在线展示](https://aki0225.github.io/vegaloom/) | GitHub Pages |
| 先了解怎么使用 | [`../README.md`](../README.md) | 当前入口 |
| 查看日常完整流程 | [`USAGE-WALKTHROUGH.md`](USAGE-WALKTHROUGH.md) | 当前使用说明 |
| 确认产品边界和成功语义 | [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) | 当前权威契约 |
| 查看调查与修改前确认协议 | [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) | Phase 2 已完成 |
| 查看日常流程完成状态 | [`DAILY-USAGE-COMPLETION-PLAN.md`](DAILY-USAGE-COMPLETION-PLAN.md) | Phase 4 已完成 |
| 查看 RCB-01 预注册合同 | [`REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md`](REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md) | 历史冻结合同 |
| 查看 RCB-01 实验结果 | [`../eval/reviewer-context-bootstrap.md`](../eval/reviewer-context-bootstrap.md) | `insufficient-evidence` |
| 查看 RCB-02 离线检索计划 | [`REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md`](REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md) | 草案，未开始 |
| 查看长期路线和历史决策 | [`ROADMAP.md`](ROADMAP.md) | 当前路线入口 |
| 查看 Runtime 与证据链 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 当前架构说明 |
| 查看 v0.1 范围 | [`MVP-SCOPE.md`](MVP-SCOPE.md) | 当前范围说明 |
| 查看工作区规范 | [`WORKSPACE-HYGIENE.md`](WORKSPACE-HYGIENE.md) | 当前规范 |

## 当前工作

### 真实日常使用观察

`v0.1.4` 发布、CRWP-V1 合同允许验证、Plan-first 协议、Finish 第一屏改进和 Phase 4 真实
使用验收都已完成。Codex assist、Claude Code assist、`vega do`、Reviewer 打回和 fail-closed
场景均已有记录。

当前不再增加新的 Runtime 或基础设施，只在真实日常使用中记录问题并修复影响判断可信度的
实际缺陷。Finish 第一屏尚有测试名称缺失、空 Scope 展示和 Workspace 汇总不一致等观察项；
只有这些问题重复造成误判时才做最小修正。当前状态与停止条件见：

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

当前不把 Context Appendix 接入主线。下一步只做离线的
[`REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md`](REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md)：
先验证 `Diff → 符号 → 有界关系扩展 → 代码区段` 的必要路径召回，不调用模型，不修改默认 Reviewer。

### 已完成的当前阶段

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
- [`RELEASE-NOTES-0.1.4.md`](RELEASE-NOTES-0.1.4.md)：v0.1.4 详细变更。
- [`RELEASE-SUMMARY-0.1.4.md`](RELEASE-SUMMARY-0.1.4.md)：GitHub Release 文案。
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
| [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md) | Goal P0 说明与 P1 设计草案 |

## 发布历史

| 版本 | 发布说明 | 发布摘要 |
|---|---|---|
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
