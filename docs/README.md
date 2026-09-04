# Vega 文档导航

本文只负责导航。产品事实、当前阶段和历史证据分别由对应权威文档维护，不在这里复制状态叙事。

## 当前入口

| 目的 | 文档 | 权威范围 |
|---|---|---|
| 安装与日常使用 | [`../README.md`](../README.md) | `vega change`、`vega status`、`vega explain` 与高级命令 |
| 产品边界与成功语义 | [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) | Core、Supervisor、证据和行为边界 |
| 当前事项与下一项 | [`CURRENT.md`](CURRENT.md) | 由机器计划和事件账本生成的当前状态 |
| 演进事项与验收 | [`../plans/vega-agent-evolution.json`](../plans/vega-agent-evolution.json) | 稳定事项、依赖和要求检查 |
| 路线决策与历史 | [`ROADMAP.md`](ROADMAP.md) | 为什么选择或停止某条路线 |
| 有界自主执行计划 | [`BOUNDED-AUTONOMY-V1-PLAN.md`](BOUNDED-AUTONOMY-V1-PLAN.md) | 自然语言 Planning、Contract Compiler 和有界批准 |
| 已完成的治理计划 | [`AI-MAINTAINABILITY-GOVERNANCE-PLAN.md`](AI-MAINTAINABILITY-GOVERNANCE-PLAN.md) | 三轮治理范围、Dogfood 和验收结果 |
| 完整使用流程 | [`USAGE-WALKTHROUGH.md`](USAGE-WALKTHROUGH.md) | ChangeRun、交互、恢复和交付 |
| 调查和计划协议 | [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) | 修改前调查、事实与假设、人工确认 |
| Runtime 与证据链 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | v0.5.0 ChangeRun、Provider Session、Core 与数据流 |
| Supervisor 状态合同 | [`SUPERVISOR-AGENT-STATE-AUTHORITY.md`](SUPERVISOR-AGENT-STATE-AUTHORITY.md) | 状态、恢复和事实权威 |
| v0.3.0 能力快照 | [`MVP-SCOPE.md`](MVP-SCOPE.md) | 已发布范围与当时明确不做的内容 |
| v0.3.0 实施记录 | [`PERSISTENT-INTERACTIVE-AGENT-V1.md`](PERSISTENT-INTERACTIVE-AGENT-V1.md) | 持久交互式 Agent 的冻结决策 |
| 工作区文件规范 | [`WORKSPACE-HYGIENE.md`](WORKSPACE-HYGIENE.md) | 临时文件、运行产物和清理边界 |

## 发布

当前稳定版本为 [`v0.5.0`](https://github.com/aki0225/vegaloom/releases/tag/v0.5.0)：

- [`RELEASE-NOTES-0.5.0.md`](RELEASE-NOTES-0.5.0.md)
- [`RELEASE-SUMMARY-0.5.0.md`](RELEASE-SUMMARY-0.5.0.md)
- [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)

上一个稳定版本为 [`v0.4.0`](https://github.com/aki0225/vegaloom/releases/tag/v0.4.0)：

- [`RELEASE-NOTES-0.4.0.md`](RELEASE-NOTES-0.4.0.md)
- [`RELEASE-SUMMARY-0.4.0.md`](RELEASE-SUMMARY-0.4.0.md)

已发布版本的历史材料：

- [`RELEASE-NOTES-0.3.1.md`](RELEASE-NOTES-0.3.1.md)
- [`RELEASE-SUMMARY-0.3.1.md`](RELEASE-SUMMARY-0.3.1.md)
- [`RELEASE-NOTES-0.3.0.md`](RELEASE-NOTES-0.3.0.md)
- [`RELEASE-SUMMARY-0.3.0.md`](RELEASE-SUMMARY-0.3.0.md)

旧版本的 `RELEASE-NOTES-*` 与 `RELEASE-SUMMARY-*` 是不可变历史材料。远端 Tag 与 GitHub
Release 是发布动作的事实来源。

## 实验与证据

- `../eval/`：追加式实验和真实运行结果；遵循 [`../eval/AGENTS.md`](../eval/AGENTS.md)。
- [`EXPERIMENT-ARCHIVES.md`](EXPERIMENT-ARCHIVES.md)：冻结实验与归档 Tag 索引。
- [`ASSURANCE-CONTRACT-CANDIDATE.md`](ASSURANCE-CONTRACT-CANDIDATE.md)：Threat/Evidence 候选合同。
- [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md)：Goal P0/P1 历史设计与实验边界。
- Reviewer Context、Assurance Stage、CRWP 和 Supervisor Gate 文档均为历史预注册或结果材料，
  不能作为当前执行入口。

## 历史材料

- 已完成或已替代的交接位于 [`archive/handoffs/`](archive/handoffs/README.md)。
- Supervisor V1 Plan、Gate 2B/2C/3B、Daily Usage Plan 和 CRWP 三件套记录实施过程，当前行为
  以产品契约、架构、源码和测试为准。
- `.vega/archive/tasks/` 保存历史 Git Task Card，不代表当前可恢复任务。

## 维护规则

1. 实施机器计划事项的 PR 在同一 Diff 中增加 `../plans/events/` 状态事件，并重新生成
   `CURRENT.md`；没有对应事项的普通修复、文档或维护任务不新增占位事件。
2. 已进入主线的状态事件及其事项定义不得改写或删除；尚无事件的未来事项可以随实现证据调整。
3. 历史预注册、失败和证据不足不得事后改写。
4. 被 `eval/` 引用的历史路径移动前必须评估链接稳定性；不能为了目录整齐破坏证据引用。
5. 公开文档只使用仓库相对路径和脱敏示例。
