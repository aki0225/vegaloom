# Vega v0.1.2 Release Summary

> 状态：`v0.1.2` Tag 已存在，但没有单独的 GitHub Release。下文“发布前”和“发布动作”
> 保留当时的检查口径，不是当前待办。

这份摘要用于 GitHub Release 文案、公开仓首页说明和面试展示。详细变更记录见
[`RELEASE-NOTES-0.1.2.md`](RELEASE-NOTES-0.1.2.md)，发布前检查步骤见
[`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.1.2 是一个本地优先的 AI 编码工作流 harness：worker 负责修改，reviewer 使用独立
会话边界审查，确定性验证和证据链决定任务是否可以交付。

## 本版本重点

- 收紧结构化验证成功语义：零验证命令、显式跳过验证、非结构化外部日志、缺失或损坏的
  verification artifact 都不能被 reviewer 结论提升为成功。
- 加强终态确认：Loop 写入成功前会重新执行 eval，证据缺失、过期或相互不一致时 fail-closed。
- 保留人工控制边界：Vega 不自动 commit、push、release、部署、删除目标文件或写长期 memory。
- 明确 reviewer 边界：reviewer 不继承 worker 的完整聊天记录，在同一目标仓库的只读视图中
  结合 diff、项目规则、验证摘要和风险证据审查。
- 保持实验隔离：Assurance Stage 1/2/3 已进入主线作为可复核实验和证据，不接入默认 Runtime
  或成功语义。

## 可以公开展示的能力

- `vega do` / `vega loop`：启动日常 bug 或 feature 工作流。
- `vega status` / `vega latest`：查看 run 状态和下一步。
- `vega finish`：生成提交前报告和结构化摘要。
- `vega run engineering-change`：保留只读 inspection loop。
- `vega list-loops`：从安装包中发现 baseline loop。
- `.vega.yaml`：配置项目验证命令、runner 策略和风险预算。

## 发布前已实测

2026-07-25 本地发布准备实测覆盖：

- 使用临时 build venv 构建 wheel/sdist。
- 在源码树外的 venv 安装 wheel。
- 安装后的 `vega --version` 输出 `0.1.2`。
- 安装后的 `vega list-loops` 能列出包内 `engineering-change`。
- 在 `.tmp/release-readiness/` 中创建最小目标仓库，使用安装后的 `vega.exe` 跑 assist loop。
- 人工修改目标 README 后执行 `loop continue --reviewer none`。
- 结构化验证命令执行通过，`finish-report.md` 和 `finish-summary.json` 正常生成。
- 因 reviewer 显式使用 `none`，最终状态保持 `needs_human`，证明没有外部审查结论时不会自动成功。

最近一次主线 CI 也需要在创建 tag 前重新确认：

- 静态检查与节点收集。
- Python 3.11 全量测试。
- Python 3.12 分片。
- Windows 专项与 wheel smoke。
- POSIX 临时目录专项。
- wheel/sdist 构建、安装和 package smoke。

## 不要扩大解释

本版本不证明：

- Vega 是通用 Agent 框架或多 Agent 平台。
- Vega 提供操作系统级 sandbox。
- Vega 自动修改生产数据库、自动执行 backfill 或自动发布。
- Stage 3 的 SQLite 个案等价于通用生产数据库安全。
- LangGraph、Memory、Goal P1 或多 Reviewer 已成为默认主线。

## 发布动作

创建 tag、GitHub Release 或 PyPI 发布都必须由人工单独确认。Vega 自身不会执行这些动作。
