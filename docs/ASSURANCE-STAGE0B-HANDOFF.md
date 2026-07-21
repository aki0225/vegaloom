# Assurance 阶段 0-B 接力说明

> 更新时间：2026-07-21
> 当前结论：成功语义修复已完成本地验证和跨平台 PR CI，等待人工 diff 复核与合并决策。
> 在人工复核和明确合并决策前，不合并 `main`、不打标签、不发布。

## 1. 阶段目标

阶段 0-B 只修复 v0.1 现有成功语义，不实现数据库迁移、数据修改、并发 detector、
adapter 或新 Agent 角色：

- 零条验证命令不得自动成功。
- `--no-verify` 不得被 reviewer `approve` 提升为成功。
- 非结构化外部测试日志不得冒充结构化验证。
- 只有最新 iteration 存在完整、未中断且全部通过的结构化验证时，Loop 才可能成功。
- verification artifact 在终态 eval 前缺失、损坏或变成不完整时必须 fail-closed。
- reviewer 完成后工作区再次变化时，不得写出 `run_finished=success`。
- 前序失败已经被最新受信通过修复时，Finish/Goal 不得继续用历史失败阻塞交付。

预注册内容位于 `eval/assurance-validation.md` 的
`AV-STAGE0B-001 · preregistration`。

## 2. Git 状态

- 工作分支：`fix/verification-success-semantics`
- 远端基线：`origin/main@176ac381`
- 基线标签：`v0.1.1`
- 首个修复提交：`1a2c715`
- 成功语义收口提交：`497513c`
- fixture 收口提交：`313503e`
- PR：`#1`
- 最终 CI：`29837944440`，`10/10 success`
- 当前分支尚未合并 `main`、打标签或发布。
- LangGraph 实验位于独立分支和工作树，不属于本阶段 diff。

## 3. 实现结论

### Loop

- reviewer `approve` 不能覆盖验证失败、跳过、零命令或证据不完整。
- success eval 会重新校验最新结构化 verification artifact。
- success eval 会重新计算 reviewer 证据与当前工作区的新鲜度。
- eval 发现失败时，最终 `state.status` 和 `run_finished.status` 都写为 `failed`。
- 明确的验证失败保留“验证命令失败”结论；未知或跳过仍使用
  `verification_unverified`。

### Finish 与 Goal

- Finish 和 Goal 复用同一可信验证判定，不只相信 Loop 顶层状态。
- 提交门禁只判断最新 iteration；历史失败保留在报告中，但不会覆盖最新受信通过。
- artifact 损坏、证据过期或 workspace fingerprint 不一致时继续 fail-closed。

### 结构化验证合同

受信通过现在同时要求：

- `command_count > 0`；
- `selected_command_count == command_count`；
- `skipped_commands` 为空；
- 顶层和逐命令 `interruption_*` 均为空；
- 命令与结果数量一致；
- 所有结果为 `passed`；
- execution、run、iteration、repo 和 shell 绑定完整。

## 4. 本地验证

本轮已确认：

- 完整收集：`516 tests collected`。
- Python 3.12 CI 分片：19 个测试文件全部覆盖，无遗漏、重复或未知文件。
- Assurance 回归：14 个节点全部通过，按完整 node id 分片执行。
- 结构化成功、验证失败、两轮失败后恢复成功三个控制案例通过。
- 终态 artifact 缺失、非法 JSON、review 后工作区变化三个场景均写出
  `state.status=failed` 和 `run_finished.status=failed`。
- Finish 新鲜度、Loop 终态 trace、artifact 完整性和 verification interruption
  定向回归通过。
- `python -m compileall -q src` 通过。
- `ruff check src tests --no-cache` 通过。
- `git diff --check` 通过。

本机同时存在另一工作树的 LangGraph 实验测试。部分组合 pytest 命令因资源竞争超过
60 秒，均按 timeout 处理，未计为通过或失败；本阶段直接相关节点随后已拆分并得到
明确通过结果，其余 516 节点的完整覆盖仍由 PR CI 裁决。

本地没有把单个超时命令冒充完整全量测试。516 个节点的跨平台完整结论由 PR CI 提供。

## 5. PR CI

首次 CI `29835869528` 的 Python 3.11 全量测试暴露两个正向 fixture 未提供真实结构化验证
证据；Runtime 没有因此放松。提交 `313503e` 只为 fixture 增加固定 verification 命令并
启用 `verify=True`。

随后 Workflow `29837944440` 完成：

- 静态检查与 516 节点收集合同：通过。
- Python 3.11 全量测试：`515 passed, 1 skipped in 115.39s`。
- Python 3.12 五个分片：全部通过。
- Windows 专项与 wheel smoke：通过。
- POSIX 临时目录专项：通过。
- wheel 构建与安装：通过。
- 合计：`10/10 success`。

结果已按预注册规则追加到 `eval/assurance-validation.md` 的
`AV-STAGE0B-001 · result`，没有改写既有实验记录。

## 6. 下一步

1. 人工复核 PR #1 的 Runtime、测试、CI 和文档 diff。
2. 明确决定是否把 PR #1 合并到 `main`。
3. 合并后另行确认 `v0.1.2` 标签和发布，不自动执行。

## 7. 剩余边界

- 当前修复证明的是本地代码工作流的验证成功语义，不证明数据库 migration、
  backfill、分布式并发或生产事务安全。
- reviewer 与 worker 是会话上下文隔离；reviewer 仍读取共享仓库的只读视图，
  不是容器或操作系统级文件系统隔离。
- 目标仓库可被外部进程并发修改；Runtime 在成功收口前重算 freshness，但不提供
  跨进程或跨机器事务锁。
- Vega 仍不自动 commit、push、release、删除文件或写入长期 Memory。
