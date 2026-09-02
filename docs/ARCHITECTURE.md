# 架构

> 本文描述 v0.4.0 架构，以及主线中已实现的只读 Planning Proposal、Contract Compiler
> 和有界自动批准。演进计划见
> [`BOUNDED-AUTONOMY-V1-PLAN.md`](BOUNDED-AUTONOMY-V1-PLAN.md)；当前事项见
> [`CURRENT.md`](CURRENT.md)。

## 总览

Vega v0.4.0 使用一条 ChangeRun：

```text
Host Session
  ├─ 自然语言目标 -> 只读 Planning Proposal -> Contract Compiler
  └─ Change Contract + Execution Plan
          │
          ▼
  Supervisor State Machine
          │
          ├─ Codex App Server Worker Thread
          ├─ Git Worktree / Candidate Commit
          ├─ Core Verification / Risk / Reviewer / Finish
          ├─ Repair / Replan / Human
          └─ Agent Final Report
```

宿主会话处理需求理解、计划展示和人工决定。Coding Agent 负责调查与实现。Vega 拥有状态、
单 Writer、Git Candidate、门禁路由和恢复。

## 组件

### CLI

`cli_entrypoint.py` 组合两部分：

- `cli.py`：`status`、`watch`、`latest`、`config` 和 Adapter 初始化；
- `agent_cli.py`：ChangeRun 的创建、批准、执行、交互、恢复和交接。

旧 Core CLI 不再注册。Core Runtime 仍被 ChangeRun 内部调用。

### ChangeRun

`agent_runtime.py` 和 `agent_runtime_logic.py` 维护确定性状态：

```text
planning
  -> awaiting_approval
  -> ready
  -> acting
  -> observing
  -> ready | needs_human | finalizing
  -> completed
```

状态机只根据当前 Agent State、Contract、Plan 和机器 Observation 路由。Provider 输出不能直接
写成功终态。

### Planning Proposal

`start --text` 在同一条 ChangeRun 中建立只读 Planning 阶段。Planner 绑定固定 Git revision，
输出事实引用、假设、未决问题、建议范围和验证建议；Workspace 发生变化、引用失效或 Provider
终态不可信时停止。Proposal 只是 Contract Compiler 的输入，不拥有批准或执行权限。

### Contract Compiler

`agent_contract_compiler.py` 负责纯确定性编译，
`agent_contract_compilation_runtime.py` 负责把结果发布回同一条 ChangeRun。Compiler：

- 绑定 Planning Request、上下文摘要、Proposal 与 source revision；
- 只接受固定 revision 中 `.vega.yaml` 已登记的验证命令；
- 校验候选路径、required review、文件数量和每轮验证命令预算；
- 保留事实、假设和未决问题的分类；
- 生成未批准的 Change Contract、Execution Plan 和 `plan-card.md`。

编译通过后状态进入 `awaiting_approval`。拒绝结果进入 `needs_human`，不启动 Worker，也不创建
第二套状态机或成功语义。AGENTS.md 等自由文本规则已绑定在 Planning 上下文中；机器强制的
路径、验证和风险仍以 `.vega.yaml` 为准。

### 批准策略

`approval_policy_config.py` 定义仓库内的 bounded 策略，
`agent_approval_policy.py` 负责纯判断，`agent_approval_runtime.py` 负责把结果接回现有
ChangeRun。

默认仍由 `vega approve` 记录人工批准。`vega run --approval bounded` 只有在仓库策略已启用，
且范围、Verification、预算、副作用和风险都满足策略时，才写入带策略摘要的批准记录。拒绝时
状态保持 `awaiting_approval`，Trace 和状态卡给出原因。

批准后的每次可执行恢复都会重新检查策略和 Contract 绑定。失效的批准不能继续启动 Worker。

### Change Contract 与 Execution Plan

`agent_change_contract.py` 定义两个独立模型：

- Change Contract：人工批准的业务、范围、风险、验证和预算；
- Execution Plan：Agent 可在合同内调整的实现安排。

`agent_change_revision.py` 比较 revision。Contract 变化进入 `awaiting_approval`；纯执行计划变化
在真实 Diff 和风险检查通过后可以自动采用。

### Worktree 与 Candidate

`agent_git_worktree.py` 为 ChangeRun 创建本地任务分支和隔离 Worktree。
`agent_git_candidate.py` 在 Worker 退出并完成范围检查后创建 Candidate Commit。

```text
Accepted Checkpoint
  -> Worker WIP
  -> Workspace / Scope 对账
  -> Candidate SHA
  -> Core 门禁
  -> Accepted Checkpoint 或 Fix Packet
```

Git SHA 是代码快照权威。Candidate 变化会使之前绑定的 Verification 和 Reviewer 结果失效。

### Provider Session

`provider_session.py` 维护 `provider-sessions.json`：

- Worker 与 Reviewer 的 Thread ID；
- 会话 owner 和生命周期；
- 当前 Work Item 与 revision；
- Turn、压缩和 Token 统计；
- 待发送 Steer；
- 待响应 App Server 请求。

文件使用摘要 envelope 和 run mutation lock。待发送或待响应项不会被历史裁剪。
这个 Artifact 只服务会话协调和状态展示，不参与成功判断。

### Codex App Server

`codex_app_server_runner.py` 把 Provider Runner 接到现有 Execution Control。
`codex_app_server.py` 是短生命周期 helper，`codex_app_server_rpc.py` 只处理有界 JSON-RPC
收发和通知白名单：

1. 启动 `codex app-server --listen stdio://`；
2. initialize；
3. `thread/start` 或 `thread/resume`；
4. `turn/start`；
5. 消费安全事件、审批请求、Token 和压缩通知；
6. 输出最终结构化 `agentMessage`；
7. 退出 helper，保留 Provider Thread。

外部进程继续使用既有 lease、heartbeat、stop request、timeout 和进程树终止逻辑。App Server
stderr 由 Execution Control 脱敏后写入诊断 Artifact；JSON-RPC stdout 单独解析。

Provider 映射保持窄而明确：

| 能力 | Vega 使用方式 |
|---|---|
| Thread | `thread/start`、`thread/resume` |
| Turn | `turn/start` |
| Event | 只接收生命周期、最终 item、Token、压缩和错误通知 |
| Steer | `turn/steer` |
| Interrupt | 复用 Vega owned-process stop 与进程树确认 |
| Status | 投影 `provider-sessions.json` |
| Review | 独立只读 Thread，再用 `turn/start` 生成严格 `ReviewVerdict` |

初始化时关闭正文和 Diff 增量通知；代码事实仍从 Worker 退出后的 Git Candidate 读取。未知
notification 在 JSON-RPC 边界忽略，不会关闭 Observation 链。App Server 返回过载错误时只做
三次有限退避；关键事件积压超过上限、请求超时或进程树终止未确认时直接失败。

Codex CLI `0.149.1` 的真实 Shadow 表明，原生 `review/start` 能发现代码问题，但请求不能绑定
Vega 的 Structured Output，响应也没有覆盖清单和风险披露。当前不替换 Reviewer，记录见
[`examples/evidence/autonomy-04-codex-review-shadow.md`](../examples/evidence/autonomy-04-codex-review-shadow.md)。

默认路径不可用时明确失败。`--fresh-session` 才显式改用一次性的 `codex exec`。

### Worker

`agent_codex_adapter.py` 负责：

- 准备当前 Work Item；
- 选择持久 App Server 或 fresh runner；
- 发布单 Writer binding；
- 启动 Worker；
- 把 Worker 终态交给 Provider 无关 Candidate Pipeline。

Worker 输出必须满足 `WorkerClaim` schema，但 Claim 不拥有完成资格。

### Candidate Pipeline

`agent_candidate_pipeline.py` 统一处理 Worker 之后的流程：

1. 确认 Worker execution 终态；
2. 捕获 Workspace；
3. 校验 Plan scope；
4. 冻结 Candidate；
5. 初始化内部 Core child；
6. 运行 Verification、Risk、Reviewer 和 Finish；
7. 生成 Machine Observation；
8. 让 Supervisor 路由。

Codex Adapter 不拥有 Workspace、Candidate、门禁或 Finish 语义。

### Core 门禁

内部 Core 保留以下职责：

- Workspace 与 Git 控制状态；
- allowed/forbidden path；
- 验证命令预检和实际执行；
- `.vega.yaml` 高风险路径；
- Reviewer 输入编译、Diff 覆盖和结构化 verdict；
- Artifact integrity、Evidence freshness 和 Finish。

Core child 的 `finish-summary.json` 必须绑定当前 Candidate，且满足可信完成条件，父 ChangeRun
才允许进入 `finalizing`。

### Reviewer

每个 Work Item 的 Reviewer 使用独立只读 Thread。输入包含：

- 已批准 Contract；
- 当前 Execution Plan 和 Work Item；
- 项目规则；
- Candidate Diff；
- Verification 与 Risk；
- 必需的关联文件。

输入不包含 Worker 完整对话或中间推理。Reviewer 必须声明 `reviewed_files`，缺少当前批次文件
时降为 `needs_human`。

以下任一条件触发最终集成审查：

- 多个 Work Item；
- Contract 或 Plan revision 大于 1；
- 合同授权了风险领域；
- 声明了高影响副作用；
- 最终 Candidate 经历 Repair。

累计 Diff 最多拆成 8 个有界批次。所有批次完成且必需风险披露齐全后才能 `approve`。

### Repair 与 Replan

Reviewer 或 Verification 的合同内问题生成 Fix Packet。Fix Packet 只包含 finding、门禁结果、
来源 Artifact 和下一步，不携带 Reviewer 完整会话。

Repair 复用 Worker Thread，产生新的 Candidate。Replan 由新的 Execution Plan revision 承接；
触及 Contract 字段时等待人工批准。

### 可见性

`agent_status_projection.py` 从当前 State、Workspace、Checkpoint、Core Artifact 和 Provider
Session 生成统一投影。文本状态卡和 JSON 状态共用该投影。

`progress.jsonl` 只记录低频安全事件，例如：

```text
thread_ready
turn_started
command_started
file_changed
context_compacted
waiting_user
verification
reviewer
checkpoint
```

它不记录模型推理、完整正文、命令参数、文件正文或凭据。

### 最终报告

`agent_visibility.py` 从以下事实生成 `agent-final-report.json/md`：

- 基线与 Accepted Candidate 的 Git 文件清单和统计；
- Work Item 状态；
- 最终 Worker Claim；
- Machine Observation；
- Core Verification、Risk 和 Reviewer；
- 条件式最终集成审查；
- Reviewer 建议优先查看的文件；
- 证据上限和人工下一步。

生成过程不调用模型。

## 上下文

每个 Turn 读取当前 Task Brief。Task Brief 只保留当前目标、合同、计划、Work Item、最近失败、
验证、风险、Reviewer 和 Artifact 引用。

Codex 发出压缩事件后，下一 Turn 追加 32 KiB 软上限的 Task Anchor。固定项目规则仍由 Codex
自己的会话与仓库读取，动态 Anchor 放在当前输入末尾，避免反复复制完整历史。

## 中断和恢复

Worker 失去可信终态时统一处理：

```text
检查旧进程
  -> 对账 Workspace
  -> 核对外部副作用
  -> 写 Checkpoint
  -> 继续、Handoff 或 needs_human
```

旧进程仍活着时禁止启动第二个 Writer。能证明操作尚未开始且 Workspace 未变时可以重新建立
会话；存在 partial Diff 或未知外部副作用时交给人工。

Handoff 把任务语义写入 Git Task Card。本机 Provider Session、Trace、凭据和聊天不进 Git。
恢复后的旧验证与审查只是历史，必须按当前 Candidate 重新判断。

## Artifact

```text
runs/<run_id>/
├── agent-state.json
├── agent-plan.json
├── planning-request.json
├── planning-proposal.json
├── planning-proposal.md
├── change-contract.json
├── execution-plan.json
├── plan-card.md
├── provider-sessions.json
├── trace.jsonl
├── progress.jsonl
├── task-brief.md
├── checkpoints/
├── observations/
├── decisions/
├── fixes/
├── integration-reviews/
├── agent-final-report.json
└── agent-final-report.md
```

Core child 保留自己的 Verification、Risk、Reviewer 和 Finish Artifact。父 run 只引用并校验，
不复制第二套门禁事实。

## 模块边界

- `agent_*`：ChangeRun、合同、状态、Worktree、Candidate、恢复和展示；
- `codex_app_server*`、`provider_session`：Provider 会话；
- `loop_*`、`verification*`、`risk_*`、`review_*`、`finish_*`：内部 Core；
- `execution_*`：进程、超时、停止与输出；
- `experimental/`：不参与当前公共成功语义的历史或实验实现。

Core 不静态依赖 `vega.experimental`。新增模块受 500 行和增量 C901 门禁约束。

## 安全边界

- Reviewer 只读和会话隔离不等于系统沙箱；
- Diff、日志、代码注释和 Provider 输出都按不可信输入处理；
- 敏感交互转入原生会话，不写 Artifact；
- 高风险修改必须按 `.vega.yaml` 和 Contract 披露；
- 证据缺失、损坏、过期或互相冲突时 fail-closed。
