# Vega 源码规则

## 产品主线

- Vega 只有一条公开 ChangeRun：Change Contract、持久 Worker、Git Candidate、Verification、
  Risk、独立 Reviewer 和 Final Report。Core Harness 继续拥有可信完成语义。
- `start / approve / run / status` 是核心生命周期入口；`watch / steer / respond / revise /
  retry / pause / stop / recover / adjudicate / takeover / reclaim / handoff / resume` 负责观察、
  人工交互和恢复。它们都操作同一条 ChangeRun，不拥有平行成功语义。
- 旧 `do / loop / agent / goal / inspection` 命令不再注册；仍被 ChangeRun 调用的 Core
  Runtime 是内部实现。
- Goal、Memory、Assurance 与 Inspection 属于实验、历史或兼容实现，不得形成第二套公共成功语义。
- 保持本地文件优先和 fail-closed。自主 ChangeRun 只允许在 Vega 管理的隔离 Worktree 中由
  控制器创建本地 Candidate/Checkpoint Commit；用户分支、push、merge、rebase、release 和
  长期 Memory 继续由人工控制。

## 依赖与事实权威

- 依赖方向固定为：CLI 组合根 -> Core；显式实验命令 -> `experimental` -> Core。
  Core 不得静态导入 `vega.experimental`，实验命令只能在调用时延迟导入。
- 用户指令、仓库规则、`<target-repo>/.vega.yaml` 和当前已批准 Plan 拥有任务意图；Git、
  真实 Workspace、活动进程和新鲜 Artifact 拥有运行事实；Worker Claim 只能作为待验证输入。
- Bounded Change Loop 中，Approved Contract 拥有人工授权边界，Execution Plan 只拥有可调整
  的实现安排。Git Candidate SHA 拥有代码快照事实；Task Card 只保存跨会话恢复所需的目标、
  当前步骤和下一动作。
- Agent State 只拥有本机控制状态。Diff、Verification、Risk、Reviewer 和 Finish 事实继续由
  Core Artifact 拥有；Trace 是追加式审计线索，Provider Session 只拥有会话协调状态。
- 文本状态和 JSON 状态必须使用同一实时证据投影。证据缺失、损坏、过期或 Workspace 漂移时，
  展示层降级为 `needs_human`；执行层仍必须严格拒绝不可信证据。

## 权威模型与调用路径

新功能先沿下面这条路径定位，不要从名称相近的旧模型或私有帮助函数另开入口：

```text
CLI
→ SupervisorAgentRuntime.start_change
→ ChangeContract / ExecutionPlan
→ Provider Adapter
→ Candidate Pipeline
→ Core Verification / Risk / Reviewer / Finish
→ Supervisor Decision
```

- `ChangeContract` 和 `ExecutionPlan` 是新 ChangeRun 的权威任务模型。`AgentPlan` 只保留为
  Core 投影和旧 Task Card 恢复格式；新产品字段不得先加到 `AgentPlan` 或
  `LoopAutomationState`。
- `SupervisorAgentRuntime.start()` 是 legacy 恢复兼容入口。新的 CLI、Planner、Adapter 或测试
  必须显式调用 `start_change()`，不能依赖 `AgentState.run_kind` 的默认值猜测运行类型。
- `_start_locked`、`_bind_locked` 等私有方法不是扩展点。现有调用允许在对应职责被修改时收敛为
  窄公共接口，但不要为了目录整齐一次性重写 Runtime。
- 已批准的 `AUTONOMY-*` 事项只补 Planning Proposal、Contract Compiler 和批准来源，产物仍进入
  上述 ChangeRun；不得创建第二套 Planner Runtime、状态机或 Finish。

## Supervisor 模块地图

当前 `agent_*` 文件保持扁平结构，按以下职责查找，不为整理文件数量做批量搬迁：

- 入口与编排：`agent_cli`、`agent_start_cli`、`agent_runtime*`、`agent_routing`、
  `agent_worker`、`agent_finalization`。
- 自然语言规划：`agent_planning*` 只生成未批准 Proposal；不得直接写入 Approved Contract、
  启动 Worker 或创造验证事实。
- 确定性合同编译：`agent_contract_compiler` 只做 Proposal 到现有合同模型的纯投影和规则检查；
  `agent_contract_compilation_runtime` 把未批准结果发布回同一条 ChangeRun，不启动 Worker。
- 有界批准：`approval_policy_config` 定义仓库策略，`agent_approval_policy` 只做资格与新鲜度
  判断，`agent_approval_runtime` 把批准或拒绝接回现有 ChangeRun；不得另建执行状态或成功语义。
- 合同与持久化：`agent_contract`、`agent_change_contract`、`agent_persistence`、`agent_run`、
  `agent_mutation`、`agent_change_core`、`agent_change_task_card`、`agent_change_verification_retry`。
- Provider 无关执行后置流程：`agent_candidate_pipeline`、`agent_worker_evidence`、
  `agent_plan_scope`、`agent_core_observation`。
- Provider 会话接入：`agent_provider_adapter`、`agent_provider_preparation`、
  `agent_provider`、`provider_session`；Claude Code 使用 `claude_code_runner` 和
  `claude_code_process`，Codex 使用
  `codex_app_server`、`codex_app_server_rpc`、`codex_app_server_runner`、
  `codex_process`、`codex_isolation`、`codex_workspace`；
  进程所有权与停止复用 `execution_control`、`execution_process`。
- 仓库与上下文：`agent_context`、`agent_repository_*`、`agent_git_worktree`、
  `agent_git_candidate`、`agent_runtime_support`、`git_read`、`git_inventory`、
  `repository_identity`。
- 恢复与交接：`agent_recovery*`、`agent_handoff*`、`agent_resume_validation`、
  `agent_side_effect_adjudication`。
- 状态与展示：`agent_run_status`、`agent_status_*`、`agent_visibility`、`agent_task_card*`。
- 共享数据和脱敏：`models`、`artifact_rendering`、`redaction`。未列出的模块按最近职责归属，
  不因缺少名称清单就新建通用 `utils` 或平行状态层。

## Core 模块地图

- CLI 组合根：`cli_entrypoint`、`cli`、`cli_support`。这里只注册命令和组合 Runtime，不拥有
  成功语义。
- 任务与项目上下文：`brief_*`、`project_*`、`agents_proposal`、`content_manifest`。
- Loop 编排：`loop_runtime`、`loop_*`、`runner`、`worker_*`。Loop 只编排已有阶段，不复制
  Verification、Risk、Reviewer 或 Finish 的裁决。
- 执行与进程：`execution_*`、`windows_*`、`worker_temp`。进程身份、停止和平台边界不能移入
  展示或通用工具模块。
- Workspace 与 Scope：`workspace_*`、`runtime_workspace`、`tracked_workspace`、`scope_*`、
  `comparison_binding`。
- Verification、Risk、Review 与 Finish：`verification*`、`risk_*`、`gate_runtime`、
  `review_*`、`finish_*`。每一层保留独立错误码和证据所有权。
- 状态、恢复与 Artifact：`run_*`、`progress`、`trace`、`recovery_*`、`artifact_rendering`。
  状态展示只能投影权威 Artifact，不能反向创造运行事实。
- `tools/` 和 `resources/`：受控工具实现与包内静态资源；资源镜像必须由同步测试绑定。

## 改动与验证矩阵

- CLI 注册、参数或帮助：运行顶层 CLI 和对应 Supervisor 测试。
- Loop、Verification、Risk、Reviewer、Finish 或恢复：运行对应 Core 文件和相关 Security
  负向合同。
- `agent_*`：运行所属 Supervisor 文件；涉及身份、路径、Artifact 或恢复时追加相关 Security
  文件。
- `experimental/`：运行对应 Experimental 文件和 Core/Experimental 依赖边界测试，不把实验
  测试塞回产品分片。
- 打包、入口、依赖或资源：从空构建目录运行 wheel/sdist smoke，并检查安装包没有残留已删除
  模块。
- 共享边界不明确时先运行最窄完整文件，再扩大到受影响文件；完整职责分片交给 PR CI。不要从
  单个绿 node 推断整个分片通过，也不在本机机械重复 CI 全量。

## 修改要求

- 优先在已有职责模块内做窄修改。新增状态、Artifact、顶级命令或事实源前，先证明现有合同无法表达。
- 大文件和既有复杂度是触碰时渐进整理的信号，不是机械拆分任务。只提取有明确输入输出、独立测试
  价值和单一调用责任的纯逻辑；不要为了行数把状态机拆成跨文件跳转。
- Worker 进程不得直接创建提交或切换分支；涉及 Candidate/Checkpoint Commit 的 Git 写操作必须
  由隔离 Worktree 所有者执行，并在写入前完成范围与工作区检查。
- 状态兼容不能通过默认值或 shim 掩盖证据不足；安全兼容应保留旧信息，同时输出当前有效投影。
- 修改成功语义、恢复、Writer 所有权或证据绑定时，先检查已有测试。只有本次确实改变了正常、
  拒绝或恢复分支且现有覆盖不足时，才补对应的最小代表场景。
