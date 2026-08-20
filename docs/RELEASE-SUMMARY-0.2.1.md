# Vega v0.2.1 发布摘要

> 版本：v0.2.1

这份摘要用于 GitHub Release 正文。详细变更见
[`RELEASE-NOTES-0.2.1.md`](https://github.com/aki0225/vegaloom/blob/v0.2.1/docs/RELEASE-NOTES-0.2.1.md)，
发布步骤见
[`RELEASE-CHECKLIST.md`](https://github.com/aki0225/vegaloom/blob/v0.2.1/docs/RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.2.1 收紧 Supervisor Agent V1 的单 Work Item、允许路径和可选依赖门禁，并明确目标
仓库工作目录、Core Finish 与父 Agent finalize、Git-only fresh-clone 恢复及 Reviewer 隔离
的实际证据边界。

## 本版本重点

- 多个未完成 Work Item、空 `allowed_paths` 或覆盖整个仓库的允许范围不能进入人工批准态。
- 已完成 Work Item 的既有 WIP 可以保留，但当前 attempt 不能借用这些路径继续修改。
- Agent 能力探测覆盖 LangGraph 图与 SQLite checkpoint 依赖，并在创建 run、child、Writer
  或恢复 Task Card 前完成。
- `stopped` 状态不再误导用户执行 `resume-local`，Task Card 也会拒绝常见 POSIX 用户主目录
  绝对路径和本机 `file://` URI。
- Adapter 初始化后必须进入目标仓库再执行 Agent CLI。
- `vega agent finalize` 只采用已有可信 Core Finish，不重新运行或绕过确定性门禁。
- Pages 继续固定引用 `v0.2.0` 发布验收证据，不改写既有来源与哈希。

## 不变边界

- 本版本不增加新 Runtime、Agent 角色、多 Writer、自动 Git 或第二套成功语义。
- 会话隔离不等同于操作系统级安全沙箱。
