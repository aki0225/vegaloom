# v0.3.0 范围

## 产品

- `Vega`：产品名；
- `vegaloom`：仓库和 Python distribution；
- `vega`：CLI 与导入包。

Vega v0.3.0 是一个单机软件工程 Agent。它用人工批准的 Change Contract 约束 Coding Agent，
在隔离 Worktree 中运行有限 Work Item，并用项目验证和独立 Reviewer 判断候选是否可以进入
人工 PR 检查。

## 已包含

### 调查和计划

- 宿主主会话读取 `AGENTS.md`、`.vega.yaml`、代码、测试和 Git 状态；
- 事实、假设和未决问题分开；
- Change Contract 与 Execution Plan 使用严格 JSON schema；
- Contract 变化必须重新批准；
- Plan 可以在合同内修订。

### 执行

- 一个 ChangeRun、一个任务分支、一个隔离 Worktree；
- 同一时刻只有一个可写 Worker；
- Codex App Server Worker Thread 在 Repair 和后续 Work Item 中复用；
- 显式 `--fresh-session` 使用一次性 `codex exec`；
- Worker timeout、heartbeat、stop request 和进程树对账；
- Worker Claim 与机器 Observation 分开。

### Git 和门禁

- Vega 在范围检查后创建本地 Candidate Commit；
- Candidate SHA 绑定 Verification、Risk 和 Reviewer；
- Accepted Checkpoint 顺序推进 Work Item；
- `.vega.yaml` 定义验证、路径、预算和必审风险；
- 验证失败、证据过期或 Workspace 漂移时 fail-closed。

### Reviewer

- 每个 Work Item 使用独立只读 Reviewer Thread；
- Reviewer 不继承 Worker Thread 或完整聊天；
- 同一 Work Item 的 Repair 复查可以复用 Reviewer Thread；
- `reviewed_files` 覆盖门禁；
- 多 Work Item、Replan、高风险或 Repair 后按条件运行最终集成审查；
- 普通 finding 自动生成 Fix Packet。

### 交互和可见性

- `status` 状态卡；
- `watch` 低频安全事件；
- `steer`；
- App Server 审批和用户输入响应；
- 原生会话 takeover/reclaim；
- Provider Session 的 Thread、owner、Turn、压缩和 Token 状态；
- 不显示推理、完整模型正文、原始命令参数或凭据。

### 恢复

- 本机 Checkpoint 恢复；
- Worker 失去可信终态后的进程、Workspace 和副作用对账；
- Git Task Card Handoff；
- fresh clone / 换机器恢复；
- Provider Thread 失效后从 Git 与 Task Card 重建任务语义。

### 交付

- Core Finish 保留唯一成功语义；
- `agent-final-report.json/md` 确定性生成；
- 完整变更文件、Reviewer 重点、验证、风险和未证明事项分开；
- 人工决定是否 push、创建 PR 或合并。

## 公共命令

```text
capabilities
config check
adapters init codex

start
approve
run
status
watch
latest

steer
respond
revise
retry
pause
stop
recover
adjudicate
takeover
reclaim
handoff
resume
```

## 当前不包含

- Claude Code、Pi 或其他 Provider 的自动 Adapter；
- 多 Worker 并行；
- Reviewer 投票或辩论；
- Planner Runtime；
- 独立 Web UI / TUI；
- 常驻 daemon、服务端队列或数据库；
- 向量库、Repo Map 或自动长期 Memory；
- 工具策略引擎；
- 自动部署、回滚或生产环境写入；
- 自动 Git 交付动作。

历史 Goal、Memory、Assurance 和 Inspection 实现只保留用于兼容、实验记录或内部复用，不再作为
当前公共入口。

## 发布判定

v0.3.0 已完成以下发布门禁：

- App Server 协议和持久会话测试；
- Supervisor、Core、Security 和 Experimental 全量测试；
- repository hygiene、architecture growth、Ruff、compileall 和 `git diff --check`；
- wheel 与 sdist 安装 smoke；
- 至少一个真实 Codex ChangeRun，覆盖 Thread 复用、独立 Reviewer、主会话可见进度和最终报告；
- 同一提交的 PR CI。

发布事实以注解 Tag [`v0.3.0`](https://github.com/aki0225/vegaloom/releases/tag/v0.3.0)
和对应 GitHub Release 为准。
