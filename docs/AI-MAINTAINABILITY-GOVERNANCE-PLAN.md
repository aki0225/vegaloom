# Vega AI 可维护性治理计划

> 状态：completed（PR `#85` 的 10 项 CI 全部通过，已合入 `main@d1a4b8d`）
> 创建日期：2026-08-23
> 完成基线：`main@d1a4b8d`
> 最后更新：2026-08-25
> 目标版本：不预设版本号；按 Dogfood 证据决定第三轮范围，产生源码修改后复跑同等级任务

## 1. 为什么现在做

Vega 已经具备 Core Coding Harness 和可选 Supervisor Agent，但活跃产品、兼容入口、冻结实验、
历史证据和本地运行产物仍共同占用维护上下文。继续增加 Agent 能力前，先让人工和 AI 能稳定回答：

1. 当前产品入口是什么；
2. 一个改动归哪个模块；
3. 必须运行哪些验证；
4. 哪些代码和测试只为历史兼容保留；
5. 哪些本地产物可以清理，哪些证据必须保留。

本计划不追求最少文件或最高测试数字，只降低理解成本、重复实现和日常验证成本。

## 2. 治理前基线与当前快照

2026-08-23 的治理前只读审计得到以下基线：

- `src/vega/`：153 个 Python 文件，约 46,546 行；129 个模块位于包根目录；
- `agent_*`：36 个文件，约 9,939 行；
- `loop_runtime.py`：3,039 行，assist 与 auto 存在重复的 post-worker 阶段；
- 内部依赖存在三组循环，其中一组跨越五个 Core 门禁与证据模块；
- 测试：49 个文件、1,408 个 node、约 43,000 行；普通 PR 执行 1,201 个产品 node；
- 文档：59 个 Markdown 文件、约 17,935 行；当前路线与历史时间线混在同一入口；
- CLI：约 23 个顶层入口、59 个叶子命令，日常、排障、兼容和实验入口展示层级接近；
- 审计机器当时存在较多 `runs/`、`.local-validation/` 与构建产物；这只用于一次性清理盘点，
  不作为项目规模或治理成效指标。

这些数字用于验证治理是否有效，不作为机械删除目标。

2026-08-25 在第三轮修改前的 `main@aadeedf` 重新收集：

- `src/vega/`：160 个 Python 文件、48,124 行；136 个模块位于包根目录；
- `agent_*`：40 个文件、11,223 行；
- `loop_runtime.py`：2,985 行；`_continue_assist_locked` 为 525 行，
  `_run_auto_iterations` 为 821 行，两条路径仍分别编排 post-worker 阶段；
- 包根目录当前有两个静态依赖环：既有五模块 Core 依赖环，以及
  `agent_task_card` / `agent_task_card_render` 两模块环；
- 当前本地完整节点收集为 1,424 个；第二轮移动测试职责后保留的 1,409 个节点没有被删除，
  PR `#84` 又补充了验证专用恢复测试；
- `docs/`：60 个 Markdown 文件、18,118 行；
- 顶层 CLI 仍为 23 个入口，默认帮助的产品展示层级已由第一轮调整。

当前代码量增加主要来自 PR `#84` 的验证专用恢复和对应证据校验。该 Diff、真实运行记录和
修改过程中触碰的模块将作为第三轮进入判断的输入，不能仅凭文件数或行数增长自动启动重构。

第三轮本地实现后的待提交快照：

- `src/vega/`：161 个 Python 文件、47,942 行；
- `loop_runtime.py`：2,772 行；`_continue_assist_locked` 从 525 行降至 150 行，
  `_run_auto_iterations` 从 821 行降至 435 行；
- Ruff C901 存量从 34 个降至 33 个；
- 新增 111 行的 `agent_operation.py`，集中 operation 与 child summary 的 canonical 引用、
  不可变身份写入和 active operation 类型校验；
- 五模块 Core 静态依赖环保持原状，本轮没有为消除静态环扩大修改范围。

## 3. 不可改变的边界

三轮治理均不得：

- 放松 Verification、Scope、Risk、Reviewer 或 Finish 的 fail-closed 语义；
- 打通 Worker 与 Reviewer 会话上下文；
- 改变现有命令、参数、退出码和 Artifact 的兼容行为；
- 增加自动 commit、push、release、长期 Memory 或多 Writer；
- 把 Experimental 静态依赖引入 Core；
- 为减少测试数量删除路径逃逸、进程所有权、恢复事务、证据完整性或跨平台边界；
- 为目录整齐批量搬迁全部 `agent_*` 模块；
- 修改或润色 `eval/` 中已有历史证据。

## 4. 第一轮：当前事实、规则与产品入口

状态：`completed`（2026-08-24，PR `#82` 已通过 CI 并合入 `main@eaea175`）

完成结果：目录与验证职责已写入分层规则，文档入口已恢复为导航，历史 SAG3B Task Card 已按
原字节归档，默认 CLI 帮助只展示 Core 与 Supervisor 主入口；兼容和实验命令仍可直接调用。

### 范围

1. 补齐根目录和 Core 模块职责地图，明确 `.github/`、`.vega/`、`scripts/`、`site/`、
   `eval/`、`runs/` 与 `memory/`。
2. 只在 `scripts/`、`site/`、`eval/` 增加必要的局部规则；不继续向小目录铺设规则文件。
3. 让本地验证说明与 CI 一致，并给出按改动职责选择测试的矩阵。
4. 将 `docs/README.md` 恢复为纯导航；让 `ROADMAP.md` 的开头直接显示当前阶段、未发布主线变化、
   唯一下一步和停止条件。
5. 为历史 `.vega/tasks/` 增加状态索引；已结束的 SAG3B Task Card 不再显示为可恢复的 `ready`。
6. 默认 CLI 帮助突出日常 Core 与 Supervisor；内部、兼容和实验命令保持可调用，但不得继续冒充主入口。
7. 制定本地产物保留规则。删除 `runs/`、`.local-validation/` 或历史临时目录前必须另行盘点并确认。

### 验收

- 新会话只读根规则、源码规则和文档导航后，可以定位 Core、Supervisor、Experimental 和历史证据；
- 没有历史 Task Card 仍声明为当前可继续任务；
- 当前路线不需要阅读数百行历史时间线才能找到下一步；
- `vega --help` 的默认第一层不再把内部机械命令与日常入口并列突出；
- 没有删除本地运行证据；
- 编译、文档链接检查、CLI smoke、仓库卫生、Ruff、相关测试和 `git diff --check` 通过。

## 5. 第二轮：测试职责与执行成本

状态：`completed`（2026-08-24，PR `#83` 已通过完整 CI 并合入 `main@08d008e`）

### 改造前基线

基线提交为 `main@eaea175`。节点按当前 PR 分片命令重新收集：

| 分片 | node |
|---|---:|
| Core | 363 |
| Core Heavy | 196 |
| Supervisor | 237 |
| Security | 406 |
| 普通 PR 产品合计 | 1,202 |
| Experimental 与冻结 CRWP | 207 |
| 全量 | 1,409 |

最近三次使用同一分片结构且成功结束的 PR CI，产品 Job 耗时如下：

| CI run | Core | Core Heavy | Supervisor | Security | 产品关键路径 |
|---|---:|---:|---:|---:|---:|
| `32625925110` | 96s | 114s | 64s | 42s | 114s |
| `32629480812` | 95s | 96s | 59s | 40s | 96s |
| `32680716774` | 92s | 117s | 62s | 37s | 117s |
| 中位数 | 95s | 114s | 62s | 40s | 114s |

第二轮以产品关键路径中位数不高于 `91.2s` 为 20% 目标。Experimental 是否因本 PR 的路径
变化而运行单独记录，不能拿跳过实验 Job 充当产品分片提速。

### 改造结果

Goal、Memory、Inspection 与 Dogfood 场景移回 Experimental；全部测试函数经过 AST 对账，
没有缺失、重复或行为改写。`test_success_semantics.py` 仍归 Core，只调整到 Core Heavy 分片，
让两个产品分片的耗时接近。

| 分片 | 改造前 node | 改造后 node |
|---|---:|---:|
| Core | 363 | 332 |
| Core Heavy | 196 | 143 |
| Supervisor | 237 | 237 |
| Security | 406 | 406 |
| 普通 PR 产品合计 | 1,202 | 1,118 |
| Experimental 与冻结 CRWP | 207 | 291 |
| 全量 | 1,409 | 1,409 |

最终分片在相同 GitHub Actions Runner 条件下连续运行三次：

| CI run | Core | Core Heavy | Supervisor | Security | 产品关键路径 |
|---|---:|---:|---:|---:|---:|
| `32686397026` | 75s | 75s | 61s | 40s | 75s |
| `32686603867` | 80s | 87s | 54s | 38s | 87s |
| `32686818721` | 74s | 69s | 63s | 42s | 74s |
| 中位数 | 75s | 75s | 61s | 40s | 75s |

产品关键路径中位数从 `114s` 降至 `75s`，下降约 `34.2%`。第一次只调整测试所有权时，
三次关键路径中位数为 `92s`，没有达到 20% 目标；随后只做分片再平衡，未删除或跳过测试。
本 PR 因修改 Experimental 测试路径而完整运行了实验与历史重放 Job。

### 范围

1. 先记录本地与 CI 各分片的 node 数和 wall-clock，建立改造前基线。同类 GitHub Actions
   Runner 的改造前后结果各取至少三次有效运行的中位数。
2. 将错误归入 Core 的 Experimental 测试移回对应职责，不让产品分片隐式维护冻结 Runtime。
3. 清理 `test_smoke.py`：只保留 CLI 接线和代表性端到端场景，底层合同由所属测试文件唯一拥有。
4. 聚合架构组合测试，减少仅由参数展开形成的独立 node。
5. 将 RCB、Showcase 和已冻结实验重放改为相关路径、release 或手工触发。
6. 在职责目录内提取必要的 Git 仓库工厂和 JSON helper，减少重复 setup，不建立全局测试框架。

### 明确保留

- 冻结 CRWP manifest 绑定测试；
- Recovery chaos 的部分工作、事务重放和损坏恢复场景；
- Security 的 realpath/junction、进程树、run lock、脱敏、证据完整性和成功语义；
- Windows、POSIX、wheel 与 sdist 干净安装验证。

### 验收

- 每个测试场景有唯一职责所有者；
- 普通 PR 关键路径 wall-clock 相对同类 Runner 基线中位数至少下降 20%，否则不得宣称测试
  优化有效；
- 同时记录各分片 node 数和 main/release 全量运行范围；不能只靠少跑测试制造耗时下降；
- 删除或合并的测试均有覆盖去向，不以 node 数减少代替行为覆盖；
- main、release 和手工入口仍能运行完整历史与跨平台门禁；
- 完整 CI 通过。

## 6. 第三轮：源码职责与重复实现

状态：`completed`（两个目标完成实现，一个目标按证据未触发）

### 范围

第三轮不是第二轮后的默认动作。先根据真实 Dogfood 暴露的问题决定是否进入；进入后按以下
顺序执行，每项使用独立提交，前一项验证通过后才进入下一项：

1. 抽取唯一 post-worker 阶段执行器，消除 assist 与 auto 在 Verification、Reflect、Scope、Risk、
   Reviewer 和 Finish 之间的重复编排；成功状态仍由 `LoopAutomationRuntime` 唯一拥有。
2. 统一 Supervisor operation Artifact 的路径、身份和摘要生成规则。
3. 拆除 `comparison_binding`、`gate_runtime`、`loop_evidence`、`risk_gate_evidence`、
   `scope_gate` 的五模块循环依赖，明确 comparison、risk evaluation 和 freshness 的所有者。

清除无独立职责的转发 wrapper 不是第四个独立目标。只有前三项修改自然消除调用方时才随项
删除对应薄模块，不为减少文件数量另开重构。

### 进入判断与处理结果

1. **post-worker 重复编排：触发并完成实现。**
   assist 与 auto 原先各自编排 Scope、Verification、Reflect、Risk 和 Reviewer；PR `#84`
   还需要单独把验证恢复基线接进 assist 路径。当前两条入口统一调用
   `_run_post_worker_stages`，成功状态继续由 `LoopAutomationRuntime` 裁决。
2. **Supervisor operation Artifact 身份分散：触发并完成实现。**
   Worker dispatch、验证专用恢复、Recovery 和状态证据此前分别生成 operation 或 child
   canonical 路径。当前由 `agent_operation.py` 唯一拥有路径、身份保留和类型校验规则；
   原有旧 Worker Artifact 仍按既有规则读取。
3. **五模块 Core 依赖环：本轮未触发。**
   静态环仍可复现，但 Echo Vault Dogfood 的失败、恢复和 PR `#84` 修改没有暴露由该环导致的
   运行故障、错误归属或维护阻塞。按本计划的进入条件保留现状，等待真实任务给出直接证据。

### 回归结果

- 本地五个测试分片共 1,428 项通过；Security 另有 2 项按平台条件跳过；
- Compileall、Ruff、仓库卫生、架构增长门禁、`git diff --check` 和展示数据一致性检查通过；
- wheel 构建、隔离虚拟环境安装、核心模块导入和 CLI smoke 通过；
- 确定性 Dogfood 8/8 通过；
- Echo Vault 固定提交上的真实复验完成 Worker、五条验证、Risk、隔离 Reviewer 和验证专用
  恢复。全部验证通过后 Reviewer 仍发现一个目标代码缺陷，Supervisor 返回 `replan /
  needs_human`。该结果证明门禁没有被治理修改绕过，但不构成 `ready_to_commit` 成功样本；
- PR `#85` 的 10 项 CI 全部通过，并以 Squash Merge 合入 `main@d1a4b8d`。

### 暂不处理

- 不一次性把 36 个 `agent_*` 文件搬进新包；
- 不合并 Risk Gate 与 Scope Gate validator；
- 不合并 Worker rerun 的 planning、runtime 与 transaction；
- 不合并 Windows command、process 和 Job Object 模块；
- 不建设新的插件框架、Provider SDK 或通用 Artifact 系统。

### 验收

- 已进入的目标满足：assist 与 auto 只调用同一个 post-worker 阶段实现；
- 已进入的目标满足：operation Artifact 身份规则只有一个实现所有者；
- 未进入的目标记录未触发证据，不以静态整洁为由继续修改；
- 源码总行数和重复函数体减少，且未增加新的超过 500 行模块；
- Core、Supervisor、Security、Experimental、跨平台与打包验证全部通过。

## 7. 真实 Dogfood 与第三轮进入条件

状态：`completed`（2026-08-24）

Echo Vault 固定提交上的真实任务已记录在 `eval/real-world-runs.md`。首次 Core 验证因
`node_modules` 缺失而 fail-closed；人工只补齐 ignored 依赖环境，未修改 tracked Diff。
`vega agent retry-verification` 随后在同一 child 上复用原 Worker execution，五条验证全部
通过，Risk Gate 为 low，独立 Reviewer 返回 `approve`，父 Agent 最终为
`completed / ready_to_commit`。该恢复能力已通过 PR `#84` 合入当前主线。

第一轮合入、第二轮完成并通过最新主线 CI 后，立即选择一个真实项目和一个中等复杂度 Work
Item 执行 Supervisor Agent Dogfood。进入条件：

- 当前文档、规则和模块职责一致；
- 默认 CLI 产品面明确；
- 普通 PR 测试耗时已有可信下降；
- 最新主线完整 CI 通过；
- 没有未解释的本地脏文件或历史 Task Card。

Dogfood 只使用现有 Artifact 记录人工步骤、耗时、Worker 轮次、Reviewer 打回、恢复和最终裁决，
不再建设第二套报告或证据系统。

Dogfood 结束后逐项判断第三轮的三个源码目标：

- 真实任务确实暴露重复执行、Artifact 身份分散或循环依赖造成的维护问题时，进入对应修改；
- 只有静态整洁收益、没有真实使用影响时，记录为未触发，不自动实施；
- 第三轮如果产生源码修改，合入后再运行一次同等级任务，确认治理没有损伤 Agent 主流程。

## 8. 分支与提交边界

- 第一轮：规则、文档、Task Card 状态和 CLI 展示；不含测试删减或 Runtime 重构。
- 第二轮：测试职责和 CI；不改产品状态机。
- 第三轮：最多三个窄源码提交；只处理 Dogfood 证据支持的目标，不混入新 Agent 能力。
- 每轮独立验证、独立审查，确认后再进入下一轮。
- 任一轮出现成功语义变化、兼容入口破坏或无法解释的测试回归时，停止该轮并回到主线事实核对。

## 9. 完成定义

本计划完成不等于项目再无可优化之处。满足以下条件即停止治理并转入真实使用：

1. 当前事实入口清楚；
2. AI 能按规则定位模块和验证范围；
3. 普通 PR 验证成本下降；
4. 完成一次中等复杂度真实 Agent Dogfood，并形成可复核的运行证据；
5. 三项源码目标已完成，或已有明确证据说明本轮未触发；
6. 完整 CI 通过；
7. 后续优化只能由真实使用中重复、可复现的问题触发。

以上七项已经满足。本计划不再承担当前进度维护；后续事项、依赖和完成事件由
`../plans/vega-agent-evolution.json`、`../plans/events/` 与生成的 `CURRENT.md` 管理。
