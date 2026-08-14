# Supervisor Agent V1 当前交接

> 日期：2026-08-14
>
> 当前实施分支：`experiment/supervisor-agent-gate-2b`
>
> 实施基线：`main@e126aa2`
>
> 状态：`Gate 2B 代码 HEAD CI 9/9 / 真实案例待执行`

## 当前结论

Gate 0、Gate 1 与 Gate 2A 已进入主线。独立审阅发现并修复了 Observation 发布、
Plan 发布顺序、Recovery 证据引用和中间 Work Item 门禁四类问题。修复后的代码 HEAD
`4180e7e` 已通过 workflow `31718078414` 的 9 项 CI，最终文档 HEAD `8ca75f2` 已通过 workflow
`31718680069` 的 9 项 CI。PR `#57` 已以 `6a5c927` 合并到 `main`，Gate 2A 没有遗留阻断项。

Gate 2B 已获人工批准，并在单一实验分支和专用 Worktree 完成机械合同。真实 Codex Adapter 的
信任边界、两个冻结案例、预算、超时和停止条件见
[`SUPERVISOR-AGENT-GATE-2B-PLAN.md`](SUPERVISOR-AGENT-GATE-2B-PLAN.md)。当前仍不能写成
Gate 2B 通过：代码 HEAD `799bb29` 已通过 PR `#58` workflow `31775697034` 的 9 项 CI，但
`SAG2B-01` 与 `SAG2B-02` 尚未执行。

既有 `vega do / loop / goal`、Reviewer、Verification、Risk Gate、Finish 的命令行为与成功
语义未改变；打包后的顶层 CLI 仍以 opt-in `vega agent` 暴露实验能力。Graph 只能路由到
`finalizing`，不能自行写入 `ready_to_commit`。

## Gate 2B 当前实现

1. `vega agent run --run <agent-run> --timeout <seconds>` 启动一个真实 `codex exec` Worker。
2. 首次 attempt 创建现有 assist child，冻结 baseline 后绑定唯一 child、operation 和显式
   execution identity；Reviewer 打回后的 repair 复用同一 child，并为新 operation 保留独立
   execution 目录。
3. Worker 最终消息只解析为 `claimed_status / summary / tests_claimed /
   remaining_questions`，不接受 changed files、测试状态、风险或完成声明。
4. Worker 退出后先检查批准 Plan 的总路径范围，再由现有 child Core 执行 Workspace、
   Verification、Risk、Reviewer 与 Finish；Core 结束后再次检查 Plan 范围，验证或其他
   Core 步骤产生的越界修改同样不能进入成功路由。
5. Adapter 读取 child execution、状态和 Finish 摘要，形成 `machine_reconcile` Observation；
   外部 `agent observe` 仍然只保存 Claim。
6. `blocked` Risk/Reviewer/Verification 直接进入人工；明确失败才按范围选择 repair 或 replan。
7. active child 的 `stop` 只向与 Agent operation 身份匹配的 owned execution 写入停止请求；
   `recover` 能读取 sibling assist child 的 execution 并在 Agent run 内保存摘要引用。
8. 单个 Work Item 最多两次 dispatch；Plan 外路径修改不进入 Core，直接形成 replan 或人工结果。
9. 本轮没有修改 `loop_runtime.py`、Verification、Risk、Reviewer、Finish、默认
   `do / loop / goal` 或自动 Git 行为。
10. Gate 2B Adapter 当前只接受一个未完成 Work Item。现有 assist child 会拒绝把旧 tracked
    diff 当作新 child baseline，因此多 Work Item 累计 Diff 归因仍未证明；本轮没有为通过测试
    而放宽该门禁。
11. 首次真实 Worker 要求干净 Workspace；repair Worker 如果没有产生新的 Workspace 变化，
    不运行 Core，也不把上一 attempt 的 Diff 重记为当前修复证据。

## Gate 2A 已进入主线的能力

1. 对同一 Agent run 的批准、计划修改、dispatch、observe、recover、pause、resume、steer 和
   stop 使用 mutation lock 串行化。
2. dispatch 在发布 `acting` State 前，以 run-local write-once marker 登记 operation identity；
   同一 run 不允许复用旧 `operation_id`。随后原子持久化唯一 child/operation binding，并保守
   标记 operation 可能已经开始；该标记表示已越过不可自动重试边界，不等于证明真实进程已经启动。
3. CLI 输入的 Observation 永远按外部 Claim 保存，不能伪造完成状态或 Gate 结果；受信
   Observation 必须绑定当前 Work Item、child 和 operation；Observation ID 不能包含路径，
   外部 Claim 与 Recovery 机器对账均使用 write-once Artifact，不能复用后覆盖旧证据。
4. 旧 binding 尚未被可靠核销时一律禁止第二 Writer；Worker 仍存活、execution 缺失或损坏、
   execution identity 与当前 operation 不一致、Trace 损坏等情况都会保留原
   child/operation binding。
5. 当前 dispatch 后不存在可依赖的 `worker_reserved` 中间态或第二次 `confirm_started`。
   只有未来由受信 Adapter 在同一原子边界内证明“进程未启动”时，才可评估自动释放旧 binding；
   Gate 2A 对缺少此类证据的现场保持 fail-closed。
6. 旧版两阶段 dispatch 留下的 `operation_started=false` 只表示未取得启动确认，不能作为
   operation 未启动证明；升级后恢复一律保留原 binding 并交给人工。
7. Recovery 只有在 Checkpoint 成功落盘后才提交解除 Writer 的新 State；Checkpoint 写入失败时
   保留原 binding，避免崩溃窗口错误释放 Writer。
8. partial diff、数据库/支付/部署/外部 API 副作用、损坏状态、未知 schema 和不完整现场均
   fail-closed 进入人工处理。
9. SQLite Graph checkpoint 丢失时仍从 Agent State、Checkpoint 与真实 Workspace 对账。
10. 增加 `pause`、`resume-local`、`stop` 和结构化 `recover` 命令；不执行自动回滚、提交或推送。
11. Plan revision 写入前先撤销旧批准和 dispatch 权限；批准、恢复和 `next / repair` 只有在当前
    Checkpoint 与 Task Brief 成功落盘后，才发布可 dispatch 的 State。
12. dispatch 前重新校验 State、批准 Plan、safe Checkpoint 与 Task Brief manifest 属于同一
    revision、Work Item 和 Workspace，拒绝 stale Plan 或 stale Task Brief。
13. 跨机器 Task Card 恢复只有在 Checkpoint、Task Brief、Trace 和状态卡全部写入成功后，才
    最后发布可 dispatch 的 State；任何向调用方报告的写入失败都不会留下隐藏 ready run。
14. 同步实施计划、状态权威 ADR、Roadmap、文档导航和 CI core-cli 分片。
15. 受信 Observation 使用独占创建；Checkpoint 和下一轮 Task Brief 成功后才发布推进后的 Plan，
    State 继续作为最后的调度安全闩。中间任一步失败都保留旧 active Writer，且不会覆盖旧证据。
16. Recovery Observation 显式引用 operation marker 和可用的 `execution.json`；中间 Work Item
    若 Verification、Risk 或 Reviewer 证据缺失或过期，一律转人工，不启动下一 Writer；
    明确失败或阻断仍按既有规则进入 `repair / replan / human`。
17. 增加打包 CLI 回归，确认新增 opt-in `agent` 后，既有 `do / loop / goal` 命令仍然存在。

## Gate 2B 本机机械证据

通过：

```text
Codex Adapter 定向回归：11 passed
active child stop/recover 定向回归：3 passed
显式 execution identity 定向回归：通过
Agent 机器 Observation、blocked Risk 与 CLI 定向回归：通过
完整测试节点收集：1216 collected
architecture growth：通过（C901 35->35，Python 模块 122->126）
Ruff：通过
compileall：通过
repository hygiene --base-ref origin/main：通过
git diff --check：通过
PR #58 代码 HEAD 799bb29 CI：9/9 success
workflow：31775697034
```

当前本机使用 Python 3.14，首次加载 LangGraph/LangChain 依赖较慢，并出现其已知 Pydantic V1
兼容性警告；定向测试按小集合串行执行。项目 PR CI 使用 Python 3.11/3.12，仍是本轮合并前
必须取得的权威自动化证据。

上述结果不包含真实 Codex Case，不得写成 `SAG2B-01` 或 `SAG2B-02` 已通过。

## 已有 Gate 2A 证据

通过：

```text
Agent 状态、恢复、Task Card 与锁定向回归：62 passed
审阅修复后完整测试节点收集：1192 collected
旧代码 HEAD 完整测试节点收集：1188 collected
旧代码 HEAD PR #57 CI：9/9 success
审阅修复代码 HEAD 4180e7e PR #57 CI：9/9 success
workflow：31718078414
最终文档 HEAD 8ca75f2 PR #57 CI：9/9 success
最终文档 workflow：31718680069
主线合并提交：6a5c927
Ruff：通过
compileall：通过
architecture growth：通过（C901 35->35，Python 模块 104->122）
repository hygiene --base-ref origin/main：通过
CI YAML 解析与 core-cli 分片：通过
git diff --check：通过
UTF-8 BOM 检查：通过
```

项目 CI 使用 Python 3.11/3.12。本轮审阅修复后的 Agent 回归已取得明确终态：Contract 19、
Recovery 20、Runtime 19、Task Card 4，共 `62 passed`。Runtime 拆为 10/9 节点，分别用时
34.62 秒与 30.49 秒；Recovery 拆为 10/10 节点，分别用时 33.18 秒与 33.65 秒。整文件 Runtime
和 Recovery 的首次命令超过 60 秒，只能记录为超时未验证，不能计为失败或通过。

审阅修复代码 HEAD `4180e7e` 已完成 workflow `31718078414` 的 9 项 PR CI。当前完整节点
收集为 `1192 collected`，比旧代码 HEAD 增加 4 个审阅回归。最终文档 HEAD `8ca75f2` 也已完成
workflow `31718680069` 的 9 项 CI，并以 `6a5c927` 合并到 `main`。

## 后续接力

```powershell
git fetch origin --prune
git switch experiment/supervisor-agent-gate-2b
git pull --ff-only
git status --short --branch
Get-Content docs/SUPERVISOR-AGENT-V1-HANDOFF.md
```

建议顺序：

1. 确认当前分支与远端实验分支一致，Workspace 干净。
2. 准备隔离目标副本，先运行 `SAG2B-01`。
3. `SAG2B-01` 形成合同允许终态后，才运行 `SAG2B-02` 的 stop/partial-diff 场景。
4. 两个真实案例和独立审查完成后，再决定是否把 Draft PR 转为 Ready、合并或继续修改。

## 下一 Gate 的边界

Gate 2B 只应连接一个真实 Codex Adapter，并复用当前：

- Plan 批准；
- Task Brief；
- 单 Writer 与 owned process；
- Observation / Decision；
- Workspace 对账；
- Verification、Risk、Reviewer 与 Finish。

Gate 2B 不新增多 Worker、Provider 平台、服务端、自动重试、自动 commit/push/release 或第二套
成功裁决。

## 未完成事项

- 尚未执行两个冻结真实案例。
- 尚未证明多 Work Item 的真实 Adapter 累计 Diff 归因；Gate 2B 当前 fail-closed 拒绝该形态。
- 尚未验证跨机器 Task Card 接力和 Claude Code 薄 Adapter；它们属于 Gate 3。
- 尚未决定 `v0.2.0` 发布时点。
- 受信 Observation 已经 write-once；若其后的 Checkpoint 写入失败，State 会保守保留 active
  Writer，但重试需要新的 Observation ID。该路径不会开放第二 Writer，后续是否需要事务化
  Observation/Decision/Graph 由 Gate 2B 真实 Adapter 故障注入决定。
