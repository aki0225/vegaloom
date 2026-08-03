# Vega 文档导航

> 更新时间：2026-08-03

本文只负责说明每份文档现在是什么用途。产品行为以产品契约、源码和实际运行证据为准；
历史交接中的“下一步”“不要合并”或旧分支状态，不代表当前主线。

## 建议阅读顺序

| 目的 | 文档 | 状态 |
|---|---|---|
| 先了解怎么使用 | [`../README.md`](../README.md) | 当前入口 |
| 查看日常完整流程 | [`USAGE-WALKTHROUGH.md`](USAGE-WALKTHROUGH.md) | 当前使用说明 |
| 确认产品边界和成功语义 | [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) | 当前权威契约 |
| 查看下一阶段执行顺序 | [`DAILY-USAGE-COMPLETION-PLAN.md`](DAILY-USAGE-COMPLETION-PLAN.md) | 已确认、待实施 |
| 查看长期路线和历史决策 | [`ROADMAP.md`](ROADMAP.md) | 当前路线入口 |
| 查看 Runtime 与证据链 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 当前架构说明 |
| 查看 v0.1 范围 | [`MVP-SCOPE.md`](MVP-SCOPE.md) | 当前范围说明 |
| 查看工作区规范 | [`WORKSPACE-HYGIENE.md`](WORKSPACE-HYGIENE.md) | 当前规范 |

## 当前进行中的工作

### `0.1.4` 发布候选

截至 2026-08-03，源码版本为 `0.1.4`，远端最新 Tag 为 `v0.1.3`。

- [`RELEASE-NOTES-0.1.4.md`](RELEASE-NOTES-0.1.4.md)：详细变更。
- [`RELEASE-SUMMARY-0.1.4.md`](RELEASE-SUMMARY-0.1.4.md)：GitHub Release 候选文案。
- [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)：发布门禁。

### CRWP-V1

- [`CORE-REAL-WORLD-PILOT-V1-HANDOFF.md`](CORE-REAL-WORLD-PILOT-V1-HANDOFF.md)：
  当前状态与下一步，以它为准。
- [`CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md`](CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md)：
  冻结的预注册合同，不反映运行后的最新状态。
- [`CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md`](CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md)：
  运行前控制登记，不替代 Handoff。
- [`../eval/real-world-runs.md`](../eval/real-world-runs.md)：
  真实运行证据，只允许追加。

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
| `v0.1.3` | [`RELEASE-NOTES-0.1.3.md`](RELEASE-NOTES-0.1.3.md) | [`RELEASE-SUMMARY-0.1.3.md`](RELEASE-SUMMARY-0.1.3.md) |
| `v0.1.2` | [`RELEASE-NOTES-0.1.2.md`](RELEASE-NOTES-0.1.2.md) | [`RELEASE-SUMMARY-0.1.2.md`](RELEASE-SUMMARY-0.1.2.md) |
| `v0.1.1` | [`RELEASE-NOTES-0.1.1.md`](RELEASE-NOTES-0.1.1.md) | 无单独摘要 |

`v0.1.3` 和 `v0.1.1` 已有 GitHub Release；`v0.1.2` 只有 Tag 和仓库内发布文档。

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
