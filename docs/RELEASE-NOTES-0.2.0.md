# Vega v0.2.0 发布说明

v0.2.0 发布候选包含第一个 opt-in Supervisor Agent。它不替代 Codex 的代码理解和修改能力，
也不把 Vega 扩张成通用多 Agent 平台；新增的是一层可观察、可暂停、可恢复的软件工程控制面。

## Supervisor Agent V1

- 主会话先调查问题并提交结构化 Plan，Vega 固定目标、Non-goals、成功条件、允许路径、
  验证命令和风险说明，仍需人工显式批准。
- 当前只接受一个未完成 Work Item，并且同一时刻只允许一个受身份绑定的 Writer。
- Worker 的自述只作为 Claim 保存；Workspace、进程、Diff、验证和 Artifact 形成 Machine
  Observation，Supervisor 再从 `next / repair / replan / human / finalize` 中选择动作。
- `vega status` 与 `vega watch` 可以从主会话查看低频状态卡和安全事件，不显示模型隐藏推理、
  原始命令参数或完整工具日志。
- 最终成功仍来自既有 Core Finish。只有绑定的 Verification、Risk、独立 Reviewer、Artifact
  完整性和证据新鲜度全部通过，父 Agent 才能发布 `completed / ready_to_commit`。

## 中断、恢复与 Git 交接

- `pause`、`stop`、`recover` 和 `resume-local` 先对账真实进程、Workspace 与外部副作用，
  不根据聊天记录猜测 Worker 是否完成。
- Handoff 在 Writer 已停止且现场可解释时生成 Checkpoint、Resume Capsule 和 Git Task Card；
  运行 State、Trace 与 LangGraph SQLite 仍留在本机。
- 新 clone 只依赖 Git 中的 WIP 与 Task Card 重建 Goal、Plan、Work Item 和比较基线；旧
  Verification、Risk 与 Reviewer 证据自动降为 historical，并在新现场重新运行。
- 已消费的 Task Card 不会让新 run 继续保持 `handoff_ready`。v0.2.0 在恢复时重置本机
  Handoff 状态，避免 Provider 异常后的人工副作用裁决被旧交接状态错误阻断。

## Codex 接入

- `vega adapters init codex --repo <repo>` 生成 `$vega-agent`、`$vega-loop` 和
  `$vega-review` 仓库级 Skill。
- `$vega-agent` 约定主会话只读调查、单 Work Item Plan、人工批准、受控执行、一次有界修复、
  交接恢复和最终证据展示。
- 真实 Codex Writer 与 Reviewer 均关闭继承 MCP、网络和额外可写根；Reviewer 保持独立只读
  会话，不继承 Worker 完整对话或中间推理。

## 真实发布验收

发布候选使用一个真实前端并发缺陷完成 Git-only 接力：

1. Worker 在允许范围内形成 WIP，经身份绑定 stop、现场对账和人工副作用裁决后生成 Task Card；
2. 新的隔离 clone 仅从 Git 恢复，没有复制旧 `runs/`、Trace、SQLite、虚拟环境或聊天；
3. Provider 429 的恢复 attempt 保持 `needs_human`，没有自动重试或虚假成功；
4. 首次完整 Core 被独立 Reviewer 打回：原测试没有覆盖 React 状态提交前的同批次竞态，
   并且缺少目标仓库要求的后端测试证据；
5. 人工批准 Plan revision 2 后，新 child 补强同批次回归，重新执行全部门禁并得到
   `ready_to_commit`；
6. 目标仓库最终验证为后端 `361 passed`、设置页 `7 passed`、前端完整 `180 passed`，
   TypeScript、隔离构建和 `git diff --check` 通过，随后经人工 PR 合入。

这项验收不改写 SAG3B-01～08 的历史失败结果。它证明 v0.2.0 候选可以在独立 clone 中从
Git Task Card 恢复真实 WIP，允许 Reviewer 推翻 Worker Claim，并在补足证据后重新形成可信
Finish。

## 不变边界

- Vega 不自动 commit、push、release、部署、回滚、删除目标文件或写入长期 Memory。
- 不支持多 Work Item 自动连续派发、多 Worker 并行、Planner Agent、Provider 平台、
  daemon、Web UI 或向量数据库。
- Task Card 是可移植交接摘要，不是完整聊天备份；Trace 是排查线索，不是第二套状态数据库。
- Worktree、只读 Reviewer 和会话隔离都不等同于容器或操作系统级安全沙箱。
- `ready_to_commit` 只表示满足人工提交前检查，不表示已经合并，也不证明生产环境绝对安全。
