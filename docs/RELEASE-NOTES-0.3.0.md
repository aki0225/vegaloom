# Vega v0.3.0 发布说明

> 状态：发布候选。正式 Tag 与 GitHub Release 创建前，不视为已发布。

v0.3.0 把 Bounded Change Loop 变成 Vega 的唯一公共 Agent 流程，并接入 Codex App Server
持久会话。目标是减少长任务中的人工转贴和重复入口，不改变 Verification、Risk、Reviewer 和
Finish 的可信完成语义。

## 主要变化

### 持久 Worker

- 一个 ChangeRun 复用一个 Codex Worker Thread；
- Repair 和后续 Work Item 在同一 Thread 继续；
- Contract revision 实质变化后重新建立 Thread；
- Codex 上下文压缩后，下一 Turn 补充 32 KiB 软上限的 Task Anchor；
- `--fresh-session` 显式保留一次性 `codex exec` 路径。

### 独立 Reviewer

- 每个 Work Item 使用独立只读 Reviewer Thread；
- Reviewer 不继承 Worker Thread、完整聊天或写权限；
- 同一 Work Item 的 Repair 复查可以复用 Reviewer Thread；
- 多 Work Item、Replan、高风险或 Repair 后按条件运行累计集成审查。

### 主会话控制

- `status` 显示 Provider Session、Work Item、Diff、门禁和下一步；
- `watch` 输出低频安全事件；
- `steer` 在安全事件边界补充指令；
- `respond` 处理 App Server 审批和用户输入；
- `takeover / reclaim` 支持原生 Codex 会话接管。

### 单一公共 CLI

公共入口统一为顶层 ChangeRun 命令。删除 `vega do`、`vega loop`、`vega agent`、
`vega goal` 和旧 inspection 命令，不保留兼容别名。仍被 ChangeRun 使用的 Core Runtime
保留为内部实现。

### 最终报告

`agent-final-report.json/md` 从 Git、Verification、Risk、Reviewer 和 Worker Claim
确定性生成。报告列出完整变更文件、Reviewer 建议重点、验证、风险和未证明事项，不增加总结
模型。

### 真实 Dogfood

两 Work Item 的真实 Codex App Server ChangeRun 已完成：Worker 复用同一 Thread，Steer 在
安全事件边界送达，两个 Work Item 分别经过独立 Reviewer，累计 Candidate 再经过一次集成
审查，最终为 `ready_to_commit`。

Dogfood 先暴露并保留了三次失败现场：Windows 子进程参数可能进入终端标题、长期 MCP
子进程阻塞 Turn 收尾、最终合同验证被过早用于中间 Work Item。对应修复没有降低门禁：
App Server 子树改为隐藏并按进程树终止；原始 stderr 不进入用户界面；Contract 级验证延后
到最后一项。完整记录见 [`../eval/real-world-runs.md`](../eval/real-world-runs.md)。

## 仍然保持

- Change Contract 与 Execution Plan 分开；
- 单 Writer 与隔离 Worktree；
- Git Candidate SHA 绑定门禁；
- 验证失败不能被 Reviewer approve 覆盖；
- 高风险修改保持人工检查；
- 中断和未知副作用 fail-closed；
- Git Task Card 跨机器恢复；
- 不自动执行 Git 交付和生产动作。

## 兼容性

这是一次公共 CLI 破坏性更新。v0.2.x 的 run Artifact 和历史 Task Card可以查看或按既有
恢复合同读取，但旧命令不再注册。自动化脚本需要迁移到 `start / approve / run / status`
及相关顶层命令。

内部 Python 模块不是稳定 SDK；稳定程序化导出仍只有 `vega.__version__`。

## 发布门禁

正式发布前必须在同一提交完成：

- 全量测试与静态检查；
- architecture growth 与 repository hygiene；
- wheel / sdist 安装 smoke；
- 真实 Codex App Server ChangeRun；
- Worker Thread 复用、独立 Reviewer、Steer 和最终报告核对；
- PR CI；
- 人工发布复核。
