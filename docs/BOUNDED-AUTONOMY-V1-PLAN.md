# Vega 有界自主执行 V1 计划

> 状态：已批准，实施尚未开始。
>
> 计划日期：2026-08-31
>
> 规划基线：`main@69358ff` / `v0.3.1`
>
> 批准记录：2026-08-31 确认仓库策略可以预先授权低风险任务；Planner 与 Worker 默认复用
> 同一 Provider Thread，并保留安全回退；`v0.4.0` 先完成 Codex，真实验收后再接 Claude Code。
>
> 本计划已作为 `AUTONOMY-01`～`AUTONOMY-05` 追加到现有机器计划，不修改既有事项或历史证据。

## 一、目标

当前 ChangeRun 已经可以执行一份人工准备好的 Change Contract 和 Execution Plan：

```text
人工准备合同与计划
→ 持久 Worker
→ Git Candidate
→ Verification / Risk / 独立 Reviewer
→ Repair / Replan / Human / Finish
```

下一阶段补齐前半段：

```text
用户描述功能、Bug 现象或工程目标
→ Coding Agent 只读调查并提出计划
→ Vega 编译合同、范围、验证和风险
→ 人工批准，或按人工预先批准的策略自动放行低风险任务
→ 进入现有 ChangeRun
```

V1 的“自动”终点是 `ready_to_commit`。提交到用户分支、Push、创建或合并 PR、发布和部署仍由
人工决定。

## 二、核心决定

### 2.1 不增加新的通用 Agent Runtime

Codex、Claude Code 或其他 Coding Agent 继续拥有：

- 代码调查与实现；
- 工具调用和子代理；
- Provider 会话、历史和上下文压缩；
- 模型选择与推理配置。

Vega 继续拥有：

- Change Contract 与批准记录；
- Execution Plan、预算和路由；
- 隔离 Worktree 与 Git Candidate；
- Verification、Risk、Reviewer 和 Finish；
- Checkpoint、Task Card、人工控制与恢复。

Git 继续拥有代码状态。模型摘要、Worker Claim 和 Trace 不成为新的代码事实。

### 2.2 Planner 与 Worker 默认复用一条 Provider Thread

Planner 不作为新的长期角色。默认过程是：

1. 创建 Provider Thread；
2. 第一个 Turn 使用只读权限调查并生成计划提案；
3. Vega 校验提案；
4. 提案获批后，同一 Thread 在后续 Turn 获得当前 Work Item 的写权限；
5. Contract 发生实质变化、Thread 不可恢复或 Provider 不支持安全切换时，才创建新的 Worker
   Thread。

这样保留调查上下文和 Provider Cache，也避免维护 Planner 与 Worker 之间的额外交接协议。

实施前必须用真实 Codex App Server 验证同一 Thread 能否可靠地从只读 Turn 进入受控写入
Turn。若验证不通过，回退为“只读 Planner Thread + 由批准合同启动 Worker Thread”，不为
复用会话放松权限边界。

### 2.3 Reviewer 保持独立

Reviewer 使用独立只读 Thread，只接收：

- 当前批准合同与 Execution Plan；
- Candidate SHA、完整变更文件清单和相关 Diff；
- 项目规则、Verification 与 Risk 结果；
- 当前未解决 Finding。

Reviewer 不接收 Worker 的完整聊天、中间推理或未经核对的自述。现有
`approve / repair / replan / needs_human` 语义保持不变。

Codex 原生 `review/start` 只作为会话启动方式的候选替代。它必须先证明能够保持 Vega 的
结构化 Verdict、覆盖清单、风险披露和只读边界，才允许删除当前 Reviewer 启动实现。

### 2.4 LLM 提案，代码裁决

LLM 可以：

- 调查未知 Bug；
- 提出 Contract 与 Execution Plan；
- 修改代码；
- 找语义问题；
- 建议 Repair 或 Replan。

确定性代码决定：

- 提案是否完整；
- 路径和验证是否可接受；
- 是否命中高风险或未知副作用；
- 是否允许自动批准；
- 当前 Candidate 的门禁是否新鲜；
- Repair、Replan 和重试预算是否耗尽；
- 什么时候必须交还人工。

不增加“判断另一个模型是否可信”的额外 Agent。

## 三、目标使用流程

### 3.1 自然语言入口

新增与现有入口互斥的自然语言方式：

```text
vega start --repo . --text "修复导出按钮偶发无响应"
```

现有显式合同入口继续保留：

```text
vega start --repo . --contract change-contract.json \
  --execution-plan execution-plan.json
```

不增加单独的 `vega plan`、Planner Runtime 或第二套任务生命周期。

### 3.2 Planning 阶段

`start --text` 创建受管 Planning Workspace，记录：

- 用户原始要求；
- 起始 Git revision；
- 仓库规则与配置摘要；
- Provider Thread ID；
- Planning attempt 和超时；
- 当前只读状态。

Planner 在只读边界内检查代码、测试、配置和调用关系。输出必须匹配
`PlanningProposal`，至少包括：

```yaml
user_goal: 用户原始目标
source_revision: 调查所基于的 Git revision

observed_facts:
  - statement: 已确认事实
    refs:
      - src/module.py:40-72

hypotheses:
  - 尚未确认的根因

unresolved_questions:
  - 修改前必须回答的问题

contract_proposal:
  goal: ...
  acceptance: []
  invariants: []
  non_goals: []
  authorized_risk_reviews: []
  side_effect_policy: {}
  required_verification: []
  authority_envelope: {}

execution_plan:
  work_items: []
  implementation_strategy: []
  additional_checks: []
```

`Observed Facts` 必须有仓库相对路径、符号、测试或命令结果引用。引用只用于追查来源，不替代
当前 Git 和验证事实。

Planning 阶段不允许修改业务文件、创建 Candidate 或执行外部写入。Planning 被中断时：

- 原 Provider Thread 仍可恢复：继续原 Thread；
- Thread 不可恢复：从原始目标、起始 revision 和已保存提案重新调查；
- 尚未形成有效提案：重新执行只读调查，不把半份输出提升为合同。

### 3.3 Contract Compiler

Planner 输出后，由 Vega 编译和检查，不直接进入执行。

Compiler 至少完成：

1. 校验 Proposal、Change Contract 和 Execution Plan Schema；
2. 确认 `source_revision` 与 Planning Workspace 一致；
3. 校验候选路径是仓库相对路径，并位于允许范围内；
4. 区分事实、假设和未决问题；
5. 限制 Work Item 数量、依赖和执行预算；
6. 检查 `.vega.yaml`、AGENTS.md 和仓库风险规则；
7. 确认 Verification 来自仓库已有配置或人工明确批准；
8. 禁止把 LLM 自由生成的 Shell 命令直接当作自动执行命令；
9. 标记数据库、迁移、支付、权限、并发、数据删除、公共 API、部署和外部写入；
10. 生成可读的 Plan Card 与机器可读的未批准合同。

不存在 `.vega.yaml` 或明确验证命令时，Planner 可以提出建议，但任务不能获得自动批准资格。
人工可以补充项目配置、批准明确命令，或仅把任务保留为人工控制模式。

Compiler 失败时返回具体字段和原因，不启动 Worker。

### 3.4 批准

V1 支持两种批准来源。

#### Human

默认模式。主会话展示：

- 目标和验收条件；
- 已确认事实与仍属假设的内容；
- 修改范围与预计 Work Item；
- Verification；
- 风险和副作用；
- 未决问题。

用户批准、修改、要求补查或停止。批准后进入现有 `ready` 阶段。

#### Bounded Policy

自动批准必须由仓库内一份人工预先批准的策略显式启用，命令行也要选择该模式。两者缺一不可。

只有同时满足以下条件才能自动批准：

- Proposal 通过 Contract Compiler；
- 没有未决问题；
- 修改范围落在策略允许路径；
- Verification 全部来自已登记配置；
- 不涉及数据库 Schema、支付资金、权限、数据删除、公共 API、部署或外部写入；
- 没有未授权的高风险规则命中；
- 文件、Work Item、Repair 和 Replan 预算在策略上限内；
- Planning Workspace、规则和配置在批准前没有漂移。

自动批准记录策略 ID、策略 digest、Contract digest 和批准时间。策略变化使旧自动批准失效。

“模型认为风险很低”不能作为自动批准依据。

## 四、执行与审查

批准后不建设第二条执行链，直接复用当前 ChangeRun：

```text
Approved Contract
→ 当前 Work Item
→ 持久 Worker Thread
→ Git Candidate
→ Verification
→ Risk
→ 独立 Reviewer
→ next / repair / replan / human / finalize
```

### 4.1 Repair

普通 Finding 转成现有 Fix Packet，返回同一 Worker Thread。Candidate SHA 变化后，旧
Verification、Risk 和 Review 失效。

### 4.2 Replan

Reviewer 只能说明原假设或实现路线的问题。Planner/Worker Thread 生成新的 Execution Plan
revision。

- 只修改 Work Item、实现策略、候选文件或增加检查：按现有合同内规则自动应用；
- 修改验收、Non-goals、风险授权、允许范围、外部副作用或 Verification：回到批准阶段。

不增加 Replan 仲裁模型。

### 4.3 最终审查

最终报告继续由 Git、Verification、Risk 和结构化 Reviewer Artifact 确定性生成。LLM 不负责
把失败改写成更好看的结论。

报告第一屏至少显示：

- 完整 changed files；
- 主要功能变化；
- 高风险文件和人工检查点；
- 实际运行的 Verification；
- Reviewer Finding 与处理状态；
- Plan 是否发生变化及原因；
- 未证明事项；
- 当前是否 `ready_to_commit`。

## 五、会话、上下文与恢复

### 5.1 Provider 负责聊天历史

Vega 只保存 Provider Thread、Turn 和必要 Item 指针，不复制完整聊天，不实现自己的
Condenser 或 Transcript 数据库。

Provider 发生上下文压缩后，继续使用现有 Task Anchor。内容只来自：

- 当前 Contract 与 revision；
- 当前 Work Item；
- Accepted Checkpoint 和 Candidate SHA；
- 已完成与失败尝试；
- Verification、Risk 和 Reviewer 结果；
- 未解决 Finding；
- 下一步允许动作。

维持现有 32 KiB 软上限，不设置下限。放不下必要约束时停止并请求人工，不能静默删掉合同
内容。

### 5.2 进度可见性

主会话继续显示低频事件：

```text
开始只读调查
计划提案已生成
Contract Compiler 通过或拒绝
等待批准或策略批准
Worker Turn 启动
Candidate 已冻结
Verification / Risk / Reviewer 结果
Repair / Replan / Human / Finalize
```

不输出模型推理、完整命令参数、凭据或每个 Token。用户仍可查看 changed files、当前
Finding、失败验证和最近 Trace，并可 steer、pause、stop 或 takeover。

### 5.3 跨机器

跨机器恢复继续依赖 Git 与 Task Card：

- 原始用户目标；
- Planning Proposal 或 Approved Contract；
- Execution Plan revision；
- 任务分支与 Accepted Checkpoint SHA；
- 当前 Work Item；
- Gate 和 Reviewer 引用；
- 下一步；
- Provider Thread ID 作为可选提示。

Provider Thread 无法跨机器恢复时，新会话从这些材料重建 Task Brief。不能因为旧会话不可用
而重放未知外部副作用。

## 六、实施事项

以下事项已追加到现有 `vega-agent-evolution` 计划。一次只维护一个开发分支，
每项实现和 `completed` 事件在同一 PR 中进入主线；合并后删除分支，不再创建状态补丁 PR。

### AUTONOMY-01：自然语言 Planning Proposal

范围：

- `start --text`；
- 只读 Planning Workspace；
- `PlanningProposal`；
- Planning 状态、状态卡和中断恢复；
- 默认仍由人工批准。

验收：

- 模糊 Bug 可以在不修改业务文件的情况下形成带引用的 Proposal；
- Proposal 不完整、引用失效或 Provider 中断时 fail-closed；
- 显式 Contract/Plan 入口行为不变；
- 同一 Thread 从只读调查进入写入的能力经过真实 App Server 验证，失败时采用独立 Worker
  Thread 回退。

### AUTONOMY-02：Contract Compiler

范围：

- 编译 Proposal 为未批准 Change Contract 与 Execution Plan；
- 路径、规则、验证、风险、预算和漂移检查；
- 可读 Plan Card；
- 不接受 LLM 自由生成命令直接执行。

验收：

- 事实、假设和未决问题不会混为一类；
- 越界路径、未知 Verification、高风险缺失和 source revision 漂移均被拒绝；
- 编译通过的结果可以进入现有人工批准与 ChangeRun；
- 不新增第二套成功状态或证据系统。

### AUTONOMY-03：有界自动批准

范围：

- 人工预先批准的仓库策略；
- `human` 与 `bounded` 两种批准来源；
- 策略 digest、新鲜度和可解释拒绝原因。

验收：

- 低风险、范围和验证明确的任务可以自动进入现有 ChangeRun；
- 高风险、配置缺失、未决问题或副作用未知时必须请求人工；
- 策略或 Contract 变化会使自动批准失效；
- 自动批准不改变最终人工 Git 交付边界。

### AUTONOMY-04：Provider 会话精简

范围：

- 把 Codex 接入收敛为 Thread、Turn、Event、Steer、Interrupt、Status 和 Review 能力映射；
- 对 Codex 原生 `review/start` 做 Shadow 对照；
- 删除有真实等价证据的重复协议处理，不重写 Provider 会话或压缩。

验收：

- 当前持久 Worker、独立 Reviewer、状态卡和恢复语义不变；
- 原生 Review 只有在结构化 Verdict、覆盖、风险和只读边界等价时才替换当前实现；
- 未识别的新事件不会关闭整个 Observation 链；
- Provider 过载、超时和中断保持有界处理。

### AUTONOMY-05：真实验收与发布判断

至少使用以下三类任务：

1. 范围清楚的低风险小修改；
2. 只有 Bug 现象、需要调查和一次 Repair 的任务；
3. 数据库、并发、权限或外部副作用导致必须人工确认的任务。

验收：

- Human 模式走通“调查、批准、执行、Repair、最终报告”；
- Bounded 模式的低风险任务不需要初始人工批准；
- 普通 Repair 不需要人工转贴 Reviewer 内容；
- Provider 压缩、中断和换目录恢复后仍绑定正确 Contract、Work Item 和 Candidate；
- 高风险案例不会被自动批准；
- 最终报告足以让人工定位重点 Diff、风险和未证明事项；
- 根据真实运行数据决定发布 `v0.4.0`，而不是按代码完成数量决定。

## 七、后续 Provider

`v0.4.0` 先用 Codex 完成上述链路。通过真实验收后，再使用同一 Provider Session 合同接入
Claude Code：

- 启动或恢复会话；
- 发送 Turn；
- 流式事件；
- Steer、Interrupt 和用户响应；
- 独立只读 Reviewer；
- Provider 自己的压缩与恢复。

Claude Adapter 不得复制 ChangeRun、Candidate、Gate 或 Finish。若某项 Provider 能力不存在，
明确降级为 fresh session 或人工接管，不伪造等价能力。

Pi、Goose 或其他 Coding Agent 只在出现真实使用需求后接入，不在 V1 同时开发。

## 八、测试和代码增长限制

只增加保护公共合同和高风险边界的测试：

- Planning Proposal Schema 与只读边界；
- Contract Compiler 的允许和拒绝路径；
- 自动批准策略的新鲜度与高风险拒绝；
- Planning 中断、恢复和 Provider 回退；
- 一条真实 Codex 端到端任务。

不为以下内容增加测试：

- 私有格式化帮助函数；
- 上游 Provider 已经覆盖的内部会话实现；
- 重复 Snapshot；
- 仅验证同一字段被多次转发的测试；
- 已删除或不再公开的入口。

新增代码必须删除等价旧路径，或说明为什么暂时需要并存。任何阶段如果只是把上游 Provider
能力重新包装一遍，应停止并删除该实现。

## 九、停止条件

出现以下任一情况，当前事项停止，不继续扩大范围：

- Planner 无法稳定区分事实和假设；
- Planner 需要写权限才能完成调查；
- Contract Compiler 只能依赖另一个模型判断是否安全；
- 自动批准需要放松高风险或 Verification 规则；
- 同一 Thread 权限切换无法证明可靠；
- 原生 Review 无法提供 Vega 要求的结构化覆盖；
- 为支持新入口需要建设第二套状态、Transcript、Memory 或成功语义；
- 真实任务没有减少人工转贴和重复操作。

这时保留现有人工 Contract 入口和 ChangeRun，不为了完成计划继续堆功能。

## 十、不在本计划内

- 多 Worker 并行写入；
- 多 Reviewer 投票或辩论；
- LangGraph、通用工作流图或长期服务；
- 向量 Memory、自动长期 Memory；
- 自研 Transcript、上下文压缩或 Prompt Cache；
- 默认给 Reviewer 增加 Repo Map；
- Web UI、TUI、Issue 队列和 daemon；
- 自动 Push、PR、Merge、Release、部署或回滚；
- 为证明证据而增加第二套 Evidence Bundle。
