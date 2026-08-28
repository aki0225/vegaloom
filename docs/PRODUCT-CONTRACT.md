# 产品契约

## 定位

Vega 是软件工程 Agent 的控制层。它不重新实现 Coding Agent，而是把一次代码变更约束在
人工批准的合同内，并把实现结果交给 Git、项目验证、风险规则和独立 Reviewer。

```text
只读调查
  -> Change Contract / Execution Plan
  -> 人工批准
  -> 隔离 Worktree
  -> 持久 Worker
  -> Git Candidate
  -> Verification / Risk / Reviewer
  -> next / repair / replan / human / finalize
  -> 最终人工 PR 判断
```

v0.3.0 只有这一条公开 ChangeRun 流程。旧 `do`、`loop`、`agent`、`goal` 和 inspection
命令不再作为公共入口；ChangeRun 仍复用其中经过验证的 Core Runtime。

## 权威关系

| 事实 | 权威来源 |
|---|---|
| 用户要解决什么 | 用户要求、仓库规则、Change Contract |
| 已经确认和仍在猜测什么 | Execution Plan |
| 当前代码内容 | Git Candidate SHA 与实际 Worktree |
| Worker 做了什么 | 机器 Observation；Worker Claim 仅作待验证输入 |
| 测试是否通过 | 当前 Candidate 上实际执行的 Verification Artifact |
| 是否命中高风险 | `.vega.yaml`、批准合同和 Risk Gate |
| Reviewer 看过什么 | Reviewer verdict、`reviewed_files` 与 finding |
| 当前允许做什么 | Agent State 与确定性路由 |
| 跨机器需要恢复什么 | 任务分支、Task Card、Contract、Plan 和 Candidate |

Trace、状态卡、Task Brief 和最终报告都是这些事实的投影，不能反向创造成功证据。

## Change Contract

Change Contract 冻结人工授权：

- 目标、验收条件和不变量；
- 明确不做的内容；
- 允许和禁止的仓库范围；
- 必跑验证；
- 已授权的高风险审查领域；
- 数据库、公共 API、依赖、部署、支付、权限、数据删除和外部写入策略；
- Repair、Replan、Review 与验证重试预算。

合同字段变化会使旧批准失效。实际 Git Diff 即使没有出现在新计划中，只要越出授权范围或命中
未授权风险，也必须停止。

## Execution Plan

Execution Plan 记录 Agent 可以调整的实现安排：

- 已观察事实与根因假设；
- 有限、粗粒度的 Work Item；
- 候选文件与依赖顺序；
- 实现策略、附加检查和未决问题。

合同不变时，Agent 可以拆分 Work Item、调整顺序、换实现方案或增加测试。Reviewer 发现原假设
错误时返回 `replan`，由新的计划 revision 承接；Reviewer 本身不批准自己提出的新合同。

## Worker 与 Reviewer

默认 Provider 是 Codex App Server：

- 一个 ChangeRun 复用一个 Worker Thread；
- Contract revision 实质变化后，旧 Worker Thread 不再自动复用；
- 每个 Work Item 有独立只读 Reviewer Thread；
- 同一 Work Item 的 Repair 复查可以复用 Reviewer Thread；
- Worker 和 Reviewer 使用不同 Thread，Reviewer 不接收 Worker 完整聊天或中间推理；
- 多 Work Item、Replan、高风险或 Repair 后的最终候选按条件增加一次累计集成审查。

Worker 沿用用户选择的 Codex profile、model 和 reasoning effort。Reviewer 同样尊重项目配置，
但固定只读、关闭审批，并禁用个人 hooks、memories、plugins 和 MCP 配置。

Provider Session 只保存本机会话协调信息：Thread ID、owner、生命周期、Turn、压缩次数、Token
用量、待发送 Steer 和待响应请求。它不参与 Verification、Risk、Reviewer 或 Finish 裁决。

## Candidate 与门禁

自主执行发生在 Vega 管理的本地任务分支和隔离 Worktree：

1. Worker 修改文件，但不能创建提交或切换分支；
2. Vega 对账 Workspace、允许范围和 Git 控制状态；
3. Vega 创建本地 Candidate Commit；
4. Verification、Risk 和 Reviewer 绑定 Candidate SHA；
5. SHA 或受控项目策略变化后，旧门禁结果失效；
6. 通过的 Candidate 成为 Accepted Checkpoint。

验证失败不能被 Reviewer `approve` 覆盖。缺少验证、证据损坏、Reviewer 覆盖不完整、风险披露
不足或 Workspace 漂移都不能进入完成状态。

## 路由

LLM 只返回结构化结果。代码选择下一动作：

| 动作 | 条件 |
|---|---|
| `next` | 当前 Work Item 通过，进入下一项 |
| `repair` | 问题仍在合同内，可生成 Fix Packet |
| `replan` | 当前假设或执行安排不成立 |
| `human` | 合同、风险、现场、副作用或预算需要人工决定 |
| `finalize` | 所有 Work Item 和必需门禁通过 |

普通 Repair 返回同一个 Worker Thread，不需要人工转贴 Reviewer finding。预算耗尽、同一问题反复
出现、Worker 没有形成有效 Diff、未知外部副作用或审查未完成时进入 `needs_human`。

## Steer、响应和接管

主会话可以：

- 查看状态卡和低频安全事件；
- 给当前 Worker 或 Reviewer 排队发送 Steer；
- 响应 App Server 的审批或用户输入请求；
- 暂停或停止自动调度；
- 把 Provider Thread 交给人工原生会话。

Steer 不能修改冻结合同。敏感输入不得写入 Vega Artifact；需要凭据、验证码或其他私密交互时，
应接管原生会话。只有空闲 Session、没有 active Writer binding 且 Workspace 未变化时才允许
直接 `reclaim`。活动 attempt 被接管时会先中断执行；人工处理后必须走 Recovery 或 Handoff，
不能把旧 attempt 直接接回自动循环。

## 上下文与压缩

Task Brief 只组合当前 Contract、Plan、Work Item、Checkpoint、验证、风险和 Reviewer 结果。
完整日志和源码通过 Artifact 路径按需读取，不复制完整聊天或内部推理。

Codex 自己负责会话压缩。Vega 在检测到压缩后，于下一 Turn 追加一个不超过 32 KiB 的
Task Anchor，帮助会话重新定位当前 run、revision、Work Item 和 Accepted Checkpoint。该软上限
没有下限；关键约束放不下时必须停止并请求人工，不能静默省略。

## 完成语义

`completed / ready_to_commit` 同时要求：

- 所有 Work Item 已完成；
- 当前 Candidate 与 Workspace 一致；
- 至少一条受信验证命令实际通过，且最新验证没有失败；
- Scope、Risk、Artifact integrity 和 Evidence freshness 通过；
- 独立 Reviewer 完成规定覆盖；
- 需要的最终集成 Reviewer 已通过；
- 外部副作用为 `none`；
- 当前 Contract approval 仍有效。

最终报告从现有结构化 Artifact 和 Git 事实确定性生成，不调用额外总结模型。报告中的 Reviewer
优先级用于导航，完整变更事实仍以 Git 文件清单为准。

`needs_human` 是正常终态：它说明 Vega 已到达授权或证据边界，不等于工具崩溃。

## 跨会话和跨机器

本机恢复优先复用 Provider Thread。Thread 不可用时，Vega 从当前 Git、Agent State 和最近
Checkpoint 重新建立会话。

换机器前运行 Handoff，人工把 WIP 和 Task Card 提交到任务分支并 push。新机器只需要拉取该
分支；Task Card 保存目标、批准边界、当前 Work Item、Candidate 与下一步，不携带旧聊天、
凭据、本机 Trace 或 Provider Thread ID。

旧 Candidate 和门禁只能作为历史。恢复后必须重新核对当前 Workspace，并按需要重新运行验证
和审查。

## 公共 CLI

```text
vega capabilities
vega config check
vega adapters init codex

vega start
vega approve
vega run
vega status
vega watch
vega latest

vega steer
vega respond
vega revise
vega retry
vega pause
vega stop
vega recover
vega adjudicate
vega takeover
vega reclaim
vega handoff
vega resume
```

内部 Runtime、Artifact helper 和 `vega.experimental.*` 不是稳定 Python SDK。稳定程序化导出
只有 `vega.__version__`。

## 行为边界

- Reviewer 会话隔离不是容器或操作系统级安全沙箱。
- Vega 不扫描或终止不属于当前 run 的 Codex、Node 或 Shell 进程。
- 自动 Git 操作只限于受管 Worktree 中的本地 Candidate/Checkpoint Commit。用户分支、
  push、merge、rebase、release、部署、回滚、删除文件和长期 Memory 仍由人工控制。
- 数据库迁移、支付、权限、数据删除、部署和外部写入必须显式声明；未知副作用不能自动重放。
- 没有真实验证命令的项目不能获得自动完成资格。

## 增长约束

新能力必须明确改善人工操作、恢复、缺陷发现、上下文成本或交付理解中的至少一项，并用真实任务
验证。只增加命令、状态、Artifact 或架构名词不算进展。
