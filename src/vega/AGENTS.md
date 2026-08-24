# Vega 源码规则

## 产品主线

- Vega 只有一条可信完成链：Workspace、Verification、Risk、Reviewer 和 Finish 由 Core
  Harness 拥有；`vega agent` 是复用该链的可选 Supervisor 控制层，不得形成第二套成功语义。
- `vega do / loop` 是 Core 执行入口，`vega agent` 负责 Plan、批准、单 Writer、Checkpoint、
  恢复和交接。Goal、Memory、Assurance 与 Inspection 属于实验或兼容能力。
- 保持本地文件优先、fail-closed、无自动 Git、无自动发布和无自动长期 Memory。

## 依赖与事实权威

- 依赖方向固定为：CLI 组合根 -> Core；显式实验命令 -> `experimental` -> Core。
  Core 不得静态导入 `vega.experimental`，实验命令只能在调用时延迟导入。
- 用户指令、仓库规则、`.vega.yaml` 和当前已批准 Plan 拥有任务意图；Git、真实 Workspace、
  活动进程和新鲜 Artifact 拥有运行事实；Worker Claim 只能作为待验证输入。
- Agent State 只拥有本机控制状态。Diff、Verification、Risk、Reviewer 和 Finish 事实继续由
  Core Artifact 拥有；Trace 是追加式审计线索，Graph checkpoint 只拥有图游标。
- 文本状态和 JSON 状态必须使用同一实时证据投影。证据缺失、损坏、过期或 Workspace 漂移时，
  展示层降级为 `needs_human`；执行层仍必须严格拒绝不可信证据。

## Supervisor 模块地图

当前 `agent_*` 文件保持扁平结构，按以下职责查找，不为整理文件数量做批量搬迁：

- 入口与编排：`agent_cli`、`agent_runtime*`、`agent_routing`、`agent_graph`、`agent_worker`、
  `agent_finalization`。
- 合同与持久化：`agent_contract`、`agent_persistence`、`agent_run`、`agent_mutation`。
- Codex 执行桥：`agent_codex_*`、`agent_execution_bridge`。
- 仓库与上下文：`agent_context`、`agent_repository_*`、`agent_runtime_support`。
- 恢复与交接：`agent_recovery*`、`agent_handoff*`、`agent_resume_validation`、
  `agent_side_effect_adjudication`。
- 状态与展示：`agent_run_status`、`agent_status_*`、`agent_visibility`、`agent_task_card*`。

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

- CLI 注册、参数或帮助：运行相关 Core CLI 测试；涉及 `vega agent` 时同时运行 Supervisor CLI
  测试。
- Loop、Verification、Risk、Reviewer、Finish 或恢复：运行对应 Core 文件和相关 Security
  负向合同。
- `agent_*`：运行所属 Supervisor 文件；涉及身份、路径、Artifact 或恢复时追加相关 Security
  文件。
- `experimental/`：运行对应 Experimental 文件和 Core/Experimental 依赖边界测试，不把实验
  测试塞回产品分片。
- 打包、入口、依赖或资源：运行 wheel/sdist smoke 或等价的干净环境验证。
- 共享边界不明确时先运行最窄完整文件，再扩大到职责目录；不要从单个绿 node 推断整个分片通过。

## 修改要求

- 优先在已有职责模块内做窄修改。新增状态、Artifact、顶级命令或事实源前，先证明现有合同无法表达。
- 状态兼容不能通过默认值或 shim 掩盖证据不足；安全兼容应保留旧信息，同时输出当前有效投影。
- 修改成功语义、恢复、Writer 所有权或证据绑定时，至少覆盖正常、损坏或越界、恢复三个相关场景。
