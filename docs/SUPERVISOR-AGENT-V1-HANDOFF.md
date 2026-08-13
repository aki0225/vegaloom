# Supervisor Agent V1 当前交接

> 日期：2026-08-13
>
> 分支：`experiment/supervisor-agent-v1`
>
> 主线基线：`origin/main@706286d`
>
> 状态：`Gate 2A 审阅修复完成 / 最新 HEAD 待 PR CI`

## 当前结论

Gate 0、Gate 1 与 Gate 2A 已在同一个实验分支实现，Draft PR `#57` 已建立。旧代码 HEAD
`0faf2a7` 的 9 项 CI 全部通过；独立审阅发现并修复了 Observation 发布、Plan 发布顺序、
Recovery 证据引用和中间 Work Item 门禁四类问题。审阅修复仍须通过最新 HEAD 的 PR CI，
因此当前**不应直接合并到 `main`，也不应开始 Gate 2B 的真实 Codex 接入**。

既有 `vega do / loop / goal`、Reviewer、Verification、Risk Gate、Finish 的命令行为与成功
语义未改变；打包后的顶层 CLI 新增了 opt-in `vega agent` 子命令。`vega agent` 仍是实验入口；
Graph 只能路由到 `finalizing`，不能自行写入 `ready_to_commit`。

## 本轮已完成

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

## 已取得的本机证据

通过：

```text
Agent 状态、恢复、Task Card 与锁定向回归：62 passed
审阅修复后完整测试节点收集：1192 collected
旧代码 HEAD 完整测试节点收集：1188 collected
旧代码 HEAD PR #57 CI：9/9 success
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

旧代码 HEAD `0faf2a7` 已完成 9 项 PR CI；审阅修复后的最新 HEAD 尚未取得跨平台 CI 终态。
当前完整节点收集为 `1192 collected`，比旧代码 HEAD 增加 4 个审阅回归。全仓最终结论必须
等待最新 PR CI，不能复用旧 HEAD 的绿色状态。

## 回家后继续

```powershell
git fetch origin --prune
git switch experiment/supervisor-agent-v1
git pull --ff-only
git status --short --branch
Get-Content docs/SUPERVISOR-AGENT-V1-HANDOFF.md
```

建议顺序：

1. 确认本地 HEAD 与远端实验分支一致，Workspace 干净。
2. 查看 Draft PR `#57` 最新 HEAD 的全部 CI，不能复用 `0faf2a7` 的成功结果。
3. CI 失败时只修复能够复现的失败，不降低 fail-closed、单 Writer 或 60 秒测试上限。
4. CI 通过后再做一次最终 diff 审查，重点检查 Observation/Plan 发布顺序、Writer binding
   核销条件、损坏 Artifact 和 partial diff 的恢复路径。
5. 审查没有阻断项后，再决定是否把 Draft 转为 Ready；Gate 2B 仍需单独获得实施决定。

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

- Draft PR `#57` 的旧代码 HEAD 已通过 9 项 CI；审阅修复后的最新 HEAD 尚待 CI。
- 尚未连接真实 Codex Worker。
- 尚未执行两个冻结真实案例。
- 尚未验证跨机器 Task Card 接力和 Claude Code 薄 Adapter；它们属于 Gate 3。
- 受信 Observation 已经 write-once；若其后的 Checkpoint 写入失败，State 会保守保留 active
  Writer，但重试需要新的 Observation ID。该路径不会开放第二 Writer，后续是否需要事务化
  Observation/Decision/Graph 由 Gate 2B 真实 Adapter 故障注入决定。
