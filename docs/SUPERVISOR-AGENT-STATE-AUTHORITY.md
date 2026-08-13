# Supervisor Agent V1 状态权威与最小合同 ADR

> 状态：`accepted / Gate 2A 本地验证完成`
>
> 日期：2026-08-13
>
> 实施分支：`experiment/supervisor-agent-v1`

## 1. 决定

Vega Agent V1 使用一个 Supervisor 控制层编排外部 Coding Agent，并继续把现有 Vega Core 作为
最终可信判断链。控制层可以记录计划、观察和路由，但不能产生第二套成功语义。

权威顺序固定为：

```text
用户指令、AGENTS.md、.vega.yaml
  > 已批准的 Goal / Plan revision
  > Worker 建议与 accepted memory

存活进程、Git 与真实 Workspace
  > 当前结构化 Artifact 和新鲜度校验
  > 已核对 Checkpoint / Task Card 历史
  > Worker Claim 和模型摘要
```

## 2. 最小对象

| 对象 | 唯一职责 | 当前事实权威 |
|---|---|---|
| Task Card | Git 中的任务、批准计划和跨机器 Resume Capsule | 任务意图与最后一次可提交交接 |
| Agent State | 当前本机节点、active operation/child、预算和允许动作 | 本机控制状态 |
| Observation | 一次对进程、Workspace 和 Artifact 的机器对账 | 决策输入 |
| Checkpoint | 已对账现场的不可变快照 | 历史恢复锚点 |
| Decision | Observation 下的允许动作、选择与理由 | 路由记录 |
| Task Brief | 为当前角色编译的有界上下文 | 派生输入，不是状态权威 |
| Trace | 追加式安全事件和路由线索 | 审计线索，不可重放为成功 |
| Graph checkpoint | LangGraph 图游标和 pending interrupt | 不拥有业务事实 |

详细 Diff、验证日志、Risk 和 Reviewer 继续使用现有 Core Artifact，不在 Agent 层复制。

## 3. 状态与动作

Agent State 的阶段限定为：

```text
planning / awaiting_approval / ready / acting / observing /
needs_human / finalizing / completed / stopped
```

Supervisor Decision 只能选择：

```text
next / repair / replan / human / finalize
```

最低优先级的模型建议必须经过确定性规则过滤：

- Plan 未批准、批准过期或 Workspace binding 冲突时不能 `next/repair/finalize`；
- 旧 Writer 或 owned process 仍存活时不能启动第二 Writer；
- Verification 失败、Risk 阻断、Artifact 不完整或 stale 时不能 `finalize`；
- 未知外部副作用不能自动重试；
- Graph `END` 不能写入 `ready_to_commit`。

## 4. Task Card 与跨机器恢复

Task Card 位于 Git 跟踪的 `.vega/tasks/YYYY-MM/YYYY-MM-DD-task-slug.md`，正文包含目标、事实与
假设、批准 Plan、进度、失败尝试、风险、验证、Resume Capsule 和下一步。

WIP 可以和 Task Card 一起提交到任务分支。Task Card 只能记录 `handoff_ready` 或
`handoff_blocked`，不能把未完成提交写成验证通过。新机器必须重新拉取远端、发现 Task Card、
校验分支与内容摘要、创建新本机 run，并把旧验证与 Reviewer 降为历史证据。

Vega 生成、校验和展示待提交清单，但不自动 commit 或 push。

## 5. Task Brief

Task Brief 无下限，默认软上限为 `32 KiB` UTF-8 字节。按以下优先级生成：

1. 完整保留 Goal、Non-goals、批准、当前 Work Item、禁止项、成功条件、风险和下一动作；
2. 结构化压缩事实、失败尝试、changed files 和门禁状态；
3. 长代码、日志和旧 attempt 只保留 Artifact 引用。

必需内容超过上限时进入人工处理或拆分 Work Item，不静默截断。宿主 Codex/Claude Code 的压缩
可以继续帮助原会话，但不能替代 Task Brief 或任务状态。

## 6. Checkpoint 与 Trace

Checkpoint 只在 Plan revision、Work Item 边界、Worker 终态/丢失后的对账、人工 steer/pause/stop、
Gate 路由变化、恢复/交接和 Finish 前写入。`safe` 只代表现场可解释，不代表代码正确。

Trace 每条只保存事件、节点、Work Item、operation/child、状态版本、Workspace fingerprint、
Observation 摘要、路由理由和 Artifact 引用。不得保存完整模型输出、内部推理、凭据或完整命令日志。

## 7. LangGraph 边界

Gate 0 的合同和 reducer 保持引擎无关。Gate 1 才引入可选 LangGraph 依赖，且仅用于：

- `StateGraph` 条件边；
- 图游标持久化；
- `interrupt()` 和人工 resume；
- 低频安全事件。

LangGraph checkpoint 丢失时，Vega 必须回到 Task Card、Agent State、Checkpoint、真实 Workspace 和
Core Artifact 对账。不能以恢复 Graph checkpoint 代替恢复执行事实。

## 8. Gate 0 退出条件

进入 Gate 1 前必须具备：

- 严格 Schema 与未知版本 fail-closed；
- Task Card 与批准 digest；
- Observation、Decision 和状态迁移纯函数；
- Task Brief 预算和敏感信息测试；
- Resume Capsule 与分支发现规则；
- 不同 Observation 至少产生三种不同合法 Decision；
- 核心默认入口和成功语义没有变化。

## 9. Gate 1 与 Gate 2A 实施证据

Gate 1 已实现 Fake Worker 可见控制循环、Plan 批准、Task Brief、状态卡、LangGraph 条件路由和
`next / repair / replan / human / finalize` 决策。Graph 只能进入 `finalizing`，不能写入
`ready_to_commit`。

Gate 2A 已补充：

- run mutation 锁，确保并发 dispatch 只有一个 Writer 能取得绑定；
- `worker_reserved` 与 `worker_started` 两阶段边界，区分“已登记”与“副作用已开始”；
- Worker 仍存活时保留 child/operation binding，禁止第二 Writer；
- operation 未开始且 Workspace 未变时，允许人工显式派发新 child；
- partial diff、未知外部副作用、Trace 损坏和状态损坏进入人工处理；
- SQLite Graph checkpoint 丢失不影响从 Agent State、Checkpoint 和真实 Workspace 对账；
- `pause / resume-local / stop` 保留 Goal、Plan、Diff 和 Artifact，不执行自动回滚。

本地故障注入与状态回归为 49 项通过；架构增长、Ruff、compileall、仓库卫生、CI 分片完整性和
`git diff --check` 均通过。Python 3.14 环境仍出现 LangChain Core 的 Pydantic V1 兼容警告；
项目 CI 使用 Python 3.11/3.12，最终结论等待 PR CI。
