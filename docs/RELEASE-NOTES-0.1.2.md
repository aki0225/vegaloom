# Vega v0.1.2 候选发布说明

v0.1.2 是 v0.1 冻结范围内的成功语义安全修复候选。它不增加数据库、Web UI、
LangGraph 主线、ATG adapter、多 Agent、自动提交或长期 Memory 写入能力。

本文件描述候选行为；在 `main` 合并、跨平台 CI 全绿和人工确认前，`v0.1.2` 尚未发布。

## 修复内容

### 结构化验证成功语义

自动成功必须依赖最新 iteration 的完整结构化 verification artifact：

- 至少真实执行一条验证命令；
- 选中命令数与执行命令数一致；
- 不存在跳过命令；
- 顶层和逐命令均未中断；
- 所有命令结果均为 `passed`；
- artifact 与当前 run、iteration、repo、shell 和 execution 绑定一致。

因此以下情况不再能够被 reviewer `approve` 提升为成功：

- 项目没有识别出验证命令；
- 用户显式使用 `--no-verify`；
- 只提供非结构化外部测试日志；
- verification artifact 缺失、损坏、错绑或只执行了部分命令；
- verification 超时、停止或终止状态不确定。

### 终态 fail-closed

Loop 在写入成功终态前会重新执行 eval：

- eval 含任意 `FAIL:` 时，最终状态写为 `failed`；
- `run_finished.status` 与 `state.status` 保持一致；
- verification artifact 在终态收口前丢失或损坏时不能留下临时成功；
- reviewer 完成后工作区发生变化时，旧 approve 不能形成成功终态。

明确的验证失败仍保留“验证命令失败”结论；验证未知、跳过或零命令则交还人工。

### 多轮修复语义

Finish 和 Goal 的提交门禁只采用最新 iteration 的受信验证结果。前序失败仍保留在历史
证据中，但当后续修改已经得到新的结构化验证通过和 reviewer approve 时，不再错误地把
任务标记为 `needs_fix`。

## 兼容性影响

- `.vega.yaml` schema 仍为 `version: 1`。
- CLI 命令和 run 目录结构不变。
- 旧 run 仍可读取和复盘，但缺少完整 verification artifact v2 字段时不会被提升为
  `ready_to_commit`。
- 依赖“零命令或 `--no-verify` 也可自动成功”的脚本会改为收到非成功状态；这是安全修复，
  不是兼容性回退。
- 包版本在正式发布提交前仍保持当前稳定版本，不在候选修复分支提前改号。

## 验证门禁

候选合并前必须满足：

1. 516 个 pytest 节点的收集合同通过。
2. Python 3.11 全量测试通过。
3. Python 3.12 全部分片通过且恰好覆盖所有测试文件。
4. Windows 专项、wheel smoke 和 POSIX 专项通过。
5. wheel/sdist 构建与干净安装验证通过。
6. `compileall`、Ruff 和 `git diff --check` 通过。
7. `eval/assurance-validation.md` 追加正式结果，不改写预注册和历史失败证据。

## 不变边界

- 不自动 commit、push、release 或删除目标文件。
- 不自动接受或写入长期 Memory。
- reviewer 与 worker 保持会话上下文隔离。
- read-only reviewer 不等于容器或操作系统级隔离。
- 不提供数据库事务、跨机器锁或目标仓库全局写入锁。
