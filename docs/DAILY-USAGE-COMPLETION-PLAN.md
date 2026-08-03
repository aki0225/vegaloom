# Vega 日常可信使用完成计划

> 日期：2026-08-03
> 状态：已确认，等待分阶段实施
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

## 三、Phase 0：优先发布 `v0.1.4`

### 当前事实

- `pyproject.toml` 和源码版本为 `0.1.4`。
- 截至 2026-08-03，远端最新 Tag 为 `v0.1.3`。
- `docs/RELEASE-NOTES-0.1.4.md` 与 `docs/RELEASE-SUMMARY-0.1.4.md` 已存在。
- 2026-08-03 的两次候选分支真实 Codex Worker smoke 分别在 180 秒和 300 秒超时：
  前一次只得到部分 JSONL，后一次 `process-output.txt` 为空。两次都正确进入
  `needs_human / timed_out` 并终止 owned process，但不构成真实 JSONL 闭环通过。
- 当前 `main` 尚无一份满足发布清单中 JSONL 验收条件的成功 smoke；Phase 0 仍阻断在该门禁。
- 因此当前准确口径是“`0.1.4` 发布候选”，不是“`v0.1.4` 已发布”。

### 执行动作

1. 从干净 `main` 执行 `docs/RELEASE-CHECKLIST.md`。
2. 按 `docs/RELEASE-CHECKLIST.md` 的固定条件，在最终候选提交上重新执行真实 Codex JSONL
   smoke，并确认 GitHub CI、wheel/sdist 构建与源码树外安装 smoke。
3. 对最终候选提交做路径、凭据、BOM 和临时产物检查。
4. 全部门禁通过后，由人工创建 annotated `v0.1.4` Tag。
5. 使用现有发布摘要创建 GitHub Release。

任一门禁失败就停止发布，保留候选状态。本计划文档所在的 PR 不创建 Tag，也不执行发布。

## 四、Phase 1：完成 CRWP-V1 剩余验证

CRWP-V1 用来回答真实任务上的缺陷发现、成本和人工接管问题，不为得到成功样本而修改 Runtime。

固定处理：

1. Case 01 保留现有 `workspace_check_failed` 结果，不清理后重跑。
2. Case 02 按预注册执行最终负向输入扫描，再启动正式 Worker。
3. Case 03 保持 `eligibility-changed-before-run`，不启动 Worker。
4. 具体状态只更新 `docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md`。
5. 新的运行结果只追加到 `eval/real-world-runs.md`，不得改写历史记录。

本阶段不增加 Runtime、命令、状态、artifact 或成功条件。

## 五、Phase 2：完成调查与计划协议

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

## 六、Phase 3：改进现有 Finish 报告

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

## 七、Phase 4：真实使用验收

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

1. `v0.1.4` Tag 与 GitHub Release 已发布；
2. CRWP-V1 三个 Case 都有合同允许的终态；
3. Codex、Claude Code 和 `vega do` 都能进入同一可信判断链；
4. Finish 能清楚展示结论、重要变更、验证、高风险和剩余问题；
5. 成功、Reviewer 打回、验证失败、高风险和恢复场景均有可复核记录。

观察期内只修复真实使用暴露的缺陷，不继续建设 Planner Agent、Multi-Worker、A2A、Web UI、
数据库、向量 Memory、Portable Evidence Bundle、Claude Code 原生自动 Runner 或新的
Assurance Stage。

## 十、当前执行顺序

```text
本计划与文档导航
  -> 发布 v0.1.4
  -> 完成 CRWP-V1
  -> 固定调查与 Plan-first 协议
  -> 改进 Finish 第一屏
  -> 完成真实使用验收
  -> 停止扩张，进入日常使用观察
```
