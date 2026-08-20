# Vega v0.2.1 发布说明

> 版本：v0.2.1

v0.2.1 是维护版本，不增加新的 Agent 角色、Runtime 或成功路径。它收紧 Supervisor Agent V1
的批准前约束与依赖探测，并把安装、恢复、Finish 和安全边界说明对齐到实际实现。

## Agent 合同加固

- 人工批准前要求 V1 Plan 恰好包含一个未完成 Work Item，避免把多项待办误当作当前
  Adapter 已支持的执行范围；历史中已完成或已替换的 Work Item 仍可保留。
- Work Item 必须声明至少一个 `allowed_paths`；没有可机械核对路径边界的计划不能进入批准态。
- V1 拒绝 `**`、`**/*` 等覆盖整个仓库的允许范围；需要扩大修改面时必须枚举路径并重新批准。
- 已完成或已替换 Work Item 的既有 WIP 会在 dispatch 前冻结。当前 Work Item 自己的 WIP
  仍可继续修复；如果同一路径同时命中历史与当前范围，Vega 会停止派发并要求重新批准 Plan。
- Agent 能力探测同时加载 LangGraph 图组件和 SQLite checkpoint 组件；可选依赖不完整时不能
  把环境报告为可执行，也不能创建 run、child、Writer binding 或恢复 Task Card。
- `stopped` 的父 Agent 不再提示使用 `resume-local`；状态卡会明确要求人工创建 Handoff 或新
  Agent run，避免把不可恢复终态描述成可直接继续。
- Task Card 可移植性检查除 Windows 盘符和 UNC 路径外，也拒绝 `/home/...`、
  `/Users/...`、`/private/var/...`、`/var/folders/...`、`/tmp/...`、`/var/tmp/...`
  下的常见本机路径，并拒绝任何 `file:` URI。

## 使用与证据边界

- `vega adapters init codex --repo <target-repo>` 只写入目标仓库 Skill，不切换 shell；后续
  Agent CLI 必须在目标仓库目录执行。
- `vega finish` 处理普通 Core run；`vega agent finalize` 只采用已绑定的可信 Core Finish，
  用于父 Agent 终态发布或中断恢复，不重新运行验证和 Reviewer。
- 对外恢复能力统一表述为 Git-only fresh-clone / 换目录接手。v0.2.0 的公开证据没有证明
  另一台物理机器已经完成验收。
- Reviewer 文案只声明仓库实现能够证明的只读会话、MCP 和个人扩展隔离，不把 Writer 的
  出站网络配置错误外推到 Reviewer。

## 发布与展示

- distribution、Python 包版本和 package smoke 更新为 `0.2.1`。
- `SECURITY.md` 的当前支持线更新为 `0.2.x`。
- GitHub Pages 保留 `v0.2.0` 发布验收的固定来源与哈希；本维护版本不改写既有证据。

## 不变边界

- 仍只支持一个未完成 Work Item 和一个身份绑定 Writer。
- 不增加自动 commit、push、release、部署、回滚、长期 Memory 或多 Writer 并行。
- Verification、Risk、独立 Reviewer 和 Core Finish 仍共同决定能否进入
  `ready_to_commit`。
