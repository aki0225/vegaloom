# Vega 日常可信使用完成计划

> 日期：2026-08-05
> 状态：Phase 0 至 Phase 4 已完成；进入真实日常使用观察
> 范围：`v0.1.4` 发布、CRWP-V1、调查与计划协议、Finish 报告、真实使用验收

## 一、完成标准

Vega 下一阶段不再扩建通用 Agent 平台，而是把已有能力整理成一条每天可重复使用的流程：

```text
用户提出 bug 或 feature
  -> 主会话只读调查
  -> 形成可审查的 Plan
  -> 人工确认范围
  -> Worker 修改代码
  -> Workspace / Scope / Verification / Risk Gate
  -> 独立 Reviewer
  -> Finish 给出结论、证据和人工检查位置
```

如果任务边界已经明确，可以省略重复调查，直接进入 `vega do`。两种入口不要求生成完全相同
的 Worker artifact，但从真实工作区差异开始，必须经过同一套 Scope、Verification、Risk、
Review 和 Finish 判断。

完成后，用户不需要逐行阅读全部 Diff，也能从 Finish 第一屏确认：

1. 实际修改了哪些文件；
2. 哪些修改风险最高；
3. 跑过哪些验证，哪些失败、跳过或未执行；
4. Reviewer 发现了什么；
5. 还有哪些结论没有证据支持；
6. 当前可以提交，还是必须人工处理。

## 二、执行原则

### 1. 不知道 bug 在哪里时，默认先调查

用户报告的通常是现象，不是根因。除非调用者已经明确修改位置、行为边界和验证方式，否则
默认流程是：

```text
只读调查 -> Plan -> 人工确认 -> 执行
```

这项约定先由 Codex、Claude Code 等宿主会话执行，不在 Vega Runtime 中增加“任务是否模糊”
的模型判断或启发式路由。

### 2. Plan 必须区分事实与假设

主会话生成的 Plan 使用固定结构：

```markdown
## User Goal
## Non-goals
## Observed Facts
## Hypotheses
## Proposed Scope
## Verification
## Risk Areas
## Unresolved Decisions
```

- `Observed Facts` 只记录已经由代码、文件或命令确认的事实，并附来源。
- `Hypotheses` 明确标记尚未确认的根因，不能作为事实交给 Worker。
- `Proposed Scope` 说明允许读取和修改的范围，但不能覆盖 `.vega.yaml`。
- `Risk Areas` 必须列出支付、数据库、并发、权限和敏感数据等命中项。

权威顺序固定为：

```text
用户任务、AGENTS.md、.vega.yaml
  > 已观察到的代码与运行事实
  > 根因假设和修改建议
```

### 3. 人工确认发生在修改前

调查完成后，主会话先把 Plan 交给用户确认。只有以下任一条件成立才进入修改：

- 用户明确批准 Plan；
- 用户一开始就提供了精确范围、验收标准，并明确要求直接执行。

这不是新增审批系统，也不新增 Plan 状态机。第一版只通过项目说明和宿主 Skill 固定使用协议。

### 4. Finish 只整理现有证据

Finish 第一屏必须由现有结构化 artifact 确定性生成，不再调用模型做二次总结。Reviewer 意见
与确定性事实分开显示，不能把“Reviewer 认为”改写成“已经证明”。

### 5. 高风险修改必须显式交给人

继续复用 `.vega.yaml` 的 `risk.required_reviews`。命中支付、数据库、并发、权限或敏感数据
路径时：

- 展示全部命中文件和风险类别；
- 展示 Reviewer 提供的关键行和判断；
- 明确实际验证证据；
- 明确人工必须检查的位置和剩余风险；
- 最终保持 `needs_human`，不由 AI 自动升级为安全。

## 三、Phase 0：发布 `v0.1.4`（completed）

### 完成证据

- annotated Tag `v0.1.4` 已发布，指向
  `289a1ad0431e0aaa2e74768c517058e62a33fdbf`；GitHub Release 已发布。
- 发布说明和发布摘要分别保存在 `docs/RELEASE-NOTES-0.1.4.md` 与
  `docs/RELEASE-SUMMARY-0.1.4.md`。
- 2026-08-03 的两次早期候选分支真实 Codex Worker smoke 分别在 180 秒和 300 秒超时：
  前一次只得到部分 JSONL，后一次 `process-output.txt` 为空。两次都正确进入
  `needs_human / timed_out` 并终止 owned process，但不构成真实 JSONL 闭环通过。
- 后续候选提交上的 run `20260803-215834-817575-feature-loop` 已完成 Worker、Verification、
  Reviewer 与 Finish 闭环，并验证 stdout/stderr 分流、固定安全进度、凭据扫描和进程清理。
- 精确 Tag 上的 fresh run `20260804-093946-135999-feature-loop` 已完成 Worker、
  Verification、Reviewer 与 Finish：只修改 `README.md`，验证为 `1 passed`，Reviewer
  `approve`、findings 为 `0`，Finish 为 `ready_to_commit`，完整性和新鲜度均通过。
- 该运行还确认 Worker/Reviewer JSONL 全部可解析、stderr 为空、安全进度不含目标路径和
  Codex 命令、凭据扫描为 `0`，登记进程均已退出。

Phase 0 已结束。后续不得移动、重建或覆盖 `v0.1.4` Tag，也不为补充新功能改写该 Release。

## 四、Phase 1：完成 CRWP-V1 剩余验证（completed）

CRWP-V1 用来回答真实任务上的缺陷发现、成本和人工接管问题，不为得到成功样本而修改 Runtime。

最终结果：

1. Case 01 保留 `needs_human / workspace_check_failed`，不清理后重跑。
2. Case 02 完成最终负向输入扫描并执行唯一正式 Worker，结果为
   `needs_human / timed_out`；Worker 未修改文件，Verification 和 Reviewer 未启动。
3. Case 03 保持 `eligibility-changed-before-run`，没有启动 Worker。
4. 三个 Case 都有合同允许的终态，不合并计算成功率，也不把 timeout 解释成模型修复失败。
5. 详细状态见 `docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md`，运行结果只追加在
   `eval/real-world-runs.md`。

Phase 1 已结束。禁止选择性重跑、延长 timeout、改换模型或修改 Runtime 来追求成功样本。

## 五、Phase 2：完成调查与计划协议（completed）

本阶段只调查现有入口并固定宿主协议，没有修改 Runtime，也没有把 Finish 报告改动提前
混入本阶段。

### 宿主会话入口

Codex 与 Claude Code 使用相同约定：

1. 模糊任务先做只读调查；
2. 按固定模板生成 Plan；
3. 用户确认后启动 `vega loop ... --mode assist`；
4. Worker 按 `worker-prompt.md` 修改代码；
5. 执行 `vega loop continue`；
6. 最后必须执行 `vega finish`。

Claude Code 可以作为外部 Worker，但不能使用同一会话代替独立 Reviewer。

### 命令行入口

`vega do` 继续表示“调用者已经确认任务边界，可以直接执行的小任务”。Vega 不在 Runtime
内部自动启动 Planner Agent，也不自行判断需求是否模糊。

### 本阶段交付物

- Codex Skill 中的 Plan-first 使用约定；
- Claude Code 等价的 assist 使用说明；
- 一份固定 Plan 模板；
- 不新增默认命令、状态或 schema。

### 完成证据

- [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) 固定直接执行条件、只读调查边界、
  Plan 模板、人工确认和 Codex/Claude Code 使用方式。
- `vega adapters init codex` 生成的 `$vega-loop` Skill 已按同一协议更新。
- `vega do`、`vega loop`、`vega plan` 的 Runtime 与命令语义没有改变。
- Claude Code 继续作为外部 Worker 使用 `assist`，同一会话不能替代独立 Reviewer。

Phase 2 已结束。后续不再扩展 Planner、adapter target 或宿主路由；真实效果在 Phase 4 按
预定场景验收。

## 六、Phase 3：改进现有 Finish 报告（completed）

只调整：

- `finish-summary.json`
- `finish-report.md`

不增加命令、模型调用、状态或第二套裁决逻辑。

Finish 第一屏按以下顺序展示：

1. **当前裁决**：`ready_to_commit`、`needs_human` 或失败原因；
2. **实际变更**：文件、范围、预算和高风险命中；
3. **确定性 Gate**：workspace、scope、verification、risk、完整性和新鲜度；
4. **验证结果**：每条命令的通过、失败、超时或跳过；
5. **Reviewer 意见**：finding、Reviewer 给出的关键行和建议；
6. **证据上限**：哪些结论没有被证明；
7. **下一步**：可以提交，还是需要修复、重跑或人工检查。

Reviewer 没有提供关键行时，Finish 不自行生成行号。报告不得隐藏失败，也不得用“基本可以”
弱化 `needs_human`。

实施结果：

- `finish-summary.json` 新增兼容性的 `first_screen` 派生视图，原字段继续保留；
- `finish-report.md` 第一屏按上述七段固定顺序展示；
- Run、仓库、任务类型和自动化模式仍在第一屏可见，没有因重排丢失运行身份；
- 验证命令明确区分通过、失败、超时和跳过；
- Reviewer 未提供有效行号时明确显示“未提供行号”，不补造 `:0`；
- 展示逻辑位于纯派生模块，没有新增模型调用、状态、命令或裁决路径。

## 七、Phase 4：真实使用验收（completed）

至少覆盖以下场景：

1. Codex assist：用户只描述 bug 现象，主会话调查、计划、确认后完成 Finish。
2. Claude Code assist：使用同一 Plan 和 Vega 判断链完成任务。
3. `vega do`：边界清晰的小任务直接执行。
4. Reviewer 打回：修改和验证通过，但 Reviewer 找到实际问题。
5. Fail-closed：验证失败、高风险必审、Provider 异常或恢复接管。

验收时让未参与执行的人只读 Finish，不先看完整 Diff，并回答：

- 改了什么；
- 哪里最危险；
- 哪些验证实际通过；
- 哪些仍需人工检查；
- 当前能不能提交。

如果这些问题不能从 Finish 中可靠回答，再做最小信息重排；不新增新的报告 Runtime。

### 完成证据

Phase 4 的五类场景均已在 [`../eval/real-world-runs.md`](../eval/real-world-runs.md) 留下真实
记录：

1. **Codex assist、Reviewer 打回和高风险 fail-closed**
   - 未公开 Java 结算任务先由主会话调查和确认计划；
   - 歧义执行文本被人工拒绝，ignored 构建目录变化被 Workspace Gate 阻止；
   - Reviewer 指出的回归缺口和测试夹具问题都在通过前得到处理；
   - 最终验证和审查通过，但结算风险仍固定为 `needs_human`。
2. **边界清晰任务的 `vega do`**
   - Echo Vault 登录页语言切换通过 fresh auto Run；
   - 前端测试、构建、三阶段 Scope Gate、Workspace Gate、Risk Gate 和独立 Reviewer 均通过；
   - Finish 为 `ready_to_commit`，同时保留首次 Python bytecode 污染触发的失败记录。
3. **Claude Code assist**
   - 用户只提供历史会话无法重新打开的界面现象；
   - Claude Code 先只读调查，主会话修正错误命令、交互方案和差异预算，人工确认后再修改；
   - 首次 `continue` 因新增测试未跟踪而 fail-closed，第二轮完整前端测试报告
     `12 files / 72 tests passed`，构建和 `git diff --check` 通过；
   - 独立 Codex Reviewer 返回 `approve`，Finish 为 `ready_to_commit`。
4. **未参与执行的人只读 Finish**
   - 全新 Claude Code 会话能从第一屏识别实际变更、验证结果、风险评级和提交前人工检查要求；
   - 同时识别出测试用例名称缺失、`Scope：未记录` 和 Workspace 状态展示不一致等证据上限。

Phase 4 已达到预定覆盖标准。当前结果证明三种入口能够进入同一判断链，也证明失败、高风险和
证据不足不会被隐藏；它不构成成功率、跨仓库泛化能力或生产安全证明。Finish 的展示观察项先
保留，只有日常使用中重复造成误判时才做最小修正，不新增新的报告 Runtime。

### 对外表述

真实验收完成后再调整 README 首屏，不把“本地优先”继续作为产品主标题。建议使用：

```text
AI 编码的验证与独立评审 Harness。

Worker 修改代码，Reviewer 使用独立会话审查。
项目自己的测试、代码差异和风险规则决定任务能不能结束。
```

“本地运行、产物默认留在本机”放在运行边界中说明。GitHub About 保留：

```text
One writes, one reviews — worker 与 reviewer 上下文隔离的 AI 编码工作流 Harness
```

## 八、分支与提交规则

- `v0.1.4` 发布在干净 `main` 上完成，不为 Tag 单独创建功能分支。
- CRWP 只提交运行登记、追加证据和必要交接，不混入 Runtime 修改。
- Plan 协议与 Finish 报告分别使用一个短生命周期分支；同一阶段的 CI 修正继续留在原分支，
  不为每次修正新建分支。
- 每个 PR 合并后删除远端分支。
- 不把多个互不相关的阶段塞进同一个 PR。

## 九、停止条件

以下条件满足后停止新增产品能力，转入真实日常使用：

1. `v0.1.4` Tag 与 GitHub Release 已发布；**已完成**。
2. CRWP-V1 三个 Case 都有合同允许的终态；**已完成**。
3. Codex、Claude Code 和 `vega do` 已有同一协议并完成真实验收；**已完成**。
4. Finish 能清楚展示结论、重要变更、验证、高风险和剩余问题；**已完成**。
5. 成功、Reviewer 打回、验证失败、高风险和恢复场景均有可复核记录；**已完成**。

观察期内只修复真实使用暴露的缺陷，不继续建设 Planner Agent、Multi-Worker、A2A、Web UI、
数据库、向量 Memory、Portable Evidence Bundle、Claude Code 原生自动 Runner 或新的
Assurance Stage。

## 十、当前执行顺序

```text
发布 v0.1.4（完成）
  -> CRWP-V1 合同允许终态（完成）
  -> 调查现有入口并固定 Plan-first 与人工确认协议（完成）
  -> 改进 Finish 第一屏（完成）
  -> 完成真实使用验收（完成）
  -> 停止扩张，进入日常使用观察（当前）
```

## 十一、2026-08-05 交接

### 本轮已经完成

- Claude Code assist 的 Echo Vault 历史会话案例已经完成调查、人工确认、外部修改、Vega
  验证、独立 Reviewer 和 Finish。
- 正式 Run 为 `20260805-172130-495761-bug-loop`。第一次 `continue` 因新增测试未跟踪而
  fail-closed；第二轮报告 `12 files / 72 tests passed`，构建和 `git diff --check` 通过，
  Reviewer 为 `approve`，Finish 为 `ready_to_commit`。
- 公开脱敏摘要已经追加到 [`../eval/real-world-runs.md`](../eval/real-world-runs.md)，历史记录
  没有改写。
- Phase 4 五类场景已经全部覆盖，本文、文档导航和 Roadmap 已切换到日常使用观察状态。

### 从另一台电脑恢复

```powershell
git switch main
git pull --ff-only origin main
git status --short --branch
```

拉取后按以下顺序阅读：

1. 本文的“Phase 4：真实使用验收”和本交接；
2. [`../eval/real-world-runs.md`](../eval/real-world-runs.md) 文件末尾的 Claude Code assist
   记录；
3. [`ROADMAP.md`](ROADMAP.md) 的当前主线；
4. [`PLAN-FIRST-PROTOCOL.md`](PLAN-FIRST-PROTOCOL.md) 的下一次真实任务使用协议。

Claude Code 原始会话、正式 `runs/` 和隔离目标仓库只保留在执行机器，不进入公开 Git；另一台
电脑不需要这些本机 artifact 才能继续主线。不要把缺失的本机 `runs/` 误判为远端提交不完整。

### 下一步

1. 优先把 Vega 用于下一项真实日常任务，不新增 Planner、Multi-Worker 或新的报告 Runtime。
2. 只读确认 Finish 的 `Scope：未记录`、Workspace 汇总不一致和测试名称缺失是否会在其他 Run
   重复出现。
3. 只有问题能够稳定复现并影响人工判断时，才从最新 `main` 建立一个短生命周期分支做最小修复；
   同一修复持续使用原分支，不为每次调整重复建分支。
4. 不重跑本次 Claude Code 案例，不移动 `v0.1.4` Tag，不把单案例结果解释为总体成功率或生产
   安全证明。
