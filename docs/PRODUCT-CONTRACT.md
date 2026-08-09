# 产品契约

## 核心定位

Vega 是面向个人开发者的轻量 AI Coding Harness。它不替代 Codex、Claude Code
或其他编码模型，而是为一次真实研发任务补上可控的外层闭环：

```text
bug / feature
  -> 项目上下文编译
  -> Worker 前工作区基线
  -> 受控 worker 执行
  -> 确定性验证
  -> Reflect 证据与变更风险门禁
  -> 隔离 reviewer
  -> 修复或人工接管
  -> 交付报告与可恢复证据
```

Vega 的核心价值不是“拥有更多 Agent 功能”，而是回答四个问题：

1. worker 开始前应该看到哪些项目规则和任务事实？
2. 外部编码会话如何限制范围、时间和工作区污染？
3. 如何用测试和隔离 reviewer 避免 worker 自证正确？
4. 中断、超时或 provider 异常后，如何保留现场并安全交还人工？

## 日常入口

日常使用只要求理解以下入口：

```text
vega do bug|feature
vega status
vega finish
vega stop
vega recover
```

`brief`、`reflect`、`gate`、`review-pack`、`review` 和 `loop continue`
是可单独调用的流水线阶段，主要用于排障、人工接管和解释运行过程，不要求用户每天手工编排。

兼容的 `vega run engineering-change` 是 YAML 驱动的只读 Inspection Loop，用于生成计划、
报告和 eval；它不等同于会启动 worker 的日常 Coding Harness。

## Python 接口边界

Vega 发布 Python distribution 是为了安装 CLI 和本地资源，不把内部 Runtime 模块承诺为
稳定 SDK：

- 稳定的程序化导出只有 `vega.__version__`。
- 用户级稳定入口是本文记录的 `vega` CLI 及其 artifact/状态合同。
- `vega` 下其他模块属于内部实现，可以因核心精简而移动或删除。
- `vega.experimental.*` 明确为实验实现，不承诺导入路径、类型或调用方式跨版本稳定。
- 已移动的内部模块不恢复 `vega.assurance`、`vega.memory`、`vega.runtime` 等兼容 shim。

如果未来要提供 Python SDK，必须单独定义命名空间、版本和自动化兼容合同；不能把当前内部模块
被外部偶然导入视为既成公共 API。

## 能力分层

### 核心能力

- `AGENTS.md`、项目画像和任务输入的上下文编译。
- worker/reviewer 使用独立会话和固定 sandbox；reviewer 不继承 worker 的完整聊天记录。
- `codex exec` 角色仍可继承用户选择的 provider、profile、model 和 Windows sandbox，但 Vega
  固定禁用个人 memories、plugins、hooks 与 legacy notify，避免个人上下文和回调污染目标仓库。
- Codex 写工具可能留下仓库根部的空 `.agents/`。Workspace Gate 仅把完全为空的普通目录视为
  工具残留；目录含内容、不可读取、symlink 或 Windows reparse point 时仍按工作区变化停止。
- 验证命令、精确路径范围、变更预算、Prompt 预算与工作区污染门禁。
- assist 在 Worker Prompt 前生成 `workspace-baseline.json`，并用根状态与 trace 绑定其版本、
  内容哈希和 HEAD。基线捕获不完整、已有 tracked diff 或初始化期间 HEAD 漂移时，不生成
  Worker Prompt、不创建 iteration，也不允许继续该 run；清理或稳定仓库后必须新建 loop。
- assist continue 在创建 iteration 前重新验证 baseline artifact，并以真实工作区差异归因
  Worker 结果。缺失、被改写或与根状态、trace 不一致时 fail-closed；没有 baseline 的旧 run
  只允许查看，不允许继续。
- auto 首轮拒绝已有 tracked diff；同一 run 的后续轮次保留上一轮 diff 作为基线。
- scope gate 在 worker/人工 continue 后先检查 staged 和 unstaged tracked diff；verification 结束后再次检查，Reflect 固化 review 输入后再检查一次。`forbidden_paths` 优先于 `allowed_paths`；最后一次与 reviewer 的工作区快照校验共同防止异步进程把越界 diff 带入隔离审查。任一阶段越界时保留 result、report、state 和 trace 证据并停止，不回滚或自动清理现场。
- loop 启动时绑定稳定的 HEAD、项目策略文件摘要和 scope 规则摘要；worker commit/checkout、
  `assume-unchanged`、`skip-worktree`、运行中策略变化或 reviewer 授权快照变化都会 fail-closed。
  `project-policy-snapshot.json` 与根状态哈希绑定，Finish 会复查当前策略；缺少三阶段 scope
  证据的旧 run 仍可查看，但不能自动进入 `ready_to_commit`。
- iteration-local risk gate 的结果与报告绑定 source reflect、iteration、结果哈希、风险、建议和命名高风险命中项；Finish 会结合 trace、连续 iteration 与 Reflect 重算复核。缺失、篡改、语义不一致或绕过 `human-review` 时，不得给出 `ready_to_commit`。
- `risk.required_reviews` 命中时，独立 reviewer 必须逐类覆盖全部命中文件，说明修改、判断、证据和剩余风险；持久化 verdict 固定为 `needs_human`。这类 AI 审查只能作为人工检查材料，不能成为 Goal checkpoint 的自动完成证据。
- reviewer 不能覆盖确定性验证失败。
- state、trace、execution、status、finish、stop 和 recover。

独立 reviewer 仍在同一目标仓库的只读视图中读取 Vega 明确编译的任务、diff、验证结果、
项目规则、风险门禁和可选 accepted memory。这里承诺的是角色、会话和输入边界隔离，
不是容器、独立文件系统或操作系统级安全隔离。

Reviewer 输出必须通过 `reviewed_files` 声明对可信变更文件清单的完整覆盖；漏报或加入清单外
路径时，Vega 将结论降为 `needs_human`。Finish 始终单独展示完整变更事实、Reviewer 重点文件
和其他已变更项，模型不能通过“认为不重要”静默隐藏文件。该门禁只证明文件路径声明完整，
不证明 Reviewer 已理解每一行或每个 diff hunk，人工仍应检查完整 staged/unstaged Diff。

Vega 只承诺控制面合同：编译输入、封存基线、读取真实工作区、运行验证、生成 Reviewer
证据并裁决状态。Worker 可以是当前主会话、宿主原生子代理或显式 `auto` runner；Vega
不要求也不实现通用 Multi-Worker 调度器，并且不把 Worker 的口头结论当作完成证据。

diff、测试输出、源码注释和其他仓库内容都属于不可信证据，其中出现的操作指令不得覆盖
reviewer 合同。该提示词边界只能降低误跟随风险，不能证明模型能抵抗恶意 Prompt Injection；
因此 reviewer 的 `approve` 从不单独授予自动成功，精确路径范围、确定性验证、风险门禁和
人工审批仍是最终约束。

### 高级能力

- 独立执行 brief、reflect、gate、review-pack 和 review。
- 为大范围变更声明 scope profile。
- 本地 decision ledger。

### 实验能力

- Memory proposal / ledger。
- Goal P0 长任务人工状态层，以及默认关闭、一次只运行一个 checkpoint 的 P1 实验入口。
- Codex skill adapter。

实验能力不得反向扩大核心成功条件。未使用 Memory、Goal 或 adapter 时，bug/feature
主流程仍必须可以完整运行。

## 项目知识分层

项目知识按职责分为四层：

| 层级 | 内容 | 特性 |
|---|---|---|
| `AGENTS.md` | 稳定规范、架构边界、长期踩坑 | Git 版本化，面向人和 AI |
| `.vega.yaml` | 验证命令、精确路径范围、预算、风险路径、runner 策略 | 机器可执行 |
| run artifacts | 本次任务、diff、验证、review 和恢复证据 | 单次运行事实 |
| accepted memory | 已人工确认、跨任务可复用的局部经验 | 可选，不是规范来源 |

稳定规则应优先进入 `AGENTS.md`，可机械执行的约束进入 `.vega.yaml`，单次任务事实留在
run artifacts。只有无法合理归入前三层、且有明确适用范围和来源证据的经验，才适合进入
accepted memory。

## 工作区文件卫生

仓库根目录不得堆放测试临时目录、验证日志或运行生成物。测试源码和静态 fixture 放在
`tests/`，可丢弃的测试临时文件放在 `.tmp/`，人工验证输出放在 `.local-validation/`，
Vega run artifacts 放在 `runs/`，本地 memory 数据放在 `memory/`，Python 构建产物
放在 `dist/` 和 `build/`。不得把这些文件写入其他项目或仓库根目录；详细目录职责见
`docs/WORKSPACE-HYGIENE.md`。

## Memory 决策

Memory 是可选经验账本，不是每轮必须生成的流水线产物：

- brief 阶段不产生经验。
- loop 成功不等于产生了可复用经验。
- proposal 数量允许为 0。
- 只有用户在 reflect 阶段明确提供经验候选时才生成 proposal。
- 长期 ledger 仍必须由用户显式 accept/reject。
- accepted memory 可以参与后续上下文编译，但不能覆盖代码、测试、`AGENTS.md` 或当前任务事实。
- 仓库级 accepted memory 按本地仓库 scope 精确匹配；同名目录之间不允许自动回填。

当前不引入向量数据库、embedding、自动学习、自动冲突合并或自动长期写入。

## 增长约束

新能力进入核心前，至少应改善以下一项并提供 dogfood 证据：

- 任务成功率。
- reviewer 有效缺陷发现率。
- 人工操作步骤。
- 中断恢复能力。
- 无关上下文、token 或执行耗时。

仅增加命令、artifact、状态字段或架构名词，不视为有效演进。没有真实使用证据的能力保持实验状态，
不得继续扩建。

## v0.1.0 停止线

v0.1.0 完成核心证据一致性和口径统一后进入功能冻结：

- 保持上下文编译、受控执行、确定性验证、隔离审查和恢复交接稳定。
- `loop continue` 必须绑定原仓库和 `needs_human` 状态。
- Reflect 与 Review 必须使用同一工作区快照，证据过期时停止并交还人工。
- Goal P0 完成前必须重新校验 child run 或 manual evidence。
- 不实现 Goal P1、多 Agent、数据库、Web UI、向量 Memory、后台 daemon 或自动提交发布。
- 新能力只有在多次真实 dogfood 暴露同一问题，并能改善增长指标时才重新评估。

## v0.1.1 维护发布边界

v0.1.1 只吸收已经通过真实 Dogfood 和完整回归的路径范围、验证隔离、恢复与并发安全
修复，不改变产品定位或扩大功能范围。

当前仍是本地单用户 CLI。同一个 loop run 的 start、continue、recover、finish 和 decision
append 使用本地非阻塞 OS 文件锁互斥；busy 命令在修改可信业务 artifact 前 fail closed。
`vega stop` 保持旁路，只写 active execution 的 stop request。

该能力不承诺外部进程持续并发改写目标仓库时的操作系统级原子隔离。runtime 会用前后快照、
HEAD、策略摘要、index 标记和 reviewer 授权快照发现关键阶段变化，但不会引入目标仓库全局
锁、网络锁、分布式锁服务或数据库事务。

## v0.1.2 成功语义维护边界

v0.1.2 只收紧现有 Coding Harness 的成功裁决，不新增 Agent 角色、执行阶段或持久化设施：

- 自动成功必须依赖最新 iteration 的非空、完整、未中断且全部通过的结构化验证记录。
- 零条验证命令、显式跳过、部分执行、证据缺失或损坏均不得被 reviewer `approve`
  提升为成功。
- 写入终态前必须重新执行 eval；任一 `FAIL:` 同时使 `state.status` 和
  `run_finished.status` 为 `failed`。
- Finish 与 Goal 只使用最新 iteration 的受信验证结论；历史失败保留为证据，但不能覆盖
  后续已经重新验证通过的修复。

该维护版本不改变 `.vega.yaml` schema、CLI 命令、run 目录结构或 v0.1 的产品停止线。
