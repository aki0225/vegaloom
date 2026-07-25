# Vega v0.1.3 Release Summary

> 状态：v0.1.3 发布摘要；tag 与 GitHub Release 由人工确认后创建。

这份摘要用于 GitHub Release 文案、公开仓首页说明和面试展示。详细变更记录见
[`RELEASE-NOTES-0.1.3.md`](RELEASE-NOTES-0.1.3.md)，发布前检查步骤见
[`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.1.3 是 v0.1.x 的维护发布：在 v0.1.2 的成功语义安全修复基础上，补充公开
Assurance Stage 2/3 实验证据、发布准备清单和更清晰的产品边界说明。

## 本版本重点

- 保留 v0.1.2 的结构化验证、终态确认和 fail-closed 成功语义。
- 合入 Stage 2 SQLite migration 个案和 Stage 3 固定 SQLite 有界 DML/Backfill 个案。
- 增加发布前检查清单，覆盖构建、源码树外安装、`vega list-loops` 和 assist loop smoke。
- 更新版本到 `0.1.3`，CI 同步验证 `vegaloom-0.1.3` wheel/sdist。
- 明确实验边界：Stage 2/3 是可复核实验，不是默认 Runtime 或生产数据库自动化能力。

## 可以公开展示的能力

- `vega do` / `vega loop`：启动日常 bug 或 feature 工作流。
- `vega status` / `vega latest`：查看 run 状态和下一步。
- `vega finish`：生成提交前报告和结构化摘要。
- `vega run engineering-change`：保留只读 inspection loop。
- `vega list-loops`：从安装包中发现 baseline loop。
- `.vega.yaml`：配置项目验证命令、runner 策略和风险预算。
- Assurance Stage 1/2/3：展示证据充分性、migration 和有界 DML 的实验路径。

## 不要扩大解释

本版本不证明：

- Vega 是通用 Agent 框架或多 Agent 平台。
- Vega 提供操作系统级 sandbox。
- Vega 自动修改生产数据库、自动执行 backfill 或自动发布。
- Stage 3 的 SQLite 个案等价于通用生产数据库安全。
- LangGraph、Memory、Goal P1 或多 Reviewer 已成为默认主线。

## 发布动作

创建 tag、GitHub Release 或 PyPI 发布都必须由人工单独确认。Vega 自身不会执行这些动作。
