# Vega AI 可维护性治理计划

> 状态：active
> 创建日期：2026-08-23
> 基线：`main@6a95970`
> 目标版本：不预设版本号；三轮完成并通过真实 Dogfood 后再决定发布

## 1. 为什么现在做

Vega 已经具备 Core Coding Harness 和可选 Supervisor Agent，但活跃产品、兼容入口、冻结实验、
历史证据和本地运行产物仍共同占用维护上下文。继续增加 Agent 能力前，先让人工和 AI 能稳定回答：

1. 当前产品入口是什么；
2. 一个改动归哪个模块；
3. 必须运行哪些验证；
4. 哪些代码和测试只为历史兼容保留；
5. 哪些本地产物可以清理，哪些证据必须保留。

本计划不追求最少文件或最高测试数字，只降低理解成本、重复实现和日常验证成本。

## 2. 当前基线

只读审计得到以下基线：

- `src/vega/`：153 个 Python 文件，约 46,546 行；129 个模块位于包根目录；
- `agent_*`：36 个文件，约 9,939 行；
- `loop_runtime.py`：3,039 行，assist 与 auto 存在重复的 post-worker 阶段；
- 内部依赖存在三组循环，其中一组跨越五个 Core 门禁与证据模块；
- 测试：49 个文件、1,408 个 node、约 43,000 行；普通 PR 执行 1,201 个产品 node；
- 文档：59 个 Markdown 文件、约 17,935 行；当前路线与历史时间线混在同一入口；
- CLI：约 23 个顶层入口、59 个叶子命令，日常、排障、兼容和实验入口展示层级接近；
- 本地工作区：117 个 `runs/` 目录、24 个 `.local-validation/` 顶层条目及构建产物。

这些数字用于验证治理是否有效，不作为机械删除目标。

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

状态：`completed`（2026-08-24）

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

状态：`pending`（下一步）

### 范围

1. 先记录本地与 CI 各分片的 node 数和 wall-clock，建立改造前基线。
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
- 普通 PR 关键路径 wall-clock 相对基线至少下降 20%，否则不得宣称测试优化有效；
- 删除或合并的测试均有覆盖去向，不以 node 数减少代替行为覆盖；
- main、release 和手工入口仍能运行完整历史与跨平台门禁；
- 完整 CI 通过。

## 6. 第三轮：源码职责与重复实现

状态：`pending`

### 范围

按以下顺序执行，每项使用独立提交，前一项验证通过后才进入下一项：

1. 抽取唯一 post-worker 阶段执行器，消除 assist 与 auto 在 Verification、Reflect、Scope、Risk、
   Reviewer 和 Finish 之间的重复编排；成功状态仍由 `LoopAutomationRuntime` 唯一拥有。
2. 统一 Supervisor operation Artifact 的路径、身份和摘要生成规则。
3. 拆除 Core 门禁与证据模块的五模块循环依赖，明确 comparison、risk evaluation 和 freshness 的所有者。
4. 清除无独立职责的转发 wrapper；只删除已经没有调用方的薄模块。

### 暂不处理

- 不一次性把 36 个 `agent_*` 文件搬进新包；
- 不合并 Risk Gate 与 Scope Gate validator；
- 不合并 Worker rerun 的 planning、runtime 与 transaction；
- 不合并 Windows command、process 和 Job Object 模块；
- 不建设新的插件框架、Provider SDK 或通用 Artifact 系统。

### 验收

- assist 与 auto 只调用同一个 post-worker 阶段实现；
- operation Artifact 身份规则只有一个实现所有者；
- Core 五模块循环依赖消失；
- 源码总行数和重复函数体减少，且未增加新的超过 500 行模块；
- Core、Supervisor、Security、Experimental、跨平台与打包验证全部通过。

## 7. 真实 Dogfood 进入条件

只有三轮均完成后，才选择一个真实项目和一个中等复杂度 Work Item 执行 Supervisor Agent Dogfood。
进入条件：

- 当前文档、规则和模块职责一致；
- 默认 CLI 产品面明确；
- 普通 PR 测试耗时已有可信下降；
- 主要重复流水线和循环依赖已经处理；
- 最新主线完整 CI 通过；
- 没有未解释的本地脏文件或历史 Task Card。

Dogfood 只使用现有 Artifact 记录人工步骤、耗时、Worker 轮次、Reviewer 打回、恢复和最终裁决，
不再建设第二套报告或证据系统。

## 8. 分支与提交边界

- 第一轮：规则、文档、Task Card 状态和 CLI 展示；不含测试删减或 Runtime 重构。
- 第二轮：测试职责和 CI；不改产品状态机。
- 第三轮：最多三个窄源码提交；不混入新 Agent 能力。
- 每轮独立验证、独立审查，确认后再进入下一轮。
- 任一轮出现成功语义变化、兼容入口破坏或无法解释的测试回归时，停止该轮并回到主线事实核对。

## 9. 完成定义

本计划完成不等于项目再无可优化之处。满足以下条件即停止治理并转入真实使用：

1. 当前事实入口清楚；
2. AI 能按规则定位模块和验证范围；
3. 普通 PR 验证成本下降；
4. 三项已识别的源码重复或耦合问题完成；
5. 完整 CI 通过；
6. 后续优化只能由真实使用中重复、可复现的问题触发。
