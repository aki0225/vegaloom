# Supervisor Agent V1 状态权威与最小合同 ADR

> 状态：`accepted / Gate 2A 已合并 / Gate 2B gate-exit-pass`
>
> 日期：2026-08-13
>
> 更新：2026-08-14
>
> Gate 0～2A 实施分支：`experiment/supervisor-agent-v1`（PR `#57` 合并后归档）

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
- 人工 `pause/stop` 必须继承最近 Checkpoint 的外部副作用状态，不能把 `unknown` 降级为
  `none` 或新的 `safe` Handoff；
- 人工副作用裁决只能在无 active Writer、Workspace 未漂移且证据位于当前 run 内时追加新
  Checkpoint；旧 `unknown` Checkpoint 和 Trace 不得改写；
- 只有人工把副作用明确为 `none` 才能进入 `stopped / safe`；明确为 `known` 时仍保持
  `needs_human / blocked`；
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
- 既有默认命令行为和成功语义没有变化；顶层 CLI 仅新增 opt-in `agent` 子命令。

## 9. Gate 1 与 Gate 2A 实施证据

Gate 1 已实现 Fake Worker 可见控制循环、Plan 批准、Task Brief、状态卡、LangGraph 条件路由和
`next / repair / replan / human / finalize` 决策。Graph 只能进入 `finalizing`，不能写入
`ready_to_commit`。

Gate 2A 已补充：

- run mutation 锁，确保并发 dispatch 只有一个 Writer 能取得绑定；
- dispatch 提交时原子持久化唯一 child/operation binding，并保守标记 operation 可能已经开始；
  该标记是不可自动重试安全闩，不是实际进程启动证明；
- dispatch 在发布 `acting` State 前写入 run-local operation identity marker；同一 run 的
  `operation_id` 不得复用，避免旧 execution 终态核销新 Writer；
- 当前正常流程没有可持久依赖的 `worker_reserved` 中间态或第二次 `confirm_started`；
- 旧版 `operation_started=false` 只表示未取得启动确认，不能证明 operation 未启动；
  升级后恢复保留原 Writer binding 并交由人工；
- 旧 binding 尚未被受信 execution、进程、Trace、Workspace 与外部副作用证据可靠核销时，
  保留 child/operation binding 并禁止第二 Writer；
- dispatch 后缺少 execution 证据时 fail-closed，不把“尚未看到进程记录”推断为
  operation 未开始；
- 外部 Observation 只能作为 Claim，受信 Observation 必须绑定当前执行身份；重复
  Observation ID 不得覆盖历史证据，Recovery 机器对账也使用 write-once Artifact；
- Recovery 机器 Observation 引用对应 operation marker，并在存在时引用受信
  `execution.json`，避免把机器摘要当作无来源事实；
- Plan revision 写入前先撤销旧批准和 dispatch 权限，防止崩溃窗口继续使用 stale approval；
- 只有当前 Checkpoint 与 Task Brief 成功落盘后，才发布 `ready` 或下一轮可 dispatch State；
- 受信 Observation 推进 Plan 时，同样先完成 Checkpoint 与下一轮 Task Brief，再发布新 Plan；
  State 保持最后的调度安全闩；
- 中间 Work Item 的 Verification、Risk 或 Reviewer 证据缺失或过期时转人工，不允许自动进入
  下一 Writer；明确失败或阻断仍按既有 `repair / replan / human` 规则处理；
- 跨机器 Task Card 恢复在 Checkpoint、Task Brief、Trace 和状态卡完成后，最后发布 State；
  失败重试不会遗留另一个可 dispatch run；
- dispatch 前校验 State、批准 Plan、safe Checkpoint 与 Task Brief manifest 的 revision、
  Work Item 和 Workspace binding；
- partial diff、未知外部副作用、Trace 损坏和状态损坏进入人工处理；
- SQLite Graph checkpoint 丢失不影响从 Agent State、Checkpoint 和真实 Workspace 对账；
- `pause / resume-local / stop` 保留 Goal、Plan、Diff、Artifact 和最近 Checkpoint 的外部
  副作用状态，不执行自动回滚。
- `adjudicate-side-effects` 复用 Recovery Request，要求 actor、reason 和 run-local
  evidence refs；它只追加裁决 Artifact 与 Checkpoint，不自动判断副作用或重放 Worker。

本地故障注入与状态回归为 62 项通过；审阅修复代码 HEAD `4180e7e` 已通过 workflow
`31718078414` 的 9 项 CI，最终文档 HEAD `8ca75f2` 已通过 workflow `31718680069` 的 9 项 CI，
并以 `6a5c927` 合并到 `main`。Gate 2A 退出条件已经满足；这些证据不代表 Gate 2B 已实现或
通过。Gate 2B 后续已在独立实验分支完成机械合同、两个冻结真实案例、最终 PR CI 与合并前
审阅，当前状态为 `gate-exit-pass`。状态权威顺序和本 ADR 的 fail-closed 决定没有改变。
Gate 3A 已完成；Gate 3B 已获批准。固定控制器、未知副作用继承和人工副作用裁决门禁均
已通过 PR CI；控制器已重新冻结，机器 A 尚未启动。
