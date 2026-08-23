# Vega 文档导航

本文只负责导航。产品事实、当前阶段和历史证据分别由对应权威文档维护，不在这里复制状态叙事。

## 当前入口

| 目的 | 文档 | 权威范围 |
|---|---|---|
| 安装与日常使用 | [`../README.md`](../README.md) | 用户入口与常用命令 |
| 产品边界与成功语义 | [`PRODUCT-CONTRACT.md`](PRODUCT-CONTRACT.md) | Core、Supervisor、证据和行为边界 |
| 当前阶段与唯一下一步 | [`ROADMAP.md`](ROADMAP.md) | 当前路线入口 |
| 当前治理计划 | [`AI-MAINTAINABILITY-GOVERNANCE-PLAN.md`](AI-MAINTAINABILITY-GOVERNANCE-PLAN.md) | 三轮范围、验收与停止条件 |
| 完整使用流程 | [`USAGE-WALKTHROUGH.md`](USAGE-WALKTHROUGH.md) | Core 与 Supervisor 操作 |
| 调查和计划协议 | [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) | 修改前调查、事实与假设、人工确认 |
| Runtime 与证据链 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 模块职责和数据流 |
| Supervisor 状态合同 | [`SUPERVISOR-AGENT-STATE-AUTHORITY.md`](SUPERVISOR-AGENT-STATE-AUTHORITY.md) | 状态、恢复和事实权威 |
| 当前版本能力 | [`MVP-SCOPE.md`](MVP-SCOPE.md) | v0.1 baseline 与 v0.2.x 可选能力 |
| 工作区文件规范 | [`WORKSPACE-HYGIENE.md`](WORKSPACE-HYGIENE.md) | 临时文件、运行产物和清理边界 |

## 发布

当前稳定版本为 `v0.2.1`：

- [`RELEASE-NOTES-0.2.1.md`](RELEASE-NOTES-0.2.1.md)
- [`RELEASE-SUMMARY-0.2.1.md`](RELEASE-SUMMARY-0.2.1.md)
- [`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)

旧版本的 `RELEASE-NOTES-*` 与 `RELEASE-SUMMARY-*` 是不可变历史材料。远端 Tag 与 GitHub
Release 是发布动作的事实来源。

## 实验与证据

- `../eval/`：追加式实验和真实运行结果；遵循 [`../eval/AGENTS.md`](../eval/AGENTS.md)。
- [`EXPERIMENT-ARCHIVES.md`](EXPERIMENT-ARCHIVES.md)：冻结实验与归档 Tag 索引。
- [`ASSURANCE-CONTRACT-CANDIDATE.md`](ASSURANCE-CONTRACT-CANDIDATE.md)：Threat/Evidence 候选合同。
- [`LONG-RUNNING-GOALS.md`](LONG-RUNNING-GOALS.md)：Goal P0/P1 兼容入口与历史边界。
- Reviewer Context、Assurance Stage、CRWP 和 Supervisor Gate 文档均为历史预注册或结果材料，
  不能作为当前执行入口。

## 历史材料

- 已完成或已替代的交接位于 [`archive/handoffs/`](archive/handoffs/README.md)。
- Supervisor V1 Plan、Gate 2B/2C/3B、Daily Usage Plan 和 CRWP 三件套记录实施过程，当前行为
  以产品契约、架构、源码和测试为准。
- `.vega/archive/tasks/` 保存历史 Git Task Card，不代表当前可恢复任务。

## 维护规则

1. 状态变化只更新权威文档，导航只调整链接和标签。
2. 新计划必须从本页或 `ROADMAP.md` 可达，并声明 active、completed 或 archived。
3. 历史预注册、失败和证据不足不得事后改写。
4. 被 `eval/` 引用的历史路径移动前必须评估链接稳定性；不能为了目录整齐破坏证据引用。
5. 公开文档只使用仓库相对路径和脱敏示例。
